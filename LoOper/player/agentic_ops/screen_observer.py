"""
ScreenObserver — Vision interface to LFM2.5-VL-450M via llama.cpp server.

Manages the LFM vision model lifecycle (start/stop server),
captures screenshots, and sends them to LFM for visual description.
"""

import base64
import io
import logging
import os
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)

# ── helpers ─────────────────────────────────────────────────────


def _capture_screenshot(save_dir: str) -> Optional[str]:
    """Capture the screen and return a saved PNG path."""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]  # primary monitor
            raw = sct.grab(monitor)
            from PIL import Image
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    except ImportError:
        # fallback to pyautogui
        try:
            import pyautogui
            img = pyautogui.screenshot()
        except Exception:
            logger.error("No screenshot library available (mss/pyautogui)")
            return None

    os.makedirs(save_dir, exist_ok=True)
    ts = int(time.time())
    path = os.path.join(save_dir, f"agent_vision_{ts}.png")
    try:
        img.save(path, "PNG")
        logger.debug("Saved screenshot: %s", path)
        return path
    except Exception as e:
        logger.error("Failed to save screenshot: %s", e)
        return None


def _image_to_base64(image_path: str) -> Optional[str]:
    """Read a PNG file and return base64-encoded string."""
    try:
        from PIL import Image
        img = Image.open(image_path)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except Exception as e:
        logger.error("Failed to encode image: %s", e)
        return None


_DEFAULT_LFM_PORT = 8082

# ── main class ──────────────────────────────────────────────────


class ScreenObserver:
    """Vision model interface using LFM2.5-VL-450M via llama.cpp server.

    The server is started lazily on first describe() call and stays alive
    until shutdown() is called.
    """

    def __init__(
        self,
        model_path: str = "",
        mmproj_path: str = "",
        server_port: int = _DEFAULT_LFM_PORT,
        gpu_layers: int = 35,
        threads: int = 8,
        context_size: int = 4096,
        screenshots_dir: str = "",
        on_minimize: Optional[Callable] = None,
        on_restore: Optional[Callable] = None,
    ):
        self.model_path = model_path
        self.mmproj_path = mmproj_path
        self.server_port = server_port
        self.gpu_layers = gpu_layers
        self.threads = threads
        self.context_size = context_size
        self._screenshots_dir = screenshots_dir
        self._engine = None  # LlamaCppEngine instance, lazy
        self._server_url = f"http://127.0.0.1:{server_port}"
        self._on_minimize = on_minimize
        self._on_restore = on_restore

    def is_server_running(self) -> bool:
        """Check if the LFM server is already running."""
        try:
            r = requests.get(f"{self._server_url}/health", timeout=1)
            return r.status_code == 200
        except Exception:
            return False

    def ensure_engine(self) -> bool:
        """Lazy-start the llama.cpp server with LFM model.

        Returns True if the server is ready.
        """
        if self.is_server_running():
            return True

        if not self.model_path or not os.path.exists(self.model_path):
            logger.error("LFM model not found: %s", self.model_path)
            return False

        try:
            from AI.llama_cpp_engine import LlamaCppEngine

            config = {
                "model_path": self.model_path,
                "mmproj_path": self.mmproj_path if self.mmproj_path and os.path.exists(self.mmproj_path) else "",
                "server_port": self.server_port,
                "gpu_layers": self.gpu_layers,
                "threads": self.threads,
                "context_size": self.context_size,
            }
            self._engine = LlamaCppEngine(config)
            logger.info(
                "Starting LFM vision server on port %d ...", self.server_port
            )
            ok = self._engine.start_server(blocking=True)
            if ok:
                logger.info("LFM vision server ready at %s", self._server_url)
            else:
                logger.error("LFM vision server failed to start")
            return ok
        except Exception as e:
            logger.error("Failed to start LFM engine: %s", e)
            return False

    # ── vision api ───────────────────────────────────────────────

    def describe(self, prompt: str = "") -> str:
        """Capture a screenshot and ask LFM to describe what it sees.

        Args:
            prompt: Optional custom prompt. If empty, a default description prompt is used.

        Returns:
            Text description of the screen, or error string on failure.
        """
        if not self.ensure_engine():
            return "[ScreenObserver: LFM server not available]"

        if not prompt:
            prompt = (
                "Describe what you see on this screen. "
                "Focus on: open applications, visible windows, buttons, text fields, "
                "and their approximate positions. Be concise (2-4 sentences)."
            )

        # Determine screenshot save dir
        save_dir = self._screenshots_dir
        if not save_dir:
            save_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "screenshots",
            )

        # Minimize GUI before screenshot so we capture the desktop, not our own UI
        if self._on_minimize:
            try:
                self._on_minimize()
            except Exception:
                pass

        try:
            screenshot_path = _capture_screenshot(save_dir)
            if not screenshot_path:
                return "[ScreenObserver: screenshot failed]"

            b64 = _image_to_base64(screenshot_path)
            if not b64:
                return "[ScreenObserver: image encoding failed]"

            # Try server multimodal completion
            return self._call_server_multimodal(prompt, b64, screenshot_path)
        finally:
            # Always restore the GUI
            if self._on_restore:
                try:
                    self._on_restore()
                except Exception:
                    pass

    def _call_server_multimodal(
        self, prompt: str, image_b64: str, image_path: str
    ) -> str:
        """Try /completion with image_data, fallback to mtmd-cli, then text-only."""
        # Attempt 1: server /completion with image_data
        payload = {
            "prompt": prompt,
            "image_data": [{"data": image_b64, "id": 0}],
            "n_predict": 512,
            "temperature": 0.3,
            "stream": False,
        }
        try:
            r = requests.post(
                f"{self._server_url}/completion",
                json=payload,
                timeout=120,
            )
            if r.status_code == 200:
                content = r.json().get("content", "").strip()
                if content:
                    return content
        except Exception:
            pass

        # Attempt 2: mtmd-cli subprocess (more reliable for some models)
        try:
            from AI.llama_cpp_engine import LlamaCppEngine
            engine = LlamaCppEngine(
                {"model_path": self.model_path, "mmproj_path": self.mmproj_path}
            )
            result = engine.generate_mtmd(
                prompt=prompt,
                image_path=image_path,
                temperature=0.3,
                max_tokens=512,
            )
            resp = result.get("response", "").strip()
            if resp:
                return resp
        except Exception:
            pass

        # Attempt 3: text-only fallback (the model has already seen nothing)
        try:
            r = requests.post(
                f"{self._server_url}/completion",
                json={
                    "prompt": prompt,
                    "n_predict": 256,
                    "temperature": 0.3,
                    "stream": False,
                },
                timeout=60,
            )
            if r.status_code == 200:
                return r.json().get("content", "").strip()
        except Exception:
            pass

        return "[ScreenObserver: vision analysis failed]"

    def verify_condition(self, condition: str) -> bool:
        """Ask LFM whether a condition is true about the current screen.

        Args:
            condition: Natural language condition, e.g. "LinkedIn is open"
                       or "The search bar is visible".

        Returns:
            True/False based on LFM's judgement.
        """
        prompt = (
            f"Look at this screen carefully. "
            f"Answer only 'YES' or 'NO': Is the following statement true?\n"
            f'"{condition}"'
        )
        desc = self.describe(prompt)
        answer = desc.strip().upper()[:10]
        return "YES" in answer

    def query(self, question: str) -> str:
        """Ask an arbitrary visual question about the current screen.

        Args:
            question: Natural language question about the screen.

        Returns:
            LFM's answer text.
        """
        return self.describe(question)

    # ── lifecycle ────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Stop the LFM server."""
        if self._engine is not None:
            try:
                self._engine.stop_server()
            except Exception:
                pass
            self._engine = None

    def __del__(self) -> None:
        self.shutdown()
