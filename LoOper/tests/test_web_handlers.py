"""Unit tests for the web replay action handlers (player/web/handlers).

Every recorded action type routes through one handler class; a handler runs a
deterministic method ladder and reports WHICH method worked.  These tests
exercise the registry, the ladder, and the per-type execute() contract.
"""

import pytest

from player.web import handlers as wh
from player.web.actions import ElementNotFoundError
from player.web.events import Event
from player.web.handlers.base import BaseHandler

_ALL_TYPES = {
    "navigate", "click", "dblclick", "contextmenu", "hover", "drag",
    "focus", "type", "key", "chrome", "scroll", "select", "submit",
}


def test_registry_covers_every_action_type():
    """One registered handler per action type the engine can dispatch."""
    assert set(wh.HANDLERS) == _ALL_TYPES
    for name, handler in wh.HANDLERS.items():
        assert isinstance(handler, BaseHandler)
        assert handler.type_name == name


def test_ladder_picks_first_success():
    """The ladder runs steps in order and returns the first one that works."""
    ran = []

    def step1():
        ran.append("s1")
        raise RuntimeError("fail")

    def step2():
        ran.append("s2")

    assert BaseHandler._ladder([("one", step1), ("two", step2)]) == "two"
    assert ran == ["s1", "s2"]


def test_ladder_propagates_last_error_when_all_fail():
    """All-fail raises the LAST error - the loudest, most specific message."""

    def step():
        raise ElementNotFoundError("nope")

    with pytest.raises(ElementNotFoundError, match="nope"):
        BaseHandler._ladder([("one", step)])


def test_execute_returns_method_report():
    """execute() never raises; it returns ok/method/error/took for the run
    log ("OK via <method>")."""
    handler = wh.HANDLERS["navigate"]
    seen = []

    class FakeDriver:
        def get(self, url):
            seen.append(url)

    evt = Event(type="navigate", ts=1.0, url="https://example.com/")
    result = handler.execute(FakeDriver(), evt, None)
    assert result["ok"] is True
    assert result["method"] == "navigate"
    assert result["error"] is None
    assert seen == ["https://example.com/"]


def test_facade_do_aliases_point_at_the_registry():
    """player.web.actions.do_* are the historic function API, re-exported
    from the handler registry - the same callables the engine dispatches."""
    from player.web import actions as web_actions

    assert web_actions.do_click.__func__ is wh.HANDLERS["click"]._execute.__func__
    assert web_actions.do_key.__func__ is wh.HANDLERS["key"]._execute.__func__
    assert web_actions.do_drag.__func__ is wh.HANDLERS["drag"]._execute.__func__


def test_drag_handler_native_chain_order(monkeypatch):
    """A drag replays as click_and_hold -> move_by_offset (recorded drop
    offset, not absolute coords) -> release, through the trusted native path."""
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def click_and_hold(self, el):
            calls.append("hold")
            return self

        def move_by_offset(self, dx, dy):
            calls.append(("move", dx, dy))
            return self

        def release(self):
            calls.append("release")
            return self

        def perform(self):
            calls.append("perform")

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: "el")
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)

    evt = Event(type="drag", ts=1.0, coordinates={"x": 10, "y": 20},
                locator=web_actions.Locator(id="src", css="div#src"))
    evt.context = {"drop_x": 60, "drop_y": 70}
    cfg = type("C", (), {"element_timeout": 15.0})()
    result = wh.HANDLERS["drag"].execute(object(), evt, cfg)
    assert result["ok"] is True and result["method"] == "native"
    assert calls == ["hold", ("move", 50, 50), "release", "perform"]


def test_drag_event_roundtrip_keeps_drop_context():
    """The recorded drag payload (start coords + drop point/locator in
    context) survives the session JSON roundtrip."""
    evt = Event(type="drag", ts=1.0, coordinates={"x": 10, "y": 20},
                context={"drop_x": 60, "drop_y": 70,
                         "drop_locator": {"id": "dst", "css": "div#dst"}})
    back = Event.from_dict(evt.to_dict())
    assert back.type == "drag"
    assert back.coordinates == {"x": 10, "y": 20}
    assert back.context["drop_x"] == 60
    assert back.context["drop_y"] == 70
    assert back.context["drop_locator"]["id"] == "dst"


def test_drag_handler_js_fallback_when_locator_unresolvable(monkeypatch):
    """When WebDriver cannot resolve the source (shadow roots), the drag
    falls back to the in-browser deep-find pointer dispatch."""
    from player.web import actions as web_actions

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append(script)
            return True

    def fail_resolve(d, e, t):
        raise ElementNotFoundError("shadow element")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)

    evt = Event(type="drag", ts=1.0, coordinates={"x": 0, "y": 0},
                locator=web_actions.Locator(id="src", css="div#src"))
    evt.context = {"drop_x": 5, "drop_y": 5}
    cfg = type("C", (), {"element_timeout": 15.0})()
    result = wh.HANDLERS["drag"].execute(FakeDriver(), evt, cfg)
    assert result["ok"] is True and result["method"] == "js"
    assert "pointermove" in executed[0]


# ---------------------------------------------------------------------------
# Layered locator tries (the web analogue of the desktop recorder's strategy
# stack): richer recorded identity -> more replay layers -> fewer misses.
# ---------------------------------------------------------------------------


def test_locator_chain_layered_order():
    """The chain runs strongest-first: id, test-hooks/data-*, attribute
    fallbacks, visible-text layers, css, classes, then structural/full xpath."""
    from player.web.events import Locator

    loc = Locator(
        tag="a",
        id="nav",
        link_text="Log in",
        text_xpath='//a[normalize-space(.)="Log in"]',
        role_text_xpath='//*[@role="link" and normalize-space(.)="Log in"]',
        css="a#nav",
        ancestor_css="header#top > nav > a:nth-of-type(2)",
        classes=["nav-link", "primary"],
        structural_xpath="/html/body/header[1]/nav[1]/a[2]",
        xpath="/html[1]/body[1]/header[1]/nav[1]/a[2]",
    )
    kinds = [c["kind"] for c in loc.chain()]
    # id first (existing contract), visible-text layers BEFORE css, classes
    # after css, xpath dead last.
    assert kinds[0] == "id"
    assert kinds.index("link_text") < kinds.index("css")
    assert kinds.index("text") < kinds.index("css")
    assert kinds.index("role_text") < kinds.index("css")
    assert kinds.index("class") > kinds.index("css")
    assert kinds[-1] == "xpath"


def test_locator_new_fields_roundtrip_and_old_session_compat():
    """New identity fields survive the session JSON roundtrip; a locator
    recorded by an OLD build (no new keys) still loads with safe defaults and
    a working chain."""
    from player.web.events import Locator

    loc = Locator(
        tag="input",
        classes=["form-control"],
        link_text=None,
        label_text="Email",
        label_xpath='//label[normalize-space(.)="Email"]/following::input[1]',
        index=2,
        text_index=1,
        viewport={"x": 100, "y": 200, "w": 300, "h": 40, "vw": 1280, "vh": 720},
    )
    back = Locator.from_dict(loc.to_dict())
    assert back.classes == ["form-control"]
    assert back.label_text == "Email"
    assert back.index == 2 and back.text_index == 1
    assert back.viewport == loc.viewport

    old = {"tag": "input", "id": "q", "css": "input#q"}  # pre-layered build
    loaded = Locator.from_dict(old)
    assert loaded.classes == [] and loaded.index is None and loaded.viewport is None
    assert [c["kind"] for c in loaded.chain()] == ["id", "css"]


def test_candidate_selector_new_kinds():
    """Every new chain kind maps to a working Selenium By + expression."""
    from selenium.webdriver.common.by import By
    from player.web import actions as web_actions

    assert web_actions._candidate_selector({"kind": "class", "value": ".btn.btn-primary"}) == \
        (By.CSS_SELECTOR, ".btn.btn-primary")
    by, value = web_actions._candidate_selector({"kind": "link_text", "value": "Log in"})
    assert by == By.XPATH and value == "//a[normalize-space(.)='Log in']"
    by, value = web_actions._candidate_selector(
        {"kind": "text", "value": '//button[normalize-space(.)="Save"]'})
    assert by == By.XPATH and value == '//button[normalize-space(.)="Save"]'
    by, value = web_actions._candidate_selector(
        {"kind": "label", "value": '//label[normalize-space(.)="Email"]/following::input[1]'})
    assert by == By.XPATH
    # quotes in attribute values are escaped, not left to break the selector
    by, value = web_actions._candidate_selector(
        {"kind": "data", "name": "placeholder", "value": "What's new?"})
    assert by == By.CSS_SELECTOR and value == "[placeholder='What\\'s new?']"


def test_xpath_literal_handles_both_quote_types():
    """XPath 1.0 has no literal that holds both quote types - concat() splices."""
    from player.web import actions as web_actions

    assert web_actions._xpath_lit("plain") == "'plain'"
    assert web_actions._xpath_lit("it's") == '"it\'s"'
    both = web_actions._xpath_lit("it's \"quoted\"")
    # concat()-spliced so both quote types survive as literals
    assert both.startswith("concat(") and both.endswith(")")
    assert "'" in both and '"' in both


def test_pick_match_uses_recorded_lookalike_index():
    """Repeated elements replay on the SAME instance: css/class candidates use
    ``index`` (sibling lookalikes), text candidates use ``text_index``."""
    from player.web import actions as web_actions
    from player.web.events import Locator

    matches = ["first", "second", "third"]
    loc = Locator(tag="button", index=2)
    assert web_actions._pick_match(matches, loc, "css") == "third"
    assert web_actions._pick_match(matches, loc, "class") == "third"
    # text kinds consult text_index instead
    loc2 = Locator(tag="button", text_index=1)
    assert web_actions._pick_match(matches, loc2, "text") == "second"
    assert web_actions._pick_match(matches, loc2, "link_text") == "second"
    # missing index degrades to the first match, never an error
    assert web_actions._pick_match(matches, Locator(tag="button"), "css") == "first"


def test_fuzzy_chain_relaxes_attribute_href_and_text():
    """When the strict chain misses, contains()-style candidates are derived
    from the recorded identity: attribute fragments, stable href path, partial
    link text, and a case-insensitive text fragment."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    evt = Event(
        type="click", ts=1.0,
        locator=Locator(
            tag="input", placeholder="Search for products",
            name="query",
        ),
    )
    fuzzy = web_actions._fuzzy_chain(evt)
    xpaths = [c["value"] for c in fuzzy]
    assert any("//*[self::input and contains(@placeholder, 'Search for products')]" == x
               for x in xpaths)
    # short fragments (<3 chars) are skipped on purpose - too noisy to match
    assert any("contains(@name, 'query')" in x for x in xpaths)

    link = Event(
        type="click", ts=1.0,
        locator=Locator(tag="a", href="https://shop.example.com/products?page=2#top",
                        link_text="View all products"),
    )
    link_fuzzy = [c["value"] for c in web_actions._fuzzy_chain(link)]
    # query/fragment stripped - only the stable path is matched
    assert any("contains(@href, 'https://shop.example.com/products')" in x
               for x in link_fuzzy)
    assert any("contains(normalize-space(.), 'View all products')" in x
               for x in link_fuzzy)

    texty = Event(
        type="click", ts=1.0,
        locator=Locator(tag="button", text="  LOG  IN  "),
    )
    text_fuzzy = [c["value"] for c in web_actions._fuzzy_chain(texty)]
    assert any("translate(" in x and "log  in" in x for x in text_fuzzy)


def _resolve_driver(found, scripts=None, scripts_return=None):
    """Minimal WebDriver stand-in for resolve_element: find_elements results
    per (by, value) call and a recording execute_script."""
    class FakeSwitch:
        def default_content(self):
            pass

        def frame(self, *args):
            pass

    class FakeDriver:
        def __init__(self):
            self.scripts = scripts if scripts is not None else []
            self._return = scripts_return if scripts_return is not None else []
            self.switch_to = FakeSwitch()

        def find_elements(self, by, value):
            return list(found.get(value, []))

        def execute_script(self, script, *args):
            self.scripts.append((script, args))
            if self._return:
                return self._return.pop(0)
            return None

    return FakeDriver()


def test_dispatch_dom_pointer_passes_recorded_ordinal():
    """The in-browser fallback receives the recorded ordinal so it clicks the
    SAME match the native ladder would (the repeating-element cursor steers
    locator.index) - without it every pass re-clicked match 0."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    driver = _resolve_driver({}, scripts_return=[True])
    evt = Event(type="click", ts=1.0,
                locator=Locator(tag="div", css="div.card", index=2))
    assert web_actions._dispatch_dom_pointer(driver, evt, None, "0", "click") is True
    script, args = driver.scripts[-1]
    assert script is web_actions.JS_DOM_POINTER
    assert args[0] == ["div.card"] and args[4] == 2  # selectors ... ordinal

    # No recorded ordinal -> None (in-page falls back to match 0).
    driver2 = _resolve_driver({}, scripts_return=[True])
    evt2 = Event(type="click", ts=1.0, locator=Locator(tag="div", css="div.card"))
    web_actions._dispatch_dom_pointer(driver2, evt2, None, "0", "click")
    assert driver2.scripts[-1][1][4] is None


def test_find_by_chain_prefers_unique_candidate_over_generic_attr():
    """A generic attribute shared by many elements must NOT win over a later,
    element-specific candidate - otherwise replay clicks the wrong row/column."""
    from player.web import actions as web_actions
    from player.web.events import Locator

    class El:
        def __init__(self, tag):
            self.tag = tag

    class Driver:
        def find_elements(self, by, value):
            if "data-component-type" in str(value):
                return [El("col0"), El("col1"), El("col2"), El("col3")]
            return [El("target")]  # the css candidate pinpoints one element

    chain = [
        {"kind": "data", "name": "data-component-type", "value": "LazyColumn"},
        {"kind": "css", "value": "div.card-x"},
    ]
    el = web_actions._find_by_chain(
        Driver(), chain, 1.0, locator=Locator(tag="div", index=0)
    )
    assert el.tag == "target"


def test_find_by_chain_falls_back_to_indexed_pick_when_none_unique():
    """With no unique candidate the recorded lookalike index still steers the
    pick (e.g. repeating elements)."""
    from player.web import actions as web_actions
    from player.web.events import Locator

    class El:
        def __init__(self, tag):
            self.tag = tag

    class Driver:
        def find_elements(self, by, value):
            return [El("r0"), El("r1"), El("r2")]

    el = web_actions._find_by_chain(
        Driver(), [{"kind": "css", "value": "div.row"}], 1.0,
        locator=Locator(tag="div", index=2),
    )
    assert el.tag == "r2"


def test_find_by_chain_never_lets_a_positional_path_win():
    """A recorded structural/absolute XPath describes WHERE the element sat on
    the RECORDING page, not what it is, so it must never outrank a candidate
    that matched by identity.

    Live regression: an Easy Apply field recorded on one job replayed on
    another - every id-bearing candidate missed, an ambiguous ``[type=text]``
    matched four elements, and the bare positional path (which resolves to
    exactly one) won and clicked an arbitrary input.
    """
    from player.web import actions as web_actions
    from player.web.events import Locator

    class El:
        def __init__(self, name):
            self.name = name

    class Driver:
        def find_elements(self, by, value):
            text = str(value)
            if text == "[type='text']":
                return [El("first-text"), El("second-text"), El("third-text")]
            if text == "/html/body/div[2]/div[1]/input[1]":
                return [El("positional-lookalike")]
            return []

    loc = Locator(
        tag="input",
        input_type="text",
        structural_xpath="/html/body/div[2]/div[1]/input[1]",
        index=0,
    )
    picked = web_actions._find_by_chain(Driver(), loc.chain(), 1.0, locator=loc)
    assert picked.name == "first-text"  # the identity fallback, never the position


def test_find_by_chain_uses_a_positional_path_when_nothing_else_matched():
    """Position stays available as a last resort: a target recorded with no
    identity at all (a bare div) still resolves by its recorded path."""
    from player.web import actions as web_actions
    from player.web.events import Locator

    class El:
        def __init__(self, name):
            self.name = name

    class Driver:
        def find_elements(self, by, value):
            if str(value) == "/html/body/div[3]":
                return [El("positional-only")]
            return []

    loc = Locator(tag="div", xpath="/html/body/div[3]")
    picked = web_actions._find_by_chain(Driver(), loc.chain(), 1.0, locator=loc)
    assert picked.name == "positional-only"


def test_locator_chain_marks_structural_paths_positional():
    """The structural and absolute XPaths are the ONLY candidates flagged
    positional; the authored identity layers stay unflagged so they keep winning
    that race."""
    from player.web.events import Locator

    loc = Locator(
        tag="a",
        css="a#nav",
        classes=["nav-link"],
        structural_xpath="/html/body/nav[1]/a[2]",
        xpath="/html[1]/body[1]/nav[1]/a[2]",
    )
    flagged = [c for c in loc.chain() if c.get("positional")]
    assert [c["value"] for c in flagged] == [
        "/html/body/nav[1]/a[2]", "/html[1]/body[1]/nav[1]/a[2]",
    ]
    assert not any(c.get("positional") for c in loc.chain() if c["kind"] != "xpath")


def test_dispatch_dom_pointer_passes_the_recorded_label():
    """A field's visible LABEL is the identity that survives a DIFFERENT
    instance of the same form, so it must reach the in-page resolver, which
    tries it before any recorded selector."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    driver = _resolve_driver({}, scripts_return=[True])
    evt = Event(type="click", ts=1.0,
                locator=Locator(tag="input", label_text="First name",
                                css="input#job-a-field"))
    assert web_actions._dispatch_dom_pointer(driver, evt, None, "0", "click") is True
    script, args = driver.scripts[-1]
    assert args[6] == "First name"
    assert "var labelEl = __wvpDeepFindLabel(label);" in script
    # The last resort is still identity-gated: a weak-candidate match is used
    # only when it AGREES with the record and is rendered.
    assert "var anyEl = __wvpDeepFindAny(selectors, want);" in script
    assert "if (anyEl && agrees(anyEl) && rendered(anyEl)) el = anyEl;" in script

    # A label with no other identity still dispatches (the label IS the target).
    driver2 = _resolve_driver({}, scripts_return=[True])
    evt2 = Event(type="click", ts=1.0,
                 locator=Locator(tag="input", label_text="City"))
    assert web_actions._dispatch_dom_pointer(driver2, evt2, None, "0", "click") is True
    assert driver2.scripts[-1][1][6] == "City"


def test_resolve_element_falls_back_to_fuzzy_layer():
    """Strict chain misses (dynamic class suffix), the fuzzy contains() layer
    still lands the element - the web analogue of a desktop low-confidence try."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    # The element's current placeholder carries a rotating suffix; the
    # recorded (strict) value no longer matches exactly, but contains() does.
    driver = _resolve_driver({
        "[name='q']": [],
        "[placeholder='Search']": [],
        "//*[self::input and contains(@placeholder, 'Search')]": ["the-input"],
    })
    evt = Event(type="click", ts=1.0,
                locator=Locator(tag="input", placeholder="Search"))
    assert web_actions.resolve_element(driver, evt, 2.0) == "the-input"


def test_resolve_element_viewport_hit_test_last_resort():
    """When every selector layer misses, the recorded document-space position
    is scrolled back into the viewport and elementFromPoint() recovers the
    element - the coordinate fallback for identity-less recordings."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    scripts = []
    driver = _resolve_driver({}, scripts=scripts, scripts_return=["the-el"])
    evt = Event(
        type="click", ts=1.0,
        locator=Locator(
            tag="div",
            viewport={"x": 100, "y": 200, "w": 40, "h": 20, "vw": 1280, "vh": 720},
        ),
    )
    assert web_actions.resolve_element(driver, evt, 1.0) == "the-el"
    script, args = scripts[-1]
    assert script is web_actions.JS_ELEMENT_FROM_POINT
    # center of the recorded rect: x+width/2, y+height/2
    assert args[:2] == (120, 210)


def test_resolve_element_viewport_only_locator_still_hits():
    """An element with no usable selector identity (viewport-only locator)
    must NOT raise on the empty chain - the coordinate hit-test resolves it."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    scripts = []
    driver = _resolve_driver({}, scripts=scripts, scripts_return=["el"])
    evt = Event(
        type="click", ts=1.0,
        locator=Locator(tag="canvas",
                        viewport={"x": 0, "y": 0, "w": 10, "h": 10,
                                  "vw": 1280, "vh": 720}),
    )
    assert web_actions.resolve_element(driver, evt, 1.0) == "el"


def test_resolve_element_loud_failure_when_every_layer_misses():
    """All layers missing raises ElementNotFoundError - never a silent no-op."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    driver = _resolve_driver({})  # nothing matches, no scripts return
    evt = Event(type="click", ts=1.0, locator=Locator(tag="button", text="Save"))
    with pytest.raises(ElementNotFoundError):
        web_actions.resolve_element(driver, evt, 1.0)


def test_chrome_handler_replays_chrome_click_at_screen_position(monkeypatch):
    """A bookmark-bar / chrome-band click replays as an OS-level click at the
    recorded absolute screen position (CDP renderer input cannot reach chrome
    UI) - the web analogue of the desktop recorder's coordinate click."""
    import player.web.handlers.chrome as chrome_handler

    clicked = []
    monkeypatch.setattr(chrome_handler, "_os_click",
                        lambda sx, sy: clicked.append((sx, sy)))

    evt = Event(type="chrome", ts=1.0, action="chrome_click",
                coordinates={"x": 1224, "y": 117},
                context={"chrome": True, "screen_x": 1324, "screen_y": 197})
    result = wh.HANDLERS["chrome"].execute(object(), evt, None)
    assert result["ok"] is True and result["method"] == "chrome_click"
    assert clicked == [(1324, 197)]


def test_chrome_handler_chrome_click_requires_screen_coords():
    """A chrome_click without screen coordinates fails loudly - never a
    silent no-op."""
    evt = Event(type="chrome", ts=1.0, action="chrome_click", context={})
    result = wh.HANDLERS["chrome"].execute(object(), evt, None)
    assert result["ok"] is False
    assert "screen coordinates" in (result["error"] or "")


def test_resolve_element_stale_frame_path_falls_back_to_frame_scan():
    """When the recorded frame_path cannot be entered (the iframe element's
    locator rotated between sessions), the action must NOT abort - the frame
    scan re-enters every iframe by index and finds the target element."""
    from selenium.webdriver.common.by import By
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    class FakeSwitch:
        def __init__(self):
            self.cur = "top"
            self.calls = []

        def default_content(self):
            self.calls.append("default")
            self.cur = "top"

        def frame(self, idx):
            self.calls.append(("frame", idx))
            self.cur = f"frame{idx}"

    class FakeDriver:
        def __init__(self):
            self.switch_to = FakeSwitch()

        def find_elements(self, by, value):
            st = self.switch_to.cur
            if st == "top":
                if by == By.TAG_NAME:
                    return ["iframe-0", "iframe-1"]
                return []  # stale iframe locator: nothing matches in the top
            if st == "frame0" and value == "div#target":
                return ["the-el"]
            return []

    driver = FakeDriver()
    # recorded frame_path points at an iframe whose own locator no longer
    # resolves (rotated id) - the element itself lives in frame index 0
    evt = Event(
        type="click", ts=1.0,
        locator=Locator(tag="div", css="div#target"),
        frame_path=[{"tag": "iframe", "id": "stale-id", "css": "iframe#stale-id"}],
        cross_origin_frame=False,
    )
    assert web_actions.resolve_element(driver, evt, 2.0) == "the-el"
    # the scan entered frame 0 (after the failed path entry reset to top)
    assert ("frame", 0) in driver.switch_to.calls


def test_resolve_element_cross_origin_frame_uses_frame_scan():
    """Cross-origin frame actions (no recorded frame_path) resolve through
    the frame-by-index scan, which runs strict then fuzzy inside each frame."""
    from selenium.webdriver.common.by import By
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    class FakeSwitch:
        def __init__(self):
            self.cur = "top"

        def default_content(self):
            self.cur = "top"

        def frame(self, idx):
            self.cur = f"frame{idx}"

    class FakeDriver:
        def __init__(self):
            self.switch_to = FakeSwitch()

        def find_elements(self, by, value):
            st = self.switch_to.cur
            if st == "top":
                if by == By.TAG_NAME:
                    return ["iframe-0"]
                return []
            if st == "frame0" and value == "body > div > button":
                return ["btn"]
            return []

    driver = FakeDriver()
    evt = Event(
        type="click", ts=1.0,
        locator=Locator(tag="button", css="body > div > button"),
        frame_path=[],
        cross_origin_frame=True,
    )
    assert web_actions.resolve_element(driver, evt, 2.0) == "btn"


# ---------------------------------------------------------------------------
# Scroll replay: a wheel scrolls the container UNDER THE CURSOR, so the burst
# replays on the nearest scrollable ancestor - never blindly on the document.
# ---------------------------------------------------------------------------


def test_scroll_script_walks_to_scrollable_ancestor():
    """JS_SCROLL must prefer the element/point under the recorded cursor and
    climb to its nearest scrollable ancestor instead of only trying the
    document (which is fixed on paned layouts like feeds/chat/lightboxes)."""
    from player.web import actions as web_actions

    assert "findScroller" in web_actions.JS_SCROLL
    assert "elementFromPoint" in web_actions.JS_SCROLL
    assert "parentElement" in web_actions.JS_SCROLL
    assert "scrollBy" in web_actions.JS_SCROLL
    # the scrollable-ancestor rule: overflow visible/hidden must NOT count
    assert "overflowY" in web_actions.JS_SCROLL
    assert "'auto'" in web_actions.JS_SCROLL and "'scroll'" in web_actions.JS_SCROLL


def test_scroll_handler_passes_cursor_position(monkeypatch):
    """The burst replays where the wheel was: the handler forwards the recorded
    cursor position to JS_SCROLL (el, delta, px, py, selector)."""
    import player.web.handlers.navigation as nav_handler
    from player.web import actions as web_actions
    from player.web.events import Locator

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))

    # the handler binds resolve_element at import time - patch its module
    monkeypatch.setattr(nav_handler, "resolve_element", lambda d, e, t: "el")
    evt = Event(
        type="scroll", ts=1.0,
        locator=Locator(tag="img", data_attrs={"data-loaded": "true"}),
        frame_path=[],
        scroll={"total_delta": 1100, "steps": 7,
                "start": {"x": 869, "y": 394}, "end": {"x": 869, "y": 394}},
    )
    cfg = type("C", (), {"element_timeout": 15.0})()
    result = wh.HANDLERS["scroll"].execute(FakeDriver(), evt, cfg)
    assert result["ok"] is True and result["method"] == "js"
    script, args = executed[0]
    assert script is web_actions.JS_SCROLL
    assert args == ("el", 1100, 869, 394, None)


def test_scroll_handler_shadow_frame_passes_selector(monkeypatch):
    """A scroll inside a shadow-hosted frame (unreachable to WebDriver) passes
    the recorded selector so JS_SCROLL scrolls THAT document, not the top."""
    import player.web.handlers.navigation as nav_handler
    from player.web.events import Locator

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append(args)

    def fail_resolve(d, e, t):
        raise ElementNotFoundError("shadow frame")

    monkeypatch.setattr(nav_handler, "resolve_element", fail_resolve)
    evt = Event(
        type="scroll", ts=1.0,
        locator=Locator(tag="div", css="div.pane"),
        context={"wvp_shadow_frame": True},
        scroll={"total_delta": -300, "steps": 3,
                "start": {"x": 50, "y": 80}, "end": {"x": 50, "y": 80}},
    )
    cfg = type("C", (), {"element_timeout": 15.0})()
    result = wh.HANDLERS["scroll"].execute(FakeDriver(), evt, cfg)
    assert result["ok"] is True and result["method"] == "js"
    # untrusted frame-relative coords dropped; the selector is forwarded.
    assert executed[0] == (None, -300, None, None, ["div.pane"])


def test_scroll_handler_shadow_dom_passes_selector(monkeypatch):
    """A scroll whose target lives inside a shadow root (recorded shadow_hosts)
    also forwards the selector, so JS_SCROLL scrolls THAT document instead of
    the top one."""
    import player.web.handlers.navigation as nav_handler
    from player.web.events import Locator

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append(args)

    def fail_resolve(d, e, t):
        raise ElementNotFoundError("shadow dom")

    monkeypatch.setattr(nav_handler, "resolve_element", fail_resolve)
    evt = Event(
        type="scroll", ts=1.0,
        locator=Locator(tag="div", css="div.pane",
                        shadow_hosts=[{"tag": "div", "id": "host"}]),
        scroll={"total_delta": -200, "steps": 2,
                "start": {"x": 5, "y": 5}, "end": {"x": 5, "y": 5}},
    )
    cfg = type("C", (), {"element_timeout": 15.0})()
    result = wh.HANDLERS["scroll"].execute(FakeDriver(), evt, cfg)
    assert result["ok"] is True and result["method"] == "js"
    assert executed[0] == (None, -200, None, None, ["div.pane"])


def test_scroll_handler_no_coords_when_unresolved_frame(monkeypatch):
    """Frame-relative cursor coords are meaningless while the driver sits in
    the top document, so a burst whose locator failed to resolve inside a
    recorded frame replays without coordinates (document fallback)."""
    import player.web.handlers.navigation as nav_handler
    from player.web.events import Locator

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append(args)

    def fail_resolve(d, e, t):
        raise ElementNotFoundError("stale frame")

    monkeypatch.setattr(nav_handler, "resolve_element", fail_resolve)
    evt = Event(
        type="scroll", ts=1.0,
        locator=Locator(tag="img", data_attrs={"data-loaded": "true"}),
        frame_path=[{"tag": "iframe", "id": "stale"}],
        scroll={"total_delta": -300, "steps": 3,
                "start": {"x": 50, "y": 80}, "end": {"x": 50, "y": 80}},
    )
    cfg = type("C", (), {"element_timeout": 15.0})()
    result = wh.HANDLERS["scroll"].execute(FakeDriver(), evt, cfg)
    assert result["ok"] is True and result["method"] == "js"
    assert executed[0] == (None, -300, None, None, None)


def test_scroll_handler_skips_when_burst_has_no_motion():
    """A zero-delta burst (wheel noise) is a no-op, never an error."""
    evt = Event(type="scroll", ts=1.0, frame_path=[],
                scroll={"total_delta": 0, "steps": 1,
                        "start": {"x": 0, "y": 0}, "end": {"x": 0, "y": 0}})
    result = wh.HANDLERS["scroll"].execute(object(), evt, None)
    assert result["ok"] is True and result["method"] == "skip"
