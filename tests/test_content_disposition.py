"""Content-Disposition for large-file links must survive Latin-1 header encoding."""
import logging
from urllib.parse import quote

import pytest
from starlette.responses import Response
from starlette.testclient import TestClient

from api_server import app, content_disposition_header
from core.secure_links import SecureLinkManager

HEBREW_NAME = "אבגדהו.mp4"
HEBREW_ENCODED = quote(HEBREW_NAME, safe="")
HEB_FILE_ID = "44444444-4444-4444-8444-444444444444"
BAD_FILE_ID = "55555555-5555-4555-8555-555555555555"


def test_raw_hebrew_filename_starlette_header_raises():
    """The production failure: raw UTF-8 in Content-Disposition."""
    raw = f"attachment; filename*=UTF-8''{HEBREW_NAME}"
    with pytest.raises(UnicodeEncodeError) as exc_info:
        Response(content="", headers={"Content-Disposition": raw})
    assert exc_info.value.start == 29


def test_hebrew_filename_header_is_latin1_and_builds_response():
    header = content_disposition_header(HEBREW_NAME)
    header.encode("latin-1")
    assert 'filename="download.mp4"' in header
    assert f"filename*=UTF-8''{HEBREW_ENCODED}" in header
    assert HEBREW_ENCODED.startswith("%D7")
    response = Response(
        content="",
        headers={
            "X-Accel-Redirect": "/protected_downloads/abc.mp4",
            "Content-Disposition": header,
        },
    )
    stored = response.headers["content-disposition"]
    stored.encode("latin-1")
    assert "download.mp4" in stored
    assert "%D7" in stored


def test_ascii_filename_stays_readable():
    header = content_disposition_header("Episode 12.mp4")
    header.encode("latin-1")
    assert 'filename="Episode 12.mp4"' in header
    assert "filename*=UTF-8''Episode%2012.mp4" in header


def test_mixed_title_drops_non_ascii_from_fallback_only():
    header = content_disposition_header("Episode 12 שלום.mp4")
    header.encode("latin-1")
    assert 'filename="Episode 12.mp4"' in header
    assert "%D7" in header


def test_download_link_serves_hebrew_title(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    link = SecureLinkManager.save_metadata(HEB_FILE_ID, str(video), "אבגדהו")
    sig = link.split("sig=", 1)[1]

    caplog.set_level(logging.INFO, logger="vdownload.audit")
    client = TestClient(app)
    response = client.get(f"/VDownload/{HEB_FILE_ID}?sig={sig}")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    disposition.encode("latin-1")
    assert 'filename="download.mp4"' in disposition
    assert "%D7" in disposition
    assert "LINK_OK" in caplog.text
    assert HEB_FILE_ID in caplog.text


def test_success_is_not_logged_when_response_cannot_be_built(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr("core.secure_links.TEMP_LINKS_DIR", str(tmp_path))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video")
    link = SecureLinkManager.save_metadata(BAD_FILE_ID, str(video), "אבגדהו")
    sig = link.split("sig=", 1)[1]

    def broken_response(*args, **kwargs):
        raise UnicodeEncodeError("latin-1", "x", 29, 35, "ordinal not in range(256)")

    monkeypatch.setattr("api_server.Response", broken_response)
    caplog.set_level(logging.INFO, logger="vdownload.audit")
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(f"/VDownload/{BAD_FILE_ID}?sig={sig}")

    assert response.status_code == 500
    assert "LINK_OK" not in caplog.text
