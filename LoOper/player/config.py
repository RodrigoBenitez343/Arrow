# config.py
"""
Configuration and logging setup for the LocalOperator automation system.
"""

import logging
import sys

# Import computer vision libraries with fallback handling
try:
    import cv2                          # OpenCV library for computer vision and template matching.
    import numpy as np                  # Fundamental package for scientific computing with Python.
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False
    print("Warning: OpenCV not available. Multi-scale template matching will be disabled.")
    print("Install with: pip install opencv-python numpy")

# Import OCR libraries with fallback handling
try:
    import pytesseract                  # Python wrapper for Tesseract OCR engine.
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    print("Warning: pytesseract not available. OCR functionality will be disabled.")
    print("Install with: pip install pytesseract")
    print("Also ensure Tesseract OCR is installed on your system.")

# --- Logging Configuration ---
# Sets up how the script will log information. It will both save to a file ('automation.log')
# and print to the console.
logging.basicConfig(
    level=logging.INFO,  # Sets the minimum level of messages to log (INFO, DEBUG, WARNING, ERROR).
    format='%(asctime)s - %(levelname)s - %(message)s',  # Defines the format of the log messages.
    handlers=[
        logging.FileHandler("automation.log"),  # Handler to write logs to a file.
        logging.StreamHandler(sys.stdout)       # Handler to print logs to the console.
    ]
)

# Creates a logger instance for the script.
logger = logging.getLogger(__name__)