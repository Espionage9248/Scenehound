"""Date parsing: Whisparr query terms and dates embedded in release titles."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Plausible content-date window. Wide on purpose; the matcher compares against
# a specific scene date anyway.
_MIN_YEAR, _MAX_YEAR = 1990, 2049

_QUERY_RE = re.compile(r"^(?P<site>.*\S)\s+(?P<a>\d{2})\.(?P<b>\d{2})\.(?P<y>\d{4})$")

_SEP = r"[.\-_/ ]"
# Group order within each regex determines reading rank: the dominant
# convention's reading is primary, alternate orderings are secondary.
_YMD4 = re.compile(rf"(?<![\d])(\d{{4}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?![\d])")
_XY4 = re.compile(rf"(?<![\d])(\d{{1,2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{4}})(?![\d])")
_TRIPLE2 = re.compile(rf"(?<![\d])(\d{{2}}){_SEP}(\d{{2}}){_SEP}(\d{{2}})(?![\d])")


@dataclass(frozen=True)
class ParsedQuery:
    site_token: str
    dates: tuple[date, ...]


def _valid(y: int, m: int, d: int) -> date | None:
    if not (_MIN_YEAR <= y <= _MAX_YEAR):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _expand_two_digit_year(yy: int) -> int:
    return 2000 + yy if yy <= 49 else 1900 + yy


def parse_query_term(term: str) -> ParsedQuery | None:
    """Parse Whisparr's search term: '<site> <dd.mm.yyyy>' (format confirmed
    from live logs). Ambiguous day<=12 also yields the mm.dd reading, dd.mm
    first so callers preferring the primary interpretation take index 0."""
    m = _QUERY_RE.match(term.strip())
    if not m:
        return None
    a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
    dates: list[date] = []
    if primary := _valid(y, b, a):  # dd.mm.yyyy
        dates.append(primary)
    if a <= 12 and a != b:
        if alt := _valid(y, a, b):  # mm.dd.yyyy reading
            if alt not in dates:
                dates.append(alt)
    if not dates:
        return None
    return ParsedQuery(site_token=m["site"], dates=tuple(dates))


@dataclass(frozen=True)
class ExtractedDates:
    """Dates found in a release title, ranked by reading plausibility.

    primary: produced by the dominant convention of their format (yy.mm.dd
    for two-digit triples, yyyy.mm.dd, dd.mm.yyyy). secondary: reachable only
    via an alternate reading of an ambiguous ordering. Disjoint — a date
    reachable both ways is primary."""

    primary: frozenset[date]
    secondary: frozenset[date]

    @property
    def all(self) -> frozenset[date]:
        return self.primary | self.secondary


def extract_dates(text: str) -> ExtractedDates:
    """Every plausible date found in a release title, across formats, ranked
    by reading. The matcher lets only primary dates become strong signals
    (a 26-07-14 release must not strongly match a 2014-07-26 scene); the
    wanted-index pre-filter uses .all to stay a lossless superset."""
    prim: set[date] = set()
    sec: set[date] = set()
    for m in _YMD4.finditer(text):
        y, b, c = int(m[1]), int(m[2]), int(m[3])
        if d := _valid(y, b, c):  # yyyy.mm.dd (dominant)
            prim.add(d)
        if d := _valid(y, c, b):  # yyyy.dd.mm
            sec.add(d)
    for m in _XY4.finditer(text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if d := _valid(y, b, a):  # dd.mm.yyyy (dominant; parse_query_term precedent)
            prim.add(d)
        if d := _valid(y, a, b):  # mm.dd.yyyy
            sec.add(d)
    for m in _TRIPLE2.finditer(text):
        a, b, c = int(m[1]), int(m[2]), int(m[3])
        if d := _valid(_expand_two_digit_year(a), b, c):  # yy.mm.dd (dominant scene convention)
            prim.add(d)
        for y2, mo, dy in ((c, b, a), (c, a, b)):  # dd.mm.yy, mm.dd.yy
            if d := _valid(_expand_two_digit_year(y2), mo, dy):
                sec.add(d)
    return ExtractedDates(frozenset(prim), frozenset(sec - prim))
