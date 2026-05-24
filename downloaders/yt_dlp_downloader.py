"""Generic platform downloader via yt-dlp (runs in a thread pool)."""
import asyncio

import yt_dlp
from config import DOWNLOAD_DIR

from .base import BaseDownloader
from .exceptions import ExtractionException
from .progress import ProgressReporter


class YtDlpDownloader(BaseDownloader):

    def __init__(self):
        self.opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }

    def build_options(self, file_id: str, format_type: str) -> dict:
        opts = self.opts.copy()
        opts["outtmpl"] = f"{DOWNLOAD_DIR}/{file_id}.%(ext)s"
        if format_type == "mp3":
            opts.update({
                "format": "bestaudio/best",
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
                ],
            })
        else:
            opts.update({
                "format": (
                    "(bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]"
                    "/best[ext=mp4][height<=720]"
                    "/bestvideo[height<=720]+bestaudio"
                    "/best)"
                ),
                "merged_output_format": "mp4",
            })
        return opts

    async def download(
        self, url: str, format_type: str, progress: ProgressReporter
    ) -> dict:
        file_id = self.generate_file_id()
        ydl_opts = self.build_options(file_id, format_type)

        def run_download():
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(url, download=True)
            except yt_dlp.utils.DownloadError as e:
                raise ExtractionException(f"Failed to download video: {e}") from e
            except Exception as e:
                raise ExtractionException(f"Unexpected error during download: {e}") from e

        await progress.report("Starting download...")
        info = await asyncio.to_thread(run_download)
        title = info.get("title", "video")
        await progress.report("Download complete.")
        return {"file_id": file_id, "title": title}
