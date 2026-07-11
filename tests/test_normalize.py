from scenehound.normalize import content_tokens, squash, tokenize


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
