"""
model_downloads.py — Thread-safe download tracker for base AI models.

Provides a singleton ModelDownloadTracker that:
  - Registers known base models (SmolLM3, LFM vision)
  - Downloads them via streaming requests from Hugging Face
  - Reports progress in a thread-safe way for UI polling
  - Handles file path resolution in both source and frozen builds
"""

import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class DownloadStatus(Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class ModelState:
    model_id: str
    display_name: str
    save_path: str
    url: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: DownloadStatus = DownloadStatus.PENDING
    error_message: str = ""


def _resolve_models_dir() -> str:
    """Resolve the AI/models/ directory, handling frozen builds.

    Returns the primary directory path (may not exist yet).
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        primary = os.path.join(exe_dir, "AI", "models")
        if os.path.isdir(primary):
            return primary
        internal = os.path.join(exe_dir, "_internal", "AI", "models")
        if os.path.isdir(internal):
            return internal
        return primary
    # Source mode: this file is at LoOper/AI/model_downloads.py,
    # so models are in the sibling 'models' directory.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def _resolve_model_path(relative_path: str) -> str:
    """Resolve a model path relative to the AI/models/ directory."""
    models_dir = _resolve_models_dir()
    return os.path.normpath(os.path.join(models_dir, relative_path))


# ── Model definitions ──────────────────────────────────────────────
# Known base models with their Hugging Face URLs.
# The save_path is relative to AI/models/.
BASE_MODELS: List[dict] = [
    {
        "model_id": "smollm3",
        "display_name": "SmolLM3-3B (chain router)",
        "save_path": "SmolLM3-Q4_K_M.gguf",
        "url": (
            "https://huggingface.co/ggml-org/SmolLM3-3B-GGUF"
            "/resolve/4965cb60b150737b68a0408c36aeefb65078f894/SmolLM3-Q4_K_M.gguf"
        ),
        "size_hint_mb": 1900,
    },
    {
        "model_id": "lfm_vision",
        "display_name": "LFM2.5-VL-450M (vision model)",
        "save_path": os.path.join("LFM2.5-VL-450M-GGUF", "LFM2.5-VL-450M-Q8_0.gguf"),
        "url": (
            "https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF"
            "/resolve/1abed04b6fe71314d8c446a1371c03d7c332266d/LFM2.5-VL-450M-Q8_0.gguf"
        ),
        "size_hint_mb": 480,
    },
    {
        "model_id": "lfm_mmproj",
        "display_name": "LFM2.5-VL mmproj (vision projector)",
        "save_path": os.path.join(
            "LFM2.5-VL-450M-GGUF", "mmproj-LFM2.5-VL-450m-Q8_0.gguf"
        ),
        "url": (
            "https://huggingface.co/LiquidAI/LFM2.5-VL-450M-GGUF"
            "/resolve/1abed04b6fe71314d8c446a1371c03d7c332266d/mmproj-LFM2.5-VL-450m-Q8_0.gguf"
        ),
        "size_hint_mb": 1,
    },
    {
        "model_id": "locateanything",
        "display_name": "LocateAnything-3B-Q8_0 (grounding model)",
        "save_path": "LocateAnything-3B-Q8_0.gguf",
        "url": (
            "https://huggingface.co/sabafallah/LocateAnything-3B-GGUF"
            "/resolve/aac6aefe07703a6c994fab05828cfa5524609380/locateanything-3b-q8_0.gguf"
        ),
        "size_hint_mb": 3700,
    },
    {
        "model_id": "locateanything_mmproj",
        "display_name": "LocateAnything-3B mmproj (vision projector)",
        "save_path": "mmproj-LocateAnything-3B-BF16.gguf",
        "url": (
            "https://huggingface.co/sabafallah/LocateAnything-3B-GGUF"
            "/resolve/aac6aefe07703a6c994fab05828cfa5524609380/mmproj-locateanything-3b-bf16.gguf"
        ),
        "size_hint_mb": 870,
    },
]


class ModelDownloadTracker:
    """Thread-safe singleton for tracking model download progress.

    Usage:
        tracker = ModelDownloadTracker.get_instance()
        tracker.register_base_models()
        # In UI: poll tracker.get_summary() via QTimer
        # In downloader: call tracker.update_progress(...)
    """

    _instance: Optional["ModelDownloadTracker"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._models: dict = {}  # model_id -> ModelState
        self._models_lock = threading.Lock()
        self._active_download_id: Optional[str] = None
        self._stop_requested = False

    @classmethod
    def get_instance(cls) -> "ModelDownloadTracker":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def register_base_models(self) -> None:
        """Register all known base models for tracking."""
        for md in BASE_MODELS:
            resolved = _resolve_model_path(md["save_path"])
            with self._models_lock:
                if md["model_id"] not in self._models:
                    self._models[md["model_id"]] = ModelState(
                        model_id=md["model_id"],
                        display_name=md["display_name"],
                        save_path=resolved,
                        url=md["url"],
                    )

    def get_state(self, model_id: str) -> Optional[ModelState]:
        with self._models_lock:
            s = self._models.get(model_id)
            if s is None:
                return None
            # Return a copy to avoid locking issues for the caller
            return ModelState(
                model_id=s.model_id,
                display_name=s.display_name,
                save_path=s.save_path,
                url=s.url,
                total_bytes=s.total_bytes,
                downloaded_bytes=s.downloaded_bytes,
                status=s.status,
                error_message=s.error_message,
            )

    def update_progress(
        self, model_id: str, downloaded_bytes: int, total_bytes: int
    ) -> None:
        """Update download progress for a model (thread-safe)."""
        with self._models_lock:
            s = self._models.get(model_id)
            if s is None:
                return
            s.downloaded_bytes = downloaded_bytes
            s.total_bytes = total_bytes
            if s.status == DownloadStatus.PENDING:
                s.status = DownloadStatus.DOWNLOADING

    def mark_complete(self, model_id: str) -> None:
        with self._models_lock:
            s = self._models.get(model_id)
            if s is None:
                return
            s.status = DownloadStatus.COMPLETE
            s.downloaded_bytes = s.total_bytes
            if self._active_download_id == model_id:
                self._active_download_id = None

    def mark_error(self, model_id: str, msg: str) -> None:
        with self._models_lock:
            s = self._models.get(model_id)
            if s is None:
                return
            s.status = DownloadStatus.ERROR
            s.error_message = msg
            if self._active_download_id == model_id:
                self._active_download_id = None

    def get_all_states(self) -> List[ModelState]:
        with self._models_lock:
            return [
                ModelState(
                    model_id=s.model_id,
                    display_name=s.display_name,
                    save_path=s.save_path,
                    url=s.url,
                    total_bytes=s.total_bytes,
                    downloaded_bytes=s.downloaded_bytes,
                    status=s.status,
                    error_message=s.error_message,
                )
                for s in self._models.values()
            ]

    def get_summary(self) -> Tuple[bool, str, float, bool, bool]:
        """Get a summary safe for UI polling.

        Returns:
            (is_downloading, current_name, percent_0_100, all_complete, has_errors)
        """
        with self._models_lock:
            if not self._models:
                return False, "", 0.0, True, False

            active = self._active_download_id
            downloading = False
            current_name = ""
            total_pct = 0.0
            total_models = len(self._models)
            completed = 0
            has_error = False

            for s in self._models.values():
                if s.status == DownloadStatus.DOWNLOADING:
                    downloading = True
                    current_name = s.display_name
                    if s.total_bytes > 0:
                        total_pct = min(100.0, s.downloaded_bytes * 100.0 / s.total_bytes)
                elif s.status == DownloadStatus.COMPLETE:
                    completed += 1
                elif s.status == DownloadStatus.ERROR:
                    has_error = True

            if active and not downloading:
                # Transitioning — find the active model
                active_state = self._models.get(active)
                if active_state:
                    current_name = active_state.display_name
                    if active_state.total_bytes > 0:
                        total_pct = min(
                            100.0,
                            active_state.downloaded_bytes * 100.0 / active_state.total_bytes,
                        )

            all_done = completed == total_models and not downloading
            return downloading, current_name, total_pct, all_done, has_error

    def get_missing_models(self) -> List[Tuple[str, str, str]]:
        """Get models whose files don't exist on disk yet.

        Returns:
            List of (model_id, display_name, save_path) for missing models.
        """
        missing = []
        with self._models_lock:
            for s in self._models.values():
                if s.status == DownloadStatus.COMPLETE:
                    continue
                if os.path.exists(s.save_path):
                    fsize = os.path.getsize(s.save_path)
                    if fsize > 0:
                        s.status = DownloadStatus.COMPLETE
                        s.downloaded_bytes = fsize
                        s.total_bytes = fsize
                        continue
                missing.append((s.model_id, s.display_name, s.save_path))
        return missing

    def start_download(self, model_id: str) -> None:
        """Download a single model in a background thread."""
        state = self.get_state(model_id)
        if state is None:
            logger.error("ModelDownloadTracker: unknown model '%s'", model_id)
            return

        if state.status == DownloadStatus.COMPLETE:
            return

        def _worker(mid: str, url: str, save_path: str, name: str):
            nonlocal self
            self._active_download_id = mid
            self.update_progress(mid, 0, 1)
            logger.info("ModelDownloadTracker: downloading %s...", name)

            save_dir = os.path.dirname(save_path)
            try:
                os.makedirs(save_dir, exist_ok=True)
            except Exception as e:
                logger.error("ModelDownloadTracker: cannot create dir %s: %s",
                             save_dir, e)
                self.mark_error(mid, f"Cannot create directory: {e}")
                return

            try:
                import requests

                resp = requests.get(url, stream=True, timeout=30)
                resp.raise_for_status()

                total = int(resp.headers.get("content-length", 0))
                downloaded = 0
                temp_path = save_path + ".download"

                with open(temp_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        f.write(chunk)
                        downloaded += len(chunk)
                        self.update_progress(mid, downloaded, total or 1)

                os.replace(temp_path, save_path)
                self.mark_complete(mid)
                logger.info("ModelDownloadTracker: %s complete", name)

            except ImportError:
                self.mark_error(mid, "requests library not available")
                logger.error("ModelDownloadTracker: 'requests' needed for download")
            except Exception as e:
                self.mark_error(mid, str(e))
                logger.error("ModelDownloadTracker: download failed for %s: %s",
                             name, e)
                temp_path = save_path + ".download"
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass

        thread = threading.Thread(
            target=_worker,
            args=(model_id, state.url, state.save_path, state.display_name),
            daemon=True,
        )
        thread.start()

    def start_all_missing(self) -> None:
        """Start downloading all missing models sequentially.

        Each model is downloaded one at a time.  Completion of one
        triggers the next via a callback chain.
        """
        missing = self.get_missing_models()
        if not missing:
            logger.info("ModelDownloadTracker: all base models present")
            return

        # Make sure we have at least size hint information for newly registered
        with self._models_lock:
            for md in BASE_MODELS:
                s = self._models.get(md["model_id"])
                if s and s.status != DownloadStatus.COMPLETE:
                    # Give it a rough size hint so the progress bar doesn't jump
                    if s.total_bytes == 0 and s.status == DownloadStatus.PENDING:
                        s.total_bytes = md["size_hint_mb"] * 1024 * 1024

        logger.info("ModelDownloadTracker: %d model(s) need downloading", len(missing))

        def _next(idx: int):
            if idx >= len(missing):
                logger.info("ModelDownloadTracker: all missing models processed")
                return
            mid, _name, _path = missing[idx]
            state = self.get_state(mid)
            if state and state.status == DownloadStatus.COMPLETE:
                _next(idx + 1)
                return

            # Start this download; it runs in a daemon thread
            self.start_download(mid)
            # Poll for completion and chain to next
            def _poll():
                s = self.get_state(mid)
                if s and s.status in (DownloadStatus.COMPLETE, DownloadStatus.ERROR):
                    _next(idx + 1)
                else:
                    t = threading.Timer(1.0, _poll)
                    t.daemon = True
                    t.start()

            t = threading.Timer(1.0, _poll)
            t.daemon = True
            t.start()

        # Start first
        if missing:
            _next(0)
