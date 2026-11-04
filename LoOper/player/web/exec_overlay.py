"""In-page orange execution overlay for web replay (JS twin of the desktop
execution overlay).

The recorder's overlay is driven by ``pointermove``; web REPLAY dispatches DOM
events without moving the OS cursor, so this overlay is driven by explicit
calls: for each action the engine asks the page to box the element the action
is ABOUT to hit and to WALK its own virtual cursor onto it.

That walk is the reason web playback never touches the real pointer: the browser
owns a pointer of its own here, so a web sequence cannot drag the user's mouse
(and the desktop player's animated cursor) across the screen the way a desktop
sequence does.  The glide keeps the desktop's human read - curved, eased,
distance-timed - it just happens inside the page.

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
# Returns the virtual-cursor walk duration in ms (0 = parked immediately).
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
  if (typeof window.__wvpExecMark === 'function')
    return window.__wvpExecMark(rect, cap, act) || 0;
  return 0;
})(arguments[0], arguments[1], arguments[2]);
"""


# Draw a mark for a rect the CALLER already resolved (the element the action
# will actually hit) instead of re-running the in-page search here.
JS_EXEC_MARK_RECT = """try {
  if (typeof window.__wvpExecMark === 'function')
    return window.__wvpExecMark({x: arguments[0][0], y: arguments[0][1],
                                w: arguments[0][2], h: arguments[0][3]},
                               arguments[2], arguments[1]) || 0;
} catch (e) {}
return 0;"""


def _caption(event) -> str:
    """Short "tag \"text\"" caption for a pre-resolved mark."""
    loc = getattr(event, "locator", None)
    try:
        return loc.describe() if loc is not None else ""
    except Exception:
        return ""


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


def mark(driver, event, act: str, geometry=None) -> float:
    """Box the element *event* is about to act on and walk the page's cursor.

    ``geometry`` (``actions.target_geometry``) is the element ALREADY resolved
    the way the action will resolve it.  Pass it whenever it is available: the
    in-page search below only sees the recorded CSS ladder, so on a page whose
    recorded css ALSO matches a sibling (a "Back" button where "Next" used to
    sit) it boxes that lookalike while the click lands on the real target.

    Returns the virtual-cursor walk duration in SECONDS (0.0 when there is
    nothing to walk to), so the caller can pace the action behind the cursor
    instead of firing it before the cursor arrives.
    """
    if geometry:
        rect = geometry.get("rect")
        if rect:
            try:
                return _walk_seconds(driver.execute_script(
                    JS_EXEC_MARK_RECT, [float(v) for v in rect],
                    str(act or ""), _caption(event)))
            except Exception as exc:
                logger.debug("Web execution mark (resolved) failed: %s", exc)
    selectors = A._css_selectors(event)
    index = getattr(event.locator, "index", None) if event.locator else None
    if not isinstance(index, int):
        index = 0
    try:
        return _walk_seconds(driver.execute_script(
            JS_EXEC_MARK, selectors, index, str(act or "")))
    except Exception:
        return 0.0


def _walk_seconds(reply) -> float:
    """The JS walk duration (ms) as seconds; anything unusable means 0.0."""
    try:
        return max(0.0, float(reply or 0)) / 1000.0
    except (TypeError, ValueError):
        return 0.0
