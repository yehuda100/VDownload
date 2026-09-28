"""Unit tests for download_manager orchestration and fallback."""
from unittest.mock import AsyncMock, MagicMock

import pytest
import yt_dlp

from core.download_manager import _YOUTUBE_CHAIN, _download_with_fallback, download
from core.download_audit import DownloadRequest
from downloaders.exceptions import DownloaderException, APIException
from tests.conftest import FakeProgress


@pytest.fixture
def request_ctx():
    return DownloadRequest(
        user_id=1,
        username="tester",
        chat_id=1,
        format_type="mp4",
        url="https://www.youtube.com/watch?v=abc",
    )


class TestDownloadWithFallback:
    async def test_first_provider_success(self, request_ctx):
        progress = FakeProgress()
        ok = MagicMock()
        ok.download = AsyncMock(return_value={"file_id": "a", "title": "T"})

        result, name = await _download_with_fallback(
            (("primary", lambda: ok),),
            request_ctx.url,
            "mp4",
            progress,
            request_ctx,
        )

        assert name == "primary"
        assert result["file_id"] == "a"
        ok.download.assert_awaited_once()

    async def test_fallback_to_second_provider(self, request_ctx):
        progress = FakeProgress()
        fail = MagicMock()
        fail.download = AsyncMock(
            side_effect=APIException(500, "fail")
        )
        ok = MagicMock()
        ok.download = AsyncMock(return_value={"file_id": "b", "title": "T2"})

        result, name = await _download_with_fallback(
            (("first", lambda: fail), ("second", lambda: ok)),
            request_ctx.url,
            "mp4",
            progress,
            request_ctx,
        )

        assert name == "second"
        assert result["file_id"] == "b"
        assert fail.download.await_count == 1
        assert ok.download.await_count == 1

    async def test_all_providers_fail_raises_last(self, request_ctx):
        progress = FakeProgress()
        fail1 = MagicMock()
        fail1.download = AsyncMock(side_effect=APIException(500, "a"))
        fail2 = MagicMock()
        fail2.download = AsyncMock(side_effect=APIException(503, "b"))

        with pytest.raises(APIException, match="503"):
            await _download_with_fallback(
                (("p1", lambda: fail1), ("p2", lambda: fail2)),
                request_ctx.url,
                "mp4",
                progress,
                request_ctx,
            )


class TestDownload:
    async def test_youtube_uses_fallback_chain(self, request_ctx, mocker):
        progress = FakeProgress()
        mocker.patch(
            "core.download_manager._download_with_fallback",
            new_callable=AsyncMock,
            return_value=({"file_id": "x", "title": "Y"}, "yt-dlp"),
        )

        result, provider = await download(
            "https://www.youtube.com/watch?v=abc",
            "mp4",
            progress,
            request_ctx,
        )

        assert provider == "yt-dlp"
        assert result["file_id"] == "x"

    def test_youtube_chain_order(self):
        names = [name for name, _ in _YOUTUBE_CHAIN]
        assert names == ["yt-dlp", "vda"]

    async def test_non_youtube_uses_yt_dlp(self, request_ctx, mocker):
        progress = FakeProgress()
        mock_dl = MagicMock()
        mock_dl.download = AsyncMock(
            return_value={"file_id": "t", "title": "TikTok"}
        )
        mocker.patch(
            "core.download_manager.get_yt_dlp_downloader",
            return_value=mock_dl,
        )

        result, provider = await download(
            "https://www.tiktok.com/@u/video/1",
            "mp4",
            progress,
            request_ctx,
        )

        assert provider == "yt-dlp"
        assert result["title"] == "TikTok"
        mock_dl.download.assert_awaited_once()

    async def test_youtube_yt_dlp_failure_falls_back_to_vda(self, request_ctx, mocker):
        """When yt-dlp fails on YouTube, VDA is still tried."""
        progress = FakeProgress()

        class FailingYDL:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, *args, **kwargs):
                raise yt_dlp.utils.DownloadError("ERROR: Video unavailable")

        mocker.patch(
            "downloaders.yt_dlp_downloader.yt_dlp.YoutubeDL",
            return_value=FailingYDL(),
        )
        mocker.patch(
            "downloaders.yt_dlp_downloader.asyncio.to_thread",
            side_effect=lambda fn: fn(),
        )
        mocker.patch(
            "downloaders.yt_dlp_downloader.YtDlpDownloader.generate_file_id",
            return_value="yt-id",
        )
        mocker.patch(
            "downloaders.vda_downloader.VdaDownloader.download",
            new_callable=AsyncMock,
            return_value={"file_id": "vda-id", "title": "From VDA"},
        )

        result, provider = await download(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "mp4",
            progress,
            request_ctx,
        )

        assert provider == "vda"
        assert result["file_id"] == "vda-id"

    async def test_non_youtube_propagates_failure(self, request_ctx, mocker):
        progress = FakeProgress()
        mock_dl = MagicMock()
        mock_dl.download = AsyncMock(
            side_effect=DownloaderException("yt-dlp failed")
        )
        mocker.patch(
            "core.download_manager.get_yt_dlp_downloader",
            return_value=mock_dl,
        )

        with pytest.raises(DownloaderException):
            await download(
                "https://www.tiktok.com/@u/video/1",
                "mp4",
                progress,
                request_ctx,
            )
