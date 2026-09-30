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

# Largest element-bbox crop used as a replay template.  cv2 match cost grows
# with both the screen area and the template size, and a huge template breaks on
# dynamic content, so the element box is capped here.
MAX_ELEMENT_REGION = (160, 120)


def element_crop_region(x, y, element_rect, max_size=MAX_ELEMENT_REGION,
                        screen_size=None):
    """Screen region to use as a click's replay template.

    The crop is at most the hovered element's bbox, clamped to ``max_size``, and
    always CENTERED on the click ``(x, y)`` - so the template's center stays the
    click point and replay (match the template, click its center) is unchanged.

    ``element_rect`` is ``(l, t, r, b)`` in screen pixels; None (or garbage)
    means "no element" and returns None so the caller keeps its square crop.
    Returns ``(left, top, width, height)``, clamped on-screen when ``screen_size``
    is given (pyautogui rejects off-screen regions).
    """
    if element_rect is None:
        return None
    try:
        l, t, r, b = (int(v) for v in element_rect)
    except Exception:
        return None

    width = max(8, min(r - l, int(max_size[0])))
    height = max(8, min(b - t, int(max_size[1])))
    left = int(x) - width // 2
    top = int(y) - height // 2

    if screen_size:
        sw, sh = int(screen_size[0]), int(screen_size[1])
        width = min(width, sw)
        height = min(height, sh)
        left = max(0, min(left, sw - width))
        top = max(0, min(top, sh - height))
    return left, top, width, height


def click_crop_region(x, y, element_rect, region_size=35, screen_size=None):
    """The screen region a click's replay template is cropped from.

    The element's bbox when one is known (clamped and centered on the click),
    else the legacy ``region_size`` square centered on the click - exactly what
    ``capture_click_screenshot`` captures, so the recording overlay can draw
    the template area under the cursor.  Never None.
    """
    region = element_crop_region(x, y, element_rect, screen_size=screen_size)
    if region is None:
        left = max(0, int(x) - region_size // 2)
        top = max(0, int(y) - region_size // 2)
        region = (left, top, region_size, region_size)
    return region


class ScreenshotManager:
    """Handles ephemeral screenshot capture for recorded actions"""

    def __init__(self, screenshots_dir='sequences/screenshots'):
        self.screenshots_dir = screenshots_dir
        self.screenshot_count = 0

    def capture_click_screenshot(self, x, y, region_size=35, element_rect=None):
        """
        Capture a screenshot around the click coordinates - EPHEMERAL (memory only).

        The captured image is converted to base64 data and returned directly.
        No file is written to disk. The base64 string is embedded in the action
        JSON when the sequence is saved.

        When ``element_rect`` (the hovered element's bbox in screen pixels) is
        given, the crop is sized to that element (clamped to
        ``MAX_ELEMENT_REGION``) instead of the fixed ``region_size`` square - a
        whole-element template matches far more reliably than a 35px patch.

        Args:
            x (int): X coordinate of click
            y (int): Y coordinate of click
            region_size (int): Default square size when there is no element
            element_rect (tuple): Element bbox (l, t, r, b), or None

        Returns:
            str: Base64-encoded image string with data URI prefix (ephemeral),
                 or None if capture failed
        """
        try:
            screen_size = None
            try:
                screen_size = pyautogui.size()
            except Exception:
                screen_size = None

            region = click_crop_region(x, y, element_rect, region_size=region_size,
                                       screen_size=screen_size)

            screenshot = pyautogui.screenshot(region=region)

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
