import os
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from secure_links import SecureLinkManager

app = FastAPI()

@app.get("/VDownload/{file_id}")
async def download_file(file_id: str, request: Request):
    sig = request.query_params.get("sig")
    filename, title = SecureLinkManager.verify(file_id, sig)
    if not filename or not os.path.exists(filename):
        error_html = """
    <div style="text-align:center; margin-top:100px; font-family:sans-serif;">
        <h1 style="color:#d9534f;">404 - Not Found</h1>
        <p>The requested file is missing or invalid.</p>
        <hr style="width:50%; border:0; border-top:1px solid #eee;">
        <small style="color:#999;">Path: {title}</small>
    </div>
    """.format(title=title if title else "N/A")
    
        return HTMLResponse(content=error_html, status_code=404)
    filename = os.path.basename(filename)
    return Response(
        content="",
        headers={
            "X-Accel-Redirect": f"/protected_downloads/{filename}",
            "Content-Disposition": f"attachment; filename*=UTF-8''{title}"
        })