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
- Superset-title veto: `title` goes strong on CONTAINMENT — every scene token
  present in the candidate — which says nothing about what the candidate brought
  of its own. "Mommy Swallows" is a substring of "Mommy Swallows Before School",
  "Pregnant Mommy Swallows" and "Stripper Step-Mommy Swallows"; all three are
  different scenes of the same studio, all scored 100, and the strong `title`
  additionally EXEMPTED them from the foreign-title veto (2026-08-09 Xev
  Bellringer false grab: 9 of 10 returned releases were the wrong scene). Any
  identity token the candidate carries that no signal explains vetoes the match.
  Explained = junk, scene title, a squashed run of a site/alias/performer name,
  inside a parsed date span (already scored as the date signal — counting it as
  title vocabulary scores one fact twice), inside a [bracketed], {braced} or
  (parenthesised) segment, a residual-only technical token (_RESIDUAL_IGNORE:
  format words too narrow to widen JUNK_TOKENS for), or the single token
  trailing the last junk one (a -GROUP tag). That trailer arm is bounded to ONE
  token because junk is not confined to the tail:
  "com" is junk, so a "[BralessForever.com]" prefix is, and "4k"/"hd" routinely
  open release names. Forgiving everything downstream of the last junk token
  therefore exempted entire titles — "XevBellringer.com - Pregnant Mommy
  Swallows" scored 100 on an empty residual, which is the very grab this veto
  exists to kill. The bar is 1: on the corpus every genuine match leaves zero
  residual, so any residual at all is evidence of a different scene. The
  residual tokens are returned on MatchScore.residual as the evidence a
  veto-override would name. A bar that low makes the explainer set the whole
  design, and its first calibration was too narrow — it was drawn from one
  studio in one punctuation style and silently dropped true matches carrying a
  parenthesised or unlisted-format tail. Widening it is the recall-restoring
  direction and is done ONLY in ways that cannot un-veto a superset title (see
  _RESIDUAL_IGNORE). One shape is knowingly still lost: a studio whose Whisparr
  title is not the release's descriptive one (ShopLyfter's "Case No. NNNNNNN")
  vetoes on the descriptive tail. Segment-scoping the residual would fix it and
  is deferred as unvalidated — it risks loosening the veto back toward the
  grabs above. The loss is invisible in Whisparr (api.py's RSS path scores
  identically, so there is no second chance) and visible only in /ui.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from scenehound.dates import date_spans, extract_dates
from scenehound.models import SceneFingerprint
from scenehound.normalize import (
    JUNK_TOKENS, content_tokens, identity_tokens, name_ngrams, squash, tokenize,
)

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
_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
# Uploader/studio/technical tags, in all three punctuation styles trackers
# actually use. Parentheses were missing from the first cut and nine corpus rows
# use them — "(Oculus 8K, UHD)", "(2026.07.21)", "(720p)" — so a genuine release
# whose tail happened to be round-bracketed was vetoed where the identical
# square-bracketed one matched. The character classes are deliberately not
# paired: a release name is not a grammar, and requiring "[" to close with "]"
# only means a mismatched pair goes unexplained, which is the strict direction.
_TAGGED_SEGMENT_RE = re.compile(r"[\[\{\(][^\]\}\)]*[\]\}\)]")

# Format/technical vocabulary consumed ONLY by _unexplained_residual.
#
# This is deliberately NOT merged into JUNK_TOKENS, and that separation is the
# entire point. JUNK_TOKENS also feeds identity_tokens and content_tokens, so
# widening it moves near_exact, the title ratio, and both foreign-title arms —
# every verdict in the file. A residual-only set can only ever forgive a token
# the superset veto was about to fire on, so it cannot change any existing
# verdict; it can only restore a true match the veto's bar of 1 was dropping.
# Keep it that way: if a token belongs in identity scoring too, it belongs in
# JUNK_TOKENS and needs corpus evidence for the wider blast radius.
#
# Membership rule: a word that is never a scene's own vocabulary — HDR/codec/
# container/language/VR-projection markers. No bare integers. identity_tokens
# keeps bare numbers on purpose (the 2026-07-19 ShopLyfter false grab: digit-
# stripping collapsed every "Case No. N" to the same boilerplate), and
# "Mommy Swallows 2" is a motivating false positive whose whole residual is
# "2" — re-junking any bare number here would chip at that decision from the
# other side. Alphanumeric markers ("8k", "10bit") are not bare integers.
_RESIDUAL_IGNORE = frozenset({
    "hdr", "hdr10", "dv", "10bit", "bluray", "bdrip", "ddp", "eac3", "opus",
    "vp9", "amzn", "sub", "subs", "eng", "multi", "oculus",
    "5k", "6k", "8k", "lr", "sbs",
})


@dataclass(frozen=True)
class MatchScore:
    confidence: int
    strong_signals: tuple[str, ...]
    veto: str | None
    detail: dict[str, float]
    # The candidate's identity tokens that no signal explains. Populated only by
    # the superset-title veto; it is the evidence a future veto-override needs to
    # name what it is overriding ("vetoed on: pregnant"). Defaulted so existing
    # four-argument constructions stay valid.
    residual: tuple[str, ...] = ()


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


def _unexplained_residual(scene: SceneFingerprint, title: str) -> tuple[str, ...]:
    """The candidate's identity tokens that no signal accounts for.

    A token is explained when it is junk, part of the scene title, part of a
    site/alias/performer name (in any squashed run — "Jane.ONeil"), inside a
    span extract_dates matched (already scored as the date signal), inside a
    [bracketed], {braced} or (parenthesised) segment (uploader/studio/technical
    tags, never title words), a residual-only technical token (_RESIDUAL_IGNORE
    — the format words JUNK_TOKENS cannot safely absorb), or is the lone token
    trailing the last junk one (a release-group tag: "...XXX.1080p.MP4-GRP").

    Anything left is the candidate's OWN title vocabulary. That last arm is
    bounded to a single trailing token on purpose. Junk is not confined to the
    tail — JUNK_TOKENS carries "com" precisely because trackers brand the studio
    with its domain ("[BralessForever.com] ..."), and "4k"/"hd"/"1080p" open
    plenty of release names — so forgiving *everything* after the last junk
    token lets one "com" at index 1 exempt the candidate's entire title:
    "XevBellringer.com - Pregnant Mommy Swallows" scored 100 with an empty
    residual, the exact false grab this veto exists to kill. One trailing token
    is the -GROUP tag and nothing else. Forgiveness is not the safe direction
    here; it is the direction in which a false import gets through, and
    unpicking one of those is expensive.

    The counterweight, learned after the first cut shipped: at a bar of 1 a
    single unrecognised format word ("...XXX.2160p.HDR.MP4-KTR") is fatal to a
    release that is unambiguously the right scene, and the loss is silent —
    vetoed candidates never reach Whisparr and the RSS path scores identically,
    so there is no second chance and nothing to see from inside Whisparr. The
    two arms that answer that (parentheses, _RESIDUAL_IGNORE) are both chosen so
    they cannot forgive a word a studio could name a scene with."""
    explained = set(name_ngrams((scene.site, *scene.site_aliases, *scene.performers)))
    explained |= set(identity_tokens(scene.title))
    dates = date_spans(title)
    tags = [m.span() for m in _TAGGED_SEGMENT_RE.finditer(title)]
    toks = [(m.group().lower(), m.span()) for m in _TOKEN_RE.finditer(title)]
    last_junk = max((i for i, (t, _) in enumerate(toks) if t in JUNK_TOKENS), default=-1)
    trailer = last_junk >= 0 and (len(toks) - 1 - last_junk) <= 1
    out: list[str] = []
    for i, (tok, (a, b)) in enumerate(toks):
        if tok in JUNK_TOKENS or tok in _RESIDUAL_IGNORE or tok in explained:
            continue
        if any(s <= a and b <= e for s, e in dates):
            continue
        if any(s <= a and b <= e for s, e in tags):
            continue
        if trailer and i > last_junk:
            continue
        out.append(tok)
    return tuple(out)


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

    # --- superset-title veto ---
    # `title` became strong by CONTAINMENT: every scene token is present in the
    # candidate. That says nothing about what else the candidate brought. A
    # candidate carrying the whole scene title plus vocabulary of its own names a
    # longer-titled scene of the same studio ("Mommy Swallows" vs "Mommy Swallows
    # Before School": same site, same performer, both score 100). The foreign-title
    # veto below cannot catch this — it needs `title` absent from the strong set,
    # and its sibling arm measures words the candidate DROPPED, which here is none
    # (coverage 1.0). Demotion does not work either: 35 + 35 + 25 = 95, still over
    # threshold. Placed after the date veto so a contradicting date keeps
    # precedence and existing corpus rows keep their veto strings.
    if "title" in strong and (residual := _unexplained_residual(scene, title)):
        detail["superset_residual"] = float(len(residual))
        return MatchScore(0, tuple(strong), "superset-title", detail, residual)

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
