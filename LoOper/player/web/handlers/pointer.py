from __future__ import annotations

"""Pointer interaction handlers: click / dblclick / contextmenu / hover / drag.

Every pointer action resolves its element natively first (trusted CDP input,
with the recorded modifiers held for multi-select macros), then falls back to
the unified shadow-piercing in-browser dispatch - element-based only, never
viewport coordinates, so replay survives window-size/zoom drift.
"""
from .. import actions as A  # module access: tests patch web_actions.* helpers
from ..actions import (
    ElementNotFoundError,
    JS_CLICK_SEQUENCE,
    JS_DRAG,
    JS_HOVER,
    _click_with_modifiers,
    _css_selectors,
    _dispatch_dom_pointer,
    _try_resolve_element,
)
from .base import BaseHandler


class _PointerBase(BaseHandler):
    """Native-first ladder shared by the pointer actions."""

    kind = "click"     # ActionChains kind passed to _click_with_modifiers
    js_mode = "click"  # mode passed to the shadow-piercing dispatch
    button_default = "0"

    def _native(self, driver, event, config, button):
        element = _try_resolve_element(driver, event, config)
        if element is None:
            raise ElementNotFoundError("locator did not resolve natively")
        A._scroll_into_view(driver, element)
        try:
            _click_with_modifiers(
                driver, element, getattr(event, "modifiers", None), self.kind
            )
        except Exception:
            # The recorder captures the DEEPEST element under the cursor - for
            # a button that is usually an inert inner span/div with no box of
            # its own ("element not interactable: has no size and location").
            # Climb to the clickable ancestor (the <button>/<a>/submit) and
            # retry a TRUSTED click there before the JS fallback: a trusted
            # click also runs default actions (submit, navigation) that
            # synthetic events never trigger.
            ancestor = A._climb_clickable(driver, element)
            if ancestor is not None:
                A._scroll_into_view(driver, ancestor)
                _click_with_modifiers(
                    driver, ancestor,
                    getattr(event, "modifiers", None), self.kind,
                )
                return
            raise

    def _js(self, driver, event, config, button):
        if not _dispatch_dom_pointer(
            driver, event, config, button, self.js_mode
        ):
            raise ElementNotFoundError("js dispatch found no target")


class ClickHandler(_PointerBase):
    """Interact with the recorded element via undetected_chromedriver.

    Native-first: the element is located by its recorded locator chain (id /
    data attrs / placeholder / aria / css / xpath, switching into iframes) and
    clicked with a real mouse through ActionChains - the same search-and-
    interact mechanism a desktop sequence uses.  Only when the locator cannot
    resolve (shadow DOM, hydration) does the unified shadow-piercing JS
    dispatch take over.
    """

    type_name = "click"
    kind = "click"
    js_mode = "click"
    button_default = "0"

    def _execute(self, driver, event, config) -> str:
        button = getattr(event, "button", None) or self.button_default
        try:
            return self._ladder([
                ("native", lambda: self._native(driver, event, config, button)),
                ("js", lambda: self._js(driver, event, config, button)),
            ])
        except ElementNotFoundError:
            chain = event.locator.chain() if event.locator else "no locator"
            raise ElementNotFoundError(f"cannot locate click target: {chain}")


class DblClickHandler(_PointerBase):
    type_name = "dblclick"
    kind = "dblclick"
    js_mode = "dblclick"
    button_default = "0"

    def _js_direct(self, driver, event, config, button):
        # Last resort: full-timeout resolve + a plain JS click sequence
        # dispatched twice (browsers synthesize dblclick from two clicks).
        element = A.resolve_element(driver, event, config.element_timeout)
        driver.execute_script(JS_CLICK_SEQUENCE, element, button)
        driver.execute_script(JS_CLICK_SEQUENCE, element, button)

    def _execute(self, driver, event, config) -> str:
        button = getattr(event, "button", None) or self.button_default
        return self._ladder([
            ("native", lambda: self._native(driver, event, config, button)),
            ("js", lambda: self._js(driver, event, config, button)),
            ("js-direct", lambda: self._js_direct(driver, event, config, button)),
        ])


class ContextMenuHandler(_PointerBase):
    type_name = "contextmenu"
    kind = "contextmenu"
    js_mode = "contextmenu"
    button_default = "2"

    def _js_direct(self, driver, event, config, button):
        element = A.resolve_element(driver, event, config.element_timeout)
        driver.execute_script(JS_CLICK_SEQUENCE, element, button)

    def _execute(self, driver, event, config) -> str:
        button = getattr(event, "button", None) or self.button_default
        return self._ladder([
            ("native", lambda: self._native(driver, event, config, button)),
            ("js", lambda: self._js(driver, event, config, button)),
            ("js-direct", lambda: self._js_direct(driver, event, config, button)),
        ])


class HoverHandler(BaseHandler):
    """Hover the recorded element with a real pointer move (native), falling
    back to the shadow-piercing event dispatch when the locator cannot
    resolve."""

    type_name = "hover"

    def _native_hover(self, driver, event, config):
        element = _try_resolve_element(driver, event, config)
        if element is None:
            raise ElementNotFoundError("locator did not resolve natively")
        A._scroll_into_view(driver, element)
        A.ActionChains(driver).move_to_element(element).perform()

    def _js_hover(self, driver, event, config):
        if not _dispatch_dom_pointer(driver, event, config, "0", "hover"):
            element = A.resolve_element(driver, event, config.element_timeout)
            driver.execute_script(JS_HOVER, element)

    def _execute(self, driver, event, config) -> str:
        return self._ladder([
            ("native", lambda: self._native_hover(driver, event, config)),
            ("js", lambda: self._js_hover(driver, event, config)),
        ])


class DragHandler(BaseHandler):
    """Drag the recorded source element to the recorded drop point.

    Native-first: click_and_hold on the source, move by the recorded offset,
    release - a real pointer drag that triggers HTML5/DnD-lib handlers.
    Coordinates are used as OFFSETS from the source element's position, never
    as absolute viewport positions, so replay survives window-size/zoom drift.
    """

    type_name = "drag"

    def _drop_offsets(self, event):
        ctx = getattr(event, "context", None) or {}
        start = getattr(event, "coordinates", None) or {}
        sx = int(start.get("x", 0) or 0)
        sy = int(start.get("y", 0) or 0)
        dx = int(ctx.get("drop_x", sx) or sx)
        dy = int(ctx.get("drop_y", sy) or sy)
        return dx - sx, dy - sy

    def _native(self, driver, event, config):
        element = _try_resolve_element(driver, event, config)
        if element is None:
            raise ElementNotFoundError("drag source locator did not resolve natively")
        A._scroll_into_view(driver, element)
        dx, dy = self._drop_offsets(event)
        chain = A.ActionChains(driver)
        chain.click_and_hold(element).move_by_offset(dx, dy).release().perform()

    def _js(self, driver, event, config):
        selectors = _css_selectors(event)
        if not selectors:
            raise ElementNotFoundError("drag source has no css selector")
        dx, dy = self._drop_offsets(event)
        ctx = getattr(event, "context", None) or {}
        start = getattr(event, "coordinates", None) or {}
        sx = int(start.get("x", 0) or 0)
        sy = int(start.get("y", 0) or 0)
        try:
            ok = bool(driver.execute_script(JS_DRAG, selectors, sx, sy, dx, dy))
        except Exception:
            ok = False
        if not ok:
            raise ElementNotFoundError("js drag dispatch found no target")

    def _execute(self, driver, event, config) -> str:
        return self._ladder([
            ("native", lambda: self._native(driver, event, config)),
            ("js", lambda: self._js(driver, event, config)),
        ])
