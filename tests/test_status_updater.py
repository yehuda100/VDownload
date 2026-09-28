"""Status message edits: throttle, flush, and failed-edit fallback."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, RetryAfter

from core.status_updater import MAX_RETRY_AFTER_ATTEMPTS, StatusUpdater


class _Message:
    def __init__(self, text: str):
        self.text = text
        self.edits: list[str] = []
        self.deleted = False
        self.edit_text = AsyncMock(side_effect=self._edit)

    async def _edit(self, text: str) -> None:
        self.text = text
        self.edits.append(text)

    async def delete(self) -> None:
        self.deleted = True


class _Bot:
    def __init__(self):
        self.sent: list[_Message] = []

    async def send_message(self, chat_id: int, text: str) -> _Message:
        message = _Message(text)
        self.sent.append(message)
        return message


async def _updater(monkeypatch, interval: float = 0) -> tuple[StatusUpdater, _Bot]:
    monkeypatch.setattr("core.status_updater.MIN_EDIT_INTERVAL_SEC", interval)
    bot = _Bot()
    updater = await StatusUpdater(bot, 1).initialize("Downloading...")
    return updater, bot


async def test_rapid_updates_collapse_to_the_latest_text(monkeypatch):
    updater, bot = await _updater(monkeypatch, interval=0)
    await updater.update("Preparing the file... 10%")
    await updater.update("Preparing the file... 20%")
    await updater.update("Preparing the file... 30%", flush=True)
    assert bot.sent[0].edits == ["Preparing the file... 30%"]


async def test_throttle_waits_before_editing(monkeypatch):
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def instant_sleep(seconds: float) -> None:
        recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("core.status_updater.asyncio.sleep", instant_sleep)
    updater, bot = await _updater(monkeypatch, interval=1.5)
    await updater.update("Preparing the file... 40%", flush=True)
    assert bot.sent[0].edits == ["Preparing the file... 40%"]
    assert recorded
    assert recorded[0] == pytest.approx(1.5, abs=0.05)


async def test_identical_text_is_not_sent_again(monkeypatch):
    updater, bot = await _updater(monkeypatch, interval=0)
    await updater.update("Preparing the file... 50%", flush=True)
    await updater.update("Preparing the file... 50%", flush=True)
    assert bot.sent[0].edits == ["Preparing the file... 50%"]
    assert len(bot.sent) == 1


async def test_message_is_not_modified_is_success(monkeypatch):
    updater, bot = await _updater(monkeypatch, interval=0)

    async def edit(_text: str) -> None:
        raise BadRequest("Message is not modified")

    bot.sent[0].edit_text = AsyncMock(side_effect=edit)
    await updater.update("Downloading...", flush=True)
    await updater.update("Sending to Telegram...", flush=True, fallback=True)
    assert len(bot.sent) == 1


async def test_failed_edit_falls_back_to_a_new_message(monkeypatch):
    updater, bot = await _updater(monkeypatch, interval=0)

    async def edit(_text: str) -> None:
        raise RuntimeError("message too long")

    bot.sent[0].edit_text = AsyncMock(side_effect=edit)
    await updater.update("❌ Couldn't download this link. Send it again.", flush=True, fallback=True)
    assert len(bot.sent) == 2
    assert bot.sent[1].text.startswith("❌ Couldn't download")
    assert bot.sent[0].deleted is True


async def test_delete_cancels_a_pending_edit(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_sleep(_seconds: float) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr("core.status_updater.asyncio.sleep", blocking_sleep)
    updater, bot = await _updater(monkeypatch, interval=1.5)
    await updater.update("Sending to Telegram...")
    await started.wait()
    await updater.delete()
    release.set()
    await asyncio.sleep(0)
    assert bot.sent[0].edits == []
    assert bot.sent[0].deleted is True


@pytest.mark.filterwarnings(
    "ignore:Deprecated since version v22.2:telegram.warnings.PTBDeprecationWarning"
)
async def test_retry_after_is_capped(monkeypatch):
    updater, bot = await _updater(monkeypatch, interval=0)
    calls = {"n": 0}

    async def edit(_text: str) -> None:
        calls["n"] += 1
        raise RetryAfter(0)

    bot.sent[0].edit_text = AsyncMock(side_effect=edit)
    await updater.update("Downloading... 1%", flush=True, fallback=True)
    assert calls["n"] == MAX_RETRY_AFTER_ATTEMPTS
    assert len(bot.sent) == 2
