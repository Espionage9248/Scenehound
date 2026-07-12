import json

from scenehound.observe import (
    CandidateTrace, Outcome, SearchSession, SessionStore, VariantTrace,
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
