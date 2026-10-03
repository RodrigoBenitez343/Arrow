from __future__ import annotations

import logging
logger = logging.getLogger(__name__)
"""Web replay engine: native-first, explicit waits, human-like pacing.

Each recorded action is dispatched through the handlers registry (one class
per action type, ``player/web/handlers``); every handler runs a deterministic
method ladder - trusted native CDP input first, in-browser JS dispatch as
fallback - and reports WHICH method worked, so the run log reads
"Web action 4 (click) OK via native" instead of hiding the interaction path.
Replay runs in a visible browser by default (headless is an opt-in flag);
ESC-cancel semantics are supported through an optional ``stop_flag`` callback.
If the browser window dies mid-run ("no such window"), the engine reconnects
via the optional ``driver_factory`` and retries the action once instead of
failing every remaining action.

The node has no start URL: recordings capture user interactions on the
page (clicks, typing, keys) and replay on the already-open browser like a
desktop sequence.  ``navigate`` actions appear only for a cold start - a
recording that began on the browser's default page and navigated to a site
through the address bar / new-tab search is saved with one explicit
"Open page" action to the landing page (the typed URL is browser chrome,
never visible to capture); older builds recorded them too.  Everything else
is element-driven, so dynamic chains replay as recorded.
"""

import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .events import Event, Locator, load_session
from .session import cleanup_session, create_session, detach_session
from . import actions
from . import exec_overlay
from .handlers import HANDLERS

try:
    from selenium.common.exceptions import NoSuchWindowException
except Exception:  # pragma: no cover - dependency guard
    NoSuchWindowException = Exception

try:
    from selenium.webdriver.common.by import By
except Exception:  # pragma: no cover - dependency guard
    By = None

StopFlag = Optional[Callable[[], bool]]


@dataclass
class ReplayConfig:
    headless: bool = False  # visible browser by default; headless opts in
    speed: float = 1.0
    min_pause: float = 0.35
    max_pause: float = 1.5
    element_timeout: float = 15.0
    native_actions: bool = True  # trusted CDP input by default, like the desktop player


def replay_config_from_item(item: dict[str, Any]) -> ReplayConfig:
    """Build a ReplayConfig from a web_sequence node config dict.

    The node stores booleans as strings ("true"/"false") and speeds as
    strings; every conversion degrades to a safe default so a malformed node
    can never crash chain execution.
    """
    def _as_bool(value: Any, default: bool) -> bool:
        try:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("true", "1", "yes", "y", "on")
        except Exception:
            return default

    try:
        speed = float(item.get("speed") or 1.0)
        if speed <= 0:
            speed = 1.0
    except (TypeError, ValueError):
        speed = 1.0
    return ReplayConfig(
        headless=_as_bool(item.get("headless"), False),
        speed=speed,
        native_actions=_as_bool(item.get("native_actions"), True),
    )


# Dispatch registry: every recorded action type routes through its handler's
# dict-returning execute() (see handlers/).  Kept as a module-level dict so
# tests can monkeypatch individual entries.
_DISPATCHERS = {type_name: handler.execute for type_name, handler in HANDLERS.items()}

# Actions worth an orange box + trail in the execution overlay.  The mark
# re-resolves the element with a full shadow-piercing DOM search, so it is only
# run for pointer actions - never before every keystroke/typing action.
_OVERLAY_MARK_TYPES = frozenset({"click", "dblclick", "contextmenu", "hover", "drag"})


def _story_host(url: Optional[str]) -> str:
    """Scheme+host of a URL - stable across SPA routes, query and fragment
    drift - used to tell which window an action was recorded in."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        return (p.scheme or "") + "://" + (p.netloc or "")
    except Exception:
        return ""


def _story_key(url: Optional[str]) -> str:
    """Scheme+host+path of a URL - like ``_story_host`` but keeps the PATH so
    two pages on the SAME host (an opener and a same-host popup) are told
    apart; query/fragment drift is ignored."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        return (p.scheme or "") + "://" + (p.netloc or "") + (p.path or "")
    except Exception:
        return ""


def _window_matches_recorded(driver, evt: Event) -> bool:
    """True when the CURRENT window is on the action's recorded url host.

    Used to validate an ordinal-based switch: if the window handle order
    differs between record and replay, the ordinal can land on the wrong
    window - then url matching must decide instead.  A same-host popup keeps
    its ordinal (hosts agree); a url-less chrome action trusts the ordinal.
    """
    want = _story_host(getattr(evt, "url", None))
    if not want:
        return True
    try:
        got = _story_host(driver.current_url)
    except Exception:
        return True
    return (not got) or got == want


def _attach_to_recorded_window(
    driver, evt: Event, wait_s: float = 5.0
) -> bool:
    """Move the driver to the window the action was recorded in.

    A session recorded across windows - clicking a sign-in button opens an
    accounts.google.com popup, and the typing/focus that follows lives in
    that popup - must replay each action on ITS OWN window.  Without this,
    typing recorded in the popup is searched for - and fails - inside the
    opener document.  The action's recorded url tells the story: when more
    than one window is open and the attached window is not on the recorded
    host, look for the window that is (a click just opened it, so it may
    still be loading - poll briefly).  When the popup shares the opener's
    HOST, the recorded page PATH is used instead, so a same-host popup is
    still followed.  Single-window sessions never switch.  Returns True when
    the driver was moved to another window.
    """
    host = _story_host(getattr(evt, "url", None))
    if not host:
        return False
    key = _story_key(getattr(evt, "url", None))
    try:
        if len(driver.window_handles) < 2:
            return False
    except Exception:
        return False
    deadline = time.time() + wait_s
    while True:
        try:
            handles = list(driver.window_handles)
        except Exception:
            return False
        if not handles:
            return False
        try:
            current = driver.current_window_handle
            cur_url = driver.current_url
        except Exception:
            current, cur_url = None, None

        same_host = bool(cur_url) and _story_host(cur_url) == host
        if same_host:
            # Already on the recorded host.  When the action's PATH also
            # matches, this is the right window - stay.  A same-host popup is
            # followed only by an exact path match on ANOTHER window; a mere
            # SPA path drift keeps us put (never bounce to a lookalike).
            if not key or _story_key(cur_url) == key:
                return False
            for handle in handles:
                if handle == current:
                    continue
                try:
                    driver.switch_to.window(handle)
                    if key and _story_key(driver.current_url) == key:
                        return True  # the same-host popup
                except Exception:
                    continue
            if current is not None:
                try:
                    driver.switch_to.window(current)
                except Exception:
                    pass
            return False

        # The attached window is on a DIFFERENT host: find the recorded one
        # (a click just opened it, so it may still be loading - poll briefly).
        # Prefer an exact page match, then any window on the recorded host.
        for want_key in (True, False) if key else (False,):
            for handle in handles:
                if handle == current:
                    continue
                try:
                    driver.switch_to.window(handle)
                    target_url = driver.current_url
                except Exception:
                    continue
                matched = (_story_key(target_url) == key) if want_key \
                    else (_story_host(target_url) == host)
                if matched:
                    return True
        # No window on the recorded story yet: restore and poll.
        if current is not None:
            try:
                driver.switch_to.window(current)
            except Exception:
                pass
        if time.time() >= deadline:
            return False
        time.sleep(0.4)


def _run_uses_native_keys(events: list[Event], config: ReplayConfig) -> bool:
    """True when the run may press native keys and needs the release guard.

    Pure-JS replays never hold native modifiers, so skipping the guard avoids
    four extra WebDriver commands (and their failure noise) on every run.
    """
    if config.native_actions:
        return True
    for evt in events:
        if evt.type == "chrome":
            return True
        if evt.type == "key" and evt.state != "up":
            if (evt.context or {}).get("chrome") or evt.key in actions.NATIVE_KEYS:
                return True
            if evt.key and evt.key.lower() in actions.MODIFIER_DEFAULT_LETTERS and evt.modifiers:
                return True
            if (evt.key, frozenset(evt.modifiers or [])) in actions.REPLAY_CHROME_CHORDS:
                return True
    return False


class ReplayEngine:
    def __init__(self, driver, config: ReplayConfig, driver_factory=None):
        self.driver = driver
        self.config = config
        # Optional callable returning a (re)connected browser when the current
        # window dies mid-run; the chain executor passes its shared-browser
        # factory so a reconnect reuses the chain's persistent browser.
        self._driver_factory = driver_factory
        self._retried: set[int] = set()

    def _pause(self, fast: bool = False) -> None:
        """Wait a small random delay between actions, like desktop sequences.

        The recorded ms gaps are deliberately NOT replayed: a human hesitates
        for 1-20s between actions, and mirroring that makes replay feel slow
        and scripted.  Desktop sequences wait a short random jitter per action
        instead, so web replay does the same - ``fast`` (key/type bursts) uses
        a much shorter floor so macros keep a snappy cadence.  ``speed``
        scales the whole pause (2x speed = half the wait).
        """
        if fast:
            floor = min(0.08, self.config.max_pause * 0.15)
            ceiling = max(floor, self.config.max_pause * 0.35)
            pause = random.uniform(floor, ceiling)
        else:
            pause = random.uniform(self.config.min_pause, self.config.max_pause)
        time.sleep(pause / self.config.speed)

    def _dispatch(self, index: int, evt: Event) -> dict[str, Any]:
        """Run one action; never raises (returns a per-action result dict).

        Handlers return dicts with ok/method/error; a plain-callable entry
        (tests) returning None counts as success.  A dead browser window is
        flagged so run() can reconnect and retry.
        """
        t0 = time.time()
        dispatcher = _DISPATCHERS.get(evt.type)
        if dispatcher is None:
            return {"index": index, "type": evt.type, "ok": False, "method": None,
                    "error": f"no dispatcher for type '{evt.type}'", "took": 0.0}
        try:
            result = dispatcher(self.driver, evt, self.config)
            if isinstance(result, dict):
                return {"index": index, "type": evt.type,
                        "ok": bool(result.get("ok")),
                        "method": result.get("method"),
                        "error": result.get("error"),
                        "took": result.get("took", round(time.time() - t0, 3))}
            return {"index": index, "type": evt.type, "ok": True, "method": None,
                    "error": None, "took": round(time.time() - t0, 3)}
        except NoSuchWindowException as exc:
            return {"index": index, "type": evt.type, "ok": False, "method": None,
                    "error": str(exc), "took": round(time.time() - t0, 3),
                    "window_lost": True}
        except Exception as exc:
            return {"index": index, "type": evt.type, "ok": False, "method": None,
                    "error": str(exc), "took": round(time.time() - t0, 3)}

    def _prepare_entity(self, evt: Event, state: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Point a repeating-element action at the FIRST unprocessed match.

        An entity action carries one or more candidate selectors that each match
        a whole SET (a "kind"); each pass picks the first match whose identity
        key has not been processed yet, so items survive re-render / removal
        between passes.  Candidates are tried in order (stable first), so a
        session recorded on one page shape still resolves on another.  The
        recorded locator is steered at that ordinal so the existing click handler
        resolves THAT element.  Returns an info dict, or None when every match
        has already been processed (the set is exhausted).

        Frame- and shadow-aware: the set is enumerated inside the event's
        recorded iframe (index path, then locator path), and a shadow-DOM /
        shadow-hosted-frame target - which WebDriver cannot reach - is
        enumerated inside the page.

        A selector set that matches NOTHING is reported distinctly (``missing``),
        never confused with exhaustion: a volatile kind selector (hashed
        classes / an absolute path the page no longer has) must not be read as
        a finished set - and must not send the action back to the recorded
        element either, since that is the ONE row the marks were made on and it
        would be clicked again on every pass.
        """
        entity = getattr(evt, "entity", None) or {}
        selectors = self._entity_selectors(entity)
        # A kind is recorded as the wrapper path it had on the recording page,
        # ending in the row's shared identity.  The wrapper levels are what a
        # different instance of the page changes while the identity survives,
        # so the trailing identity is appended as a weaker candidate - that
        # keeps resolution SET-based on another page (and keeps existing
        # recordings working) instead of degrading to the recorded element.
        selectors += [sel for sel in self._widen_kind_selectors(selectors)
                      if sel not in selectors]
        if not selectors or By is None:
            return {"missing": True, "selector": entity.get("selector"), "total": 0}
        processed = state.setdefault("processed", set())
        info = self._entity_cursor(evt, selectors, entity.get("key_attr"), processed)
        if info is None:
            return {"missing": True, "selector": selectors[0], "total": 0}
        if info.get("exhausted"):
            return None
        # Steer the cursor with a FRESH locator holding the kind selector
        # + ordinal as the ONLY identity.  The recorded locator's other
        # layers (id / data-* / text / xpath of the recorded instance)
        # must not stay on the chain - they would win the ladder and click
        # the ORIGINAL element every pass instead of the ordinal-th match.
        evt.locator = Locator(
            tag=getattr(evt.locator, "tag", None),
            css=info["selector"],
            index=info["ordinal"],
        )
        processed.add(info["key"])
        return {"ordinal": info["ordinal"], "total": info["total"], "key": info["key"]}

    @staticmethod
    def _entity_selectors(entity: dict[str, Any]) -> list[str]:
        """Ordered, deduped candidate kind selectors for an entity action.

        New recordings carry ``selectors`` (stable first); older ones carry a
        single ``selector`` - both are accepted, and the single one is appended
        as a last resort when it is not already in the list.
        """
        out: list[str] = []
        raw = entity.get("selectors")
        if isinstance(raw, (list, tuple)):
            for sel in raw:
                if sel and sel not in out:
                    out.append(sel)
        single = entity.get("selector")
        if single and single not in out:
            out.append(single)
        return out

    @staticmethod
    def _widen_kind_selectors(selectors: list[str]) -> list[str]:
        """Trailing-identity rescue for a container-scoped kind selector.

        A kind selector is a wrapper path whose LAST compound carries the row's
        shared identity (classes / data-* / role).  Only the wrapper levels
        above it differ between one instance of a page and another, so keeping
        the last compound alone turns a page-specific path back into the
        portable set selector.  A positional compound - a bare tag or an
        ``:nth-of-type`` - names a place rather than a repeating item and is
        skipped: widening it would match far too much.  ponytail: splitting on
        the last ``>`` is enough for the selectors this module records.
        """
        out: list[str] = []
        for sel in selectors:
            last = sel.rsplit(">", 1)[-1].strip()
            if ("." not in last and "[" not in last) or "nth-of-type" in last:
                continue
            if last not in out:
                out.append(last)
        return out

    def _entity_cursor(self, evt: Event, selectors: list[str],
                       key_attr: Optional[str],
                       processed: set) -> Optional[dict[str, Any]]:
        """Enumerate the kind inside the event's frame, or in the page.

        Returns ``None`` when no candidate matched, ``{"exhausted": True}``
        when a candidate matched but every match was already processed, else the
        pick ``{selector, ordinal, total, key}``.
        """
        if actions._needs_dom_dispatch(evt):
            # Shadow root / shadow-hosted frame: WebDriver cannot reach it, so
            # enumerate and pick inside the page.
            return self._entity_cursor_js(selectors, key_attr, processed)
        return self._entity_cursor_native(evt, selectors, key_attr, processed)

    def _entity_cursor_native(self, evt: Event, selectors: list[str],
                              key_attr: Optional[str],
                              processed: set) -> Optional[dict[str, Any]]:
        """Native enumeration, entering the event's recorded iframe first."""
        has_scope = bool(evt.frame_path) or bool(evt.frame_index_path)
        if has_scope:
            entered = actions.enter_recorded_frame(self.driver, evt)
        else:
            entered = False
            # The driver may still sit in a PREVIOUS action's frame (WebDriver
            # keeps the context between calls) - reset it.
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
        try:
            if has_scope and not entered:
                # A stale frame_path must not silently drop the cursor: the
                # in-page cursor searches same-origin iframes from the top doc.
                return self._entity_cursor_js(selectors, key_attr, processed)
            for selector in selectors:
                try:
                    matches = self.driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    matches = []
                if not matches:
                    continue
                return self._pick_entity(matches, selector, key_attr, processed)
            return None
        finally:
            if has_scope:
                try:
                    self.driver.switch_to.default_content()
                except Exception:
                    pass

    def _entity_cursor_js(self, selectors: list[str], key_attr: Optional[str],
                          processed: set) -> Optional[dict[str, Any]]:
        """In-page shadow-piercing enumeration for an unreachable target."""
        try:
            info = self.driver.execute_script(
                actions.JS_ENTITY_CURSOR, selectors, key_attr, list(processed)
            )
        except Exception:
            return None
        return info if isinstance(info, dict) else None

    def _pick_entity(self, matches: list, selector: str, key_attr: Optional[str],
                     processed: set) -> Optional[dict[str, Any]]:
        """First unprocessed match, skipping container matches.

        The kind selector that covers every row frequently ALSO matches the row
        CONTAINER (a container shares the row classes) and the container comes
        FIRST in document order - a match that ENCLOSES another match is that
        outer div, never a repeating item.  When EVERY match nests (the
        heuristic would leave nothing to click) the full set is kept.
        """
        encloses = [self._encloses_match(m, selector) for m in matches]
        skip_containers = not all(encloses)
        for ordinal, element in enumerate(matches):
            if skip_containers and encloses[ordinal]:
                continue  # an outer div that also matches the kind
            key = self._entity_key(element, key_attr, ordinal)
            if key in processed:
                continue
            return {"selector": selector, "ordinal": ordinal,
                    "total": len(matches), "key": key}
        return {"selector": selector, "total": len(matches), "exhausted": True}

    def _encloses_match(self, element, selector: str) -> bool:
        """True when *element* itself CONTAINS another match for the kind.

        Such a match is the outer container - a class combination covering
        every row also matches the row container, which shares the row
        classes - so it is an ancestor of the real repeating items and must
        never be clicked.  A driver error degrades to 'not a container' so a
        failing check can never hide a legitimate item.
        """
        try:
            return bool(element.find_elements(By.CSS_SELECTOR, selector))
        except Exception:
            return False

    def _entity_key(self, element, key_attr: Optional[str], ordinal: int) -> str:
        """Stable identity for a repeating element.

        Prefer the stable data-* identity; otherwise use the element's POSITION
        in the matched set.  Position is used instead of visible text as the
        fallback because clicking a list item frequently MUTATES its text
        (LinkedIn marks the job "Viewed"), which would change a text key between
        passes and make the SAME item get re-clicked.
        """
        try:
            if key_attr:
                value = element.get_attribute(key_attr)
                if value:
                    return str(value)
        except Exception:
            pass
        return f"__ordinal_{ordinal}"  # no stable attr - identify by position

    def run(self, events: list[Event], stop_flag: StopFlag = None,
            entity_state: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        total = len(events)
        started = time.time()

        prev_url: Optional[str] = None
        needs_release = _run_uses_native_keys(events, self.config)
        # Shared across passes of a "repeat per matching element" run: the set
        # of already-processed repeating-element identities.
        entity_state = entity_state if entity_state is not None else {}
        entity_total = 0
        entity_exhausted = False
        try:
            # The frame context is deliberately NOT reset here.  A node runs on
            # the SESSION the previous node left, frame focus included.  What
            # that focus means is decided per ACTION: a native action re-enters
            # its recorded frame (actions.resolve_element), and the
            # shadow-piercing in-page dispatch starts from the top-most
            # same-origin document by itself - see JS_DEEP_SEARCH.__wvpRootDoc,
            # "climb to the top-most SAME-ORIGIN document so the action guides
            # itself through the whole frame tree instead of being trapped in a
            # stale frame".  Forcing default_content() here therefore bought
            # nothing and only discarded a frame a previous node had entered -
            # which is what sent a following click OUTSIDE the frame the form
            # filler was working in.
            #
            # Orange in-page execution overlay: box each element about to be
            # acted on, trail the virtual cursor to it, banner the action.
            exec_overlay.enable(self.driver)
            for index, evt in enumerate(events):
                if evt.type in ("stop", "bridge_ready", "unknown"):
                    continue
                if evt.type == "key" and evt.state == "up":
                    continue  # release events add nothing (taps release atomically)
                if stop_flag and stop_flag():
                    logger.info("Web replay stopped by user request")
                    break
                # Chrome back/forward on a fresh replay browser has no history -
                # give it the neighbouring action's recorded URL as the fallback
                # navigation target.
                if evt.type == "chrome" and evt.action in ("back", "forward"):
                    ctx = dict(evt.context or {})
                    if evt.action == "back" and prev_url:
                        ctx.setdefault("fallback_url", prev_url)
                    elif evt.action == "forward":
                        nxt = next((e.url for e in events[index + 1:] if e.url), None)
                        if nxt:
                            ctx.setdefault("fallback_url", nxt)
                    evt.context = ctx
                # Replay is interaction-driven: navigation comes from the
                # session's own recorded actions.  A session recorded from a
                # cold start (browser default page -> address bar / new-tab
                # search) carries an explicit leading ``navigate`` action that
                # positions the browser via driver.get; everything after it is
                # clicks/typing/keys.  Sessions recorded on a parked page have
                # no navigate - the shared workbench browser already carries
                # the page state.
                #
                # The recorded url is deliberately NOT compared against the
                # live one.  A web sequence is modular and universal: it is
                # expected to run on a DIFFERENT instance of the page - another
                # job id, another query string, another SPA route, even another
                # host - so which page the recording happened on is not a fact
                # replay may depend on or complain about.  What identifies the
                # target there is the element's own PORTABLE identity
                # (test-id hook / aria-label / role+text / visible text /
                # label), never the page it was recorded on.
                if evt.url:
                    prev_url = evt.url
                # A session recorded across windows (a click opened a popup
                # and later actions live in it) replays each action on the
                # window its page ran on - typing recorded in the popup is
                # otherwise searched for, and fails, inside the opener.
                # The recorded ordinal (window handle index) is exact and
                # disambiguates a same-host popup; url-host matching is the
                # legacy fallback for sessions recorded before ordinals.
                ordinal = getattr(evt, "window_ordinal", None)
                moved_by_ordinal = actions.switch_to_window_ordinal(self.driver, ordinal)
                moved = moved_by_ordinal
                if moved_by_ordinal and not _window_matches_recorded(self.driver, evt):
                    # The ordinal landed on a window that is not on the
                    # recorded url (handle order changed): let url matching
                    # decide instead of trusting a stale ordinal.
                    moved = False
                if not moved and (moved_by_ordinal or ordinal is None):
                    # Correct a switch the ordinal ACTUALLY made (a stale ordinal),
                    # plus the legacy case of an action carrying no ordinal at
                    # all.  When the ordinal already names the CURRENT window
                    # there is nothing stale to fix, and a url lookup would
                    # instead jump to a LEFTOVER tab: the chain browser is
                    # long-lived and accumulates tabs across runs, so the
                    # recorded url frequently matches a BACKGROUND tab from an
                    # earlier run.  That is how a chain's final step got replayed
                    # on the page underneath the modal instead of on the modal -
                    # and why it only ever happened inside a chain (standalone
                    # replay has a single window).
                    moved = _attach_to_recorded_window(self.driver, evt)
                if moved:
                    logger.info(
                        "Web action %d (%s): moved to recorded window (ordinal=%s)",
                        index, evt.type, ordinal,
                    )
                entity_info = None
                if getattr(evt, "entity", None):
                    entity_info = self._prepare_entity(evt, entity_state)
                    if entity_info is None:
                        logger.info(
                            "Entity action %d: no unprocessed match left - "
                            "sequence complete", index,
                        )
                        entity_exhausted = True
                        break
                    if entity_info.get("missing"):
                        # A repeating action must NEVER fall back to the
                        # recorded element: that is the single row the marks
                        # were made on, so every pass would click it again and
                        # the set would never advance.  Fail the action loudly
                        # and leave the set open instead.
                        logger.error(
                            "Entity action %d: no repeating set matched (%s) "
                            "- skipping instead of re-clicking the recorded "
                            "element",
                            index, entity_info.get("selector"),
                        )
                        results.append({
                            "index": index, "type": evt.type, "ok": False,
                            "method": None,
                            "error": "repeating-element set not found",
                            "took": 0.0,
                        })
                        continue
                    entity_total = max(
                        entity_total, entity_info.get("total", 0)
                    )
                self._pause(fast=evt.type in ("key", "type"))
                # Show what this action is about to hit before it runs (pointer
                # actions only - see _OVERLAY_MARK_TYPES).
                if evt.type in _OVERLAY_MARK_TYPES:
                    exec_overlay.mark(self.driver, evt, f"{index}: {evt.type}")
                result = self._dispatch(index, evt)
                # The browser window died mid-run ("no such window"): reconnect
                # through the factory and retry the SAME action once, so a
                # transient window loss does not fail every remaining action.
                if result.get("window_lost") and self._driver_factory is not None \
                        and index not in self._retried:
                    self._retried.add(index)
                    logger.warning(
                        "Web replay window lost; reconnecting and retrying action %d", index
                    )
                    try:
                        self.driver = self._driver_factory()
                        result = self._dispatch(index, evt)
                    except Exception as exc:
                        result = {"index": index, "type": evt.type, "ok": False,
                                  "method": None,
                                  "error": f"reconnect failed: {exc}", "took": 0.0}
                if result["ok"]:
                    logger.info(
                        "Web action %d (%s) OK via %s",
                        index, evt.type, result.get("method") or "?",
                    )
                else:
                    logger.warning(
                        "Web action %d (%s) failed: %s", index, evt.type, result.get("error")
                    )
                if entity_info:
                    result["entity"] = entity_info
                    logger.info(
                        "Repeating element %d/%d processed (key=%s)",
                        entity_info["ordinal"] + 1, entity_info["total"],
                        entity_info.get("key"),
                    )
                results.append(result)
        finally:
            # Never leave native modifiers stuck on an aborted/exception replay
            # (skipped for pure-JS runs that never pressed native keys).
            if needs_release:
                actions.release_all_keys(self.driver)
            # Remove the in-page overlay from every window (best-effort).
            exec_overlay.disable(self.driver)

        ok = sum(1 for r in results if r["ok"])
        return {
            "total": total,
            "attempted": len(results),
            "ok": ok,
            "failed": [r for r in results if not r["ok"]],
            "duration_sec": round(time.time() - started, 2),
            "entity_exhausted": entity_exhausted,
            "entity_total": entity_total,
        }


def replay_session(
    session_path: str,
    config: Optional[ReplayConfig] = None,
    stop_flag: StopFlag = None,
    driver: Any = None,
    driver_factory: Optional[Callable[[], Any]] = None,
    entity_state: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Replay a web session file.

    With ``driver`` the replay runs on that browser and the caller owns the
    lifecycle - the chain executor passes its per-chain shared browser, so all
    web sequence nodes of a chain share ONE instance (cookies, localStorage
    and page state persist across the nodes of that chain).  ``driver_factory``
    provides a replacement browser when the window dies mid-run (reconnect +
    single retry); the executor passes its shared-browser factory.  Without a
    ``driver`` a fresh, isolated session is created for the replay: it is left
    open for inspection after a visible replay (the user closes it manually)
    and cleaned up automatically for headless replays.
    """
    config = config or ReplayConfig()
    data = load_session(session_path)
    own_driver = driver is None
    # A headless web orchestrator forces an invisible browser even when the
    # node itself did not opt in (see web.session.run_headless).
    try:
        from .session import run_headless
        headless = bool(config.headless) or bool(run_headless())
    except Exception:
        headless = bool(config.headless)
    if own_driver:
        driver = create_session(headless=headless)
        driver_factory = driver_factory or (
            lambda: create_session(headless=headless)
        )
    engine = ReplayEngine(driver, config, driver_factory=driver_factory)
    try:
        return engine.run(data["actions"], stop_flag=stop_flag, entity_state=entity_state)
    finally:
        driver = getattr(engine, "driver", driver)  # may have been reconnected
        if own_driver:
            if headless:
                cleanup_session(driver)
            else:
                detach_session(driver)
