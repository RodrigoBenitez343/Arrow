"""Self-checks for pointer movement and the ONE element it aims at.

A web sequence must NEVER steer the real pointer: the cursor that walks to each
target lives inside the browser (``exec_overlay``), the way the desktop player's
animated cursor walks on screen for a desktop sequence.  The OS glide is for the
paths that really click on screen (desktop), and stays parked under the suite's
``LOOPER_OS_CURSOR=off`` pin.

The overlay box and the walked cursor must also describe the element the action
ACTUALLY hits: a recorded css that now also matches a sibling (a "Back" button
where "Next" used to sit) must not aim at that lookalike.
"""
import sys
import types

from player import human_cursor
from player.web import actions as web_actions
from player.web import engine as web_engine
from player.web import exec_overlay
from player.web.events import Event, Locator


class _FakePyAutoGUI(types.ModuleType):
    def __init__(self):
        super().__init__("pyautogui")
        self.moves = []
        self.easeOutQuad = object()
        self.easeInOutQuad = object()

    def size(self):
        return (1920, 1080)

    def position(self):
        return (100, 100)

    def moveTo(self, x, y, **kwargs):
        self.moves.append((round(x), round(y)))


def _driver(reply):
    class _Driver:
        def execute_script(self, *args):
            return reply

    return _Driver()


def test_glide_walks_a_midpoint_then_the_target(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.delenv("LOOPER_OS_CURSOR", raising=False)
    human_cursor.glide_os_cursor(900, 700)
    assert fake.moves[-1] == (900, 700)
    assert len(fake.moves) >= 2  # a mid path point, not a straight jump


def test_glide_is_parked_by_the_env_switch(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setenv("LOOPER_OS_CURSOR", "off")
    human_cursor.glide_os_cursor(900, 700)
    assert fake.moves == []


def test_geometry_reply_maps_to_overlay_box_and_screen_point():
    reply = web_actions._geometry_reply([10.0, 20.0, 30.0, 40.0, 100.0, 200.0, 2.0])
    assert reply["rect"] == [10.0, 20.0, 30.0, 40.0]
    # center (25, 40) + client origin (100, 200), scaled by dpr 2
    assert reply["point"] == (250.0, 480.0)


def test_target_geometry_uses_recorded_screen_position_for_chrome():
    chrome = Event(type="chrome", ts=0, action="chrome_click",
                   context={"screen_x": 15, "screen_y": 25})
    assert web_actions.target_geometry(_driver(None), chrome) == {
        "rect": None, "point": (15.0, 25.0)}


def test_target_geometry_without_a_locator_is_none():
    assert web_actions.target_geometry(_driver(None), Event(type="navigate", ts=0)) is None


def test_web_replay_walks_the_page_cursor_and_never_the_os_pointer(monkeypatch):
    """The web path asks the PAGE to walk its own cursor, then paces the action
    behind it - the real mouse is never moved."""
    moved = []
    monkeypatch.setattr(human_cursor, "glide_os_cursor",
                        lambda x, y: moved.append((x, y)))
    walked = []

    def _fake_mark(driver, event, act, geometry=None):
        walked.append((event.type, act, geometry))
        return 0.25

    monkeypatch.setattr(exec_overlay, "mark", _fake_mark)
    slept = []
    monkeypatch.setattr(web_engine.time, "sleep", lambda s: slept.append(s))

    engine = web_engine.ReplayEngine(_driver(None), web_engine.ReplayConfig())
    click = Event(type="click", ts=0,
                  locator=Locator(tag="button", text="Next"))
    geom = {"rect": [10.0, 20.0, 30.0, 40.0], "point": (1.0, 2.0)}

    assert engine._glide_to_action(click, geom, "3: click") == 0.25
    assert walked == [("click", "3: click", geom)]
    assert slept == [0.25]          # the action waits for the cursor to arrive
    assert moved == []              # and the OS pointer is untouched

    # Key bursts never walk: they arrive in batches and would read as jitter.
    assert engine._glide_to_action(Event(type="key", ts=0, key="a")) == 0.0
    assert len(walked) == 1


def test_mark_returns_the_walk_seconds_so_the_action_waits():
    """mark() reports the in-page walk so the caller paces the action behind it."""
    assert exec_overlay._walk_seconds(320) == 0.32
    assert exec_overlay._walk_seconds(None) == 0.0     # no walk, no wait
    assert exec_overlay._walk_seconds("junk") == 0.0   # a bad reply never stalls

    class _Driver:
        def execute_script(self, script, *args):
            return 420

    event = Event(type="click", ts=0, locator=Locator(tag="button", text="Go"))
    assert exec_overlay.mark(_Driver(), event, "1: click",
                             {"rect": [1.0, 2.0, 3.0, 4.0]}) == 0.42


def test_mark_boxes_the_resolved_rect_not_a_lookalike():
    calls = []

    class _Driver:
        def execute_script(self, script, *args):
            calls.append((script, args))
            return True

    event = Event(type="click", ts=0, locator=Locator(tag="button", text="Next"))
    exec_overlay.mark(_Driver(), event, "3: click",
                      {"rect": [10.0, 20.0, 30.0, 40.0], "point": (1.0, 2.0)})
    script, args = calls[0]
    assert script is exec_overlay.JS_EXEC_MARK_RECT
    assert args[0] == [10.0, 20.0, 30.0, 40.0]
    assert "Next" in args[2]  # the recorded identity captions the box
