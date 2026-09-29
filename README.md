# VDownload Bot

A single-user Telegram bot that downloads video (MP4) or audio (MP3) from a link. Small files are uploaded into the chat. Files at or above the size limit, and files Telegram refuses to accept, are handed out as an expiring HMAC-signed download link.

The process runs two local services: the bot webhook, and a FastAPI server that nginx uses to send those large files.

## Platforms

| Link | What runs |
|------|-----------|
| YouTube (`youtube.com`, `youtu.be`, `music.youtube.com`, including `m.` and `www.`) | **yt-dlp**, then **VDA** if yt-dlp fails |
| Anything else yt-dlp supports (Instagram, TikTok, Facebook, and others) | **yt-dlp only** |

yt-dlp is the only provider for non-YouTube links. A `DownloaderException` from that call is the final result.

Video is capped around 720p and saved as MP4 when the downloader can merge it. Audio is MP3. `/start` and `/mp4` select video (the default). `/mp3` selects audio. Only the Telegram user in `USER_ID` can use the bot.

`downloaders/ytstream_downloader.py` still contains a RapidAPI YTStream downloader. The live YouTube chain leaves it unused.

## Provider chain

### YouTube

1. **yt-dlp** (optional `YTDLP_PROXY`, YouTube only). On a bot check (`Sign in to confirm you're not a bot`) or HTTP 403, it deletes the partial file, waits 2 seconds, and tries **once** more. `Video unavailable` is not retried.
2. If yt-dlp still raises, **VDA**. VDA calls `https://p.savenow.to` and, if that response has no progress URL, `https://p.lbserver.xyz`. It then polls (up to 10 minutes) and streams the finished file. MP3 or 720p.

If both providers fail, the chat gets a short English error. The raw exception stays in the log.

### Other sites

yt-dlp once, with no proxy and no automatic retry. Instagram rate-limit and login errors are rewritten into a cookies hint (see below). Cookies from `YTDLP_COOKIES_FILE`, when the file exists, are passed on every yt-dlp download. The jar only sends cookies for the matching domain.

## Features

### One status message

Each link gets a single chat message, edited in place. Edits are at least 1.5 seconds apart, so a fast progress stream cannot starve the update. While another download holds the lock, the message says the new link is next. It then shows `Downloading...` with size, total, and speed when those are known, or VDA's prepare percent. A Telegram upload switches the same message to `Sending to Telegram...` and deletes it after the file is accepted. A signed link replaces that message and stays in the chat.

### Queue

One download runs at a time (`asyncio.Lock`). The URL handler does not block the bot, so a second link is accepted immediately and shows `Still downloading the previous link. This one is next.` It starts when the lock is free. The queue lives in the process; a restart drops anything still waiting.

### Size-based delivery

`MAX_SIZE` defaults to 50 MB.

- **Smaller than `MAX_SIZE`:** upload with `reply_video` (streaming) or `reply_audio`. Write and media-write timeouts are **120 seconds** (the library default media timeout is 20 seconds, which drops large uploads).
- **`MAX_SIZE` or larger:** skip the upload. The status message becomes a signed link.
- **Upload fails** (timeout or any other send error): keep the file and send the same kind of signed link. If writing the link metadata also fails, the chat says the send failed and does not include a URL.

A successful Telegram upload deletes the user's source message and the file on disk. A link delivery leaves the file on disk for the hourly cleanup.

### Signed links

`SecureLinkManager` writes `{TEMP_LINKS_DIR}/{file_id}.json` (owner-only, replaced atomically) and returns:

```text
{URL}VDownload/{file_id}?sig={hmac}
```

The HMAC-SHA256 covers the title, file id, expiry, and file path, keyed by `SECRET_KEY`. `file_id` must be a UUID. Links issued before the path was part of the MAC still verify until they expire. `GET /VDownload/{file_id}` checks the signature and expiry, then answers with `X-Accel-Redirect: /protected_downloads/{filename}` and a `Content-Disposition` filename. A bad signature, a non-UUID id, or a missing file is a 404. Opening an expired link deletes its metadata.

Lifetime is `EXPIRY` (default 86400 seconds, 24 hours). The chat text states when the link expires.

### Cleanup

A daemon thread calls `cleanup()` once an hour:

- Expired link metadata is removed, and the media file is deleted when it sits inside `DOWNLOAD_DIR`.
- Other files in `DOWNLOAD_DIR` older than 36 hours are removed.
- A failed download deletes its `{file_id}.*` partials. Paths outside `DOWNLOAD_DIR` are not deleted.

## Architecture

`python main.py` starts all of this in one process:

| Piece | Bind | Role |
|-------|------|------|
| Telegram webhook | `127.0.0.1:8003` | Commands and downloads. Public path `/telegram-webhook` (below) |
| FastAPI (`api_server.py`) | `127.0.0.1:5000` | `GET /VDownload/{file_id}`. Access log off with the webhook change, so `sig=` is not written there |
| Cleanup thread | hourly | Expired links and stale files |

Neither port is meant to face the internet. nginx terminates TLS and proxies both paths. The API and cleanup thread are daemons; the webhook runs on the main thread.

**Webhook (requires nginx config).** The bot registers `https://your-domain.com/telegram-webhook` and sets Telegram's `secret_token`. Telegram sends that value in `X-Telegram-Bot-Api-Secret-Token`. The path is fixed so the bot token is not part of the URL. The same change turns the uvicorn access log off, so signed-link query strings are not logged by the API process. **`WEBHOOK_SECRET` is optional.** When it is unset, or is not 1–256 characters from `A-Za-z0-9_-`, the process derives a 32-character secret from `BOT_TOKEN`, and an older `config.py` still starts. Point nginx at `/telegram-webhook` when this build is deployed. Until that process is running, an older build still registers a webhook URL that ends in the bot token and still writes the API access log.

Each request is also written to the `vdownload.audit` logger (`DOWNLOAD_START`, `PROVIDER_TRY`, `PROVIDER_FAIL`, `PROVIDER_OK`, `DOWNLOAD_OK` / `DOWNLOAD_FAIL`). Bot tokens are redacted from log lines.

## Configuration

```bash
cp config.py.example config.py
```

`config.py` is gitignored. Do not commit it.

| Key | Meaning |
|-----|---------|
| `BOT_TOKEN` | Telegram bot token from BotFather. |
| `USER_ID` | The only Telegram user id allowed to download. |
| `URL` | Public base URL, **with a trailing slash** (`https://your-domain.com/`). Used for the webhook and for signed links. |
| `WEBHOOK_SECRET` | Optional. Value Telegram must echo as `secret_token`. 1–256 characters from `A-Za-z0-9_-` only. Leave it unset to derive one from `BOT_TOKEN`. The webhook path is `/telegram-webhook` either way. |
| `SECRET_KEY` | HMAC key for download links. Use a long random string. |
| `RAPIDAPI_KEY` | Key for the YTStream downloader left in the tree. The live YouTube chain leaves it unread. |
| `VDA_API_KEY` | API key for the VDA YouTube fallback. |
| `DOWNLOAD_DIR` | Directory for media files. Relative paths are inside the working directory. |
| `TEMP_LINKS_DIR` | Directory for signed-link JSON. |
| `MAX_SIZE` | Max bytes uploaded to Telegram. Default `50 * 1024 * 1024`. At or above this, the bot sends a link. |
| `EXPIRY` | Signed-link lifetime in seconds. Default `24 * 3600`. |
| `YTDLP_COOKIES_FILE` | Optional Netscape cookies file for yt-dlp. `""` means no cookies. Used for Instagram rate limits. |
| `YTDLP_PROXY` | Optional proxy URL for **YouTube yt-dlp only**, for example `socks5://127.0.0.1:40000`. Other sites ignore it. `""` means a direct connection. |

## Instagram cookies

Optional. Leave `YTDLP_COOKIES_FILE = ""` to download without cookies.

Instagram often answers a server with HTTP 429 or "login required". A Netscape cookies file from a logged-in browser lets yt-dlp send that session. The same file is passed for every yt-dlp site; cookies are only attached when the domain matches.

1. On a computer where you can log in (the server does not need a browser), install [Get cookies.txt LOCALLY](https://github.com/kairi003/Get-cookies.txt-LOCALLY). Prefer an extension that writes the file locally.
2. Log in at instagram.com. A spare account is safer than a personal one.
3. Export. The first line should be `# Netscape HTTP Cookie File`.
4. Copy it to the server, outside git, and restrict it: `chmod 600 /var/lib/vdownload/instagram-cookies.txt`.
5. Set `YTDLP_COOKIES_FILE` to that path and restart the bot.

The same export can be made with yt-dlp where the browser profile exists:

```bash
yt-dlp --cookies-from-browser chrome --cookies cookies.txt --skip-download "https://www.instagram.com/"
```

The bot reads that file through yt-dlp's `cookiefile` option. Replacing the file at the same path is picked up on the next download. Cookies expire; export a new file when Instagram rejects the session.

The status text says which case it is: no cookies configured, the path does not exist, or the file was sent and Instagram still rejected it.

`cookies.txt`, `*cookies*.txt`, `*.cookies`, and `cookies/` are gitignored.

## YouTube proxy (Cloudflare WARP)

Optional. A datacenter IP often gets a YouTube bot check. WARP in **proxy-only** mode can help, but its exit addresses are shared, and YouTube bot-checks those too. It may work for a while and then start failing. Reconnecting for a new IP often does not clear it.

When yt-dlp fails, the bot retries once and then uses VDA. YouTube still downloads, only slower.

The most reliable way to stay on yt-dlp is a Netscape cookies file from a secondary (throwaway) Google account, set as `YTDLP_COOKIES_FILE`. That account may get flagged. Export it the same way as the Instagram cookies above. One file is enough for both.

```bash
warp-cli registration new
warp-cli mode proxy
warp-cli proxy port 40000
warp-cli connect
```

Then in `config.py`:

```python
YTDLP_PROXY = "socks5://127.0.0.1:40000"
```

Restart the bot. The proxy applies only to YouTube URLs. Other sites stay direct.

**Do not use full-tunnel mode on a server.** `warp-cli mode warp` sends all of the machine's traffic through Cloudflare, including SSH and the webhook, and can lock you out. Proxy mode only listens on the local SOCKS port.

## Setup and running

Python 3.10 or newer (CI uses 3.12). [FFmpeg](https://ffmpeg.org/) must be on `PATH`: yt-dlp uses it to merge video and to extract MP3. VDA returns a finished file and does not call FFmpeg.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
ffmpeg -version
cp config.py.example config.py
# edit config.py
python main.py
```

On the production host the virtualenv is `bot3/` inside `/var/www/yehuda100/bot3`, and the process runs in a [screen](https://www.gnu.org/software/screen/) session named `Bot3`:

```bash
cd /var/www/yehuda100/bot3
source bot3/bin/activate
screen -S Bot3
python3 main.py
```

Detach with `Ctrl-A` then `D`. Reattach with `screen -r Bot3`. `scripts/deploy.sh` restarts that same session; it does not create the virtualenv or edit `config.py`.

## nginx

`URL` must be the HTTPS origin in front of this server. Replace `your-domain.com` and the download alias with your own paths. The alias directory is `DOWNLOAD_DIR`. Keep the trailing slashes on `location` and `alias` matched.

```nginx
server {
    server_name your-domain.com;

    # Telegram webhook. Required with the fixed path /telegram-webhook.
    location /telegram-webhook {
        proxy_pass http://127.0.0.1:8003;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Signed download links (FastAPI). No trailing slash on proxy_pass:
    # the /VDownload/ prefix has to reach the app.
    location /VDownload/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # X-Accel-Redirect target. internal: not reachable from the internet.
    location /protected_downloads/ {
        internal;
        alias /var/www/yehuda100/bot3/downloads/;
    }
}
```

Once the API access log is off, uvicorn will not record `sig=`. If nginx logs `/VDownload/` requests, the query string is still in that log. Turn that location's access log off if you do not want signatures stored there.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

`requirements-dev.txt` pulls in `requirements.txt`, pytest, pytest-asyncio, pytest-mock, and PyYAML. `tests/conftest.py` installs a fake `config` module, so the suite does not read a real `config.py` and does not call Telegram, yt-dlp, VDA, or RapidAPI. Coverage includes the downloaders, the YouTube fallback, signed links, the status message, URL and file helpers, logging, and the CI/deploy workflow.

## CI and auto-deploy

Workflow: [`.github/workflows/ci-deploy.yml`](.github/workflows/ci-deploy.yml) (`CI and Deploy`), Python 3.12. The test job also runs shellcheck on `scripts/deploy.sh`.

| Event | Tests | Deploy |
|-------|-------|--------|
| Pull request targeting `V2.0` | yes | no |
| Push to `V2.0` (including a merge) | yes | yes, after tests pass |
| **Run workflow** on branch `V2.0` | yes | yes, after tests pass |

The deploy job SSHes to the server and fast-forwards `/var/www/yehuda100/bot3` to `origin/V2.0`, then runs `scripts/deploy.sh`. That script installs `requirements.txt` with `bot3/bin/pip` only when the file changed, restarts `python3 main.py` in the `Bot3` screen session, and checks that the process is still up. It stops without changing the checkout if tracked files are dirty or the pull cannot fast-forward. `config.py` and the virtualenv are left as they are.

Repository secrets used by that job, by name:

- `SERVER_HOST`
- `SERVER_USER`
- `DEPLOY_KEY`

They live under **Settings → Secrets and variables → Actions**. Do not commit the host, the key, or any token.

## Troubleshooting

### YouTube bot check

yt-dlp says `Sign in to confirm you're not a bot`, or the download dies with HTTP 403. The bot retries once, then falls back to VDA, so the video still arrives, only slower. `Video unavailable` skips the retry.

A local WARP SOCKS proxy (`YTDLP_PROXY`, for example `socks5://127.0.0.1:40000`, proxy mode only) can help. WARP's shared IPs are often bot-checked as well, and a fresh exit IP often does not fix it. The most reliable yt-dlp option is `YTDLP_COOKIES_FILE` from a secondary (throwaway) Google account. That account may get flagged.

### Instagram 429 and cookies

The chat reports a rate limit or login requirement, and names whether `YTDLP_COOKIES_FILE` is empty, missing on disk, or already in use. Export a fresh Netscape file while logged in, put it at the configured path, `chmod 600` it, and restart only if you changed `config.py`. A file replaced at the same path is read on the next download. Wait before retrying a bare 429 with no cookies; the site is limiting the server IP. Cookies are not applied to VDA, and VDA is not used for Instagram.

### Large-file timeouts

Telegram uploads use a 120 second write timeout. A file that does not finish in that window, or any other failed upload, stays on disk and the chat receives a signed link instead of a hard failure. Files at or above `MAX_SIZE` never attempt the upload.

If the link returns 404, the signature is wrong, the link is past `EXPIRY`, or the hourly cleanup already removed it (expired metadata, or any download older than 36 hours). With the API access log off, check nginx to see whether the request arrived. VDA's own file transfer has no total time limit, but it fails if the socket is silent for 60 seconds or the prepare step stalls for 30 seconds. That provider failure stops the download before a link is built.

## Layout

```text
main.py                  webhook, API thread, cleanup thread
api_server.py            GET /VDownload/{file_id}
config.py.example
scripts/deploy.sh
core/                    bot, download routing, status, signed links, audit
downloaders/             yt-dlp, VDA, and the unused YTStream downloader
utils/                   URLs, files, cleanup
tests/
```
