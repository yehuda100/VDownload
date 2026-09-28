"""
Rate-limited Telegram status message (edit in place during long downloads).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot
from telegram.error import BadRequest, RetryAfter

logger = logging.getLogger(__name__)

MIN_EDIT_INTERVAL_SEC = 1.5
MAX_RETRY_AFTER_ATTEMPTS = 3


class StatusUpdater:
    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.status = ""
        self.message = None
        self.last_update_time = datetime.now(timezone.utc) - timedelta(
            seconds=MIN_EDIT_INTERVAL_SEC
        )
        self._update_task: asyncio.Task | None = None
        self._fallback = False

    async def initialize(self, text: str) -> "StatusUpdater":
        self.status = text
        self._fallback = False
        self.message = await self.bot.send_message(chat_id=self.chat_id, text=text)
        self.last_update_time = datetime.now(timezone.utc)
        return self

    async def report(self, message: str) -> None:
        await self.update(message)

    async def update(
        self,
        new_status: str,
        *,
        flush: bool = False,
        fallback: bool = False,
    ) -> None:
        if new_status == self.status:
            if flush:
                await self._drain()
            return
        self.status = new_status
        self._fallback = fallback
        await self._cancel_pending()
        self._update_task = asyncio.create_task(self._process_update())
        if flush:
            await self._drain()

    async def delete(self) -> None:
        await self._cancel_pending()
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                logger.debug("Could not delete status message", exc_info=True)
            self.message = None

    async def close(self) -> None:
        await self._cancel_pending()

    async def _cancel_pending(self) -> None:
        task = self._update_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._update_task is task:
            self._update_task = None

    async def _drain(self) -> None:
        task = self._update_task
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _process_update(self) -> None:
        try:
            elapsed = (
                datetime.now(timezone.utc) - self.last_update_time
            ).total_seconds()
            if elapsed < MIN_EDIT_INTERVAL_SEC:
                await asyncio.sleep(MIN_EDIT_INTERVAL_SEC - elapsed)
            ok = await self._send_to_telegram()
        except asyncio.CancelledError:
            raise
        if not ok and self._fallback:
            await self._fallback_send()

    async def _send_to_telegram(self) -> bool:
        if not self.message:
            return False
        attempts = 0
        while True:
            try:
                await self.message.edit_text(self.status)
                self.last_update_time = datetime.now(timezone.utc)
                return True
            except RetryAfter as exc:
                attempts += 1
                if attempts >= MAX_RETRY_AFTER_ATTEMPTS:
                    logger.debug("Status edit still rate-limited", exc_info=True)
                    return False
                delay = exc.retry_after
                if not isinstance(delay, (int, float)):
                    delay = delay.total_seconds()
                await asyncio.sleep(delay)
            except BadRequest as exc:
                if "not modified" in str(exc).lower():
                    self.last_update_time = datetime.now(timezone.utc)
                    return True
                logger.debug("Could not update status message", exc_info=True)
                return False
            except Exception:
                logger.debug("Could not update status message", exc_info=True)
                return False

    async def _fallback_send(self) -> None:
        try:
            new_message = await self.bot.send_message(
                chat_id=self.chat_id, text=self.status
            )
        except Exception:
            logger.debug("Could not send fallback status message", exc_info=True)
            return
        old = self.message
        self.message = new_message
        self.last_update_time = datetime.now(timezone.utc)
        if old is not None and old is not new_message:
            try:
                await old.delete()
            except Exception:
                logger.debug("Could not delete stale status message", exc_info=True)
