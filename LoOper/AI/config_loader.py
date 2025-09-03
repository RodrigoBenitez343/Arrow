# AI/config_loader.py

import os
import json
from typing import Dict, Any

def load_ai_config() -> Dict[str, Any]:
    """Load AI configuration from config file and environment variables"""
    config = {
        "TAILSCALE_SERVER_IP": "100.125.184.115",
        "API_PORT": "8000",
        "OLLAMA_HOST": "localhost",
        "OLLAMA_PORT": "11434",
        "API_TIMEOUT": "30",
        "AUTO_START_API": "false",
        "USE_COMORAG": "true",
        "OVERWATCH_API_URL": "http://100.125.184.115:8000"
    }
    
    # Try to load from config file first
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception as e:
            print(f"Warning: Failed to load AI config file: {e}")
    
    # Override with environment variables if they exist
    for key in config.keys():
        env_value = os.getenv(key)
        if env_value is not None:
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
    return f"http://{host}:{port}"

def should_auto_start_api() -> bool:
    """Check if API should be auto-started"""
    config = load_ai_config()
    return config.get("AUTO_START_API", "false").lower() == "true"