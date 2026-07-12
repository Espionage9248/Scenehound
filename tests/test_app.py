import asyncio

import httpx
import pytest

from scenehound.api import AppState, IndexHolder
from scenehound.app import create_app, refresh_loop
from scenehound.clients.whisparr import WhisparrClient

CONFIG_YAML = """
whisparr:
  url: http://w:6969
  api_key: wk
prowlarr:
  url: http://p:9696
  api_key: pk
indexers:
  - slug: empornium
    prowlarr_id: 12
"""

WANTED_PAGE = {
    "page": 1, "pageSize": 1000, "totalRecords": 1,
    "records": [{
        "id": 1, "title": "T", "releaseDate": "2026-07-07",
        "studioTitle": "S", "credits": [],
    }],
}


def test_create_app_boots_with_config(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    state = app.state.scenehound
    assert state.config.whisparr.api_key == "wk"
    assert "empornium" in state.buckets
    assert state.index_holder.current is None  # populated by refresh task


def test_create_app_missing_config_logs_clear_error(tmp_path, caplog):
    # No config.yaml at all: must log an actionable error before dying, not
    # crash the factory silently (the container would otherwise crash-loop mute).
    with caplog.at_level("ERROR"):
        with pytest.raises(FileNotFoundError):
            create_app(config_dir=tmp_path)
    assert "config not found" in caplog.text
    assert "indexers:" in caplog.text


def test_create_app_env_tag_config_logs_clear_error(tmp_path, caplog):
    # The design-doc example used `api_key: !env ...`, which yaml.safe_load rejects.
    # Startup must explain that keys come from environment variables, not a YAML tag.
    (tmp_path / "config.yaml").write_text(
        "whisparr:\n  api_key: !env WHISPARR_API_KEY\nindexers: []\n"
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(Exception):
            create_app(config_dir=tmp_path)
    assert "config invalid" in caplog.text
    assert "environment variables" in caplog.text


async def test_refresh_populates_index(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    state: AppState = app.state.scenehound

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=WANTED_PAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        whisparr = WhisparrClient("http://w:6969", "wk", hc)
        task = asyncio.create_task(
            refresh_loop(state, whisparr, interval_seconds=3600)
        )
        for _ in range(100):
            if state.index_holder.current is not None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
    assert len(state.index_holder.current) == 1


async def test_refresh_failure_keeps_old_index(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    state: AppState = app.state.scenehound
    from scenehound.wanted_index import WantedIndex

    old = WantedIndex([])
    state.index_holder.set(old)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        whisparr = WhisparrClient("http://w:6969", "wk", hc)
        task = asyncio.create_task(
            refresh_loop(state, whisparr, interval_seconds=3600)
        )
        await asyncio.sleep(0.1)
        task.cancel()
    assert state.index_holder.current is old


def _paths(app):
    # FastAPI's lazy routing wraps each include_router() call in an
    # _IncludedRouter with no .path attribute; the real per-route paths only
    # show up via effective_route_contexts(). Fall back to that when a route
    # in app.routes has no .path of its own.
    paths = set()
    for r in app.routes:
        if hasattr(r, "path"):
            paths.add(r.path)
        elif hasattr(r, "effective_route_contexts"):
            for ctx in r.effective_route_contexts():
                paths.add(ctx.path)
    return paths


def test_ui_enabled_by_default_mounts_routes_and_store(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    assert app.state.scenehound.store is not None
    assert app.state.scenehound.store.max_candidates == 200
    assert {"/ui", "/ui/api/sessions", "/import/webhook"} <= _paths(app)


def test_ui_disabled_no_routes_no_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENEHOUND_UI_ENABLED", "false")
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    assert app.state.scenehound.store is None
    paths = _paths(app)
    assert "/ui" not in paths and "/ui/api/sessions" not in paths
    # import-completer is off by default, so the webhook vanishes too
    assert "/import/webhook" not in paths


def test_webhook_mounted_for_ui_even_without_completer(tmp_path):
    # ui on (default) + completer off (default) -> webhook still mounted so
    # Grab events reach the store.
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    assert "/import/webhook" in _paths(app)


async def test_completer_gets_the_store(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENEHOUND_IMPORT_ENABLED", "true")
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    # Enter lifespan so the completer is constructed, then check the wiring.
    from fastapi.testclient import TestClient
    with TestClient(app):
        completer = app.state.import_completer
        assert completer._store is app.state.scenehound.store
        assert completer._store is not None
