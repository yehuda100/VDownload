import os
import aiofiles
import time
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes
from downloader import VideoDownloader, DOWNLOAD_DIR
from secure_links import SecureLinkManager

MAX_SIZE = 50 * 1024 * 1024

class TelegramVideoBot:
    def __init__(self):
        self.downloader = VideoDownloader()
        self.linker = SecureLinkManager()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        VideoDownloader.format_type = "mp4"
        await update.message.reply_text("🎬 שלח קישור ואני אוריד לך את הסרטון!")

    async def mp3(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["format"] = "mp3"
        await update.message.reply_text("🎵 שלח קישור ואני אוריד לך את השיר!")

    async def mp4(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["format"] = "mp4"
        await update.message.reply_text("🎬 שלח קישור ואני אוריד לך את הסרטון!")

    async def no_entry(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🚫 אם אתה לא @yehuda100 – זה לא בשבילך 🤖")

    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()
        msg = await update.message.reply_text("🔄 מתחיל להוריד...")
        self.downloader.status_msg = msg
        try:
            result = await self.downloader.download(url, format_type=context.user_data.get("format", "mp4"))
            if not result.get("success"):
                await msg.edit_text(f"❌ שגיאת הורדה: {result.get('Download error')}")
                return
            filename, title, size = result['filename'], result['title'], result['size']
            await msg.edit_text("📤 מעלה את הסרטון...")
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)
            if size <= MAX_SIZE:
                async with aiofiles.open(filename, 'rb') as f:
                    await update.message.reply_video(video=await f.read(), supports_streaming=True)
                os.remove(filename)
            else:
                link = self.linker.generate(filename, title)
                mb = size / 1024 / 1024
                await update.message.reply_text(f"🔗 הסרטון גדול מדי ({mb:.1f}MB)\n📥 {title}\n{link}")
            await msg.delete()
        except Exception as e:
            await msg.edit_text(f"❌ שגיאה: {str(e)}")
        finally:
            self.downloader.status_msg = None
            await self._cleanup()

    async def _cleanup(self):
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            path = os.path.join(DOWNLOAD_DIR, f)
            if os.path.getctime(path) < now - 3600:
                os.remove(path)
