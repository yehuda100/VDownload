"""Unit tests for SecureLinkManager."""
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from core.secure_links import SecureLinkManager
import config


class TestSecureLinkManager:
    def test_save_and_verify_valid_link(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        link = SecureLinkManager.save_metadata(
            "file-123", "/data/video.mp4", "My Title"
        )
        assert link.startswith(config.URL)
        assert "file-123" in link
        assert "sig=" in link

        sig = link.split("sig=")[1]
        verified = SecureLinkManager.verify("file-123", sig)
        assert verified["filename"] == "/data/video.mp4"
        assert verified["title"] == "My Title"

    def test_verify_rejects_bad_signature(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        SecureLinkManager.save_metadata("f1", "/x.mp4", "T")
        assert SecureLinkManager.verify("f1", "bad-signature") is None

    def test_verify_rejects_expired_link(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        link = SecureLinkManager.save_metadata("f2", "/y.mp4", "Expired")
        sig = link.split("sig=")[1]
        meta = tmp_path / "f2.json"
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["expiry"] = int(time.time()) - 10
        meta.write_text(json.dumps(data), encoding="utf-8")

        assert SecureLinkManager.verify("f2", sig) is None
        assert not meta.exists()

    def test_save_replaces_atomically_and_keeps_previous_on_failure(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
        SecureLinkManager.save_metadata("f1", "/x.mp4", "T")
        original = (tmp_path / "f1.json").read_text(encoding="utf-8")
        assert json.loads(original)["title"] == "T"
        assert list(tmp_path.glob("*.tmp")) == []

        with patch("core.secure_links.json.dump", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                SecureLinkManager.save_metadata("f1", "/x.mp4", "T2")

        assert (tmp_path / "f1.json").read_text(encoding="utf-8") == original
        assert list(tmp_path.glob("*.tmp")) == []
