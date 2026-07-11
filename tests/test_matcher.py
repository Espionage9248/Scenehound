from datetime import date

from scenehound.matcher import SINGLE_SIGNAL_CAP, MatchScore, score
from scenehound.models import SceneFingerprint

SCENE = SceneFingerprint(
    scene_id=7,
    site="That Fetish Girl",
    site_aliases=("TFG",),
    date=date(2026, 7, 7),
    title="Latex Worship Session",
    performers=("Jane Doe", "Mary Major"),
)


def test_site_plus_date_clears_threshold():
    s = score(SCENE, "ThatFetishGirl.26.07.07.Latex.Worship.Session.XXX.1080p")
    assert s.veto is None
    assert {"date", "site"} <= set(s.strong_signals)
    assert s.confidence >= 75


def test_date_plus_performer_clears_threshold_without_site():
    s = score(SCENE, "Jane Doe - Latex Worship 2026-07-07 [1080p]")
    assert {"date", "performer"} <= set(s.strong_signals)
    assert s.confidence >= 75


def test_single_strong_signal_capped():
    # date matches, nothing else does
    s = score(SCENE, "Unrelated.Thing.2026-07-07.mp4")
    assert s.strong_signals == ("date",)
    assert s.confidence <= SINGLE_SIGNAL_CAP


def test_alias_counts_as_site():
    s = score(SCENE, "TFG.26.07.07.Latex.Worship.Session")
    assert "site" in s.strong_signals


def test_conflicting_date_vetoes():
    s = score(SCENE, "ThatFetishGirl.2025-01-01.Latex.Worship.Session")
    assert s.veto == "date-mismatch"
    assert s.confidence == 0


def test_adjacent_date_does_not_veto():
    # off-by-one dates happen (timezones); ±1 day is not a contradiction
    s = score(SCENE, "ThatFetishGirl.2026-07-08.Latex.Worship.Session")
    assert s.veto is None


def test_other_site_vetoes():
    s = score(
        SCENE,
        "OtherStudio.26.07.07.Latex.Worship.Session",
        other_sites=frozenset({"otherstudio"}),
    )
    assert s.veto == "site-mismatch"


def test_own_site_present_beats_other_site_veto():
    s = score(
        SCENE,
        "ThatFetishGirl.OtherStudio.26.07.07.Latex.Worship",
        other_sites=frozenset({"otherstudio"}),
    )
    assert s.veto is None


def test_two_performers_near_conclusive():
    s = score(SCENE, "Jane Doe and Mary Major latex worship")
    assert "performer" in s.strong_signals
    assert s.detail["performer"] > 35


def test_garbage_scores_zero_ish():
    s = score(SCENE, "Totally.Different.Studio.Random.Clip.720p")
    assert s.confidence < 40


def test_generic_one_word_title_is_not_a_strong_signal():
    scene = SceneFingerprint(10, "That Fetish Girl", (), date(2026, 7, 7), "Casting", ())
    s = score(scene, "ThatFetishGirl.Casting.Couch.Special.Edition.1080p")
    assert "title" not in s.strong_signals
    assert s.confidence < 75


def test_date_plus_generic_title_does_not_match():
    scene = SceneFingerprint(10, "That Fetish Girl", (), date(2026, 7, 7), "Casting", ())
    s = score(scene, "RandomSite.2026.07.07.Casting.Couch.Amateur.1080p")
    assert s.confidence < 75


def test_short_performer_names_do_not_substring_match():
    scene = SceneFingerprint(11, "Some Site", (), date(2026, 7, 7), "Whatever", ("Ai", "Bo"))
    s = score(scene, "SomeOtherStudio.2026.07.07.Maintenance.Training.Bonus.1080p")
    assert "performer" not in s.strong_signals
    assert s.confidence < 75


def test_short_site_does_not_match_inside_longer_word():
    scene = SceneFingerprint(12, "Vixen", (), date(2026, 7, 7), "Some Scene", ())
    s = score(scene, "Brazzers.2026.07.07.Vixens.Live.In.Latex.1080p")
    assert "site" not in s.strong_signals
    assert s.confidence < 75


def test_distinctive_full_title_still_strong_with_site_no_date():
    scene = SceneFingerprint(13, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ("Jane Doe",))
    s = score(scene, "TFG - Latex Worship Session [1080]")
    assert s.confidence >= 75
    assert {"site", "title"} <= set(s.strong_signals)


def test_distinctive_full_title_still_strong_with_performer_no_date():
    scene = SceneFingerprint(13, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ("Jane Doe",))
    s = score(scene, "Jane.Doe.Latex.Worship.Session.720p")
    assert s.confidence >= 75
    assert {"performer", "title"} <= set(s.strong_signals)


def test_fuzzy_site_does_not_fabricate_from_coincidental_phrase():
    scene = SceneFingerprint(20, "SisLovesMe", (), date(2026, 7, 7), "Some Scene", ())
    s = score(scene, "WrongStudio.2026.07.07.Sis.Loves.My.Stepbro.720p")
    assert "site" not in s.strong_signals
    assert s.confidence < 75
    scene2 = SceneFingerprint(21, "MyFamilyPies", (), date(2026, 7, 7), "Some Scene", ())
    s2 = score(scene2, "WrongStudio.2026.07.07.My.Family.Lies.720p")
    assert "site" not in s2.strong_signals
    assert s2.confidence < 75


def test_site_matches_exact_boundary_ngram_only():
    scene = SceneFingerprint(22, "Scott Stark Studios", (), date(2026, 7, 5), "Beach Day", ())
    # correctly-spelled site matches via exact boundary n-gram
    assert "site" in score(scene, "Scott.Stark.Studios.2026-07-05.Beach.Day.1080p").strong_signals
    # plural/near-spelling coincidences on real studios must NOT fabricate a site match
    for site, title in [
        ("PublicAgent", "Studio.2026.07.07.Public.Agents.Report.1080p"),
        ("MyFamilyPies", "Studio.2026.07.07.My.Family.Pie.Recipe.1080p"),
        ("FakeHostel", "Studio.2026.07.07.Fake.Hostels.List.1080p"),
    ]:
        sc = SceneFingerprint(0, site, (), date(2026, 7, 7), "Some Scene", ())
        r = score(sc, title)
        assert "site" not in r.strong_signals, (site, r.confidence, r.strong_signals)
        assert r.confidence < 75


def test_punctuated_performer_names_match():
    scene = SceneFingerprint(23, "Some Site", (), date(2026, 7, 7),
                             "Latex Worship Session", ("Jane O'Neil",))
    s = score(scene, "Jane.ONeil.Latex.Worship.Session.720p")
    assert "performer" in s.strong_signals
    assert s.confidence >= 75
    scene2 = SceneFingerprint(24, "Some Site", (), date(2026, 7, 7),
                              "Latex Worship Session", ("Mary-Jane Watson",))
    s2 = score(scene2, "MaryJane.Watson.Latex.Worship.Session.720p")
    assert "performer" in s2.strong_signals
    assert s2.confidence >= 75


def test_short_performer_still_rejected_after_ngram_change():
    scene = SceneFingerprint(25, "Some Site", (), date(2026, 7, 7), "Whatever", ("Ai", "Bo"))
    s = score(scene, "SomeOtherStudio.2026.07.07.Maintenance.Training.Bonus.1080p")
    assert "performer" not in s.strong_signals
    assert s.confidence < 75


def test_date_plus_generic_title_only_does_not_match():
    # Title is near-exact but neither site nor performer is present, so title
    # must NOT count as a strong signal — date alone is capped below threshold.
    scene = SceneFingerprint(26, "That Fetish Girl", (), date(2026, 7, 7),
                             "Casting Couch", ())
    s = score(scene, "BangBros.2026-07-07.Casting.Couch.1080p")
    assert "title" not in s.strong_signals
    assert s.strong_signals == ("date",)
    assert s.confidence < 75
    assert s.confidence <= SINGLE_SIGNAL_CAP


def test_site_plus_date_without_title_overlap_still_matches():
    # Two strong signals (site + date), no title-word overlap — unchanged 75.
    scene = SceneFingerprint(27, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ())
    s = score(scene, "ThatFetishGirl.2026-07-07.Unrelated.Clip.Name.1080p")
    assert {"date", "site"} <= set(s.strong_signals)
    assert "title" not in s.strong_signals
    assert s.confidence >= 75
