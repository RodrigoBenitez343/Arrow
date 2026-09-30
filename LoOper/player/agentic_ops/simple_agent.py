"""
simple_agent.py — System-chain-driven agent for LoOper.

The agent has no hardcoded behavior.  It requires a **system chain** to
function: a chain JSON file (collection="System") that defines the agent's
entire cognitive architecture — Input nodes receive the user query,
LLM nodes reason, Context nodes store thoughts, ChainImport nodes execute
tools via other chains, and the output is returned directly to the user.

Without a system chain the agent returns a clear error — no fallback, no
hardcoded intent classification, no automatic chain selection.  The user
designs the agent's mind in the chain editor.
"""

import logging
import os
import uuid
from typing import Any, Dict, List

from .system_chains import is_system_chain, read_chains

# Records propagate to the unified telemetry dispatch (see logging_setup.py):
# colorized console + logs/automation.log + per-session md log.
logger = logging.getLogger(__name__)


# ── Debug diagnostics ────────────────────────────────────────
_DEBUG = False


def _dbg(msg: str) -> None:
    if _DEBUG:
        import threading as _thr
        print(f"[D][{_thr.current_thread().name}] {msg}", flush=True)


# ── Router class ────────────────────────────────────────────────


class SimpleChainRouter:
    """Agent dispatcher that routes every query through a user-defined system chain.

    The system chain is the agent's brain — it receives the user query,
    reasons through its LLM nodes, executes tools via chain imports, and
    stores its reasoning in context nodes.  The agent's response *is* the
    system chain's output.

    Without a system chain, the agent cannot respond.
    """

    def __init__(self, model_path: str = "", chains_dir: str = ""):
        self._chains_dir = chains_dir
        self._chains: List[Dict[str, Any]] = []
        self._ask_user_callback = None  # Callable for asking user input mid-execution
        self._ask_user_v2_callback = None  # Rich ask (choices/attachments), optional
        self._output_display_callback = None  # Callable for displaying output node results mid-execution
        self._memory_only = False  # memory-chat mode: answer from recorded activity only
        self._system_chain_path = ""  # path to the designated system chain JSON
        self._system_chain_id = ""    # chain id (filename without .json) of the system chain
        self._refresh_chains()

    def _refresh_chains(self) -> None:
        """Scan the chains directory for available chain JSON files."""
        self._chains = read_chains(self._chains_dir)

    def list_system_chains(self) -> List[Dict[str, Any]]:
        """Every System chain on disk — one entry per selectable agent persona.

        Unlike a single fixed system chain, this surfaces all chains whose
        ``collection`` is "System" so the UI can offer them as coworkers.
        """
        self._refresh_chains()
        return [c for c in self._chains if is_system_chain(c)]

    def set_ask_user_callback(self, callback):
        """Set a callable that asks the user for input in the chat and returns the response.

        Used by Input nodes with a ``user_prompt`` during agent-mode chain execution.
        The callable receives a question string and should return the user's typed response.
        """
        self._ask_user_callback = callback

    def set_ask_user_v2_callback(self, callback):
        """Set a rich ask-user callable (v2 protocol: dict request -> dict response).

        When present, Input nodes prefer it over the legacy text callback:
        yes/no and choice questions render as buttons on surfaces that support
        them, and text inputs may carry file attachments.
        """
        self._ask_user_v2_callback = callback

    def set_output_display_callback(self, callback):
        """Set a callable that displays output node results in the chat overlay.

        Unlike ask_user_callback, this does NOT block — it posts the output
        text and returns immediately so the chain continues executing.
        Receives (label, content) strings.
        """
        self._output_display_callback = callback

    def set_memory_only(self, enabled: bool) -> None:
        """Toggle memory-chat mode: requests answer from recorded activity only.

        With it on, ``handle_request`` never dispatches chains — it answers
        from the linear activity timeline (player/agentic_ops/run_memory.py),
        strictly from recorded events with their timestamps.
        """
        self._memory_only = bool(enabled)
        logger.info(
            "SimpleChainRouter: memory-only chat %s", "ON" if enabled else "OFF",
        )

    def set_system_chain(self, chain_path: str) -> bool:
        """Designate a chain as the agent's cognitive architecture (System Chain).

        When a system chain is set, handle_request() routes all queries through
        this chain.  The chain must contain Input nodes (to receive the user query),
        LLM nodes (for reasoning), and Context nodes (clear_on_finish=True) for
        the agent to store and retrieve its thoughts.

        Returns True if the chain exists and was set successfully.
        """
        if not chain_path or not os.path.exists(chain_path):
            logger.warning(
                "SimpleChainRouter: system chain not found at %s", chain_path
            )
            self._system_chain_path = ""
            self._system_chain_id = ""
            return False
        self._system_chain_path = os.path.normpath(chain_path)
        self._system_chain_id = os.path.basename(chain_path).replace(".json", "")
        logger.info(
            "SimpleChainRouter: system chain set to '%s' (%s)",
            self._system_chain_id, self._system_chain_path,
        )
        return True

    def handle_request(self, query: str, stop_flag=None) -> str:
        """Route a user query through the currently selected system chain.

        The system chain *is* the agent — it receives the query, reasons,
        executes tools, and produces the response.  Without one, the agent
        returns an error.

        The user's explicit selection (set via ``set_system_chain``) is kept
        across requests; it is dropped only when the chain file disappears.
        When nothing is selected yet, the first System chain on disk becomes
        the default, so multiple coworkers coexist without overriding a choice.
        """
        # Memory-chat mode: answer strictly from the recorded activity
        # timeline (player/agentic_ops/run_memory.py) — no chain execution,
        # no system chain required.
        if self._memory_only:
            if stop_flag and stop_flag():
                return "Cancelled."
            try:
                from player.agentic_ops import run_memory
                return run_memory.answer_question(query, stop_flag=stop_flag)
            except Exception as e:
                logger.exception("SimpleChainRouter: memory chat failed")
                return f"Memory chat error: {e}"

        # Re-scan chains to pick up newly added/removed System chains.
        self._refresh_chains()

        # Drop a selection whose file vanished (moved/deleted).
        if self._system_chain_path and not os.path.exists(self._system_chain_path):
            self._system_chain_path = ""
            self._system_chain_id = ""

        # No selection yet → default to the flagged root chain (is_default),
        # else the first System chain on disk.
        if not self._system_chain_path:
            system_chains = [c for c in self._chains if is_system_chain(c)]
            if system_chains:
                _default = next(
                    (c for c in system_chains if c.get("is_default")),
                    system_chains[0],
                )
                self.set_system_chain(_default["path"])

        if not self._system_chain_path or not os.path.exists(self._system_chain_path):
            return (
                "No system chain loaded. "
                "Create a chain with collection='System' in the chain editor "
                "to define the agent's behavior."
            )

        if stop_flag and stop_flag():
            return "Cancelled."

        return self._run_system_chain(query, stop_flag)

    def _run_system_chain(self, query: str, stop_flag=None) -> str:
        """Execute the system chain and return its output directly."""
        req_id = uuid.uuid4().hex[:8]
        logger.info(
            "[AGENT-SYS] [%s] ===== System chain execution =====", req_id
        )
        logger.info("[AGENT-SYS] [%s] Chain: '%s'", req_id, self._system_chain_id)
        logger.info("[AGENT-SYS] [%s] Query: %s", req_id, query[:200])

        if stop_flag and stop_flag():
            logger.info("[AGENT-SYS] [%s] Stop requested before execution", req_id)
            return "Cancelled."

        # Execute the system chain — the chain executor handles its own
        # infrastructure (llama.cpp server, API gateway, etc.)
        try:
            from player.agentic_ops.chain_executor import ChainExecutor
            executor = ChainExecutor()
            result = executor.run_chain(
                self._system_chain_path,
                agent_query=query,
                llamacpp_server_url=None,
                ask_user_callback=self._ask_user_callback,
                ask_user_v2_callback=self._ask_user_v2_callback,
                on_output_ready=self._output_display_callback,
                stop_flag=stop_flag,
            )
            logger.info(
                "[AGENT-SYS] [%s] System chain finished: %s",
                req_id, (result[:200] if result else "None"),
            )
        except Exception as e:
            logger.exception("[AGENT-SYS] [%s] System chain execution failed: %s", req_id, e)
            try:
                from player.agentic_ops import run_memory
                run_memory.record_chat(query, f"error: {e}")
            except Exception:
                pass
            return f"Agent error: {e}"

        # Linear activity timeline: pair the ask with its answer (the chain
        # run recorded its own events; this is the chat-turn record).
        try:
            from player.agentic_ops import run_memory
            run_memory.record_chat(query, result or "")
        except Exception:
            pass

        # Return the execution result directly — this contains context node
        # content from the system chain's own LLM nodes, not a separate
        # SmolLM3 chat response.
        return result or "System chain completed."

    def shutdown(self) -> None:
        pass

    def __del__(self) -> None:
        self.shutdown()
