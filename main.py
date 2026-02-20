import time
import asyncio
import logging
import uvicorn
import threading
from api_server import app
from downloader import VideoDownloader
from bot_token import BOT_TOKEN, URL, USER_ID
from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram_bot import TelegramVideoBot


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def run_cleanup():
    while True:
        try:
            VideoDownloader().cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        time.sleep(3600)


def run_bot():
    bot = TelegramVideoBot()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("mp3", bot.mp3))
    app.add_handler(CommandHandler("mp4", bot.mp4))
    app.add_handler(MessageHandler(filters.Chat(USER_ID) \
                                   & filters.TEXT & ~filters.COMMAND\
                                    &filters.Regex(r'^https?://'), bot.handle_url))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.no_entry))

    loop = asyncio.get_event_loop()
    loop.run_until_complete(app.bot.set_my_commands([
        BotCommand("start", "התחל"),
        BotCommand("mp3", "הורד שיר"),
        BotCommand("mp4", "הורד סרטון")
    ]))
    app.run_webhook(listen="127.0.0.1", 
                    port=8003, 
                    url_path=BOT_TOKEN, 
                    webhook_url=URL + BOT_TOKEN)


def run_api():
    uvicorn.run(app, host="127.0.0.1", port=5000)

if __name__ == "__main__":
    threading.Thread(target=run_api, daemon=True).start()
    threading.Thread(target=run_cleanup, daemon=True).start()
    run_bot()

