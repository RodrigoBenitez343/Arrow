"""Element-first replay locator: geometry, verification and click offset.

The UIA half needs a live Windows desktop and is exercised by the recording
overlay tests / manual runs; these pin the pure logic that decides WHERE to
click and WHETHER a match can be trusted.
"""
import os

import pytest

from recorder.element_recorder import attach_click_offset

locator = pytest.importorskip("player.uia_locator")


# ---------------------------------------------------------------- click point

def test_click_point_uses_the_recorded_relative_offset():
    rect = (100, 200, 300, 400)
    assert locator.click_point_from_rect(rect, 0.0, 0.0) == (100, 200)
    assert locator.click_point_from_rect(rect, 1.0, 1.0) == (300, 400)
    assert locator.click_point_from_rect(rect) == (200, 300)  # default centre


def test_click_point_survives_a_bad_offset():
    rect = (0, 0, 200, 100)
    assert locator.click_point_from_rect(rect, "x", None) == (100, 50)


# ------------------------------------------------------- size verification

def test_size_check_accepts_a_moved_but_same_sized_element():
    recorded = (0, 0, 100, 40)
    assert locator.rect_size_plausible(recorded, (500, 500, 600, 540))


def test_size_check_rejects_a_container_or_degenerate_box():
    recorded = (0, 0, 100, 40)
    assert not locator.rect_size_plausible(recorded, (0, 0, 1000, 400))  # 10x
    assert not locator.rect_size_plausible(recorded, (0, 0, 0, 0))       # empty


def test_containment_accepts_a_child_but_rejects_an_outsider():
    element = (100, 200, 300, 400)
    assert locator.rect_contains(element, (150, 250, 250, 350))  # a child
    assert locator.rect_contains(element, element)               # itself
    assert not locator.rect_contains(element, (400, 400, 500, 500))  # elsewhere


def test_rect_distance_prefers_the_same_box():
    recorded = (100, 100, 300, 300)
    same = (100, 100, 300, 300)
    assert locator._rect_distance(recorded, same) == 0
    assert locator._rect_distance(recorded, same) < locator._rect_distance(
        recorded, (400, 400, 600, 600))          # a shifted same-size box
    assert locator._rect_distance(recorded, same) < locator._rect_distance(
        recorded, (100, 100, 500, 500))          # a bigger box


# ------------------------------------------------------------ tier ordering

def test_tiers_prefer_stable_signals_and_never_go_control_type_only():
    desc = {"automation_id": "okButton", "name": "OK", "class_name": "Button",
            "control_type": 50000}
    labels = [label for _, label in locator.element_tiers(desc)]
    assert labels == ["AutomationId+ControlType", "Name+ControlType",
                      "ControlType+ClassName"]

    # No stable signal at all -> no tiers, so replay falls back to the image.
    assert locator.element_tiers({"control_type": 50000}) == []


# ------------------------------------------------------- recorded click offset

def test_attach_click_offset_records_position_inside_the_element():
    desc = {"rect": (100, 200, 300, 400), "name": "OK"}
    out = attach_click_offset(desc, 150, 250)
    assert out["rel_x"] == 0.25 and out["rel_y"] == 0.25
    assert out is not desc
    assert desc == {"rect": (100, 200, 300, 400), "name": "OK"}  # original kept


def test_attach_click_offset_degrades_on_unusable_input():
    desc = {"rect": (100, 200, 100, 400)}  # zero width
    assert attach_click_offset(desc, 5, 5) == desc
    assert attach_click_offset(None, 5, 5) is None


# --------------------------------------------------------- degrade to None

def test_resolve_degrades_to_none_without_a_usable_descriptor():
    # A missing/empty descriptor must fall back to the image cascade, never raise.
    assert locator.resolve_element(None) is None
    assert locator.resolve_element({}) is None


# ------------------------------------------------- repeating sibling set

def _row(rt, cls="row", ctype=50007, ancestors=None):
    return {"runtime_id": [rt], "control_type": ctype, "class_name": cls,
            "ancestors": ancestors or [], "rect": (0, 0, 10, 10), "process_id": 1}


def _anc(rt, ctype, cls=""):
    return {"runtime_id": [rt], "control_type": ctype, "class_name": cls,
            "automation_id": "", "name": "", "rect": (0, 0, 50, 50)}


def test_pair_derives_the_shared_row_kind():
    container = _anc(100, 50008, "list")
    a = _row(10, ancestors=[container, _anc(200, 50032, "win")])
    b = _row(11, ancestors=[container, _anc(200, 50032, "win")])
    kind = locator.sibling_kind_from_pair(a, b)
    assert kind is not None
    assert kind["kind"]["control_type"] == 50007
    assert kind["kind"]["class_name"] == "row"
    assert kind["container"]["runtime_id"] == [100]


def test_pair_uses_the_row_not_the_clicked_child():
    # Click landed INSIDE two different rows: the set is the ROWS, not the text.
    container = _anc(100, 50008, "list")
    a = _row(1, cls="text", ctype=50020,
             ancestors=[_row(10), container, _anc(200, 50032, "win")])
    b = _row(2, cls="text", ctype=50020,
             ancestors=[_row(11), container, _anc(200, 50032, "win")])
    kind = locator.sibling_kind_from_pair(a, b)
    assert kind is not None
    assert kind["kind"]["control_type"] == 50007
    assert kind["kind"]["class_name"] == "row"


def test_pair_refuses_same_element_or_class_mismatch():
    container = _anc(100, 50008, "list")
    a = _row(10, ancestors=[container])
    assert locator.sibling_kind_from_pair(a, a) is None           # same row
    assert locator.sibling_kind_from_pair(
        a, _row(10, ancestors=[container])) is None               # identical
    assert locator.sibling_kind_from_pair(
        a, _row(11, cls="other", ancestors=[container])) is None  # class mismatch


def test_pair_refuses_missing_runtime_ids_and_depth_mismatch():
    container = _anc(100, 50008, "list")
    no_rt = {"control_type": 50007, "class_name": "row", "ancestors": []}
    assert locator.sibling_kind_from_pair(no_rt, no_rt) is None
    a = _row(1, ancestors=[_row(10), container])
    b = _row(2, ancestors=[container])   # row sits one level shallower
    assert locator.sibling_kind_from_pair(a, b) is None


def test_pair_allows_an_empty_shared_class():
    # SysListView32-style rows carry no class: control type + container is the
    # verified set (the web recorder's tag-only fallback).
    container = _anc(100, 50008, "list")
    a = _row(10, cls="", ctype=50007, ancestors=[container])
    b = _row(11, cls="", ctype=50007, ancestors=[container])
    kind = locator.sibling_kind_from_pair(a, b)
    assert kind is not None
    assert kind["kind"]["control_type"] == 50007
    assert kind["kind"]["class_name"] == ""


def test_pair_carries_framework_id_as_parity():
    container = _anc(100, 50008, "list")
    a = _row(10, ancestors=[container])
    b = _row(11, ancestors=[container])
    a["framework_id"] = "Win32"
    b["framework_id"] = "Win32"
    assert locator.sibling_kind_from_pair(a, b)["kind"]["framework_id"] == "Win32"


def test_pair_omits_an_absent_framework_id():
    # A provider that exposes no framework must not over-constrain the match.
    container = _anc(100, 50008, "list")
    a = _row(10, ancestors=[container])
    b = _row(11, ancestors=[container])
    assert "framework_id" not in locator.sibling_kind_from_pair(a, b)["kind"]


def test_pair_records_the_row_name_for_file_type_parity():
    # On a shell file list the row's display name is the ONLY handle on its file
    # type - the extension is hidden from it, so the name is kept to resolve it.
    container = _anc(100, 50008, "list")
    a = _row(10, ancestors=[container])
    b = _row(11, ancestors=[container])
    a["name"] = "targeted resume"
    b["name"] = "resume_alex_doe"
    kind = locator.sibling_kind_from_pair(a, b)["kind"]
    assert kind["row_name"] == "targeted resume"


def test_pair_omits_an_absent_row_name():
    container = _anc(100, 50008, "list")
    a = _row(10, ancestors=[container])
    b = _row(11, ancestors=[container])
    assert "row_name" not in locator.sibling_kind_from_pair(a, b)["kind"]


def test_pair_records_the_row_extent_for_parity():
    # The row's BOX is what keeps a too-high container's toolbar children out of
    # the set - they share the control type but not the row's shape.
    container = _anc(100, 50008, "list")
    a = _row(10, ancestors=[container])
    b = _row(11, ancestors=[container])
    a["rect"] = (100, 200, 500, 230)
    b["rect"] = (100, 240, 500, 270)
    assert locator.sibling_kind_from_pair(a, b)["kind"]["row_rect"] == [100, 200, 500, 230]


def test_extent_parity_rejects_a_differently_shaped_candidate():
    row = (100, 200, 500, 230)       # a wide 400x30 list row
    toolbar = (40, 20, 70, 45)       # a small title-bar button
    sibling = (100, 240, 500, 270)   # same shape as the row
    assert not locator.rect_size_plausible(row, toolbar, 0.5, 2.0)
    assert locator.rect_size_plausible(row, sibling, 0.5, 2.0)


def test_repeat_cursor_degrades_without_a_kind():
    assert locator.enumerate_repeat({}, set()) == {"missing": True}
    assert locator.resolve_repeat({}, set()) == {"missing": True}
    assert locator.resolve_repeat(None, set()) == {"missing": True}


# --------------------------- container re-derived from the rows' kind

def _box(l, t, r, b):
    return type("_Rect", (), {"left": l, "top": t, "right": r, "bottom": b})()


class _Coll:
    def __init__(self, items):
        self._items = list(items)

    @property
    def Length(self):
        return len(self._items)

    def GetElement(self, index):
        return self._items[index]


class _El:
    """Minimal UIA element: runtime id, box, parent/child wiring."""

    def __init__(self, rt, rect=None, children=()):
        self._rt = rt
        self._rect = rect
        self.children = list(children)
        self.parent = None
        for child in self.children:
            child.parent = self

    def GetRuntimeId(self):
        return self._rt

    @property
    def CurrentBoundingRectangle(self):
        return self._rect

    def GetParentElement(self, element):
        return element.parent


class _Root(_El):
    """Root whose searches answer with the window's kind matches."""

    def __init__(self, hits):
        super().__init__([0], rect=_box(0, 0, 4000, 4000))
        self._hits = list(hits)

    def FindAll(self, scope, condition):
        return _Coll(self._hits)


class _List(_El):
    def FindAll(self, scope, condition):
        return _Coll(self.children)


class _FakeUia:
    def __init__(self, root):
        self._root = root
        self.ControlViewWalker = root  # an element knows its own parent

    def GetRootElement(self):
        return self._root

    def CreatePropertyCondition(self, prop, value):
        return (prop, value)

    def CreateAndCondition(self, left, right):
        return (left, right)


def test_container_is_re_derived_from_the_rows_shared_kind(monkeypatch):
    """The recorded container's identity tiers describe WHERE the list sat on
    the recording page and break first; the rows' shared kind does not, so the
    container is re-derived as the parent the marked pair actually shared."""
    rows = [_El([1], rect=_box(0, 10, 400, 40)),
            _El([2], rect=_box(0, 50, 400, 80))]
    container = _List([101], rect=_box(0, 0, 400, 300), children=rows)
    stray = _El([3], rect=_box(0, 0, 40, 20))       # a lone same-kind element
    stray.parent = _List([202], rect=_box(0, 0, 50, 50))
    uia = _FakeUia(_Root(rows + [stray]))

    desc_repeat = {"container": {"control_type": 50008, "class_name": "list"},
                   "kind": {"control_type": 50007, "class_name": "row"}}
    condition = locator._and_condition(uia, [(locator._CONTROL_TYPE, 50007)])

    # The 2-row group wins; the lone row's parent is not a repeating set.
    assert locator._find_container_by_kind(uia, desc_repeat, condition) is container

    # And the cursor walks that set instead of reporting `missing` - which the
    # caller used to read as "click the recorded row again".
    monkeypatch.setattr(locator, "_get_uia", lambda: uia)
    monkeypatch.setattr(locator, "_find_container", lambda *a, **k: None)
    first = locator.enumerate_repeat(desc_repeat, set())
    assert first["total"] == 2 and first["key"] == "ordinal_0"
    second = locator.enumerate_repeat(desc_repeat, {"ordinal_0"})
    assert second["key"] == "ordinal_1"            # advances, never repeats row 0


# ------------------------------------------------------- Insert marker key

def test_marker_active_holds_and_has_a_grace_window():
    import time as _time

    kh = pytest.importorskip("recorder.keyboard_handler")
    handler = kh.KeyboardHandler()
    assert handler.marker_active() is False
    handler.handle_keypress("insert")       # a gesture, never recorded
    assert handler.marker_active() is True
    assert handler.handle_keyrelease("insert") is None
    assert handler.marker_active() is True  # still inside the grace window
    handler._marker_up_ts = _time.time() - (kh._MARKER_GRACE_S + 1)
    assert handler.marker_active() is False


def test_marker_accepts_the_virtual_key_form():
    # pynput normally reports Insert as Key.insert, but a low-level hook can hand
    # it over as vk_45 - both must arm the marker.
    kh = pytest.importorskip("recorder.keyboard_handler")
    handler = kh.KeyboardHandler()
    handler.handle_keypress("vk_45")
    assert handler.marker_active() is True
    handler.handle_keyrelease("vk_45")


def test_runtime_id_reads_getruntimeid_and_degrades():
    overlay = pytest.importorskip("NGUI.widgets.recording_overlay")

    class _El:
        def GetRuntimeId(self):
            return (42, 7, 9)

    assert overlay._runtime_id(_El()) == [42, 7, 9]

    class _Legacy:
        CurrentRuntimeId = [1, 2]   # property form, no method

    assert overlay._runtime_id(_Legacy()) == [1, 2]

    class _Broken:
        def GetRuntimeId(self):
            raise RuntimeError("provider has no runtime id")

    assert overlay._runtime_id(_Broken()) == []


# ------------------------------------ repeat cursor persistence (no clearing)

def test_repeat_key_is_stable_and_distinguishes_sets():
    ah = pytest.importorskip("player.action_handlers")

    def build(kind_class="row", row_name="a"):
        return {"container": {"automation_id": "L", "class_name": "list"},
                "kind": {"control_type": 50007, "class_name": kind_class,
                         "row_name": row_name}}

    assert ah._repeat_key(build()) == ah._repeat_key(build())
    assert ah._repeat_key(build()) != ah._repeat_key(build("other"))
    # Two sets in ONE container that differ only by the marked row's file type
    # (PDFs vs folders on the desktop) must not share a cursor.
    assert ah._repeat_key(build()) != ah._repeat_key(build(row_name="b"))


def test_repeat_row_parity_is_backfilled_from_the_element():
    ah = pytest.importorskip("player.action_handlers")
    repeat = {"container": {}, "kind": {"control_type": 1}}
    action = {"element": {"rect": (10, 20, 300, 50), "name": "targeted resume"}}

    out = ah._with_row_parity(repeat, action)
    assert out["row_rect"] == [10, 20, 300, 50]
    assert out["kind"]["row_name"] == "targeted resume"
    assert "row_rect" not in repeat                    # original untouched
    assert "row_name" not in repeat["kind"]
    # an explicit value wins, and a missing element leaves both absent
    explicit = {"row_rect": [1, 2, 3, 4], "kind": {"row_name": "kept"}}
    assert ah._with_row_parity(explicit, action) == explicit
    assert ah._with_row_parity({"kind": {}}, {}) == {"kind": {}}


def test_visual_match_clicks_skip_the_element_locator():
    """``match == "visual"`` (right-Ctrl recording) must not run the UIA
    element-first re-find: that path can resolve to a container region in a
    subtree and land the click in the wrong spot - the legacy template match
    owns the click."""
    ah = pytest.importorskip("player.action_handlers")
    element = {"rect": (0, 0, 10, 10), "name": "row"}
    assert ah._use_element_first({"element": element}) is True
    assert ah._use_element_first({"element": element, "match": "visual"}) is False
    # no element recorded -> image cascade, as before
    assert ah._use_element_first({"match": "visual"}) is False
    assert ah._use_element_first({}) is False


def test_repeat_cursor_state_survives_passes_but_resets_per_run():
    # The processed set must survive loop boundaries so each pass ADVANCES,
    # not re-click the first row (mirrors the web entity cursor).
    ah = pytest.importorskip("player.action_handlers")
    repeat = {"container": {"automation_id": "L", "class_name": "list"},
              "kind": {"control_type": 50007, "class_name": "row"}}
    key = ah._repeat_key(repeat)

    selection_memory = {}
    selection_memory.setdefault(key, {}).setdefault("processed", set()).add("row1")
    # pass 2 - same shared dict + stable key => the set survived
    state = selection_memory.setdefault(key, {})
    assert state["processed"] == {"row1"}
    state["processed"].add("row2")
    assert selection_memory[key]["processed"] == {"row1", "row2"}

    # a NEW chain run starts from a cleared selection_memory
    selection_memory = {}
    assert selection_memory.setdefault(key, {}).setdefault("processed", set()) == set()


# --------------------------------------------- recorder pairing (no GUI/UIA)

def test_recorder_emits_one_repeat_action_from_two_marked_siblings():
    """Two Insert-marked sibling clicks -> ONE repeating action (first dropped)."""
    rec_mod = pytest.importorskip("recorder.element_recorder")
    pytest.importorskip("player.uia_locator")

    container = _anc(100, 50008, "list")
    descriptors = [
        _row(10, ancestors=[container, _anc(200, 50032, "win")]),
        _row(11, ancestors=[container, _anc(200, 50032, "win")]),
    ]

    class _KH:
        def marker_active(self):
            return True

    captured = []

    class _SM:
        def add_action(self, action):
            captured.append(action)

        def get_action_count(self):
            return len(captured)

    recorder = rec_mod.ElementRecorder(sequence_name="t")
    recorder.keyboard_handler = _KH()
    recorder.sequence_manager = _SM()
    recorder._element_provider = lambda x, y: descriptors.pop(0) if descriptors else None

    recorder._emit_action({"type": "click", "coordinates": {"x": 5, "y": 5}}, 5, 5)
    assert captured == []          # the first mark is buffered, not recorded
    recorder._emit_action({"type": "click", "coordinates": {"x": 5, "y": 25}}, 5, 25)

    assert len(captured) == 1      # the first mark was dropped, not duplicated
    repeat = captured[0].get("repeat") or {}
    assert repeat.get("kind", {}).get("control_type") == 50007
    assert repeat.get("kind", {}).get("class_name") == "row"
    assert repeat.get("mode") == "click"
    assert captured[0].get("type") == "click"


# ----------------------------------- right-Ctrl visual-match marker (no UIA)

def _recorder_with_marker(rec_mod, visual):
    """ElementRecorder whose fakes record a left click while the visual-match
    marker is (or is not) held.  Returns (recorder, captured actions, crops)."""
    class _KH:
        modifiers = {}

        def marker_active(self):
            return False

        def visual_active(self):
            return visual

    class _MH:
        def handle_mouse_press(self, *a):
            pass

        def handle_mouse_release(self, x, y, button):
            return {"type": "click", "button": "left",
                    "coordinates": {"x": x, "y": y}, "needs_screenshot": True}

    crops = []

    class _SM:
        def capture_click_screenshot(self, x, y, element_rect=None):
            crops.append(element_rect)
            return "b64template"

    captured = []

    class _SeqM:
        def add_action(self, action):
            captured.append(action)

    recorder = rec_mod.ElementRecorder(sequence_name="t")
    recorder.keyboard_handler = _KH()
    recorder.mouse_handler = _MH()
    recorder.screenshot_manager = _SM()
    recorder.sequence_manager = _SeqM()
    recorder._element_rect_provider = lambda: (0, 0, 200, 100)
    recorder._element_provider = lambda x, y: {"rect": (0, 0, 200, 100), "name": "row"}
    return recorder, captured, crops


def test_right_ctrl_click_records_a_visual_match_without_element():
    """Holding the visual-match marker at record time must flag the click
    ``match=visual``, keep the LEGACY cursor crop (no element bbox) and NOT
    store the UIA element - an element-less click is what replay (and any older
    build reading the sequence) treats as a template match."""
    rec_mod = pytest.importorskip("recorder.element_recorder")
    recorder, captured, crops = _recorder_with_marker(rec_mod, visual=True)
    # Moving to the target with the marker held armed the hover capture; the
    # click must CONSUME it, or a trailing move_to lands in the sequence and
    # replay acts twice (click, then move to the recorded coordinates).
    recorder._right_ctrl_move_capture = {"start": (0, 0), "last": (5, 5)}
    recorder.on_mouse_release(5, 5, None)

    assert len(captured) == 1
    action = captured[0]
    assert action.get("match") == "visual"
    assert "element" not in action
    assert crops == [None]  # legacy cursor crop, not the element bbox
    assert action["screenshot_data"] == "b64template"
    assert "needs_screenshot" not in action
    assert recorder._right_ctrl_move_capture is None


def test_plain_click_keeps_the_element_template_and_descriptor():
    rec_mod = pytest.importorskip("recorder.element_recorder")
    recorder, captured, crops = _recorder_with_marker(rec_mod, visual=False)
    recorder._right_ctrl_move_capture = {"start": (0, 0), "last": (5, 5)}
    recorder.on_mouse_release(5, 5, None)

    assert len(captured) == 1
    action = captured[0]
    assert action.get("match") is None
    assert action["element"]["name"] == "row"
    assert crops == [(0, 0, 200, 100)]  # element-sized crop as before
    # Only a visual click consumes the hover gesture.
    assert recorder._right_ctrl_move_capture == {"start": (0, 0), "last": (5, 5)}


# ------------------------------------------------- drag endpoints (entity)

def test_resolve_point_is_none_without_a_usable_descriptor():
    ah = pytest.importorskip("player.action_handlers")
    assert ah._resolve_point(None) is None
    assert ah._resolve_point("garbage") is None
    assert ah._resolve_point({}) is None


def test_recorder_attaches_elements_to_both_drag_endpoints():
    rec_mod = pytest.importorskip("recorder.element_recorder")
    pytest.importorskip("player.uia_locator")
    container = _anc(100, 50008, "list")
    queue = [_row(10, ancestors=[container]), _row(11, ancestors=[container])]
    recorder = rec_mod.ElementRecorder(sequence_name="t")
    recorder._element_provider = lambda x, y: queue.pop(0) if queue else None

    action = {"type": "drag_drop", "from": {"x": 5, "y": 5}, "to": {"x": 5, "y": 25}}
    recorder._attach_drag_elements(action)
    assert action["from_element"]["runtime_id"] == [10]
    assert action["to_element"]["runtime_id"] == [11]


def test_recorder_skips_a_drag_endpoint_without_a_point():
    rec_mod = pytest.importorskip("recorder.element_recorder")
    recorder = rec_mod.ElementRecorder(sequence_name="t")
    recorder._element_provider = lambda x, y: None
    action = {"type": "drag_drop", "from": {"x": None, "y": None}, "to": {"x": 5, "y": 25}}
    recorder._attach_drag_elements(action)
    assert "from_element" not in action
    assert "to_element" not in action


def test_recorder_captures_the_drag_source_at_press():
    # A move-drag takes the source away from its point by release, so the SOURCE
    # must be the element seen at press - not a fresh hit-test at release.
    rec_mod = pytest.importorskip("recorder.element_recorder")
    pytest.importorskip("player.uia_locator")
    container = _anc(100, 50008, "list")
    queue = [_row(10, ancestors=[container]), _row(99, ancestors=[container])]
    recorder = rec_mod.ElementRecorder(sequence_name="t")
    recorder._element_provider = lambda x, y: queue.pop(0) if queue else None

    recorder.on_mouse_press(5, 5, "left", True)
    assert recorder._drag_from_element["runtime_id"] == [10]

    action = {"type": "drag_drop", "from": {"x": 5, "y": 5}, "to": {"x": 5, "y": 25}}
    recorder._attach_drag_elements(action)
    assert action["from_element"]["runtime_id"] == [10]   # the pressed source
    assert action["to_element"]["runtime_id"] == [99]     # fresh at release


# ------------------------------------------------- shell file-type parity

def test_ext_map_for_indexes_folders_by_base_name(tmp_path):
    (tmp_path / "targeted resume.pdf").write_text("x")
    (tmp_path / "CT-PCM Technical Specification.PDF").write_text("x")
    (tmp_path / "license.txt").write_text("x")
    (tmp_path / "New folder").mkdir()

    mapping = locator._ext_map_for([str(tmp_path)])
    assert mapping["targeted resume"] == ".pdf"
    assert mapping["CT-PCM Technical Specification"] == ".pdf"   # case-folded
    assert mapping["license"] == ".txt"
    assert mapping["New folder"] == ""                           # a folder


def test_ext_map_for_is_none_without_a_readable_folder():
    assert locator._ext_map_for([]) is None
    assert locator._ext_map_for([r"Z:\no\such\folder"]) is None


def test_shell_folder_extensions_ignores_non_shell_lists():
    # Only a Win32 SysListView32 is a file list; every other list keeps the
    # property-only behaviour (no disk lookups, no over-constraining).
    class _El:
        CurrentClassName = "SysTreeView32"

    assert locator._shell_folder_extensions(_El()) is None


def test_desktop_dirs_resolve_to_real_folders():
    dirs = locator._desktop_dirs()
    assert dirs                                   # Windows always has one
    assert all(os.path.isdir(d) for d in dirs)


# ------------------------------------------------ row keys (cursor stability)

def test_row_keyer_prefers_aid_then_unique_name_then_ordinal():
    class _El:
        def __init__(self, aid="", name=""):
            self.CurrentAutomationId = aid
            self.CurrentName = name

    rows = [_El(aid="A1", name="first"), _El(name="dup"), _El(name="dup")]
    key_of = locator._row_keyer(rows)
    assert key_of(0, rows[0]) == "A1"           # AutomationId wins
    assert key_of(1, rows[1]) == "ordinal_1"    # name NOT unique -> positional
    assert key_of(2, rows[2]) == "ordinal_2"

    named = [_El(name="targeted resume"), _El(name="resume_alex_doe")]
    key_of = locator._row_keyer(named)
    assert key_of(0, named[0]) == "name:targeted resume"
    assert key_of(1, named[1]) == "name:resume_alex_doe"
    assert key_of(0, _El()) == "ordinal_0"      # nothing to identify it by
