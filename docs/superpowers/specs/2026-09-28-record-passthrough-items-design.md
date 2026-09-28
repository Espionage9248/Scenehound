# Record passed-through items

**Date:** 2026-09-28
**Status:** Approved for planning

## Problem

The unmatched-grabs strip in `/ui` fills with Whisparr grabs that Scenehound
never rewrote. Scenehound returns them, but it never records them, so
`SessionStore.record_grab` has no row to correlate them to. Two code paths
return items without recording them:

1. **RSS.** `_rss_mode` (`api.py`) adds every candidate to the feed, but
   `rec.rss_summary(...)` records only the rewritten ones. Whisparr identifies
   a scene by studio and release date alone, so it can parse and auto-grab a raw
   tracker title that Scenehound passed through.
2. **Search-mode passthrough.** `_passthrough` is reached via the
   `unparseable-query`, `scene-unresolved` and `no-index` fallbacks, and it
   records only a count (`rec.passthrough_results(n)`). Every Whisparr search
   click sends `<studio> yy.mm.dd` as well as `<studio> dd.mm.yyyy`. The first
   form is unparseable, so this path runs on every click.

The diagnosis is recorded in the project memory for the RSS passthrough grabs.
It's not a correlation failure: there was never a row to correlate to.

## Decision

Keep passing non-matching items through to Whisparr unchanged, and **also
record them**, so that a Whisparr grab of one correlates to a session. Stopping
passthrough was rejected: every item Scenehound fails to match would then be
silently lost on RSS, including the misses accepted with the superset-title
veto.

Settled during brainstorming:

- **Storage: the latest poll holds the full list.** When an RSS poll is
  recorded, the store removes those items' passed-through rows from the same
  slug's older RSS sessions. Each item is listed once, under the latest poll
  that returned it.
- **Search-passthrough results are scored** against the wanted index, but only
  when the UI is enabled. The response stays byte-identical.
- **Secret redaction is fixed in the same change**, because this feature would
  multiply an existing leak (see [Redaction](#redaction)).
- **Release:** no version bump in the PR. After merge and the live check, a
  separate `chore: release 0.7.0` commit and tag, following the v0.6.1 pattern.

## Evidence

Measured 2026-09-28 against the live instance (`192.168.1.5:9797`, wanted index
6562 scenes) and a `/ui/api/sessions` dump taken at 18:44 the same day.

| Measure | Value |
|---|---|
| Live `/ui/api/sessions` | 38 KB: 50 sessions (34 RSS, 8 search, 8 passthrough), 24 stored candidates |
| Items per RSS poll | 25 in all 42 RSS sessions of the 18:44 dump, both slugs |
| RSS cadence | every 30.5 min per slug, both slugs together; 21 polls per slug fill about 10 h of the ring |
| Stored candidate | about 460–620 B of JSON (the guid URL is most of it) |
| Empornium churn | the 25 feed items span 5.8 h of `pubDate`: about 2.1 new items per 30-min poll |
| HappyFappy churn | the 25 feed items span 87.5 h of `pubDate`: about 0.1 new items per 30-min poll |
| Measured churn, 20:02 → 20:32 | Empornium: 3 new, 22 carried over, 3 scrolled off. HappyFappy: 0 new, 25 carried over. The measurement compares numeric torrent ids from two live feed fetches. |
| Search passthroughs in the 18:44 dump | 4, all `familytherapyxxx 26.07.26`, all 0 results |

Projected ring contents after this change:

| Storage choice | Stored passed-through rows | State file and `/ui` poll |
|---|---|---|
| Full list in every session | about 1,050 | about 680 KB |
| **Latest poll holds all (chosen)** | about 95 distinct items | about 60 KB |

## Design

### 1. Data model (`scenehound/observe.py`)

`CandidateTrace` changes:

```python
scene_id: int | None             # was int; None = no wanted scene was a candidate
...
residual: tuple[str, ...] = ()
passed_through: bool = False     # returned to Whisparr unmodified
scene: SceneRef | None = None    # closest wanted scene; passed-through rows only
```

- `scene_id` is `None` when no wanted scene shared a date bucket or name token
  with the title, or when there was no index. Rows scored the way they are today
  always carry a real id.
- `matched` keeps its meaning: Scenehound matched the item **and rewrote it**.
  It is always `False` on a passed-through row. That includes a search
  passthrough result whose confidence clears the threshold; the UI derives
  "would have matched" from `confidence >= threshold` and `passed_through`.
- `scene` is stored per row, because the sessions these rows live in have no
  scene list: RSS sessions never have one, and nor do search-passthrough
  sessions.

`Outcome` gains `passed_through: int = 0`: the number of items returned
unchanged, counted at capture time. The UI needs it to tell rows that moved to a
later poll apart from rows that were never recorded. Every RSS session stored
before this change has a count of 0 and no rows, and must not claim its items
are "listed under later polls".

Decoding (`_candidate`, `_outcome`) reads every new field with a default:
`scene_id` → `None`, `passed_through` → `False`, `scene` → `None` (decoded with
the existing `_scene_ref` when present), `Outcome.passed_through` → `0`. State
files written by v0.6.1 load unchanged. A file written by this build and read
by v0.6.1 also loads: the old decoder ignores the unknown keys, and
passed-through rows appear as ordinary unmatched candidates.

### Redaction

On both live trackers (`www.empornium.sx`, `www.happyfappy.net`) the guid is
the download URL `torrents.php?action=download&id=…&authkey=…&torrent_pass=…`.
`_SECRET_PARAM` redacts only `apikey|api_key|passkey|token`. Both tracker
secrets are therefore stored unredacted in `ui-sessions.json` today and served
by `/ui/api/sessions`. This feature would add about 50 guids per 30-minute poll.

- `_SECRET_PARAM` gains `authkey` and `torrent_pass`.
- On load, `_candidate` re-sanitizes `guid` and `_outcome` re-sanitizes every
  non-empty `grabbed_guid` with the same `_sanitize`, so correlation keys still
  agree.
- `load()` sets `_dirty = True` when `_sanitize(text) != text` for the file's
  raw text. The next 10-second flush then rewrites the file without the secrets.
  A file that was already clean stays unwritten, as it is today.
- Identity survives redaction because `id=` stays in the guid.
- README: the Web UI section currently says the file "never holds URLs or API
  keys". Replace that with an accurate statement: it holds each release's
  tracker link, with keys and passkeys redacted.

### 2. Capture (`scenehound/api.py`)

A new helper picks the scene for one title:

```python
def _best_scene(index, title, threshold, skew) -> tuple[SceneFingerprint, MatchScore] | None
```

- If any candidate scene clears the threshold, it returns the highest
  confidence, and the first scene on a tie. **That is exactly today's RSS
  rewrite rule**, and a test pins it.
- Otherwise it returns the **closest** scene. That is the one with the highest
  confidence, then the most strong signals (vetoes set confidence to 0, so this
  separates a vetoed near-miss from an unrelated scene), then the first.
- It returns `None` when `index.candidates_for_title(title)` is empty.

`_rss_mode`:

- For each candidate, call `_best_scene` when the index is loaded.
- At or above the threshold: rewrite, log and record exactly as today.
- Otherwise: collect `(candidate, scene, score)`, or `(candidate, None, None)`
  when there is no index or no candidate scene.
- After the loop, call `rec.passed_through(rows)` and then
  `rec.rss_summary(len(candidates), rss_matched)`.
- When the index is not loaded, also call `rec.note("wanted list not loaded —
  items passed through unscored")`.
- The feed entries are built exactly as today.

`_passthrough`:

- After the Prowlarr fetch, call `rec.passthrough_results(len(results))`
  unchanged.
- Then, **only when `state.store is not None`**, score each result with
  `_best_scene`. With no index, every row is `(c, None, None)`.
- Record the rows with `rec.passed_through(rows)`.
- This block runs inside a `try/except Exception` that logs and skips
  recording. Scoring is new work on this path, and observability must never
  break a search that works today.
- The feed is still built from the raw `results`.
- The rate-deferred branch has no results and records nothing new.

`Recorder` (observe.py):

- A new method, `passed_through(items)`, which is `_shielded`. It takes
  `(ReleaseCandidate, SceneFingerprint | None, MatchScore | None)` tuples and
  appends one `CandidateTrace` per item with:
  - `passed_through=True`, `matched=False`, `rewritten_title=None`
  - a sanitized guid
  - `scene_id`, `scene` (`SceneRef.from_scene`), `confidence`,
    `strong_signals`, `veto`, `detail` and `residual` taken from the score, or
    `None`/`0`/`()`/`{}` when there is no score
  - It also adds `len(items)` to the count that `commit()` writes into
    `Outcome.passed_through`.
- `rss_summary` computes `_rewritten` from its own `matched` argument, not from
  `len(self._cands)`, because `_cands` now also holds passed-through rows.
- `commit()` needs no other change. Passed-through rows are non-matched, so the
  existing cap (`max_candidates`, matched first) places them after the matched
  rows. RSS stores at most 25 plus the rewritten rows, and a search passthrough
  stores one Prowlarr result page. Both are well under the 200 cap in practice,
  and the cap and `dropped_candidates` still apply if a page is ever larger.
- `NullRecorder` gains a no-op `passed_through`.

### 3. Pruning and correlation (`SessionStore`)

`add(session)` appends the new session first. Then, if `session.kind == "rss"`,
it prunes:

1. Collect the non-empty guids of the new session's passed-through rows. If
   there are none, stop.
2. For every **older** session in the ring with the same `slug` and
   `kind == "rss"`, drop each passed-through row whose guid is in that set,
   **unless** one of that session's grab records points at the guid
   (`grabbed_guid`).
3. Replace each trimmed session with `dataclasses.replace(s, candidates=kept)`.
   The mutable `Outcome` is carried over by reference, so grabs stamped earlier
   stay attached. Iterate over indices, not over the deque, while replacing.

Pruning never touches the following:

- rewritten rows
- search sessions
- search-passthrough sessions
- other slugs
- `dropped_candidates`
- `Outcome.passed_through`

Because the append happens first, a pruning failure (logged by the shield)
leaves the new session in place, and costs at worst some duplicate rows. The
newest RSS session of each slug always lists the whole current feed, so any
item Whisparr can grab from that feed correlates, however long it has sat in the
feed.

**Correlation logic doesn't change.** A passed-through row's `title` is
byte-identical to the `<title>` Whisparr received (`torznab._sub` writes it
verbatim), so the existing exact-title rule in `_correlates` matches it. The
newest-first walk lands on the latest poll that listed it. The INFO
`grab correlated` log line gains `passed_through=<bool>`, true when any
matching row is passed through. The unmatched strip stops receiving these
grabs by construction. Its existing entries do not correlate retroactively;
they roll off the 20-entry cap.

Known and accepted: the webhook (`import_api.py`) never reads
`release.indexer`. A grab from a direct-Prowlarr indexer of a release with the
same title would correlate to Scenehound's row. That is harmless today, because
every live Whisparr indexer goes through Scenehound (`Empornium (Scenehound)`,
`HappyFappy (Scenehound)`).

### 4. UI (`scenehound/static/ui.html`)

- **Candidates table:** filter out passed-through rows. RSS cards look as they
  do today unless something was rewritten.
- **New collapsed block,** placed after Candidates and closed by default:
  `<details>` with the summary "Passed through — not rewritten (N)".
  - **RSS cards only:** the summary gets "· M more listed under later polls",
    where M = `outcome.passed_through − N` and M > 0. Search-passthrough cards
    never show this line: only RSS rows are pruned, and a search card's missing
    rows can only be ones the cap dropped, which the Candidates heading already
    reports.
  - When N is 0 and M > 0, show a plain line, not an expandable block:
    "Passed through unchanged: all M are listed under later polls."
  - When a row inside is grabbed, the summary carries that row's grab badge, so
    the grab stays visible while the block is collapsed.
- **Rows** use the Candidates columns (title plus row grab badge, size,
  seeders, confidence bar). The "why" text:
  - opens with "Closest wanted scene: *Studio — Title (date)*" from `c.scene`,
    or "No wanted scene shares a date or name with this release" when there is
    none
  - continues with the existing veto and signal lines
  - ends with "N < threshold T → not rewritten, passed through unchanged".
    For a row at or above the threshold (search passthrough only) it ends
    instead with "N ≥ threshold T → would have matched, but this query path
    returns results unchanged", in the warning colour.

  When the index was missing, the session's fallback text (search) or note
  (RSS) states that nothing was scored. The row's "no wanted scene" line is
  accepted as slightly imprecise during that startup window.
- **Header pills:** for `rss` and `passthrough` cards, the kind pill ("RSS: 0
  rewritten of 25", "Passthrough (N)") stays next to any grab pills; today a
  grab hides it. A card must never suggest that Scenehound rewrote something it
  only passed through. Search cards are unchanged.
- **Outcome ladder:**
  - The RSS line gains ", N passed through unchanged" when
    `outcome.passed_through` is non-zero.
  - A grab line whose `grabbed_guid` is a passed-through row adds "— passed
    through unchanged (Scenehound's best: <confidence>[, <veto>])".
- **Open state:** `render()` keys open `<details>` by a `data-key` attribute,
  `"<sid>"` for cards and `"<sid>-pt"` for the nested block, in place of
  `data-sid`. Without this, an open nested block would close on every 4-second
  poll.
- **Unmatched strip copy:** "not correlated to a recorded search" becomes "not
  correlated to any recorded search or RSS poll".

README Web UI section: one sentence under **Candidates** saying that items
passed through unchanged (on RSS and on unparseable searches) are listed in a
collapsed block with Scenehound's closest wanted scene, and are badged when
Whisparr grabs them. Also the redaction sentence from [Redaction](#redaction).

## Error handling

- Everything new in `observe.py` is `_shielded` or runs inside a shielded
  method.
- The only new work on the search path is the recording block in
  `_passthrough`, which has its own `try/except`.
- The RSS scoring loop already runs in the hot path. The only thing it adds is
  tracking of the closest scene, which introduces no new operation that can
  fail.
- A pruning failure costs duplicate rows, never a lost session.

## Testing

TDD, extending `tests/test_observe.py` and `tests/test_api.py` (399 tests
today).

**observe:**

- An old-shaped candidate dict loads, with `scene_id` missing decoding to
  `None`, `passed_through` to `False` and `scene` to `None`. An old outcome
  decodes `passed_through` to `0`.
- `passed_through`, `scene` and `Outcome.passed_through` round-trip through
  save and load.
- `authkey` and `torrent_pass` are redacted.
- `load()` re-sanitizes `guid` and `grabbed_guid`, keeps them agreeing (a grab
  after load still correlates and badges), and marks the store dirty so the
  file is rewritten. A clean file does not.
- Pruning:
  - same slug and RSS kind only
  - a grabbed row is kept
  - rewritten rows are kept
  - other slugs, search sessions and passthrough sessions are untouched
  - an empty guid never prunes
  - `Outcome` identity is preserved (a grab stamped before pruning survives)
  - a failure during pruning still leaves the new session in the ring
- `record_grab` of a passed-through title correlates to the newest session
  listing it and leaves `unmatched_grabs` empty.
- `rss_summary` counts only rewritten rows when passed-through rows exist.
- `NullRecorder` accepts `passed_through`.

**api:**

- RSS records below-threshold items with the closest scene and its score.
- An RSS item with no candidate scene records `scene_id=None`.
- RSS with no index records every item unscored and adds the note.
- The rewrite choice on a confidence tie is unchanged: pin the first-wins scene.
- The closest-scene tie-break prefers more strong signals at equal confidence.
- Search passthrough is scored when a store is present, and records nothing
  and scores nothing when it is not.
- A passthrough result at or above the threshold is recorded with
  `passed_through=True` and `matched=False`, and the feed is unchanged.
- A scoring exception in `_passthrough` still returns the full feed.
- Feeds are byte-identical with the UI on and off for RSS and all three
  passthrough fallbacks, extending
  `test_no_store_means_no_capture_and_identical_bytes`.

**UI:** a headless `chrome-headless-shell --dump-dom` render against a stub
store, the method used for PRs #19 and #22. Isolate the rendered `#sessions`
subtree before counting matches, because the dump also contains the script's
template literals. Check that:

- the block is collapsed by default
- the summary badge shows when a grabbed row is hidden inside the block
- the "listed under later polls" line appears for pruned sessions and not for
  pre-feature sessions
- the kind pill stays next to a grab pill
- the open state survives a re-render

**Live check after deploy:**

- New Whisparr RSS grabs of raw titles appear as grabs on passed-through rows
  in RSS sessions, not in the unmatched strip.
- `/ui/api/sessions` stays in the tens of KB.
- The restarted instance's `ui-sessions.json` no longer contains an unredacted
  `authkey=` or `torrent_pass=`.

## Files

| File | Change |
|---|---|
| `scenehound/observe.py` | `CandidateTrace` and `Outcome` fields, decoding, redaction, `Recorder.passed_through`, `rss_summary` count, `SessionStore.add` pruning, grab log field, `NullRecorder` no-op |
| `scenehound/api.py` | `_best_scene`; `_rss_mode` and `_passthrough` recording |
| `scenehound/static/ui.html` | Passed-through block, why text, pills, ladder, open-state key, strip copy |
| `README.md` | Web UI section: passed-through sentence and corrected file-contents sentence |
| `tests/test_observe.py`, `tests/test_api.py` | As listed under [Testing](#testing) |

## Out of scope

- **Accepting `yy.mm.dd` in `parse_query_term`.** It would move those clicks
  from passthrough into search mode, but it doubles the query cost of every
  click against the rate bucket (burst 4, refill 15 s). This is a separate
  decision for the user. The new "would have matched" rows will show how often
  it matters.
- **Rewriting a search-passthrough result that clears the threshold.** That
  changes behaviour, and it contradicts the decision to pass items through
  unchanged.
- **Filtering the webhook by `release.indexer`.** It isn't needed while every
  live indexer goes through Scenehound.
- **Recording Whisparr's movie id from the grab webhook.** This would let the UI
  show "Whisparr grabbed it for scene X" next to Scenehound's closest scene,
  which is the comparison that sorts a grab into a Scenehound recall miss or a
  Whisparr false grab. It's a possible follow-up.
- **RSS sessions filling most of the ring** (42 of 50 slots in the 18:44 dump),
  which ages search sessions out after about 10 hours. This is a pre-existing
  behaviour.
- **Pruning rewritten RSS rows.** These repeat every poll as well, but they're
  rare (0 in all 42 sessions of the dump), and their count drives the RSS pill.
