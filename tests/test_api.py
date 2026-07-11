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


def test_rss_mode_rewrites_to_best_scene_not_first(app):
    import httpx
    from datetime import date
    from fastapi.testclient import TestClient

    from scenehound.api import IndexHolder
    from scenehound.clients.prowlarr import ProwlarrClient
    from scenehound.models import SceneFingerprint
    from scenehound.wanted_index import WantedIndex

    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>ThatFetishGirl.2026-07-07.Latex.Worship.Session.1080p</title>
      <guid>g1</guid><link>http://p/dl/1</link>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=feed)

    state = app.state.scenehound
    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    state.prowlarr = ProwlarrClient(
        state.config.prowlarr.url, state.config.prowlarr.api_key, hc
    )
    # Same site+date, different titles. The higher-id scene's title matches the
    # release exactly (site+date+title -> 100); the lower-id scene only shares
    # site+date (-> 75). Both clear the threshold, so a naive "first >= threshold"
    # would rewrite to the lower-id scene. Best-scene must pick the higher-id one.
    low = SceneFingerprint(100, "That Fetish Girl", (), date(2026, 7, 7),
                           "Generic Session", ())
    high = SceneFingerprint(200, "That Fetish Girl", (), date(2026, 7, 7),
                            "Latex Worship Session", ())
    holder = IndexHolder()
    holder.set(WantedIndex([low, high]))
    state.index_holder = holder

    r = TestClient(app).get(
        "/indexer/empornium/api", params={"t": "search", "apikey": "shk"}
    )
    assert titles(r) == [
        "That.Fetish.Girl.2026-07-07.Latex.Worship.Session.XXX.1080p"
    ]


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["index_size"] == 1


def test_unknown_function_returns_203(client):
    r = client.get("/indexer/empornium/api", params={"t": "tvsearch", "apikey": "shk"})
    assert ET.fromstring(r.content).get("code") == "203"


def test_prowlarr_error_returns_900(app):
    import httpx
    from fastapi.testclient import TestClient

    from scenehound.clients.prowlarr import ProwlarrClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    # Swap in a Prowlarr transport that 500s (mirrors the conftest app fixture,
    # which builds ProwlarrClient the same way, but with a failing handler).
    state = app.state.scenehound
    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    state.prowlarr = ProwlarrClient(
        state.config.prowlarr.url, state.config.prowlarr.api_key, hc
    )
    # unresolved scene -> passthrough -> one gated Prowlarr call -> 500 -> ProwlarrError -> 900
    r = TestClient(app).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "unknownsite 01.01.2026", "apikey": "shk"},
    )
    assert ET.fromstring(r.content).get("code") == "900"


def test_healthz_reports_index_age(client, app):
    body = client.get("/healthz").json()
    assert isinstance(body["index_age_seconds"], float)  # index was set in the fixture
    assert body["index_age_seconds"] >= 0


def test_healthz_null_age_when_no_index(app):
    from fastapi.testclient import TestClient

    app.state.scenehound.index_holder = IndexHolder()  # never refreshed
    body = TestClient(app).get("/healthz").json()
    assert body["index_size"] == 0
    assert body["index_age_seconds"] is None
