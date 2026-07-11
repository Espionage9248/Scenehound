from datetime import date

from scenehound.models import SceneFingerprint
from scenehound.rewriter import extract_quality_tokens, rewrite_title

SCENE = SceneFingerprint(
    scene_id=1,
    site="That Fetish Girl",
    site_aliases=(),
    date=date(2026, 7, 7),
    title="Some Great Scene",
    performers=("Jane Doe",),
)


def test_extracts_and_canonicalizes_resolutions():
    assert extract_quality_tokens("blah 1080p blah") == ("1080p",)
    assert extract_quality_tokens("blah [1080] blah") == ("1080p",)
    assert extract_quality_tokens("something 4k uhd") == ("2160p",)


def test_extracts_source_and_codec():
    assert extract_quality_tokens("t 1080p WEB-DL x265") == ("1080p", "WEB-DL", "x265")
    assert extract_quality_tokens("t hevc webrip") == ("WEBRip", "x265")


def test_no_tokens_means_empty_never_fabricated():
    assert extract_quality_tokens("Sitename Jane Doe hot scene") == ()


def test_date_fragments_not_mistaken_for_resolution():
    # 26.07.05 must not produce quality tokens
    assert extract_quality_tokens("Site.26.07.05.Title") == ()


def test_bare_numbers_never_fabricate_resolution():
    # A standalone 1080/720 (tip amount, like count, etc.) is ambiguous and must
    # NOT be recognized as a resolution — never fabricate quality tokens.
    assert extract_quality_tokens("Model got a $1080 tip from fans tonight") == ()
    assert extract_quality_tokens("Best scene ever 720 likes so far") == ()


def test_marker_bearing_resolutions_still_recognized():
    # Guard against over-removal: explicit markers (brackets, p suffix) still match.
    assert extract_quality_tokens("clip [1080]") == ("1080p",)
    assert extract_quality_tokens("clip 720p") == ("720p",)


def test_rewrite_full():
    out = rewrite_title(SCENE, "messy jane doe 07/07/26 [1080] x264")
    assert out == "That.Fetish.Girl.2026-07-07.Some.Great.Scene.XXX.1080p.x264"


def test_rewrite_without_quality():
    out = rewrite_title(SCENE, "messy jane doe title only")
    assert out == "That.Fetish.Girl.2026-07-07.Some.Great.Scene.XXX"


def test_rewrite_sanitizes_weird_chars():
    scene = SceneFingerprint(2, "Site!", (), date(2026, 1, 2), "What?! A #Title", ())
    assert rewrite_title(scene, "x") == "Site.2026-01-02.What.A.Title.XXX"
