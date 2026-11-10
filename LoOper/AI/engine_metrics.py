"""engine_metrics.py — wall time and compute samples for the burst engines.

The app runs three model backends with very different lifecycles (Laya's
daemon, the llama.cpp embedding server, the llama.cpp generation server) and
the cost question is always the same: how long did a burst take, how much of
that was a COLD LOAD, and what is resident while it runs.  This module is the
one place those numbers land.

Two sinks, both optional at runtime:

  - a bounded in-memory ring (``snapshot`` / ``summary``), what the tests and
    the benchmark scripts read;
  - an append-only JSONL next to the app's other runtime files, so a live run
    can be replayed afterwards (``read_series``).

Recorded fields are NUMBERS ONLY (ms, bytes, counts, and the engine CODE
from :func:`engine_code`).  Text (a route label, a model name) is
deliberately not recorded: this file is a measurement log, not a transcript,
and a form value must never end up in it.

``LOOPER_ENGINE_METRICS=0/off/false`` turns every recorder into a no-op, which
is what a shipped build with no interest in the numbers can set.

Field values are sanitised on the way IN, so a caller may pass whatever it has
(a route string, a CRAG verdict) and only the numeric ones survive.
"""

import json
import os
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

#: Kinds emitted by the engines.  Not enforced (callers may add their own),
#: listed so the log stays greppable.
KINDS = ("engine_load", "engine_unload", "engine_start", "forward",
         "embed", "form_field", "context_trim", "index_purge")

#: Engines as NUMBERS: a sample cannot carry a name (numeric-only rule), and
#: "which engine paid that millisecond" is the first question anyone asks of
#: this log.  0 = unknown.
ENGINE_CODES = {"laya": 1, "embed": 2, "llama": 3, "llama-server": 3}


def engine_code(name: str) -> int:
    """Numeric id for an engine name (0 when it is not one of ours)."""
    return ENGINE_CODES.get(str(name or "").strip().lower(), 0)

_RING_MAX = 2048
_RECORDS: "deque[Dict[str, Any]]" = deque(maxlen=_RING_MAX)
_LOCK = threading.Lock()
_FILE_NAME = "engine_metrics.jsonl"

_OFF_VALUES = ("0", "off", "false", "disabled", "no")


def enabled() -> bool:
    """False when the operator turned the recorder off."""
    raw = (os.getenv("LOOPER_ENGINE_METRICS") or "").strip().lower()
    return raw not in _OFF_VALUES


def _num(value: Any) -> Optional[float]:
    """A field value as a number, or ``None`` when it is not one."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def jsonl_path() -> str:
    """Where the JSONL sink writes (runtime dir, never the install dir)."""
    try:
        from AI.runtime_paths import get_runtime_dir
        return os.path.join(get_runtime_dir(), _FILE_NAME)
    except Exception:
        import tempfile
        return os.path.join(tempfile.gettempdir(), _FILE_NAME)


def _append(line: str) -> None:
    try:
        with open(jsonl_path(), "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 - measurement never breaks a run
        pass


def record(kind: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Store one sample (ring + JSONL).  Unknown field types are dropped."""
    if not enabled():
        return None
    row: Dict[str, Any] = {"kind": str(kind or ""), "t": round(time.time(), 3)}
    for key, value in fields.items():
        number = _num(value)
        if number is not None:
            row[str(key)] = number if number != int(number) else int(number)
    with _LOCK:
        _RECORDS.append(row)
    _append(json.dumps(row, separators=(",", ":")))
    return row


class measure:
    """``with measure("forward", engine="laya"): ...`` -> one ``ms`` sample."""

    __slots__ = ("kind", "fields", "started")

    def __init__(self, kind: str, **fields: Any):
        self.kind = kind
        self.fields = fields
        self.started = 0.0

    def __enter__(self):
        self.started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = (time.perf_counter() - self.started) * 1000.0
        record(self.kind, ms=round(elapsed, 1), **self.fields)
        return False


def snapshot(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every recorded sample (optionally one kind), oldest first."""
    with _LOCK:
        rows = list(_RECORDS)
    if kind:
        return [r for r in rows if r.get("kind") == kind]
    return rows


def count(kind: str) -> int:
    """How many samples of *kind* were recorded."""
    return len(snapshot(kind))


def summary(kind: str) -> Dict[str, float]:
    """``{count, total_ms, mean_ms, max_ms}`` for one kind (zeros when empty)."""
    rows = snapshot(kind)
    times = [float(r.get("ms") or 0.0) for r in rows]
    if not times:
        return {"count": 0, "total_ms": 0.0, "mean_ms": 0.0, "max_ms": 0.0}
    return {"count": len(times), "total_ms": round(sum(times), 1),
            "mean_ms": round(sum(times) / len(times), 1),
            "max_ms": round(max(times), 1)}


def series(kind: str, field: str = "ms") -> List[float]:
    """One field of one kind, oldest first — the curve a scaling test reads."""
    return [float(r.get(field) or 0.0) for r in snapshot(kind)]


def read_series(path: str = "", kind: str = "", field: str = "ms") -> List[float]:
    """Same as :func:`series`, but from a JSONL file (post-run analysis)."""
    target = path or jsonl_path()
    out: List[float] = []
    try:
        with open(target, "r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if kind and row.get("kind") != kind:
                    continue
                value = _num(row.get(field))
                if value is not None:
                    out.append(value)
    except Exception:  # noqa: BLE001 - a missing log is an empty series
        return []
    return out


def reset() -> None:
    """Drop the in-memory ring (the JSONL is left alone)."""
    with _LOCK:
        _RECORDS.clear()


def rss_mb(process_name: str) -> float:
    """Resident MB of every process whose base name matches (0.0 when none).

    Windows images carry ``.exe``; the comparison strips it, so ``laya``
    matches ``laya.exe`` and ``llama-server`` matches ``llama-server.exe``.
    """
    wanted = str(process_name or "").strip().lower()
    if not wanted:
        return 0.0
    if wanted.endswith(".exe"):
        wanted = wanted[:-4]
    total = 0
    try:
        import psutil
        for proc in psutil.process_iter(["name", "memory_info"]):
            try:
                name = str(proc.info.get("name") or "").lower()
                if name.endswith(".exe"):
                    name = name[:-4]
                if name != wanted:
                    continue
                info = proc.info.get("memory_info")
                total += int(getattr(info, "rss", 0) or 0)
            except Exception:
                continue
    except Exception:  # noqa: BLE001 - psutil is optional at this layer
        return 0.0
    return round(total / (1024 * 1024), 1)


#: Bytes per KV element by cache type (llama.cpp ``-ctk`` / ``-ctv`` spellings).
_KV_BYTES = {"f32": 4.0, "f16": 2.0, "bf16": 2.0, "q8_0": 1.0,
             "q5_1": 0.75, "q5_0": 0.69, "q4_1": 0.63, "q4_0": 0.56}


def kv_elem_bytes(kv_type: str) -> float:
    """Bytes per KV element for a cache type (f16 when unknown)."""
    return _KV_BYTES.get(str(kv_type or "f16").strip().lower(), 2.0)


def kv_bytes(model_path: str, n_ctx: int, kv_type: str = "f16") -> int:
    """KV-cache bytes for *n_ctx* at *kv_type*, from the GGUF's own geometry.

    ``gguf_model_info.estimate_kv_cache_bytes`` already knows the layout; this
    only rescales its f16 answer for a quantised cache.  ``0`` when the model
    geometry cannot be read (never a guess).
    """
    try:
        from AI.gguf_model_info import (estimate_kv_cache_bytes,
                                        read_model_kv_params)
        params = read_model_kv_params(model_path)
        if params is None:
            return 0
        per_token = estimate_kv_cache_bytes(1, params)
        if per_token <= 0:
            return 0
        return int(per_token * max(1, int(n_ctx or 0))
                   * (kv_elem_bytes(kv_type) / 2.0))
    except Exception:  # noqa: BLE001
        return 0
