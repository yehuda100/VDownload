"""Unit tests for file_utils filename sanitization."""
from utils.file_utils import build_display_filename, sanitize_filename


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
