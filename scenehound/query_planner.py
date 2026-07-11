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
    yymmdd = d.strftime("%y.%m.%d")
    title_toks = content_tokens(scene.title)
    title_str = " ".join(title_toks[:3])
    performers = scene.performers
    # Primary site first, then aliases (e.g. the xxx-toggled spelling from
    # normalize.xxx_site_variant). The tracker matches only the literal title text,
    # so a studio that carries "xxx" on one side of the divide but not the other
    # must be searched in BOTH spellings; squash() makes them equivalent for the
    # matcher/index, but never for the tracker's own search.
    sites = (scene.site, *scene.site_aliases)

    variants: list[str] = [
        f"{scene.site} {yymmdd}",                    # dated scene convention: Site.YY.MM.DD
    ]
    # --- Date-free, high-recall retrievers, ordered to fire within a tight rate
    # budget. These work for undated-title trackers (Empornium) where any date term
    # ANDs the result to zero. The two that actually retrieve there are the
    # distinctive TITLE and the PERFORMERS -- NOT the studio: trackers glue the
    # studio into one token ("[FamilyTherapy]"), so a spaced studio query can't
    # match it. So title + performers come first; site queries are demoted.
    #
    # Distinctive title alone (>=2 content tokens): performer-independent, and the
    # scene title reliably appears in the release title regardless of which
    # performer the tracker names it after.
    if len(title_toks) >= 2:
        variants.append(title_str)
    # Every performer alone -- NOT just performers[0]. The tracker names the scene
    # after one specific performer, often not the one Whisparr lists first (e.g. the
    # male lead is listed first but essentially never appears in the title). Whisparr
    # exposes no reliable gender, so query them all; a non-matching performer query
    # merely returns candidates the two-strong-signal matcher rejects.
    variants += list(performers)
    if title_str:
        variants += [f"{p} {title_str}" for p in performers]  # performer + distinctive title
    # Site-based queries: lower priority (they miss on glued-studio trackers) but
    # still help trackers that DO split the studio and the dated scene convention.
    if title_str:
        variants += [f"{s} {title_str}" for s in sites]  # site/alias + distinctive title
    variants += list(sites)                          # site/alias alone (matcher filters by date)
    # ISO-dated and alias-dated fallbacks: rare in real titles, worth a slot only
    # if budget remains (the primary dated convention is already variant 0).
    variants += [f"{s} {yymmdd}" for s in scene.site_aliases]
    variants.append(f"{scene.site} {d.isoformat()}")
    variants += [f"{p} {d.isoformat()}" for p in performers]

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
