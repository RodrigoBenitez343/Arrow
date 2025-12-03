import os


def get_default_api_url() -> str:
    """Get default API URL using environment variables.

    Matches the logic used by the previous implementation and consult.py.
    """
    tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{tailscale_ip}:{port}"