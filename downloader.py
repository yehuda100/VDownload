import os
import time
import asyncio
import uuid
import yt_dlp
from telegram import Message

DOWNLOAD_DIR = "protected_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class VideoDownloader:
    def __init__(self, status_msg: Message = None) -> None:
        self.status_msg: Message = status_msg
        self.loop: asyncio.AbstractEventLoop = None
        self.last_progress_update: float = 0.0

    def build_options(self, file_id: str, format_type: str) -> dict:
        opts = {
            'quiet': True, 
            'no_warnings': True,
            'noplaylist': True,
            'progress_hooks': [self._progress_hook],
            'outtmpl': f'{DOWNLOAD_DIR}/{file_id}.%(ext)s'
        }
        if format_type == "mp3":
            opts.update({
                'format': 'bestaudio/best',
                "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}
                ]
            })
        else:
            opts.update({
                'format': 'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best',
                'merged_output_format': 'mp4'
            })
        return opts


    def _progress_hook(self, d: dict) -> None:
        if d.get('status') != 'downloading':
            return
        
        if not self.status_msg or not self.loop:
            return

        now = time.monotonic()
        if now - self.last_progress_update < 2.0:
            return
        self.last_progress_update = now
        total = d.get('total_bytes') or d.get('total_bytes_estimate')
        downloaded = d.get('downloaded_bytes', 0)
        percent = downloaded / total * 100 if total else 0
        title = d.get('info_dict', {}).get('title', 'unknown')
        text = f"⬇️ {title}\n📊 התקדמות: {percent:.1f}%"

        self.loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self.update_progress(text))
        )

    async def download(self, url: str, format_type: str) -> dict:
        try:
            self.loop = asyncio.get_running_loop()
            file_id = str(uuid.uuid4())
            ydl_opts = self.build_options(file_id, format_type)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await self.loop.run_in_executor(
                    None, lambda: ydl.extract_info(url, download=True)
                )

            if 'requested_downloads' in info and info['requested_downloads']:
                final_path = info['requested_downloads'][0].get('filepath')
            else:
                final_path = ydl.prepare_filename(info)

            filename = os.path.basename(final_path)
            ext = os.path.splitext(filename)[1]

            return {
                'filename': filename,
                'title': f"{info.get('title', 'unknown')}.{ext}",
                'size': os.path.getsize(final_path),
                'success': True
            }

        except Exception as e:
            return {'Download error': str(e).split("please report")[0]}
    
async def update_progress(self, text: str):
    try:
        await self.status_msg.edit_text(text)
    except Exception:
        pass 