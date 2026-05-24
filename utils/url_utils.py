"""URL extraction, validation, and YouTube-specific parsing."""
import re
from urllib.parse import parse_qs, urlparse

_URL_PATTERN = re.compile(r"(https?://[^\s]+)")


def is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.scheme and parsed.netloc)


def extract_url(message: str) -> str | None:
    match = _URL_PATTERN.search(message)
    return match.group(0) if match else None


def is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    return host in ("youtube.com", "youtu.be", "music.youtube.com")


def extract_youtube_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/") or None
    if "/shorts/" in parsed.path or "/embed/" in parsed.path:
        return parsed.path.split("/")[-1] or None
    if "youtube" in host:
        return parse_qs(parsed.query).get("v", [None])[0]
    return None
