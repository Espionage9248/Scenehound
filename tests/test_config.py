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
