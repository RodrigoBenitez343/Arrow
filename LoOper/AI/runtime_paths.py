"""runtime_paths.py — durable runtime directory resolution.

All writable file outputs (automation.log, run.log, context.db) must use
:func:`get_runtime_dir` so they land in a durable, writable location:

  - ``exe_dir/runtime`` when writable (next to the executable)
  - else ``%LOCALAPPDATA%/Arrow/<agent-id>`` (per-user app data)
  - else ``%TEMP%`` (last resort)

Never ``_MEIPASS`` (ephemeral temp extraction dir) and never ``cwd`` (the
install directory may be read-only, e.g. ``C:\\Program Files\\...``).
"""

import os
import sys
import tempfile


def _agent_id() -> str:
    """Derive a stable per-agent id from the executable name (frozen builds)."""
    if getattr(sys, "frozen", False):
        try:
            return os.path.splitext(os.path.basename(sys.executable))[0] or "Arrow"
        except Exception:
            pass
    return "Arrow"


def get_runtime_dir() -> str:
    """Return a writable runtime directory for logs and databases.

    Priority:
      1. ``exe_dir/runtime`` — durable across restarts when the install dir
         is writable
      2. ``%LOCALAPPDATA%/Arrow/<agent-id>`` — per-user app data
      3. ``%TEMP%`` — last resort (never crashes startup)

    The chosen directory is created and verified writable before return.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        try:
            exe_dir = os.path.dirname(sys.executable)
            candidates.append(os.path.join(exe_dir, "runtime"))
        except Exception:
            pass
    local_appdata = os.environ.get("LOCALAPPDATA") or ""
    if local_appdata and str(local_appdata).strip():
        candidates.append(
            os.path.join(local_appdata, "Arrow", _agent_id())
        )
    candidates.append(tempfile.gettempdir())
    for candidate in candidates:
        try:
            os.makedirs(candidate, exist_ok=True)
            # Verify writability with a probe file (read-only installs fail here)
            probe = os.path.join(candidate, ".arrow_rw_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return candidate
        except Exception:
            continue
    return tempfile.gettempdir()
