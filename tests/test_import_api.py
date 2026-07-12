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


class FakeStore:
    def __init__(self):
        self.grabs = []

    def record_grab(self, release_title, download_id):
        self.grabs.append((release_title, download_id))


def _app_with_store(completer, store):
    app = _app(completer)
    app.state.scenehound.store = store
    return app


GRAB_PAYLOAD = {
    "eventType": "Grab",
    "release": {"releaseTitle": "That Fetish Girl 2026-07-07 Latex 1080p"},
    "downloadId": "HASH1",
}


def test_webhook_grab_records_and_still_notifies():
    fc, fs = FakeCompleter(), FakeStore()
    r = TestClient(_app_with_store(fc, fs)).post(
        "/import/webhook?apikey=shk", json=GRAB_PAYLOAD)
    assert r.status_code == 200
    assert fs.grabs == [("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")]
    assert fc.notified == 1


def test_webhook_grab_download_id_fallback_inside_release():
    fc, fs = FakeCompleter(), FakeStore()
    payload = {"eventType": "Grab",
               "release": {"releaseTitle": "T", "downloadId": "HASH2"}}
    TestClient(_app_with_store(fc, fs)).post("/import/webhook?apikey=shk", json=payload)
    assert fs.grabs == [("T", "HASH2")]


def test_webhook_grab_without_store_still_ok():
    fc = FakeCompleter()
    r = TestClient(_app(fc)).post("/import/webhook?apikey=shk", json=GRAB_PAYLOAD)
    assert r.status_code == 200
    assert fc.notified == 1


def test_webhook_non_grab_event_does_not_record():
    fc, fs = FakeCompleter(), FakeStore()
    TestClient(_app_with_store(fc, fs)).post(
        "/import/webhook?apikey=shk", json={"eventType": "Download"})
    assert fs.grabs == []
    assert fc.notified == 1
