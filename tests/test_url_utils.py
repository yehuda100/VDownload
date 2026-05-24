"""Unit tests for url_utils."""
import pytest

from utils.url_utils import (
    extract_url,
    is_youtube_url,
    extract_youtube_id,
    is_valid_url,
)


class TestExtractUrl:
    def test_extracts_https_url(self):
        assert extract_url("Check this https://youtu.be/abc123 please") == (
            "https://youtu.be/abc123"
        )

    def test_returns_none_when_missing(self):
        assert extract_url("no link here") is None


class TestIsYoutubeUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=abc",
            "https://youtube.com/watch?v=abc",
            "https://m.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://music.youtube.com/watch?v=abc",
        ],
    )
    def test_recognizes_youtube_hosts(self, url):
        assert is_youtube_url(url) is True

    def test_rejects_non_youtube(self):
        assert is_youtube_url("https://www.tiktok.com/@u/video/1") is False


class TestExtractYoutubeId:
    def test_watch_url(self):
        assert (
            extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            == "dQw4w9WgXcQ"
        )

    def test_youtu_be(self):
        assert extract_youtube_id("https://youtu.be/shortid") == "shortid"

    def test_shorts(self):
        assert (
            extract_youtube_id("https://www.youtube.com/shorts/xyz789")
            == "xyz789"
        )


class TestIsValidUrl:
    def test_valid_http(self):
        assert is_valid_url("https://example.com/path") is True

    def test_invalid_without_host(self):
        assert is_valid_url("not-a-url") is False
