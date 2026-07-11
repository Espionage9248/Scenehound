"""Canonical title emission. Quality tokens are extracted from the original
title only — Scenehound never fabricates quality."""
from __future__ import annotations

import re

from scenehound.models import SceneFingerprint

# (pattern, canonical token). Order = emission order: resolution, source, codec.
# Patterns must not match inside date fragments; \b plus explicit non-dot
# guards handle bracketed bare resolutions like [1080] without eating 07.05.
_RESOLUTION = [
    (re.compile(r"\b(2160p|4k|uhd)\b", re.I), "2160p"),
    (re.compile(r"\b1080p\b|\[1080\]|\b1080(?=\s|$)", re.I), "1080p"),
    (re.compile(r"\b720p\b|\[720\]|\b720(?=\s|$)", re.I), "720p"),
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


def rewrite_title(scene: SceneFingerprint, original_title: str) -> str:
    parts = [
        _dotify(scene.site),
        scene.date.isoformat(),
        _dotify(scene.title),
        "XXX",
        *extract_quality_tokens(original_title),
    ]
    return ".".join(p for p in parts if p)
