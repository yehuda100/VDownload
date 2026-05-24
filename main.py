"""Entry point: Telegram webhook bot, FastAPI link server, and cleanup thread."""
import asyncio
import logging
import threading
import time

import uvicorn
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from api_server import app as api_app
from config import BOT_TOKEN, URL, USER_ID
from core.telegram_bot import TelegramVideoBot
from utils import cleanup

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("vdownload.audit").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

URL_MESSAGE_FILTER = (
    filters.Chat(USER_ID)
    & filters.TEXT
    & ~filters.COMMAND
    & filters.Regex(r"^https?://")
)


def run_cleanup_loop() -> None:
    while True:
        try:
            cleanup()
        except Exception:
            logger.exception("Error during cleanup")
        time.sleep(3600)


def run_bot() -> None:
    bot = TelegramVideoBot()
    telegram_app = Application.builder().token(BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", bot.start))
    telegram_app.add_handler(CommandHandler("mp3", bot.mp3))
    telegram_app.add_handler(CommandHandler("mp4", bot.mp4))
    telegram_app.add_handler(MessageHandler(URL_MESSAGE_FILTER, bot.handle_url))
    telegram_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, bot.no_entry)
    )

    async def set_commands() -> None:
        await telegram_app.bot.set_my_commands([
            BotCommand("start", "התחל"),
            BotCommand("mp3", "הורד שיר"),
            BotCommand("mp4", "הורד סרטון"),
        ])

    asyncio.run(set_commands())
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
