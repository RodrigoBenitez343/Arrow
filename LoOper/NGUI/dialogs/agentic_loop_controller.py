"""Agentic loop controller for iterative code generation and refinement.

Encapsulates the iteration state machine (DRAFT → SYNTAX_CHECK → TEST →
ANALYZE → REFINE → COMPLETE) and runs the LLM calls and code execution
in a background QThread, emitting signals for the dialog to update the UI.
"""

import ast
import json
import os
import re
import subprocess
import sys
import time
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import importlib.util

from PyQt5.QtCore import QObject, QThread, pyqtSignal


# Mapping from module name to pip package name for known mismatches.
# When execution fails with ModuleNotFoundError, we consult this table
# before attempting pip install.
_MODULE_TO_PACKAGE = {
    "curses": "windows-curses",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "sklearn": "scikit-learn",
}


_KNOWN_VENV_PYTHON: Optional[str] = None
"""Cached result of `_resolve_python()` to avoid re-scanning on every call."""
_PYTHON_RESOLUTION_LOG: str = ""
"""Debug log from the last `_resolve_python()` call."""


def _resolve_python() -> str:
    """Return the project virtual environment's Python executable.

    The agentic loop runs code and pip-installs packages in a subprocess.
    Using `sys.executable` fails when the UI is compiled (PyInstaller) or
    launched outside the venv shell.  This method walks up the directory
    tree looking for a local ``.venv`` or ``venv`` folder.

    The result is cached in ``_KNOWN_VENV_PYTHON`` after the first call.
    """
    global _KNOWN_VENV_PYTHON
    if _KNOWN_VENV_PYTHON is not None:
        return _KNOWN_VENV_PYTHON

    # 1. sys.prefix check — works when the current process IS in the venv
    if sys.prefix != sys.base_prefix:
        candidate = os.path.join(sys.prefix, "Scripts", "python.exe")
        if os.path.exists(candidate):
            _KNOWN_VENV_PYTHON = candidate
            _log_resolved_python("sys.prefix", candidate)
            return candidate

    # 2. VIRTUAL_ENV environment variable
    ve = os.environ.get("VIRTUAL_ENV")
    if ve:
        candidate = os.path.join(ve, "Scripts", "python.exe")
        if os.path.exists(candidate):
            _KNOWN_VENV_PYTHON = candidate
            _log_resolved_python("VIRTUAL_ENV", candidate)
            return candidate

    # 3. Scan upward from this file's directory for .venv or venv
    _log_resolved_python("scan", "(scanning for .venv or venv)")
    start = os.path.dirname(os.path.abspath(__file__))
    current = start
    for _level in range(12):  # max 12 levels up
        for folder in (".venv", "venv", "virtualenv"):
            candidate = os.path.join(current, folder, "Scripts", "python.exe")
            if os.path.exists(candidate):
                _KNOWN_VENV_PYTHON = candidate
                _log_resolved_python("scan", candidate)
                return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    # 4. Fall back to the current process's executable
    _KNOWN_VENV_PYTHON = sys.executable
    _log_resolved_python("fallback", sys.executable)
    return sys.executable


def _log_resolved_python(source: str, resolved: str) -> None:
    """Emit a debug log when the python resolution changes.

    Stored in a module-level var so the controller can emit it as a
    status message on first use.
    """
    global _PYTHON_RESOLUTION_LOG
    _PYTHON_RESOLUTION_LOG = f"Python: {resolved} (source: {source})"


# ---------------------------------------------------------------------------
# Phase enum
# ---------------------------------------------------------------------------
class LoopPhase(Enum):
    IDLE = auto()
    DRAFTING = auto()
    SYNTAX_CHECK = auto()
    TESTING = auto()
    ANALYZING = auto()
    REFINING = auto()
    COMPLETE = auto()
    FAILED = auto()


# ---------------------------------------------------------------------------
# Structured iteration record for persistence
# ---------------------------------------------------------------------------
@dataclass
class IterationRecord:
    iteration: int
    code: str
    syntax_ok: bool
    syntax_error: str = ""
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    analysis: str = ""
    ready: bool = False


# ---------------------------------------------------------------------------
# System prompt for the agentic coding loop
# ---------------------------------------------------------------------------
AGENTIC_SYSTEM_PROMPT = (
    "You are an expert Python developer operating in an iterative code refinement loop.\n"
    "Your environment: LoOper workflow automation tool.\n"
    "\n"
    "CURRENT ITERATION: {iteration}/{max_iterations}\n"
    "PREVIOUS RESULT: {previous_result}\n"
    "ERRORS TO FIX: {errors}\n"
    "\n"
    "PROTOCOL:\n"
    "1. Write clean, correct Python code inside ```python ... ``` blocks.\n"
    "2. The code must assign a result to the 'result' variable for downstream use.\n"
    "3. 'input_data' (list) and 'args' (dict) are available at runtime.\n"
    "4. Standard libraries (os, sys, json, csv, re, math, etc.) are available.\n"
    "5. After each iteration you will receive execution feedback.\n"
    "6. Analyze errors carefully and fix them in your next response.\n"
    "7. When the code passes syntax and all tests, include the marker: [CODE_READY]\n"
    "8. If you cannot fix the error after multiple attempts, explain why.\n"
    "9. Keep responses concise. Focus on the code, not explanations.\n"
)


# ---------------------------------------------------------------------------
# Helper: extract code block from LLM response
# ---------------------------------------------------------------------------
def _extract_code(text: str) -> str:
    """Return the content of the first ```python ... ``` block, or empty string."""
    m = re.search(r'```python\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ''


# ---------------------------------------------------------------------------
# Worker thread — runs the agentic loop
# ---------------------------------------------------------------------------
class _LoopWorker(QThread):
    """Background thread that runs the full agentic iteration lifecycle."""

    phase_changed = pyqtSignal(object, int)     # LoopPhase, iteration
    code_updated = pyqtSignal(str)              # generated code string
    status_message = pyqtSignal(str)            # status text for chat
    test_output = pyqtSignal(str, int)          # stdout, exit_code
    iteration_done = pyqtSignal(object)         # IterationRecord
    loop_finished = pyqtSignal(bool, str)       # success, message
    error_occurred = pyqtSignal(str)            # error message

    def __init__(
        self,
        user_request: str,
        model: str,
        api_url: str,
        use_llamacpp: bool,
        initial_code: str = "",
        max_iterations: int = 5,
        test_args: Optional[List[Dict[str, Any]]] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._user_request = user_request
        self._model = model
        self._api_url = api_url.rstrip("/")
        self._use_llamacpp = use_llamacpp
        self._initial_code = initial_code
        self._max_iterations = max_iterations
        self._test_args = test_args or []
        self._stop_requested = False
        self._history: List[IterationRecord] = []

    def request_stop(self) -> None:
        self._stop_requested = True

    def get_history(self) -> List[IterationRecord]:
        return list(self._history)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self) -> None:
        try:
            self._run_loop()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            self.loop_finished.emit(False, f"Loop error: {exc}")

    def _run_loop(self) -> None:
        iteration = 0
        current_code = self._initial_code
        last_output = ""
        last_exit_code = -1
        last_errors = ""

        # Show which Python is being used for code execution
        _resolve_python()  # ensure cache is warm
        if _PYTHON_RESOLUTION_LOG:
            self.status_message.emit(_PYTHON_RESOLUTION_LOG)

        # Conversation memory: system prompt + messages exchanged so far
        conversation: List[Dict[str, str]] = []

        while iteration < self._max_iterations and not self._stop_requested:
            iteration += 1

            # ---- Phase: DRAFTING / REFINING ----
            if iteration == 1:
                self.phase_changed.emit(LoopPhase.DRAFTING, iteration)
            else:
                self.phase_changed.emit(LoopPhase.REFINING, iteration)

            # Build the system prompt with current iteration context.
            # When the code crashed (stderr with content but stdout empty),
            # signal the crash instead of saying "(no prior run)".
            if last_output:
                previous_result = last_output[:500]
            elif last_errors:
                previous_result = "(code crashed, see ERRORS below)"
            else:
                previous_result = "(no prior run)"

            system = AGENTIC_SYSTEM_PROMPT.format(
                iteration=iteration,
                max_iterations=self._max_iterations,
                previous_result=previous_result,
                errors=last_errors[:500] if last_errors else "none",
            )

            # Build messages list
            messages: List[Dict[str, str]] = [
                {"role": "system", "content": system},
            ]

            # If we have prior history, include the last 3 exchanges
            # (user request + assistant response + execution result)
            # to keep context bounded.
            recent = conversation[-6:] if len(conversation) > 6 else conversation[:]
            messages.extend(recent)

            if iteration == 1:
                # First iteration: include user request + optionally existing code context
                prompt = self._user_request
                if self._initial_code:
                    prompt += f"\n\nExisting code to improve:\n```python\n{self._initial_code}\n```"
                messages.append({"role": "user", "content": prompt})
            else:
                # Feedback prompt with last execution results
                # Put errors FIRST so the LLM can't miss them.
                if last_errors:
                    feedback = (
                        f"The previous code FAILED.  Here is the Python traceback:\n"
                        f"```\n{last_errors[:1500]}\n```\n"
                        f"Exit code: {last_exit_code}\n"
                    )
                    if last_output:
                        feedback += f"Stdout:\n{last_output[:500]}\n"
                else:
                    feedback = (
                        f"The previous code produced:\n"
                        f"Stdout:\n{last_output[:1000]}\n"
                        f"Exit code: {last_exit_code}\n"
                    )
                feedback += "\nPlease fix the issues and provide corrected code in a ```python block."
                messages.append({"role": "user", "content": feedback})

            # Call LLM
            self.status_message.emit(
                f"Iteration {iteration}: {'Generating initial code...' if iteration == 1 else 'Refining code...'}"
            )
            response_text = self._call_llm(messages)
            if self._stop_requested:
                break
            if response_text is None:
                self.loop_finished.emit(False, "LLM call failed")
                return

            # Store assistant response for context
            conversation.append({"role": "assistant", "content": response_text})

            # Extract code
            code = _extract_code(response_text)
            if not code:
                # If no code block found, use the whole response as a fallback
                code = response_text.strip()
            if code:
                current_code = code
                self.code_updated.emit(code)

            # Check for [CODE_READY] marker — sets a flag only; we still test
            # through syntax check + execution.  The outcome only skips iteration
            # if the code actually passes.
            llm_ready = "[CODE_READY]" in response_text
            if llm_ready:
                response_text = response_text.replace("[CODE_READY]", "")

            # ---- Phase: SYNTAX CHECK ----
            self.phase_changed.emit(LoopPhase.SYNTAX_CHECK, iteration)
            self.status_message.emit(f"Iteration {iteration}: Checking syntax...")
            syntax_ok, syntax_err = self._check_syntax(current_code)

            if not syntax_ok:
                last_errors = syntax_err
                record = IterationRecord(
                    iteration=iteration,
                    code=current_code,
                    syntax_ok=False,
                    syntax_error=syntax_err,
                    ready=False,
                )
                self._history.append(record)
                self.iteration_done.emit(record)
                # Feed syntax error back into conversation for next iteration
                conversation.append({
                    "role": "user",
                    "content": f"Syntax error:\n{syntax_err}\nPlease fix.",
                })
                continue

            # ---- Phase: TESTING ----
            self.phase_changed.emit(LoopPhase.TESTING, iteration)

            # Auto-install missing packages before execution
            self.status_message.emit(f"Iteration {iteration}: Ensuring dependencies...")
            installed_pkgs = self._install_dependencies(current_code)
            if installed_pkgs:
                self.status_message.emit(
                    f"Iteration {iteration}: Installed {', '.join(installed_pkgs)}"
                )

            self.status_message.emit(f"Iteration {iteration}: Running code...")
            stdout_text, stderr_text, exit_code = self._execute_code(current_code)

            # Retry once if execution failed with ModuleNotFoundError
            if exit_code != 0 and "ModuleNotFoundError" in stderr_text:
                retry_installed = self._retry_from_module_error(stderr_text)
                if retry_installed:
                    self.status_message.emit(
                        f"Iteration {iteration}: Installed {', '.join(retry_installed)}, retrying..."
                    )
                    stdout_text, stderr_text, exit_code = self._execute_code(current_code)

            self.test_output.emit(stdout_text + stderr_text, exit_code)

            last_output = stdout_text
            last_exit_code = exit_code
            last_errors = stderr_text if exit_code != 0 else ""

            # ---- Phase: ANALYZING ----
            self.phase_changed.emit(LoopPhase.ANALYZING, iteration)
            self.status_message.emit(f"Iteration {iteration}: Analyzing results...")

            success = exit_code == 0 and not stderr_text.strip()

            # Accept if LLM claimed ready AND tests pass
            if success and llm_ready:
                record = IterationRecord(
                    iteration=iteration,
                    code=current_code,
                    syntax_ok=True,
                    exit_code=exit_code,
                    stdout=stdout_text[:500],
                    stderr=stderr_text[:500],
                    ready=True,
                )
                self._history.append(record)
                self.iteration_done.emit(record)
                self.phase_changed.emit(LoopPhase.COMPLETE, iteration)
                self.loop_finished.emit(True, "Code marked ready and tests passed")
                return

            record = IterationRecord(
                iteration=iteration,
                code=current_code,
                syntax_ok=True,
                exit_code=exit_code,
                stdout=stdout_text[:500],
                stderr=stderr_text[:500],
                ready=success,
            )
            self._history.append(record)
            self.iteration_done.emit(record)

            if success:
                self.phase_changed.emit(LoopPhase.COMPLETE, iteration)
                self.loop_finished.emit(True, "All tests passed")
                return

            # Build feedback — include LLM-ready flag if it was set
            if stderr_text:
                info = f"Python traceback:\n```\n{stderr_text[:1500]}\n```\n"
            else:
                info = f"Stdout:\n{stdout_text[:800]}\n"
            if llm_ready:
                info += (
                    "Note: The LLM marked this code as [CODE_READY], but "
                    "execution failed. Re-check the logic.\n"
                )
            feedback = (
                f"Execution completed with exit code {exit_code}.\n"
                f"{info}\n"
                f"Please fix the issues and output corrected code."
            )
            conversation.append({"role": "user", "content": feedback})

        # Max iterations reached without success
        if not self._stop_requested:
            self.phase_changed.emit(LoopPhase.FAILED, iteration)
            if current_code:
                self.loop_finished.emit(
                    False,
                    f"Max iterations ({self._max_iterations}) reached. "
                    "The last generated code is in the editor for manual review.",
                )
            else:
                self.loop_finished.emit(False, "Max iterations reached without producing valid code.")

    # ------------------------------------------------------------------
    # Dependency management
    # ------------------------------------------------------------------
    def _install_dependencies(self, code: str) -> List[str]:
        """Parse imports from *code* and pip-install any that are missing.

        Returns a list of module names that were newly installed.
        """
        imports: set[str] = set()
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])
        except SyntaxError:
            return []

        installed: List[str] = []
        for mod_name in sorted(imports):
            if not self._is_importable(mod_name):
                pkg = _MODULE_TO_PACKAGE.get(mod_name, mod_name)
                try:
                    r = subprocess.run(
                        [_resolve_python(), "-m", "pip", "install", pkg],
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if r.returncode == 0:
                        installed.append(mod_name)
                except Exception:
                    pass
        return installed

    @staticmethod
    def _is_importable(module_name: str) -> bool:
        """Return True if *module_name* can be imported right now."""
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError, ModuleNotFoundError):
            return False

    @staticmethod
    def _retry_from_module_error(stderr: str) -> List[str]:
        """Parse ModuleNotFoundError from *stderr* and try pip-install.

        Returns module names that were successfully installed.
        """
        m = re.search(r"ModuleNotFoundError: No module named '(\S+)'", stderr)
        if not m:
            return []
        raw_name = m.group(1)
        # stderr may report the C-extension name (_curses) instead of the
        # user-facing module name (curses).  Try the mapping first.
        pkg = _MODULE_TO_PACKAGE.get(raw_name, raw_name)
        try:
            r = subprocess.run(
                [_resolve_python(), "-m", "pip", "install", pkg],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode == 0:
                return [raw_name]
        except Exception:
            pass
        return []

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------
    def _call_llm(self, messages: List[Dict[str, str]]) -> Optional[str]:
        """Send messages to the LLM and return the response text."""
        try:
            import requests

            payload: Dict[str, Any] = {
                "model": self._model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 4096,
            }

            if self._use_llamacpp:
                resp = requests.post(
                    f"{self._api_url}/llamacpp/chat",
                    json=payload,
                    timeout=None,
                )
            else:
                payload["stream"] = False
                resp = requests.post(
                    f"{self._api_url}/chat",
                    json=payload,
                    timeout=None,
                )

            if resp.status_code != 200:
                self.error_occurred.emit(
                    f"LLM error ({resp.status_code}): {resp.text[:200]}"
                )
                return None

            data = resp.json()
            text = data.get("response") or data.get("text") or ""
            return text.strip()

        except Exception as e:
            self.error_occurred.emit(f"LLM call failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Syntax check
    # ------------------------------------------------------------------
    @staticmethod
    def _check_syntax(code: str):
        """Return (ok: bool, error_msg: str)."""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"Line {e.lineno}: {e.msg}"


    # ------------------------------------------------------------------
    # Code execution via subprocess
    # ------------------------------------------------------------------
    def _execute_code(self, code: str):
        """Write code to temp file and run via subprocess.

        Returns:
            (stdout: str, stderr: str, exit_code: int)
        """
        temp_file = os.path.join(
            os.environ.get("TEMP", os.getcwd()),
            f"_looper_agentic_{os.getpid()}_{int(time.time())}.py",
        )
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(code)

            result = subprocess.run(
                [_resolve_python(), temp_file],
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.stdout or "", result.stderr or "", result.returncode

        except subprocess.TimeoutExpired:
            return "", "[Timeout: execution exceeded 30 seconds]", -1
        except Exception as e:
            return "", f"[Execution error: {e}]", -1
        finally:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Public controller — the dialog interacts with this
# ---------------------------------------------------------------------------
class AgenticLoopController(QObject):
    """High-level controller for the agentic code development loop.

    The dialog creates one of these, connects to its signals, and calls
    start_loop()/stop_loop().  All LLM calls and code execution happen
    in a background thread.
    """

    phase_changed = pyqtSignal(object, int)     # LoopPhase, iteration
    code_updated = pyqtSignal(str)
    status_message = pyqtSignal(str)
    test_output = pyqtSignal(str, int)          # stdout, exit_code
    iteration_done = pyqtSignal(object)         # IterationRecord
    loop_finished = pyqtSignal(bool, str)       # success, message
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._worker: Optional[_LoopWorker] = None
        self._history: List[IterationRecord] = []

    def start_loop(
        self,
        user_request: str,
        model: str,
        api_url: str,
        use_llamacpp: bool = True,
        initial_code: str = "",
        max_iterations: int = 5,
        test_args: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Start the agentic loop in a background thread."""
        self.stop_loop()

        self._worker = _LoopWorker(
            user_request=user_request,
            model=model,
            api_url=api_url,
            use_llamacpp=use_llamacpp,
            initial_code=initial_code,
            max_iterations=max_iterations,
            test_args=test_args,
            parent=self,
        )

        # Forward signals
        self._worker.phase_changed.connect(self.phase_changed)
        self._worker.code_updated.connect(self.code_updated)
        self._worker.status_message.connect(self.status_message)
        self._worker.test_output.connect(self.test_output)
        self._worker.iteration_done.connect(self._on_iteration_done)
        self._worker.loop_finished.connect(self._on_loop_finished)
        self._worker.error_occurred.connect(self.error_occurred)

        self._worker.start()

    def stop_loop(self) -> None:
        """Request graceful stop of the current loop."""
        if self._worker is not None:
            self._worker.request_stop()
            if self._worker.isRunning():
                self._worker.wait(3000)
            self._worker = None

    def get_history(self) -> List[IterationRecord]:
        """Return structured iteration history for persistence."""
        if self._worker is not None:
            return self._worker.get_history()
        return list(self._history)

    # ------------------------------------------------------------------
    # Internal signal handlers
    # ------------------------------------------------------------------
    def _on_iteration_done(self, record: IterationRecord) -> None:
        self._history.append(record)
        self.iteration_done.emit(record)

    def _on_loop_finished(self, success: bool, message: str) -> None:
        # Capture history before worker finishes
        if self._worker is not None:
            self._history = self._worker.get_history()
        self.loop_finished.emit(success, message)
