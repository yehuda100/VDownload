"""Unit tests for ytstream_downloader."""
from unittest.mock import AsyncMock, patch

import pytest

from downloaders.ytstream_downloader import YtstreamDownloader
from downloaders.exceptions import (
    InvalidURLException,
    APIException,
    StreamNotFoundException,
    FFmpegException,
)
from tests.conftest import make_aiohttp_response, make_session
import config


@pytest.fixture
def downloader():
    return YtstreamDownloader("test-api-key")


YTSTREAM_API_DATA = {
    "title": "Test Video",
    "adaptiveFormats": [
        {"itag": 140, "url": "https://audio.example/a.m4a", "mimeType": "audio/mp4"},
        {"itag": 136, "url": "https://video.example/v720.mp4", "mimeType": "video/mp4"},
    ],
    "formats": [
        {"itag": 22, "url": "https://combined.example/720.mp4"},
    ],
    "thumbnail": [{"url": "https://thumb.example/cover.jpg"}],
}


class TestDownloadBestVideo:
    def test_prefers_combined_720_itag_22(self, downloader):
        cmd = downloader.download_best_video("vid1", YTSTREAM_API_DATA)
        assert cmd[0] == "ffmpeg"
        assert "https://combined.example/720.mp4" in cmd
        assert cmd[-1].endswith("/vid1.mp4")

    def test_merges_adaptive_video_and_audio_when_no_combined(self, downloader):
        data = {
            "adaptiveFormats": [
                {"itag": 140, "url": "https://audio.example/a.m4a"},
                {"itag": 135, "url": "https://video.example/v480.mp4"},
            ],
            "formats": [],
        }
        cmd = downloader.download_best_video("vid2", data)
        assert "https://video.example/v480.mp4" in cmd
        assert "https://audio.example/a.m4a" in cmd
        assert "-shortest" in cmd

    def test_raises_when_no_streams(self, downloader):
        with pytest.raises(StreamNotFoundException, match="video stream"):
            downloader.download_best_video("vid3", {"adaptiveFormats": [], "formats": []})


class TestDownloadBestAudio:
    def test_uses_itag_140(self, downloader):
        cmd = downloader.download_best_audio("aud1", YTSTREAM_API_DATA)
        assert "https://audio.example/a.m4a" in cmd
        assert cmd[-1].endswith("/aud1.mp3")

    def test_with_thumbnail_adds_cover_mapping(self, downloader):
        cmd = downloader.download_best_audio("aud2", YTSTREAM_API_DATA)
        assert "https://thumb.example/cover.jpg" in cmd
        assert "-map" in cmd

    def test_fallback_audio_by_mimetype(self, downloader):
        data = {
            "adaptiveFormats": [
                {
                    "itag": 251,
                    "url": "https://audio.example/opus",
                    "mimeType": "audio/webm",
                    "bitrate": 120,
                },
            ],
            "thumbnail": [],
        }
        cmd = downloader.download_best_audio("aud3", data)
        assert "https://audio.example/opus" in cmd

    def test_raises_when_no_audio(self, downloader):
        with pytest.raises(StreamNotFoundException, match="audio stream"):
            downloader.download_best_audio("aud4", {"adaptiveFormats": []})


class TestDownload:
    async def test_invalid_youtube_url_raises(self, downloader, progress):
        with pytest.raises(InvalidURLException):
            await downloader.download("https://example.com/not-yt", "mp4", progress)

    async def test_api_error_raises(self, downloader, progress, mocker):
        ctx, _ = make_aiohttp_response(status=500, text="server error")
        session_ctx, _ = make_session([ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )

        with pytest.raises(APIException, match="500"):
            await downloader.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
            )

    async def test_success_mp4(self, downloader, progress, mocker):
        api_ctx, _ = make_aiohttp_response(json_data=YTSTREAM_API_DATA)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="stream-id")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = 0
        mock_process.kill = lambda: None
        mock_process.wait = AsyncMock()
        mocker.patch("asyncio.create_subprocess_exec", return_value=mock_process)

        result = await downloader.download(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
        )

        assert result["file_id"] == "stream-id"
        assert result["title"] == "Test Video"
        assert "Getting video info from ytstream" in progress.messages[0]
        assert progress.messages[-1] == "Download complete."

    async def test_ffmpeg_failure_raises(self, downloader, progress, mocker):
        api_ctx, _ = make_aiohttp_response(json_data=YTSTREAM_API_DATA)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="id")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"encode error"))
        mock_process.returncode = 1
        mock_process.kill = lambda: None
        mock_process.wait = AsyncMock()
        mocker.patch("asyncio.create_subprocess_exec", return_value=mock_process)

        with pytest.raises(FFmpegException, match="encode error"):
            await downloader.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
            )

    async def test_ffmpeg_timeout_raises(self, downloader, progress, mocker):
        api_ctx, _ = make_aiohttp_response(json_data=YTSTREAM_API_DATA)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="id")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(side_effect=TimeoutError())
        mock_process.kill = lambda: None
        mock_process.wait = AsyncMock()
        mocker.patch("asyncio.create_subprocess_exec", return_value=mock_process)

        with patch(
            "downloaders.ytstream_downloader.asyncio.wait_for",
            side_effect=TimeoutError(),
        ):
            with pytest.raises(FFmpegException, match="timed out"):
                await downloader.download(
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
                )
