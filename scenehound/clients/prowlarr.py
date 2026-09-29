"""Prowlarr Torznab client: queries the real indexers on Scenehound's behalf.

Prowlarr exposes each indexer at {base}/{indexer_id}/api speaking Torznab;
download links in results point back at Prowlarr's own proxy, so grabs reuse
Prowlarr's tracker auth untouched."""
from __future__ import annotations

from typing import Sequence

import httpx

from scenehound.models import ReleaseCandidate
from scenehound.torznab import parse_feed


class ProwlarrError(Exception):
    pass


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client

    def _redact(self, text: str) -> str:
        # Guarded: replacing "" would splice REDACTED between every character.
        return text.replace(self._api_key, "REDACTED") if self._api_key else text

    async def search(
        self,
        indexer_id: int,
        query: str | None,
        categories: Sequence[int],
        limit: int = 100,
    ) -> list[ReleaseCandidate]:
        params: dict[str, str] = {
            "t": "search",
            "cat": ",".join(str(c) for c in categories),
            "limit": str(limit),
            "apikey": self._api_key,
        }
        if query is not None:
            params["q"] = query
        try:
            resp = await self._client.get(
                f"{self._base}/{indexer_id}/api", params=params, timeout=40.0
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            # httpx puts the request URL, apikey included, in its message; this
            # text reaches the log, the UI's state file and Whisparr.
            raise ProwlarrError(f"prowlarr search failed: {self._redact(str(exc))}") from exc
        try:
            return parse_feed(resp.content)
        except Exception as exc:  # malformed XML from upstream
            raise ProwlarrError(f"unparseable torznab feed: {exc}") from exc
