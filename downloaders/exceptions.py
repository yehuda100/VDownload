"""
Typed errors for download failures. Raised by downloaders, caught by manager/bot.
"""

class DownloaderException(Exception):
    """Base exception for all downloader errors."""
    pass


class APIException(DownloaderException):
    """Raised when API request fails."""
    
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API error [{status_code}]: {message}")


class ProgressException(DownloaderException):
    """Raised when progress tracking fails."""
    pass


class ProgressStalledException(ProgressException):
    """Raised when download progress stalls."""
    
    def __init__(self, timeout: int = 3):
        self.timeout = timeout
        super().__init__(f"Progress stalled: no progress change for {timeout} seconds")


class ProgressURLNotFoundException(ProgressException):
    """Raised when progress URL is not available."""
    
    def __init__(self):
        super().__init__("Progress URL not available")


class DownloadException(DownloaderException):
    """Raised when file download fails."""
    
    def __init__(self, status_code: int = None, message: str = "Download failed"):
        self.status_code = status_code
        if status_code:
            super().__init__(f"Download error [{status_code}]: {message}")
        else:
            super().__init__(message)


class DownloadURLNotFoundException(DownloadException):
    """Raised when download URL is not available."""
    
    def __init__(self):
        super().__init__(message="Download URL not available")


class InvalidURLException(DownloaderException):
    """Raised when provided URL is invalid."""
    
    def __init__(self, url_type: str = "URL"):
        self.url_type = url_type
        super().__init__(f"Invalid {url_type}")


class StreamNotFoundException(DownloaderException):
    """Raised when no suitable stream is found."""
    
    def __init__(self, stream_type: str = "stream"):
        self.stream_type = stream_type
        super().__init__(f"No suitable {stream_type} found for this video")


class FFmpegException(DownloaderException):
    """Raised when FFmpeg processing fails."""
    
    def __init__(self, stderr: str = ""):
        self.stderr = stderr
        super().__init__(f"FFmpeg error: {stderr}")


class ExtractionException(DownloaderException):
    """Raised when video/audio info extraction fails."""
    
    def __init__(self, message: str = "Failed to extract video information"):
        super().__init__(message)