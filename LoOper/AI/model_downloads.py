"""
model_downloads.py — Lazy runtime asset provisioner + download tracker.

Provisions everything the compiled app needs but that is too large (or too
awkward) to ship inside the MSI: base GGUF models, the embedding/Laya GGUF
models, and the engine "utilities" (llama.cpp binaries, laya.exe, Chrome for
Testing, Vosk, Piper, PaddleOCR).  Assets are fetched ONE AT A TIME, skipping
whatever is already on disk, so a fresh install becomes usable with no manual
setup.

Consumers:
  - main.py ``--install-deps`` -> :func:`run_provisioning` (blocking, then exit)
  - main.py startup            -> ModelDownloadTracker.start_all_missing()
  - Agent Mode UI              -> polls ModelDownloadTracker.get_summary()
"""

import logging
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
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
    # Asset shape (added for the full provisioner manifest).
    root: str = "models"      # "models" | "bin" | "data" | "paddle"
    kind: str = "file"        # "file" | "zip" | "tar"
    extract_to: str = ""      # archive unpack dir (absolute)
    extract_member: str = ""  # only this member (recursive search) -> save_path
    extract_subpath: str = "" # only this subpath's contents -> extract_to


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


def _project_root() -> str:
    """Repo root in source mode (this file is LoOper/AI/model_downloads.py)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _frozen_root() -> Optional[str]:
    """The _MEIPASS (=_internal) dir in frozen builds, else None."""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    return None


def _resolve_bin_dir() -> str:
    """Engine binaries (llama-server/mtmd-cli/laya + DLLs, bundled Chrome)."""
    fr = _frozen_root()
    if fr:
        return os.path.join(fr, "bin")
    return os.path.join(_project_root(), "LoOper", "AI", "bin")


def _resolve_data_dir() -> str:
    """Bundled data (vosk models, piper voices) - resolved as _MEIPASS/data."""
    fr = _frozen_root()
    if fr:
        return os.path.join(fr, "data")
    return os.path.join(_project_root(), "LoOper", "data")


def _resolve_paddle_dir() -> str:
    """PaddleOCR det/rec/cls models, as shipped at _internal/paddle_models."""
    fr = _frozen_root()
    if fr:
        return os.path.join(fr, "paddle_models")
    return os.path.join(os.path.expanduser("~"), ".paddleocr", "whl")


def _dest_root(root: str) -> str:
    """Map a logical asset root to its on-disk directory."""
    if root == "bin":
        return _resolve_bin_dir()
    if root == "data":
        return _resolve_data_dir()
    if root == "paddle":
        return _resolve_paddle_dir()
    return _resolve_models_dir()


# ── Asset manifest ─────────────────────────────────────────────────
# Every runtime asset the app needs, so a fresh install can be provisioned
# lazily (one asset at a time) instead of shipping multi-GB models in the MSI.
# Entry keys:
#   model_id / display_name / url / size_hint_mb
#   root         : "models" | "bin" | "data" | "paddle"  (destination root)
#   save_path    : relative marker that proves presence (file OR dir)
#   kind         : "file" (default) | "zip" | "tar"
#   extract_to   : (archives) dir under root to unpack into (default = root)
#   extract_member  : (archives) only this member (searched recursively)
#                     is copied to save_path
#   extract_subpath : (archives) only this subpath's CONTENTS are flattened
#                     into extract_to
# `url == ""` means verify-only (no known direct URL).


MODELS: List[dict] = [
    {
        "model_id": "smollm3",
        "display_name": "SmolLM3-3B (chain router)",
        "root": "models",
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
        "root": "models",
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
        "root": "models",
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
        "root": "models",
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
        "root": "models",
        "save_path": "mmproj-LocateAnything-3B-BF16.gguf",
        "url": (
            "https://huggingface.co/sabafallah/LocateAnything-3B-GGUF"
            "/resolve/aac6aefe07703a6c994fab05828cfa5524609380/mmproj-locateanything-3b-bf16.gguf"
        ),
        "size_hint_mb": 870,
    },
    {
        "model_id": "embeddinggemma",
        "display_name": "EmbeddingGemma-300M (RAG embeddings)",
        "root": "models",
        "save_path": "embeddinggemma-300M-Q8_0.gguf",
        "url": (
            "https://huggingface.co/unsloth/embeddinggemma-300m-GGUF"
            "/resolve/6661a6504c30d8304af13455cb4a5d4f5bc6011f/embeddinggemma-300M-Q8_0.gguf"
        ),
        "size_hint_mb": 313,
    },
    {
        "model_id": "laya_ud_q4_k_m",
        "display_name": "Laya engine model (ud_q4_k_m)",
        "root": "models",
        "save_path": os.path.join("laya-GGUF", "laya_english_ud_q4_k_m.gguf"),
        "url": (
            "https://huggingface.co/mys/laya-GGUF"
            "/resolve/713ae6f6e39fb54835e010485656e4484e5ec411/laya_english_ud_q4_k_m.gguf"
        ),
        "size_hint_mb": 500,
    },
    {
        "model_id": "laya_q8_0",
        "display_name": "Laya engine model (q8_0)",
        "root": "models",
        "save_path": os.path.join("laya-GGUF", "laya_english_q8_0.gguf"),
        "url": (
            "https://huggingface.co/mys/laya-GGUF"
            "/resolve/713ae6f6e39fb54835e010485656e4484e5ec411/laya_english_q8_0.gguf"
        ),
        "size_hint_mb": 900,
    },
]

# Engine "utilities": normally bundled with the MSI, but verified (and, when a
# direct URL exists, re-downloaded) by the worker so a stripped build still
# heals.  Mirrors the download section of Arrow_setup.bat.
_CFT_VER = "153.0.8010.48"
_CFT_BASE = f"https://storage.googleapis.com/chrome-for-testing-public/{_CFT_VER}/win64"

UTILITIES: List[dict] = [
    {
        "model_id": "laya_exe",
        "display_name": "Laya decision engine (laya.exe)",
        "root": "bin",
        "save_path": "laya.exe",
        "url": "https://github.com/monatis/ggmlc/releases/download/v0.9.6/laya-windows-x86_64-vulkan.zip",
        "kind": "zip",
        "extract_member": "laya.exe",
        "size_hint_mb": 2,
    },
    {
        "model_id": "llama_engine",
        "display_name": "llama.cpp engine (llama-server + mtmd-cli)",
        "root": "bin",
        "save_path": "llama-server.exe",
        "url": "@llama-prebuilt",
        "kind": "zip",
        "size_hint_mb": 120,
    },
    {
        "model_id": "chrome",
        "display_name": f"Chrome for Testing {_CFT_VER}",
        "root": "bin",
        "save_path": os.path.join("chrome", "chrome.exe"),
        "url": f"{_CFT_BASE}/chrome-win64.zip",
        "kind": "zip",
        "extract_to": "chrome",
        "extract_subpath": "chrome-win64",
        "size_hint_mb": 160,
    },
    {
        "model_id": "chromedriver",
        "display_name": "chromedriver (web automation)",
        "root": "bin",
        "save_path": os.path.join("chrome", "chromedriver.exe"),
        "url": f"{_CFT_BASE}/chromedriver-win64.zip",
        "kind": "zip",
        "extract_to": "chrome",
        "extract_member": "chromedriver.exe",
        "size_hint_mb": 9,
    },
    {
        "model_id": "vosk_stt",
        "display_name": "Vosk STT model (voice input)",
        "root": "data",
        "save_path": os.path.join("vosk_models", "vosk-model-small-en-us-0.15"),
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "kind": "zip",
        "extract_to": "vosk_models",
        "size_hint_mb": 40,
    },
    {
        "model_id": "piper_en",
        "display_name": "Piper voice (en_US-lessac-medium)",
        "root": "data",
        "save_path": os.path.join("piper_voices", "en_US-lessac-medium.onnx"),
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "size_hint_mb": 63,
    },
    {
        "model_id": "piper_en_json",
        "display_name": "Piper voice config (en_US-lessac-medium)",
        "root": "data",
        "save_path": os.path.join("piper_voices", "en_US-lessac-medium.onnx.json"),
        "url": "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
        "size_hint_mb": 1,
    },
    {
        "model_id": "paddle_det",
        "display_name": "PaddleOCR detection model",
        "root": "paddle",
        "save_path": os.path.join("det", "inference.pdmodel"),
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar",
        "kind": "tar",
        "extract_to": "det",
        "extract_subpath": "en_PP-OCRv3_det_infer",
        "size_hint_mb": 3,
    },
    {
        "model_id": "paddle_rec",
        "display_name": "PaddleOCR recognition model",
        "root": "paddle",
        "save_path": os.path.join("rec", "inference.pdmodel"),
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv4/english/en_PP-OCRv4_rec_infer.tar",
        "kind": "tar",
        "extract_to": "rec",
        "extract_subpath": "en_PP-OCRv4_rec_infer",
        "size_hint_mb": 10,
    },
    {
        "model_id": "paddle_cls",
        "display_name": "PaddleOCR classification model",
        "root": "paddle",
        "save_path": os.path.join("cls", "inference.pdmodel"),
        "url": "https://paddleocr.bj.bcebos.com/dygraph_v2.0/ch/ch_ppocr_mobile_v2.0_cls_infer.tar",
        "kind": "tar",
        "extract_to": "cls",
        "extract_subpath": "ch_ppocr_mobile_v2.0_cls_infer",
        "size_hint_mb": 1,
    },
]

BASE_ASSETS: List[dict] = MODELS + UTILITIES
# Backwards-compatible alias (older callers/tests referenced BASE_MODELS).
BASE_MODELS: List[dict] = MODELS


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
        """Register every runtime asset (models + utilities) for tracking."""
        for md in BASE_ASSETS:
            root = md.get("root", "models")
            if root == "paddle" and not getattr(sys, "frozen", False):
                # Source mode: PaddleOCR reads its own ~/.paddleocr/whl (nested
                # per-model dirs, populated by Arrow_setup.bat), NOT the flat
                # paddle_models layout the frozen build ships - nothing to do.
                continue
            root_dir = _dest_root(root)
            resolved = os.path.normpath(os.path.join(root_dir, md["save_path"]))
            extract_to = (
                os.path.normpath(os.path.join(root_dir, md["extract_to"]))
                if md.get("extract_to") else ""
            )
            with self._models_lock:
                if md["model_id"] not in self._models:
                    self._models[md["model_id"]] = ModelState(
                        model_id=md["model_id"],
                        display_name=md["display_name"],
                        save_path=resolved,
                        url=md["url"],
                        root=root,
                        kind=md.get("kind", "file"),
                        extract_to=extract_to,
                        extract_member=md.get("extract_member", ""),
                        extract_subpath=md.get("extract_subpath", ""),
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
                root=s.root,
                kind=s.kind,
                extract_to=s.extract_to,
                extract_member=s.extract_member,
                extract_subpath=s.extract_subpath,
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
                    root=s.root,
                    kind=s.kind,
                    extract_to=s.extract_to,
                    extract_member=s.extract_member,
                    extract_subpath=s.extract_subpath,
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
        """Get assets whose marker file/dir is absent or empty on disk.

        Returns:
            List of (model_id, display_name, save_path) for missing assets.
        """
        missing = []
        with self._models_lock:
            for s in self._models.values():
                if s.status == DownloadStatus.COMPLETE:
                    continue
                if os.path.isdir(s.save_path):
                    s.status = DownloadStatus.COMPLETE
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

        def _worker(st: ModelState):
            self._active_download_id = st.model_id
            self.update_progress(st.model_id, 0, 1)
            logger.info("ModelDownloadTracker: provisioning %s...", st.display_name)
            try:
                _download_asset(
                    st,
                    lambda done, total: self.update_progress(st.model_id, done, total),
                )
                self.mark_complete(st.model_id)
                logger.info("ModelDownloadTracker: %s ready", st.display_name)
            except Exception as e:
                self.mark_error(st.model_id, str(e))
                logger.error("ModelDownloadTracker: %s failed: %s",
                             st.display_name, e)

        thread = threading.Thread(target=_worker, args=(state,), daemon=True)
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
            for md in BASE_ASSETS:
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


# ── Provisioner engine (single place that actually fetches an asset) ────────

def _llama_prebuilt_url() -> str:
    """Newest ggml-org/llama.cpp Windows release asset we can use.

    Releases/latest is source-only; the Windows zips (incl. llama-mtmd-cli.exe)
    live on the b#### nightly pre-releases, so scan the list for the first
    release that actually ships the asset.  Mirrors Arrow_setup.bat.
    """
    import requests

    variants = ["vulkan-x64", "cpu-x64"]
    if (os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper() == "ARM64":
        variants = ["cpu-arm64"]
    rels = requests.get(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=40",
        headers={"User-Agent": "arrow-provisioner"}, timeout=30,
    ).json()
    for name in variants:
        for rel in rels:
            for asset in rel.get("assets", []):
                if asset.get("name", "").endswith(f"-bin-win-{name}.zip"):
                    return asset["browser_download_url"]
    raise RuntimeError("no llama.cpp Windows release asset found")


def _download_asset(state: ModelState, progress_cb) -> None:
    """Fetch ONE asset, honouring kind (file/zip/tar) + extraction rules.

    progress_cb(downloaded_bytes, total_bytes) is called during transfer.
    """
    import requests

    url = state.url
    if url == "@llama-prebuilt":
        url = _llama_prebuilt_url()
    if not url:
        raise RuntimeError("no download URL known (verify-only asset)")

    dest_dir = os.path.dirname(state.save_path) or _dest_root(state.root)
    os.makedirs(dest_dir, exist_ok=True)

    tmp_dir = tempfile.mkdtemp(prefix="arrow_asset_")
    try:
        archive = os.path.join(tmp_dir, os.path.basename(url.split("?")[0]) or "asset.bin")
        resp = requests.get(url, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        done = 0
        with open(archive, "wb") as f:
            for chunk in resp.iter_content(chunk_size=262144):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                progress_cb(done, total or 1)

        if state.kind == "file":
            os.replace(archive, state.save_path)
            return

        extract_root = os.path.join(tmp_dir, "unpacked")
        os.makedirs(extract_root, exist_ok=True)
        if state.kind == "zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(extract_root)
        else:
            with tarfile.open(archive) as t:
                t.extractall(extract_root)

        if state.extract_member:
            found = None
            for root, _dirs, files in os.walk(extract_root):
                for fn in files:
                    if fn.lower() == state.extract_member.lower():
                        found = os.path.join(root, fn)
                        break
                if found:
                    break
            if not found:
                raise RuntimeError(f"{state.extract_member} not found in archive")
            shutil.copy2(found, state.save_path)
            return

        target = state.extract_to or dest_dir
        src = (
            os.path.join(extract_root, state.extract_subpath)
            if state.extract_subpath else extract_root
        )
        if not os.path.isdir(src):
            src = extract_root  # subpath missing - fall back to whole archive
        os.makedirs(target, exist_ok=True)
        for entry in os.listdir(src):
            s, d = os.path.join(src, entry), os.path.join(target, entry)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


_PROVISION_LOCK = threading.Lock()
_provision_running = False


def provisioning_active() -> bool:
    """True while a provisioning pass owns the lock."""
    return _provision_running


def _provision_sequential(tracker: "ModelDownloadTracker",
                          missing: List[Tuple[str, str, str]]) -> None:
    """Fetch each missing asset in turn; a failure never stops the rest."""
    for mid, name, _path in missing:
        st = tracker.get_state(mid)
        if st is None:
            continue
        logger.info("Provisioning: %s", name)
        try:
            _download_asset(
                st, lambda d, t, _m=mid: tracker.update_progress(_m, d, t)
            )
            tracker.mark_complete(mid)
        except Exception as e:
            tracker.mark_error(mid, str(e))
            logger.error("Provisioning: %s failed: %s", name, e)


def run_provisioning(block: bool = True) -> bool:
    """Ensure every runtime asset exists, one at a time.

    Returns True when nothing is missing afterwards.  ``block=False`` runs the
    pass on a daemon thread (app startup); ``block=True`` runs it inline
    (``arrow.exe --install-deps``).  Only one pass runs at a time.
    """
    global _provision_running
    tracker = ModelDownloadTracker.get_instance()
    tracker.register_base_models()
    missing = tracker.get_missing_models()
    if not missing:
        logger.info("Provisioning: all %d assets already present",
                    len(tracker._models))
        return True

    with _PROVISION_LOCK:
        if _provision_running:
            logger.info("Provisioning already running - skipping")
            return False
        _provision_running = True

    def _run():
        global _provision_running
        try:
            logger.info("Provisioning: %d asset(s) missing - fetching one by one",
                        len(missing))
            _provision_sequential(tracker, missing)
            logger.info("Provisioning: done; %d still missing",
                        len(tracker.get_missing_models()))
        finally:
            _provision_running = False

    if block:
        _run()
        return not tracker.get_missing_models()
    threading.Thread(target=_run, daemon=True).start()
    return False
