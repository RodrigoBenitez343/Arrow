"""Client side of the generation-server hold (``AI/api.py`` owns the state).

The API gateway runs as its own process and starts/stops ``llama-server`` per
burst: a FORM pass asks the same model once per field, so the burst policy made
every field pay a cold start plus a full prompt re-prefill (live: 29 starts /
121.9 s of startup in a 44 min pass, 30 s of prefill for a 2-char reply, and the
``--cache-reuse`` / ``--keep`` flags with nothing to reuse).

A node execution pins the server here for its duration and drops it in a
``finally``: ``/llamacpp/hold`` increments the API's ref count,
``/llamacpp/unhold`` decrements it and the LAST one releases the engine.  The
API refuses the hold below ``LOOPER_RESIDENT_BUDGET_MB`` (see
``AI/engine_residency.py``), in which case the burst policy stands exactly as
before - this module never raises and never unloads anything itself.

Mirrors ``laya_client.hold()`` / ``embedding_server.hold()`` so a caller can
treat the three engines the same way::

    held = [m for m in (laya_client, embedding_server, generation_hold)
            if m.hold()]
    ...
    for module in held:
        module.unhold()
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_HOLD_TIMEOUT = 5.0


def _api_url() -> str:
    """The gateway base URL, or '' when it cannot be resolved."""
    try:
        from AI.config_loader import get_api_url
        return str(get_api_url() or "").rstrip("/")
    except Exception:
        return ""


def _post(path: str, timeout: Optional[float] = None) -> Optional[dict]:
    """POST to one hold endpoint; None on any fault (never raises)."""
    url = _api_url()
    if not url:
        logger.debug("[hold] no API url - generation hold skipped")
        return None
    try:
        import requests
        resp = requests.post(url + path, timeout=timeout or _HOLD_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, dict) else {}
        logger.debug("[hold] %s returned %s", path, resp.status_code)
    except Exception as exc:  # noqa: BLE001 - a hold is best effort
        logger.debug("[hold] %s failed: %s", path, exc)
    return None


def hold(timeout: Optional[float] = None) -> bool:
    """Pin the generation server for this execution; False when refused."""
    data = _post("/llamacpp/hold", timeout)
    return bool((data or {}).get("held"))


def unhold(timeout: Optional[float] = None) -> None:
    """Drop one hold (the last one releases the engine)."""
    _post("/llamacpp/unhold", timeout)


__all__ = ["hold", "unhold"]
