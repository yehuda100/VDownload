import os
from fastapi import FastAPI, Request, Response
from secure_links import SecureLinkManager

app = FastAPI()

@app.get("/VDownload/{file_id}")
async def download_file(file_id: str, request: Request):
    sig = request.query_params.get("sig")
    filename = SecureLinkManager.verify(file_id, sig)
    if not filename or not os.path.exists(filename):
        return Response(content="❌ קישור פג תוקף", status_code=403)
    safe = os.path.basename(filename)
    return Response(
        content="",
        headers={
            "X-Accel-Redirect": f"/protected_downloads/{safe}",
            "Content-Disposition": f'attachment; filename="{safe}"'
        }
    )