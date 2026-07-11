from datetime import date

from scenehound.dates import extract_dates, parse_query_term


def test_parse_query_term_unambiguous_day():
    q = parse_query_term("thatfetishgirl 25.03.2026")
    assert q.site_token == "thatfetishgirl"
    assert q.dates == (date(2026, 3, 25),)  # dd.mm.yyyy


def test_parse_query_term_ambiguous_yields_both_ddmm_first():
    q = parse_query_term("Scott Stark Studios 05.07.2026")
    assert q.site_token == "Scott Stark Studios"
    assert q.dates == (date(2026, 7, 5), date(2026, 5, 7))


def test_parse_query_term_symmetric_date_single():
    q = parse_query_term("site 07.07.2026")
    assert q.dates == (date(2026, 7, 7),)


def test_parse_query_term_rejects_non_matching():
    assert parse_query_term("no date here") is None
    assert parse_query_term("") is None


def test_extract_iso_and_dotted():
    assert date(2026, 7, 7) in extract_dates("Site.2026-07-07.Title")
    assert date(2026, 7, 7) in extract_dates("Site 2026.07.07 Title")


def test_extract_two_digit_year_scene_format():
    # 26.07.05 → yy.mm.dd is the dominant scene convention
    ds = extract_dates("Site.26.07.05.Title.XXX")
    assert date(2026, 7, 5) in ds


def test_extract_dmy_and_mdy_four_digit_year():
    ds = extract_dates("released 12-07-2026 in HD")
    assert date(2026, 7, 12) in ds and date(2026, 12, 7) in ds


def test_extract_ignores_resolutions_and_garbage():
    assert extract_dates("Site.Title.1080p.x265") == frozenset()
    assert extract_dates("no dates at all") == frozenset()


def test_extract_implausible_years_dropped():
    assert extract_dates("Thing.1085-01-01.wat") == frozenset()
