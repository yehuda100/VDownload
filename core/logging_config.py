"""
Application logging setup.

httpx logs every Telegram API call at INFO with the full request URL, and that
URL contains the bot token (``/bot<token>/<method>``). Those lines are silenced
by raising the httpx/httpcore level to WARNING. Warnings and errors are kept,
and a filter redacts the token wherever it still shows up (including tracebacks).
"""
from __future__ import annotations

import logging
import re
import sys

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Bot API tokens are ``<numeric id>:<secret>``. The secret is 35 chars from
# this alphabet; allow 30+ so slightly different lengths are still covered.
# ``%3A`` is the colon when a URL is percent-encoded.
_TELEGRAM_BOT_TOKEN_RE = re.compile(r"\d{6,}(?::|%3[Aa])[A-Za-z0-9_-]{30,}")

_REDACTED = "<REDACTED>"
_MIN_CONFIGURED_TOKEN_LEN = 8


def _configured_tokens() -> tuple[str, ...]:
    """Exact bot token from config, including a percent-encoded colon form.

    The regex above misses non-standard tokens (tests, placeholders). Replacing
    the configured value as well means that token cannot be written to a log.
    """
    config = sys.modules.get("config")
    if config is None:
        return ()
    token = getattr(config, "BOT_TOKEN", None)
    if not isinstance(token, str):
        return ()
    token = token.strip()
    if len(token) < _MIN_CONFIGURED_TOKEN_LEN or ":" not in token:
        return ()
    variants = [token]
    for encoded_colon in ("%3A", "%3a"):
        encoded = token.replace(":", encoded_colon)
        if encoded not in variants:
            variants.append(encoded)
    return tuple(variants)


def redact_telegram_token(text: str) -> str:
    """Replace Telegram bot tokens in ``text`` with ``<REDACTED>``."""
    if not text:
        return text
    for secret in _configured_tokens():
        if secret in text:
            text = text.replace(secret, _REDACTED)
    return _TELEGRAM_BOT_TOKEN_RE.sub(_REDACTED, text)


def redact_log_record(record: logging.LogRecord) -> None:
    """Redact tokens in a log record, including formatted args and tracebacks.

    httpx passes ``request.url`` (not a ``str``) as a ``%s`` argument, so the
    message has to be rendered before the token can be found. Tracebacks are
    rendered here as well: ``Formatter`` will reuse ``exc_text`` instead of
    formatting ``exc_info`` again.

    Failures while redacting are swallowed so a logging bug cannot hide the
    original warning or error.
    """
    if getattr(record, "_vdownload_redacted", False):
        return
    try:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        message = redact_telegram_token(message)
        record.msg = message
        record.args = ()
        record.message = message
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_telegram_token(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_telegram_token(record.stack_info)
    except Exception:
        return
    record._vdownload_redacted = True


class TelegramTokenRedactingFilter(logging.Filter):
    """Drop bot tokens from log records without dropping the records themselves."""

    def filter(self, record: logging.LogRecord) -> bool:
        redact_log_record(record)
        return True


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts tokens in the fully rendered line.

    Also rewrites cached record fields. ``Formatter.format`` stores the raw
    traceback on ``exc_text`` and the raw message on ``message``; a later
    handler must not be able to read the token back out of those caches.
    """

    def format(self, record: logging.LogRecord) -> str:
        rendered = redact_telegram_token(super().format(record))
        record.message = redact_telegram_token(getattr(record, "message", rendered))
        record.msg = record.message
        record.args = ()
        if record.exc_text:
            record.exc_text = redact_telegram_token(record.exc_text)
        record._vdownload_redacted = True
        return rendered


def _attach_filter(target: logging.Logger | logging.Handler) -> None:
    if any(isinstance(existing, TelegramTokenRedactingFilter) for existing in target.filters):
        return
    target.addFilter(TelegramTokenRedactingFilter())


def configure_logging() -> None:
    """Install the process log handler, quiet HTTP client chatter, redact tokens."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RedactingFormatter(_LOG_FORMAT))
        root.addHandler(handler)

    for handler in root.handlers:
        _attach_filter(handler)
        if not isinstance(handler.formatter, RedactingFormatter):
            current = handler.formatter
            fmt = getattr(current, "_fmt", None) if current is not None else None
            datefmt = getattr(current, "datefmt", None) if current is not None else None
            handler.setFormatter(RedactingFormatter(fmt or _LOG_FORMAT, datefmt))

    # Request URLs are logged at INFO by httpx and at DEBUG by httpcore.
    # WARNING keeps real failures visible; the filter covers the token in those lines.
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.WARNING)
    _attach_filter(httpx_logger)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("vdownload.audit").setLevel(logging.INFO)
