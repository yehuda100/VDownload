"""Entry-point wiring that does not start the webhook."""
from main import MEDIA_WRITE_TIMEOUT, build_application


def test_application_uses_long_write_timeouts():
    application = build_application()
    request = application.bot.request
    assert request._media_write_timeout == MEDIA_WRITE_TIMEOUT
    assert request._client.timeout.write == MEDIA_WRITE_TIMEOUT
    assert MEDIA_WRITE_TIMEOUT == 120
