"""description_repair.py — ratings -> rewritten chain descriptions (closed loop).

The agent overlay lets the user rate every tool output 1-10.  A rating below
9 is a penalty: the rated chain mis-routed (fired when it should not have,
or another tool should have fired).  Instead of retraining anything, this
module asks a local LLM to rewrite chain descriptions — the single signal
both routing stages read (the embedding ranker corpus and the Router LLM
prompt; brain descriptions feed the Orchestrator's Laya picker).  The next
run routes on the corrected text: recursive reinforcement through data,
not weights.

Rules:
* stars 9-10 -> NO penalty: nothing is rewritten (the event is recorded).
* stars <= 8 -> penalty: lower stars = stronger correction.  The rewrite
  narrows scope / adds exclusions for leaks, or adds "use when" coverage
  when the tool was supposed to fire.
* The rewrite may TARGET A DIFFERENT chain when the feedback says another
  tool should have run and that tool's description is the one missing the
  case: the prompt receives the sibling tools/brains (same router or the
  Orchestrator's brains) with their descriptions and the model answers
  which chain file it rewrites.  The target MUST be one of those
  candidates — an unknown file name falls back to the rated chain.

Storage / safety:
* Audit trail: ``LoOper/data/description_feedback.jsonl`` — one JSON object
  per event (chain, alias, stars, feedback, old/new description, goal).
* The chain file's description is replaced with a targeted regex so the
  rest of the file (formatting, embedded data) is preserved byte-for-byte;
  the result is JSON-validated before an atomic write.
* Kill switch: ``LOOPER_DESC_REPAIR=off`` (feedback is still recorded).
"""

import json
import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)

RATING_MAX = 10
PENALTY_MAX = 8          # 9-10 -> no penalty (per the rating contract)
MIN_DESC_CHARS = 30
MAX_DESC_CHARS = 600

# SmolLM3 is always shipped and is the router's own model — the rewrite
# speaks exactly the register the router reads.
DEFAULT_MODEL = "SmolLM3-Q4_K_M.gguf"

_REPAIR_LOCK = threading.Lock()


# ------------------------------------------------------------------ helpers

def penalty_weight(stars) -> int:
    """0 for 9-10; 8..1 for stars 1..8 (each level is less penalty)."""
    try:
        s = int(stars)
    except Exception:
        return 0
    s = max(1, min(RATING_MAX, s))
    if s > PENALTY_MAX:
        return 0
    return PENALTY_MAX - s + 1


def _repair_disabled() -> bool:
    return str(os.environ.get("LOOPER_DESC_REPAIR", "")).strip().lower() in (
        "0", "off", "false", "disabled",
    )


def _data_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "data"))


def _audit_path() -> str:
    return os.environ.get("LOOPER_DESC_FEEDBACK_LOG") or os.path.join(
        _data_dir(), "description_feedback.jsonl")


def _append_audit(entry: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_audit_path()), exist_ok=True)
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # never break the overlay
        logger.warning("description_repair: audit append failed: %s", e)


# Root-level 'description' (line-anchored first: chain files are pretty-printed
# so the fallback only fires on compact/minified JSON).
_DESC_RE_ANCHORED = r'^[ \t]*"description"\s*:\s*"((?:[^"\\]|\\.)*)"'
_DESC_RE_ANY = r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"'


def _read_chain_desc(text: str) -> str:
    """The root 'description' value, unescaped ('' when absent)."""
    m = (re.search(_DESC_RE_ANCHORED, text, flags=re.MULTILINE)
         or re.search(_DESC_RE_ANY, text))
    if not m:
        return ""
    try:
        return json.loads('"' + m.group(1) + '"')
    except Exception:
        return m.group(1)


def _replace_chain_desc(text: str, new_desc: str) -> str:
    """Replace the FIRST root 'description' value, preserving the rest."""
    escaped = json.dumps(new_desc, ensure_ascii=False)[1:-1]
    repl = lambda m: m.group(1) + '"' + escaped + '"'
    new_text = re.sub(
        r'^([ \t]*"description"\s*:\s*)"(?:[^"\\]|\\.)*"',
        repl, text, count=1, flags=re.MULTILINE,
    )
    if new_text == text:  # compact/minified JSON: key not at a line start
        new_text = re.sub(
            r'("description"\s*:\s*)"(?:[^"\\]|\\.)*"',
            repl, text, count=1,
        )
    return new_text


def _chain_summary(cfg: dict) -> str:
    """What the chain actually does, from its own structure."""
    parts = []
    counts = []
    for key, label in (
        ("web_sequences", "web sequences"),
        ("sequences", "desktop sequences"),
        ("input_nodes", "input nodes"),
        ("llm_nodes", "LLM nodes"),
        ("conditional_nodes", "conditionals"),
        ("chain_import_nodes", "imported sub-chains"),
        ("output_nodes", "outputs"),
        ("handle_nodes", "handle nodes"),
        ("context_nodes", "context nodes"),
        ("code_nodes", "code nodes"),
    ):
        n = len(cfg.get(key) or [])
        if n:
            counts.append(f"{n} {label}")
    if counts:
        parts.append(", ".join(counts))
    labels = []
    for inode in (cfg.get("input_nodes") or []):
        lbl = str(inode.get("label") or "").strip()
        if lbl:
            labels.append(lbl)
    if labels:
        parts.append("asks for: " + "; ".join(labels[:4]))
    prompts = []
    for lnode in (cfg.get("llm_nodes") or [])[:2]:
        p = str(lnode.get("prompt") or "").strip().replace("\n", " ")
        if p:
            prompts.append(p[:120])
    if prompts:
        parts.append("its models do: " + " | ".join(prompts))
    return "; ".join(parts) or "(no structural hints)"


def _basename(path: str) -> str:
    return os.path.splitext(os.path.basename(str(path)))[0].lower()


def _chains_dir(chain_path: str) -> str:
    d = os.path.dirname(os.path.abspath(str(chain_path)))
    if os.path.isdir(d):
        return d
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", "chains"))


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def tree_context(chain_path: str, limit: int = 8):
    """Sibling tools/brains at the rated chain's level(s), with descriptions.

    Two discovery passes over the chains dir:
    * routers: files whose chain_import nodes reference this chain on a
      'tools' port -> their OTHER tool imports are siblings;
    * brains: files with an orchestrator node whose 'brains' imports are
      chains -> those brains are the orchestration-level candidates.
    Returns [{'path','alias','description'}] without the rated chain itself.
    """
    chains_dir = _chains_dir(chain_path)
    rated_base = _basename(chain_path)
    found = {}
    try:
        files = [os.path.join(chains_dir, f) for f in os.listdir(chains_dir)
                 if f.lower().endswith(".json")]
    except OSError:
        return []

    for fpath in files:
        cfg = _load_json(fpath)
        if not cfg:
            continue
        imports = cfg.get("chain_import_nodes") or []
        imports_chain = any(
            _basename(n.get("chain_file_path") or n.get("chain_file") or "") == rated_base
            for n in imports if isinstance(n, dict)
        )
        if imports_chain:
            for n in imports:
                if not isinstance(n, dict):
                    continue
                base = _basename(n.get("chain_file_path") or n.get("chain_file") or "")
                if not base or base == rated_base or base in found:
                    continue
                target = os.path.join(chains_dir, base + ".json")
                desc = _read_chain_desc(_read_text(target))
                if desc:
                    found[base] = {
                        "path": target,
                        "alias": str(n.get("prefix") or base),
                        "description": desc,
                    }
        # Orchestrator brains: any chain reaching this one at brain level.
        orch = cfg.get("orchestrator_nodes") or []
        if orch:
            for n in imports:
                if not isinstance(n, dict):
                    continue
                base = _basename(n.get("chain_file_path") or n.get("chain_file") or "")
                if not base or base == rated_base or base in found:
                    continue
                target = os.path.join(chains_dir, base + ".json")
                desc = _read_chain_desc(_read_text(target))
                if desc:
                    found[base] = {
                        "path": target,
                        "alias": base,
                        "description": desc,
                    }
    return list(found.values())[:limit]


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


# ------------------------------------------------------------------ the LLM

def _api_base() -> str:
    try:
        from AI.config_loader import load_ai_config
        port = str(load_ai_config().get("API_PORT") or "8000")
    except Exception:
        port = "8000"
    return f"http://127.0.0.1:{port}"


# Bounded wait for the app's own model gateway.  A dead/unloaded engine
# trickles connection retries for minutes (measured 2026-09-23: memory chat
# stalled 4 min on `/completion` retries after a Vulkan OOM); a hard bound
# lets every caller fall back to its honest non-model path promptly.
_MODEL_TIMEOUT = 180


def _call_local_model(prompt: str, system: str, model: str,
                      max_tokens: int = 220) -> str:
    """One local generation via the app's own FastAPI ('' on failure)."""
    import requests
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    resp = requests.post(f"{_api_base()}/llamacpp/generate", json=payload,
                         timeout=_MODEL_TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"generate returned {resp.status_code}: {resp.text[:200]}")
    data = resp.json() or {}
    return str(data.get("response") or data.get("text") or "")


def _build_prompt(chain_path, alias, desc, summary, stars, feedback,
                  goal_text, output_excerpt, candidates) -> str:
    weight = penalty_weight(stars)
    lines = [
        f"TOOL FILE: {os.path.basename(chain_path)} (alias <{alias or _basename(chain_path)}>)",
        f"CURRENT DESCRIPTION: {desc or '(none)'}",
        f"WHAT IT ACTUALLY DOES (from its own structure): {summary}",
        f"USER RATING: {stars}/10 (penalty {weight}/8, higher = worse)",
    ]
    if feedback:
        lines.append(f"USER FEEDBACK: {str(feedback).strip()[:400]}")
    else:
        lines.append("USER FEEDBACK: (none given — infer from the request and output)")
    if goal_text:
        lines.append(f"USER REQUEST THAT TRIGGERED IT: {str(goal_text).strip()[:300]}")
    if output_excerpt:
        lines.append(f"ITS OUTPUT (excerpt): {str(output_excerpt).strip()[:250]}")
    if candidates:
        lines.append("OTHER AVAILABLE TOOLS AT THE SAME LEVEL:")
        for c in candidates:
            lines.append(f"- <{c['alias']}> ({os.path.basename(c['path'])}): "
                         f"{c['description'][:160]}")
    lines.append("")
    lines.append("Rewrite the description so the router picks correctly next time:")
    lines.append("- Keep the tool's real purpose; add or sharpen explicit exclusions as "
                 "routing rules, e.g. \"do not use to open LinkedIn or browse jobs\".")
    lines.append("- If the feedback blames a different listed tool, you MAY rewrite THAT "
                 "tool instead when its description is the one missing the case.")
    lines.append("- Never mention the rating, the feedback, the user, or these "
                 "instructions; no meta sentences like \"if it misfired\".")
    lines.append("Answer EXACTLY in this format (two lines, nothing else):")
    lines.append(f"FILE: {os.path.basename(chain_path)}")
    lines.append("DESCRIPTION: <one to three sentences, concrete verbs, "
                 "state prerequisites, no quotes>")
    return "\n".join(lines)


_SYSTEM = ("You are a precise technical writer for a tool-routing system. "
           "You output only the two requested lines: FILE and DESCRIPTION.")


def _parse_reply(raw: str):
    """(file_base or '', description) from the model's two-line answer."""
    file_base = ""
    desc = ""
    for line in str(raw or "").splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("file:") and not file_base:
            file_base = s.split(":", 1)[1].strip().strip('<>').strip()
        elif low.startswith("description:") and not desc:
            desc = s.split(":", 1)[1].strip()
        elif desc:
            desc += " " + s  # model wrapped the description over lines
    if not desc:
        # Fallback: first non-empty line that is not the FILE header.
        for line in str(raw or "").splitlines():
            s = line.strip()
            if s and not s.lower().startswith("file:"):
                desc = s
                break
    desc = desc.strip().strip('"').strip("'").strip()
    desc = re.sub(r"\s+", " ", desc)
    if "\n" in desc:
        desc = desc.splitlines()[0]
    return file_base, desc


def _valid_desc(desc: str) -> bool:
    return bool(desc) and MIN_DESC_CHARS <= len(desc) <= MAX_DESC_CHARS


# ------------------------------------------------------------------ public

def repair_description(chain_path: str, alias: str, stars, feedback_text: str = "",
                       goal_text: str = "", output_excerpt: str = "",
                       model: str = "") -> dict:
    """Record the event; on penalty, rewrite the (or the implicated) chain's
    description with the local model.  Returns an event dict; never raises."""
    event = {
        "ts": time.time(),
        "chain": str(chain_path),
        "alias": str(alias or ""),
        "stars": int(stars) if str(stars).strip().lstrip("-").isdigit() else stars,
        "penalty": penalty_weight(stars),
        "feedback": str(feedback_text or "")[:500],
        "goal": str(goal_text or "")[:300],
        "model": model or os.environ.get("LOOPER_DESC_MODEL") or DEFAULT_MODEL,
        "updated": False,
        "old": "",
        "new": "",
        "target": "",
        "reason": "",
    }
    try:
        text = _read_text(chain_path)
        if not text:
            event["reason"] = "chain file unreadable"
            _append_audit(event)
            return event
        cfg = _load_json(chain_path)
        old_desc = _read_chain_desc(text)
        event["old"] = old_desc

        if penalty_weight(stars) == 0:
            event["reason"] = "no penalty (rating 9-10)"
            _append_audit(event)
            return event
        if _repair_disabled():
            event["reason"] = "LOOPER_DESC_REPAIR=off"
            _append_audit(event)
            return event

        candidates = tree_context(chain_path)
        summary = _chain_summary(cfg)
        prompt = _build_prompt(chain_path, alias, old_desc, summary, stars,
                               feedback_text, goal_text, output_excerpt, candidates)
        with _REPAIR_LOCK:
            raw = _call_local_model(prompt, _SYSTEM, event["model"])
        file_base, new_desc = _parse_reply(raw)
        if not _valid_desc(new_desc):
            event["reason"] = f"rewrite rejected (len={len(new_desc)})"
            _append_audit(event)
            return event
        if new_desc == old_desc:
            event["reason"] = "rewrite identical to current description"
            _append_audit(event)
            return event

        # Target resolution: the model may rewrite a candidate instead of the
        # rated chain (the "should have been selected" case).  Unknown names
        # fall back to the rated chain.
        target_path = chain_path
        if file_base:
            fb = _basename(file_base)
            for c in candidates:
                if _basename(c["path"]) == fb:
                    target_path = c["path"]
                    break
        target_text = text if target_path == chain_path else _read_text(target_path)
        if not target_text:
            event["reason"] = "target chain unreadable"
            _append_audit(event)
            return event
        new_text = _replace_chain_desc(target_text, new_desc)
        json.loads(new_text)  # validate before writing
        tmp = target_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new_text)
        os.replace(tmp, target_path)

        event.update({
            "updated": True,
            "new": new_desc,
            "target": target_path,
            "reason": "description rewritten",
        })
        logger.info(
            "description_repair: %s -> rewritten (%d stars, penalty %d): %s",
            os.path.basename(target_path), event["stars"], event["penalty"],
            new_desc[:120],
        )
    except Exception as e:  # noqa: BLE001 — feedback must never crash the overlay
        event["reason"] = f"error: {e}"
        logger.warning("description_repair failed: %s", e)
    _append_audit(event)
    return event
