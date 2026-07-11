"""Core data types shared across all modules."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SceneFingerprint:
    scene_id: int
    site: str
    site_aliases: tuple[str, ...]
    date: datetime.date
    title: str
    performers: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseCandidate:
    title: str
    guid: str
    link: str
    size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    categories: tuple[int, ...] = ()
    pub_date: str | None = None
    raw_attrs: dict[str, str] = field(default_factory=dict)
    # The <enclosure> attributes (url/type/length) carry the torrent download URL.
    # Torrent Torznab clients (Whisparr) read the download URL from here, not <link>,
    # so it MUST be preserved verbatim or grabs fail.
    enclosure: dict[str, str] = field(default_factory=dict)
