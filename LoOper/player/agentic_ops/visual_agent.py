"""
VisualAgent — Main agent orchestrator.

Observe -> Decide -> Act loop that chains together:
  - ScreenObserver (LFM vision)
  - DecisionEngine (LLM reasoning)
  - ChainRunner (execute deterministic chains)
  - TypingAssistant (keyboard input)
  - LearningStore (persistent memory)
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .action_executor import ActionExecutor
from .chain_builder import ChainBuilder
from .chain_registry import ChainRegistry
from .chain_runner import ChainRunner
from .decision_engine import Decision, DecisionEngine
from .learning_store import LearningStore
from .screen_observer import ScreenObserver

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """Mutable state tracked across the agent loop."""
    user_request: str = ""
    screen_state: str = ""
    iteration: int = 0
    max_iterations: int = 10
    history: List[Dict[str, Any]] = field(default_factory=list)
    consecutive_failures: int = 0
    done: bool = False
    final_response: str = ""
    # Interactive / chain-learning state
    state: str = "idle"  # idle | waiting_guidance | waiting_confirmation
    waiting_for_input: bool = False
    pending_actions: List[Dict[str, Any]] = field(default_factory=list)
    pending_chain_id: str = ""
    pending_chain_desc: str = ""


# ── default LLM caller: uses LFM server or Ollama ──────────────


def _make_default_llm_caller(
    lfm_server_url: str = "http://127.0.0.1:8082",
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_model: str = "llama3.2:latest",
) -> Callable[[str, str], str]:
    """Create a call_llm function that tries LFM text endpoint first, then Ollama."""

    import requests as _r

    def _call(system: str, user: str) -> str:
        prompt = f"{system}\n\n{user}" if system else user
        # Attempt 1: LFM server text completion
        try:
            resp = _r.post(
                f"{lfm_server_url}/completion",
                json={
                    "prompt": prompt,
                    "n_predict": 512,
                    "temperature": 0.2,
                    "stream": False,
                },
                timeout=60,
            )
            if resp.status_code == 200:
                content = resp.json().get("content", "").strip()
                if content:
                    return content
        except Exception:
            pass

        # Attempt 2: Ollama
        try:
            resp = _r.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 512, "temperature": 0.2},
                },
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json().get("response", "").strip()
        except Exception:
            pass

        return ""

    return _call


# ── main loop ───────────────────────────────────────────────────


class VisualAgent:
    """Observe->Decide->Act loop.

    Typical usage::

        agent = VisualAgent(chain_runner, screen_observer, decision_engine, ...)
        result = agent.run("Go to LinkedIn")
    """

    def __init__(
        self,
        chain_runner: ChainRunner,
        screen_observer: Optional[ScreenObserver],
        decision_engine: DecisionEngine,
        chain_registry: Optional[ChainRegistry] = None,
        typing_assistant: Optional[Any] = None,
        learning_store: Optional[LearningStore] = None,
        log_event: Optional[Callable] = None,
        add_chat_bubble: Optional[Callable] = None,
        scroll_to_bottom: Optional[Callable] = None,
        set_processing_label: Optional[Callable] = None,
        translate: Optional[Callable] = None,
        accent_color: str = "",
        max_iterations: int = 10,
        action_executor: Optional[ActionExecutor] = None,
        chain_builder: Optional[ChainBuilder] = None,
        on_ask_user: Optional[Callable[[str], None]] = None,
    ):
        self._chain_runner = chain_runner
        self._chain_registry = chain_registry
        self._screen_observer = screen_observer
        self._decision_engine = decision_engine
        self._typing_assistant = typing_assistant
        self._learning_store = learning_store
        self._log_event = log_event or (lambda *a, **kw: None)
        self._add_chat_bubble = add_chat_bubble
        self._scroll_to_bottom = scroll_to_bottom
        self._set_processing_label = set_processing_label
        self._translate = translate or (lambda s: s)
        self._accent_color = accent_color
        self._max_iterations = max_iterations
        self._action_executor = action_executor
        self._chain_builder = chain_builder
        self._on_ask_user = on_ask_user
        self._ctx = AgentContext(max_iterations=max_iterations)

    def run(self, user_text: str) -> str:
        """Execute the agent loop for a user request.

        Returns the final response text.
        """
        self._ctx = AgentContext(
            user_request=user_text,
            max_iterations=self._max_iterations,
        )

        self._log_event("agent_start", {"request": user_text})

        # Show the user we're starting
        if self._add_chat_bubble:
            label = self._translate("Processing request...")
            self._add_chat_bubble(label, False, self._accent_color)
            if self._scroll_to_bottom:
                self._scroll_to_bottom()

        for iteration in range(1, self._max_iterations + 1):
            self._ctx.iteration = iteration

            if self._set_processing_label:
                label = self._translate("Step {}/{}...").format(
                    iteration, self._max_iterations
                )
                self._set_processing_label(label)

            # ── OBSERVE ────────────────────────────────────────
            if self._set_processing_label:
                self._set_processing_label(
                    self._translate("Analyzing screen...")
                )
            self._ctx.screen_state = self._observe()
            self._log_event(
                "agent_observe",
                {"iteration": iteration, "screen": self._ctx.screen_state[:300]},
            )

            # ── transparent: show what vision sees ─────────────
            if self._add_chat_bubble and self._ctx.screen_state:
                screen_short = self._ctx.screen_state[:200]
                # Only show if it's not an error message
                if not screen_short.startswith("["):
                    self._add_chat_bubble(
                        f"👁️ {screen_short}",
                        False,
                        self._accent_color,
                    )
                    if self._scroll_to_bottom:
                        self._scroll_to_bottom()

            # ── DECIDE ─────────────────────────────────────────
            if self._set_processing_label:
                self._set_processing_label(
                    self._translate("Deciding next action...")
                )
            decision = self._decision_engine.decide(
                user_request=user_text,
                screen_state=self._ctx.screen_state,
                history=self._ctx.history,
            )
            self._log_event(
                "agent_decide",
                {"iteration": iteration, "decision": str(decision)},
            )

            # ── transparent: show what reasoning decided ───────
            if self._add_chat_bubble:
                action_desc = self._format_decision_description(decision)
                self._add_chat_bubble(
                    f"🧠 {action_desc}",
                    False,
                    self._accent_color,
                )
                if self._scroll_to_bottom:
                    self._scroll_to_bottom()

            # ── ACT ────────────────────────────────────────────
            self._act(decision)

            # ── CHECK ──────────────────────────────────────────
            if self._ctx.done:
                break

            if self._ctx.consecutive_failures >= 3:
                self._log_event(
                    "agent_too_many_failures",
                    {"iteration": iteration, "failures": self._ctx.consecutive_failures},
                )
                self._ctx.final_response = self._translate(
                    "Too many failures. Stopping."
                )
                self._ctx.done = True
                break

        # ── FINALIZE ───────────────────────────────────────────
        if not self._ctx.waiting_for_input and not self._ctx.final_response:
            self._ctx.final_response = self._translate("Task completed.")

        self._log_event(
            "agent_end",
            {
                "iterations_used": self._ctx.iteration,
                "response": self._ctx.final_response[:500],
            },
        )

        # Record a learning if the task succeeded
        if self._ctx.done and self._learning_store and self._ctx.history:
            try:
                self._learning_store.record_experience(
                    issue=f"Task: {user_text}",
                    cause="Completed via agent loop",
                    fix="Steps: " + "; ".join(
                        h.get("action", "") + ":" + h.get("detail", "")[:50]
                        for h in self._ctx.history[-3:]
                    ),
                    tags=["agent", "success"],
                )
            except Exception:
                pass

        return self._ctx.final_response

    # ── internal helpers ────────────────────────────────────────

    def _format_decision_description(self, decision: Decision) -> str:
        """Format a decision into a human-readable chat message."""
        desc = f"Decision: {decision.action}"
        if decision.action == "execute_chain":
            cid = decision.chain_id
            # Try to get the chain description from registry
            chain_desc = ""
            if self._chain_registry:
                info = self._chain_registry.find_by_id(cid)
                if info and info.get("description"):
                    chain_desc = f" ({info['description']})"
            desc = f"Run chain '{cid}'{chain_desc}"
        elif decision.action == "type_text":
            desc = f"Type: {decision.text[:100]}"
        elif decision.action == "wait":
            desc = f"Wait {decision.duration}s"
        elif decision.action == "done":
            desc = decision.reason or "Task complete"
        elif decision.action == "failed":
            desc = decision.reason or "Cannot proceed"
        return desc

    def _observe(self) -> str:
        """Capture and describe screen state. Returns a text description.

        The GUI is minimized before the screenshot and restored after
        the vision model responds (handled internally by ScreenObserver).
        """
        if self._screen_observer is None:
            return "(No vision model available)"
        try:
            return self._screen_observer.describe()
        except Exception as e:
            logger.warning("Screen observation failed: %s", e)
            return f"(Observation error: {e})"

    def _act(self, decision: Decision) -> None:
        """Execute a single decision and record the result."""
        action = decision.action
        entry: Dict[str, Any] = {
            "action": action,
            "detail": "",
            "result": "",
            "success": False,
        }

        if action == "execute_chain":
            entry["detail"] = f"chain:{decision.chain_id}"
            success = self._execute_chain(decision.chain_id)
            entry["success"] = success
            entry["result"] = (
                "ok" if success else "failed"
            )
            # Show result bubble
            if self._add_chat_bubble:
                icon = "✅" if success else "❌"
                label = f"{icon} Chain '{decision.chain_id}' {'succeeded' if success else 'failed'}"
                self._add_chat_bubble(label, False, self._accent_color)
                if self._scroll_to_bottom:
                    self._scroll_to_bottom()
            if success:
                self._ctx.consecutive_failures = 0
            else:
                self._ctx.consecutive_failures += 1
                self._record_failure_learning(decision)

        elif action == "type_text":
            entry["detail"] = f"type:{decision.text[:60]}"
            success = self._type_text(decision.text)
            entry["success"] = success
            entry["result"] = "ok" if success else "failed"
            # Show result bubble
            if self._add_chat_bubble:
                icon = "✅" if success else "❌"
                label = f"{icon} {'Typed' if success else 'Failed to type'}: {decision.text[:100]}"
                self._add_chat_bubble(label, False, self._accent_color)
                if self._scroll_to_bottom:
                    self._scroll_to_bottom()
            if success:
                self._ctx.consecutive_failures = 0
            else:
                self._ctx.consecutive_failures += 1

        elif action == "wait":
            entry["detail"] = f"wait:{decision.duration}s"
            self._log_event("agent_wait", {"duration": decision.duration})
            try:
                time.sleep(decision.duration)
                entry["success"] = True
            except Exception:
                entry["success"] = False

        elif action == "done":
            entry["detail"] = decision.reason or "task done"
            entry["success"] = True
            self._ctx.done = True
            self._ctx.final_response = decision.reason or self._translate(
                "Task completed."
            )

        elif action == "ask_user":
            entry["detail"] = "ask_user"
            entry["success"] = False
            self._ctx.consecutive_failures += 1
            self.request_guidance(decision.reason or decision.text or "I need your help.")
            # Break the loop — ChatController will route next user message
            self._ctx.done = True
            self._ctx.final_response = ""  # Don't show an extra bubble
            return  # Skip history append — we'll continue later

        elif action == "failed":
            entry["detail"] = decision.reason or "agent stuck"
            entry["success"] = False
            self._ctx.consecutive_failures += 1
            self._ctx.done = True
            self._ctx.final_response = self._translate(
                "Stuck: {}"
            ).format(decision.reason or "Could not proceed")

        self._ctx.history.append(entry)

    def _execute_chain(self, chain_id: str) -> bool:
        """Run a single chain file by id. Returns success."""
        if not chain_id or not chain_id.strip():
            logger.warning("Empty chain_id — skipping execution")
            return False
        chain_id = chain_id.strip()
        try:
            # Resolve chain_id to path via the chain registry
            path = None
            if self._chain_registry:
                chain_info = self._chain_registry.find_by_id(chain_id)
                if chain_info:
                    path = chain_info.get("path")

            if not path or not os.path.exists(path):
                logger.warning("Chain not found or path invalid: %s", chain_id)
                return False

            ok = self._chain_runner.run_chain_file(path)
        except Exception as e:
            logger.error("Chain execution error: %s", e)
            ok = False
        return ok

    def _type_text(self, text: str) -> bool:
        """Type text using TypingAssistant."""
        if self._typing_assistant is None:
            logger.warning("No typing assistant available")
            return False
        try:
            return self._typing_assistant.perform_typing(text)
        except Exception as e:
            logger.error("Typing error: %s", e)
            return False

    def _record_failure_learning(self, decision: Decision) -> None:
        """Record a learning from a chain execution failure."""
        if self._learning_store is None:
            return
        try:
            self._learning_store.record_experience(
                issue=f"Agent: chain {decision.chain_id} failed while handling '{self._ctx.user_request[:100]}'",
                cause=f"Decision: {decision.reason}",
                fix=f"Screen state was: {self._ctx.screen_state[:200]}",
                tags=["agent", "failure", decision.chain_id],
            )
        except Exception:
            pass

    # ── interactive guidance methods ───────────────────────────

    def request_guidance(self, reason: str = "") -> None:
        """Ask the user for guidance when the agent is stuck or unsure."""
        self._ctx.state = "waiting_guidance"
        self._ctx.waiting_for_input = True
        question = (
            f"I'm not sure what to do next. {reason}\n\n"
            "What should I do? (e.g. 'click on the search bar' or 'type example.com and press enter')"
        )
        if self._on_ask_user:
            self._on_ask_user(question)
        else:
            logger.warning("No on_ask_user callback — agent stuck: %s", reason)
        self._log_event("agent_ask_user", {"reason": reason})

    def process_guidance(self, instructions: str) -> str:
        """Process user guidance: parse instructions into actions, execute, record.

        Args:
            instructions: Natural language instructions from the user.

        Returns:
            Result message.
        """
        self._ctx.state = "idle"
        self._ctx.waiting_for_input = False

        if self._add_chat_bubble:
            self._add_chat_bubble(
                self._translate("Following your instructions..."),
                False,
                self._accent_color,
            )
            if self._scroll_to_bottom:
                self._scroll_to_bottom()

        if self._action_executor is None:
            return "No action executor available — cannot perform actions."

        # Clear previous recordings
        self._action_executor.clear_recorded_actions()

        # Parse the user's instructions into structured steps using the reasoning model
        steps = self._parse_guidance_steps(instructions)

        if not steps:
            return "Could not understand the instructions. Please try again with specific steps."

        # Execute each step and record
        success_count = 0
        for step in steps:
            ok = self._execute_guidance_step(step)
            if ok:
                success_count += 1

        self._ctx.pending_actions = self._action_executor.get_recorded_actions()

        msg = (
            f"Executed {success_count}/{len(steps)} steps."
            if success_count == len(steps)
            else f"Completed {success_count}/{len(steps)} steps (some may have failed)."
        )
        if self._add_chat_bubble:
            self._add_chat_bubble(msg, False, self._accent_color)
            if self._scroll_to_bottom:
                self._scroll_to_bottom()

        self.request_confirmation()
        return msg

    def request_confirmation(self) -> None:
        """Ask the user if the guided actions worked correctly."""
        self._ctx.state = "waiting_confirmation"
        self._ctx.waiting_for_input = True
        question = (
            "Did that work correctly?\n\n"
            "Reply YES to save these steps as a new chain, "
            "or NO to discard and try again."
        )
        if self._on_ask_user:
            self._on_ask_user(question)
        self._log_event("agent_ask_confirmation", {})

    def process_confirmation(self, response: str, user_request: str) -> str:
        """Handle the user's confirmation response.

        Args:
            response: User's "yes" or "no" response.
            user_request: Original user request (used for chain name/description).

        Returns:
            Result message.
        """
        self._ctx.state = "idle"
        self._ctx.waiting_for_input = False

        lower = response.strip().lower()
        if lower.startswith("y") or "yes" in lower or "save" in lower:
            return self._save_chain_from_pending(user_request)
        else:
            if self._action_executor:
                self._action_executor.clear_recorded_actions()
            self._ctx.pending_actions = []
            self._ctx.pending_chain_id = ""
            msg = "Discarded. You can try again with different instructions."
            if self._add_chat_bubble:
                self._add_chat_bubble(msg, False)
                if self._scroll_to_bottom:
                    self._scroll_to_bottom()
            return msg

    def _save_chain_from_pending(self, user_request: str) -> str:
        """Save recorded actions as a new chain file."""
        if not self._ctx.pending_actions:
            msg = "No actions recorded — nothing to save."
            if self._add_chat_bubble:
                self._add_chat_bubble(msg, False)
            return msg

        if self._chain_builder is None:
            msg = "No chain builder available — cannot save chain."
            if self._add_chat_bubble:
                self._add_chat_bubble(msg, False)
            return msg

        # Generate chain ID from user request
        import re as _re
        chain_id = _re.sub(r"[^a-z0-9_]+", "_", user_request.lower().strip())[:40]
        chain_id = chain_id.strip("_")
        if not chain_id:
            chain_id = f"guided_{int(time.time())}"

        chain_path = self._chain_builder.build_chain(
            chain_id=chain_id,
            description=f"Guided chain created from: {user_request[:100]}",
            actions=self._ctx.pending_actions,
        )

        if chain_path:
            if self._action_executor:
                self._action_executor.clear_recorded_actions()
            self._ctx.pending_actions = []
            msg = f"Saved new chain '{chain_id}'! It will be available for future requests."
            self._log_event("agent_saved_chain", {"chain_id": chain_id, "path": chain_path})
        else:
            msg = "Failed to save chain. Check the logs for details."

        if self._add_chat_bubble:
            self._add_chat_bubble(msg, False, self._accent_color)
            if self._scroll_to_bottom:
                self._scroll_to_bottom()
        return msg

    def _parse_guidance_steps(self, instructions: str) -> List[Dict[str, Any]]:
        """Parse user instructions into structured action steps using the reasoning model."""
        # Use the DecisionEngine's LLM caller to parse instructions
        prompt = (
            "Convert these user instructions into a list of action steps. "
            'Each step must be one of: click x y, type "text", press key, wait N\n\n'
            f"Instructions: {instructions}\n\n"
            "Output one step per line, starting with the action keyword."
        )
        try:
            resp = self._decision_engine._call_llm(
                "You are an action planner. Output ONLY action lines, no explanations.",
                prompt,
            )
        except Exception:
            resp = ""

        if not resp or not resp.strip():
            # Fallback: treat entire instruction as a type_text action
            return [{"action": "type", "text": instructions}]

        steps = []
        for line in resp.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            lower = line.lower()
            if lower.startswith("click"):
                # click x y — use current screen coords from vision or defaults
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        x = int(parts[1])
                        y = int(parts[2])
                        steps.append({"action": "click", "x": x, "y": y})
                    except ValueError:
                        steps.append({"action": "click", "x": 100, "y": 100})
                else:
                    steps.append({"action": "click", "x": 100, "y": 100, "fallback": line})
            elif lower.startswith("type"):
                # type "text" or type text
                text = line[4:].strip().strip('"').strip("'")
                if text:
                    steps.append({"action": "type", "text": text})
            elif lower.startswith("press"):
                parts = line.split()
                key = parts[-1].lower() if len(parts) > 1 else "enter"
                steps.append({"action": "press", "key": key})
            elif lower.startswith("wait"):
                parts = line.split()
                try:
                    duration = float(parts[-1])
                except (ValueError, IndexError):
                    duration = 1.0
                steps.append({"action": "wait", "duration": duration})
            else:
                # Unknown format — treat as description
                steps.append({"action": "type", "text": line})

        return steps if steps else [{"action": "type", "text": instructions}]

    def _execute_guidance_step(self, step: Dict[str, Any]) -> bool:
        """Execute a single parsed guidance step via ActionExecutor."""
        action = step.get("action", "")
        try:
            if action == "click":
                return self._action_executor.click(step.get("x", 100), step.get("y", 100))
            elif action == "type":
                return self._action_executor.type_text(step.get("text", ""))
            elif action == "press":
                return self._action_executor.press_key(step.get("key", "enter"))
            elif action == "wait":
                return self._action_executor.wait(step.get("duration", 1.0))
            else:
                logger.warning("Unknown guidance step: %s", action)
                return False
        except Exception as e:
            logger.error("Guidance step failed: %s", e)
            return False

