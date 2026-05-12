"""
ChatController — Simplified entry point for the visual agent loop.

Creates and wires together:
  - ChainRegistry   (scans chains/ folder)
  - ChainRunner     (executes chain JSON files)
  - ScreenObserver  (LFM vision via llama.cpp)
  - DecisionEngine  (LLM-based action selection)
  - VisualAgent     (the observe-decide-act loop)
"""

import logging
import os
import threading
import time
from typing import Any, Callable, Optional

import requests as _rq

from .action_executor import ActionExecutor
from .chain_builder import ChainBuilder
from .chain_registry import ChainRegistry
from .chain_runner import ChainRunner
from .chat_logger import ChatLogger
from .decision_engine import DecisionEngine
from .learning_store import LearningStore
from .screen_observer import ScreenObserver
from .typing_ops import TypingAssistant
from .visual_agent import VisualAgent

logger = logging.getLogger(__name__)


# ── helper: detect LFM model paths ──────────────────────────────


def _find_lfm_model(base_dir: str) -> tuple:
    """Scan AI/models/ for the first LFM GGUF + mmproj pair.

    Returns (model_path, mmproj_path) or ("", "").
    """
    models_dir = os.path.join(base_dir, "AI", "models")
    if not os.path.isdir(models_dir):
        return ("", "")
    for root, _dirs, files in os.walk(models_dir):
        model = ""
        mmproj = ""
        for f in files:
            if f.endswith(".gguf"):
                if f.startswith("mmproj-"):
                    if not mmproj:
                        mmproj = os.path.join(root, f)
                else:
                    if not model:
                        model = os.path.join(root, f)
        if model:
            return (model, mmproj)
    return ("", "")


# ── helper: build LLM caller for decisions ──────────────────────


def _build_decision_llm_caller(
    ollama_url: str = "http://127.0.0.1:11434",
    ollama_model: str = "granite4:3b",
) -> Callable[[str, str], str]:
    """Create a call_llm function for the DecisionEngine.

    Uses a text-only reasoning model via Ollama (granite4:3b by default).
    The vision model (LFM) is ONLY used by ScreenObserver for describing
    screenshots — it should NOT be used for structured decision-making.
    """

    def _call(system_prompt: str, user_prompt: str) -> str:
        full = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt

        # Primary: reasoning model via Ollama
        try:
            resp = _rq.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": ollama_model,
                    "prompt": full,
                    "stream": False,
                    "options": {"num_predict": 512, "temperature": 0.2},
                },
                timeout=60,
            )
            if resp.status_code == 200:
                content = resp.json().get("response", "").strip()
                if content:
                    return content
        except Exception:
            pass

        logger.debug("Ollama unavailable — decision engine will return failed")
        return ""

    return _call


# ── main controller ─────────────────────────────────────────────


class ChatController:
    """Wires up all agentic components and provides handle_request().

    Designed to be created once by the UI (BottomActionsToolbar) and
    reused for each chat message.
    """

    def __init__(
        self,
        graph_view,
        post_ui: Callable,
        post_ui_sync: Callable,
        add_chat_bubble: Callable,
        scroll_to_bottom: Callable,
        force_ui_flush: Callable,
        set_processing_label: Callable,
        start_processing_indicator: Callable,
        stop_processing_indicator: Callable,
        translate: Callable,
        accent_color: str,
        on_minimize: Optional[Callable] = None,
        on_restore: Optional[Callable] = None,
        get_model: Optional[Callable] = None,
    ):
        self._graph_view = graph_view
        self._post_ui = post_ui
        self._post_ui_sync = post_ui_sync
        self._add_chat_bubble = add_chat_bubble
        self._scroll_to_bottom = scroll_to_bottom
        self._force_ui_flush = force_ui_flush
        self._set_processing_label = set_processing_label
        self._start_processing_indicator = start_processing_indicator
        self._stop_processing_indicator = stop_processing_indicator
        self._translate = translate or (lambda s: s)
        self._accent_color = accent_color
        self._on_minimize = on_minimize
        self._on_restore = on_restore
        self._get_model = get_model

        # Determine base directory (project root)
        _self_path = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self._base_dir = _self_path

        # ── Core subsystems ─────────────────────────────────────
        self._chat_logger = ChatLogger(self._base_dir)
        self._learning_store = LearningStore(self._base_dir, self._log_event)

        # Chain registry & runner
        self._chain_registry = ChainRegistry(
            getattr(graph_view, "chains_folder", "")
        )
        self._chain_runner = ChainRunner(
            self._get_llm_executor,
            on_minimize=on_minimize,
            on_restore=on_restore,
        )

        # Typing assistant
        self._typing_assistant = TypingAssistant(
            on_minimize=on_minimize,
            on_restore=on_restore,
        )

        # Screen observer (LFM vision) — lazily started
        lfm_model_path, lfm_mmproj_path = _find_lfm_model(self._base_dir)
        self._screen_observer: Optional[ScreenObserver] = None
        if lfm_model_path:
            self._screen_observer = ScreenObserver(
                model_path=lfm_model_path,
                mmproj_path=lfm_mmproj_path,
                server_port=8082,
                gpu_layers=35,
                threads=8,
                screenshots_dir=os.path.join(self._base_dir, "screenshots"),
                on_minimize=on_minimize,
                on_restore=on_restore,
            )
        else:
            logger.warning("No LFM model found — visual observation disabled")

        # Decision engine — uses a text-only reasoning model (granite4:3b via Ollama).
        # The vision model (LFM) is only used by ScreenObserver.describe().
        self._call_llm = _build_decision_llm_caller()
        self._decision_engine = DecisionEngine(
            call_llm=self._call_llm,
            chain_registry=self._chain_registry,
            log_fn=self._log_event,
        )

        # ── Action executor & chain builder (interactive learning) ─
        self._action_executor = ActionExecutor(
            on_minimize=on_minimize,
            on_restore=on_restore,
        )
        self._chain_builder = ChainBuilder(
            chains_dir=os.path.join(self._base_dir, "chains"),
            sequences_dir=os.path.join(self._base_dir, "sequences"),
            registry=self._chain_registry,
        )

        # ── Persistent agent reference (survives across requests) ─
        self._agent: Optional[VisualAgent] = None

        # ── LLM executor (lazy, shared) ─────────────────────────
        self._llm_executor = None

    def handle_request(self, text: str) -> None:
        """Process a user chat request in a background thread."""
        thread = threading.Thread(
            target=self._handle_internal, args=(text,), daemon=True
        )
        thread.start()

    # ── internal ─────────────────────────────────────────────────

    def _on_ask_user(self, question: str) -> None:
        """Show the agent's question/prompt as a chat bubble."""
        self._post_ui(lambda q=question: self._add_chat_bubble(q, False))
        self._post_ui(self._scroll_to_bottom)

    def _handle_internal(self, text: str) -> None:
        """Run the VisualAgent in a background thread.

        If the persistent agent is waiting for user input, routes to the
        appropriate handler (guidance or confirmation) instead of
        creating a new agent.
        """
        try:
            # ── Interactive routing ──────────────────────────────
            if (
                self._agent is not None
                and self._agent._ctx.waiting_for_input
            ):
                state = self._agent._ctx.state
                user_request = self._agent._ctx.user_request

                if state == "waiting_guidance":
                    response = self._agent.process_guidance(text)
                elif state == "waiting_confirmation":
                    response = self._agent.process_confirmation(
                        text, user_request
                    )
                else:
                    response = "Unknown interaction state. Starting fresh."
                    self._agent = None

                if response:
                    self._post_ui(
                        lambda r=response: self._add_chat_bubble(r, False)
                    )
                if self._stop_processing_indicator:
                    self._stop_processing_indicator()
                self._post_ui(self._scroll_to_bottom)
                return

            # ── Normal flow: start a new agent ─────────────────
            if self._start_processing_indicator:
                self._start_processing_indicator(
                    self._translate("Processing request")
                )

            self._chat_logger.start_log(text)

            agent = VisualAgent(
                chain_runner=self._chain_runner,
                screen_observer=self._screen_observer,
                decision_engine=self._decision_engine,
                chain_registry=self._chain_registry,
                typing_assistant=self._typing_assistant,
                learning_store=self._learning_store,
                log_event=self._log_event,
                add_chat_bubble=self._add_chat_bubble,
                scroll_to_bottom=self._scroll_to_bottom,
                set_processing_label=self._set_processing_label,
                translate=self._translate,
                accent_color=self._accent_color,
                max_iterations=10,
                action_executor=self._action_executor,
                chain_builder=self._chain_builder,
                on_ask_user=self._on_ask_user,
            )
            self._agent = agent

            response = agent.run(text)

            # Show final response if not already shown by agent
            if response:
                self._post_ui(
                    lambda r=response: self._add_chat_bubble(r, False)
                )

            if self._stop_processing_indicator:
                self._stop_processing_indicator()
            self._post_ui(self._scroll_to_bottom)
            self._chat_logger.end_log({"success": True})

        except Exception as e:
            if self._stop_processing_indicator:
                self._stop_processing_indicator()
            self._post_ui(
                lambda: self._add_chat_bubble(
                    self._translate("Chat execution failed."), False
                )
            )
            self._post_ui(self._scroll_to_bottom)
            self._log_event("dispatch_error", {"error": str(e)})
            self._chat_logger.end_log(
                {"success": False, "reason": "exception", "error": str(e)}
            )

    def _log_event(self, event: str, payload: Any = None) -> None:
        self._chat_logger.log_event(event, payload)

    def _log_llm_interaction(
        self, name: str, llm_config: dict, response: Any
    ) -> None:
        payload = {
            "name": name,
            "model": llm_config.get("model"),
            "system_message": llm_config.get("system_message"),
            "prompt": llm_config.get("prompt"),
            "output_variable": llm_config.get("output_variable"),
            "response": response,
        }
        self._log_event("llm_interaction", payload)

    def _get_llm_executor(self, llm_executor_class=None):
        """Lazy-load LLMExecutor (keeps chain_runner compatible)."""
        if self._llm_executor is None and llm_executor_class:
            try:
                from player.multi_sequence.llm_executor import LLMExecutor
                self._llm_executor = LLMExecutor()
            except Exception:
                pass
        return self._llm_executor

    def _get_selected_model(self):
        """Return selected model string (for TypingAssistant compat)."""
        try:
            if self._get_model:
                return self._get_model()
        except Exception:
            return None
        return None
