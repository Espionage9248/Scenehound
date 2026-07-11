"""Torznab XML: parsing Prowlarr feeds and building feeds for Whisparr."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Sequence

from scenehound.models import ReleaseCandidate

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
_NS = {"torznab": TORZNAB_NS}
ORIGINAL_TITLE_ATTR = "scenehound_original_title"


@dataclass(frozen=True)
class FeedEntry:
    candidate: ReleaseCandidate
    title_override: str | None = None


def parse_feed(xml: bytes) -> list[ReleaseCandidate]:
    root = ET.fromstring(xml)
    out: list[ReleaseCandidate] = []
    for item in root.findall(".//channel/item"):
        attrs: dict[str, str] = {}
        categories: list[int] = []
        for a in item.findall("torznab:attr", _NS):
            name, value = a.get("name", ""), a.get("value", "")
            if name == "category":
                try:
                    categories.append(int(value))
                except ValueError:
                    pass
            else:
                attrs[name] = value
        size_text = item.findtext("size")
        out.append(
            ReleaseCandidate(
                title=item.findtext("title", ""),
                guid=item.findtext("guid", ""),
                link=item.findtext("link", ""),
                size=int(size_text) if size_text and size_text.isdigit() else None,
                seeders=int(attrs["seeders"]) if attrs.get("seeders", "").isdigit() else None,
                leechers=None,
                categories=tuple(categories),
                pub_date=item.findtext("pubDate"),
                raw_attrs=attrs,
            )
        )
    return out


def _sub(parent: ET.Element, tag: str, text: str | None) -> None:
    if text is not None:
        ET.SubElement(parent, tag).text = text


def _attr(item: ET.Element, name: str, value: str) -> None:
    ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": name, "value": value})


def build_feed(entries: Sequence[FeedEntry]) -> bytes:
    ET.register_namespace("torznab", TORZNAB_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    _sub(channel, "title", "Scenehound")
    for entry in entries:
        c = entry.candidate
        item = ET.SubElement(channel, "item")
        _sub(item, "title", entry.title_override or c.title)
        _sub(item, "guid", c.guid)
        _sub(item, "link", c.link)
        _sub(item, "size", str(c.size) if c.size is not None else None)
        _sub(item, "pubDate", c.pub_date)
        for cat in c.categories:
            _attr(item, "category", str(cat))
        for name, value in c.raw_attrs.items():
            _attr(item, name, value)
        if c.seeders is not None and "seeders" not in c.raw_attrs:
            _attr(item, "seeders", str(c.seeders))
        if entry.title_override is not None:
            _attr(item, ORIGINAL_TITLE_ATTR, c.title)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build_caps() -> bytes:
    caps = ET.Element("caps")
    searching = ET.SubElement(caps, "searching")
    ET.SubElement(searching, "search", {"available": "yes", "supportedParams": "q"})
    cats = ET.SubElement(caps, "categories")
    adult = ET.SubElement(cats, "category", {"id": "6000", "name": "XXX"})
    ET.SubElement(adult, "subcat", {"id": "6010", "name": "XXX/DVD"})
    ET.SubElement(cats, "category", {"id": "6010", "name": "XXX/DVD"})
    return ET.tostring(caps, encoding="utf-8", xml_declaration=True)


def build_error(code: int, description: str) -> bytes:
    el = ET.Element("error", {"code": str(code), "description": description})
    return ET.tostring(el, encoding="utf-8", xml_declaration=True)
