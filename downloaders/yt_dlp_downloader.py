"""Generic platform downloader via yt-dlp (runs in a thread pool).

Optional Netscape cookies: set YTDLP_COOKIES_FILE in config.py. When the
setting is missing or empty, downloads run without cookies.
"""
import asyncio
import logging
import os
import time
from urllib.parse import urlparse

import yt_dlp

import config
from config import DOWNLOAD_DIR
from utils import is_youtube_url
from utils.file_utils import remove_partial_downloads

from .base import BaseDownloader
from .exceptions import ExtractionException
from .progress import ProgressReporter

logger = logging.getLogger(__name__)

# Phrases yt-dlp uses when Instagram answers 429 or demands a logged-in session.
_INSTAGRAM_AUTH_MARKERS = (
    "429",
    "too many requests",
    "rate-limit",
    "rate limit",
    "login required",
    "cookies-from-browser",
    "empty media response",
)


def configured_proxy() -> str:
    """Return the proxy URL from config, or "" when unset or blank."""
    raw = getattr(config, "YTDLP_PROXY", None)
    if raw is None:
        return ""
    return str(raw).strip()


def configured_cookies_path() -> str:
    """Return the cookies path from config, or "" when unset or blank.

    Missing attribute is treated as unset so older config.py files keep working.
    """
    raw = getattr(config, "YTDLP_COOKIES_FILE", None)
    if raw is None:
        return ""
    return str(raw).strip()


def usable_cookies_file() -> str | None:
    """Absolute cookies path when the configured file exists, else None."""
    path = configured_cookies_path()
    if path and os.path.isfile(path):
        return os.path.abspath(path)
    return None


def _is_instagram_target(url: str, error_text: str) -> bool:
    if "instagram" in error_text:
        return True
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host == "instagram.com" or host.endswith(".instagram.com")


def is_youtube_transient_download_error(error: BaseException) -> bool:
    """True for YouTube bot-check or HTTP 403 errors that often succeed on retry."""
    text = str(error).lower()
    if "video unavailable" in text:
        return False
    if "sign in to confirm" in text or "not a bot" in text:
        return True
    if "http error 403" in text:
        return True
    if "403" in text and "forbidden" in text:
        return True
    return False


def is_instagram_auth_failure(url: str, error: BaseException) -> bool:
    """True for Instagram 429 / rate-limit / login-required yt-dlp errors."""
    text = str(error).lower()
    if not _is_instagram_target(url, text):
        return False
    return any(marker in text for marker in _INSTAGRAM_AUTH_MARKERS)


def _schedule_progress(progress: ProgressReporter, text: str) -> None:
    try:
        asyncio.create_task(progress.report(text))
    except RuntimeError:
        logger.debug("No running loop for yt-dlp progress", exc_info=True)


def _yt_dlp_progress_hook(loop: asyncio.AbstractEventLoop, progress: ProgressReporter):
    """Coalesce worker-thread progress callbacks onto the bot loop."""
    from core.messages import format_byte_progress

    pending: dict[str, object] = {"text": None, "scheduled": False}

    def hook(data: dict) -> None:
        if data.get("status") != "downloading":
            return
        text = format_byte_progress(
            data.get("downloaded_bytes") or 0,
            data.get("total_bytes") or data.get("total_bytes_estimate"),
            data.get("speed"),
        )
        if not text:
            return
        pending["text"] = text
        if pending["scheduled"]:
            return
        pending["scheduled"] = True

        def flush() -> None:
            pending["scheduled"] = False
            latest = pending["text"]
            if isinstance(latest, str):
                _schedule_progress(progress, latest)

        try:
            loop.call_soon_threadsafe(flush)
        except RuntimeError:
            pending["scheduled"] = False

    return hook


def instagram_auth_user_message() -> str:
    """Short Telegram message. Export steps live in the README."""
    path = configured_cookies_path()
    if path and os.path.isfile(path):
        return (
            "Instagram blocked this download (rate limit or login required). "
            "The cookies file was used, but Instagram still rejected the session. "
            "Export a fresh Netscape cookies file while logged into Instagram "
            "and replace the file set in YTDLP_COOKIES_FILE."
        )
    if path:
        return (
            "Instagram blocked this download (rate limit or login required). "
            f"YTDLP_COOKIES_FILE points to {path}, which was not found. "
            "Add a Netscape cookies file at that path, or update config.py and restart the bot."
        )
    return (
        "Instagram blocked this download (rate limit or login required). "
        "Export a Netscape cookies file from a browser logged into Instagram, "
        "set YTDLP_COOKIES_FILE in config.py, and restart the bot."
    )


class YtDlpDownloader(BaseDownloader):

    def __init__(self):
        self.opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

    def build_options(self, file_id: str, format_type: str, url: str = "") -> dict:
        opts = self.opts.copy()
        opts["outtmpl"] = f"{DOWNLOAD_DIR}/{file_id}.%(ext)s"
        proxy = configured_proxy()
        if proxy and url and is_youtube_url(url):
            opts["proxy"] = proxy
        if format_type == "mp3":
            opts.update({
                "format": "bestaudio/best",
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
                ],
            })
        else:
            opts.update({
                "format": (
                    "(bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
                    "/best[ext=mp4][height<=720]"
                    "/bestvideo[height<=720]+bestaudio"
                    "/best)"
                ),
                "merged_output_format": "mp4",
            })
        configured = configured_cookies_path()
        cookies = usable_cookies_file()
        if cookies:
            opts["cookiefile"] = cookies
        elif configured:
            logger.warning(
                "YTDLP_COOKIES_FILE is set but the file was not found: %s",
                configured,
            )
        return opts

    async def download(
        self, url: str, format_type: str, progress: ProgressReporter
    ) -> dict:
        file_id = self.generate_file_id()
        ydl_opts = self.build_options(file_id, format_type, url)
        max_attempts = 2 if is_youtube_url(url) else 1

        def run_download():
            last_error: BaseException | None = None
            for attempt in range(max_attempts):
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        return ydl.extract_info(url, download=True)
                except yt_dlp.utils.DownloadError as e:
                    last_error = e
                    if is_instagram_auth_failure(url, e):
                        logger.warning(
                            "yt-dlp Instagram rate-limit or login failure: %s", e
                        )
                        raise ExtractionException(
                            instagram_auth_user_message()
                        ) from e
                    if (
                        attempt + 1 < max_attempts
                        and is_youtube_transient_download_error(e)
                    ):
                        logger.warning(
                            "yt-dlp YouTube transient error (retrying): %s", e
                        )
                        remove_partial_downloads(file_id)
                        time.sleep(2)
                        continue
                    raise ExtractionException(
                        f"Failed to download video: {e}"
                    ) from e
                except Exception as e:
                    raise ExtractionException(
                        f"Unexpected error during download: {e}"
                    ) from e
            if last_error is not None:
                raise ExtractionException(
                    f"Failed to download video: {last_error}"
                ) from last_error
            raise ExtractionException("Failed to download video")

        from core.messages import DOWNLOADING

        loop = asyncio.get_running_loop()
        ydl_opts["progress_hooks"] = [_yt_dlp_progress_hook(loop, progress)]
        await progress.report(DOWNLOADING)
        try:
            info = await asyncio.to_thread(run_download)
        except Exception:
            remove_partial_downloads(file_id)
            raise
        title = info.get("title", "video")
        return {"file_id": file_id, "title": title}
