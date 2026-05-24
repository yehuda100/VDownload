"""
Rate-limited Telegram status message (edit in place during long downloads).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Bot
from telegram.error import RetryAfter

logger = logging.getLogger(__name__)

MIN_EDIT_INTERVAL_SEC = 1.5


class StatusUpdater:
    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.status = "Initializing..."
        self.message = None
        self.last_update_time = datetime.now(timezone.utc) - timedelta(
            seconds=MIN_EDIT_INTERVAL_SEC
        )
        self._update_task = None

    async def initialize(self) -> "StatusUpdater":
        self.message = await self.bot.send_message(
            chat_id=self.chat_id,
            text=f"Status: {self.status}",
        )
        self.last_update_time = datetime.now(timezone.utc)
        return self

    async def report(self, message: str) -> None:
        await self.update(message)

    async def update(self, new_status: str) -> None:
        if new_status == self.status:
            return
        self.status = new_status

        if self._update_task and not self._update_task.done():
            self._update_task.cancel()

        self._update_task = asyncio.create_task(self._process_update())

    async def delete(self) -> None:
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                logger.debug("Could not delete status message", exc_info=True)

    async def _process_update(self) -> None:
        try:
            elapsed = (
                datetime.now(timezone.utc) - self.last_update_time
            ).total_seconds()
            if elapsed < MIN_EDIT_INTERVAL_SEC:
                await asyncio.sleep(MIN_EDIT_INTERVAL_SEC - elapsed)
            await self._send_to_telegram()
        except asyncio.CancelledError:
            pass

    async def _send_to_telegram(self) -> None:
        try:
            if self.message:
                await self.message.edit_text(f"Status: {self.status}")
                self.last_update_time = datetime.now(timezone.utc)
        except RetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await self._send_to_telegram()
        except Exception:
            logger.debug("Could not update status message", exc_info=True)

    async def close(self) -> None:
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
