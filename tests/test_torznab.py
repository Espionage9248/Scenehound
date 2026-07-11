import xml.etree.ElementTree as ET

from scenehound.models import ReleaseCandidate
from scenehound.torznab import (
    ORIGINAL_TITLE_ATTR,
    FeedEntry,
    build_caps,
    build_error,
    build_feed,
    parse_feed,
)

PROWLARR_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Empornium</title>
    <item>
      <title>Messy.Release.Name.26.07.05.XXX</title>
      <guid>https://tracker/torrents.php?id=111</guid>
      <link>http://prowlarr:9696/12/download?apikey=k&amp;link=abc</link>
      <size>1073741824</size>
      <pubDate>Sun, 05 Jul 2026 10:00:00 +0000</pubDate>
      <torznab:attr name="seeders" value="12"/>
      <torznab:attr name="peers" value="15"/>
      <torznab:attr name="category" value="6000"/>
      <torznab:attr name="downloadvolumefactor" value="0"/>
    </item>
  </channel>
</rss>"""


def test_parse_feed_extracts_candidates():
    items = parse_feed(PROWLARR_FEED)
    assert len(items) == 1
    c = items[0]
    assert c.title == "Messy.Release.Name.26.07.05.XXX"
    assert c.guid == "https://tracker/torrents.php?id=111"
    assert c.link.startswith("http://prowlarr:9696/12/download")
    assert c.size == 1073741824
    assert c.seeders == 12
    assert c.categories == (6000,)
    assert c.raw_attrs["downloadvolumefactor"] == "0"


def test_roundtrip_preserves_everything_except_title():
    c = parse_feed(PROWLARR_FEED)[0]
    out = build_feed([FeedEntry(c, title_override="Site.2026-07-05.Title.XXX")])
    reparsed = parse_feed(out)[0]
    assert reparsed.title == "Site.2026-07-05.Title.XXX"
    assert reparsed.guid == c.guid
    assert reparsed.link == c.link
    assert reparsed.size == c.size
    assert reparsed.seeders == c.seeders
    assert reparsed.raw_attrs["downloadvolumefactor"] == "0"
    # original title preserved for audit
    root = ET.fromstring(out)
    ns = {"torznab": "http://torznab.com/schemas/2015/feed"}
    attrs = root.findall(".//item/torznab:attr", ns)
    orig = [a for a in attrs if a.get("name") == "scenehound_original_title"]
    assert orig and orig[0].get("value") == c.title


def test_passthrough_entry_keeps_title():
    c = parse_feed(PROWLARR_FEED)[0]
    out = build_feed([FeedEntry(c)])
    assert parse_feed(out)[0].title == c.title


def test_caps_advertises_q_search_and_adult_categories():
    root = ET.fromstring(build_caps())
    search = root.find("searching/search")
    assert search.get("available") == "yes"
    assert "q" in search.get("supportedParams")
    cat_ids = {c.get("id") for c in root.findall("categories/category")}
    assert {"6000", "6010"} <= cat_ids


def test_error_shape():
    root = ET.fromstring(build_error(100, "Incorrect user credentials"))
    assert root.tag == "error"
    assert root.get("code") == "100"
    assert root.get("description") == "Incorrect user credentials"


LEECHERS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Empornium</title>
    <item>
      <title>Messy.Release.Name.26.07.05.XXX</title>
      <guid>https://tracker/torrents.php?id=111</guid>
      <link>http://prowlarr:9696/12/download?apikey=k&amp;link=abc</link>
      <size>1073741824</size>
      <pubDate>Sun, 05 Jul 2026 10:00:00 +0000</pubDate>
      <torznab:attr name="seeders" value="12"/>
      <torznab:attr name="leechers" value="7"/>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""


def test_parse_populates_typed_leechers_and_roundtrips():
    c = parse_feed(LEECHERS_FEED)[0]
    assert c.leechers == 7
    out = build_feed([FeedEntry(c, title_override="Site.2026-07-05.Title.XXX")])
    reparsed = parse_feed(out)[0]
    assert reparsed.leechers == 7
    # no duplicate leechers attr emitted in the rebuilt XML
    root = ET.fromstring(out)
    ns = {"torznab": "http://torznab.com/schemas/2015/feed"}
    leech_attrs = [
        a
        for a in root.findall(".//item/torznab:attr", ns)
        if a.get("name") == "leechers"
    ]
    assert len(leech_attrs) == 1


def test_empty_title_override_is_a_real_override():
    c = parse_feed(PROWLARR_FEED)[0]
    out = build_feed([FeedEntry(c, title_override="")])
    reparsed = parse_feed(out)[0]
    assert reparsed.title == ""
    root = ET.fromstring(out)
    ns = {"torznab": "http://torznab.com/schemas/2015/feed"}
    orig = [
        a
        for a in root.findall(".//item/torznab:attr", ns)
        if a.get("name") == ORIGINAL_TITLE_ATTR
    ]
    assert orig and orig[0].get("value") == c.title
