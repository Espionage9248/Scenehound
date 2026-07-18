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


def test_site_plus_date_with_foreign_title_now_vetoes():
    # Pre-2026-07-15 this pinned "site+date, no title overlap → 75". The
    # production false grab proved the class dangerous: three residual content
    # tokens naming a different clip are contradiction, not absence.
    scene = SceneFingerprint(27, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                             "Latex Worship Session", ())
    s = score(scene, "ThatFetishGirl.2026-07-07.Unrelated.Clip.Name.1080p")
    assert s.veto == "foreign-title"
    assert s.confidence < 75


# --- foreign-title veto (contradiction, not absence) ---


def test_foreign_title_vetoes_site_date_pair():
    # Same class as the incident but via a primary-reading date collision:
    # site+date agree, yet the candidate names a different scene outright.
    s = score(
        INCIDENT_SCENE,
        "[FamilyTherapy] Alexa Chains - The Goth Latina Experience [14-07-25] [1080p]",
    )
    assert s.veto == "foreign-title"
    assert s.confidence == 0


def test_bare_site_date_release_still_matches():
    # Absence is not contradiction: zero residual tokens → site+date clears.
    s = score(INCIDENT_SCENE, "FamilyTherapy.14.07.25.XXX.1080p")
    assert s.veto is None
    assert {"date", "site"} <= set(s.strong_signals)
    assert s.confidence >= 75


def test_two_residual_filler_tokens_do_not_veto():
    # "Bonus Scene"-style filler (2 residual tokens, ratio 34.8) is
    # absence-adjacent, not a foreign title — the decorative-xxx corpus entry
    # and losslessness test depend on this staying a match.
    scene = SceneFingerprint(54, "Family Therapy", ("Family Therapy XXX",),
                             date(2026, 7, 7), "The Massage Lesson", ("Jane Doe",))
    s = score(scene, "FamilyTherapyXXX.26.07.07.Bonus.Scene.XXX.1080p")
    assert s.veto is None
    assert s.confidence >= 75
    # Repeated filler must not inflate the residual count past the gate.
    s = score(scene, "FamilyTherapyXXX.26.07.07.Bonus.Scene.Bonus.XXX.1080p")
    assert s.veto is None
    assert s.confidence >= 75


def test_fuzzy_title_overlap_defuses_foreign_veto():
    # Partial title overlap (ratio 76.5) corroborates: not a foreign title.
    s = score(SCENE, "ThatFetishGirl.2026-07-07.Latex.Worship.Compilation.1080p")
    assert s.veto is None
    assert s.confidence >= 75


def test_generic_scene_title_skips_foreign_veto():
    # A 1-content-token scene title can't establish contradiction.
    scene = SceneFingerprint(33, "That Fetish Girl", (), date(2026, 7, 7), "Casting", ())
    s = score(scene, "ThatFetishGirl.2026-07-07.Totally.Different.Words.1080p")
    assert s.veto is None
    assert s.confidence >= 75


# --- date-skew forgiveness (uploader-stamped dates a few days off) ---

FORGIVE_SCENE = SceneFingerprint(
    scene_id=20,
    site="Household Fantasy",
    site_aliases=(),
    date=date(2026, 7, 7),
    title="Big Titty Step-Sistinder Match",
    performers=("Zarina Noir",),
)


def test_skewed_date_forgiven_with_two_strong_signals():
    # Production mismatch 2026-07-13: uploader stamped 07-05 for the 07-07 scene.
    s = score(
        FORGIVE_SCENE,
        "[ScottStark-HouseholdFantasy] Zarina Noir - "
        "Big Titty Step Sister Tinder Match (2026-07-05) [1080p]",
    )
    assert s.veto is None
    assert {"site", "performer"} <= set(s.strong_signals)
    assert "date" not in s.strong_signals
    assert s.detail["date_skew_days"] == 2.0
    assert s.confidence >= 75


def test_forgiven_date_contributes_no_points():
    # Deliberately below 100 total (site+performer, no title hit) so the
    # min(100, ...) clamp can't mask metadata leaking into the sum.
    dated = score(SCENE, "ThatFetishGirl.2026-07-09.Jane.Doe.Solo.Clip")
    undated = score(SCENE, "ThatFetishGirl.Jane.Doe.Solo.Clip")
    assert dated.veto is None
    assert dated.confidence == undated.confidence < 100


def test_skewed_date_beyond_window_still_vetoes():
    # 4 days off > default window of 3 — hard contradiction even with strong evidence.
    s = score(SCENE, "ThatFetishGirl.2026-07-11.Latex.Worship.Session.Jane.Doe")
    assert s.veto == "date-mismatch"
    assert s.confidence == 0


def test_skewed_date_with_one_strong_signal_vetoes():
    # Site alone can't carry a contradicted date: forgiveness needs two strong signals.
    s = score(SCENE, "ThatFetishGirl.2026-07-09.Something.Unrelated")
    assert s.veto == "date-mismatch"
    assert s.confidence == 0


def test_skew_forgiven_at_exact_window_boundary():
    s = score(SCENE, "ThatFetishGirl.2026-07-10.Latex.Worship.Session.Jane.Doe",
              date_skew_days=3)
    assert s.veto is None
    assert s.detail["date_skew_days"] == 3.0


def test_skew_window_one_restores_hard_veto():
    s = score(SCENE, "ThatFetishGirl.2026-07-09.Latex.Worship.Session.Jane.Doe",
              date_skew_days=1)
    assert s.veto == "date-mismatch"


# --- secondary-reading date demotion (2026-07-15 production false grab) ---

INCIDENT_SCENE = SceneFingerprint(
    scene_id=30,
    site="Family Therapy XXX",
    site_aliases=("Family Therapy",),
    date=date(2014, 7, 25),
    title="Slut Training Day",
    performers=(),
)
INCIDENT_RELEASE = (
    "[FamilyTherapy] Alexa Chains - The Goth Latina Experience [26-07-14] [1080p]"
)


def test_incident_secondary_reading_date_is_not_strong():
    # Production false grab 2026-07-15: [26-07-14] is 2026-07-14 in the
    # uploader's yy-mm-dd; the dd-mm-yy alternate (2014-07-26) sat 1 day from
    # the scene and fabricated a strong date next to the site hit (40+35=75).
    # Since the 2026-07-19 ShopLyfter grab, a single-signal candidate rescued
    # only by a misreading is a hard veto, not merely capped: the primary
    # reading contradicts and site alone can't forgive it.
    s = score(INCIDENT_SCENE, INCIDENT_RELEASE)
    assert s.veto == "date-mismatch"
    assert s.confidence == 0


def test_secondary_reading_traced_not_scored():
    s = score(INCIDENT_SCENE, INCIDENT_RELEASE)
    assert s.detail["date_secondary_reading"] == 1.0
    assert s.detail["date"] == 0.0  # zero points from the date


def test_secondary_reading_forgives_veto_for_ddmmyy_uploaders():
    # Honest dd.mm.yy uploader: the dominant yy.mm.dd reading of [14-07-26]
    # contradicts (2014-07-26), the alternate matches exactly (2026-07-14) →
    # no veto; the match is carried by site+performer+title.
    scene = SceneFingerprint(31, "Some Site", (), date(2026, 7, 14),
                             "Latex Worship Session", ("Jane Doe",))
    s = score(scene, "[SomeSite] Jane Doe - Latex Worship Session [14-07-26] [1080p]")
    assert s.veto is None
    assert "date" not in s.strong_signals
    assert {"site", "performer"} <= set(s.strong_signals)
    assert s.detail["date_secondary_reading"] == 1.0
    assert s.confidence >= 75


def test_secondary_reading_contributes_no_points():
    # Same construction as test_forgiven_date_contributes_no_points: keep the
    # total below the min(100, …) clamp so a leaked point would show.
    scene = SceneFingerprint(31, "Some Site", (), date(2026, 7, 14),
                             "Latex Worship Session", ("Jane Doe",))
    dated = score(scene, "[SomeSite] Jane Doe - Solo Clip [14-07-26]")
    undated = score(scene, "[SomeSite] Jane Doe - Solo Clip")
    assert dated.veto is None
    assert dated.confidence == undated.confidence < 100


# --- foreign-title veto generalization: any title-less strong set ---
# 2026-07-15 production false grab #3 (GloryholeSecrets): {date, performer} both
# honest (Sydney Paige in both scenes; primary yyyy-mm-dd date is ±1) but the
# candidate names a different scene AND a different studio. The {site,date}-only
# arm did not fire. See docs/superpowers/specs/2026-07-15-foreign-title-veto-generalization-design.md

GLORYHOLE_SCENE = SceneFingerprint(
    scene_id=40,
    site="Shoplyfter Mylf",
    site_aliases=("Shoplyfter",),
    date=date(2024, 6, 15),
    title="Case No. 8002506 Bending the Right Way",
    performers=("Sydney Paige",),
)
GLORYHOLE_RELEASE = (
    "[GloryholeSecrets] Sydney Paige's First Glory Hole - Sydney Paige (2024-06-14) [2160p]"
)


def test_incident_date_performer_foreign_title_vetoes():
    # The candidate agrees on date (June 14, ±1 of June 15) and performer (Sydney
    # Paige) but its title names a different scene: 5 distinct residual tokens at
    # ratio 36.4 (< 40). Killed by the generalized arm.
    s = score(GLORYHOLE_SCENE, GLORYHOLE_RELEASE)
    assert s.veto == "foreign-title"
    assert s.strong_signals == ("date", "performer")
    assert s.confidence == 0


def test_date_performer_absence_still_matches():
    # Absence is not contradiction: a bare performer + date with no foreign title
    # tokens (residual 0) still clears on {date, performer}.
    s = score(SCENE, "Jane Doe (2026-07-07) 1080p")
    assert s.veto is None
    assert {"date", "performer"} <= set(s.strong_signals)
    assert s.confidence >= 75


def test_date_performer_legit_recall_matches():
    # The sole in-corpus title-less {date, performer} match: both performers +
    # date + partial title (ratio 76.5, residual 0). Must survive the new arm.
    s = score(SCENE, "Jane Doe & Mary Major - Latex Worship (07.07.2026) 2160p")
    assert s.veto is None
    assert s.confidence >= 75


def test_date_performer_near_exact_title_is_exempt():
    # A near-exact title promotes 'title' into strong → {date, performer, title};
    # "title" in strong means the gate is skipped and the scene is confirmed.
    s = score(SCENE, "Jane Doe - Latex Worship Session (2026-07-07) 1080p")
    assert s.veto is None
    assert "title" in s.strong_signals
    assert s.confidence >= 75


def test_site_date_performer_foreign_title_now_vetoes():
    # Three attributes agree (site + date + performer) but the title is foreign
    # (ratio 27.0, ≥3 residual). The 3-signal title-less set arms too.
    s = score(SCENE, "ThatFetishGirl Jane Doe - Wildly Different Bondage Clip (2026-07-07) 1080p")
    assert s.veto == "foreign-title"
    assert s.confidence == 0


def test_site_date_performer_corroborating_title_matches():
    # Same three attributes, but the title corroborates (ratio 76.5 ≥ 40): match.
    s = score(SCENE, "ThatFetishGirl Jane Doe - Latex Worship (2026-07-07) 1080p")
    assert s.veto is None
    assert s.confidence >= 75


def test_site_performer_corroborating_title_still_matches():
    # Generalization must not regress the existing {site, performer} path: the
    # Zarina Noir shape (residual 3 but ratio 80 ≥ 40) still matches.
    scene = SceneFingerprint(41, "Household Fantasy", (), date(2026, 7, 7),
                             "Big Titty Step-Sistinder Match", ("Zarina Noir",))
    s = score(scene, "[ScottStark-HouseholdFantasy] Zarina Noir - Big Titty Step Sister Tinder Match [1080p]")
    assert s.veto is None
    assert {"site", "performer"} <= set(s.strong_signals)
    assert s.confidence >= 75


# --- 2026-07-19 production false grab: numeric titles + misread-date rescue ---
# ShopLyfter stamps yy.mm.dd; "26.07.18" is 2026-07-18. Its dd.mm.yy misreading
# (2018-07-26) sat 1 day from a wanted 2018-07-25 scene and forgave the veto,
# while the scene title "Case No. 2658794" lost its number to content_tokens and
# strong-matched "Case No. 8004900" on the boilerplate {case, no} alone (35+40=75).

SHOPLYFTER_SCENE = SceneFingerprint(
    scene_id=50,
    site="Shoplyfter",
    site_aliases=(),
    date=date(2018, 7, 25),
    title="Case No. 2658794",
    performers=(),
)
SHOPLYFTER_RELEASE = (
    "ShopLyfter - 26.07.18 - Case No. 8004900 - "
    "Sakura Lin, the Rich Girl vs Two Cocks - 1080p {Se7enSeas}"
)


def test_numeric_title_with_wrong_number_is_not_strong():
    # The case number is the only identifying part of the title; boilerplate
    # {case, no} containment must not fabricate a strong title signal.
    s = score(SHOPLYFTER_SCENE, "ShopLyfter - Case No. 8004900 - 1080p")
    assert "title" not in s.strong_signals
    assert s.confidence < 75


def test_numeric_title_with_matching_number_still_strong():
    s = score(SHOPLYFTER_SCENE, "ShopLyfter - Case No. 2658794 - 1080p")
    assert "title" in s.strong_signals
    assert s.confidence >= 75


def test_incident_shoplyfter_release_vetoed():
    # Full incident release: with the boilerplate title demoted, only the site
    # signal stands, and a lone strong signal cannot forgive the contradicting
    # primary date (2026-07-18) via its dd.mm.yy misreading.
    s = score(SHOPLYFTER_SCENE, SHOPLYFTER_RELEASE)
    assert s.veto == "date-mismatch"
    assert s.confidence == 0


def test_secondary_rescue_with_two_strong_signals_still_forgiven():
    # The two-strong-signal rule already governs skew forgiveness; secondary-
    # reading rescue uses the same bar. Site+performer keeps the honest
    # dd.mm.yy-uploader shape working even without a title in the release.
    scene = SceneFingerprint(31, "Some Site", (), date(2026, 7, 14),
                             "Latex Worship Session", ("Jane Doe",))
    s = score(scene, "[SomeSite] Jane Doe - Solo Clip [14-07-26]")
    assert s.veto is None
    assert s.detail["date_secondary_reading"] == 1.0
    assert s.confidence < 75  # capped: no title corroboration, date not strong
