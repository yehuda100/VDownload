"""
FastAPI endpoint for secure large-file downloads (nginx X-Accel-Redirect).
"""
import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse

from core import SecureLinkManager
from core.download_audit import log_link_access
from utils import build_display_filename
from utils.file_utils import KNOWN_MEDIA_EXTENSIONS

logger = logging.getLogger(__name__)
app = FastAPI()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def content_disposition_header(filename: str) -> str:
    """RFC 6266 attachment header that Starlette can encode as Latin-1.

    ``filename*`` is percent-encoded UTF-8 (RFC 5987). ``filename`` is an ASCII
    fallback for clients that ignore ``filename*``.
    """
    encoded = quote(filename, safe="")
    suffix = Path(filename).suffix.lower()
    if suffix not in KNOWN_MEDIA_EXTENSIONS:
        suffix = ".mp4"
    collapsed = " ".join(filename.encode("ascii", "ignore").decode("ascii").split())
    stem = collapsed
    if stem.lower().endswith(suffix):
        stem = stem[: -len(suffix)]
    stem = stem.strip(" .")
    fallback = f"download{suffix}" if not stem else f"{stem}{suffix}"
    fallback = (
        fallback.replace("\\", "")
        .replace('"', "")
        .replace("\r", "")
        .replace("\n", "")
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"


def _not_found_html(title: str = "N/A") -> str:
    return f"""
    <div style="text-align:center; margin-top:100px; font-family:sans-serif;">
        <h1 style="color:#d9534f;">404 - Not Found</h1>
        <p>The requested file is missing or invalid.</p>
        <hr style="width:50%; border:0; border-top:1px solid #eee;">
        <small style="color:#999;">Path: {title}</small>
    </div>
    """


@app.get("/VDownload/{file_id}")
async def download_file(file_id: str, request: Request):
    client_ip = _client_ip(request)
    sig = request.query_params.get("sig")
    verified = SecureLinkManager.verify(file_id, sig)

    if not verified:
        log_link_access(
            file_id,
            title="",
            client_ip=client_ip,
            success=False,
            reason="invalid_or_expired_signature",
        )
        return HTMLResponse(content=_not_found_html(), status_code=404)

    if not os.path.exists(verified["filename"]):
        log_link_access(
            file_id,
            title=verified["title"],
            client_ip=client_ip,
            success=False,
            reason="file_missing_on_disk",
        )
        return HTMLResponse(content=_not_found_html(verified["title"]), status_code=404)

    disk_name = os.path.basename(verified["filename"])
    download_name = build_display_filename(verified["title"], verified["filename"])
    response = Response(
        content="",
        headers={
            "X-Accel-Redirect": f"/protected_downloads/{disk_name}",
            "Content-Disposition": content_disposition_header(download_name),
        },
    )
    log_link_access(
        file_id,
        title=verified["title"],
        client_ip=client_ip,
        success=True,
    )
    return response
