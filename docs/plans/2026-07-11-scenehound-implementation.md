# Scenehound Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Scenehound, a Torznab matching proxy between Whisparr v3 and Prowlarr that identifies badly-named scene releases on private trackers and returns them to Whisparr with canonical, parseable titles.

**Architecture:** A single stateless Python service. Whisparr queries Scenehound as a Torznab indexer; Scenehound resolves queries to scene fingerprints via an in-memory index of Whisparr's wanted list, fans adaptive query variants out to Prowlarr, scores candidates with a pure-function matcher (two-strong-signal rule), and returns rewritten results. Every failure degrades to passthrough. See `docs/plans/2026-07-11-scenehound-design.md` — the design doc is the authority on behaviour; this plan is the authority on construction order.

**Tech Stack:** Python 3.12, FastAPI, httpx, rapidfuzz, PyYAML, uvicorn. Tests: pytest + pytest-asyncio, httpx.MockTransport (no extra mock deps). Docker (python:3.12-slim), Unraid template, Forgejo Actions CI.

## Global Constraints

- Python `>=3.12`. Runtime deps ONLY: `fastapi`, `httpx`, `rapidfuzz`, `pyyaml`, `uvicorn`. Dev deps ONLY: `pytest`, `pytest-asyncio`.
- Service listens on port `9797`. All persistent files live under a single config dir (default `/config`, overridable via `SCENEHOUND_CONFIG_DIR` for tests).
- Defaults (exact values): match threshold `75`, max queries per search `5`, rate-limit burst `4`, refill `15.0` seconds/token, wanted-index refresh `900` seconds, per-request time budget `45` seconds, single-strong-signal confidence cap `65`.
- **Never fabricate quality tokens.** No size-based quality estimation anywhere.
- **Degrade to passthrough, never block**: any resolution failure forwards the query verbatim to Prowlarr and returns results unrewritten.
- All tracker-bound *search* queries go through Prowlarr and are gated by the per-indexer token bucket. The single RSS fetch per sync is NOT bucket-gated (identical cost to status-quo RSS).
- Matcher, rewriter, planner, normalizer, date engine: pure functions, zero I/O.
- Structured key=value logging to stdout only. No database.
- Commit after every task. Conventional-commit style messages (`feat:`, `test:`, `docs:`, `build:`, `ci:`).

## File Structure

```
scenehound/
  __init__.py           # empty
  config.py             # YAML + env config, API key generation
  models.py             # SceneFingerprint, ReleaseCandidate, dataclasses
  normalize.py          # squash, tokenize, content_tokens, junk-token set
  dates.py              # query-term parsing, date extraction from titles
  matcher.py            # scoring, two-strong-signal rule, vetoes
  rewriter.py           # quality token extraction, canonical title emission
  query_planner.py      # adaptive query variant generation
  wanted_index.py       # date + token indexes over the wanted list
  rate_limiter.py       # per-indexer token bucket
  torznab.py            # Torznab XML parse/build/caps/error
  clients/
    __init__.py         # empty
    whisparr.py         # wanted-list fetch, record→fingerprint mapping
    prowlarr.py         # Torznab search against real indexers
  api.py                # /indexer/{slug}/api orchestration, /healthz
  app.py                # app factory, lifespan, background refresh, logging setup
tests/
  conftest.py
  fixtures/corpus.yaml
  fixtures/whisparr_wanted_sample.json   (captured in Task 11)
  test_config.py  test_normalize.py  test_dates.py  test_rate_limiter.py
  test_torznab.py test_rewriter.py   test_matcher.py test_corpus.py
  test_query_planner.py test_wanted_index.py
  test_whisparr_client.py test_prowlarr_client.py test_api.py test_app.py
pyproject.toml
Dockerfile
.dockerignore
docker-compose.example.yml
unraid/scenehound.xml
README.md
.forgejo/workflows/ci.yml
```

---

### Task 1: Project scaffold + config loader

**Files:**
- Create: `pyproject.toml`, `scenehound/__init__.py`, `scenehound/config.py`, `tests/test_config.py`, `.gitignore`

**Interfaces:**
- Produces: `load_config(config_dir: Path, env: Mapping[str, str]) -> Config` and the frozen dataclasses `Config`, `ServiceConfig`, `IndexerConfig`, `MatchingConfig`, `RateLimitConfig`. Later tasks read `config.whisparr.url`, `config.whisparr.api_key`, `config.prowlarr.url`, `config.prowlarr.api_key`, `config.indexers` (tuple of `IndexerConfig(slug, prowlarr_id)`), `config.matching.threshold`, `config.matching.max_queries_per_search`, `config.rate_limit.burst`, `config.rate_limit.refill_seconds`, `config.api_key`, `config.log_level`.

- [ ] **Step 1: Write scaffold files**

`pyproject.toml`:

```toml
[project]
name = "scenehound"
version = "0.1.0"
description = "Torznab matching proxy between Whisparr and Prowlarr"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "httpx>=0.27",
    "rapidfuzz>=3.9",
    "pyyaml>=6.0",
    "uvicorn[standard]>=0.30",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["scenehound*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

`.gitignore`:

```
__pycache__/
*.egg-info/
.venv/
.pytest_cache/
```

`scenehound/__init__.py`: empty file.

Then create the venv and install:

Run: `cd /Users/jamesking/VS/Scenehound && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
Expected: installs without error. All later `pytest` commands mean `.venv/bin/pytest`.

- [ ] **Step 2: Write the failing tests**

`tests/test_config.py`:

```python
from pathlib import Path

import pytest

from scenehound.config import Config, load_config

MINIMAL_YAML = """
whisparr:
  url: http://w:6969
  api_key: wkey
prowlarr:
  url: http://p:9696
  api_key: pkey
indexers:
  - slug: empornium
    prowlarr_id: 12
  - slug: happyfappy
    prowlarr_id: 15
"""


def write_config(tmp_path: Path, text: str = MINIMAL_YAML) -> Path:
    (tmp_path / "config.yaml").write_text(text)
    return tmp_path


def test_loads_yaml_with_defaults(tmp_path):
    cfg = load_config(write_config(tmp_path), env={})
    assert cfg.whisparr.url == "http://w:6969"
    assert cfg.prowlarr.api_key == "pkey"
    assert [i.slug for i in cfg.indexers] == ["empornium", "happyfappy"]
    assert cfg.indexers[0].prowlarr_id == 12
    assert cfg.matching.threshold == 75
    assert cfg.matching.max_queries_per_search == 5
    assert cfg.rate_limit.burst == 4
    assert cfg.rate_limit.refill_seconds == 15.0
    assert cfg.log_level == "info"


def test_env_overrides_win(tmp_path):
    env = {
        "WHISPARR_URL": "http://other:1",
        "WHISPARR_API_KEY": "envkey",
        "PROWLARR_URL": "http://other:2",
        "PROWLARR_API_KEY": "envkey2",
        "SCENEHOUND_THRESHOLD": "80",
        "SCENEHOUND_LOG_LEVEL": "debug",
    }
    cfg = load_config(write_config(tmp_path), env=env)
    assert cfg.whisparr.url == "http://other:1"
    assert cfg.whisparr.api_key == "envkey"
    assert cfg.matching.threshold == 80
    assert cfg.log_level == "debug"


def test_api_key_generated_and_persisted(tmp_path):
    cfg1 = load_config(write_config(tmp_path), env={})
    assert len(cfg1.api_key) >= 32
    cfg2 = load_config(tmp_path, env={})
    assert cfg2.api_key == cfg1.api_key  # persisted to apikey file
    assert (tmp_path / "apikey").read_text().strip() == cfg1.api_key


def test_api_key_env_override(tmp_path):
    cfg = load_config(write_config(tmp_path), env={"SCENEHOUND_API_KEY": "fixed"})
    assert cfg.api_key == "fixed"


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path, env={})
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `scenehound.config`.

- [ ] **Step 4: Implement `scenehound/config.py`**

```python
"""Configuration: YAML file + explicit env-var overrides."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml


@dataclass(frozen=True)
class ServiceConfig:
    url: str
    api_key: str


@dataclass(frozen=True)
class IndexerConfig:
    slug: str
    prowlarr_id: int


@dataclass(frozen=True)
class MatchingConfig:
    threshold: int = 75
    max_queries_per_search: int = 5


@dataclass(frozen=True)
class RateLimitConfig:
    burst: int = 4
    refill_seconds: float = 15.0


@dataclass(frozen=True)
class Config:
    whisparr: ServiceConfig
    prowlarr: ServiceConfig
    indexers: tuple[IndexerConfig, ...]
    api_key: str
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    log_level: str = "info"


def _service(raw: dict, env: Mapping[str, str], prefix: str) -> ServiceConfig:
    return ServiceConfig(
        url=env.get(f"{prefix}_URL", raw.get("url", "")).rstrip("/"),
        api_key=env.get(f"{prefix}_API_KEY", raw.get("api_key", "")),
    )


def _scenehound_api_key(raw: dict, env: Mapping[str, str], config_dir: Path) -> str:
    if key := env.get("SCENEHOUND_API_KEY", raw.get("api_key", "")):
        return key
    keyfile = config_dir / "apikey"
    if keyfile.exists():
        return keyfile.read_text().strip()
    key = secrets.token_hex(16)
    keyfile.write_text(key + "\n")
    return key


def load_config(config_dir: Path, env: Mapping[str, str]) -> Config:
    path = config_dir / "config.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    m = raw.get("matching", {})
    r = raw.get("rate_limit", {})
    return Config(
        whisparr=_service(raw.get("whisparr", {}), env, "WHISPARR"),
        prowlarr=_service(raw.get("prowlarr", {}), env, "PROWLARR"),
        indexers=tuple(
            IndexerConfig(slug=i["slug"], prowlarr_id=int(i["prowlarr_id"]))
            for i in raw.get("indexers", [])
        ),
        api_key=_scenehound_api_key(raw, env, config_dir),
        matching=MatchingConfig(
            threshold=int(env.get("SCENEHOUND_THRESHOLD", m.get("threshold", 75))),
            max_queries_per_search=int(
                env.get("SCENEHOUND_MAX_QUERIES", m.get("max_queries_per_search", 5))
            ),
        ),
        rate_limit=RateLimitConfig(
            burst=int(env.get("SCENEHOUND_RATE_BURST", r.get("burst", 4))),
            refill_seconds=float(
                env.get("SCENEHOUND_RATE_REFILL", r.get("refill_seconds", 15.0))
            ),
        ),
        log_level=env.get("SCENEHOUND_LOG_LEVEL", raw.get("log_level", "info")),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore scenehound/ tests/
git commit -m "feat: project scaffold and config loader"
```

---

### Task 2: Models + normalization

**Files:**
- Create: `scenehound/models.py`, `scenehound/normalize.py`, `tests/test_normalize.py`

**Interfaces:**
- Produces: `SceneFingerprint(scene_id: int, site: str, site_aliases: tuple[str, ...], date: datetime.date, title: str, performers: tuple[str, ...])` (frozen); `ReleaseCandidate(title: str, guid: str, link: str, size: int | None, seeders: int | None, leechers: int | None, categories: tuple[int, ...], pub_date: str | None, raw_attrs: dict[str, str])`; `normalize.squash(s: str) -> str`; `normalize.tokenize(s: str) -> list[str]`; `normalize.content_tokens(s: str) -> list[str]`; `normalize.JUNK_TOKENS: frozenset[str]`.

- [ ] **Step 1: Write `scenehound/models.py`** (pure data, no test cycle of its own)

```python
"""Core data types shared across all modules."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SceneFingerprint:
    scene_id: int
    site: str
    site_aliases: tuple[str, ...]
    date: datetime.date
    title: str
    performers: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseCandidate:
    title: str
    guid: str
    link: str
    size: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    categories: tuple[int, ...] = ()
    pub_date: str | None = None
    raw_attrs: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_normalize.py`:

```python
from scenehound.normalize import content_tokens, squash, tokenize


def test_squash_strips_case_punctuation_spacing():
    assert squash("That Fetish Girl!") == "thatfetishgirl"
    assert squash("Scott Stark Studios") == "scottstarkstudios"
    assert squash("h.265") == "h265"


def test_tokenize_splits_on_non_alnum():
    assert tokenize("Site.2026-07-07.Some.Title.XXX.1080p") == [
        "site", "2026", "07", "07", "some", "title", "xxx", "1080p",
    ]


def test_content_tokens_drops_junk_and_bare_numbers():
    assert content_tokens("Site.Name.Great.Scene.XXX.1080p.WEB-DL.x265-GRP") == [
        "site", "name", "great", "scene", "grp",
    ]
    assert content_tokens("2026 07 07") == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.normalize`.

- [ ] **Step 4: Implement `scenehound/normalize.py`**

```python
"""Text normalization shared by the matcher, rewriter, planner, and index."""
from __future__ import annotations

import re

# Tokens that carry no identity information: containers, codecs, sources,
# resolutions, and scene-release filler. Lowercase.
JUNK_TOKENS: frozenset[str] = frozenset({
    "xxx", "mp4", "wmv", "avi", "mkv", "mov", "ts",
    "480p", "540p", "720p", "1080p", "2160p", "480", "540", "720", "1080", "2160",
    "4k", "uhd", "hd", "sd", "fhd", "qhd",
    "web", "webdl", "webrip", "web-dl", "hdrip", "dvdrip", "dvd",
    "h264", "h265", "x264", "x265", "hevc", "avc", "av1",
    "aac", "ac3", "mp3", "flac",
    "repack", "internal", "remastered", "proper", "readnfo",
    "siterip", "split", "scenes", "psychoporn", "rq", "kleenex", "kt",
})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_SPLIT = re.compile(r"[^a-zA-Z0-9]+")


def squash(s: str) -> str:
    """Lowercase and strip everything that is not a letter or digit."""
    return _NON_ALNUM.sub("", s.lower())


def tokenize(s: str) -> list[str]:
    """Lowercase tokens split on any non-alphanumeric run."""
    return [t for t in _SPLIT.split(s.lower()) if t]


def content_tokens(s: str) -> list[str]:
    """Tokens that plausibly identify content: junk and bare numbers removed."""
    return [t for t in tokenize(s) if t not in JUNK_TOKENS and not t.isdigit()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_normalize.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add scenehound/models.py scenehound/normalize.py tests/test_normalize.py
git commit -m "feat: core models and text normalization"
```

---

### Task 3: Date engine

**Files:**
- Create: `scenehound/dates.py`, `tests/test_dates.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ParsedQuery(site_token: str, dates: tuple[datetime.date, ...])` (frozen dataclass); `parse_query_term(term: str) -> ParsedQuery | None` (Whisparr's `<site> <dd.mm.yyyy>` format; ambiguous day≤12 yields both interpretations, dd.mm first); `extract_dates(text: str) -> frozenset[datetime.date]` (all plausible dates found in a release title, all formats, both interpretations of ambiguous ones).

- [ ] **Step 1: Write the failing tests**

`tests/test_dates.py`:

```python
from datetime import date

from scenehound.dates import extract_dates, parse_query_term


def test_parse_query_term_unambiguous_day():
    q = parse_query_term("thatfetishgirl 25.03.2026")
    assert q.site_token == "thatfetishgirl"
    assert q.dates == (date(2026, 3, 25),)  # dd.mm.yyyy


def test_parse_query_term_ambiguous_yields_both_ddmm_first():
    q = parse_query_term("Scott Stark Studios 05.07.2026")
    assert q.site_token == "Scott Stark Studios"
    assert q.dates == (date(2026, 7, 5), date(2026, 5, 7))


def test_parse_query_term_symmetric_date_single():
    q = parse_query_term("site 07.07.2026")
    assert q.dates == (date(2026, 7, 7),)


def test_parse_query_term_rejects_non_matching():
    assert parse_query_term("no date here") is None
    assert parse_query_term("") is None


def test_extract_iso_and_dotted():
    assert date(2026, 7, 7) in extract_dates("Site.2026-07-07.Title")
    assert date(2026, 7, 7) in extract_dates("Site 2026.07.07 Title")


def test_extract_two_digit_year_scene_format():
    # 26.07.05 → yy.mm.dd is the dominant scene convention
    ds = extract_dates("Site.26.07.05.Title.XXX")
    assert date(2026, 7, 5) in ds


def test_extract_dmy_and_mdy_four_digit_year():
    ds = extract_dates("released 12-07-2026 in HD")
    assert date(2026, 7, 12) in ds and date(2026, 12, 7) in ds


def test_extract_ignores_resolutions_and_garbage():
    assert extract_dates("Site.Title.1080p.x265") == frozenset()
    assert extract_dates("no dates at all") == frozenset()


def test_extract_implausible_years_dropped():
    assert extract_dates("Thing.1085-01-01.wat") == frozenset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_dates.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.dates`.

- [ ] **Step 3: Implement `scenehound/dates.py`**

```python
"""Date parsing: Whisparr query terms and dates embedded in release titles."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Plausible content-date window. Wide on purpose; the matcher compares against
# a specific scene date anyway.
_MIN_YEAR, _MAX_YEAR = 1990, 2049

_QUERY_RE = re.compile(r"^(?P<site>.*\S)\s+(?P<a>\d{2})\.(?P<b>\d{2})\.(?P<y>\d{4})$")

_SEP = r"[.\-_/ ]"
# Order matters only for readability; results are set-unioned.
_YMD4 = re.compile(rf"(?<![\d])(\d{{4}}){_SEP}(\d{{1,2}}){_SEP}(\d{{1,2}})(?![\d])")
_XY4 = re.compile(rf"(?<![\d])(\d{{1,2}}){_SEP}(\d{{1,2}}){_SEP}(\d{{4}})(?![\d])")
_TRIPLE2 = re.compile(rf"(?<![\d])(\d{{2}}){_SEP}(\d{{2}}){_SEP}(\d{{2}})(?![\d])")


@dataclass(frozen=True)
class ParsedQuery:
    site_token: str
    dates: tuple[date, ...]


def _valid(y: int, m: int, d: int) -> date | None:
    if not (_MIN_YEAR <= y <= _MAX_YEAR):
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _expand_two_digit_year(yy: int) -> int:
    return 2000 + yy if yy <= 49 else 1900 + yy


def parse_query_term(term: str) -> ParsedQuery | None:
    """Parse Whisparr's search term: '<site> <dd.mm.yyyy>' (format confirmed
    from live logs). Ambiguous day<=12 also yields the mm.dd reading, dd.mm
    first so callers preferring the primary interpretation take index 0."""
    m = _QUERY_RE.match(term.strip())
    if not m:
        return None
    a, b, y = int(m["a"]), int(m["b"]), int(m["y"])
    dates: list[date] = []
    if primary := _valid(y, b, a):  # dd.mm.yyyy
        dates.append(primary)
    if a <= 12 and a != b:
        if alt := _valid(y, a, b):  # mm.dd.yyyy reading
            if alt not in dates:
                dates.append(alt)
    if not dates:
        return None
    return ParsedQuery(site_token=m["site"], dates=tuple(dates))


def extract_dates(text: str) -> frozenset[date]:
    """Every plausible date found in a release title, across formats, with
    both interpretations of ambiguous day/month orderings."""
    found: set[date] = set()
    for m in _YMD4.finditer(text):
        y, b, c = int(m[1]), int(m[2]), int(m[3])
        for mo, dy in ((b, c), (c, b)):
            if d := _valid(y, mo, dy):
                found.add(d)
    for m in _XY4.finditer(text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        for mo, dy in ((b, a), (a, b)):
            if d := _valid(y, mo, dy):
                found.add(d)
    for m in _TRIPLE2.finditer(text):
        a, b, c = int(m[1]), int(m[2]), int(m[3])
        # yy.mm.dd (dominant scene convention), dd.mm.yy, mm.dd.yy
        for y2, mo, dy in ((a, b, c), (c, b, a), (c, a, b)):
            if d := _valid(_expand_two_digit_year(y2), mo, dy):
                found.add(d)
    return frozenset(found)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dates.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add scenehound/dates.py tests/test_dates.py
git commit -m "feat: date engine for query terms and release titles"
```

---

### Task 4: Token-bucket rate limiter

**Files:**
- Create: `scenehound/rate_limiter.py`, `tests/test_rate_limiter.py`

**Interfaces:**
- Produces: `TokenBucket(burst: int, refill_seconds: float, clock: Callable[[], float] = time.monotonic)` with `try_acquire() -> bool` (non-blocking, thread/task-safe enough for asyncio single-loop use).

- [ ] **Step 1: Write the failing tests**

`tests/test_rate_limiter.py`:

```python
from scenehound.rate_limiter import TokenBucket


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_burst_then_deny():
    clock = FakeClock()
    b = TokenBucket(burst=4, refill_seconds=15.0, clock=clock)
    assert [b.try_acquire() for _ in range(4)] == [True] * 4
    assert b.try_acquire() is False


def test_refills_one_token_per_interval():
    clock = FakeClock()
    b = TokenBucket(burst=4, refill_seconds=15.0, clock=clock)
    for _ in range(4):
        b.try_acquire()
    clock.now = 14.9
    assert b.try_acquire() is False
    clock.now = 15.0
    assert b.try_acquire() is True
    assert b.try_acquire() is False


def test_never_exceeds_burst_capacity():
    clock = FakeClock()
    b = TokenBucket(burst=2, refill_seconds=1.0, clock=clock)
    clock.now = 1000.0
    assert [b.try_acquire() for _ in range(3)] == [True, True, False]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rate_limiter.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.rate_limiter`.

- [ ] **Step 3: Implement `scenehound/rate_limiter.py`**

```python
"""Per-indexer token bucket gating all tracker-bound search queries."""
from __future__ import annotations

import time
from typing import Callable


class TokenBucket:
    def __init__(
        self,
        burst: int,
        refill_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._refill_seconds = refill_seconds
        self._clock = clock
        self._updated = clock()

    def try_acquire(self) -> bool:
        now = self._clock()
        elapsed = now - self._updated
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed / self._refill_seconds)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rate_limiter.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scenehound/rate_limiter.py tests/test_rate_limiter.py
git commit -m "feat: token-bucket rate limiter"
```

---

### Task 5: Torznab XML — parse, build, caps, error

**Files:**
- Create: `scenehound/torznab.py`, `tests/test_torznab.py`

**Interfaces:**
- Consumes: `ReleaseCandidate` from `scenehound.models`.
- Produces: `parse_feed(xml: bytes) -> list[ReleaseCandidate]`; `FeedEntry(candidate: ReleaseCandidate, title_override: str | None = None)` (frozen dataclass — `title_override=None` means passthrough); `build_feed(entries: Sequence[FeedEntry]) -> bytes`; `build_caps() -> bytes`; `build_error(code: int, description: str) -> bytes`.

- [ ] **Step 1: Write the failing tests**

`tests/test_torznab.py`:

```python
import xml.etree.ElementTree as ET

from scenehound.models import ReleaseCandidate
from scenehound.torznab import FeedEntry, build_caps, build_error, build_feed, parse_feed

PROWLARR_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>Empornium</title>
    <item>
      <title>Messy.Release.Name.26.07.05.XXX</title>
      <guid>https://tracker/torrents.php?id=111</guid>
      <link>http://prowlarr:9696/12/download?apikey=k&amp;link=abc</link>
      <size>1073741824</size>
      <pubDate>Sun, 05 Jul 2026 10:00:00 +0000</pubDate>
      <torznab:attr name="seeders" value="12"/>
      <torznab:attr name="peers" value="15"/>
      <torznab:attr name="category" value="6000"/>
      <torznab:attr name="downloadvolumefactor" value="0"/>
    </item>
  </channel>
</rss>"""


def test_parse_feed_extracts_candidates():
    items = parse_feed(PROWLARR_FEED)
    assert len(items) == 1
    c = items[0]
    assert c.title == "Messy.Release.Name.26.07.05.XXX"
    assert c.guid == "https://tracker/torrents.php?id=111"
    assert c.link.startswith("http://prowlarr:9696/12/download")
    assert c.size == 1073741824
    assert c.seeders == 12
    assert c.categories == (6000,)
    assert c.raw_attrs["downloadvolumefactor"] == "0"


def test_roundtrip_preserves_everything_except_title():
    c = parse_feed(PROWLARR_FEED)[0]
    out = build_feed([FeedEntry(c, title_override="Site.2026-07-05.Title.XXX")])
    reparsed = parse_feed(out)[0]
    assert reparsed.title == "Site.2026-07-05.Title.XXX"
    assert reparsed.guid == c.guid
    assert reparsed.link == c.link
    assert reparsed.size == c.size
    assert reparsed.seeders == c.seeders
    assert reparsed.raw_attrs["downloadvolumefactor"] == "0"
    # original title preserved for audit
    root = ET.fromstring(out)
    ns = {"torznab": "http://torznab.com/schemas/2015/feed"}
    attrs = root.findall(".//item/torznab:attr", ns)
    orig = [a for a in attrs if a.get("name") == "scenehound_original_title"]
    assert orig and orig[0].get("value") == c.title


def test_passthrough_entry_keeps_title():
    c = parse_feed(PROWLARR_FEED)[0]
    out = build_feed([FeedEntry(c)])
    assert parse_feed(out)[0].title == c.title


def test_caps_advertises_q_search_and_adult_categories():
    root = ET.fromstring(build_caps())
    search = root.find("searching/search")
    assert search.get("available") == "yes"
    assert "q" in search.get("supportedParams")
    cat_ids = {c.get("id") for c in root.findall("categories/category")}
    assert {"6000", "6010"} <= cat_ids


def test_error_shape():
    root = ET.fromstring(build_error(100, "Incorrect user credentials"))
    assert root.tag == "error"
    assert root.get("code") == "100"
    assert root.get("description") == "Incorrect user credentials"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_torznab.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.torznab`.

- [ ] **Step 3: Implement `scenehound/torznab.py`**

```python
"""Torznab XML: parsing Prowlarr feeds and building feeds for Whisparr."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Sequence

from scenehound.models import ReleaseCandidate

TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
_NS = {"torznab": TORZNAB_NS}
ORIGINAL_TITLE_ATTR = "scenehound_original_title"


@dataclass(frozen=True)
class FeedEntry:
    candidate: ReleaseCandidate
    title_override: str | None = None


def parse_feed(xml: bytes) -> list[ReleaseCandidate]:
    root = ET.fromstring(xml)
    out: list[ReleaseCandidate] = []
    for item in root.findall(".//channel/item"):
        attrs: dict[str, str] = {}
        categories: list[int] = []
        for a in item.findall("torznab:attr", _NS):
            name, value = a.get("name", ""), a.get("value", "")
            if name == "category":
                try:
                    categories.append(int(value))
                except ValueError:
                    pass
            else:
                attrs[name] = value
        size_text = item.findtext("size")
        out.append(
            ReleaseCandidate(
                title=item.findtext("title", ""),
                guid=item.findtext("guid", ""),
                link=item.findtext("link", ""),
                size=int(size_text) if size_text and size_text.isdigit() else None,
                seeders=int(attrs["seeders"]) if attrs.get("seeders", "").isdigit() else None,
                leechers=None,
                categories=tuple(categories),
                pub_date=item.findtext("pubDate"),
                raw_attrs=attrs,
            )
        )
    return out


def _sub(parent: ET.Element, tag: str, text: str | None) -> None:
    if text is not None:
        ET.SubElement(parent, tag).text = text


def _attr(item: ET.Element, name: str, value: str) -> None:
    ET.SubElement(item, f"{{{TORZNAB_NS}}}attr", {"name": name, "value": value})


def build_feed(entries: Sequence[FeedEntry]) -> bytes:
    ET.register_namespace("torznab", TORZNAB_NS)
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    _sub(channel, "title", "Scenehound")
    for entry in entries:
        c = entry.candidate
        item = ET.SubElement(channel, "item")
        _sub(item, "title", entry.title_override or c.title)
        _sub(item, "guid", c.guid)
        _sub(item, "link", c.link)
        _sub(item, "size", str(c.size) if c.size is not None else None)
        _sub(item, "pubDate", c.pub_date)
        for cat in c.categories:
            _attr(item, "category", str(cat))
        for name, value in c.raw_attrs.items():
            _attr(item, name, value)
        if c.seeders is not None and "seeders" not in c.raw_attrs:
            _attr(item, "seeders", str(c.seeders))
        if entry.title_override is not None:
            _attr(item, ORIGINAL_TITLE_ATTR, c.title)
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def build_caps() -> bytes:
    caps = ET.Element("caps")
    searching = ET.SubElement(caps, "searching")
    ET.SubElement(searching, "search", {"available": "yes", "supportedParams": "q"})
    cats = ET.SubElement(caps, "categories")
    adult = ET.SubElement(cats, "category", {"id": "6000", "name": "XXX"})
    ET.SubElement(adult, "subcat", {"id": "6010", "name": "XXX/DVD"})
    ET.SubElement(cats, "category", {"id": "6010", "name": "XXX/DVD"})
    return ET.tostring(caps, encoding="utf-8", xml_declaration=True)


def build_error(code: int, description: str) -> bytes:
    el = ET.Element("error", {"code": str(code), "description": description})
    return ET.tostring(el, encoding="utf-8", xml_declaration=True)
```

Note: `parse_feed` folds `seeders` into `raw_attrs` as well as the typed field; `build_feed` re-emits `raw_attrs` verbatim, which round-trips every torznab attr (peers, volume factors, etc.) without Scenehound needing to understand them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_torznab.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scenehound/torznab.py tests/test_torznab.py
git commit -m "feat: torznab XML parse/build/caps/error"
```

---

### Task 6: Quality extraction + title rewriter

**Files:**
- Create: `scenehound/rewriter.py`, `tests/test_rewriter.py`

**Interfaces:**
- Consumes: `SceneFingerprint`.
- Produces: `extract_quality_tokens(title: str) -> tuple[str, ...]` (canonicalized resolution/source/codec tokens, empty when none found — NEVER fabricated); `rewrite_title(scene: SceneFingerprint, original_title: str) -> str` emitting `Site.Name.YYYY-MM-DD.Scene.Title.XXX[.tokens]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_rewriter.py`:

```python
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


def test_rewrite_full():
    out = rewrite_title(SCENE, "messy jane doe 07/07/26 [1080] x264")
    assert out == "That.Fetish.Girl.2026-07-07.Some.Great.Scene.XXX.1080p.x264"


def test_rewrite_without_quality():
    out = rewrite_title(SCENE, "messy jane doe title only")
    assert out == "That.Fetish.Girl.2026-07-07.Some.Great.Scene.XXX"


def test_rewrite_sanitizes_weird_chars():
    scene = SceneFingerprint(2, "Site!", (), date(2026, 1, 2), "What?! A #Title", ())
    assert rewrite_title(scene, "x") == "Site.2026-01-02.What.A.Title.XXX"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rewriter.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.rewriter`.

- [ ] **Step 3: Implement `scenehound/rewriter.py`**

```python
"""Canonical title emission. Quality tokens are extracted from the original
title only — Scenehound never fabricates quality."""
from __future__ import annotations

import re

from scenehound.models import SceneFingerprint

# (pattern, canonical token). Order = emission order: resolution, source, codec.
# Patterns must not match inside date fragments; \b plus explicit non-dot
# guards handle bracketed bare resolutions like [1080] without eating 07.05.
_RESOLUTION = [
    (re.compile(r"\b(2160p|4k|uhd)\b", re.I), "2160p"),
    (re.compile(r"\b1080p\b|\[1080\]|\b1080(?=\s|$)", re.I), "1080p"),
    (re.compile(r"\b720p\b|\[720\]|\b720(?=\s|$)", re.I), "720p"),
    (re.compile(r"\b(480p|540p)\b", re.I), "480p"),
]
_SOURCE = [
    (re.compile(r"\bweb-?dl\b", re.I), "WEB-DL"),
    (re.compile(r"\bwebrip\b", re.I), "WEBRip"),
]
_CODEC = [
    (re.compile(r"\b(x265|h\.?265|hevc)\b", re.I), "x265"),
    (re.compile(r"\b(x264|h\.?264|avc)\b", re.I), "x264"),
]

_SANITIZE = re.compile(r"[^A-Za-z0-9]+")


def extract_quality_tokens(title: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for group in (_RESOLUTION, _SOURCE, _CODEC):
        for pattern, canonical in group:
            if pattern.search(title):
                tokens.append(canonical)
                break  # one token per group
    return tuple(tokens)


def _dotify(text: str) -> str:
    return _SANITIZE.sub(".", text).strip(".")


def rewrite_title(scene: SceneFingerprint, original_title: str) -> str:
    parts = [
        _dotify(scene.site),
        scene.date.isoformat(),
        _dotify(scene.title),
        "XXX",
        *extract_quality_tokens(original_title),
    ]
    return ".".join(p for p in parts if p)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rewriter.py -v`
Expected: 7 passed. If `test_date_fragments_not_mistaken_for_resolution` fails, tighten the bare-number resolution patterns — the fix must keep `[1080]` matching while rejecting digits adjacent to dots.

- [ ] **Step 5: Commit**

```bash
git add scenehound/rewriter.py tests/test_rewriter.py
git commit -m "feat: quality token extraction and canonical title rewriter"
```

---

### Task 7: Matcher

**Files:**
- Create: `scenehound/matcher.py`, `tests/test_matcher.py`

**Interfaces:**
- Consumes: `SceneFingerprint`, `normalize.squash/content_tokens`, `dates.extract_dates`.
- Produces: `MatchScore(confidence: int, strong_signals: tuple[str, ...], veto: str | None, detail: dict[str, float])` (frozen); `score(scene: SceneFingerprint, title: str, other_sites: frozenset[str] = frozenset()) -> MatchScore`. `other_sites` is squashed site names of *other* wanted sites, used for site-contradiction vetoes. Constants: `STRONG_DATE=40, STRONG_SITE=35, STRONG_PERFORMER=35, MULTI_PERFORMER_BONUS=15, STRONG_TITLE=40, TITLE_MAX=25, SINGLE_SIGNAL_CAP=65`. Title similarity is medium by default but counts as a strong signal (`STRONG_TITLE`) when token-set ratio ≥ 95 — a near-exact title match is real evidence (this is what lets `site + exact title, no date` releases clear the threshold: 35 + 40 = 75).

- [ ] **Step 1: Write the failing tests**

`tests/test_matcher.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_matcher.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.matcher`.

- [ ] **Step 3: Implement `scenehound/matcher.py`**

```python
"""Scoring of candidate release titles against scene fingerprints.

Pure functions. The two-strong-signal rule and contradiction vetoes are the
core false-positive defenses — change them only with corpus evidence."""
from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from scenehound.dates import extract_dates
from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens, squash

STRONG_DATE = 40
STRONG_SITE = 35
STRONG_PERFORMER = 35
MULTI_PERFORMER_BONUS = 15
STRONG_TITLE = 40
TITLE_MAX = 25
SINGLE_SIGNAL_CAP = 65
_FUZZY_SITE_MIN = 90
_TITLE_RATIO_GATE = 60
_TITLE_STRONG_RATIO = 95


@dataclass(frozen=True)
class MatchScore:
    confidence: int
    strong_signals: tuple[str, ...]
    veto: str | None
    detail: dict[str, float]


def _site_in_title(squashed_title: str, scene: SceneFingerprint) -> bool:
    names = (scene.site, *scene.site_aliases)
    for name in names:
        sq = squash(name)
        if sq and sq in squashed_title:
            return True
    # fuzzy fallback for slight misspellings of the primary site name
    sq_site = squash(scene.site)
    if sq_site and fuzz.partial_ratio(sq_site, squashed_title) >= _FUZZY_SITE_MIN:
        return True
    return False


def score(
    scene: SceneFingerprint,
    title: str,
    other_sites: frozenset[str] = frozenset(),
) -> MatchScore:
    squashed = squash(title)
    detail: dict[str, float] = {}
    strong: list[str] = []

    # --- date ---
    title_dates = extract_dates(title)
    date_hit = any(abs((d - scene.date).days) <= 1 for d in title_dates)
    if date_hit:
        strong.append("date")
        detail["date"] = STRONG_DATE
    elif title_dates:
        return MatchScore(0, (), "date-mismatch", {"date": 0.0})

    # --- site ---
    own_site = _site_in_title(squashed, scene)
    if own_site:
        strong.append("site")
        detail["site"] = STRONG_SITE
    else:
        for other in other_sites:
            if other and other in squashed:
                return MatchScore(0, tuple(strong), "site-mismatch", detail)

    # --- performers ---
    hits = sum(1 for p in scene.performers if squash(p) and squash(p) in squashed)
    if hits:
        strong.append("performer")
        detail["performer"] = STRONG_PERFORMER + (MULTI_PERFORMER_BONUS if hits > 1 else 0)

    # --- title similarity ---
    scene_tokens = " ".join(content_tokens(scene.title))
    cand_tokens = " ".join(content_tokens(title))
    if scene_tokens and cand_tokens:
        ratio = fuzz.token_set_ratio(scene_tokens, cand_tokens)
        if ratio >= _TITLE_STRONG_RATIO:
            strong.append("title")
            detail["title"] = STRONG_TITLE
        elif ratio >= _TITLE_RATIO_GATE:
            detail["title"] = ratio / 100.0 * TITLE_MAX

    total = sum(detail.values())
    if len(strong) < 2:
        total = min(total, SINGLE_SIGNAL_CAP)
    return MatchScore(min(100, round(total)), tuple(strong), None, detail)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_matcher.py -v`
Expected: 10 passed. If `test_site_plus_date_clears_threshold` lands below 75, verify title-similarity is contributing (scene title tokens appear in the release) — the intended arithmetic is 40 (date) + 35 (site) ≥ 75 before title similarity is even added.

- [ ] **Step 5: Commit**

```bash
git add scenehound/matcher.py tests/test_matcher.py
git commit -m "feat: fingerprint matcher with two-strong-signal rule and vetoes"
```

---

### Task 8: Corpus harness

**Files:**
- Create: `tests/fixtures/corpus.yaml`, `tests/test_corpus.py`

**Interfaces:**
- Consumes: `matcher.score`, `SceneFingerprint`.
- Produces: the accuracy-ratchet test file real tracker titles get appended to. Corpus entry schema (exact):

```yaml
- release: "<original tracker title>"
  scene:
    site: "..."
    aliases: []          # optional, default []
    date: 2026-07-07
    title: "..."
    performers: []       # optional, default []
  other_sites: []        # optional, squashed-or-not site names of other wanted sites
  expect: match          # 'match' (>= 75) or 'no_match' (< 75)
```

- [ ] **Step 1: Write the seed corpus**

`tests/fixtures/corpus.yaml`:

```yaml
# Scenehound matching corpus — the accuracy ratchet.
# Every production mismatch gets appended here as a regression test.
# expect: match => confidence >= 75; no_match => confidence < 75.

- release: "ThatFetishGirl.26.07.07.Latex.Worship.Session.XXX.1080p.MP4-GRP"
  scene: &latex
    site: "That Fetish Girl"
    aliases: ["TFG"]
    date: 2026-07-07
    title: "Latex Worship Session"
    performers: ["Jane Doe", "Mary Major"]
  expect: match

- release: "Jane Doe & Mary Major - Latex Worship (07.07.2026) 2160p"
  scene: *latex
  expect: match

- release: "TFG - Latex Worship Session [1080]"        # site alias + title, no date
  scene: *latex
  expect: match

- release: "Jane.Doe.Latex.Worship.Session.720p"        # performer + title, no site/date
  scene: *latex
  expect: match

- release: "ThatFetishGirl.Compilation.Best.Of.2025"    # site only, wrong content
  scene: *latex
  expect: no_match

- release: "Unrelated.Studio.2026-07-07.Random.Clip"    # date only
  scene: *latex
  expect: no_match

- release: "OtherStudio.26.07.07.Latex.Worship.Session"
  scene: *latex
  other_sites: ["Other Studio"]
  expect: no_match

- release: "ThatFetishGirl.2024-01-01.Latex.Worship.Session"  # contradicting date
  scene: *latex
  expect: no_match

- release: "Scott Stark Studios - Beach Day 05.07.2026 1080p"
  scene:
    site: "Scott Stark Studios"
    aliases: []
    date: 2026-07-05
    title: "Beach Day"
    performers: ["Alex Roe"]
  expect: match
```

- [ ] **Step 2: Write the failing harness**

`tests/test_corpus.py`:

```python
from datetime import date
from pathlib import Path

import pytest
import yaml

from scenehound.matcher import score
from scenehound.models import SceneFingerprint
from scenehound.normalize import squash

CORPUS = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "corpus.yaml").read_text()
)
THRESHOLD = 75


def _fingerprint(raw: dict) -> SceneFingerprint:
    d = raw["date"]
    return SceneFingerprint(
        scene_id=0,
        site=raw["site"],
        site_aliases=tuple(raw.get("aliases", [])),
        date=d if isinstance(d, date) else date.fromisoformat(str(d)),
        title=raw["title"],
        performers=tuple(raw.get("performers", [])),
    )


@pytest.mark.parametrize(
    "entry", CORPUS, ids=[e["release"][:60] for e in CORPUS]
)
def test_corpus(entry):
    result = score(
        _fingerprint(entry["scene"]),
        entry["release"],
        other_sites=frozenset(squash(s) for s in entry.get("other_sites", [])),
    )
    if entry["expect"] == "match":
        assert result.confidence >= THRESHOLD, (
            f"expected match, got {result.confidence} "
            f"(strong={result.strong_signals}, veto={result.veto}, {result.detail})"
        )
    else:
        assert result.confidence < THRESHOLD, (
            f"expected no_match, got {result.confidence} "
            f"(strong={result.strong_signals}, {result.detail})"
        )
```

- [ ] **Step 3: Run the harness**

Run: `.venv/bin/pytest tests/test_corpus.py -v`
Expected: all 9 pass. Failures here are matcher bugs — fix `matcher.py` (constants or logic), NOT the corpus expectations, unless an expectation is genuinely wrong.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/corpus.yaml tests/test_corpus.py
git commit -m "test: matching corpus harness with seed entries"
```

---

### Task 9: Query planner

**Files:**
- Create: `scenehound/query_planner.py`, `tests/test_query_planner.py`

**Interfaces:**
- Consumes: `SceneFingerprint`, `normalize.content_tokens`.
- Produces: `plan_queries(scene: SceneFingerprint, max_queries: int = 5) -> tuple[str, ...]` — deduplicated, best-first, length ≤ max_queries. Tracker search is title-keyword-only, so every variant is a string of tokens likely to appear in a release title.

- [ ] **Step 1: Write the failing tests**

`tests/test_query_planner.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_query_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.query_planner`.

- [ ] **Step 3: Implement `scenehound/query_planner.py`**

```python
"""Adaptive query variant generation.

Order encodes retrieval likelihood: the yy.mm.dd scene convention with site
name is the most common well-formed naming on these trackers; later variants
trade precision for recall. The orchestrator fires variants one at a time and
stops as soon as something clears the threshold."""
from __future__ import annotations

from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens


def plan_queries(scene: SceneFingerprint, max_queries: int = 5) -> tuple[str, ...]:
    d = scene.date
    variants: list[str] = [
        f"{scene.site} {d.strftime('%y.%m.%d')}",   # scene convention: Site.YY.MM.DD
        f"{scene.site} {d.isoformat()}",             # ISO-dated titles
    ]
    if scene.performers:
        variants.append(f"{scene.performers[0]} {d.isoformat()}")
    variants.append(scene.site)                      # site alone (date filtering client-side)
    title_words = content_tokens(scene.title)[:3]
    if title_words:
        variants.append(f"{scene.site} {' '.join(title_words)}")
        if scene.performers:
            variants.append(f"{scene.performers[0]} {' '.join(title_words)}")

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
        if len(out) == max_queries:
            break
    return tuple(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_query_planner.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add scenehound/query_planner.py tests/test_query_planner.py
git commit -m "feat: adaptive query planner"
```

---

### Task 10: Wanted index

**Files:**
- Create: `scenehound/wanted_index.py`, `tests/test_wanted_index.py`

**Interfaces:**
- Consumes: `SceneFingerprint`, `normalize`, `dates.extract_dates`.
- Produces: `WantedIndex(scenes: Iterable[SceneFingerprint])` with:
  - `.resolve(site_token: str, dates: Sequence[date]) -> tuple[SceneFingerprint, ...]` — scenes whose squashed site/alias equals `squash(site_token)` and whose date is within ±1 day of any given date.
  - `.candidates_for_title(title: str) -> tuple[SceneFingerprint, ...]` — lossless pre-filter: union of date-bucket hits (±1 day) and token-index hits; empty when no strong signal is shared.
  - `.site_vocab: frozenset[str]` — all squashed site names/aliases in the wanted list.
  - `.other_sites_for(scene: SceneFingerprint) -> frozenset[str]` — `site_vocab` minus the scene's own squashed names.
  - `len(index)` — scene count.

- [ ] **Step 1: Write the failing tests**

`tests/test_wanted_index.py`:

```python
from datetime import date

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_wanted_index.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.wanted_index`.

- [ ] **Step 3: Implement `scenehound/wanted_index.py`**

```python
"""In-memory index over the wanted list.

Pre-filtering is lossless by construction: a match requires two strong
signals, so any (release, scene) pair sharing neither a date bucket nor a
content token can never clear the threshold and is safe to skip."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Sequence

from scenehound.dates import extract_dates
from scenehound.models import SceneFingerprint
from scenehound.normalize import content_tokens, squash

_MAX_CANDIDATES = 200


class WantedIndex:
    def __init__(self, scenes: Iterable[SceneFingerprint]) -> None:
        self._scenes: list[SceneFingerprint] = list(scenes)
        self._by_date: dict[date, list[SceneFingerprint]] = defaultdict(list)
        self._by_site: dict[str, list[SceneFingerprint]] = defaultdict(list)
        self._by_token: dict[str, list[SceneFingerprint]] = defaultdict(list)
        vocab: set[str] = set()
        for s in self._scenes:
            self._by_date[s.date].append(s)
            for name in (s.site, *s.site_aliases):
                sq = squash(name)
                if sq:
                    self._by_site[sq].append(s)
                    vocab.add(sq)
            for tok in {
                *content_tokens(s.title),
                *(t for p in s.performers for t in content_tokens(p)),
            }:
                self._by_token[tok].append(s)
        self.site_vocab: frozenset[str] = frozenset(vocab)

    def __len__(self) -> int:
        return len(self._scenes)

    def resolve(
        self, site_token: str, dates: Sequence[date]
    ) -> tuple[SceneFingerprint, ...]:
        candidates = self._by_site.get(squash(site_token), [])
        out = [
            s
            for s in candidates
            if any(abs((s.date - d).days) <= 1 for d in dates)
        ]
        return tuple(sorted(out, key=lambda s: s.scene_id))

    def candidates_for_title(self, title: str) -> tuple[SceneFingerprint, ...]:
        hits: dict[int, SceneFingerprint] = {}
        for d in extract_dates(title):
            for delta in (-1, 0, 1):
                for s in self._by_date.get(d + timedelta(days=delta), []):
                    hits[s.scene_id] = s
        for tok in content_tokens(title):
            for s in self._by_token.get(tok, []):
                hits[s.scene_id] = s
        out = sorted(hits.values(), key=lambda s: s.scene_id)
        return tuple(out[:_MAX_CANDIDATES])

    def other_sites_for(self, scene: SceneFingerprint) -> frozenset[str]:
        own = {squash(n) for n in (scene.site, *scene.site_aliases)}
        return self.site_vocab - own
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_wanted_index.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add scenehound/wanted_index.py tests/test_wanted_index.py
git commit -m "feat: wanted-list index with lossless pre-filtering"
```

---

### Task 11: Whisparr client

**Files:**
- Create: `scenehound/clients/__init__.py` (empty), `scenehound/clients/whisparr.py`, `tests/test_whisparr_client.py`, `tests/fixtures/whisparr_wanted_sample.json`

**Interfaces:**
- Consumes: `SceneFingerprint`, `httpx`.
- Produces: `WhisparrClient(base_url: str, api_key: str, client: httpx.AsyncClient)` with `async fetch_wanted() -> list[SceneFingerprint]` (pages through the full wanted-missing list) and module-level `scene_from_record(record: dict) -> SceneFingerprint | None` (returns None for records missing site/date/title).

- [ ] **Step 1: Capture a real API sample (field-mapping ground truth)**

The exact JSON shape of Whisparr v3 ("eros") wanted records must be confirmed against the live instance — do not trust guessed field names. Run (ask the user for `WHISPARR_URL`/`WHISPARR_API_KEY` if not provided, or ask them to run it and paste the output):

```bash
curl -s "$WHISPARR_URL/api/v3/wanted/missing?page=1&pageSize=2" \
  -H "X-Api-Key: $WHISPARR_API_KEY" | python3 -m json.tool
```

Save the (sanitised if desired) response as `tests/fixtures/whisparr_wanted_sample.json`. Inspect one record and identify: the scene title field, the release/available date field, the site/studio name field, and the performer names path. **Adjust `scene_from_record` in Step 4 to the actual field names** — the implementation below encodes the best-guess mapping (`title`, `releaseDate`, `studioTitle` falling back to `studio.title`, `credits[].performer.name` falling back to `credits[].name`); if reality differs, change the mapper and the fixture-based test together, and note the true shape in a comment.

- [ ] **Step 2: Write the failing tests**

`tests/test_whisparr_client.py`:

```python
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from scenehound.clients.whisparr import WhisparrClient, scene_from_record

SAMPLE = {
    "id": 42,
    "title": "Latex Worship Session",
    "releaseDate": "2026-07-07",
    "studioTitle": "That Fetish Girl",
    "credits": [
        {"performer": {"name": "Jane Doe"}},
        {"performer": {"name": "Mary Major"}},
    ],
}


def test_scene_from_record_maps_fields():
    s = scene_from_record(SAMPLE)
    assert s.scene_id == 42
    assert s.site == "That Fetish Girl"
    assert s.date == date(2026, 7, 7)
    assert s.title == "Latex Worship Session"
    assert s.performers == ("Jane Doe", "Mary Major")


def test_scene_from_record_rejects_incomplete():
    assert scene_from_record({"id": 1, "title": "x"}) is None
    assert scene_from_record({**SAMPLE, "releaseDate": None}) is None
    assert scene_from_record({**SAMPLE, "studioTitle": "", "studio": None}) is None


def test_real_fixture_records_map():
    fixture = Path(__file__).parent / "fixtures" / "whisparr_wanted_sample.json"
    records = json.loads(fixture.read_text())["records"]
    mapped = [scene_from_record(r) for r in records]
    assert any(mapped), "no record in the captured sample mapped — mapper is wrong"


async def test_fetch_wanted_pages_until_done():
    pages = {
        1: {"page": 1, "pageSize": 2, "totalRecords": 3,
            "records": [SAMPLE, {**SAMPLE, "id": 43}]},
        2: {"page": 2, "pageSize": 2, "totalRecords": 3,
            "records": [{**SAMPLE, "id": 44}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Api-Key"] == "k"
        page = int(dict(request.url.params)["page"])
        return httpx.Response(200, json=pages[page])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        client = WhisparrClient("http://w:6969", "k", hc)
        scenes = await client.fetch_wanted()
    assert [s.scene_id for s in scenes] == [42, 43, 44]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_whisparr_client.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.clients.whisparr`.

- [ ] **Step 4: Implement `scenehound/clients/whisparr.py`**

```python
"""Whisparr v3 API client: pages the wanted/missing list.

Field mapping is grounded in tests/fixtures/whisparr_wanted_sample.json,
captured from the live instance. If Whisparr changes shape, recapture the
fixture and adjust scene_from_record."""
from __future__ import annotations

import logging
from datetime import date

import httpx

from scenehound.models import SceneFingerprint

log = logging.getLogger("scenehound.whisparr")
_PAGE_SIZE = 1000


def scene_from_record(record: dict) -> SceneFingerprint | None:
    title = record.get("title") or ""
    raw_date = record.get("releaseDate") or ""
    site = record.get("studioTitle") or (record.get("studio") or {}).get("title") or ""
    if not (title and raw_date and site):
        return None
    try:
        parsed = date.fromisoformat(raw_date[:10])
    except ValueError:
        return None
    performers = tuple(
        name
        for c in record.get("credits") or []
        if (name := (c.get("performer") or {}).get("name") or c.get("name"))
    )
    return SceneFingerprint(
        scene_id=int(record.get("id", 0)),
        site=site,
        site_aliases=(),
        date=parsed,
        title=title,
        performers=performers,
    )


class WhisparrClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"X-Api-Key": api_key}
        self._client = client

    async def fetch_wanted(self) -> list[SceneFingerprint]:
        scenes: list[SceneFingerprint] = []
        page, received, total = 1, 0, None
        while total is None or received < total:
            resp = await self._client.get(
                f"{self._base}/api/v3/wanted/missing",
                params={"page": page, "pageSize": _PAGE_SIZE},
                headers=self._headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            body = resp.json()
            total = int(body.get("totalRecords", 0))
            records = body.get("records", [])
            if not records:
                break
            received += len(records)
            skipped = 0
            for r in records:
                if s := scene_from_record(r):
                    scenes.append(s)
                else:
                    skipped += 1
            if skipped:
                log.warning("wanted-fetch page=%d skipped=%d unmappable records", page, skipped)
            page += 1
        log.info("wanted-fetch complete scenes=%d", len(scenes))
        return scenes
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_whisparr_client.py -v`
Expected: 4 passed (including the real-fixture test — if that one fails, the live API shape differs from the guess: fix `scene_from_record` to match the fixture, not vice versa).

- [ ] **Step 6: Commit**

```bash
git add scenehound/clients/ tests/test_whisparr_client.py tests/fixtures/whisparr_wanted_sample.json
git commit -m "feat: whisparr client with verified wanted-record mapping"
```

---

### Task 12: Prowlarr client

**Files:**
- Create: `scenehound/clients/prowlarr.py`, `tests/test_prowlarr_client.py`

**Interfaces:**
- Consumes: `torznab.parse_feed`, `ReleaseCandidate`.
- Produces: `ProwlarrClient(base_url: str, api_key: str, client: httpx.AsyncClient)` with `async search(indexer_id: int, query: str | None, categories: Sequence[int], limit: int = 100) -> list[ReleaseCandidate]`. `query=None` sends no `q` param (RSS fetch). Raises `ProwlarrError` on HTTP/network failure.

- [ ] **Step 1: Write the failing tests**

`tests/test_prowlarr_client.py`:

```python
import httpx
import pytest

from scenehound.clients.prowlarr import ProwlarrClient, ProwlarrError

FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel><item>
    <title>A.Release</title><guid>g1</guid><link>http://p/dl/1</link>
    <torznab:attr name="category" value="6000"/>
  </item></channel>
</rss>"""


def make_client(handler):
    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ProwlarrClient("http://p:9696", "pk", hc), hc


async def test_search_hits_indexer_torznab_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=FEED)

    client, hc = make_client(handler)
    async with hc:
        results = await client.search(12, "site 26.07.07", [6000])
    assert "/12/api" in seen["url"]
    assert seen["params"]["t"] == "search"
    assert seen["params"]["q"] == "site 26.07.07"
    assert seen["params"]["cat"] == "6000"
    assert seen["params"]["apikey"] == "pk"
    assert len(results) == 1 and results[0].title == "A.Release"


async def test_rss_fetch_omits_q():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=FEED)

    client, hc = make_client(handler)
    async with hc:
        await client.search(12, None, [6000])
    assert "q" not in seen["params"]


async def test_http_error_raises_prowlarr_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client, hc = make_client(handler)
    async with hc:
        with pytest.raises(ProwlarrError):
            await client.search(12, "x", [6000])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_prowlarr_client.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.clients.prowlarr`.

- [ ] **Step 3: Implement `scenehound/clients/prowlarr.py`**

```python
"""Prowlarr Torznab client: queries the real indexers on Scenehound's behalf.

Prowlarr exposes each indexer at {base}/{indexer_id}/api speaking Torznab;
download links in results point back at Prowlarr's own proxy, so grabs reuse
Prowlarr's tracker auth untouched."""
from __future__ import annotations

from typing import Sequence

import httpx

from scenehound.models import ReleaseCandidate
from scenehound.torznab import parse_feed


class ProwlarrError(Exception):
    pass


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client

    async def search(
        self,
        indexer_id: int,
        query: str | None,
        categories: Sequence[int],
        limit: int = 100,
    ) -> list[ReleaseCandidate]:
        params: dict[str, str] = {
            "t": "search",
            "cat": ",".join(str(c) for c in categories),
            "limit": str(limit),
            "apikey": self._api_key,
        }
        if query is not None:
            params["q"] = query
        try:
            resp = await self._client.get(
                f"{self._base}/{indexer_id}/api", params=params, timeout=40.0
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProwlarrError(f"prowlarr search failed: {exc}") from exc
        try:
            return parse_feed(resp.content)
        except Exception as exc:  # malformed XML from upstream
            raise ProwlarrError(f"unparseable torznab feed: {exc}") from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_prowlarr_client.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scenehound/clients/prowlarr.py tests/test_prowlarr_client.py
git commit -m "feat: prowlarr torznab client"
```

---

### Task 13: API orchestration

**Files:**
- Create: `scenehound/api.py`, `tests/test_api.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: everything built so far.
- Produces: `router: fastapi.APIRouter` with `GET /indexer/{slug}/api` and `GET /healthz`; `AppState(config: Config, prowlarr: ProwlarrClient, index_holder: IndexHolder, buckets: dict[str, TokenBucket])`; `IndexHolder` with `.current: WantedIndex | None` and `.refreshed_at: float | None`. State is read from `request.app.state.scenehound: AppState`. Constants: `TIME_BUDGET_SECONDS = 45.0`.

Orchestration logic (this is the behavioural contract the tests below pin down):

1. `apikey` query param must equal `config.api_key` → else Torznab error `100`.
2. Unknown `{slug}` → Torznab error `201` ("Incorrect parameter").
3. `t=caps` → `build_caps()`.
4. `t=search` with non-empty `q` → **search mode**; empty/absent `q` → **RSS mode**. Any other `t` → error `203`.
5. Search mode: `parse_query_term(q)`; on failure or when `index_holder.current` is `None` → **passthrough** (one bucket-gated Prowlarr query with the verbatim term; bucket empty → empty feed + `rate-deferred` log). Resolved scenes come from `index.resolve()`; none found → passthrough. Otherwise: iterate `plan_queries()` variants; each variant costs one `bucket.try_acquire()` (deny → stop, log `rate-deferred`); score every candidate against every resolved scene with `other_sites=index.other_sites_for(scene)`; keep per-candidate best score; stop early once any candidate ≥ threshold; whole loop wrapped in `asyncio.timeout(TIME_BUDGET_SECONDS)` with partial results returned on expiry. Return matched candidates (score ≥ threshold, deduped by guid, best score first) as rewritten `FeedEntry`s.
6. RSS mode: one Prowlarr fetch with `query=None`, NOT bucket-gated. No index → passthrough everything. Else for each candidate: `index.candidates_for_title()`, score, best scene ≥ threshold → rewrite, else passthrough unmodified.
7. `ProwlarrError` anywhere → Torznab error `900` with description.
8. `/healthz` → 200 JSON `{"status": "ok", "index_size": <n or 0>, "index_age_seconds": <float or null>}`.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
from datetime import date

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scenehound.api import AppState, IndexHolder, router
from scenehound.clients.prowlarr import ProwlarrClient
from scenehound.config import (
    Config, IndexerConfig, MatchingConfig, RateLimitConfig, ServiceConfig,
)
from scenehound.models import SceneFingerprint
from scenehound.rate_limiter import TokenBucket
from scenehound.wanted_index import WantedIndex

SCENE = SceneFingerprint(
    scene_id=7,
    site="That Fetish Girl",
    site_aliases=("TFG",),
    date=date(2026, 7, 7),
    title="Latex Worship Session",
    performers=("Jane Doe", "Mary Major"),
)

FEED_MATCHING = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>TFG.26.07.07.Latex.Worship.Session.1080p</title>
      <guid>g-match</guid><link>http://p/dl/1</link>
      <torznab:attr name="category" value="6000"/>
      <torznab:attr name="seeders" value="5"/>
    </item>
    <item>
      <title>Unrelated.Studio.Thing.720p</title>
      <guid>g-nomatch</guid><link>http://p/dl/2</link>
      <torznab:attr name="category" value="6000"/>
    </item>
  </channel>
</rss>"""


def make_config(**overrides) -> Config:
    base = dict(
        whisparr=ServiceConfig("http://w:6969", "wk"),
        prowlarr=ServiceConfig("http://p:9696", "pk"),
        indexers=(IndexerConfig("empornium", 12), IndexerConfig("happyfappy", 15)),
        api_key="shk",
        matching=MatchingConfig(),
        rate_limit=RateLimitConfig(),
        log_level="debug",
    )
    base.update(overrides)
    return Config(**base)


@pytest.fixture
def prowlarr_calls():
    return []


@pytest.fixture
def app(prowlarr_calls):
    def handler(request: httpx.Request) -> httpx.Response:
        prowlarr_calls.append(dict(request.url.params))
        return httpx.Response(200, content=FEED_MATCHING)

    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = make_config()
    holder = IndexHolder()
    holder.set(WantedIndex([SCENE]))
    state = AppState(
        config=config,
        prowlarr=ProwlarrClient(config.prowlarr.url, config.prowlarr.api_key, hc),
        index_holder=holder,
        buckets={
            i.slug: TokenBucket(config.rate_limit.burst, config.rate_limit.refill_seconds)
            for i in config.indexers
        },
    )
    application = FastAPI()
    application.include_router(router)
    application.state.scenehound = state
    return application


@pytest.fixture
def client(app):
    return TestClient(app)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_api.py`:

```python
import xml.etree.ElementTree as ET

from scenehound.api import IndexHolder
from scenehound.torznab import parse_feed


def titles(response):
    return [c.title for c in parse_feed(response.content)]


def test_wrong_apikey_rejected(client):
    r = client.get("/indexer/empornium/api", params={"t": "caps", "apikey": "bad"})
    assert ET.fromstring(r.content).get("code") == "100"


def test_unknown_slug_rejected(client):
    r = client.get("/indexer/nope/api", params={"t": "caps", "apikey": "shk"})
    assert ET.fromstring(r.content).get("code") == "201"


def test_caps(client):
    r = client.get("/indexer/empornium/api", params={"t": "caps", "apikey": "shk"})
    assert ET.fromstring(r.content).tag == "caps"


def test_search_mode_returns_only_rewritten_match(client, prowlarr_calls):
    r = client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    got = titles(r)
    assert got == ["That.Fetish.Girl.2026-07-07.Latex.Worship.Session.XXX.1080p"]
    assert prowlarr_calls  # went to prowlarr
    assert prowlarr_calls[0]["apikey"] == "pk"


def test_search_early_exit_single_query(client, prowlarr_calls):
    client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026",
                "cat": "6000", "apikey": "shk"},
    )
    # first variant already found a >=75 match; no escalation
    assert len(prowlarr_calls) == 1


def test_unresolvable_scene_passes_through(client, prowlarr_calls):
    r = client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "unknownsite 01.01.2026",
                "cat": "6000", "apikey": "shk"},
    )
    # passthrough: verbatim query forwarded, results unrewritten
    assert prowlarr_calls[0]["q"] == "unknownsite 01.01.2026"
    assert set(titles(r)) == {
        "TFG.26.07.07.Latex.Worship.Session.1080p",
        "Unrelated.Studio.Thing.720p",
    }


def test_unparseable_query_passes_through(client, prowlarr_calls):
    client.get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "just some words", "apikey": "shk"},
    )
    assert prowlarr_calls[0]["q"] == "just some words"


def test_missing_index_passes_through(app, prowlarr_calls):
    from fastapi.testclient import TestClient

    app.state.scenehound.index_holder = IndexHolder()  # no index loaded
    r = TestClient(app).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026", "apikey": "shk"},
    )
    assert prowlarr_calls[0]["q"] == "thatfetishgirl 07.07.2026"


def test_rate_limit_returns_empty_when_dry(app, prowlarr_calls):
    from fastapi.testclient import TestClient

    for bucket in app.state.scenehound.buckets.values():
        while bucket.try_acquire():
            pass
    r = TestClient(app).get(
        "/indexer/empornium/api",
        params={"t": "search", "q": "thatfetishgirl 07.07.2026", "apikey": "shk"},
    )
    assert titles(r) == []
    assert prowlarr_calls == []


def test_rss_mode_rewrites_matches_and_passes_rest(client, prowlarr_calls):
    r = client.get(
        "/indexer/empornium/api", params={"t": "search", "apikey": "shk"}
    )
    got = titles(r)
    assert "That.Fetish.Girl.2026-07-07.Latex.Worship.Session.XXX.1080p" in got
    assert "Unrelated.Studio.Thing.720p" in got
    assert "q" not in prowlarr_calls[0]


def test_rss_mode_ignores_rate_limit(app, prowlarr_calls):
    from fastapi.testclient import TestClient

    for bucket in app.state.scenehound.buckets.values():
        while bucket.try_acquire():
            pass
    r = TestClient(app).get(
        "/indexer/empornium/api", params={"t": "search", "apikey": "shk"}
    )
    assert len(titles(r)) == 2  # fetch still happened


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["index_size"] == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.api`.

- [ ] **Step 4: Implement `scenehound/api.py`**

```python
"""HTTP surface and orchestration: search mode, RSS mode, passthrough."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Request, Response

from scenehound.clients.prowlarr import ProwlarrClient, ProwlarrError
from scenehound.config import Config, IndexerConfig
from scenehound.dates import parse_query_term
from scenehound.matcher import score
from scenehound.models import ReleaseCandidate, SceneFingerprint
from scenehound.query_planner import plan_queries
from scenehound.rate_limiter import TokenBucket
from scenehound.rewriter import rewrite_title
from scenehound.torznab import FeedEntry, build_caps, build_error, build_feed
from scenehound.wanted_index import WantedIndex

log = logging.getLogger("scenehound.api")
router = APIRouter()

TIME_BUDGET_SECONDS = 45.0
_DEFAULT_CATS = (6000,)


class IndexHolder:
    def __init__(self) -> None:
        self.current: WantedIndex | None = None
        self.refreshed_at: float | None = None

    def set(self, index: WantedIndex) -> None:
        self.current = index
        self.refreshed_at = time.monotonic()


@dataclass
class AppState:
    config: Config
    prowlarr: ProwlarrClient
    index_holder: IndexHolder
    buckets: dict[str, TokenBucket]


def _xml(content: bytes) -> Response:
    return Response(content=content, media_type="application/xml")


def _cats(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return _DEFAULT_CATS
    out = tuple(int(c) for c in raw.split(",") if c.strip().isdigit())
    return out or _DEFAULT_CATS


@dataclass
class _Scored:
    candidate: ReleaseCandidate
    scene: SceneFingerprint
    confidence: int


async def _passthrough(
    state: AppState, indexer: IndexerConfig, query: str, cats: tuple[int, ...]
) -> Response:
    bucket = state.buckets[indexer.slug]
    if not bucket.try_acquire():
        log.info("search slug=%s decision=rate-deferred q=%r", indexer.slug, query)
        return _xml(build_feed([]))
    results = await state.prowlarr.search(indexer.prowlarr_id, query, cats)
    log.info("search slug=%s mode=passthrough q=%r results=%d",
             indexer.slug, query, len(results))
    return _xml(build_feed([FeedEntry(c) for c in results]))


async def _search_mode(
    state: AppState, indexer: IndexerConfig, q: str, cats: tuple[int, ...]
) -> Response:
    index = state.index_holder.current
    parsed = parse_query_term(q)
    if index is None or parsed is None:
        if parsed is None:
            log.warning("search slug=%s unparseable q=%r -> passthrough", indexer.slug, q)
        return await _passthrough(state, indexer, q, cats)

    scenes = index.resolve(parsed.site_token, list(parsed.dates))
    if not scenes:
        log.info("search slug=%s q=%r scene=unresolved -> passthrough", indexer.slug, q)
        return await _passthrough(state, indexer, q, cats)

    threshold = state.config.matching.threshold
    bucket = state.buckets[indexer.slug]
    best: dict[str, _Scored] = {}
    variants = plan_queries(scenes[0], state.config.matching.max_queries_per_search)
    fired = 0
    try:
        async with asyncio.timeout(TIME_BUDGET_SECONDS):
            for variant in variants:
                if not bucket.try_acquire():
                    log.info("search slug=%s decision=rate-deferred after=%d", indexer.slug, fired)
                    break
                fired += 1
                candidates = await state.prowlarr.search(indexer.prowlarr_id, variant, cats)
                for c in candidates:
                    for scene in scenes:
                        s = score(scene, c.title, other_sites=index.other_sites_for(scene))
                        log.debug(
                            "score slug=%s scene=%d title=%r conf=%d strong=%s veto=%s",
                            indexer.slug, scene.scene_id, c.title,
                            s.confidence, s.strong_signals, s.veto,
                        )
                        prev = best.get(c.guid)
                        if prev is None or s.confidence > prev.confidence:
                            best[c.guid] = _Scored(c, scene, s.confidence)
                if any(v.confidence >= threshold for v in best.values()):
                    break
    except TimeoutError:
        log.warning("search slug=%s q=%r time budget expired after %d queries",
                    indexer.slug, q, fired)

    matched = sorted(
        (v for v in best.values() if v.confidence >= threshold),
        key=lambda v: -v.confidence,
    )
    log.info(
        "search slug=%s q=%r scenes=%s variants_fired=%d candidates=%d matched=%d",
        indexer.slug, q, [s.scene_id for s in scenes], fired, len(best), len(matched),
    )
    return _xml(build_feed([
        FeedEntry(v.candidate, title_override=rewrite_title(v.scene, v.candidate.title))
        for v in matched
    ]))


async def _rss_mode(
    state: AppState, indexer: IndexerConfig, cats: tuple[int, ...]
) -> Response:
    # One fetch, identical cost to status-quo RSS sync: not bucket-gated.
    candidates = await state.prowlarr.search(indexer.prowlarr_id, None, cats)
    index = state.index_holder.current
    entries: list[FeedEntry] = []
    rewritten = 0
    for c in candidates:
        entry = FeedEntry(c)
        if index is not None:
            for scene in index.candidates_for_title(c.title):
                s = score(scene, c.title, other_sites=index.other_sites_for(scene))
                if s.confidence >= state.config.matching.threshold:
                    entry = FeedEntry(c, title_override=rewrite_title(scene, c.title))
                    rewritten += 1
                    log.info(
                        "rss slug=%s matched scene=%d conf=%d original=%r",
                        indexer.slug, scene.scene_id, s.confidence, c.title,
                    )
                    break
        entries.append(entry)
    log.info("rss slug=%s items=%d rewritten=%d", indexer.slug, len(candidates), rewritten)
    return _xml(build_feed(entries))


@router.get("/indexer/{slug}/api")
async def torznab_endpoint(slug: str, request: Request) -> Response:
    state: AppState = request.app.state.scenehound
    params = request.query_params
    if params.get("apikey") != state.config.api_key:
        return _xml(build_error(100, "Incorrect user credentials"))
    indexer = next((i for i in state.config.indexers if i.slug == slug), None)
    if indexer is None:
        return _xml(build_error(201, "Incorrect parameter"))
    t = params.get("t", "")
    if t == "caps":
        return _xml(build_caps())
    if t != "search":
        return _xml(build_error(203, f"Function not available: {t!r}"))
    cats = _cats(params.get("cat"))
    q = (params.get("q") or "").strip()
    try:
        if q:
            return await _search_mode(state, indexer, q, cats)
        return await _rss_mode(state, indexer, cats)
    except ProwlarrError as exc:
        log.error("search slug=%s prowlarr error: %s", slug, exc)
        return _xml(build_error(900, str(exc)))


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    state: AppState = request.app.state.scenehound
    index = state.index_holder.current
    age = (
        time.monotonic() - state.index_holder.refreshed_at
        if state.index_holder.refreshed_at is not None
        else None
    )
    return {
        "status": "ok",
        "index_size": len(index) if index is not None else 0,
        "index_age_seconds": age,
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: 12 passed. Then run the whole suite: `.venv/bin/pytest -q` — everything passes.

- [ ] **Step 6: Commit**

```bash
git add scenehound/api.py tests/test_api.py tests/conftest.py
git commit -m "feat: torznab endpoint orchestration with search/rss/passthrough"
```

---

### Task 14: App factory, background refresh, logging

**Files:**
- Create: `scenehound/app.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: everything.
- Produces: `create_app(config_dir: Path | None = None) -> FastAPI` (default config dir from `SCENEHOUND_CONFIG_DIR` env or `/config`); `refresh_loop(state, whisparr, interval_seconds=900)` background task; `configure_logging(level: str)`. `uvicorn scenehound.app:app` must work (module-level `app = create_app()` guarded so tests can build their own).

- [ ] **Step 1: Write the failing tests**

`tests/test_app.py`:

```python
import asyncio

import httpx
import pytest

from scenehound.api import AppState, IndexHolder
from scenehound.app import create_app, refresh_loop
from scenehound.clients.whisparr import WhisparrClient

CONFIG_YAML = """
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

WANTED_PAGE = {
    "page": 1, "pageSize": 1000, "totalRecords": 1,
    "records": [{
        "id": 1, "title": "T", "releaseDate": "2026-07-07",
        "studioTitle": "S", "credits": [],
    }],
}


def test_create_app_boots_with_config(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    state = app.state.scenehound
    assert state.config.whisparr.api_key == "wk"
    assert "empornium" in state.buckets
    assert state.index_holder.current is None  # populated by refresh task


async def test_refresh_populates_index(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    state: AppState = app.state.scenehound

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=WANTED_PAGE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        whisparr = WhisparrClient("http://w:6969", "wk", hc)
        task = asyncio.create_task(
            refresh_loop(state, whisparr, interval_seconds=3600)
        )
        for _ in range(100):
            if state.index_holder.current is not None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
    assert len(state.index_holder.current) == 1


async def test_refresh_failure_keeps_old_index(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML)
    app = create_app(config_dir=tmp_path)
    state: AppState = app.state.scenehound
    from scenehound.wanted_index import WantedIndex

    old = WantedIndex([])
    state.index_holder.set(old)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as hc:
        whisparr = WhisparrClient("http://w:6969", "wk", hc)
        task = asyncio.create_task(
            refresh_loop(state, whisparr, interval_seconds=3600)
        )
        await asyncio.sleep(0.1)
        task.cancel()
    assert state.index_holder.current is old
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: scenehound.app`.

- [ ] **Step 3: Implement `scenehound/app.py`**

```python
"""Application factory and lifecycle."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI

from scenehound.api import AppState, IndexHolder, router
from scenehound.clients.prowlarr import ProwlarrClient
from scenehound.clients.whisparr import WhisparrClient
from scenehound.config import load_config
from scenehound.rate_limiter import TokenBucket
from scenehound.wanted_index import WantedIndex

log = logging.getLogger("scenehound")
REFRESH_INTERVAL_SECONDS = 900.0


def configure_logging(level: str) -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def refresh_loop(
    state: AppState,
    whisparr: WhisparrClient,
    interval_seconds: float = REFRESH_INTERVAL_SECONDS,
) -> None:
    while True:
        try:
            scenes = await whisparr.fetch_wanted()
            state.index_holder.set(WantedIndex(scenes))
            log.info("index refreshed scenes=%d", len(scenes))
        except Exception as exc:
            log.error("index refresh failed (keeping previous index): %s", exc)
        await asyncio.sleep(interval_seconds)


def create_app(config_dir: Path | None = None) -> FastAPI:
    config_dir = config_dir or Path(os.environ.get("SCENEHOUND_CONFIG_DIR", "/config"))
    config = load_config(config_dir, env=os.environ)
    configure_logging(config.log_level)

    state = AppState(
        config=config,
        prowlarr=None,  # type: ignore[arg-type]  # set in lifespan with a live client
        index_holder=IndexHolder(),
        buckets={
            i.slug: TokenBucket(config.rate_limit.burst, config.rate_limit.refill_seconds)
            for i in config.indexers
        },
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient() as http_client:
            state.prowlarr = ProwlarrClient(
                config.prowlarr.url, config.prowlarr.api_key, http_client
            )
            whisparr = WhisparrClient(
                config.whisparr.url, config.whisparr.api_key, http_client
            )
            task = asyncio.create_task(refresh_loop(state, whisparr))
            log.info(
                "scenehound started indexers=%s threshold=%d",
                [i.slug for i in config.indexers], config.matching.threshold,
            )
            try:
                yield
            finally:
                task.cancel()

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    app.state.scenehound = state
    return app
```

Note for the `test_create_app_boots_with_config` path: `create_app` builds `state.prowlarr` lazily in lifespan; tests that hit the Torznab endpoint (Task 13) construct `AppState` directly, so the `None` placeholder is never dereferenced in tests that don't run lifespan. Do NOT add a module-level `app = create_app()` — uvicorn is invoked with the factory flag instead (Task 15).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_app.py -v` then `.venv/bin/pytest -q`
Expected: 3 passed; full suite green.

- [ ] **Step 5: Commit**

```bash
git add scenehound/app.py tests/test_app.py
git commit -m "feat: app factory, background index refresh, logging"
```

---

### Task 15: Docker packaging

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `docker-compose.example.yml`

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN groupadd -g 1000 scenehound && useradd -u 1000 -g scenehound -m scenehound

WORKDIR /app
COPY pyproject.toml ./
COPY scenehound/ ./scenehound/
RUN pip install --no-cache-dir .

RUN mkdir -p /config && chown scenehound:scenehound /config
VOLUME /config
ENV SCENEHOUND_CONFIG_DIR=/config
EXPOSE 9797

USER scenehound
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9797/healthz', timeout=3).status==200 else 1)"

CMD ["uvicorn", "--factory", "scenehound.app:create_app", "--host", "0.0.0.0", "--port", "9797"]
```

`.dockerignore`:

```
.venv/
.git/
__pycache__/
tests/
docs/
*.egg-info/
```

- [ ] **Step 2: Write `docker-compose.example.yml`**

```yaml
services:
  scenehound:
    image: scenehound:latest
    build: .
    container_name: scenehound
    ports:
      - "9797:9797"
    volumes:
      - ./config:/config
    environment:
      - WHISPARR_URL=http://whisparr:6969
      - WHISPARR_API_KEY=changeme
      - PROWLARR_URL=http://prowlarr:9696
      - PROWLARR_API_KEY=changeme
    restart: unless-stopped
```

- [ ] **Step 3: Build and smoke-test the image**

Run:

```bash
docker build -t scenehound:dev .
mkdir -p /tmp/sh-config && cat > /tmp/sh-config/config.yaml <<'EOF'
whisparr: {url: "http://127.0.0.1:1", api_key: "x"}
prowlarr: {url: "http://127.0.0.1:1", api_key: "x"}
indexers: [{slug: empornium, prowlarr_id: 12}]
EOF
docker run -d --name sh-smoke -p 9797:9797 -v /tmp/sh-config:/config scenehound:dev
sleep 3
curl -sf http://127.0.0.1:9797/healthz
docker rm -f sh-smoke
```

Expected: `curl` prints `{"status":"ok","index_size":0,...}` and exits 0 (index refresh fails against the dead Whisparr URL and correctly keeps running — that IS the degraded mode working). If Docker is unavailable on the dev machine, mark this step as requiring the user's server and verify there before release.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.example.yml
git commit -m "build: docker packaging with healthcheck"
```

---

### Task 16: Unraid template + README

**Files:**
- Create: `unraid/scenehound.xml`, `README.md`

- [ ] **Step 1: Write `unraid/scenehound.xml`**

Replace `YOUR_FORGEJO` with the real repo URL when the remote exists (leave the placeholder host in until then — it is repo metadata, not code):

```xml
<?xml version="1.0"?>
<Container version="2">
  <Name>scenehound</Name>
  <Repository>scenehound:latest</Repository>
  <Registry/>
  <Network>bridge</Network>
  <Shell>sh</Shell>
  <Privileged>false</Privileged>
  <Support>https://YOUR_FORGEJO/scenehound/issues</Support>
  <Project>https://YOUR_FORGEJO/scenehound</Project>
  <Overview>Torznab matching proxy between Whisparr and Prowlarr. Identifies badly-named scene releases on private trackers using Whisparr's own metadata and returns them with canonical titles Whisparr can parse. Add Scenehound's per-tracker endpoints to Whisparr as Torznab indexers; searches and RSS flow through unchanged otherwise.</Overview>
  <Category>Downloaders: Tools:</Category>
  <WebUI>http://[IP]:[PORT:9797]/healthz</WebUI>
  <TemplateURL/>
  <Icon>https://raw.githubusercontent.com/selfhosters/unRAID-CA-templates/master/templates/img/prowlarr.png</Icon>
  <ExtraParams>--restart unless-stopped</ExtraParams>
  <PostArgs/>
  <DonateText/>
  <DonateLink/>
  <Config Name="WebUI Port" Target="9797" Default="9797" Mode="tcp" Description="Torznab endpoint port" Type="Port" Display="always" Required="true" Mask="false">9797</Config>
  <Config Name="Config Path" Target="/config" Default="/mnt/user/appdata/scenehound" Mode="rw" Description="Holds config.yaml and the generated API key" Type="Path" Display="always" Required="true" Mask="false">/mnt/user/appdata/scenehound</Config>
  <Config Name="Whisparr URL" Target="WHISPARR_URL" Default="http://192.168.1.10:6969" Mode="" Description="Base URL of your Whisparr v3 instance" Type="Variable" Display="always" Required="true" Mask="false"/>
  <Config Name="Whisparr API Key" Target="WHISPARR_API_KEY" Default="" Mode="" Description="Whisparr Settings > General > API Key" Type="Variable" Display="always" Required="true" Mask="true"/>
  <Config Name="Prowlarr URL" Target="PROWLARR_URL" Default="http://192.168.1.10:9696" Mode="" Description="Base URL of your Prowlarr instance" Type="Variable" Display="always" Required="true" Mask="false"/>
  <Config Name="Prowlarr API Key" Target="PROWLARR_API_KEY" Default="" Mode="" Description="Prowlarr Settings > General > API Key" Type="Variable" Display="always" Required="true" Mask="true"/>
  <Config Name="Match Threshold" Target="SCENEHOUND_THRESHOLD" Default="75" Mode="" Description="Minimum confidence (0-100) to return a match. Raise if you ever see a wrong grab." Type="Variable" Display="advanced" Required="false" Mask="false">75</Config>
  <Config Name="Log Level" Target="SCENEHOUND_LOG_LEVEL" Default="info" Mode="" Description="info or debug (debug logs per-candidate scoring)" Type="Variable" Display="advanced" Required="false" Mask="false">info</Config>
  <Config Name="Rate Limit Burst" Target="SCENEHOUND_RATE_BURST" Default="4" Mode="" Description="Token bucket burst per indexer" Type="Variable" Display="advanced" Required="false" Mask="false">4</Config>
  <Config Name="Rate Limit Refill Seconds" Target="SCENEHOUND_RATE_REFILL" Default="15" Mode="" Description="Seconds per token refill (higher = gentler on trackers)" Type="Variable" Display="advanced" Required="false" Mask="false">15</Config>
</Container>
```

The indexer list (slug → Prowlarr indexer ID) stays in `/config/config.yaml` — Unraid templates cannot express lists.

- [ ] **Step 2: Write `README.md`**

```markdown
# Scenehound

Torznab matching proxy between [Whisparr v3] and [Prowlarr]. Whisparr searches
for scenes by exact `site + date`; private-tracker release naming is chaos.
Scenehound sits between them, resolves Whisparr's rigid queries against its own
scene metadata (title, performers, date, site), hunts the tracker via Prowlarr
with smarter query variants, scores every candidate, and returns matches with
canonical titles Whisparr can actually parse. Everything downstream — grabs,
downloads, imports — is stock Whisparr.

Design: `docs/plans/2026-07-11-scenehound-design.md`.

## How it works

    Whisparr ──torznab──▶ Scenehound ──torznab──▶ Prowlarr ──▶ trackers
                              └──REST──▶ Whisparr API (wanted list)

- **Search**: `thatfetishgirl 07.07.2026` → scene fingerprint → adaptive query
  variants → candidates scored (two independent strong signals required) →
  rewritten results returned.
- **RSS sync**: every new tracker upload is matched against your entire wanted
  list; recognised releases get canonical titles.
- **Any failure → passthrough**: results flow unmodified, never worse than stock.
- **Tracker-safe**: per-indexer token bucket (default: burst 4, one query per
  15 s sustained) on top of Prowlarr's own 2 s floor. Deliberately conservative.

## Setup prerequisites

1. **Your Whisparr quality profile must allow "Unknown"** (Settings → Profiles).
   Quality filtering happens at grab time; honestly-rewritten releases with no
   quality tokens parse as Unknown and would otherwise be rejected before
   download. Scenehound never invents quality it can't see.
2. **Unassign the real tracker indexers from Whisparr** (keep them in Prowlarr
   for other apps). Whisparr should reach those trackers only through Scenehound,
   or you'll get duplicate results.

## Install (Unraid)

Add Container → Template → point at `unraid/scenehound.xml` raw URL. Fill in
the Whisparr/Prowlarr URLs and API keys. Then create
`/mnt/user/appdata/scenehound/config.yaml`:

    indexers:
      - slug: empornium        # -> http://SERVER:9797/indexer/empornium/api
        prowlarr_id: 12        # Prowlarr indexer ID (visible in its URL when edited)
      - slug: happyfappy
        prowlarr_id: 15

Start the container. Read the Scenehound API key from
`/mnt/user/appdata/scenehound/apikey`.

## Add to Whisparr

For each indexer: Settings → Indexers → Add → Torznab:

- URL: `http://SERVER:9797/indexer/<slug>` (Whisparr appends `/api`)
- API Key: contents of the `apikey` file
- Categories: 6000

Press Test — a green check means the whole chain works. Run one interactive
search on a monitored scene and watch `docker logs scenehound`.

## Logs are the UI

    docker logs -f scenehound

`info` shows one line per search/RSS decision with scores. `debug` shows every
candidate's per-signal breakdown. A rejected match always says which signal
fell short. Wrong grab? The original tracker title is in the log line and in
the `scenehound_original_title` attribute of every rewritten result — add the
case to `tests/fixtures/corpus.yaml` and it becomes a regression test.

## Not in v1 (deliberate)

- External metadata providers (ThePornDB etc.) — the interface exists, nothing
  plugs in yet.
- Defeating tracker search's title-only retrieval for ancient backlog items:
  RSS catches things going forward; search mode is best-effort for the past.
- A web UI.
```

- [ ] **Step 3: Validate the template XML parses**

Run: `python3 -c "import xml.etree.ElementTree as ET; ET.parse('unraid/scenehound.xml'); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add unraid/scenehound.xml README.md
git commit -m "docs: unraid template and README"
```

---

### Task 17: CI workflow

**Files:**
- Create: `.forgejo/workflows/ci.yml`

- [ ] **Step 1: Write `.forgejo/workflows/ci.yml`**

Forgejo Actions is GitHub-Actions-compatible; this runs tests on every push and builds/publishes the image on version tags. The registry host/credentials are configured as Forgejo repo secrets (`REGISTRY_HOST`, `REGISTRY_USER`, `REGISTRY_TOKEN`) — set these up in the Forgejo UI when pushing the repo.

```yaml
name: ci
on:
  push:
    branches: [main]
    tags: ["v*"]
  pull_request:

jobs:
  test:
    runs-on: docker
    container:
      image: python:3.12-slim
    steps:
      - uses: actions/checkout@v4
      - run: pip install -e ".[dev]"
      - run: pytest -q

  build-image:
    needs: test
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: docker
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/login-action@v3
        with:
          registry: ${{ secrets.REGISTRY_HOST }}
          username: ${{ secrets.REGISTRY_USER }}
          password: ${{ secrets.REGISTRY_TOKEN }}
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ${{ secrets.REGISTRY_HOST }}/scenehound:${{ github.ref_name }}
            ${{ secrets.REGISTRY_HOST }}/scenehound:latest
```

- [ ] **Step 2: Validate YAML parses**

Run: `.venv/bin/python -c "import yaml; yaml.safe_load(open('.forgejo/workflows/ci.yml')); print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Run the full suite one final time**

Run: `.venv/bin/pytest -q`
Expected: all tests pass, no warnings that indicate real problems.

- [ ] **Step 4: Commit**

```bash
git add .forgejo/workflows/ci.yml
git commit -m "ci: test on push, build image on version tags"
```

---

## Post-implementation: live verification (manual, with the user)

Not a coded task — run through this with the user once the container is on the server:

1. Deploy via the Unraid template; create `config.yaml` with real Prowlarr indexer IDs.
2. `curl http://SERVER:9797/healthz` → `index_size` roughly matches the Whisparr wanted count within ~15 minutes of start.
3. Add both indexers to Whisparr (Torznab, categories 6000) → Test passes.
4. Interactive search on a monitored scene known to exist badly-named on the tracker → rewritten result appears → grab it → confirm the download starts and imports.
5. Watch one RSS sync cycle in the logs (`rss slug=... items=N rewritten=M`).
6. Capture ~100 real release titles from the logs and extend `tests/fixtures/corpus.yaml` with genuine cases (both match and no_match), replacing reliance on the synthetic seeds.
```
