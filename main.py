import threading
import logging
import uvicorn
from bot_token import BOT_TOKEN, URL, USER_ID
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram_bot import TelegramVideoBot
from telegram import BotCommand
from api_server import app


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def run_bot():
    bot = TelegramVideoBot()
    app = Application.builder().token('304491376:AAGxkyC3jz12VrBr5vWGimIu4I2A47SQ9tY').build()
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("mp3", bot.mp3))
    app.add_handler(CommandHandler("mp4", bot.mp4))
    app.add_handler(MessageHandler(filters.Chat(USER_ID) & filters.TEXT & ~filters.COMMAND, bot.handle_url))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.no_entry))
    app.bot.set_my_commands([
        BotCommand("start", "התחל"),
        BotCommand("mp3", "הורד שיר"),
        BotCommand("mp4", "הורד סרטון")
    ])
    # app.run_webhook(listen="127.0.0.1", 
    #                 port=8003, 
    #                 url_path=BOT_TOKEN, 
    #                 webhook_url=URL + BOT_TOKEN)
    app.run_polling()

def run_api():
    uvicorn.run(app, host="127.0.0.1", port=5000)

if __name__ == "__main__":
    threading.Thread(target=run_api, daemon=True).start()
    run_bot()