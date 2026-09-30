from __future__ import annotations

"""Navigation and scroll handlers."""

import logging

from ..actions import (
    JS_SCROLL,
    ElementNotFoundError,
    _css_selectors,
    _needs_dom_dispatch,
    resolve_element,
)
from .base import BaseHandler

logger = logging.getLogger(__name__)


class NavigateHandler(BaseHandler):
    """driver.get() for sessions recorded by older builds (legacy action)."""

    type_name = "navigate"

    def _execute(self, driver, event, config) -> str:
        url = event.url or (event.context or {}).get("url")
        if not url:
            raise ValueError("navigate event has no url")
        logger.info("Web navigate -> %s", url)
        driver.get(url)
        return "navigate"


class ScrollHandler(BaseHandler):
    """Replay a recorded scroll burst on the container under the cursor.

    The wheel target recorded is the DEEPEST element under the cursor (usually
    a non-scrollable leaf inside a scrollable pane); JS_SCROLL walks up to the
    nearest scrollable ancestor - or falls back to the recorded locator / the
    document - so a wheel over a feed/chat/lightbox pane scrolls THAT pane
    instead of silently no-op'ing when the page body itself cannot scroll.
    """

    type_name = "scroll"

    def _execute(self, driver, event, config) -> str:
        if not event.scroll:
            return "skip"
        total_delta = int(event.scroll.get("total_delta") or 0)
        if total_delta == 0:
            return "skip"
        element = None
        if event.locator:
            try:
                element = resolve_element(driver, event, config.element_timeout)
            except ElementNotFoundError:
                element = None
        shadow_frame = _needs_dom_dispatch(event)
        # Recorded cursor position of the burst: elementFromPoint() there is
        # the faithful wheel target when the locator degenerated to a lookalike
        # match.  Frame-relative coords are only trustworthy while the driver
        # context matches the recorded frame (element resolved), or when the
        # burst happened in the top-level document (no frame_path).
        point_x = point_y = None
        if not shadow_frame and (element is not None or not event.frame_path):
            pos = event.scroll.get("end") or event.scroll.get("start") or {}
            x, y = pos.get("x"), pos.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                point_x, point_y = int(x), int(y)
        # A shadow-DOM / shadow-hosted-frame target is never WebDriver-
        # reachable: pass the recorded selector so JS_SCROLL deep-searches it
        # through the shadow root / iframe instead of scrolling the top one.
        selector = _css_selectors(event) if shadow_frame else None
        driver.execute_script(JS_SCROLL, element, total_delta, point_x, point_y, selector)
        return "js"
