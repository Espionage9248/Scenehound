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


def test_variants_cover_iso_site_alone_performer_and_title():
    qs = plan_queries(SCENE)
    assert "That Fetish Girl 2026-07-07" in qs
    assert "That Fetish Girl" in qs
    assert "Jane Doe 2026-07-07" in qs


def test_capped_and_deduplicated():
    qs = plan_queries(SCENE, max_queries=3)
    assert len(qs) == 3
    assert len(set(qs)) == 3


def test_no_performers_no_performer_variant():
    scene = SceneFingerprint(2, "Site", (), date(2026, 1, 1), "A Title Here", ())
    qs = plan_queries(scene)
    assert all(
        "2026-01-01" in q or "26.01.01" in q or q == "Site" or "title" in q.lower()
        for q in qs
    )
