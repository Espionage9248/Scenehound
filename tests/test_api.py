import xml.etree.ElementTree as ET

from scenehound.api import IndexHolder
from scenehound.torznab import parse_feed


def titles(response):
    return [c.title for c in parse_feed(response.content)]


REWRITTEN = (
    "ThatFetishGirl.2026-07-07.Latex.Worship.Session.XXX.1080p"
    " [TFG.26.07.07.Latex.Worship.Session.1080p]"
)


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
    assert got == [REWRITTEN]
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
    assert REWRITTEN in got
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
    # This test's feed title spells the site out and dates it in full
    # ("ThatFetishGirl.2026-07-07...") where FEED_MATCHING uses the alias and a
    # yy.mm.dd stamp ("TFG.26.07.07..."). Both are dotted; the site token and
    # date form are what differ. So the bracketed suffix differs from REWRITTEN's
    # too — it is the tracker's own title, verbatim, not a normalized one. Do not
    # collapse this assertion into the shared REWRITTEN constant.
    assert titles(r) == [
        "ThatFetishGirl.2026-07-07.Latex.Worship.Session.XXX.1080p"
        " [ThatFetishGirl.2026-07-07.Latex.Worship.Session.1080p]"
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
    rewritten = [c for c in s["candidates"] if not c["passed_through"]]
    assert len(rewritten) == 1
    assert rewritten[0]["rewritten_title"] is not None


def test_prowlarr_error_records_error(make_app, store):
    app = make_app(store=store, status=500)
    r = _get(app, q=SEARCH_Q)
    assert b'code="900"' in r.content
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["status"] == "error"
    assert any("prowlarr" in n.lower() for n in s["notes"])


def test_prowlarr_error_never_exposes_the_prowlarr_key(make_app, store):
    app = make_app(store=store, status=500)
    r = _get(app, q=SEARCH_Q)
    assert b"apikey=pk" not in r.content
    assert "apikey=pk" not in json.dumps(store.snapshot())


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


def test_feed_title_matches_the_recorded_title(app_with_store, store):
    # The recorder's rewritten_title is what observe.record_grab correlates
    # incoming grab webhooks against. If the feed and the recorder disagree,
    # grab tracking silently stops working.
    from fastapi.testclient import TestClient

    r = TestClient(app_with_store).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    feed_title = titles(r)[0]
    recorded = [c for c in store.snapshot()["sessions"][0]["candidates"] if c["matched"]]
    assert recorded[0]["rewritten_title"] == feed_title


def test_original_title_attr_survives_the_suffix(client):
    from scenehound.torznab import ORIGINAL_TITLE_ATTR

    r = client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    root = ET.fromstring(r.content)
    attrs = root.findall(
        ".//channel/item/{http://torznab.com/schemas/2015/feed}attr")
    values = {a.get("name"): a.get("value") for a in attrs}
    assert values[ORIGINAL_TITLE_ATTR] == "TFG.26.07.07.Latex.Worship.Session.1080p"


def test_suffix_can_be_switched_off(make_app):
    from fastapi.testclient import TestClient

    from scenehound.config import NamingConfig

    app = make_app(naming=NamingConfig(original_title_suffix=False))
    r = TestClient(app).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    assert titles(r) == ["ThatFetishGirl.2026-07-07.Latex.Worship.Session.XXX.1080p"]


# ---- passed-through items -----------------------------------------------------
import json
from datetime import date

import pytest

from scenehound.api import _best_scene
from scenehound.models import SceneFingerprint
from scenehound.wanted_index import WantedIndex

# Scores below were computed with the real matcher against conftest's SCENE
# (That Fetish Girl / TFG, 2026-07-07, "Latex Worship Session", Jane Doe).
FEED_NEAR_MISS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>TFG.Jane.Doe.Beach.Day.720p</title>
      <guid>https://tracker.example/torrents.php?action=download&amp;id=42&amp;authkey=AUTHKEY123&amp;torrent_pass=PASS456</guid>
      <link>http://p/dl/42</link>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""


def test_best_scene_keeps_the_first_scene_on_a_rewrite_tie():
    # Both clear the threshold at 100; 200 has MORE strong signals. Today's
    # RSS rule is first-wins on a confidence tie, and must stay so.
    low = SceneFingerprint(100, "That Fetish Girl", (), date(2026, 7, 7),
                           "Latex Worship Session", ())
    high = SceneFingerprint(200, "That Fetish Girl", (), date(2026, 7, 7),
                            "Latex Worship Session", ("Jane Doe",))
    scene, ms = _best_scene(WantedIndex([low, high]),
                            "ThatFetishGirl.2026-07-07.Latex.Worship.Session.[Jane.Doe].1080p",
                            75, 3)
    assert (scene.scene_id, ms.confidence) == (100, 100)


def test_best_scene_prefers_more_strong_signals_below_threshold():
    # Both vetoed to 0: 100 is a site-mismatch with only the date agreeing,
    # 200 a foreign-title with site AND date agreeing -- the nearer miss.
    other = SceneFingerprint(100, "Other Studio", (), date(2026, 7, 7), "Beach Day", ())
    tfg = SceneFingerprint(200, "That Fetish Girl", (), date(2026, 7, 7),
                           "Latex Worship Session", ())
    scene, ms = _best_scene(WantedIndex([other, tfg]),
                            "ThatFetishGirl.2026-07-07.Totally.Different.Film.1080p",
                            75, 3)
    assert scene.scene_id == 200
    assert ms.veto == "foreign-title"


def test_best_scene_is_none_when_no_wanted_scene_is_a_candidate():
    scene = SceneFingerprint(7, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ("Jane Doe", "Mary Major"))
    assert _best_scene(WantedIndex([scene]), "Unrelated.Studio.Thing.720p", 75, 3) is None


def test_rss_records_passed_through_items(app_with_store, store):
    _get(app_with_store)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["rewritten"] == 1
    assert s["outcome"]["passed_through"] == 1
    [pt] = [c for c in s["candidates"] if c["passed_through"]]
    assert pt["title"] == "Unrelated.Studio.Thing.720p"
    assert pt["scene_id"] is None and pt["scene"] is None
    assert pt["matched"] is False


def test_rss_records_the_closest_scene_below_threshold(make_app, store):
    _get(make_app(store=store, feed=FEED_NEAR_MISS))
    s = store.snapshot()["sessions"][0]
    [pt] = s["candidates"]
    assert pt["passed_through"] is True
    assert pt["confidence"] == 70
    assert set(pt["strong_signals"]) == {"site", "performer"}
    assert pt["scene_id"] == 7
    assert pt["scene"]["title"] == "Latex Worship Session"
    assert "AUTHKEY123" not in json.dumps(s) and "PASS456" not in json.dumps(s)


def test_rss_without_index_records_every_item_unscored(make_app, store):
    _get(make_app(store=store, with_index=False))
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["rewritten"] == 0
    assert s["outcome"]["passed_through"] == 2
    assert all(c["passed_through"] and c["scene_id"] is None for c in s["candidates"])
    assert "wanted list not loaded — items passed through unscored" in s["notes"]


def test_unparseable_passthrough_records_scored_results(app_with_store, store):
    r = _get(app_with_store, q="just some words")
    # The response is Prowlarr's, verbatim.
    assert set(titles(r)) == {"TFG.26.07.07.Latex.Worship.Session.1080p",
                              "Unrelated.Studio.Thing.720p"}
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["matched_count"] == 2
    assert s["outcome"]["passed_through"] == 2
    hit = next(c for c in s["candidates"]
               if c["title"] == "TFG.26.07.07.Latex.Worship.Session.1080p")
    assert hit["confidence"] == 100                    # would have matched...
    assert hit["matched"] is False                     # ...but went back unchanged
    assert hit["passed_through"] is True and hit["scene_id"] == 7


def test_no_index_passthrough_records_unscored_results(make_app, store):
    _get(make_app(store=store, with_index=False), q=SEARCH_Q)
    s = store.snapshot()["sessions"][0]
    assert s["fallback_reason"] == "no-index"
    assert len(s["candidates"]) == 2
    assert all(c["scene_id"] is None and c["confidence"] == 0 for c in s["candidates"])


def test_passthrough_scores_nothing_without_a_store(make_app, monkeypatch):
    calls = []
    monkeypatch.setattr("scenehound.api._best_scene",
                        lambda *a, **k: calls.append(a) or None)
    _get(make_app(), q="just some words")
    assert calls == []


def test_passthrough_scoring_failure_still_returns_the_feed(app_with_store, store,
                                                            monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("scoring exploded")

    monkeypatch.setattr("scenehound.api._best_scene", boom)
    r = _get(app_with_store, q="just some words")
    assert len(titles(r)) == 2
    s = store.snapshot()["sessions"][0]
    assert s["candidates"] == []
    assert s["outcome"]["matched_count"] == 2
    assert "passthrough scoring for the UI failed" in caplog.text


@pytest.mark.parametrize("q,with_index", [
    (None, True),                          # RSS
    (None, False),                         # RSS, no index
    ("just some words", True),             # unparseable-query
    ("Unknown Studio 01.01.2020", True),   # scene-unresolved
    (SEARCH_Q, False),                     # no-index
])
def test_ui_on_or_off_returns_identical_bytes(make_app, q, with_index):
    from scenehound.observe import SessionStore
    st = SessionStore(max_sessions=50, max_candidates=200)
    plain = _get(make_app(with_index=with_index), q=q)
    traced = _get(make_app(store=st, with_index=with_index), q=q)
    assert plain.content == traced.content
    assert st.snapshot()["sessions"][0]["candidates"]    # and it did record
