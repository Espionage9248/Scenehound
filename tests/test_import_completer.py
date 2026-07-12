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


_FIXTURES = Path(__file__).parent / "fixtures"


def test_real_queue_fixture_maps_and_is_by_id_hold():
    # Captured from live Whisparr eros (sanitized). Locks the parser to reality.
    data = json.loads((_FIXTURES / "whisparr_queue_sample.json").read_text())
    (record,) = data["records"]
    qi = queue_item_from_record(record)
    assert qi is not None
    assert qi.movie_id == record["movie"]["id"]   # movieId == embedded movie.id (import target)
    assert qi.tracked_state == "importBlocked"
    assert is_by_id_hold(qi) is True


def test_real_manualimport_fixture_maps():
    # The clean single-file by-ID case: pre-populated movie == grabbed movieId,
    # zero rejections -> phase 1 would import.
    data = json.loads((_FIXTURES / "whisparr_manualimport_sample.json").read_text())
    records = data if isinstance(data, list) else data.get("records", [])
    items = [manual_import_from_record(r) for r in records]
    assert items, "captured manualimport sample had no candidates"
    c = items[0]
    assert c.movie_id == 13503 and c.rejections == ()
    assert c.item_id == 67318430 and c.monitored is True and c.movie_file_id == 0
    assert c.quality["quality"]["name"] == "WEBDL-2160p"


def test_real_fixtures_end_to_end_plan_phase1_imports():
    # The captured queue + manualimport together must yield an ActionPlan.
    q = json.loads((_FIXTURES / "whisparr_queue_sample.json").read_text())
    m = json.loads((_FIXTURES / "whisparr_manualimport_sample.json").read_text())
    item = queue_item_from_record(q["records"][0])
    cands = [manual_import_from_record(r) for r in m]
    plan = plan_phase1(item, cands, ImportCompleterConfig())
    assert isinstance(plan, ActionPlan)
    (f,) = plan.files
    assert f["movieId"] == 13503 and f["id"] == 67318430 and f["downloadId"] == item.download_id


# --- service (sweep / grace / retry / dry-run) ---

import httpx

from scenehound.api import IndexHolder
from scenehound.clients.whisparr import WhisparrClient
from scenehound.import_completer import ImportCompleter
from scenehound.wanted_index import WantedIndex

QUEUE_ONE = {"page": 1, "pageSize": 1000, "totalRecords": 1, "records": [BY_ID]}
MANUAL_CLEAN = [{
    "path": "/dl/AdultTime.mp4", "folderName": "AdultTime", "movie": {"id": 7},
    "quality": {"quality": {"id": 19}}, "languages": [{"id": 1}],
    "releaseGroup": "GRP", "rejections": [], "size": 6_000_000_000,
}]


class Spy:
    """Records every request; serves queue + manualimport; captures POSTs."""
    def __init__(self, queue=QUEUE_ONE, manual=MANUAL_CLEAN):
        self.queue, self.manual = queue, manual
        self.calls: list[tuple[str, str]] = []
        self.posted: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url)))
        url = str(request.url)
        if "/api/v3/queue" in url:
            return httpx.Response(200, json=self.queue)
        if "/api/v3/manualimport" in url:
            return httpx.Response(200, json=self.manual)
        if url.endswith("/api/v3/command"):
            self.posted.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)


def _completer(spy, **cfg):
    hc = httpx.AsyncClient(transport=httpx.MockTransport(spy.handler))
    client = WhisparrClient("http://w:6969", "k", hc)
    holder = IndexHolder()
    holder.set(WantedIndex([]))
    return ImportCompleter(client, holder, ImportCompleterConfig(enabled=True, **cfg))


async def test_dry_run_fires_no_post_and_only_gets():
    spy = Spy()
    ic = _completer(spy, dry_run=True, grace_seconds=0)
    summary = await ic.sweep(now=1000.0)
    assert summary.acted == 1
    assert spy.posted == []
    assert all(method == "GET" for method, _ in spy.calls)  # hard dry-run property


async def test_live_run_posts_manual_import():
    spy = Spy()
    ic = _completer(spy, dry_run=False, grace_seconds=0)
    summary = await ic.sweep(now=1000.0)
    assert summary.acted == 1
    assert len(spy.posted) == 1
    assert spy.posted[0]["name"] == "ManualImport"
    assert spy.posted[0]["files"][0]["movieId"] == 7


async def test_grace_defers_then_acts():
    spy = Spy()
    ic = _completer(spy, dry_run=False, grace_seconds=120)
    first = await ic.sweep(now=1000.0)   # first-seen stamped, within grace
    assert first.waited == 1 and spy.posted == []
    second = await ic.sweep(now=1000.0 + 121)  # grace elapsed
    assert second.acted == 1 and len(spy.posted) == 1


async def test_retry_then_park_after_max_attempts():
    # Item never clears (Spy keeps returning it). Fires up to max_attempts then parks.
    spy = Spy()
    ic = _completer(spy, dry_run=False, grace_seconds=0, max_attempts=2)
    await ic.sweep(now=1.0)   # attempt 1
    await ic.sweep(now=2.0)   # attempt 2
    parked = await ic.sweep(now=3.0)  # capped -> park, no further POST
    assert len(spy.posted) == 2
    assert parked.parked == 1


async def test_skip_does_not_count_as_attempt():
    spy = Spy(manual=[{**MANUAL_CLEAN[0], "rejections": ["Unknown quality"]}])
    ic = _completer(spy, dry_run=False, grace_seconds=0, max_attempts=2)
    s = await ic.sweep(now=1.0)
    assert s.skipped == 1 and spy.posted == []


async def test_non_by_id_items_ignored():
    downloading = {**BY_ID, "trackedDownloadState": "downloading"}
    spy = Spy(queue={"page": 1, "pageSize": 1000, "totalRecords": 1, "records": [downloading]})
    ic = _completer(spy, dry_run=False, grace_seconds=0)
    s = await ic.sweep(now=1.0)
    assert (s.acted, s.skipped, s.waited) == (0, 0, 0)
    assert not any("manualimport" in url for _, url in spy.calls)  # no manualimport fetch


async def test_multipack_disabled_skips_multifile():
    two = [MANUAL_CLEAN[0], {**MANUAL_CLEAN[0], "path": "/dl/b.mp4"}]
    spy = Spy(manual=two)
    ic = _completer(spy, dry_run=False, grace_seconds=0, multipack=False)
    s = await ic.sweep(now=1.0)
    assert s.skipped == 1 and spy.posted == []


async def test_notify_sets_wake_event():
    spy = Spy()
    ic = _completer(spy, grace_seconds=0)
    assert not ic._wake.is_set()
    ic.notify()
    assert ic._wake.is_set()


# --- phase 2: pure pack-matching decision functions ---

from datetime import date

from scenehound.models import SceneFingerprint
from scenehound.import_completer import FileMatch, PackMatch, finalize_pack, match_pack


def _index(*scenes):
    return WantedIndex(list(scenes))


SCENE_A = SceneFingerprint(101, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                           "Latex Worship Session", ("Jane Doe",))
SCENE_B = SceneFingerprint(102, "That Fetish Girl", ("TFG",), date(2026, 7, 8),
                           "Rubber Gloves", ("Mary Major",))


def _packcand(path, movie=None):
    return manual_import_from_record({
        "path": path, "folderName": "TFG.Pack", "movie": movie,
        "quality": {"quality": {"id": 3}}, "languages": [{"id": 1}],
        "releaseGroup": "GRP", "rejections": [], "size": 5_000_000_000,
    })


def test_match_pack_all_matched_via_scoring():
    cands = [
        _packcand("TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4"),
        _packcand("TFG.26.07.08.Rubber.Gloves.Mary.Major.1080p.mp4"),
    ]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A, SCENE_B),
                      ImportCompleterConfig(import_threshold=75))
    assert pack.fully_matched
    assert pack.matched_movie_ids == frozenset({101, 102})


def test_match_pack_unmatched_file_blocks_pack():
    cands = [
        _packcand("TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4"),
        _packcand("Totally.Unknown.Release.2019.720p.mp4"),
    ]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A, SCENE_B),
                      ImportCompleterConfig(import_threshold=75))
    assert not pack.fully_matched
    assert any(f.verdict == "unmatched" for f in pack.files)


def test_match_pack_ambiguous_when_margin_too_thin():
    # Two same-day scenes, filename carries only site+date -> both score equal,
    # margin below ambiguity_margin -> ambiguous, pack blocked.
    twin = SceneFingerprint(103, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                            "Different Title", ("Someone Else",))
    cands = [_packcand("TFG.26.07.07.1080p.mp4")]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A, twin),
                      ImportCompleterConfig(import_threshold=60, ambiguity_margin=10))
    assert not pack.fully_matched
    assert pack.files[0].verdict == "ambiguous"


def test_match_pack_prepopulated_movie_counts_only_if_grabbed_id():
    good = _packcand("weird.name.mp4", movie={"id": 7})   # == BY_ID movieId
    pack = match_pack(queue_item_from_record(BY_ID), [good], _index(),
                      ImportCompleterConfig())
    assert pack.fully_matched and pack.matched_movie_ids == frozenset({7})

    bad = _packcand("weird.name.mp4", movie={"id": 999})  # foreign movie -> not trusted
    pack2 = match_pack(queue_item_from_record(BY_ID), [bad], _index(),
                       ImportCompleterConfig())
    assert not pack2.fully_matched


def test_finalize_pack_requires_monitored_and_no_file():
    cands = [_packcand("TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4")]
    item = queue_item_from_record(BY_ID)
    pack = match_pack(item, cands, _index(SCENE_A), ImportCompleterConfig(import_threshold=75))
    ok = finalize_pack(item, pack, {101: (True, False)}, ImportCompleterConfig())
    assert isinstance(ok, ActionPlan) and ok.files[0]["movieId"] == 101

    has_file = finalize_pack(item, pack, {101: (True, True)}, ImportCompleterConfig())
    assert isinstance(has_file, Skip) and "hasfile" in has_file.reason.lower()

    unmonitored = finalize_pack(item, pack, {101: (False, False)}, ImportCompleterConfig())
    assert isinstance(unmonitored, Skip) and "monitor" in unmonitored.reason.lower()


def test_finalize_pack_skips_when_not_fully_matched():
    item = queue_item_from_record(BY_ID)
    pack = PackMatch(files=(FileMatch("/x.mp4", _packcand("/x.mp4"), None, "unmatched"),))
    out = finalize_pack(item, pack, {}, ImportCompleterConfig())
    assert isinstance(out, Skip)


# --- phase 2: service integration (sweep -> match_pack -> /movie -> one command) ---

_PACK_TWO = [
    {"path": "TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4", "movie": None,
     "quality": {"quality": {"id": 3}}, "languages": [{"id": 1}], "rejections": []},
    {"path": "TFG.26.07.08.Rubber.Gloves.Mary.Major.1080p.mp4", "movie": None,
     "quality": {"quality": {"id": 3}}, "languages": [{"id": 1}], "rejections": []},
]


def _phase2_completer(movie_file_id):
    # Scorer-matched pack (movie: None) => _plan_phase2 fetches /movie/{id}. This
    # eros fork returns movieFileId (NOT hasFile); 0 == no file, >0 == already has one.
    posted: list[dict] = []

    def handler(request):
        url = str(request.url)
        if "/api/v3/queue" in url:
            return httpx.Response(200, json=QUEUE_ONE)
        if "/api/v3/manualimport" in url:
            return httpx.Response(200, json=_PACK_TWO)
        if "/api/v3/movie/" in url:
            mid = int(url.rsplit("/", 1)[1].split("?")[0])
            return httpx.Response(200, json={"id": mid, "monitored": True,
                                             "movieFileId": movie_file_id})
        if url.endswith("/api/v3/command"):
            posted.append(json.loads(request.content))
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    holder = IndexHolder()
    holder.set(WantedIndex([SCENE_A, SCENE_B]))
    ic = ImportCompleter(
        WhisparrClient("http://w:6969", "k", hc), holder,
        ImportCompleterConfig(enabled=True, dry_run=False, multipack=True,
                              grace_seconds=0, import_threshold=75),
    )
    return ic, posted


async def test_phase2_all_matched_posts_one_batched_command():
    ic, posted = _phase2_completer(movie_file_id=0)
    s = await ic.sweep(now=1.0)
    assert s.acted == 1
    assert len(posted) == 1  # ONE batched command for the whole pack
    assert {f["movieId"] for f in posted[0]["files"]} == {101, 102}


async def test_phase2_skips_pack_when_a_movie_already_has_file():
    # A matched movie with movieFileId>0 already has a file -> hasfile guard skips
    # the WHOLE pack, no ManualImport command is posted.
    ic, posted = _phase2_completer(movie_file_id=5)
    s = await ic.sweep(now=1.0)
    assert s.acted == 0 and s.skipped == 1
    assert posted == []
