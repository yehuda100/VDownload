"""Entry point: Telegram webhook bot, FastAPI link server, and cleanup thread."""
import hashlib
import logging
import re
import threading
import time

import uvicorn
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from api_server import app as api_app
import config
from config import BOT_TOKEN, URL
from core.logging_config import configure_logging
from core.messages import MENU_MP3, MENU_MP4, MENU_START
from core.telegram_bot import URL_MESSAGE_FILTER, TelegramVideoBot
from utils import cleanup

configure_logging()
logger = logging.getLogger(__name__)

# Fixed path so the bot token is not part of the URL nginx and access logs see.
WEBHOOK_PATH = "telegram-webhook"
# Telegram secret_token: 1-256 chars from A-Za-z0-9_-.
_WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")


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


def webhook_secret() -> str:
    """Secret Telegram sends in X-Telegram-Bot-Api-Secret-Token.

    Uses config.WEBHOOK_SECRET when it matches Telegram's charset. Otherwise
    derives one from BOT_TOKEN so an existing config.py keeps working.
    """
    configured = getattr(config, "WEBHOOK_SECRET", "")
    if isinstance(configured, str) and _WEBHOOK_SECRET_RE.fullmatch(configured):
        return configured
    return hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]


def webhook_settings() -> dict[str, str]:
    return {
        "url_path": WEBHOOK_PATH,
        "webhook_url": URL + WEBHOOK_PATH,
        "secret_token": webhook_secret(),
    }


def run_bot() -> None:
    bot = TelegramVideoBot()
    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )
    register_handlers(telegram_app, bot)

    settings = webhook_settings()
    telegram_app.run_webhook(
        listen="127.0.0.1",
        port=8003,
        url_path=settings["url_path"],
        webhook_url=settings["webhook_url"],
        secret_token=settings["secret_token"],
    )


def run_api() -> None:
    # Query strings on /VDownload include the HMAC. Do not write them to the access log.
    uvicorn.run(api_app, host="127.0.0.1", port=5000, access_log=False)


if __name__ == "__main__":
    threading.Thread(target=run_api, daemon=True).start()
    threading.Thread(target=run_cleanup_loop, daemon=True).start()
    run_bot()
