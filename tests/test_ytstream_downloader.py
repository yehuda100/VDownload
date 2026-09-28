"""Unit tests for ytstream_downloader."""
import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from downloaders.ytstream_downloader import (
    YtstreamDownloader,
    default_route_source_ips,
    foreign_locked_playback_ip,
)
from downloaders.exceptions import (
    InvalidURLException,
    APIException,
    DownloadException,
    StreamNotFoundException,
    FFmpegException,
)
from tests.conftest import make_aiohttp_response, make_session
import config


HOST_IPV4 = ipaddress.ip_address("23.238.1.237")
FOREIGN_IPV6 = ipaddress.ip_address("2a02:3035:b7a:a44b:f76e:ddd4:89da:738a")
FOREIGN_IPV4 = ipaddress.ip_address("80.187.120.40")


def _googlevideo(ip: str, itag: int = 140) -> str:
    return (
        "https://redirector.googlevideo.com/videoplayback"
        f"?expire=1790613954&ip={ip}&itag={itag}&mime=audio%2Fmp4"
    )


FOREIGN_GV = _googlevideo("2a02%3A3035%3Ab7a%3Aa44b%3Af76e%3Addd4%3A89da%3A738a")


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


class TestForeignLockedPlaybackIp:
    def test_percent_encoded_ipv6_from_production_log(self):
        locked = foreign_locked_playback_ip(FOREIGN_GV, {HOST_IPV4})
        assert locked == FOREIGN_IPV6

    def test_foreign_ipv4_is_locked(self):
        url = _googlevideo("80.187.120.40", itag=136)
        assert foreign_locked_playback_ip(url, {HOST_IPV4}) == FOREIGN_IPV4

    def test_matching_source_ip_is_usable(self):
        url = _googlevideo("23.238.1.237", itag=22)
        assert foreign_locked_playback_ip(url, {HOST_IPV4}) is None

    def test_ipv4_mapped_source_matches(self):
        mapped = ipaddress.ip_address("::ffff:23.238.1.237")
        url = _googlevideo("23.238.1.237")
        assert foreign_locked_playback_ip(url, {mapped}) is None

    def test_non_googlevideo_ip_param_is_ignored(self):
        url = "https://combined.example/720.mp4?ip=1.2.3.4"
        assert foreign_locked_playback_ip(url, {HOST_IPV4}) is None

    def test_missing_ip_param_is_usable(self):
        url = "https://redirector.googlevideo.com/videoplayback?itag=18"
        assert foreign_locked_playback_ip(url, {HOST_IPV4}) is None

    def test_empty_source_set_does_not_reject(self):
        assert foreign_locked_playback_ip(FOREIGN_GV, set()) is None

    def test_malformed_ip_is_not_treated_as_locked(self):
        url = _googlevideo("not-an-ip")
        assert foreign_locked_playback_ip(url, {HOST_IPV4}) is None

    def test_default_route_source_ips_returns_addresses(self):
        for ip in default_route_source_ips():
            assert isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address))
            assert not ip.is_loopback


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

    async def test_timeout_and_client_error_are_api_exceptions(
        self, downloader, progress, mocker
    ):
        class _Session:
            def __init__(self, error):
                self._error = error

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def get(self, *args, **kwargs):
                raise self._error

        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=_Session(TimeoutError()),
        )
        with pytest.raises(APIException, match="timed out"):
            await downloader.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
            )

        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=_Session(aiohttp.ClientConnectionError("reset")),
        )
        with pytest.raises(APIException, match="ClientConnectionError"):
            await downloader.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
            )

    async def test_non_json_body_is_api_exception(self, downloader, progress, mocker):
        import json

        class _Response:
            status = 200

            async def text(self):
                return "<html>"

            async def json(self):
                raise json.JSONDecodeError("Expecting value", "<html>", 0)

        class _Get:
            async def __aenter__(self):
                return _Response()

            async def __aexit__(self, *args):
                return None

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            def get(self, *args, **kwargs):
                return _Get()

        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=_Session(),
        )
        with pytest.raises(APIException, match="not JSON"):
            await downloader.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
            )

    async def test_missing_stream_url_is_stream_not_found(
        self, downloader, progress, mocker
    ):
        data = {
            "title": "Broken",
            "adaptiveFormats": [{"itag": 140}],
            "formats": [{"itag": 22}],
        }
        api_ctx, _ = make_aiohttp_response(json_data=data)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch(
            "downloaders.ytstream_downloader.default_route_source_ips",
            return_value=set(),
        )
        exec_mock = mocker.patch("asyncio.create_subprocess_exec")
        with pytest.raises(StreamNotFoundException):
            await downloader.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
            )
        exec_mock.assert_not_called()

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
        assert progress.messages[0] == "Downloading..."
        assert progress.messages[-1] == "Downloading..."
        assert all("ytstream" not in m.lower() for m in progress.messages)

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

    async def test_foreign_locked_url_skips_ffmpeg(self, downloader, progress, mocker):
        data = {
            "title": "Locked",
            "adaptiveFormats": [
                {"itag": 140, "url": FOREIGN_GV, "mimeType": "audio/mp4"},
                {
                    "itag": 136,
                    "url": _googlevideo(
                        "2a02%3A3035%3Ab7a%3Aa44b%3Af76e%3Addd4%3A89da%3A738a",
                        136,
                    ),
                },
            ],
            "formats": [
                {"itag": 22, "url": _googlevideo("80.187.120.40", 22)},
            ],
        }
        api_ctx, _ = make_aiohttp_response(json_data=data)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch(
            "downloaders.ytstream_downloader.default_route_source_ips",
            return_value={HOST_IPV4},
        )
        exec_mock = mocker.patch(
            "asyncio.create_subprocess_exec", new_callable=MagicMock
        )

        with pytest.raises(DownloadException, match="locked to") as exc_info:
            await downloader.download(
                "https://youtu.be/tS-ofBMxaCQ", "mp3", progress
            )

        message = str(exc_info.value)
        assert "2a02:3035:b7a:a44b:f76e:ddd4:89da:738a" in message
        assert "23.238.1.237" in message
        assert "skipping ffmpeg" in message
        exec_mock.assert_not_called()
        assert not any("FFmpeg" in msg for msg in progress.messages)

    async def test_empty_streams_remain_stream_not_found(
        self, downloader, progress, mocker
    ):
        data = {"title": "Empty", "adaptiveFormats": [], "formats": []}
        api_ctx, _ = make_aiohttp_response(json_data=data)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch("asyncio.create_subprocess_exec", new_callable=MagicMock)

        with pytest.raises(StreamNotFoundException):
            await downloader.download(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
            )

    async def test_unlocked_format_is_used_when_another_is_locked(
        self, downloader, progress, mocker
    ):
        data = {
            "title": "Mixed",
            "adaptiveFormats": [
                {"itag": 140, "url": "https://audio.example/a.m4a", "mimeType": "audio/mp4"},
            ],
            "formats": [
                {"itag": 22, "url": FOREIGN_GV},
                {"itag": 18, "url": "https://combined.example/360.mp4"},
            ],
        }
        api_ctx, _ = make_aiohttp_response(json_data=data)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch(
            "downloaders.ytstream_downloader.default_route_source_ips",
            return_value={HOST_IPV4},
        )
        mocker.patch.object(downloader, "generate_file_id", return_value="mixed-id")

        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = 0
        mock_process.kill = lambda: None
        mock_process.wait = AsyncMock()
        exec_mock = mocker.patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        )

        result = await downloader.download(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
        )

        cmd = exec_mock.call_args.args
        assert result["file_id"] == "mixed-id"
        assert "https://combined.example/360.mp4" in cmd
        assert all("googlevideo.com" not in arg for arg in cmd)

    async def test_matching_host_ip_still_invokes_ffmpeg(
        self, downloader, progress, mocker
    ):
        data = {
            "title": "Local",
            "adaptiveFormats": [],
            "formats": [
                {"itag": 22, "url": _googlevideo("23.238.1.237", 22)},
            ],
        }
        api_ctx, _ = make_aiohttp_response(json_data=data)
        session_ctx, _ = make_session([api_ctx])
        mocker.patch(
            "downloaders.ytstream_downloader.aiohttp.ClientSession",
            return_value=session_ctx,
        )
        mocker.patch(
            "downloaders.ytstream_downloader.default_route_source_ips",
            return_value={HOST_IPV4},
        )
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = 0
        mock_process.kill = lambda: None
        mock_process.wait = AsyncMock()
        exec_mock = mocker.patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        )

        await downloader.download(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mp4", progress
        )

        assert _googlevideo("23.238.1.237", 22) in exec_mock.call_args.args
