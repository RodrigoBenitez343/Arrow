# image_utils.py
"""
Image encoding/decoding utilities for embedded screenshot storage.
Provides functions to convert between PIL Images, OpenCV arrays, and base64 strings.
"""

import base64
import io
import logging
import os
from .config import CV2_AVAILABLE

logger = logging.getLogger(__name__)

if CV2_AVAILABLE:
    import cv2
    import numpy as np

DATA_IMAGE_PREFIX = "data:image/png;base64,"


def pil_to_base64(pil_image, format="PNG"):
    """
    Convert a PIL Image to a base64-encoded string with data URI prefix.

    Args:
        pil_image (PIL.Image): The image to encode
        format (str): Image format (PNG, JPEG, etc.)

    Returns:
        str: Base64 string with data URI prefix (data:image/png;base64,...)
    """
    if pil_image is None:
        return None
    try:
        buffer = io.BytesIO()
        pil_image.save(buffer, format=format)
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"{DATA_IMAGE_PREFIX}{encoded}"
    except Exception as e:
        logger.error(f"Failed to convert PIL image to base64: {e}")
        return None


def base64_to_pil(b64_string):
    """
    Convert a base64-encoded image string (with or without data URI prefix) to a PIL Image.

    Args:
        b64_string (str): Base64 string, optionally with data:image/...;base64, prefix

    Returns:
        PIL.Image: Decoded image, or None if decoding failed
    """
    if not b64_string:
        return None
    try:
        # Strip data URI prefix if present
        if DATA_IMAGE_PREFIX in b64_string:
            raw = b64_string.split(DATA_IMAGE_PREFIX, 1)[1]
        elif "base64," in b64_string:
            raw = b64_string.split("base64,", 1)[1]
        else:
            raw = b64_string

        from PIL import Image
        image_data = base64.b64decode(raw)
        buffer = io.BytesIO(image_data)
        return Image.open(buffer).convert("RGB")
    except Exception as e:
        logger.error(f"Failed to decode base64 to PIL image: {e}")
        return None


def base64_to_cv2(b64_string):
    """
    Convert a base64-encoded image string to an OpenCV BGR numpy array.

    Args:
        b64_string (str): Base64 string with or without data URI prefix

    Returns:
        numpy.ndarray: Image in BGR format for OpenCV, or None if failed
    """
    if not CV2_AVAILABLE:
        return None
    try:
        pil_image = base64_to_pil(b64_string)
        if pil_image is None:
            return None
        import numpy as np
        rgb = np.array(pil_image)
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as e:
        logger.error(f"Failed to decode base64 to OpenCV image: {e}")
        return None


def is_embedded_image(val):
    """
    Check if a value is an embedded base64 image string.

    Args:
        val: The value to check

    Returns:
        bool: True if the value looks like a base64-encoded image
    """
    if not isinstance(val, str) or not val:
        return False
    # Check for data URI prefix
    if val.startswith("data:image/"):
        return True
    # Check if it's a sufficiently long base64 string (PNG images are typically >50 chars)
    if len(val) > 100:
        try:
            # Validate base64
            base64.b64decode(val)
            return True
        except Exception:
            pass
    return False


def load_template_from_field(screenshot_field, screenshot_data=None):
    """
    Load an image template from either a base64 data field or a file path.

    This is the primary dispatch function used by action handlers and template
    matching to obtain image data regardless of storage format.

    Args:
        screenshot_field (str): File path to the screenshot (may be None)
        screenshot_data (str): Base64-encoded image data (may be None)

    Returns:
        tuple: (pil_image, cv2_image) - both may be None if loading fails
    """
    pil_image = None
    cv2_image = None

    # Priority 1: Use embedded base64 data
    if screenshot_data and is_embedded_image(screenshot_data):
        pil_image = base64_to_pil(screenshot_data)
        if pil_image is not None and CV2_AVAILABLE:
            import numpy as np
            cv2_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        return pil_image, cv2_image

    # Priority 2: Use file path
    if screenshot_field and os.path.exists(screenshot_field):
        try:
            from PIL import Image
            pil_image = Image.open(screenshot_field).convert("RGB")
            if CV2_AVAILABLE:
                import numpy as np
                cv2_image = cv2.imread(screenshot_field)
            return pil_image, cv2_image
        except Exception as e:
            logger.error(f"Failed to load image from path {screenshot_field}: {e}")
            return None, None

    return None, None


def load_cv2_from_field(screenshot_field, screenshot_data=None):
    """
    Load a template image as an OpenCV BGR numpy array from either base64 data or file path.

    Args:
        screenshot_field (str): File path to the screenshot
        screenshot_data (str): Base64-encoded image data

    Returns:
        numpy.ndarray: Image in BGR format, or None if failed
    """
    _, cv2_image = load_template_from_field(screenshot_field, screenshot_data)
    return cv2_image


def read_image_to_base64(file_path):
    """
    Read an image file and convert it to a base64-encoded string with data URI prefix.

    Args:
        file_path (str): Path to the image file

    Returns:
        str: Base64 string with data URI prefix, or None if failed
    """
    try:
        from PIL import Image
        pil_image = Image.open(file_path).convert("RGB")
        return pil_to_base64(pil_image)
    except Exception as e:
        logger.error(f"Failed to read image to base64 from {file_path}: {e}")
        return None
