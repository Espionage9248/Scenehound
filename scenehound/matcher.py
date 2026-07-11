"""Scoring of candidate release titles against scene fingerprints.

Pure functions. The two-strong-signal rule and contradiction vetoes are the
core false-positive defenses — change them only with corpus evidence."""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from scenehound.dates import extract_dates
from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens, squash

STRONG_DATE = 40
STRONG_SITE = 35
STRONG_PERFORMER = 35
MULTI_PERFORMER_BONUS = 15
STRONG_TITLE = 40
TITLE_MAX = 25
SINGLE_SIGNAL_CAP = 65
_FUZZY_SITE_MIN = 90
_TITLE_RATIO_GATE = 60
_TITLE_STRONG_RATIO = 95


@dataclass(frozen=True)
class MatchScore:
    confidence: int
    strong_signals: tuple[str, ...]
    veto: str | None
    detail: dict[str, float]


def _site_in_title(squashed_title: str, scene: SceneFingerprint) -> bool:
    names = (scene.site, *scene.site_aliases)
    for name in names:
        sq = squash(name)
        if sq and sq in squashed_title:
            return True
    # fuzzy fallback for slight misspellings of the primary site name
    sq_site = squash(scene.site)
    if sq_site and fuzz.partial_ratio(sq_site, squashed_title) >= _FUZZY_SITE_MIN:
        return True
    return False


def score(
    scene: SceneFingerprint,
    title: str,
    other_sites: frozenset[str] = frozenset(),
) -> MatchScore:
    squashed = squash(title)
    detail: dict[str, float] = {}
    strong: list[str] = []

    # --- date ---
    title_dates = extract_dates(title)
    date_hit = any(abs((d - scene.date).days) <= 1 for d in title_dates)
    if date_hit:
        strong.append("date")
        detail["date"] = STRONG_DATE
    elif title_dates:
        return MatchScore(0, (), "date-mismatch", {"date": 0.0})

    # --- site ---
    own_site = _site_in_title(squashed, scene)
    if own_site:
        strong.append("site")
        detail["site"] = STRONG_SITE
    else:
        for other in other_sites:
            if other and other in squashed:
                return MatchScore(0, tuple(strong), "site-mismatch", detail)

    # --- performers ---
    hits = sum(1 for p in scene.performers if squash(p) and squash(p) in squashed)
    if hits:
        strong.append("performer")
        detail["performer"] = STRONG_PERFORMER + (MULTI_PERFORMER_BONUS if hits > 1 else 0)

    # --- title similarity ---
    scene_tokens = " ".join(content_tokens(scene.title))
    cand_tokens = " ".join(content_tokens(title))
    if scene_tokens and cand_tokens:
        ratio = fuzz.token_set_ratio(scene_tokens, cand_tokens)
        if ratio >= _TITLE_STRONG_RATIO:
            strong.append("title")
            detail["title"] = STRONG_TITLE
        elif ratio >= _TITLE_RATIO_GATE:
            detail["title"] = ratio / 100.0 * TITLE_MAX

    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
