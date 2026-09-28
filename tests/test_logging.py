"""Token redaction and informative audit error text."""
import asyncio
import io
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import BOT_TOKEN
from core.download_audit import (
    DownloadRequest,
    format_error,
    log_download_failed,
    log_provider_failed,
)
from core.download_manager import _download_with_fallback
from core.logging_config import (
    RedactingFormatter,
    TelegramTokenRedactingFilter,
    configure_logging,
    redact_telegram_token,
)
from core.telegram_bot import TelegramVideoBot
from downloaders.exceptions import APIException, DownloaderException
from tests.conftest import FakeProgress

# Realistic Bot API token shape: numeric id + 35-char secret.
_TOKEN = "123456789:AAHabcdefghijklmnopqrstuvwxyz012345"
_URL = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"


def _request() -> DownloadRequest:
    return DownloadRequest(
        user_id=1,
        username="tester",
        chat_id=1,
        format_type="mp4",
        url="https://youtu.be/V9TCk-qCIxI",
    )


def _capture_logger(name: str, *, formatter: logging.Formatter, log_filter: logging.Filter | None):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(formatter)
    if log_filter is not None:
        handler.addFilter(log_filter)
    logger.addHandler(handler)
    return logger, buffer


class TestRedactTelegramToken:
    def test_leaves_ordinary_audit_text(self):
        sample = (
            "PROVIDER_FAIL | user_id=258871997 chat_id=258871997 @yehuda100 | "
            "provider=yt-dlp | error=Failed to download video: ERROR: HTTP Error 403: Forbidden | "
            "next=none"
        )
        assert redact_telegram_token(sample) == sample

    def test_redacts_bot_api_url(self):
        text = f'HTTP Request: POST {_URL} "HTTP/1.1 200 OK"'
        redacted = redact_telegram_token(text)
        assert _TOKEN not in redacted
        assert "bot<REDACTED>/sendMessage" in redacted

    def test_redacts_percent_encoded_colon(self):
        encoded = _TOKEN.replace(":", "%3A")
        redacted = redact_telegram_token(f"https://api.telegram.org/bot{encoded}/getMe")
        assert encoded not in redacted
        assert _TOKEN not in redacted
        assert "<REDACTED>" in redacted

    def test_redacts_configured_token_even_if_short(self):
        # conftest token does not match the 30+ secret regex; it must still be removed.
        assert len(BOT_TOKEN.split(":", 1)[1]) < 30
        redacted = redact_telegram_token(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
        )
        assert BOT_TOKEN not in redacted
        assert "bot<REDACTED>/setWebhook" in redacted

    def test_redacts_multiple_tokens(self):
        other = "987654321:BBHabcdefghijklmnopqrstuvwxyz012345"
        redacted = redact_telegram_token(f"{_TOKEN} and {other}")
        assert _TOKEN not in redacted
        assert other not in redacted
        assert redacted.count("<REDACTED>") == 2


class TestRedactingFilter:
    def test_redacts_url_object_and_keeps_warnings(self):
        class Url:
            def __str__(self) -> str:
                return _URL

        logger, buffer = _capture_logger(
            "test.logging.filter",
            formatter=logging.Formatter("%(levelname)s %(message)s"),
            log_filter=TelegramTokenRedactingFilter(),
        )
        try:
            logger.info('HTTP Request: POST %s "HTTP/1.1 200 OK"', Url())
            logger.warning(
                "Pool timeout: All connections in the connection pool are occupied."
            )
            logger.error("delivery failed for %(url)s", {"url": _URL})
        finally:
            logger.handlers.clear()

        text = buffer.getvalue()
        assert _TOKEN not in text
        assert 'HTTP Request: POST https://api.telegram.org/bot<REDACTED>/sendMessage "HTTP/1.1 200 OK"' in text
        assert "WARNING Pool timeout: All connections in the connection pool are occupied." in text
        assert "ERROR delivery failed for" in text

    def test_redacts_traceback_and_keeps_exception_type(self):
        logger, buffer = _capture_logger(
            "test.logging.traceback",
            formatter=logging.Formatter("%(message)s"),
            log_filter=TelegramTokenRedactingFilter(),
        )
        try:
            try:
                raise RuntimeError(f"request failed for {_URL}")
            except RuntimeError:
                logger.exception("Unexpected error while downloading")
        finally:
            logger.handlers.clear()

        text = buffer.getvalue()
        assert _TOKEN not in text
        assert "Unexpected error while downloading" in text
        assert "RuntimeError" in text
        assert "Traceback (most recent call last):" in text
        assert "<REDACTED>" in text

    def test_formatter_redacts_even_without_filter(self):
        logger, buffer = _capture_logger(
            "test.logging.formatter",
            formatter=RedactingFormatter("%(message)s"),
            log_filter=None,
        )
        try:
            try:
                raise RuntimeError(_URL)
            except RuntimeError:
                logger.error("send failed", exc_info=True)
        finally:
            logger.handlers.clear()

        text = buffer.getvalue()
        assert _TOKEN not in text
        assert "send failed" in text
        assert "RuntimeError" in text
        assert "<REDACTED>" in text


class TestConfigureLogging:
    def test_http_client_info_hidden_warnings_redacted(self):
        root = logging.getLogger()
        httpx_logger = logging.getLogger("httpx")
        httpcore_logger = logging.getLogger("httpcore")
        audit_logger = logging.getLogger("vdownload.audit")
        saved = {
            "handlers": root.handlers[:],
            "level": root.level,
            "httpx_level": httpx_logger.level,
            "httpx_filters": httpx_logger.filters[:],
            "httpcore_level": httpcore_logger.level,
            "audit_level": audit_logger.level,
        }
        root.handlers.clear()
        try:
            configure_logging()
            configure_logging()
            assert len(root.handlers) == 1
            assert httpx_logger.getEffectiveLevel() == logging.WARNING
            assert logging.getLogger("httpcore.http11").getEffectiveLevel() == logging.WARNING
            assert audit_logger.getEffectiveLevel() == logging.INFO

            buffer = io.StringIO()
            root.handlers[0].setStream(buffer)

            httpx_logger.info("HTTP Request: POST %s INFO_SHOULD_BE_HIDDEN", _URL)
            httpx_logger.warning(
                'HTTP Request: POST %s "HTTP/1.1 400 Bad Request"', _URL
            )
            logging.getLogger("httpcore.connection").debug(
                "connect DEBUG_SHOULD_BE_HIDDEN %s", _URL
            )
            logging.getLogger("httpcore.connection").warning(
                "connection failed for %s", _URL
            )
            audit_logger.info("DOWNLOAD_START | still visible")
            logging.getLogger("core.telegram_bot").error(
                "Failed to send file, token=%s", BOT_TOKEN
            )
            try:
                raise asyncio.TimeoutError()
            except TimeoutError:
                logging.getLogger("core.telegram_bot").exception(
                    "Unexpected error while downloading %s", _URL
                )
        finally:
            root.handlers[:] = saved["handlers"]
            root.setLevel(saved["level"])
            httpx_logger.setLevel(saved["httpx_level"])
            httpx_logger.filters[:] = saved["httpx_filters"]
            httpcore_logger.setLevel(saved["httpcore_level"])
            audit_logger.setLevel(saved["audit_level"])

        text = buffer.getvalue()
        assert _TOKEN not in text
        assert BOT_TOKEN not in text
        assert "INFO_SHOULD_BE_HIDDEN" not in text
        assert "DEBUG_SHOULD_BE_HIDDEN" not in text
        assert "400 Bad Request" in text
        assert "bot<REDACTED>/sendMessage" in text
        assert "connection failed for" in text
        assert "DOWNLOAD_START | still visible" in text
        assert "Failed to send file, token=<REDACTED>" in text
        assert "TimeoutError" in text
        assert "Unexpected error while downloading" in text


class TestFormatError:
    def test_timeout_error_uses_type_name(self):
        assert str(asyncio.TimeoutError()) == ""
        assert format_error(asyncio.TimeoutError()) == "TimeoutError"
        assert format_error(TimeoutError()) == "TimeoutError"

    def test_blank_message_uses_type_name(self):
        class Blank(Exception):
            def __str__(self) -> str:
                return " \n"

        assert format_error(Blank()) == "Blank"
        assert format_error(DownloaderException()) == "DownloaderException"

    def test_keeps_existing_message(self):
        assert format_error(APIException(500, "nope")) == "API error [500]: nope"
        assert format_error(RuntimeError("disk full")) == "disk full"


class TestAuditCallers:
    def test_download_fail_names_timeout(self, caplog):
        caplog.set_level(logging.INFO)
        log_download_failed(
            _request(), asyncio.TimeoutError(), stage="download_unexpected"
        )
        assert "DOWNLOAD_FAIL" in caplog.text
        assert "stage=download_unexpected" in caplog.text
        assert "error=TimeoutError" in caplog.text
        assert "error= |" not in caplog.text

    def test_provider_fail_names_empty_exception_and_keeps_messages(self, caplog):
        caplog.set_level(logging.INFO)
        req = _request()
        log_provider_failed(req, "vda", DownloaderException(), next_provider=None)
        log_provider_failed(req, "ytstream", APIException(503, "busy"), next_provider="vda")
        assert "error=DownloaderException" in caplog.text
        assert "next=none" in caplog.text
        assert "error=API error [503]: busy" in caplog.text
        assert "next=vda" in caplog.text
        assert "error= |" not in caplog.text

    async def test_fallback_logs_empty_downloader_exception(self, caplog):
        caplog.set_level(logging.INFO)
        fail = MagicMock()
        fail.download = AsyncMock(side_effect=DownloaderException())
        with pytest.raises(DownloaderException):
            await _download_with_fallback(
                (("vda", lambda: fail),),
                _request().url,
                "mp4",
                FakeProgress(),
                _request(),
            )
        assert "provider=vda" in caplog.text
        assert "error=DownloaderException" in caplog.text
        assert "error= |" not in caplog.text

    async def test_handle_url_logs_timeout_type(self, caplog):
        caplog.set_level(logging.INFO)
        user = MagicMock()
        user.id = 258871997
        user.username = "yehuda100"
        chat = MagicMock()
        chat.id = 258871997
        update = MagicMock()
        update.effective_user = user
        update.effective_chat = chat
        update.message.text = "https://youtu.be/V9TCk-qCIxI"
        update.message.chat_id = 258871997
        context = MagicMock()
        context.user_data = {"format": "mp3"}

        status = MagicMock()
        status.initialize = AsyncMock(return_value=status)
        status.update = AsyncMock()

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("core.telegram_bot.StatusUpdater", lambda *args, **kwargs: status)
            patch.setattr(
                "core.telegram_bot.download",
                AsyncMock(side_effect=asyncio.TimeoutError()),
            )
            await TelegramVideoBot().handle_url(update, context)

        assert "stage=download_unexpected" in caplog.text
        assert "error=TimeoutError" in caplog.text
        assert "error= |" not in caplog.text
        status.update.assert_awaited()
        shown = status.update.await_args.args[0]
        assert shown == "❌ Something went wrong. Send the link again in a little while."
        assert "TimeoutError" not in shown
        assert "Download failed" not in shown
