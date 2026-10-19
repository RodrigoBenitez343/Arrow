"""goal_ledger.py — file-backed long-term goal ledger for the Orchestrator.

A wake-driven ledger, never a busy daemon: the Orchestrator node loads the
active goal on activation, appends its step trace, and writes the goal's
status back (open / blocked / done).  Scheduler entries or user messages
provide the wakes; this module only owns the state.

Storage: ``LoOper/data/agent_goals.json`` (override: ``LOOPER_GOAL_LEDGER``).
Writes are atomic (temp file + os.replace); a corrupt ledger is logged and
treated as empty — it must never crash the agent.
"""

import json
import logging
import os
import sys
import time
import uuid

logger = logging.getLogger(__name__)


def _default_path() -> str:
    # Frozen builds: the ledger lives in the durable runtime dir (like logs /
    # context.db), never inside the bundle — an install-dir write is lost on
    # update and fails outright on a read-only install.
    try:
        if getattr(sys, "frozen", False):
            from AI.runtime_paths import get_runtime_dir
            return os.path.join(get_runtime_dir(), "agent_goals.json")
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "data", "agent_goals.json"))


def _seed_from_bundle(path: str) -> None:
    """One-shot: adopt a goal ledger shipped INSIDE the frozen bundle.

    Old builds packaged the ledger at ``_MEIPASS/data/`` (or LoOper/data) while
    the resolver pointed elsewhere — the packaged goals were never read.  Copy
    the bytes once so a migrated install keeps its shipped goals.
    """
    if os.path.exists(path):
        return
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if not meipass:
            return
        for cand in ("data/agent_goals.json", "LoOper/data/agent_goals.json"):
            src = os.path.join(meipass, cand)
            if not os.path.exists(src):
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(src, "r", encoding="utf-8") as f:
                raw = f.read()
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(raw)
            os.replace(tmp, path)
            return
    except Exception:
        pass


def _path() -> str:
    env = os.environ.get("LOOPER_GOAL_LEDGER")
    if env:
        return env
    path = _default_path()
    _seed_from_bundle(path)
    return path


def load_goals() -> list:
    """All goals; [] on any failure (logged, never raised)."""
    path = _path()
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning("goal_ledger: %s is not a list — treating as empty", path)
    except Exception as e:
        logger.warning("goal_ledger: failed to read %s: %s — treating as empty", path, e)
    return []


def save_goals(goals: list) -> bool:
    """Atomic write. Returns False (logged) on failure."""
    path = _path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(goals, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning("goal_ledger: failed to write %s: %s", path, e)
        return False


def get_active_goal() -> dict:
    """The first open goal, else the first blocked one, else {}."""
    goals = load_goals()
    for g in goals:
        if isinstance(g, dict) and g.get("status") == "open":
            return g
    for g in goals:
        if isinstance(g, dict) and g.get("status") == "blocked":
            return g
    return {}


def start_goal(text: str) -> dict:
    """Create and persist a new open goal; returns its record."""
    goal = {
        "id": uuid.uuid4().hex[:12],
        "text": str(text or "").strip(),
        "status": "open",
        "created_at": time.time(),
        "updated_at": time.time(),
        "trace": [],
    }
    goals = load_goals()
    goals.append(goal)
    save_goals(goals)
    return goal


def update_goal(goal_id: str, **fields) -> bool:
    """Patch a goal by id (status/trace/...). False when not found."""
    goals = load_goals()
    found = False
    for g in goals:
        if isinstance(g, dict) and g.get("id") == goal_id:
            g.update(fields)
            g["updated_at"] = time.time()
            found = True
            break
    if found:
        save_goals(goals)
    return found


def record_activation(goal_id: str, trace: list, status: str, **fields) -> bool:
    """Append one activation's steps and outcome (status + fields) for a goal.

    The trace is APPENDED (bounded to the newest 200 entries), never replaced —
    the ledger's whole point is accumulated progress across activations.
    """
    goals = load_goals()
    for g in goals:
        if isinstance(g, dict) and g.get("id") == goal_id:
            merged = list(g.get("trace") or [])
            merged.extend(trace or [])
            # ponytail: bounded history; an unbounded file re-bloats the ledger
            g["trace"] = merged[-200:]
            g["status"] = str(status or "open")
            g["updated_at"] = time.time()
            for k, v in (fields or {}).items():
                g[k] = v
            save_goals(goals)
            return True
    return False
