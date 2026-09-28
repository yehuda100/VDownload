"""
HMAC-signed, time-limited download links for files too large for Telegram.
"""
import hashlib
import hmac
import json
import os
import tempfile
import time

from config import EXPIRY, SECRET_KEY, TEMP_LINKS_DIR, URL


class SecureLinkManager:
    @staticmethod
    def save_metadata(file_id: str, filepath: str, title: str) -> str:
        expiry = int(time.time()) + EXPIRY
        data = f"{title}:{file_id}:{expiry}"
        sig = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        meta_path = os.path.join(TEMP_LINKS_DIR, f"{file_id}.json")
        payload = {
            "file_id": file_id,
            "filename": filepath,
            "title": title,
            "expiry": expiry,
            "signature": sig,
        }
        os.makedirs(TEMP_LINKS_DIR, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".link-", suffix=".tmp", dir=TEMP_LINKS_DIR
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, meta_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return f"{URL}VDownload/{file_id}?sig={sig}"

    @staticmethod
    def verify(file_id: str, sig: str) -> dict[str, str] | None:
        path = os.path.join(TEMP_LINKS_DIR, f"{file_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
        if time.time() > info["expiry"]:
            os.remove(path)
            return None
        expected = hmac.new(
            SECRET_KEY.encode(),
            f"{info['title']}:{file_id}:{info['expiry']}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return {"filename": info["filename"], "title": info["title"]}
