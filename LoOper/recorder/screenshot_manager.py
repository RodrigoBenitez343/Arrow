# screenshot_manager.py
"""
Ephemeral screenshot manager - captures to memory only, returns base64 data.
No temporal screenshots are written to disk during recording.
Permanent screenshots are embedded directly in sequence JSON as base64.
"""
import io
import base64
import time
import pyautogui

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False

DATA_IMAGE_PREFIX = "data:image/png;base64,"


class ScreenshotManager:
    """Handles ephemeral screenshot capture for recorded actions"""

    def __init__(self, screenshots_dir='sequences/screenshots'):
        self.screenshots_dir = screenshots_dir
        self.screenshot_count = 0

    def capture_click_screenshot(self, x, y, region_size=35):
        """
        Capture a screenshot around the click coordinates - EPHEMERAL (memory only).

        The captured image is converted to base64 data and returned directly.
        No file is written to disk. The base64 string is embedded in the action
        JSON when the sequence is saved.

        Args:
            x (int): X coordinate of click
            y (int): Y coordinate of click
            region_size (int): Size of the screenshot region (default: 35px)

        Returns:
            str: Base64-encoded image string with data URI prefix (ephemeral),
                 or None if capture failed
        """
        try:
            left = max(0, x - region_size // 2)
            top = max(0, y - region_size // 2)

            screenshot = pyautogui.screenshot(region=(left, top, region_size, region_size))

            self.screenshot_count += 1

            # Convert to base64 in-memory - no disk write
            buffer = io.BytesIO()
            screenshot.save(buffer, format="PNG")
            encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
            b64_data = f"{DATA_IMAGE_PREFIX}{encoded}"

            print(f"Screenshot captured (ephemeral, {len(b64_data)} bytes base64)")
            return b64_data

        except Exception as e:
            print(f"Error taking screenshot: {e}")
            return None
