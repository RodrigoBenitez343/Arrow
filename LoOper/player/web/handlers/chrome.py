from __future__ import annotations

"""Browser-chrome interaction handler (back/forward/reload/tabs/omnibox)."""

from .. import actions as A  # module access: tests patch web_actions.* helpers
from ..actions import MOD_KEYS
from .base import BaseHandler


def _os_click(screen_x: int, screen_y: int) -> None:
    """Click at an absolute screen position through the OS input stack.

    Browser chrome (bookmarks, extension popups) is invisible to CDP renderer
    input, so the ONLY way to re-click it is a real OS-level click - exactly
    what the desktop recorder does for its coordinate actions.  Raises when
    pyautogui is unavailable (it never is in the desktop player, but the web
    replay degrades with a clear error instead of silently skipping)."""
    try:
        import pyautogui
    except Exception as exc:
        raise RuntimeError(
            f"chrome_click replay needs pyautogui (missing: {exc})"
        ) from exc
    pyautogui.click(screen_x, screen_y)


class ChromeHandler(BaseHandler):
    """Replay a browser-chrome interaction captured by the OS-level hooks.

    ``event.action`` is a semantic action (back/forward/reload/new_tab/
    close_tab/switch_tab/omnibox_focus) - never raw coordinates, so replay
    survives DPI and window-size drift.  The one exception is
    ``chrome_click``: a coordinate click on the bookmark bar / top strip
    captured by the OS mouse hook (those UI elements are invisible to CDP and
    have no DOM identity), replayed natively at the recorded screen position.
    On a fresh replay browser with no history, back/forward fall back to
    re-navigating to the recorded URL of the neighbouring action (the engine
    passes it via context["fallback_url"]).
    """

    type_name = "chrome"

    def _execute(self, driver, event, config) -> str:
        action = event.action or (event.context or {}).get("action")
        if action == "chrome_click":
            ctx = event.context or {}
            sx = ctx.get("screen_x")
            sy = ctx.get("screen_y")
            if sx is None or sy is None:
                raise ValueError("chrome_click event missing screen coordinates")
            _os_click(int(sx), int(sy))
            return action
        if action == "back":
            before = driver.current_url or ""
            try:
                driver.back()
            except Exception:
                pass
            if (driver.current_url or "") != before:
                return action
            fallback = (event.context or {}).get("fallback_url")
            if fallback:
                driver.get(fallback)
                return action
            raise RuntimeError("back() had no history effect and no fallback_url")
        elif action == "forward":
            before = driver.current_url or ""
            try:
                driver.forward()
            except Exception:
                pass
            if (driver.current_url or "") != before:
                return action
            fallback = (event.context or {}).get("fallback_url")
            if fallback:
                driver.get(fallback)
                return action
            raise RuntimeError("forward() had no history effect and no fallback_url")
        elif action == "reload":
            driver.refresh()
        elif action == "new_tab":
            driver.execute_cdp_cmd("Target.createTarget", {"url": "about:blank"})
            handles = driver.window_handles
            if handles:
                driver.switch_to.window(handles[-1])
        elif action == "close_tab":
            handles = driver.window_handles
            if len(handles) > 1:
                driver.close()
                driver.switch_to.window(driver.window_handles[-1])
        elif action == "switch_tab":
            try:
                index = int(event.value)
            except (TypeError, ValueError):
                index = 0
            handles = driver.window_handles
            if handles:
                if index < 0:
                    index = len(handles) - 1  # Ctrl+9 = last tab
                if 0 <= index < len(handles):
                    driver.switch_to.window(handles[index])
        elif action == "next_tab":
            handles = driver.window_handles
            if len(handles) > 1:
                cur = driver.current_window_handle
                try:
                    idx = handles.index(cur)
                except ValueError:
                    idx = -1
                driver.switch_to.window(handles[(idx + 1) % len(handles)])
        elif action == "prev_tab":
            handles = driver.window_handles
            if len(handles) > 1:
                cur = driver.current_window_handle
                try:
                    idx = handles.index(cur)
                except ValueError:
                    idx = 0
                driver.switch_to.window(handles[(idx - 1) % len(handles)])
        elif action == "omnibox_focus":
            chain = A.ActionChains(driver)
            chain.key_down(MOD_KEYS["Ctrl"]).send_keys("l").key_up(MOD_KEYS["Ctrl"])
            chain.perform()
        else:
            raise ValueError(f"unknown chrome action: {action}")
        return action or "chrome"
