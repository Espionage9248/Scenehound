"""Application factory and lifecycle."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from scenehound.api import AppState, IndexHolder, router
from scenehound.clients.prowlarr import ProwlarrClient
from scenehound.clients.whisparr import WhisparrClient
from scenehound.config import load_config
from scenehound.rate_limiter import TokenBucket
from scenehound.wanted_index import WantedIndex

log = logging.getLogger("scenehound")
REFRESH_INTERVAL_SECONDS = 900.0


def configure_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def refresh_loop(
    state: AppState,
    whisparr: WhisparrClient,
    interval_seconds: float = REFRESH_INTERVAL_SECONDS,
) -> None:
    while True:
        try:
            scenes = await whisparr.fetch_wanted()
            state.index_holder.set(WantedIndex(scenes))
            log.info("index refreshed scenes=%d", len(scenes))
        except Exception as exc:
            log.error("index refresh failed (keeping previous index): %s", exc)
        await asyncio.sleep(interval_seconds)


def create_app(config_dir: Path | None = None) -> FastAPI:
    config_dir = config_dir or Path(os.environ.get("SCENEHOUND_CONFIG_DIR", "/config"))
    config = load_config(config_dir, env=os.environ)
    configure_logging(config.log_level)

    state = AppState(
        config=config,
        prowlarr=None,  # type: ignore[arg-type]  # set in lifespan with a live client
        index_holder=IndexHolder(),
        buckets={
            i.slug: TokenBucket(config.rate_limit.burst, config.rate_limit.refill_seconds)
            for i in config.indexers
        },
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient() as http_client:
            state.prowlarr = ProwlarrClient(
                config.prowlarr.url, config.prowlarr.api_key, http_client
            )
            whisparr = WhisparrClient(
                config.whisparr.url, config.whisparr.api_key, http_client
            )
            task = asyncio.create_task(refresh_loop(state, whisparr))
            log.info(
                "scenehound started indexers=%s threshold=%d",
                [i.slug for i in config.indexers], config.matching.threshold,
            )
            try:
                yield
            finally:
                task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.state.scenehound = state
    return app
