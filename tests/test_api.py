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


SEARCH_Q = "That Fetish Girl 07.07.2026"


def _get(app, q=None, apikey="shk"):
    from fastapi.testclient import TestClient
    params = {"t": "search", "apikey": apikey}
    if q is not None:
        params["q"] = q
    return TestClient(app).get("/indexer/empornium/api", params=params)


def test_search_records_session(app_with_store, store):
    r = _get(app_with_store, q=SEARCH_Q)
    assert r.status_code == 200
    s = store.snapshot()["sessions"][0]
    assert s["kind"] == "search"
    assert s["slug"] == "empornium"
    assert s["raw_query"] == SEARCH_Q
    assert s["parsed_site"] == "That Fetish Girl"
    assert s["parsed_dates"] == ["2026-07-07"]
    assert s["scenes"][0]["scene_id"] == 7
    assert s["threshold"] == 75
    # first variant fired and returned the 2-item feed; early exit leaves the
    # remaining planned variants recorded as not fired
    fired = [v for v in s["variants"] if v["fired"]]
    assert fired == [{"query": "That Fetish Girl 26.07.07", "fired": True, "result_count": 2}]
    assert any(not v["fired"] for v in s["variants"])
    assert s["outcome"]["status"] == "matched"
    assert s["outcome"]["matched_count"] == 1
    top = s["candidates"][0]
    assert top["matched"] is True
    assert top["title"] == "TFG.26.07.07.Latex.Worship.Session.1080p"
    assert top["rewritten_title"] is not None
    assert top["strong_signals"] and top["detail"]
    nomatch = s["candidates"][1]
    assert nomatch["matched"] is False and nomatch["rewritten_title"] is None


def test_unparseable_query_records_passthrough(app_with_store, store):
    _get(app_with_store, q="not a dated query")
    s = store.snapshot()["sessions"][0]
    assert s["kind"] == "passthrough"
    assert s["fallback_reason"] == "unparseable-query"
    assert s["outcome"]["status"] == "matched"   # 2 verbatim results returned
    assert s["outcome"]["matched_count"] == 2


def test_unresolved_scene_records_passthrough(app_with_store, store):
    _get(app_with_store, q="Unknown Studio 01.01.2020")
    s = store.snapshot()["sessions"][0]
    assert s["fallback_reason"] == "scene-unresolved"


def test_missing_index_records_passthrough(make_app, store):
    app = make_app(store=store, with_index=False)
    _get(app, q=SEARCH_Q)
    assert store.snapshot()["sessions"][0]["fallback_reason"] == "no-index"


def test_rate_deferred_search_records_note(app_with_store, store):
    app_with_store.state.scenehound.buckets["empornium"]._tokens = 0
    _get(app_with_store, q=SEARCH_Q)
    s = store.snapshot()["sessions"][0]
    assert s["kind"] == "search"
    assert s["outcome"]["status"] == "empty"
    assert any("rate-deferred" in n for n in s["notes"])


def test_rss_records_summary(app_with_store, store):
    _get(app_with_store)   # no q -> RSS mode
    s = store.snapshot()["sessions"][0]
    assert s["kind"] == "rss"
    assert s["outcome"]["status"] == "rss-summary"
    assert s["outcome"]["items_total"] == 2
    assert s["outcome"]["rewritten"] == 1
    assert len(s["candidates"]) == 1
    assert s["candidates"][0]["rewritten_title"] is not None


def test_prowlarr_error_records_error(make_app, store):
    app = make_app(store=store, status=500)
    r = _get(app, q=SEARCH_Q)
    assert b'code="900"' in r.content
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["status"] == "error"
    assert any("prowlarr" in n.lower() for n in s["notes"])


def test_no_store_means_no_capture_and_identical_bytes(make_app):
    from scenehound.observe import SessionStore
    st = SessionStore(max_sessions=50, max_candidates=200)
    app_plain = make_app()                          # store=None -> NULL_RECORDER
    app_traced = make_app(store=st)
    r_plain = _get(app_plain, q=SEARCH_Q)
    r_traced = _get(app_traced, q=SEARCH_Q)
    assert r_plain.content == r_traced.content      # byte-identical responses
    assert len(st.snapshot()["sessions"]) == 1


def test_caps_request_not_captured(app_with_store, store):
    from fastapi.testclient import TestClient
    TestClient(app_with_store).get(
        "/indexer/empornium/api", params={"t": "caps", "apikey": "shk"})
    assert store.snapshot()["sessions"] == []


def test_unexpected_exception_records_error_session(app_with_store, store, monkeypatch):
    import pytest
    from fastapi.testclient import TestClient

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("scenehound.api.build_feed", _boom)
    with pytest.raises(RuntimeError):
        TestClient(app_with_store, raise_server_exceptions=True).get(
            "/indexer/empornium/api",
            params={"t": "search", "q": SEARCH_Q, "apikey": "shk"},
        )
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["status"] == "error"
    assert any("internal error" in n for n in s["notes"])


from fastapi.testclient import TestClient

from scenehound.config import MatchingConfig

FEED_SKEWED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>TFG.26.07.05.Latex.Worship.Session.Jane.Doe.1080p</title>
      <guid>g-skew</guid><link>http://p/dl/9</link>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""


def test_search_respects_configured_date_skew(make_app):
    # Release stamped 07-05 for the 07-07 scene (2 days off, site+performer+title).
    params = {"t": "search", "q": "thatfetishgirl 07.07.2026",
              "cat": "6000", "apikey": "shk"}
    # Default window (3): forgiven and rewritten.
    lenient = TestClient(make_app(feed=FEED_SKEWED))
    assert len(titles(lenient.get("/indexer/empornium/api", params=params))) == 1
    # Window 1: the old hard veto — proves the CONFIGURED value reaches score().
    strict = TestClient(make_app(
        matching=MatchingConfig(date_skew_days=1), feed=FEED_SKEWED))
    assert titles(strict.get("/indexer/empornium/api", params=params)) == []
