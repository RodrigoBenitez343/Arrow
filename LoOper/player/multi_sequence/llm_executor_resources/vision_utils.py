import os
import time
from typing import Optional, Tuple, List

from ...config import logger
from ...screenshot_cleanup import register_temporal_screenshot, cleanup_screenshot


def capture_screenshot(stop_flag) -> Optional[str]:
    """Capture a screenshot and return the saved path registered for cleanup."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False

        if stop_flag and stop_flag():
            logger.info("Stopped before vision screenshot capture")
            return None

        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "screenshots")
        os.makedirs(screenshots_dir, exist_ok=True)
        screenshot = pyautogui.screenshot()
        timestamp = int(time.time())
        screenshot_path = os.path.join(screenshots_dir, f"llm_vision_{timestamp}.png")
        screenshot.save(screenshot_path)
        register_temporal_screenshot(screenshot_path)
        return screenshot_path
    except Exception as e:
        logger.error(f"Screenshot capture failed: {e}")
        return None


def prepare_vision_images(should_use_vision: bool, screenshot_enabled: bool, stop_flag) -> Tuple[Optional[List[str]], Optional[str]]:
    """Capture screenshot if needed and return image list for vision models.

    Returns (images, ocr_text_placeholder).
    """
    if not should_use_vision:
        return None, None
    if not screenshot_enabled:
        logger.info("Vision requested but screenshots disabled")
        return None, None

    path = capture_screenshot(stop_flag)
    if not path:
        return None, None
    # Keep cleanup to the caller when done, to allow API usage.
    return [path], None