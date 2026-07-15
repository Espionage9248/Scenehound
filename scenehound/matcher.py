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
  title whose tokens are all present in the candidate AND only alongside a site
  or performer strong signal — a generic one-word title cannot become a second
  strong signal by mere containment, and a distinctive title never pairs with
  date alone to clear the threshold (a near-exact title with no site/performer
  still contributes up to TITLE_MAX medium points but is not a strong signal).
- A parsed date that contradicts the scene's date is a hard veto, EXCEPT when
  the skew is within date_skew_days (default 3) and the match clears the
  two-strong-signal rule without the date — uploaders sometimes stamp
  rip/upload dates a few days off the studio release date. A forgiven date
  contributes no points; the skew is recorded in detail["date_skew_days"]
  for the UI trace.
- Only a PRIMARY-reading date — the dominant convention of its format
  (yy.mm.dd for two-digit triples, yyyy.mm.dd, dd.mm.yyyy) — can be a strong
  signal. A date matched only via an alternate reading of an ambiguous
  ordering forgives the date veto but is never strong and contributes no
  points (detail["date_secondary_reading"] traces it): a [26-07-14] release
  must not strongly match a 2014-07-26 scene by cherry-picking the dd.mm.yy
  reading (2026-07-15 production false grab).
- Foreign-title veto: when site+date is the ENTIRE strong set, the scene
  title is distinctive, and the candidate carries >= 3 content tokens beyond
  the scene's site/title/performers at near-zero title similarity, the
  candidate names a different scene and is vetoed. Absence is not
  contradiction — a bare Site.YY.MM.DD release (no residual) still matches;
  2 residual tokens are routinely filler ("Bonus Scene") and forgiven.
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
_FOREIGN_TITLE_RATIO = 35     # below this, the candidate's own words read as a different scene
_MIN_FOREIGN_RESIDUAL = 3     # candidate content tokens beyond scene site/title/performers;
#                               2 is routinely filler ("Bonus Scene"), 3+ is a foreign title
_MIN_PERFORMER_TOKEN_LEN = 3  # single-token performer names shorter than this are ignored
_MIN_TITLE_STRONG_TOKENS = 2  # title needs >= this many content tokens to be a strong signal
_MAX_SITE_TOKENS = 6          # longest contiguous token run considered a site n-gram;
#                               wanted_index._MAX_NAME_TOKENS must stay >= this or the
#                               RSS pre-filter stops being a lossless superset.


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
    date_skew_days: int = 3,
) -> MatchScore:
    ngrams = _title_ngrams(title)
    detail: dict[str, float] = {}
    strong: list[str] = []

    # --- date ---
    extracted = extract_dates(title)
    date_off: int | None = None  # smallest days-off when no reading is within ±1
    date_secondary = False
    if extracted.all:
        if any(abs((d - scene.date).days) <= 1 for d in extracted.primary):
            strong.append("date")
            detail["date"] = STRONG_DATE
        elif any(abs((d - scene.date).days) <= 1 for d in extracted.secondary):
            # Matched only via an alternate reading of an ambiguous ordering
            # (e.g. dd.mm.yy of a yy.mm.dd stamp): forgives the veto, never
            # strong, contributes no points. Flag recorded after summation.
            date_secondary = True
        else:
            date_off = min(abs((d - scene.date).days) for d in extracted.all)

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
    title_ratio: float | None = None
    if scene_ctoks and cand_ctoks:
        cand_ctok_set = set(cand_ctoks)
        near_exact = len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS and all(
            t in cand_ctok_set for t in scene_ctoks
        )
        # Title is strong ONLY alongside a site or performer strong signal (the
        # site/performer blocks run before this one, so `strong` is populated).
        # This encodes the design's valid strong-pairs (date+performer, site+date,
        # site+performer, site+title): title must never pair with date alone.
        if near_exact and ("site" in strong or "performer" in strong):
            strong.append("title")
            detail["title"] = STRONG_TITLE
        else:
            title_ratio = fuzz.token_set_ratio(" ".join(scene_ctoks), " ".join(cand_ctoks))
            if title_ratio >= _TITLE_RATIO_GATE:
                detail["title"] = title_ratio / 100.0 * TITLE_MAX

    # --- date veto, decided after the other signals ---
    # A mismatched date is forgiven only when the skew is small (uploaders
    # stamp rip/upload dates a few days off the studio release date) AND the
    # match clears the two-strong-signal rule without the date. Otherwise it
    # stays a hard contradiction: on daily-release sites the date is often
    # the only thing separating sibling scenes.
    if date_off is not None and (date_off > date_skew_days or len(strong) < 2):
        return MatchScore(0, (), "date-mismatch", {"date": 0.0})

    # --- foreign-title veto ---
    # {site, date} is the only strong pair with no content confirmation: on a
    # studio's own feed the site name discriminates nothing, and dates collide
    # across ambiguous stamps and same-day siblings. When the candidate carries
    # enough of its own words — beyond the scene's site, title, and performers —
    # with near-zero title similarity, it names a DIFFERENT scene. Absence is
    # not contradiction: a bare Site.YY.MM.DD release has no residual and
    # still matches on site+date.
    if (
        set(strong) == {"date", "site"}
        and len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS
        and (title_ratio is None or title_ratio < _FOREIGN_TITLE_RATIO)
    ):
        known = set(scene_ctoks)
        for name in (scene.site, *scene.site_aliases, *scene.performers):
            known.update(tokenize(name))
            known.add(squash(name))  # glued forms: "[FamilyTherapy]" is not foreign
        if sum(1 for t in set(cand_ctoks) if t not in known) >= _MIN_FOREIGN_RESIDUAL:
            return MatchScore(0, tuple(strong), "foreign-title", detail)

    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    if date_off is not None:
        detail["date_skew_days"] = float(date_off)  # trace metadata, not points
    if date_secondary:
        detail["date_secondary_reading"] = 1.0  # trace metadata, not points
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
