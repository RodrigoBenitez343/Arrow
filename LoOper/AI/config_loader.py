# AI/config_loader.py

import os
import sys
import json
from typing import Dict, Any


# In frozen builds, __file__ may resolve to a different path than where
# data files are bundled.  Search multiple candidate locations.
def _find_config_path() -> str:
    # Default: next to this module
    candidates = [os.path.join(os.path.dirname(__file__), "config.json")]
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for prefix in ["", "_internal", "arrow", os.path.join("_internal", "arrow"), os.path.join("_internal", "LoOper")]:
            candidates.append(os.path.join(exe_dir, prefix, "AI", "config.json"))
        # One-file builds: everything is inside sys._MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for prefix in ["", "LoOper"]:
                candidates.append(os.path.join(meipass, prefix, "AI", "config.json"))
    for p in candidates:
        normalized = os.path.normpath(p)
        if os.path.isfile(normalized):
            return normalized
    return candidates[0]  # fall back to default


_CONFIG_PATH = _find_config_path()


def get_config_path() -> str:
    """Return the resolved AI config.json path."""
    return _CONFIG_PATH


def get_models_dir() -> str:
    """Return the AI/models directory for llama.cpp GGUF model files.

    In source mode this is <project>/LoOper/AI/models/.
    In compiled (frozen) mode we try multiple locations because
    PyInstaller's COLLECT puts data files inside _internal/, but
    users may also place models alongside the exe.
    The directory may not exist yet — callers should create it if needed.
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        # 1) Preferred: next to the exe (e.g. dist/arrow/AI/models/)
        primary = os.path.join(exe_dir, "AI", "models")
        if os.path.isdir(primary):
            return primary
        # 2) PyInstaller onedir: inside _internal/ (e.g. dist/arrow/_internal/AI/models/)
        internal = os.path.join(exe_dir, "_internal", "AI", "models")
        if os.path.isdir(internal):
            return internal
        # 3) PyInstaller onedir: inside _internal/LoOper/ (e.g. dist/arrow/_internal/LoOper/AI/models/)
        looper_internal = os.path.join(exe_dir, "_internal", "LoOper", "AI", "models")
        if os.path.isdir(looper_internal):
            return looper_internal
        # 4) One-file build: extracted to sys._MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for sub in ["", "LoOper"]:
                mp = os.path.join(meipass, sub, "AI", "models")
                if os.path.isdir(mp):
                    return mp
        # 5) Return primary regardless — caller should create it
        return primary
    # Source mode: this module lives at LoOper/AI/config_loader.py,
    # so models are in the sibling 'models' directory.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

def load_ai_config() -> Dict[str, Any]:
    """Load AI configuration from config file and environment variables"""
    config = {
        "API_PORT": "8000",
        "OLLAMA_HOST": "localhost",
        "OLLAMA_PORT": "11434",
        "API_TIMEOUT": "30",
        "AUTO_START_API": "false",
        "OVERWATCH_API_URL": "http://localhost:8001"
    }
    
    # Try to load from config file first
    file_config_loaded = False
    file_keys = set()
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                file_config = json.load(f)
                if isinstance(file_config, dict):
                    file_keys = set(file_config.keys())
                config.update(file_config)
                file_config_loaded = True
        except Exception as e:
            print(f"Warning: Failed to load AI config file: {e}")
    
    # Override with environment variables where appropriate
    for key in config.keys():
        env_value = os.getenv(key)
        if env_value is not None:
            if file_config_loaded and key in file_keys:
                continue
            config[key] = env_value

    # Drop the legacy Tailscale bridge keys (feature removed).  They can
    # linger in older config files and must not re-seed env vars.
    for stale in [k for k in config if k.startswith("TAILSCALE_")]:
        config.pop(stale, None)
        os.environ.pop(stale, None)
    
    # Set environment variables for current session
    for key, value in config.items():
        # Only set string values as environment variables
        # Skip nested dicts like LLAMA_CPP config
        if isinstance(value, str):
            os.environ[key] = value
        elif isinstance(value, (int, float, bool)):
            os.environ[key] = str(value)
        # Skip dict, list, and other complex types
    
    return config

def get_api_url() -> str:
    """Get the current API URL based on configuration (local gateway)."""
    config = load_ai_config()
    port = config.get("API_PORT", "8000")
    return f"http://localhost:{port}"

def should_auto_start_api() -> bool:
    """Check if API should be auto-started"""
    config = load_ai_config()
    return config.get("AUTO_START_API", "false").lower() == "true"

def get_llamacpp_config() -> Dict[str, Any]:
    """Get llama.cpp specific configuration"""
    config = load_ai_config()
    llamacpp_config = config.get("LLAMA_CPP", {})
    
    # Return default config if not specified
    # CPU-first defaults: gpu_layers 0, threads -1 (auto), context 0 (native).
    if not llamacpp_config:
        return {
            "enabled": False,
            "llama_cpp_path": "utils/llama.cpp",
            "default_model_path": "",
            "embedding_model_path": "embeddinggemma-300M-Q8_0.gguf",
            "server_port": 8081,
            "gpu_layers": 0,
            "threads": -1,
            "context_size": 0,
            "batch_size": 2048
        }
    
    return llamacpp_config
