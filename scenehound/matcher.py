"""Scoring of candidate release titles against scene fingerprints.

Pure functions. The two-strong-signal rule and contradiction vetoes are the
core false-positive defenses — change them only with corpus evidence.

Presence detection is boundary-aware to prevent spurious strong signals:
- Site names match only on exact boundary-aligned n-grams (squashed
  contiguous-token n-grams) or known aliases, so a short site like 'Vixen'
  does not match inside 'Vixens'. There is no fuzzy/edit-distance fallback:
  matching a name against title words by edit distance fabricates strong site
  signals from coincidental near-spellings (e.g. 'PublicAgent' vs 'Public
  Agents'), which the two-strong-signal rule cannot absorb.
- Performer names match by squashed full name against the boundary-aligned
  n-grams (not raw substrings), so 'Ai' does not match inside 'maintenance'
  while punctuated names that render differently in the release (O'Neil ->
  ONeil, Mary-Jane -> MaryJane) still match; ultra-short single-token names
  are ignored.
- Title counts as a STRONG signal only for a distinctive (>= 2 content token)
  title whose tokens are all present in the candidate — a generic one-word
  title cannot become a second strong signal by mere containment.
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from scenehound.dates import extract_dates
from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens, squash, tokenize

STRONG_DATE = 40
STRONG_SITE = 35
STRONG_PERFORMER = 35
MULTI_PERFORMER_BONUS = 15
STRONG_TITLE = 40
TITLE_MAX = 25
SINGLE_SIGNAL_CAP = 65
_TITLE_RATIO_GATE = 60
_MIN_PERFORMER_TOKEN_LEN = 3  # single-token performer names shorter than this are ignored
_MIN_TITLE_STRONG_TOKENS = 2  # title needs >= this many content tokens to be a strong signal
_MAX_SITE_TOKENS = 6          # longest contiguous token run considered a site n-gram


@dataclass(frozen=True)
class MatchScore:
    confidence: int
    strong_signals: tuple[str, ...]
    veto: str | None
    detail: dict[str, float]


def _title_ngrams(title: str) -> frozenset[str]:
    """Squashed concatenations of every contiguous run of up to _MAX_SITE_TOKENS
    tokens. A squashed site name matches the title only if it equals one of
    these boundary-aligned n-grams (never a substring inside a longer token)."""
    toks = tokenize(title)
    grams: set[str] = set()
    for i in range(len(toks)):
        acc = ""
        for j in range(i, min(i + _MAX_SITE_TOKENS, len(toks))):
            acc += toks[j]
            grams.add(acc)
    return frozenset(grams)


def _site_in_title(ngrams: frozenset[str], scene: SceneFingerprint) -> bool:
    """Present only when the squashed site name or an alias equals a
    boundary-aligned n-gram. No fuzzy/edit-distance fallback: matching a name
    against arbitrary title words by edit distance fabricates strong site
    signals from coincidental near-spellings (e.g. 'PublicAgent' vs the phrase
    'Public Agents', 'MyFamilyPies' vs 'My Family Pie'), which the two-strong-
    signal rule cannot absorb. Correctly-spelled sites match exactly; aliases
    cover known variants."""
    for name in (scene.site, *scene.site_aliases):
        sq = squash(name)
        if sq and sq in ngrams:
            return True
    return False


def _performer_present(performer: str, ngrams: frozenset[str]) -> bool:
    """Present when the squashed full name equals a boundary-aligned n-gram, so
    punctuation that renders differently in the release (O'Neil -> ONeil,
    Mary-Jane -> MaryJane) still matches, while short single-token names and
    substrings-inside-words do not. NOTE: accented names whose accent the
    release replaces with a base letter (Renee <- Renée) are not matched here
    because squash drops non-ASCII rather than transliterating; that is a known
    normalize-layer limitation tracked separately, not addressed in this fix."""
    p_toks = tokenize(performer)
    if not p_toks:
        return False
    if len(p_toks) == 1 and len(p_toks[0]) < _MIN_PERFORMER_TOKEN_LEN:
        return False
    return squash(performer) in ngrams


def score(
    scene: SceneFingerprint,
    title: str,
    other_sites: frozenset[str] = frozenset(),
) -> MatchScore:
    ngrams = _title_ngrams(title)
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
    if _site_in_title(ngrams, scene):
        strong.append("site")
        detail["site"] = STRONG_SITE
    else:
        for other in other_sites:
            if other and other in ngrams:
                return MatchScore(0, tuple(strong), "site-mismatch", detail)

    # --- performers ---
    hits = sum(1 for p in scene.performers if _performer_present(p, ngrams))
    if hits:
        strong.append("performer")
        detail["performer"] = STRONG_PERFORMER + (MULTI_PERFORMER_BONUS if hits > 1 else 0)

    # --- title similarity ---
    scene_ctoks = content_tokens(scene.title)
    cand_ctoks = content_tokens(title)
    if scene_ctoks and cand_ctoks:
        cand_ctok_set = set(cand_ctoks)
        near_exact = len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS and all(
            t in cand_ctok_set for t in scene_ctoks
        )
        if near_exact:
            strong.append("title")
            detail["title"] = STRONG_TITLE
        else:
            ratio = fuzz.token_set_ratio(" ".join(scene_ctoks), " ".join(cand_ctoks))
            if ratio >= _TITLE_RATIO_GATE:
                detail["title"] = ratio / 100.0 * TITLE_MAX

    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
