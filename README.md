# 🎬 VDownload Bot

A Telegram bot for downloading videos and audio from YouTube and other platforms, with a FastAPI server for securely sharing large files via expiring HMAC-signed links.

---

## ✨ Features

- Download **MP4** video and **MP3** audio from YouTube
- Additional platforms (TikTok, Instagram, etc.) via **yt-dlp**
- **Automatic fallback** for YouTube: YTStream (RapidAPI) → VDA API
- **Live status messages** during download (rate-limited edits)
- Files over Telegram’s limit → **signed download link** (nginx `X-Accel-Redirect`)
- **Structured errors** (`DownloaderException` hierarchy) and **audit logging**
- Hourly **cleanup** of expired links and old downloads
- **44 unit tests** — no live API calls required

---

## 🏗️ Project Structure

```
├── main.py                     # Entry: webhook bot, API server, cleanup thread
├── config.py                   # Secrets (copy from config.py.example)
├── api_server.py               # GET /VDownload/{file_id}
├── requirements.txt
├── requirements-dev.txt        # pytest, pytest-asyncio, pytest-mock
├── pytest.ini
│
├── docs/
│   └── CODE.md                 # Architecture, errors, logging, tests
│
├── tests/                      # Unit tests (mocked HTTP / FFmpeg / yt-dlp)
│   ├── conftest.py
│   ├── test_yt_dlp_downloader.py
│   ├── test_ytstream_downloader.py
│   ├── test_vda_downloader.py
│   ├── test_download_manager.py
│   ├── test_secure_links.py
│   └── test_url_utils.py
│
├── core/
│   ├── telegram_bot.py         # Telegram handlers only
│   ├── download_manager.py     # Routing + fallback chain
│   ├── download_audit.py       # Structured audit logs
│   ├── secure_links.py         # HMAC links
│   └── status_updater.py       # In-chat progress
│
├── downloaders/                # Strategy pattern
│   ├── base.py                 # BaseDownloader (ABC)
│   ├── progress.py             # ProgressReporter protocol
│   ├── exceptions.py
│   ├── ytstream_downloader.py  # YtstreamDownloader — YouTube primary
│   ├── vda_downloader.py       # VdaDownloader — YouTube fallback
│   └── yt_dlp_downloader.py    # YtDlpDownloader — other platforms
│
└── utils/
    ├── url_utils.py
    └── file_utils.py
```

📖 **Developer docs:** [docs/CODE.md](docs/CODE.md)

---

## 📋 Prerequisites

| Requirement | Purpose |
|-------------|---------|
| Python 3.10+ | `match` statements, modern typing |
| [FFmpeg](https://ffmpeg.org/) on `PATH` | ytstream + yt-dlp MP3 |
| nginx (production) | Webhook proxy + `X-Accel-Redirect` |
| RapidAPI + VDA keys | YouTube providers |

---

## ⚙️ Configuration

```bash
cp config.py.example config.py
# Edit config.py with your tokens and paths
```

| Variable | Description |
|----------|-------------|
| `BOT_TOKEN` | Telegram bot token (@BotFather) |
| `USER_ID` | Allowed Telegram user ID |
| `URL` | Public base URL with trailing slash |
| `SECRET_KEY` | HMAC secret for download links |
| `RAPIDAPI_KEY` | YTStream RapidAPI key |
| `VDA_API_KEY` | VDA fallback API key |
| `DOWNLOAD_DIR` | Downloaded media directory |
| `TEMP_LINKS_DIR` | Signed-link metadata JSON |
| `MAX_SIZE` | Max bytes sent via Telegram (~50MB) |
| `EXPIRY` | Link lifetime in seconds (default 24h) |

---

## 🚀 Getting Started

```bash
pip install -r requirements.txt
ffmpeg -version          # must succeed
cp config.py.example config.py
# edit config.py

pip install -r requirements-dev.txt
pytest                   # optional — 44 tests

python main.py
```

| Component | Port / schedule | Role |
|-----------|-----------------|------|
| Telegram bot (webhook) | 8003 | Commands + downloads |
| FastAPI | 5000 | Secure file links |
| Cleanup thread | hourly | Expired files + metadata |

---

## 🔄 Download Flow

```
User sends URL (/mp3 or /mp4 sets format)
        │
        ▼
YouTube?  →  YtstreamDownloader  →  fail?  →  VdaDownloader
Else      →  YtDlpDownloader
        │
        ▼
find_file(file_id)  →  Telegram send  or  SecureLinkManager
```

---

## 🤖 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Default **MP4** mode |
| `/mp4` | Video mode |
| `/mp3` | Audio mode |

Send a message with `http://` or `https://` to download. Access is limited to `USER_ID` in `main.py`.

---

## 📊 Logging

Audit logger: **`vdownload.audit`** — each request logs user, provider tried, fallback, and outcome.

```
DOWNLOAD_START → PROVIDER_TRY → PROVIDER_FAIL (next=vda) → PROVIDER_OK → DOWNLOAD_OK
```

See [docs/CODE.md](docs/CODE.md#logging) for full event list.

---

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
pytest -v
```

Covers all three downloaders, `download_manager` fallback, secure links, and URL utils. External services are mocked.

---

## 🛠️ Tech Stack

| Package | Role |
|---------|------|
| python-telegram-bot | Webhook bot |
| FastAPI + uvicorn | Download link API |
| yt-dlp | Non-YouTube platforms |
| aiohttp | ytstream + VDA HTTP |
| aiofiles | Async file I/O |
| FFmpeg | Stream merge / transcode |

---

## 📐 Code Conventions

| Item | Convention |
|------|------------|
| Downloader classes | `YtDlpDownloader`, `YtstreamDownloader`, `VdaDownloader` |
| Module files | `snake_case.py` (unchanged) |
| Imports | stdlib → third-party → local |
| Private helpers | Leading `_` (e.g. `_build_request`) |
| Progress API | `await progress.report("...")` via `ProgressReporter` |
