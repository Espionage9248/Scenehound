"""Adaptive query variant generation.

Order encodes retrieval likelihood: the yy.mm.dd scene convention with site
name is the most common well-formed naming on these trackers; later variants
trade precision for recall. The orchestrator fires variants one at a time and
stops as soon as something clears the threshold."""
from __future__ import annotations

from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens


def plan_queries(scene: SceneFingerprint, max_queries: int = 5) -> tuple[str, ...]:
    d = scene.date
    variants: list[str] = [
        f"{scene.site} {d.strftime('%y.%m.%d')}",   # scene convention: Site.YY.MM.DD
        f"{scene.site} {d.isoformat()}",             # ISO-dated titles
    ]
    if scene.performers:
        variants.append(f"{scene.performers[0]} {d.isoformat()}")
    variants.append(scene.site)                      # site alone (date filtering client-side)
    title_words = content_tokens(scene.title)[:3]
    if title_words:
        variants.append(f"{scene.site} {' '.join(title_words)}")
        if scene.performers:
            variants.append(f"{scene.performers[0]} {' '.join(title_words)}")

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) == max_queries:
            break
    return tuple(out)
