"""Primary YouTube downloader: RapidAPI metadata + FFmpeg merge/copy."""
import asyncio

import aiohttp
from config import DOWNLOAD_DIR
from utils import extract_youtube_id

from .base import BaseDownloader
from .exceptions import (
    APIException,
    FFmpegException,
    InvalidURLException,
    StreamNotFoundException,
)
from .progress import ProgressReporter

FFMPEG_TIMEOUT_SEC = 600


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

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.base_url, headers=self.headers, params={"id": youtube_id}
            ) as response:
                await progress.report("Getting video info from ytstream...")
                if response.status != 200:
                    raise APIException(response.status, await response.text())
                data = await response.json()

        if format_type == "mp3":
            cmd = self.download_best_audio(video_id, data)
        else:
            cmd = self.download_best_video(video_id, data)

        await progress.report("Downloading and processing with FFmpeg...")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=FFMPEG_TIMEOUT_SEC
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise FFmpegException("FFmpeg timed out") from None

        if process.returncode != 0:
            raise FFmpegException(stderr.decode(errors="replace"))

        title = data.get("title", "video")
        await progress.report("Download complete.")
        return {"file_id": video_id, "title": title}

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
