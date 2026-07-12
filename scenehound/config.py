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
class ImportCompleterConfig:
    enabled: bool = False
    dry_run: bool = True
    multipack: bool = False
    grace_seconds: float = 120.0
    reconcile_seconds: float = 900.0
    max_attempts: int = 3
    import_threshold: int = 90
    ambiguity_margin: int = 10


@dataclass(frozen=True)
class Config:
    whisparr: ServiceConfig
    prowlarr: ServiceConfig
    indexers: tuple[IndexerConfig, ...]
    api_key: str
    matching: MatchingConfig = field(default_factory=MatchingConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    import_completer: ImportCompleterConfig = field(default_factory=ImportCompleterConfig)
    log_level: str = "info"


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
        import_completer=_import_completer(raw, env),
        log_level=env.get("SCENEHOUND_LOG_LEVEL", raw.get("log_level", "info")),
    )
