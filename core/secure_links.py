"""
HMAC-signed, time-limited download links for files too large for Telegram.
"""
import hashlib
import hmac
import json
import os
import time

from config import EXPIRY, SECRET_KEY, TEMP_LINKS_DIR, URL


class SecureLinkManager:
    @staticmethod
    def save_metadata(file_id: str, filepath: str, title: str) -> str:
        expiry = int(time.time()) + EXPIRY
        data = f"{title}:{file_id}:{expiry}"
        sig = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        meta_path = os.path.join(TEMP_LINKS_DIR, f"{file_id}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "file_id": file_id,
                    "filename": filepath,
                    "title": title,
                    "expiry": expiry,
                    "signature": sig,
                },
                f,
            )
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
