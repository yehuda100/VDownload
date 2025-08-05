# VDownload
## Telegram Video Downloader Bot

A private Telegram bot that downloads videos from various platforms (YouTube, etc.) and delivers them either directly or via secure download links.

## Features

- 🎬 Downloads videos from multiple platforms using yt-dlp
- 📱 Direct upload for small files (<50MB)
- 🔗 Secure temporary download links for larger files
- 📊 Real-time download progress updates
- 🔒 User access control (private bot)
- ⏰ Automatic file cleanup after 1 hour
- 🌐 Web API for secure file downloads

## Architecture

- **Telegram Bot**: Handles user interactions and video processing
- **FastAPI Server**: Serves secure download links
- **yt-dlp Integration**: Downloads videos from various platforms
- **Secure Link Manager**: Generates time-limited download URLs

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create `bot_token.py` with your configuration:
```python
BOT_TOKEN = "your_telegram_bot_token"
SECRET_KEY = "your_secret_key_for_links"
URL = "https://yourdomain.com/"
USER_ID = your_telegram_user_id, or set(telegram_ids)
```

3. Run the application:
```bash
python main.py
```

## Usage

1. Send `/start` to initialize the bot
2. Send any video URL to download
3. For files <50MB: Direct upload to chat
4. For larger files: Secure download link (expires in 24h)

## File Structure

- `main.py` - Application entry point
- `telegram_bot.py` - Bot handlers and logic
- `downloader.py` - Video download functionality
- `api_server.py` - FastAPI server for file downloads
- `secure_links.py` - Secure link generation and verification

## Security

- User access restricted to specific Telegram user ID
- Download links are cryptographically signed
- Links expire after 24 hours
- Automatic cleanup of downloaded files

---

**Note**: This is a private bot designed for personal use. Ensure you have proper permissions for downloading content.