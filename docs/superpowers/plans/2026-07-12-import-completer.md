# Import-Completer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in, webhook-driven subsystem to Scenehound that auto-triggers Whisparr's held "matched to movie by ID — Manual Import required" imports, isolated from the Torznab search path and disabled by default.

**Architecture:** The webhook is a *doorbell* — it authenticates, 200s, and wakes a background sweep. All authoritative state comes from Whisparr's queue API, so webhook payload drift is irrelevant and webhook/reconcile/startup collapse into one idempotent sweep. Pure decision functions (gate, phase-1 plan, phase-2 pack match) are separated from the stateful `ImportCompleter` service that performs I/O. Phase 1 (single-file by-ID) ships first; phase 2 (multipack, all-or-nothing) is behind an independent flag.

**Tech Stack:** Python 3.12, FastAPI, httpx (`MockTransport` in tests), pytest (`asyncio_mode = "auto"`), rapidfuzz (via existing matcher), PyYAML.

## Global Constraints

- Python 3.12; run tests with `.venv/bin/python3.12 -m pytest -q`.
- `asyncio_mode = "auto"` — write async tests as bare `async def test_...`, no marker.
- Frozen dataclasses for all config and parsed-record types (house style: see `models.py`, `config.py`).
- Client parsing/response shaping stays out of the client where practical: new `WhisparrClient` methods return **raw JSON** (`list[dict]` / `dict`); the import-completer module owns typed parsing. (`fetch_wanted` is pre-existing and unchanged.)
- `importMode` is always `"copy"`, never `"move"`.
- Disabled-by-default is **structural**: when `import_completer.enabled` is false, no route is registered and no background task is created.
- Dry-run is a **hard property**: a dry-run sweep performs zero non-GET HTTP calls.
- Reuse `WantedIndex`, `matcher.score`, `normalize` for phase-2 matching — do not reimplement matching.
- Every new module gets a module docstring; follow the terse, rationale-carrying comment style of `matcher.py` / `wanted_index.py`.
- Env override style: explicit `env.get("SCENEHOUND_IMPORT_*", <yaml-or-default>)`, mirroring `config.py`.
- Spec: `docs/superpowers/specs/2026-07-12-import-completer-design.md` (authoritative; this plan implements it).

## File Structure

- `scripts/probe_whisparr.sh` — **create.** One-off probe script the user runs against the live Whisparr to capture fixtures. Not imported by the app.
- `tests/fixtures/whisparr_queue_sample.json` — **create (from probe).** Sanitized `/api/v3/queue` capture with a held by-ID item.
- `tests/fixtures/whisparr_manualimport_sample.json` — **create (from probe).** Sanitized `/api/v3/manualimport?downloadId=…` capture.
- `scenehound/config.py` — **modify.** Add `ImportCompleterConfig` + wire into `Config` + `load_config`.
- `scenehound/clients/whisparr.py` — **modify.** Add `fetch_queue`, `fetch_manual_import`, `fetch_movie`, `post_manual_import` (raw JSON I/O only).
- `scenehound/import_completer.py` — **create.** Parsed record types (`QueueItem`, `ManualImportItem`) + parse fns, pure decision functions (`is_by_id_hold`, `plan_phase1`, `match_pack`, `finalize_pack`), and the `ImportCompleter` service (in-memory state, sweep orchestration, run loop, dry-run). No FastAPI import.
- `scenehound/import_api.py` — **create.** Small `APIRouter` with `POST /import/webhook`.
- `scenehound/app.py` — **modify.** Conditionally wire the router + start the completer task only when enabled.
- `tests/test_config.py` — **modify.** Import-completer config coverage.
- `tests/test_whisparr_client.py` — **modify.** New client method coverage.
- `tests/test_import_completer.py` — **create.** Parse fns + pure decisions + service (dry-run, retry/park, grace) via fake transports.
- `tests/test_import_api.py` — **create.** Webhook auth + Test-event 200 + wake.
- `tests/test_import_wiring.py` — **create.** Disabled-by-default structural guarantees.
- `tests/test_import_corpus.py` — **create.** Phase-2 pack-filename corpus.
- `tests/fixtures/import_pack_corpus.yaml` — **create.** Pack-filename → expected match/skip cases.
- `README.md`, `docker-compose.example.yml` — **modify.** Document the subsystem, config block, env vars, rollout ladder.

---

### Task 1: Probe script + fixture capture (human checkpoint)

**Files:**
- Create: `scripts/probe_whisparr.sh`
- Create (by user, from probe output): `tests/fixtures/whisparr_queue_sample.json`, `tests/fixtures/whisparr_manualimport_sample.json`

**Interfaces:**
- Produces: two fixture files consumed by Tasks 3, 4, 7 for "real fixture maps" tests. Field names observed here are the source of truth; if they differ from the Radarr-lineage names assumed in later tasks, adjust the parse functions and inline synthetic fixtures in those tasks to match.

> **This task is a checkpoint.** The script runs against the user's LAN Whisparr (`http://192.168.1.5:6979`) while a held by-ID item exists — the agent cannot run it. Tasks 2, 3 (client methods), and 6's structural tests do **not** depend on the captured fixtures and may proceed in parallel; the "real fixture maps" steps and phase-2 corpus wait for the captures.

- [ ] **Step 1: Write the probe script**

```bash
#!/usr/bin/env bash
# Probe Whisparr v3 (eros) API shapes for the import-completer. Read-only (GETs only).
# Usage: WHISPARR_URL=http://192.168.1.5:6979 WHISPARR_API_KEY=xxx ./scripts/probe_whisparr.sh
# Run while at least one download is held with "matched to movie by ID / Manual Import required".
# Sanitize output (strip infohashes/paths you don't want committed) before saving as fixtures.
set -euo pipefail
: "${WHISPARR_URL:?set WHISPARR_URL}"; : "${WHISPARR_API_KEY:?set WHISPARR_API_KEY}"
H=(-H "X-Api-Key: ${WHISPARR_API_KEY}")
base="${WHISPARR_URL%/}"

echo "== /api/v3/queue (find held items: trackedDownloadState, statusMessages, movieId, downloadId) =="
curl -fsS "${H[@]}" "${base}/api/v3/queue?page=1&pageSize=50&includeMovie=true" | tee /tmp/wh_queue.json | python3 -m json.tool | head -120

dl="$(python3 -c 'import json,sys; d=json.load(open("/tmp/wh_queue.json")); \
print(next((r.get("downloadId","") for r in d.get("records",[]) \
if "id" in str(r.get("statusMessages","")).lower() or "manual" in str(r.get("statusMessages","")).lower()), ""))')"
echo "== held downloadId detected: ${dl:-<none>} =="

if [ -n "${dl}" ]; then
  echo "== /api/v3/manualimport?downloadId=… (candidate shape: movie, rejections, quality, languages, sample flags) =="
  curl -fsS "${H[@]}" "${base}/api/v3/manualimport?downloadId=${dl}&filterExistingFiles=true" \
    | tee /tmp/wh_manualimport.json | python3 -m json.tool | head -160
fi

echo
echo "NEXT: verify a wanted-record 'id' equals a queue record 'movieId' (scene_id == movieId):"
echo "  curl -s ${H[*]} '${base}/api/v3/wanted/missing?pageSize=5' | python3 -m json.tool | grep -E '\"id\"|\"title\"'"
echo "SOURCE-READ (no safe write probe): confirm the ManualImport command body + exact webhook eventType"
echo "  in the Whisparr eros branch (Radarr lineage: POST /api/v3/command {name:'ManualImport', importMode, files:[…]})."
echo "SAVE sanitized: /tmp/wh_queue.json -> tests/fixtures/whisparr_queue_sample.json"
echo "                /tmp/wh_manualimport.json -> tests/fixtures/whisparr_manualimport_sample.json"
```

- [ ] **Step 2: Make it executable and commit the script**

```bash
chmod +x scripts/probe_whisparr.sh
git add scripts/probe_whisparr.sh
git commit -m "chore: add read-only Whisparr probe script for import-completer fixtures"
```

- [ ] **Step 3: User runs the probe and saves fixtures**

The user runs `./scripts/probe_whisparr.sh` against the live instance, sanitizes, and saves the two fixture files. Record in the plan any field-name deltas from the assumptions below (`trackedDownloadState`, `statusMessages[].messages[]`, `movieId`, `downloadId`; manualimport `movie.id`, `rejections`, `quality`, `languages`, `releaseGroup`, sample flag). Commit the fixtures:

```bash
git add tests/fixtures/whisparr_queue_sample.json tests/fixtures/whisparr_manualimport_sample.json
git commit -m "test: capture live Whisparr queue + manualimport fixtures"
```

---

### Task 2: ImportCompleterConfig

**Files:**
- Modify: `scenehound/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `ImportCompleterConfig(enabled: bool=False, dry_run: bool=True, multipack: bool=False, grace_seconds: float=120.0, reconcile_seconds: float=900.0, max_attempts: int=3, import_threshold: int=90, ambiguity_margin: int=10)`; new field `Config.import_completer: ImportCompleterConfig`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_import_completer_defaults(tmp_path):
    cfg = load_config(write_config(tmp_path), env={})
    ic = cfg.import_completer
    assert ic.enabled is False
    assert ic.dry_run is True
    assert ic.multipack is False
    assert ic.grace_seconds == 120.0
    assert ic.reconcile_seconds == 900.0
    assert ic.max_attempts == 3
    assert ic.import_threshold == 90
    assert ic.ambiguity_margin == 10


def test_import_completer_from_yaml(tmp_path):
    text = MINIMAL_YAML + """
import_completer:
  enabled: true
  dry_run: false
  multipack: true
  grace_seconds: 30
  import_threshold: 88
"""
    cfg = load_config(write_config(tmp_path, text), env={})
    ic = cfg.import_completer
    assert ic.enabled is True
    assert ic.dry_run is False
    assert ic.multipack is True
    assert ic.grace_seconds == 30.0
    assert ic.import_threshold == 88
    assert ic.ambiguity_margin == 10  # unspecified -> default


def test_import_completer_env_overrides(tmp_path):
    env = {
        "SCENEHOUND_IMPORT_ENABLED": "true",
        "SCENEHOUND_IMPORT_DRY_RUN": "false",
        "SCENEHOUND_IMPORT_MULTIPACK": "1",
        "SCENEHOUND_IMPORT_GRACE": "45",
        "SCENEHOUND_IMPORT_RECONCILE": "600",
        "SCENEHOUND_IMPORT_MAX_ATTEMPTS": "5",
        "SCENEHOUND_IMPORT_THRESHOLD": "92",
        "SCENEHOUND_IMPORT_MARGIN": "15",
    }
    cfg = load_config(write_config(tmp_path), env=env)
    ic = cfg.import_completer
    assert (ic.enabled, ic.dry_run, ic.multipack) == (True, False, True)
    assert ic.grace_seconds == 45.0 and ic.reconcile_seconds == 600.0
    assert ic.max_attempts == 5 and ic.import_threshold == 92 and ic.ambiguity_margin == 15


def test_import_completer_env_bool_falsey(tmp_path):
    # An explicit false-y env value must override a true YAML value.
    text = MINIMAL_YAML + "\nimport_completer:\n  enabled: true\n"
    cfg = load_config(write_config(tmp_path, text), env={"SCENEHOUND_IMPORT_ENABLED": "false"})
    assert cfg.import_completer.enabled is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3.12 -m pytest tests/test_config.py -q -k import_completer`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'import_completer'`).

- [ ] **Step 3: Implement the config**

In `scenehound/config.py`, add the dataclass after `RateLimitConfig`:

```python
@dataclass(frozen=True)
class ImportCompleterConfig:
    enabled: bool = False
    dry_run: bool = True
    multipack: bool = False
    grace_seconds: float = 120.0
    reconcile_seconds: float = 900.0
    max_attempts: int = 3
    import_threshold: int = 90
    ambiguity_margin: int = 10
```

Add the field to `Config` (after `rate_limit`):

```python
    import_completer: ImportCompleterConfig = field(default_factory=ImportCompleterConfig)
```

Add a bool env helper near the top-level helpers:

```python
def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

Add a builder and call it in `load_config`:

```python
def _import_completer(raw: dict, env: Mapping[str, str]) -> ImportCompleterConfig:
    d = ImportCompleterConfig()
    ic = raw.get("import_completer", {}) or {}
    return ImportCompleterConfig(
        enabled=_env_bool(env, "SCENEHOUND_IMPORT_ENABLED", bool(ic.get("enabled", d.enabled))),
        dry_run=_env_bool(env, "SCENEHOUND_IMPORT_DRY_RUN", bool(ic.get("dry_run", d.dry_run))),
        multipack=_env_bool(env, "SCENEHOUND_IMPORT_MULTIPACK", bool(ic.get("multipack", d.multipack))),
        grace_seconds=float(env.get("SCENEHOUND_IMPORT_GRACE", ic.get("grace_seconds", d.grace_seconds))),
        reconcile_seconds=float(
            env.get("SCENEHOUND_IMPORT_RECONCILE", ic.get("reconcile_seconds", d.reconcile_seconds))
        ),
        max_attempts=int(env.get("SCENEHOUND_IMPORT_MAX_ATTEMPTS", ic.get("max_attempts", d.max_attempts))),
        import_threshold=int(
            env.get("SCENEHOUND_IMPORT_THRESHOLD", ic.get("import_threshold", d.import_threshold))
        ),
        ambiguity_margin=int(
            env.get("SCENEHOUND_IMPORT_MARGIN", ic.get("ambiguity_margin", d.ambiguity_margin))
        ),
    )
```

In the `Config(...)` return inside `load_config`, add:

```python
        import_completer=_import_completer(raw, env),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3.12 -m pytest tests/test_config.py -q`
Expected: PASS (all config tests, old and new).

- [ ] **Step 5: Commit**

```bash
git add scenehound/config.py tests/test_config.py
git commit -m "feat: add ImportCompleterConfig (disabled + dry-run by default)"
```

---

### Task 3: WhisparrClient read/write methods

**Files:**
- Modify: `scenehound/clients/whisparr.py`
- Test: `tests/test_whisparr_client.py`

**Interfaces:**
- Consumes: `httpx.AsyncClient`, existing `WhisparrClient.__init__(base_url, api_key, client)`.
- Produces:
  - `async fetch_queue() -> list[dict]` — GET `/api/v3/queue`, paginated, returns raw records.
  - `async fetch_manual_import(download_id: str, filter_existing: bool = True) -> list[dict]` — GET `/api/v3/manualimport`.
  - `async fetch_movie(movie_id: int) -> dict` — GET `/api/v3/movie/{id}`.
  - `async post_manual_import(files: list[dict], import_mode: str = "copy") -> None` — POST `/api/v3/command` `{name, importMode, files}`.

> **Fixture note:** endpoint paths and the command body are Radarr-lineage assumptions; reconcile against Task 1's source-read/captures if they differ.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_whisparr_client.py`:

```python
async def test_fetch_queue_pages_and_returns_raw_records():
    pages = {
        1: {"page": 1, "pageSize": 2, "totalRecords": 3,
            "records": [{"downloadId": "A"}, {"downloadId": "B"}]},
        2: {"page": 2, "pageSize": 2, "totalRecords": 3,
            "records": [{"downloadId": "C"}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "/api/v3/queue" in str(request.url)
        return httpx.Response(200, json=pages[int(dict(request.url.params)["page"])])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        recs = await WhisparrClient("http://w:6969", "k", hc).fetch_queue()
    assert [r["downloadId"] for r in recs] == ["A", "B", "C"]


async def test_fetch_manual_import_sends_download_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/v3/manualimport" in str(request.url)
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=[{"path": "/x.mp4"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        out = await WhisparrClient("http://w:6969", "k", hc).fetch_manual_import("HASH1")
    assert seen["downloadId"] == "HASH1"
    assert seen["filterExistingFiles"] == "true"
    assert out == [{"path": "/x.mp4"}]


async def test_fetch_movie_returns_object():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/api/v3/movie/42")
        return httpx.Response(200, json={"id": 42, "monitored": True, "hasFile": False})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        movie = await WhisparrClient("http://w:6969", "k", hc).fetch_movie(42)
    assert movie["monitored"] is True and movie["hasFile"] is False


async def test_post_manual_import_builds_command_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url).endswith("/api/v3/command")
        captured.update(request.read() and __import__("json").loads(request.content))
        return httpx.Response(201, json={"id": 1})

    files = [{"path": "/dl/a.mp4", "movieId": 7}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        await WhisparrClient("http://w:6969", "k", hc).post_manual_import(files)
    assert captured["name"] == "ManualImport"
    assert captured["importMode"] == "copy"
    assert captured["files"] == files
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3.12 -m pytest tests/test_whisparr_client.py -q -k "queue or manual_import or movie or command"`
Expected: FAIL (`AttributeError: 'WhisparrClient' object has no attribute 'fetch_queue'`).

- [ ] **Step 3: Implement the methods**

In `scenehound/clients/whisparr.py`, add these methods to `WhisparrClient` (after `fetch_wanted`):

```python
    async def fetch_queue(self) -> list[dict]:
        records: list[dict] = []
        page, received, total = 1, 0, None
        while total is None or received < total:
            resp = await self._client.get(
                f"{self._base}/api/v3/queue",
                params={"page": page, "pageSize": _PAGE_SIZE, "includeMovie": "true"},
                headers=self._headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            body = resp.json()
            total = int(body.get("totalRecords", 0))
            batch = body.get("records", [])
            if not batch:
                break
            received += len(batch)
            records.extend(batch)
            page += 1
        return records

    async def fetch_manual_import(
        self, download_id: str, filter_existing: bool = True
    ) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/api/v3/manualimport",
            params={
                "downloadId": download_id,
                "filterExistingFiles": "true" if filter_existing else "false",
            },
            headers=self._headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return body if isinstance(body, list) else body.get("records", [])

    async def fetch_movie(self, movie_id: int) -> dict:
        resp = await self._client.get(
            f"{self._base}/api/v3/movie/{movie_id}",
            headers=self._headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()

    async def post_manual_import(
        self, files: list[dict], import_mode: str = "copy"
    ) -> None:
        resp = await self._client.post(
            f"{self._base}/api/v3/command",
            json={"name": "ManualImport", "importMode": import_mode, "files": files},
            headers=self._headers,
            timeout=60.0,
        )
        resp.raise_for_status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3.12 -m pytest tests/test_whisparr_client.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scenehound/clients/whisparr.py tests/test_whisparr_client.py
git commit -m "feat: add Whisparr queue/manualimport/movie/command client methods"
```

---

### Task 4: Parsed record types + phase-1 decision functions (pure)

**Files:**
- Create: `scenehound/import_completer.py`
- Test: `tests/test_import_completer.py`

**Interfaces:**
- Consumes: `SceneFingerprint` (not directly here), config `ImportCompleterConfig`.
- Produces (all pure, no I/O):
  - `QueueItem(download_id: str, movie_id: int, tracked_state: str, status_messages: tuple[str, ...], title: str)` + `queue_item_from_record(record: dict) -> QueueItem | None`.
  - `ManualImportItem(path: str, folder_name: str, movie_id: int | None, quality: dict | None, languages: tuple[dict, ...], release_group: str | None, rejections: tuple[str, ...], is_sample: bool, size: int)` + `manual_import_from_record(record: dict) -> ManualImportItem`.
  - `is_by_id_hold(item: QueueItem) -> bool`.
  - `ActionPlan(files: tuple[dict, ...])` and `Skip(reason: str)`.
  - `plan_phase1(item: QueueItem, candidates: list[ManualImportItem], config: ImportCompleterConfig) -> ActionPlan | Skip`.
  - `_file_entry(path, movie_id, cand, download_id) -> dict` (module-private helper).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_import_completer.py`:

```python
import json
from pathlib import Path

from scenehound.config import ImportCompleterConfig
from scenehound.import_completer import (
    ActionPlan, ManualImportItem, QueueItem, Skip,
    is_by_id_hold, manual_import_from_record, plan_phase1, queue_item_from_record,
)

BY_ID = {
    "downloadId": "HASH1", "movieId": 7, "title": "AdultTime...",
    "trackedDownloadState": "importBlocked",
    "statusMessages": [{"title": "AdultTime.mp4", "messages": [
        "Found matching movie via grab history, but release was matched to movie by ID. "
        "Manual Import required."]}],
}


def _cand(**over):
    base = {
        "path": "/dl/AdultTime.mp4", "folderName": "AdultTime",
        "movie": {"id": 7}, "quality": {"quality": {"id": 19, "name": "Bluray-2160p"}},
        "languages": [{"id": 1, "name": "English"}], "releaseGroup": "GRP",
        "rejections": [], "size": 6_000_000_000,
    }
    base.update(over)
    return manual_import_from_record(base)


def test_queue_item_from_record_maps_fields():
    qi = queue_item_from_record(BY_ID)
    assert qi.download_id == "HASH1" and qi.movie_id == 7
    assert qi.tracked_state == "importBlocked"
    assert any("matched to movie by id" in m.lower() for m in qi.status_messages)


def test_queue_item_from_record_rejects_incomplete():
    assert queue_item_from_record({"movieId": 7}) is None          # no downloadId
    assert queue_item_from_record({"downloadId": "H"}) is None      # no movieId


def test_is_by_id_hold_true_for_marker_and_state():
    assert is_by_id_hold(queue_item_from_record(BY_ID)) is True


def test_is_by_id_hold_false_for_other_state():
    other = queue_item_from_record({**BY_ID, "trackedDownloadState": "downloading"})
    assert is_by_id_hold(other) is False


def test_is_by_id_hold_false_without_marker():
    quiet = queue_item_from_record({**BY_ID, "statusMessages": [
        {"title": "x", "messages": ["Not an upgrade for existing movie file"]}]})
    assert is_by_id_hold(quiet) is False


def test_manual_import_from_record_maps_movie_and_rejections():
    c = _cand()
    assert c.movie_id == 7 and c.rejections == () and c.release_group == "GRP"
    assert c.quality == {"quality": {"id": 19, "name": "Bluray-2160p"}}
    absent = _cand(movie=None)
    assert absent.movie_id is None


def test_plan_phase1_imports_clean_single_file():
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand()], ImportCompleterConfig())
    assert isinstance(plan, ActionPlan)
    (f,) = plan.files
    assert f["movieId"] == 7 and f["path"] == "/dl/AdultTime.mp4"
    assert f["downloadId"] == "HASH1" and f["quality"]["quality"]["id"] == 19
    assert f["languages"] == [{"id": 1, "name": "English"}] and f["releaseGroup"] == "GRP"


def test_plan_phase1_skips_on_movie_id_mismatch():
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand(movie={"id": 999})], ImportCompleterConfig())
    assert isinstance(plan, Skip) and "movie" in plan.reason


def test_plan_phase1_skips_on_rejections():
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand(rejections=["Unknown quality"])], ImportCompleterConfig())
    assert isinstance(plan, Skip) and "reject" in plan.reason.lower()


def test_plan_phase1_skips_when_no_movie_pre_populated():
    # A file Whisparr could not scene-match itself has no movie; phase 1 will not
    # invent one — only confirms Whisparr's own by-ID decision (movie.id present == movieId).
    qi = queue_item_from_record(BY_ID)
    plan = plan_phase1(qi, [_cand(movie=None)], ImportCompleterConfig())
    assert isinstance(plan, Skip)


def test_plan_phase1_skips_multiple_video_files():
    qi = queue_item_from_record(BY_ID)
    two = [_cand(path="/dl/a.mp4"), _cand(path="/dl/b.mp4")]
    plan = plan_phase1(qi, two, ImportCompleterConfig())
    assert isinstance(plan, Skip) and "single" in plan.reason.lower()


def test_plan_phase1_ignores_sample_files():
    qi = queue_item_from_record(BY_ID)
    cands = [_cand(path="/dl/main.mp4"),
             _cand(path="/dl/sample.mp4", rejections=["Sample"], size=10_000_000)]
    plan = plan_phase1(qi, cands, ImportCompleterConfig())
    # Sample is excluded from the single-file count AND its rejection is not counted.
    assert isinstance(plan, ActionPlan)
    (f,) = plan.files
    assert f["path"] == "/dl/main.mp4"


def test_real_manualimport_fixture_maps():
    # Depends on Task 1 capture. Skips cleanly until the fixture exists.
    fx = Path(__file__).parent / "fixtures" / "whisparr_manualimport_sample.json"
    if not fx.exists():
        import pytest
        pytest.skip("run scripts/probe_whisparr.sh to capture the fixture")
    data = json.loads(fx.read_text())
    records = data if isinstance(data, list) else data.get("records", [])
    items = [manual_import_from_record(r) for r in records]
    assert items, "captured manualimport sample had no candidates"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_completer.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'scenehound.import_completer'`).

- [ ] **Step 3: Implement the module (parse + phase-1 decisions)**

Create `scenehound/import_completer.py`:

```python
"""Import-completer: auto-triggers Whisparr's held by-ID manual imports.

Opt-in and dry-run by default; fully isolated from the Torznab search path.
This module holds the *brain* — parsed Whisparr record types and PURE decision
functions — plus the stateful ImportCompleter service (added later). No FastAPI
import lives here; the webhook route is in import_api.py.

Decision purity: plan_phase1 / match_pack / finalize_pack take already-fetched
data and return an ActionPlan or a Skip(reason). The service performs all I/O
around them, so decisions are tested against fixtures with no HTTP.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from scenehound.config import ImportCompleterConfig

log = logging.getLogger("scenehound.import_completer")

HELD_STATES = frozenset({"importBlocked", "importPending"})
# The proven by-ID hold. Matched case-insensitively as substrings so minor
# wording drift across eros versions fails SAFE (no marker -> not handled).
_BY_ID_MARKERS = ("matched to movie by id", "manual import required")


@dataclass(frozen=True)
class QueueItem:
    download_id: str
    movie_id: int
    tracked_state: str
    status_messages: tuple[str, ...]
    title: str


def queue_item_from_record(record: dict) -> QueueItem | None:
    download_id = record.get("downloadId")
    movie_id = record.get("movieId")
    if not download_id or not movie_id:
        return None
    msgs: list[str] = []
    for block in record.get("statusMessages", []) or []:
        if isinstance(block, dict):
            if title := block.get("title"):
                msgs.append(str(title))
            for m in block.get("messages", []) or []:
                msgs.append(str(m))
        elif isinstance(block, str):
            msgs.append(block)
    return QueueItem(
        download_id=str(download_id),
        movie_id=int(movie_id),
        tracked_state=str(record.get("trackedDownloadState", "")),
        status_messages=tuple(msgs),
        title=str(record.get("title", "")),
    )


@dataclass(frozen=True)
class ManualImportItem:
    path: str
    folder_name: str
    movie_id: int | None
    quality: dict | None
    languages: tuple[dict, ...]
    release_group: str | None
    rejections: tuple[str, ...]
    is_sample: bool
    size: int


def _rejection_strings(raw: list) -> tuple[str, ...]:
    out: list[str] = []
    for r in raw or []:
        if isinstance(r, dict):
            out.append(str(r.get("reason", r)))
        else:
            out.append(str(r))
    return tuple(out)


def manual_import_from_record(record: dict) -> ManualImportItem:
    movie = record.get("movie") or None
    movie_id = int(movie["id"]) if isinstance(movie, dict) and movie.get("id") else None
    rejections = _rejection_strings(record.get("rejections", []))
    is_sample = any("sample" in r.lower() for r in rejections)
    langs = tuple(l for l in (record.get("languages") or []) if isinstance(l, dict))
    return ManualImportItem(
        path=str(record.get("path", "")),
        folder_name=str(record.get("folderName", "")),
        movie_id=movie_id,
        quality=record.get("quality"),
        languages=langs,
        release_group=record.get("releaseGroup"),
        rejections=rejections,
        is_sample=is_sample,
        size=int(record.get("size", 0)),
    )


def is_by_id_hold(item: QueueItem) -> bool:
    if item.tracked_state not in HELD_STATES:
        return False
    haystack = " ".join(item.status_messages).lower()
    return any(marker in haystack for marker in _BY_ID_MARKERS)


@dataclass(frozen=True)
class ActionPlan:
    files: tuple[dict, ...]


@dataclass(frozen=True)
class Skip:
    reason: str


def _file_entry(path: str, movie_id: int, cand: ManualImportItem, download_id: str) -> dict:
    # Quality / languages / releaseGroup are taken VERBATIM from Whisparr's own
    # candidate parse — we never second-guess the file. downloadId associates the
    # import with the tracked download so it clears and downstream import events fire.
    return {
        "path": path,
        "movieId": movie_id,
        "quality": cand.quality,
        "languages": list(cand.languages),
        "releaseGroup": cand.release_group,
        "downloadId": download_id,
    }


def plan_phase1(
    item: QueueItem, candidates: list[ManualImportItem], config: ImportCompleterConfig
) -> ActionPlan | Skip:
    videos = [c for c in candidates if not c.is_sample]
    if len(videos) != 1:
        return Skip(f"not-single-video-file (videos={len(videos)})")
    cand = videos[0]
    if cand.movie_id is None:
        return Skip("candidate-has-no-movie (Whisparr did not pre-populate a by-ID match)")
    if cand.movie_id != item.movie_id:
        return Skip(f"movie-id-mismatch (candidate={cand.movie_id} grabbed={item.movie_id})")
    if cand.rejections:
        return Skip(f"has-rejections {list(cand.rejections)}")
    return ActionPlan(files=(_file_entry(cand.path, cand.movie_id, cand, item.download_id),))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_completer.py -q`
Expected: PASS (the real-fixture test skips until Task 1 captures land).

- [ ] **Step 5: Commit**

```bash
git add scenehound/import_completer.py tests/test_import_completer.py
git commit -m "feat: import-completer record parsing + phase-1 decision function"
```

---

### Task 5: ImportCompleter service (sweep, grace, retry/park, dry-run)

**Files:**
- Modify: `scenehound/import_completer.py`
- Test: `tests/test_import_completer.py`

**Interfaces:**
- Consumes: `WhisparrClient` (queue/manualimport/command methods from Task 3), `IndexHolder` (from `scenehound.api`), `plan_phase1`, parse fns.
- Produces:
  - `SweepSummary(acted: int, skipped: int, waited: int, parked: int)`.
  - `ImportCompleter(client, index_holder, config)` with `notify() -> None`, `async sweep(now: float) -> SweepSummary`, `async run() -> None`.
  - Phase-2 branch calls `match_pack`/`finalize_pack` (added in Task 7); until then, multi-video packs Skip with `"multipack-disabled"` when `multipack` is false and `"phase2-not-implemented"` when true.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_import_completer.py`:

```python
import httpx

from scenehound.api import IndexHolder
from scenehound.clients.whisparr import WhisparrClient
from scenehound.import_completer import ImportCompleter
from scenehound.wanted_index import WantedIndex

QUEUE_ONE = {"page": 1, "pageSize": 1000, "totalRecords": 1, "records": [BY_ID]}
MANUAL_CLEAN = [{
    "path": "/dl/AdultTime.mp4", "folderName": "AdultTime", "movie": {"id": 7},
    "quality": {"quality": {"id": 19}}, "languages": [{"id": 1}],
    "releaseGroup": "GRP", "rejections": [], "size": 6_000_000_000,
}]


class Spy:
    """Records every request; serves queue + manualimport; captures POSTs."""
    def __init__(self, queue=QUEUE_ONE, manual=MANUAL_CLEAN):
        self.queue, self.manual = queue, manual
        self.calls: list[tuple[str, str]] = []
        self.posted: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, str(request.url)))
        url = str(request.url)
        if "/api/v3/queue" in url:
            return httpx.Response(200, json=self.queue)
        if "/api/v3/manualimport" in url:
            return httpx.Response(200, json=self.manual)
        if url.endswith("/api/v3/command"):
            self.posted.append(__import__("json").loads(request.content))
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)


def _completer(spy, **cfg):
    from scenehound.config import ImportCompleterConfig
    hc = httpx.AsyncClient(transport=httpx.MockTransport(spy.handler))
    client = WhisparrClient("http://w:6969", "k", hc)
    holder = IndexHolder()
    holder.set(WantedIndex([]))
    return ImportCompleter(client, holder, ImportCompleterConfig(enabled=True, **cfg))


async def test_dry_run_fires_no_post_and_only_gets():
    spy = Spy()
    ic = _completer(spy, dry_run=True, grace_seconds=0)
    summary = await ic.sweep(now=1000.0)
    assert summary.acted == 1
    assert spy.posted == []
    assert all(method == "GET" for method, _ in spy.calls)  # hard dry-run property


async def test_live_run_posts_manual_import():
    spy = Spy()
    ic = _completer(spy, dry_run=False, grace_seconds=0)
    summary = await ic.sweep(now=1000.0)
    assert summary.acted == 1
    assert len(spy.posted) == 1
    assert spy.posted[0]["name"] == "ManualImport"
    assert spy.posted[0]["files"][0]["movieId"] == 7


async def test_grace_defers_then_acts():
    spy = Spy()
    ic = _completer(spy, dry_run=False, grace_seconds=120)
    first = await ic.sweep(now=1000.0)   # first-seen stamped, within grace
    assert first.waited == 1 and spy.posted == []
    second = await ic.sweep(now=1000.0 + 121)  # grace elapsed
    assert second.acted == 1 and len(spy.posted) == 1


async def test_retry_then_park_after_max_attempts():
    # Item never clears (Spy keeps returning it). Fires up to max_attempts then parks.
    spy = Spy()
    ic = _completer(spy, dry_run=False, grace_seconds=0, max_attempts=2)
    await ic.sweep(now=1.0)   # attempt 1
    await ic.sweep(now=2.0)   # attempt 2
    parked = await ic.sweep(now=3.0)  # capped -> park, no further POST
    assert len(spy.posted) == 2
    assert parked.parked == 1


async def test_skip_does_not_count_as_attempt():
    spy = Spy(manual=[{**MANUAL_CLEAN[0], "rejections": ["Unknown quality"]}])
    ic = _completer(spy, dry_run=False, grace_seconds=0, max_attempts=2)
    s = await ic.sweep(now=1.0)
    assert s.skipped == 1 and spy.posted == []


async def test_non_by_id_items_ignored():
    downloading = {**BY_ID, "trackedDownloadState": "downloading"}
    spy = Spy(queue={"page": 1, "pageSize": 1000, "totalRecords": 1, "records": [downloading]})
    ic = _completer(spy, dry_run=False, grace_seconds=0)
    s = await ic.sweep(now=1.0)
    assert (s.acted, s.skipped, s.waited) == (0, 0, 0)
    assert not any("manualimport" in url for _, url in spy.calls)  # no manualimport fetch


async def test_multipack_disabled_skips_multifile():
    two = [MANUAL_CLEAN[0], {**MANUAL_CLEAN[0], "path": "/dl/b.mp4"}]
    spy = Spy(manual=two)
    ic = _completer(spy, dry_run=False, grace_seconds=0, multipack=False)
    s = await ic.sweep(now=1.0)
    assert s.skipped == 1 and spy.posted == []


async def test_notify_sets_wake_event():
    spy = Spy()
    ic = _completer(spy, grace_seconds=0)
    assert not ic._wake.is_set()
    ic.notify()
    assert ic._wake.is_set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_completer.py -q -k "dry_run or live_run or grace or park or attempt or by_id or multipack or notify"`
Expected: FAIL (`ImportError: cannot import name 'ImportCompleter'`).

- [ ] **Step 3: Implement the service**

Append to `scenehound/import_completer.py`:

```python
import asyncio


@dataclass
class SweepSummary:
    acted: int = 0
    skipped: int = 0
    waited: int = 0
    parked: int = 0


class ImportCompleter:
    """Doorbell-driven sweep of Whisparr's queue for held by-ID imports.

    State is process-local: first-seen timestamps (grace), attempt counts +
    parked set (bounded retry), and a last-logged-skip map (quiet logs). A
    successful import removes the item from the queue, so duplicate wakes are
    naturally no-ops. Import errors from Whisparr are logged and re-tried on the
    next sweep (bounded by max_attempts), never raised out of the loop.
    """

    def __init__(self, client, index_holder, config: ImportCompleterConfig) -> None:
        self._client = client
        self._index_holder = index_holder
        self._config = config
        self._wake = asyncio.Event()
        self._first_seen: dict[str, float] = {}
        self._attempts: dict[str, int] = {}
        self._parked: set[str] = set()
        self._logged_skips: dict[str, str] = {}

    def notify(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        loop = asyncio.get_event_loop()
        await self._safe_sweep(loop.time())  # startup sweep covers missed webhooks
        while True:
            timeout = self._next_timeout(loop.time())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            self._wake.clear()
            await self._safe_sweep(loop.time())

    def _next_timeout(self, now: float) -> float:
        # Wake at the earliest pending grace expiry, else the reconcile interval.
        pending = [
            self._first_seen[d] + self._config.grace_seconds - now
            for d in self._first_seen
            if d not in self._parked
            and self._first_seen[d] + self._config.grace_seconds > now
        ]
        if pending:
            return max(0.5, min(self._config.reconcile_seconds, min(pending)))
        return self._config.reconcile_seconds

    async def _safe_sweep(self, now: float) -> SweepSummary:
        try:
            return await self.sweep(now)
        except Exception as exc:  # never let the loop die on a transient Whisparr error
            log.error("import sweep failed (will retry): %s", exc)
            return SweepSummary()

    async def sweep(self, now: float) -> SweepSummary:
        s = SweepSummary()
        records = await self._client.fetch_queue()
        for record in records:
            item = queue_item_from_record(record)
            if item is None or not is_by_id_hold(item):
                continue
            did = item.download_id
            if did in self._parked:
                continue
            self._first_seen.setdefault(did, now)
            if now - self._first_seen[did] < self._config.grace_seconds:
                s.waited += 1
                continue
            attempts = self._attempts.get(did, 0)
            if attempts >= self._config.max_attempts:
                self._parked.add(did)
                log.warning("import parked download_id=%s after %d attempts", did, attempts)
                s.parked += 1
                continue
            plan = await self._plan(item)
            if isinstance(plan, Skip):
                if self._logged_skips.get(did) != plan.reason:
                    self._logged_skips[did] = plan.reason
                    log.info("import skip download_id=%s reason=%s", did, plan.reason)
                s.skipped += 1
                continue
            self._attempts[did] = attempts + 1
            if self._config.dry_run:
                log.info(
                    "DRY-RUN import download_id=%s files=%d body=%s",
                    did, len(plan.files),
                    {"name": "ManualImport", "importMode": "copy", "files": list(plan.files)},
                )
            else:
                await self._client.post_manual_import(list(plan.files))
                log.info("import fired download_id=%s files=%d movie=%d",
                         did, len(plan.files), item.movie_id)
            s.acted += 1
        return s

    async def _plan(self, item: QueueItem) -> "ActionPlan | Skip":
        cand_records = await self._client.fetch_manual_import(item.download_id)
        candidates = [manual_import_from_record(c) for c in cand_records]
        videos = [c for c in candidates if not c.is_sample]
        if len(videos) <= 1:
            return plan_phase1(item, candidates, self._config)
        if not self._config.multipack:
            return Skip(f"multipack-disabled (videos={len(videos)})")
        return await self._plan_phase2(item, candidates)

    async def _plan_phase2(
        self, item: QueueItem, candidates: list[ManualImportItem]
    ) -> "ActionPlan | Skip":
        return Skip("phase2-not-implemented")  # replaced in Task 7
```

Move the `import asyncio` line to the top of the file with the other imports (keep imports grouped); the inline placement above is only to show where it is used.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_completer.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scenehound/import_completer.py tests/test_import_completer.py
git commit -m "feat: ImportCompleter sweep with grace, bounded retry/park, dry-run"
```

---

### Task 6: Webhook route + app wiring + disabled-by-default guarantees

**Files:**
- Create: `scenehound/import_api.py`
- Modify: `scenehound/app.py`
- Test: `tests/test_import_api.py`, `tests/test_import_wiring.py`

**Interfaces:**
- Consumes: `ImportCompleter` (Task 5), `AppState` (`scenehound.api`), `Config.import_completer`.
- Produces: `import_router` (APIRouter) with `POST /import/webhook`; `create_app` conditionally includes it and starts the completer task; lifespan sets `app.state.import_completer` only when enabled.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_import_api.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scenehound.api import AppState, IndexHolder, router as search_router
from scenehound.import_api import import_router
from scenehound.rate_limiter import TokenBucket
from tests.conftest import make_config


class FakeCompleter:
    def __init__(self):
        self.notified = 0

    def notify(self):
        self.notified += 1


def _app(completer):
    cfg = make_config()
    app = FastAPI()
    app.include_router(search_router)
    app.include_router(import_router)
    app.state.scenehound = AppState(
        config=cfg, prowlarr=None, index_holder=IndexHolder(),
        buckets={i.slug: TokenBucket(4, 15.0) for i in cfg.indexers},
    )
    app.state.import_completer = completer
    return app


def test_webhook_rejects_bad_apikey():
    fc = FakeCompleter()
    r = TestClient(_app(fc)).post("/import/webhook?apikey=wrong", json={"eventType": "Test"})
    assert r.status_code == 401
    assert fc.notified == 0


def test_webhook_test_event_returns_200_without_waking():
    fc = FakeCompleter()
    r = TestClient(_app(fc)).post("/import/webhook?apikey=shk", json={"eventType": "Test"})
    assert r.status_code == 200
    assert fc.notified == 0  # Test event must not trigger a sweep


def test_webhook_real_event_wakes_completer():
    fc = FakeCompleter()
    r = TestClient(_app(fc)).post(
        "/import/webhook?apikey=shk", json={"eventType": "ManualInteractionRequired"})
    assert r.status_code == 200
    assert fc.notified == 1


def test_webhook_ok_when_completer_absent():
    # Route registered but no completer instance -> still 200, no crash.
    cfg = make_config()
    app = FastAPI()
    app.include_router(import_router)
    app.state.scenehound = AppState(
        config=cfg, prowlarr=None, index_holder=IndexHolder(),
        buckets={i.slug: TokenBucket(4, 15.0) for i in cfg.indexers},
    )
    app.state.import_completer = None
    r = TestClient(app).post("/import/webhook?apikey=shk", json={"eventType": "Grab"})
    assert r.status_code == 200
```

Create `tests/test_import_wiring.py`:

```python
from fastapi.testclient import TestClient

from scenehound.app import create_app

CONFIG = """
whisparr:
  url: http://w:6969
  api_key: wk
prowlarr:
  url: http://p:9696
  api_key: pk
indexers:
  - slug: empornium
    prowlarr_id: 12
"""


def _write(tmp_path, extra=""):
    (tmp_path / "config.yaml").write_text(CONFIG + extra)
    return tmp_path


def test_disabled_registers_no_webhook_route(tmp_path):
    app = create_app(config_dir=_write(tmp_path))  # import_completer absent -> disabled
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/import/webhook" not in paths


def test_disabled_starts_no_completer_task(tmp_path):
    app = create_app(config_dir=_write(tmp_path))
    with TestClient(app):  # runs lifespan (refresh task still starts; completer must not)
        assert getattr(app.state, "import_completer", None) is None


def test_enabled_registers_webhook_route(tmp_path):
    app = create_app(config_dir=_write(tmp_path, "import_completer:\n  enabled: true\n"))
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/import/webhook" in paths


def test_enabled_sets_completer_on_state(tmp_path):
    app = create_app(config_dir=_write(tmp_path, "import_completer:\n  enabled: true\n"))
    with TestClient(app):
        assert getattr(app.state, "import_completer", None) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_api.py tests/test_import_wiring.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'scenehound.import_api'`).

- [ ] **Step 3: Implement the router**

Create `scenehound/import_api.py`:

```python
"""Webhook route for the import-completer. Kept separate from the search API so
the search surface (api.py) has zero import-completer coupling."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

log = logging.getLogger("scenehound.import_api")
import_router = APIRouter()


@import_router.post("/import/webhook")
async def import_webhook(request: Request) -> Response:
    state = request.app.state.scenehound
    if request.query_params.get("apikey") != state.config.api_key:
        return Response(status_code=401)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    event = str(payload.get("eventType", ""))
    log.debug("webhook event=%s payload=%s", event, payload)
    completer = getattr(request.app.state, "import_completer", None)
    # Whisparr's "Test" button posts eventType=Test; 200 it so the Connect saves,
    # but never trigger a sweep from it. Any real event rings the doorbell.
    if event and event != "Test" and completer is not None:
        completer.notify()
    return Response(status_code=200)
```

- [ ] **Step 4: Wire it into the app**

In `scenehound/app.py`, add the imports near the existing ones:

```python
from scenehound.import_api import import_router
from scenehound.import_completer import ImportCompleter
```

In `create_app`, immediately after `app.include_router(router)`:

```python
    if config.import_completer.enabled:
        app.include_router(import_router)
```

In the `lifespan` function, after the `whisparr = WhisparrClient(...)` line and before `task = asyncio.create_task(refresh_loop(...))`, add:

```python
            completer_task = None
            if config.import_completer.enabled:
                completer = ImportCompleter(
                    whisparr, state.index_holder, config.import_completer
                )
                app.state.import_completer = completer
                completer_task = asyncio.create_task(completer.run())
                log.info(
                    "import-completer enabled dry_run=%s multipack=%s",
                    config.import_completer.dry_run, config.import_completer.multipack,
                )
```

Then in the `finally:` block of the lifespan, after the existing refresh-task teardown, add:

```python
                if completer_task is not None:
                    completer_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await completer_task
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_api.py tests/test_import_wiring.py -q`
Expected: PASS.

- [ ] **Step 6: Run the full suite (regression gate)**

Run: `.venv/bin/python3.12 -m pytest -q`
Expected: PASS (search path untouched; all pre-existing tests green).

- [ ] **Step 7: Commit**

```bash
git add scenehound/import_api.py scenehound/app.py tests/test_import_api.py tests/test_import_wiring.py
git commit -m "feat: webhook route + conditional wiring (disabled-by-default, structural)"
```

---

### Task 7: Phase-2 pack matching (all-or-nothing) + corpus

**Files:**
- Modify: `scenehound/import_completer.py`
- Create: `tests/fixtures/import_pack_corpus.yaml`, `tests/test_import_corpus.py`
- Test: `tests/test_import_completer.py`

**Interfaces:**
- Consumes: `WantedIndex.candidates_for_title`, `matcher.score`, `SceneFingerprint` (scene_id == movieId), `ImportCompleterConfig.import_threshold` / `.ambiguity_margin`.
- Produces (pure):
  - `FileMatch(path: str, cand: ManualImportItem, movie_id: int | None, verdict: str)` where verdict ∈ `{"matched", "unmatched", "ambiguous"}`.
  - `PackMatch(files: tuple[FileMatch, ...])` with `.fully_matched -> bool` and `.matched_movie_ids -> frozenset[int]`.
  - `match_pack(item, candidates, index, config) -> PackMatch`.
  - `finalize_pack(item, pack, movie_states: dict[int, tuple[bool, bool]], config) -> ActionPlan | Skip` (movie_states: movie_id -> (monitored, has_file)).
  - Wires `ImportCompleter._plan_phase2` to `match_pack` → fetch movie states → `finalize_pack`.

- [ ] **Step 1: Write the failing pure-function tests**

Append to `tests/test_import_completer.py`:

```python
from datetime import date

from scenehound.models import SceneFingerprint
from scenehound.import_completer import FileMatch, PackMatch, finalize_pack, match_pack


def _index(*scenes):
    return WantedIndex(list(scenes))


SCENE_A = SceneFingerprint(101, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                           "Latex Worship Session", ("Jane Doe",))
SCENE_B = SceneFingerprint(102, "That Fetish Girl", ("TFG",), date(2026, 7, 8),
                           "Rubber Gloves", ("Mary Major",))


def _packcand(path, movie=None):
    return manual_import_from_record({
        "path": path, "folderName": "TFG.Pack", "movie": movie,
        "quality": {"quality": {"id": 3}}, "languages": [{"id": 1}],
        "releaseGroup": "GRP", "rejections": [], "size": 5_000_000_000,
    })


def test_match_pack_all_matched_via_scoring():
    cands = [
        _packcand("TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4"),
        _packcand("TFG.26.07.08.Rubber.Gloves.Mary.Major.1080p.mp4"),
    ]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A, SCENE_B),
                      ImportCompleterConfig(import_threshold=75))
    assert pack.fully_matched
    assert pack.matched_movie_ids == frozenset({101, 102})


def test_match_pack_unmatched_file_blocks_pack():
    cands = [
        _packcand("TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4"),
        _packcand("Totally.Unknown.Release.2019.720p.mp4"),
    ]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A, SCENE_B),
                      ImportCompleterConfig(import_threshold=75))
    assert not pack.fully_matched
    assert any(f.verdict == "unmatched" for f in pack.files)


def test_match_pack_ambiguous_when_margin_too_thin():
    # Two same-day scenes, filename carries only site+date -> both score equal,
    # margin below ambiguity_margin -> ambiguous, pack blocked.
    twin = SceneFingerprint(103, "That Fetish Girl", ("TFG",), date(2026, 7, 7),
                            "Different Title", ("Someone Else",))
    cands = [_packcand("TFG.26.07.07.1080p.mp4")]
    pack = match_pack(queue_item_from_record(BY_ID), cands, _index(SCENE_A, twin),
                      ImportCompleterConfig(import_threshold=60, ambiguity_margin=10))
    assert not pack.fully_matched
    assert pack.files[0].verdict == "ambiguous"


def test_match_pack_prepopulated_movie_counts_only_if_grabbed_id():
    good = _packcand("weird.name.mp4", movie={"id": 7})   # == BY_ID movieId
    pack = match_pack(queue_item_from_record(BY_ID), [good], _index(),
                      ImportCompleterConfig())
    assert pack.fully_matched and pack.matched_movie_ids == frozenset({7})

    bad = _packcand("weird.name.mp4", movie={"id": 999})  # foreign movie -> not trusted
    pack2 = match_pack(queue_item_from_record(BY_ID), [bad], _index(),
                       ImportCompleterConfig())
    assert not pack2.fully_matched


def test_finalize_pack_requires_monitored_and_no_file():
    cands = [_packcand("TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4")]
    item = queue_item_from_record(BY_ID)
    pack = match_pack(item, cands, _index(SCENE_A), ImportCompleterConfig(import_threshold=75))
    ok = finalize_pack(item, pack, {101: (True, False)}, ImportCompleterConfig())
    assert isinstance(ok, ActionPlan) and ok.files[0]["movieId"] == 101

    has_file = finalize_pack(item, pack, {101: (True, True)}, ImportCompleterConfig())
    assert isinstance(has_file, Skip) and "hasfile" in has_file.reason.lower()

    unmonitored = finalize_pack(item, pack, {101: (False, False)}, ImportCompleterConfig())
    assert isinstance(unmonitored, Skip) and "monitor" in unmonitored.reason.lower()


def test_finalize_pack_skips_when_not_fully_matched():
    item = queue_item_from_record(BY_ID)
    pack = PackMatch(files=(FileMatch("/x.mp4", _packcand("/x.mp4"), None, "unmatched"),))
    out = finalize_pack(item, pack, {}, ImportCompleterConfig())
    assert isinstance(out, Skip)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_completer.py -q -k "match_pack or finalize_pack"`
Expected: FAIL (`ImportError: cannot import name 'match_pack'`).

- [ ] **Step 3: Implement phase-2 matching**

In `scenehound/import_completer.py`, add the import at the top (with the others):

```python
from scenehound.matcher import score
```

Append the pure functions:

```python
@dataclass(frozen=True)
class FileMatch:
    path: str
    cand: "ManualImportItem"
    movie_id: int | None
    verdict: str  # "matched" | "unmatched" | "ambiguous"


@dataclass(frozen=True)
class PackMatch:
    files: tuple[FileMatch, ...]

    @property
    def fully_matched(self) -> bool:
        return bool(self.files) and all(f.verdict == "matched" for f in self.files)

    @property
    def matched_movie_ids(self) -> frozenset[int]:
        return frozenset(f.movie_id for f in self.files if f.movie_id is not None)


def _match_one(cand: ManualImportItem, item: QueueItem, index, config) -> FileMatch:
    # A movie Whisparr pre-populated is trusted ONLY when it equals the grabbed
    # movieId (never a foreign by-ID guess we didn't make).
    if cand.movie_id is not None:
        verdict = "matched" if cand.movie_id == item.movie_id else "unmatched"
        return FileMatch(cand.path, cand, cand.movie_id if verdict == "matched" else None, verdict)
    scored = []
    for scene in index.candidates_for_title(cand.path):
        s = score(scene, cand.path, other_sites=index.other_sites_for(scene))
        scored.append((s.confidence, bool(s.strong_signals), scene.scene_id))
    scored.sort(key=lambda t: -t[0])
    if not scored or scored[0][0] < config.import_threshold or not scored[0][1]:
        return FileMatch(cand.path, cand, None, "unmatched")
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if scored[0][0] - runner_up < config.ambiguity_margin:
        return FileMatch(cand.path, cand, None, "ambiguous")
    return FileMatch(cand.path, cand, scored[0][2], "matched")  # scene_id == movieId


def match_pack(
    item: QueueItem, candidates: list[ManualImportItem], index, config: ImportCompleterConfig
) -> PackMatch:
    videos = [c for c in candidates if not c.is_sample]
    return PackMatch(tuple(_match_one(c, item, index, config) for c in videos))


def finalize_pack(
    item: QueueItem,
    pack: PackMatch,
    movie_states: dict[int, tuple[bool, bool]],
    config: ImportCompleterConfig,
) -> ActionPlan | Skip:
    if not pack.fully_matched:
        verdicts = {f.path: f.verdict for f in pack.files if f.verdict != "matched"}
        return Skip(f"pack-not-fully-matched {verdicts}")
    for mid in pack.matched_movie_ids:
        monitored, has_file = movie_states.get(mid, (False, True))
        if not monitored:
            return Skip(f"movie-not-monitored ({mid})")
        if has_file:
            return Skip(f"movie-hasfile-already ({mid})")
    files = tuple(
        _file_entry(f.path, f.movie_id, f.cand, item.download_id)
        for f in pack.files
    )
    return ActionPlan(files=files)
```

Replace the `_plan_phase2` stub from Task 5 with:

```python
    async def _plan_phase2(
        self, item: QueueItem, candidates: list[ManualImportItem]
    ) -> "ActionPlan | Skip":
        index = self._index_holder.current
        if index is None:
            return Skip("no-wanted-index")
        pack = match_pack(item, candidates, index, self._config)
        if not pack.fully_matched:
            # Log the per-file verdict table so the manual fallback is a checkbox job.
            table = [(f.path, f.verdict, f.movie_id) for f in pack.files]
            log.info("pack blocked download_id=%s verdicts=%s", item.download_id, table)
            return finalize_pack(item, pack, {}, self._config)
        movie_states: dict[int, tuple[bool, bool]] = {}
        for mid in pack.matched_movie_ids:
            movie = await self._client.fetch_movie(mid)
            movie_states[mid] = (bool(movie.get("monitored")), bool(movie.get("hasFile")))
        return finalize_pack(item, pack, movie_states, self._config)
```

- [ ] **Step 4: Write the failing corpus + service integration tests**

Create `tests/fixtures/import_pack_corpus.yaml`:

```yaml
# Phase-2 pack-filename corpus. Each entry is ONE file from a multi-file torrent.
# expect: matched => unique scene at/above import_threshold with margin;
#         unmatched => no confident scene; ambiguous => tie within margin.
# Real production mispredictions get appended here as regressions.
- filename: "TFG.26.07.07.Latex.Worship.Session.Jane.Doe.2160p.mp4"
  scenes:
    - &tfg_a {scene_id: 101, site: "That Fetish Girl", aliases: ["TFG"],
             date: 2026-07-07, title: "Latex Worship Session", performers: ["Jane Doe"]}
  expect: matched
  expect_scene_id: 101

- filename: "Random.Homemade.Clip.2018.mp4"
  scenes:
    - *tfg_a
  expect: unmatched
```

Create `tests/test_import_corpus.py`:

```python
from datetime import date
from pathlib import Path

import pytest
import yaml

from scenehound.config import ImportCompleterConfig
from scenehound.import_completer import match_pack, manual_import_from_record, queue_item_from_record
from scenehound.models import SceneFingerprint
from scenehound.wanted_index import WantedIndex

CORPUS = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "import_pack_corpus.yaml").read_text()
)
ITEM = queue_item_from_record({
    "downloadId": "H", "movieId": -1,  # -1 so no pre-population accidentally matches
    "trackedDownloadState": "importBlocked",
    "statusMessages": [{"messages": ["matched to movie by ID"]}],
})


def _scene(raw):
    d = raw["date"]
    return SceneFingerprint(
        scene_id=raw["scene_id"], site=raw["site"],
        site_aliases=tuple(raw.get("aliases", [])),
        date=d if isinstance(d, date) else date.fromisoformat(str(d)),
        title=raw["title"], performers=tuple(raw.get("performers", [])),
    )


@pytest.mark.parametrize("entry", CORPUS, ids=[e["filename"][:50] for e in CORPUS])
def test_pack_corpus(entry):
    index = WantedIndex([_scene(s) for s in entry["scenes"]])
    cand = manual_import_from_record({"path": entry["filename"], "movie": None, "rejections": []})
    pack = match_pack(ITEM, [cand], index, ImportCompleterConfig())
    (fm,) = pack.files
    assert fm.verdict == entry["expect"], (
        f"{entry['filename']}: got {fm.verdict} movie_id={fm.movie_id}")
    if entry["expect"] == "matched":
        assert fm.movie_id == entry["expect_scene_id"]
```

Append a phase-2 service test to `tests/test_import_completer.py`:

```python
async def test_phase2_all_matched_posts_one_batched_command():
    two = [
        {"path": "TFG.26.07.07.Latex.Worship.Session.Jane.Doe.1080p.mp4", "movie": None,
         "quality": {"quality": {"id": 3}}, "languages": [{"id": 1}], "rejections": []},
        {"path": "TFG.26.07.08.Rubber.Gloves.Mary.Major.1080p.mp4", "movie": None,
         "quality": {"quality": {"id": 3}}, "languages": [{"id": 1}], "rejections": []},
    ]
    spy = Spy(manual=two)

    def handler(request):
        import json as _j
        spy.calls.append((request.method, str(request.url)))
        url = str(request.url)
        if "/api/v3/queue" in url:
            return httpx.Response(200, json=QUEUE_ONE)
        if "/api/v3/manualimport" in url:
            return httpx.Response(200, json=two)
        if "/api/v3/movie/" in url:
            mid = int(url.rsplit("/", 1)[1].split("?")[0])
            return httpx.Response(200, json={"id": mid, "monitored": True, "hasFile": False})
        if url.endswith("/api/v3/command"):
            spy.posted.append(_j.loads(request.content))
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    from scenehound.config import ImportCompleterConfig
    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    from scenehound.clients.whisparr import WhisparrClient
    holder = IndexHolder()
    holder.set(WantedIndex([SCENE_A, SCENE_B]))
    ic = ImportCompleter(
        WhisparrClient("http://w:6969", "k", hc), holder,
        ImportCompleterConfig(enabled=True, dry_run=False, multipack=True,
                              grace_seconds=0, import_threshold=75),
    )
    s = await ic.sweep(now=1.0)
    assert s.acted == 1
    assert len(spy.posted) == 1  # ONE batched command for the whole pack
    assert {f["movieId"] for f in spy.posted[0]["files"]} == {101, 102}
```

- [ ] **Step 5: Run tests to verify they fail, then pass**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_corpus.py tests/test_import_completer.py -q`
Expected: FAIL first (missing symbols), then PASS after Step 3's implementation is complete. If a corpus case fails, tune `import_threshold`/`ambiguity_margin` defaults or the corpus expectation against matcher behavior — never weaken the movie-id-equality or all-or-nothing gates.

- [ ] **Step 6: Commit**

```bash
git add scenehound/import_completer.py tests/test_import_completer.py tests/test_import_corpus.py tests/fixtures/import_pack_corpus.yaml
git commit -m "feat: phase-2 all-or-nothing pack matching + pack corpus"
```

---

### Task 8: Documentation + rollout ladder

**Files:**
- Modify: `README.md`, `docker-compose.example.yml`
- Test: full suite (no new tests; docs only)

**Interfaces:**
- Consumes: final config surface from Task 2, endpoint from Task 6.
- Produces: user-facing setup docs.

- [ ] **Step 1: Add a README section**

Add a new section to `README.md` (after "Add to Whisparr", before "Logs are the UI"):

````markdown
## Auto-completing held imports (opt-in)

Some grabs download fine but Whisparr holds them: *"matched to movie by ID —
Manual Import required"* (the tracker's filename can't be scene-matched at import
time). Scenehound can auto-trigger these. **Off by default; dry-run when first
enabled.**

1. Add to `config.yaml`:

   ```yaml
   import_completer:
     enabled: true
     dry_run: true      # logs the import it WOULD fire; flip to false when satisfied
     multipack: false   # phase 2: multi-file packs, all-or-nothing (see below)
   ```

2. In Whisparr → Settings → Connect → add a **Webhook**:
   - URL: `http://<scenehound-host>:<port>/import/webhook?apikey=<your scenehound apikey>`
   - Method: POST; trigger: **On Manual Interaction Required**.
   - Click **Test** (must succeed) then **Save**.

3. Watch the logs. In dry-run you'll see `DRY-RUN import …` lines. When they look
   right, set `dry_run: false`.

**Rollout ladder:** `enabled: false` → `enabled: true, dry_run: true` (observe) →
`dry_run: false` (live) → optionally `multipack: true` (repeat the ladder).

**Multipack (phase 2)** matches every file in a pack to a wanted scene and imports
only if **all** match (a partial import would let Whisparr discard the unmatched
files). Packs that don't fully match stay held with a per-file verdict logged, so
finishing them by hand is a checkbox exercise.

Imports always use `copy` mode, so qBittorrent seeding is never disturbed.
````

- [ ] **Step 2: Document env vars in the compose example**

In `docker-compose.example.yml`, add under the existing `environment:` list:

```yaml
      # Import-completer (opt-in; off by default). See README.
      # - SCENEHOUND_IMPORT_ENABLED=true
      # - SCENEHOUND_IMPORT_DRY_RUN=true
      # - SCENEHOUND_IMPORT_MULTIPACK=false
```

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python3.12 -m pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md docker-compose.example.yml
git commit -m "docs: document opt-in import-completer + rollout ladder"
```

---

### Task 9: Final verification + PR

**Files:** none (verification + PR only)

- [ ] **Step 1: Full suite, clean run**

Run: `.venv/bin/python3.12 -m pytest -q`
Expected: PASS with the new tests present. Confirm the dry-run and disabled-guarantee tests are in the run.

- [ ] **Step 2: Confirm the disabled no-op holds**

Run: `.venv/bin/python3.12 -m pytest tests/test_import_wiring.py tests/test_api.py -q`
Expected: PASS — search path tests unchanged, wiring tests prove no route/task when disabled.

- [ ] **Step 3: Push and open the PR to GitHub origin**

```bash
git push -u origin feat/import-completer
gh pr create --repo Espionage9248/Scenehound --base main --head feat/import-completer \
  --title "Import-completer: opt-in auto-import of held by-ID matches" \
  --body "$(cat <<'EOF'
Opt-in, webhook-driven subsystem that auto-triggers Whisparr's held
"matched to movie by ID — Manual Import required" imports.

- Disabled by default and dry-run when first enabled; structurally isolated
  from the Torznab search path (no route/task when disabled).
- Webhook is a doorbell; the queue API is the source of truth. Webhook,
  reconcile, and startup share one idempotent sweep.
- Phase 1: single-file by-ID auto-import behind a conservative gate
  (held-state + marker, movie-id equality, single video file, zero
  rejections, grace period). Bounded retry then park.
- Phase 2 (opt-in `multipack`): all-or-nothing pack matching with a stricter
  bar (threshold + strong signals + uniqueness margin) and monitored/hasFile
  re-check; never partial-imports a pack.
- importMode=copy always (seeding safe).

Spec: docs/superpowers/specs/2026-07-12-import-completer-design.md
Plan: docs/superpowers/plans/2026-07-12-import-completer.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage** (spec § → task):
- Doorbell architecture, one sweep for webhook/reconcile/startup → Tasks 5, 6.
- Webhook endpoint, apikey auth, Test-event 200, fast 200 → Task 6.
- Phase-1 gate (held-state+marker, movieId equality, single video, zero rejections, grace, verbatim metadata, copy mode) → Task 4 (`plan_phase1`) + Task 5 (grace/orchestration).
- Retry/park (cap, park, process-local) → Task 5.
- Phase-2 (threshold+strong+margin, all-or-nothing, monitored/hasFile re-check, batched POST, verdict logging, studio-derivation deferred) → Task 7.
- Config surface + env overrides + rollout ladder → Tasks 2, 8.
- Idempotency/dedup (item leaves queue; process-local state) → Task 5.
- Client extensions (queue/manualimport/movie/command) → Task 3.
- Testing (decision-function fixtures, corpus, three disabled/dry-run guarantees incl. zero-non-GET spy) → Tasks 4–7 (`test_dry_run_fires_no_post_and_only_gets`, `test_import_wiring.py`).
- Probes-first → Task 1.
- Out of scope (rename, search-path changes, persistence, studio-derivation, rejection allowlist, `partial_import`) → not implemented; noted in spec.

**Placeholder scan:** No "TBD"/"handle appropriately" — every code step carries full code. The Task 5 `_plan_phase2` stub is an intentional, named interim (`Skip("phase2-not-implemented")`) explicitly replaced in Task 7, not a placeholder.

**Type consistency:** `ActionPlan(files: tuple[dict,...])` / `Skip(reason: str)` used identically in Tasks 4, 5, 7. `plan_phase1(item, candidates, config)`, `match_pack(item, candidates, index, config)`, `finalize_pack(item, pack, movie_states, config)` signatures match their call sites in `ImportCompleter._plan` / `_plan_phase2`. `SweepSummary` fields (`acted/skipped/waited/parked`) match assertions. `queue_item_from_record` / `manual_import_from_record` names consistent across tests and impl. `notify()` / `sweep(now)` / `run()` consistent between service and webhook/tests.

**Known assumptions flagged for probe reconciliation (Task 1):** Whisparr field names (`trackedDownloadState`, `statusMessages[].messages[]`, `movieId`, `downloadId`; manualimport `movie.id`, `rejections`, `quality`, `languages`, `releaseGroup`, sample flag), the ManualImport command body (`POST /api/v3/command {name, importMode, files}`), and the webhook `eventType` string. All parsing is defensive; deltas are localized to the parse functions + inline synthetic fixtures.
