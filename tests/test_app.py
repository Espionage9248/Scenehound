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
