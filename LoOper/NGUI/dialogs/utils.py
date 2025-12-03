# dialogs/utils.py

import os
import sys

# Add parent directory to path for AI imports
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from AI.config_loader import get_api_url
except ImportError:
    # Fallback if config_loader is not available
    def get_api_url():
        tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
        port = os.getenv("API_PORT", "8000")
        return f"http://{tailscale_ip}:{port}"

def get_default_api_url():
    """Get default API URL using configuration system"""
    return get_api_url()