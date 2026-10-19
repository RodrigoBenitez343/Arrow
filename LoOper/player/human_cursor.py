"""Human-like OS pointer movement, shared by web replay and form filling.

Desktop sequences walk the pointer through a noisy Bezier before every click
(``base_bot.human_mouse_move``); the web and form-filling paths drove only the
in-page/virtual pointer, so the automation looked like it teleported.  This is
the one glide every non-desktop-path caller uses, so the feel cannot drift
between them.

``LOOPER_OS_CURSOR=off`` parks the pointer (the test suite pins it, so a
fake-driver run never hijacks the developer's mouse).
"""
from __future__ import annotations

import logging
import math
import os
import random

logger = logging.getLogger(__name__)

# Glide parameters.  Hardware is never the ideal on paper: a real clock, a
# slow compositor and a 120 Hz display all change how this reads, so keep the
# knobs here instead of inlining magic numbers at every call site.
MIN_DURATION_S = 0.18   # shortest travel (a nearby button still reads as motion)
MAX_DURATION_S = 0.55   # longest travel - further reads as "scripted slow"
PIXELS_PER_SECOND = 1600.0  # distance -> duration


def moves_enabled() -> bool:
    """True when this run may move the real pointer."""
    return os.environ.get("LOOPER_OS_CURSOR", "").strip().lower() not in (
        "off", "0", "false", "no")


def glide_os_cursor(x: float, y: float) -> None:
    """Walk the real pointer to (x, y) along a curved, eased path.

    Two eased segments through a jittered midpoint give the human read of a
    noisy multi-waypoint Bezier without paying a sleep per waypoint.  Purely
    cosmetic: every caller has already decided WHICH element to act on by
    identity, so a coordinate a few pixels off only LOOKS off.  Never raises -
    a failsafe corner (pyautogui trips on one), a locked session or a headless
    display must not fail the action.
    """
    if not moves_enabled():
        return
    try:
        import pyautogui
    except Exception:  # pyautogui is optional for a pure-JS replay
        return
    try:
        width, height = pyautogui.size()
        x = max(0.0, min(float(x), width - 1.0))
        y = max(0.0, min(float(y), height - 1.0))
        start_x, start_y = pyautogui.position()
        distance = math.hypot(x - start_x, y - start_y)
        if distance < 3:
            pyautogui.moveTo(x, y, _pause=False)
            return
        mid_x = (start_x + x) / 2 + random.uniform(-0.12, 0.12) * distance
        mid_y = (start_y + y) / 2 + random.uniform(-0.08, 0.08) * distance
        duration = min(MAX_DURATION_S,
                       max(MIN_DURATION_S, distance / PIXELS_PER_SECOND))
        pyautogui.moveTo(mid_x, mid_y, duration=duration * 0.55,
                         tween=pyautogui.easeOutQuad, _pause=False)
        pyautogui.moveTo(x, y, duration=duration * 0.45,
                         tween=pyautogui.easeInOutQuad, _pause=False)
    except Exception as exc:  # no pointer, failsafe corner, ... - never fatal
        logger.debug("OS cursor glide skipped: %s", exc)
