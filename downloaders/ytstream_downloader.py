"""Primary YouTube downloader: RapidAPI metadata + FFmpeg merge/copy."""
import asyncio
import ipaddress
import os
import signal
import socket
from urllib.parse import parse_qs, urlparse

import aiohttp
from config import DOWNLOAD_DIR
from utils import extract_youtube_id

from .base import BaseDownloader
from .exceptions import (
    APIException,
    DownloadException,
    FFmpegException,
    InvalidURLException,
    StreamNotFoundException,
)
from .progress import ProgressReporter

FFMPEG_TIMEOUT_SEC = 600


async def _stop_ffmpeg(process: asyncio.subprocess.Process) -> None:
    """Kill ffmpeg and its session. start_new_session makes the pid the pgid."""
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError, TypeError):
            try:
                process.kill()
            except (OSError, ProcessLookupError, TypeError):
                pass
    else:
        try:
            process.kill()
        except (OSError, ProcessLookupError, TypeError):
            pass
    try:
        await process.wait()
    except (OSError, ProcessLookupError):
        pass


def _is_googlevideo_host(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    return host == "googlevideo.com" or host.endswith(".googlevideo.com")


def _same_host_ip(
    bound: ipaddress.IPv4Address | ipaddress.IPv6Address,
    source: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if bound == source:
        return True
    if isinstance(bound, ipaddress.IPv6Address) and bound.ipv4_mapped == source:
        return True
    if isinstance(source, ipaddress.IPv6Address) and source.ipv4_mapped == bound:
        return True
    return False


def foreign_locked_playback_ip(
    url: str,
    source_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return the signed ``ip`` when this googlevideo URL cannot be fetched here.

    YouTube puts the address that requested the player response into ``ip``
    and signs it. ytstream's API host is that requester, so the value is
    often a foreign IPv6 address. Playback from any other address — including
    with a browser User-Agent and Referer — is rejected with HTTP 403.
    URLs with no ``ip`` parameter, or whose ``ip`` is one of ``source_ips``,
    are left alone. An empty ``source_ips`` set does not reject anything.
    """
    if not url or not source_ips:
        return None
    parsed = urlparse(url)
    if not _is_googlevideo_host(parsed.hostname or ""):
        return None
    raw_values = parse_qs(parsed.query).get("ip")
    if not raw_values or not raw_values[0]:
        return None
    try:
        bound = ipaddress.ip_address(raw_values[0])
    except ValueError:
        return None
    if any(_same_host_ip(bound, source) for source in source_ips):
        return None
    return bound


def default_route_source_ips() -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Addresses this host would use as the TCP source toward the public internet."""
    found: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    probes = (
        (socket.AF_INET, ("8.8.8.8", 80)),
        (socket.AF_INET6, ("2001:4860:4860::8888", 80)),
    )
    for family, dest in probes:
        try:
            sock = socket.socket(family, socket.SOCK_DGRAM)
            try:
                sock.settimeout(1.0)
                sock.connect(dest)
                addr = sock.getsockname()[0].split("%", 1)[0]
            finally:
                sock.close()
            ip = ipaddress.ip_address(addr)
        except OSError:
            continue
        if ip.is_loopback or ip.is_unspecified or ip.is_link_local:
            continue
        found.add(ip)
    return found


class YtstreamDownloader(BaseDownloader):

    def __init__(self, api_key):
        self.base_url = "https://ytstream-download-youtube-videos.p.rapidapi.com/dl"
        self.headers = {
            "X-RapidAPI-Key": api_key,
            "x-rapidapi-host": "ytstream-download-youtube-videos.p.rapidapi.com",
        }
        self.RECONNECT_ARGS = [
            "-reconnect",
            "1",
            "-reconnect_at_eof",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "2",
            "-timeout",
            "5000000",
        ]

    async def download(
        self, url: str, format_type: str, progress: ProgressReporter
    ) -> dict:
        youtube_id = extract_youtube_id(url)
        if not youtube_id:
            raise InvalidURLException("YouTube URL")
        video_id = self.generate_file_id()
        from core.messages import DOWNLOADING

        await progress.report(DOWNLOADING)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.base_url, headers=self.headers, params={"id": youtube_id}
            ) as response:
                if response.status != 200:
                    raise APIException(response.status, await response.text())
                data = await response.json()

        source_ips = default_route_source_ips()
        data, locked_ips = self._omit_foreign_locked_streams(data, source_ips)
        try:
            if format_type == "mp3":
                cmd = self.download_best_audio(video_id, data)
            else:
                cmd = self.download_best_video(video_id, data)
        except StreamNotFoundException:
            if locked_ips:
                raise DownloadException(
                    403, self._foreign_lock_message(locked_ips, source_ips)
                ) from None
            raise

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=FFMPEG_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            await _stop_ffmpeg(process)
            raise FFmpegException("FFmpeg timed out") from None
        except asyncio.CancelledError:
            await _stop_ffmpeg(process)
            raise

        if process.returncode != 0:
            raise FFmpegException(stderr.decode(errors="replace"))

        title = data.get("title", "video")
        return {"file_id": video_id, "title": title}

    def _omit_foreign_locked_streams(
        self,
        data: dict,
        source_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> tuple[dict, set[ipaddress.IPv4Address | ipaddress.IPv6Address]]:
        locked: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        cleaned = dict(data)
        for key in ("adaptiveFormats", "formats"):
            kept = []
            for item in data.get(key) or []:
                url = item.get("url") if isinstance(item, dict) else None
                if not isinstance(url, str):
                    kept.append(item)
                    continue
                foreign = foreign_locked_playback_ip(url, source_ips)
                if foreign is not None:
                    locked.add(foreign)
                    continue
                kept.append(item)
            cleaned[key] = kept
        return cleaned, locked

    @staticmethod
    def _foreign_lock_message(
        locked_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
        source_ips: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> str:
        locked = ", ".join(sorted(str(ip) for ip in locked_ips))
        via = ", ".join(sorted(str(ip) for ip in source_ips))
        return (
            f"googlevideo URL is locked to {locked}, not this host ({via}); "
            "skipping ffmpeg"
        )

    def download_best_audio(self, video_id: str, data: dict) -> list:
        thumbnail = data.get("thumbnail", [])
        thumbnail_url = None
        if thumbnail:
            thumbnail_url = thumbnail[-1].get("url", None)

        adaptive = data.get("adaptiveFormats", [])
        url = next((f["url"] for f in adaptive if f["itag"] == 140), None)

        if not url:
            audio_streams = [
                f for f in adaptive if "audio" in f.get("mimeType", "")
            ]
            if audio_streams:
                url = sorted(
                    audio_streams, key=lambda x: x.get("bitrate", 0), reverse=True
                )[0]["url"]
            else:
                raise StreamNotFoundException("audio stream")
        output_path = f"{DOWNLOAD_DIR}/{video_id}.mp3"

        if thumbnail_url:
            return [
                "ffmpeg",
                "-i",
                url,
                "-i",
                thumbnail_url,
                "-map",
                "0:a:0",
                "-map",
                "1:v:0",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                "-c:v",
                "mjpeg",
                "-id3v2_version",
                "3",
                "-metadata:s:v",
                'title="Album cover"',
                "-metadata:s:v",
                'comment="Cover (front)"',
                "-y",
                output_path,
            ]

        return [
            "ffmpeg",
            "-i",
            url,
            "-vn",
            "-acodec",
            "libmp3lame",
            "-q:a",
            "2",
            "-y",
            output_path,
        ]

    def download_best_video(self, video_id: str, data: dict) -> list:
        adaptive = data.get("adaptiveFormats", [])
        combined = data.get("formats", [])
        a140 = next((f["url"] for f in adaptive if f["itag"] == 140), None)

        v720_c = next((f["url"] for f in combined if f["itag"] == 22), None)
        v720 = next((f["url"] for f in adaptive if f["itag"] == 136), None)
        v480 = next((f["url"] for f in adaptive if f["itag"] == 135), None)
        v360_c = next((f["url"] for f in combined if f["itag"] == 18), None)

        output_path = f"{DOWNLOAD_DIR}/{video_id}.mp4"

        match (v720_c, v720, v480, v360_c, a140):
            case (stream_url, _, _, _, _) if stream_url:
                return [
                    "ffmpeg",
                    *self.RECONNECT_ARGS,
                    "-i",
                    stream_url,
                    "-c",
                    "copy",
                    "-map",
                    "0",
                    "-y",
                    output_path,
                ]
            case (_, stream_url, _, _, a_url) if stream_url and a_url:
                return [
                    "ffmpeg",
                    *self.RECONNECT_ARGS,
                    "-i",
                    stream_url,
                    "-i",
                    a_url,
                    "-c",
                    "copy",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-shortest",
                    "-y",
                    output_path,
                ]
            case (_, _, stream_url, _, a_url) if stream_url and a_url:
                return [
                    "ffmpeg",
                    *self.RECONNECT_ARGS,
                    "-i",
                    stream_url,
                    "-i",
                    a_url,
                    "-c",
                    "copy",
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-shortest",
                    "-y",
                    output_path,
                ]
            case (_, _, _, stream_url, _) if stream_url:
                return [
                    "ffmpeg",
                    "-i",
                    stream_url,
                    "-c",
                    "copy",
                    "-map",
                    "0",
                    "-y",
                    output_path,
                ]
            case _:
                raise StreamNotFoundException("video stream")
