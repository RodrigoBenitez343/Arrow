"""Dev-tools-style element picker for web conditional capture.

Arms a picker in the chain's SHARED browser (the same instance web sequence
nodes and web conditionals use): the user hovers the page (highlight box) and
clicks an element; its locator + frame path come back for the conditional to
store.  Reuses ``locators.js`` for the locator extraction.

The GUI runs this on a worker thread (the same pattern as the web recorder in
``NGUI/graph_elements/node_operations_modules/web_sequence.py``): the picker
blocks until the user selects an element (ESC cancels) or the timeout elapses.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_INJECT_DIR = Path(__file__).parent / "inject"
# Max iframe nesting depth when injecting the picker into the live page tree.
_FRAME_MAX_DEPTH = 4
# Re-inject cadence while waiting (catches frames that load after arming).
_REINJECT_INTERVAL_S = 1.0

try:
    from selenium.webdriver.common.by import By
except Exception:  # pragma: no cover - dependency guard
    By = None


class _Config:
    """Minimal ReplayConfig stand-in (wait_for_element reads element_timeout)."""

    def __init__(self, element_timeout: float):
        self.element_timeout = element_timeout


def _picker_payload() -> str:
    parts = []
    for name in ("locators.js", "picker.js"):
        parts.append((_INJECT_DIR / name).read_text(encoding="utf-8"))
    return "\n".join(parts)


def _inject_all_frames(driver, payload: str, max_depth: int = _FRAME_MAX_DEPTH) -> bool:
    """Inject + arm the picker in EVERY frame of the current document tree.

    Recursive and index-based, so nested and cross-origin frames are covered
    (Selenium can switch into any frame by index, regardless of origin) - CDP
    new-document injection alone does not reach every existing OOPIF.  Returns
    True when the top document took the injection.  Frames inside shadow roots
    are NOT in WebDriver's frame tree - ``_inject_shadow_frames`` covers them.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    ok = _inject_at(driver, payload, 0, max_depth)
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    return ok


# JS: inject + arm the picker into every SAME-ORIGIN iframe reachable only
# through a shadow root (WebDriver cannot enumerate it, so _inject_all_frames
# never reaches it).  Runs in the top document; cross-origin contentDocuments
# are unreadable and stay unsupported.
JS_INJECT_SHADOW_PICKER = """return (function (payload) {
  var injected = 0;
  function walk(root, depth, unreachable) {
    if (!root || depth > 8) return;
    var rootIsShadow = !!(root.getRootNode && root.getRootNode().nodeType === 11);
    var iframes;
    try { iframes = root.querySelectorAll('iframe'); } catch (e) { iframes = []; }
    for (var i = 0; i < iframes.length; i++) {
      var f = iframes[i];
      var cd = null;
      try { cd = f.contentDocument; } catch (e) { cd = null; }
      if (!cd) continue;
      var fUnreachable = unreachable || rootIsShadow;
      if (fUnreachable) {
        try {
          var w = f.contentWindow;
          if (w && !w.__wvpPicker) { w.eval(payload); }
          if (w && w.__wvpPickerStart) { w.__wvpPickerStart(); injected++; }
        } catch (e) {}
      }
      walk(cd, depth + 1, fUnreachable);
    }
    var all;
    try { all = root.querySelectorAll('*'); } catch (e) { all = []; }
    for (var j = 0; j < all.length; j++) {
      var h = all[j];
      if (h.shadowRoot) walk(h.shadowRoot, depth + 1, unreachable);
    }
  }
  walk(document, 0, false);
  return injected;
})(arguments[0]);
"""


def _inject_shadow_frames(driver, payload: str) -> None:
    """Arm the picker in already-loaded shadow-hosted same-origin iframes.

    Best-effort: a strict page CSP can block the in-page ``eval`` - then only
    frames loaded AFTER arming (covered by the per-window CDP registration) get
    the picker.
    """
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        count = driver.execute_script(JS_INJECT_SHADOW_PICKER, payload)
        if count:
            logger.info("Element picker armed in %d shadow-hosted frame(s)", count)
    except Exception as exc:
        logger.debug("Element picker: shadow-frame injection skipped: %s", exc)


def _inject_at(driver, payload: str, depth: int, max_depth: int) -> bool:
    script = payload + "; try { window.__wvpPickerStart && window.__wvpPickerStart(); } catch (e) {}"
    try:
        driver.execute_script(script)
        ok = True
    except Exception:
        ok = False
    if depth >= max_depth:
        return ok
    try:
        count = len(driver.find_elements(By.TAG_NAME, "iframe")) if By is not None else 0
    except Exception:
        count = 0
    for index in range(count):
        try:
            driver.switch_to.frame(index)
        except Exception:
            continue
        _inject_at(driver, payload, depth + 1, max_depth)
        try:
            driver.switch_to.parent_frame()
        except Exception:
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
    return ok


def _window_handles(driver) -> list:
    """Open window handles, or ``[None]`` when the driver exposes none.

    ``[None]`` is a single implicit window (the attached one): the helpers
    then act on the current window without switching - so a stub/simple
    driver still works.
    """
    try:
        handles = list(driver.window_handles)
        if handles:
            return handles
    except Exception:
        pass
    return [None]


def _inject_all_windows(driver, payload: str) -> bool:
    """Arm the picker in every open window (popups included).

    A popup opened by the page is a separate driver target: the CDP
    new-document injection registered for the opener does not reach it, and
    ``_inject_all_frames`` only touches the attached window.  Each window is
    visited, registered for FUTURE documents and injected into its current
    frame tree, then the originally-attached window is restored.  Returns True
    when the originally-attached window took the injection.
    """
    handles = _window_handles(driver)
    try:
        original = driver.current_window_handle
    except Exception:
        original = None
    top_ok = False
    switched = False
    for handle in handles:
        # switch_to.window ACTIVATES the window in Chrome, so the attached
        # window is injected IN PLACE - only a genuinely different window is
        # worth raising (a raise steals the user's focus).
        if handle is not None and handle != original:
            try:
                driver.switch_to.window(handle)
                switched = True
            except Exception:
                continue
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": payload, "runImmediately": True},
            )
        except Exception:
            pass
        ok = _inject_all_frames(driver, payload)
        if handle is None or handle == original:
            top_ok = ok
    if switched and original is not None:
        try:
            driver.switch_to.window(original)
        except Exception:
            pass
    return top_ok


def _poll_result_any_window(driver, allow_switch: bool = False):
    """Read ``__wvpPickerResult``; returns (result, window ordinal).

    The result is read from the ATTACHED window with NO ``switch_to.window``:
    chromedriver ACTIVATES the target window on every switch, so polling the
    windows on a timer stole the user's focus several times a second while they
    were trying to pick - which made the rest of the app (the graph GUI)
    unusable.  A pick made in a frame or a popup is relayed back to the window
    Python armed (``window.top`` postMessage / the opener relay in picker.js),
    so the attached window is enough in practice.  ``allow_switch`` enables a
    SLOW best-effort scan of the OTHER windows (a popup with no opener link
    cannot relay).

    The ordinal is the window's index in ``window_handles`` - the recorded
    window context a later condition replays into (a popup shares the opener's
    host, so URL alone cannot tell them apart).
    """
    result = _read_picker_result(driver)
    if result:
        _clear_picker_result(driver)
        ordinal = _current_ordinal(driver)
        if isinstance(result, dict):
            # The pick names the window it was MADE in: a popup relays its pick
            # to the opener, so the ordinal must come from the source window
            # (a cross-origin frame cannot be attributed - its id is its own).
            source = None if result.get("cross_origin_frame") else result.pop(
                "__win", None)
            resolved = _window_ordinal_for_id(driver, source)
            if resolved is not None:
                ordinal = resolved
        return result, ordinal
    if not allow_switch:
        return None, None
    handles = _window_handles(driver)
    try:
        original = driver.current_window_handle
    except Exception:
        original = None
    for ordinal, handle in enumerate(handles):
        if handle is None or handle == original:
            continue  # already read in place above
        try:
            driver.switch_to.window(handle)
        except Exception:
            continue
        result = _read_picker_result(driver)
        if result:
            _clear_picker_result(driver)
            try:
                driver.switch_to.window(original)
            except Exception:
                pass
            return result, ordinal
    if original is not None:
        try:  # nothing found - never strand the driver on another window
            driver.switch_to.window(original)
        except Exception:
            pass
    return None, None


def _refresh_overlay(driver) -> None:
    """Re-assert the in-page picker + highlight in the attached window.

    Called on EVERY poll: a popup that opened after arming is a NEW top-layer
    entry (a ``<dialog showModal()>`` modal paints above every z-index), so the
    highlight must be rebuilt and re-raised there - otherwise the user sees no
    picker at all over the popup even though it is armed.

    When the attached document has no picker at all (a click navigated it, or a
    popup/frame replaced it), the full index-based walk runs RIGHT NOW instead
    of waiting for the slow cadence - the very next hover is then already
    covered.  ``switch_to.frame(index)`` re-enters cross-origin frames too, so
    nothing is out of reach.
    """
    try:
        alive = driver.execute_script(
            "if (!window.__wvpPicker) return false;"
            "return window.__wvpPickerRefresh ? window.__wvpPickerRefresh() : false;"
        )
    except Exception:
        alive = False
    if alive:
        return
    try:
        payload = _picker_payload()
    except Exception:
        return
    _inject_all_frames(driver, payload)
    _inject_shadow_frames(driver, payload)


def _read_picker_result(driver):
    """``__wvpPickerResult`` of the attached window, or None."""
    try:
        return driver.execute_script("return window.__wvpPickerResult || null;")
    except Exception:
        return None


def _picker_window_id(driver):
    """In-page picker id of the ATTACHED window, or None.

    picker.js stamps every pick with the id of the WINDOW it was made in; the
    owning window can therefore be found even when the pick was relayed to
    another one (a popup posts its pick to its opener).
    """
    try:
        return driver.execute_script("return (window.__wvpPickerWinId || null);")
    except Exception:
        return None


def _window_ordinal_for_id(driver, win_id):
    """Ordinal of the window whose picker id is ``win_id``, or None.

    A popup's pick is relayed to its opener, so reading it there used to record
    the OPENER's ordinal - a replay/form-fill then never re-entered the popup
    ("the popup is ignored").  Returns None when the pick cannot be attributed
    (id unknown / no other window), so the caller keeps the attached ordinal.
    The attached window is always restored, so the scan never strands the
    driver on another window.
    """
    if not win_id:
        return None
    if _picker_window_id(driver) == win_id:
        return _current_ordinal(driver)
    handles = _window_handles(driver)
    if len(handles) < 2:
        return None
    try:
        original = driver.current_window_handle
    except Exception:
        original = None
    found = None
    for ordinal, handle in enumerate(handles):
        if handle is None or handle == original:
            continue
        try:
            driver.switch_to.window(handle)
        except Exception:
            continue
        if _picker_window_id(driver) == win_id:
            found = ordinal
            break
    if original is not None:
        try:
            driver.switch_to.window(original)
        except Exception:
            pass
    return found


def _clear_picker_result(driver) -> None:
    try:
        driver.execute_script("window.__wvpPickerResult = null;")
    except Exception:
        pass


def _current_ordinal(driver):
    """Index of the attached window in ``window_handles``, or None."""
    try:
        return list(driver.window_handles).index(driver.current_window_handle)
    except Exception:
        return None


def _stop_any_window(driver) -> None:
    """Disarm the picker in every window, restoring the attached one.

    The attached window is disarmed IN PLACE (a ``switch_to.window`` activates
    the target and would steal focus); only other windows are visited.
    """
    try:
        driver.execute_script(
            "window.__wvpPickerStop && window.__wvpPickerStop();"
        )
    except Exception:
        pass
    handles = _window_handles(driver)
    try:
        original = driver.current_window_handle
    except Exception:
        original = None
    switched = False
    for handle in handles:
        if handle is None or handle == original:
            continue
        try:
            driver.switch_to.window(handle)
            switched = True
        except Exception:
            continue
        try:
            driver.execute_script(
                "window.__wvpPickerStop && window.__wvpPickerStop();"
            )
        except Exception:
            continue
    if switched and original is not None:
        try:
            driver.switch_to.window(original)
        except Exception:
            pass


def _shared_driver(chain_key: Optional[str]):
    from .session import SHARED_WEB_SCOPE, ensure_workbench_session

    return ensure_workbench_session(chain_key=chain_key or SHARED_WEB_SCOPE)


def pick_element(timeout: float = 120.0, chain_key: Optional[str] = None,
                 cancel: Optional[Any] = None) -> Optional[dict[str, Any]]:
    """Arm the picker in the shared browser and wait for a selection.

    Returns the picked element dict (locator + frame_path + cross_origin_frame),
    ``{'cancelled': True}`` when the user pressed ESC (or ``cancel`` - a
    ``threading.Event`` - was set), or None on timeout/error.  ``cancel`` lets a
    dialog tear down without waiting out the timeout; the picker is disarmed and
    the call returns within one poll.
    """
    payload = _picker_payload()
    try:
        driver = _shared_driver(chain_key)
    except Exception as exc:
        logger.error("Element picker: cannot open the shared browser: %s", exc)
        return None

    # Cover FUTURE documents (navigations) via CDP, and the ALREADY-loaded
    # document tree - every frame, nested / cross-origin included - via a
    # recursive Selenium injection (CDP new-document injection does not reach
    # every existing OOPIF).  This runs per WINDOW, so a popup already open is
    # armed too.
    if not _inject_all_windows(driver, payload):
        logger.error("Element picker: injection failed on the top document")
        return None
    _inject_shadow_frames(driver, payload)

    logger.info("Element picker armed - click an element in the browser (ESC cancels)")
    deadline = time.time() + max(5.0, timeout)
    next_reinject = time.time() + _REINJECT_INTERVAL_S
    try:
        known_windows = set(driver.window_handles)
    except Exception:
        known_windows = set()

    def _collected(result, ordinal):
        if not result:
            return None
        if isinstance(result, dict) and result.get("cancelled"):
            return {"cancelled": True}
        if isinstance(result, dict) and ordinal is not None:
            result.setdefault("_window_ordinal", ordinal)
        return result

    while time.time() < deadline:
        if cancel is not None and cancel.is_set():
            logger.info("Element picker cancelled")
            _stop_any_window(driver)
            return {"cancelled": True}
        picked = _collected(*_poll_result_any_window(driver))
        if picked:
            return picked
        # Cheap every-poll re-assert: the highlight exists again and sits in the
        # CURRENT top layer, so a popup/frame that appeared since the last poll
        # is covered the moment the user moves onto it.
        _refresh_overlay(driver)
        if time.time() >= next_reinject:
            # A pick in a window that could not relay (no opener link) is only
            # found by ACTIVATING it - so scan on the slow cadence, never on
            # every poll, or the poll steals the user's focus mid-pick.
            picked = _collected(*_poll_result_any_window(driver, allow_switch=True))
            if picked:
                return picked
            # Re-inject so frames that mount AFTER arming (dynamic pages build
            # their iframes late) still get the picker - the attached window
            # only, unless a NEW window appeared (a popup is a new driver target
            # CDP did not cover and must be armed once).
            _inject_all_frames(driver, payload)
            _inject_shadow_frames(driver, payload)
            try:
                handles = set(driver.window_handles)
            except Exception:
                handles = known_windows
            if handles - known_windows:
                _inject_all_windows(driver, payload)
                known_windows = handles
            next_reinject = time.time() + _REINJECT_INTERVAL_S
        time.sleep(0.2)
    logger.warning("Element picker: timed out after %.0fs", timeout)
    _stop_any_window(driver)
    return None


def capture_text(locator_raw: Optional[Any] = None, chain_key: Optional[str] = None) -> str:
    """Visible text of a picked element (or the whole page when no locator).

    Backs the "Capture text" button: pick an element, then read its innerText
    (or the page's when no element is given) into the condition's text field.
    """
    try:
        driver = _shared_driver(chain_key)
    except Exception as exc:
        logger.error("Element picker: cannot open the shared browser: %s", exc)
        return ""

    if locator_raw:
        data = locator_raw
        if isinstance(locator_raw, str):
            try:
                data = json.loads(locator_raw)
            except (TypeError, ValueError):
                data = None
        if isinstance(data, dict):
            from .events import Event, Locator
            from . import actions as web_actions

            locator = Locator.from_dict(data)
            if locator is not None:
                # Keep the recorded document: a frame-relative pick must be read
                # in its own frame.  A legacy locator (no scope key) keeps the
                # old behaviour and searches every frame.
                scope_known = "_frame_path" in data
                event = Event(
                    type="element", ts=0.0, locator=locator,
                    frame_path=list(data.get("_frame_path") or []),
                    cross_origin_frame=bool(data.get("_cross_origin")) or not scope_known,
                )
                # A shadow-root target (or a shadow-hosted frame) is invisible to
                # WebDriver - read its text in-page, or the capture came back
                # empty and the condition was built with no text to match.
                if web_actions.needs_deep_dispatch(event):
                    deep = web_actions.deep_element_text(driver, event)
                    if deep:
                        return deep
                try:
                    element = web_actions.wait_for_element(driver, event, _Config(5.0))
                    if element is not None:
                        return element.text or ""
                except Exception:
                    pass

    try:
        return driver.execute_script(
            "return document.body ? document.body.innerText : ''"
        ) or ""
    except Exception:
        return ""
