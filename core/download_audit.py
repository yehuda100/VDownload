"""
Structured audit logging for download requests and provider fallback chain.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("vdownload.audit")


@dataclass(frozen=True)
class DownloadRequest:
    """Who initiated a download (Telegram user)."""

    user_id: int
    username: str | None
    chat_id: int
    format_type: str
    url: str

    def label(self) -> str:
        handle = f"@{self.username}" if self.username else "no_username"
        return f"user_id={self.user_id} chat_id={self.chat_id} {handle}"


def log_request_started(req: DownloadRequest) -> None:
    logger.info(
        "DOWNLOAD_START | %s | format=%s | url=%s",
        req.label(),
        req.format_type,
        req.url,
    )


def log_provider_attempt(
    req: DownloadRequest,
    provider: str,
    *,
    platform: str,
) -> None:
    logger.info(
        "PROVIDER_TRY | %s | provider=%s | platform=%s | url=%s",
        req.label(),
        provider,
        platform,
        req.url,
    )


def log_provider_failed(
    req: DownloadRequest,
    provider: str,
    error: BaseException,
    *,
    next_provider: str | None = None,
) -> None:
    if next_provider:
        logger.warning(
            "PROVIDER_FAIL | %s | provider=%s | error=%s | next=%s",
            req.label(),
            provider,
            error,
            next_provider,
        )
    else:
        logger.warning(
            "PROVIDER_FAIL | %s | provider=%s | error=%s | next=none",
            req.label(),
            provider,
            error,
        )


def log_provider_success(req: DownloadRequest, provider: str, result: dict[str, Any]) -> None:
    logger.info(
        "PROVIDER_OK | %s | provider=%s | file_id=%s | title=%s",
        req.label(),
        provider,
        result.get("file_id"),
        result.get("title"),
    )


def log_download_success(
    req: DownloadRequest,
    *,
    provider: str,
    delivery: str,
    file_id: str,
    title: str,
) -> None:
    logger.info(
        "DOWNLOAD_OK | %s | provider=%s | delivery=%s | file_id=%s | title=%s | url=%s",
        req.label(),
        provider,
        delivery,
        file_id,
        title,
        req.url,
    )


def log_download_failed(req: DownloadRequest, error: BaseException, *, stage: str) -> None:
    logger.error(
        "DOWNLOAD_FAIL | %s | stage=%s | error=%s | url=%s",
        req.label(),
        stage,
        error,
        req.url,
    )


def log_unauthorized_access(
    user_id: int | None,
    username: str | None,
    chat_id: int | None,
    *,
    reason: str,
) -> None:
    logger.warning(
        "ACCESS_DENIED | user_id=%s chat_id=%s username=%s | reason=%s",
        user_id,
        chat_id,
        username,
        reason,
    )


def log_link_access(
    file_id: str,
    *,
    title: str,
    client_ip: str,
    success: bool,
    reason: str | None = None,
) -> None:
    if success:
        logger.info(
            "LINK_OK | file_id=%s | title=%s | client_ip=%s",
            file_id,
            title,
            client_ip,
        )
    else:
        logger.warning(
            "LINK_FAIL | file_id=%s | client_ip=%s | reason=%s",
            file_id,
            client_ip,
            reason,
        )
