"""
Telegram presentation layer: commands, URL handling, file delivery.

Does not perform downloads — delegates to core.download_manager.download().
"""
import asyncio
import logging
import os

import aiofiles
from telegram import Message, Update
from telegram.ext import ContextTypes

from config import MAX_SIZE
from core import SecureLinkManager, StatusUpdater, download
from core.download_audit import (
    DownloadRequest,
    log_download_failed,
    log_download_success,
    log_request_started,
    log_unauthorized_access,
)
from downloaders.exceptions import DownloaderException
from utils import extract_url, find_file

logger = logging.getLogger(__name__)


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

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["format"] = "mp4"
        await update.message.reply_text("🎬 שלח קישור ואני אוריד לך את הסרטון!")

    async def mp3(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["format"] = "mp3"
        await update.message.reply_text("🎵 שלח קישור ואני אוריד לך את השיר!")

    async def mp4(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        context.user_data["format"] = "mp4"
        await update.message.reply_text("🎬 שלח קישור ואני אוריד לך את הסרטון!")

    async def no_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id, username, chat_id = _user_from_update(update)
        log_unauthorized_access(
            user_id,
            username,
            chat_id,
            reason="message_from_non_allowed_user",
        )
        await update.message.reply_text("🚫 אם אתה לא @yehuda100 – זה לא בשבילך 🤖")

    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        url = extract_url(update.message.text)
        if not url:
            await update.message.reply_text(
                "❌ לא מצאתי קישור בהודעה שלך. אנא שלח קישור תקין."
            )
            return

        format_type = context.user_data.get("format", "mp4")
        request = _build_request(update, url, format_type)
        log_request_started(request)

        status_updater = await StatusUpdater(
            context.bot, update.message.chat_id
        ).initialize()

        try:
            result, provider = await download(
                url, format_type, status_updater, request
            )
        except DownloaderException as e:
            log_download_failed(request, e, stage="download")
            await status_updater.update(f"❌ ההורדה נכשלה: {e}")
            return
        except Exception as e:
            log_download_failed(request, e, stage="download_unexpected")
            logger.exception("Unexpected error while downloading %s", url)
            await status_updater.update("❌ שגיאה לא צפויה. נסה שוב מאוחר יותר.")
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
            await status_updater.update("❌ לא מצאתי את הקובץ שהורד. אנא נסה שוב.")
            return

        file_size = await asyncio.to_thread(lambda: file.stat().st_size)
        filepath = str(file.absolute())

        if file_size >= MAX_SIZE:
            link = SecureLinkManager.save_metadata(
                result["file_id"], filepath, result["title"]
            )
            mb = file_size / 1024 / 1024
            await update.message.reply_text(
                f"🔗 הסרטון גדול מדי ({mb:.1f}MB)\n📥 {result['title']}\n{link}"
            )
            await status_updater.delete()
            log_download_success(
                request,
                provider=provider,
                delivery="secure_link",
                file_id=result["file_id"],
                title=result["title"],
            )
            return

        await status_updater.update("📤 שולח את הקובץ ל-Telegram...")
        try:
            await self._send_file(update, update.message, filepath, result["title"])
        except Exception as e:
            log_download_failed(request, e, stage="telegram_send")
            logger.exception("Failed to send file to Telegram: %s", filepath)
            await status_updater.update("❌ שליחה ל-Telegram נכשלה.")
            return

        await status_updater.delete()
        log_download_success(
            request,
            provider=provider,
            delivery="telegram",
            file_id=result["file_id"],
            title=result["title"],
        )

    async def _send_file(
        self, update: Update, msg: Message, filepath: str, title: str
    ) -> None:
        if filepath.endswith((".mp3", ".m4a", ".ogg", ".opus")):
            async with aiofiles.open(filepath, "rb") as f:
                await update.message.reply_audio(
                    audio=await f.read(), filename=title
                )
        else:
            async with aiofiles.open(filepath, "rb") as f:
                await update.message.reply_video(
                    video=await f.read(),
                    filename=title,
                    supports_streaming=True,
                )
        await msg.delete()
        await asyncio.to_thread(os.remove, filepath)
