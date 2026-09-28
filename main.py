"""Entry point: Telegram webhook bot, FastAPI link server, and cleanup thread."""
import logging
import threading
import time

import uvicorn
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.request import HTTPXRequest

from api_server import app as api_app
from config import BOT_TOKEN, URL
from core.logging_config import configure_logging
from core.messages import MENU_MP3, MENU_MP4, MENU_START
from core.telegram_bot import URL_MESSAGE_FILTER, TelegramVideoBot
from utils import cleanup

configure_logging()
logger = logging.getLogger(__name__)

# Large videos need longer than PTB's 20s media write default.
MEDIA_WRITE_TIMEOUT = 120


def run_cleanup_loop() -> None:
    while True:
        try:
            cleanup()
        except Exception:
            logger.exception("Error during cleanup")
        time.sleep(3600)


async def _post_init(application: Application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", MENU_START),
        BotCommand("mp3", MENU_MP3),
        BotCommand("mp4", MENU_MP4),
    ])


def register_handlers(application: Application, bot: TelegramVideoBot) -> None:
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CommandHandler("mp3", bot.mp3))
    application.add_handler(CommandHandler("mp4", bot.mp4))
    application.add_handler(
        MessageHandler(URL_MESSAGE_FILTER, bot.handle_url, block=False)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, bot.no_entry)
    )


def build_application() -> Application:
    request = HTTPXRequest(
        write_timeout=MEDIA_WRITE_TIMEOUT,
        media_write_timeout=MEDIA_WRITE_TIMEOUT,
    )
    return (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(_post_init)
        .build()
    )


def run_bot() -> None:
    bot = TelegramVideoBot()
    telegram_app = build_application()
    register_handlers(telegram_app, bot)

    telegram_app.run_webhook(
        listen="127.0.0.1",
        port=8003,
        url_path=BOT_TOKEN,
        webhook_url=URL + BOT_TOKEN,
    )


def run_api() -> None:
    uvicorn.run(api_app, host="127.0.0.1", port=5000)


if __name__ == "__main__":
    threading.Thread(target=run_api, daemon=True).start()
    threading.Thread(target=run_cleanup_loop, daemon=True).start()
    run_bot()
