from datetime import date
from pathlib import Path

import pytest
import yaml

from scenehound.matcher import score
from scenehound.models import SceneFingerprint
from scenehound.normalize import squash

CORPUS = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "corpus.yaml").read_text()
)
THRESHOLD = 75


def _fingerprint(raw: dict) -> SceneFingerprint:
    d = raw["date"]
    return SceneFingerprint(
        scene_id=0,
        site=raw["site"],
        site_aliases=tuple(raw.get("aliases", [])),
        date=d if isinstance(d, date) else date.fromisoformat(str(d)),
        title=raw["title"],
        performers=tuple(raw.get("performers", [])),
    )


@pytest.mark.parametrize(
    "entry", CORPUS, ids=[e["release"][:60] for e in CORPUS]
)
def test_corpus(entry):
    result = score(
        _fingerprint(entry["scene"]),
        entry["release"],
        other_sites=frozenset(squash(s) for s in entry.get("other_sites", [])),
    )
    if entry["expect"] == "match":
        assert result.confidence >= THRESHOLD, (
            f"expected match, got {result.confidence} "
            f"(strong={result.strong_signals}, veto={result.veto}, {result.detail})"
        )
    else:
        assert result.confidence < THRESHOLD, (
            f"expected no_match, got {result.confidence} "
            f"(strong={result.strong_signals}, {result.detail})"
        )
