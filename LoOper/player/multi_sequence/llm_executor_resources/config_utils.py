import os


def get_default_api_url() -> str:
    """Get default API URL using environment variables."""
    try:
        from AI.config_loader import get_api_url
        return get_api_url()
    except Exception:
        pass
    env_overwatch = os.getenv("OVERWATCH_API_URL")
    if env_overwatch and str(env_overwatch).strip():
        return env_overwatch.strip().rstrip("/")
    tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    if not tailscale_ip or not str(tailscale_ip).strip():
        tailscale_ip = "localhost"
    port = os.getenv("API_PORT", "8000")
    if not port or not str(port).strip():
        port = "8000"
    return f"http://{tailscale_ip}:{port}"
