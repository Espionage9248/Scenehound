"""In-memory index over the wanted list.

Pre-filtering is lossless by construction: a match requires two strong
signals, so any (release, scene) pair sharing neither a date bucket nor a
content token can never clear the threshold and is safe to skip. To stay
lossless against the matcher's name signals, the token index also covers the
squashed site/alias/performer names of each scene, and the lookup covers the
release's squashed boundary n-grams; so the pre-filter's key set is a superset
of the matcher's name-signal vocabulary and never drops a scene the matcher
would match on a site or performer name (including pure-digit/junk or glued
renderings that content_tokens strips)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Sequence

from scenehound.dates import extract_dates
from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens, squash, tokenize

_MAX_CANDIDATES = 200
_MAX_NAME_TOKENS = 6


def _squashed_ngrams(text: str) -> set[str]:
    """Squashed concatenations of every contiguous run of up to _MAX_NAME_TOKENS
    tokens — the same boundary-aligned form the matcher compares squashed site
    and performer names against. Ensures the index's key vocabulary is a
    superset of the matcher's name-signal vocabulary (losslessness)."""
    toks = tokenize(text)
    grams: set[str] = set()
    for i in range(len(toks)):
        acc = ""
        for j in range(i, min(i + _MAX_NAME_TOKENS, len(toks))):
            acc += toks[j]
            grams.add(acc)
    return grams


class WantedIndex:
    def __init__(self, scenes: Iterable[SceneFingerprint]) -> None:
        self._scenes: list[SceneFingerprint] = list(scenes)
        self._by_date: dict[date, list[SceneFingerprint]] = defaultdict(list)
        self._by_site: dict[str, list[SceneFingerprint]] = defaultdict(list)
        self._by_token: dict[str, list[SceneFingerprint]] = defaultdict(list)
        vocab: set[str] = set()
        for s in self._scenes:
            self._by_date[s.date].append(s)
            for name in (s.site, *s.site_aliases):
                sq = squash(name)
                if sq:
                    self._by_site[sq].append(s)
                    vocab.add(sq)
            name_keys = {sq for name in (s.site, *s.site_aliases) if (sq := squash(name))}
            name_keys.update(sq for p in s.performers if (sq := squash(p)))
            for tok in {
                *content_tokens(s.title),
                *(t for p in s.performers for t in content_tokens(p)),
                *name_keys,
            }:
                self._by_token[tok].append(s)
        self.site_vocab: frozenset[str] = frozenset(vocab)

    def __len__(self) -> int:
        return len(self._scenes)

    def resolve(
        self, site_token: str, dates: Sequence[date]
    ) -> tuple[SceneFingerprint, ...]:
        candidates = self._by_site.get(squash(site_token), [])
        out = [
            s
            for s in candidates
            if any(abs((s.date - d).days) <= 1 for d in dates)
        ]
        return tuple(sorted(out, key=lambda s: s.scene_id))

    def candidates_for_title(self, title: str) -> tuple[SceneFingerprint, ...]:
        hits: dict[int, SceneFingerprint] = {}
        for d in extract_dates(title):
            for delta in (-1, 0, 1):
                for s in self._by_date.get(d + timedelta(days=delta), []):
                    hits[s.scene_id] = s
        for tok in set(content_tokens(title)) | _squashed_ngrams(title):
            for s in self._by_token.get(tok, []):
                hits[s.scene_id] = s
        out = sorted(hits.values(), key=lambda s: s.scene_id)
        return tuple(out[:_MAX_CANDIDATES])

    def other_sites_for(self, scene: SceneFingerprint) -> frozenset[str]:
        own = {squash(n) for n in (scene.site, *scene.site_aliases)}
        return self.site_vocab - own
