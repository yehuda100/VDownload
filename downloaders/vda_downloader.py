"""Fallback YouTube downloader via VDA API (poll progress, then stream file)."""
import asyncio
import json
import logging
import os
from time import time

import aiofiles
import aiohttp
from config import DOWNLOAD_DIR
from utils.file_utils import remove_partial_downloads

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

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SEC = 600
STALL_TIMEOUT_SEC = 30
FILE_PROGRESS_INTERVAL_SEC = 1.5
# aiohttp's default ClientTimeout(total=300) covers the whole response body.
# A file that is still streaming past five minutes raises TimeoutError from
# iter_chunked. No total cap: the transfer may run as long as bytes arrive.
# sock_read fails the GET only when the peer stops sending.
FILE_SOCK_CONNECT_TIMEOUT_SEC = 30
FILE_SOCK_READ_TIMEOUT_SEC = 60
FILE_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(
    total=None,
    sock_connect=FILE_SOCK_CONNECT_TIMEOUT_SEC,
    sock_read=FILE_SOCK_READ_TIMEOUT_SEC,
)


def _unlink_partial(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        return
    except OSError:
        logger.exception("Could not remove partial download %s", path)


async def _load_provider_payload(session, url: str, params: dict) -> tuple[int, str, dict | None]:
    """Fetch one VDA init response.

    Status is checked before the body is parsed. A non-200 or non-JSON body
    returns ``None`` so the caller can try the other host. Timeouts and
    connection errors are reported the same way instead of escaping as
    ``ClientError`` / ``TimeoutError``.
    """
    try:
        async with session.get(url, params=params) as response:
            status = response.status
            if status != 200:
                return status, await response.text(), None
            try:
                data = await response.json()
            except (json.JSONDecodeError, aiohttp.ContentTypeError):
                return status, "Response was not JSON", None
    except TimeoutError:
        return 0, "Request timed out", None
    except aiohttp.ClientError as exc:
        return 0, type(exc).__name__, None
    if not isinstance(data, dict):
        return status, "Response was not a JSON object", None
    return status, "", data


async def _report_bytes(progress, formatter, downloaded: int, total, elapsed: float) -> None:
    speed = downloaded / elapsed if elapsed > 0 else None
    text = formatter(downloaded, total, speed)
    if text:
        await progress.report(text)


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
        from core.messages import DOWNLOADING, format_byte_progress, format_prepare_percent

        await progress.report(DOWNLOADING)

        async with aiohttp.ClientSession() as session:
            response_data = None
            last_status = 0
            last_detail = ""
            for endpoint in (self.base_url, self.secondary_url):
                status, detail, payload = await _load_provider_payload(
                    session, endpoint, params
                )
                last_status, last_detail = status, detail
                if payload and payload.get("progress_url"):
                    response_data = payload
                    break
            if response_data is None:
                raise APIException(last_status, last_detail or "Progress URL not available")

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
                        download_url = progress_data.get("download_url")
                        if not download_url:
                            raise DownloadURLNotFoundException()
                        break

                    raw = progress_data.get("progress", -1)
                    new_progress = raw if isinstance(raw, (int, float)) else -1

                    if new_progress >= 1000:
                        if display_progress < 1000:
                            await progress.report(format_prepare_percent(100))
                            display_progress = 1000
                        last_change_time = time()
                    elif new_progress >= 0:
                        if new_progress > display_progress:
                            display_progress = new_progress
                            last_change_time = time()
                            pct = min(100.0, new_progress / 10.0)
                            await progress.report(format_prepare_percent(pct))
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

            try:
                async with session.get(
                    download_url, timeout=FILE_DOWNLOAD_TIMEOUT
                ) as response:
                    if response.status != 200:
                        raise DownloadException(
                            response.status, await response.text()
                        )
                    downloaded = 0
                    started = time()
                    last_report = started
                    total = getattr(response, "content_length", None)
                    async with aiofiles.open(dest, "wb") as f:
                        async for chunk in response.content.iter_chunked(65536):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            now = time()
                            if now - last_report >= FILE_PROGRESS_INTERVAL_SEC:
                                await _report_bytes(
                                    progress,
                                    format_byte_progress,
                                    downloaded,
                                    total,
                                    now - started,
                                )
                                last_report = now
                    await _report_bytes(
                        progress,
                        format_byte_progress,
                        downloaded,
                        total,
                        time() - started,
                    )
            except TimeoutError as exc:
                _unlink_partial(dest)
                remove_partial_downloads(file_id)
                raise ProgressStalledException(
                    timeout=FILE_SOCK_READ_TIMEOUT_SEC
                ) from exc
            except BaseException:
                remove_partial_downloads(file_id)
                raise

        return {"file_id": file_id, "title": title}
