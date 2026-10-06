"""Picked-scope resolution and its persistence."""

import json
import sys

from ._test_support import (
    _Harness,
)


def test_scope_selectors_from_picker_result():
    ff = _Harness()
    # Picker result shape: {"locator": {...}, "frame_path": [...], ...}
    picked = {"locator": {"css": "div.easy-apply", "id": "apply"},
              "frame_path": [0], "cross_origin_frame": False}
    # The id is tried before the recorded (hashable) css.  It is an ATTRIBUTE
    # selector: a generated id can hold CSS-invalid characters (React 19's
    # useId emits '«r54»'), which '#id' cannot express.
    assert ff._ff_scope_selectors(picked) == ['[id="apply"]', "div.easy-apply"]
    # Bare locator dict (no nesting) is accepted too.
    assert ff._ff_scope_selectors({"css": "form.x"}) == ["form.x"]
    # JSON string is accepted.
    assert ff._ff_scope_selectors(json.dumps(picked)) == ['[id="apply"]',
                                                         "div.easy-apply"]
    # Empty / junk -> no scoping (whole document).
    assert ff._ff_scope_selectors("") == []
    assert ff._ff_scope_selectors(None) == []
    assert ff._ff_scope_selectors("{not json") == []


def test_scope_ladder_prefers_stable_shape_over_hashed_css():
    """The recorded css bakes in a rotating #emberNNN id and a hashed class;
    the scope must be re-found by its STABLE shape on the next form instance."""
    ff = _Harness()
    picked = {"locator": {
        "css": ("div#ember518 > div.lXeRWjeRtKPhubkYPSWIPPEiFpfihtXzWxM > "
                "div > form > div.ph5"),
        "id": None, "data_attrs": {}, "tag": "div", "classes": ["ph5"],
        "ancestor_css": ("div#ember518 > div:nth-of-type(1) > "
                          "div:nth-of-type(2) > form:nth-of-type(1) > "
                          "div:nth-of-type(1)"),
    }, "frame_path": [], "cross_origin_frame": False}
    sels = ff._ff_scope_selectors(picked)
    assert sels[0] == "form > div.ph5"     # stable shape wins
    assert "div.ph5" in sels
    assert sels[-1].startswith("div#ember518")   # hashed css is the last resort


def test_scoped_scan_does_not_fall_back_to_the_whole_page():
    """A picked scope that holds no field must NOT fall back to the whole page.

    That fallback is exactly what reached the site's global 'Search' box behind
    the modal and broke the flow (live: 'Resolved via: whole page' -> 'Form
    Fields to Fill (1): Search', answered then failed).  A stale / empty scope
    yields an EMPTY field list; the chain's own loop re-runs the node against
    the next page.
    """
    import json as _json

    enum_scopes = []

    class _Driver:
        def execute_script(self, script, *args):
            if args:      # the enumerator passes (scopeSelectors, inFrame)
                enum_scopes.append(list(args[0] or []))
            return "[]"   # the scope holds nothing (and so does the page)

    h = _Harness()
    h._ff_web_driver = _Driver()    # pre-seeded: no browser is acquired
    cfg = h._ff_cfg({"web_scope": _json.dumps({"locator": {
        "css": "#stale", "id": "stale", "tag": "div",
        "classes": ["ph5"], "ancestor_css": "form > div",
    }})})
    fields = h._ff_enumerate_web(cfg, None)
    assert fields == []
    # The enumerator was only ever called WITH the picked scope - never `[]`.
    assert enum_scopes and all(s for s in enum_scopes)


def test_scope_frame_reads_window_and_iframe_context():
    """A pick carries the WINDOW and IFRAME it was made in; both are needed to
    re-enter a popup before its container is resolved."""
    ff = _Harness()
    picked = {"locator": {"css": "div#modal"}, "frame_path": [{"tag": "iframe"}],
              "cross_origin_frame": False, "_window_ordinal": 1}
    ctx = ff._ff_scope_frame(picked)
    assert ctx["window_ordinal"] == 1
    assert ctx["frame_path"] == [{"tag": "iframe"}]
    assert ctx["cross_origin_frame"] is False
    # JSON string (as persisted on the node) is accepted too.
    assert ff._ff_scope_frame(json.dumps(picked))["window_ordinal"] == 1
    # No scope / junk -> nothing to enter (the whole-page scan runs).
    assert ff._ff_scope_frame("") is None
    assert ff._ff_scope_frame(None) is None
    assert ff._ff_scope_frame("{not json") is None
    # A same-document pick has neither -> no entry attempted.
    bare = ff._ff_scope_frame({"locator": {"css": "#x"}})
    assert bare["window_ordinal"] is None and bare["frame_path"] == []


def test_enumerate_enters_the_picked_popup_window_and_iframe():
    """Regression: the form filler resolved the picked container in the BASE
    document, ignoring the popup the picker had correctly picked ("the popup is
    ignored, it only works on the base page").  The recorded window ordinal and
    iframe chain must be entered before the scan, and the scan must root the
    scope search in that frame."""
    import json as _json

    class _Frame:
        pass

    class _Switch:
        def __init__(self, d):
            self._d = d

        def window(self, handle):
            self._d.switches.append(("window", handle))
            self._d._c = self._d.handles.index(handle)

        def default_content(self):
            self._d.switches.append(("default",))

        def frame(self, element=None):
            self._d.switches.append(("frame", element))

        def parent_frame(self):
            pass

    class _Driver:
        handles = ["w0", "w1"]

        def __init__(self):
            self._c = 0
            self.switches = []
            self.switch_to = _Switch(self)
            self.execs = []

        @property
        def current_window_handle(self):
            return self.handles[self._c]

        @property
        def window_handles(self):
            return list(self.handles)

        def execute_script(self, script, *args):
            self.execs.append((script, args))
            return "[]"

        def find_elements(self, by, value):
            return [_Frame()]      # the popup iframe element

    driver = _Driver()
    h = _Harness()
    h._ff_web_driver = driver       # pre-seeded: the scope entry still applies
    cfg = h._ff_cfg({"web_scope": _json.dumps({
        "locator": {"css": "div#modal", "tag": "div"},
        "frame_path": [{"tag": "iframe", "css": "#popup"}],
        "cross_origin_frame": False, "_window_ordinal": 1,
    })})
    h._ff_enumerate_web(cfg, None)

    # The popup WINDOW (ordinal 1) and then its IFRAME were entered.
    assert ("window", "w1") in driver.switches
    kinds = [s[0] for s in driver.switches]
    assert "frame" in kinds
    assert kinds.index("window") < kinds.index("frame")
    # The scope scan rooted the search in the entered frame (2nd JS arg).
    enum_calls = [a for s, a in driver.execs if "scopeSelectors, inFrame" in s]
    assert enum_calls and all(len(a) == 2 and a[1] is True for a in enum_calls)
    # The scope context is applied ONCE per run, not per candidate.
    assert kinds.count("window") == 1


def test_js_enumerator_is_scope_aware():
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    # Resolves the picked container through the deep search and enumerates its
    # subtree; the whole document stays the fallback when the scope is gone.
    assert "__wvpDeepFindAny" in enum_js
    assert "scopeSelectors" in enum_js
    assert "arguments[0]" in enum_js
    assert "contentDocument" in enum_js  # a picked iframe is entered


def test_dialog_round_trips_web_scope():
    from PyQt5.QtWidgets import QApplication
    _app = QApplication.instance() or QApplication([])

    from NGUI.dialogs.form_filler_dialog import FormFillerDialog

    picked = {"locator": {"css": "#apply-form"}, "frame_path": []}
    dlg = FormFillerDialog(None, {"web_scope": json.dumps(picked)})
    out = dlg.get_config()
    assert json.loads(out["web_scope"])["locator"]["css"] == "#apply-form"
    assert "apply-form" in dlg.scope_label.text()

    # Clearing resets to the whole page.
    dlg.clear_scope()
    assert dlg.get_config()["web_scope"] == ""
    assert "whole page" in dlg.scope_label.text()


def test_edit_operation_persists_web_scope(monkeypatch):
    """Regression: the edit path must copy every dialog field onto the node.

    A property the dialog returns but ``edit_form_filler_node`` does not copy
    silently reverts to '' on save - web_scope was dropped this way, so the
    picked container never took effect and enumeration stayed whole-page.
    """
    from NGUI.graph_elements.node_operations_modules import form_filler as ff_ops
    import NGUI.dialogs.form_filler_dialog as ffd

    picked = {"locator": {"css": "#apply-form"}, "frame_path": []}
    cfg = {
        "mode": "web", "instruction": "x", "fields_include": "",
        "fields_skip": "", "probe_top_k": 3, "probe_char_budget": 1500,
        "probe_context_chars": 6000, "verify": True, "max_fields": 40,
        "engine": "ollama", "model": "m", "temperature": 0.1,
        "max_tokens": 256, "rag_documents": [],
        "web_scope": json.dumps(picked),
    }

    class _FakeDialog:
        def __init__(self, *a, **k):
            pass

        def exec_(self):
            return 1  # QDialog.Accepted

        def get_config(self):
            return cfg

    class _FakeNode:
        def __init__(self):
            self.props = {}
            self.id = "ff1"

        def get_form_filler_config(self):
            return {"rag_documents": []}

        def get_property(self, key):
            return self.props.get(key)

        def set_property(self, key, value):
            self.props[key] = value

        def set_name(self, name):
            self.name = name

    monkeypatch.setattr(ffd, "FormFillerDialog", _FakeDialog)

    class _Ops(ff_ops.FormFillerOperationsMixin):
        parent_widget = None

    node = _FakeNode()
    _Ops().edit_form_filler_node(node)
    assert json.loads(node.props["web_scope"])["locator"]["css"] == "#apply-form"


def test_scope_is_identified_by_position_not_by_content():
    """A pick means "this KIND of element at this PLACE".

    The recorded id / hashed CSS-module class / the div's own text all differ
    on the next form instance, so an identity built from them only ever
    resolves on the recording's own instance - the automation breaks when the
    same container is used on another form.  The structural path (tag plus
    nth-of-type at every level) is the same on any form with the same layout,
    so it LEADS the ladder; the content rungs stay behind it as fallbacks.

    Verified live: it resolved the LinkedIn Easy Apply container - a div with
    no id and only hashed classes - and the enumerator returned exactly its 6
    fields.
    """
    ff = _Harness()
    picked = {"locator": {
        "tag": "div", "id": None, "classes": ["dj9mgo", "dj9al5"],
        "css": "div.dj9mgo",
        "structural_xpath": "/html/body/dialog[1]/div[1]/div[2]/div[1]",
    }}
    sels = ff._ff_scope_selectors(picked)
    assert sels[0] == ("dialog:nth-of-type(1) > div:nth-of-type(1) > "
                       "div:nth-of-type(2) > div:nth-of-type(1)")
    # No volatile content leads the ladder.
    assert "dj9mgo" not in sels[0]
    # The conversion itself: tag + place, nothing else.
    assert ff._ff_scope_positional(
        {"structural_xpath": "/html/body/form[1]/div[3]"}) == [
            "form:nth-of-type(1) > div:nth-of-type(3)"]
    # No / junk structural path -> no positional rung (falls back as before).
    assert ff._ff_scope_positional({}) == []
    assert ff._ff_scope_positional({"structural_xpath": "/html/nope[!"}) == []
    assert ff._ff_scope_selectors({"locator": {"css": "form.x"}}) == ["form.x"]
