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
    NULL_RECORDER.passed_through([])
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


def test_record_grab_correlates_by_canonical_prefix_of_suffixed_title():
    # Part A appends " [<original tracker title>]" to what we hand Whisparr, so
    # that whole string is what gets stored as rewritten_title. Whether Whisparr's
    # On Grab webhook echoes it verbatim is UNVERIFIED — the parse contract was
    # checked against GET /api/v3/parse, and CI cannot reach a live Whisparr. If
    # it reports the canonical part alone, exact matching drops the grab into
    # unmatched and the UI ladder stalls at Matched with no error logged. The
    # prefix arm keeps that from being a silent failure.
    store = _store_with_matched_session(
        rewritten="That Fetish Girl 2026-07-07 Latex 1080p [TFG.26.07.07.Latex.1080p]"
    )
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH_PFX")
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH_PFX"
    assert s["outcome"]["grabs"][0]["grabbed_guid"] == "g1"
    assert store.snapshot()["unmatched_grabs"] == []


def test_record_grab_prefix_arm_does_not_match_a_different_canonical():
    # The hedge must not become a fuzzy matcher: only the exact canonical prefix
    # correlates, never a shorter left-anchored slice of it.
    store = _store_with_matched_session(
        rewritten="That Fetish Girl 2026-07-07 Latex 1080p [TFG.26.07.07.Latex.1080p]"
    )
    store.record_grab("That Fetish Girl 2026-07-07 Latex", "HASH_NOPE")
    assert store.snapshot()["sessions"][0]["outcome"]["grabs"] == []
    assert len(store.snapshot()["unmatched_grabs"]) == 1


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


def test_snapshot_carries_the_superset_residual(store=None):
    if store is None:
        store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "xevbellringer 06.01.2015")
    cand = ReleaseCandidate(title="Xev Bellringer - Mommy Swallows Before School",
                            guid="g1", link="http://p/dl/1")
    ms = MatchScore(0, ("site", "performer", "title"), "superset-title",
                    {"superset_residual": 2.0}, ("before", "school"))
    rec.scored([(cand, SCENE, ms, None)])
    rec.commit()

    c = store.snapshot()["sessions"][0]["candidates"][0]
    assert c["veto"] == "superset-title"
    assert c["residual"] == ["before", "school"]


# ---- persistence across restarts -------------------------------------------
# The store is still a process-local ring; save()/load() only let that ring
# survive a container restart. Both are shielded: a corrupt, truncated, or
# unwritable state file degrades to an empty UI, never to a failed startup.


def _restore(path, max_sessions=10) -> SessionStore:
    store = SessionStore(max_sessions=max_sessions, max_candidates=200)
    store.load(path)
    return store


def test_save_then_load_round_trips_the_snapshot(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate()],
                           status="matched", matched_count=1))
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    assert _restore(path).snapshot() == store.snapshot()


def test_load_restores_grab_and_import_stamps(tmp_path):
    store = _store_with_matched_session()
    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    store.record_import("HASH1", movie_id=7, file_count=2, dry_run=True)
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    g = _restore(path).snapshot()["sessions"][0]["outcome"]["grabs"][0]
    assert g["grab"]["download_id"] == "HASH1"
    assert g["grabbed_guid"] == "g1"
    assert g["imported"]["movie_id"] == 7
    assert g["imported"]["file_count"] == 2
    assert g["imported"]["dry_run"] is True


def test_load_restores_unmatched_grabs(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.record_grab("Something We Never Searched For", "HASH9", 500)
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    u = _restore(path).snapshot()["unmatched_grabs"][0]
    assert u["grab"]["release_title"] == "Something We Never Searched For"
    assert u["grab"]["download_id"] == "HASH9"


def test_next_id_resumes_above_the_restored_sessions(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    for _ in range(3):
        store.add(make_session(store))
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    # Reusing ids 1-3 would make two different sessions share one id in the UI.
    assert _restore(path).next_id() == 4


def test_load_of_a_missing_file_leaves_an_empty_store(tmp_path):
    store = _restore(tmp_path / "never-written.json")
    assert store.snapshot() == {"sessions": [], "unmatched_grabs": []}
    assert store.next_id() == 1


def test_load_of_a_corrupt_file_leaves_an_empty_store(tmp_path, caplog):
    path = tmp_path / "ui-sessions.json"
    path.write_text('{"sessions": [{"slug": "trunc')  # killed mid-write
    with caplog.at_level("ERROR"):
        store = _restore(path)
    assert store.snapshot()["sessions"] == []
    assert "observe" in caplog.text


def test_load_skips_a_structurally_broken_session_and_keeps_the_rest(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, slug="good-one"))
    path = tmp_path / "ui-sessions.json"
    store.save(path)
    data = json.loads(path.read_text())
    data["sessions"].insert(0, {"session_id": 99, "candidates": "not-a-list"})
    path.write_text(json.dumps(data))

    assert [s["slug"] for s in _restore(path).snapshot()["sessions"]] == ["good-one"]


def test_load_tolerates_unknown_and_missing_candidate_fields(tmp_path):
    # Schema drift both ways: `residual` postdates v0.2.0, and a file written by
    # a newer build may carry fields this one has never heard of.
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate()]))
    path = tmp_path / "ui-sessions.json"
    store.save(path)
    data = json.loads(path.read_text())
    cand = data["sessions"][0]["candidates"][0]
    del cand["residual"]
    cand["field_from_the_future"] = "???"
    path.write_text(json.dumps(data))

    c = _restore(path).snapshot()["sessions"][0]["candidates"][0]
    assert c["title"] == "TFG.26.07.07.X.1080p"
    assert c["residual"] == []
    assert "field_from_the_future" not in c


def test_load_keeps_only_the_newest_when_max_sessions_shrank(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    for _ in range(5):
        store.add(make_session(store))
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    restored = _restore(path, max_sessions=2)
    assert [s["session_id"] for s in restored.snapshot()["sessions"]] == [5, 4]


def test_save_skips_the_write_when_nothing_changed(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store))
    path = tmp_path / "ui-sessions.json"
    store.save(path)
    path.unlink()

    store.save(path)  # flush tick with no new sessions: must not rewrite
    assert not path.exists()

    store.add(make_session(store))
    store.save(path)
    assert path.exists()


def test_a_grab_marks_the_store_dirty_again(tmp_path):
    store = _store_with_matched_session()
    path = tmp_path / "ui-sessions.json"
    store.save(path)
    path.unlink()

    store.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    store.save(path)
    assert json.loads(path.read_text())["sessions"][0]["outcome"]["grabs"]


def test_save_leaves_no_temp_file_behind(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store))
    store.save(tmp_path / "ui-sessions.json")
    assert [p.name for p in tmp_path.iterdir()] == ["ui-sessions.json"]


def test_save_to_an_unwritable_path_never_raises(tmp_path, caplog):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store))
    with caplog.at_level("ERROR"):
        store.save(tmp_path / "no-such-dir" / "ui-sessions.json")
    assert "observe" in caplog.text


def test_a_grab_after_a_restart_still_correlates(tmp_path):
    # Restored sessions are real SearchSessions, not inert dicts, so a webhook
    # that lands after the restart still finds its session.
    store = _store_with_matched_session()
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    restored = _restore(path)
    restored.record_grab("That Fetish Girl 2026-07-07 Latex 1080p", "HASH1", 1000)
    snap = restored.snapshot()
    assert snap["sessions"][0]["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH1"
    assert snap["unmatched_grabs"] == []


# ---- tracker secrets in guids ---------------------------------------------
# Both live trackers' guids are download URLs carrying authkey= and
# torrent_pass=. They must never be stored or served.

TRACKER_GUID = ("https://www.happyfappy.net/torrents.php?action=download"
                "&id=149855&authkey=AUTHKEY123&torrent_pass=PASS456")
REDACTED_GUID = ("https://www.happyfappy.net/torrents.php?action=download"
                 "&id=149855&authkey=REDACTED&torrent_pass=REDACTED")


def test_recorder_redacts_tracker_authkey_and_torrent_pass():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("happyfappy", 75, "q")
    rec.scored([(_cand(TRACKER_GUID), SCENE, _ms(90), "rewritten")])
    rec.commit()
    snap = store.snapshot()
    assert snap["sessions"][0]["candidates"][0]["guid"] == REDACTED_GUID
    assert "AUTHKEY123" not in json.dumps(snap)
    assert "PASS456" not in json.dumps(snap)


def test_notes_and_errors_are_sanitized():
    # httpx puts the request URL, apikey included, in its error message; that
    # text reaches note() and error() verbatim and must not survive into the
    # stored session.
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    text = "url 'http://p:9696/12/api?t=search&apikey=SECRETPK&q=x'"
    rec.note(text)
    rec.error(text)
    rec.commit()
    snap = store.snapshot()
    assert "SECRETPK" not in json.dumps(snap)
    notes = snap["sessions"][0]["notes"]
    assert any("apikey=REDACTED" in n for n in notes)


def _write_unredacted_state(path):
    # A v0.6.1 file: add() stores what it's given, so building the session by
    # hand reproduces the unredacted guid and grabbed_guid an old build wrote.
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate(guid=TRACKER_GUID)],
                           status="matched", matched_count=1))
    store.record_grab("That Fetish Girl 2026-07-07 X 1080p", "HASH1", 1000)
    store.save(path)
    assert "AUTHKEY123" in path.read_text()


def test_load_scrubs_tracker_secrets_and_rewrites_the_file(tmp_path):
    path = tmp_path / "ui-sessions.json"
    _write_unredacted_state(path)

    restored = _restore(path)
    s = restored.snapshot()["sessions"][0]
    assert s["candidates"][0]["guid"] == REDACTED_GUID
    # Both sides of the correlation key were scrubbed the same way, so the
    # restored grab still badges its row.
    assert s["outcome"]["grabs"][0]["grabbed_guid"] == REDACTED_GUID

    restored.save(path)  # the next flush tick
    text = path.read_text()
    assert "AUTHKEY123" not in text and "PASS456" not in text


def test_load_scrubbed_state_still_correlates_a_new_grab(tmp_path):
    path = tmp_path / "ui-sessions.json"
    _write_unredacted_state(path)

    restored = _restore(path)
    restored.record_grab("That Fetish Girl 2026-07-07 X 1080p", "HASH2", 1000)
    grabs = restored.snapshot()["sessions"][0]["outcome"]["grabs"]
    assert [g["grabbed_guid"] for g in grabs] == [REDACTED_GUID, REDACTED_GUID]
    assert restored.snapshot()["unmatched_grabs"] == []


def test_load_scrubs_secrets_from_stored_notes(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "q")
    rec.note("placeholder")
    rec.commit()
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    # A pre-fix file: put the raw, unredacted note back. The store now
    # sanitizes notes on the way in, so this reproduces what an older build
    # (before error text was sanitized) left on disk.
    data = json.loads(path.read_text())
    data["sessions"][0]["notes"] = [
        "prowlarr search failed: url 'http://p:9696/12/api?apikey=SECRETPK&q=x'"
    ]
    path.write_text(json.dumps(data))

    restored = _restore(path)
    notes = restored.snapshot()["sessions"][0]["notes"]
    assert "SECRETPK" not in json.dumps(notes)
    assert any("apikey=REDACTED" in n for n in notes)

    restored.save(path)
    assert "SECRETPK" not in path.read_text()

    restored2 = _restore(path)
    path.unlink()
    restored2.save(path)
    assert not path.exists()


def test_load_of_a_clean_file_does_not_rewrite_it(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate()]))
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    restored = _restore(path)
    path.unlink()
    restored.save(path)
    assert not path.exists()


def test_load_of_an_already_scrubbed_file_does_not_rewrite_it(tmp_path):
    # In raw JSON a secret param's value runs on into the closing quote, so a
    # naive re-sanitize of the text would flag every redacted file as dirty.
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate(guid=REDACTED_GUID)]))
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    restored = _restore(path)
    path.unlink()
    restored.save(path)
    assert not path.exists()


# ---- passed-through rows ----------------------------------------------------
# Items Scenehound returned to Whisparr unchanged. Recorded so a Whisparr grab
# of one correlates to its session instead of the unmatched strip.

def test_recorder_records_passed_through_rows():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([
        (_cand("g1", "Raw.Near.Miss"), SCENE, _ms(70, strong=("site", "performer"))),
        (_cand(TRACKER_GUID, "Raw.Unrelated"), None, None),
    ])
    rec.rss_summary(2, [])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    near, unrelated = s["candidates"]          # confidence desc
    assert near["passed_through"] is True
    assert near["matched"] is False and near["rewritten_title"] is None
    assert near["scene_id"] == 7 and near["confidence"] == 70
    assert near["strong_signals"] == ["site", "performer"]
    assert near["scene"]["title"] == "Latex Worship Session"
    assert near["scene"]["date"] == "2026-07-07"
    assert unrelated["passed_through"] is True
    assert unrelated["scene_id"] is None and unrelated["scene"] is None
    assert unrelated["confidence"] == 0 and unrelated["strong_signals"] == []
    assert unrelated["veto"] is None and unrelated["detail"] == {}
    assert unrelated["guid"] == REDACTED_GUID
    assert s["outcome"]["passed_through"] == 2


def test_passed_through_row_above_threshold_is_never_matched():
    # A search passthrough result that WOULD clear the threshold is still
    # returned unchanged: matched means "Scenehound rewrote it".
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "familytherapyxxx 26.07.26")
    rec.fallback("unparseable-query")
    rec.passthrough_results(1)
    rec.passed_through([(_cand("g1"), SCENE, _ms(100))])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    c = s["candidates"][0]
    assert c["confidence"] == 100
    assert c["matched"] is False and c["passed_through"] is True
    assert s["outcome"]["matched_count"] == 1     # passthrough: results returned
    assert s["outcome"]["passed_through"] == 1


def test_rss_summary_counts_only_rewritten_rows():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("p1", "Raw.1"), None, None),
                        (_cand("p2", "Raw.2"), None, None)])
    rec.rss_summary(3, [(_cand("g1"), SCENE, _ms(90), "rewritten")])
    rec.commit()
    s = store.snapshot()["sessions"][0]
    assert s["outcome"]["rewritten"] == 1
    assert s["outcome"]["items_total"] == 3
    assert s["outcome"]["passed_through"] == 2
    assert len(s["candidates"]) == 3


def test_record_grab_correlates_a_passed_through_title():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("p1", "[Bellesa House] - Raw Tracker Title - 2023-06-22"),
                         None, None)])
    rec.rss_summary(1, [])
    rec.commit()

    store.record_grab("[Bellesa House] - Raw Tracker Title - 2023-06-22", "HASH1", 1000)
    snap = store.snapshot()
    assert snap["sessions"][0]["outcome"]["grabs"][0]["grabbed_guid"] == "p1"
    assert snap["unmatched_grabs"] == []


def test_duplicate_item_in_one_poll_still_correlates_to_the_session():
    # Review focus 1: the same release twice in one RSS response. The tie is
    # ambiguous (same title, same size), so no row badge -- but the grab must
    # still land on the session, never in the unmatched strip.
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("p1", "Raw.Dup"), None, None),
                        (_cand("p1", "Raw.Dup"), None, None)])
    rec.rss_summary(2, [])
    rec.commit()

    store.record_grab("Raw.Dup", "HASH1", 1000)
    snap = store.snapshot()
    assert len(snap["sessions"][0]["outcome"]["grabs"]) == 1
    assert snap["unmatched_grabs"] == []


def test_load_decodes_pre_feature_rows_with_defaults(tmp_path):
    # Review focus 3: a v0.6.1 file has none of the new keys.
    store = SessionStore(max_sessions=10, max_candidates=200)
    store.add(make_session(store, candidates=[make_candidate()]))
    path = tmp_path / "ui-sessions.json"
    store.save(path)
    data = json.loads(path.read_text())
    cand = data["sessions"][0]["candidates"][0]
    for key in ("scene_id", "passed_through", "scene"):
        del cand[key]
    del data["sessions"][0]["outcome"]["passed_through"]
    path.write_text(json.dumps(data))

    s = _restore(path).snapshot()["sessions"][0]
    c = s["candidates"][0]
    assert c["scene_id"] is None
    assert c["passed_through"] is False
    assert c["scene"] is None
    assert s["outcome"]["passed_through"] == 0


def test_passed_through_rows_round_trip(tmp_path):
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "")
    rec.passed_through([(_cand("g1", "Raw.Near"), SCENE, _ms(70, strong=("site",))),
                        (_cand("g2", "Raw.None"), None, None)])
    rec.rss_summary(2, [])
    rec.commit()
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    assert _restore(path).snapshot() == store.snapshot()


# ---- one row per passed-through RSS item -----------------------------------
# The same feed items come back on every poll (HappyFappy's 25 span ~87 h).
# Each passed-through item is listed once, under the latest poll that
# returned it, so the newest poll per slug always lists the whole feed.

def _rss_poll(store, guids, *, slug="empornium", rewritten=()):
    rec = store.recorder(slug, 75, "")
    rec.passed_through([(_cand(g, f"Raw.{g}"), None, None) for g in guids])
    rec.rss_summary(len(guids) + len(rewritten),
                    [(_cand(g, f"Rel.{g}"), SCENE, _ms(90), f"Rewritten {g}")
                     for g in rewritten])
    rec.commit()


def _rows(store):
    """{session_id: [guid, ...]} in stored order."""
    return {s["session_id"]: [c["guid"] for c in s["candidates"]]
            for s in store.snapshot()["sessions"]}


def test_rss_poll_moves_relisted_rows_to_the_newest_poll():
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b", "c"])
    _rss_poll(store, ["b", "c", "d"])
    assert _rows(store) == {2: ["b", "c", "d"], 1: ["a"]}
    older = store.snapshot()["sessions"][1]
    assert older["outcome"]["passed_through"] == 3   # capture-time count survives


def test_pruning_keeps_a_grabbed_row_through_later_polls():
    # Review focus 4.
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b"])
    store.record_grab("Raw.a", "HASH1", 1000)        # lands on poll 1
    _rss_poll(store, ["a", "b"])
    _rss_poll(store, ["a", "b"])
    assert _rows(store) == {3: ["a", "b"], 2: [], 1: ["a"]}
    first = store.snapshot()["sessions"][2]
    assert first["outcome"]["grabs"][0]["grabbed_guid"] == "a"

    store.record_grab("Raw.a", "HASH2", 1000)        # a second grab: newest poll
    newest = store.snapshot()["sessions"][0]
    assert newest["outcome"]["grabs"][0]["grab"]["download_id"] == "HASH2"


def test_pruning_never_touches_rewritten_rows():
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a"], rewritten=["r"])
    _rss_poll(store, ["a", "r"])       # r's scene left the wanted list
    assert _rows(store)[1] == ["r"]


def test_pruning_is_per_slug_and_rss_only():
    store = SessionStore(max_sessions=10, max_candidates=200)
    rec = store.recorder("empornium", 75, "familytherapyxxx 26.07.26")
    rec.fallback("unparseable-query")
    rec.passthrough_results(1)
    rec.passed_through([(_cand("a", "Raw.a"), None, None)])
    rec.commit()                                   # 1: search passthrough
    _rss_poll(store, ["a"], slug="happyfappy")     # 2: other slug
    _rss_poll(store, ["a"], slug="empornium")      # 3: prunes nothing above
    assert _rows(store) == {3: ["a"], 2: ["a"], 1: ["a"]}


def test_an_empty_guid_never_prunes():
    # Review focus 5.
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, [""])
    _rss_poll(store, [""])
    assert _rows(store) == {2: [""], 1: [""]}


def test_pruning_carries_the_outcome_by_reference():
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b"])
    store.record_grab("Raw.a", "HASH1", 1000)
    _rss_poll(store, ["b"])                        # trims poll 1 (drops b)
    store.record_import("HASH1", movie_id=7, file_count=1, dry_run=False)
    first = store.snapshot()["sessions"][1]
    assert first["outcome"]["grabs"][0]["imported"]["movie_id"] == 7


def test_a_pruning_failure_still_keeps_the_new_session(monkeypatch, caplog):
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a"])

    def boom(new):
        raise RuntimeError("prune exploded")

    monkeypatch.setattr(store, "_prune_relisted", boom)
    _rss_poll(store, ["a"])
    assert _rows(store) == {2: ["a"], 1: ["a"]}    # duplicates, nothing lost
    assert "failed (ignored)" in caplog.text


def test_restored_sessions_are_pruned_by_the_next_poll(tmp_path):
    # Review focus 2: a restart between polls.
    store = SessionStore(max_sessions=10, max_candidates=200)
    _rss_poll(store, ["a", "b"])
    path = tmp_path / "ui-sessions.json"
    store.save(path)

    restored = _restore(path)
    _rss_poll(restored, ["b"])
    assert _rows(restored) == {2: ["b"], 1: ["a"]}
