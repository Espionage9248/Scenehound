import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from scenehound.clients.whisparr import WhisparrClient, scene_from_record

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


def test_scene_from_record_multiple_performers():
    rec = {**SAMPLE, "performerNames": ["Anna Example", "Bella Example"]}
    assert scene_from_record(rec).performers == ("Anna Example", "Bella Example")


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
