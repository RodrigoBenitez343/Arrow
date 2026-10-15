"""Interactive HTML artifact storage for Output nodes (``render_mode='html'``).

An LLM node that emits HTML is a self-contained artifact (HTML + CSS + JS).
This module persists it to a durable, user-browsable folder and normalizes
lightly-formatted model output into a loadable document, so both the desktop
overlay (QWebEngineView) and the web client (sandboxed iframe) can render it
interactively.
"""

import os
import re
import sys
import time

# ```html ... ``` fence an LLM often wraps the document in.
_FENCE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n?```\s*$", re.S)
_HTML_TAG = re.compile(r"<html[\s>]", re.I)


def artifacts_dir() -> str:
    """Return a durable, writable directory for saved artifacts.

    Frozen builds use the shared runtime dir (``AI.runtime_paths``); a dev
    checkout keeps them beside the other runtime state under ``LoOper/data``.
    """
    if getattr(sys, "frozen", False):
        try:
            from AI.runtime_paths import get_runtime_dir
            base = str(get_runtime_dir())
        except Exception:
            import tempfile
            base = os.path.join(tempfile.gettempdir(), "Arrow")
    else:
        from pathlib import Path
        base = str(Path(__file__).resolve().parent.parent.parent / "data")
    path = os.path.join(base, "artifacts")
    os.makedirs(path, exist_ok=True)
    return path


def _normalize(content: str) -> str:
    """Strip a code fence and wrap a bare fragment into a full document."""
    text = (content or "").strip()
    fence = _FENCE.match(text)
    if fence:
        text = fence.group(1).strip()
    if _HTML_TAG.search(text):
        return text
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "</head><body>" + text + "</body></html>"
    )


def save_artifact(content: str, label: str = "") -> str:
    """Write *content* as an HTML artifact and return its absolute path."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(label or "artifact")).strip("-")[:40]
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug or 'artifact'}.html"
    path = os.path.join(artifacts_dir(), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_normalize(content))
    return path
