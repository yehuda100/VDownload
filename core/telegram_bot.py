"""
Telegram presentation layer: commands, URL handling, file delivery.

Does not perform downloads — delegates to core.download_manager.download().
"""
import asyncio
import logging
import os

import aiofiles
from telegram import Message, Update
from telegram.ext import ContextTypes, filters

from config import MAX_SIZE, USER_ID
from core import SecureLinkManager, StatusUpdater, download
from core.download_audit import (
    DownloadRequest,
    log_download_failed,
    log_download_success,
    log_request_started,
    log_unauthorized_access,
)
from core.messages import (
    DOWNLOADING,
    FILE_NOT_SAVED,
    MP3_TEXT,
    MP4_TEXT,
    NO_LINK_HINT,
    NOT_AVAILABLE,
    SEND_FAILED,
    SENDING,
    START_TEXT,
    UNEXPECTED,
    WAITING_FOR_PREVIOUS,
    message_for_exception,
    too_big_message,
)
from downloaders.exceptions import DownloaderException
from utils import build_display_filename, extract_url, find_file

logger = logging.getLogger(__name__)

URL_MESSAGE_FILTER = (
    filters.Chat(USER_ID)
    & filters.TEXT
    & ~filters.COMMAND
    & filters.Regex(r"https?://")
)


def _user_from_update(update: Update) -> tuple[int | None, str | None, int | None]:
    user = update.effective_user
    if not user:
        chat = update.effective_chat
        return None, None, chat.id if chat else None
    chat = update.effective_chat
    return user.id, user.username, chat.id if chat else None


def _build_request(update: Update, url: str, format_type: str) -> DownloadRequest:
    user = update.effective_user
    chat = update.effective_chat
    return DownloadRequest(
        user_id=user.id if user else 0,
        username=user.username if user else None,
        chat_id=chat.id if chat else 0,
        format_type=format_type,
        url=url,
    )


class TelegramVideoBot:
    """Handlers for /start, /mp3, /mp4 and URL messages."""

    def __init__(self) -> None:
        self._download_lock = asyncio.Lock()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["format"] = "mp4"
        await update.message.reply_text(START_TEXT)

    async def mp3(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["format"] = "mp3"
        await update.message.reply_text(MP3_TEXT)

    async def mp4(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["format"] = "mp4"
        await update.message.reply_text(MP4_TEXT)

    async def no_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        if chat and chat.id == USER_ID:
            await update.message.reply_text(NO_LINK_HINT)
            return
        user_id, username, chat_id = _user_from_update(update)
        log_unauthorized_access(
            user_id,
            username,
            chat_id,
            reason="message_from_non_allowed_user",
        )
        await update.message.reply_text(NOT_AVAILABLE)

    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        url = extract_url(update.message.text or "")
        if not url:
            await update.message.reply_text(NO_LINK_HINT)
            return

        format_type = context.user_data.get("format", "mp4")
        request = _build_request(update, url, format_type)
        log_request_started(request)

        waiting = self._download_lock.locked()
        status_updater = await StatusUpdater(
            context.bot, update.message.chat_id
        ).initialize(WAITING_FOR_PREVIOUS if waiting else DOWNLOADING)

        async with self._download_lock:
            if waiting:
                await status_updater.update(DOWNLOADING, flush=True)
            try:
                result, provider = await download(
                    url, format_type, status_updater, request
                )
            except DownloaderException as e:
                log_download_failed(request, e, stage="download")
                await status_updater.update(
                    message_for_exception(e), flush=True, fallback=True
                )
                return
            except Exception as e:
                log_download_failed(request, e, stage="download_unexpected")
                logger.exception("Unexpected error while downloading %s", url)
                await status_updater.update(UNEXPECTED, flush=True, fallback=True)
                return

            file = await asyncio.to_thread(find_file, result["file_id"])
            if not file:
                err = RuntimeError("file missing after successful download")
                log_download_failed(request, err, stage="file_lookup")
                logger.error(
                    "Download reported success but file not found: file_id=%s url=%s provider=%s",
                    result["file_id"],
                    url,
                    provider,
                )
                await status_updater.update(FILE_NOT_SAVED, flush=True, fallback=True)
                return

            file_size = await asyncio.to_thread(lambda: file.stat().st_size)
            filepath = str(file.absolute())
            display_name = build_display_filename(result["title"], filepath)

            if file_size >= MAX_SIZE:
                link = SecureLinkManager.save_metadata(
                    result["file_id"], filepath, display_name
                )
                await status_updater.update(
                    too_big_message(file_size, result["title"], link),
                    flush=True,
                    fallback=True,
                )
                log_download_success(
                    request,
                    provider=provider,
                    delivery="secure_link",
                    file_id=result["file_id"],
                    title=result["title"],
                )
                return

            await status_updater.update(SENDING, flush=True)
            try:
                await self._send_file(update, filepath, display_name)
            except Exception as e:
                log_download_failed(request, e, stage="telegram_send")
                logger.exception("Failed to send file to Telegram: %s", filepath)
                await status_updater.update(SEND_FAILED, flush=True, fallback=True)
                return

            await self._cleanup_after_send(update.message, filepath)
            await status_updater.delete()
            log_download_success(
                request,
                provider=provider,
                delivery="telegram",
                file_id=result["file_id"],
                title=result["title"],
            )

    async def _send_file(self, update: Update, filepath: str, title: str) -> None:
        if filepath.endswith((".mp3", ".m4a", ".ogg", ".opus")):
            async with aiofiles.open(filepath, "rb") as f:
                payload = await f.read()
            await update.message.reply_audio(audio=payload, filename=title)
        else:
            async with aiofiles.open(filepath, "rb") as f:
                payload = await f.read()
            await update.message.reply_video(
                video=payload,
                filename=title,
                supports_streaming=True,
            )

    async def _cleanup_after_send(self, msg: Message, filepath: str) -> None:
        try:
            await msg.delete()
        except Exception:
            logger.exception("Failed to delete the source message")
        try:
            await asyncio.to_thread(os.remove, filepath)
        except Exception:
            logger.exception("Failed to remove downloaded file %s", filepath)
