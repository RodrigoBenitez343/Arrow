"""Element-sized click templates + human click deviation.

Recording used to crop a 35x35 patch around the cursor; it now crops the hovered
element's bbox (clamped to a budget, centered on the click).  Replay nudges the
click 5-10px inside that template.  These pin the two pure helpers.
"""
import math

import pytest

from recorder.screenshot_manager import (
    MAX_ELEMENT_REGION,
    click_crop_region,
    element_crop_region,
)


# ---------------------------------------------------------------- crop region

def test_no_element_keeps_the_default_square_crop():
    assert element_crop_region(500, 400, None) is None
    assert element_crop_region(500, 400, "garbage") is None


def test_crop_is_sized_to_the_element_and_centered_on_the_click():
    # 120x40 element, click at (500, 400): crop is the element's size, centered
    # on the click - so the template's centre IS the click point.
    assert element_crop_region(500, 400, (440, 380, 560, 420)) == (440, 380, 120, 40)


def test_crop_is_clamped_to_the_budget_and_recentered():
    left, top, width, height = element_crop_region(500, 400, (0, 0, 1000, 800))
    assert (width, height) == MAX_ELEMENT_REGION
    assert (left, top) == (500 - MAX_ELEMENT_REGION[0] // 2,
                           400 - MAX_ELEMENT_REGION[1] // 2)


def test_crop_is_kept_on_screen():
    left, top, _, _ = element_crop_region(5, 5, (0, 0, 200, 100),
                                          screen_size=(1920, 1080))
    assert left >= 0 and top >= 0

    left, top, width, height = element_crop_region(
        1918, 1078, (1800, 1000, 2000, 1200), screen_size=(1920, 1080))
    assert left + width <= 1920 and top + height <= 1080


def test_click_crop_region_falls_back_to_the_legacy_square():
    # No element -> the legacy 35px patch centered on the click.  This IS the
    # template a right-Ctrl visual click matches on at replay.
    assert click_crop_region(500, 400, None) == (500 - 17, 400 - 17, 35, 35)


def test_click_crop_region_uses_the_element_box_when_known():
    assert click_crop_region(500, 400, (440, 380, 560, 420)) == (440, 380, 120, 40)


# ---------------------------------------------------------------- click jitter

action_handlers = pytest.importorskip("player.action_handlers")


def test_jitter_nudges_5_to_10px_within_the_template():
    seen = set()
    for _ in range(300):
        x, y = action_handlers.jittered_click_target(500, 400, (160, 120))
        d = math.hypot(x - 500, y - 400)
        assert 4 <= d <= 11  # the requested 5-10px, allowing for rounding
        seen.add((x, y))
    assert len(seen) > 1  # it actually varies between clicks


def test_jitter_shrinks_so_a_tiny_target_is_never_missed():
    for _ in range(300):
        x, y = action_handlers.jittered_click_target(500, 400, (8, 8))
        # limit = 8//2 - 3 = 1, so the click stays within 1px of the centre
        assert abs(x - 500) <= 1 and abs(y - 400) <= 1


def test_jitter_uses_the_configured_drift_range():
    for _ in range(300):
        x, y = action_handlers.jittered_click_target(500, 400, (160, 120), 1.0, 2.0)
        d = math.hypot(x - 500, y - 400)
        assert 0.9 <= d <= 3  # the requested 1-2px, allowing for rounding


def test_zero_drift_clicks_the_exact_centre():
    assert action_handlers.jittered_click_target(500, 400, (160, 120), 0, 0) == (500, 400)
