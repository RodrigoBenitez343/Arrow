"""
DecisionEngine — Given context, decides the next action for the visual agent.

Uses a small LLM (LFM text-only, or any provided caller) to reason about
the current screen state and choose which chain to execute next.

The prompt is deliberately SIMPLE — small models (450M) cannot reliably
output structured JSON, so we use a one-line format:

  CHAIN:chain_name     — execute a chain by its id
  TYPE:text to type    — type this text
  WAIT:seconds         — wait N seconds
  DONE                 — task complete
  FAILED:reason        — cannot proceed
  ASK_USER:question    — ask the user for guidance when unsure
"""

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional

from .chain_registry import ChainRegistry

logger = logging.getLogger(__name__)


@dataclass
class Decision:
    """Structured decision output from the engine."""

    action: str  # "execute_chain" | "type_text" | "wait" | "done" | "failed" | "ask_user"
    chain_id: str = ""
    text: str = ""
    duration: float = 1.0
    reason: str = ""
    raw: str = ""


# ── Simple prompts for small models ──────────────────────────────

DECISION_SYSTEM_PROMPT = """You are a desktop automation agent.

Available chains:
{chain_tools}

Rules:
- Use CHAIN:name to run a chain for opening apps, clicking, or navigating.
- Use TYPE:text to type text at the current cursor.
- Use WAIT:seconds to wait for loading.
- Use DONE when the user's request is fulfilled.
- Use FAILED:reason if you are stuck and cannot proceed.
- Use ASK_USER:question if you are unsure what to do and need guidance from the user.

Respond with EXACTLY ONE LINE. No explanations. No JSON. No extra text."""

DECISION_USER_PROMPT = """User: {user_request}

Current screen: {screen_state}

History:
{history_summary}

What next?"""


def _format_history(history: List[Dict[str, Any]], max_items: int = 5) -> str:
    """Format the recent execution history for the prompt."""
    if not history:
        return "No steps executed yet."
    lines = []
    recent = history[-max_items:]
    for i, h in enumerate(recent):
        action = h.get("action", "?")
        detail = h.get("detail", "")
        result = h.get("result", "")
        line = f"  {i+1}. {action}"
        if detail:
            line += f" ({detail})"
        if result:
            line += f" -> {str(result)[:60]}"
        lines.append(line)
    return "\n".join(lines)


# ── main class ──────────────────────────────────────────────────


class DecisionEngine:
    """Decides the next action based on context.

    Uses a provided ``call_llm`` function (a callable that accepts a system
    message and user prompt and returns a text response).
    """

    def __init__(
        self,
        call_llm: Callable[[str, str], str],
        chain_registry: ChainRegistry,
        log_fn: Optional[Callable] = None,
        system_prompt: str = "",
    ):
        self._call_llm = call_llm
        self._chain_registry = chain_registry
        self._log = log_fn or (lambda *a, **kw: None)
        self._system_prompt = system_prompt or DECISION_SYSTEM_PROMPT

    def decide(
        self,
        user_request: str,
        screen_state: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> Decision:
        """Given context, decide the next action."""
        # Build the chain_tools line for the system prompt
        tool_ids = [c.get("id", "?") for c in self._chain_registry.list_chains()]
        chain_tools = ", ".join(tool_ids) if tool_ids else "(none)"

        system_prompt = self._system_prompt.format(chain_tools=chain_tools)
        history_summary = _format_history(history or [])

        user_prompt = DECISION_USER_PROMPT.format(
            user_request=user_request,
            screen_state=screen_state[:1500],
            history_summary=history_summary,
        )

        try:
            raw = self._call_llm(system_prompt, user_prompt)
        except Exception as e:
            logger.error("Decision LLM call failed: %s", e)
            return Decision(
                action="failed",
                reason=f"LLM call error: {e}",
                raw=str(e),
            )

        if not raw or not raw.strip():
            return Decision(
                action="failed",
                reason="Empty LLM response",
                raw="",
            )

        parsed = self._parse_response(raw)
        self._log("decision", {"decision": asdict(parsed), "raw": raw[:500]})
        return parsed

    # ── robust multi-strategy parser ───────────────────────────

    def _parse_response(self, raw: str) -> Decision:
        """Parse LLM response into a Decision using multiple strategies."""

        cleaned = raw.strip()

        # ── Strategy 1: Simple one-line format (CHAIN:xxx) ──
        decision = self._parse_simple_format(cleaned)
        if decision:
            return decision

        # ── Strategy 2: JSON ──
        decision = self._parse_json_format(cleaned)
        if decision:
            return decision

        # ── Strategy 3: Natural language — look for chain names in the output ──
        decision = self._parse_natural_language(cleaned)
        if decision:
            return decision

        # ── Strategy 4: Keyword fallback ──
        lower = cleaned.lower()
        if re.search(r"\bdone\b|\bcomplete\b|\bfinished\b", lower):
            return Decision(action="done", reason="Keyword: done", raw=raw)
        if re.search(r"\bfailed\b|\bstuck\b|\berror\b|\bcannot\b", lower):
            return Decision(action="failed", reason="Keyword: failed/stuck", raw=raw)
        if re.search(r"\bwait\b|\bdelay\b|\bpause\b", lower):
            return Decision(action="wait", duration=2.0, reason="Keyword: wait", raw=raw)
        # Uncertainty keywords → ask user
        if re.search(
            r"\bunsure\b|\bnot sure\b|\bwhat should|\bask user\b|\bneed help\b|\bdon't know\b|\bdo not know\b",
            lower,
        ):
            return Decision(
                action="ask_user",
                text="What should I do next?",
                reason="Keyword: unsure",
                raw=raw,
            )

        return Decision(action="failed", reason="Could not parse decision", raw=raw)

    # ── parser strategies ─────────────────────────────────────

    def _parse_simple_format(self, cleaned: str) -> Optional[Decision]:
        """Parse CHAIN:xxx / TYPE:xxx / WAIT:N / DONE / FAILED:xxx format."""
        # Remove code fences
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            cleaned = cleaned.strip("`").strip()

        first_line = cleaned.split("\n")[0].strip().upper()

        # CHAIN:xxx
        if first_line.startswith("CHAIN:"):
            chain_id = first_line[6:].strip().lower()
            # Validate against registry
            if chain_id and self._chain_registry.find_by_id(chain_id):
                return Decision(
                    action="execute_chain",
                    chain_id=chain_id,
                    reason=f"Simple format: {chain_id}",
                    raw=cleaned,
                )
            # Chain not found in registry — try to match substring
            if chain_id:
                for c in self._chain_registry.list_chains():
                    cid = c.get("id", "")
                    if chain_id in cid or cid in chain_id:
                        return Decision(
                            action="execute_chain",
                            chain_id=cid,
                            reason=f"Fuzzy match: {chain_id}->{cid}",
                            raw=cleaned,
                        )
            return Decision(action="failed", reason=f"Unknown chain: {chain_id}", raw=cleaned)

        # TYPE:xxx
        if first_line.startswith("TYPE:"):
            text = cleaned.split("\n")[0][5:].strip()
            return Decision(
                action="type_text", text=text, reason="Simple format: type", raw=cleaned
            )

        # WAIT:N
        if first_line.startswith("WAIT:"):
            try:
                duration = float(first_line[5:].strip())
            except ValueError:
                duration = 2.0
            return Decision(
                action="wait", duration=duration, reason="Simple format: wait", raw=cleaned
            )

        # DONE
        if first_line == "DONE" or first_line.startswith("DONE:"):
            reason = first_line[5:].strip() if ":" in first_line else "Task complete"
            return Decision(action="done", reason=reason, raw=cleaned)

        # FAILED:xxx
        if first_line.startswith("FAILED:"):
            reason = first_line[7:].strip()
            return Decision(action="failed", reason=reason, raw=cleaned)

        # ASK_USER:question
        if first_line.startswith("ASK_USER:"):
            question = cleaned.split("\n")[0][9:].strip()
            return Decision(
                action="ask_user", text=question, reason="Simple format: ask user", raw=cleaned
            )

        return None

    def _parse_json_format(self, cleaned: str) -> Optional[Decision]:
        """Parse JSON format as secondary fallback."""
        # Strip code fences
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            cleaned = cleaned.strip("`").strip()

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        json_str = cleaned[start : end + 1]
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return None

        action = str(data.get("action", "failed")).strip()
        chain_id = str(data.get("chain_id", "")).strip()

        # Validate chain_id against registry
        if action == "execute_chain":
            if not chain_id:
                return None  # refused — no chain_id in JSON
            # Try direct match
            if self._chain_registry.find_by_id(chain_id):
                return Decision(
                    action="execute_chain",
                    chain_id=chain_id,
                    reason="JSON format",
                    raw=cleaned,
                )
            # Try fuzzy match
            for c in self._chain_registry.list_chains():
                cid = c.get("id", "")
                if chain_id in cid or cid in chain_id:
                    return Decision(
                        action="execute_chain",
                        chain_id=cid,
                        reason=f"JSON fuzzy: {chain_id}->{cid}",
                        raw=cleaned,
                    )
            return Decision(action="failed", reason=f"Unknown chain in JSON: {chain_id}", raw=cleaned)

        return Decision(
            action=action,
            chain_id=chain_id,
            text=str(data.get("text", "")).strip(),
            duration=float(data.get("duration", 1.0)),
            reason=str(data.get("reason", "")).strip(),
            raw=cleaned,
        )

    def _parse_natural_language(self, cleaned: str) -> Optional[Decision]:
        """Last resort: look for known chain names anywhere in the output."""
        lower = cleaned.lower()
        # Check each chain name in reverse priority order
        for c in self._chain_registry.list_chains():
            cid = c.get("id", "").lower()
            cname = c.get("name", "").lower()
            if cid and (cid in lower or cname in lower):
                return Decision(
                    action="execute_chain",
                    chain_id=c.get("id", ""),
                    reason=f"Natural language match: {cid}",
                    raw=cleaned,
                )
        return None
