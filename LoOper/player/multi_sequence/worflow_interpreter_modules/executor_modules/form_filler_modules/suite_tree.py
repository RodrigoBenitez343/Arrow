"""Laya-first element resolution: the page tree, its walk and its fallbacks."""

import json

from ._test_support import _Harness


class _FakeDriver:
    """A browser stand-in that answers with a canned tree payload."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def execute_script(self, script, *args):
        self.calls.append((script, args))
        return self.payload


def _flat(*rows):
    """rows ``(i, parent, control, label)`` -> the FLAT shape the JS emits."""
    return [
        {"i": i, "parent": p, "depth": 0, "control": 1 if control else 0,
         "tag": "input" if control else "div", "role": "", "label": label,
         "value": ""}
        for i, p, control, label in rows
    ]


def _payload(nodes, scoped=True, root="Easy Apply"):
    return json.dumps({"nodes": nodes, "scoped": scoped, "root": root})


def _split_tree():
    """Easy Apply -> {Contact info -> Email*, Resume -> Upload}: two branches,
    so the first level the walk meets is a real CHOICE."""
    return _payload(_flat(
        (0, -1, False, "Easy Apply"),
        (1, 0, False, "Contact info"),
        (2, 1, True, "Email address*"),
        (3, 0, False, "Resume"),
        (4, 3, True, "Upload resume"),
    ))


def _stub_laya(monkeypatch, pick=None, status="ready", calls=None):
    """Patch the embedded engine: its status plus ONE typed choice per level."""
    from AI import laya_client

    def _choice(state, instructions, criteria):
        if calls is not None:
            calls.append({"state": state, "criteria": list(criteria)})
        if callable(pick):
            return pick(state, instructions, list(criteria))
        return pick

    monkeypatch.setattr(laya_client, "status", lambda: status)
    monkeypatch.setattr(laya_client, "choice", _choice)


def test_the_tree_js_marks_nodes_and_pierces_shadow_iframes_and_popups():
    """The tree is the resolution substrate, so its CONTRACT matters: every node
    carries the marker that becomes the field's identity, stale markers are
    dropped, and the walk crosses shadow and same-origin iframe boundaries."""
    from player.web import actions as web_actions
    js = web_actions.JS_ENUMERATE_FORM_TREE
    assert web_actions.FF_TREE_MARKER_ATTR == "data-wvp-ff"
    assert "setAttribute(MARK, String(node.i))" in js
    # A leftover marker IS a node ordinal: reusing it would rewire the pick.
    assert "removeAttribute(MARK)" in js
    assert "rn.host" in js              # shadow boundary
    assert "frameElement" in js         # same-origin iframe / popup boundary
    # A FLAT parent-indexed list: the Python side rebuilds the tree from it.
    assert "i: out.length" in js and "parent: parent" in js
    # A picked container is authoritative - never the whole page behind it.
    assert "picked container not resolved" in js
    assert "scoped ? root : null" in js
    # Unscoped navigation climbs to the control's OWN <form>: the document
    # chain's first real choice would otherwise be the site's navigation.
    assert "chain.slice(q)" in js
    # ...and it reads ONLY the layer actually ON SCREEN (an open modal), never
    # the base page behind it - the SAME layers the enumerator and the
    # write/read scope use.  Verified live: without this the tree also held the
    # page's global 'Search' box beside the LinkedIn Easy Apply dialog.
    assert "__wvpTopLayers()" in js
    assert "__wvpOwns(candidates" in js


def test_the_scan_rebuilds_the_tree_and_reports_what_it_rooted_at():
    h = _Harness()
    h._ff_web_driver = _FakeDriver(_split_tree())
    roots = h._ff_tree_scan(h._ff_cfg({"instruction": "x"}), None)
    assert [r["label"] for r in roots] == ["Easy Apply"]
    branches = roots[0]["children"]
    assert [b["label"] for b in branches] == ["Contact info", "Resume"]
    assert [c["label"] for c in branches[1]["children"]] == ["Upload resume"]
    assert branches[0]["children"][0]["control"] == 1


def test_a_self_parented_or_orphaned_node_never_builds_a_cycle():
    """A malformed parent index must degrade to a root, never to a cycle: the
    walk would otherwise spin forever on a page whose markup changed mid-scan."""
    h = _Harness()
    h._ff_web_driver = _FakeDriver(_payload([
        {"i": 0, "parent": 0, "control": 0, "tag": "div", "label": "self"},
        {"i": 1, "parent": 99, "control": 1, "tag": "input", "label": "orphan"},
    ]))
    roots = h._ff_tree_scan(h._ff_cfg({}), None)
    assert sorted(r["i"] for r in roots) == [0, 1]


def test_the_marker_is_the_first_rung_and_the_ladder_stays_behind_it():
    """The resolver rewrites nothing: it puts the pick FIRST and leaves the
    recorded ladder intact, so an off/down engine or a re-rendered marker falls
    straight back to today's behaviour."""
    h = _Harness()
    field = {"id": "f", "label": "First name*", "kind": "text"}
    plain = h._ff_field_selectors(field)
    field["_ff_tree_sel"] = '[data-wvp-ff="7"]'
    sels = h._ff_field_selectors(field)
    assert sels[0] == '[data-wvp-ff="7"]'
    assert list(sels[1:]) == list(plain)


def test_resolve_aims_the_field_at_the_control_laya_picked(monkeypatch):
    h = _Harness()
    h._ff_web_driver = _FakeDriver(_split_tree())
    calls = []
    _stub_laya(monkeypatch, pick=lambda *a: "Contact info", calls=calls)
    field = {"id": "f", "label": "Email address*", "kind": "text"}

    assert h._ff_resolve_target(field, h._ff_cfg({}), None) == "laya-tree"
    # The marker is the LEAF the branch held, not the branch that was chosen.
    assert field["_ff_tree_sel"] == '[data-wvp-ff="2"]'
    assert h._ff_field_selectors(field)[0] == '[data-wvp-ff="2"]'
    # The question IS the state, and the branches are what gets offered.
    assert "Email address*" in calls[0]["state"]
    assert calls[0]["criteria"] == ["Contact info", "Resume"]


def test_a_refusal_keeps_the_ladder_and_leaves_no_marker(monkeypatch):
    """A refusal (the engine answered but no option won) must NOT become a
    guess: the field keeps its ladder, which is exactly today's behaviour."""
    h = _Harness()
    h._ff_web_driver = _FakeDriver(_split_tree())
    _stub_laya(monkeypatch, pick=lambda *a: None)
    field = {"id": "f", "label": "Email address*", "kind": "text"}

    assert h._ff_resolve_target(field, h._ff_cfg({}), None) == "ladder"
    assert "_ff_tree_sel" not in field
    assert h._ff_field_selectors(field) == h._ff_selectors(field)


def test_no_live_session_never_opens_a_browser(monkeypatch):
    """A run that is not driving a page (a desktop pass, a unit harness) must
    not spawn a browser just to resolve - it keeps the ladder."""
    h = _Harness()                      # no _ff_web_driver at all
    _stub_laya(monkeypatch, pick=lambda *a: "anything")
    field = {"id": "f", "label": "Email address*", "kind": "text"}

    assert h._ff_resolve_target(field, h._ff_cfg({}), None) == "ladder"
    assert "_ff_tree_sel" not in field


def test_the_kill_switch_reproduces_todays_behaviour(monkeypatch):
    h = _Harness()
    h._ff_web_driver = _FakeDriver(_payload(_flat((0, -1, True, "Email*"))))
    _stub_laya(monkeypatch, pick=lambda *a: "Email*", status="off")
    field = {"id": "f", "label": "Email*", "kind": "text"}

    assert h._ff_resolve_target(field, h._ff_cfg({}), None) == "ladder"
    assert "_ff_tree_sel" not in field


def test_a_scope_that_did_not_resolve_resolves_nothing():
    """The picked container IS the form: when it cannot be resolved the tree is
    empty and nothing is aimed - never a whole-page fallback that would act on
    the site's global bar (the 'Search' box behind the modal)."""
    h = _Harness()
    h._ff_web_driver = _FakeDriver(json.dumps(
        {"nodes": [], "scoped": False, "root": "",
         "reason": "picked container not resolved"}))
    field = {"id": "f", "label": "Email*", "kind": "text"}

    assert h._ff_resolve_target(field, h._ff_cfg({"web_scope": "{}"}), None) \
        == "ladder"
    assert "_ff_tree_sel" not in field


def test_a_resolved_field_is_re_decided_only_after_the_dom_moves(monkeypatch):
    """Markers are node ORDINALS: a write re-renders the form, so the tree is
    dropped then - and reused before, instead of paying a forward per read."""
    h = _Harness()
    h._ff_web_driver = _FakeDriver(_split_tree())
    calls = []
    _stub_laya(monkeypatch, pick=lambda *a: "Contact info", calls=calls)
    field = {"id": "f", "label": "Email address*", "kind": "text"}
    cfg = h._ff_cfg({})

    assert h._ff_resolve_target(field, cfg, None) == "laya-tree"
    assert h._ff_resolve_target(field, cfg, None) == "laya-tree"
    assert len(calls) == 1                     # the pick was reused
    h._ff_forget_tree()                        # a write re-rendered the form
    assert h._ff_resolve_target(field, cfg, None) == "laya-tree"
    assert len(calls) == 2


def test_a_single_child_chain_costs_no_decision(monkeypatch):
    """A document tree reaches its form through a dozen single-child levels
    (html > body > div > ...).  Counting those as decisions spent the budget
    before the form was ever reached, and every field fell back to the ladder."""
    h = _Harness()
    rows = [(0, -1, False, "html")]
    for k in range(1, 20):
        rows.append((k, k - 1, k == 19, "Email address*"))
    h._ff_web_driver = _FakeDriver(_payload(_flat(*rows)))
    calls = []
    _stub_laya(monkeypatch, pick=lambda *a: "Email address*", calls=calls)
    field = {"id": "f", "label": "Email address*", "kind": "text"}

    assert h._ff_resolve_target(field, h._ff_cfg({}), None) == "laya-tree"
    assert field["_ff_tree_sel"] == '[data-wvp-ff="19"]'
    assert calls == []                      # never forked, so never asked


def test_only_the_engine_slots_are_offered_and_the_keys_stay_unique(monkeypatch):
    """The engine's option map is keyed by LABEL: two nodes with the same page
    label would collapse into ONE option (the pick would be untraceable), and a
    level wider than the engine's slots is cut after ranking."""
    h = _Harness()
    rows = [(0, -1, False, "Form")]
    rows += [(1 + k, 0, False, "Contact info") for k in range(20)]
    h._ff_web_driver = _FakeDriver(_payload(_flat(*rows)))
    seen = {}

    def _pick(state, instructions, criteria):
        seen["criteria"] = list(criteria)
        return criteria[0]

    _stub_laya(monkeypatch, pick=_pick)
    h._ff_tree_scan(h._ff_cfg({}), None)
    level = h._ff_tree_nodes()[0]["children"]

    assert h._ff_tree_choose("Email address*", level) is not None
    assert len(seen["criteria"]) == h._FF_TREE_OPTIONS
    assert len(set(seen["criteria"])) == len(seen["criteria"])
    assert "Contact info (2)" in seen["criteria"]


def test_a_pick_that_shares_no_term_with_the_field_keeps_the_ladder(monkeypatch):
    """The tree spans the WHOLE page when unscoped, so a field the ladder cannot
    resolve could be "matched" to page chrome.  Live: the required 'Linkedin
    Profile Url' was resolved to the site's global 'Search' box behind the modal
    and the marker then led the ladder to another question's control (the field
    read back the EMAIL and was skipped as 'already filled').  A pick whose own
    label shares NO term with the field is not this field: keep the ladder."""
    h = _Harness()
    h._ff_web_driver = _FakeDriver(_payload(_flat((0, -1, True, "Search"))))
    _stub_laya(monkeypatch, pick=lambda *a: "Search")
    field = {"id": "f", "label": "Linkedin Profile Url*", "kind": "text"}

    assert h._ff_resolve_target(field, h._ff_cfg({}), None) == "ladder"
    assert "_ff_tree_sel" not in field


def test_a_branch_label_names_the_control_the_question_means(monkeypatch):
    """A label-less branch carrying more controls than the label can hold must
    STILL name the one this field means: live, a LinkedIn form branch listed its
    first four controls and cut 'Location (city)*' off, so Laya was offered no
    option naming the field and fell to the page's 'Search' box.  The controls
    whose own label matches the question are listed FIRST."""
    h = _Harness()
    rows = [
        (0, -1, False, "Form"),
        (1, 0, False, ""),               # label-less: described by its controls
        (2, 1, True, "First name*"),
        (3, 1, True, "Last name*"),
        (4, 1, True, "Email address*"),
        (5, 1, True, "Phone country code*"),
        (6, 1, True, "Location (city)*"),  # the 5th control: the old label cut it
        (7, 0, True, "Search"),            # page chrome in the SAME level
    ]
    h._ff_web_driver = _FakeDriver(_payload(_flat(*rows)))
    calls = []

    def _pick(state, instructions, criteria):
        calls.append(list(criteria))
        return next((c for c in criteria if "Location" in c), criteria[0])

    _stub_laya(monkeypatch, pick=_pick)
    field = {"id": "f", "label": "Location (city)*", "kind": "combo"}

    assert h._ff_resolve_target(field, h._ff_cfg({}), None) == "laya-tree"
    # The branch NAMES the field it holds (not just its first four controls).
    assert any("Location (city)*" in c for c in calls[0])
    # ...so the pick lands on the field, not the page's 'Search' box.
    assert field["_ff_tree_sel"] == '[data-wvp-ff="6"]'
