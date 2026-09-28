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
- **65 unit tests** — no live API calls required

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
| `YTDLP_COOKIES_FILE` | Optional Netscape cookies file for yt-dlp. Leave `""` to download without cookies |

---

## Instagram cookies (optional)

Instagram often answers the server with `HTTP Error 429: Too Many Requests` or `rate-limit reached or login required`. yt-dlp can send a logged-in browser session when `YTDLP_COOKIES_FILE` points at a **Netscape** cookies file. Leave the setting as `""` (or omit it in an older `config.py`) and every download runs without cookies, same as before.

The file is passed on every yt-dlp download (Instagram, Facebook, TikTok, and others). The cookie jar only sends cookies that match the request domain, so an Instagram-only file does not log other sites in.

### Export a cookies file

On a computer where you can log into Instagram (the server itself does not need a browser):

1. Install the browser extension **[Get cookies.txt LOCALLY](https://github.com/kairi003/Get-cookies.txt-LOCALLY)** (Chrome, Firefox, or Edge). It writes a Netscape cookies file on your machine. Avoid extensions that upload cookies to a third-party site.
2. Log in at [instagram.com](https://www.instagram.com/). A spare account is safer than your personal one, because the bot will use that session.
3. Open the extension on the Instagram tab and export. Save the file as `cookies.txt`. The first line is `# Netscape HTTP Cookie File`.
4. Copy it to the server, outside git, for example `/var/lib/vdownload/instagram-cookies.txt`.
5. Restrict permissions: `chmod 600 /var/lib/vdownload/instagram-cookies.txt`.

The same export can be done with yt-dlp on that computer, after you are logged into Instagram in Chrome:

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://www.instagram.com/"
```

Then upload `cookies.txt` to the server. `--cookies-from-browser` only works where that browser profile exists; the bot itself only reads the file via `--cookies`.

### Configure

In `config.py` (this file is gitignored):

```python
YTDLP_COOKIES_FILE = "/var/lib/vdownload/instagram-cookies.txt"
```

Restart the bot after changing `config.py`. yt-dlp reads the file on each download, so replacing the file at the same path is picked up on the next request.

Cookies expire. When Instagram rejects the session again, export a fresh file and replace the one on the server.

If a download still fails, the Telegram status message says which case it is: cookies were not configured, the path does not exist, or the file was sent and Instagram still rejected it.

`cookies.txt`, `*cookies*.txt`, `*.cookies`, and the `cookies/` directory are gitignored. Do not commit the file or paste it into chat.

---

## 🚀 Getting Started

```bash
pip install -r requirements.txt
ffmpeg -version          # must succeed
cp config.py.example config.py
# edit config.py

pip install -r requirements-dev.txt
pytest                   # optional — 65 tests

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
