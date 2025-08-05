import os
import time
import uuid
import hashlib
import hmac
import json
from bot_token import SECRET_KEY, URL

TEMP_LINKS_DIR = "temp_links"
EXPIRY = 24 * 3600
os.makedirs(TEMP_LINKS_DIR, exist_ok=True)

class SecureLinkManager:
    @staticmethod
    def generate(filename, title):
        fid = str(uuid.uuid4())
        expiry = int(time.time()) + EXPIRY
        data = f"{fid}:{filename}:{expiry}"
        sig = hmac.new(SECRET_KEY.encode(), data.encode(), hashlib.sha256).hexdigest()
        with open(os.path.join(TEMP_LINKS_DIR, f"{fid}.json"), 'w', encoding='utf-8') as f:
            json.dump({'filename': filename, 'title': title, 'expiry': expiry, 'signature': sig}, f)
        return f"{URL}VDownload/{fid}?sig={sig}"

    @staticmethod
    def verify(fid, sig):
        path = os.path.join(TEMP_LINKS_DIR, f"{fid}.json")
        if not os.path.exists(path): return None
        info = json.load(open(path, 'r', encoding='utf-8'))
        if time.time() > info['expiry']: os.remove(path); return None
        expected = hmac.new(SECRET_KEY.encode(), f"{fid}:{info['filename']}:{info['expiry']}".encode(), hashlib.sha256).hexdigest()
        if sig != expected: return None
        return info['filename']