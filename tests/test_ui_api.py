from fastapi import FastAPI
from fastapi.testclient import TestClient

from scenehound.api import AppState, IndexHolder
from scenehound.config import Config, IndexerConfig, ServiceConfig
from scenehound.observe import SessionStore
from scenehound.rate_limiter import TokenBucket
from scenehound.ui_api import ui_router
from scenehound.wanted_index import WantedIndex


def _config():
    return Config(
        whisparr=ServiceConfig("http://w:6969", "wk"),
        prowlarr=ServiceConfig("http://p:9696", "pk"),
        indexers=(IndexerConfig("empornium", 12),),
        api_key="shk",
    )


def _app(store=None, with_index=False):
    cfg = _config()
    holder = IndexHolder()
    if with_index:
        holder.set(WantedIndex([]))
    app = FastAPI()
    app.include_router(ui_router)
    app.state.scenehound = AppState(
        config=cfg, prowlarr=None, index_holder=holder,
        buckets={i.slug: TokenBucket(4, 15.0) for i in cfg.indexers},
        store=store,
    )
    return app


def test_ui_page_served_without_auth():
    r = TestClient(_app()).get("/ui")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Scenehound" in r.text
    assert "shk" not in r.text          # no key embedded in the shell


def test_sessions_requires_apikey():
    store = SessionStore(max_sessions=5, max_candidates=10)
    client = TestClient(_app(store))
    assert client.get("/ui/api/sessions").status_code == 401
    assert client.get("/ui/api/sessions?apikey=wrong").status_code == 401


def test_sessions_returns_snapshot_and_index():
    store = SessionStore(max_sessions=5, max_candidates=10)
    rec = store.recorder("empornium", 75, "q")
    rec.commit()
    r = TestClient(_app(store, with_index=True)).get("/ui/api/sessions?apikey=shk")
    assert r.status_code == 200
    body = r.json()
    assert len(body["sessions"]) == 1
    assert body["unmatched_grabs"] == []
    assert body["sessions"][0]["outcome"]["grabbed_guid"] is None
    assert body["index"]["size"] == 0
    assert body["index"]["age_seconds"] is not None


def test_sessions_empty_when_store_none():
    r = TestClient(_app(store=None)).get("/ui/api/sessions?apikey=shk")
    assert r.status_code == 200
    assert r.json()["sessions"] == []


def test_ui_page_has_app_markers():
    r = TestClient(_app()).get("/ui")
    for marker in ('id="sessions"', 'id="keyform"', 'id="indexinfo"',
                   "scenehound_apikey", "/ui/api/sessions", "grabbed_guid"):
        assert marker in r.text, marker
