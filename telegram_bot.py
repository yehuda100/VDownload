import os
import asyncio
from telegram import Message, Update
from telegram.ext import ContextTypes
from downloader import VideoDownloader, DOWNLOAD_DIR
from secure_links import SecureLinkManager


MAX_SIZE = 50 * 1024 * 1024


class TelegramVideoBot:

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
        downloader = VideoDownloader(status_msg = msg)

        try:
            result = await downloader.download(url, format_type=context.user_data.get("format", "mp4"))
            if not result.get("success"):
                await msg.edit_text(f"❌ שגיאת הורדה: {result.get('Download error')}")
                return
            filename, title, size = result['filename'], result['title'], result['size']
            filepath = os.path.join(DOWNLOAD_DIR, filename)
            await msg.edit_text("📤 מעלה את הקובץ...")
            if size <= MAX_SIZE:
                asyncio.create_task(
                    self._send_file(update, msg, filepath, title)
                    )
            else:
                await msg.delete()
                link = SecureLinkManager().save_metadata(filename, title)
                mb = size / 1024 / 1024
                await update.message.reply_text(f"🔗 הסרטון גדול מדי ({mb:.1f}MB)\n📥 {title}\n{link}")
        except Exception as e:
            await msg.edit_text(f"❌ שגיאה: {str(e)}")

    async def _send_file(self, update: Update, msg: Message, filepath: str, title: str):
        if filepath.endswith(('.mp3', '.m4a', '.ogg', '.opus')):
            with open(filepath, 'rb') as f:
                await update.message.reply_audio(audio=f, filename=title)
        else:
            with open(filepath, 'rb') as f:
                await update.message.reply_video(video=f, filename=title, supports_streaming=True)
        await msg.delete()
        os.remove(filepath)
