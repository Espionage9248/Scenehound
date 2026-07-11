"""Text normalization shared by the matcher, rewriter, planner, and index."""
from __future__ import annotations

import re

# Tokens that carry no identity information: containers, codecs, sources,
# resolutions, and scene-release filler. Lowercase.
JUNK_TOKENS: frozenset[str] = frozenset({
    "xxx", "mp4", "wmv", "avi", "mkv", "mov", "ts",
    "480p", "540p", "720p", "1080p", "2160p", "480", "540", "720", "1080", "2160",
    "4k", "uhd", "hd", "sd", "fhd", "qhd",
    "web", "webdl", "webrip", "web-dl", "dl", "hdrip", "dvdrip", "dvd",
    "h264", "h265", "x264", "x265", "hevc", "avc", "av1",
    "aac", "ac3", "mp3", "flac",
    "repack", "internal", "remastered", "proper", "readnfo",
    "siterip", "split", "scenes", "psychoporn", "rq", "kleenex", "kt",
})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPLIT = re.compile(r"[^a-zA-Z0-9]+")

# A decorative trailing "xxx" studio marker, optionally preceded by separators
# (" XXX", ".xxx", "-XXX", or glued straight onto the last token). Anchored at the
# end so only a *suffix* is toggled.
_XXX_SUFFIX_RE = re.compile(r"[\s._-]*xxx$", re.IGNORECASE)
# A stem shorter than this (squashed) is too generic to alias safely: stripping it
# would let a coincidental short token fabricate a site signal (e.g. "Maxxx" -> "Ma").
_MIN_XXX_STEM = 3


def squash(s: str) -> str:
    """Lowercase and strip everything that is not a letter or digit."""
    return _NON_ALNUM.sub("", s.lower())


def xxx_site_variant(site: str) -> str | None:
    """Toggle a studio's decorative trailing 'xxx', returning the other spelling
    as an alias, or None when no sensible toggle exists.

    Studios routinely carry an 'xxx' suffix on one side of the Whisparr/tracker
    divide but not the other ('Family Therapy XXX' in Whisparr vs '[FamilyTherapy]
    ...' on Empornium, and the reverse). The returned string is deliberately usable
    BOTH as a raw tracker search term (query_planner.plan_queries feeds it verbatim)
    and, once squash()ed, as a matcher/index site key. Scoped to the decorative
    suffix only; generalizing to other decorative tokens would live here too but is
    intentionally out of scope (a broader toggle risks merging distinct studios)."""
    s = site.strip()
    if not s:
        return None
    if _XXX_SUFFIX_RE.search(s):
        stem = _XXX_SUFFIX_RE.sub("", s).strip()
        return stem if len(squash(stem)) >= _MIN_XXX_STEM else None
    # No suffix present: add one. squash() collapses the space, so " XXX" and a
    # glued "XXX" are the same key; the space keeps it a readable search term.
    return f"{s} XXX"


def tokenize(s: str) -> list[str]:
    """Lowercase tokens split on any non-alphanumeric run."""
    return [t for t in _SPLIT.split(s.lower()) if t]


def content_tokens(s: str) -> list[str]:
    """Tokens that plausibly identify content: junk and bare numbers removed."""
    return [t for t in tokenize(s) if t not in JUNK_TOKENS and not t.isdigit()]
