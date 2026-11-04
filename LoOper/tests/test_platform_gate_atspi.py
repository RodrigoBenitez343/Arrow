"""The AT-SPI adapter must serve the same element-tree surface as UIA.

A fake AT-SPI backend stands in for pyatspi / gi so this runs on any OS: it
fails if FindAll scoping, condition matching, property mapping, the walker or
the Value/Toggle patterns drift from what the player and recorder call.
"""
import pytest

from LoOper import platform_gate_atspi as atspi


class _State:
    def __init__(self, flags):
        self.flags = set(flags)

    def contains(self, flag):
        return flag in self.flags


class _Component:
    def __init__(self, box, hits=()):
        self.box = box          # (x, y, w, h)
        self.hits = list(hits)  # nodes this component hit-tests

    def getExtents(self, coords):
        return self.box

    def getAccessibleAtPoint(self, x, y, coords):
        for node in self.hits:
            left, top, width, height = node.component.box
            if left <= x <= left + width and top <= y <= top + height:
                return node
        return None


class _Text:
    def __init__(self, text):
        self.text = text

    def getText(self, start, end):
        return self.text


class _Node:
    def __init__(self, role, name="", attrs=None, children=(), box=None,
                 states=(), text=None, pid=None, hits=()):
        self.role = role
        self.name = name
        self.attrs = attrs or {}
        self._children = list(children)
        self.component = _Component(box or (0, 0, 0, 0), hits)
        self.states = set(states)
        self.text = text
        self.pid = pid
        self.parent = None
        for child in self._children:
            child.parent = self

    # pyatspi-style (camelCase) surface
    def getRoleName(self):
        return self.role

    def getName(self):
        return self.name

    def getAttributes(self):
        return dict(self.attrs)

    def getChildCount(self):
        return len(self._children)

    def getChildAt(self, index):
        return self._children[index]

    def getParent(self):
        return self.parent

    def getState(self):
        return _State(self.states)

    def queryComponent(self):
        return self.component

    def queryEditableText(self):
        return _Text(self.text) if self.text is not None else None

    def queryText(self):
        return _Text(self.text) if self.text is not None else None

    def getApplication(self):
        node = self
        while node is not None:
            if node.pid is not None:
                return node
            node = node.parent
        return self

    def getProcessId(self):
        node = self
        while node is not None:
            if node.pid is not None:
                return node.pid
            node = node.parent
        return None


class _FakeModule:
    DESKTOP_COORDS = 0
    STATE_CHECKED = "CHECKED"
    STATE_EDITABLE = "EDITABLE"
    STATE_SHOWING = "SHOWING"

    def __init__(self, root):
        self.root = root

    def getDesktop(self, index):
        return self.root


@pytest.fixture()
def tree(monkeypatch):
    label = _Node("label", "Hint", {}, box=(10, 120, 40, 12),
                  states={"SHOWING"})
    entry = _Node("entry", "Email", {"id": "email", "class": "Edit"},
                  box=(10, 20, 200, 24), states={"EDITABLE", "SHOWING"},
                  text="a@b.c")
    check = _Node("check box", "Newsletter", {"id": "news", "class": "Button"},
                  box=(10, 60, 16, 16), states={"CHECKED", "SHOWING"})
    save = _Node("push button", "Save", {"id": "save", "class": "Button"},
                 box=(10, 90, 60, 24), states={"SHOWING"}, children=[label])
    frame = _Node("frame", "Contacts", {}, children=[entry, check, save],
                  box=(0, 0, 400, 300), states={"SHOWING"})
    app = _Node("application", "Contacts", {}, children=[frame],
                box=(0, 0, 400, 300), states={"SHOWING"}, pid=4242,
                hits=(entry, check, save))
    desktop = _Node("desktop frame", "", {}, children=[app],
                    box=(0, 0, 1920, 1080), states={"SHOWING"})
    monkeypatch.setattr(atspi, "_BACKEND", ("fake", _FakeModule(desktop)))
    return {"root": desktop, "app": app, "frame": frame, "entry": entry,
            "check": check, "save": save, "label": label}


def test_find_all_matches_name_and_control_type(tree):
    uia = atspi.automation()
    root = uia.GetRootElement()
    condition = uia.CreateAndCondition(
        uia.CreatePropertyCondition(atspi.CONTROL_TYPE, 50004),
        uia.CreatePropertyCondition(atspi.NAME, "Email"))
    found = root.FindAll(atspi.SCOPE_DESCENDANTS, condition)
    assert found.Length == 1
    assert found.GetElement(0).CurrentName == "Email"


def test_children_scope_does_not_descend(tree):
    uia = atspi.automation()
    frame = atspi.Element(tree["frame"])
    assert frame.FindAll(atspi.SCOPE_CHILDREN).Length == 3
    assert frame.FindAll(atspi.SCOPE_DESCENDANTS).Length == 4  # + save's label


def test_element_properties_map_to_uia_ids(tree):
    element = atspi.Element(tree["entry"])
    assert element.CurrentControlType == 50004
    assert element.CurrentAutomationId == "email"
    assert element.CurrentClassName == "Edit"
    rect = element.CurrentBoundingRectangle
    assert (rect.left, rect.top, rect.right, rect.bottom) == (10, 20, 210, 44)
    assert tuple(rect) == (10, 20, 210, 44)
    assert element.CurrentIsOffscreen is False


def test_process_id_comes_from_the_application(tree):
    assert atspi.Element(tree["entry"]).CurrentProcessId == 4242
    uia = atspi.automation()
    matches = uia.GetRootElement().FindAll(
        atspi.SCOPE_DESCENDANTS,
        uia.CreatePropertyCondition(atspi.PROCESS_ID, 4242))
    assert matches.Length == 6  # app + frame + entry + checkbox + save + label


def test_value_and_toggle_patterns(tree):
    entry = atspi.Element(tree["entry"])
    value = entry.GetCurrentPattern(atspi.VALUE_PATTERN)
    assert value.QueryInterface(object()) is value
    assert value.CurrentValue == "a@b.c"
    assert value.CurrentIsReadOnly is False

    check = atspi.Element(tree["check"])
    assert check.GetCurrentPattern(atspi.TOGGLE_PATTERN).CurrentToggleState == 1
    assert atspi.Element(tree["save"]).GetCurrentPattern(
        atspi.TOGGLE_PATTERN).CurrentToggleState == 0


def test_walker_hops_to_the_parent(tree):
    uia = atspi.automation()
    parent = uia.ControlViewWalker.GetParentElement(atspi.Element(tree["entry"]))
    assert parent.CurrentName == "Contacts"


def test_element_from_point_hits_the_entry(tree):
    uia = atspi.automation()
    hit = uia.ElementFromPoint((15, 25))
    assert hit.CurrentName == "Email"


def test_runtime_id_is_a_stable_chain(tree):
    runtime = atspi.Element(tree["entry"]).GetRuntimeId()
    assert runtime[0] == "atspi"
    assert runtime[-1] == "entry"


def test_available_reports_the_bound_backend(tree):
    assert atspi.available() is True
