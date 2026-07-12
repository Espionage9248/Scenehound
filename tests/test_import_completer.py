import json
from pathlib import Path

from scenehound.config import ImportCompleterConfig
from scenehound.import_completer import (
    ActionPlan, ManualImportItem, QueueItem, Skip,
    is_by_id_hold, manual_import_from_record, plan_phase1, queue_item_from_record,
)

BY_ID = {
    "downloadId": "HASH1", "movieId": 7, "title": "AdultTime...",
    "trackedDownloadState": "importBlocked",
    "statusMessages": [{"title": "AdultTime.mp4", "messages": [
        "Found matching movie via grab history, but release was matched to movie by ID. "
        "Manual Import required."]}],
}


def _cand(**over):
    base = {
        "path": "/dl/AdultTime.mp4", "folderName": "AdultTime",
        "movie": {"id": 7}, "quality": {"quality": {"id": 19, "name": "Bluray-2160p"}},
        "languages": [{"id": 1, "name": "English"}], "releaseGroup": "GRP",
        "rejections": [], "size": 6_000_000_000,
    }
    base.update(over)
    return manual_import_from_record(base)


def test_queue_item_from_record_maps_fields():
    qi = queue_item_from_record(BY_ID)
    assert qi.download_id == "HASH1" and qi.movie_id == 7
    assert qi.tracked_state == "importBlocked"
    assert any("matched to movie by id" in m.lower() for m in qi.status_messages)


def test_queue_item_from_record_rejects_incomplete():
    assert queue_item_from_record({"movieId": 7}) is None          # no downloadId
    assert queue_item_from_record({"downloadId": "H"}) is None      # no movieId


def test_is_by_id_hold_true_for_marker_and_state():
    assert is_by_id_hold(queue_item_from_record(BY_ID)) is True


def test_is_by_id_hold_false_for_other_state():
    other = queue_item_from_record({**BY_ID, "trackedDownloadState": "downloading"})
    assert is_by_id_hold(other) is False


def test_is_by_id_hold_false_without_marker():
    quiet = queue_item_from_record({**BY_ID, "statusMessages": [
        {"title": "x", "messages": ["Not an upgrade for existing movie file"]}]})
    assert is_by_id_hold(quiet) is False


def test_manual_import_from_record_maps_movie_and_rejections():
    c = _cand()
    assert c.movie_id == 7 and c.rejections == () and c.release_group == "GRP"
    assert c.quality == {"quality": {"id": 19, "name": "Bluray-2160p"}}
    absent = _cand(movie=None)
    assert absent.movie_id is None


def test_plan_phase1_imports_clean_single_file():
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand()], ImportCompleterConfig())
    assert isinstance(plan, ActionPlan)
    (f,) = plan.files
    assert f["movieId"] == 7 and f["path"] == "/dl/AdultTime.mp4"
    assert f["downloadId"] == "HASH1" and f["quality"]["quality"]["id"] == 19
    assert f["languages"] == [{"id": 1, "name": "English"}] and f["releaseGroup"] == "GRP"


def test_plan_phase1_skips_on_movie_id_mismatch():
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand(movie={"id": 999})], ImportCompleterConfig())
    assert isinstance(plan, Skip) and "movie" in plan.reason


def test_plan_phase1_skips_on_rejections():
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand(rejections=["Unknown quality"])], ImportCompleterConfig())
    assert isinstance(plan, Skip) and "reject" in plan.reason.lower()


def test_plan_phase1_skips_when_no_movie_pre_populated():
    # A file Whisparr could not scene-match itself has no movie; phase 1 will not
    # invent one — only confirms Whisparr's own by-ID decision (movie.id present == movieId).
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand(movie=None)], ImportCompleterConfig())
    assert isinstance(plan, Skip)


def test_plan_phase1_skips_multiple_video_files():
    qi = queue_item_from_record(BY_ID)
    two = [_cand(path="/dl/a.mp4"), _cand(path="/dl/b.mp4")]
    plan = plan_phase1(qi, two, ImportCompleterConfig())
    assert isinstance(plan, Skip) and "single" in plan.reason.lower()


def test_plan_phase1_ignores_sample_files():
    qi = queue_item_from_record(BY_ID)
    cands = [_cand(path="/dl/main.mp4"),
             _cand(path="/dl/sample.mp4", rejections=["Sample"], size=10_000_000)]
    plan = plan_phase1(qi, cands, ImportCompleterConfig())
    # Sample is excluded from the single-file count AND its rejection is not counted.
    assert isinstance(plan, ActionPlan)
    (f,) = plan.files
    assert f["path"] == "/dl/main.mp4"


def test_real_manualimport_fixture_maps():
    # Depends on Task 1 capture. Skips cleanly until the fixture exists.
    fx = Path(__file__).parent / "fixtures" / "whisparr_manualimport_sample.json"
    if not fx.exists():
        import pytest
        pytest.skip("run scripts/probe_whisparr.sh to capture the fixture")
    data = json.loads(fx.read_text())
    records = data if isinstance(data, list) else data.get("records", [])
    items = [manual_import_from_record(r) for r in records]
    assert items, "captured manualimport sample had no candidates"
