"""Adaptive query variant generation.

Order encodes retrieval likelihood. Tracker search matches release *title text*
only and ANDs the query terms, so a query term that isn't in the title (most
importantly a date) drops the result to zero. Two title conventions dominate:

- dated: ``Studio.YY.MM.DD.Performer.Title.XXX`` — the ``site + yy.mm.dd`` query wins.
- undated: ``[Studio] Performer - Title {Group}`` (e.g. Empornium) — NO date in the
  title, so every dated query returns nothing; retrieval must lean on performer,
  site, and distinctive title words with NO date term.

So date-free, high-recall variants (performer alone, performer + title, site +
title) are issued early — right after the dated scene-convention query — and the
ISO-dated variants, which almost never appear in real titles, are demoted to
last-resort fallbacks. The orchestrator fires variants one at a time (subject to
the per-indexer rate budget) and stops as soon as something clears the threshold,
so the first few variants must cover both conventions."""
from __future__ import annotations

from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens


def plan_queries(scene: SceneFingerprint, max_queries: int = 5) -> tuple[str, ...]:
    d = scene.date
    perf = scene.performers[0] if scene.performers else None
    title_str = " ".join(content_tokens(scene.title)[:3])

    variants: list[str] = [
        f"{scene.site} {d.strftime('%y.%m.%d')}",   # dated scene convention: Site.YY.MM.DD
    ]
    # Date-free, high-recall variants: work for undated-title trackers where any
    # date term ANDs the result to zero. Performer alone is the single best query
    # for the "[Studio] Performer - Title" convention.
    if perf:
        variants.append(perf)                        # performer alone
    if perf and title_str:
        variants.append(f"{perf} {title_str}")       # performer + distinctive title words
    if title_str:
        variants.append(f"{scene.site} {title_str}")  # site + distinctive title words
    variants.append(scene.site)                      # site alone (matcher filters by date)
    # ISO-dated fallbacks: rare in real titles, worth a slot only if budget remains.
    variants.append(f"{scene.site} {d.isoformat()}")
    if perf:
        variants.append(f"{perf} {d.isoformat()}")

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) == max_queries:
            break
    return tuple(out)
