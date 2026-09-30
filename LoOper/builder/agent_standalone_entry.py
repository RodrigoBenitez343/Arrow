"""Standalone Agent Entry Point — compiled into the agent .exe.

Self-contained script that runs the LLM inference IN-PROCESS (no HTTP API)
and presents the SAME Agent Mode UI as the full Arrow app: the
semi-transparent AgentOverlay chat panel plus the floating AgentDot
that follows the cursor.  Dispatches user input through
SimpleChainRouter → ChainExecutor.  All paths resolved relative to
sys.executable (frozen-compatible).
"""

import ctypes
import io
import logging
import os
import sys
import threading

# ── DPI awareness (same as main.py) ──
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

if sys.platform == "win32":
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        else:
            try:
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
        else:
            try:
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        # Any FileHandler created before this reconfigure would default to
        # the locale encoding (CP1252 on Windows) and crash on non-ASCII
        # log text (arrows, quotes, emoji).  Force UTF-8 on existing handlers.
        try:
            for _handler in list(logging.root.handlers):
                if getattr(_handler, "encoding", None) is None:
                    try:
                        _handler.encoding = "utf-8"
                    except Exception:
                        pass
        except Exception:
            pass
    except Exception:
        pass

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import multiprocessing

# PyInstaller multiprocessing support
if __name__ == "__main__":
    multiprocessing.freeze_support()

# ── Path setup (same pattern as main.py) ──
if getattr(sys, "frozen", False):
    meipass = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    exe_dir = os.path.dirname(sys.executable)
    internal_dir = meipass
    if os.path.basename(internal_dir).lower() != "_internal":
        candidate_internal = os.path.join(exe_dir, "_internal")
        if os.path.isdir(candidate_internal):
            internal_dir = candidate_internal
    try:
        os.chdir(exe_dir)
    except Exception:
        pass
    if exe_dir and exe_dir not in sys.path:
        sys.path.insert(0, exe_dir)
    if internal_dir and internal_dir not in sys.path:
        sys.path.insert(0, internal_dir)
else:
    _here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _here)
    # LoOper/ — for player, NGUI and AI imports in source-tree runs
    sys.path.insert(0, os.path.dirname(_here))

# ── Resolve data directories ──
def _resolve_dir(subdir: str) -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates = [
            os.path.join(exe_dir, subdir),
            os.path.join(exe_dir, "_internal", subdir),
            os.path.join(exe_dir, "_internal", "LoOper", subdir),
        ]
        # One-file build: files are inside sys._MEIPASS
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for sub in ["", "LoOper"]:
                candidates.append(os.path.join(meipass, sub, subdir))
        for c in candidates:
            if os.path.isdir(c):
                return c
        return candidates[0]
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), subdir)


CHAINS_DIR = _resolve_dir("chains")
MODELS_DIR = _resolve_dir(os.path.join("AI", "models"))
AI_BIN_DIR = _resolve_dir(os.path.join("AI", "bin"))

# Preload torch (avoids WinError 1114 on frozen builds — same pattern as main.py)
try:
    import torch
except Exception:
    pass

# Logger — unified telemetry dispatch (console + logs/automation.log +
# per-session md log); logging_setup.py is bundled by the exporter.
try:
    from logging_setup import setup_logging

    setup_logging()
except Exception:
    pass
logger = logging.getLogger("agent_standalone")

# ═══════════════════════════════════════════════════════════════
# Direct Engine Transport — in-process LlamaCppEngine (no HTTP API)
# ═══════════════════════════════════════════════════════════════
# The exported agent is a frozen artifact with no dynamic model switching,
# so it needs NO FastAPI server: all LLM inference runs in-process via
# AI/llama_cpp_engine.LlamaCppEngine through the AI/inprocess_transport seam.

import atexit

_engine = None
_engine_bootstrap_error = ""


def bootstrap_direct_engine() -> str:
    """Start the in-process llama.cpp engine and verify it with a real probe.

    Returns an empty string on success, or a user-visible error message
    after exhausting restart attempts.  Engine startup is verified by a
    real 1-token generation (not a sleep), so a dead model file fails
    loudly here instead of producing empty LLM responses later.
    """
    global _engine, _engine_bootstrap_error
    os.environ["ARROW_DIRECT_ENGINE"] = "1"
    try:
        from AI import inprocess_transport as _it

        _it.ensure_started(max_restarts=2)
        _engine = _it.get_engine()
        logger.info(
            "Direct engine ready (model: %s, port: %d)",
            _engine.model_path, _engine.server_port,
        )
    except Exception as e:
        _engine_bootstrap_error = f"LLM engine failed to start: {e}"
        logger.error(_engine_bootstrap_error)
        try:
            if _engine is not None:
                _engine.stop_server()
        except Exception:
            pass
    return _engine_bootstrap_error


def shutdown_engine():
    """Stop the in-process engine so no llama-server.exe child survives exit."""
    global _engine
    # Stop the lazy embedding server too (if it was ever started) so the
    # exported agent leaves no orphaned llama-server.exe processes.
    try:
        from AI import embedding_server
        embedding_server.shutdown()
    except Exception:
        pass
    if _engine is not None:
        try:
            _engine.stop_server()
            logger.info("Direct engine stopped")
        except Exception as e:
            logger.error("Failed to stop direct engine: %s", e)
        finally:
            _engine = None


atexit.register(shutdown_engine)


# ═══════════════════════════════════════════════════════════════
# Agent Mode UI — the same overlay + dot as the full Arrow app
# ═══════════════════════════════════════════════════════════════
# Mirrors the AgentOverlay/AgentDot wiring in NGUI/main_window.py so
# the compiled agent looks and behaves exactly like agent mode in the
# main app (Ctrl+Shift+A → semi-transparent overlay + floating dot).

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtGui import QCursor
from PyQt5.QtWidgets import QApplication

from NGUI.widgets.agent_overlay import AgentOverlay
from NGUI.widgets.agent_dot import AgentDot


class StandaloneAgentApp(QObject):
    """Runs the compiled agent with the exact Agent Mode UI of Arrow.

    Behaviour:
      - on launch the floating AgentDot appears beside the cursor
      - clicking the dot (or pressing Ctrl+Shift+A) opens the
        semi-transparent AgentOverlay chat panel
      - Esc / ✕ / back-arrow collapse the overlay back to the dot
      - Ctrl+A inside the overlay closes agent mode — which in a
        standalone agent (no main GUI to restore) quits the app
    """

    def __init__(self, chains_dir=CHAINS_DIR):
        super().__init__()
        self._chains_dir = chains_dir
        self._app = QApplication.instance() or QApplication(sys.argv)

        # ── Same widgets as the full app's agent mode ──
        self._overlay = AgentOverlay()
        self._overlay.hide()
        self._dot = AgentDot()
        self._dot.hide()

        # No main window to minimize/restore during automation
        self._overlay.set_window_manager(None, None)

        # A distributed agent has no build tree — hide the export button
        self._overlay._gear_btn.hide()

        # Surface engine bootstrap failure (if any) as a visible chat bubble
        if _engine_bootstrap_error:
            def _show_bootstrap_error():
                try:
                    self._overlay._add_bubble(_engine_bootstrap_error, False)
                except Exception:
                    logger.exception("Failed to show engine error bubble")

            QTimer.singleShot(800, _show_bootstrap_error)

        # Stop the engine on Qt exit (in addition to atexit)
        self._app.aboutToQuit.connect(shutdown_engine)

        # ── Signal wiring (mirrors main_window.py) ──
        self._overlay.collapsed.connect(self._on_overlay_collapsed)
        self._overlay.closed.connect(self._on_overlay_closed)
        self._overlay.execution_state_changed.connect(self._on_execution_state)
        self._dot.clicked.connect(self.toggle_agent_mode)

        self._hotkey_listener = None
        self._hotkey_thread = None
        self._init_router()
        self._ensure_global_hotkey()
        logger.info("Standalone agent UI ready (overlay + dot)")

    # ── Router setup (same as main_window._init_overlay_chat_controller) ──

    def _init_router(self):
        """Create the SimpleChainRouter and inject it into the overlay."""
        try:
            from player.agentic_ops.simple_agent import SimpleChainRouter

            router = SimpleChainRouter(chains_dir=self._chains_dir)

            # Auto-detect system chain: first chain with collection="system"
            for c in router._chains:
                coll = c.get("collection", "") or ""
                if coll.lower() == "system":
                    router.set_system_chain(c["path"])
                    logger.info(
                        "Standalone: auto-selected system chain '%s'", c.get("id")
                    )
                    break

            self._overlay.set_chat_controller(router)
            logger.info(
                "SimpleChainRouter initialized (chains dir: %s)", self._chains_dir
            )
        except Exception as e:
            logger.exception("Failed to initialize router: %s", e)

    # ── Global hotkey Ctrl+Shift+A (same as main_window) ──

    def _ensure_global_hotkey(self):
        """Start a pynput listener for Ctrl+Shift+A when app is not focused.

        Uses GetAsyncKeyState (not modifier tracking) so key-repeat races
        can't desync the modifier state from the physical keyboard.
        """
        if getattr(self, "_hotkey_listener", None) is not None:
            return

        _vk_ctrl = 0x11
        _vk_shift = 0x10
        _get_key = ctypes.windll.user32.GetAsyncKeyState

        def on_press(key):
            # Match 'A' via char or virtual-key code (0x41)
            ch = getattr(key, 'char', None)
            vk = getattr(key, 'vk', None)
            if not ((ch and (ch.lower() == 'a' or ch == '\x01')) or vk == 0x41):
                return
            try:
                if _get_key(_vk_ctrl) & 0x8000 and _get_key(_vk_shift) & 0x8000:
                    logger.info("Global hotkey Ctrl+Shift+A detected via pynput")
                    QTimer.singleShot(0, self.toggle_agent_mode)
            except Exception:
                logger.exception("pynput on_press error")

        from pynput import keyboard as pynput_keyboard
        self._hotkey_listener = pynput_keyboard.Listener(on_press=on_press)
        self._hotkey_thread = threading.Thread(
            target=self._hotkey_listener.start, daemon=True
        )
        self._hotkey_thread.start()
        logger.debug("Global hotkey listener (Ctrl+Shift+A) started via pynput")

    # ── Toggle dot ↔ overlay ─────────────────────────────────────

    def toggle_agent_mode(self, checked=None):
        """Toggle between the floating dot and the overlay chat."""
        try:
            if self._overlay.isVisible():
                self._overlay._collapse_to_dot()
            else:
                self._dot.hide()
                cursor = QCursor.pos()
                self._overlay.show_overlay(
                    cursor.x() - self._overlay.width() // 2,
                    cursor.y() - 20,
                )
        except Exception:
            logger.exception("toggle_agent_mode: error")

    # ── Signal handlers ──────────────────────────────────────────

    def _on_overlay_collapsed(self):
        """Overlay collapsed (Esc / ✕ / back-arrow) — back to the dot."""
        self._dot.set_execute_mode(False)
        self._dot.show()

    def _on_overlay_closed(self):
        """Overlay fully closed (Ctrl+A) — exit agent mode.

        In the full app this restores the main window; a standalone
        agent has no main GUI to restore, so closing quits the app.
        """
        logger.info("Agent mode closed — exiting")
        self._app.quit()

    def _on_execution_state(self, executing: bool):
        """Toggle dot appearance between idle (teal) and executing (red)."""
        self._dot.set_execute_mode(executing)
        if executing:
            # Show red dot during chain execution (overlay hides for automation)
            if not self._dot.isVisible():
                self._dot.show()
        else:
            # Execution finished — hide dot, overlay shows response again
            if self._dot.isVisible():
                self._dot.hide()

    # ── Run ───────────────────────────────────────────────────────

    def run(self):
        """Enter agent mode immediately and start the Qt event loop."""
        self._dot.set_execute_mode(False)
        self._dot.show()
        sys.exit(self._app.exec_())


# ═══════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════

def main():
    # Bootstrap the in-process LLM engine (probe-verified) BEFORE the UI
    # signals ready — no HTTP API server, no port 8000 listener, no
    # process-killing cleanup.  On failure a visible error bubble is shown
    # in the overlay instead of continuing with a dead engine that would
    # produce empty LLM responses.
    bootstrap_direct_engine()

    # Create and run the agent (same overlay + dot UI as the full app)
    app = StandaloneAgentApp(chains_dir=CHAINS_DIR)
    app.run()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
