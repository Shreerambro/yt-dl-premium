"""
yt-dlp wrapper — fetch info, download with progress tracking.
Per-user cookies support for members-only content.
"""
import asyncio
import time
import re
from pathlib import Path
from typing import Optional

import yt_dlp

from config import DOWNLOAD_DIR, COOKIES_DIR, MAX_FILE_SIZE


# ─── Helpers ────────────────────────────────────────────────

def _sanitize_filename(name: str) -> str:
    """Strip characters that break filesystems."""
    return re.sub(r'[<>:"/\\|?*]', '', name)[:200]


def _sizeof_fmt(num: float) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024:
            return f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"


def _duration_fmt(seconds: int) -> str:
    """Seconds → HH:MM:SS or MM:SS."""
    if not seconds:
        return "Live / Unknown"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _cookies_path(user_id: int) -> Optional[str]:
    """Return cookie file path for a user if it exists."""
    p = COOKIES_DIR / f"{user_id}.txt"
    return str(p) if p.exists() else None


def _base_opts(user_id: int) -> dict:
    """Base yt-dlp options shared across extract & download."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_retries": 3,
    }
    cookies = _cookies_path(user_id)
    if cookies:
        opts["cookiefile"] = cookies
    return opts


# ─── Data Classes ───────────────────────────────────────────

class VideoFormat:
    __slots__ = ("label", "height", "filesize", "fps", "format_note")

    def __init__(self, label, height, filesize, fps, format_note):
        self.label = label
        self.height = height
        self.filesize = filesize
        self.fps = fps
        self.format_note = format_note

    @property
    def size_str(self) -> str:
        return _sizeof_fmt(self.filesize) if self.filesize else "~?"

    def button_text(self) -> str:
        fps_tag = f" {self.fps}fps" if self.fps and self.fps > 30 else ""
        return f"📹 {self.label}{fps_tag} ({self.size_str})"


class VideoInfo:
    def __init__(self, data: dict):
        self.title: str = data.get("title", "Unknown")
        self.duration: int = data.get("duration") or 0
        self.duration_str: str = _duration_fmt(self.duration)
        self.thumbnail: str = data.get("thumbnail", "")
        self.uploader: str = data.get("uploader", "Unknown")
        self.url: str = data.get("webpage_url") or data.get("original_url", "")
        self.is_live: bool = data.get("is_live", False)
        self.view_count: int = data.get("view_count") or 0
        self._raw = data
        self.formats: dict[str, VideoFormat] = self._parse_formats(data.get("formats", []))
        self.audio_size: int = self._best_audio_size(data.get("formats", []))

    # ── format parsing ──────────────────────────────────────

    @staticmethod
    def _parse_formats(formats: list) -> dict[str, VideoFormat]:
        quality_map: dict[str, VideoFormat] = {}

        for f in formats:
            h = f.get("height")
            if not h or f.get("vcodec", "none") == "none":
                continue

            label = f"{h}p"
            filesize = f.get("filesize") or f.get("filesize_approx") or 0

            existing = quality_map.get(label)
            if not existing or filesize > existing.filesize:
                quality_map[label] = VideoFormat(
                    label=label,
                    height=h,
                    filesize=filesize,
                    fps=f.get("fps") or 30,
                    format_note=f.get("format_note", ""),
                )
        return quality_map

    @staticmethod
    def _best_audio_size(formats: list) -> int:
        best = 0
        for f in formats:
            if f.get("acodec", "none") != "none" and f.get("vcodec", "none") == "none":
                sz = f.get("filesize") or f.get("filesize_approx") or 0
                if sz > best:
                    best = sz
        return best

    def sorted_qualities(self) -> list[VideoFormat]:
        """Return formats sorted by resolution ascending."""
        return sorted(self.formats.values(), key=lambda x: x.height)

    def info_text(self) -> str:
        return (
            f"🎬 **{self.title}**\n\n"
            f"👤 {self.uploader}\n"
            f"⏱ {self.duration_str}  •  👁 {self.view_count:,} views\n"
        )


# ─── Download Progress Tracker ──────────────────────────────

class DownloadProgress:
    """Thread-safe progress state polled by the bot."""

    def __init__(self):
        self.status: str = "starting"     # starting | downloading | merging | done | error
        self.downloaded: int = 0
        self.total: int = 0
        self.speed: float = 0.0
        self.eta: int = 0
        self.filepath: str = ""
        self.error: str = ""
        self._last_hook_time: float = 0

    def _hook(self, d: dict):
        """yt-dlp progress_hooks callback (runs in executor thread)."""
        if d["status"] == "downloading":
            self.status = "downloading"
            self.downloaded = d.get("downloaded_bytes") or 0
            self.total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            self.speed = d.get("speed") or 0
            self.eta = d.get("eta") or 0
        elif d["status"] == "finished":
            self.status = "merging"
            self.filepath = d.get("filename", "")

    def bar(self, width: int = 20) -> str:
        """Render a text progress bar."""
        if self.total <= 0:
            pct = 0
        else:
            pct = min(self.downloaded / self.total, 1.0)

        filled = int(width * pct)
        empty = width - filled
        bar_str = "⬢" * filled + "⬡" * empty
        pct_str = f"{pct * 100:.1f}%"
        dl_str = _sizeof_fmt(self.downloaded)
        tot_str = _sizeof_fmt(self.total) if self.total else "?"
        spd_str = f"{_sizeof_fmt(self.speed)}/s" if self.speed else "..."
        eta_str = f"{int(self.eta)}s" if self.eta else "..."

        lines = [
            f"{bar_str}  {pct_str}",
            f"📥 {dl_str} / {tot_str}",
            f"⚡ {spd_str}  •  ⏱ ETA: {eta_str}",
        ]
        if self.status == "merging":
            lines.append("🔄 Merging audio + video …")
        return "\n".join(lines)


# ─── Core Functions ─────────────────────────────────────────

async def fetch_info(url: str, user_id: int) -> VideoInfo:
    """Fetch video metadata without downloading."""
    opts = _base_opts(user_id)

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _extract)
    return VideoInfo(data)


async def download_video(
    url: str,
    quality: str,
    user_id: int,
    progress: DownloadProgress,
) -> str:
    """
    Download video at requested quality.
    quality: "360p", "720p", "1080p", … or "audio"
    Returns path to the downloaded file.
    """
    opts = _base_opts(user_id)
    opts["progress_hooks"] = [progress._hook]
    opts["outtmpl"] = str(DOWNLOAD_DIR / "%(title).150s [%(id)s].%(ext)s")

    if quality == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
    else:
        height = quality.replace("p", "")
        opts["format"] = (
            f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/best"
        )
        opts["merge_output_format"] = "mp4"

    def _dl():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    loop = asyncio.get_event_loop()
    raw_path = await loop.run_in_executor(None, _dl)

    # Resolve final extension (yt-dlp may have converted)
    raw = Path(raw_path)
    if quality == "audio":
        candidates = [raw.with_suffix(".mp3"), raw.with_suffix(".m4a"), raw.with_suffix(".opus")]
    else:
        candidates = [raw.with_suffix(".mp4"), raw.with_suffix(".mkv"), raw.with_suffix(".webm")]

    for c in candidates:
        if c.exists():
            progress.status = "done"
            progress.filepath = str(c)
            return str(c)

    if raw.exists():
        progress.status = "done"
        progress.filepath = str(raw)
        return str(raw)

    raise FileNotFoundError(f"Downloaded file not found: {raw_path}")
