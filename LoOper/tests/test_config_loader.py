"""Unit tests for AI/config_loader.py and AI/runtime_paths.py.
"""

import json
import os

import pytest

from AI import config_loader as cl
from AI import runtime_paths as rp


# ---------------------------------------------------------------------------
# config_loader
# ---------------------------------------------------------------------------


def test_get_models_dir_source_mode(monkeypatch):
    monkeypatch.setattr(cl.sys, "frozen", False, raising=False)
    expected = os.path.join(os.path.dirname(os.path.abspath(cl.__file__)), "models")
    assert cl.get_models_dir() == expected


def test_load_ai_config_defaults(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cl, "_CONFIG_PATH", str(cfg))
    # load_ai_config syncs env vars as a side effect — clear any that other
    # tests (or api.py module init) may have left behind
    for var in ("API_PORT", "OLLAMA_HOST", "OLLAMA_PORT",
                "AUTO_START_API", "OVERWATCH_API_URL"):
        monkeypatch.delenv(var, raising=False)
    config = cl.load_ai_config()
    assert config["API_PORT"] == "8000"
    assert config["OLLAMA_HOST"] == "localhost"
    assert config["AUTO_START_API"] == "false"


def test_load_ai_config_from_file(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"API_PORT": "9000", "OLLAMA_HOST": "10.0.0.1"}),
                   encoding="utf-8")
    monkeypatch.setattr(cl, "_CONFIG_PATH", str(cfg))
    config = cl.load_ai_config()
    assert config["API_PORT"] == "9000"
    assert config["OLLAMA_HOST"] == "10.0.0.1"


def test_load_ai_config_env_override_only_when_not_in_file(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"API_PORT": "9000"}), encoding="utf-8")
    monkeypatch.setattr(cl, "_CONFIG_PATH", str(cfg))
    monkeypatch.setenv("API_PORT", "7000")  # file wins over env
    monkeypatch.setenv("OLLAMA_HOST", "env-host")  # not in file -> env wins
    config = cl.load_ai_config()
    assert config["API_PORT"] == "9000"
    assert config["OLLAMA_HOST"] == "env-host"


def test_load_ai_config_bad_file_warns(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(cl, "_CONFIG_PATH", str(cfg))
    # Previous tests may have left API_PORT in the environment via
    # load_ai_config's env-sync side effect — clear it for a clean default.
    monkeypatch.delenv("API_PORT", raising=False)
    config = cl.load_ai_config()  # falls back to defaults, no crash
    assert config["API_PORT"] == "8000"


def test_load_ai_config_sets_env_vars(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"API_PORT": "1234", "LLAMA_CPP": {"enabled": True}}),
                   encoding="utf-8")
    monkeypatch.setattr(cl, "_CONFIG_PATH", str(cfg))
    cl.load_ai_config()
    assert os.environ["API_PORT"] == "1234"
    # Complex types (dict) must NOT be stringified into env
    assert "LLAMA_CPP" not in os.environ or os.environ["LLAMA_CPP"] != "{'enabled': True}"


def test_get_api_url_localhost(monkeypatch):
    monkeypatch.setattr(cl, "load_ai_config",
                        lambda: {"API_PORT": "8000"})
    assert cl.get_api_url() == "http://localhost:8000"


def test_get_api_url_custom_port(monkeypatch):
    monkeypatch.setattr(cl, "load_ai_config",
                        lambda: {"API_PORT": "7777"})
    assert cl.get_api_url() == "http://localhost:7777"


def test_should_auto_start_api(monkeypatch):
    monkeypatch.setattr(cl, "load_ai_config",
                        lambda: {"AUTO_START_API": "true"})
    assert cl.should_auto_start_api() is True
    monkeypatch.setattr(cl, "load_ai_config",
                        lambda: {"AUTO_START_API": "FALSE"})
    assert cl.should_auto_start_api() is False


def test_get_llamacpp_config_defaults(monkeypatch):
    monkeypatch.setattr(cl, "load_ai_config", lambda: {})
    cfg = cl.get_llamacpp_config()
    assert cfg["enabled"] is False
    assert cfg["server_port"] == 8081
    # CPU-first defaults: gpu_layers 0, threads -1 (auto), context 0 (native).
    assert cfg["gpu_layers"] == 0
    assert cfg["threads"] == -1
    assert cfg["context_size"] == 0
    assert cfg["batch_size"] == 2048


def test_get_llamacpp_config_from_file(monkeypatch):
    monkeypatch.setattr(cl, "load_ai_config",
                        lambda: {"LLAMA_CPP": {"enabled": True, "server_port": 9999}})
    cfg = cl.get_llamacpp_config()
    assert cfg["enabled"] is True
    assert cfg["server_port"] == 9999


# ---------------------------------------------------------------------------
# runtime_paths
# ---------------------------------------------------------------------------


def test_agent_id_source_mode():
    assert rp._agent_id() == "Arrow"


def test_agent_id_frozen_uses_exe_name(monkeypatch):
    monkeypatch.setattr(rp.sys, "frozen", True, raising=False)
    monkeypatch.setattr(rp.sys, "executable", "C:\\agents\\MyAgent.exe")
    assert rp._agent_id() == "MyAgent"


def test_get_runtime_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(rp.sys, "frozen", False, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    appdata = tmp_path / "AppData"
    appdata.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    result = rp.get_runtime_dir()
    assert result == str(appdata / "Arrow" / "Arrow")
    assert os.path.isdir(result)


def test_get_runtime_dir_falls_back_to_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(rp.sys, "frozen", False, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(rp.tempfile, "gettempdir", lambda: str(tmp_path))
    result = rp.get_runtime_dir()
    assert result == str(tmp_path)
