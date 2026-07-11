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


def squash(s: str) -> str:
    """Lowercase and strip everything that is not a letter or digit."""
    return _NON_ALNUM.sub("", s.lower())


def tokenize(s: str) -> list[str]:
    """Lowercase tokens split on any non-alphanumeric run."""
    return [t for t in _SPLIT.split(s.lower()) if t]


def content_tokens(s: str) -> list[str]:
    """Tokens that plausibly identify content: junk and bare numbers removed."""
    return [t for t in tokenize(s) if t not in JUNK_TOKENS and not t.isdigit()]
