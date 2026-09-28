"""Unit tests for file_utils filename sanitization and cleanup."""
import json
import os
import time

from utils.file_utils import (
    build_display_filename,
    cleanup,
    remove_partial_downloads,
    sanitize_filename,
)


class TestSanitizeFilename:
    def test_removes_punctuation(self):
        title = "66K views · 1.1K reactions | Cave Podcast"
        assert "|" not in sanitize_filename(title)
        assert "·" not in sanitize_filename(title)

    def test_dots_become_spaces_not_extension(self):
        title = (
            "has arrived. Ep. 238 #2bears1cave #TonyHinchcliffe | Cave Podcast"
        )
        base = sanitize_filename(title)
        assert "." not in base
        assert "238" in base

    def test_empty_title_fallback(self):
        assert sanitize_filename("") == "download"
        assert sanitize_filename("   ") == "download"


class TestBuildDisplayFilename:
    def test_episode_title_gets_mp4_extension(self):
        title = "has arrived. Ep. 238 #2bears1cave | Cave Podcast"
        name = build_display_filename(title, "/tmp/abc-123.mp4")
        assert name.endswith(".mp4")
        assert not name.endswith(".238")
        assert "238" in name

    def test_mp3_uses_file_extension(self):
        name = build_display_filename("My Song.v2", "/downloads/id.mp3")
        assert name.endswith(".mp3")


def test_cleanup_isolates_bad_metadata_and_stays_inside_download_dir(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    links = tmp_path / "links"
    downloads.mkdir()
    links.mkdir()
    monkeypatch.setattr("utils.file_utils.DOWNLOAD_DIR", str(downloads))
    monkeypatch.setattr("utils.file_utils.TEMP_LINKS_DIR", str(links))

    inside = downloads / "inside.mp4"
    inside.write_bytes(b"gone")
    stale = downloads / "stale.mp4"
    stale.write_bytes(b"old")
    os.utime(stale, (1, 1))
    fresh = downloads / "fresh.mp4"
    fresh.write_bytes(b"new")
    keep = downloads / "keep.mp4"
    keep.write_bytes(b"keep")
    outside = tmp_path / "secret.txt"
    outside.write_text("keep", encoding="utf-8")

    (links / "bad.json").write_text("{not json", encoding="utf-8")
    (links / "good.json").write_text(
        json.dumps({"expiry": 1, "filename": str(inside)}),
        encoding="utf-8",
    )
    (links / "evil.json").write_text(
        json.dumps({"expiry": 1, "filename": str(outside)}),
        encoding="utf-8",
    )
    (links / "live.json").write_text(
        json.dumps({"expiry": int(time.time()) + 3600, "filename": str(keep)}),
        encoding="utf-8",
    )

    cleanup()

    assert not inside.exists()
    assert not (links / "good.json").exists()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert not (links / "evil.json").exists()
    assert not stale.exists()
    assert fresh.exists()
    assert keep.exists()
    assert (links / "live.json").exists()
    assert (links / "bad.json").exists()


def test_remove_partial_downloads_only_matches_that_id(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    monkeypatch.setattr("utils.file_utils.DOWNLOAD_DIR", str(downloads))
    partial = downloads / "abc.mp4.part"
    finished = downloads / "abc.mp4"
    other = downloads / "other.mp4"
    partial.write_bytes(b"p")
    finished.write_bytes(b"f")
    other.write_bytes(b"o")

    remove_partial_downloads("abc")

    assert not partial.exists()
    assert not finished.exists()
    assert other.exists()

    outside = tmp_path / "secret.txt"
    outside.write_text("x", encoding="utf-8")
    remove_partial_downloads("../secret")
    remove_partial_downloads("*")
    assert outside.exists()
    assert other.exists()
