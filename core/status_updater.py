"""
Rate-limited Telegram status message (edit in place during long downloads).

New text does not cancel a pending edit. One worker waits out the minimum
interval, then edits in the latest text. Cancelling that worker on every
progress report starved the chat: VDA polls about once a second, so the
edit was aborted before Telegram accepted it and the percent never moved.
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
        self._visible = ""
        self._closed = False

    async def initialize(self, text: str) -> "StatusUpdater":
        self.status = text
        self._visible = text
        self._fallback = False
        self._closed = False
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
        changed = new_status != self.status
        if not changed and not flush and not fallback:
            return
        if changed:
            self.status = new_status
        self._fallback = fallback
        if self._visible != self.status or self._worker_running():
            self._ensure_worker()
        if flush:
            await self._flush()

    async def delete(self) -> None:
        self._closed = True
        await self._cancel_pending()
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                logger.debug("Could not delete status message", exc_info=True)
            self.message = None

    async def close(self) -> None:
        self._closed = True
        await self._cancel_pending()

    def _worker_running(self) -> bool:
        return self._update_task is not None and not self._update_task.done()

    def _ensure_worker(self) -> None:
        if self._closed or self._worker_running():
            return
        self._update_task = asyncio.create_task(self._process_update())

    async def _flush(self) -> None:
        task = self._update_task
        if task is None or task.done():
            return
        try:
            await task
        except asyncio.CancelledError:
            return

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

    async def _process_update(self) -> None:
        try:
            while not self._closed:
                delay = self._remaining_delay()
                if delay > 0:
                    await asyncio.sleep(delay)
                if self._closed:
                    return
                text = self.status
                ok = await self._send_to_telegram(text)
                if self._closed:
                    return
                if not ok:
                    if self._fallback:
                        await self._fallback_send()
                    return
                if self.status == text:
                    return
        except asyncio.CancelledError:
            raise

    def _remaining_delay(self) -> float:
        elapsed = (
            datetime.now(timezone.utc) - self.last_update_time
        ).total_seconds()
        return max(0.0, MIN_EDIT_INTERVAL_SEC - elapsed)

    async def _send_to_telegram(self, text: str) -> bool:
        if not self.message:
            return False
        attempts = 0
        while True:
            try:
                await self.message.edit_text(text)
                self.last_update_time = datetime.now(timezone.utc)
                self._visible = text
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
                    self._visible = text
                    return True
                logger.debug("Could not update status message", exc_info=True)
                return False
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("Could not update status message", exc_info=True)
                return False

    async def _fallback_send(self) -> None:
        text = self.status
        try:
            new_message = await self.bot.send_message(
                chat_id=self.chat_id, text=text
            )
        except Exception:
            logger.debug("Could not send fallback status message", exc_info=True)
            return
        old = self.message
        self.message = new_message
        self._visible = text
        self.last_update_time = datetime.now(timezone.utc)
        if old is not None and old is not new_message:
            try:
                await old.delete()
            except Exception:
                logger.debug("Could not delete stale status message", exc_info=True)
