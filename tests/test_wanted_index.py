from datetime import date

from scenehound.matcher import score
from scenehound.models import SceneFingerprint
from scenehound.wanted_index import WantedIndex

S1 = SceneFingerprint(1, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                      "Latex Worship Session", ("Jane Doe",))
S2 = SceneFingerprint(2, "Scott Stark Studios", (), date(2026, 7, 5),
                      "Beach Day", ("Alex Roe",))
S3 = SceneFingerprint(3, "That Fetish Girl", ("TFG",), date(2026, 7, 8),
                      "Another Session", ("Mary Major",))


def make_index():
    return WantedIndex([S1, S2, S3])


def test_resolve_site_and_date():
    idx = make_index()
    assert idx.resolve("thatfetishgirl", [date(2026, 7, 7)]) == (S1, S3)


def test_resolve_via_alias_and_spacing():
    idx = make_index()
    assert S1 in idx.resolve("TFG", [date(2026, 7, 7)])
    assert idx.resolve("Scott Stark Studios", [date(2026, 7, 5)]) == (S2,)


def test_resolve_unknown_site_empty():
    assert make_index().resolve("nosuchsite", [date(2026, 7, 7)]) == ()


def test_candidates_by_date_bucket():
    idx = make_index()
    cands = idx.candidates_for_title("Random.Name.2026-07-05.No.Other.Info")
    assert S2 in cands
    assert S1 not in cands


def test_candidates_by_token_overlap_without_date():
    idx = make_index()
    cands = idx.candidates_for_title("Jane Doe latex worship clip")
    assert S1 in cands
    assert S2 not in cands


def test_no_shared_signal_no_candidates():
    idx = make_index()
    assert idx.candidates_for_title("completely unrelated 720p clip") == ()


def test_site_vocab_and_other_sites():
    idx = make_index()
    assert "thatfetishgirl" in idx.site_vocab
    assert "scottstarkstudios" in idx.site_vocab
    others = idx.other_sites_for(S1)
    assert "scottstarkstudios" in others
    assert "thatfetishgirl" not in others and "tfg" not in others


def test_len():
    assert len(make_index()) == 3


def test_lossless_numeric_performer_name():
    # performer name is a pure-digit token: content_tokens strips it, but the
    # matcher still matches it via squash-in-ngrams. The pre-filter must include it.
    from scenehound.matcher import score
    scene = SceneFingerprint(50, "ExampleSite", (), date(2015, 1, 1),
                             "Foo Bar Baz", ("2020", "2021"))
    idx = WantedIndex([scene])
    release = "ExampleSite.2020.2021.ClipName.Extra.Words.Here.1080p.mp4"
    assert score(scene, release).confidence >= 75          # matcher WOULD match
    assert scene in idx.candidates_for_title(release)      # so pre-filter must not drop it


def test_lossless_site_plus_two_performers_no_date_no_title_overlap():
    from scenehound.matcher import score
    scene = SceneFingerprint(51, "ExampleSite", (), date(2015, 1, 1),
                             "Unshared Title Words", ("Jane Doe", "Mary Major"))
    idx = WantedIndex([scene])
    # site + both performers present; no date, no title-word overlap
    release = "ExampleSite.Jane.Doe.And.Mary.Major.ClipName.1080p"
    assert score(scene, release).confidence >= 75
    assert scene in idx.candidates_for_title(release)


def test_site_name_indexed_for_token_lookup():
    scene = SceneFingerprint(52, "ExampleSite", ("ExSite",), date(2015, 1, 1),
                             "Some Scene", ("Jane Doe",))
    idx = WantedIndex([scene])
    # a release naming only the site (glued) shares the squashed site n-gram
    assert scene in idx.candidates_for_title("ExampleSite.Random.Clip.Name")
