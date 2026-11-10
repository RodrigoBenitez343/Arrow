"""engine_residency.py — whether this machine can afford loaded burst engines.

Laya (encoder) and the embedding server are burst resources: both are loaded
on first use and stopped once their burst ends, because a resident engine
starves the generation server's KV-cache allocation on a small box (measured
2026-09-23: a resident Laya collapsed the native context 262144 -> 1024 and
OOM'd the Vulkan KV slot).

That policy is right for a constrained machine and wasteful for any other: a
form pass pays one cold load per field (see the hold API in
``AI/laya_client.py`` and ``AI/embedding_server.py``).  This module answers the
one question that decides between the two policies:

    is there enough free RAM to keep the burst engines loaded?

The answer is a plain env budget so the operator can tune it per machine:

  ``LOOPER_RESIDENT_BUDGET_MB``  free MB required before an engine is pinned
                                 (default 1024; ``0`` or negative = always
                                 allow, which is how a big box forces the
                                 resident set regardless of measurements)

When psutil is missing the answer is ``False``: the burst policy is the safe
default, never a silent downgrade of anything the user asked for.
"""

import os

DEFAULT_BUDGET_MB = 1024


def budget_mb() -> int:
    """Free-MB threshold required before an engine may stay resident."""
    raw = (os.getenv("LOOPER_RESIDENT_BUDGET_MB")
           or "").strip()
    if not raw:
        return DEFAULT_BUDGET_MB
    try:
        return int(float(raw))
    except ValueError:
        return DEFAULT_BUDGET_MB


def available_mb() -> float:
    """System-wide available RAM in MB (0.0 when it cannot be read)."""
    try:
        import psutil
        return round(psutil.virtual_memory().available / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001 - psutil is optional here
        return 0.0


def residents_allowed() -> bool:
    """True when the burst engines may be held loaded between requests."""
    need = budget_mb()
    if need <= 0:
        return True
    free = available_mb()
    return free > 0.0 and free >= need
