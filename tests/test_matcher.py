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
