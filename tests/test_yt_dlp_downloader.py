"""Unit tests for yt_dlp_downloader."""
import asyncio
import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yt_dlp

from downloaders.yt_dlp_downloader import (
    YtDlpDownloader,
    instagram_auth_user_message,
    is_instagram_auth_failure,
)
from downloaders.exceptions import ExtractionException
import config

ROOT = Path(__file__).resolve().parents[1]

INSTAGRAM_429 = (
    "ERROR: [instagram:user] reganews: Unable to download webpage: "
    "HTTP Error 429: Too Many Requests "
    "(caused by <HTTPError 429: Too Many Requests>)"
)
INSTAGRAM_LOGIN = (
    "ERROR: [Instagram] Ddaz00sO5VC: Requested content is not available, "
    "rate-limit reached or login required. Use --cookies-from-browser or "
    "--cookies for the authentication. See  "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  "
    "for how to manually pass cookies"
)
INSTAGRAM_EMPTY = (
    "ERROR: [Instagram] DZdJouwscqs: Instagram sent an empty media response. "
    "Check if this post is accessible in your browser without being logged-in. "
    "If it is not, then use --cookies-from-browser or --cookies for the authentication."
)


@pytest.fixture
def downloader():
    return YtDlpDownloader()


class TestBuildOptions:
    def test_mp4_sets_merged_format(self, downloader):
        opts = downloader.build_options("abc-123", "mp4")
        assert opts["outtmpl"].endswith("/abc-123.%(ext)s")
        assert "merged_output_format" in opts
        assert opts["merged_output_format"] == "mp4"
        assert "postprocessors" not in opts

    def test_mp3_sets_audio_postprocessor(self, downloader):
        opts = downloader.build_options("abc-123", "mp3")
        assert opts["format"] == "bestaudio/best"
        assert opts["postprocessors"][0]["preferredcodec"] == "mp3"

    def test_omits_cookiefile_when_unset(self, downloader, monkeypatch):
        monkeypatch.delattr(config, "YTDLP_COOKIES_FILE", raising=False)
        opts = downloader.build_options("abc-123", "mp4")
        assert "cookiefile" not in opts

    def test_omits_cookiefile_when_blank(self, downloader, monkeypatch, caplog):
        monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", "   ", raising=False)
        with caplog.at_level(logging.WARNING):
            opts = downloader.build_options("abc-123", "mp4")
        assert "cookiefile" not in opts
        assert "YTDLP_COOKIES_FILE" not in caplog.text

    def test_sets_cookiefile_when_file_exists(self, downloader, monkeypatch, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(cookies), raising=False)
        opts = downloader.build_options("abc-123", "mp4")
        assert opts["cookiefile"] == os.path.abspath(cookies)
        assert opts["merged_output_format"] == "mp4"

    def test_missing_cookies_file_is_skipped(self, downloader, monkeypatch, caplog):
        missing = "/tmp/vdownload-missing-cookies.txt"
        monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", missing, raising=False)
        with caplog.at_level(logging.WARNING):
            opts = downloader.build_options("abc-123", "mp4")
        assert "cookiefile" not in opts
        assert missing in caplog.text


class TestDownload:
    async def test_success_returns_file_id_and_title(self, downloader, progress, mocker):
        mocker.patch.object(
            downloader,
            "generate_file_id",
            return_value="fixed-id",
        )
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"title": "My Video"}
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)

        mocker.patch(
            "downloaders.yt_dlp_downloader.yt_dlp.YoutubeDL",
            return_value=mock_ydl,
        )
        mocker.patch(
            "downloaders.yt_dlp_downloader.asyncio.to_thread",
            side_effect=lambda fn: fn(),
        )

        result = await downloader.download(
            "https://tiktok.com/x", "mp4", progress
        )

        assert result == {"file_id": "fixed-id", "title": "My Video"}
        assert progress.messages[0] == "Downloading..."
        assert "Download complete." not in progress.messages
        mock_ydl.extract_info.assert_called_once_with(
            "https://tiktok.com/x", download=True
        )

    async def test_progress_hook_reports_size_and_speed(
        self, downloader, progress, mocker
    ):
        mocker.patch.object(downloader, "generate_file_id", return_value="fixed-id")
        captured = {}

        class RecordingYDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *args, **kwargs):
                captured["opts"]["progress_hooks"][0]({
                    "status": "downloading",
                    "downloaded_bytes": 13_002_342,
                    "total_bytes": 31_457_280,
                    "speed": 1_258_291,
                })
                captured["opts"]["progress_hooks"][0]({"status": "finished"})
                return {"title": "My Video"}

        mocker.patch(
            "downloaders.yt_dlp_downloader.yt_dlp.YoutubeDL",
            RecordingYDL,
        )
        mocker.patch(
            "downloaders.yt_dlp_downloader.asyncio.to_thread",
            side_effect=lambda fn: fn(),
        )

        await downloader.download("https://tiktok.com/x", "mp4", progress)
        for _ in range(5):
            await asyncio.sleep(0)
            if any("MB/s" in m for m in progress.messages):
                break

        assert "Downloading... 12.4 / 30.0 MB · 1.2 MB/s" in progress.messages

    async def test_download_error_raises_extraction_exception(
        self, downloader, progress, mocker
    ):
        mocker.patch.object(downloader, "generate_file_id", return_value="id")

        mocker.patch(
            "downloaders.yt_dlp_downloader.asyncio.to_thread",
            side_effect=lambda fn: fn(),
        )
        class FailingYDL:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *args, **kwargs):
                raise yt_dlp.utils.DownloadError("blocked")

        mocker.patch(
            "downloaders.yt_dlp_downloader.yt_dlp.YoutubeDL",
            return_value=FailingYDL(),
        )

        with pytest.raises(ExtractionException, match="Failed to download"):
            await downloader.download("https://example.com/v", "mp4", progress)

    async def test_passes_cookiefile_to_yt_dlp(
        self, downloader, progress, mocker, monkeypatch, tmp_path
    ):
        cookies = tmp_path / "instagram.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(cookies), raising=False)
        mocker.patch.object(downloader, "generate_file_id", return_value="fixed-id")

        captured = {}

        class RecordingYDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *args, **kwargs):
                return {"title": "Reel"}

        mocker.patch(
            "downloaders.yt_dlp_downloader.yt_dlp.YoutubeDL",
            RecordingYDL,
        )
        mocker.patch(
            "downloaders.yt_dlp_downloader.asyncio.to_thread",
            side_effect=lambda fn: fn(),
        )

        result = await downloader.download(
            "https://www.instagram.com/reel/abc/", "mp4", progress
        )
        assert result["title"] == "Reel"
        assert captured["opts"]["cookiefile"] == os.path.abspath(cookies)


class TestInstagramAuthMessage:
    @pytest.mark.parametrize(
        "url,message",
        [
            ("https://www.instagram.com/reganews/reel/Ddaz00sO5VC/", INSTAGRAM_429),
            ("https://www.instagram.com/reel/Ddaz00sO5VC/", INSTAGRAM_LOGIN),
            ("https://www.instagram.com/reel/DZdJouwscqs/", INSTAGRAM_EMPTY),
            ("https://example.com/v", INSTAGRAM_LOGIN),
        ],
    )
    def test_detects_rate_limit_and_login_errors(self, url, message):
        assert is_instagram_auth_failure(url, yt_dlp.utils.DownloadError(message))

    def test_ignores_instagram_post_without_video(self):
        err = yt_dlp.utils.DownloadError(
            "ERROR: [Instagram] DZ0Z7hYswEJ: There is no video in this post"
        )
        assert not is_instagram_auth_failure(
            "https://www.instagram.com/p/DZ0Z7hYswEJ/", err
        )

    def test_ignores_non_instagram_429(self):
        err = yt_dlp.utils.DownloadError(
            "ERROR: [TikTok] abc: HTTP Error 429: Too Many Requests"
        )
        assert not is_instagram_auth_failure("https://www.tiktok.com/abc", err)

    def test_unset_message_tells_owner_how_to_configure(self, monkeypatch):
        monkeypatch.delattr(config, "YTDLP_COOKIES_FILE", raising=False)
        message = instagram_auth_user_message()
        assert "rate limit or login required" in message
        assert "YTDLP_COOKIES_FILE" in message
        assert "Netscape" in message
        assert "cookies-from-browser" not in message
        assert "github.com" not in message

    def test_missing_file_message_names_the_path(self, monkeypatch):
        monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", "/var/cookies/instagram.txt", raising=False)
        message = instagram_auth_user_message()
        assert "/var/cookies/instagram.txt" in message
        assert "not found" in message

    def test_existing_file_message_asks_for_a_fresh_export(self, monkeypatch, tmp_path):
        cookies = tmp_path / "cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
        monkeypatch.setattr(config, "YTDLP_COOKIES_FILE", str(cookies), raising=False)
        message = instagram_auth_user_message()
        assert "fresh Netscape" in message
        assert "not found" not in message

    async def test_download_error_is_rewritten_for_the_user(
        self, downloader, progress, mocker, monkeypatch
    ):
        monkeypatch.delattr(config, "YTDLP_COOKIES_FILE", raising=False)
        mocker.patch.object(downloader, "generate_file_id", return_value="id")
        mocker.patch(
            "downloaders.yt_dlp_downloader.asyncio.to_thread",
            side_effect=lambda fn: fn(),
        )

        class FailingYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *args, **kwargs):
                raise yt_dlp.utils.DownloadError(INSTAGRAM_LOGIN)

        mocker.patch(
            "downloaders.yt_dlp_downloader.yt_dlp.YoutubeDL",
            FailingYDL,
        )

        with pytest.raises(ExtractionException) as exc_info:
            await downloader.download(
                "https://www.instagram.com/reel/Ddaz00sO5VC/", "mp4", progress
            )
        message = str(exc_info.value)
        assert "rate limit or login required" in message
        assert "YTDLP_COOKIES_FILE" in message
        assert "cookies-from-browser" not in message
        assert "wiki/FAQ" not in message


def test_cookie_files_are_gitignored():
    names = [
        "cookies.txt",
        "instagram_cookies.txt",
        "www.instagram.com_cookies.txt",
        "cookies/instagram.txt",
    ]
    result = subprocess.run(
        ["git", "check-ignore", "-v", "--", *names],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    ignored = {line.split("\t", 1)[1] for line in result.stdout.splitlines()}
    assert ignored == set(names)
