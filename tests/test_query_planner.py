from datetime import date

from scenehound.models import SceneFingerprint
from scenehound.query_planner import plan_queries

SCENE = SceneFingerprint(
    scene_id=1,
    site="That Fetish Girl",
    site_aliases=(),
    date=date(2026, 7, 7),
    title="Latex Worship Session",
    performers=("Jane Doe",),
)


def test_best_first_is_site_plus_scene_date_format():
    qs = plan_queries(SCENE)
    assert qs[0] == "That Fetish Girl 26.07.07"


def test_date_free_variants_precede_iso_fallbacks():
    qs = plan_queries(SCENE, max_queries=99)
    # performer-alone and site-alone (date-free, high recall) must come before the
    # ISO-dated fallbacks, which almost never appear in real release titles.
    assert "Jane Doe" in qs
    assert "That Fetish Girl" in qs
    assert "Jane Doe latex worship session" in qs
    assert qs.index("Jane Doe") < qs.index("That Fetish Girl 2026-07-07")
    assert qs.index("That Fetish Girl") < qs.index("Jane Doe 2026-07-07")


def test_performer_alone_fires_within_a_small_budget():
    # The retrieval bug: an undated "[Studio] Performer - Title" release is only
    # findable by a date-free query, but the rate budget often allows just ~3
    # variants. A bare performer query must be among the first three.
    scene = SceneFingerprint(
        13523, "Mom Comes First", (), date(2026, 7, 3),
        "The Medical Emergency", ("Skylar Snow",),
    )
    qs = plan_queries(scene, max_queries=3)
    assert "Skylar Snow" in qs
    # and none of the first three should carry a date term (which would AND to zero
    # against an undated title)
    assert not any("2026" in q or "26.07.03" in q for q in qs if q == "Skylar Snow")


def test_capped_and_deduplicated():
    qs = plan_queries(SCENE, max_queries=3)
    assert len(qs) == 3
    assert len(set(qs)) == 3


def test_all_performers_queried_not_just_first():
    # Regression (Empornium "Special Medicine"): Whisparr lists the male lead first
    # ("Alex Adams"), but the tracker names the scene after the female performer
    # ("Lulu Romanova"). Querying only performers[0] never retrieved it. Every
    # performer must be queried; the wrong ones just return matcher-rejected noise.
    scene = SceneFingerprint(
        13530, "Family Therapy XXX", ("Family Therapy",), date(2026, 7, 8),
        "Special Medicine", ("Alex Adams", "Lulu Romanova"),
    )
    qs = plan_queries(scene, max_queries=99)
    assert "Alex Adams" in qs
    assert "Lulu Romanova" in qs      # the non-first performer, missed before


def test_distinctive_title_queried_alone():
    # A distinctive (>=2 content token) title reliably retrieves on undated trackers
    # that glue the studio token ("[FamilyTherapy]") — where a spaced studio query
    # can't match — and regardless of which performer the tracker names it after.
    scene = SceneFingerprint(
        13530, "Family Therapy XXX", ("Family Therapy",), date(2026, 7, 8),
        "Special Medicine", ("Alex Adams", "Lulu Romanova"),
    )
    qs = plan_queries(scene, max_queries=99)
    assert "special medicine" in qs
    # and it must fire within a tight rate budget (it is THE reliable retriever here)
    assert "special medicine" in plan_queries(scene, max_queries=3)


def test_title_and_performers_precede_site_queries():
    # Site-based queries are unreliable on trackers that glue the studio token, so
    # the title/performer retrievers must come BEFORE any site-only / site+title query.
    scene = SceneFingerprint(
        13530, "Family Therapy XXX", ("Family Therapy",), date(2026, 7, 8),
        "Special Medicine", ("Alex Adams", "Lulu Romanova"),
    )
    qs = plan_queries(scene, max_queries=99)
    assert qs.index("special medicine") < qs.index("Family Therapy XXX")
    assert qs.index("Lulu Romanova") < qs.index("Family Therapy XXX")


def test_generic_one_word_title_not_queried_alone():
    # A one-content-token title is too generic to search alone; fall back to
    # performers instead of blasting the tracker with a single common word.
    scene = SceneFingerprint(9, "Some Site", (), date(2026, 1, 1), "Punished", ("Ann Jones",))
    qs = plan_queries(scene, max_queries=99)
    assert "punished" not in qs
    assert "Ann Jones" in qs


def test_no_performers_no_performer_variant():
    scene = SceneFingerprint(2, "Site", (), date(2026, 1, 1), "A Title Here", ())
    qs = plan_queries(scene)
    assert all(
        "2026-01-01" in q or "26.01.01" in q or q == "Site" or "title" in q.lower()
        for q in qs
    )


def test_site_aliases_produce_search_variants():
    # An "…XXX"/non-"…XXX" studio must be searched in BOTH spellings, because the
    # tracker only matches the literal title text. The alias appears in the same
    # site-keyed slots as the primary site (site+title, site-alone), primary first.
    scene = SceneFingerprint(
        7, "Family Therapy XXX", ("Family Therapy",), date(2026, 7, 7),
        "The Massage Lesson", ("Jane Doe",),
    )
    qs = plan_queries(scene, max_queries=99)
    assert "Family Therapy" in qs                       # bare alias, site-alone
    assert "Family Therapy the massage lesson" in qs     # alias + distinctive title words
    # primary spelling still leads its alias in every shared slot
    assert qs.index("Family Therapy XXX") < qs.index("Family Therapy")
    assert (
        qs.index("Family Therapy XXX the massage lesson")
        < qs.index("Family Therapy the massage lesson")
    )


def test_site_aliases_still_searched_at_full_budget():
    # The xxx-toggled spelling is still emitted (both spellings get searched), but
    # it is demoted below the title/performer retrievers that actually work on
    # undated glued-studio trackers — so assert reachability at full budget rather
    # than within the tight default budget.
    scene = SceneFingerprint(
        8, "Family Therapy XXX", ("Family Therapy",), date(2026, 7, 7),
        "The Massage Lesson", ("Jane Doe",),
    )
    qs = plan_queries(scene, max_queries=99)
    assert any(q.startswith("Family Therapy ") and "XXX" not in q for q in qs)
    assert "Family Therapy" in qs  # alias site-alone


def test_query_shape_and_ordering():
    # Full variant order for a simple scene: dated convention, then the date-free
    # retrievers (distinctive title, then performer), then combined/site variants.
    scene = SceneFingerprint(3, "Foo", (), date(2026, 7, 7), "Bar Baz", ("Foo",))
    assert plan_queries(scene) == (
        "Foo 26.07.07",
        "bar baz",
        "Foo",
        "Foo bar baz",
        "Foo 2026-07-07",
    )


def test_dedup_collapses_colliding_variants():
    # site == performers[0] makes the site+ISO / performer+ISO variants collide,
    # and the site+title / performer+title variants collide — exercising dedup.
    scene = SceneFingerprint(3, "Foo", (), date(2026, 7, 7), "Bar Baz", ("Foo",))
    qs = plan_queries(scene)
    assert len(qs) == len(set(qs))  # no duplicates survive
    assert qs == (
        "Foo 26.07.07",
        "bar baz",
        "Foo",
        "Foo bar baz",
        "Foo 2026-07-07",
    )
