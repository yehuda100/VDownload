"""
Download orchestration: platform detection and provider fallback chain.

YouTube: ytstream -> VDA. Other URLs: yt-dlp only.
"""
from collections.abc import Callable

from config import RAPIDAPI_KEY, VDA_API_KEY
from downloaders import (
    BaseDownloader,
    VdaDownloader,
    YtDlpDownloader,
    YtstreamDownloader,
)
from downloaders.exceptions import DownloaderException
from utils import is_youtube_url

from .download_audit import (
    DownloadRequest,
    log_provider_attempt,
    log_provider_failed,
    log_provider_success,
)
from .status_updater import StatusUpdater

_vda: VdaDownloader | None = None
_ytstream: YtstreamDownloader | None = None
_yt_dlp: YtDlpDownloader | None = None


def get_vda_downloader() -> VdaDownloader:
    global _vda
    if _vda is None:
        _vda = VdaDownloader(VDA_API_KEY)
    return _vda


def get_ytstream_downloader() -> YtstreamDownloader:
    global _ytstream
    if _ytstream is None:
        _ytstream = YtstreamDownloader(RAPIDAPI_KEY)
    return _ytstream


def get_yt_dlp_downloader() -> YtDlpDownloader:
    global _yt_dlp
    if _yt_dlp is None:
        _yt_dlp = YtDlpDownloader()
    return _yt_dlp


_YOUTUBE_CHAIN: tuple[tuple[str, Callable[[], BaseDownloader]], ...] = (
    ("ytstream", get_ytstream_downloader),
    ("vda", get_vda_downloader),
)


async def _download_with_fallback(
    chain: tuple[tuple[str, Callable[[], BaseDownloader]], ...],
    url: str,
    format_type: str,
    status_updater: StatusUpdater,
    request: DownloadRequest,
) -> tuple[dict, str]:
    last_exc: DownloaderException | None = None
    names = [name for name, _ in chain]

    for i, (name, get_downloader) in enumerate(chain):
        next_name = names[i + 1] if i + 1 < len(names) else None
        log_provider_attempt(request, name, platform="youtube")
        try:
            result = await get_downloader().download(url, format_type, status_updater)
            log_provider_success(request, name, result)
            return result, name
        except DownloaderException as e:
            log_provider_failed(request, name, e, next_provider=next_name)
            last_exc = e

    if last_exc:
        raise last_exc
    raise RuntimeError("No downloaders configured")


async def download(
    url: str,
    format_type: str,
    status_updater: StatusUpdater,
    request: DownloadRequest,
) -> tuple[dict, str]:
    """Run download for URL. Returns (result dict, winning provider name)."""
    if is_youtube_url(url):
        return await _download_with_fallback(
            _YOUTUBE_CHAIN, url, format_type, status_updater, request
        )

    name = "yt-dlp"
    log_provider_attempt(request, name, platform="generic")
    try:
        result = await get_yt_dlp_downloader().download(
            url, format_type, status_updater
        )
        log_provider_success(request, name, result)
        return result, name
    except DownloaderException as e:
        log_provider_failed(request, name, e, next_provider=None)
        raise
