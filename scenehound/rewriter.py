"""Canonical title emission. Quality tokens are extracted from the original
title only — Scenehound never fabricates quality."""
from __future__ import annotations

import re

from scenehound.models import SceneFingerprint

# (pattern, canonical token). Order = emission order: resolution, source, codec.
# Resolution tokens are only recognized when they carry an explicit marker (a `p`
# suffix like 1080p, or brackets like [1080]). Bare unbracketed numbers (1080, 720)
# are deliberately NOT matched: they are ambiguous with tip amounts, like counts,
# durations, and date fragments, so recognizing them would fabricate quality from
# unrelated digits. No marker → no token (Whisparr parses that as Unknown quality).
_RESOLUTION = [
    (re.compile(r"\b(2160p|4k|uhd)\b", re.I), "2160p"),
    (re.compile(r"\b1080p\b|\[1080\]", re.I), "1080p"),
    (re.compile(r"\b720p\b|\[720\]", re.I), "720p"),
    (re.compile(r"\b(480p|540p)\b", re.I), "480p"),
]
_SOURCE = [
    (re.compile(r"\bweb-?dl\b", re.I), "WEB-DL"),
    (re.compile(r"\bwebrip\b", re.I), "WEBRip"),
]
_CODEC = [
    (re.compile(r"\b(x265|h\.?265|hevc)\b", re.I), "x265"),
    (re.compile(r"\b(x264|h\.?264|avc)\b", re.I), "x264"),
]

_SANITIZE = re.compile(r"[^A-Za-z0-9]+")


def extract_quality_tokens(title: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for group in (_RESOLUTION, _SOURCE, _CODEC):
        for pattern, canonical in group:
            if pattern.search(title):
                tokens.append(canonical)
                break  # one token per group
    return tuple(tokens)


def _dotify(text: str) -> str:
    return _SANITIZE.sub(".", text).strip(".")


def _squash(text: str) -> str:
    return _SANITIZE.sub("", text)


def rewrite_title(
    scene: SceneFingerprint, original_title: str, *, include_original: bool = False
) -> str:
    """The canonical name Whisparr parses, optionally followed by the tracker's
    own title in square brackets.

    The studio is squashed into one token ("FamilyTherapyXXX"), the way
    trackers write it. Dotted, Whisparr's parser drops a standalone XXX word
    from a studio such as "Family Therapy XXX", reads "Family Therapy", maps
    the release to no scene and rejects it as "Unknown Movie" — Automated
    Search and RSS could never grab those, only a forced Interactive grab.
    Verified against GET /api/v3/parse over all 6562 wanted scenes of a live
    library (2026-09-28): squashing fixed 471 of the 473 affected scenes and
    still mapped every one of the 6060 the dotted form already mapped.

    The suffix is verbatim — not scrubbed, not truncated. Verified against
    Whisparr's own GET /api/v3/parse (2026-08-09): studio, releaseDate, quality
    and scene mapping are identical with and without it, because Whisparr
    identifies an adult scene by studio + date and ignores everything after the
    quality token. A DASH separator is deliberately not used: it made the parser
    emit a phantom releaseGroup, which would reach Whisparr's file naming.

    Defaults to off so the pure function keeps its historical contract; the
    service passes the config value explicitly at every call site."""
    parts = [
        _squash(scene.site),
        scene.date.isoformat(),
        _dotify(scene.title),
        "XXX",
        *extract_quality_tokens(original_title),
    ]
    canonical = ".".join(p for p in parts if p)
    return f"{canonical} [{original_title}]" if include_original else canonical
