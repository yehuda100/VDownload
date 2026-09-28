# Code Documentation — VDownload

This document describes logical clusters (not every function individually), error handling flow, and logging.

---

## Layer Structure

```
main.py               → Startup: webhook, API, background cleanup
core/telegram_bot     → Telegram UI only (commands, file delivery)
core/download_manager → Strategy selection + fallback
downloaders/*         → Actual downloads (ytstream, VDA, yt-dlp)
core/status_updater   → Live status message in chat
core/secure_links     → Signed links for large files
api_server            → Serves links via nginx (X-Accel-Redirect)
utils/*               → URLs, files, cleanup
```

---

## Clusters & Responsibilities

### `TelegramVideoBot` (`core/telegram_bot.py`)

| Cluster | Functions | Role |
|---------|-----------|------|
| Format setup | `start`, `mp3`, `mp4` | Stores `mp3` / `mp4` in `user_data` |
| URL download | `handle_url` | Extracts URL → calls `download()` → sends file or link |
| Chat delivery | `_send_file` | `reply_audio` / `reply_video`; deletes source message and disk file |
| Access | `no_entry` | Message for unauthorized users (filter is in `main.py`) |

**Does not:** download media, call YouTube APIs, or run FFmpeg.

---

### `download()` (`core/download_manager.py`)

| Condition | Behavior |
|-----------|----------|
| YouTube URL | Chain: **ytstream** → on `DownloaderException` → **VDA** |
| Other URL | **yt-dlp** only, using the user’s `format_type` |

Returns: `{"file_id": str, "title": str}`.

---

### Download strategies (`downloaders/`)

| Class | Platform | Mechanism |
|-------|----------|-----------|
| `YtstreamDownloader` | YouTube (primary) | RapidAPI + FFmpeg (async subprocess) |
| `VdaDownloader` | YouTube (fallback) | External API + polling + `aiofiles` download |
| `YtDlpDownloader` | Generic | `yt-dlp` via `asyncio.to_thread`. Optional Netscape cookies from `YTDLP_COOKIES_FILE` (`cookiefile`). Missing or blank setting means no cookies |

All inherit `BaseDownloader` and report progress through `ProgressReporter.report()`.

---

### `StatusUpdater` (`core/status_updater.py`)

- `initialize()` — sends the initial status message.
- `report()` / `update()` — edits message text (rate-limited to ~1.5s between edits).
- `delete()` — removes the status message when done.

---

### `SecureLinkManager` (`core/secure_links.py`)

- `save_metadata(file_id, filepath, title)` — writes JSON + HMAC signature; returns public URL.
- `verify(file_id, sig)` — validates signature and expiry; returns file path dict or `None`.

---

### `utils`

- **`url_utils`:** `extract_url`, `is_youtube_url`, `extract_youtube_id`
- **`file_utils`:** `find_file(file_id)` — newest `{DOWNLOAD_DIR}/{file_id}.*`; `cleanup()` — removes expired files and link metadata

---

## Error Handling

### Hierarchy (`downloaders/exceptions.py`)

```
DownloaderException          ← base for all download failures
├── APIException             ← HTTP / external API
├── ProgressException        ← progress tracking (timeout, stall, etc.)
├── DownloadException        ← fetching the final file
├── InvalidURLException
├── StreamNotFoundException  ← no suitable stream (ytstream)
├── FFmpegException
└── ExtractionException      ← yt-dlp
```

### Flow

1. **In downloaders** — on failure, `raise` the appropriate type (errors are not swallowed).
2. **In `download_manager`** — strategy fails → `logger.warning` + try next; all fail → `logger.error` + re-raise last error.
3. **In `telegram_bot`** — `DownloaderException` → user message + `logger.warning`; anything else → generic user message + `logger.exception` (with traceback).

Instagram `HTTP 429`, `rate-limit reached or login required`, and the empty-media login error are rewritten before they reach Telegram. The chat message tells the owner to export a Netscape cookies file and set `YTDLP_COOKIES_FILE` (or to refresh the file when one is already configured). The raw yt-dlp text is logged at `WARNING` on `downloaders.yt_dlp_downloader`. Other yt-dlp failures keep the original `Failed to download video: ...` message.

Minor Telegram errors (edit/delete status message) use `logger.debug` only and do not abort the download.

---

## Logging

### Audit logger (`vdownload.audit`)

Structured lines for every download and fallback step:

| Event | Meaning |
|-------|---------|
| `DOWNLOAD_START` | User requested a URL (`user_id`, `username`, `format`, `url`) |
| `PROVIDER_TRY` | Module about to run (`ytstream`, `vda`, `yt-dlp`) |
| `PROVIDER_FAIL` | Module failed; `next=` shows the fallback module (or `none`) |
| `PROVIDER_OK` | Module succeeded |
| `DOWNLOAD_OK` | Full flow succeeded (`delivery=telegram` or `secure_link`) |
| `DOWNLOAD_FAIL` | Failed (`stage=download`, `file_lookup`, `telegram_send`, …) |
| `ACCESS_DENIED` | Unauthorized Telegram user |
| `LINK_OK` / `LINK_FAIL` | Secure link hit (`client_ip`, `file_id`) |

Example fallback chain:

```
PROVIDER_TRY  | user_id=123 ... | provider=ytstream | platform=youtube
PROVIDER_FAIL | ... | provider=ytstream | error=... | next=vda
PROVIDER_TRY  | ... | provider=vda | platform=youtube
PROVIDER_OK   | ... | provider=vda | file_id=...
DOWNLOAD_OK   | ... | provider=vda | delivery=telegram
```

### Other loggers

| Level | When | Where |
|-------|------|-------|
| `WARNING` / `ERROR` | Same events, module loggers | `telegram_bot`, `api_server` |
| `DEBUG` | Status message edit/delete failed | `status_updater` |
| `ERROR` | Background cleanup failure | `main.py` |

Default: `logging.INFO` in `main.py` (`configure_logging`). For verbose diagnostics: `logging.DEBUG`.

### Token redaction

`httpx` logs each Telegram call at INFO with the full URL (`/bot<token>/method`). `configure_logging()` raises `httpx` and `httpcore` to WARNING so those request lines are not emitted. WARNING and ERROR from those loggers still pass. A redacting filter on the application handler (and on the `httpx` logger) replaces bot tokens, including inside tracebacks, with `<REDACTED>`.

Audit `error=` fields use `format_error()`: the exception text, or the exception type name when that text is empty (`TimeoutError`).

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

| File | Coverage |
|------|----------|
| `tests/test_yt_dlp_downloader.py` | `build_options`, optional `cookiefile`, success, `ExtractionException`, Instagram rate-limit/login messages |
| `tests/test_ytstream_downloader.py` | stream selection, API/FFmpeg errors, full `download()` |
| `tests/test_vda_downloader.py` | polling, secondary API, progress UI, stall, file fetch |
| `tests/test_download_manager.py` | fallback chain, YouTube vs generic routing |
| `tests/test_secure_links.py` | HMAC save/verify/expiry |
| `tests/test_url_utils.py` | URL parsing helpers |
| `tests/test_logging.py` | Token redaction and non-empty audit errors |
| `tests/test_ci_workflow.py` | CI workflow triggers, deploy gated to `V2.0` push/dispatch, deploy script safety |

External APIs, FFmpeg, and Telegram are **mocked** — no network required.

---

## Not Logged (by design)

- Successful downloads — avoids log noise.
- Full FFmpeg stdout — only stderr on failure, inside `FFmpegException`.
