import os
import time
import asyncio
import yt_dlp
from telegram import Message

DOWNLOAD_DIR = "protected_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

class VideoDownloader:
    def __init__(self) -> None:
        self.status_msg: Message = None
        self.loop: asyncio.AbstractEventLoop = None
        self._last_update: float = 0.0

    def build_options(self, format_type: str) -> dict:
        opts = {
            'output': f'{DOWNLOAD_DIR}/%(title).25s.%(ext)s',
            'restrictfilenames': True,
            'noplaylist': True,
            'progress_hooks': [self._progress_hook]
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
        if d.get('status') != 'downloading' or not self.status_msg:
            return

        now = time.monotonic()
        if now - self._last_update < 1.0:
            return
        self._last_update = now
        total = d.get('total_bytes') or d.get('total_bytes_estimate') or 1
        downloaded = d.get('downloaded_bytes', 0)
        percent = downloaded / total * 100
        title = d.get('info_dict', {}).get('title', 'וידאו')
        text = f"⬇️ {title}\n📊 התקדמות: {percent:.1f}%"

        asyncio.run_coroutine_threadsafe(
            self.status_msg.edit_text(text),
            self.loop
        )

    async def download(self, url: str, format_type: str) -> dict:
        try:
            self.loop = asyncio.get_running_loop()

            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = await self.loop.run_in_executor(
                    None, ydl.extract_info, url, False
                )
            title = info.get("title", "ללא שם")
            ydl_opts = self.build_options(format_type)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await self.loop.run_in_executor(None, ydl.download, [url])

            file = self._find_file(title)
            return {
                'filename': file,
                'title': title,
                'size': os.path.getsize(file),
                'success': True
            }

        except Exception as e:
            return {'Download error': str(e).split("please report")[0]}

    def _find_file(self, title: str) -> str:
        first_word = title.strip().split()[0].lower()
        dir_files = os.listdir(DOWNLOAD_DIR)
        matched_files = [f for f in dir_files if f.lower().startswith(first_word)]

        candidates = matched_files if matched_files else dir_files
        candidates.sort(
            key=lambda x: os.path.getctime(os.path.join(DOWNLOAD_DIR, x)),
            reverse=True
        )
        return os.path.join(DOWNLOAD_DIR, candidates[0]) if candidates else None