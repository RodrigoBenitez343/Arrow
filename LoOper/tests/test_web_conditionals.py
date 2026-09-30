"""Tests for the web-mode conditional system + playback wait helpers.

Covers:
- conditional_fallback_resources/web_conditions.py evaluators (dispatch,
  text-present, browser JS, layout-match guards);
- actions.wait_for_element presence + visibility polling;
- web picker round-trip and text capture (with a fake driver);
- the recorder.js Right-Ctrl hover-hold behavior (via a node harness);
- standalone JS snippets carrying the __wvp* helper bundle they call.
"""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).parent / "js" / "recorder_hover_harness.js"
_SHADOW_HARNESS = Path(__file__).parent / "js" / "recorder_shadow_drain_harness.js"
_OVERLAY_HARNESS = Path(__file__).parent / "js" / "recorder_overlay_lifecycle_harness.js"
_ENTITY_CURSOR_HARNESS = Path(__file__).parent / "js" / "entity_cursor_harness.js"
_LABEL_LOOKUP_HARNESS = Path(__file__).parent / "js" / "label_lookup_harness.js"
_LABEL_CAPTURE_HARNESS = Path(__file__).parent / "js" / "label_capture_harness.js"
_SHADOW_PROBE_HARNESS = Path(__file__).parent / "js" / "shadow_probe_harness.js"
_POINTER_DISPATCH_HARNESS = Path(__file__).parent / "js" / "pointer_dispatch_harness.js"
_FORM_SCOPE_HARNESS = Path(__file__).parent / "js" / "form_scope_topmost_harness.js"
_ROOT_DOC_HARNESS = Path(__file__).parent / "js" / "root_doc_harness.js"
_FIELD_STATE_HARNESS = Path(__file__).parent / "js" / "field_state_error_harness.js"


class _FakeDriver:
    """Script-result stub: execute_script pops one canned result per call."""

    def __init__(self, script_results=None, raises=False):
        self._results = list(script_results or [])
        self._raises = raises

    def execute_script(self, script, *args):
        if self._raises:
            raise RuntimeError("driver dead")
        if self._results:
            return self._results.pop(0)
        return None

    def execute_cdp_cmd(self, *args, **kwargs):
        return None


class _Switch:
    def __init__(self, driver):
        self._d = driver

    def default_content(self):
        self._d._ctx = 0

    def frame(self, index):
        self._d._ctx = self._d._frames[self._d._ctx][index]

    def parent_frame(self):
        self._d._ctx = self._d._parent[self._d._ctx]


class _FrameDriver:
    """Minimal frame-tree driver for _scan_frames / _inject_all_frames.

    ``frames`` maps a context id to its child context ids (0 = top).
    """

    def __init__(self, frames, matches=()):
        self._frames = frames
        self._matches = set(matches)
        self._parent = {child: p for p, kids in frames.items() for child in kids}
        self._ctx = 0
        self.switch_to = _Switch(self)

    def find_elements(self, by, value):
        if str(value).upper() == "IFRAME":
            return [object()] * len(self._frames.get(self._ctx, []))
        return []


# ------------------------------------------------------------------ web_conditions
def test_browser_js_truthy_and_falsey():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    assert w.browser_js(_FakeDriver(["yes"]), "return true") is True
    assert w.browser_js(_FakeDriver([""]), "return true") is False
    assert w.browser_js(_FakeDriver(["false"]), "return true") is False
    assert w.browser_js(_FakeDriver([0]), "return true") is False
    # No JS configured never runs the driver.
    assert w.browser_js(_FakeDriver(["yes"]), "   ") is False


def test_browser_js_error_is_false():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    assert w.browser_js(_FakeDriver(raises=True), "return true") is False


def test_text_present_page_case():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    assert w.text_present(_FakeDriver(["Welcome to LinkedIn"]), "", "page", "linkedin", False, 0.0) is True
    assert w.text_present(_FakeDriver(["Welcome"]), "", "page", "linkedin", False, 0.0) is False
    # case-sensitive: mixed case no longer matches
    assert w.text_present(_FakeDriver(["LINKEDIN"]), "", "page", "linkedin", True, 0.0) is False


def test_text_present_requires_target_text():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    assert w.text_present(_FakeDriver(["x"]), "", "page", "   ", False, 0.0) is False


def test_text_present_element_source_requires_a_pick():
    """An element-scoped condition with no element picked must NOT widen to the
    whole page (that routed TRUE on unrelated text elsewhere)."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    assert w.text_present(_FakeDriver(["target text"]), "", "element", "target", False, 0.0) is False


def test_evaluate_dispatch_and_unknown():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    # element_located with no locator -> False (never touches the driver)
    assert w.evaluate({"web_condition_type": "element_located"}, _FakeDriver([])) is False
    # browser_js via the dispatcher
    assert w.evaluate(
        {"web_condition_type": "browser_js", "web_js": "return 1"}, _FakeDriver(["1"])
    ) is True
    assert w.evaluate({"web_condition_type": "nope"}, _FakeDriver([])) is False


class _PageDriver:
    """Page/browser stub for element_located's on-screen frame walk.

    Body-level find_elements only ever returns iframes; the on-screen probe
    pops one queued answer per call (None when the queue runs dry).
    """

    def __init__(self, probe_results=(), iframes=0):
        self._probe = list(probe_results)
        self._iframes = iframes
        self.depth = 0
        self.entered = []
        self.switch_to = self

    def execute_script(self, script, *args):
        return self._probe.pop(0) if self._probe else None

    def default_content(self):
        self.depth = 0

    def frame(self, index):
        self.entered.append((self.depth, index))
        self.depth += 1

    def parent_frame(self):
        self.depth = max(0, self.depth - 1)

    def find_elements(self, by, value):
        return [object()] * self._iframes if str(value).upper() == "IFRAME" else []


class _El:
    """Fake WebElement for the record-validation step (tag + textContent)."""

    def __init__(self, tag="button", text=""):
        self.tag_name = tag
        self.text = text

    def get_attribute(self, name):
        return self.text if name == "textContent" else None


def test_element_located_requires_element_on_screen(monkeypatch):
    """element_located must be FALSE for an element that merely exists in the
    DOM: hidden / collapsed (never visible), or a probe the browser cannot
    answer (regression: presence anywhere in the page routed TRUE)."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    monkeypatch.setattr(
        web_actions, "_find_by_chain", lambda d, chain, t, locator=None: _El("button")
    )
    scrolled = []
    monkeypatch.setattr(
        web_actions, "_scroll_into_view", lambda d, el: scrolled.append(el)
    )

    locator = json.dumps({"tag": "button", "id": "apply"})

    # Present but never reports on screen (hidden / collapsed) -> not located,
    # even though the scroll-into-view nudge was attempted.
    assert w.element_located(_PageDriver([False] * 20), locator, 0.5) is False
    assert scrolled  # the present-but-off-screen candidate was nudged into view
    # Rendered and intersecting the viewport -> located, no scroll needed.
    scrolled.clear()
    assert w.element_located(_PageDriver([True]), locator, 0.5) is True
    assert scrolled == []
    # A probe the browser cannot answer is NOT treated as located.
    assert w.element_located(_PageDriver([None] * 20), locator, 0.5) is False
    # No locator configured is never located.
    assert w.element_located(_PageDriver([True]), "", 0.5) is False


def test_element_located_scrolls_present_but_offscreen_into_view(monkeypatch):
    """A target present in the current document but scrolled out of a scrollable
    box / the viewport is scrolled into view (like a web sequence before a
    click) and then routes TRUE once it is on screen."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    monkeypatch.setattr(
        web_actions, "_find_by_chain", lambda d, chain, t, locator=None: _El("button")
    )
    scrolled = []
    monkeypatch.setattr(
        web_actions, "_scroll_into_view", lambda d, el: scrolled.append(el)
    )

    locator = json.dumps({"tag": "button", "id": "apply"})

    # Off-screen on the first probe, on screen after the scroll -> located.
    assert w.element_located(_PageDriver([False, True]), locator, 0.5) is True
    assert len(scrolled) == 1  # exactly one nudge, for the matching candidate


def test_element_located_walks_only_visible_frames(monkeypatch):
    """The current page state decides: a hidden frame is never entered, and in a
    visible frame the probe runs inside that frame's own context."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    def fake_find(driver, chain, timeout, locator=None):
        if driver.depth == 1:  # only the child frame holds the element
            return _El("button")
        raise web_actions.ElementNotFoundError("not in this frame")

    monkeypatch.setattr(web_actions, "_find_by_chain", fake_find)
    locator = json.dumps({"tag": "button", "id": "apply"})

    # Hidden frame: its own on-screen probe says False -> never entered.
    hidden = _PageDriver([False], iframes=1)
    assert w.element_located(hidden, locator, 0.5) is False
    assert hidden.entered == []

    # Visible frame: entered, element found and probed on screen inside it.
    visible = _PageDriver([True, True], iframes=1)
    assert w.element_located(visible, locator, 0.5) is True
    assert visible.entered == [(0, 0)]


def test_element_located_prefers_identity_then_exact_text(monkeypatch):
    """Candidates are tried strongest-first: attribute identity, then the
    EXACT-text layer, then the recorded structure."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    seen = {}

    def fake_find(driver, chain, timeout, locator=None):
        seen["kinds"] = [c.get("kind") for c in chain]
        return _El("a", text="Apply")

    monkeypatch.setattr(web_actions, "_find_by_chain", fake_find)

    mixed = json.dumps({
        "tag": "a",
        "id": "apply-btn",
        "href": "/jobs",
        "text": "Apply",
        "text_xpath": '//a[normalize-space(.)="Apply"]',
        "link_text": "Apply",
        "label_xpath": '//label/following::a[1]',
        "css": "a.apply",
    })
    assert w.element_located(_PageDriver([True]), mixed, 0.5) is True
    # Attribute identity wins: the weaker tiers never reach the resolver.
    assert set(seen["kinds"]) == {"id", "data"}

    # No attribute identity -> the EXACT-text layer identifies a text button.
    text_only = json.dumps({
        "tag": "button",
        "text": "Log in",
        "text_xpath": '//button[normalize-space(.)="Log in"]',
    })
    def find_text(driver, chain, timeout, locator=None):
        seen["kinds"] = [c.get("kind") for c in chain]
        return _El("button", text="Log in")

    monkeypatch.setattr(web_actions, "_find_by_chain", find_text)
    seen.clear()
    assert w.element_located(_PageDriver([True]), text_only, 0.5) is True
    assert seen["kinds"] == ["text"]

    # No attributes AND no text -> the recorded structure stands in.
    def record_structural(driver, chain, timeout, locator=None):
        seen["structural"] = [c.get("kind") for c in chain]
        return _El("div")

    monkeypatch.setattr(web_actions, "_find_by_chain", record_structural)
    plain = json.dumps({"tag": "div", "css": "div.card", "xpath": "/html/body/div[3]"})
    assert w.element_located(_PageDriver([True]), plain, 0.5) is True
    assert "css" in seen["structural"]


def test_element_located_rejects_a_text_lookalike(monkeypatch):
    """A sibling span sharing every class but rendering DIFFERENT text is NOT the
    target (live: "Easy Apply" was picked and the "Apply" span was reported) -
    the recorded text is not contained in the lookalike."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    locator = json.dumps({
        "tag": "span",
        "text": "Easy Apply",
        "text_xpath": '//span[normalize-space(.)="Easy Apply"]',
        "css": "span.artdeco-button__text",
    })

    # The wrong span is on screen and matches a candidate, but not the record.
    monkeypatch.setattr(
        web_actions, "_find_by_chain",
        lambda d, chain, t, locator=None: _El("span", text="Apply"),
    )
    assert w.element_located(_PageDriver([True] * 20), locator, 0.5) is False

    # The real element is accepted.
    monkeypatch.setattr(
        web_actions, "_find_by_chain",
        lambda d, chain, t, locator=None: _El("span", text="Easy Apply"),
    )
    assert w.element_located(_PageDriver([True]), locator, 0.5) is True


def test_element_located_ignores_tag_when_text_matches(monkeypatch):
    """The recorded TEXT is the identity: a dynamic element re-rendered with a
    different TAG but the same text (same action) is still the target - the tag
    is only required when no text was recorded."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    text_locator = json.dumps({"tag": "button", "text": "Easy Apply",
                               "css": "button.apply"})
    monkeypatch.setattr(
        web_actions, "_find_by_chain",
        lambda d, chain, t, locator=None: _El("a", text="Easy Apply"),
    )
    assert w.element_located(_PageDriver([True]), text_locator, 0.5) is True

    # An icon-only pick records no text: the tag is the only signal left.
    icon_locator = json.dumps({"tag": "button", "css": "button.icon"})
    monkeypatch.setattr(
        web_actions, "_find_by_chain",
        lambda d, chain, t, locator=None: _El("div"),
    )
    assert w.element_located(_PageDriver([True] * 20), icon_locator, 0.5) is False


def test_element_located_accepts_text_drift(monkeypatch):
    """A reused condition must not fail just because the element gained text:
    the recorded text only has to be CONTAINED (badge / suffix drift), while a
    shorter lookalike is still rejected (see ..._rejects_a_text_lookalike)."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    locator = json.dumps({
        "tag": "span", "text": "Easy Apply",
        "css": "span.artdeco-button__text",
    })
    monkeypatch.setattr(
        web_actions, "_find_by_chain",
        lambda d, chain, t, locator=None: _El("span", text="Easy Apply to this job"),
    )
    assert w.element_located(_PageDriver([True]), locator, 0.5) is True


def test_element_located_stays_in_the_recorded_frame(monkeypatch):
    """A frame-relative locator is applied to the SAME document it was picked in.

    Live regression: the frame's css was re-applied to the TOP document, so a
    lookalike there reported located regardless of the iframe being visible.
    """
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    lookups = []

    def fake_find(driver, chain, timeout, locator=None):
        values = [c.get("value") for c in chain]
        lookups.append((driver.depth, values))
        if "iframe#jobs" in values:        # the <iframe> element itself
            return "FRAME-EL"
        if driver.depth == 1:              # the target lives inside the frame
            return _El("div")
        raise web_actions.ElementNotFoundError("top document has no such element")

    monkeypatch.setattr(web_actions, "_find_by_chain", fake_find)

    frame_locator = {"tag": "iframe", "css": "iframe#jobs"}
    locator = json.dumps({
        "tag": "div",
        "css": "div.card",
        "_frame_path": [frame_locator],
        "_cross_origin": False,
    })

    # Frame on screen -> entered, and the target is found INSIDE that document.
    visible = _PageDriver([True, True], iframes=1)
    assert w.element_located(visible, locator, 0.5) is True
    assert visible.entered == [(0, "FRAME-EL")]
    assert (0, ["div.card"]) not in lookups    # the top document is never searched

    # Frame hidden -> never entered, so NOT located.
    lookups.clear()
    hidden = _PageDriver([False], iframes=1)
    assert w.element_located(hidden, locator, 0.5) is False
    assert hidden.entered == []
    assert (0, ["div.card"]) not in lookups

    # Top-document pick -> iframes are never walked, even a visible one.
    monkeypatch.setattr(
        web_actions, "_find_by_chain", lambda d, chain, t, locator=None: _El("button")
    )
    top_locator = json.dumps({
        "tag": "button", "id": "go", "_frame_path": [], "_cross_origin": False,
    })
    top = _PageDriver([True] * 10, iframes=1)
    assert w.element_located(top, top_locator, 0.5) is True
    assert top.entered == []


def test_layout_match_guards():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    # no locator -> False
    assert w.layout_match(_FakeDriver([]), "", (0, 0, 0, 0), -1, 3, 12, 0.0) is False
    # locator present but invalid (zero-size) rect -> False
    loc = json.dumps({"tag": "div", "css": "div.card"})
    assert w.layout_match(_FakeDriver([]), loc, (0, 0, 0, 0), -1, 3, 12, 0.0) is False


def test_layout_match_found_and_not_found():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    loc = json.dumps({"tag": "div", "css": "div.card"})
    found = _FakeDriver([{"found": True, "matched": True, "rect": {}}])
    assert w.layout_match(found, loc, (10, 10, 100, 50), -1, 3, 12, 0.0) is True
    # never matching -> exhausts attempts and returns False
    never = _FakeDriver([{"found": True, "matched": False}] * 4)
    assert w.layout_match(never, loc, (10, 10, 100, 50), -1, 3, 12, 0.0) is False


def test_locator_event_parsing():
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    evt = w._locator_event(json.dumps({"tag": "button", "id": "apply"}))
    assert evt is not None and evt.locator.tag == "button" and evt.locator.id == "apply"
    # the frame scan must be permitted so a framed target resolves
    assert evt.cross_origin_frame is True
    assert w._locator_event("") is None
    assert w._locator_event("not json") is None


# ------------------------------------------------------------------ wait_for_element
def _cfg(timeout):
    return type("C", (), {"element_timeout": timeout})()


def _evt():
    return type("E", (), {"locator": object()})()


def test_wait_for_element_polls_until_present(monkeypatch):
    from player.web import actions as web_actions

    calls = {"n": 0}

    def fake_resolve(driver, event, timeout):
        calls["n"] += 1
        if calls["n"] < 2:
            raise web_actions.ElementNotFoundError("not yet")
        return "el"

    monkeypatch.setattr(web_actions, "resolve_element", fake_resolve)
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)
    monkeypatch.setattr(web_actions, "_element_visibility", lambda d, e: True)

    assert web_actions.wait_for_element(object(), _evt(), _cfg(5.0)) == "el"
    assert calls["n"] == 2


def test_wait_for_element_visibility_poll(monkeypatch):
    from player.web import actions as web_actions

    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: "el")
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)
    vis = {"n": 0}

    def vis_fn(d, e):
        vis["n"] += 1
        return vis["n"] >= 2

    monkeypatch.setattr(web_actions, "_element_visibility", vis_fn)
    assert web_actions.wait_for_element(object(), _evt(), _cfg(5.0)) == "el"
    assert vis["n"] >= 2


def test_wait_for_element_present_but_never_visible(monkeypatch):
    from player.web import actions as web_actions

    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: "el")
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)
    monkeypatch.setattr(web_actions, "_element_visibility", lambda d, e: False)

    # Present but never reports visible -> still returned at the deadline.
    assert web_actions.wait_for_element(object(), _evt(), _cfg(0.5)) == "el"


def test_wait_for_element_raises_when_never_present(monkeypatch):
    from player.web import actions as web_actions

    def boom(d, e, t):
        raise web_actions.ElementNotFoundError("missing")

    monkeypatch.setattr(web_actions, "resolve_element", boom)
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions.wait_for_element(object(), _evt(), _cfg(0.5))


def test_element_visibility_none_when_probe_unavailable():
    from player.web import actions as web_actions

    # A driver whose JS bridge is down yields None (treated as visible).
    assert web_actions._element_visibility(_FakeDriver(raises=True), "el") is None
    assert web_actions._element_visibility(_FakeDriver([None]), "el") is None
    assert web_actions._element_visibility(_FakeDriver([True]), "el") is True
    assert web_actions._element_visibility(_FakeDriver([False]), "el") is False


# ------------------------------------------------------------------ web picker
def test_pick_element_returns_locator(monkeypatch):
    from player.web import picker

    result = {
        "locator": {"tag": "button", "id": "apply"},
        "rect": {"x": 5, "y": 6, "w": 7, "h": 8},
        "frame_path": [],
        "cross_origin_frame": False,
    }
    driver = _FakeDriver([True, None, result, None])  # inject, shadow-inject, poll, clear
    monkeypatch.setattr(picker, "_shared_driver", lambda chain_key=None: driver)

    assert picker.pick_element(timeout=5.0) == result


def test_pick_element_cancel(monkeypatch):
    from player.web import picker

    driver = _FakeDriver([True, None, {"cancelled": True}, None])
    monkeypatch.setattr(picker, "_shared_driver", lambda chain_key=None: driver)

    assert picker.pick_element(timeout=5.0) == {"cancelled": True}


def test_picker_poll_reads_attached_window_without_switching():
    """The steady-state picker poll must read the ATTACHED window IN PLACE.

    switch_to.window ACTIVATES the target window in Chrome, so polling every
    200ms cycle raised the browser window (and flipped between open windows)
    several times a second - stealing the user's focus and making the rest of
    the app (the graph GUI) unusable while a web conditional was set up.
    """
    from player.web import picker

    class Win:
        window_handles = ["w0", "w1"]
        current_window_handle = "w0"

        def __init__(self):
            self.switches = []
            self.switch_to = self

        def window(self, handle):
            self.switches.append(handle)

        def execute_script(self, script, *args):
            if "__wvpPickerResult || null" in script:
                return {"locator": {"tag": "button"}}
            return None

    d = Win()
    result, ordinal = picker._poll_result_any_window(d)
    assert result == {"locator": {"tag": "button"}}
    assert ordinal == 0  # w0's index in window_handles - no switch needed
    assert d.switches == []  # read in place: no window was activated


def test_picker_poll_only_scans_other_windows_when_allowed():
    """Only the slow allow_switch scan may visit the OTHER windows (a popup
    with no opener link cannot relay its pick); the attached one is never
    re-activated."""
    from player.web import picker

    class Win:
        window_handles = ["w0", "w1"]
        current_window_handle = "w0"

        def __init__(self):
            self.switches = []
            self.switch_to = self

        def window(self, handle):
            self.switches.append(handle)

        def execute_script(self, script, *args):
            return None  # nothing picked yet

    d = Win()
    assert picker._poll_result_any_window(d) == (None, None)
    assert d.switches == []  # steady-state poll never switches
    assert picker._poll_result_any_window(d, allow_switch=True) == (None, None)
    assert d.switches[0] == "w1"  # the other window was visited
    assert d.switches[-1] == "w0"  # and the attached one restored
    assert d.switches.count("w0") == 1  # never a redundant raise of it


def test_picker_attributes_a_relayed_popup_pick_to_its_own_window():
    """A popup's pick is relayed to its OPENER (so the poller stays put), which
    used to record the opener's ordinal - the popup was then never re-entered
    and a form fill resolved the page UNDER it ("the popup is ignored").  The
    pick names the window it was MADE in, so that window's ordinal must win."""
    from player.web import picker

    class Win:
        window_handles = ["w0", "w1"]

        def __init__(self):
            self._c = 0
            self.switches = []
            self.switch_to = self

        @property
        def current_window_handle(self):
            return self.window_handles[self._c]

        def window(self, handle):
            self.switches.append(handle)
            self._c = self.window_handles.index(handle)

        def execute_script(self, script, *args):
            if "__wvpPickerResult || null" in script:
                # The opener holds the RELAYED pick (made in the popup).
                return {"locator": {"tag": "button"}, "__win": "POPUP"}
            if "__wvpPickerWinId" in script:
                return "POPUP" if self.current_window_handle == "w1" else "OPENER"
            return None

    d = Win()
    result, ordinal = picker._poll_result_any_window(d)
    assert ordinal == 1                 # the POPUP window, not the opener
    assert "__win" not in result        # the helper key is never persisted
    assert d.current_window_handle == "w0"   # attached window restored

    # A cross-origin frame pick cannot be attributed by window id (its id is its
    # own frame's), so the attached ordinal is kept and no window is activated.
    class WinX(Win):
        def execute_script(self, script, *args):
            if "__wvpPickerResult || null" in script:
                return {"locator": {"tag": "button"}, "__win": "FRAME",
                        "cross_origin_frame": True}
            return super().execute_script(script, *args)

    dx = WinX()
    _, ordinal_x = picker._poll_result_any_window(dx)
    assert ordinal_x == 0
    assert dx.switches == []


def test_inject_shadow_frames_runs_js_walk():
    """The picker arms itself in already-loaded shadow-hosted frames via the
    in-page walk (WebDriver's frame tree cannot reach them)."""
    from player.web import picker

    calls = []

    class FakeDriver:
        def execute_script(self, script, *args):
            calls.append((script, args))

    picker._inject_shadow_frames(FakeDriver(), "PAYLOAD")
    assert calls and calls[0][0] is picker.JS_INJECT_SHADOW_PICKER
    assert calls[0][1] == ("PAYLOAD",)


def test_capture_text_page(monkeypatch):
    from player.web import picker

    driver = _FakeDriver(["Hello page"])
    monkeypatch.setattr(picker, "_shared_driver", lambda chain_key=None: driver)

    assert picker.capture_text(None) == "Hello page"


# ------------------------------------------------------------------ frames
def _id_locator():
    from player.web.events import Locator

    return Locator(tag="button", id="x")


def _locator_event_obj():
    return type("E", (), {"locator": _id_locator(), "cross_origin_frame": True})()


def test_scan_frames_recurses_into_nested_frames(monkeypatch):
    """A target inside a NESTED frame is found by the recursive frame scan."""
    from player.web import actions as web_actions

    frames = {0: [1], 1: [2], 2: []}  # top -> iframe0 -> nested iframe
    driver = _FrameDriver(frames, matches={2})
    seen = []

    def fake_find(d, chain, timeout, locator=None):
        seen.append(d._ctx)
        if d._ctx in d._matches:
            return "el"
        raise web_actions.ElementNotFoundError("nope")

    monkeypatch.setattr(web_actions, "_find_by_chain", fake_find)

    assert web_actions._scan_frames(driver, _locator_event_obj(), 2.0) == "el"
    assert 2 in seen
    assert driver._ctx == 0  # reset to the top document afterwards


def test_scan_frames_raises_when_absent_everywhere(monkeypatch):
    from player.web import actions as web_actions

    driver = _FrameDriver({0: [1], 1: []}, matches=set())

    def always_miss(d, chain, timeout, locator=None):
        raise web_actions.ElementNotFoundError("nope")

    monkeypatch.setattr(web_actions, "_find_by_chain", always_miss)
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions._scan_frames(driver, _locator_event_obj(), 1.0)
    assert driver._ctx == 0


def test_scan_frames_include_top_false_skips_initial_document(monkeypatch):
    """A frame-scoped event must never be matched in the top/initial document:
    the scan descends into frames but does not search the top context."""
    from player.web import actions as web_actions

    driver = _FrameDriver({0: [1], 1: []}, matches={1})  # target only in iframe 1
    seen = []

    def fake_find(d, chain, timeout, locator=None):
        seen.append(d._ctx)
        if d._ctx in d._matches:
            return "el"
        raise web_actions.ElementNotFoundError("nope")

    monkeypatch.setattr(web_actions, "_find_by_chain", fake_find)
    assert web_actions._scan_frames(
        driver, _locator_event_obj(), 2.0, include_top=False
    ) == "el"
    assert 0 not in seen and 1 in seen  # the initial frame was not searched


def test_resolve_element_frame_scope_ignores_top_lookalike(monkeypatch):
    """A frame-scoped action whose frame cannot be entered must NOT fall back
    to a lookalike in the initial document (regression: playback ran in the
    initial frame instead of the opened one)."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    driver = _FrameDriver({0: []})  # no iframes -> the recorded frame is gone
    top_hits = []

    def fake_find(d, chain, timeout, locator=None):
        top_hits.append(d._ctx)
        return "TOP-LOOKALIKE"  # only the initial document matches

    monkeypatch.setattr(web_actions, "_find_by_chain", fake_find)
    evt = Event(type="click", ts=1.0, locator=Locator(tag="button", id="x"),
                frame_index_path=[0])
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions.resolve_element(driver, evt, 0.5)
    assert top_hits == []  # the top document was never searched


def test_inject_all_frames_visits_every_frame():
    """The picker payload is injected + armed in the top and every child frame."""
    from player.web import picker

    driver = _FrameDriver({0: [1], 1: []})
    injected = []

    def fake_exec(script, *args):
        injected.append(driver._ctx)
        return True

    driver.execute_script = fake_exec
    assert picker._inject_all_frames(driver, "PAYLOAD") is True
    assert injected == [0, 1]
    assert driver._ctx == 0  # reset to the top document afterwards


# ------------------------------------------------------------------ repeating elements
class _EntEl:
    def __init__(self, text="", attrs=None):
        self.text = text
        self._attrs = attrs or {}

    def get_attribute(self, name):
        return self._attrs.get(name)

    def find_elements(self, by, value):
        return []  # a leaf item encloses no other match


class _EntContainer(_EntEl):
    """A match that ENCLOSES other matches (the outer row container)."""

    def __init__(self, text="", attrs=None, children=None):
        super().__init__(text, attrs)
        self._children = list(children or [])

    def find_elements(self, by, value):
        return list(self._children)


class _EntDriver:
    def __init__(self, elements):
        self._elements = list(elements)

    def find_elements(self, by, value):
        return list(self._elements)


def _entity_event():
    from player.web.events import Event, Locator

    return Event(type="click", ts=0.0,
                 entity={"selector": "div.card", "key_attr": "data-job-id"},
                 locator=Locator(tag="div", css="div.card"))


def test_event_entity_round_trip():
    from player.web.events import Event

    evt = Event(type="click", ts=1.0, entity={"selector": "div.x", "key_attr": "data-id"})
    raw = evt.to_dict()
    assert raw["entity"] == {"selector": "div.x", "key_attr": "data-id"}
    assert Event.from_dict(raw).entity == {"selector": "div.x", "key_attr": "data-id"}
    assert Event.from_dict({"type": "click", "ts": 1.0}).entity is None


def test_entity_prepare_first_unprocessed_then_exhausted():
    from player.web import engine as web_engine

    elements = [_EntEl("a", {"data-job-id": "a"}), _EntEl("b", {"data-job-id": "b"})]
    eng = web_engine.ReplayEngine(_EntDriver(elements), web_engine.ReplayConfig())
    evt = _entity_event()
    state = {}
    first = eng._prepare_entity(evt, state)
    assert first == {"ordinal": 0, "total": 2, "key": "a"}
    assert evt.locator.index == 0 and evt.locator.css == "div.card"
    second = eng._prepare_entity(evt, state)
    assert second["ordinal"] == 1 and second["key"] == "b"
    assert eng._prepare_entity(evt, state) is None


def test_entity_key_prefers_attr_then_position():
    """Identity is the stable data-* attr, else the element's POSITION - never
    its text, which a click can mutate (LinkedIn marks a job "Viewed") and so
    would make the same item get re-clicked."""
    from player.web import engine as web_engine

    eng = web_engine.ReplayEngine(_EntDriver([]), web_engine.ReplayConfig())
    assert eng._entity_key(_EntEl("hello"), None, 3) == "__ordinal_3"
    assert eng._entity_key(_EntEl("", {"data-id": "z"}), "data-id", 0) == "z"
    assert eng._entity_key(_EntEl(""), None, 7) == "__ordinal_7"


def test_engine_run_reports_entity_exhaustion_across_passes(monkeypatch):
    from player.web import engine as web_engine

    elements = [_EntEl("a", {"data-job-id": "a"})]
    eng = web_engine.ReplayEngine(_EntDriver(elements), web_engine.ReplayConfig())
    monkeypatch.setattr(eng, "_pause", lambda fast=False: None)
    monkeypatch.setattr(
        eng, "_dispatch",
        lambda i, e: {"index": i, "type": e.type, "ok": True,
                      "method": "stub", "error": None, "took": 0.0},
    )
    evt = _entity_event()
    state = {}
    first = eng.run([evt], entity_state=state)
    assert first["entity_total"] == 1 and first["entity_exhausted"] is False
    second = eng.run([evt], entity_state=state)
    assert second["entity_exhausted"] is True
    assert second["attempted"] == 0


def test_entity_prepare_reports_missing_when_selector_matches_nothing():
    """A kind selector that matches 0 elements is 'missing', NOT exhausted -
    a volatile selector must never be read as a finished set."""
    from player.web import engine as web_engine

    eng = web_engine.ReplayEngine(_EntDriver([]), web_engine.ReplayConfig())
    info = eng._prepare_entity(_entity_event(), {})
    assert info == {"missing": True, "selector": "div.card", "total": 0}


def test_entity_prepare_steers_with_fresh_cursor_locator():
    """Steering keeps ONLY the kind selector + ordinal, so the recorded
    instance's own layers cannot win the ladder and re-click the original."""
    from player.web import engine as web_engine
    from player.web.events import Locator

    eng = web_engine.ReplayEngine(
        _EntDriver([_EntEl("a", {"data-job-id": "a"})]),
        web_engine.ReplayConfig(),
    )
    evt = _entity_event()
    evt.locator = Locator(tag="div", css="div.card", id="inst-1", text="hello")
    eng._prepare_entity(evt, {})
    assert evt.locator.css == "div.card" and evt.locator.index == 0
    assert evt.locator.id is None and evt.locator.text is None


def test_engine_run_skips_when_entity_selector_misses(monkeypatch):
    """A stale kind selector must never fall back to the recorded element —
    that is the single row the marks were made on, so re-clicking it would
    never advance the set.  The action fails loudly instead, and the run is
    NOT reported exhausted."""
    from player.web import engine as web_engine

    eng = web_engine.ReplayEngine(_EntDriver([]), web_engine.ReplayConfig())
    monkeypatch.setattr(eng, "_pause", lambda fast=False: None)
    dispatched = []

    def fake_dispatch(i, e):
        dispatched.append(e)
        return {"index": i, "type": e.type, "ok": True,
                "method": "stub", "error": None, "took": 0.0}

    monkeypatch.setattr(eng, "_dispatch", fake_dispatch)
    stats = eng.run([_entity_event()], entity_state={})
    assert stats["entity_exhausted"] is False
    assert stats["attempted"] == 1 and len(dispatched) == 0
    assert stats["failed"] and stats["failed"][0]["error"] == (
        "repeating-element set not found")


def test_entity_prepare_skips_outer_container_matches():
    """A kind selector that ALSO matches the row container must not click it:
    a match enclosing another match is the outer div, not a repeating item."""
    from player.web import engine as web_engine

    item_a = _EntEl("a", {"data-job-id": "a"})
    item_b = _EntEl("b", {"data-job-id": "b"})
    outer = _EntContainer(attrs={"data-job-id": "box"}, children=[item_a, item_b])
    eng = web_engine.ReplayEngine(
        _EntDriver([outer, item_a, item_b]), web_engine.ReplayConfig()
    )
    evt = _entity_event()
    state = {}
    first = eng._prepare_entity(evt, state)
    assert first == {"ordinal": 1, "total": 3, "key": "a"}
    second = eng._prepare_entity(evt, state)
    assert second["ordinal"] == 2 and second["key"] == "b"
    assert eng._prepare_entity(evt, state) is None


def test_entity_selectors_order_and_dedup():
    """Candidates keep their recorded order (stable first); the legacy single
    selector is appended last when it is not already present."""
    from player.web import engine as web_engine

    eng = web_engine.ReplayEngine(_EntDriver([]), web_engine.ReplayConfig())
    assert eng._entity_selectors({}) == []
    assert eng._entity_selectors({"selector": "div.a"}) == ["div.a"]
    assert eng._entity_selectors({
        "selector": "div.a",
        "selectors": ["input[name='q']", "div.a", "input[type='text']"],
    }) == ["input[name='q']", "div.a", "input[type='text']"]
    # A single legacy selector absent from the list is still a last resort.
    assert eng._entity_selectors({
        "selector": "div.legacy", "selectors": ["input[name='q']"],
    }) == ["input[name='q']", "div.legacy"]


def test_entity_prepare_uses_first_matching_candidate():
    """Candidates are tried in order: a miss on the strongest falls through to
    the next, and the cursor is steered at the candidate that matched."""
    from player.web import engine as web_engine

    class _SelDriver:
        def __init__(self, by_selector):
            self._by = by_selector

        def find_elements(self, by, value):
            return list(self._by.get(value, []))

    elements = [_EntEl("a", {"data-job-id": "a"}), _EntEl("b", {"data-job-id": "b"})]
    eng = web_engine.ReplayEngine(
        _SelDriver({"div.card": elements}), web_engine.ReplayConfig()
    )
    evt = _entity_event()
    evt.entity = {"selector": "div.card", "key_attr": "data-job-id",
                  "selectors": ["input[name='q']", "div.card"]}
    info = eng._prepare_entity(evt, {})
    assert info == {"ordinal": 0, "total": 2, "key": "a"}
    assert evt.locator.css == "div.card" and evt.locator.index == 0


def test_entity_prepare_shadow_event_uses_in_page_cursor():
    """A shadow-DOM / shadow-hosted-frame target is enumerated inside the page
    (WebDriver cannot reach it), and a fully processed set reports exhaustion."""
    from player.web import engine as web_engine
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    calls = {}

    class _ShadowDriver:
        def __init__(self):
            self.n = 0

        def find_elements(self, by, value):
            raise AssertionError("shadow target must not be searched natively")

        def execute_script(self, script, *args):
            self.n += 1
            calls["script"] = script
            calls["args"] = args
            if self.n == 1:
                return {"selector": "div[role='switch']", "ordinal": 2,
                        "total": 5, "key": "__ordinal_2"}
            return {"selector": "div[role='switch']", "total": 5,
                    "exhausted": True}

    eng = web_engine.ReplayEngine(_ShadowDriver(), web_engine.ReplayConfig())
    evt = Event(
        type="click", ts=0.0,
        entity={"selectors": ["div[role='switch']"],
                "selector": "div[role='switch']"},
        locator=Locator(tag="div", css="div[role='switch']"),
        context={"wvp_shadow_frame": True},
    )
    first = eng._prepare_entity(evt, {})
    assert first == {"ordinal": 2, "total": 5, "key": "__ordinal_2"}
    assert calls["script"] is web_actions.JS_ENTITY_CURSOR
    assert calls["args"][0] == ["div[role='switch']"]
    assert evt.locator.css == "div[role='switch']" and evt.locator.index == 2
    assert eng._prepare_entity(evt, {}) is None  # exhausted, not missing


def test_entity_prepare_enters_recorded_frame():
    """A light-DOM target inside an iframe is enumerated INSIDE that frame
    (index path), and the driver is restored to the top document afterwards."""
    from player.web import engine as web_engine
    from player.web.events import Event, Locator

    class _SwitchTo:
        def __init__(self, driver):
            self._d = driver

        def default_content(self):
            self._d.default_calls += 1
            self._d.in_frame = False

        def frame(self, index):
            self._d.in_frame = True

        def parent_frame(self):
            pass

    class _FrameDriver:
        def __init__(self):
            self.default_calls = 0
            self.in_frame = False
            self.found_in_frame = None
            self.switch_to = _SwitchTo(self)

        def find_elements(self, by, value):
            self.found_in_frame = self.in_frame
            return [_EntEl("a", {"data-job-id": "a"})]

    driver = _FrameDriver()
    eng = web_engine.ReplayEngine(driver, web_engine.ReplayConfig())
    evt = Event(
        type="click", ts=0.0,
        entity={"selector": "input[name='q']", "key_attr": "data-job-id"},
        locator=Locator(tag="input", css="input[name='q']"),
        frame_index_path=[0],
    )
    info = eng._prepare_entity(evt, {})
    assert info == {"ordinal": 0, "total": 1, "key": "a"}
    assert driver.found_in_frame is True     # searched inside the frame
    assert driver.in_frame is False          # restored to the top document
    assert driver.default_calls >= 2


def test_pair_mark_keeps_one_repeating_action():
    """Two Insert-marked clicks define a row set: the second carries the
    verified kind and supersedes the first (which only named the element), so
    the saved session has ONE repeating action iterating the rows."""
    from player.web import recorder as web_recorder
    from player.web.events import Event

    naming = Event(type="click", ts=100.0, entity={"selector": "div.row"})
    defined = Event(type="click", ts=200.0,
                    entity={"selector": "div.list > div.row",
                            "supersedes_ts": 100.0, "supersedes_type": "click"})
    kept = web_recorder._drop_superseded_pair_marks([naming, defined])
    assert [a.ts for a in kept] == [200.0]
    assert kept[0].entity == {"selector": "div.list > div.row"}

    # No pair was recorded: every action is left untouched.
    solo = [Event(type="click", ts=1.0, entity={"selector": "div.row"})]
    assert web_recorder._drop_superseded_pair_marks(solo) == solo

    # A supersede key that matches nothing must not drop anything.
    loner = Event(type="click", ts=5.0,
                  entity={"selector": "x", "supersedes_ts": 999.0})
    both = web_recorder._drop_superseded_pair_marks([loner, solo[0]])
    assert [a.ts for a in both] == [5.0, 1.0]
    assert both[0].entity == {"selector": "x"}


# ------------------------------------------------------------------ recorder.js (node)
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_recorder_hover_hold_js():
    """recorder.js: no dwell hovers; Right Ctrl hold+move emits one hover and
    ControlRight never becomes a key action."""
    proc = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["afterPlain"] == 0
    assert data["hoverCount"] == 1
    assert data["ctrlKeyActions"] == 0
    assert data["hoverTag"] == "button"
    assert data["entitySelector"] == "div[data-job-id]"
    assert data["entityKeyAttr"] == "data-job-id"
    assert data["dataEntitySelector"] == "li[data-occludable-job-id]"
    assert data["entityModifiers"] == []
    assert data["entityKeyNoise"] == 0
    # Repeating LIST items derive from the clicked element's OWN SIBLINGS in the
    # repeating CONTAINER found by climbing - never a descendant selector on an
    # outer anchor (matched the whole nested tree, outer div first) and never
    # the clicked element's inner siblings (the card's own title/meta divs).
    assert data["brothersFirstSelector"] == "div.joblist > div"
    assert 'div[data-display-contents="true"] div' not in data["brothersFirstList"]
    assert data["rowFromInsideSelector"] == "div.joblist > div"
    # A container anchor shared by SEVERAL containers must not scope the rows:
    # the set would span them all and start in a different section.
    assert data["uniqueContainerSelector"] == "div.col.page-a > div"
    # Two marked clicks define the row set: the second carries the VERIFIED kind
    # and supersedes the first (dropped at save time).
    assert data["pairSelector"] == "div.pairlist > div.pcommon"
    assert data["pairSupersedesTs"] is not None
    assert data["unrelPairDiag"] == "no common container"
    # A zero-box row wrapper (display: contents) can never be clicked: the row
    # must be the clickable element inside it, one wrapper per row.
    assert data["zeroBoxRowSelector"] == "div.zerolist > div.zwrap > div.zcard"
    # Form controls derive a STABLE kind (the form bug: hashed classes).
    assert data["formSelector"] == 'form#survey input[name="q1"]'
    assert data["textInputSelector"] == 'input[type="text"]'
    assert data["switchSelector"] == 'div#settings div[role="switch"]'


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_entity_cursor_js():
    """actions.JS_ENTITY_CURSOR: descends into a shadow root, skips a match
    that encloses another match, skips a processed match, and reports a fully
    processed set as EXHAUSTED (never a miss, which must fall back)."""
    proc = subprocess.run(
        ["node", str(_ENTITY_CURSOR_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["firstOrdinal"] == 0
    assert data["secondOrdinal"] == 1
    assert data["skippedContainerOrdinal"] == 1
    assert data["exhausted"] is True
    assert data["miss"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_label_lookup_js():
    """actions.JS_DEEP_SEARCH.__wvpDeepFindLabel: a form control is found by its
    visible LABEL across shadow roots (the recorded id embeds per-instance
    tokens), whitespace/case are forgiven, and an absent OR ambiguous label
    returns null so the recorded selectors decide instead of a lookalike."""
    proc = subprocess.run(
        ["node", str(_LABEL_LOOKUP_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["firstOk"] is True      # the shadow-hosted control, not a lookalike
    assert data["wsOk"] is True         # case + whitespace forgiven
    assert data["lastOk"] is True
    assert data["wrapOk"] is True       # a label that WRAPS its control
    assert data["ambiguousOk"] is True  # 2 claimants -> null, never a guess
    assert data["missOk"] is True
    assert data["emptyOk"] is True
    assert data["byIdOk"] is True       # descends into the shadow root


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_deep_search_roots_at_the_top_document():
    """actions.JS_DEEP_SEARCH.__wvpRootDoc: a chain shares ONE browser and
    WebDriver keeps its frame context between commands, so an action can be left
    inside a frame a previous node entered.  The element it must act on is
    whatever is RENDERED now, which the TOP document composites - so the in-page
    search climbs to the top-most SAME-ORIGIN document instead of being trapped
    in the stale frame, and falls back to ``document`` when there is no
    ``window.parent``."""
    proc = subprocess.run(
        ["node", str(_ROOT_DOC_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["directMiss"] is True   # the stale frame holds no target
    assert data["rootIsTop"] is True    # the search climbs out of the frame
    assert data["found"] is True        # ...and resolves the top-document element
    assert data["byIdFound"] is True    # the id resolver climbs too
    assert data["fallbackOk"] is True   # no window.parent -> current document


def test_standalone_js_snippets_carry_their_helper_bundle():
    """Every JS snippet the driver runs STANDALONE must prefix the bundle that
    defines the ``__wvp*`` helpers it calls.  ``JS_VISIBLE_TEXT`` called
    ``__wvpRootDoc()`` without ``JS_DEEP_SEARCH``, so "read the text from the
    open page" died with "__wvpRootDoc is not defined" and the source silently
    read as EMPTY.

    ``JS_FOREGROUND`` and ``JS_DEEP_ELEMENT_TEXT`` are building blocks - their
    call sites always execute them concatenated behind ``JS_DEEP_SEARCH`` - so
    they are exempt.
    """
    from player.web import actions as web_actions

    bundles = {
        "JS_DEEP_SEARCH": web_actions.JS_DEEP_SEARCH,
        "JS_FOREGROUND": web_actions.JS_FOREGROUND,
    }
    defined = {}
    for bundle_name, bundle in bundles.items():
        for helper in re.findall(r"function\s+(__wvp\w+)\s*\(", bundle):
            defined.setdefault(helper, bundle_name)

    fragments = {"JS_FOREGROUND", "JS_DEEP_ELEMENT_TEXT"}
    for name in dir(web_actions):
        if not name.startswith("JS_") or name in fragments:
            continue
        script = getattr(web_actions, name)
        if not isinstance(script, str):
            continue
        # The helper bundles the snippet prefixes itself with (order-agnostic).
        rest, carried = script, set()
        while True:
            for bundle_name, bundle in bundles.items():
                if bundle_name not in carried and rest.startswith(bundle):
                    carried.add(bundle_name)
                    rest = rest[len(bundle):]
                    break
            else:
                break
        missing = sorted(
            helper for helper in set(re.findall(r"(__wvp\w+)\s*\(", script))
            if helper in defined and defined[helper] not in carried
        )
        assert not missing, (
            f"{name} calls {missing} but does not prefix "
            f"{[defined[h] for h in missing]}"
        )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_page_error_reads_a_localized_message():
    """actions.JS_FIELD_STATE.pageError: a framework rejection is decided by the
    page's STRUCTURE and WIRING, never by the message wording - so it holds in
    any language and no per-locale keyword list has to grow.  A Spanish
    "Introduce una respuesta valida" was ignored by the English keyword list, so
    the rejected field was never flagged, the form filler kept skipping it as
    already-filled, and the review loop re-ran forever without correcting it."""
    proc = subprocess.run(
        ["node", str(_FIELD_STATE_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["localized"]            # error-classed channel, any language
    assert data["ariaInvalid"]          # aria-invalid state, any language
    assert data["wiredErrormessage"]    # the field NAMES its message (aria-errormessage)
    assert data["wiredDescribedby"]     # aria-describedby -> an error channel
    assert data["helperText"] == ""     # describedby WITHOUT an error channel = helper
    assert data["monthHeader"] == ""    # picker live region: not an error
    assert data["english"]              # weak role=alert + keyword fallback
    assert data["ownValue"] == ""       # the field's own value is not feedback


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_shadow_probe_js():
    """web_conditions.JS_SHADOW_ON_SCREEN: the in-page on-screen probe for a
    shadow-root target must AGREE with the record - the recorded TEXT is the
    identity (a dynamic element may re-render with a different tag), a shorter
    lookalike is rejected, a missing target returns null, a hidden target
    returns false, and the tag only gates when no text was recorded."""
    proc = subprocess.run(
        ["node", str(_SHADOW_PROBE_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["matchOk"] is True       # lookalike rejected, real target by text
    assert data["missOk"] is True        # nothing matches -> null, never TRUE
    assert data["hiddenOk"] is True      # right element but hidden -> false
    assert data["tagIgnoredOk"] is True  # different tag, same text -> TRUE
    assert data["driftOk"] is True       # extra text (reuse) -> still TRUE
    assert data["shortOk"] is True       # shorter lookalike -> rejected
    assert data["noTextTagOk"] is True   # no text -> tag is required


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_pointer_dispatch_prefers_record_agreeing_match():
    """actions.JS_DOM_POINTER: a shadow-root click must dispatch on the element
    that AGREES with the record, not the first match.

    LinkedIn/Ember ids rotate and the class candidates are weak, so first-match-
    wins clicked a wrapping container that reused the recorded id, or an
    off-screen "Next" pagination twin, and still reported success - the page
    never advanced ("Web action 0 (click) OK via js").  The visible, most
    precise record-matching element must win; a record that matches NOTHING
    dispatches nothing (the last resort is still identity-gated), so a wrong
    click can never be reported as success.  And when NOTHING agreeing is on
    screen, an outer WRAPPER that regained the volatile recorded id must still
    never be clicked in place of the button it holds."""
    from player.web import actions as web_actions

    # Contract: the resolution is identity-aware and stays element-based.
    assert "__wvpDeepFindAll" in web_actions.JS_DOM_POINTER
    assert "__wvpDeepTextAll" in web_actions.JS_DOM_POINTER
    assert "elementFromPoint" not in web_actions.JS_DOM_POINTER

    proc = subprocess.run(
        ["node", str(_POINTER_DISPATCH_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["idReuseOk"] is True    # a container reusing the id is not clicked
    assert data["offscreenOk"] is True  # visible modal button beats a pagination twin
    assert data["fallbackOk"] is True   # nothing agrees -> no dispatch, not a wrong click
    assert data["wrapperOk"] is True    # off screen: the WRAPPER is never clicked


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_form_scope_resolves_to_the_top_modal():
    """actions.JS_ENUMERATE_FORM_FIELDS: a picked container must resolve to the
    LAYER the user picked.

    The container's stable-shape candidates (``form > div.ph5``, ``div.ph5``,
    ``form > div``) are tried before the hashed recorded css, and on a page with
    a SHADOW-HOSTED modal (LinkedIn's interop-outlet) a weak candidate also
    matches the page's own form.  The deep search used to return as soon as the
    FIRST root matched - the LIGHT DOM is searched before any shadow root - so
    the scope resolved to the page UNDERNEATH the modal, and because a scope
    drops the covered/on-top gate the fields BEHIND the modal were enumerated
    ("it only sees the base one").  Among every deep match, the rendered + ON
    TOP (uncovered) one must win."""
    from player.web import actions as web_actions

    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "__wvpDeepAll" in enum_js          # every deep match is inspected
    assert "__wvpTopmost" in enum_js          # the uncovered (modal) one wins

    proc = subprocess.run(
        ["node", str(_FORM_SCOPE_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["scopedOk"] is True   # scoped scan enumerates the MODAL, not the base
    assert data["wholeOk"] is True    # foreground-only scan skips the covered base
    assert data["hostOk"] is True     # a pick that resolved to the shadow HOST still
                                      # enumerates the modal inside it
    assert data["firstOk"] is True    # proof: first-match used to return the base


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_label_capture_js():
    """locators.js __wvpLoc.labelTextOf: the label is read from the control's
    OWN root, so a form rendered into a shadow root (LinkedIn's interop-outlet)
    still contributes the portable identity its replay needs - the light DOM
    holds neither the label nor the control."""
    proc = subprocess.run(
        ["node", str(_LABEL_CAPTURE_HARNESS)], capture_output=True, text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["firstOk"] is True    # found in the shadow root, whitespace collapsed
    assert data["wrapOk"] is True     # a label that WRAPS its control
    assert data["orphanOk"] is True   # unlabeled control -> None
    assert data["nonFieldOk"] is True # a LABEL is not a field


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_recorder_shadow_drain_js():
    """recorder.js __wvpDrainShadow collects a same-origin iframe inside a
    shadow root (tagged) but never a light-DOM iframe (the Python drain owns
    that one, so collecting it here would double-record)."""
    proc = subprocess.run(
        ["node", str(_SHADOW_HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["shadowCollected"] == 1
    assert data["shadowTagged"] is True
    assert data["lightCollected"] == 0
    assert data["shadowBufferLeft"] == 0  # spliced, not copied
    assert data["lightBufferLeft"] == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_recorder_overlay_survives_page_changes():
    """The recording overlay is injected at DOCUMENT CREATION (before <html>
    exists), so it must be deferred until the root appears and rebuilt whenever
    the page drops it - otherwise it showed once and vanished on every
    navigation / re-render."""
    proc = subprocess.run(
        ["node", str(_OVERLAY_HARNESS)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["afterInit"] == 0  # no <html> yet -> nothing built, no crash
    assert data["afterReady"] == 3  # box + label + cheat sheet, once root exists
    assert data["hasBox"] and data["hasCheat"]
    assert data["rebuildOk"] is True  # page dropped it -> DOM change rebuilds it
    assert data["offOk"] is True  # after stop the flag keeps it away


# ------------------------------------------------------------------ chain runtime
def test_web_condition_attaches_to_shared_session(monkeypatch):
    """A chain-run web conditional must attach to the shared browser session.

    The graph executor (WorkflowExecutor) owns no web driver - that helper lives
    on the sequence executor - so asking it for one always failed:
    live error "'WorkflowExecutor' object has no attribute
    '_get_or_create_web_driver'", after which every web conditional evaluated
    False.  The executor used here deliberately lacks the helper.
    """
    from player.multi_sequence.worflow_interpreter_modules.executor_modules\
        .conditional_ops import ConditionalMixin
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import session as web_session

    seen = {}

    def fake_session(chain_key=None):
        seen["key"] = chain_key
        return "SHARED-DRIVER"

    def fake_evaluate(node, driver, llm_evaluator=None, stop_flag=None):
        seen["driver"] = driver
        seen["node"] = node
        return True

    monkeypatch.setattr(web_session, "ensure_workbench_session", fake_session)
    monkeypatch.setattr(w, "evaluate", fake_evaluate)

    class _GraphExecutor(ConditionalMixin):
        """Mirrors WorkflowExecutor: no _get_or_create_web_driver."""

        def _evaluate_llm_condition(self, node, stop_flag, *args, **kwargs):
            return False

    node = {"web_mode": "true", "condition_type": "web"}
    assert _GraphExecutor()._evaluate_web_condition(node, None) is True
    assert seen["key"] == web_session.SHARED_WEB_SCOPE
    assert seen["driver"] == "SHARED-DRIVER"
    assert seen["node"] is node


# ------------------------------------------------------------------ dialog lifecycle
def test_web_picker_does_not_hide_dialog(monkeypatch):
    """The element picker must MINIMIZE the dialog, never hide it.

    Hiding a dialog that is running exec_() makes exec_() return Rejected, so
    the whole conditional edit - including the web condition that was just
    picked - is silently dropped as "Dialog cancelled" and the node stays a
    desktop conditional.  Guard that lifecycle against regressions.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from NGUI.dialogs import conditional_dialogs as cd

    class _Signal:
        def connect(self, *args, **kwargs):
            pass

    class _NoThread:
        """Stands in for _WebPickThread: arms nothing, returns at once."""

        def __init__(self, *args, **kwargs):
            self.picked = _Signal()
            self.finished = _Signal()

        def start(self):
            pass

    monkeypatch.setattr(cd, "_WebPickThread", _NoThread)
    dlg = cd.AdvancedConditionalDialog(None, 0, [], {})
    dlg.show()
    app.processEvents()
    try:
        dlg._pick_web_element("element")
        app.processEvents()
        assert dlg.isVisible(), "picker hid the dialog: exec_() would return Rejected"
    finally:
        dlg.close()


# ------------------------------------------------------------------ node executor
def test_node_executor_is_web_conditional_detection():
    from player.multi_sequence.conditional_fallback_resources.node_executor import NodeExecutor

    assert NodeExecutor._is_web_conditional({"web_mode": "true"}) is True
    assert NodeExecutor._is_web_conditional({"web_mode": True}) is True
    assert NodeExecutor._is_web_conditional({"web_mode": "false"}) is False
    assert NodeExecutor._is_web_conditional({"condition_type": "web"}) is True
    assert NodeExecutor._is_web_conditional({"condition_type": "presence"}) is False


def test_node_executor_routes_web_conditional_to_browser(monkeypatch):
    """A web-mode conditional is evaluated on the shared browser, never as a
    desktop presence trigger (regression: condition_type 'web' fell through to
    the presence_trigger default and warned "image not found")."""
    from player.multi_sequence.conditional_fallback_resources import node_executor as ne
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import session as web_session

    seen = {}

    def fake_evaluate(node, driver, llm_evaluator=None, stop_flag=None):
        seen["node"] = node
        seen["driver"] = driver
        return True

    monkeypatch.setattr(w, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        web_session, "ensure_workbench_session", lambda chain_key=None: "SHARED-DRIVER"
    )

    executor = ne.NodeExecutor(None, None, None, None, None, None, None)

    class _PresenceBoom:
        def check_presence_with_timeout(self, *args, **kwargs):
            raise AssertionError("web conditional ran the desktop presence trigger")

    executor.presence_trigger = _PresenceBoom()

    node = {
        "condition_type": "web",
        "web_mode": "true",
        "web_condition_type": "element_located",
        "web_element_locator": '{"tag": "button", "id": "apply"}',
    }
    assert executor.evaluate_conditional_from_node(node) is True
    assert seen["node"] is node
    assert seen["driver"] == "SHARED-DRIVER"


def test_locator_event_reads_window_and_index_scope():
    """A pick carries its window ordinal + index frame chain into the Event,
    so a later condition searches the exact window/frame it was picked in."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    evt = w._locator_event(json.dumps({
        "tag": "button", "id": "apply",
        "_frame_path": [], "_cross_origin": False,
        "_window_ordinal": 1, "_frame_index_path": [2, 0],
    }))
    assert evt.window_ordinal == 1
    assert evt.frame_index_path == [2, 0]
    # Legacy pick (no scope keys) -> no window/frame hint, old behaviour.
    legacy = w._locator_event(json.dumps({"tag": "button", "id": "x"}))
    assert legacy.window_ordinal is None and legacy.frame_index_path == []


def test_element_located_switches_to_recorded_window(monkeypatch):
    """element_located re-enters the window the element was picked in - a
    same-host popup cannot be told apart by URL (regression: the condition
    searched the opener and reported 'element not on screen')."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    monkeypatch.setattr(
        web_actions, "_find_by_chain", lambda d, chain, t, locator=None: _El("button")
    )
    monkeypatch.setattr(w, "_element_on_screen", lambda d, el: True)

    class WinDriver:
        window_handles = ["w0", "w1"]

        def __init__(self):
            self._c = 0
            self.switch_to = self

        @property
        def current_window_handle(self):
            return self.window_handles[self._c]

        def window(self, handle):
            self._c = self.window_handles.index(handle)

        def default_content(self):
            pass

        def execute_script(self, *args):
            return None

        def find_elements(self, by, value):
            return []

    d = WinDriver()
    locator = json.dumps({
        "tag": "button", "id": "go",
        "_frame_path": [], "_cross_origin": False, "_window_ordinal": 1,
    })
    assert w.element_located(d, locator, 0.5) is True
    assert d.current_window_handle == "w1"


def test_element_located_shadow_dom_uses_in_page_probe(monkeypatch):
    """A condition on a shadow-root element is verified in-page (WebDriver
    cannot see it) instead of matching a light-DOM lookalike."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    seen = {}

    def fake_probe(driver, event):
        seen["called"] = True
        return True

    monkeypatch.setattr(w, "_shadow_on_screen", fake_probe)
    locator = json.dumps({
        "tag": "button", "css": "div.modal button",
        "xpath": "/div[1]/div[@id='artdeco-modal-outlet']/button[1]",
        "_frame_path": [], "_cross_origin": False,
    })
    assert w.element_located(object(), locator, 0.5) is True
    assert seen.get("called") is True


def test_element_located_scrolls_shadow_dom_target_into_view(monkeypatch):
    """A shadow-rooted target present but off screen (e.g. a button inside a
    scrollable popup modal) is deep-scrolled into view, then routes TRUE - the
    shadow path gets the same pre-action scroll the light-DOM path already
    has (regression: it routed FALSE while the target was merely scrolled)."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w

    probes = iter([False, True])
    calls = {"scroll": 0}

    monkeypatch.setattr(w, "_shadow_on_screen", lambda d, e: next(probes))
    monkeypatch.setattr(
        w, "_shadow_scroll_into_view", lambda d, e: calls.__setitem__("scroll", calls["scroll"] + 1)
    )
    locator = json.dumps({
        "tag": "button", "css": "button#ember395",
        "xpath": "/div[1]/div[@id='artdeco-modal-outlet']/button[1]",
        "_frame_path": [], "_cross_origin": False,
    })
    assert w.element_located(object(), locator, 0.5) is True
    assert calls["scroll"] == 1  # one deep-scroll nudge, for the off-screen target


def test_text_present_element_source_reads_shadow_in_page(monkeypatch):
    """An element-scoped text condition on a shadow-root target is read in-page:
    WebDriver cannot RETURN a shadow element, so the native read always came
    back empty and the condition routed FALSE for a visible target."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    monkeypatch.setattr(web_actions, "deep_element_text", lambda d, e: "Easy Apply")

    locator = json.dumps({
        "tag": "span", "text": "Easy Apply",
        "xpath": "/div[1]/span[1]",  # not /html-rooted -> recorded in a shadow root
        "_frame_path": [], "_cross_origin": False,
    })
    assert w.text_present(_FakeDriver([]), locator, "element", "apply", False, 0.0) is True
    assert w.text_present(_FakeDriver([]), locator, "element", "absent", False, 0.0) is False


def test_text_present_element_source_uses_native_for_light_dom(monkeypatch):
    """A light-DOM pick keeps the native, frame-aware text read."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web import actions as web_actions

    class _El2:
        text = "Welcome back"

    monkeypatch.setattr(web_actions, "wait_for_element", lambda d, e, c: _El2())

    locator = json.dumps({"tag": "div", "text": "Welcome back",
                          "xpath": "/html/body/div[1]"})
    assert w.text_present(_FakeDriver([]), locator, "element", "welcome", False, 0.0) is True
    assert w.text_present(_FakeDriver([]), locator, "element", "missing", False, 0.0) is False


def test_needs_deep_detects_shadow_hosted_frame():
    """A pick inside a shadow-HOSTED iframe is routed to the in-page deep search:
    its frame element carries shadow_hosts, which WebDriver's frame tree never
    exposes, so the recorded frame_path could never be entered natively."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web.events import Event, Locator

    shadow_frame = Locator(tag="iframe", shadow_hosts=[{"tag": "interop-outlet"}]).to_dict()
    locator = Locator(tag="button", xpath="/html/body/button[1]")
    assert w._needs_deep(Event(type="element", ts=0.0, locator=locator,
                               frame_path=[shadow_frame])) is True
    # A plain same-origin frame pick stays on the native path.
    assert w._needs_deep(Event(type="element", ts=0.0, locator=locator,
                               frame_path=[Locator(tag="iframe").to_dict()])) is False


def test_element_located_shadow_frame_uses_deep_probe(monkeypatch):
    """element_located for a target picked inside a shadow-hosted frame uses the
    in-page probe (its frame_path can never be entered by WebDriver)."""
    from player.multi_sequence.conditional_fallback_resources import web_conditions as w
    from player.web.events import Locator

    seen = {}
    monkeypatch.setattr(w, "_shadow_on_screen",
                        lambda d, e: seen.__setitem__("called", True) or True)
    locator = {
        "tag": "button", "xpath": "/html/body/button[1]",
        "_frame_path": [Locator(tag="iframe", shadow_hosts=[{"tag": "x"}]).to_dict()],
        "_cross_origin": False,
    }
    assert w.element_located(object(), json.dumps(locator), 0.5) is True
    assert seen.get("called") is True
