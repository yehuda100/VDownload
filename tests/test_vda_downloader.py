"""Unit tests for vda_downloader."""
import asyncio
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from downloaders.vda_downloader import (
    DOWNLOAD_DIR,
    FILE_DOWNLOAD_TIMEOUT,
    FILE_SOCK_CONNECT_TIMEOUT_SEC,
    FILE_SOCK_READ_TIMEOUT_SEC,
    STALL_TIMEOUT_SEC,
    VdaDownloader,
)
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
        assert progress.messages[0] == "Downloading..."
        assert "Preparing the file... 50%" in progress.messages
        assert "Download complete." not in progress.messages
        assert all("VDA" not in m for m in progress.messages)

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
        assert progress.messages[0] == "Downloading..."
        assert all("API" not in m and "secondary" not in m.lower() for m in progress.messages)

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

        pct_messages = [m for m in progress.messages if m.startswith("Preparing")]
        assert pct_messages == ["Preparing the file... 50%"]

    async def test_file_download_reports_size(self, downloader, progress, mocker):
        init_ctx = _init_response()
        done_ctx, _ = make_aiohttp_response(
            json_data={"success": 1, "download_url": "https://vda.example/f.mp4"}
        )
        payload = b"x" * (2 * 1024 * 1024)
        file_ctx, file_resp = make_aiohttp_response(chunks=[payload])
        file_resp.content_length = len(payload)

        session_ctx, _ = make_session([init_ctx, done_ctx, file_ctx])
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

        assert any("Downloading... 2.0 / 2.0 MB" in m for m in progress.messages)

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

    async def test_file_get_has_no_total_timeout(self, downloader, progress, mocker):
        """Large-file GET must not inherit aiohttp's 300s total cap."""
        init_ctx = _init_response()
        done_ctx, _ = make_aiohttp_response(
            json_data={"success": 1, "download_url": "https://vda.example/file.bin"}
        )
        file_ctx, _ = make_aiohttp_response(chunks=[b"x"])

        session_ctx, session = make_session([init_ctx, done_ctx, file_ctx])
        session_cls = mocker.patch(
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

        await downloader.download(
            "https://www.youtube.com/watch?v=abc", "mp4", progress
        )

        assert FILE_DOWNLOAD_TIMEOUT.total is None
        assert FILE_DOWNLOAD_TIMEOUT.sock_connect == FILE_SOCK_CONNECT_TIMEOUT_SEC
        assert FILE_DOWNLOAD_TIMEOUT.sock_read == FILE_SOCK_READ_TIMEOUT_SEC
        assert "timeout" not in session_cls.call_args.kwargs

        file_calls = [c for c in session._calls if c[0].endswith("/file.bin")]
        assert len(file_calls) == 1
        assert file_calls[0][2]["timeout"] is FILE_DOWNLOAD_TIMEOUT

        other_calls = [c for c in session._calls if not c[0].endswith("/file.bin")]
        assert other_calls
        assert all("timeout" not in c[2] for c in other_calls)


def _local_vda_app(chunks, *, gap_sec=0.0, stall_after_first=False):
    """Local stand-in for the VDA init, progress, and file endpoints."""

    async def init(request):
        base = f"{request.scheme}://{request.host}"
        return web.json_response(
            {"progress_url": f"{base}/progress", "title": "Slow Song"}
        )

    async def progress(request):
        base = f"{request.scheme}://{request.host}"
        return web.json_response(
            {"success": 1, "download_url": f"{base}/file"}
        )

    async def file_stream(_request):
        response = web.StreamResponse()
        await response.prepare(_request)
        for index, chunk in enumerate(chunks):
            if stall_after_first and index > 0:
                await asyncio.sleep(5)
            await response.write(chunk)
            if gap_sec and index < len(chunks) - 1:
                await asyncio.sleep(gap_sec)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/ajax/download.php", init)
    app.router.add_get("/progress", progress)
    app.router.add_get("/file", file_stream)
    return app


class TestFileStreamTimeout:
    async def test_slow_stream_completes_while_data_flows(
        self, downloader, progress, mocker, monkeypatch
    ):
        """Elapsed time may exceed sock_read when chunks keep arriving."""
        read_timeout = 0.35
        gap_sec = 0.05
        # 15 gaps of 0.05s outlast one sock_read window; each gap stays under it.
        chunks = [bytes([index]) * 128 for index in range(16)]
        monkeypatch.setattr(
            "downloaders.vda_downloader.FILE_DOWNLOAD_TIMEOUT",
            aiohttp.ClientTimeout(
                total=None, sock_connect=5, sock_read=read_timeout
            ),
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="slow-id")

        app = _local_vda_app(chunks, gap_sec=gap_sec)
        async with TestServer(app) as server:
            downloader.base_url = str(server.make_url("/ajax/download.php"))
            downloader.secondary_url = str(server.make_url("/unused"))
            started = monotonic()
            result = await downloader.download(
                "https://www.youtube.com/watch?v=abc", "mp3", progress
            )
            elapsed = monotonic() - started

        assert result == {"file_id": "slow-id", "title": "Slow Song"}
        # Gaps between chunks add up to more than one sock_read window.
        assert elapsed > read_timeout
        dest = Path(DOWNLOAD_DIR) / "slow-id.mp3"
        assert dest.read_bytes() == b"".join(chunks)
        dest.unlink()

    async def test_stalled_stream_raises(self, downloader, progress, monkeypatch):
        """A socket that stops sending fails without waiting out a long total."""
        monkeypatch.setattr(
            "downloaders.vda_downloader.FILE_DOWNLOAD_TIMEOUT",
            aiohttp.ClientTimeout(total=None, sock_connect=5, sock_read=0.4),
        )
        app = _local_vda_app([b"partial", b"never"], stall_after_first=True)
        async with TestServer(app) as server:
            downloader.base_url = str(server.make_url("/ajax/download.php"))
            downloader.secondary_url = str(server.make_url("/unused"))
            started = monotonic()
            with pytest.raises(aiohttp.ServerTimeoutError):
                await downloader.download(
                    "https://www.youtube.com/watch?v=abc", "mp3", progress
                )
            elapsed = monotonic() - started

        assert elapsed < 2.0
