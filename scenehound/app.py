"""Application factory and lifecycle."""
from __future__ import annotations

import asyncio
import contextlib
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
from scenehound.import_api import import_router
from scenehound.import_completer import ImportCompleter
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
    # Configure logging BEFORE loading config: a bad or missing config.yaml crashes
    # the uvicorn factory before the app ever starts, so this is the only chance to
    # say *why* instead of the container silently crash-looping.
    configure_logging(os.environ.get("SCENEHOUND_LOG_LEVEL", "info"))
    config_path = config_dir / "config.yaml"
    try:
        config = load_config(config_dir, env=os.environ)
    except FileNotFoundError:
        log.error(
            "config not found: %s does not exist. Create it with at least an "
            "'indexers:' list; Whisparr/Prowlarr URLs and API keys are supplied via "
            "environment variables (WHISPARR_URL, WHISPARR_API_KEY, PROWLARR_URL, "
            "PROWLARR_API_KEY). Exiting.",
            config_path,
        )
        raise
    except Exception as exc:
        log.error(
            "config invalid: could not parse %s: %s. Common cause: a '!env ...' YAML "
            "tag — Scenehound reads API keys and URLs from environment variables, not "
            "YAML tags, so config.yaml only needs the 'indexers:' list. Exiting.",
            config_path, exc,
        )
        raise
    # Re-apply the level from the loaded config (basicConfig above is idempotent for
    # handlers, so set the level explicitly in case config.yaml specifies a different one).
    logging.getLogger().setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

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
            completer_task = None
            if config.import_completer.enabled:
                completer = ImportCompleter(
                    whisparr, state.index_holder, config.import_completer
                )
                app.state.import_completer = completer
                completer_task = asyncio.create_task(completer.run())
                log.info(
                    "import-completer enabled dry_run=%s multipack=%s",
                    config.import_completer.dry_run, config.import_completer.multipack,
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
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                if completer_task is not None:
                    completer_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await completer_task

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    if config.import_completer.enabled:
        app.include_router(import_router)
    app.state.scenehound = state
    return app
