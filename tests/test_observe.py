import json
from datetime import date

from scenehound.dates import ParsedQuery
from scenehound.matcher import MatchScore
from scenehound.models import ReleaseCandidate, SceneFingerprint
from scenehound.observe import (
    CandidateTrace, NULL_RECORDER, Outcome, SearchSession, SessionStore, VariantTrace,
)


def make_session(store: SessionStore, *, sid=None, slug="empornium",
                 candidates=(), status="empty", matched_count=0) -> SearchSession:
    return SearchSession(
        session_id=sid if sid is not None else store.next_id(),
        started_at=1000.0, finished_at=1001.5,
        slug=slug, kind="search", raw_query="That Fetish Girl 07.07.2026",
        threshold=75, parsed_site="That Fetish Girl", parsed_dates=("2026-07-07",),
        scenes=(), variants=(VariantTrace("That Fetish Girl 26.07.07", True, 2),),
        candidates=tuple(candidates), dropped_candidates=0,
        outcome=Outcome(status=status, matched_count=matched_count),
        fallback_reason=None, notes=(),
    )


def make_candidate(*, title="TFG.26.07.07.X.1080p", guid="g1", confidence=80,
                   matched=True, rewritten="That Fetish Girl 2026-07-07 X 1080p") -> CandidateTrace:
    return CandidateTrace(
        title=title, guid=guid, size=1000, seeders=5, scene_id=7,
        confidence=confidence, strong_signals=("date", "site"), veto=None,
        detail={"date": 40.0, "site": 35.0}, matched=matched,
        rewritten_title=rewritten if matched else None,
    )


def test_store_bounded_ring_evicts_oldest():
    store = SessionStore(max_sessions=3, max_candidates=200)
    for _ in range(5):
        store.add(make_session(store))
    snap = store.snapshot()
    assert len(snap["sessions"]) == 3
    # newest first: ids 5, 4, 3 survive
    assert [s["session_id"] for s in snap["sessions"]] == [5, 4, 3]


def test_snapshot_is_json_serializable_and_complete():
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate()],
                           status="matched", matched_count=1))
    snap = store.snapshot()
    text = json.dumps(snap)  # must not raise
    s = snap["sessions"][0]
    assert s["slug"] == "empornium"
    assert s["threshold"] == 75
    assert s["parsed_dates"] == ["2026-07-07"]
    assert s["variants"][0]["query"] == "That Fetish Girl 26.07.07"
    assert s["candidates"][0]["strong_signals"] == ["date", "site"]
    assert s["candidates"][0]["detail"] == {"date": 40.0, "site": 35.0}
    assert s["outcome"]["status"] == "matched"
    assert s["outcome"]["grab"] is None
    assert snap["unmatched_grabs"] == []
    assert "shk" not in text  # no config keys can appear: they are never stored


def test_next_id_monotonic():
    store = SessionStore(max_sessions=2, max_candidates=200)
    assert [store.next_id(), store.next_id(), store.next_id()] == [1, 2, 3]


def test_snapshot_skips_bad_entries_never_raises():
    store = SessionStore(max_sessions=2, max_candidates=200)
    store.add(object())      # not a SearchSession: add is shielded, snapshot copes
    snap = store.snapshot()  # snapshot swallows the bad entry
    assert snap["sessions"] == []


SCENE = SceneFingerprint(
    scene_id=7, site="That Fetish Girl", site_aliases=("TFG",),
    date=date(2026, 7, 7), title="Latex Worship Session",
    performers=("Jane Doe", "Mary Major"),
)


def _cand(guid="g1", title="TFG.26.07.07.Latex.Worship.Session.1080p"):
    return ReleaseCandidate(title=title, guid=guid, link="http://p/dl?apikey=SECRET",
                            size=1000, seeders=5)


def _ms(conf, strong=("date", "site"), veto=None):
    return MatchScore(conf, tuple(strong), veto, {"date": 40.0, "site": 35.0})


def test_recorder_records_full_search_session():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "That Fetish Girl 07.07.2026")
    rec.query(ParsedQuery("That Fetish Girl", (date(2026, 7, 7),)), (SCENE,))
    rec.variants_planned(["q1", "q2", "q3"])
    rec.variant_fired("q1", 2)
    rec.note("early exit: threshold met")
    rec.scored([
        (_cand("g1"), SCENE, _ms(90), "That Fetish Girl 2026-07-07 Latex Worship Session 1080p"),
        (_cand("g2", "Unrelated.Thing"), SCENE, _ms(10, strong=()), None),
    ])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert s["kind"] == "search"
    assert s["slug"] == "empornium"
    assert s["raw_query"] == "That Fetish Girl 07.07.2026"
    assert s["parsed_site"] == "That Fetish Girl"
    assert s["parsed_dates"] == ["2026-07-07"]
    assert s["scenes"][0]["scene_id"] == 7
    assert s["variants"] == [
        {"query": "q1", "fired": True, "result_count": 2},
        {"query": "q2", "fired": False, "result_count": None},
        {"query": "q3", "fired": False, "result_count": None},
    ]
    # confidence desc; matched flag from threshold captured at construction
    assert [c["confidence"] for c in s["candidates"]] == [90, 10]
    assert s["candidates"][0]["matched"] is True
    assert s["candidates"][1]["matched"] is False
    assert s["candidates"][1]["rewritten_title"] is None
    assert s["outcome"]["status"] == "matched"
    assert s["outcome"]["matched_count"] == 1
    assert s["notes"] == ["early exit: threshold met"]
    assert s["finished_at"] >= s["started_at"]


def test_recorder_cap_keeps_matched_and_records_dropped():
    store = SessionStore(max_sessions=10, max_candidates=5)
    rec = store.recorder("empornium", 75, "q")
    items = [(_cand(f"g{i}", f"Unrelated.{i}"), SCENE, _ms(10 + i, strong=()), None)
             for i in range(10)]
    # one matched candidate with LOW sort position pressure: matched must survive
    items.append((_cand("gm"), SCENE, _ms(90), "rewritten"))
    rec.scored(items)
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert len(s["candidates"]) == 5
    assert s["dropped_candidates"] == 6
    assert any(c["guid"] == "gm" and c["matched"] for c in s["candidates"])
    # still sorted by confidence desc
    confs = [c["confidence"] for c in s["candidates"]]
    assert confs == sorted(confs, reverse=True)


def test_recorder_guid_sanitized_but_title_verbatim():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    poisoned = ReleaseCandidate(
        title="Some.Release", guid="http://prowlarr/dl/1?apikey=SECRET123&x=1",
        link="http://p/dl?apikey=SECRET123")
    rec.scored([(poisoned, SCENE, _ms(10, strong=()), None)])
    rec.commit()
    snap = store.snapshot()
    text = json.dumps(snap)
    assert "SECRET123" not in text
    assert snap["sessions"][0]["candidates"][0]["guid"] == \
        "http://prowlarr/dl/1?apikey=REDACTED&x=1"
    assert snap["sessions"][0]["candidates"][0]["title"] == "Some.Release"


def test_recorder_fallback_marks_passthrough():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "gibberish")
    rec.query(None, ())
    rec.fallback("unparseable-query")
    rec.passthrough_results(3)
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert s["kind"] == "passthrough"
    assert s["fallback_reason"] == "unparseable-query"
    assert s["outcome"]["status"] == "matched"      # verbatim results returned
    assert s["outcome"]["matched_count"] == 3


def test_recorder_rate_deferred_passthrough_is_empty():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "gibberish")
    rec.fallback("unparseable-query")
    rec.note("rate-deferred: returned empty feed without querying Prowlarr")
    rec.passthrough_results(0)
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["status"] == "empty"


def test_recorder_rss_summary():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.rss_summary(87, [(_cand("g1"), SCENE, _ms(90), "rewritten title")])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert s["kind"] == "rss"
    assert s["outcome"]["status"] == "rss-summary"
    assert s["outcome"]["items_total"] == 87
    assert s["outcome"]["rewritten"] == 1
    assert len(s["candidates"]) == 1


def test_recorder_error_status():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.error("prowlarr search failed: boom")
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["status"] == "error"
    assert "prowlarr search failed: boom" in s["notes"]


def test_recorder_commit_idempotent():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.commit()
    rec.commit()
    assert len(store.snapshot()["sessions"]) == 1


def test_recorder_methods_never_raise(caplog):
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.scored(None)          # not iterable — must be swallowed, not raised
    rec.query("bogus", None)  # wrong types — swallowed
    rec.commit()              # still commits what it has
    assert len(store.snapshot()["sessions"]) == 1
    assert "failed (ignored)" in caplog.text


def test_null_recorder_accepts_everything():
    NULL_RECORDER.query(None, ())
    NULL_RECORDER.fallback("x")
    NULL_RECORDER.variants_planned([])
    NULL_RECORDER.variant_fired("q", 0)
    NULL_RECORDER.note("n")
    NULL_RECORDER.scored([])
    NULL_RECORDER.passthrough_results(0)
    NULL_RECORDER.rss_summary(0, [])
    NULL_RECORDER.error("e")
    NULL_RECORDER.commit()
