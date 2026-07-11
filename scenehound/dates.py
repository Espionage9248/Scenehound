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
# Order matters only for readability; results are set-unioned.
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


def extract_dates(text: str) -> frozenset[date]:
    """Every plausible date found in a release title, across formats, with
    both interpretations of ambiguous day/month orderings."""
    found: set[date] = set()
    for m in _YMD4.finditer(text):
        y, b, c = int(m[1]), int(m[2]), int(m[3])
        for mo, dy in ((b, c), (c, b)):
            if d := _valid(y, mo, dy):
                found.add(d)
    for m in _XY4.finditer(text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        for mo, dy in ((b, a), (a, b)):
            if d := _valid(y, mo, dy):
                found.add(d)
    for m in _TRIPLE2.finditer(text):
        a, b, c = int(m[1]), int(m[2]), int(m[3])
        # yy.mm.dd (dominant scene convention), dd.mm.yy, mm.dd.yy
        for y2, mo, dy in ((a, b, c), (c, b, a), (c, a, b)):
            if d := _valid(_expand_two_digit_year(y2), mo, dy):
                found.add(d)
    return frozenset(found)
