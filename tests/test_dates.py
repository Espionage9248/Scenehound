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


def test_extract_iso_and_dotted_are_primary():
    assert date(2026, 7, 7) in extract_dates("Site.2026-07-07.Title").primary
    assert date(2026, 7, 7) in extract_dates("Site 2026.07.07 Title").primary


def test_extract_two_digit_year_scene_format_primary():
    # 26.07.05 → yy.mm.dd is the dominant scene convention
    ds = extract_dates("Site.26.07.05.Title.XXX")
    assert date(2026, 7, 5) in ds.primary


def test_extract_dmy_primary_mdy_secondary_four_digit_year():
    ds = extract_dates("released 12-07-2026 in HD")
    assert date(2026, 7, 12) in ds.primary     # dd.mm.yyyy, dominant
    assert date(2026, 12, 7) in ds.secondary   # mm.dd.yyyy, alternate
    assert ds.all == ds.primary | ds.secondary


def test_extract_ignores_resolutions_and_garbage():
    assert extract_dates("Site.Title.1080p.x265").all == frozenset()
    assert extract_dates("no dates at all").all == frozenset()


def test_extract_implausible_years_dropped():
    assert extract_dates("Thing.1085-01-01.wat").all == frozenset()


def test_triple2_alternate_readings_are_secondary():
    # The 2026-07-15 false-grab token: [26-07-14] is 2026-07-14 in the
    # dominant yy-mm-dd convention; 2014-07-26 only via the dd-mm-yy alternate.
    ds = extract_dates("[FamilyTherapy] Alexa Chains - Goth Latina [26-07-14] [1080p]")
    assert date(2026, 7, 14) in ds.primary
    assert date(2014, 7, 26) in ds.secondary
    assert date(2014, 7, 26) not in ds.primary


def test_primary_wins_dedup_across_tokens():
    # 2026-07-05 is primary via the ISO token AND secondary via dd.mm.yy of
    # the triple; the sets must stay disjoint with primary winning.
    ds = extract_dates("Site.2026-07-05.and.05-07-26.Clip")
    assert date(2026, 7, 5) in ds.primary
    assert date(2026, 7, 5) not in ds.secondary
