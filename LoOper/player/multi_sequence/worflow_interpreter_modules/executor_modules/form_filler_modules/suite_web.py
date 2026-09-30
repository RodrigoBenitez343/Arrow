"""The web substrate: JS contracts, writes, the overlay."""

import pytest

from ._test_support import (
    _Harness,
    _node,
    _wire,
)


def test_web_js_contracts():
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "querySelectorAll" in enum_js
    assert "shadowRoot" in enum_js
    assert "JSON.stringify" in enum_js
    assert "outerHTML" not in enum_js and "innerHTML" not in enum_js  # never raw HTML
    set_js = web_actions.JS_SET_FIELD
    assert "__wvpDeepFindAny" in set_js       # resolves the runtime's selectors
    assert "getOwnPropertyDescriptor" in set_js  # native setter (React/Vue safe)
    assert "dispatchEvent" in set_js
    # A field inside a SAME-ORIGIN IFRAME is not an instanceof the TOP window's
    # HTMLInputElement, so the write must NOT use instanceof (it would reject
    # every iframe-hosted field as 'not editable').
    assert "instanceof HTMLInputElement" not in set_js
    assert "ownerDocument.defaultView" in set_js


def test_shadow_host_gate_needs_no_box():
    """A shadow HOST is entered when it is NOT HIDDEN - its own box is NOT
    required.  Such outlets are absolutely-positioned wrappers with a ZERO-height
    box whose shadow content overflows it (LinkedIn's ``interop-outlet`` is
    1360x0, position:absolute), so a box / on-top test on the host rejected the
    whole subtree and the Easy Apply form enumerated as 0 fields (live: 2 real
    fields once the host gate stopped requiring a box).
    """
    from player.web import actions as web_actions

    fg = web_actions.JS_FOREGROUND
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "function __wvpHostEnterable(" in fg
    assert "__wvpHostEnterable(h)" in enum_js   # host gate is box-free...
    assert "__wvpVisible(h)" not in enum_js       # ...it no longer needs a box
    # ...while the FIELD gates still require visibility and on-top position, so
    # a covered / hidden control is skipped - satisfied by the control OR its
    # label, so the old raw-element behaviour is preserved (never subtracted).
    assert "if (!__wvpVisible(el) && !__wvpVisible(vis_el)) continue;" in enum_js
    assert ("if (!scoped && !__wvpTopmost(el) && !__wvpTopmost(vis_el)) "
            "continue;" in enum_js)
    # The host helper keeps the hidden / aria-hidden / inert checks.
    assert "aria-hidden" in fg and "inert" in fg


def test_web_js_choice_and_combo_contracts():
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "qTextFor" in enum_js            # group QUESTION is resolved
    assert "isChoiceRole" in enum_js
    # ARIA form widgets ARE enumerated (a custom combobox / toggle / Yes-No
    # radio group is not an <input> and used to be invisible to the filler)...
    assert '[role="radio"]' in enum_js and '[role="switch"]' in enum_js
    assert '[role="combobox"]' in enum_js and '[role="checkbox"]' in enum_js
    # ...but a NON-NATIVE widget only counts inside a <form> or the picked
    # scope, and never when it merely WRAPS a native control (already counted).
    assert "if (!native) {" in enum_js
    assert "el.querySelector(" in enum_js
    assert "if (!(scoped || (el.closest && el.closest('form')))) continue;" in enum_js
    # Foreground only: covered / background elements are never enumerated.
    assert "__wvpVisible" in enum_js and "__wvpTopmost" in enum_js
    choice_js = web_actions.JS_SET_CHOICE
    assert "__wvpDeepFindAny" in choice_js  # resolves the runtime's selectors
    assert "__wvpTopmost" in choice_js       # never clicks a covered twin
    assert "label[for=" in choice_js        # option label matched
    # A choice control is often the REAL <input> hidden behind a styled label,
    # so the gate is judged on the label and the LABEL is what gets clicked -
    # through the ONE shared predicate, so the write and the read-back can
    # never disagree about which element is actionable.
    assert "function __wvpClickTarget(" in choice_js
    assert "__wvpClickTarget(" in web_actions.JS_FIELD_STATE
    assert "closest('label')" in choice_js
    set_js = web_actions.JS_SET_FIELD
    assert "selectedIndex" in set_js        # native select
    assert "ArrowDown" in set_js            # typeahead combobox nudge
    assert "__wvpTopmost" in set_js         # never types into a covered twin
    assert "kind === 'choice'" in web_actions.JS_FIELD_STATE


def test_counter_and_validation_text_never_become_the_field_label():
    """The sibling-text fallback reaches any nearby block, so it adopted the
    page's own counter ("0/20 0 of 20 characters") and later its validation
    message ("Invalid input 133/20 133 of 20 characters") as the field LABEL:
    the model was asked a nonsense question and the label changed every pass."""
    from player.web import actions as web_actions
    fg = web_actions.JS_FOREGROUND
    assert "function isNoise(" in fg
    assert "!isNoise(ct)" in fg and "!isNoise(kt)" in fg
    # A maxlength set as a PROPERTY (React/Vue) is a real limit too.
    assert "el.maxLength > 0" in web_actions.JS_ENUMERATE_FORM_FIELDS


def test_the_pages_own_character_counter_supplies_the_missing_maxlength():
    """A page can enforce a limit with NO native maxlength: it renders its own
    counter / validation line and wires it through aria-describedby.  With the
    limit invisible the repair pass had nothing to flag or fit, so the field
    kept a rejected 418-character value in a 20-character input and the Review
    loop never ended (live: LinkedIn's gross-salary input)."""
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "function charLimitFromPage(" in enum_js
    # Wired in only AFTER the native attribute and property give nothing.
    assert "|| charLimitFromPage(el)," in enum_js
    # Read from the field's OWN described-by text...
    assert "aria-describedby" in enum_js
    # ...and only the two shapes that state a limit: "of N characters", "N/M".
    assert "characters?" in enum_js
    assert "getElementById" in enum_js


def test_the_label_noise_filter_reaches_the_browser_as_real_word_boundaries():
    """``isNoise`` lives in a NON-raw Python string, so a single \\b is Python's
    BACKSPACE escape: the regex reached the browser with CONTROL bytes instead
    of word boundaries and matched nothing, so the page's counter /
    "Invalid input 418/20 418 of 20 characters" was adopted as the field LABEL
    (live).  The repair pass then re-asked a nonsense question and the rejected
    field never moved.  Guard the encoding, not just the string's presence."""
    from player.web import actions as web_actions
    fg = web_actions.JS_FOREGROUND
    bad = [c for c in "\x07\x08\x0b\x0c" if c in fg]
    assert not bad, (
        "a control character reached the JS - an escape collapsed in the "
        "non-raw Python string: %r" % bad)
    # The boundary escapes are DOUBLED in the source so the runtime JS holds \b.
    assert r"/\bcharacters?\b/i" in fg
    assert r"/\binvalid (input|value|format)\b/i" in fg


def test_field_resolution_never_takes_an_ambiguous_candidates_first_match():
    """A candidate that matches SEVERAL visible controls is NOT this field's
    identity: a bare 'input' matches every input on the page and its FIRST match
    IS the form's first field - the way a value meant for another question was
    read from / typed over the already-correct 'First name'."""
    from player.web import actions as web_actions
    set_js = web_actions.JS_SET_FIELD
    assert "function onlyOne(" in set_js
    assert "function bareTag(" in set_js
    # The want-indexed fallback never runs on a bare tag.
    assert "if (bareTag(selectors[_f])) continue;" in set_js
    state_js = web_actions.JS_FIELD_STATE
    assert "var _choice = (kind === 'choice' || kind === 'switch');" in state_js
    assert ("if (_hits.length === 1) { _seen.push(_hits[0]); "
            "controls = [_hits[0]]; break; }" in state_js)


def test_write_resolves_an_spa_option_label_from_a_sibling_block(tmp_path):
    """The write's option label must resolve an SPA choice option whose text
    lives in a SIBLING block - a hidden <input> plus an EMPTY <label for> (the
    radio circle).  A `label[for]`-only read returned '' for exactly that shape,
    so `matches('')` was always false and the write reported 'target unresolved
    or not editable' while the model had picked the CORRECT option (verified
    live on LinkedIn's Easy Apply questionnaire: the enumerator read the full
    option text and the write read an empty string).
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    from player.web import actions as web_actions

    fg = web_actions.JS_FOREGROUND
    start = fg.index("function __wvpFieldLabel(el) {")
    end = fg.index("/**\n * The page's current TOP LAYER")
    fn = fg[start:end]

    harness = """
    global.window = {CSS: {escape: function (s) { return String(s); }}};
    global.document = {querySelector: function () { return null; },
                       getElementById: function () { return null; }};
    function __wvpCssEsc(s) { return String(s); }
    function nd(tag, text, kids) {
      return {tagName: tag.toUpperCase(), innerText: text || '',
              textContent: text || '', children: kids || [], parentElement: null,
              getAttribute: function () { return ''; },
              closest: function () { return null; },
              querySelector: function () { return null; },
              contains: function () { return false; }};
    }
    var circle = nd('label', '', []);
    var input = nd('input', '', []);
    var row = nd('div', '', [input, circle]);
    var text = nd('label', 'Option text', []);
    var outer = nd('div', '', [row, text]);
    input.parentElement = row; row.parentElement = outer; text.parentElement = outer;
    row.querySelector = function () { return circle; };   // ':scope > label' = EMPTY circle
    row.contains = function (n) { return n === input; };
    outer.contains = function (n) { return n === input || n === row; };
    process.stdout.write(__wvpFieldLabel(input));
    """
    p = tmp_path / "spa_option_label.js"
    p.write_text(fn + "\n" + harness, encoding="utf-8")
    proc = subprocess.run([node, str(p)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Option text", proc.stdout

    # ONE option-label resolver, shared by the enumeration, the write and the
    # read-back - a local copy in either writer is exactly the disagreement
    # that made a correct answer never land.
    assert "function __wvpOptionLabel(" in fg
    assert "return __wvpFieldLabel(el);" in web_actions.JS_ENUMERATE_FORM_FIELDS
    for name in ("JS_SET_CHOICE", "JS_FIELD_STATE"):
        js = getattr(web_actions, name)
        assert "__wvpOptionLabel(" in js, name
        assert "function optLabel(" not in js, name   # no local duplicate


def test_foreground_helpers_are_form_filler_only():
    from player.web import actions as web_actions
    fg = web_actions.JS_FOREGROUND
    assert "function __wvpVisible(" in fg
    assert "function __wvpTopmost(" in fg
    assert "function __wvpHostEnterable(" in fg
    assert "function __wvpDeepAll(" in fg
    # The shared deep-search must stay lean and coordinate-free: it is prepended
    # to the pointer/typing snippets, which must never hit-test by coordinates.
    deep = web_actions.JS_DEEP_SEARCH
    assert "elementFromPoint" not in deep
    assert "__wvpVisible" not in deep
    assert "elementFromPoint" not in web_actions.JS_DOM_POINTER
    # ...while every form-fill snippet carries the foreground gate.
    for name in ("JS_ENUMERATE_FORM_FIELDS", "JS_SET_FIELD",
                 "JS_SET_CHOICE", "JS_FIELD_STATE"):
        js = getattr(web_actions, name)
        assert "function __wvpVisible(" in js, name
        assert "function __wvpTopmost(" in js, name


def test_scoped_enumeration_drops_the_on_top_gate():
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "function visit(root, depth, scoped, confine)" in enum_js
    assert ("if (!scoped && !__wvpTopmost(el) && !__wvpTopmost(vis_el)) "
            "continue;" in enum_js)
    assert "visit(root, 0, scoped, null)" in enum_js
    # An UNSCOPED scan is confined to an open popup's layer (the page behind a
    # popup is not "covered" everywhere - a header search box stays visible).
    assert "__wvpTopLayers()" in enum_js
    assert "if (confine && !__wvpOwns(el, confine)) continue;" in enum_js
    # A field scrolled out of view is not "covered" - the viewport early-out
    # must allow it, else every field below the fold is missed.
    assert "Scrolled out of view" in web_actions.JS_FOREGROUND


def test_form_js_snippets_compile(tmp_path):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    from player.web import actions as web_actions
    for name in ("JS_ENUMERATE_FORM_FIELDS", "JS_SET_FIELD",
                 "JS_SET_CHOICE", "JS_FIELD_STATE",
                 "JS_COMBO_OPTIONS",
                 "JS_SETTLE_ARM", "JS_SETTLE_READ"):
        js = getattr(web_actions, name)
        # The snippets are function BODIES: wrap them so `node --check` can
        # parse the top-level `return` / `arguments`.
        f = tmp_path / (name + ".js")
        f.write_text("function __wvpWrap(){\n" + js + "\n}\n", encoding="utf-8")
        proc = subprocess.run([node, "--check", str(f)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"{name}: {proc.stderr}"


def test_hidden_choice_is_gated_on_its_label():
    """A styled radio / checkbox / switch is the REAL input hidden behind a
    label (display:none / zero box).  Testing the raw input dropped a whole
    Yes/No question list as 'invisible' - the label is what a user clicks, so
    visibility and on-top are judged on it (exactly like JS_SET_CHOICE)."""
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "var vis_el = el;" in enum_js
    assert "if (isChoiceRole(el, tag, itype) || role === 'switch')" in enum_js
    assert "el.closest('label')" in enum_js
    assert "vis_lab = document.querySelector('label[for=" in enum_js
    # Additive: the control OR its label may satisfy the gate.
    assert "!__wvpVisible(el) && !__wvpVisible(vis_el)" in enum_js


def test_enumeration_has_no_whole_page_fallback():
    """An OPEN popup is the ONLY place a field may live: NO whole-page fallback.

    That fallback reached the global 'Search' box behind the modal (live:
    'Resolved via: whole page' -> 'Form Fields to Fill (1): Search').  A popup
    that holds no field for a moment yields NOTHING and the settle gate waits.
    A page with NO popup is still enumerated normally (the `else` branch).
    """
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "keep the old whole-page behaviour" not in enum_js
    assert "if (!out.length) {" not in enum_js
    # No popup -> the whole document is the NORMAL scan (not a fallback).
    assert enum_js.count("visit(root, 0, scoped, null)") == 1
    assert "__wvpModalOpen" not in enum_js


def test_choice_write_and_readback_collect_across_all_candidates():
    """A merged group's ladder is one anchor per OPTION, so the writer and the
    read-back must collect controls across ALL candidates.  Stopping at the
    first selector that matched anything left the answer's own option out of the
    set - nothing could click it (live: selectors=['input', ''] ->
    'target unresolved or not editable')."""
    from player.web import actions as web_actions
    for name in ("JS_SET_CHOICE", "JS_FIELD_STATE"):
        js = getattr(web_actions, name)
        assert "if (controls.length) break;" not in js, name
        assert "_seen.indexOf(" in js, name


def test_settle_gate_probe_is_armed_and_polled():
    from player.web import actions as web_actions
    assert "MutationObserver" in web_actions.JS_SETTLE_ARM
    assert "__wvpSettle" in web_actions.JS_SETTLE_ARM
    assert "__wvpSettle" in web_actions.JS_SETTLE_READ
    assert "return -1" in web_actions.JS_SETTLE_READ   # unmeasurable -> no block


def test_settle_gate_probe_uses_the_mutation_age():
    h = _Harness()

    class _Web:
        JS_SETTLE_READ = "read"

    class _Ages:
        def __init__(self, ages):
            self.ages = list(ages)

        def execute_script(self, script, *a):
            return self.ages.pop(0) if self.ages else 1

    # Still mutating -> never quiet within the budget.
    assert h._ff_dom_quiet(_Ages([10]), _Web(), None, 0.05, 0.12) is False
    # The quiet age (>= threshold) settles at once.
    assert h._ff_dom_quiet(_Ages([500]), _Web(), None, 0.05, 0.5) is True

    class _Bad:
        def execute_script(self, script, *a):
            raise RuntimeError("no observer")

    # Unmeasurable -> treated as settled (the gate must never block).
    assert h._ff_dom_quiet(_Bad(), _Web(), None, 0.05, 0.5) is True


def test_settle_gate_waits_while_the_modal_is_still_loading():
    """The gate keeps re-scanning the picked container while the page is still
    MUTATING, so it does not run mid-transition - and returns the moment the
    container holds a field (it does not wait out the whole timeout)."""
    h = _Harness()
    h._FF_SETTLE_TIMEOUT = 1.0
    h._FF_SETTLE_QUIET = 100.0     # nothing ever counts as "quiet" here

    class _Web:
        JS_SETTLE_ARM = "arm"
        JS_SETTLE_READ = "read"

    class _Drv:
        def execute_script(self, script, *a):
            return 5               # always mutating (still loading)

    scans = {"n": 0, "whole": 0}

    def _scan(driver, web_actions, selectors, in_frame=False):
        if not selectors:          # the whole-page fallback must NOT be used here
            scans["whole"] += 1
            return []
        scans["n"] += 1
        return [] if scans["n"] < 2 else [{"id": "f", "label": "City"}]

    h._ff_scan_web = _scan
    fields, via = h._ff_wait_and_scan(
        _Drv(), _Web(), ["form.modal"], False, None)
    assert [f["id"] for f in fields] == ["f"]
    assert scans["n"] == 2         # the empty first scan did NOT end the gate
    assert scans["whole"] == 0     # never fell back to the whole page


def test_settle_gate_never_scans_the_whole_page_for_a_picked_scope():
    """A picked scope that holds no field yields NOTHING - the gate must not
    fall back to the whole page.  Live: 'Resolved via: whole page' produced
    'Form Fields to Fill (1): Search' - the site's global search box behind the
    modal - and broke the flow."""
    h = _Harness()
    h._FF_SETTLE_TIMEOUT = 0.3

    class _Web:
        JS_SETTLE_ARM = "arm"
        JS_SETTLE_READ = "read"

    class _Drv:
        def execute_script(self, script, *a):
            return 500             # settled, yet the scope holds nothing

    seen = []

    def _scan(driver, web_actions, selectors, in_frame=False):
        seen.append(list(selectors))
        return []

    h._ff_scan_web = _scan
    fields, via = h._ff_wait_and_scan(
        _Drv(), _Web(), ["#stale"], False, None)
    assert fields == []
    # The whole page ([]) was NEVER scanned.
    assert seen and all(sel == ["#stale"] for sel in seen)


def test_web_write_marks_the_field_before_setting_it(monkeypatch):
    """The in-page EXECUTION overlay (the orange box every web node shows
    during playback) must box the field BEFORE its value lands."""
    from player.multi_sequence.llm_executor_resources import inputs
    from player.web import actions as web_actions
    from player.web import exec_overlay

    class _Drv:
        def __init__(self):
            self.scripts = []

        def execute_script(self, script, *args):
            self.scripts.append(script)
            return True

    drv = _Drv()
    monkeypatch.setattr(inputs, "_web_driver", lambda: drv)
    h = _Harness()
    field = {"id": "f", "label": "Full name", "kind": "text",
             "selectors": ["input#full-name"], "type_index": 0}
    assert h._ff_write_web(field, "Alex", None) is True
    assert exec_overlay.JS_EXEC_MARK in drv.scripts
    assert drv.scripts.index(exec_overlay.JS_EXEC_MARK) < \
        drv.scripts.index(web_actions.JS_SET_FIELD)
    # The node owns the overlay for the run: enabled once, taken down at the end.
    assert h._ff_overlay_on is True
    h._ff_disable_overlay()
    assert h._ff_overlay_on is False


def test_overlay_names_the_field_being_processed(monkeypatch):
    """The overlay is marked when a field STARTS being reasoned about, not
    only when its value lands: marking at write time alone left the banner
    naming the PREVIOUS field for the whole (slow) probe/extract of the
    current one, so it looked like the wrong question was being processed."""
    from player.multi_sequence.llm_executor_resources import inputs
    from player.web import exec_overlay

    events = []

    class _Drv:
        def execute_script(self, script, *args):
            if script == exec_overlay.JS_EXEC_MARK:
                events.append(("mark", args[2] if len(args) > 2 else ""))
            return True

    monkeypatch.setattr(inputs, "_web_driver", lambda: _Drv())
    h = _Harness()
    _wire(h, fields=[{"id": "a", "label": "First name"},
                     {"id": "b", "label": "Why this role"}])

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint=""):
        events.append(("probe", label))
        return "evidence"
    h._ff_probe = _probe
    h._ff_shared_driver()          # enumeration opened the session (memoised)
    h._execute_form_filling_node(_node(), None)

    marks = [c for k, c in events if k == "mark"]
    assert marks[:2] == ["form: First name", "form: Why this role"]
    # Each field's probe runs AFTER its own mark: the overlay never names the
    # previous question while the current one is being processed.
    assert events.index(("mark", "form: Why this role")) < \
        events.index(("probe", "Why this role"))


def test_shared_web_driver_is_resolved_once_per_run(monkeypatch):
    """ONE browser session per run.  Acquiring it per OPERATION (2 per field
    plus 1 per repair target) re-attaches and re-spawns a browser every few
    seconds, and that endless reusing/spawn/state-saved trio with no other
    output is exactly what reads as an infinite loop during a correction."""
    from player.multi_sequence.llm_executor_resources import inputs

    calls = {"n": 0}

    def _fake_driver():
        calls["n"] += 1
        return object()

    monkeypatch.setattr(inputs, "_web_driver", _fake_driver)
    h = _Harness()
    first = h._ff_shared_driver()
    assert h._ff_shared_driver() is first     # memoised, not re-acquired
    assert calls["n"] == 1
    # Later operations reuse the same session instead of re-attaching.
    h._ff_field_state({"id": "f", "label": "L"}, None)
    assert calls["n"] == 1
    # Nothing to do -> no browser is ever opened.
    h2 = _Harness()
    _wire(h2, fields=[], value="v")
    h2._execute_form_filling_node(_node(), None)
    assert calls["n"] == 1


def test_set_field_reports_a_silently_cleared_typed_value():
    from player.web import actions as web_actions
    js = web_actions.JS_SET_FIELD
    assert "el.value === ''" in js        # a rejected typed value falls back to empty


def test_option_enumeration_does_not_drop_the_tail_of_a_long_list():
    """Every real form option list (countries, roles, universities) is under a
    few hundred entries, but the enumerator cut BOTH a <select> and a combobox
    at 40 - the tail of the list never reached the prompt, so the field looked
    like it had fewer options than the page really offers."""
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "options.length < 300" in enum_js          # a <select>'s own options
    assert "opts.length < 300" in enum_js             # comboOptions()
    assert "options.length < 40" not in enum_js        # the old tail-cutting cap
    assert "opts.length < 40" not in enum_js


def test_a_choice_options_text_is_its_own_not_the_group_question():
    """An option's text can be a bare TEXT NODE beside the control (no element
    of its own).  The generic label resolver walked past it and adopted the
    group's QUESTION, so every 'option' became the question and the list
    collapsed to ONE entry that WAS the question (live: '[choice] Choose one:
    Which option best describes your experience with AWS and DataOps tooling?
    options: Which option best describes your experience with AWS and DataOps
    tooling?')."""
    from player.web import actions as web_actions
    fg = web_actions.JS_FOREGROUND
    # The option's OWN text is the ONE resolver the enumerator, the write and
    # the read-back ALL use, so they can never disagree about what an option is
    # called - that disagreement is why a correct answer never landed.
    assert "function __wvpOptionLabel(" in fg
    assert "node.childNodes" in fg and "el.parentElement" in fg
    for name in ("JS_ENUMERATE_FORM_FIELDS", "JS_SET_CHOICE", "JS_FIELD_STATE"):
        assert "__wvpOptionLabel(" in getattr(web_actions, name), name
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    assert "var olabel = choice ? __wvpOptionLabel(el) : labelFor(el);" in enum_js
    assert "if (!olabel) olabel = labelFor(el);" in enum_js
    # An option can be a full SENTENCE; the generic resolver caps sibling text
    # at 120 chars, which is what rejected a long option and returned the
    # question instead.
    assert "t.length <= 400" in fg
    # The group's question can be the block ABOVE the group container.
    assert "function prevBlockText(node)" in enum_js


def test_readback_of_a_hidden_radio_uses_the_shared_click_target():
    """A choice radio is a hidden <input> whose LABEL is the visible thing, so a
    read-back that required the raw input to be visible collected NOTHING and
    reported an empty value for a selection that had really landed (verified
    live: the radio was ``checked`` and the read-back returned '').  The
    read-back must use the SAME actionable predicate as the write."""
    from player.web import actions as web_actions
    state_js = web_actions.JS_FIELD_STATE
    assert "if (__wvpClickTarget(_all[_v])) _hits.push(_all[_v]);" in state_js
    assert ("if (__wvpVisible(_all[_v]) && __wvpTopmost(_all[_v])) "
            "_hits.push(_all[_v]);") not in state_js


def test_select_prompt_and_combo_options_are_handled():
    """A dropdown's PROMPT entry is not an option, and a NON-NATIVE dropdown
    keeps its options outside the control - both were dropped, so the model
    answered against a list it could never select."""
    from player.web import actions as web_actions
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    # A select's prompt entry (the HTML convention: an empty value attr) is
    # never offered as an answer.
    assert "String(_o.value || '') === ''" in enum_js
    assert "_o.disabled" in enum_js
    # A combobox's options live in its datalist and/or the listbox it declares
    # (aria-controls / aria-owns) or sits inside.
    assert "function comboOptions(" in enum_js
    assert "getAttribute('list')" in enum_js
    assert "aria-controls" in enum_js and "aria-owns" in enum_js
    # A native datalist input (and an editable inside a role=combobox wrapper)
    # is LIST-BACKED: classified as plain text, its datalist was never read and
    # the model typed a value the page refused to submit.
    assert "el.getAttribute('list')) return 'combo'" in enum_js
    assert "el.closest('[role=\"combobox\"]')) return 'combo'" in enum_js
    set_js = web_actions.JS_SET_FIELD
    # The combo write CLICKS the matching option - an untrusted synthetic Enter
    # is ignored by most widgets, so a typed-only value never committed.
    assert '[role="option"]' in set_js
    assert "hit.click()" in set_js
    assert "ArrowDown" in set_js            # keys stay as the fallback
    # The page-wide listbox fallback only runs for a DECLARED combobox, so a
    # datalist input never clicks an unrelated open listbox.
    assert "var declared = " in set_js
    state_js = web_actions.JS_FIELD_STATE
    # A select on its prompt option reads back as NOTHING selected...
    assert "String(so.value || '') !== ''" in state_js
    # ...and a combobox reports its ACTIVE option when the input is empty.
    assert "aria-activedescendant" in state_js


def test_foreground_topmost_crosses_shadow_boundaries():
    """A shadow HOST whose shadow content is hit at its centre is ON TOP.

    ``Node.contains`` does not cross shadow boundaries, so the host was judged
    "covered" by its OWN shadow content and the whole shadow subtree - every
    field of a LinkedIn-style form (``interop-outlet``) - was silently skipped:
    the form node ran, reported ``fields_total: 0``, and looked as if the graph
    had skipped it.
    """
    import json
    import shutil
    import subprocess
    from pathlib import Path

    from player.web import actions as web_actions

    fg = web_actions.JS_FOREGROUND
    assert "n.getRootNode && n.getRootNode().host" in fg  # shadow-crossing walk
    assert "top.contains(el)" not in fg                  # the boundary-blind test

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    harness = (Path(__file__).resolve().parents[5] / "tests" / "js"
               / "foreground_topmost_harness.js")
    proc = subprocess.run([node, str(harness)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["hostOk"] is True       # the shadow host is not "covered"
    assert data["fieldOk"] is True      # a shadow field hit on itself is on top
    assert data["coveredOk"] is True    # a real overlay still rejects the field
    assert data["offscreenOk"] is True  # off-screen is not "covered"
    assert data["zeroBoxOk"] is True    # a zero-height host IS enterable
    assert data["noneBoxOk"] is True    # display:none host is not
    assert data["ariaOk"] is True       # aria-hidden host is not


def test_combo_options_reader_opens_the_popup_and_only_reads_a_combo():
    """A typeahead renders its options only once OPENED, so the enumerator saw
    none ('Options: (free text)') and the model typed free text the page
    refused.  The reader must open the control and only ever read a LIST-BACKED
    control - a generic ladder candidate must not read a plain input."""
    from player.web import actions as web_actions
    js = web_actions.JS_COMBO_OPTIONS
    assert "function isCombo(" in js          # never a plain input
    assert 'role === \'combobox\'' in js
    assert "getAttribute('list')" in js     # datalist-backed
    assert "fromDatalist" in js and "fromPopup" in js
    assert "ArrowDown" in js                # opens a typeahead's popup
    assert "JSON.stringify" in js


def test_combo_free_text_is_not_treated_as_already_filled():
    """A combobox can only HOLD one of its own options.  Free text a previous
    pass typed is NOT an answer: reading it as 'already filled' skipped the
    field on every later pass and made it un-salvageable (observed: a city
    combo left holding 'riverside' after the page never accepted it)."""
    h = _Harness()
    field = {"id": "c", "label": "Location (city)*", "kind": "combo",
             "options": ["Riverside, Illinois, United States",
                         "Springfield, Illinois, United States"]}
    h._ff_field_state = lambda f, sf, driver=None: {
        "found": True, "value": "riverside"}
    assert h._ff_current_value(field, None) == ""     # refilled, not skipped
    # ...but a value that IS one of the options stays 'already filled'.
    h._ff_field_state = lambda f, sf, driver=None: {
        "found": True, "value": "Springfield, Illinois, United States"}
    assert h._ff_current_value(field, None) == (
        "Springfield, Illinois, United States")
    # A combo with NO known options is unchanged (no false re-filling).
    h._ff_field_state = lambda f, sf, driver=None: {
        "found": True, "value": "riverside"}
    assert h._ff_current_value(
        {"id": "c", "label": "City", "kind": "combo"}, None) == "riverside"


def test_closed_combo_popup_options_are_read_and_constrain_the_answer():
    """The node must READ a closed combo's option list (opening the popup) and
    anchor the model's answer to it, instead of accepting free text the page
    does not offer."""
    h = _Harness()
    seen = {}

    def _combo_options(field, sf, driver=None):
        seen["label"] = field.get("label")
        return ["Springfield, Illinois, United States", "Bristol, England"]

    h._ff_combo_options = _combo_options
    _wire(h, fields=[{"id": "c", "label": "Location (city)*",
                      "kind": "combo"}],
          evidence="ev", llm_value="Springfield, Illinois, United States",
          reads=["Springfield, Illinois, United States"])
    h._execute_form_filling_node(_node(), None)
    assert seen["label"] == "Location (city)*"
    assert h.writes == [("c", "Springfield, Illinois, United States")]
