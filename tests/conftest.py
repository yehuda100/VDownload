"""Shared fixtures: test config injection and async helpers."""
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DOWNLOAD_DIR = tempfile.mkdtemp(prefix="vdownload_dl_")
_LINKS_DIR = tempfile.mkdtemp(prefix="vdownload_links_")

_config = types.ModuleType("config")
_config.DOWNLOAD_DIR = _DOWNLOAD_DIR
_config.TEMP_LINKS_DIR = _LINKS_DIR
_config.SECRET_KEY = "test-secret-key"
_config.URL = "https://test.example/"
_config.EXPIRY = 3600
_config.RAPIDAPI_KEY = "test-rapidapi-key"
_config.VDA_API_KEY = "test-vda-key"
_config.MAX_SIZE = 50 * 1024 * 1024
_config.BOT_TOKEN = "000000:test-token"
_config.USER_ID = 123456789
sys.modules["config"] = _config


class FakeProgress:
    """Records progress messages for assertions."""

    def __init__(self):
        self.messages: list[str] = []

    async def report(self, message: str) -> None:
        self.messages.append(message)


@pytest.fixture
def progress():
    return FakeProgress()


@pytest.fixture
def download_dir():
    return Path(_config.DOWNLOAD_DIR)


class _MockResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        json_data=None,
        text: str = "",
        chunks: list[bytes] | None = None,
    ):
        self.status = status
        self._json_data = json_data if json_data is not None else {}
        self._text = text
        self._chunks = chunks

    async def json(self):
        return self._json_data

    async def text(self):
        return self._text

    @property
    def content(self):
        return self

    def iter_chunked(self, _size):
        return _async_iter(self._chunks or [])


class _MockGetResult:
    """Supports both `async with session.get()` and `await session.get()`."""

    def __init__(self, response: _MockResponse):
        self._response = response

    def __await__(self):
        return _await_response(self._response).__await__()

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return None


async def _await_response(response):
    return response


def make_aiohttp_response(
    *,
    status: int = 200,
    json_data=None,
    text: str = "",
    chunks: list[bytes] | None = None,
):
    """Build a mock aiohttp GET context manager + response."""
    response = _MockResponse(
        status=status, json_data=json_data, text=text, chunks=chunks
    )
    return _MockGetResult(response), response


async def _async_iter(items):
    for item in items:
        yield item


def make_session(get_handlers: list):
    """
    Mock ClientSession whose .get() returns queued mock responses in order.
    Each handler is either a context manager from make_aiohttp_response or callable(url, **kw).
    """
    queue = list(get_handlers)
    calls = []

    def get(url, *args, **kwargs):
        calls.append((url, args, kwargs))
        if not queue:
            raise RuntimeError("Unexpected HTTP GET")
        item = queue.pop(0)
        if callable(item):
            return item(url, *args, **kwargs)
        return item

    class _Session:
        _calls = calls

        @staticmethod
        def get(url, *args, **kwargs):
            return get(url, *args, **kwargs)

    class _SessionCtx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *args):
            return None

    return _SessionCtx(), _Session()
