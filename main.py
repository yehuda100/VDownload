import threading
import logging
import uvicorn
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from bot_token import BOT_TOKEN, URL, USER_ID
from telegram_bot import TelegramVideoBot
from api_server import app


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def run_bot():
    bot = TelegramVideoBot()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(MessageHandler(filters.Chat(USER_ID) & filters.TEXT & ~filters.COMMAND, bot.handle_url))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.no_entry))
    app.run_webhook(listen="127.0.0.1", 
                    port=8003, 
                    url_path=BOT_TOKEN, 
                    webhook_url=URL + BOT_TOKEN)

def run_api():
    uvicorn.run(app, host="127.0.0.1", port=5000)

if __name__ == "__main__":
    threading.Thread(target=run_api, daemon=True).start()
    run_bot()