# Honest Titles & the Superset-Title Veto

**Date:** 2026-08-09
**Status:** Approved for planning
**Branch:** `feat/honest-titles-superset-veto`

## Purpose

An Interactive Search in Whisparr shows Scenehound's rewritten name, not the
tracker's. When one scene attracts many candidates, every row renders as the same
string — distinguishable only by size and, when the tokens differ, quality:

```
Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX.2160p
Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX.1080p
Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX.720p    ×2
Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX         ×6
```

Two distinct defects hide inside that:

1. **Undecidable**: among genuine matches, there is no way to tell a 4K rip from a
   re-encode, or one uploader's pack from another's.
2. **Concealed false positives**: the motivating search returned 10 releases, of
   which **exactly one** is the wanted scene. The other nine are *different scenes*
   — `Mommy Swallows Before School`, `Stripper Step-Mommy Swallows`,
   `Pregnant Mommy Swallows` — all scoring 100 and all wearing the wanted scene's
   name.

This spec fixes both: **Part A** puts the real tracker title in front of the human,
**Part B** stops the false positives reaching Whisparr at all.

## Constraints

- The emitted title must keep parsing to the same studio, date, quality and scene
  in Whisparr. Verified empirically, not assumed (see *Parse contract*).
- Matcher and rewriter stay pure functions with no I/O, per the standing design
  rule — accuracy remains corpus-testable.
- Automated running leans strict. A false import is expensive to identify and
  unpick; a false negative leaves the scene missing and RSS may catch it later.
- Any failure still degrades to passthrough, never to a broken search.

---

## Part A — Honest titles

### Format

`rewrite_title` appends the original tracker title, verbatim, in square brackets:

```
<canonical> [<original tracker title>]

Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX.2160p [[XevUnleashed] Xev Bellringer - Pregnant Mommy Swallows - 4K 2160p]
Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX.720p [Xev Bellringer - Mommy Swallows Before Your Date (720p)]
Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX [Stripper Step-Mommy Swallows Xev Bellringer]
```

Nothing is scrubbed and nothing is truncated. Truncation would cut exactly the tail
(`- 4K 2160p`, `{Se7enSeas}`) that separates duplicate uploads of one scene, and
scrubbing is unnecessary because the payload is provably inert.

### Parse contract

Verified against a live Whisparr via `GET /api/v3/parse` on 2026-08-09. Seven
variants of one title were compared against the bare canonical baseline
`Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX.720p`:

| Variant | Result |
|---|---|
| baseline | `studio=Xev Bellringer`, `releaseDate=2015-01-06`, `quality=WEBDL-720p`, `mapped=4622` |
| `.Dotted.Append` | identical |
| `-Dotted.Append` | identical **except** `releaseGroup: "A"` appears |
| `-Dotted.Append` repeating `720p` | identical |
| space-separated words | identical |
| ` [Bracketed Words]` | identical |
| ` [Full original with dashes and (parens)]` | identical |

Three findings follow, and they are the foundation of this part:

- **Whisparr identifies an adult scene by studio + release date.** `movieTitles` is
  empty even for the clean canonical title; `mapped` resolves on studio and date.
  The title words are not part of scene identity.
- **Everything after the quality token is inert.** `releaseTokens` stayed
  `.Mommy.Swallows` in all seven; a repeated `720p` in the payload did not disturb
  the parsed quality.
- **The dash separator is unsafe.** It manufactured `releaseGroup: "A"` out of the
  parser's own substitution token. A phantom release group can reach Whisparr's
  file naming, so the separator is a space plus square brackets.

One case remains unverified and is accepted: a payload naming a *lower* resolution
than the canonical (an original citing both a `1080p` master and a `720p` sample).
Whisparr's quality parser survived a repeated token but was not tested on a
conflicting one. If a quality misparse is ever observed in the wild, the fallback
is to strip resolution tokens from the payload only — the canonical already carries
the extracted quality, so nothing is lost.

### Decisions

- **Separator**: space + `[...]`. Not a dash (see above).
- **Scope**: search mode *and* RSS mode. RSS grabs are the unattended ones, so a
  self-documenting history entry is worth more there, not less.
- **Config**: new `naming:` section, `original_title_suffix: bool = True`, env
  `SCENEHOUND_ORIGINAL_TITLE_SUFFIX`. A separate section from `matching:`, which
  stays the accuracy knobs. Default on — an escape hatch defaulted off is an escape
  hatch nobody finds.
- `scenehound_original_title` on the Torznab item is **retained**. It is the
  machine-readable record; the suffix is the human-readable one.

### Call-site hazard

`rewrite_title` is called three times in [api.py](../../../scenehound/api.py) —
lines 148 (recorder), 156 (feed), 187 (RSS). The recorder's stored
`rewritten_title` is what [observe.py:195](../../../scenehound/observe.py#L195)
matches incoming grab webhooks against. **If the three disagree, grab correlation
silently stops working** and the UI's Grabbed/Imported ladder stalls at Matched.

Mitigation: read the flag once per request and pass the same value to all three;
assert equality of feed title and recorded title in an API test.

---

## Part B — The superset-title veto

### Diagnosis

At [matcher.py:226-235](../../../scenehound/matcher.py#L226-L235), `near_exact` asks
only *"are all the scene's identity tokens present in the candidate?"* — pure
one-directional containment. It never asks what else the candidate brought. So
`Mommy Swallows Before School` contains `Mommy Swallows`, earns `STRONG_TITLE`
(+40), and — worse — the resulting `"title" in strong` **exempts it from the
foreign-title veto** at [matcher.py:272](../../../scenehound/matcher.py#L272). That
exemption assumes a title match confirms the scene. Under containment, it doesn't.

Measured against the real matcher, the motivating scene (`Xev Bellringer`,
2015-01-06, *Mommy Swallows*). The ten returned releases reduce to eight distinct
title shapes — two are re-uploads of a shape already listed — and every one of the
eight takes the title-strong path:

| Confidence | Unexplained residual | Release | Truth |
|---:|---|---|---|
| 100 | *(none)* | `Xev Bellringer - Mommy Swallows (.mp4)` | the real one |
| 100 | `pregnant` | `[XevUnleashed] … Pregnant Mommy Swallows - 4K 2160p` | different scene |
| 100 | `pregnant` | `[Clips4Sale] Pregnant Mommy Swallows [Xev Bellringer]` | different scene |
| 100 | `pregnant` | `Xev Bellringer - Pregnant Mommy Swallows (1080p)` | different scene |
| 100 | `2` | `Xev Bellringer - Mommy Swallows 2` | different scene |
| 100 | `stripper`,`step` | `Stripper Step-Mommy Swallows Xev Bellringer` | different scene |
| 100 | `before`,`school` | `Xev Bellringer - Mommy Swallows Before School (.mp4)` | different scene |
| 100 | `before`,`your`,`date` | `Xev Bellringer - Mommy Swallows Before Your Date (720p)` | different scene |

The one true positive is the one row with an empty residual.

The existing sibling arm cannot catch this shape: it fires when a candidate *drops*
scene words. These keep every word and add more, so `coverage` is 1.0.

**Demotion does not work** and was rejected on evidence: reducing title to the
medium band still leaves `35 (site) + 35 (performer) + 25 (title) = 95`, far above
the threshold of 75, and the foreign-title veto still cannot fire at coverage 1.0.
It has to be a veto.

### Rule

> When `title` would be strong by containment **and** the candidate carries any
> unexplained identity token, return `veto="superset-title"`. The candidate names a
> longer-titled scene, not this one.

A token is **explained** when it is any of:

| Explainer | Catches | Source |
|---|---|---|
| `JUNK_TOKENS` | `xxx`, `1080p`, `mp4`, `com` | existing, unchanged |
| Scene title identity tokens | the matched title itself | existing |
| **Name n-grams** — squashed contiguous runs of each site/alias/performer's own tokens | `Jane.ONeil` → `oneil` | new |
| Inside a span matched by `extract_dates` | `26`,`07`,`07` | new |
| Inside a `[...]`, `{...}` or `(...)` segment | `[FamilyTherapy]`, `{Se7enSeas}`, `(Oculus 8K, UHD)` | new |
| `_RESIDUAL_IGNORE` — format words, residual-only, never merged into `JUNK_TOKENS` | `hdr`, `8k`, `sbs` | new (post-review) |
| After the last junk token | `grp` in `MP4-GRP` | new |

The bar is **1**: any unexplained token vetoes.

Two explainers are worth their own note.

**Name n-grams** mirror the existing `_title_ngrams`, applied to the name side
instead of the title side. For `Jane O'Neil` → tokens `[jane, o, neil]` → runs
`{jane, o, neil, janeo, oneil, janeoneil}`. This closes the accented/punctuated
name limitation flagged at
[matcher.py:162-165](../../../scenehound/matcher.py#L162-L165) without introducing
any edit-distance fallback, which that same docstring rules out for good reason.

**Date spans** rest on the principle that a token already explained by another
signal is not residual. Digits inside a parsed date were scored as the date signal;
counting them again as foreign title words scores one fact twice — the same error
the de-named foreign-title ratio already corrects for names.

### Validation

Prototyped and run against the full corpus plus the motivating case:

- **0 regressions.** All ten `expect: match` corpus rows that take the title-strong
  path go to residual 0 under this definition.
- **7 of 7** motivating false positives vetoed.
- **The true positive survives.**
- Two hand-built controls also survive: `[XevUnleashed] Xev Bellringer - Mommy
  Swallows - 4K 2160p` (legit release wearing an uploader tag) and `Xev Bellringer
  - Mommy Swallows [Bonus Scene] 1080p` (legit release with filler).

### Placement

The arm goes **after** the existing date-veto block, beside the foreign-title veto.
A candidate with both a contradicting date and a superset title therefore still
reports `date-mismatch`: the older, stronger contradiction keeps precedence and no
existing corpus row changes its veto string.

`strong` and `detail["title"]` are already populated when the arm runs. Both are
still reported on the vetoed `MatchScore` — they are the UI's trace of what the
candidate *did* agree on, which is exactly the context a human needs to judge the
veto.

### Forward compatibility

A future feature will let a veto be overridden on the Scenehound side. To make that
possible without re-deriving anything, the veto emits the evidence, not just a
label: `detail["superset_residual"]` carries the actual unexplained tokens. An
override UI can then say *"vetoed on: pregnant"* and allow that one release, rather
than forcing the rule to be loosened globally. Nothing is built for this here.

### Reach

The matcher is shared, so the veto applies to search mode, RSS mode, and the
import-completer's multipack file matching. In the last of these it strictly
reduces auto-imports — the safe direction, and consistent with the standing
all-or-nothing rule for packs.

---

## Consequences

**The motivating search will return 1 result instead of 10.** That is the intent,
but it changes how failure feels. Vetoed candidates are not returned at all, so an
over-firing veto is an *invisible* loss inside Whisparr.

The recovery path already exists: `/ui` records every candidate with its reason, so
a veto is always inspectable, and `VETO_TEXT` in
[ui.html](../../../scenehound/static/ui.html) gains a `superset-title` entry
(its fallback already renders unknown vetoes readably).

**The explainer list is the fragile part of this design, and the first cut of it
was too narrow.** The bar of 1 rests on 10 corpus rows and 10 hand-checked
candidates — one studio, one punctuation style — and review found it dropping
releases that are unambiguously the right scene. Two were repaired in the same
branch: `(...)` joined `[...]` and `{...}` as a tagged segment (nine corpus rows
already use parens), and `_RESIDUAL_IGNORE` — a set consumed *only* by
`_unexplained_residual`, deliberately not merged into `JUNK_TOKENS`, which also
feeds `identity_tokens` and would move unrelated verdicts — forgives format words
like `HDR` that junk does not list.

**Accepted regression: studios whose Whisparr title is not the release's
descriptive title.** ShopLyfter is the worked example, and it is already in the
corpus: the scene is titled `Case No. 2658794`, while the release calls itself
`ShopLyfter - 18.07.25 - Case No. 2658794 - Sakura Lin, the Rich Girl vs Two
Cocks - 1080p {Se7enSeas}`. Site, date and the full numeric title all agree, and
the veto still fires on the eight-token descriptive tail the studio adds. Every
genuine release from such a studio is lost. The same shape costs an unbracketed
filler pair (`Mommy Swallows Bonus Scene 1080p`) too.

This is a real loss, not a theoretical one, and it is **not softened by a retry**:
`api.py`'s RSS path calls the same `score()`, so RSS vetoes identically. There is
no second chance and nothing to see from inside Whisparr — the scene simply stays
missing. It also reaches the import-completer, where the all-or-nothing pack rule
means one descriptively-named file blocks an entire pack. The only place the loss
is visible is the `/ui` trace, which names the residual tokens.

It is accepted rather than fixed because the fix — scoping the residual to the
segment the matched title lives in — is unvalidated and would loosen the veto back
toward the false grabs it exists to stop. Deliberately no corpus row asserts
`no_match` for these two shapes: they *are* the right scene, and pinning them would
ratchet a known-wrong verdict in as correct. Mitigated by the `/ui` trace and the
planned override; revisit with segment scoping when there is evidence to validate
it against.

**Two brackets that do meet, on one path that does not exist yet.** Part A brackets
the *outgoing* title to Whisparr; Part B treats brackets in *incoming* tracker
titles as explained. They sit on opposite sides of the proxy and share no code
today, but the resemblance is not harmless: feed Part A's own output back into
`score()` and Part B's bracket explainer swallows the entire payload, so
`Xev.Bellringer.2015-01-06.Mommy.Swallows.XXX [Xev Bellringer - Mommy Swallows
Before School (.mp4)]` re-scores as `veto=None, confidence=100` — the veto defeated
by the thing it is supposed to catch. **There is no live path today**: all three
`score()` callers are fed either a Prowlarr title or an on-disk basename, never an
emitted one. But the already-designed veto-override feature would by construction
re-examine an emitted candidate, so the delimiter choice is load-bearing for it and
that arm must not re-score a suffixed title. Nothing is tested here, because
testing behaviour on a path that does not exist pins the wrong thing.

---

## Files

**Part A**

| File | Change |
|---|---|
| `scenehound/rewriter.py` | `rewrite_title(scene, original_title, *, include_original: bool)` |
| `scenehound/config.py` | `NamingConfig`, `original_title_suffix: bool = True`, env override |
| `scenehound/api.py` | Thread one flag value through all three call sites |

**Part B**

| File | Change |
|---|---|
| `scenehound/dates.py` | `date_spans(text) -> list[tuple[int, int]]` over the existing three regexes |
| `scenehound/normalize.py` | `name_ngrams(names) -> frozenset[str]` |
| `scenehound/matcher.py` | `_unexplained_residual()`, the veto arm, docstring entry in house style |
| `scenehound/static/ui.html` | `superset-title` in `VETO_TEXT` |
| `tests/fixtures/corpus.yaml` | 7 `no_match` + 1 `match` + 2 controls |

## Error handling

Neither part does I/O. Both are pure functions over strings, so there are no new
failure modes — only a changed verdict. The veto returns `MatchScore(0, …)` exactly
like its three siblings. A **missing** config value falls back to the `True`
default; a **malformed** one does not, and that is worth stating plainly because
the default is what makes this feature visible. `_env_bool` resolves anything it
does not recognise to `False`, so `SCENEHOUND_ORIGINAL_TITLE_SUFFIX=yep` silently
turns the suffix off, as does an empty value. In YAML the trap runs the other way:
a quoted `original_title_suffix: "false"` is a non-empty string, `bool()` of it is
`True`, and the suffix stays on; a bare `original_title_suffix:` (null) reads as
`False`. This is not a defect of this feature — the loader is character-for-
character the same as its siblings `_ui` and `_import_completer`, and diverging
here would make one flag behave unlike every other. Recorded as known behaviour;
the code is deliberately left alone. An unresolvable scene still degrades to
passthrough, unrewritten.

## Testing

The corpus is the ratchet, so it leads.

1. **Corpus rows first** — the ten new entries, through the existing
   `test_corpus.py` harness. Written before the matcher change: red, then green.
2. **Per-explainer unit tests** in `test_matcher.py` — one per row of the explainer
   table, so a future change that breaks the `{Se7enSeas}` exclusion names itself.
3. **`date_spans` and `name_ngrams`** unit tests in their own modules, including
   `Jane O'Neil → oneil` and `Mary-Jane → maryjane`.
4. **Rewriter** — suffix on and off; an original containing `]`; empty quality
   tokens.
5. **API** — the feed `<title>` carries the suffix, `scenehound_original_title` is
   still present, and the recorder's `rewritten_title` equals the feed title (the
   three-call-site guard, asserted).
6. **Parse contract** — recorded as the documented table above, not a live test. CI
   cannot reach a Whisparr instance, and a test that silently skips is worse than a
   statement of what was verified and when.

## Out of scope

- Overriding a veto from the Scenehound UI (designed for, not built).
- Distinguishing Interactive from Automatic search. Both arrive as
  `t=search&q=…` with no distinguishing parameter; only RSS is separable. The
  strict posture is therefore applied uniformly, which matches the standing
  preference for automated runs.
- Stripping resolution tokens from the Part A payload. Held as the documented
  fallback should a conflicting-resolution misparse ever be observed.
