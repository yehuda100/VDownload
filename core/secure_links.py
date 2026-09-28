"""
HMAC-signed, time-limited download links for files too large for Telegram.
"""
import hashlib
import hmac
import json
import os
import re
import time

from config import EXPIRY, SECRET_KEY, TEMP_LINKS_DIR, URL

# Production ids come from uuid4. Reject anything else before touching the filesystem.
_FILE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _is_file_id(file_id: object) -> bool:
    return isinstance(file_id, str) and _FILE_ID_RE.fullmatch(file_id) is not None


def _mac(payload: str) -> str:
    return hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _payload(title: str, file_id: str, expiry: int | float, filename: str) -> str:
    return f"{title}:{file_id}:{expiry}:{filename}"


def _legacy_payload(title: str, file_id: str, expiry: int | float) -> str:
    """MAC used before the filename was bound. Kept so unexpired links still open."""
    return f"{title}:{file_id}:{expiry}"


class SecureLinkManager:
    @staticmethod
    def save_metadata(file_id: str, filepath: str, title: str) -> str:
        if not _is_file_id(file_id):
            raise ValueError("file_id must be a UUID")
        expiry = int(time.time()) + EXPIRY
        sig = _mac(_payload(title, file_id, expiry, filepath))
        meta_path = os.path.join(TEMP_LINKS_DIR, f"{file_id}.json")
        payload = {
            "file_id": file_id,
            "filename": filepath,
            "title": title,
            "expiry": expiry,
            "signature": sig,
        }
        fd = os.open(meta_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if fd >= 0:
                os.close(fd)
        os.chmod(meta_path, 0o600)
        return f"{URL}VDownload/{file_id}?sig={sig}"

    @staticmethod
    def verify(file_id: str, sig: str) -> dict[str, str] | None:
        if not _is_file_id(file_id):
            return None
        if not isinstance(sig, str) or not sig:
            return None
        path = os.path.join(TEMP_LINKS_DIR, f"{file_id}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                info = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(info, dict):
            return None
        title = info.get("title")
        filename = info.get("filename")
        expiry = info.get("expiry")
        if not isinstance(title, str) or not isinstance(filename, str):
            return None
        if isinstance(expiry, bool) or not isinstance(expiry, (int, float)):
            return None
        expected_new = _mac(_payload(title, file_id, expiry, filename))
        expected_old = _mac(_legacy_payload(title, file_id, expiry))
        if not (
            hmac.compare_digest(sig, expected_new)
            or hmac.compare_digest(sig, expected_old)
        ):
            return None
        if time.time() > expiry:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
        return {"filename": filename, "title": title}
