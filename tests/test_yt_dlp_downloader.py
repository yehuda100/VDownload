"""Unit tests for yt_dlp_downloader."""
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from downloaders.yt_dlp_downloader import YtDlpDownloader
from downloaders.exceptions import ExtractionException
import config


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
        assert progress.messages[0] == "Starting download..."
        assert progress.messages[-1] == "Download complete."
        mock_ydl.extract_info.assert_called_once_with(
            "https://tiktok.com/x", download=True
        )

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
