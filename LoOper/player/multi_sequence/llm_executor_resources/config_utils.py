import os


def get_default_api_url() -> str:
    """Get default API URL using environment variables."""
    try:
        from AI.config_loader import get_api_url
        return get_api_url()
    except Exception:
        pass
    tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    port = os.getenv("API_PORT", "8001")
    return f"http://{tailscale_ip}:{port}"
