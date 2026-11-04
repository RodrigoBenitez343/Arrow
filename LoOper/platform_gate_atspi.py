"""AT-SPI2 twin of the Windows UI Automation surface Arrow uses.

The player/recorder are written against a duck-typed subset of
``IUIAutomation``: ``GetRootElement``, ``CreatePropertyCondition`` /
``CreateAndCondition``, ``FindAll(scope, condition)``, ``ElementFromPoint``,
``ControlViewWalker.GetParentElement``, the ``Current*`` element properties,
``GetRuntimeId`` and the Value / Toggle patterns. Everything below implements
that same surface on AT-SPI2 (pyatspi, or gi.repository.Atspi), so
element-first replay and element recording run on Linux/X11 too.

Kept apart from ``platform_gate`` so it can be imported and tested on any OS
with a fake backend (see ``LoOper/tests/test_platform_gate_atspi.py``).
"""
from __future__ import annotations

import collections

SCOPE_CHILDREN = 2
SCOPE_DESCENDANTS = 4

PROCESS_ID = 30002      # UIA_ProcessIdPropertyId
CONTROL_TYPE = 30003    # UIA_ControlTypePropertyId
NAME = 30005            # UIA_NamePropertyId
AUTOMATION_ID = 30011   # UIA_AutomationIdPropertyId
CLASS_NAME = 30012      # UIA_ClassNamePropertyId
WINDOW_CONTROL_TYPE = 50032

VALUE_PATTERN = 10002
TOGGLE_PATTERN = 10015

# UIA ControlType id -> accepted AT-SPI role names. Both recordings and
# replays map through this table, so they always agree on an id.
UIA_ROLES = {
    50000: ("push button", "button", "toggle button"),
    50001: ("calendar",),
    50002: ("check box", "checkbox"),
    50003: ("combo box", "combobox"),
    50004: ("entry", "text", "password text"),
    50005: ("link",),
    50006: ("image", "icon"),
    50007: ("list item",),
    50008: ("list",),
    50009: ("menu",),
    50010: ("menu bar",),
    50011: ("menu item", "check menu item", "radio menu item"),
    50012: ("progress bar",),
    50013: ("radio button",),
    50014: ("scroll bar",),
    50015: ("slider",),
    50016: ("spin button",),
    50017: ("status bar",),
    50018: ("page tab list",),
    50019: ("page tab",),
    50020: ("label", "static", "text", "paragraph", "heading"),
    50021: ("tool bar",),
    50022: ("tool tip",),
    50023: ("tree",),
    50024: ("tree item", "table tree item"),
    50025: ("panel", "filler", "drawing area", "canvas"),
    50026: ("panel", "grouping", "group", "redundant object"),
    50027: ("scroll bar",),
    50028: ("table", "tree table"),
    50029: ("list item", "table cell", "table row"),
    50030: ("document text", "document frame", "document web", "frame",
            "application"),
    50031: ("push button",),
    50032: ("frame", "dialog", "window", "application"),
    50033: ("panel", "filler", "scroll pane", "viewport", "application",
            "frame"),
    50034: ("table column header",),
    50035: ("table column header", "table row header"),
    50036: ("table", "tree table"),
    50037: ("title bar", "frame"),
}

ROLE_TO_UIA = {}
for _uia_id, _roles in UIA_ROLES.items():
    for _role in _roles:
        ROLE_TO_UIA.setdefault(_role, _uia_id)
del _uia_id, _roles, _role

# ponytail: caps keep a huge desktop tree from stalling a click. Raise them
# (or stream lazily) if a 20k-node session ever shows up.
MAX_DEPTH = 24
MAX_NODES = 8000

_Rect = collections.namedtuple("_Rect", "left top right bottom")


class Unsupported(RuntimeError):
    """No AT-SPI2 binding is installed."""


_BACKEND = None


def backend():
    """(kind, module) for the AT-SPI2 binding: pyatspi, else gi Atspi."""
    global _BACKEND
    if _BACKEND is not None:
        return _BACKEND
    try:
        import pyatspi

        _BACKEND = ("pyatspi", pyatspi)
        return _BACKEND
    except Exception:
        pass
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        _BACKEND = ("gi", Atspi)
        return _BACKEND
    except Exception as exc:
        raise Unsupported(
            "Linux element control needs AT-SPI2 (install at-spi2-core and "
            "python3-pyatspi, or python3-gi with gir1.2-atspi-2.0): "
            f"{exc}")


def desktop():
    kind, mod = backend()
    try:
        return mod.getDesktop(0)
    except AttributeError:
        return mod.get_desktop(0)


def _coords():
    kind, mod = backend()
    try:
        return mod.DESKTOP_COORDS
    except AttributeError:
        return mod.CoordType.DESKTOP


def _state(mod, name):
    try:  # pyatspi exposes STATE_*; gi exposes StateType.*
        return getattr(mod, "STATE_" + name)
    except AttributeError:
        return getattr(mod.StateType, name)


def _call(node, camel, snake, *args, default=None):
    """Call the pyatspi (camelCase) or gi (snake_case) variant."""
    fn = getattr(node, camel, None)
    if fn is None:
        fn = getattr(node, snake, None)
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception:
        return default


def role_name(node) -> str:
    name = _call(node, "getRoleName", "get_role_name")
    if not name:
        try:
            name = node.getRole().value_name
        except Exception:
            name = ""
    return str(name or "").replace("_", " ").lower()


def _attribute(node, key) -> str:
    attrs = _call(node, "getAttributes", "get_attributes") or {}
    try:
        value = attrs.get(key)
        if value is not None:
            return str(value)
    except Exception:
        pass
    return ""


def _rect_of(node):
    component = _call(node, "queryComponent", "query_component")
    if component is None:
        return _Rect(0, 0, 0, 0)
    try:
        x, y, w, h = component.getExtents(_coords())
    except Exception:
        return _Rect(0, 0, 0, 0)
    return _Rect(int(x), int(y), int(x) + int(w), int(y) + int(h))


def _state_contains(node, state_name) -> bool:
    state = _call(node, "getState", "get_state")
    if state is None:
        return False
    try:
        return bool(state.contains(_state(backend()[1], state_name)))
    except Exception:
        return False


def process_of(node):
    """PID behind an accessible (libatspi reports it per accessible)."""
    app = _call(node, "getApplication", "get_application")
    for target in (node, app):
        if target is None:
            continue
        pid = _call(target, "getProcessId", "get_process_id")
        if pid:
            return int(pid)
    return None


def children(node):
    count = _call(node, "getChildCount", "get_child_count", default=0) or 0
    out = []
    for index in range(int(count)):
        child = _call(node, "getChildAt", "get_child_at", index)
        if child is not None:
            out.append(child)
    return out


class Condition:
    """AND of ``(UIA property id, value)`` pairs, matched on AT-SPI."""

    __slots__ = ("pairs",)

    def __init__(self, pairs):
        self.pairs = tuple(pairs)

    def match(self, node) -> bool:
        for prop, value in self.pairs:
            if prop == CONTROL_TYPE:
                if role_name(node) not in UIA_ROLES.get(int(value), ()):
                    return False
            elif prop == NAME:
                name = _call(node, "getName", "get_name", default="")
                if str(name or "") != str(value):
                    return False
            elif prop == AUTOMATION_ID:
                ident = _attribute(node, "id") or _attribute(node, "automation-id")
                if ident != str(value):
                    return False
            elif prop == CLASS_NAME:
                if _attribute(node, "class") != str(value):
                    return False
            elif prop == PROCESS_ID:
                if process_of(node) != int(value):
                    return False
        return True


class Collection:
    """UIAElementArray twin: ``Length`` + ``GetElement(index)``."""

    __slots__ = ("_items",)

    def __init__(self, items):
        self._items = list(items)

    @property
    def Length(self):
        return len(self._items)

    def GetElement(self, index):
        return Element(self._items[index])


class Pattern:
    """ValuePattern / TogglePattern twin (QueryInterface is a no-op)."""

    __slots__ = ("node", "pattern_id")

    def __init__(self, node, pattern_id):
        self.node = node
        self.pattern_id = int(pattern_id)

    def QueryInterface(self, *_ignored):
        return self

    @property
    def CurrentValue(self):
        editable = _call(self.node, "queryEditableText", "query_editable_text")
        if editable is not None:
            text = _call(editable, "getText", "get_text", 0, -1)
            if text is not None:
                return str(text)
        text_iface = _call(self.node, "queryText", "query_text")
        if text_iface is not None:
            text = _call(text_iface, "getText", "get_text", 0, -1)
            if text is not None:
                return str(text)
        return ""

    @property
    def CurrentIsReadOnly(self):
        return not _state_contains(self.node, "EDITABLE")

    @property
    def CurrentToggleState(self):
        return 1 if _state_contains(self.node, "CHECKED") else 0


class Element:
    """Duck-typed IUIAutomationElement over one AT-SPI accessible."""

    __slots__ = ("node",)

    def __init__(self, node):
        self.node = node

    # UIA ``Current*`` property surface used by the player and recorder.
    def __getattr__(self, name):
        if name == "CurrentName":
            return str(_call(self.node, "getName", "get_name", default="") or "")
        if name == "CurrentAutomationId":
            return _attribute(self.node, "id") or _attribute(
                self.node, "automation-id")
        if name == "CurrentClassName":
            return _attribute(self.node, "class")
        if name == "CurrentFrameworkId":
            return "atspi"
        if name == "CurrentControlType":
            return ROLE_TO_UIA.get(role_name(self.node), 50025)
        if name == "CurrentProcessId":
            return process_of(self.node)
        if name == "CurrentBoundingRectangle":
            return _rect_of(self.node)
        if name == "CurrentIsOffscreen":
            return not _state_contains(self.node, "SHOWING")
        raise AttributeError(name)

    def GetRuntimeId(self):
        tree = []
        node = self.node
        while node is not None and len(tree) < 32:
            tree.append(role_name(node))
            node = _call(node, "getParent", "get_parent")
        return ["atspi"] + list(reversed(tree))

    def FindAll(self, scope, condition=None):
        return Collection(_find(self.node, int(scope), condition))

    def FindFirst(self, scope, condition=None):
        found = _find(self.node, int(scope), condition, first_only=True)
        return Element(found[0]) if found else None

    def GetCurrentPattern(self, pattern_id):
        return Pattern(self.node, pattern_id)


def _find(root, scope, condition=None, first_only=False):
    if condition is None:
        condition = Condition(())
    found = []
    budget = [MAX_NODES]

    def walk(node, depth):
        if budget[0] <= 0 or depth > MAX_DEPTH:
            return True
        for child in children(node):
            budget[0] -= 1
            if budget[0] <= 0:
                return False
            if condition.match(child):
                found.append(child)
                if first_only:
                    return False
            if scope == SCOPE_DESCENDANTS and not walk(child, depth + 1):
                return False
        return True

    walk(root, 0)
    return found


class Walker:
    """TreeWalker twin: only parent hops are used by the call sites."""

    __slots__ = ()

    def GetParentElement(self, element):
        parent = _call(element.node, "getParent", "get_parent")
        return Element(parent) if parent is not None else None


class Automation:
    """Duck-typed IUIAutomation entry point (root, conditions, hit test)."""

    def __init__(self):
        self.ControlViewWalker = Walker()
        self.RawViewWalker = Walker()

    def GetRootElement(self):
        return Element(desktop())

    def CreatePropertyCondition(self, prop, value):
        return Condition(((int(prop), value),))

    def CreateAndCondition(self, left, right):
        return Condition(left.pairs + right.pairs)

    def CreateOrCondition(self, left, right):
        # Only AND is used today; OR degrades to the wider condition.
        return Condition(left.pairs)

    def ElementFromPoint(self, point):
        try:
            x, y = int(point[0]), int(point[1])
        except Exception:
            x, y = int(getattr(point, "x", 0)), int(getattr(point, "y", 0))
        for app in children(desktop()):
            component = _call(app, "queryComponent", "query_component")
            if component is None:
                continue
            try:
                hit = component.getAccessibleAtPoint(x, y, _coords())
            except Exception:
                continue
            if hit is not None:
                return Element(hit)
        raise Unsupported(
            "AT-SPI reported no accessible element at "
            f"({x}, {y}); the app may not expose accessibility")


def automation():
    """Element-tree client (cheap; raises ``Unsupported`` when unbound)."""
    backend()  # raises with the install hint when nothing is available
    return Automation()


def available() -> bool:
    try:
        backend()
        return True
    except Unsupported:
        return False
