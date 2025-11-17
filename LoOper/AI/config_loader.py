# AI/config_loader.py

import os
import json
from typing import Dict, Any

def load_ai_config() -> Dict[str, Any]:
    """Load AI configuration from config file and environment variables"""
    config = {
        "TAILSCALE_SERVER_IP": "",  # Leave blank to allow user configuration
        "API_PORT": "8000",
        "OLLAMA_HOST": "localhost",
        "OLLAMA_PORT": "11434",
        "API_TIMEOUT": "30",
        "AUTO_START_API": "false",
        "USE_COMORAG": "true",
        "OVERWATCH_API_URL": ""  # Leave blank to allow user configuration
    }
    
    # Try to load from config file first
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    file_config_loaded = False
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
                file_config_loaded = True
        except Exception as e:
            print(f"Warning: Failed to load AI config file: {e}")
    
    # Override with environment variables where appropriate
    # Prefer config file value for AUTO_START_API to honor GUI toggle if present,
    # but allow environment override when no config.json exists yet.
    for key in config.keys():
        env_value = os.getenv(key)
        if env_value is not None:
            if key == "AUTO_START_API" and file_config_loaded:
                # If a config file exists, keep its AUTO_START_API to honor GUI toggle
                continue
            config[key] = env_value
    
    # Set environment variables for current session
    for key, value in config.items():
        os.environ[key] = value
    
    return config

def get_api_url() -> str:
    """Get the current API URL based on configuration"""
    config = load_ai_config()
    host = config.get("TAILSCALE_SERVER_IP", "localhost")
    port = config.get("API_PORT", "8000")
    
    # If host is empty or localhost, use localhost for local development
    if not host or host.strip() == "":
        host = "localhost"
    
    return f"http://{host}:{port}"

def should_auto_start_api() -> bool:
    """Check if API should be auto-started"""
    config = load_ai_config()
    return config.get("AUTO_START_API", "false").lower() == "true"
