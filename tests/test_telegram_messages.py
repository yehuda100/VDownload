"""Telegram copy, link-anywhere handling, and one-at-a-time downloads."""
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import Chat, Message, Update, User
from telegram.ext import Application

from config import USER_ID
from core.messages import (
    DOWNLOADING,
    FILE_NOT_SAVED,
    GENERIC_FAIL,
    MP3_TEXT,
    MP4_TEXT,
    NO_LINK_HINT,
    NOT_AVAILABLE,
    SEND_FAILED,
    SENDING,
    START_TEXT,
    WAITING_FOR_PREVIOUS,
)
from core.telegram_bot import URL_MESSAGE_FILTER, TelegramVideoBot
from downloaders.exceptions import DownloaderException
from main import _post_init, register_handlers


def _message(text: str, chat_id: int = USER_ID) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=chat_id, type="private"),
        text=text,
        from_user=User(id=chat_id, is_bot=False, first_name="Y"),
    )


def _update(text: str, chat_id: int = USER_ID):
    user = MagicMock()
    user.id = chat_id
    user.username = "owner"
    chat = MagicMock()
    chat.id = chat_id
    message = MagicMock()
    message.text = text
    message.chat_id = chat_id
    message.reply_text = AsyncMock()
    message.reply_audio = AsyncMock()
    message.reply_video = AsyncMock()
    message.delete = AsyncMock()
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = message
    context = MagicMock()
    context.user_data = {}
    context.bot = MagicMock()
    return update, context


class _RecordingStatus:
    def __init__(self, bot, chat_id):
        self.text = None
        self.updates = []
        self.deleted = False

    async def initialize(self, text):
        self.text = text
        _RecordingStatus.created.append(self)
        return self

    async def update(self, text, **kwargs):
        self.text = text
        self.updates.append(text)

    async def delete(self):
        self.deleted = True


def _incoming(text: str, chat_id: int = USER_ID) -> Update:
    return Update(update_id=1, message=_message(text, chat_id))


def test_url_filter_matches_a_link_after_hebrew_or_english():
    assert URL_MESSAGE_FILTER.check_update(_incoming("תסתכל https://youtu.be/abc"))
    assert URL_MESSAGE_FILTER.check_update(_incoming("check this https://youtu.be/abc"))
    assert not URL_MESSAGE_FILTER.check_update(_incoming("שלום"))
    assert not URL_MESSAGE_FILTER.check_update(
        _incoming("https://youtu.be/abc", chat_id=USER_ID + 1)
    )


async def test_menu_and_url_handler_does_not_block_the_queue():
    application = Application.builder().token("000000:test-token").build()
    bot = TelegramVideoBot()
    register_handlers(application, bot)
    url_handlers = [
        handler
        for handler in application.handlers[0]
        if getattr(handler, "callback", None) == bot.handle_url
    ]
    assert len(url_handlers) == 1
    assert url_handlers[0].block is False

    menu_app = MagicMock()
    menu_app.bot.set_my_commands = AsyncMock()
    await _post_init(menu_app)
    commands = menu_app.bot.set_my_commands.await_args.args[0]
    assert [(item.command, item.description) for item in commands] == [
        ("start", "How to download"),
        ("mp3", "Download audio"),
        ("mp4", "Download video"),
    ]


async def test_command_replies():
    bot = TelegramVideoBot()
    start_update, start_context = _update("/start")
    await bot.start(start_update, start_context)
    assert start_context.user_data["format"] == "mp4"
    start_update.message.reply_text.assert_awaited_with(START_TEXT)

    mp3_update, mp3_context = _update("/mp3")
    await bot.mp3(mp3_update, mp3_context)
    assert mp3_context.user_data["format"] == "mp3"
    mp3_update.message.reply_text.assert_awaited_with(MP3_TEXT)

    mp4_update, mp4_context = _update("/mp4")
    await bot.mp4(mp4_update, mp4_context)
    mp4_update.message.reply_text.assert_awaited_with(MP4_TEXT)


async def test_owner_without_a_link_gets_a_hint(caplog):
    caplog.set_level(logging.INFO)
    bot = TelegramVideoBot()
    update, context = _update("שלום")
    await bot.no_entry(update, context)
    update.message.reply_text.assert_awaited_with(NO_LINK_HINT)
    assert "ACCESS_DENIED" not in caplog.text


async def test_other_chat_is_refused(caplog):
    caplog.set_level(logging.INFO)
    bot = TelegramVideoBot()
    update, context = _update("https://youtu.be/abc", chat_id=999)
    await bot.no_entry(update, context)
    update.message.reply_text.assert_awaited_with(NOT_AVAILABLE)
    assert "ACCESS_DENIED" in caplog.text


async def test_link_after_hebrew_is_downloaded():
    bot = TelegramVideoBot()
    update, context = _update("תסתכל https://youtu.be/abcdefghijk")
    seen = {}

    async def download(url, format_type, status, request):
        seen["url"] = url
        raise DownloaderException("nope")

    _RecordingStatus.created = []
    with (
        patch("core.telegram_bot.StatusUpdater", _RecordingStatus),
        patch("core.telegram_bot.download", download),
    ):
        await bot.handle_url(update, context)

    assert seen["url"] == "https://youtu.be/abcdefghijk"
    assert _RecordingStatus.created[0].updates[-1] == GENERIC_FAIL
    assert "nope" not in _RecordingStatus.created[0].updates[-1]


def _file(size: int):
    found = MagicMock()
    found.stat.return_value = MagicMock(st_size=size)
    found.absolute.return_value = "/tmp/video.mp4"
    return found


async def test_oversized_file_is_edited_into_the_status_message(monkeypatch):
    monkeypatch.setattr("config.EXPIRY", 24 * 3600)
    bot = TelegramVideoBot()
    update, context = _update("https://youtu.be/abcdefghijk")

    async def download(url, format_type, status, request):
        return {"file_id": "fid", "title": "שלום"}, "yt-dlp"

    _RecordingStatus.created = []
    with (
        patch("core.telegram_bot.StatusUpdater", _RecordingStatus),
        patch("core.telegram_bot.download", download),
        patch("core.telegram_bot.find_file", return_value=_file(87_241_523)),
        patch(
            "core.telegram_bot.SecureLinkManager.save_metadata",
            return_value="https://example/file",
        ),
    ):
        await bot.handle_url(update, context)

    status = _RecordingStatus.created[0]
    assert status.deleted is False
    text = status.updates[-1]
    assert "83.2 MB" in text
    assert "שלום" in text
    assert "https://example/file" in text
    assert "24 hours" in text
    update.message.reply_text.assert_not_awaited()


async def test_missing_file_names_the_next_step():
    bot = TelegramVideoBot()
    update, context = _update("https://youtu.be/abcdefghijk")

    async def download(url, format_type, status, request):
        return {"file_id": "fid", "title": "T"}, "yt-dlp"

    _RecordingStatus.created = []
    with (
        patch("core.telegram_bot.StatusUpdater", _RecordingStatus),
        patch("core.telegram_bot.download", download),
        patch("core.telegram_bot.find_file", return_value=None),
    ):
        await bot.handle_url(update, context)

    assert _RecordingStatus.created[0].updates[-1] == FILE_NOT_SAVED


async def test_cleanup_failure_is_not_a_send_failure():
    bot = TelegramVideoBot()
    update, context = _update("https://youtu.be/abcdefghijk")
    update.message.delete = AsyncMock(side_effect=RuntimeError("cannot delete"))

    async def download(url, format_type, status, request):
        return {"file_id": "fid", "title": "T"}, "yt-dlp"

    _RecordingStatus.created = []
    with (
        patch("core.telegram_bot.StatusUpdater", _RecordingStatus),
        patch("core.telegram_bot.download", download),
        patch("core.telegram_bot.find_file", return_value=_file(100)),
        patch.object(bot, "_send_file", AsyncMock()),
        patch("core.telegram_bot.os.remove", side_effect=OSError("busy")),
    ):
        await bot.handle_url(update, context)

    status = _RecordingStatus.created[0]
    assert SENDING in status.updates
    assert SEND_FAILED not in status.updates
    assert status.deleted is True


async def test_send_failure_edits_status_with_a_signed_link():
    bot = TelegramVideoBot()
    update, context = _update("https://youtu.be/abcdefghijk")

    async def download(url, format_type, status, request):
        return {
            "file_id": "66666666-6666-4666-8666-666666666666",
            "title": "Clip",
        }, "yt-dlp"

    _RecordingStatus.created = []
    with (
        patch("core.telegram_bot.StatusUpdater", _RecordingStatus),
        patch("core.telegram_bot.download", download),
        patch("core.telegram_bot.find_file", return_value=_file(100)),
        patch.object(bot, "_send_file", AsyncMock(side_effect=RuntimeError("timeout"))),
        patch(
            "core.telegram_bot.SecureLinkManager.save_metadata",
            return_value="https://example/file?sig=abc",
        ) as save_metadata,
    ):
        await bot.handle_url(update, context)

    status = _RecordingStatus.created[0]
    text = status.updates[-1]
    assert SEND_FAILED in text
    assert "https://example/file?sig=abc" in text
    assert "Clip" in text
    assert "too big" not in text.lower()
    assert status.deleted is False
    save_metadata.assert_called_once()


async def test_send_failure_without_a_link_keeps_the_plain_error():
    bot = TelegramVideoBot()
    update, context = _update("https://youtu.be/abcdefghijk")

    async def download(url, format_type, status, request):
        return {
            "file_id": "66666666-6666-4666-8666-666666666666",
            "title": "Clip",
        }, "yt-dlp"

    _RecordingStatus.created = []
    with (
        patch("core.telegram_bot.StatusUpdater", _RecordingStatus),
        patch("core.telegram_bot.download", download),
        patch("core.telegram_bot.find_file", return_value=_file(100)),
        patch.object(bot, "_send_file", AsyncMock(side_effect=RuntimeError("timeout"))),
        patch(
            "core.telegram_bot.SecureLinkManager.save_metadata",
            side_effect=OSError("disk full"),
        ),
    ):
        await bot.handle_url(update, context)

    assert _RecordingStatus.created[0].updates[-1] == SEND_FAILED


async def test_second_link_waits_for_the_first():
    bot = TelegramVideoBot()
    release = asyncio.Event()
    started = asyncio.Event()
    calls = {"n": 0}

    async def download(url, format_type, status, request):
        calls["n"] += 1
        if calls["n"] == 1:
            started.set()
            await release.wait()
        return {"file_id": f"id{calls['n']}", "title": "T"}, "yt-dlp"

    _RecordingStatus.created = []
    first_update, first_context = _update("https://youtu.be/aaaaaaaaaaa")
    second_update, second_context = _update("check this https://youtu.be/bbbbbbbbbbb")
    with (
        patch("core.telegram_bot.StatusUpdater", _RecordingStatus),
        patch("core.telegram_bot.download", download),
        patch("core.telegram_bot.find_file", return_value=_file(100)),
        patch.object(bot, "_send_file", AsyncMock()),
        patch.object(bot, "_cleanup_after_send", AsyncMock()),
    ):
        first = asyncio.create_task(bot.handle_url(first_update, first_context))
        await started.wait()
        second = asyncio.create_task(bot.handle_url(second_update, second_context))
        for _ in range(20):
            if len(_RecordingStatus.created) >= 2:
                break
            await asyncio.sleep(0)
        assert _RecordingStatus.created[0].text == DOWNLOADING
        assert _RecordingStatus.created[1].text == WAITING_FOR_PREVIOUS
        release.set()
        await first
        await second

    assert DOWNLOADING in _RecordingStatus.created[1].updates
