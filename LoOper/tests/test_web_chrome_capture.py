"""Unit tests for the OS-level browser-chrome capture (player/web/chrome_capture).

The OS hooks (``keyboard`` / pynput) are environment-dependent, so the tests
exercise the pure classification logic and the hook state machines through
faked packages - never by installing real global hooks.
"""

import sys
import types

from player.web import chrome_capture as cc
from player.web.events import Event


# ---------------------------------------------------------------------------
# classify_chrome_region (pure function)
# ---------------------------------------------------------------------------


def test_classify_chrome_region_back_forward_reload_zones():
    assert cc.classify_chrome_region(10, 20) == "back"
    assert cc.classify_chrome_region(70, 20) == "forward"
    assert cc.classify_chrome_region(120, 20) == "reload"
    assert cc.classify_chrome_region(0, 0) == "back"  # boundary


def test_classify_chrome_region_captures_the_whole_chrome_band():
    """Every click inside the chrome band (toolbar + bookmark-bar strip)
    classifies to SOMETHING: semantic actions in the left toolbar cluster,
    coordinate chrome_click elsewhere.  Only clicks clearly inside the page
    (beyond the band) return None - the DOM recorder owns those."""
    # semantic toolbar cluster
    assert cc.classify_chrome_region(10, 20) == "back"
    assert cc.classify_chrome_region(70, 20) == "forward"
    assert cc.classify_chrome_region(120, 20) == "reload"
    # right of reload / below the toolbar -> coordinate chrome click
    assert cc.classify_chrome_region(200, 20) == "chrome_click"
    assert cc.classify_chrome_region(10, 120) == "chrome_click"
    assert cc.classify_chrome_region(1224, 117) == "chrome_click"  # the missed click
    # deep in the page -> the DOM recorder owns it
    assert cc.classify_chrome_region(10, 200) is None
    assert cc.classify_chrome_region(600, 400) is None


def test_classify_chrome_region_respects_scale():
    # At scale 2.0 the zones double: x=120 moves from reload back to forward.
    assert cc.classify_chrome_region(120, 20, scale=1.0) == "reload"
    assert cc.classify_chrome_region(120, 20, scale=2.0) == "forward"
    # the chrome band scales too - x=400 stays chrome (coordinate click), and
    # a click below the doubled band is the page's
    assert cc.classify_chrome_region(400, 20, scale=2.0) == "chrome_click"
    assert cc.classify_chrome_region(400, 400, scale=2.0) is None


# ---------------------------------------------------------------------------
# KEY_NAME_MAP / CAPTURE_CHORDS
# ---------------------------------------------------------------------------


def test_key_name_map_covers_common_chrome_keys():
    assert cc.KEY_NAME_MAP["left"] == "ArrowLeft"
    assert cc.KEY_NAME_MAP["f5"] == "F5"
    assert cc.KEY_NAME_MAP["space"] == " "
    assert cc.KEY_NAME_MAP["page up"] == "PageUp"
    assert cc.KEY_NAME_MAP["+"] == "+"


def test_capture_chords_are_page_invisible_only():
    assert frozenset({"ctrl", "t"}) in cc.CAPTURE_CHORDS
    assert frozenset({"ctrl", "shift", "t"}) in cc.CAPTURE_CHORDS
    assert frozenset({"ctrl", "tab"}) in cc.CAPTURE_CHORDS
    assert frozenset({"ctrl", "9"}) in cc.CAPTURE_CHORDS
    # Page-visible chords are NOT captured by the OS hook (no double recording):
    # Alt+Left/Right, F5, Ctrl+R and zoom keys all reach the page recorder.
    assert frozenset({"alt", "left"}) not in cc.CAPTURE_CHORDS
    assert frozenset({"f5"}) not in cc.CAPTURE_CHORDS
    assert frozenset({"ctrl", "r"}) not in cc.CAPTURE_CHORDS
    assert frozenset({"ctrl", "0"}) not in cc.CAPTURE_CHORDS


def test_bare_chrome_keys_are_special_keys_only():
    """Bare chrome capture covers navigation/editing keys, never printables:
    printable single chars are page-owned, and Space/Escape are excluded (type
    buffer / stop chord)."""
    assert "enter" in cc.BARE_CHROME_KEYS
    assert "tab" in cc.BARE_CHROME_KEYS
    assert "backspace" in cc.BARE_CHROME_KEYS
    assert "left" in cc.BARE_CHROME_KEYS
    assert "f5" in cc.BARE_CHROME_KEYS
    assert "space" not in cc.BARE_CHROME_KEYS
    assert "escape" not in cc.BARE_CHROME_KEYS
    assert "a" not in cc.BARE_CHROME_KEYS
    assert "1" not in cc.BARE_CHROME_KEYS


# ---------------------------------------------------------------------------
# ChromeKeyHook (fake keyboard package)
# ---------------------------------------------------------------------------


class FakeKeyboardEvent:
    def __init__(self, name, event_type):
        self.name = name
        self.event_type = event_type


def _install_fake_keyboard(monkeypatch):
    """Patch sys.modules['keyboard'] so ChromeKeyHook never touches the OS."""
    fake_keyboard = types.ModuleType("keyboard")
    captured = []

    def fake_hook(callback, suppress=False):
        captured.append(callback)

    fake_keyboard.hook = fake_hook
    monkeypatch.setitem(sys.modules, "keyboard", fake_keyboard)
    return captured


def test_chrome_key_hook_emits_chrome_tagged_event(monkeypatch):
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 111)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    cb(FakeKeyboardEvent("ctrl", "down"))
    cb(FakeKeyboardEvent("t", "down"))
    cb(FakeKeyboardEvent("t", "up"))
    cb(FakeKeyboardEvent("ctrl", "up"))

    events = hook.drain()
    assert len(events) == 1
    evt = events[0]
    assert evt.type == "key"
    assert evt.key == "t"
    assert evt.modifiers == ["Ctrl"]
    assert evt.context == {"chrome": True}
    assert evt.url is None  # never touched from the hook thread (crash guard)
    assert isinstance(evt.ts, float)


def test_chrome_key_hook_uppercases_shifted_letters(monkeypatch):
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 111)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    cb(FakeKeyboardEvent("ctrl", "down"))
    cb(FakeKeyboardEvent("shift", "down"))
    cb(FakeKeyboardEvent("t", "down"))
    cb(FakeKeyboardEvent("t", "up"))
    cb(FakeKeyboardEvent("shift", "up"))
    cb(FakeKeyboardEvent("ctrl", "up"))

    events = hook.drain()
    assert len(events) == 1
    assert events[0].key == "T"  # browser e.key uppercases letters under Shift
    assert set(events[0].modifiers) == {"Ctrl", "Shift"}


def test_chrome_key_hook_skips_page_visible_chords(monkeypatch):
    """Alt+Left is page-visible and must NOT be captured by the OS hook."""
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: None)

    hook = cc.ChromeKeyHook()
    cb = captured[0]
    cb(FakeKeyboardEvent("alt", "down"))
    cb(FakeKeyboardEvent("left", "down"))
    cb(FakeKeyboardEvent("left", "up"))
    cb(FakeKeyboardEvent("alt", "up"))

    assert hook.drain() == []


def test_chrome_key_hook_suppresses_insert_marker(monkeypatch):
    """Insert is a pure recording marker (held for the repeating-element
    gesture) - the OS hook must never record it (auto-repeat included), but it
    must TRACK its hold so the recording loop can bridge it into the page."""
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 111)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    assert hook.marker_held is False
    for _ in range(5):
        cb(FakeKeyboardEvent("insert", "down"))  # OS auto-repeat
    assert hook.marker_held is True
    cb(FakeKeyboardEvent("insert", "up"))
    assert hook.marker_held is False
    assert hook.drain() == []


def test_chrome_key_hook_dedupes_bare_key_repeat(monkeypatch):
    """Holding a bare special key records ONE action (the `keyboard` hook fires
    a 'down' per auto-repeat); a fresh press after release records again."""
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 111)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    for _ in range(5):
        cb(FakeKeyboardEvent("enter", "down"))
    events = hook.drain()
    assert len(events) == 1
    assert events[0].key == "Enter" and events[0].state == "down"
    cb(FakeKeyboardEvent("enter", "up"))
    cb(FakeKeyboardEvent("enter", "down"))
    assert len(hook.drain()) == 1


def test_chrome_key_hook_captures_bare_enter_when_foreground(monkeypatch):
    """A bare Enter typed while the browser chrome has focus (omnibox) is
    recorded - the page recorder can never see it, the OS hook is the only
    capture path (deduped against a page twin at save time)."""
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 111)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    cb(FakeKeyboardEvent("enter", "down"))
    cb(FakeKeyboardEvent("enter", "up"))

    events = hook.drain()
    assert len(events) == 1  # downs only, like chords
    evt = events[0]
    assert evt.type == "key"
    assert evt.key == "Enter"
    assert evt.modifiers == []
    assert evt.context == {"chrome": True}


def test_chrome_key_hook_skips_bare_printable_keys(monkeypatch):
    """Bare single-char keys stay page-owned - never captured by the OS hook
    (an 'a' typed in the page is a page event; in the omnibox it is
    navigation, which the recorder does not record)."""
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 111)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    cb(FakeKeyboardEvent("a", "down"))
    cb(FakeKeyboardEvent("1", "down"))
    cb(FakeKeyboardEvent("space", "down"))
    cb(FakeKeyboardEvent("escape", "down"))

    assert hook.drain() == []


def test_chrome_key_hook_bare_keys_respect_foreground_check(monkeypatch):
    """Bare keys are gated by the same fail-closed foreground check as chords:
    typing Enter in another app while recording must not leak into the session."""
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 999)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    cb(FakeKeyboardEvent("enter", "down"))

    assert hook.drain() == []


def test_chrome_key_hook_skips_when_browser_not_foreground(monkeypatch):
    """With a known session pid, chords typed in another app are not recorded."""
    captured = _install_fake_keyboard(monkeypatch)
    monkeypatch.setattr(cc, "_foreground_pid", lambda: 999)

    hook = cc.ChromeKeyHook(foreground_pid=111)
    cb = captured[0]
    cb(FakeKeyboardEvent("ctrl", "down"))
    cb(FakeKeyboardEvent("t", "down"))

    assert hook.drain() == []


# ---------------------------------------------------------------------------
# ChromeMouseHook (fake pynput package)
# ---------------------------------------------------------------------------


def _install_fake_pynput(monkeypatch):
    """Patch sys.modules['pynput'] so ChromeMouseHook never touches the OS."""
    listener_calls = []

    class FakeListener:
        def __init__(self, on_click=None):
            self.on_click = on_click

        def start(self):
            listener_calls.append(("start",))

        def stop(self):
            listener_calls.append(("stop",))

    fake_pynput = types.ModuleType("pynput")
    fake_mouse = types.ModuleType("pynput.mouse")
    fake_mouse.Listener = FakeListener
    fake_pynput.mouse = fake_mouse
    monkeypatch.setitem(sys.modules, "pynput", fake_pynput)
    monkeypatch.setitem(sys.modules, "pynput.mouse", fake_mouse)
    return listener_calls


def test_chrome_mouse_hook_classifies_toolbar_clicks(monkeypatch):
    listener_calls = _install_fake_pynput(monkeypatch)

    hook = cc.ChromeMouseHook(window_rect_cb=lambda: (0, 0, 1200, 800))
    hook.start()
    hook._on_click(30, 20, None, True)    # back button
    hook._on_click(600, 400, None, True)  # page click - owned by the DOM recorder
    hook._on_click(30, 20, None, False)   # release - ignored
    hook.stop()

    events = hook.drain()
    assert len(events) == 1
    assert events[0].type == "chrome"
    assert events[0].action == "back"
    assert events[0].context == {"chrome": True}
    assert listener_calls == [("start",), ("stop",)]


def test_chrome_mouse_hook_captures_bookmark_band_click(monkeypatch):
    """A click in the band below the toolbar (bookmark bar / top strip) is
    captured as a coordinate chrome_click with BOTH window-relative and
    absolute screen coordinates - the click the DOM recorder can never see."""
    _install_fake_pynput(monkeypatch)

    hook = cc.ChromeMouseHook(window_rect_cb=lambda: (100, 80, 1500, 900))
    hook._on_click(1324, 197, None, True)  # rel (1224, 117) - the missed click

    events = hook.drain()
    assert len(events) == 1
    evt = events[0]
    assert evt.type == "chrome"
    assert evt.action == "chrome_click"
    assert evt.coordinates == {"x": 1224, "y": 117}
    assert evt.context == {"chrome": True, "screen_x": 1324, "screen_y": 197}


def test_chrome_mouse_hook_ignores_clicks_outside_browser_window(monkeypatch):
    _install_fake_pynput(monkeypatch)

    hook = cc.ChromeMouseHook(window_rect_cb=lambda: (100, 100, 1300, 900))
    hook._on_click(50, 150, None, True)   # left of the window
    hook._on_click(150, 50, None, True)   # above the window (another app)

    assert hook.drain() == []


def test_chrome_mouse_hook_forward_and_reload_regions(monkeypatch):
    _install_fake_pynput(monkeypatch)

    hook = cc.ChromeMouseHook(window_rect_cb=lambda: (0, 0, 1200, 800))
    hook._on_click(70, 20, None, True)    # forward
    hook._on_click(120, 20, None, True)   # reload

    actions = [e.action for e in hook.drain()]
    assert actions == ["forward", "reload"]


# ---------------------------------------------------------------------------
# chrome_click dedupe + session-name metadata (recorder-side helpers)
# ---------------------------------------------------------------------------


def test_dedupe_os_chrome_clicks_keeps_bookmark_bar_click():
    """A chrome-band click with NO page twin is a true chrome click (bookmark
    bar) - it stays, so the navigation the user made is not lost."""
    from player.web.recorder import _dedupe_os_chrome_clicks

    bookmark = Event(type="chrome", ts=1000.0, action="chrome_click",
                     coordinates={"x": 1224, "y": 117},
                     context={"chrome": True, "screen_x": 1324, "screen_y": 197})
    actions = [bookmark]
    assert _dedupe_os_chrome_clicks(actions) == actions


def test_dedupe_os_chrome_clicks_drops_page_twin():
    """A chrome-band click that ALSO reached the page is page-owned - the OS
    twin is dropped so replay never clicks twice."""
    from player.web.recorder import _dedupe_os_chrome_clicks

    page = Event(type="click", ts=1000.0, button="0")
    chrome_twin = Event(type="chrome", ts=1200.0, action="chrome_click",
                        context={"chrome": True, "screen_x": 100, "screen_y": 200})
    later = Event(type="chrome", ts=5000.0, action="chrome_click",
                  context={"chrome": True, "screen_x": 300, "screen_y": 400})
    kept = _dedupe_os_chrome_clicks([page, chrome_twin, later])
    assert kept == [page, later]


def test_save_session_stores_user_chosen_name(tmp_path):
    """The name chosen at record time lands in the metadata so the GUI
    library can show the friendly label instead of the timestamp filename."""
    from player.web.events import save_session, load_session

    out = tmp_path / "web_session_x.json"
    save_session([], str(out), name="Gmail login")
    data = load_session(out)
    assert data["metadata"]["name"] == "Gmail login"
    # without a name, no metadata key is written (old sessions stay clean)
    out2 = tmp_path / "web_session_y.json"
    save_session([], str(out2))
    assert "name" not in load_session(out2)["metadata"]


def test_drain_reads_memory_and_storage_mirror_and_dedupes():
    """_drain splices the live buffer AND the durable sessionStorage mirror
    (the mirror is how the click that caused a navigation is recovered from
    the next document), then dedupes the overlap by (type, ts) so nothing
    replays twice."""
    from player.web.recorder import _drain

    def mk(t, ts, css=None):
        d = {"type": t, "ts": ts, "locator": None, "frame_path": [],
             "cross_origin_frame": False, "value": None, "coordinates": None,
             "button": None, "key": None, "modifiers": [], "state": None,
             "repeat": None, "action": None, "scroll": None, "url": None,
             "title": None, "sensitive": False, "dom_snapshot": None,
             "gap_ms": None, "context": {}}
        if css:
            d["locator"] = {"css": css}
        return d

    class FakeDriver:
        def __init__(self, raw):
            self._raw = raw

        def execute_script(self, script, *args):
            return self._raw

    # memory + mirror overlap: the same click appears in both - deduped to one
    raw = [mk("click", 1000.0, "a#x"), mk("click", 1000.0, "a#x"), mk("hover", 2500.0, "a#x")]
    evts = _drain(FakeDriver(raw))
    assert [(e.type, e.ts) for e in evts] == [("click", 1000.0), ("hover", 2500.0)]

    # distinct events with different timestamps are all kept
    raw2 = [mk("click", 1000.0), mk("click", 1300.0), mk("type", 1600.0)]
    assert len(_drain(FakeDriver(raw2))) == 3
