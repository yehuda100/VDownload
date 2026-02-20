import os
import time
import hmac
import json
import hashlib
from bot_token import SECRET_KEY, URL

TEMP_LINKS_DIR = "temp_links"
EXPIRY = 24 * 3600
os.makedirs(TEMP_LINKS_DIR, exist_ok=True)


class SecureLinkManager:
    @staticmethod
    def save_metadata(filename, title) -> str:
        expiry = int(time.time()) + EXPIRY
        data = f"{title}:{filename}:{expiry}"
        sig = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        with open(os.path.join(TEMP_LINKS_DIR, f"{filename}.json"), 'w', encoding='utf-8') as f:
            json.dump({'filename': filename, 'title': title, 'expiry': expiry, 'signature': sig}, f)
        return f"{URL}VDownload/{filename}?sig={sig}"

    @staticmethod
    def verify(filename, sig):
        path = os.path.join(TEMP_LINKS_DIR, f"{filename}.json")
        if not os.path.exists(path): 
            return None
        with open(path, 'r', encoding='utf-8') as f:
            info = json.load(f)
        if time.time() > info['expiry']: 
            os.remove(path)
            return None
        expected = hmac.new(SECRET_KEY.encode(), f"{info['title']}:{filename}:{info['expiry']}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return info['filename']