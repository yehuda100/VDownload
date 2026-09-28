"""Unit tests for SecureLinkManager."""
import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.secure_links import SecureLinkManager
import config

FILE_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"
EXPIRED_ID = "33333333-3333-4333-8333-333333333333"


def _legacy_sig(title: str, file_id: str, expiry: int) -> str:
    payload = f"{title}:{file_id}:{expiry}"
    return hmac.new(
        config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


class TestSecureLinkManager:
    def test_save_and_verify_valid_link(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        link = SecureLinkManager.save_metadata(FILE_ID, "/data/video.mp4", "My Title")
        assert link.startswith(config.URL)
        assert FILE_ID in link
        assert "sig=" in link

        sig = link.split("sig=")[1]
        verified = SecureLinkManager.verify(FILE_ID, sig)
        assert verified["filename"] == "/data/video.mp4"
        assert verified["title"] == "My Title"

    def test_metadata_is_owner_read_write_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        SecureLinkManager.save_metadata(FILE_ID, "/data/video.mp4", "My Title")
        mode = (tmp_path / f"{FILE_ID}.json").stat().st_mode & 0o777
        assert mode == 0o600

    def test_verify_rejects_bad_signature(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        SecureLinkManager.save_metadata(OTHER_ID, "/x.mp4", "T")
        assert SecureLinkManager.verify(OTHER_ID, "bad-signature") is None
        assert (tmp_path / f"{OTHER_ID}.json").exists()

    def test_verify_rejects_expired_link(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        link = SecureLinkManager.save_metadata(EXPIRED_ID, "/y.mp4", "Expired")
        sig = link.split("sig=")[1]
        meta = tmp_path / f"{EXPIRED_ID}.json"
        later = json.loads(meta.read_text(encoding="utf-8"))["expiry"] + 10
        monkeypatch.setattr("core.secure_links.time.time", lambda: later)

        assert SecureLinkManager.verify(EXPIRED_ID, sig) is None
        assert not meta.exists()

    def test_expired_link_with_bad_signature_is_not_deleted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        link = SecureLinkManager.save_metadata(EXPIRED_ID, "/y.mp4", "Expired")
        meta = tmp_path / f"{EXPIRED_ID}.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        later = data["expiry"] + 10
        monkeypatch.setattr("core.secure_links.time.time", lambda: later)

        assert SecureLinkManager.verify(EXPIRED_ID, "not-the-signature") is None
        assert meta.exists()
        assert link

    def test_missing_or_non_string_sig_is_not_found(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        SecureLinkManager.save_metadata(FILE_ID, "/data/video.mp4", "My Title")
        meta = tmp_path / f"{FILE_ID}.json"
        assert SecureLinkManager.verify(FILE_ID, None) is None
        assert SecureLinkManager.verify(FILE_ID, "") is None
        assert SecureLinkManager.verify(FILE_ID, 12345) is None
        assert meta.exists()

    def test_new_signature_rejects_tampered_filename(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        link = SecureLinkManager.save_metadata(FILE_ID, "/data/video.mp4", "My Title")
        sig = link.split("sig=")[1]
        meta = tmp_path / f"{FILE_ID}.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["filename"] = "/etc/passwd"
        meta.write_text(json.dumps(data), encoding="utf-8")

        assert SecureLinkManager.verify(FILE_ID, sig) is None
        assert meta.exists()

    def test_save_replaces_atomically_and_keeps_previous_on_failure(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        SecureLinkManager.save_metadata(OTHER_ID, "/x.mp4", "T")
        original = (tmp_path / f"{OTHER_ID}.json").read_text(encoding="utf-8")
        assert json.loads(original)["title"] == "T"
        assert list(tmp_path.glob("*.tmp")) == []

        with patch("core.secure_links.json.dump", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                SecureLinkManager.save_metadata(OTHER_ID, "/x.mp4", "T2")

        assert (tmp_path / f"{OTHER_ID}.json").read_text(encoding="utf-8") == original
        assert list(tmp_path.glob("*.tmp")) == []

    def test_legacy_signature_still_verifies(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        title = "Old Title"
        filename = "/data/old.mp4"
        expiry = int(time.time()) + 3600
        sig = _legacy_sig(title, FILE_ID, expiry)
        (tmp_path / f"{FILE_ID}.json").write_text(
            json.dumps(
                {
                    "file_id": FILE_ID,
                    "filename": filename,
                    "title": title,
                    "expiry": expiry,
                    "signature": sig,
                }
            ),
            encoding="utf-8",
        )

        verified = SecureLinkManager.verify(FILE_ID, sig)
        assert verified == {"filename": filename, "title": title}

    def test_legacy_signature_does_not_bind_filename(self, tmp_path, monkeypatch):
        """Old links stay valid after deploy. Their MAC does not cover the path."""
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        title = "Old Title"
        expiry = int(time.time()) + 3600
        sig = _legacy_sig(title, FILE_ID, expiry)
        (tmp_path / f"{FILE_ID}.json").write_text(
            json.dumps(
                {
                    "file_id": FILE_ID,
                    "filename": "/tmp/replaced.mp4",
                    "title": title,
                    "expiry": expiry,
                    "signature": sig,
                }
            ),
            encoding="utf-8",
        )

        verified = SecureLinkManager.verify(FILE_ID, sig)
        assert verified["filename"] == "/tmp/replaced.mp4"

    def test_non_uuid_file_id_is_rejected_before_joining_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        victim = Path("/tmp/vdownload-secure-link-victim.json")
        victim.write_text("keep", encoding="utf-8")
        try:
            assert SecureLinkManager.verify("../..", "sig") is None
            assert SecureLinkManager.verify("../../etc/passwd", "sig") is None
            assert SecureLinkManager.verify("file-123", "sig") is None
            assert victim.read_text(encoding="utf-8") == "keep"
            with pytest.raises(ValueError):
                SecureLinkManager.save_metadata("file-123", "/data/video.mp4", "T")
            assert list(tmp_path.iterdir()) == []
        finally:
            victim.unlink(missing_ok=True)
