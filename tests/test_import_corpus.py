from datetime import date
from pathlib import Path

import pytest
import yaml

from scenehound.config import ImportCompleterConfig
from scenehound.import_completer import match_pack, manual_import_from_record, queue_item_from_record
from scenehound.models import SceneFingerprint
from scenehound.wanted_index import WantedIndex

CORPUS = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "import_pack_corpus.yaml").read_text()
)
ITEM = queue_item_from_record({
    "downloadId": "H", "movieId": -1,  # -1 so no pre-population accidentally matches
    "trackedDownloadState": "importBlocked",
    "statusMessages": [{"messages": ["matched to movie by ID"]}],
})


def _scene(raw):
    d = raw["date"]
    return SceneFingerprint(
        scene_id=raw["scene_id"], site=raw["site"],
        site_aliases=tuple(raw.get("aliases", [])),
        date=d if isinstance(d, date) else date.fromisoformat(str(d)),
        title=raw["title"], performers=tuple(raw.get("performers", [])),
    )


@pytest.mark.parametrize("entry", CORPUS, ids=[e["filename"][:50] for e in CORPUS])
def test_pack_corpus(entry):
    index = WantedIndex([_scene(s) for s in entry["scenes"]])
    cand = manual_import_from_record({"path": entry["filename"], "movie": None, "rejections": []})
    pack = match_pack(ITEM, [cand], index, ImportCompleterConfig())
    (fm,) = pack.files
    assert fm.verdict == entry["expect"], (
        f"{entry['filename']}: got {fm.verdict} movie_id={fm.movie_id}")
    if entry["expect"] == "matched":
        assert fm.movie_id == entry["expect_scene_id"]
