import json
from datetime import date
from pathlib import Path

import httpx

from scenehound.clients.whisparr import WhisparrClient, scene_from_record
from scenehound.matcher import score


async def test_fetch_queue_pages_and_returns_raw_records():
    pages = {
        1: {"page": 1, "pageSize": 2, "totalRecords": 3,
            "records": [{"downloadId": "A"}, {"downloadId": "B"}]},
        2: {"page": 2, "pageSize": 2, "totalRecords": 3,
            "records": [{"downloadId": "C"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "/api/v3/queue" in str(request.url)
        return httpx.Response(200, json=pages[int(dict(request.url.params)["page"])])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        recs = await WhisparrClient("http://w:6969", "k", hc).fetch_queue()
    assert [r["downloadId"] for r in recs] == ["A", "B", "C"]


async def test_fetch_manual_import_sends_download_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/v3/manualimport" in str(request.url)
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[{"path": "/x.mp4"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        out = await WhisparrClient("http://w:6969", "k", hc).fetch_manual_import("HASH1")
    assert seen["downloadId"] == "HASH1"
    assert seen["filterExistingFiles"] == "true"
    assert out == [{"path": "/x.mp4"}]


async def test_fetch_movie_returns_object():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/v3/movie/42")
        return httpx.Response(200, json={"id": 42, "monitored": True, "hasFile": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        movie = await WhisparrClient("http://w:6969", "k", hc).fetch_movie(42)
    assert movie["monitored"] is True and movie["hasFile"] is False


async def test_post_manual_import_builds_command_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url).endswith("/api/v3/command")
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"id": 1})

    files = [{"path": "/dl/a.mp4", "movieId": 7}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        await WhisparrClient("http://w:6969", "k", hc).post_manual_import(files)
    assert captured["name"] == "ManualImport"
    assert captured["importMode"] == "copy"
    assert captured["files"] == files

SAMPLE = {
    "id": 12225,
    "title": "Zuzu Sweet | Fatal Temptaion",
    "releaseDate": "2025-03-17T00:00:00Z",
    "studioTitle": "Fitting-Room",
    "performerNames": ["Zuzu Sweet"],
}


def test_scene_from_record_maps_fields():
    s = scene_from_record(SAMPLE)
    assert s.scene_id == 12225
    assert s.site == "Fitting-Room"
    assert s.date == date(2025, 3, 17)          # ISO datetime truncated to the date
    assert s.title == "Zuzu Sweet | Fatal Temptaion"
    assert s.performers == ("Zuzu Sweet",)


def test_scene_from_record_rejects_incomplete():
    assert scene_from_record({"id": 1, "title": "x"}) is None            # no date/site
    assert scene_from_record({**SAMPLE, "releaseDate": None}) is None
    assert scene_from_record({**SAMPLE, "studioTitle": ""}) is None      # no site (and no studio fallback)


def test_scene_from_record_generates_xxx_site_alias_both_directions():
    # Whisparr has "…XXX" but the tracker doesn't -> the stripped form is aliased.
    suffixed = scene_from_record({**SAMPLE, "studioTitle": "Family Therapy XXX"})
    assert suffixed.site == "Family Therapy XXX"
    assert "Family Therapy" in suffixed.site_aliases
    # Whisparr has the bare name but the tracker carries "…XXX" -> the "xxx" form is aliased.
    bare = scene_from_record({**SAMPLE, "studioTitle": "Family Therapy"})
    assert bare.site == "Family Therapy"
    assert "Family Therapy XXX" in bare.site_aliases


def test_scene_from_record_no_alias_when_no_sensible_toggle():
    # A degenerate site ("XXX") yields no alias rather than an empty/bogus one.
    s = scene_from_record({**SAMPLE, "studioTitle": "XXX"})
    assert s.site_aliases == ()


def test_generated_alias_lets_matcher_hit_the_stripped_tracker_spelling():
    # End-to-end: the auto-generated alias is what makes an "…XXX" Whisparr studio
    # score a match against a release that titles the studio WITHOUT the suffix.
    scene = scene_from_record({
        **SAMPLE,
        "studioTitle": "Family Therapy XXX",
        "title": "The Massage Lesson",
        "performerNames": ["Jane Doe"],
    })
    release = "[FamilyTherapy] The Massage Lesson 1080p"   # stripped spelling, no date, no performer
    assert score(scene, release).confidence >= 75


def test_scene_from_record_multiple_performers():
    rec = {**SAMPLE, "performerNames": ["Anna Example", "Bella Example"]}
    assert scene_from_record(rec).performers == ("Anna Example", "Bella Example")


def test_scene_from_record_bare_string_performer_names_ignored():
    # A bare string (not a list) must not be iterated into single-char
    # "performers"; site/date/title still map normally.
    rec = {**SAMPLE, "performerNames": "JaneDoe"}
    s = scene_from_record(rec)
    assert s.performers == ()
    assert s.site == "Fitting-Room"
    assert s.date == date(2025, 3, 17)
    assert s.title == "Zuzu Sweet | Fatal Temptaion"


def test_real_fixture_records_map():
    fixture = Path(__file__).parent / "fixtures" / "whisparr_wanted_sample.json"
    records = json.loads(fixture.read_text())["records"]
    mapped = [scene_from_record(r) for r in records]
    assert all(mapped), "a record in the captured live sample failed to map"
    assert mapped[0].site == "Fitting-Room"
    assert mapped[0].performers == ("Zuzu Sweet",)


async def test_fetch_wanted_pages_until_done():
    pages = {
        1: {"page": 1, "pageSize": 2, "totalRecords": 3,
            "records": [SAMPLE, {**SAMPLE, "id": 12226}]},
        2: {"page": 2, "pageSize": 2, "totalRecords": 3,
            "records": [{**SAMPLE, "id": 12227}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "k"
        page = int(dict(request.url.params)["page"])
        return httpx.Response(200, json=pages[page])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        client = WhisparrClient("http://w:6969", "k", hc)
        scenes = await client.fetch_wanted()
    assert [s.scene_id for s in scenes] == [12225, 12226, 12227]
