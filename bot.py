"""
YouTube Members-Only Downloader Bot
────────────────────────────────────
Telegram bot built with Pyrogram + yt-dlp.
Supports quality selection, members-only via cookies, progress bar.
"""
import asyncio
import logging
import os
import time
from pathlib import Path

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pyrogram.errors import MessageNotModified, FloodWait

from config import (
    API_ID, API_HASH, BOT_TOKEN,
    AUTH_USERS, MAX_FILE_SIZE,
    PROGRESS_UPDATE_INTERVAL, COOKIES_DIR, DOWNLOAD_DIR,
)
from downloader import (
    fetch_info, download_video, DownloadProgress, _sizeof_fmt, has_cookies,
)

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("bot")

# ─── Bot Init ───────────────────────────────────────────────
app = Client(
    name="yt_dl_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir="./sessions",
)

# In-memory state
pending: dict[int, dict] = {}       # user_id → {url, info}
active_downloads: dict[int, bool] = {}  # user_id → busy flag
COOKIE_WAIT: set[int] = set()       # users waiting to upload cookies


# ─── Auth Decorator ─────────────────────────────────────────

def authorized(func):
    """Skip if AUTH_USERS is set and sender isn't in the list."""
    async def wrapper(client, update):
        uid = update.from_user.id if hasattr(update, "from_user") else None
        if AUTH_USERS and uid not in AUTH_USERS:
            if isinstance(update, Message):
                await update.reply("🚫 **Access denied.** You're not authorized.")
            return
        return await func(client, update)
    return wrapper


# ─── /start ─────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.private)
@authorized
async def cmd_start(_, msg: Message):
    text = (
        "🎬 **YouTube Downloader Bot**\n\n"
        "Send me any YouTube link and I'll let you pick the quality.\n\n"
        "**Features:**\n"
        "• Quality selection (144p → 4K)\n"
        "• Audio-only (MP3 320kbps)\n"
        "• Members-only videos via cookies\n\n"
        "**Commands:**\n"
        "/start  — This message\n"
        "/cookies  — Upload cookies.txt for members-only\n"
        "/clearcookies  — Remove your saved cookies\n"
        "/cancel  — Cancel active download\n"
        "/help  — Usage guide"
    )
    await msg.reply(text)


# ─── /help ──────────────────────────────────────────────────

@app.on_message(filters.command("help") & filters.private)
@authorized
async def cmd_help(_, msg: Message):
    text = (
        "📖 **How to use:**\n\n"
        "1️⃣  Send any YouTube URL\n"
        "2️⃣  Bot fetches video info & available qualities\n"
        "3️⃣  Tap the quality button you want\n"
        "4️⃣  Wait for download + upload to finish\n\n"
        "**For Members-Only videos:**\n"
        "1. Install a browser extension like **Get cookies.txt LOCALLY**\n"
        "2. Go to YouTube, make sure you're logged in\n"
        "3. Export cookies as `cookies.txt` (Netscape format)\n"
        "4. Send /cookies and upload the file here\n"
        "5. Done! Now members-only links will work.\n\n"
        "**Limits:** Max file size ~2 GB (Telegram limit)\n"
    )
    await msg.reply(text)


# ─── /cookies ───────────────────────────────────────────────

@app.on_message(filters.command("cookies") & filters.private)
@authorized
async def cmd_cookies(_, msg: Message):
    uid = msg.from_user.id
    cookie_file = COOKIES_DIR / f"{uid}.txt"

    if cookie_file.exists():
        sz = cookie_file.stat().st_size
        text = (
            f"🍪 You already have cookies saved ({_sizeof_fmt(sz)}).\n\n"
            "Upload a new `cookies.txt` to replace, or /clearcookies to remove."
        )
    else:
        text = (
            "🍪 **Upload your `cookies.txt` file.**\n\n"
            "Export YouTube cookies from your browser in Netscape format\n"
            "(use the **Get cookies.txt LOCALLY** extension).\n\n"
            "Then send the `.txt` file here as a document."
        )
    COOKIE_WAIT.add(uid)
    await msg.reply(text)


@app.on_message(filters.command("clearcookies") & filters.private)
@authorized
async def cmd_clearcookies(_, msg: Message):
    uid = msg.from_user.id
    cookie_file = COOKIES_DIR / f"{uid}.txt"
    if cookie_file.exists():
        cookie_file.unlink()
        await msg.reply("✅ Cookies removed.")
    else:
        await msg.reply("ℹ️ No cookies found to remove.")


# ─── Cookie file upload handler ─────────────────────────────

@app.on_message(filters.document & filters.private)
@authorized
async def handle_document(_, msg: Message):
    uid = msg.from_user.id

    # Only accept if user triggered /cookies or file looks like cookies
    fname = msg.document.file_name or ""
    if uid not in COOKIE_WAIT and "cookie" not in fname.lower():
        return

    COOKIE_WAIT.discard(uid)
    status = await msg.reply("⏳ Saving cookies…")

    dest = COOKIES_DIR / f"{uid}.txt"
    await msg.download(file_name=str(dest))

    # Basic validation
    content = dest.read_text(errors="ignore")
    if "youtube.com" not in content.lower() and ".youtube." not in content.lower():
        dest.unlink()
        await status.edit_text("❌ This doesn't look like a YouTube cookies file. Make sure you export from youtube.com.")
        return

    await status.edit_text(
        f"✅ **Cookies saved!** ({_sizeof_fmt(dest.stat().st_size)})\n\n"
        "Members-only videos should work now. Send me a link to try!"
    )
    log.info("Cookies saved for user %s", uid)


# ─── /cancel ────────────────────────────────────────────────

@app.on_message(filters.command("cancel") & filters.private)
@authorized
async def cmd_cancel(_, msg: Message):
    uid = msg.from_user.id
    if uid in active_downloads:
        active_downloads[uid] = False
        await msg.reply("🛑 Cancelling…")
    else:
        await msg.reply("ℹ️ Nothing to cancel.")


# ─── YouTube URL Handler ────────────────────────────────────

YT_REGEX = r"(https?://)?(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/.+"


@app.on_message(filters.regex(YT_REGEX) & filters.private)
@authorized
async def handle_url(_, msg: Message):
    uid = msg.from_user.id
    url = msg.text.strip()

    if uid in active_downloads:
        await msg.reply("⏳ You already have an active download. Wait or /cancel first.")
        return

    status = await msg.reply("🔍 **Fetching video info…**")

    try:
        info = await fetch_info(url, uid)
    except Exception as e:
        err = str(e)
        if "sign in" in err.lower() or "confirm you're not a bot" in err.lower() or "bot" in err.lower() and "sign" in err.lower():
            cookie_hint = (
                "\n\n✅ You have cookies saved. They might be expired — try re-uploading fresh ones via /cookies"
                if has_cookies(uid) else
                "\n\n👉 Use /cookies to upload your YouTube cookies."
            )
            await status.edit_text(
                "🤖 **YouTube is asking to verify you're not a bot.**\n\n"
                "This happens on cloud servers. You need to upload browser cookies "
                "so YouTube recognizes you as a real user.\n\n"
                "**How to fix:**\n"
                "1. Install **Get cookies.txt LOCALLY** extension in Chrome\n"
                "2. Go to youtube.com (make sure you're logged in)\n"
                "3. Click extension → Export\n"
                "4. Send /cookies here and upload the file"
                f"{cookie_hint}"
            )
        elif "members-only" in err.lower() or "join this channel" in err.lower():
            if has_cookies(uid):
                await status.edit_text(
                    "🔒 **Members-only video!**\n\n"
                    "Your cookies are loaded, but your YouTube account "
                    "**does not have a membership** on this channel.\n\n"
                    "You need to **join/subscribe as a paid member** on "
                    "this channel first, then it will work."
                )
            else:
                await status.edit_text(
                    "🔒 **Members-only video detected!**\n\n"
                    "Use /cookies to upload your YouTube cookies.\n"
                    "Your account must have an **active membership** on this channel."
                )
        elif "private video" in err.lower():
            await status.edit_text("🔒 **Private video.** Can't access it.")
        elif "unavailable" in err.lower():
            await status.edit_text("❌ **Video unavailable.** It may be deleted or region-locked.")
        else:
            log.exception("Failed to fetch info for %s", url)
            await status.edit_text(f"❌ **Failed to fetch video info.**\n\n`{err[:300]}`")
        return

    if info.is_live:
        await status.edit_text("🔴 **Live streams aren't supported.** Wait until the stream ends.")
        return

    # Store for callback
    pending[uid] = {"url": url, "info": info}

    # Build quality keyboard
    buttons = []
    sorted_q = info.sorted_qualities()

    # Standard qualities we want to show (if available)
    target_heights = [144, 240, 360, 480, 720, 1080, 1440, 2160]
    shown = set()

    for fmt in sorted_q:
        if fmt.height in shown:
            continue
        if fmt.height not in target_heights:
            # Include non-standard heights too (e.g., 1920 for some vids)
            if fmt.height < 144:
                continue
        shown.add(fmt.height)
        buttons.append(
            InlineKeyboardButton(
                text=fmt.button_text(),
                callback_data=f"dl:{fmt.label}",
            )
        )

    # Audio-only option
    audio_size = _sizeof_fmt(info.audio_size) if info.audio_size else "~?"
    buttons.append(
        InlineKeyboardButton(
            text=f"🎵 Audio MP3 ({audio_size})",
            callback_data="dl:audio",
        )
    )

    # Layout: 2 buttons per row
    keyboard = []
    for i in range(0, len(buttons), 2):
        keyboard.append(buttons[i : i + 2])

    # Send info + keyboard
    caption = info.info_text() + "\n⬇️ **Select quality to download:**"

    try:
        await status.delete()
        if info.thumbnail:
            await msg.reply_photo(
                photo=info.thumbnail,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
        else:
            await msg.reply(
                caption,
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
    except Exception:
        # Fallback if thumbnail fails
        await msg.reply(
            caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )


# ─── Quality Selection Callback ─────────────────────────────

@app.on_callback_query(filters.regex(r"^dl:"))
@authorized
async def handle_quality_select(_, cb: CallbackQuery):
    uid = cb.from_user.id
    quality = cb.data.split(":", 1)[1]

    if uid not in pending:
        await cb.answer("❌ Session expired. Send the URL again.", show_alert=True)
        return

    if uid in active_downloads:
        await cb.answer("⏳ Download already in progress!", show_alert=True)
        return

    data = pending.pop(uid)
    url = data["url"]
    info = data["info"]

    await cb.answer(f"⬇️ Starting {quality} download…")

    # Remove keyboard
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Start download
    active_downloads[uid] = True
    progress = DownloadProgress()

    status_msg = await cb.message.reply(
        f"⬇️ **Downloading:** {info.title}\n"
        f"📊 Quality: **{quality.upper()}**\n\n"
        f"{progress.bar()}"
    )

    # Progress updater task
    async def update_progress():
        last_text = ""
        while active_downloads.get(uid):
            await asyncio.sleep(PROGRESS_UPDATE_INTERVAL)
            if progress.status in ("done", "error"):
                break
            text = (
                f"⬇️ **Downloading:** {info.title}\n"
                f"📊 Quality: **{quality.upper()}**\n\n"
                f"{progress.bar()}"
            )
            if text != last_text:
                try:
                    await status_msg.edit_text(text)
                    last_text = text
                except MessageNotModified:
                    pass
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                except Exception:
                    pass

    progress_task = asyncio.create_task(update_progress())

    filepath = None
    try:
        filepath = await download_video(url, quality, uid, progress)

        # Check file size
        fsize = Path(filepath).stat().st_size
        if fsize > MAX_FILE_SIZE:
            await status_msg.edit_text(
                f"❌ **File too large!** ({_sizeof_fmt(fsize)})\n"
                f"Telegram limit is {_sizeof_fmt(MAX_FILE_SIZE)}.\n"
                "Try a lower quality."
            )
            return

        # Cancel check
        if not active_downloads.get(uid):
            await status_msg.edit_text("🛑 **Download cancelled.**")
            return

        await status_msg.edit_text(
            f"📤 **Uploading to Telegram…** ({_sizeof_fmt(fsize)})\n"
            "This may take a moment for large files."
        )

        # Upload
        thumb = None
        if quality == "audio":
            await cb.message.reply_audio(
                audio=filepath,
                caption=f"🎵 {info.title}",
                title=info.title,
                performer=info.uploader,
                progress=_upload_progress(status_msg, info.title),
            )
        else:
            await cb.message.reply_video(
                video=filepath,
                caption=f"🎬 {info.title} [{quality}]",
                duration=info.duration,
                supports_streaming=True,
                progress=_upload_progress(status_msg, info.title),
            )

        await status_msg.edit_text(f"✅ **Done!** {info.title} [{quality}]")
        log.info("Sent %s [%s] to user %s (%s)", info.title, quality, uid, _sizeof_fmt(fsize))

    except asyncio.CancelledError:
        await status_msg.edit_text("🛑 **Download cancelled.**")
    except Exception as e:
        log.exception("Download failed for user %s", uid)
        await status_msg.edit_text(f"❌ **Download failed.**\n\n`{str(e)[:400]}`")
    finally:
        active_downloads.pop(uid, None)
        progress_task.cancel()
        # Cleanup downloaded file
        if filepath and Path(filepath).exists():
            try:
                Path(filepath).unlink()
            except Exception:
                pass


def _upload_progress(status_msg: Message, title: str):
    """Returns a pyrogram upload progress callback."""
    last_update = [0.0]

    async def _progress(current: int, total: int):
        now = time.time()
        if now - last_update[0] < PROGRESS_UPDATE_INTERVAL:
            return
        last_update[0] = now
        pct = current / total * 100 if total else 0
        try:
            await status_msg.edit_text(
                f"📤 **Uploading…** {pct:.1f}%\n"
                f"{_sizeof_fmt(current)} / {_sizeof_fmt(total)}"
            )
        except (MessageNotModified, FloodWait):
            pass
        except Exception:
            pass

    return _progress


# ─── Main ───────────────────────────────────────────────────

if __name__ == "__main__":
    # Ensure required dirs
    Path("./sessions").mkdir(exist_ok=True)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Bot starting…")
    app.run()
