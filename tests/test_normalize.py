from scenehound.normalize import (
    content_tokens,
    squash,
    tokenize,
    xxx_site_variant,
)


def test_squash_strips_case_punctuation_spacing():
    assert squash("That Fetish Girl!") == "thatfetishgirl"
    assert squash("Scott Stark Studios") == "scottstarkstudios"
    assert squash("h.265") == "h265"


def test_tokenize_splits_on_non_alnum():
    assert tokenize("Site.2026-07-07.Some.Title.XXX.1080p") == [
        "site", "2026", "07", "07", "some", "title", "xxx", "1080p",
    ]


def test_content_tokens_drops_junk_and_bare_numbers():
    assert content_tokens("Site.Name.Great.Scene.XXX.1080p.WEB-DL.x265-GRP") == [
        "site", "name", "great", "scene", "grp",
    ]
    assert content_tokens("2026 07 07") == []


def test_xxx_site_variant_strips_suffix_in_all_spellings():
    # A trailing decorative "xxx" is removed so the non-"xxx" tracker spelling
    # can be searched and matched. Result must squash to the stem and be a clean
    # search term (usable verbatim by plan_queries).
    assert xxx_site_variant("Family Therapy XXX") == "Family Therapy"
    assert xxx_site_variant("familytherapyxxx") == "familytherapy"
    assert xxx_site_variant("FamilyTherapyXXX") == "FamilyTherapy"
    assert xxx_site_variant("Family.Therapy.XXX") == "Family.Therapy"
    assert squash(xxx_site_variant("Family Therapy XXX")) == "familytherapy"


def test_xxx_site_variant_appends_suffix_when_absent():
    # The reverse direction: Whisparr has the bare name, the tracker carries "xxx".
    assert xxx_site_variant("Family Therapy") == "Family Therapy XXX"
    assert squash(xxx_site_variant("Family Therapy")) == "familytherapyxxx"
    assert squash(xxx_site_variant("familytherapy")) == "familytherapyxxx"


def test_xxx_site_variant_guards_degenerate_names():
    # Names that would strip to nothing or to too-generic a stem yield no alias,
    # so a bogus site key can't fabricate a strong signal.
    assert xxx_site_variant("XXX") is None
    assert xxx_site_variant("xxx") is None
    assert xxx_site_variant("") is None
    assert xxx_site_variant("   ") is None
    assert xxx_site_variant("Maxxx") is None   # stem "Ma" is too short to alias safely
