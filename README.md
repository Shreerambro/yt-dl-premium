# 🎬 YouTube Members-Only Downloader Bot

Telegram bot that downloads YouTube videos — including **members-only** content — with quality selection, progress tracking, and one-click cloud deploy.

## Features

- 📹 **Quality Selection** — Pick from 144p to 4K, or audio-only MP3
- 🔒 **Members-Only Support** — Upload cookies to access member/paid content
- 📊 **Live Progress Bar** — Real-time download + upload progress
- 📦 **Large Files** — Up to 2GB via Telegram MTProto (Pyrogram)
- ☁️ **One-Click Deploy** — Heroku & Render buttons below
- 🔐 **User Whitelist** — Restrict access to specific Telegram user IDs

## Deploy

### Heroku

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/Shreerambro/yt-dl-premium)

### Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Shreerambro/yt-dl-premium)



## Setup

### Prerequisites

- Python 3.10+
- ffmpeg installed (`choco install ffmpeg` on Windows, `apt install ffmpeg` on Linux)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)
- Bot token from [@BotFather](https://t.me/BotFather)

### Local Setup

```bash
# Clone
git clone https://github.com/Shreerambro/yt-dl-premium.git
cd yt-dl-premium

# Install deps
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your credentials

# Run
python bot.py
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `API_ID` | ✅ | Telegram API ID from my.telegram.org |
| `API_HASH` | ✅ | Telegram API Hash from my.telegram.org |
| `BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `AUTH_USERS` | ❌ | Comma-separated user IDs (empty = public) |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/help` | Usage guide |
| `/cookies` | Upload cookies.txt for members-only |
| `/clearcookies` | Remove saved cookies |
| `/cancel` | Cancel active download |

## How Members-Only Works

1. Install [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) browser extension
2. Go to YouTube, log in with the account that has the membership
3. Click the extension → Export cookies
4. Send `/cookies` to the bot and upload the exported file
5. Now send any members-only video link!

## Architecture

```
yt-dl-bot/
├── bot.py              # Telegram bot (Pyrogram)
├── downloader.py       # yt-dlp wrapper + progress tracking
├── config.py           # Environment config
├── requirements.txt    # Python dependencies
├── Procfile            # Heroku worker
├── app.json            # Heroku deploy button config
├── render.yaml         # Render deploy config
├── runtime.txt         # Python version
├── .env.example        # Example environment file
└── .gitignore          # Git ignore rules
```

## License

MIT
