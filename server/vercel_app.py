from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.main import app


REPO_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = REPO_DIR / "web" / "dist"


if (WEB_DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST_DIR / "assets"), name="web-assets")


@app.get("/", include_in_schema=False)
@app.get("/{full_path:path}", include_in_schema=False)
def serve_web_app(full_path: str = ""):
    if full_path.startswith(("api/", "media/")):
        raise HTTPException(status_code=404, detail="Not Found")
    index_path = WEB_DIST_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="Frontend bundle not found")
    requested_path = (WEB_DIST_DIR / full_path).resolve()
    if WEB_DIST_DIR.resolve() in requested_path.parents and requested_path.is_file():
        return FileResponse(requested_path)
    return FileResponse(index_path)
