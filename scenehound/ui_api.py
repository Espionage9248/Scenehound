"""Read-only web UI routes. Kept separate from the search API (api.py) and the
webhook (import_api.py) so the Torznab surface has zero UI coupling; mounted
by app.py only when config.ui.enabled.

Auth split: /ui is an unauthenticated static shell (it contains no data and no
secrets — the unraid WebUI button and bare bookmarks must work); all data comes
from /ui/api/sessions, which requires the Scenehound API key exactly like every
other keyed route.
"""
from __future__ import annotations

import logging
import time
from importlib import resources

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("scenehound.ui_api")
ui_router = APIRouter()

# Read once at import: the page never changes at runtime.
_PAGE = resources.files("scenehound").joinpath("static/ui.html").read_text(encoding="utf-8")


@ui_router.get("/ui")
async def ui_page() -> HTMLResponse:
    return HTMLResponse(_PAGE)


@ui_router.get("/ui/api/sessions")
async def ui_sessions(request: Request) -> Response:
    state = request.app.state.scenehound
    if request.query_params.get("apikey") != state.config.api_key:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    store = state.store
    data = store.snapshot() if store is not None else {"sessions": [], "unmatched_grabs": []}
    holder = state.index_holder
    age = (
        time.monotonic() - holder.refreshed_at
        if holder.refreshed_at is not None
        else None
    )
    data["index"] = {
        "size": len(holder.current) if holder.current is not None else 0,
        "age_seconds": age,
    }
    return JSONResponse(data)
