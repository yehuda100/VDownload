"""Unit tests for vda_downloader."""
from unittest.mock import AsyncMock, patch

import pytest

from downloaders.vda_downloader import STALL_TIMEOUT_SEC, VdaDownloader
from downloaders.exceptions import (
    APIException,
    ProgressURLNotFoundException,
    ProgressStalledException,
    DownloadException,
)
from tests.conftest import make_aiohttp_response, make_session
import config


@pytest.fixture
def downloader():
    return VdaDownloader("test-vda-key")


def _init_response(progress_url="https://vda.example/progress/1", title="VDA Title"):
    return make_aiohttp_response(
        json_data={"progress_url": progress_url, "title": title}
    )[0]


class TestDownload:
    async def test_success_full_flow(self, downloader, progress, mocker):
        init_ctx = _init_response()
        poll_ctx, _ = make_aiohttp_response(
            json_data={"success": 0, "progress": 500}
        )
        done_ctx, _ = make_aiohttp_response(
            json_data={
                "success": 1,
                "download_url": "https://vda.example/file.bin",
            }
        )
        file_ctx, _ = make_aiohttp_response(chunks=[b"video", b"data"])

        session_ctx, session = make_session([init_ctx, poll_ctx, done_ctx, file_ctx])
        mocker.patch(
            "downloaders.vda_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="vda-file-id")

        mock_file = AsyncMock()
        mock_file.write = AsyncMock()
        mock_file.__aenter__ = AsyncMock(return_value=mock_file)
        mock_file.__aexit__ = AsyncMock(return_value=None)
        mocker.patch("aiofiles.open", return_value=mock_file)
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        result = await downloader.download(
            "https://www.youtube.com/watch?v=abc", "mp4", progress
        )

        assert result == {"file_id": "vda-file-id", "title": "VDA Title"}
        mock_file.write.assert_any_call(b"video")
        mock_file.write.assert_any_call(b"data")
        assert any("Getting link data from VDA" in m for m in progress.messages)
        assert progress.messages[-1] == "Download complete."

    async def test_uses_secondary_api_when_primary_fails(
        self, downloader, progress, mocker
    ):
        primary_ctx, _ = make_aiohttp_response(status=500, json_data={})
        secondary_ctx, _ = make_aiohttp_response(
            json_data={
                "progress_url": "https://vda.example/progress/2",
                "title": "Secondary",
            }
        )
        done_ctx, _ = make_aiohttp_response(
            json_data={"success": 1, "download_url": "https://vda.example/f.mp4"}
        )
        file_ctx, _ = make_aiohttp_response(chunks=[b"x"])

        session_ctx, _ = make_session(
            [primary_ctx, secondary_ctx, done_ctx, file_ctx]
        )
        mocker.patch(
            "downloaders.vda_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="fid")
        mocker.patch(
            "aiofiles.open",
            return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=AsyncMock(write=AsyncMock())),
                __aexit__=AsyncMock(return_value=None),
            ),
        )
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        result = await downloader.download(
            "https://www.youtube.com/watch?v=abc", "mp3", progress
        )

        assert result["title"] == "Secondary"
        assert any("secondary API" in m for m in progress.messages)

    async def test_progress_does_not_display_regression(
        self, downloader, progress, mocker
    ):
        """When API resets progress, UI should not show a lower percentage."""
        init_ctx = _init_response()
        p50_ctx, _ = make_aiohttp_response(json_data={"success": 0, "progress": 500})
        p0_ctx, _ = make_aiohttp_response(json_data={"success": 0, "progress": 0})
        done_ctx, _ = make_aiohttp_response(
            json_data={"success": 1, "download_url": "https://vda.example/f.mp4"}
        )
        file_ctx, _ = make_aiohttp_response(chunks=[b"x"])

        session_ctx, _ = make_session([init_ctx, p50_ctx, p0_ctx, done_ctx, file_ctx])
        mocker.patch(
            "downloaders.vda_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="fid")
        mocker.patch("aiofiles.open", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=AsyncMock(write=AsyncMock())),
            __aexit__=AsyncMock(return_value=None),
        ))
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        await downloader.download(
            "https://www.youtube.com/watch?v=abc", "mp4", progress
        )

        pct_messages = [m for m in progress.messages if m.startswith("Processing")]
        assert pct_messages == ["Processing... 50.0%"]

    async def test_stall_raises(self, downloader, progress, mocker):
        init_ctx = _init_response()
        stuck_ctx, _ = make_aiohttp_response(json_data={"success": 0, "progress": 100})

        session_ctx, _ = make_session([init_ctx, stuck_ctx, stuck_ctx])
        mocker.patch(
            "downloaders.vda_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        times = [1000.0] * 5 + [1000.0 + STALL_TIMEOUT_SEC + 5]
        call_idx = [0]

        def fake_time():
            i = min(call_idx[0], len(times) - 1)
            call_idx[0] += 1
            return times[i]

        with patch("downloaders.vda_downloader.time", side_effect=fake_time):
            with pytest.raises(ProgressStalledException):
                await downloader.download(
                    "https://www.youtube.com/watch?v=abc", "mp4", progress
                )

    async def test_missing_progress_url_raises(
        self, downloader, progress, mocker
    ):
        bad_ctx, _ = make_aiohttp_response(json_data={}, status=200)
        session_ctx, _ = make_session([bad_ctx, bad_ctx])
        mocker.patch(
            "downloaders.vda_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )

        with pytest.raises(APIException):
            await downloader.download(
                "https://www.youtube.com/watch?v=abc", "mp4", progress
            )

    async def test_file_download_http_error_raises(
        self, downloader, progress, mocker
    ):
        init_ctx = _init_response()
        done_ctx, _ = make_aiohttp_response(
            json_data={"success": 1, "download_url": "https://vda.example/f.mp4"}
        )
        file_ctx, _ = make_aiohttp_response(status=503, text="unavailable")

        session_ctx, _ = make_session([init_ctx, done_ctx, file_ctx])
        mocker.patch(
            "downloaders.vda_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="fid")
        mocker.patch("asyncio.sleep", new_callable=AsyncMock)

        with pytest.raises(DownloadException, match="503"):
            await downloader.download(
                "https://www.youtube.com/watch?v=abc", "mp4", progress
            )
