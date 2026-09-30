"""Handle node web mode: request text -> page candidates -> Laya pick -> click.

Covers the DOM-grounded click pipeline that lets a Handle node stop depending
on screenshot grounding (LocateAnything):
1. The page scan's candidate list is ranked against the request, the Laya
   engine picks among the top candidates, and the pick's MARKER
   (``data-wvp-handle``) is the locator the web click ladder receives.
2. A connected Input node's value (passthrough) is the request text.
3. Laya off/down degrades to the best lexical match instead of failing.
4. No browser, or nothing on the page matching the request, fails the node
   instead of clicking a random element.
"""

import json

from player.multi_sequence.worflow_interpreter_modules.executor_modules.handle_ops import HandleMixin
from player.multi_sequence.llm_executor_resources import inputs as inputs_mod

import AI.laya_client as laya_client
from player.web import exec_overlay as web_exec_overlay
from player.web import handlers as web_handlers


class _FakePortStore:
    def __init__(self):
        self.data = {}

    def get_output(self, chain_id, node_id, port):
        return self.data.get((chain_id, node_id, port))

    def set_output(self, chain_id, node_id, port, value):
        self.data[(chain_id, node_id, port)] = value


class _FakeLLMExecutor:
    def __init__(self):
        self.vars = {}

    def get_variable(self, name, default=None):
        return self.vars.get(name, default)

    def set_variable(self, name, value):
        self.vars[name] = value


class _FakeDriver:
    """Serves the candidate scan; records every script it was asked to run."""

    def __init__(self, candidates):
        self.candidates = candidates
        self.scripts = []

    def execute_script(self, script, *args):
        self.scripts.append((script, args))
        if 'removeAttribute' in script:
            return len(self.candidates)  # the marker cleanup
        return self.candidates

    def scan_args(self):
        return [args for script, args in self.scripts if args]


class _FakeClickHandler:
    """Records the click events the web ladder would have dispatched."""

    def __init__(self, ok=True):
        self.ok = ok
        self.events = []

    def execute(self, driver, event, config):
        self.events.append(event)
        return {
            "ok": self.ok,
            "method": "native" if self.ok else None,
            "error": None if self.ok else "locator did not resolve natively",
            "took": 0.0,
        }


class _Executor(HandleMixin):
    """Binds the real mixin onto a stub with fake stores."""

    def __init__(self, graph=None):
        self.port_store = _FakePortStore()
        self.chain_id = "chain"
        self.llm_executor = _FakeLLMExecutor()
        self.workflow_graph = graph or {}


def _web_handle_node(goal="", inputs=None):
    return {
        "type": "handle",
        "id": "HDL",
        "data": {
            "node_id": "HDL",
            "action_type": "click",
            "goal_description": goal,
            "target_description": "",
            "agent_adaptive": False,
            "web_mode": True,
        },
        "inputs": inputs or [],
        "connections": {"output": []},
    }


_CANDIDATES = [
    {"i": 0, "tag": "a", "label": "Home"},
    {"i": 1, "tag": "button", "label": "Sign in"},
    {"i": 2, "tag": "a", "label": "Settings"},
]


def _install(monkeypatch, candidates=_CANDIDATES, laya_pick=None, click_ok=True):
    """Wire the fakes the web pipeline talks to; returns (driver, clicks)."""
    driver = _FakeDriver(list(candidates))
    clicks = _FakeClickHandler(ok=click_ok)
    monkeypatch.setattr(inputs_mod, "_web_driver", lambda: driver)
    monkeypatch.setattr(laya_client, "choice",
                        lambda state, instructions, criteria: laya_pick)
    monkeypatch.setitem(web_handlers.HANDLERS, "click", clicks)
    monkeypatch.setattr(web_exec_overlay, "enable", lambda d: None)
    monkeypatch.setattr(web_exec_overlay, "mark", lambda d, e, act: None)
    return driver, clicks


# ---------------------------------------------------------------------------
# The Laya pick is clicked via its marker
# ---------------------------------------------------------------------------


def test_web_mode_clicks_the_laya_pick(monkeypatch):
    driver, clicks = _install(monkeypatch, laya_pick="Sign in")
    ex = _Executor()
    out = ex._execute_handle_web("HDL", "the sign in button", lambda: False)

    payload = json.loads(out)
    assert payload["mode"] == "web"
    assert payload["element"] == "Sign in"
    assert payload["picked_by"] == "laya"
    assert payload["method"] == "native"

    # The click went to the PICK's marker, not to any other candidate.
    assert len(clicks.events) == 1
    assert clicks.events[0].locator.css == '[data-wvp-handle="1"]'
    # The scanner got the candidate cap, and the markers were cleaned up.
    assert driver.scan_args() == [(80,)]
    assert any('removeAttribute' in script for script, _a in driver.scripts)


def test_web_mode_uses_connected_input_value_as_request(monkeypatch):
    driver, clicks = _install(monkeypatch, laya_pick=None)  # Laya engine off
    graph = {"IN": {"id": "IN", "type": "input"}}
    ex = _Executor(graph)
    ex.port_store.set_output("chain", "IN", "data", "open the settings page")
    node = _web_handle_node(
        goal="",
        inputs=[{"from_node": "IN", "output_type": "data",
                 "input_port": "data"}],
    )

    assert ex._execute_handle_node(node, lambda: False) == "__done__"

    payload = json.loads(ex.llm_executor.get_variable("node_HDL_data"))
    assert payload["goal"] == "open the settings page"
    assert payload["element"] == "Settings"   # best lexical match
    assert payload["picked_by"] == "lexical"


# ---------------------------------------------------------------------------
# Failure paths never click blindly
# ---------------------------------------------------------------------------


def test_web_mode_fails_without_a_browser(monkeypatch):
    _driver, clicks = _install(monkeypatch, laya_pick="Sign in")
    monkeypatch.setattr(inputs_mod, "_web_driver", lambda: None)
    ex = _Executor()

    assert ex._execute_handle_web("HDL", "sign in", lambda: False) == ""
    assert clicks.events == []


def test_web_mode_fails_when_nothing_matches(monkeypatch):
    driver, clicks = _install(monkeypatch, laya_pick="Sign in")
    ex = _Executor()

    # No candidate shares a word with the request -> no click at all.
    assert ex._execute_handle_web("HDL", "download invoice", lambda: False) == ""
    assert clicks.events == []
    # The scan's markers are still cleaned up on the failure path.
    assert any('removeAttribute' in script for script, _a in driver.scripts)


def test_web_mode_fails_when_the_click_ladder_fails(monkeypatch):
    _driver, clicks = _install(monkeypatch, laya_pick="Sign in", click_ok=False)
    ex = _Executor()

    assert ex._execute_handle_web("HDL", "sign in", lambda: False) == ""
    assert len(clicks.events) == 1  # attempted, reported as failed


def test_web_mode_needs_a_request_text(monkeypatch):
    _driver, clicks = _install(monkeypatch, laya_pick="Sign in")
    ex = _Executor()

    assert ex._execute_handle_web("HDL", "  ", lambda: False) == ""
    assert clicks.events == []


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_match_score_prefers_the_contained_word():
    score = HandleMixin._web_match_score
    assert score("login button", "Log in") > score("login button", "Home")
    assert score("open settings", "Settings") > score("open settings", "Sign in")
    assert score("sign in", "Home") == 0
    # A 2-letter fragment must not claim a match ('in' inside 'invoice').
    assert score("download invoice", "Sign in") == 0
