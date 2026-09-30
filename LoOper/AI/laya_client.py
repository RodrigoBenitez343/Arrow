"""laya_client.py — embedded client for the Laya System-1 decision engine.

Laya (ModernBERT-large, typed ``noul`` / ``choice`` / ``score`` questions, one
encoder pass, no token generation) ships inside the app bundle and runs as a
persistent child process:

    <AI>/bin/laya.exe daemon <model.gguf> --device cpu --threads N

The daemon speaks newline JSON-RPC on stdin/stdout: the model loads once
(~2 s), then every verdict is a single forward (~2 s on 12 CPU threads).
One request is one JSON line with an ``"id"``; responses carry the same
``"id"`` plus ``answers`` and ``usage`` (the first stdout line is a
``{"status": "ready"}`` banner).

Load -> use -> unload: the daemon is a BURST resource, not a resident one.
It shares one memory pool with llama-server (measured 2026-09-23 on the APU
build: a resident Laya collapsed the llama.cpp native context 262144 -> 1024
tokens and OOM'd the Vulkan KV-cache allocation), so it starts on the first
request and stops once the burst ends — ``LOOPER_LAYA_KEEP_ALIVE_SECONDS``
(grace after the last request, default 2; negative pins it resident) — and
``release()`` stops it on demand (the llama-server start path calls it before
allocating its model).  A request that arrives after a stop simply relaunches
the daemon (~2 s), exactly like a cold start.

This client is deliberately forgiving: **every** failure path returns ``None``
so callers (the orchestrator's Laya picker, the input node's decision
evaluator, ``AI/laya_hooks``) degrade to their LLM fallbacks instead of
failing the node.  ``LOOPER_LAYA=0/off/disabled`` forces every consumer onto
the LLM path (``LOOPER_JEV`` is honored as the legacy name of the same kill
switch).
"""

import atexit
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Default quant: the fastest of the three shipped English quants (measured
# 9/22: q4_k_m 2.0 s / q8_0 2.6 s / f16 4.2 s per noul, same verdicts).
# LOOPER_LAYA_MODEL overrides (bare filename resolves in AI/models/laya-GGUF,
# an absolute path is used as-is).
_MODEL_DEFAULT = "laya_english_ud_q4_k_m.gguf"
_MODEL_FALLBACKS = ("laya_english_q8_0.gguf", "laya_english_f16.gguf")
_GGUF_DIR = "laya-GGUF"

_READY_GRACE_S = 4.0  # first-launch grace; later calls re-probe

# Burst policy knobs (see the module docstring).  The grace lets the calls of
# ONE decision burst (done-probe -> worker choice -> ask-probe, ~2 s apart)
# share a single loaded model; anything longer reloads it.
def _env_float(name: str, default: float) -> float:
    try:
        raw = (os.environ.get(name) or "").strip()
        return float(raw) if raw else default
    except ValueError:
        return default


_KEEP_ALIVE_SECONDS = _env_float("LOOPER_LAYA_KEEP_ALIVE_SECONDS", 2.0)

_PROC: Optional[subprocess.Popen] = None
_STDIN_LOCK = threading.Lock()
_QUEUE: "queue.Queue[str]" = queue.Queue()
_READY = False
_SESSION_DISABLED = False
_SEQ = 0
_INFLIGHT = 0
_LAUNCH_TRIED = False


def _disabled() -> bool:
    """Global kill-switch: ``LOOPER_LAYA=0/off/disabled`` (legacy: LOOPER_JEV)."""
    val = (os.environ.get("LOOPER_LAYA")
           or os.environ.get("LOOPER_JEV") or "").strip().lower()
    return val in ("0", "off", "false", "disabled")


def _models_dir() -> str:
    try:
        from AI.config_loader import get_models_dir
        base = get_models_dir()
    except Exception:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
    return os.path.join(base, _GGUF_DIR)


def _find_model() -> str:
    """Resolve the GGUF: LOOPER_LAYA_MODEL, else the default quant, else the
    first shipped fallback that exists, else any *.gguf in the models dir."""
    wanted = (os.environ.get("LOOPER_LAYA_MODEL") or "").strip()
    if wanted:
        cand = wanted if os.path.isabs(wanted) else os.path.join(_models_dir(), wanted)
        if os.path.exists(cand):
            return cand
        logger.info("Laya: LOOPER_LAYA_MODEL=%r not found — using defaults", wanted)
    d = _models_dir()
    for name in (_MODEL_DEFAULT,) + _MODEL_FALLBACKS:
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
    try:
        for f in sorted(os.listdir(d)):
            if f.lower().endswith(".gguf"):
                return os.path.join(d, f)
    except OSError:
        pass
    return ""


def _find_exe() -> str:
    """Resolve laya.exe: LOOPER_LAYA_EXE, else the frozen bundle layouts,
    else the source tree (LoOper/AI/bin/laya.exe)."""
    override = (os.environ.get("LOOPER_LAYA_EXE") or "").strip()
    if override and os.path.exists(override):
        return override
    candidates: List[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates += [
            os.path.join(meipass, "bin", "laya.exe"),
            os.path.join(meipass, "LoOper", "AI", "bin", "laya.exe"),
            os.path.join(meipass, "laya.exe"),
        ]
    if getattr(sys, "frozen", False):
        candidates.append(os.path.join(os.path.dirname(sys.executable), "bin", "laya.exe"))
    else:
        candidates.append(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "bin", "laya.exe"))
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    return ""


def _threads() -> int:
    try:
        n = int((os.environ.get("LOOPER_LAYA_THREADS") or "").strip() or 0)
        if n > 0:
            return min(n, 32)
    except ValueError:
        pass
    return min(os.cpu_count() or 4, 16)


def _reader(proc: subprocess.Popen) -> None:
    """Drain stdout line-by-line (banner + responses) into the queue so a
    wedged model can never block the caller on a raw pipe read."""
    global _READY
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = (line or "").strip()
            if not line:
                continue
            if '"status"' in line:
                _READY = True
            _QUEUE.put(line)
    except Exception:
        pass


def _disable_for_session(reason: str) -> None:
    """Definitive unavailability: stop retrying for the whole session."""
    global _SESSION_DISABLED
    _SESSION_DISABLED = True
    logger.info(
        "Laya engine disabled for this session (%s) — Laya pickers/decisions "
        "fall back to the LLM path", reason,
    )


def _wait_response(req_id: int, timeout: Optional[float]) -> Optional[dict]:
    """Pop queue lines until the response with *req_id* arrives."""
    deadline = None if timeout is None else time.time() + timeout
    while True:
        remaining = None if deadline is None else deadline - time.time()
        if remaining is not None and remaining <= 0:
            return None
        try:
            line = _QUEUE.get(timeout=remaining)
        except queue.Empty:
            return None
        try:
            data = json.loads(line)
        except Exception:
            continue
        if isinstance(data, dict) and str(data.get("id")) == str(req_id):
            return data


def status() -> str:
    """Diagnostic state: 'ready' (model loaded), 'loading' (process up, model
    still loading), 'down' (nothing running), 'off' (kill switch)."""
    global _PROC
    if _disabled() or _SESSION_DISABLED:
        return "off"
    if _PROC is not None and _PROC.poll() is None:
        return "ready" if _READY else "loading"
    return "down"


def available() -> bool:
    return status() == "ready"


def ensure_running() -> bool:
    """Lazily start the daemon once, then report readiness.

    Auto-detection: runtime = ``LOOPER_LAYA_EXE`` or ``AI/bin/laya.exe``;
    model = ``LOOPER_LAYA_MODEL`` or the first shipped quant under
    ``AI/models/laya-GGUF``.  When prerequisites are missing the engine is
    disabled for the session (logged once) and every consumer keeps its LLM
    fallback.
    """
    global _PROC, _LAUNCH_TRIED, _READY
    if _disabled() or _SESSION_DISABLED:
        return False
    if _PROC is not None and _PROC.poll() is None:
        return _READY
    exe = _find_exe()
    if not exe:
        _disable_for_session("laya.exe not found — run build_pipeline or copy it to AI/bin")
        return False
    model = _find_model()
    if not model:
        _disable_for_session("no Laya GGUF under AI/models/laya-GGUF")
        return False
    _LAUNCH_TRIED = True
    cmd = [exe, "daemon", model, "--device", "cpu", "--threads", str(_threads())]
    try:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        _READY = False
        while not _QUEUE.empty():  # stale lines from a dead process
            _QUEUE.get_nowait()
        _PROC = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            bufsize=1, cwd=os.path.dirname(model), **kwargs,
        )
        threading.Thread(target=_reader, args=(_PROC,), daemon=True,
                         name="laya-daemon-reader").start()
        logger.info("Laya engine launching: %s", " ".join(cmd))
    except Exception as e:
        _disable_for_session(f"launch failed: {e}")
        return False
    # Short grace window: report ready only if the model finished loading,
    # otherwise the caller falls back and the next call re-probes.
    deadline = time.time() + _READY_GRACE_S
    while time.time() < deadline:
        if _READY:
            break
        if _PROC.poll() is not None:
            _disable_for_session("daemon exited during startup — check AI/bin/laya.exe")
            return False
        time.sleep(0.2)
    return _READY


def _stop_daemon(reason: str) -> bool:
    """Terminate the daemon and clear the process state (idempotent)."""
    global _PROC, _READY
    with _STDIN_LOCK:
        proc = _PROC
        _PROC = None
        _READY = False
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.terminate()
        except Exception:
            return False
    while not _QUEUE.empty():  # stale lines from the dead process
        try:
            _QUEUE.get_nowait()
        except queue.Empty:
            break
    logger.info("Laya model unloaded (%s) — load -> use -> unload per burst", reason)
    return True


def release(wait: float = 5.0) -> bool:
    """Stop the daemon NOW, freeing its RAM; the next request relaunches it.

    Called by the llama-server start path (ONE model per memory pool) and by
    the idle unloader once a burst ends.  A forward in flight is never killed
    out from under its caller: wait up to *wait* s for it, then give up and
    return False (the daemon stays up; the caller keeps its verdict).
    """
    deadline = time.time() + max(0.0, float(wait or 0.0))
    while _INFLIGHT > 0 and time.time() < deadline:
        time.sleep(0.02)
    if _INFLIGHT > 0:
        return False
    return _stop_daemon("released")


def _unload_after_idle(seq_at_schedule: int) -> None:
    """Stop the daemon once the burst that scheduled this check is done."""
    if _KEEP_ALIVE_SECONDS > 0:
        time.sleep(_KEEP_ALIVE_SECONDS)
    if _SEQ != seq_at_schedule or _INFLIGHT > 0:
        return  # a newer request is using the daemon — keep it loaded
    release(wait=0.0)


def _schedule_unload(seq_at_schedule: int) -> None:
    """Arm the idle unloader after every completed request."""
    if _KEEP_ALIVE_SECONDS < 0:
        return  # operator pinned the model resident
    threading.Thread(
        target=_unload_after_idle, args=(seq_at_schedule,),
        daemon=True, name="laya-idle-unloader",
    ).start()


def _request(payload: Dict, timeout: Optional[float] = None) -> Optional[dict]:
    """One JSON-RPC round-trip with the daemon (serialized)."""
    global _SEQ, _INFLIGHT
    if _disabled() or _SESSION_DISABLED:
        return None
    if not available() and not ensure_running():
        return None
    proc = _PROC
    if proc is None or proc.poll() is not None:
        return None
    with _STDIN_LOCK:
        if _PROC is not proc or proc.poll() is not None:
            return None  # a concurrent release stopped it — next call relaunches
        _SEQ += 1
        req_id = _SEQ
        _INFLIGHT += 1
        payload = dict(payload, id=str(req_id))
        try:
            proc.stdin.write(json.dumps(payload) + "\n")  # type: ignore[union-attr]
            proc.stdin.flush()  # type: ignore[union-attr]
        except Exception as e:
            _INFLIGHT -= 1
            logger.debug("Laya daemon write failed: %s", e)
            return None
    try:
        resp = _wait_response(req_id, timeout)
    finally:
        _INFLIGHT -= 1
        _schedule_unload(req_id)  # burst end: unload unless a newer request lands
    if resp is None:
        logger.debug("Laya daemon gave no response for id=%s", req_id)
    return resp


def _answer(resp: Optional[dict], qid: str) -> Optional[dict]:
    if not resp:
        return None
    answers = resp.get("answers") or {}
    return answers.get(qid) if isinstance(answers, dict) else None


# ---------------------------------------------------------------- primitives

def noul(state: str, instructions: str) -> Optional[float]:
    """P(the statement *instructions* holds for *state*), or None on failure."""
    resp = _request({"state": str(state), "questions": {
        "q": {"type": "noul", "instructions": str(instructions)}}})
    ans = _answer(resp, "q")
    if ans is None:
        return None
    try:
        return float(ans.get("noul"))
    except (TypeError, ValueError):
        return None


def choice(state: str, instructions: str,
           criteria: Dict[str, str]) -> Optional[str]:
    """The criterion key the model picks for *state*, or None on failure."""
    if not criteria:
        return None
    resp = _request({"state": str(state), "questions": {
        "q": {"type": "choice", "instructions": str(instructions),
              "criteria": criteria}}})
    ans = _answer(resp, "q")
    if ans is None:
        return None
    picked = ans.get("choice")
    return picked if picked in criteria else None


# ------------------------------------------------------------- verdict API

def decision(premise: str, criterion: str) -> Optional[bool]:
    """Binary decision: does the premise satisfy the criterion?
    Calibrated noul probability with a 0.5 threshold; None when unreachable."""
    p = noul(premise, criterion)
    if p is None:
        return None
    return bool(p > 0.5)


def rerank(premise: str, options: Sequence[str]) -> Optional[int]:
    """Index of the best-matching option for the premise, or None.

    Laya answers with one 'choice' among the option texts (single forward);
    the app's pickers treat the returned index exactly like the old
    entailment-argmax rerank."""
    opts = [str(o) for o in options if str(o).strip()]
    if not opts:
        return None
    picked = choice(
        str(premise),
        "Which option is the best next step for the state?",
        {o: "" for o in opts},
    )
    if picked is None:
        return None
    try:
        return opts.index(picked)
    except ValueError:
        return None


def _shutdown() -> None:
    release(wait=1.0)


atexit.register(_shutdown)
