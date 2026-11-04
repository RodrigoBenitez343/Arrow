"""Desktop substrate: Windows UI Automation reads the form, the field loop fills it.

The desktop twin of the web DOM scan.  UIA reports every fillable control of a
window with its accessible NAME, its AutomationId (the page's own input id), its
bounding box and the value it already holds - so a desktop field carries the
same ``label`` / ``kind`` / ``current`` the web scan produces, and every write is
a plain click on the control's own box.

Three control families, one write rail each:

* ``text``   - an editable Edit: click, clear, type; ValuePattern reads it back;
* ``choice`` - a list-backed control (ComboBox, or a hidden / read-only
  react-select input): click to OPEN it, diff the UIA tree for the option rows
  that appeared below the control, click the option the answer names, then read
  the value the control now displays; a RADIO group is the same rail with its
  options already on screen;
* ``switch`` - a CheckBox: TogglePattern says whether it is on, the answer says
  whether it should be, one click settles the difference.

Why not vision: LFM2.5-VL-450M reads a form screenshot's LABELS correctly but
returns the SAME box for every field (probed on a synthetic form: five fields,
one shared box), and LocateAnything costs ~90 s per field through the mtmd-cli
fallback because the gateway answers it with an empty response.  UIA is exact,
instant, needs neither - and it reports a react-select's option list (probed:
four options with boxes) which a pixel model could not ground.

ponytail: an app that draws its form on a canvas (no UIA tree) still yields no
fields - the VLM + grounding path would have to come back for that.
"""

import logging
import os
import re
import time

from .common import (
    _ff_option_match,
    log_block,
    log_table,
    logger,
)

# UIA property ids / control types / tree scopes / pattern ids (standard
# constants, hardcoded so the node never depends on comtypes' generated module
# at import time).
_CTRL_TYPE_PROP = 30003
_EDIT = 50004
_COMBO = 50003
_RADIO = 50013
_CHECK = 50002
_TEXT = 50020
_LIST_ITEM = 50007
_MENU_ITEM = 50011
_DOCUMENT = 50030
_WINDOW = 50032
_SCOPE_CHILDREN = 2
_SCOPE_DESCENDANTS = 4
_VALUE_PATTERN = 10002
_TOGGLE_PATTERN = 10015

# A top-level window smaller than this is a palette/tooltip, not a form.
_MIN_WINDOW_W = 300
_MIN_WINDOW_H = 200

# A control narrower than this is a widget's HIDDEN input (a react-select /
# typeahead dummy): it takes no free text, it opens a list.
_HIDDEN_INPUT_W = 40

_OPEN_TIMEOUT_S = 2.0     # an option list must appear within this
_OPTION_GAP_PX = 6        # an option row starts at/below the control's top

# A browser announces state changes to screen readers inside the SAME tree
# ('option Pasaporte, selected.' / '... 4 results available.').  Such a line is
# not an option and must never be listed or picked.
_ANNOUNCEMENT_RE = re.compile(
    r"^(option|radio|checkbox|item)\b.*\b(selected|focused|checked|available)\b",
    re.IGNORECASE)


def _ff_uia():
    """A UI Automation client for this thread (platform gate), or None."""
    try:  # LoOper/ on sys.path: the app and the test suite both run that way
        from platform_gate import uia, uia_available
    except ImportError:  # imported as LoOper.*
        from LoOper.platform_gate import uia, uia_available

    if not uia_available():
        logger.error(
            "[FORM] desktop form filling needs UI Automation, which this "
            "platform does not provide - use the vision path instead.")
        return None
    return uia()


def _ff_types():
    """The generated COM typelib module (for QueryInterface casts).

    Windows only: the AT-SPI adapter exposes the same pattern attributes
    without a typelib, so casts become no-ops there.
    """
    try:  # LoOper/ on sys.path: the app and the test suite both run that way
        from platform_gate import IS_WINDOWS
    except ImportError:  # imported as LoOper.*
        from LoOper.platform_gate import IS_WINDOWS

    if not IS_WINDOWS:
        return None
    from comtypes.gen import UIAutomationClient
    return UIAutomationClient


def _ff_cast(pattern, interface_name):
    """QueryInterface a UIA pattern; the AT-SPI twin is returned as-is."""
    types = _ff_types()
    if types is None:
        return pattern
    return pattern.QueryInterface(getattr(types, interface_name))


def _ff_rect(element):
    """Element box (l, t, r, b) in screen pixels, or None."""
    try:
        r = element.CurrentBoundingRectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _ff_prop(element, attr):
    """A UIA ``Current*`` string property, or "" when the provider throws."""
    try:
        return str(getattr(element, attr) or "").strip()
    except Exception:
        return ""


def _ff_area(rect):
    if not rect:
        return 0
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1])


def _ff_center(rect):
    return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)


def _ff_inside(inner, outer, tolerance=0):
    return (inner[0] >= outer[0] - tolerance
            and inner[1] >= outer[1] - tolerance
            and inner[2] <= outer[2] + tolerance
            and inner[3] <= outer[3] + tolerance)


def _ff_find(scope, uia, *control_types):
    """Descendant elements of the given control types ([] on failure)."""
    try:
        condition = None
        for kind in control_types:
            piece = uia.CreatePropertyCondition(_CTRL_TYPE_PROP, kind)
            condition = piece if condition is None else uia.CreateOrCondition(
                condition, piece)
        found = scope.FindAll(_SCOPE_DESCENDANTS, condition)
    except Exception:
        return []
    return [found.GetElement(i) for i in range(0 if found is None else found.Length)]


def _ff_visible(element, min_w=2, min_h=2):
    """Element box when it is on screen and not degenerate, else None."""
    try:
        if element.CurrentIsOffscreen:
            return None
    except Exception:
        pass
    rect = _ff_rect(element)
    if not rect or rect[2] - rect[0] < min_w or rect[3] - rect[1] < min_h:
        return None
    return rect


def _ff_value(element):
    """An Edit's value via ValuePattern, or None when it has no such pattern."""
    try:
        pattern = _ff_cast(element.GetCurrentPattern(_VALUE_PATTERN),
                           "IUIAutomationValuePattern")
        return str(pattern.CurrentValue or "")
    except Exception:
        return None


def _ff_read_only(element):
    """ValuePattern.IsReadOnly, or False when the control has no value pattern."""
    try:
        pattern = _ff_cast(element.GetCurrentPattern(_VALUE_PATTERN),
                           "IUIAutomationValuePattern")
        return bool(pattern.CurrentIsReadOnly)
    except Exception:
        return False


def _ff_toggle(element):
    """A CheckBox's state: True (on), False (off), None (unreadable)."""
    try:
        pattern = _ff_cast(element.GetCurrentPattern(_TOGGLE_PATTERN),
                           "IUIAutomationTogglePattern")
        return int(pattern.CurrentToggleState) == 1
    except Exception:
        return None


def _ff_parent_rect(uia, element, window_rect):
    """The nearest ancestor box that is a usable click area, else None.

    A hidden widget input is a few px wide and cannot be clicked reliably;
    clicking its PARENT (the painted control around it) is what a user does.
    """
    try:
        walker = uia.ControlViewWalker
    except Exception:
        return None
    node = element
    for _ in range(3):
        try:
            node = walker.GetParentElement(node)
        except Exception:
            return None
        if node is None:
            return None
        rect = _ff_visible(node)
        if not rect:
            continue
        if window_rect and not _ff_inside(rect, window_rect, 8):
            return None
        if (rect[2] - rect[0]) >= 60 and (rect[3] - rect[1]) >= 18:
            return rect
    return None


def _ff_placeholder_like(text):
    """A prompt value ('Seleccione una opción', 'Select...') is not an answer."""
    t = str(text or "").strip().lower().strip(".:*")
    if not t:
        return True
    return (t in ("select", "select...", "choose", "choose...", "-", "--",
                  "n/a", "na")
            or t.startswith(("select an", "select a", "seleccione", "selecciona",
                             "elige", "escoger", "choose", "please select")))


def _ff_visible_texts(uia, scope):
    """Visible TEXT rows of *scope* as ``[(name, rect)]``."""
    rows = []
    for element in _ff_find(scope, uia, _TEXT):
        rect = _ff_visible(element)
        if not rect:
            continue
        name = _ff_prop(element, "CurrentName")
        if name:
            rows.append((name[:120], rect))
    return rows


def _ff_option_texts(uia, scope):
    """Visible option-ish rows of *scope* as ``[(name, rect)]``.

    A react-select renders its rows as ARIA options; Chromium exposes those as
    TEXT nodes (probed), a native popup as ListItem/MenuItem - all three are
    read, and the caller diffs them against what was there before the click.
    """
    rows = []
    for element in _ff_find(scope, uia, _TEXT, _LIST_ITEM, _MENU_ITEM):
        rect = _ff_visible(element, 8, 8)
        if not rect:
            continue
        name = _ff_prop(element, "CurrentName")
        if name:
            rows.append((name[:120], rect))
    return rows


def _ff_new_options(before, after, control_rect):
    """The option rows that appeared BELOW the control after it was opened.

    The geometric filter is what separates the list from the screen-reader
    ANNOUNCEMENTS a browser emits on the same keystroke ('option Pasaporte
    focused, 1 of 4. 4 results available.'), which sit at/above the control.
    """
    seen = {name for name, _rect in before}
    top = (control_rect or (0, 0, 0, 0))[1] - _OPTION_GAP_PX
    out = []
    for name, rect in after:
        if name in seen or rect[3] <= top:
            continue
        if (_ff_placeholder_like(name) or _ANNOUNCEMENT_RE.match(name)
                or "results available" in name.lower()):
            continue
        out.append((name, rect))
    return out


def _ff_row_text(uia, scope, rect):
    """The value painted INSIDE a control's row, '' when it shows none.

    Only the row band counts (the text's top inside the control's vertical
    extent) and only text starting at the control's left edge: that separates
    the committed VALUE from the field's LABEL, which sits directly above it.
    A react-select paints the value across the whole widget while its hidden
    input stays 3 px wide (probed: 'No especificado' at x=364 for an input at
    x=364..367), which is why a strict inside-the-control test finds nothing.
    """
    for name, box in _ff_visible_texts(uia, scope):
        if _ff_placeholder_like(name) or _ANNOUNCEMENT_RE.match(name):
            continue
        if (box[1] >= rect[1] - 8 and box[3] <= rect[3] + 8
                and rect[0] - 10 <= box[0] <= rect[2] + 10):
            return name
    return ""


def _ff_label_above(texts, rect):
    """The question a bare group of controls belongs to (the text above it)."""
    best = None
    for name, box in texts:
        if box[3] <= rect[1] and box[1] >= rect[1] - 70:
            if box[0] < rect[2] and box[2] > rect[0]:       # same column
                if best is None or box[3] > best[1][3]:
                    best = (name, box)
    return best[0] if best else ""


def _ff_control_fields(uia, scope):
    """Every fillable control of *scope* as the field dicts the node fills."""
    fields = []
    radios = {}
    for control in _ff_find(scope, uia, _EDIT, _COMBO, _CHECK, _RADIO):
        rect = _ff_visible(control)
        if not rect:
            continue
        try:
            ctype = int(control.CurrentControlType)
        except Exception:
            ctype = _EDIT
        name = _ff_prop(control, "CurrentName")
        automation_id = _ff_prop(control, "CurrentAutomationId")
        # A RADIO belongs to its GROUP, not to itself: the model answers the
        # question once, and each option is one of its choices.
        if ctype == _RADIO:
            try:
                parent = uia.ControlViewWalker.GetParentElement(control)
                key = tuple(int(v) for v in parent.GetRuntimeId())
            except Exception:
                key = ("radio", rect[0] // 60, rect[1] // 60)
            group = radios.get(key)
            if group is None:
                radios[key] = group = {"rect": rect, "options": []}
            group["options"].append((name or "option %d" % len(group["options"]),
                                     rect))
            group["rect"] = (min(group["rect"][0], rect[0]),
                             min(group["rect"][1], rect[1]),
                             max(group["rect"][2], rect[2]),
                             max(group["rect"][3], rect[3]))
            continue
        label = name or _ff_prop(control, "CurrentHelpText") or automation_id
        if not label:
            continue
        current = _ff_value(control)
        if ctype == _CHECK:
            kind = "switch"
            state = _ff_toggle(control)
            current = "" if state is None else ("on" if state else "off")
        elif (ctype == _COMBO or (rect[2] - rect[0]) < _HIDDEN_INPUT_W
                or _ff_read_only(control)):
            # A read-only / hidden input cannot take typed text: the widget
            # around it is a LIST, and its value is the text it displays.
            kind = "choice"
            anchor = (_ff_parent_rect(uia, control, _ff_rect(scope)) or rect)
            current = (_ff_row_text(uia, scope, anchor)
                       or _ff_row_text(uia, scope, rect)
                       or (current or ""))
        else:
            kind = "text"
        fields.append({
            "id": automation_id or "uia_%d" % len(fields),
            "label": label,
            "placeholder": _ff_prop(control, "CurrentHelpText"),
            "tag": "input",
            "kind": kind,
            "box": list(rect),
            "x": _ff_center(rect)[0],
            "y": _ff_center(rect)[1],
            "current": current or "",
        })
    if radios:
        texts = _ff_visible_texts(uia, scope)
        for index, group in enumerate(radios.values()):
            label = _ff_label_above(texts, group["rect"])
            if not label:
                logger.warning("[FORM] radio group at %s has no label - skipped",
                               group["rect"])
                continue
            fields.append({
                "id": "radio_%d" % index,
                "label": label,
                "tag": "input",
                "kind": "choice",
                "box": list(group["rect"]),
                "options": [name for name, _rect in group["options"]],
                "option_boxes": {name: list(rect)
                                 for name, rect in group["options"]},
                "current": "",
            })
    # Reading order: top-to-bottom, left-to-right, like the page shows them.
    fields.sort(key=lambda f: (f["box"][1] // 20, f["box"][0]))
    return fields


def _ff_window_fields(uia, window):
    """``(fields, has_document)`` for one top-level window.

    Scoped to the window's largest DOCUMENT when it has one: a browser/Electron
    window exposes its toolbar (the address bar is an Edit control too) next to
    the page, and only the page's controls are form fields.
    """
    documents = _ff_find(window, uia, _DOCUMENT)
    scope = (max(documents, key=lambda d: _ff_area(_ff_rect(d)))
             if documents else window)
    return _ff_control_fields(uia, scope), bool(documents)


def _ff_windows(uia):
    """Visible, own-process-excluded top-level windows as UIA elements."""
    try:
        root = uia.GetRootElement()
        found = root.FindAll(
            _SCOPE_CHILDREN,
            uia.CreatePropertyCondition(_CTRL_TYPE_PROP, _WINDOW),
        )
    except Exception as exc:
        logger.error("[FORM] UIA window scan failed: %s", exc)
        return []
    own_pid = os.getpid()
    windows = []
    for i in range(0 if found is None else found.Length):
        window = found.GetElement(i)
        if not _ff_prop(window, "CurrentName"):
            continue
        rect = _ff_rect(window)
        if (_ff_area(rect) < _MIN_WINDOW_W * _MIN_WINDOW_H
                or (rect[2] - rect[0]) < _MIN_WINDOW_W
                or (rect[3] - rect[1]) < _MIN_WINDOW_H):
            continue
        try:
            if window.CurrentIsOffscreen or int(window.CurrentProcessId) == own_pid:
                continue
        except Exception:
            pass
        windows.append(window)
    return windows


def _ff_pick_fields(candidates):
    """The ``(has_document, fields, window, title)`` candidate holding the form.

    A window exposing a DOCUMENT (browser, Electron, WinUI) beats one that does
    not - a form lives in a document, while an app shell's stray Edit boxes (a
    terminal, a shell column header) are not a form - and inside that pool the
    MOST fillable fields wins.  Ties keep the first candidate, i.e. UIA's
    z-order (topmost).  None when no window holds a field.
    """
    pool = ([c for c in candidates if c[0] and c[1]]
            or [c for c in candidates if c[1]])
    if not pool:
        return None
    return max(pool, key=lambda c: len(c[1]))


def _ff_activate(window):
    """Bring *window* to the front; True when it now owns the foreground.

    A click lands on whatever is TOPMOST, so a write into a window that is not
    in front types into another application.  The platform gate's
    force_foreground handles the Win32 foreground lock (ALT tap) and the EWMH
    activate request on X11.
    """
    try:
        hwnd = int(window.CurrentNativeWindowHandle)
    except Exception:
        return False
    if not hwnd:
        return False
    try:  # LoOper/ on sys.path: the app and the test suite both run that way
        from platform_gate import active_window, force_foreground
    except ImportError:  # imported as LoOper.*
        from LoOper.platform_gate import active_window, force_foreground
    try:
        force_foreground(hwnd)
        time.sleep(0.2)
        info = active_window()
        return bool(info and int(info["handle"]) == hwnd)
    except Exception as exc:
        logger.warning("[FORM] could not activate the target window: %s", exc)
        return False


def _ff_truthy(text):
    """Does the answer mean YES? (a toggle's answer is yes/no, never a value)"""
    t = str(text or "").strip().lower()
    if not t:
        return False
    if t.startswith(("no", "false", "nope", "nunca", "never", "off", "n/a")):
        return False
    return t.startswith(("yes", "true", "si", "sí", "y", "1", "on", "x",
                         "acepto", "accept", "agree", "check"))


class FormFillerDesktopMixin:
    """Desktop substrate: UIA field harvest + the Handle actuator."""

    def _ff_desktop_state(self):
        """This pass's picked window/scope (a UIA element cannot be rebuilt
        from a serialized field, so it is kept instead of re-scanned)."""
        if getattr(self, "_ff_desktop", None) is None:
            self._ff_desktop = {"uia": None, "window": None, "scope": None,
                                "title": ""}
        state = self._ff_desktop
        if state.get("uia") is None:
            state["uia"] = _ff_uia()
        if state.get("window") is None:
            self._ff_desktop_pick(state)
        return state

    def _ff_desktop_pick(self, state):
        """Choose the window that holds the form and remember its scope."""
        uia = state["uia"]
        if uia is None:
            state["fields"] = []
            return []
        candidates = []
        for window in _ff_windows(uia):
            fields, has_document = _ff_window_fields(uia, window)
            candidates.append((has_document, fields, window,
                               _ff_prop(window, "CurrentName")))
        log_table(logger, logging.INFO, "Form Windows",
                  [("Window", "%s  (%d field(s)%s)" % (
                      title[:90], len(fields), ", page" if has_doc else ""))
                   for has_doc, fields, _win, title in candidates]
                  or [("Window", "(none)")])
        picked = _ff_pick_fields(candidates)
        if picked is None:
            state["fields"] = []
            return []
        _has_doc, fields, window, title = picked
        documents = _ff_find(window, uia, _DOCUMENT)
        state["window"] = window
        state["title"] = title
        state["scope"] = (max(documents, key=lambda d: _ff_area(_ff_rect(d)))
                          if documents else window)
        state["fields"] = fields
        return fields

    def _ff_desktop_scope(self):
        return self._ff_desktop_state().get("scope")

    def _ff_desktop_control(self, field):
        """The live control element for *field* (None when it went away)."""
        state = self._ff_desktop_state()
        scope = state.get("scope")
        if scope is None:
            return None
        uia = state["uia"]
        automation_id = str(field.get("id") or "")
        has_id = bool(automation_id) and not automation_id.startswith("uia_")
        label = str(field.get("label") or "")
        named = None
        for control in _ff_find(scope, uia, _EDIT, _COMBO, _CHECK):
            if has_id and _ff_prop(control, "CurrentAutomationId") == automation_id:
                return control
            if named is None and label and _ff_prop(control, "CurrentName") == label:
                named = control
        return named

    def _ff_click_anchor(self, field):
        """The box to click in order to hit *field*'s control."""
        state = self._ff_desktop_state()
        control = self._ff_desktop_control(field)
        if control is not None:
            window_rect = _ff_rect(state.get("window")) if state.get("window") \
                else None
            box = (_ff_parent_rect(state["uia"], control, window_rect)
                   or _ff_visible(control))
            if box:
                return list(box)
        return field.get("box") or []

    # ------------------------------------------------------------ enumerate

    def _ff_enumerate_desktop(self, cfg, stop_flag):
        """The fillable controls of the window that holds the form."""
        if self._ff_halt(stop_flag):
            return []
        self._ff_desktop = None          # a new pass reads the screen again
        state = self._ff_desktop_state()
        if state["uia"] is None:
            return []
        fields = state.get("fields")
        if fields is None:
            fields = self._ff_desktop_pick(state)
        if not fields:
            log_block(logger, logging.WARNING, "Form Filler",
                      "no fillable control in any window: UI Automation sees "
                      "no Edit/ComboBox/CheckBox/RadioButton field.  A form "
                      "drawn on a canvas has no UIA tree - fill it with the "
                      "web engine or Handle nodes instead.")
            return []
        log_block(logger, logging.INFO, "Form Filler",
                  "filling %r (%d field(s))" % (
                      str(state.get("title"))[:120], len(fields)))
        # A list-backed control has NO options until it is opened, so the model
        # would answer free text the widget can never commit (the web twin
        # reads them for the same reason).
        for field in fields:
            if self._ff_halt(stop_flag):
                break
            if (str(field.get("kind")) == "choice" and not field.get("options")
                    and not field.get("option_boxes")):
                options = self._ff_desktop_options(field, stop_flag)
                if options:
                    field["options"] = [name for name, _rect in options]
        return fields

    def _ff_open_options(self, field, stop_flag):
        """Open *field*'s list and return the rows that appeared, list OPEN."""
        state = self._ff_desktop_state()
        scope = state.get("scope")
        anchor = self._ff_click_anchor(field)
        if scope is None or not anchor:
            return []
        uia = state["uia"]
        before = _ff_option_texts(uia, scope)
        for _attempt in range(2):
            if self._ff_halt(stop_flag):
                return []
            self._ff_desktop_click(anchor, stop_flag)
            deadline = time.time() + _OPEN_TIMEOUT_S
            while time.time() < deadline:
                time.sleep(0.15)
                new = _ff_new_options(before, _ff_option_texts(uia, scope),
                                      anchor)
                if new:
                    return new
                if self._ff_halt(stop_flag):
                    return []
        return []

    def _ff_desktop_options(self, field, stop_flag):
        """``[(option, box)]`` of a list-backed control (peek: closes it again)."""
        if field.get("option_boxes"):
            return [(name, tuple(box))
                    for name, box in field["option_boxes"].items()]
        options = self._ff_open_options(field, stop_flag)
        if options:
            self._ff_press_escape()
        return options

    @staticmethod
    def _ff_press_escape():
        try:
            import pyautogui
            pyautogui.press("escape")
        except Exception:
            pass

    def _ff_desktop_click(self, box, stop_flag):
        """Activate the target window, then click the box's centre."""
        if not box:
            return
        state = getattr(self, "_ff_desktop", None) or {}
        window = state.get("window")
        if window is not None:
            _ff_activate(window)
        point = _ff_center(box)
        self._execute_action("click", (int(point[0]), int(point[1])), stop_flag)

    # ---------------------------------------------------------------- write

    def _ff_write_desktop(self, field, value, stop_flag, driver=None):
        """Write *value* through the control's own family rail."""
        kind = str(field.get("kind") or "text").lower()
        if kind == "switch":
            return self._ff_write_switch(field, value, stop_flag)
        if kind == "choice":
            return self._ff_write_choice(field, value, stop_flag)
        return self._ff_write_text(field, value, stop_flag)

    def _ff_write_text(self, field, value, stop_flag):
        """Click the control's own box, clear it, type the value."""
        label = field.get("label") or ""
        box = self._ff_click_anchor(field)
        if not box:
            logger.warning("[FORM] Desktop write: no box for '%s' - skipped",
                           label)
            return False
        cfg = getattr(self, "_ff_pass_cfg", None) or {}
        try:
            self._ff_desktop_click(box, stop_flag)
            ah = getattr(self, "action_handlers", None)
            if not (ah and hasattr(ah, "handle_type_string_action")):
                logger.error("[FORM] Desktop write: no action handler available")
                return False
            import pyautogui
            try:
                pyautogui.hotkey("ctrl", "a")  # clear existing content
            except Exception:
                pass
            ah.handle_type_string_action(0, {
                "text": value,
                "force_typing": True,
                "batch_size": cfg.get("typing_batch_size") or 20,
                "batch_delay": cfg.get("typing_batch_delay") or 0.02,
                "char_by_char": False,
                "keystroke_delay": 0.001,
            }, stop_flag)
            return True
        except Exception as exc:
            logger.error("[FORM] Desktop write failed for '%s': %s", label, exc)
            return False

    def _ff_write_choice(self, field, value, stop_flag):
        """Click the option the answer names (opening the list when needed)."""
        label = field.get("label") or ""
        boxes = field.get("option_boxes") or {}
        if boxes:
            # A radio group: every option is already on screen.
            names = list(boxes)
            pick = (_ff_option_match(value, names)
                    or self._ff_pick_option_semantically(label, value, names))
            if not pick:
                self._ff_log_unmatched(label, value, names)
                return False
            self._ff_desktop_click(boxes[pick], stop_flag)
            return True
        opened = self._ff_open_options(field, stop_flag)
        if not opened:
            logger.warning("[FORM] Desktop choice '%s': the control opened no "
                           "option list - nothing to pick", label)
            return False
        names = [name for name, _rect in opened]
        pick = (_ff_option_match(value, names)
                or self._ff_pick_option_semantically(label, value, names))
        if not pick:
            self._ff_log_unmatched(label, value, names)
            self._ff_press_escape()
            return False
        target = dict(opened).get(pick)
        self._ff_desktop_click(target, stop_flag)
        time.sleep(0.3)
        # Only dismiss a list that is STILL open: ESC on a modal form is not
        # free (it can close the dialog), so it is never sent blind.
        state = self._ff_desktop_state()
        still = {name for name, _rect in
                 _ff_option_texts(state["uia"], state.get("scope"))}
        if all(name in still for name in names):
            self._ff_press_escape()
        return True

    @staticmethod
    def _ff_log_unmatched(label, value, names):
        log_block(logger, logging.WARNING, "Form Choice: %s" % label,
                  "the answer %r matches none of the %d option(s): %s"
                  % (str(value)[:60], len(names), ", ".join(names)))

    def _ff_pick_option_semantically(self, label, value, names):
        """Laya's semantic pick over the option list (the web rail's rung 2)."""
        try:
            from AI.laya_hooks import choose_option
            index, _answered = choose_option(label, value, list(names))
        except Exception:
            return ""
        if index is not None and 0 <= index < len(names):
            return names[index]
        return ""

    def _ff_write_switch(self, field, value, stop_flag):
        """Settle a CheckBox: click only when it disagrees with the answer."""
        label = field.get("label") or ""
        box = field.get("box") or []
        if not box:
            return False
        control = self._ff_desktop_control(field)
        state = _ff_toggle(control) if control is not None else None
        want = _ff_truthy(value)
        if state is not None and state == want:
            log_block(logger, logging.INFO, "Form Switch: %s" % label,
                      "already %s - left unchanged"
                      % ("on" if state else "off"))
            return True
        if state is None and not want:
            # State unknown and the answer is NO: a click could TICK it, which
            # is the one mistake a toggle must never make.
            return True
        self._ff_desktop_click(box, stop_flag)
        return True

    # ----------------------------------------------------------------- read

    def _ff_read_desktop(self, field, stop_flag, driver=None):
        """The value the control HOLDS now, or None when it cannot be read.

        ``None`` (not ``""``) is what keeps an unreadable widget from being
        reported as an empty write: the verify rail skips it instead of
        re-asking a field that did land.
        """
        kind = str(field.get("kind") or "text").lower()
        if kind == "choice" and field.get("option_boxes"):
            return None      # a radio group has no single readable value
        control = self._ff_desktop_control(field)
        if control is None:
            return None
        if kind == "switch":
            state = _ff_toggle(control)
            return None if state is None else ("on" if state else "off")
        value = _ff_value(control)
        if value is not None and value.strip():
            return value
        state = self._ff_desktop_state()
        box = _ff_visible(control) or (field.get("box") or None)
        scope = state.get("scope")
        if not box or scope is None:
            return value
        # A hidden dummy input is 3 px wide: the value a react-select COMMITTED
        # is painted across the whole control, so the search area is the click
        # anchor (the parent container), not the input itself.
        shown = (_ff_row_text(state["uia"], scope,
                              self._ff_click_anchor(field) or box)
                 or _ff_row_text(state["uia"], scope, box))
        return shown or (value or "")

    def _ff_state_desktop(self, field, stop_flag, driver=None):
        """``{found, value, valid, message}`` for the repair pass.

        Desktop has no native constraint API to ask, so ``valid`` is always
        True: the repair pass still corrects CONTENT (placeholder text, an
        invented identifier, an over-long answer) off the read-back.
        """
        value = self._ff_read_desktop(field, stop_flag)
        if value is None:
            return None
        return {"found": True, "value": value, "valid": True, "message": ""}
