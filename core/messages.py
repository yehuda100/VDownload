"""User-facing English copy for the Telegram bot.

Titles from the video site are passed through as-is. Everything the bot
itself writes stays in English.
"""
from __future__ import annotations

START_TEXT = (
    "🎬 Send a link and I'll download it as video.\n"
    "\n"
    "/mp4 — video (default)\n"
    "/mp3 — audio"
)
MP4_TEXT = "🎬 Video mode. Send a link."
MP3_TEXT = "🎵 Audio mode. Send a link."

MENU_START = "How to download"
MENU_MP3 = "Download audio"
MENU_MP4 = "Download video"

NO_LINK_HINT = "Send a link. /mp3 is audio, /mp4 is video."
NOT_AVAILABLE = "🚫 This bot is not available for you."

DOWNLOADING = "Downloading..."
WAITING_FOR_PREVIOUS = "Still downloading the previous link. This one is next."
SENDING = "Sending to Telegram..."

UNSUPPORTED = "❌ Couldn't use this link. Send a video or post URL."
NO_VIDEO = "❌ This post has no video."
PRIVATE = "❌ This video is private, so I can't download it."
UNAVAILABLE = "❌ This video isn't available. It may have been removed or blocked."
RATE_LIMIT = (
    "❌ The site is limiting downloads right now. "
    "Wait a few minutes and send the link again."
)
TIMEOUT = "❌ The download took too long and was stopped. Send the link again."
STALLED = "❌ The download stalled. Send the link again."
FILE_NOT_SAVED = "❌ The file didn't save. Send the link again."
SEND_FAILED = (
    "❌ The file downloaded, but sending it on Telegram failed. Send the link again."
)
GENERIC_FAIL = "❌ Couldn't download this link. Send it again."
UNEXPECTED = "❌ Something went wrong. Send the link again in a little while."

_MB = 1024 * 1024


def format_prepare_percent(percent: float) -> str:
    """Whole-number percent for the remote prepare step."""
    shown = max(0, min(100, round(percent)))
    return f"Preparing the file... {shown}%"


def format_byte_progress(
    downloaded: float,
    total: float | None,
    speed: float | None,
) -> str | None:
    """Size and speed line. Returns None when there is nothing useful to show."""
    if downloaded <= 0:
        return None
    shown_downloaded = downloaded / _MB
    shown_total = (total / _MB) if total and total > 0 else None
    if shown_downloaded < 0.05 and (shown_total is None or shown_total < 0.05):
        return None
    text = f"Downloading... {shown_downloaded:.1f}"
    if shown_total is not None:
        text += f" / {shown_total:.1f} MB"
    else:
        text += " MB"
    if speed and speed > 0:
        shown_speed = speed / _MB
        if shown_speed >= 0.05:
            text += f" · {shown_speed:.1f} MB/s"
    return text


def format_link_expiry(seconds: int) -> str:
    if seconds > 0 and seconds % 3600 == 0:
        hours = seconds // 3600
        unit = "hour" if hours == 1 else "hours"
        return f"{hours} {unit}"
    if seconds > 0 and seconds % 60 == 0:
        minutes = seconds // 60
        unit = "minute" if minutes == 1 else "minutes"
        return f"{minutes} {unit}"
    return f"{seconds} seconds"


def too_big_message(
    size_bytes: int,
    title: str,
    link: str,
    expiry_seconds: int | None = None,
) -> str:
    if expiry_seconds is None:
        from config import EXPIRY

        expiry_seconds = int(EXPIRY)
    mb = size_bytes / _MB
    expiry = format_link_expiry(int(expiry_seconds))
    return (
        f"This file is {mb:.1f} MB, which is too big to send here.\n"
        f"\n"
        f"{title}\n"
        f"{link}\n"
        f"\n"
        f"Open the link to download it. It expires in {expiry}."
    )


def message_for_exception(exc: BaseException) -> str:
    """Short chat line for a download failure. The raw exception stays in the log."""
    from downloaders.exceptions import (
        DownloaderException,
        InvalidURLException,
        ProgressStalledException,
    )

    text = str(exc).strip()
    lower = text.lower()

    if "Instagram blocked this download" in text:
        return f"❌ {text}"
    if isinstance(exc, InvalidURLException) or "Unsupported URL" in text:
        return UNSUPPORTED
    if "There is no video in this post" in text:
        return NO_VIDEO
    if "Private video" in text:
        return PRIVATE
    if "video unavailable" in lower or "this video is unavailable" in lower:
        return UNAVAILABLE
    if isinstance(exc, ProgressStalledException):
        return STALLED
    if isinstance(exc, TimeoutError) or "timed out" in lower:
        return TIMEOUT
    if (
        "too many requests" in lower
        or "rate-limit" in lower
        or "rate limit" in lower
    ):
        return RATE_LIMIT
    if isinstance(exc, DownloaderException):
        return GENERIC_FAIL
    return UNEXPECTED
