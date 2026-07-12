from fastapi import FastAPI
from fastapi.testclient import TestClient

from scenehound.api import AppState, IndexHolder, router as search_router
from scenehound.config import Config, IndexerConfig, ServiceConfig
from scenehound.import_api import import_router
from scenehound.rate_limiter import TokenBucket


def _config():
    return Config(
        whisparr=ServiceConfig("http://w:6969", "wk"),
        prowlarr=ServiceConfig("http://p:9696", "pk"),
        indexers=(IndexerConfig("empornium", 12),),
        api_key="shk",
    )


class FakeCompleter:
    def __init__(self):
        self.notified = 0

    def notify(self):
        self.notified += 1


def _app(completer):
    cfg = _config()
    app = FastAPI()
    app.include_router(search_router)
    app.include_router(import_router)
    app.state.scenehound = AppState(
        config=cfg, prowlarr=None, index_holder=IndexHolder(),
        buckets={i.slug: TokenBucket(4, 15.0) for i in cfg.indexers},
    )
    app.state.import_completer = completer
    return app


def test_webhook_rejects_bad_apikey():
    fc = FakeCompleter()
    r = TestClient(_app(fc)).post("/import/webhook?apikey=wrong", json={"eventType": "Test"})
    assert r.status_code == 401
    assert fc.notified == 0


def test_webhook_test_event_returns_200_without_waking():
    fc = FakeCompleter()
    r = TestClient(_app(fc)).post("/import/webhook?apikey=shk", json={"eventType": "Test"})
    assert r.status_code == 200
    assert fc.notified == 0  # Test event must not trigger a sweep


def test_webhook_real_event_wakes_completer():
    fc = FakeCompleter()
    r = TestClient(_app(fc)).post(
        "/import/webhook?apikey=shk", json={"eventType": "ManualInteractionRequired"})
    assert r.status_code == 200
    assert fc.notified == 1


def test_webhook_ok_when_completer_absent():
    # Route registered but no completer instance -> still 200, no crash.
    cfg = _config()
    app = FastAPI()
    app.include_router(import_router)
    app.state.scenehound = AppState(
        config=cfg, prowlarr=None, index_holder=IndexHolder(),
        buckets={i.slug: TokenBucket(4, 15.0) for i in cfg.indexers},
    )
    app.state.import_completer = None
    r = TestClient(app).post("/import/webhook?apikey=shk", json={"eventType": "Grab"})
    assert r.status_code == 200
