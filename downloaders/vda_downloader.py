"""Fallback YouTube downloader via VDA API (poll progress, then stream file)."""
import asyncio
from time import time

import aiofiles
import aiohttp
from config import DOWNLOAD_DIR

from .base import BaseDownloader
from .exceptions import (
    APIException,
    DownloadException,
    DownloadURLNotFoundException,
    ProgressException,
    ProgressStalledException,
    ProgressURLNotFoundException,
)
from .progress import ProgressReporter

POLL_TIMEOUT_SEC = 600
STALL_TIMEOUT_SEC = 30


class VdaDownloader(BaseDownloader):

    def __init__(self, api_key):
        self.base_url = "https://p.savenow.to/ajax/download.php"
        self.secondary_url = "https://p.lbserver.xyz/ajax/download.php"
        self.params = {"apikey": api_key}

    async def download(
        self, url: str, format_type: str, progress: ProgressReporter
    ) -> dict:
        params = self.params.copy()
        params["url"] = url
        if format_type == "mp3":
            params["format"] = "mp3"
        else:
            params["format"] = "720"

        ext = "mp3" if format_type == "mp3" else "mp4"
        deadline = time() + POLL_TIMEOUT_SEC

        async with aiohttp.ClientSession() as session:
            async with session.get(self.base_url, params=params) as response:
                await progress.report("Getting link data from VDA...")
                response_data = await response.json()
                if response.status != 200 or not response_data.get("progress_url"):
                    async with session.get(
                        self.secondary_url, params=params
                    ) as secondary_response:
                        await progress.report(
                            "Primary API failed, trying secondary API..."
                        )
                        response_data = await secondary_response.json()
                        if secondary_response.status != 200 or not response_data.get(
                            "progress_url"
                        ):
                            raise APIException(
                                secondary_response.status,
                                await secondary_response.text(),
                            )

            progress_url = response_data.get("progress_url")
            if not progress_url:
                raise ProgressURLNotFoundException()
            title = response_data.get("title", "video")

            # High-water mark for display only (VDA may reset progress between phases).
            display_progress = -1
            last_change_time = time()
            download_url = None

            while True:
                if time() > deadline:
                    raise ProgressException("VDA download timed out")

                try:
                    progress_response = await session.get(progress_url)
                    if progress_response.status != 200:
                        raise APIException(
                            progress_response.status,
                            await progress_response.text(),
                        )

                    progress_data = await progress_response.json()

                    if progress_data.get("success", 0) == 1:
                        await progress.report("Download ready.")
                        download_url = progress_data.get("download_url")
                        if not download_url:
                            raise DownloadURLNotFoundException()
                        break

                    raw = progress_data.get("progress", -1)
                    new_progress = raw if isinstance(raw, (int, float)) else -1

                    if new_progress >= 1000:
                        if display_progress < 1000:
                            await progress.report("Processing... 100%")
                            display_progress = 1000
                        last_change_time = time()
                    elif new_progress >= 0:
                        if new_progress > display_progress:
                            display_progress = new_progress
                            last_change_time = time()
                            pct = min(100.0, new_progress / 10.0)
                            await progress.report(f"Processing... {pct:.1f}%")
                        elif new_progress < display_progress:
                            # Server reset progress for a new phase — keep UI, reset stall clock
                            last_change_time = time()
                        elif time() - last_change_time > STALL_TIMEOUT_SEC:
                            raise ProgressStalledException(
                                timeout=int(time() - last_change_time)
                            )

                    await asyncio.sleep(1)

                except (
                    APIException,
                    DownloadURLNotFoundException,
                    ProgressStalledException,
                ):
                    raise
                except Exception as e:
                    raise ProgressException(
                        f"Failed to retrieve progress data: {e}"
                    ) from e

            file_id = self.generate_file_id()
            dest = f"{DOWNLOAD_DIR}/{file_id}.{ext}"

            async with session.get(download_url) as response:
                await progress.report("Downloading file...")
                if response.status != 200:
                    raise DownloadException(
                        response.status, await response.text()
                    )
                async with aiofiles.open(dest, "wb") as f:
                    async for chunk in response.content.iter_chunked(65536):
                        await f.write(chunk)

        await progress.report("Download complete.")
        return {"file_id": file_id, "title": title}
