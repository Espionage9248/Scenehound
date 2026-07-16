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
    assert s["outcome"]["grabs"] == []
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


def _cand(guid="g1", title="TFG.26.07.07.Latex.Worship.Session.1080p", size=1000):
    return ReleaseCandidate(title=title, guid=guid, link="http://p/dl?apikey=SECRET",
                            size=size, seeders=5)


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


def _store_with_matched_session(guid="g1", rewritten="That Fetish Girl 2026-07-07 Latex 1080p"):
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "That Fetish Girl 07.07.2026")
    rec.scored([(_cand(guid), SCENE, _ms(90), rewritten)])
    rec.commit()
    return store


def test_record_grab_correlates_by_rewritten_title():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    s = store.snapshot()["sessions"][0]
    assert len(s["outcome"]["grabs"]) == 1
    assert s["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH1"
    assert store.snapshot()["unmatched_grabs"] == []


def test_record_grab_correlates_by_original_title():
    store = _store_with_matched_session()
    store.record_grab("TFG.26.07.07.Latex.Worship.Session.1080p", "HASH2")
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH2"


def test_record_grab_picks_newest_matching_session():
    store = SessionStore(max_sessions=10, max_candidates=200)
    for _ in range(2):
        rec = store.recorder("empornium", 75, "q")
        rec.scored([(_cand("g1"), SCENE, _ms(90), "SAME rewritten")])
        rec.commit()
    store.record_grab("SAME rewritten", "HASH3")
    snap = store.snapshot()["sessions"]
    assert len(snap[0]["outcome"]["grabs"]) == 1   # newest
    assert snap[1]["outcome"]["grabs"] == []        # older untouched


def test_record_grab_unmatched_is_kept_and_bounded():
    store = SessionStore(max_sessions=10, max_candidates=200)
    for i in range(25):
        store.record_grab(f"Never.Seen.{i}", f"H{i}")
    grabs = store.snapshot()["unmatched_grabs"]
    assert len(grabs) == 20                            # bounded
    assert grabs[0]["grab"]["release_title"] == "Never.Seen.24"  # newest first


def test_record_import_stamps_grabbed_session():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    imp = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]["imported"]
    assert imp["movie_id"] == 7 and imp["dry_run"] is False


def test_record_import_dry_run_flagged():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=True)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabs"][0]["imported"]["dry_run"] is True


def test_record_import_stamps_unmatched_grab():
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.record_grab("Never.Seen.Release", "HASHX")
    store.record_import("HASHX", movie_id=9, file_count=2, dry_run=False)
    u = store.snapshot()["unmatched_grabs"][0]
    assert u["imported"]["movie_id"] == 9


def test_record_import_without_any_grab_surfaces():
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.record_import("GHOST", movie_id=3, file_count=1, dry_run=False)
    u = store.snapshot()["unmatched_grabs"][0]
    assert u["grab"]["download_id"] == "GHOST"
    assert u["imported"]["movie_id"] == 3


def _store_with_twin_titles(size_a=1000, size_b=2000):
    # Two candidates whose rewritten titles are IDENTICAL — the real-world
    # ambiguity this feature must survive (2026-07-14 trace).
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "That Fetish Girl 07.07.2026")
    rec.scored([
        (_cand("gA", "Release.A", size=size_a), SCENE, _ms(90), "SAME rewritten"),
        (_cand("gB", "Release.B", size=size_b), SCENE, _ms(90), "SAME rewritten"),
    ])
    rec.commit()
    return store


def test_record_grab_stamps_grabbed_guid_on_unique_title_match():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grabbed_guid"] == "g1"
    assert g["grab"]["size"] == 1000


def test_record_grab_without_size_still_stamps_unique_match():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1")
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grabbed_guid"] == "g1"


def test_record_grab_size_breaks_title_tie():
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1", 2000)
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grabbed_guid"] == "gB"
    assert g["grab"]["download_id"] == "HASH1"


def test_record_grab_tie_without_size_leaves_guid_none():
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1")
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grab"] is not None                  # record still created
    assert g["grabbed_guid"] is None              # UI degrades to session level


def test_record_grab_unhelpful_size_leaves_guid_none():
    # size matches neither twin
    store = _store_with_twin_titles()
    store.record_grab("SAME rewritten", "HASH1", 3000)
    assert store.snapshot()["sessions"][0]["outcome"]["grabs"][0]["grabbed_guid"] is None
    # size matches both twins
    store2 = _store_with_twin_titles(size_a=1000, size_b=1000)
    store2.record_grab("SAME rewritten", "HASH2", 1000)
    g2 = store2.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g2["grab"] is not None
    assert g2["grabbed_guid"] is None


# --- multi-grab: a second grab APPENDS a second record (the whole feature) ---


def _store_with_two_rewrites():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.scored([
        (_cand("g1", "Release.One", size=1000), SCENE, _ms(90), "Rewritten One"),
        (_cand("g2", "Release.Two", size=2000), SCENE, _ms(85), "Rewritten Two"),
    ])
    rec.commit()
    return store


def test_second_grab_appends_second_record():
    store = _store_with_two_rewrites()
    store.record_grab("Rewritten One", "HASH1", 1000)
    store.record_grab("Rewritten Two", "HASH2", 2000)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 2
    assert grabs[0]["grab"]["download_id"] == "HASH1"
    assert grabs[0]["grabbed_guid"] == "g1"
    assert grabs[1]["grab"]["download_id"] == "HASH2"
    assert grabs[1]["grabbed_guid"] == "g2"
    assert store.snapshot()["unmatched_grabs"] == []


def test_ambiguous_second_grab_appends_record_with_none_guid():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.scored([
        (_cand("g1", "Release.One", size=1000), SCENE, _ms(90), "Rewritten One"),
        (_cand("gA", "Release.A", size=500), SCENE, _ms(85), "SAME rewritten"),
        (_cand("gB", "Release.B", size=500), SCENE, _ms(85), "SAME rewritten"),
    ])
    rec.commit()
    store.record_grab("Rewritten One", "HASH1", 1000)
    store.record_grab("SAME rewritten", "HASH2")  # ambiguous: no size, twin titles
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 2
    assert grabs[0]["grabbed_guid"] == "g1"       # first record untouched
    assert grabs[1]["grab"]["download_id"] == "HASH2"
    assert grabs[1]["grabbed_guid"] is None       # its own ambiguity, its own None


def test_two_grabs_import_independently():
    store = _store_with_two_rewrites()
    store.record_grab("Rewritten One", "HASH1", 1000)
    store.record_grab("Rewritten Two", "HASH2", 2000)
    store.record_import("HASH2", movie_id=8, file_count=2, dry_run=False)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert grabs[0]["imported"] is None            # first grab: not yet imported
    assert grabs[1]["imported"]["movie_id"] == 8   # second grab: imported
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=True)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert grabs[0]["imported"]["dry_run"] is True
    assert grabs[1]["imported"]["dry_run"] is False
    assert store.snapshot()["unmatched_grabs"] == []


def test_regrab_same_download_id_updates_in_place():
    # Webhook resend / re-grab: same download_id must NOT duplicate the record,
    # and must keep the import stamp it already earned.
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 1
    assert grabs[0]["grabbed_guid"] == "g1"
    assert grabs[0]["imported"]["movie_id"] == 7   # import stamp survives


def test_empty_download_id_grabs_always_append():
    # A grab without a download_id can't be deduped; two of them = two records.
    store = _store_with_two_rewrites()
    store.record_grab("Rewritten One", "")
    store.record_grab("Rewritten Two", "")
    grabs = store.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert len(grabs) == 2


def test_import_with_empty_download_id_never_matches_a_grab():
    # An id-less import must not bind to an id-less grab record; it surfaces
    # as unmatched instead of stamping the wrong record.
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "")
    store.record_import("", movie_id=5, file_count=1, dry_run=False)
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabs"][0]["imported"] is None
    assert store.snapshot()["unmatched_grabs"][0]["imported"]["movie_id"] == 5


def test_record_import_leaves_grabbed_guid_unchanged():
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    g = store.snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["imported"]["movie_id"] == 7
    assert g["grabbed_guid"] == "g1"
