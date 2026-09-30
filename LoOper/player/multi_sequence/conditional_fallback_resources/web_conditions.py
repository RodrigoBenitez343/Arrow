"""Live-browser conditional evaluators for web-mode conditional nodes.

A web conditional routes on the state of the chain's SHARED browser (the same
instance every web sequence node of the chain uses), instead of the desktop
visual/OCR conditions.  ``evaluate()`` dispatches on ``web_condition_type``:

- ``element_located`` - visual trigger: a picked element is present, rendered AND on screen
  (top document, a currently visible iframe, or a shadow root / shadow-hosted frame -
  never a hidden/collapsed frame); a candidate present but scrolled out of view is
  scrolled into view first, and every candidate must match the recorded text (the
  tag when no text was recorded)
- ``text_present``    - visible text (whole page or a picked element) is present;
  both reads pierce shadow roots and same-origin frames
- ``browser_js``      - a user JS snippet run in the page returns truthy
- ``llm``             - an LLM reads the page's visible text + element presence
- ``layout_match``    - scroll up/down until an element sits at a target window rect

The caller (``conditional_ops.ConditionalMixin``) owns the driver lifecycle and
passes the executor's bound ``_evaluate_llm_condition`` in for the ``llm``
variant, so the web LLM condition reuses the desktop LLM pipeline unchanged.
"""
from __future__ import annotations

import json
import logging
import time

logger = logging.getLogger(__name__)

_POLL_S = 0.25


class _Config:
    """Minimal ReplayConfig stand-in: wait_for_element only reads element_timeout."""

    def __init__(self, element_timeout: float):
        self.element_timeout = element_timeout


def _as_bool(value, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes", "y", "on")
    except Exception:
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _locator_event(locator_raw, action_type: str = "web_condition"):
    """Build an Event carrying a locator for ``resolve_element``, or None.

    The picker stores the locator as a JSON string on the node; a dict is
    accepted too (the dialog hands one over directly).  ``_frame_path`` /
    ``_cross_origin`` travel beside the locator keys and name the document the
    element was picked in.
    """
    if not locator_raw:
        return None
    data = locator_raw
    if isinstance(locator_raw, str):
        try:
            data = json.loads(locator_raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    from ...web.events import Event, Locator  # player.web.events

    locator = Locator.from_dict(data)
    if locator is None:
        return None
    # The picker records WHERE the element lives.  Without it a frame-relative
    # css/xpath re-applied to the top document can match an unrelated lookalike;
    # with it the search stays in the document the element came from.  A legacy
    # locator (no scope keys) keeps the old behaviour: scan the frames.  The
    # window ordinal / frame index path name the WINDOW and the cross-origin
    # frame chain, so a target inside a popup or an embed is searched in the
    # exact place it was picked.
    scope_known = "_frame_path" in data
    window_ordinal = data.get("_window_ordinal")
    return Event(
        type=action_type,
        ts=0.0,
        locator=locator,
        frame_path=list(data.get("_frame_path") or []),
        cross_origin_frame=bool(data.get("_cross_origin")) or not scope_known,
        window_ordinal=(int(window_ordinal) if window_ordinal is not None else None),
        frame_index_path=[int(i) for i in (data.get("_frame_index_path") or [])],
    )


def _needs_deep(event) -> bool:
    """True when WebDriver cannot reach the target, so the in-page deep search
    must be used: the element lives in a shadow root, OR it was picked inside a
    shadow-HOSTED iframe (a frame WebDriver's frame tree never exposes - a
    recorded ``frame_path`` can never be entered).
    """
    from ...web import actions as web_actions

    return web_actions.needs_deep_dispatch(event)


def _js_truthy(result) -> bool:
    if isinstance(result, str):
        return result.strip().lower() not in ("", "false", "0", "no", "none", "null", "undefined")
    return bool(result)


# On-screen probe: rendered (ancestor-aware CSS visibility via checkVisibility)
# AND intersecting the current viewport.  getBoundingClientRect is
# viewport-relative, so no scroll offsets are needed.
JS_ELEMENT_ON_SCREEN = """return (function (el) {
  if (!el || el.nodeType !== 1) return false;
  try {
    if (typeof el.checkVisibility === 'function' &&
        !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
  } catch (e) {}
  var r = el.getBoundingClientRect();
  if (!r || r.width <= 0 || r.height <= 0) return false;
  var vw = window.innerWidth || 0, vh = window.innerHeight || 0;
  return r.left < vw && r.top < vh && r.right > 0 && r.bottom > 0;
})(arguments[0]);
"""


def _element_on_screen(driver, element):
    """Rendered AND intersecting the viewport: True/False, None when unknown.

    None means the browser could not answer (JS bridge down, or the reference
    does not belong to the current context).  A CONDITION requires a definite
    True, so an unanswerable probe never counts as located.
    """
    try:
        result = driver.execute_script(JS_ELEMENT_ON_SCREEN, element)
    except Exception as exc:
        logger.debug("Web element_located: on-screen probe failed: %s", exc)
        return None
    if result is None:
        return None
    return bool(result)


# Depth cap for the condition's frame walk (mirrors actions._FRAME_SCAN_MAX_DEPTH).
_MAX_FRAME_DEPTH = 4

# A condition resolves the target by IDENTITY - an attribute it was recorded
# with - never by its visible text (a lookalike rendering the same text would
# report located while the real target is gone) and never by a position alone
# when an attribute was available.  `type` is excluded because a bare
# input[type=...] matches any field of that kind.
_IDENTITY_KINDS = ("id", "data")
_WEAK_IDENTITY_ATTRS = ("type",)
# Exact-equality visible-text layers (never the replay engine's contains()-based
# fuzzy chain): a text button is still identifiable by the text it renders.
_EXACT_TEXT_KINDS = ("link_text", "text", "role_text", "label")
_STRUCTURAL_KINDS = ("css", "class", "xpath")


def _condition_tiers(event):
    """Candidate tiers a CONDITION may use, strongest first.

    1. attribute identity (id / data-* / name / placeholder / aria-label /
       href) - the element's own stable handle,
    2. exact visible-text match (link text / text XPath / role+text / label),
    3. recorded structure (unique css / ancestor css / classes / xpath) - last,
       because position alone is what a shifted layout turns into a lookalike.

    Every accepted match is still checked against the record by
    ``_matches_record``, so a weaker tier can never smuggle in another element.
    """
    chain = event.locator.chain() if event.locator else []
    identity = [
        c for c in chain
        if c.get("kind") in _IDENTITY_KINDS and c.get("name") not in _WEAK_IDENTITY_ATTRS
    ]
    text = [c for c in chain if c.get("kind") in _EXACT_TEXT_KINDS]
    structural = [c for c in chain if c.get("kind") in _STRUCTURAL_KINDS]
    return [tier for tier in (identity, text, structural) if tier]


def _norm_text(value) -> str:
    """Match the picker's ``cleanText``: whitespace collapsed, trimmed, 120 cap."""
    return " ".join(str(value or "").split())[:120]


def _collapse_text(value) -> str:
    """Whitespace-collapsed text, no length cap (containment haystack)."""
    return " ".join(str(value or "").split())


def _element_text(element) -> str:
    """Whitespace-collapsed text of a WebElement (textContent, then .text)."""
    try:
        have = element.get_attribute("textContent")
    except Exception:
        have = None
    if have is None:
        try:
            have = element.text
        except Exception:
            have = ""
    return _collapse_text(have)


def _matches_record(locator, element) -> bool:
    """Confirm a resolved element really is the one that was picked.

    The recorded TEXT is the identity: a dynamic element may re-render with a
    different tag or classes while showing the same text and doing the same
    thing, so a candidate only has to CONTAIN the recorded text - the tag is NOT
    required when text was recorded.  Containment also keeps a reused condition
    working when the element gained a badge / suffix, while a shorter lookalike
    (LinkedIn's "Apply" span vs the picked "Easy Apply" one) is still rejected.

    With no recorded text (an icon-only control) the tag is the only signal
    left, so it must agree.
    """
    want = _norm_text(locator.text)
    if want:
        return want in _element_text(element)
    tag = str(locator.tag or "").strip().lower()
    if not tag:
        return True
    try:
        return str(element.tag_name or "").strip().lower() == tag
    except Exception:
        return False


def _find_on_screen(driver, event, deadline):
    """Resolve + validate + on-screen-probe the target in the CURRENT document.

    Tiers are tried strongest-first and a tier's match is rejected unless it is
    on screen AND agrees with the record, so a weaker candidate never wins over
    nothing.

    A candidate that AGREES with the record but is present-only (rendered yet
    scrolled out of the viewport / a scrollable box) is scrolled into view and
    re-probed before it is rejected.  A web SEQUENCE scrolls its target into
    view before acting, so a CONDITION must not route FALSE for a target that is
    merely out of view.  The page is only nudged for a candidate that already
    matches the record, and a hidden / collapsed / ``display:none`` element
    stays off screen after the scroll (so it is still rejected).
    """
    from ...web import actions as web_actions

    for chain in _condition_tiers(event):
        if time.time() >= deadline:
            return None
        try:
            element = web_actions._find_by_chain(
                driver, chain, min(3.0, max(0.5, deadline - time.time())),
                locator=event.locator,
            )
        except Exception:
            continue
        if element is None or not _matches_record(event.locator, element):
            continue
        verdict = _element_on_screen(driver, element)
        if verdict is True:
            return element
        if verdict is False:
            # Present but out of view: bring it into view (walks every
            # scrollable ancestor) and re-probe - the same pre-action nudge a
            # web sequence performs before clicking.
            web_actions._scroll_into_view(driver, element)
            if _element_on_screen(driver, element) is True:
                return element
        logger.debug(
            "Web element_located: match rejected (on-screen=%s, matches record=True)",
            verdict,
        )
    return None


def _enter_frame_path(driver, event, deadline) -> bool:
    """Enter the recorded same-origin iframe chain, or False when unusable.

    Every frame on the way must itself be on screen: the element inside a
    hidden / collapsed frame is not part of what the user can see, so the search
    must not descend into it at all.
    """
    from ...web import actions as web_actions
    from ...web.events import Locator

    try:
        driver.switch_to.default_content()
    except Exception:
        return False
    for raw in event.frame_path or []:
        frame_locator = Locator.from_dict(raw)
        chain = frame_locator.chain() if frame_locator is not None else []
        if not chain:
            return False
        try:
            frame_element = web_actions._find_by_chain(
                driver, chain, min(3.0, max(0.5, deadline - time.time()))
            )
        except Exception:
            return False
        if frame_element is None or _element_on_screen(driver, frame_element) is not True:
            return False
        try:
            driver.switch_to.frame(frame_element)
        except Exception:
            return False
    return True


def _walk_visible_frames(driver, event, deadline, depth: int = 0):
    """Search the current document, then every VISIBLE child iframe.

    Used only when the element's document is not recorded (a legacy pick, or one
    from a cross-origin frame whose path could not be captured).
    """
    if depth > _MAX_FRAME_DEPTH or time.time() >= deadline:
        return None
    found = _find_on_screen(driver, event, deadline)
    if found is not None:
        return found
    try:
        from selenium.webdriver.common.by import By
        frames = driver.find_elements(By.TAG_NAME, "iframe")
    except Exception:
        frames = []
    for index in range(len(frames)):
        if time.time() >= deadline:
            return None
        # Only descend into frames that are themselves on screen: an element
        # inside a hidden/collapsed frame is not part of the visible page.
        if _element_on_screen(driver, frames[index]) is not True:
            continue
        try:
            driver.switch_to.frame(index)
        except Exception:
            continue
        try:
            found = _walk_visible_frames(driver, event, deadline, depth + 1)
        finally:
            try:
                driver.switch_to.parent_frame()
            except Exception:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
        if found is not None:
            return found
    return None


def _enter_index_path(driver, event, deadline) -> bool:
    """Enter the recorded index-based frame chain (works across origins)."""
    from ...web import actions as web_actions

    return web_actions._enter_frame_index_path(driver, event.frame_index_path)


def _resolve_on_screen(driver, event, deadline):
    """Resolve the locator to an element that is ON SCREEN right now, or None.

    The CURRENT page state decides, scoped to the document the element was
    picked in:

    - recorded frame chain -> that iframe document ONLY (every frame on the way
      must be on screen), so a frame-relative css/xpath can never be satisfied
      by a lookalike in another document,
    - recorded frame index path -> enter by index (covers cross-origin frames),
    - cross-origin pick -> the top document, then every VISIBLE iframe,
    - top-document pick -> the top document only,
    - legacy pick (no scope recorded) -> visible-frame walk, as before.

    Attribute identity only (see ``_condition_chain``), and never the replay
    engine's fuzzy / recorded-coordinate ladders.
    """
    try:
        if event.frame_path:
            if _enter_frame_path(driver, event, deadline):
                return _find_on_screen(driver, event, deadline)
            # The locator chain could not be entered: try the recorded index
            # chain (a cross-origin frame has no locator path), then give up -
            # a frame-scoped pick must never silently widen to the whole page.
            if event.frame_index_path and _enter_index_path(driver, event, deadline):
                found = _find_on_screen(driver, event, deadline)
                if found is not None:
                    return found
            return None
        if event.frame_index_path:
            if _enter_index_path(driver, event, deadline):
                found = _find_on_screen(driver, event, deadline)
                if found is not None:
                    return found
        if event.cross_origin_frame:
            return _walk_visible_frames(driver, event, deadline)
        return _find_on_screen(driver, event, deadline)
    finally:
        # A condition is a READ of the page state: it must hand the driver back
        # on the SAME document the next node starts in.  The probe may have
        # descended into the recorded iframe chain, so restore the top document
        # of the current page - focus only, never a navigation.
        try:
            driver.switch_to.default_content()
        except Exception:
            pass


# Shadow-piercing presence + on-screen probe, run with
# ``web_actions.JS_DEEP_SEARCH`` prepended (it supplies __wvpDeepFindAny).
# WebDriver cannot see elements inside shadow roots, so a picked shadow-DOM
# target is verified in-page: every recorded candidate is deep-searched (with a
# text match as the last resort), then checked for render + viewport
# intersection.  Returns null (not found), false (found but hidden/off-screen)
# or true (on screen).
#
# A candidate must AGREE with the record, exactly like the light-DOM
# ``_matches_record``: the recorded TEXT is the identity, so the element's text
# must CONTAIN it - the tag is NOT required (a dynamic element may re-render
# with a different tag while showing the same text and doing the same thing).
# A shorter lookalike (LinkedIn's "Apply" span vs the picked "Easy Apply" one)
# is rejected instead of routing TRUE.  With no recorded text the tag is the only
# signal, so it must agree.
# See ``_dispatch_dom_pointer``: the recorded LABEL is the portable identity of
# a form control (its id may embed per-instance tokens), so it is resolved
# FIRST - ``__wvpDeepFindLabel`` only answers for an unambiguous label.
JS_SHADOW_ON_SCREEN = """return (function (selectors, text, tag, label) {
  function collapse(s) {
    return String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
  }
  var needle = text ? collapse(text).slice(0, 120) : '';
  function ok(el) {
    if (!el) return false;
    // The recorded TEXT is the identity: a dynamic element may re-render with a
    // different tag while showing the same text, so the tag is NOT required.  It
    // is only checked when no text was recorded (an icon-only control).
    if (needle) return collapse(el.textContent).indexOf(needle) >= 0;
    return !tag || String(el.tagName || '').toLowerCase() === String(tag).toLowerCase();
  }
  function inViewport(el) {
    var r = null;
    try { r = el.getBoundingClientRect(); } catch (e) { return false; }
    if (!r || r.width <= 0 || r.height <= 0) return false;
    var vw = window.innerWidth || 0, vh = window.innerHeight || 0;
    return r.left < vw && r.top < vh && r.right > 0 && r.bottom > 0;
  }
  function rendered(el) {
    try {
      if (typeof el.checkVisibility === 'function' &&
          !el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    } catch (e) {}
    var n = el, g = 0;
    while (n && n.nodeType === 1 && g++ < 60) {
      if (n.hidden === true) return false;
      try {
        if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') return false;
        if (n.hasAttribute && n.hasAttribute('inert')) return false;
      } catch (e) {}
      n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
    }
    return true;
  }
  // EVERY record-matching deep match is inspected, not just the first one the
  // find happened to return: a volatile recorded identity (an Ember id that now
  // belongs to an UNRELATED node) and an exact-text hit can both land on a
  // background / off-screen twin - the page under a modal, or a "Next"
  // pagination far below the fold - which made a VISIBLE target route FALSE.
  var cands = [];
  var l = __wvpDeepFindLabel(label);
  if (l) cands.push(l);
  var sel = __wvpDeepFindAll(selectors);
  for (var i = 0; i < sel.length; i++) cands.push(sel[i]);
  var tx = text ? __wvpDeepTextAll(text) : [];
  for (var j = 0; j < tx.length; j++) cands.push(tx[j]);
  var found = false;
  for (var k = 0; k < cands.length; k++) {
    if (!ok(cands[k])) continue;
    found = true;
    if (rendered(cands[k]) && inViewport(cands[k])) return true;
  }
  return found ? false : null;
})(arguments[0], arguments[1], arguments[2], arguments[3]);
"""


def _shadow_on_screen(driver, event):
    """True/False/None on-screen verdict for a shadow-root target (None = the
    target was not found this pass, so the caller keeps polling).

    The recorded tag + text are handed to the in-page probe so a selector that
    matches a shadow lookalike is rejected (see ``JS_SHADOW_ON_SCREEN``).
    """
    from ...web import actions as web_actions

    selectors = web_actions._css_selectors(event)
    text = (event.locator.text if event.locator else None) or None
    tag = (event.locator.tag if event.locator else None) or None
    label = (event.locator.label_text if event.locator else None) or None
    if not selectors and not text and not label:
        return None
    try:
        verdict = driver.execute_script(
            web_actions.JS_DEEP_SEARCH + JS_SHADOW_ON_SCREEN,
            selectors, text, tag, label,
        )
    except Exception as exc:
        logger.debug("Web element_located: shadow probe failed: %s", exc)
        return None
    if verdict is None:
        return None
    return bool(verdict)


# Shadow-piercing scroll-into-view, run with ``web_actions.JS_DEEP_SEARCH``
# prepended.  WebDriver cannot reach a shadow-root element to scroll it, so the
# deep-find + ``scrollIntoView`` runs in-page - the same pre-action nudge a web
# sequence applies to a light-DOM target before clicking.  Returns true when a
# target was found (whether or not the scroll moved it).
JS_SHADOW_SCROLL_INTO_VIEW = """return (function (selectors, text, tag, label) {
  function collapse(s) {
    return String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
  }
  var needle = text ? collapse(text).slice(0, 120) : '';
  function ok(el) {
    if (!el) return false;
    if (needle) return collapse(el.textContent).indexOf(needle) >= 0;
    return !tag || String(el.tagName || '').toLowerCase() === String(tag).toLowerCase();
  }
  function shown(el) {
    var n = el, g = 0;
    while (n && n.nodeType === 1 && g++ < 60) {
      if (n.hidden === true) return false;
      try {
        if (n.getAttribute && n.getAttribute('aria-hidden') === 'true') return false;
        if (n.hasAttribute && n.hasAttribute('inert')) return false;
      } catch (e) {}
      n = n.parentElement || (n.getRootNode && n.getRootNode().host) || null;
    }
    return true;
  }
  // Prefer a record-matching, NOT background candidate: the same volatile
  // identity / first-text-match ambiguity that made the probe answer FALSE also
  // made the nudge scroll the WRONG twin (an unrelated node) into view.
  var cands = [];
  var l = __wvpDeepFindLabel(label);
  if (l) cands.push(l);
  var sel = __wvpDeepFindAll(selectors);
  for (var i = 0; i < sel.length; i++) cands.push(sel[i]);
  var tx = text ? __wvpDeepTextAll(text) : [];
  for (var j = 0; j < tx.length; j++) cands.push(tx[j]);
  var hit = null;
  for (var k = 0; k < cands.length; k++) {
    if (!ok(cands[k]) || !shown(cands[k])) continue;
    hit = cands[k];
    break;
  }
  if (!hit) return false;
  try {
    hit.scrollIntoView({block: 'center', inline: 'center'});
  } catch (e) {
    try { hit.scrollIntoView(); } catch (e2) {}
  }
  return true;
})(arguments[0], arguments[1], arguments[2], arguments[3]);
"""


def _shadow_scroll_into_view(driver, event) -> None:
    """Deep-find the shadow-root target and scroll it into view (best effort)."""
    from ...web import actions as web_actions

    selectors = web_actions._css_selectors(event)
    text = (event.locator.text if event.locator else None) or None
    tag = (event.locator.tag if event.locator else None) or None
    label = (event.locator.label_text if event.locator else None) or None
    if not selectors and not text and not label:
        return
    try:
        driver.execute_script(
            web_actions.JS_DEEP_SEARCH + JS_SHADOW_SCROLL_INTO_VIEW,
            selectors, text, tag, label,
        )
    except Exception as exc:
        logger.debug("Web element_located: shadow scroll failed: %s", exc)


def element_located(driver, locator_raw, timeout: float) -> bool:
    """True when the picked element is present, rendered AND on screen.

    Deliberately stricter than ``wait_for_element`` (which returns a
    present-but-never-visible element so a guarded click can still act): a
    CONDITION must not route TRUE for an element that merely exists somewhere in
    the DOM - a hidden duplicate, a collapsed panel, or a hidden frame.  A
    target that is present but scrolled out of view (e.g. inside a scrollable
    box, or below the viewport) IS brought into view first, mirroring the web
    sequence's pre-click scroll, and the condition routes TRUE once it is on
    screen.  A target inside a shadow root is verified in-page (WebDriver
    cannot see it), so it never falls back to a light-DOM lookalike - and a
    shadow-rooted target that is present but scrolled out of view (e.g. a
    button in a scrollable popup modal) is deep-scrolled into view first, the
    same way as a light-DOM target.
    """
    event = _locator_event(locator_raw, "element_located")
    if event is None:
        logger.warning("Web element_located: no element locator configured")
        return False

    deadline = time.time() + max(0.5, timeout)
    from ...web import actions as web_actions

    shadow = _needs_deep(event)
    while True:
        # Re-enter the window the element was picked in (a popup opens on top
        # of the opener): a same-host popup cannot be told apart by URL, so
        # the recorded window ordinal is the ground truth.
        web_actions.switch_to_window_ordinal(
            driver, event.window_ordinal, wait_s=0.5
        )
        if shadow:
            # Shadow-root target: verified in-page (WebDriver cannot reach it).
            verdict = _shadow_on_screen(driver, event)
            if verdict is True:
                return True
            if verdict is False:
                # Found but off screen (present, rendered, scrolled out of a
                # scrollable popup / the viewport): deep-scroll it into view and
                # re-probe - the same pre-action nudge a web sequence performs.
                _shadow_scroll_into_view(driver, event)
                if _shadow_on_screen(driver, event) is True:
                    return True
        else:
            # Evaluate from the top of the CURRENT page state, visible frames only.
            try:
                driver.switch_to.default_content()
            except Exception:
                pass
            if _resolve_on_screen(driver, event, deadline) is not None:
                return True
        if time.time() >= deadline:
            logger.info("Web element_located: element not on screen")
            return False
        time.sleep(_POLL_S)


# Visible text of the WHOLE page, shadow-piercing: ``document.body.innerText``
# stops at a shadow boundary (LinkedIn-style web components render their text
# into a shadow root) and ignores same-origin iframes entirely - so a
# page-scoped "text present" saw neither.  Light-DOM innerText keeps its
# layout-aware filtering; every open shadow root and same-origin frame document
# contributes its textContent.
JS_PAGE_TEXT = """return (function () {
  var parts = [];
  try { if (document.body) parts.push(document.body.innerText || ''); } catch (e) {}
  (function walk(root, depth) {
    if (depth > 12 || !root || !root.querySelectorAll) return;
    var all;
    try { all = root.querySelectorAll('*'); } catch (e) { return; }
    for (var i = 0; i < all.length; i++) {
      var h = all[i], sr = null, cd = null;
      try { sr = h.shadowRoot; } catch (e) {}
      if (sr) { try { parts.push(sr.textContent || ''); } catch (e) {} walk(sr, depth + 1); }
      else { try { cd = h.contentDocument; } catch (e) {} if (cd) walk(cd, depth + 1); }
    }
  })(document, 0);
  return parts.join('\\n');
})();
"""


def _visible_text(driver, locator_raw=None) -> str:
    """Visible text of the picked element, or the whole page's text.

    Both reads are shadow-piercing.  An element-scoped read uses the in-page
    deep search when the target is a shadow root / shadow-hosted frame (a
    native read could never see it - it returned "" and the condition always
    routed FALSE) and the native, frame-aware resolver otherwise.  The page
    read appends every open shadow root and same-origin frame (see
    ``JS_PAGE_TEXT``).
    """
    if locator_raw:
        event = _locator_event(locator_raw, "element")
        if event is not None:
            from ...web import actions as web_actions

            if _needs_deep(event):
                deep = web_actions.deep_element_text(driver, event)
                if deep is not None:
                    return deep
            try:
                element = web_actions.wait_for_element(driver, event, _Config(5.0))
                return (element.text or "") if element is not None else ""
            except Exception:
                return ""
    try:
        return driver.execute_script(JS_PAGE_TEXT) or ""
    except Exception:
        return ""


def text_present(driver, locator_raw, source, target_text, case_sensitive, timeout) -> bool:
    """True when ``target_text`` appears in the page (or picked element) text.

    ``source == 'element'`` reads the picked element's text - shadow-piercing
    when the element lives in a shadow root / shadow-hosted frame (see
    ``_visible_text``); otherwise the whole page's text (shadow roots and
    same-origin frames included).
    """
    if not str(target_text or "").strip():
        logger.warning("Web text_present: no target text configured")
        return False
    element_source = str(source or "page").strip().lower() == "element"
    if element_source and not locator_raw:
        # Never silently widen an element-scoped condition to the whole page
        # (it would route TRUE on unrelated text elsewhere): a missing pick is a
        # configuration error, not a match.
        logger.warning("Web text_present: element source selected but no element picked")
        return False
    element_locator = locator_raw if element_source else None
    deadline = time.time() + max(0.0, timeout)
    needle = str(target_text)
    while True:
        text = _visible_text(driver, element_locator)
        haystack = text if case_sensitive else text.lower()
        wanted = needle if case_sensitive else needle.lower()
        if wanted in haystack:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(_POLL_S)


def browser_js(driver, js) -> bool:
    """True when the user's JS snippet run in the page returns a truthy value."""
    if not str(js or "").strip():
        logger.warning("Web browser_js: no JS configured")
        return False
    try:
        result = driver.execute_script(str(js))
    except Exception as exc:
        logger.error("Web browser_js failed: %s", exc)
        return False
    return _js_truthy(result)


def _llm(driver, conditional_node, llm_evaluator, stop_flag) -> bool:
    """Route the page's visible text + element presence through the LLM evaluator."""
    if llm_evaluator is None:
        logger.warning("Web LLM condition: no LLM evaluator available")
        return False
    prompt = str(conditional_node.get("web_llm_prompt") or "").strip()
    if not prompt:
        logger.warning("Web LLM condition: no prompt configured")
        return False

    page_text = _visible_text(driver, None)
    context = f"Page visible text:\n{page_text}"
    locator_raw = conditional_node.get("web_element_locator")
    if locator_raw:
        context += f"\n\nConfigured element present: {element_located(driver, locator_raw, 2.0)}"

    synth = {
        "llm_prompt": f"{prompt}\n\n{context}",
        "llm_engine": conditional_node.get("web_llm_engine") or "ollama",
        "llm_model": conditional_node.get("web_llm_model") or "",
        "llm_timeout": _as_float(conditional_node.get("web_llm_timeout"), 10.0),
        "id": conditional_node.get("id") or conditional_node.get("node_id"),
        "node_id": conditional_node.get("node_id") or conditional_node.get("id"),
    }
    try:
        return bool(llm_evaluator(synth, stop_flag))
    except TypeError:
        return bool(llm_evaluator(synth, stop_flag, []))


# Scroll-and-probe: find the element (shadow-piercing) and report whether its
# viewport box sits on the target rect within tolerance.
JS_LAYOUT_PROBE = """return (function (selector, tx, ty, tw, th, tol) {
  function deepFind(sel) {
    var out = null;
    function collect(root, depth) {
      if (out || depth > 12 || !root || !root.querySelectorAll) return;
      var r = null;
      try { r = root.querySelectorAll(sel); } catch (e) { return; }
      if (r.length) { out = r[0]; return; }
      var all = root.querySelectorAll('*');
      for (var j = 0; j < all.length; j++) {
        if (out) return;
        var h = all[j];
        if (h.shadowRoot) collect(h.shadowRoot, depth + 1);
        else if (h.contentDocument) collect(h.contentDocument, depth + 1);
      }
    }
    collect(document, 0);
    return out;
  }
  if (!selector) return {found: false};
  var el = deepFind(selector);
  if (!el) return {found: false};
  var r = el.getBoundingClientRect();
  var matched = Math.abs(r.left - tx) <= tol && Math.abs(r.top - ty) <= tol &&
                Math.abs(r.width - tw) <= tol && Math.abs(r.height - th) <= tol;
  return {found: true, matched: matched,
          rect: {x: Math.round(r.left), y: Math.round(r.top),
                 w: Math.round(r.width), h: Math.round(r.height)}};
})(arguments[0], arguments[1], arguments[2], arguments[3], arguments[4], arguments[5]);
"""


def layout_match(driver, locator_raw, rect, direction, attempts, tolerance, timeout) -> bool:
    """Scroll up/down until the element sits at the target window rect.

    The target rect is a viewport box (x, y, w, h) - the element must land
    there within ``tolerance`` px on every edge.  The element is deep-searched
    (shadow-piercing) at each step; while it is not found the page keeps
    scrolling in ``direction`` so an off-screen target is brought into view.
    """
    event = _locator_event(locator_raw, "layout_match")
    if event is None:
        logger.warning("Web layout_match: no element locator configured")
        return False
    tx, ty, tw, th = rect
    if tw <= 0 or th <= 0:
        logger.warning("Web layout_match: invalid target rect (%s)", rect)
        return False

    from ...web import actions as web_actions

    selector = web_actions._first_css_selector(event)
    if selector is None:
        logger.warning("Web layout_match: element has no CSS selector to probe")
        return False

    deadline = (time.time() + timeout) if timeout and timeout > 0 else None
    # direction: 1 = up (scroll viewport up), -1 = down (the desktop convention).
    amount = -40 if direction >= 0 else 40
    for _ in range(max(1, attempts)):
        if deadline is not None and time.time() > deadline:
            return False
        try:
            probe = driver.execute_script(JS_LAYOUT_PROBE, selector, tx, ty, tw, th, tolerance)
        except Exception as exc:
            logger.debug("Web layout_match probe failed: %s", exc)
            probe = None
        if isinstance(probe, dict) and probe.get("found") and probe.get("matched"):
            return True
        try:
            driver.execute_script("window.scrollBy(0, arguments[0]);", amount)
        except Exception:
            pass
        time.sleep(0.15)
    return False


def evaluate(conditional_node, driver, llm_evaluator=None, stop_flag=None) -> bool:
    """Dispatch a web-mode conditional by its ``web_condition_type``."""
    kind = str(conditional_node.get("web_condition_type") or "element_located").strip().lower()
    timeout = _as_float(conditional_node.get("web_timeout"), 10.0)

    if kind in ("element_located", "element", "visual", "visual_trigger"):
        return element_located(driver, conditional_node.get("web_element_locator"), timeout)
    if kind in ("text_present", "text", "loop", "conditional_loop"):
        return text_present(
            driver,
            conditional_node.get("web_text_locator"),
            conditional_node.get("web_text_source", "page"),
            conditional_node.get("web_target_text"),
            _as_bool(conditional_node.get("web_case_sensitive"), False),
            timeout,
        )
    if kind in ("browser_js", "js", "code", "code_condition"):
        return browser_js(driver, conditional_node.get("web_js"))
    if kind in ("llm", "llm_condition", "ai"):
        return _llm(driver, conditional_node, llm_evaluator, stop_flag)
    if kind in ("layout_match", "layout"):
        rect = (
            _as_int(conditional_node.get("web_layout_x"), -1),
            _as_int(conditional_node.get("web_layout_y"), -1),
            _as_int(conditional_node.get("web_layout_w"), 0),
            _as_int(conditional_node.get("web_layout_h"), 0),
        )
        return layout_match(
            driver,
            conditional_node.get("web_layout_locator"),
            rect,
            _as_int(conditional_node.get("web_layout_direction"), -1),
            _as_int(conditional_node.get("web_layout_attempts"), 20),
            _as_int(conditional_node.get("web_layout_tolerance"), 12),
            timeout,
        )
    logger.warning("Unknown web condition type: %s", kind)
    return False
