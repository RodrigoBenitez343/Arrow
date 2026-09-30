from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)
"""Python-side web recording orchestration.

Flow:
1. Launch a headed undetected_chromedriver session (recording needs a visible
   browser - you drive it with a real mouse/keyboard).
2. Register the injected payload (locators.js + recorder.js) via CDP
   Page.addScriptToEvaluateOnNewDocument so every new document and frame gets
   it, including across navigations and SPA route changes.
3. Poll the in-page event buffer (window.__webversionpw_events) every 200 ms
   and drain it into typed Event objects.
4. Stop when any of these happens:
   - ESC anywhere (global hook via `keyboard`, plus the in-page listener), or
   - the browser window is closed by the user.
   We then settle for in-flight cross-origin postMessages, run the in-page
   flush, and save the session as a LoOper-style JSON file (metadata + actions).

No start URL required: the recording browser opens on its default page.  A
session that starts there and navigates to a site through the address bar or
the new-tab search records the landing page as ONE explicit "Open page"
action (the typed URL is browser chrome - never visible to the page recorder,
and the new-tab search box only mirrors fragments of it, so those keys are
unreliable and are not replayed).  Everything after the landing is recorded
as clicks/typing/keys on the real page: SPA route changes stay page state and
button/element-driven dynamic chains replay as recorded.  Interactions that
land on Chrome-internal pages (the new-tab page's search box, settings, ...)
are not replayable against a real site - they are dropped at save time or
collapsed into the cold-start Open-page action.

The recording browser is PERSISTENT and APP-WIDE (the "workbench"):
recordings reuse the long-lived Chrome of the app's single shared durable
profile, so a new web sequence continues in the browser where the previous
recording left off (cookies, tabs, history) and the logins made there
survive app restarts in every run mode - GUI, agent, scheduler, CLI.  ESC
stops and saves but leaves the browser open; closing the window ends the
session (the next recording relaunches on the shared profile - pages are
never restored, only cookies/history).
"""

import threading
import time
from pathlib import Path
from typing import Optional

from .events import Event, save_session
from .session import (
    SHARED_WEB_SCOPE,
    ensure_workbench_session,
    release_workbench,
)
from .chrome_capture import ChromeKeyHook, ChromeMouseHook

# Guarded so the package imports even without the web dependencies; the
# driver factory (web/session.py) raises the authoritative error at use time.
try:
    from selenium.common.exceptions import NoSuchWindowException
    from selenium.webdriver.common.by import By
except Exception:  # pragma: no cover - dependency guard
    NoSuchWindowException = Exception
    By = None

_INJECT_DIR = Path(__file__).parent / "inject"
# Two-tier poll for the in-page event buffer.  A click/Enter that navigates
# can destroy the page (and its in-memory buffer) faster than a slow poll
# drains it - and the sessionStorage mirror only survives SAME-origin
# navigation, so a cross-origin jump (clicking a search result to another
# site) loses the click entirely if the drain is late.  A cheap every-10ms
# "does the CURRENT window have events waiting?" check triggers the
# expensive full drain (all frames + the storage mirror) immediately, long
# before the destination page commits; the full drain also still runs on its
# own cadence so chrome:// WebUI pages regain their recorder at most a poll
# later.
#
# Polling NEVER switches windows: chromedriver raises the target window on
# switch_to.window, so a steady-state scan across every open window makes
# the recorder fight the user's own focus - a popup and its opener flip
# back and forth, every raise firing a spurious in-page focus action that
# keeps the buffer dirty ("recorder glitches until ESC").  Window changes
# are followed ONCE per event instead (see _follow_active_window).
_POLL_INTERVAL_S = 0.1  # full-drain cadence (max latency for idle events)
_FAST_POLL_INTERVAL_S = 0.01  # dirty-check cadence (closes the nav race)
_SETTLE_AFTER_STOP_S = 0.4

# Max |dt| (ms) between an OS-captured key and its in-page twin for them to
# count as the SAME user keypress (see _dedupe_os_chrome_keys).  The page and
# the keyboard hook share the epoch-ms timebase and the poll drains both
# within one cycle, so a few hundred ms is plenty of slack.
_DEDUPE_OS_KEY_WINDOW_MS = 300.0

# Max |dt| (ms) between an OS-captured chrome-band click and the page's own
# click on the same element for them to count as the SAME user click (see
# _dedupe_os_chrome_clicks).  The chrome band covers the bookmark bar / top
# strip; a click there that ALSO reached the page is page-owned and the OS
# twin must be dropped, or replay would click twice.
_DEDUPE_OS_CLICK_WINDOW_MS = 600.0


def _recorder_fingerprint() -> str:
    """Content hash of the injected scripts - identifies a stale in-page copy."""
    h = hashlib.sha1()
    for name in ("locators.js", "recorder.js"):
        try:
            h.update((_INJECT_DIR / name).read_bytes())
        except Exception:
            pass
    return h.hexdigest()[:12]


def _payload() -> str:
    # Handed to recorder.js, which stamps __wvpRecorderFingerprint only when it
    # ACTUALLY initialises (after its own __wvpRecorder guard) - so evaluating
    # the payload into a document that already runs an older recorder does not
    # falsely mark it current (see _inject_or_refresh).
    parts = ["window.__wvpRecorderPayloadFp = %r;" % _recorder_fingerprint()]
    for name in ("locators.js", "recorder.js"):
        parts.append((_INJECT_DIR / name).read_text(encoding="utf-8"))
    return "\n".join(parts)


def _inject_or_refresh(driver, payload: str) -> None:
    """Make the CURRENT document run the up-to-date recorder.

    ``_register_cdp_injection`` only covers documents loaded AFTER it runs, and
    recorder.js keeps its own ``__wvpRecorder`` guard, so re-evaluating the
    payload into an already-loaded document is a no-op.  A long-lived SPA tab
    (LinkedIn, Gmail, ...) therefore keeps whatever recorder it first loaded.
    A stale in-page build is refreshed IN PLACE - never by reloading the page:
    a reload wipes the user's page state (the very modal a run is working on)
    and flashes the document WHITE, which made every action crawl.
    """
    want = _recorder_fingerprint()
    try:
        state = driver.execute_script(
            "return {ready: !!window.__wvpRecorder,"
            " fp: window.__wvpRecorderFingerprint || null,"
            " refreshable: typeof window.__wvpRecorderRefresh === 'function'};"
        )
    except Exception as exc:
        logger.warning("Could not read the in-page recorder state: %s", exc)
        return
    state = state or {}
    if state.get("fp") == want:
        logger.info("In-page web recorder is up to date (fp=%s)", want)
        return
    if not state.get("ready"):
        # No recorder in this document yet - inject it directly.
        try:
            driver.execute_script(payload + "; true")
        except Exception:
            logger.warning("Could not inject the web recorder into the current page")
        else:
            logger.info("Injected the web recorder into the current page (fp=%s)", want)
        return
    # Refresh IN PLACE.  Reloading here wiped the document (the very popup the
    # run is working on) and flashed it WHITE on every action - the recorder
    # already loaded does its job, so it is re-armed when it exposes a hook and
    # otherwise left running (the payload re-eval only adds the newest globals).
    if state.get("refreshable"):
        try:
            driver.execute_script("window.__wvpRecorderRefresh();")
            logger.info("Re-armed the in-page web recorder in place (fp=%s)", want)
            return
        except Exception:
            pass
    logger.warning(
        "In-page web recorder is out of date (in-page fp=%s, want=%s) - "
        "keeping it: NO reload (a reload would wipe the page state and flash "
        "the document white on every action)",
        state.get("fp"), want,
    )
    try:
        driver.execute_script(payload + "; true")
    except Exception:
        pass


def _stamp_scope(evt: Event, window_ordinal: Optional[int],
                 index_path: tuple[int, ...]) -> None:
    """Stamp an event with the window/frame STRUCTURE it was captured in.

    The recorded ordinal (the window's index in ``window_handles``) and the
    index-based frame chain are the deterministic context replay re-enters -
    url-host matching cannot tell a same-host popup from its opener, and the
    locator-based frame_path cannot describe cross-origin frames.
    """
    if window_ordinal is not None:
        evt.window_ordinal = int(window_ordinal)
    if index_path:
        evt.frame_index_path = [int(i) for i in index_path]


def _current_window_ordinal(driver) -> Optional[int]:
    """Index of the attached window in ``window_handles`` (0 = opener)."""
    try:
        handles = list(driver.window_handles)
        current = driver.current_window_handle
        return handles.index(current) if current in handles else None
    except Exception:
        return None


def _drain(driver) -> list[Event]:
    """Atomically drain the top-frame event buffer into typed Events.

    The in-memory buffer (``window.__webversionpw_events``) dies with its
    document, so the recorder ALSO mirrors every event into sessionStorage
    (survives same-origin navigation).  This reads BOTH: the live buffer
    first, then the durable mirror - which is how the click that CAUSED a
    navigation is recovered from the next document instead of being lost.
    Overlapping events (mirror + memory hold the same ones) are deduped by
    (type, ts).
    """
    raw = driver.execute_script(
        "return (function () {"
        "  var evts = (window.__webversionpw_events || []).splice(0);"
        "  if (window.self === window.top) {"
        "    try {"
        "      var v = sessionStorage.getItem('looper.web.events.top');"
        "      if (v) {"
        "        var log = JSON.parse(v);"
        "        sessionStorage.removeItem('looper.web.events.top');"
        "        if (log instanceof Array) evts = evts.concat(log);"
        "      }"
        "    } catch (e) {}"
        # WebDriver cannot see frames inside shadow roots; the top document
        # collects their events itself (see recorder.js __wvpDrainShadow).
        "    try {"
        "      if (window.__wvpDrainShadow) evts = evts.concat(window.__wvpDrainShadow());"
        "    } catch (e) {}"
        "  }"
        "  return evts;"
        "})()"
    )
    events = [Event.from_dict(item) for item in (raw or [])]
    # The mirror and the live buffer hold the SAME events for the current
    # document - keep one copy per (type, ts) so nothing replays twice.
    seen: set[tuple[str, float]] = set()
    out: list[Event] = []
    for evt in events:
        key = (evt.type, round(evt.ts, 1))
        if key in seen:
            continue
        seen.add(key)
        out.append(evt)
    return out


# Same-origin iframes hosted INSIDE a shadow root are invisible to WebDriver's
# frame tree AND to the CDP new-document injector (an out-of-process iframe is
# its own target), so a frame a CLICK OPENS mid-session was never instrumented
# and its actions were silently missed - "the recorder cannot reach it".
# recorder.js's own __wvpDrainShadow can only collect a buffer that exists, so
# the payload is evaluated into those frames too (same walk the picker uses),
# re-asserted on a cadence by the poll loop.
JS_INJECT_SHADOW_RECORDER = """return (function (payload) {
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
          if (w && !w.__wvpRecorder) { w.eval(payload); injected++; }
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
# Shadow-frame re-injection cadence: the walk is a full DOM traversal, so it is
# not run on every (fast) drain.
_SHADOW_INJECT_INTERVAL_S = 2.0
_last_shadow_inject = 0.0


def _inject_shadow_frames(driver, payload: str) -> None:
    """Instrument same-origin iframes reachable only through a shadow root.

    Best-effort: a strict page CSP can block the in-page ``eval``, and a
    cross-origin frame's window is unreachable from here either way.
    """
    global _last_shadow_inject
    now = time.time()
    if now - _last_shadow_inject < _SHADOW_INJECT_INTERVAL_S:
        return
    _last_shadow_inject = now
    try:
        driver.switch_to.default_content()
    except Exception:
        pass
    try:
        count = driver.execute_script(JS_INJECT_SHADOW_RECORDER, payload)
        if count:
            logger.info("Recorder injected into %d shadow-hosted frame(s)", count)
    except Exception as exc:
        logger.debug("Shadow-frame injection skipped: %s", exc)


def _ensure_injected(driver, payload: str) -> None:
    """Belt-and-braces re-inject for the top document (CDP covers new docs)."""
    try:
        ready = driver.execute_script("return !!window.__wvpRecorder")
        if not ready:
            driver.execute_script(payload + "; true")
    except Exception:
        pass


def _any_buffered(driver) -> bool:
    """Cheap dirty check: does the CURRENT window have events waiting?

    Only reads the in-memory buffer of the window the recorder is attached
    to - never switches windows (chromedriver raises the target window on
    switch_to.window, so a cross-window scan would flip popup/opener focus
    back and forth).  Returns True when the window is gone so the caller's
    full cycle detects the close.
    """
    try:
        return bool(driver.execute_script(
            "return !!(window.__webversionpw_events && "
            "window.__webversionpw_events.length)"
        ))
    except Exception:
        return True  # window closed / session lost - run the full cycle


def _register_cdp_injection(driver, payload: str) -> None:
    """Register the injector for FUTURE documents of the attached target.

    CDP new-document injection is registered per target (window), so every
    window the recorder attaches to needs its own registration - a popup
    opened after recording started never saw the start-of-session
    registration and would otherwise record nothing.
    """
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": payload}
        )
    except Exception as exc:  # CDP unavailable - fall back to poll-time re-injection
        logger.warning(
            "CDP pre-injection unavailable (%s); relying on poll-time injection", exc
        )


# The in-page recording overlay (highlight box + modifier cheat sheet) is gated
# on this sessionStorage flag: it is per-origin and survives same-origin
# navigation, so it can be turned ON for the whole recording and OFF at stop -
# the overlay then never lingers on later navigations.
_OVERLAY_STORAGE_KEY = "looper.web.overlay"


def _set_overlay(driver, on: bool) -> None:
    """Turn the in-page recording overlay on/off in the current document.

    Also calls the in-page hook so a RE-RECORD on an already-instrumented page
    (whose recorder fingerprint is current, so the payload is not re-run) still
    (re)creates / removes the overlay.
    """
    hook = "__wvpOverlayOn" if on else "__wvpOverlayOff"
    try:
        driver.execute_script(
            "try { sessionStorage.setItem(arguments[0], arguments[1]); } catch (e) {}"
            "try { window[arguments[2]] && window[arguments[2]](); } catch (e) {}",
            _OVERLAY_STORAGE_KEY, "on" if on else "off", hook,
        )
    except Exception:
        pass


def _disable_overlay_all(driver) -> None:
    """Switch the overlay OFF in every open window (called at stop)."""
    try:
        handles = list(driver.window_handles)
        original = driver.current_window_handle
    except Exception:
        return
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            _set_overlay(driver, False)
        except Exception:
            continue
    try:  # restore the attached window so later reads (final_url) stay correct
        driver.switch_to.window(original)
    except Exception:
        pass


def _follow_active_window(
    driver, payload: str, known_handles: set[str]
) -> tuple[set[str], bool]:
    """Re-anchor the recorder to the window the user is actually in.

    Steady-state polling never switches windows (each switch_to.window RAISES
    the target window in Chrome, which flips popup/opener focus back and
    forth and records spurious in-page focus actions).  Window changes are
    followed here instead - once per event, on the full-drain cadence, right
    after the current window was drained:

    - A window appeared that was not there before (a click just opened a
      popup): attach to it.  The user gesture opened and focused it, so it
      IS the current focused window; its documents need the injector
      registered (the start-of-session registration was per-target).
    - The attached window closed: attach to the first remaining window
      (Chrome focused it when its own window closed).
    - No windows remain (or the session died): window_gone=True.

    Returns (new_known_handles, window_gone).
    """
    try:
        handles = list(driver.window_handles)
    except Exception:
        return set(), True
    if not handles:
        return set(), True
    new_set = set(handles)
    try:
        current = driver.current_window_handle
    except Exception:
        current = None
    if current is not None and current in new_set:
        added = [h for h in handles if h not in known_handles]
        if added and known_handles:
            # A popup/tab was just created.  The opener's click that caused
            # it was drained by the caller before this ran, so switching away
            # loses nothing.  Attach to the first new window and register the
            # injector for its documents.
            for h in added:
                if h == current:
                    continue
                try:
                    driver.switch_to.window(h)
                    _set_overlay(driver, True)
                    _register_cdp_injection(driver, payload)
                except Exception:
                    pass
                break
        return new_set, False
    # The attached window closed - Chrome already focused one of the rest.
    try:
        driver.switch_to.window(handles[0])
        _set_overlay(driver, True)
        _register_cdp_injection(driver, payload)
    except Exception:
        pass
    return new_set, False


def _foreground_window() -> Optional[tuple[int, tuple[int, int, int, int]]]:
    """(pid, rect) of the OS foreground window, or None (Win32 only)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return pid.value, (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        return None


def _match_handle_by_os_rect(driver, rect: tuple[int, int, int, int]) -> Optional[str]:
    """The driver window whose OS rectangle is `rect` (physical px), or None.

    Compares each window's JS geometry (screenX/Y, outer size - DIP) against
    the physical rect, sweeping common DPI scale factors (mirrors
    session._match_session_window: window.screenX reports DIPs, Win32 rects
    are physical px).  The CURRENT window is checked first WITHOUT switching
    - probing raises windows, and that must never happen in a steady-state
    poll.  Only when the rect is not the attached window are the others
    probed (a genuine user-move moment); the original window is restored if
    nothing matches.  Ends attached to the matched window.
    """
    pl, pt, pr, pb = rect
    pw, ph = pr - pl, pb - pt

    def _matches(x, y, w, h) -> bool:
        for scale in (1.0, 1.25, 1.5, 2.0, 0.75):
            if (abs(int(w) * scale - pw) <= 20
                    and abs(int(h) * scale - ph) <= 20
                    and abs(int(x) * scale - pl) <= 60
                    and abs(int(y) * scale - pt) <= 60):
                return True
        return False

    try:
        handles = list(driver.window_handles)
        current = driver.current_window_handle
    except Exception:
        return None
    try:
        x, y, w, h = driver.execute_script(
            "return [window.screenX, window.screenY, "
            "window.outerWidth, window.outerHeight]"
        )
        if _matches(x, y, w, h):
            return current  # already on the window the user is in - no probe
    except Exception:
        pass
    for handle in handles:
        if handle == current:
            continue
        try:
            driver.switch_to.window(handle)
            x, y, w, h = driver.execute_script(
                "return [window.screenX, window.screenY, "
                "window.outerWidth, window.outerHeight]"
            )
        except Exception:
            continue
        if _matches(x, y, w, h):
            return handle
    try:  # nothing matched - restore the original window
        driver.switch_to.window(current)
    except Exception:
        pass
    return None


def _follow_user_window(driver, payload: str, browser_pid: Optional[int]) -> bool:
    """Attach the recorder to the browser window the user is actually in.

    The handle-set follower (_follow_active_window) reacts to windows OPENING
    and CLOSING; it cannot see the user move into an ALREADY-OPEN window - e.g.
    recording the next step of a chain whose sign-in popup is still open from
    the previous step: no new handle appears, so the recorder stays on the
    opener and the popup typing is never captured.  Chrome raises whichever
    window the user interacts with, so polling the OS foreground window tells
    which one that is.  Only switches when the foreground window is one of
    THIS browser's windows (same pid) and differs from the attached one -
    windows of other applications never trigger a switch.  Returns True when
    the driver was moved.
    """
    if os.name != "nt" or not browser_pid:
        return False
    try:
        if len(driver.window_handles) < 2:
            return False
    except Exception:
        return False
    fg = _foreground_window()
    if fg is None or fg[0] != browser_pid:
        return False
    try:
        original = driver.current_window_handle
    except Exception:
        original = None
    # The matcher leaves the driver attached to the window it finds (it had
    # to switch there to probe its geometry); compare against the handle we
    # had BEFORE matching, so "already on the focused window" is detected
    # without a spurious re-switch.
    matched = _match_handle_by_os_rect(driver, fg[1])
    if matched is None or matched == original:
        return False
    try:
        _set_overlay(driver, True)
        _register_cdp_injection(driver, payload)
        try:
            driver.execute_script(payload + "; true")
        except Exception:
            pass
    except Exception:
        return False
    logger.info(
        "Following user to the focused browser window (%s)", matched
    )
    return True


def _drain_all(driver, payload: str):
    """Drain every open tab AND every iframe inside them.

    Returns (events, window_gone): window_gone is True when the browser was
    closed, in which case partial results are still returned.  Iframes are
    drained recursively by index (WebDriver can switch into ANY frame,
    including cross-origin / out-of-process embeds) so interactions inside
    embedded sites are recorded - not just the top document.
    """
    drained: list[Event] = []
    try:
        handles = driver.window_handles
    except NoSuchWindowException:
        return drained, True
    if not handles:
        # All windows closed (browser may still be alive): probe for a dead session.
        try:
            driver.execute_script("return 1")
        except NoSuchWindowException:
            return drained, True
        return drained, False
    for handle in list(handles):
        try:
            driver.switch_to.window(handle)
        except NoSuchWindowException:
            return drained, True
        except Exception:
            continue
        try:
            _inject_shadow_frames(driver, payload)
            drained.extend(
                _drain_tree(driver, payload, 0, _current_window_ordinal(driver), ())
            )
        except Exception:
            continue
    return drained, False


def _drain_tree(driver, payload: str, depth: int = 0,
                window_ordinal: Optional[int] = None,
                index_path: tuple[int, ...] = ()) -> list[Event]:
    """Drain the current frame and every nested iframe, then restore context.

    Frame switching is index-based so cross-origin frames work too.  Each
    frame keeps its own ``window.__webversionpw_events`` buffer (see
    recorder.js), so the top-level postMessage aggregation is no longer
    needed - every document is drained directly.  Every drained event is
    stamped with the window ordinal and the index-based frame chain it was
    reached through, so replay can re-enter the exact window/frame.
    """
    drained: list[Event] = []
    if depth > 8:
        return drained
    try:
        _ensure_injected(driver, payload)
        for evt in _drain(driver):
            _stamp_scope(evt, window_ordinal, index_path)
            drained.append(evt)
    except Exception:
        pass
    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        return drained
    for index in range(len(frames)):
        try:
            driver.switch_to.frame(index)
        except Exception:
            continue
        try:
            drained.extend(_drain_tree(driver, payload, depth + 1,
                                       window_ordinal, index_path + (index,)))
        finally:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                break  # frame context lost - cannot continue safely
    return drained


def _flush_all(driver, payload: str) -> list[Event]:
    """Final in-page flush across every open tab AND every nested iframe."""
    flushed: list[Event] = []
    try:
        handles = driver.window_handles
    except NoSuchWindowException:
        return flushed
    for handle in list(handles):
        try:
            driver.switch_to.window(handle)
        except Exception:
            continue
        try:
            flushed.extend(
                _flush_tree(driver, payload, 0, _current_window_ordinal(driver), ())
            )
        except Exception:
            continue
    return flushed


def _flush_tree(driver, payload: str, depth: int = 0,
                window_ordinal: Optional[int] = None,
                index_path: tuple[int, ...] = ()) -> list[Event]:
    """Flush the current frame and every nested iframe, then restore context."""
    flushed: list[Event] = []
    if depth > 8:
        return flushed
    try:
        raw = driver.execute_script(
            "return window.__wvpFlush ? window.__wvpFlush() "
            ": (window.__webversionpw_events || []).splice(0)"
        )
        for item in (raw or []):
            evt = Event.from_dict(item)
            _stamp_scope(evt, window_ordinal, index_path)
            flushed.append(evt)
    except Exception:
        pass
    try:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        return flushed
    for index in range(len(frames)):
        try:
            driver.switch_to.frame(index)
        except Exception:
            continue
        try:
            flushed.extend(_flush_tree(driver, payload, depth + 1,
                                       window_ordinal, index_path + (index,)))
        finally:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                break
    return flushed


def _consume_drained(actions: list[Event], drained: list[Event]) -> bool:
    """Append every drained event and report whether a stop control was seen.

    ``stop``/``bridge_ready`` are internal control events filtered out before
    saving - they must never truncate the append.  A stale ``stop`` already in
    the persistent workbench page buffer would otherwise discard the real
    actions drained after it (signaled live but never saved, producing
    "0 actions" sessions).
    """
    actions.extend(drained)
    return any(evt.type == "stop" for evt in drained)


# Chrome-internal page URL prefixes: the new-tab page (chrome://new-tab-page),
# settings/history/... WebUI documents.  Interactions recorded there carry a
# WebUI-internal DOM and replay positions the browser on the session's real
# start page instead of re-doing them, so they can never replay meaningfully.
_CHROME_INTERNAL_PREFIXES = (
    "chrome://", "chrome-untrusted://", "chrome-extension://",
    "devtools://", "edge://", "about:",
)


def _is_chrome_internal_url(url: Optional[str]) -> bool:
    """True when the event was recorded inside a Chrome-internal document.

    Only DOM-captured events carry a url (recorder.js stamps location.href);
    OS-captured chrome events (back/forward, Ctrl+T, toolbar clicks) have
    url=None and are never internal - they are page-independent by design.
    """
    if not url:
        return False
    low = url.lower()
    return low.startswith(_CHROME_INTERNAL_PREFIXES)


def _drop_chrome_internal_actions(actions: list[Event]) -> list[Event]:
    """Drop actions recorded inside Chrome-internal documents (new-tab page).

    The new-tab search box / tiles look recordable but their locators are
    WebUI-internal and never exist on a real site; sessions that mixed them
    with real-page actions made replays click/type into random page state
    ("executed some actions, not the whole sequence").  The navigation-era
    noise is replaced by the cold-start ``navigate`` action
    (see ``_collapse_cold_start_navigation``) before this drop runs.
    """
    dropped = [a for a in actions if _is_chrome_internal_url(a.url)]
    if not dropped:
        return actions
    logger.info(
        "Dropped %d action(s) recorded on Chrome-internal pages "
        "(new-tab/settings - not replayable on a real site)", len(dropped),
    )
    kept = [a for a in actions if not _is_chrome_internal_url(a.url)]
    return kept


def _is_real_http_url(url: Optional[str]) -> bool:
    """True for an http(s) page URL - a real site, never browser-internal."""
    return bool(url) and url.lower().startswith(("http://", "https://"))


def _is_cold_start_noise(evt: Event) -> bool:
    """True when an event belongs to a recording's cold-start era.

    Every fresh workbench browser opens on the default new-tab page, so the
    first real user action is preceded by browser setup: interactions on
    Chrome-internal pages (url stamped) and OS-captured chrome events (no url:
    address-bar keys, toolbar clicks).  Neither can replay against a real site.
    """
    if _is_chrome_internal_url(evt.url):
        return True
    if (evt.context or {}).get("chrome"):
        if evt.type == "chrome":
            return True
        if evt.type == "key" and evt.key in ("Enter", "Tab", "Backspace", "Delete"):
            return True
    return False


def _collapse_cold_start_navigation(actions: list[Event]) -> list[Event]:
    """Turn a recording's cold-start era into one explicit ``navigate`` action.

    The browser opens on its default page; the user navigates to a site
    through the address bar or the new-tab search box.  The typed URL is
    browser chrome (invisible to the page recorder - and the new-tab search
    box only mirrors fragments of it into the DOM, so even the captured keys
    are unreliable).  The navigation itself is real and must survive: when a
    run of pure cold-start noise precedes the first real-page action, the
    noise is replaced by a single navigate to that page, so replay opens the
    site exactly where the user did - as a recorded action, never an implicit
    jump.  Sessions that start directly on a real page (the parked workbench
    browser) are untouched: their element-driven chain replays as recorded.
    """
    first_real = next(
        (i for i, a in enumerate(actions) if _is_real_http_url(a.url)), None
    )
    if first_real is None or first_real == 0:
        return actions  # no cold start (no real page, or it was already there)
    prefix = actions[:first_real]
    if not all(a.url is None or _is_chrome_internal_url(a.url) for a in prefix):
        return actions  # prefix contains real-page actions - not a cold start
    if not any(_is_cold_start_noise(a) for a in prefix):
        return actions  # nothing navigation-shaped in the prefix
    nav = Event(
        type="navigate",
        ts=actions[first_real].ts - 1.0,
        url=actions[first_real].url,
    )
    logger.info(
        "Recorded the cold-start navigation as one action: open %s "
        "(address-bar / new-tab-search text is browser chrome, not replayable)",
        actions[first_real].url,
    )
    return [nav] + actions[first_real:]


def _collapse_mid_session_navigation(actions: list[Event]) -> list[Event]:
    """Collapse address-bar eras BETWEEN real pages into one ``navigate``.

    Once a session is on a real page the user can still move on through the
    address bar (Ctrl+L, type a URL, Enter) or a new tab.  Those chrome-era
    actions are url-less OS events (or Chrome-internal-page events) that can
    never drive replay to the new page - replaying the raw keys would make
    the browser navigate wherever its own omnibox suggests.  When such a run
    separates two real pages with DIFFERENT urls and contains a navigation
    trigger, it is replaced by one navigate to the new page; same-page runs
    (a quick Ctrl+T/Ctrl+W detour) stay untouched.
    """
    result: list[Event] = []
    prev_real_url: Optional[str] = None
    idx = 0
    while idx < len(actions):
        evt = actions[idx]
        if _is_real_http_url(evt.url):
            result.append(evt)
            prev_real_url = evt.url
            idx += 1
            continue
        end = idx
        while end < len(actions) and not _is_real_http_url(actions[end].url):
            end += 1
        run = actions[idx:end]
        if (
            prev_real_url is not None and end < len(actions)
            and actions[end].url != prev_real_url
            and any(_is_cold_start_noise(e) for e in run)
        ):
            logger.info(
                "Recorded a mid-session address-bar navigation as one action: "
                "open %s", actions[end].url,
            )
            result.append(
                Event(type="navigate", ts=run[0].ts, url=actions[end].url)
            )
            idx = end
            continue
        result.extend(run)
        idx = end
    return result


def _finalize_actions(
    actions: list[Event],
    *,
    saw_chrome_enter: bool = False,
    final_url: Optional[str] = None,
    initial_url: Optional[str] = None,
) -> list[Event]:
    """The record-time cleanup pipeline shared by the capture paths.

    clean -> sort -> OS-twin dedupe -> cold-start collapse -> Chrome-internal
    drop.  A session that ends with only browser-chrome noise still becomes a
    real session when the user demonstrably navigated during the recording and
    the browser is now on a real page: the destination is saved as one
    navigate action (the typed URL itself was never visible to capture).  The
    navigation is inferred from the observable transition - ``initial_url``
    (the page the browser was on when recording started) being a
    Chrome-internal/default page while ``final_url`` is a real http(s) page -
    not from incidental signals like whether the OS hook happened to capture
    the Enter.
    """
    actions = _clean_actions(actions)
    actions.sort(key=lambda a: a.ts)
    # A pair-marked repeating action supersedes the click that only NAMED the
    # element (see the web recorder's pair derivation): keep one row action.
    actions = _drop_superseded_pair_marks(actions)
    # Chrome-captured bare keys (Enter/Tab/...) that also reached the page are
    # page-owned - drop the OS twin so replay never presses the key twice.
    actions = _dedupe_os_chrome_keys(actions)
    # Chrome-band clicks (bookmark bar / top strip) that ALSO reached the page
    # are page-owned too - drop the OS twin so replay never clicks twice.
    actions = _dedupe_os_chrome_clicks(actions)
    actions = _collapse_cold_start_navigation(actions)
    actions = _collapse_mid_session_navigation(actions)
    # Interactions on Chrome-internal pages (the new-tab search box, tiles,
    # settings) can never replay against a real site.  This drop runs AFTER
    # the OS dedupes so an internal-page key's OS twin is still matched
    # against its DOM twin before both disappear.
    had_actions = bool(actions)
    actions = _drop_chrome_internal_actions(actions)
    page_actions = [a for a in actions if a.url]
    if not page_actions:
        started_internal = _is_chrome_internal_url(initial_url)
        if started_internal and _is_real_http_url(final_url):
            # The browser left its default page during the recording and no
            # in-page action was captured: the user navigated (address bar /
            # new-tab search) and stopped.  The landing page is the session.
            actions = [
                Event(type="navigate", ts=time.time() * 1000.0, url=final_url)
            ]
            logger.info(
                "Recorded the cold-start navigation to %s as the session's "
                "only action", final_url,
            )
        elif saw_chrome_enter and _is_real_http_url(final_url):
            # Legacy fallback: initial page unknown but a chrome Enter was
            # captured - same rescue, same landing.
            actions = [
                Event(type="navigate", ts=time.time() * 1000.0, url=final_url)
            ]
            logger.info(
                "Recorded the cold-start navigation to %s as the session's "
                "only action", final_url,
            )
        else:
            if had_actions:
                logger.warning(
                    "No replayable actions captured - the session was saved "
                    "empty. Navigate the browser to a real website and "
                    "interact with it (the address bar / new-tab search box "
                    "are browser chrome: the text typed there is not "
                    "replayable, only the page you land on is recorded as one "
                    "'Open page' action)."
                )
            actions = []
    return actions


def _drop_superseded_pair_marks(actions: list[Event]) -> list[Event]:
    """Keep ONE action per repeating row set defined by two marked clicks.

    Two Insert-marked clicks let the recorder VERIFY the row set (the kind must
    match both marks) instead of guessing it from one click.  The first click
    only named the element, so the second carries the verified kind plus the ts
    of the click it supersedes; that one is dropped here, leaving a single
    repeating action that iterates the rows.
    """
    superseded: set[tuple[str, float]] = set()
    for a in actions:
        ent = getattr(a, "entity", None) or {}
        ts = ent.get("supersedes_ts")
        if ts is not None:
            superseded.add((str(ent.get("supersedes_type") or "click"), float(ts)))
    if not superseded:
        return actions
    kept: list[Event] = []
    for a in actions:
        if (a.type, float(a.ts)) in superseded:
            logger.info(
                "Repeating-element pair: dropped the marker click at ts=%s "
                "(the second marked click carries the verified kind)", a.ts,
            )
            continue
        ent = getattr(a, "entity", None)
        if isinstance(ent, dict) and "supersedes_ts" in ent:
            ent = dict(ent)
            ent.pop("supersedes_ts", None)
            ent.pop("supersedes_type", None)
            a.entity = ent
        kept.append(a)
    return kept


def _clean_actions(actions: list[Event]) -> list[Event]:
    """Drop internal control signals and the ESC stop chord's keyups.

    ESC is the global stop chord, so its keyup is recorder control noise,
    never a user action (sessions of pure Escape keyups replay as
    "0/0 actions OK").  Warns loudly when nothing real survives.
    """
    cleaned = [
        a for a in actions
        if a.type not in ("stop", "bridge_ready")
        and not (a.type == "key" and a.key == "Escape")
    ]
    if not cleaned:
        logger.warning(
            "No real actions captured - the session was saved empty. "
            "ESC keyups are not recorded; interact with the page before stopping."
        )
    return cleaned


def _dedupe_os_chrome_keys(actions: list[Event]) -> list[Event]:
    """Drop OS-captured key events that duplicate an in-page twin.

    The ChromeKeyHook records bare special keys (Enter, Tab, Backspace, ...)
    typed while the browser chrome has focus (the omnibox) - keys the in-page
    recorder can never see.  But when the same key DOES reach the page, the
    page recorder owns it (richer locator/frame context) and the OS twin must
    not be saved too, or replay would press the key twice.  Matching is
    greedy on (key, state, modifiers) within a short time window, in event
    order; an OS event with no page twin is a true chrome-only key and stays.
    Only OS events are ever dropped - page events always survive.
    """
    matched_page: dict[int, Event] = {
        i: a for i, a in enumerate(actions)
        if a.type == "key" and not (a.context or {}).get("chrome")
    }
    drop: set[int] = set()
    # The OS hook stamps every press as "down" (and only ever emits presses);
    # page events carry "down"/"up".  A legacy OS event with no state means a
    # press too - normalize so the twin is still matched and dropped.
    def _press_state(state):
        return state or "down"

    for i, evt in enumerate(actions):
        if evt.type != "key" or not (evt.context or {}).get("chrome"):
            continue
        best: Optional[int] = None
        best_dt: Optional[float] = None
        for j, twin in matched_page.items():
            if twin.key != evt.key \
                    or _press_state(twin.state) != _press_state(evt.state) \
                    or twin.modifiers != evt.modifiers:
                continue
            dt = abs(twin.ts - evt.ts)
            if dt > _DEDUPE_OS_KEY_WINDOW_MS:
                continue
            if best_dt is None or dt < best_dt:
                best, best_dt = j, dt
        if best is not None:
            drop.add(i)
            del matched_page[best]
    if not drop:
        return actions
    return [a for i, a in enumerate(actions) if i not in drop]


def _dedupe_os_chrome_clicks(actions: list[Event]) -> list[Event]:
    """Drop OS-captured chrome-band clicks that duplicate an in-page click.

    The ChromeMouseHook now captures coordinate clicks in the chrome band
    below the toolbar (bookmark bar / top strip).  When such a click ALSO
    reached the page (the band overlaps the top of the page when the bookmark
    bar is hidden), the page recorder owns it - it carries the rich locator
    chain - and the OS twin must be dropped, or replay would click twice.
    Matching is greedy on (type, ts) within a short window; an OS click with
    no page twin is a true chrome-only click (bookmark bar) and stays.
    """
    matched_page: dict[int, Event] = {
        i: a for i, a in enumerate(actions)
        if a.type in ("click", "dblclick", "contextmenu")
    }
    drop: set[int] = set()
    for i, evt in enumerate(actions):
        if evt.type != "chrome" or evt.action != "chrome_click":
            continue
        best: Optional[int] = None
        best_dt: Optional[float] = None
        for j, twin in matched_page.items():
            dt = abs(twin.ts - evt.ts)
            if dt > _DEDUPE_OS_CLICK_WINDOW_MS:
                continue
            if best_dt is None or dt < best_dt:
                best, best_dt = j, dt
        if best is not None:
            drop.add(i)
            del matched_page[best]
    if not drop:
        return actions
    return [a for i, a in enumerate(actions) if i not in drop]


def _install_global_esc_listener(stop_event: threading.Event):
    """Global ESC hook so recording stops even when the terminal has focus.

    Returns the `keyboard` module on success, None when unavailable (the
    in-page ESC listener and browser-close detection still work then).
    """
    try:
        import keyboard
    except Exception:
        logger.warning(
            "Global ESC hook unavailable (install the 'keyboard' package); "
            "press ESC inside the page or close the browser window to stop."
        )
        return None
    keyboard.on_press_key("esc", lambda _event: stop_event.set(), suppress=False)
    return keyboard


def record_web_session(
    output: Optional[str] = None,
    url: Optional[str] = None,
    headed: bool = True,
    chain_key: Optional[str] = None,
    name: Optional[str] = None,
) -> Path:
    """Record a browser session and save it as a web session JSON.

    ``url`` is optional: omit it to open a blank browser and navigate manually.
    Only user interactions are recorded (clicks, typing, keys — URL changes are
    not), so dynamic/SPA pages replay without hard navigations.  Recording
    always uses the persistent visible workbench browser and returns the path
    of the saved session file.
    ``chain_key`` is normalized to the app-wide shared scope (kept for API
    compatibility): every recording lands in the SAME durable profile, so
    cookies/history recorded once survive app restarts everywhere.  Pages are
    never restored on relaunch.
    ``name`` (optional) is the user-facing label for the session - stored in
    the metadata so the GUI's web-sequences library can show the friendly
    name instead of the timestamped filename.
    """
    payload = _payload()
    started_at = time.time()
    actions: list[Event] = []
    # The app-wide shared recording browser: reuses the open one when alive
    # (so the user continues where they left off), relaunches the durable
    # shared profile otherwise.  ``headed`` is kept for API compatibility -
    # the workbench is inherently visible.
    scope = chain_key or SHARED_WEB_SCOPE
    driver = ensure_workbench_session(chain_key=scope)
    stop_event = threading.Event()
    keyboard_hook = _install_global_esc_listener(stop_event)

    # OS-level browser-chrome capture (page JS can never see chrome UI): the
    # key hook records page-invisible shortcuts (Ctrl+T/W/L, Ctrl+Tab, ...)
    # and the mouse hook classifies toolbar clicks into semantic actions.
    # Both degrade gracefully when the OS packages are unavailable.
    browser_pid = None
    try:
        from .session import _enumerate_top_windows, _match_session_window

        browser_pid = _match_session_window(driver, _enumerate_top_windows())
    except Exception:
        browser_pid = None
    chrome_key_hook = None
    chrome_mouse_hook = None
    try:
        chrome_key_hook = ChromeKeyHook(foreground_pid=browser_pid)
    except Exception as exc:
        logger.warning(
            "Browser-chrome key capture unavailable (%s); page-visible shortcuts still record", exc
        )
    try:
        # Toolbar-click classification needs the session window's OS rect.  The
        # workbench window is stable for the recording, so capture it once.
        rect = None
        if browser_pid is not None:
            for win in _enumerate_top_windows():
                if win.get("pid") == browser_pid:
                    rect = win.get("rect")
                    break
        if rect:
            chrome_mouse_hook = ChromeMouseHook(window_rect_cb=lambda r=rect: r)
            chrome_mouse_hook.start()
        else:
            logger.warning(
                "Browser-chrome click capture unavailable (no session window rect); use keyboard shortcuts"
            )
    except Exception as exc:
        logger.warning(
            "Browser-chrome click capture unavailable (%s); use keyboard shortcuts", exc
        )

    logger.info(
        "Starting web session recording (scope=%s output=%s)", scope, output
    )
    logger.info("\n=== LoOper web recorder ===")
    if scope:
        logger.info(f"Chain web scope: {scope}")
    if url:
        logger.info(f"Opening: {url}")
    else:
        logger.info(
            "No start URL - navigate to the site, then interact:\n"
            "the address bar / new-tab search are browser chrome, so the text "
            "typed there is NOT recorded - the page you land on is saved as "
            "one 'Open page' action at the start of the sequence."
        )
    logger.info(
        "Interact with the page normally. ESC (in the page or the terminal)\n"
        "stops and saves - the browser stays open so you can record more\n"
        "sequences on top; close the browser window to end the session.\n"
        "Closing it does not lose this chain's story - the next recording\n"
        "for this chain relaunches it where you left off.\n"
        "Browser-chrome shortcuts (Ctrl+T/W/L/N/H, Ctrl+Tab, Ctrl+1-9) and\n"
        "toolbar back/forward/reload clicks are auto-recorded and replayed\n"
        "natively; back/forward also works via Alt+Left / Alt+Right.\n"
    )

    window_gone = False
    recorded_count = 0
    saw_chrome_enter = False

    def _signal_actions(drained_events: list[Event]) -> None:
        """Emit the live per-action signal for every real user action.

        Control events (stop/bridge_ready) are internal and never signal.  The
        counter is session-local so the numbers match what will be saved.
        """
        nonlocal recorded_count
        for evt in drained_events:
            if evt.type not in ("stop", "bridge_ready"):
                recorded_count += 1
                if _is_chrome_internal_url(evt.url):
                    # Live feedback must not claim a capture that can never
                    # replay: interactions on the browser's own pages are
                    # dropped at save time and only land as the cold-start
                    # "Open page" action.
                    logger.info(
                        "WEB ACTION #%03d: %s (NOT replayable - recorded on "
                        "the browser's own page)",
                        recorded_count, evt.describe(),
                    )
                else:
                    logger.info(
                        "WEB ACTION #%03d: %s", recorded_count, evt.describe()
                    )

    def _note_chrome_enter(drained_events: list[Event]) -> None:
        """Remember an OS-captured Enter pressed while the browser chrome had
        focus (address bar / new-tab search submit).  Its typed text is never
        visible to the page recorder, but the Enter is the signal that the
        user cold-started a navigation - the destination is what survives.
        """
        nonlocal saw_chrome_enter
        if saw_chrome_enter:
            return
        for evt in drained_events:
            if (
                evt.type == "key" and evt.key == "Enter" and evt.state != "up"
                and (evt.context or {}).get("chrome")
            ):
                saw_chrome_enter = True
                return

    try:
        _register_cdp_injection(driver, payload)
        if url:
            driver.get(url)
        _set_overlay(driver, True)
        _inject_or_refresh(driver, payload)
        # A chain-linked replay can leave real clicks/typing in the live
        # in-page buffer - discard it so the next recording never ingests
        # the previous replay's actions as user actions.
        try:
            driver.execute_script(
                "window.__webversionpw_events && (window.__webversionpw_events.length = 0);"
                "try { sessionStorage.removeItem('looper.web.events.top'); } catch (e) {}"
                "window.__wvpMarkerHeld = false; window.__wvpMarkerStamp = 0;"
                "return true;"
            )
        except Exception:
            pass

        # create_session already guarantees a single browser window: its
        # port-wait launcher warms uc's pre-launched Chrome before chromedriver
        # attaches, so no extra blank window can appear (no consolidation or
        # prune pass needed here).

        # The page the recording STARTED on.  When it is the browser's own
        # default page and the recording ends on a real site, the user
        # navigated - that landing is recorded as one "Open page" action even
        # when no in-page action was captured (see _finalize_actions).
        try:
            initial_url = driver.current_url or None
        except Exception:
            initial_url = None
        last_url = initial_url

        last_full_drain = 0.0
        last_marker_held = None
        # Window set the recorder started on.  Steady-state polling stays on
        # ONE window; _follow_active_window updates this set and re-anchors
        # when a popup opens / a window closes.  The baseline is ALL open
        # windows (a reused browser may already have several, e.g. a sign-in
        # popup left open by an earlier step): seeding it with only the
        # current window would make every pre-existing window look "new" and
        # the first cycle would abandon the user's window for it.
        known_handles: set[str] = set()
        try:
            known_handles = set(driver.window_handles)
        except Exception:
            try:
                known_handles = {driver.current_window_handle}
            except Exception:
                pass
        while not stop_event.is_set():
            time.sleep(_FAST_POLL_INTERVAL_S)
            # Bridge the OS-level marker-key (Insert) state into the page.  The
            # low-level hook sees the key system-wide even when the browser page
            # never receives it, so the in-page recorder can still treat the
            # click as a repeating-element ("kind") marker.  Pushed only on a
            # change (down/up) - two tiny calls per gesture.
            if chrome_key_hook is not None:
                held = bool(getattr(chrome_key_hook, "marker_held", False))
                if held != last_marker_held:
                    last_marker_held = held
                    try:
                        driver.execute_script(
                            "(function(h){ if (window.__wvpMarkerHeld !== h) {"
                            " window.__wvpMarkerHeld = h;"
                            " window.__wvpMarkerStamp = Date.now(); } })"
                            "(arguments[0]);",
                            held,
                        )
                    except Exception:
                        pass
            # A click/Enter that navigates can destroy the page - and its
            # in-memory buffer - before the full-drain cadence.  Drain
            # immediately whenever the CURRENT window has events waiting (or
            # when the cadence elapsed anyway), so a navigation click is
            # captured long before the destination page commits.  Only the
            # current window is checked: a cross-window scan would raise
            # every window in turn (chromedriver focuses on switch) and flip
            # popup/opener focus back and forth.
            if (
                time.time() - last_full_drain < _POLL_INTERVAL_S
                and not _any_buffered(driver)
            ):
                continue
            last_full_drain = time.time()
            drained = _drain_tree(
                driver, payload, 0, _current_window_ordinal(driver), ()
            )
            # Follow window changes ONCE per event, right after the current
            # window was drained - a click that opened a popup is already in
            # `drained`, so switching to the popup loses nothing.  Each
            # switch_to.window raises the target window in Chrome, which is
            # fine at a user-move moment (the user just focused that window)
            # but must never happen in a steady-state scan.
            known_handles, window_gone = _follow_active_window(
                driver, payload, known_handles,
            )
            if window_gone:
                break
            # Follow the user into the browser window they are actually using:
            # the handle-set follower above cannot see a move into an
            # ALREADY-OPEN window (re-recording a step whose popup is still
            # open from the previous step adds no new handle).  Chrome raises
            # whatever window the user interacts with, so the OS foreground
            # window is the ground truth - cheap to poll here, switches only
            # when the foreground window differs from the attached one.
            try:
                _follow_user_window(driver, payload, browser_pid)
            except Exception:
                pass
            # Keep the last page the browser was verifiably on: if the window
            # is closed at stop time the driver read fails, and the landing
            # URL of a cold-start recording must still survive.
            try:
                current_url = driver.current_url or None
            except Exception:
                current_url = None
            if current_url is not None:
                if current_url != last_url:
                    # A navigation can land on a different ORIGIN, where the
                    # sessionStorage overlay gate does not exist (it is
                    # per-origin) - re-assert it so the recording overlay
                    # survives the page change instead of vanishing.
                    _set_overlay(driver, True)
                last_url = current_url
            if chrome_key_hook is not None:
                drained.extend(chrome_key_hook.drain())
            if chrome_mouse_hook is not None:
                drained.extend(chrome_mouse_hook.drain())
            _note_chrome_enter(drained)
            _signal_actions(drained)
            if _consume_drained(actions, drained):
                break

        # Let in-flight postMessages from child frames land, then final flush.
        if not window_gone:
            time.sleep(_SETTLE_AFTER_STOP_S)
            drained, window_gone = _drain_all(driver, payload)
            if chrome_key_hook is not None:
                drained.extend(chrome_key_hook.drain())
            if chrome_mouse_hook is not None:
                drained.extend(chrome_mouse_hook.drain())
            _note_chrome_enter(drained)
            _signal_actions(drained)
            actions.extend(drained)
            if not window_gone:
                flushed = _flush_all(driver, payload)
                _signal_actions(flushed)
                actions.extend(flushed)
    finally:
        if chrome_mouse_hook is not None:
            try:
                chrome_mouse_hook.stop()
            except Exception:
                pass
        if keyboard_hook is not None:
            try:
                keyboard_hook.unhook_all()
            except Exception:
                pass
        # Leave the browser open for the next recording in this session
        # (closing the window manually ends it).  On a fresh launch the chain
        # profile is reused - cookies/sessions persist - but the browser
        # starts at its default page; the last page is deliberately not
        # restored so replays behave the same after an app restart.
        _disable_overlay_all(driver)
        release_workbench(driver)

    # Drop internal control signals, order by timestamp, and record pacing gaps
    # (ms between consecutive actions) so replay can respect how long the user
    # actually took.  Escape keyups are also dropped: ESC is the global stop
    # chord, so its keyup is recorder control noise, never a user action
    # (sessions of pure Escape keyups replay as "0/0 actions OK").
    # Diagnostic: the last click's repeating-element (Insert) marker outcome, so
    # a marker key that never reached the page is distinguishable from an
    # underivable "kind" - both otherwise surface as entity=null.
    try:
        mark = driver.execute_script("return window.__wvpEntityMark || null")
    except Exception:
        mark = None
    if mark:
        logger.info(
            "Repeating-element marker: key-held=%s bridged=%s active=%s derived-selectors=%s %s",
            mark.get("held"), mark.get("bridged"), mark.get("active"),
            mark.get("selectors") or mark.get("selector"),
            ("pair=%s" % mark.get("pair")) if mark.get("pair")
            else ("pair-diag=%s" % mark.get("pair_diag")) if mark.get("pair_diag")
            else "pair=none",
        )
        for line in (mark.get("diag") or []):
            logger.info("Repeating-element candidate: %s", line)
    try:
        final_url = driver.current_url or None
    except Exception:
        final_url = None
    if final_url is None:
        final_url = last_url  # window closed - last page it was verifiably on
    actions = _finalize_actions(
        actions,
        saw_chrome_enter=saw_chrome_enter,
        final_url=final_url,
        initial_url=initial_url,
    )
    prev_ts: Optional[float] = None
    for action in actions:
        if prev_ts is not None:
            action.gap_ms = max(0, int(action.ts - prev_ts))
        prev_ts = action.ts

    if not output:
        output = str(Path("recordings") / f"session-{time.strftime('%Y%m%d-%H%M%S')}.json")
    # Reference only: the page of the first recorded in-page action.  Replay
    # never navigates to it - the session runs on the browser's current page,
    # so the browser must be left on the page where the sequence starts.
    start_url = next((a.url for a in actions if a.url), None)
    save_session(
        actions,
        output,
        start_url=url or start_url,
        started_at=started_at,
        duration_sec=time.time() - started_at,
        name=name,
    )
    logger.info("Web session saved: %s (%d actions)", output, len(actions))
    logger.info("Saved %d actions -> %s", len(actions), output)
    return Path(output)
