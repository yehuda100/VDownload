"""Generic platform downloader via yt-dlp (runs in a thread pool).

Optional Netscape cookies: set YTDLP_COOKIES_FILE in config.py. When the
setting is missing or empty, downloads run without cookies.
"""
import asyncio
import logging
import os
from urllib.parse import urlparse

import yt_dlp

import config
from config import DOWNLOAD_DIR

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


def is_instagram_auth_failure(url: str, error: BaseException) -> bool:
    """True for Instagram 429 / rate-limit / login-required yt-dlp errors."""
    text = str(error).lower()
    if not _is_instagram_target(url, text):
        return False
    return any(marker in text for marker in _INSTAGRAM_AUTH_MARKERS)


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

    def build_options(self, file_id: str, format_type: str) -> dict:
        opts = self.opts.copy()
        opts["outtmpl"] = f"{DOWNLOAD_DIR}/{file_id}.%(ext)s"
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
        ydl_opts = self.build_options(file_id, format_type)

        def run_download():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as e:
                if is_instagram_auth_failure(url, e):
                    logger.warning(
                        "yt-dlp Instagram rate-limit or login failure: %s", e
                    )
                    raise ExtractionException(instagram_auth_user_message()) from e
                raise ExtractionException(f"Failed to download video: {e}") from e
            except Exception as e:
                raise ExtractionException(f"Unexpected error during download: {e}") from e

        await progress.report("Starting download...")
        info = await asyncio.to_thread(run_download)
        title = info.get("title", "video")
        await progress.report("Download complete.")
        return {"file_id": file_id, "title": title}
