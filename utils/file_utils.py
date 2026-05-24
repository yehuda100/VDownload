"""File lookup under DOWNLOAD_DIR and periodic cleanup of expired artifacts."""
import json
import os
import time
from pathlib import Path

from config import DOWNLOAD_DIR, TEMP_LINKS_DIR

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_LINKS_DIR, exist_ok=True)

STALE_FILE_AGE_SEC = 36 * 3600
MAX_FILENAME_BASE_LEN = 150
KNOWN_MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mp3", ".m4a", ".webm", ".mkv", ".opus", ".ogg", ".mov",
})


def cleanup() -> None:
    """Remove expired link metadata and downloads older than 36 hours."""
    now = time.time()
    for filename in os.listdir(TEMP_LINKS_DIR):
        meta_path = os.path.join(TEMP_LINKS_DIR, filename)
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        if meta["expiry"] < now:
            os.remove(meta_path)
            file_path = meta["filename"]
            if os.path.isfile(file_path):
                os.remove(file_path)

    for filename in os.listdir(DOWNLOAD_DIR):
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.isfile(file_path) and now - os.path.getmtime(file_path) > STALE_FILE_AGE_SEC:
            os.remove(file_path)


def sanitize_filename(title: str, *, max_length: int = MAX_FILENAME_BASE_LEN) -> str:
    """
    Build a safe filename *base* (no extension).

    Dots are converted to spaces so titles like ``Ep. 238`` are not treated as
  a ``.238`` extension when sent to Telegram or browsers.
    """
    if not title or not str(title).strip():
        return "download"

    chars = []
    for char in str(title):
        if char.isalnum() or char in " _-":
            chars.append(char)
        elif char == ".":
            chars.append(" ")

    base = " ".join("".join(chars).split())
    if not base:
        return "download"

    return base[:max_length].strip()


def build_display_filename(title: str, filepath: str | Path) -> str:
    """Sanitized title plus the real media extension from the file on disk."""
    path = Path(filepath)
    ext = path.suffix.lower()
    if ext not in KNOWN_MEDIA_EXTENSIONS:
        ext = ".mp4"
    base = sanitize_filename(title)
    return f"{base}{ext}"


def find_file(file_id: str) -> Path | None:
    """Return the newest file matching ``{DOWNLOAD_DIR}/{file_id}.*``."""
    directory = Path(DOWNLOAD_DIR)
    files = list(directory.glob(f"{file_id}.*"))
    if not files:
        return None
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0]
