from functools import lru_cache
from typing import Any, Dict, List, Optional
import logging

from .config import OCR_AVAILABLE, OCR_ENGINE
import os
import sys

logger = logging.getLogger(__name__)

# Force disable MKLDNN to avoid OneDnnContext errors
# This must be done before importing paddle
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_mkldnn"] = "0"


def ocr_available() -> bool:
    return bool(OCR_AVAILABLE and OCR_ENGINE)


def ocr_engine() -> Optional[str]:
    return OCR_ENGINE


def _normalize_language_for_paddle(language: str) -> str:
    language = (language or "").strip().lower()
    if language in {"eng", "en", "english"}:
        return "en"
    if language in {"ch", "chi", "zh", "zho", "cn", "chinese"}:
        return "ch"
    return language or "en"


@lru_cache(maxsize=4)
def _get_paddle_ocr(lang: str):
    import os
    import sys
    import importlib

    # Force disable MKLDNN to avoid OneDnnContext errors
    os.environ["FLAGS_use_mkldnn"] = "0"
    os.environ["FLAGS_enable_mkldnn"] = "0"
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    try:
        paddle = importlib.import_module("paddle")
        if hasattr(paddle, "set_flags"):
            paddle.set_flags({'FLAGS_use_mkldnn': False})
    except Exception as e:
        raise e

    from paddleocr import PaddleOCR

    # Prepare arguments
    kwargs = {
        'use_angle_cls': True,
        'lang': lang,
        'enable_mkldnn': False
    }

    # Check for bundled models in frozen mode (PyInstaller)
    if getattr(sys, 'frozen', False):
        try:
            # In one-folder COLLECT mode, sys._MEIPASS is the directory
            # containing the executable (e.g. dist/arrow/).
            base_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
            exe_dir = os.path.dirname(sys.executable)

            # Collect candidate search paths for paddle_models.
            # Priority order:
            #   1. _internal/ subdirectory (traditional PyInstaller layout)
            #   2. sys._MEIPASS / exe directory root
            #   3. exe_dir as last resort
            candidates = []
            if os.path.basename(base_dir).lower() != '_internal':
                cand_internal = os.path.join(exe_dir, '_internal')
                if os.path.isdir(cand_internal):
                    candidates.append(cand_internal)
            candidates.append(base_dir)
            if exe_dir != base_dir:
                candidates.append(exe_dir)

            # Try each candidate until we find paddle_models
            models_dir = None
            for cand in candidates:
                test_path = os.path.join(cand, 'paddle_models')
                if os.path.exists(test_path):
                    models_dir = test_path
                    break

            if models_dir:
                logger.debug(f"Found bundled PaddleOCR models at {models_dir}")

                det_path = os.path.join(models_dir, 'det')
                rec_path = os.path.join(models_dir, 'rec')
                cls_path = os.path.join(models_dir, 'cls')

                if os.path.exists(det_path):
                    kwargs['det_model_dir'] = det_path
                if os.path.exists(rec_path):
                    kwargs['rec_model_dir'] = rec_path
                if os.path.exists(cls_path):
                    kwargs['cls_model_dir'] = cls_path
        except Exception as e:
            logger.error(f"Error checking for bundled models: {e}")

    # Try to initialize with enable_mkldnn first
    try:
        logger.debug(f"Initializing PaddleOCR with kwargs={kwargs}")
        ocr = PaddleOCR(**kwargs)
        logger.debug("PaddleOCR initialized successfully")
        return ocr
    except TypeError:
        # Fallback if the installed version doesn't support enable_mkldnn arg
        if 'enable_mkldnn' in kwargs:
            del kwargs['enable_mkldnn']
        logger.debug("PaddleOCR initialized with fallback (no enable_mkldnn arg)")
        return PaddleOCR(**kwargs)


def _ensure_numpy():
    import numpy as np

    return np


def _as_numpy_image(image: Any):
    np = _ensure_numpy()
    if hasattr(image, "convert"):
        arr = np.array(image.convert("RGB"))
        return arr[:, :, ::-1]
    return np.array(image)


def _ensure_color_bgr(image_arr):
    np = _ensure_numpy()
    if image_arr is None:
        return None
    if len(image_arr.shape) == 2:
        return np.stack([image_arr, image_arr, image_arr], axis=-1)
    if len(image_arr.shape) == 3 and image_arr.shape[2] == 4:
        return image_arr[:, :, :3]
    return image_arr


_OCR_WORKER_TIMEOUT = 180  # seconds; a hung worker must not hang the app


def _ocr_via_subprocess(image: Any, min_confidence: float, language: str) -> List[Dict[str, Any]]:
    """Run paddle OCR in an isolated worker process.

    Paddle's native inference has crashed the whole app before (WerFault /
    0xc0000005 access violation during OCR — a native fault has no Python
    exception to catch).  In the worker the crash kills only the worker;
    a non-zero exit or timeout yields an empty result and a warning, never
    a dead app.  The worker also pays the model-load cost per call, which
    is the price of crash isolation.
    """
    import json
    import subprocess
    import tempfile

    tmp_img = None
    tmp_out = None
    try:
        image_arr = _ensure_color_bgr(_as_numpy_image(image))
        if image_arr is None:
            return []
        import cv2

        fd, tmp_img = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        if not cv2.imwrite(tmp_img, image_arr):
            return []
        fd, tmp_out = tempfile.mkstemp(suffix=".json")
        os.close(fd)

        worker = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "ocr_worker.py"
        )
        lang = _normalize_language_for_paddle(language)
        proc = subprocess.run(
            [sys.executable, worker, tmp_img, str(float(min_confidence)), lang, tmp_out],
            capture_output=True,
            timeout=_OCR_WORKER_TIMEOUT,
            creationflags=0x08000000 if os.name == "nt" else 0,  # CREATE_NO_WINDOW
        )
        if proc.returncode != 0:
            _err = proc.stderr.decode("utf-8", "replace")[:300]
            logger.warning(
                "OCR worker crashed (rc=%s) — returning empty result%s",
                proc.returncode, f": {_err}" if _err else "",
            )
            return []
        with open(tmp_out, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("error"):
            logger.warning("OCR worker reported an error: %s", data["error"])
        return data.get("elements") or []
    except subprocess.TimeoutExpired:
        logger.warning(
            "OCR worker timed out after %ds — returning empty result",
            _OCR_WORKER_TIMEOUT,
        )
        return []
    except Exception as exc:
        logger.warning("OCR subprocess failed: %s", exc)
        return []
    finally:
        for _p in (tmp_img, tmp_out):
            if _p:
                try:
                    os.remove(_p)
                except Exception:
                    pass


def extract_text_elements(image: Any, min_confidence: float = 0.6, language: str = "eng") -> List[Dict[str, Any]]:
    if not ocr_available():
        return []

    engine = ocr_engine()
    if engine == "paddle":
        # Isolated subprocess: paddle's native inference has crashed the
        # whole app before (WerFault / 0xc0000005).  A crash in the worker
        # only kills the worker — the app falls back to an empty result.
        return _ocr_via_subprocess(image, min_confidence, language)

    return []


def extract_text(image: Any, min_confidence: float = 0.0, language: str = "eng") -> str:
    elements = extract_text_elements(image=image, min_confidence=min_confidence, language=language)
    if not elements:
        return ""
    return "\n".join([e["text"] for e in elements if e.get("text")])


def extract_text_elements_from_image_path(image_path: str, min_confidence: float = 0.6, language: str = "eng"):
    try:
        from PIL import Image
    except Exception:
        return []

    try:
        image = Image.open(image_path)
    except Exception:
        return []

    return extract_text_elements(image=image, min_confidence=min_confidence, language=language)
