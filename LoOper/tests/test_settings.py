"""Unit tests for AI/settings.py — settings dataclasses, file/env loading,
merging, validation, and persistence.
"""

import json
from pathlib import Path

import pytest

from AI import settings as s


# ---------------------------------------------------------------------------
# Dataclass defaults
# ---------------------------------------------------------------------------


def test_default_settings_values():
    settings = s.Settings()
    assert settings.ollama.base_url == "http://localhost:11434"
    assert settings.ollama.context_length == 4096
    assert settings.ollama.temperature == 0.7
    assert settings.api.port == 8000
    assert settings.api.max_request_size == 10 * 1024 * 1024
    assert settings.logging.level == "INFO"
    assert settings.API_PORT == 8000


# ---------------------------------------------------------------------------
# load_config_from_file
# ---------------------------------------------------------------------------


def test_load_config_from_file_missing(tmp_path, caplog):
    assert s.load_config_from_file(tmp_path / "nope.json") == {}


def test_load_config_from_file_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    assert s.load_config_from_file(p) == {}


def test_load_config_from_file_valid(tmp_path):
    p = tmp_path / "ok.json"
    p.write_text(json.dumps({"api": {"port": 9000}}), encoding="utf-8")
    assert s.load_config_from_file(p) == {"api": {"port": 9000}}


# ---------------------------------------------------------------------------
# load_config_from_env
# ---------------------------------------------------------------------------


def test_load_config_from_env(monkeypatch):
    monkeypatch.setenv("OVERWATCH_HOST", "10.0.0.5")
    monkeypatch.setenv("OVERWATCH_PORT", "9000")
    monkeypatch.setenv("OVERWATCH_DEBUG", "true")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    cfg = s.load_config_from_env()
    assert cfg["api"]["host"] == "10.0.0.5"
    assert cfg["api"]["port"] == 9000
    assert cfg["api"]["debug"] is True
    assert cfg["logging"]["level"] == "DEBUG"


def test_load_config_from_env_empty(monkeypatch):
    for var in ("OVERWATCH_HOST", "OVERWATCH_PORT", "LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    assert s.load_config_from_env() == {}


# ---------------------------------------------------------------------------
# merge_configs
# ---------------------------------------------------------------------------


def test_merge_configs_deep_merge():
    a = {"api": {"port": 8000, "host": "x"}}
    b = {"api": {"port": 9000}, "other": 1}
    merged = s.merge_configs(a, b)
    assert merged == {"api": {"port": 9000, "host": "x"}, "other": 1}


def test_merge_configs_scalar_wins():
    merged = s.merge_configs({"a": 1}, {"a": 2})
    assert merged == {"a": 2}


# ---------------------------------------------------------------------------
# create_settings_from_dict
# ---------------------------------------------------------------------------


def test_create_settings_from_dict():
    settings = s.create_settings_from_dict({
        "ollama": {"context_length": 8192, "temperature": 0.2},
        "api": {"port": 9000},
        "logging": {"level": "DEBUG"},
    })
    assert settings.ollama.context_length == 8192
    assert settings.ollama.temperature == 0.2
    assert settings.api.port == 9000
    assert settings.logging.level == "DEBUG"


def test_create_settings_from_dict_vllm_migration():
    settings = s.create_settings_from_dict({
        "vllm": {"max_model_len": 16384, "default_models": ["a", "b"]},
    })
    assert settings.ollama.context_length == 16384
    assert settings.ollama.default_models == ["a", "b"]


# ---------------------------------------------------------------------------
# save / load roundtrip
# ---------------------------------------------------------------------------


def test_save_and_load_settings_roundtrip(tmp_path):
    path = tmp_path / "cfg.json"
    settings = s.Settings()
    settings.api.port = 9999
    settings.ollama.temperature = 0.1
    assert s.save_settings(settings, path) is True
    loaded = s.load_settings(path)
    assert loaded.api.port == 9999
    assert loaded.ollama.temperature == 0.1


def test_save_settings_failure_returns_false(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(Path, "mkdir", boom)
    assert s.save_settings(s.Settings(), tmp_path / "x" / "cfg.json") is False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_validate_settings_clean():
    assert s.validate_settings(s.Settings()) == []


def test_validate_settings_issues():
    settings = s.Settings()
    settings.ollama.context_length = 128
    settings.ollama.temperature = 3.0
    settings.api.port = 80
    settings.api.timeout = 1
    issues = s.validate_settings(settings)
    assert any("context length" in i for i in issues)
    assert any("temperature" in i for i in issues)
    assert any("port" in i for i in issues)
    assert any("timeout" in i for i in issues)


# ---------------------------------------------------------------------------
# get_api_url
# ---------------------------------------------------------------------------


def test_get_api_url_default_host():
    assert s.get_api_url(s.Settings()) == "http://localhost:8000"


def test_get_api_url_zero_host_maps_to_localhost():
    settings = s.Settings()
    settings.api.port = 7777
    assert s.get_api_url(settings) == "http://localhost:7777"


# ---------------------------------------------------------------------------
# get_config_path
# ---------------------------------------------------------------------------


def test_get_config_path_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OVERWATCH_CONFIG_PATH", str(tmp_path / "custom.json"))
    assert s.get_config_path() == tmp_path / "custom.json"


def test_get_config_path_default(monkeypatch):
    monkeypatch.delenv("OVERWATCH_CONFIG_PATH", raising=False)
    monkeypatch.setattr(s.sys, "frozen", False, raising=False)
    assert s.get_config_path() == Path(s.__file__).parent / "config.json"
