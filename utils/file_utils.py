"""File lookup under DOWNLOAD_DIR and periodic cleanup of expired artifacts."""
import json
import logging
import os
import time
from pathlib import Path

from config import DOWNLOAD_DIR, TEMP_LINKS_DIR

logger = logging.getLogger(__name__)

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_LINKS_DIR, exist_ok=True)

STALE_FILE_AGE_SEC = 36 * 3600
MAX_FILENAME_BASE_LEN = 150
KNOWN_MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mp3", ".m4a", ".webm", ".mkv", ".opus", ".ogg", ".mov",
})


def _download_root() -> Path:
    return Path(DOWNLOAD_DIR).resolve()


def path_is_inside_download_dir(file_path: str | Path) -> bool:
    """True when the directory entry itself lives inside DOWNLOAD_DIR.

    The final path component is not resolved, so a symlink inside the download
    directory is considered inside even if its target is not. Callers then
    unlink the symlink rather than the outside target.
    """
    raw = Path(file_path)
    if not raw.is_absolute():
        raw = Path(DOWNLOAD_DIR) / raw
    try:
        parent = raw.parent.resolve()
        parent.relative_to(_download_root())
    except (OSError, ValueError):
        return False
    return True


def remove_download_file(file_path: str | Path) -> bool:
    """Unlink a file only when it is inside DOWNLOAD_DIR. Returns True if removed."""
    raw = Path(file_path)
    if not raw.is_absolute():
        raw = Path(DOWNLOAD_DIR) / raw
    if not path_is_inside_download_dir(raw):
        logger.warning("Refusing to delete %s outside %s", raw, DOWNLOAD_DIR)
        return False
    if raw.is_dir() and not raw.is_symlink():
        logger.warning("Refusing to delete directory %s", raw)
        return False
    if not raw.exists() and not raw.is_symlink():
        return False
    try:
        raw.unlink()
    except OSError:
        logger.exception("Could not delete %s", raw)
        return False
    return True


def remove_partial_downloads(file_id: str) -> None:
    """Delete ``{DOWNLOAD_DIR}/{file_id}.*`` left behind by a failed download."""
    if not file_id or file_id in {".", ".."}:
        return
    if any(sep in file_id for sep in ("/", "\\", "\x00")):
        return
    if any(char in file_id for char in "*?[]"):
        return
    directory = Path(DOWNLOAD_DIR)
    if not directory.is_dir():
        return
    for path in directory.glob(f"{file_id}.*"):
        remove_download_file(path)


def cleanup() -> None:
    """Remove expired link metadata and downloads older than 36 hours.

    One bad metadata file does not stop the rest of the sweep. Media paths are
    deleted only when they sit inside DOWNLOAD_DIR.
    """
    now = time.time()
    try:
        link_names = os.listdir(TEMP_LINKS_DIR)
    except OSError:
        logger.exception("Could not list %s", TEMP_LINKS_DIR)
        link_names = []
    for filename in link_names:
        meta_path = os.path.join(TEMP_LINKS_DIR, filename)
        try:
            if not os.path.isfile(meta_path):
                continue
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            expiry = meta.get("expiry")
            if not isinstance(expiry, (int, float)) or expiry >= now:
                continue
            file_path = meta.get("filename")
            os.remove(meta_path)
            if isinstance(file_path, str):
                remove_download_file(file_path)
        except Exception:
            logger.exception("Skipping link metadata %s", meta_path)

    try:
        download_names = os.listdir(DOWNLOAD_DIR)
    except OSError:
        logger.exception("Could not list %s", DOWNLOAD_DIR)
        download_names = []
    for filename in download_names:
        file_path = os.path.join(DOWNLOAD_DIR, filename)
        try:
            if (
                os.path.isfile(file_path)
                and now - os.path.getmtime(file_path) > STALE_FILE_AGE_SEC
            ):
                os.remove(file_path)
        except Exception:
            logger.exception("Skipping stale file %s", file_path)


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
