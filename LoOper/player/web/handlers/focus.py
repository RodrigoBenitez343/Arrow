from __future__ import annotations

"""Focus / select / submit handlers: element state changes."""

from .. import actions as A  # module access: tests patch web_actions.* helpers
from ..actions import (
    ElementNotFoundError,
    JS_FOCUS_DOM,
    JS_SELECT,
    JS_SUBMIT,
    _css_selectors,
)
from .base import BaseHandler


class FocusHandler(BaseHandler):
    """Focus the recorded editable element (input/textarea/contenteditable)."""

    type_name = "focus"

    def _focus(self, driver, element) -> None:
        """Focus *element* natively (click), falling back to JS focus."""
        try:
            element.click()
        except Exception:
            driver.execute_script(
                "arguments[0].focus({preventScroll:true}); return true;", element
            )

    def _execute(self, driver, event, config) -> str:
        # Full-timeout native resolve first; when WebDriver cannot see the
        # target (elements inside shadow roots, e.g. Google login's
        # identifierId under c-wiz) fall back to the in-browser deep find +
        # focus.
        try:
            element = A.resolve_element(driver, event, config.element_timeout)
        except ElementNotFoundError:
            element = None
        if element is not None:
            A._scroll_into_view(driver, element)
            try:
                self._focus(driver, element)
                return "native"
            except Exception:
                # The page re-rendered between resolve and focus (Google's
                # sign-in advances and rebuilds the form): re-resolve ONCE
                # against the fresh DOM and focus the new element.
                try:
                    element = A.resolve_element(
                        driver, event, min(config.element_timeout, 5.0)
                    )
                    A._scroll_into_view(driver, element)
                    self._focus(driver, element)
                    return "native-retry"
                except Exception:
                    raise
        selectors = _css_selectors(event)
        if selectors:
            try:
                if driver.execute_script(JS_FOCUS_DOM, selectors):
                    return "js"
            except Exception:
                pass
        chain = event.locator.chain() if event.locator else "no locator"
        raise ElementNotFoundError(f"cannot locate focus target: {chain}")


class SelectHandler(BaseHandler):
    type_name = "select"

    def _execute(self, driver, event, config) -> str:
        if event.value is None:
            return "skip"
        element = A.resolve_element(driver, event, config.element_timeout)
        A._scroll_into_view(driver, element)
        driver.execute_script(JS_SELECT, element, event.value)
        return "js"


class SubmitHandler(BaseHandler):
    type_name = "submit"

    def _execute(self, driver, event, config) -> str:
        element = A.resolve_element(driver, event, config.element_timeout)
        A._scroll_into_view(driver, element)
        driver.execute_script(JS_SUBMIT, element)
        return "js"
