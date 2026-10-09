# config.py
"""
Configuration and logging setup for the LocalOperator automation system.
"""

import logging
import os
import importlib.util

# Import computer vision libraries with fallback handling
try:
    import cv2                          # OpenCV library for computer vision and template matching.
    import numpy as np                  # Fundamental package for scientific computing with Python.
    CV2_AVAILABLE = True
except ImportError as _cv_err:
    # Do NOT hide the reason: on a fresh / compiled machine this is almost
    # always a NATIVE dependency problem - the OpenCV wheel links the MSVC
    # runtime and system DLLs (Media Foundation MF.dll/MFReadWrite.dll on
    # Windows "N" editions, dxgi/d3d11 across the board), so a missing OS
    # component surfaces here as a bare ImportError.  Log the real error so it
    # is diagnosable instead of a generic "not available".
    CV2_AVAILABLE = False
    print(f"Warning: OpenCV not available ({type(_cv_err).__name__}: {_cv_err}). "
          "Multi-scale template matching will be disabled.")
    print("Install with: pip install opencv-python numpy")

# Import OCR libraries with fallback handling
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_mkldnn", "0")
_paddleocr_spec = None
try:
    _paddleocr_spec = importlib.util.find_spec("paddleocr")
except Exception:
    _paddleocr_spec = None
OCR_AVAILABLE = bool(_paddleocr_spec is not None)
OCR_ENGINE = "paddle" if OCR_AVAILABLE else None

# --- Logging Configuration ---
# Single unified telemetry dispatch (see logging_setup.py): a colorized
# console handler, a rotating text log (logs/automation.log) and a
# per-instance Markdown session log (logs/session_<ts>.md).  Anchored to the
# LoOper package dir in source mode and the durable runtime dir when frozen
# -- never cwd, which is why the old logs scattered across three directories.
try:
    from logging_setup import setup_logging

    setup_logging()
except Exception:
    # Last-resort fallback so logging can never break startup.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

# Suppress Numba's DEBUG-level SSA rewrite logs (megabytes per JIT compile)
logging.getLogger("numba").setLevel(logging.WARNING)

# Creates a logger instance for the script.
logger = logging.getLogger(__name__)
