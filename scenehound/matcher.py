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
  Near-exactness is judged on identity tokens (bare numbers KEPT): for titles
  like "Case No. 2658794" the number is the only distinguishing part, and
  digit-stripping would let the boilerplate {case, no} strong-match every
  release of the studio (2026-07-19 ShopLyfter false grab).
- A parsed date that contradicts the scene's date is a hard veto, EXCEPT when
  the skew is within date_skew_days (default 3) and the match clears the
  two-strong-signal rule without the date — uploaders sometimes stamp
  rip/upload dates a few days off the studio release date. A forgiven date
  contributes no points; the skew is recorded in detail["date_skew_days"]
  for the UI trace.
- Only a PRIMARY-reading date — the dominant convention of its format
  (yy.mm.dd for two-digit triples, yyyy.mm.dd, dd.mm.yyyy) — can be a strong
  signal. A date matched only via an alternate reading of an ambiguous
  ordering is never strong and contributes no points
  (detail["date_secondary_reading"] traces it): a [26-07-14] release
  must not strongly match a 2014-07-26 scene by cherry-picking the dd.mm.yy
  reading (2026-07-15 production false grab). Such a reading forgives the
  date veto only under the same bar as skew forgiveness — the match must
  clear the two-strong-signal rule without the date. A single-signal
  candidate rescued only by a misreading of a contradicting stamp is vetoed
  (2026-07-19 ShopLyfter false grab: primary 2026-07-18, eight years off,
  laundered through its dd.mm.yy misreading landing 1 day from the scene).
- Foreign-title veto: when the strong set is title-less (any pair or triple of
  site/date/performer, with no title match), the scene title is distinctive,
  and the candidate carries >= 3 DISTINCT content tokens beyond the scene's
  site/title/performers at title similarity below _FOREIGN_TITLE_RATIO (40),
  the candidate names a different scene and is vetoed. That similarity is
  measured on DE-NAMED tokens (site/alias/performer names stripped from both
  sides): a name shared with the candidate is already its own strong signal, and
  counting it again as title corroboration scores one fact twice. Scene titles
  routinely end in the performer ("...Three Cocks for River Lynn"), and that
  shared name alone lifted the ratio over the gate, disarming this veto for a
  release from an unrelated studio (2026-08-06 FuckPassVR false grab). The ratio
  also only counts at all when the de-named titles share at least one word:
  rapidfuzz rates unrelated word sets in the high 30s-50s on character overlap
  alone, so the gate is barely above its own noise floor and needs a real shared
  word underneath it. A title-less set confirms
  only colliding attributes — a performer confirms a person, not the scene — so
  a {date, performer} grab of the right person on the right day with a foreign
  title is a false positive (2026-07-15 GloryholeSecrets grab). A strong set
  containing "title" is exempt (the title confirms the scene). Absence is not
  contradiction — a bare Site.YY.MM.DD release (no residual) still matches;
  2 residual tokens are routinely filler ("Bonus Scene") and forgiven.
- Sibling-scene arm of the same veto: the residual count above measures how much
  of its OWN a candidate brings, which says nothing when the candidate's title is
  short. A release sharing one generic word with the scene title while dropping
  half or more of its distinctive ones, and substituting a word of its own, is
  the studio's NEXT scene rather than this one ("Beach Days" vs "Shady Beach",
  same studio, same couple, no date: 2026-08-06 BralessForever grab, 2 residual
  tokens so the absence arm could never fire). Requires actual overlap — at zero
  overlap the filler forgiveness above governs — and yields when every missing
  word is merely SPELLED differently by the candidate. That last test is
  word-to-word (_SIBLING_TOKEN_RATIO), never title-to-title: "shady beach" vs
  "beach days" rates 76.2 on shared letters alone, whereas the real question
  ("shady" vs the substituted "days" = 44.4) separates cleanly from a tracker
  typo ("shady" vs "shadey" = 90.9).
"""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from scenehound.dates import extract_dates
from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens, identity_tokens, squash, tokenize

STRONG_DATE = 40
STRONG_SITE = 35
STRONG_PERFORMER = 35
MULTI_PERFORMER_BONUS = 15
STRONG_TITLE = 40
TITLE_MAX = 25
SINGLE_SIGNAL_CAP = 65
_TITLE_RATIO_GATE = 60
_FOREIGN_TITLE_RATIO = 40     # below this, the candidate's own words read as a different scene.
#                               Raised from 35 (2026-07-15 GloryholeSecrets grab: a generic scene
#                               title "case no … the right way" fuzz-aligns to 36.4 against foreign
#                               words — not real corroboration).
_MIN_FOREIGN_RESIDUAL = 3     # candidate content tokens beyond scene site/title/performers;
#                               2 is routinely filler ("Bonus Scene"), 3+ is a foreign title
_MAX_SIBLING_COVERAGE = 0.5   # fraction of the scene title's distinctive words the candidate
#                               carries. At or below half, with a substituted word of its own,
#                               it names the studio's NEXT scene, not this one (2026-08-06
#                               BralessForever grab: "Beach Days" vs "Shady Beach", 50%).
_SIBLING_TOKEN_RATIO = 75     # …unless every missing word is merely SPELLED differently by the
#                               candidate. Judged word-to-word, never title-to-title: "shady" vs
#                               the substituted "days" rates 44.4, a real typo ("shadey") 90.9.
#                               The whole-title ratio cannot answer this — "shady beach" vs
#                               "beach days" rates 76.2 on shared letters alone.
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
        # Near-exactness includes bare numbers (identity_tokens): a case or
        # episode number is often the only part separating sibling scenes, so
        # "Case No. 2658794" must not strong-match "Case No. 8004900" on the
        # boilerplate words alone. Distinctiveness stays on content tokens —
        # digits alone must not promote a generic title to strong-eligible.
        cand_itok_set = set(identity_tokens(title))
        near_exact = len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS and all(
            t in cand_itok_set for t in identity_tokens(scene.title)
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
    # the only thing separating sibling scenes. A secondary-reading rescue is
    # held to the same two-strong bar: the primary reading contradicts, and a
    # lone signal must not launder that through a misreading that happens to
    # land near the scene date (2026-07-19 ShopLyfter grab).
    if (date_off is not None and (date_off > date_skew_days or len(strong) < 2)) or (
        date_secondary and len(strong) < 2
    ):
        veto_detail = {"date": 0.0}
        if date_secondary:
            veto_detail["date_secondary_reading"] = 1.0
        return MatchScore(0, (), "date-mismatch", veto_detail)

    # --- foreign-title veto ---
    # A title-less strong set (any pair or triple of site/date/performer, no
    # title match) confirms only attributes that can independently collide: the
    # site discriminates nothing on a studio's own feed, dates collide across
    # ambiguous stamps and same-day siblings, and a performer confirms a person,
    # not the specific scene (Sydney Paige is in many releases). When the
    # candidate carries enough of its own words — beyond the scene's site, title,
    # and performers — at near-zero title similarity, it names a DIFFERENT scene.
    # Only a title match (which would put "title" in strong) confirms the scene,
    # so a strong set containing "title" is exempt. Absence is not contradiction:
    # a bare Performer.YYYY.MM.DD or Site.YY.MM.DD release has no residual and
    # still matches.
    if (
        len(strong) >= 2
        and "title" not in strong
        and len(scene_ctoks) >= _MIN_TITLE_STRONG_TOKENS
    ):
        name_toks: set[str] = set()
        for name in (scene.site, *scene.site_aliases, *scene.performers):
            name_toks.update(tokenize(name))
            name_toks.add(squash(name))  # glued forms: "[FamilyTherapy]" is not foreign
        # Corroboration is measured on the DISTINCTIVE tokens of both titles:
        # words that merely repeat the scene's own site or performer names are
        # already counted as their own strong signal, so letting them also raise
        # title similarity scores one fact twice. Scene titles routinely end in
        # the performer ("...Three Cocks for River Lynn"), and that shared name
        # alone carried the ratio over the gate, disarming this veto for a
        # release from an entirely different studio (2026-08-06 FuckPassVR false
        # grab: {date, performer} honest, ratio 48.2, 5 foreign residual tokens).
        scene_rest = [t for t in scene_ctoks if t not in name_toks]
        cand_rest = [t for t in cand_ctoks if t not in name_toks]
        # The ratio may only defuse the veto when the two titles share at least
        # one distinctive word. rapidfuzz rates unrelated word sets in the high
        # 30s-50s on character overlap alone ("Late Night Bedroom Secrets" vs
        # "Tight White Leather Corset" = 50.0, not one word in common), so
        # _FOREIGN_TITLE_RATIO sits barely above that noise floor and a slightly
        # longer foreign title clears it with nothing real in common. One shared
        # word is the cheapest evidence the ratio measures paraphrase rather
        # than coincidence; without it there is nothing to be forgiving about.
        shared = set(scene_rest) & set(cand_rest)
        residual = set(cand_rest) - set(scene_ctoks)  # the candidate's OWN words
        distinct_ratio = (
            fuzz.token_set_ratio(" ".join(scene_rest), " ".join(cand_rest))
            if shared
            else 0.0  # nothing corroborates; the residual count decides
        )
        coverage = len(shared) / len(set(scene_rest)) if scene_rest else 0.0
        # A word missing from the candidate is forgiven when the candidate simply
        # SPELLS it differently — asked word-to-word, because the whole-title
        # ratio cannot answer it: "shady beach" vs "beach days" rates 76.2 purely
        # on shared letters, while the real question ("shady" vs "days" = 44.4)
        # separates cleanly from a tracker typo ("shady" vs "shadey" = 90.9).
        reworded = all(
            any(fuzz.ratio(miss, own) >= _SIBLING_TOKEN_RATIO for own in residual)
            for miss in set(scene_rest) - shared
        )
        # Two shapes of "different scene", separated by whether the candidate
        # engages with the scene's title at all:
        #
        # (a) ABSENCE-shaped — shares nothing with the scene title and brings
        #     enough words of its own to be naming something else. Filler is
        #     forgiven here ("Bonus Scene" is 2 tokens at zero overlap), so the
        #     bar is _MIN_FOREIGN_RESIDUAL.
        # (b) SIBLING-shaped — shares a word but drops half or more of the
        #     scene's distinctive ones AND substitutes its own. That is the
        #     studio's NEXT scene, not this one: "Beach Days" vs "Shady Beach",
        #     same studio, same couple, no date (2026-08-06 BralessForever false
        #     grab — only 2 residual tokens, so arm (a) could never fire). The
        #     shared word is generic; the differing one is the whole identity.
        #     The `shared` guard is load-bearing: without it a zero-overlap
        #     coverage of 0.0 satisfies the bar and arm (b) swallows the filler
        #     forgiveness arm (a) exists to grant. `reworded` is the escape
        #     hatch: a tracker typo ("Shadey Beach") also drops coverage to 50%,
        #     but every missing word is spelled right there in the candidate.
        if (
            (distinct_ratio < _FOREIGN_TITLE_RATIO
             and len(residual) >= _MIN_FOREIGN_RESIDUAL)
            or (shared
                and residual
                and coverage <= _MAX_SIBLING_COVERAGE
                and not reworded)
        ):
            detail["foreign_title_ratio"] = distinct_ratio  # trace, not points
            detail["title_coverage"] = coverage             # trace, not points
            return MatchScore(0, tuple(strong), "foreign-title", detail)

    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    if date_off is not None:
        detail["date_skew_days"] = float(date_off)  # trace metadata, not points
    if date_secondary:
        detail["date_secondary_reading"] = 1.0  # trace metadata, not points
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
