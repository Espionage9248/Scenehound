from fastapi.testclient import TestClient

from scenehound.app import create_app

CONFIG = """
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


def _write(tmp_path, extra=""):
    (tmp_path / "config.yaml").write_text(CONFIG + extra)
    return tmp_path


def test_disabled_serves_no_webhook_route(tmp_path, monkeypatch):
    # Route absence is checked by behavior (404), robust across FastAPI versions
    # that nest included routers rather than flattening app.routes.
    # The webhook also serves the UI's Grab events, so the UI (on by default)
    # must be off too: everything off -> no webhook route.
    monkeypatch.setenv("SCENEHOUND_UI_ENABLED", "false")
    app = create_app(config_dir=_write(tmp_path))  # import_completer absent -> disabled
    r = TestClient(app).post("/import/webhook?apikey=wk", json={"eventType": "Test"})
    assert r.status_code == 404


def test_disabled_starts_no_completer_task(tmp_path):
    app = create_app(config_dir=_write(tmp_path))
    with TestClient(app):  # runs lifespan (refresh task still starts; completer must not)
        assert getattr(app.state, "import_completer", None) is None


def test_enabled_serves_webhook_route(tmp_path):
    app = create_app(config_dir=_write(tmp_path, "import_completer:\n  enabled: true\n"))
    # Wrong apikey -> 401 (not 404) proves the route is registered.
    r = TestClient(app).post("/import/webhook?apikey=wrong", json={"eventType": "Test"})
    assert r.status_code == 401


def test_enabled_sets_completer_on_state(tmp_path):
    app = create_app(config_dir=_write(tmp_path, "import_completer:\n  enabled: true\n"))
    with TestClient(app):
        assert getattr(app.state, "import_completer", None) is not None
