"""In-page orange execution overlay for web replay (JS twin of the desktop
execution overlay).

The recorder's overlay is driven by ``pointermove``; web REPLAY dispatches DOM
events without moving the OS cursor, so this overlay is driven by explicit
calls: for each action the engine asks the page to box the element the action
is ABOUT to hit and to extend the virtual cursor trail to it.

Gated on a sessionStorage flag (per-origin, survives same-origin navigation -
exactly like the recording overlay) and re-injected into every new document
through CDP, so it follows a replay across navigations.  Best-effort
throughout: a failure to draw must never affect the replay itself.
"""
from __future__ import annotations

import logging
from pathlib import Path

from . import actions as A

logger = logging.getLogger(__name__)

_INJECT = Path(__file__).parent / "inject" / "exec_overlay.js"
_STORAGE_KEY = "looper.web.execOverlay"


def _payload() -> str:
    try:
        return _INJECT.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("Web execution overlay payload unavailable: %s", exc)
        return ""


# Resolve the recorded element with the SAME shadow-piercing deep search the JS
# dispatch path uses, hand its viewport rect + a short caption to the overlay,
# and always update the action banner (even when the element cannot be found).
JS_EXEC_MARK = A.JS_DEEP_SEARCH + """return (function (selectors, index, act) {
  var el = null;
  try { el = (typeof __wvpDeepFindAny === 'function') ? __wvpDeepFindAny(selectors, index) : null; } catch (e) { el = null; }
  var rect = null, cap = '';
  if (el && el.getBoundingClientRect) {
    var b = el.getBoundingClientRect();
    rect = { x: b.left, y: b.top, w: b.width, h: b.height };
    var t = (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 40);
    cap = (el.tagName || '').toLowerCase() + (t ? '  "' + t + '"' : '');
  }
  if (typeof window.__wvpExecMark === 'function') window.__wvpExecMark(rect, cap, act);
  return true;
})(arguments[0], arguments[1], arguments[2]);
"""


def enable(driver) -> None:
    """Turn the overlay ON for this document and every future one."""
    payload = _payload()
    if not payload:
        return
    try:
        driver.execute_script(
            "try { sessionStorage.setItem(arguments[0], 'on'); } catch (e) {}",
            _STORAGE_KEY,
        )
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": payload}
        )
    except Exception as exc:  # CDP unavailable - rely on the current document
        logger.debug("Web execution overlay CDP pre-injection unavailable: %s", exc)
    try:
        driver.execute_script(
            payload + "\ntry { window.__wvpExecOn && window.__wvpExecOn(); } catch (e) {}"
        )
    except Exception:
        pass


def disable(driver) -> None:
    """Turn the overlay OFF in every open window (called at run end)."""
    try:
        handles = list(driver.window_handles)
        original = driver.current_window_handle
    except Exception:
        return
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            driver.execute_script(
                "try { sessionStorage.setItem(arguments[0], 'off'); } catch (e) {}"
                "try { window.__wvpExecOff && window.__wvpExecOff(); } catch (e) {}",
                _STORAGE_KEY,
            )
        except Exception:
            continue
    try:  # restore the attached window so later reads stay correct
        driver.switch_to.window(original)
    except Exception:
        pass


def mark(driver, event, act: str) -> None:
    """Box the element *event* is about to act on.  Best-effort."""
    selectors = A._css_selectors(event)
    index = getattr(event.locator, "index", None) if event.locator else None
    if not isinstance(index, int):
        index = 0
    try:
        driver.execute_script(JS_EXEC_MARK, selectors, index, str(act or ""))
    except Exception:
        pass
