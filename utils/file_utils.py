"""File lookup under DOWNLOAD_DIR and periodic cleanup of expired artifacts."""
import json
import os
import time
from pathlib import Path

from config import DOWNLOAD_DIR, TEMP_LINKS_DIR

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_LINKS_DIR, exist_ok=True)

STALE_FILE_AGE_SEC = 36 * 3600


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


def sanitize_filename(filename: str) -> str:
    return "".join(c for c in filename if c.isalnum() or c in " ._-").rstrip()


def find_file(file_id: str) -> Path | None:
    """Return the newest file matching ``{DOWNLOAD_DIR}/{file_id}.*``."""
    directory = Path(DOWNLOAD_DIR)
    files = list(directory.glob(f"{file_id}.*"))
    if not files:
        return None
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0]
