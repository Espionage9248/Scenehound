"""Webhook route for the import-completer. Kept separate from the search API so
the search surface (api.py) has zero import-completer coupling."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

log = logging.getLogger("scenehound.import_api")
import_router = APIRouter()


@import_router.post("/import/webhook")
async def import_webhook(request: Request) -> Response:
    state = request.app.state.scenehound
    if request.query_params.get("apikey") != state.config.api_key:
        return Response(status_code=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    event = str(payload.get("eventType", ""))
    log.debug("webhook event=%s payload=%s", event, payload)
    completer = getattr(request.app.state, "import_completer", None)
    # Whisparr's "Test" button posts eventType=Test; 200 it so the Connect saves,
    # but never trigger a sweep from it. Any real event rings the doorbell.
    if event and event != "Test" and completer is not None:
        completer.notify()
    return Response(status_code=200)
