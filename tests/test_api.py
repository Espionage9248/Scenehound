import xml.etree.ElementTree as ET

from scenehound.api import IndexHolder
from scenehound.torznab import parse_feed


def titles(response):
    return [c.title for c in parse_feed(response.content)]


def test_wrong_apikey_rejected(client):
    r = client.get("/indexer/empornium/api", params={"t": "caps", "apikey": "bad"})
    assert ET.fromstring(r.content).get("code") == "100"


def test_unknown_slug_rejected(client):
    r = client.get("/indexer/nope/api", params={"t": "caps", "apikey": "shk"})
    assert ET.fromstring(r.content).get("code") == "201"


def test_caps(client):
    r = client.get("/indexer/empornium/api", params={"t": "caps", "apikey": "shk"})
    assert ET.fromstring(r.content).tag == "caps"


def test_search_mode_returns_only_rewritten_match(client, prowlarr_calls):
    r = client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    got = titles(r)
    assert got == ["That.Fetish.Girl.2026-07-07.Latex.Worship.Session.XXX.1080p"]
    assert prowlarr_calls  # went to prowlarr
    assert prowlarr_calls[0]["apikey"] == "pk"


def test_search_early_exit_single_query(client, prowlarr_calls):
    client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    # first variant already found a >=75 match; no escalation
    assert len(prowlarr_calls) == 1


def test_unresolvable_scene_passes_through(client, prowlarr_calls):
    r = client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "unknownsite 01.01.2026",
                "cat": "6000", "apikey": "shk"},
    )
    # passthrough: verbatim query forwarded, results unrewritten
    assert prowlarr_calls[0]["q"] == "unknownsite 01.01.2026"
    assert set(titles(r)) == {
        "TFG.26.07.07.Latex.Worship.Session.1080p",
        "Unrelated.Studio.Thing.720p",
    }


def test_unparseable_query_passes_through(client, prowlarr_calls):
    client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "just some words", "apikey": "shk"},
    )
    assert prowlarr_calls[0]["q"] == "just some words"


def test_missing_index_passes_through(app, prowlarr_calls):
    from fastapi.testclient import TestClient

    app.state.scenehound.index_holder = IndexHolder()  # no index loaded
    r = TestClient(app).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026", "apikey": "shk"},
    )
    assert prowlarr_calls[0]["q"] == "thatfetishgirl 07.07.2026"


def test_rate_limit_returns_empty_when_dry(app, prowlarr_calls):
    from fastapi.testclient import TestClient

    for bucket in app.state.scenehound.buckets.values():
        while bucket.try_acquire():
            pass
    r = TestClient(app).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026", "apikey": "shk"},
    )
    assert titles(r) == []
    assert prowlarr_calls == []


def test_rss_mode_rewrites_matches_and_passes_rest(client, prowlarr_calls):
    r = client.get(
        "/indexer/empornium/api", params={"t": "search", "apikey": "shk"}
    )
    got = titles(r)
    assert "That.Fetish.Girl.2026-07-07.Latex.Worship.Session.XXX.1080p" in got
    assert "Unrelated.Studio.Thing.720p" in got
    assert "q" not in prowlarr_calls[0]


def test_rss_mode_ignores_rate_limit(app, prowlarr_calls):
    from fastapi.testclient import TestClient

    for bucket in app.state.scenehound.buckets.values():
        while bucket.try_acquire():
            pass
    r = TestClient(app).get(
        "/indexer/empornium/api", params={"t": "search", "apikey": "shk"}
    )
    assert len(titles(r)) == 2  # fetch still happened


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["index_size"] == 1
