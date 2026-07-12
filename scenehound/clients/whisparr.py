"""Whisparr v3 API client: pages the wanted/missing list.

Field mapping grounded in a live sample (tests/fixtures/whisparr_wanted_sample.json):
  title        -> scene title (note: often "Performer | Scene Title" for some studios;
                  kept verbatim — it yields a sensible canonical rewrite and only makes
                  title-matching more conservative; site+date carries the match)
  releaseDate  -> ISO datetime "YYYY-MM-DDT..Z" (truncated to the date)
  studioTitle  -> site (top-level; a nested studio.title fallback is kept defensively)
  performerNames -> flat list of performer name strings (NOT credits[])
  id           -> scene_id
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from scenehound.models import SceneFingerprint
from scenehound.normalize import squash, xxx_site_variant

log = logging.getLogger("scenehound.whisparr")
_PAGE_SIZE = 1000


def scene_from_record(record: dict) -> SceneFingerprint | None:
    title = record.get("title") or ""
    raw_date = record.get("releaseDate") or ""
    site = record.get("studioTitle") or (record.get("studio") or {}).get("title") or ""
    if not (title and raw_date and site):
        return None
    try:
        parsed = date.fromisoformat(str(raw_date)[:10])
    except ValueError:
        return None
    names = record.get("performerNames")
    if not isinstance(names, list):
        # A bare string is iterable but must not be split into single-char
        # "performers"; anything that is not a list is treated as absent.
        names = ()
    performers = tuple(p for p in names if isinstance(p, str) and p)
    # A studio's "xxx" suffix is decorative and appears on only one side of the
    # Whisparr/tracker divide ("Family Therapy XXX" vs "[FamilyTherapy] …", and the
    # reverse). Alias the toggled spelling so both retrieval (plan_queries) and
    # matching/indexing (which consume site_aliases) cover both forms. squash() is
    # the same key the matcher/index compare against, so the guard here mirrors theirs.
    alias = xxx_site_variant(site)
    site_aliases = (alias,) if alias and squash(alias) != squash(site) else ()
    return SceneFingerprint(
        scene_id=int(record.get("id", 0)),
        site=site,
        site_aliases=site_aliases,
        date=parsed,
        title=title,
        performers=performers,
    )


class WhisparrClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}
        self._client = client

    async def fetch_wanted(self) -> list[SceneFingerprint]:
        scenes: list[SceneFingerprint] = []
        page, received, total = 1, 0, None
        while total is None or received < total:
            resp = await self._client.get(
                f"{self._base}/api/v3/wanted/missing",
                params={"page": page, "pageSize": _PAGE_SIZE},
                headers=self._headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            body = resp.json()
            total = int(body.get("totalRecords", 0))
            records = body.get("records", [])
            if not records:
                break
            received += len(records)
            skipped = 0
            for r in records:
                if s := scene_from_record(r):
                    scenes.append(s)
                else:
                    skipped += 1
            if skipped:
                log.warning("wanted-fetch page=%d skipped=%d unmappable records", page, skipped)
            page += 1
        log.info("wanted-fetch complete scenes=%d", len(scenes))
        return scenes

    async def fetch_queue(self) -> list[dict]:
        records: list[dict] = []
        page, received, total = 1, 0, None
        while total is None or received < total:
            resp = await self._client.get(
                f"{self._base}/api/v3/queue",
                params={"page": page, "pageSize": _PAGE_SIZE, "includeMovie": "true"},
                headers=self._headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            body = resp.json()
            total = int(body.get("totalRecords", 0))
            batch = body.get("records", [])
            if not batch:
                break
            received += len(batch)
            records.extend(batch)
            page += 1
        return records

    async def fetch_manual_import(
        self, download_id: str, filter_existing: bool = True
    ) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/api/v3/manualimport",
            params={
                "downloadId": download_id,
                "filterExistingFiles": "true" if filter_existing else "false",
            },
            headers=self._headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, list) else body.get("records", [])

    async def fetch_movie(self, movie_id: int) -> dict:
        resp = await self._client.get(
            f"{self._base}/api/v3/movie/{movie_id}",
            headers=self._headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def post_manual_import(
        self, files: list[dict], import_mode: str = "copy"
    ) -> None:
        resp = await self._client.post(
            f"{self._base}/api/v3/command",
            json={"name": "ManualImport", "importMode": import_mode, "files": files},
            headers=self._headers,
            timeout=60.0,
        )
        resp.raise_for_status()
