"""
Download strategy interface (Strategy Pattern).

Each implementation writes to DOWNLOAD_DIR as {file_id}.{ext} and returns metadata.
"""
import uuid
from abc import ABC, abstractmethod

from .progress import ProgressReporter


class BaseDownloader(ABC):
    """Async download strategy; report progress via ProgressReporter."""

    @abstractmethod
    async def download(
        self, url: str, format_type: str, progress: ProgressReporter
    ) -> dict:
        """
        Downloads the video or audio from the given URL and returns:
        - file_id: unique identifier for the downloaded file
        - title: title of the video/audio
        """
        pass

    @staticmethod
    def generate_file_id() -> str:
        return str(uuid.uuid4())
