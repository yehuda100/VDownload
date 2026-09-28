"""User-facing copy and exception mapping."""
import config
from core.messages import (
    GENERIC_FAIL,
    NO_VIDEO,
    PRIVATE,
    RATE_LIMIT,
    STALLED,
    TIMEOUT,
    UNAVAILABLE,
    UNEXPECTED,
    UNSUPPORTED,
    format_byte_progress,
    format_link_expiry,
    format_prepare_percent,
    message_for_exception,
    too_big_message,
)
from downloaders.exceptions import (
    APIException,
    DownloaderException,
    ExtractionException,
    FFmpegException,
    InvalidURLException,
    ProgressException,
    ProgressStalledException,
)
from downloaders.yt_dlp_downloader import instagram_auth_user_message


def test_prepare_percent_is_a_whole_number():
    assert format_prepare_percent(50.0) == "Preparing the file... 50%"
    assert format_prepare_percent(99.6) == "Preparing the file... 100%"


def test_byte_progress_with_total_and_speed():
    assert format_byte_progress(13_002_342, 31_457_280, 1_258_291) == (
        "Downloading... 12.4 / 30.0 MB · 1.2 MB/s"
    )


def test_byte_progress_omits_unknown_total_and_non_positive_speed():
    assert format_byte_progress(13_002_342, None, 0) == "Downloading... 12.4 MB"
    assert format_byte_progress(10, None, None) is None


def test_expiry_uses_hours_when_it_divides_evenly():
    assert format_link_expiry(24 * 3600) == "24 hours"
    assert format_link_expiry(3600) == "1 hour"
    assert format_link_expiry(120) == "2 minutes"
    assert format_link_expiry(45) == "45 seconds"


def test_too_big_message_includes_expiry(monkeypatch):
    monkeypatch.setattr(config, "EXPIRY", 24 * 3600)
    text = too_big_message(87_241_523, "שלום", "https://example/file")
    assert "83.2 MB" in text
    assert "שלום" in text
    assert "https://example/file" in text
    assert "It expires in 24 hours." in text


def test_private_unavailable_unsupported_and_no_video():
    assert message_for_exception(
        ExtractionException("ERROR: [youtube] x: Private video. Sign in")
    ) == PRIVATE
    assert message_for_exception(
        ExtractionException("ERROR: [youtube] x: Video unavailable")
    ) == UNAVAILABLE
    assert message_for_exception(
        ExtractionException("ERROR: [youtube] x: This video is unavailable")
    ) == UNAVAILABLE
    assert message_for_exception(InvalidURLException("YouTube URL")) == UNSUPPORTED
    assert message_for_exception(
        ExtractionException("ERROR: Unsupported URL: https://example.com")
    ) == UNSUPPORTED
    assert message_for_exception(
        ExtractionException("ERROR: [Instagram] x: There is no video in this post")
    ) == NO_VIDEO


def test_timeout_stall_empty_and_huge_body():
    assert message_for_exception(FFmpegException("FFmpeg timed out")) == TIMEOUT
    assert message_for_exception(ProgressException("VDA download timed out")) == TIMEOUT
    assert message_for_exception(ProgressStalledException(timeout=30)) == STALLED
    assert message_for_exception(DownloaderException()) == GENERIC_FAIL
    huge = "<html>" + ("x" * 5000)
    shown = message_for_exception(APIException(500, huge))
    assert shown == GENERIC_FAIL
    assert "html" not in shown
    assert "API error" not in shown


def test_other_rate_limit_is_not_the_instagram_paragraph():
    shown = message_for_exception(
        ExtractionException("ERROR: [TikTok] abc: HTTP Error 429: Too Many Requests")
    )
    assert shown == RATE_LIMIT
    assert "YTDLP_COOKIES_FILE" not in shown


def test_instagram_paragraph_is_kept(monkeypatch):
    monkeypatch.delattr(config, "YTDLP_COOKIES_FILE", raising=False)
    paragraph = instagram_auth_user_message()
    shown = message_for_exception(ExtractionException(paragraph))
    assert shown == f"❌ {paragraph}"
    assert "Download failed" not in shown
    assert "YTDLP_COOKIES_FILE" in shown
    assert "Netscape" in shown
    assert "rate limit or login required" in shown


def test_unexpected_exception_stays_generic():
    assert message_for_exception(RuntimeError("disk full")) == UNEXPECTED
