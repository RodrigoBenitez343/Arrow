"""Integration tests for the Web Sequence node (WebSequenceNode).

Covers the absorbed web subsystem (LoOper/player/web), the workflow builder
mapping (web_sequences -> 'web_sequence' graph nodes), the sequence executor
web dispatch (including the defensive mode=web routing in the desktop path),
node config serialization helpers, and integration purity (no external
webversionpw import anywhere in LoOper).
"""
import json
import logging
import os
import re

import pytest

from player.web import events as web_events
from player.web.engine import replay_config_from_item
from player.multi_sequence.worflow_interpreter_modules.builder import WorkflowGraphBuilder
from player.multi_sequence.sequence_executor import SequenceExecutor
from player.json_cache import _is_sequence_shape
from NGUI.nodes_resources.web_sequence_node import WebSequenceNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_web_session(path, actions=None, mode="web"):
    """Write a web-session-shaped JSON file and return its path."""
    actions = actions or [
        {"type": "navigate", "ts": 1.0, "locator": None, "frame_path": [],
         "cross_origin_frame": False, "url": "https://example.com", "context": {}},
        {"type": "click", "ts": 2.0, "locator": {"tag": "button", "id": "go",
                                               "css": "button#go", "data_attrs": {}},
         "frame_path": [], "cross_origin_frame": False, "button": "0", "context": {}},
    ]
    payload = {
        "schema_version": 1,
        "mode": mode,
        "metadata": {"created_at": "2026-08-20 00:00:00", "total_actions": len(actions),
                     "duration_sec": 1.0, "mode": mode, "start_url": None},
        "actions": actions,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# 1. Session contract & shape
# ---------------------------------------------------------------------------


def test_web_session_shape_and_contract(tmp_path):
    """A web session JSON passes the desktop shape check and round-trips."""
    session = _write_web_session(str(tmp_path / "web" / "login.json"))
    assert _is_sequence_shape(session)  # has an actions list
    assert web_events.is_web_session(session)

    data = web_events.load_session(session)
    assert data["metadata"]["mode"] == "web"
    assert len(data["actions"]) == 2
    first = data["actions"][0]
    assert first.type == "navigate" and first.url == "https://example.com"
    click = data["actions"][1]
    assert click.locator is not None
    assert click.locator.chain()[0] == {"kind": "id", "value": "go"}

    # Desktop-shaped file is NOT a web session
    desktop = tmp_path / "desktop.json"
    desktop.write_text(json.dumps({"metadata": {"mode": "desktop_only"}, "actions": []}),
                       encoding="utf-8")
    assert not web_events.is_web_session(str(desktop))
    assert _is_sequence_shape(str(desktop))  # still a valid desktop sequence


def test_save_session_roundtrip(tmp_path):
    """save_session -> load_session preserves the typed action contract."""
    events = [
        web_events.Event(type="type", ts=100.0, value="hello",
                         locator=web_events.Locator(tag="input", css="input#q"),
                         frame_path=[], sensitive=False),
        web_events.Event(type="scroll", ts=150.0, scroll={"total_delta": 240, "steps": 2}),
    ]
    out = tmp_path / "saved.json"
    web_events.save_session(events, out, start_url=None, duration_sec=5.0)
    data = web_events.load_session(str(out))
    assert data["schema_version"] == 1
    assert data["actions"][0].value == "hello"
    assert data["actions"][1].scroll["total_delta"] == 240


# ---------------------------------------------------------------------------
# 2. Replay config mapping
# ---------------------------------------------------------------------------


def test_replay_config_mapping():
    """Node item dict -> ReplayConfig conversions; no URL fields present."""
    cfg = replay_config_from_item({
        "headless": "true",
        "speed": "2.5",
        "native_actions": "yes",
    })
    assert cfg.headless is True
    assert cfg.speed == 2.5
    assert cfg.native_actions is True
    assert not hasattr(cfg, "start_url") and not hasattr(cfg, "url_override")

    # Safe fallbacks for malformed values
    cfg2 = replay_config_from_item({"headless": "banana", "speed": "abc",
                                    "native_actions": None})
    assert cfg2.headless is False
    assert cfg2.speed == 1.0
    assert cfg2.native_actions is False


# ---------------------------------------------------------------------------
# 3. Workflow builder mapping
# ---------------------------------------------------------------------------


def test_builder_maps_web_sequences():
    """web_sequences[] config entries become 'web_sequence' graph nodes."""
    ws = {
        "node_id": "ws_a",
        "name": "login.json",
        "session_file": "login.json",
        "headless": False,
        "speed": 1.0,
        "native_actions": False,
        "loop_count": 1,
        "extra_delay": 1.0,
        "outputs": {"output": [{"node_id": "llm_1", "input_port": "input"}]},
    }
    llm = {"node_id": "llm_1", "name": "LLM", "outputs": {"output": []}}
    builder = WorkflowGraphBuilder(web_sequences=[ws], llm_nodes=[llm])
    graph = builder.build_workflow_graph()

    node = graph["ws_a"]
    assert node["type"] == "web_sequence"
    assert node["data"]["session_file"] == "login.json"
    assert node["connections"]["output"][0]["node_id"] == "llm_1"

    # Downstream inputs are derived from the web node's output connection
    llm_inputs = graph["llm_1"].get("inputs", [])
    assert any(i["from_node"] == "ws_a" for i in llm_inputs)


def test_builder_web_sequence_connections_format():
    """Web sequences support the legacy 'connections' list shape too."""
    ws = {
        "node_id": "ws_b",
        "name": "cart.json",
        "session_file": "cart.json",
        "connections": [{"output_port": "output", "target_node_id": "llm_9",
                         "input_port": "input"}],
    }
    builder = WorkflowGraphBuilder(web_sequences=[ws], llm_nodes=[
        {"node_id": "llm_9", "name": "LLM", "outputs": {"output": []}}])
    graph = builder.build_workflow_graph()
    assert graph["ws_b"]["connections"]["output"][0]["node_id"] == "llm_9"


def test_builder_web_sequence_ctx_out_connections_are_data_only():
    """ctx_out connections are bucketed separately from execution 'output' and
    the target's input records output_type='ctx_out' (data wiring, not flow)."""
    ws = {
        "node_id": "ws_ctx",
        "name": "search.json",
        "session_file": "search.json",
        "connections": [
            {"output_port": "output", "target_node_id": "llm_9", "input_port": "input"},
            {"output_port": "ctx_out", "target_node_id": "llm_10", "input_port": "context"},
        ],
    }
    builder = WorkflowGraphBuilder(web_sequences=[ws], llm_nodes=[
        {"node_id": "llm_9", "name": "LLM", "outputs": {"output": []}},
        {"node_id": "llm_10", "name": "LLM", "outputs": {"output": []}}])
    graph = builder.build_workflow_graph()

    conns = graph["ws_ctx"]["connections"]
    assert [c["node_id"] for c in conns["output"]] == ["llm_9"]
    assert [c["node_id"] for c in conns["ctx_out"]] == ["llm_10"]

    # ctx_out target gets a data input (output_type='ctx_out'), never an
    # execution input — dependency gating only follows output/true/false/error.
    llm10_inputs = graph["llm_10"].get("inputs", [])
    assert any(i["from_node"] == "ws_ctx" and i["output_type"] == "ctx_out"
               for i in llm10_inputs)


# ---------------------------------------------------------------------------
# 4. Node config helpers (Qt-free)
# ---------------------------------------------------------------------------


def test_web_sequence_node_bool_float_helpers():
    """The node's string-to-bool/float helpers convert node property values."""
    assert WebSequenceNode._as_bool("true") is True
    assert WebSequenceNode._as_bool("1") is True
    assert WebSequenceNode._as_bool("no") is False
    assert WebSequenceNode._as_bool(True) is True
    assert WebSequenceNode._as_bool(None) is False
    assert WebSequenceNode._as_float("2.5") == 2.5
    assert WebSequenceNode._as_float("abc") is None


# ---------------------------------------------------------------------------
# 5. Sequence executor web dispatch
# ---------------------------------------------------------------------------


def test_execute_web_sequence_resolves_from_web_sequences_dir(tmp_path, monkeypatch):
    """execute_web_sequence resolves session_file from web_sequences/ dirs."""
    session = _write_web_session(str(tmp_path / "web_sequences" / "login.json"))
    executor = SequenceExecutor(chain_file_dir=str(tmp_path))
    called = {}

    def fake_run(item, session_path, stop_flag, loops, extra_delay):
        called["path"] = session_path
        called["loops"] = loops
        return True

    monkeypatch.setattr(executor, "_run_web_replay", fake_run)
    assert executor.execute_web_sequence(
        {"session_file": "login.json", "loop_count": 2, "extra_delay": 0.5}, None) is True
    assert called["path"] == session
    assert called["loops"] == 2


def test_execute_web_sequence_missing_file_fails(tmp_path):
    """Unresolved session file -> False with no engine invocation."""
    executor = SequenceExecutor(chain_file_dir=str(tmp_path))
    assert executor.execute_web_sequence(
        {"session_file": "zz_missing_web_session_xyz.json"}, None) is False


def test_execute_web_sequence_rejects_desktop_file(tmp_path, monkeypatch):
    """A desktop sequence file (no mode=web) is rejected for web replay."""
    web_dir = tmp_path / "web_sequences"
    web_dir.mkdir(parents=True, exist_ok=True)
    desktop = web_dir / "desktop.json"
    desktop.write_text(json.dumps({"actions": [{"type": "wait", "duration": 0.1}]}),
                       encoding="utf-8")
    executor = SequenceExecutor(chain_file_dir=str(tmp_path))
    called = []

    def fake_run(*args, **kwargs):
        called.append(args)
        return True

    monkeypatch.setattr(executor, "_run_web_replay", fake_run)
    assert executor.execute_web_sequence(
        {"session_file": "desktop.json"}, None) is False
    assert not called


def test_desktop_execute_sequence_routes_web_marker(tmp_path, monkeypatch):
    """A mode=web session on a desktop sequence node routes to the web engine."""
    session = _write_web_session(str(tmp_path / "sequences" / "login.json"))
    executor = SequenceExecutor(chain_file_dir=str(tmp_path))
    called = {}

    def fake_run(item, session_path, stop_flag, loops, extra_delay):
        called["path"] = session_path
        return True

    monkeypatch.setattr(executor, "_run_web_replay", fake_run)
    assert executor.execute_sequence(
        {"sequence_file": "login.json", "loop_count": 1}, None) is True
    assert called["path"] == session


def test_web_sequence_node_failure_returns_none():
    """execute_web_sequence_node returns None when the session fails."""
    executor = SequenceExecutor()
    node = {
        "data": {"session_file": "zz_missing_web_session_xyz.json",
                 "name": "missing.json"},
        "connections": {"output": [{"node_id": "n2"}]},
    }
    assert executor.execute_web_sequence_node(node, None) is None


# ---------------------------------------------------------------------------
# 6. Config manager serialization (Qt-free path)
# ---------------------------------------------------------------------------


def test_config_manager_saves_web_sequence_node(monkeypatch):
    """_save_web_sequence_node emits a web_sequences entry with web props."""
    from NGUI.graph_elements.config_manager import ConfigManager

    class FakeParent:
        pass

    class FakeNode:
        def __init__(self):
            self.id = "ws_1"
            self._props = {
                "session_file": "login.json",
                "headless": "true",
                "speed": "2.0",
                "native_actions": "false",
                "loop_count": "3",
                "extra_delay": "0.5",
            }

        def get_property(self, key):
            return self._props.get(key, "")

        def name(self):
            return "login.json"

        def pos(self):
            return [10, 20]

    cm = ConfigManager(FakeParent())
    monkeypatch.setattr(cm, "_get_node_connections", lambda node: [{"output_port": "output"}])
    cm._save_web_sequence_node(FakeNode())

    entries = cm.chain_config["web_sequences"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["session_file"] == "login.json"
    assert entry["headless"] is True
    assert entry["speed"] == 2.0
    assert entry["native_actions"] is False
    assert entry["loop_count"] == 3
    assert entry["extra_delay"] == 0.5
    assert entry["node_id"] == "ws_1"
    assert entry["position"] == [10, 20]


def test_config_manager_loads_web_sequence_extract_items():
    """Loading a chain restores extract_items onto the node.

    Regression: the loader set every web-sequence property EXCEPT
    extract_items, so the ctx_out toggles (page_text/url/entity_exhausted)
    were silently dropped on every reload and re-saved as [].
    """
    from NGUI.graph_elements.config_manager import ConfigManager

    captured = {}

    class FakeNode:
        id = "ws_1"

        def set_property(self, key, value):
            captured[key] = value

        def set_pos(self, *args):
            pass

        def name(self):
            return "siguiente.json"

    class FakeGraphManager:
        def create_node(self, *args, **kwargs):
            return FakeNode()

    class FakeParent:
        pass

    parent = FakeParent()
    parent.graph_manager = FakeGraphManager()
    cm = ConfigManager(parent)
    cm.chain_config = {
        "web_sequences": [
            {
                "session_file": "siguiente.json",
                "name": "siguiente.json",
                "extract_items": ["entity_exhausted", "url"],
            }
        ]
    }

    cm._create_web_sequence_nodes({})
    assert captured["extract_items"] == '["entity_exhausted", "url"]'


def test_config_manager_saves_custom_web_sequence_name(monkeypatch):
    """The chain config persists the node's DISPLAY label separately from the
    session file, so a custom name chosen at re-record survives reloads."""
    from NGUI.graph_elements.config_manager import ConfigManager

    class FakeParent:
        pass

    class FakeNode:
        def __init__(self):
            self.id = "ws_custom"
            self._props = {
                "session_file": "web_session_20260901-000000.json",
                "headless": "false",
                "speed": "1.5",
                "native_actions": "true",
                "loop_count": "2",
                "extra_delay": "0.5",
                "extract_items": "[]",
            }

        def get_property(self, key):
            return self._props.get(key, "")

        def name(self):
            return "Login to Gmail"

        def pos(self):
            return [10, 20]

    cm = ConfigManager(FakeParent())
    monkeypatch.setattr(cm, "_get_node_connections", lambda node: [])
    cm._save_web_sequence_node(FakeNode())
    entry = cm.chain_config["web_sequences"][0]
    assert entry["name"] == "Login to Gmail"
    assert entry["session_file"] == "web_session_20260901-000000.json"


def test_config_manager_restores_custom_web_sequence_name():
    """Loading a chain recreates each web sequence node with its saved
    display label (custom names survive reload; legacy configs stored the
    session filename there, which is identical to the fallback)."""
    from NGUI.graph_elements.config_manager import ConfigManager

    created = []

    class FakeGraphManager:
        def create_node(self, node_type, name=None, pos=None):
            created.append((node_type, name))
            return type("N", (), {
                "id": "ws_1",
                "set_property": lambda self, k, v: None,
                "set_pos": lambda self, *a: None,
                "get_property": lambda self, k: "",
            })()

    class FakeParent:
        graph_manager = FakeGraphManager()

    cm = ConfigManager(FakeParent())
    cm.chain_config["web_sequences"] = [{
        "name": "Login to Gmail",
        "session_file": "web_session_20260901-000000.json",
        "headless": False, "speed": 1.0, "native_actions": True,
        "loop_count": 1, "extra_delay": 1.0, "position": [0, 0],
    }]
    cm._create_web_sequence_nodes({})
    assert created == [("web_sequence.WebSequenceNode", "Login to Gmail")]


def test_web_sequence_ops_detects_default_names():
    """Auto-generated labels (blank, 'web_sequence', timestamped session
    names) are flagged for the re-record rename prompt; custom names are not."""
    from NGUI.graph_elements.node_operations_modules.web_sequence import (
        WebSequenceOperationsMixin,
    )

    assert WebSequenceOperationsMixin._is_default_web_sequence_name("")
    assert WebSequenceOperationsMixin._is_default_web_sequence_name("web_sequence")
    assert WebSequenceOperationsMixin._is_default_web_sequence_name(
        "web_session_20260831-224022.json"
    )
    assert not WebSequenceOperationsMixin._is_default_web_sequence_name("Login")
    assert not WebSequenceOperationsMixin._is_default_web_sequence_name("Search hola")
    assert not WebSequenceOperationsMixin._is_default_web_sequence_name(
        "web_session_custom.json"
    )


def test_web_sequence_node_default_name_detector():
    """The node-level default-name guard (the condition behind "re-record
    keeps my custom label") flags auto-generated labels and allows custom
    ones.  Bound to a fake instance - instantiating a real NodeGraphQt node
    requires a QApplication and hard-crashes under pytest."""
    for label, expected in (
        ("", True),
        ("web_sequence", True),
        ("web_session_20260831-224022.json", True),
        ("Login to Gmail", False),
        ("Search hola", False),
        ("web_session_custom.json", False),
    ):
        fake = type("N", (), {"name": lambda self, l=label: l})()
        detector = WebSequenceNode._is_default_name.__get__(fake)
        assert detector() is expected, label


def test_config_manager_web_sequences_key_present():
    """Every chain_config initializer carries the web_sequences key."""
    from NGUI.graph_elements.config_manager import ConfigManager

    cm = ConfigManager(type("P", (), {})())
    assert "web_sequences" in cm.chain_config
    assert cm.chain_config["web_sequences"] == []


def test_restore_connections_includes_web_sequences():
    """_restore_connections restores web_sequence node edges (web->web, web->llm)."""
    from NGUI.graph_elements.config_manager import ConfigManager

    calls = []

    class FakeGraphManager:
        def connect_nodes(self, source, target, output_port, input_port):
            calls.append((source.id, target.id, output_port, input_port))

    class FakeParent:
        graph_manager = FakeGraphManager()

    cm = ConfigManager(FakeParent())
    cm.chain_config["web_sequences"] = [
        {
            "node_id": "ws_a",
            "name": "a.json",
            "session_file": "a.json",
            "connections": [
                {"output_port": "output", "target_node_id": "ws_b", "input_port": "input"},
                {"output_port": "output", "target_node_id": "llm_1", "input_port": "input"},
            ],
        },
        {"node_id": "ws_b", "name": "b.json", "session_file": "b.json", "connections": []},
    ]
    cm.chain_config["llm_nodes"] = [
        {"node_id": "llm_1", "name": "LLM", "connections": []}
    ]

    node_map = {
        "ws_a": type("N", (), {"id": "ws_a"})(),
        "ws_b": type("N", (), {"id": "ws_b"})(),
        "llm_1": type("N", (), {"id": "llm_1"})(),
    }
    cm._restore_connections(node_map)

    assert ("ws_a", "ws_b", "output", "input") in calls
    assert ("ws_a", "llm_1", "output", "input") in calls


# ---------------------------------------------------------------------------
# 6b. Recording signal & quiet logging
# ---------------------------------------------------------------------------


def test_event_describe_signal_readable():
    """Event.describe() yields a one-line human-readable action summary."""
    from player.web.events import Event, Locator

    click = Event(
        type="click", ts=1.0,
        locator=Locator(tag="button", id="go", text="  Login  \n now "),
    )
    desc = click.describe()
    assert desc.startswith("click button#go")
    assert '"Login now"' in desc

    typed = Event(type="type", ts=2.0, value="secret-pw", sensitive=True,
                  locator=Locator(tag="input", name="password"))
    assert typed.describe() == 'type "*****" into input[name=password]'

    nav = Event(type="navigate", ts=3.0, url="https://example.com")
    assert nav.describe() == "navigate to https://example.com"

    key = Event(type="key", ts=4.0, key="Enter", modifiers=["Ctrl"])
    assert key.describe() == "press Ctrl+Enter"


def test_replay_engine_does_not_auto_navigate(monkeypatch):
    """Replay is interaction-driven: the engine NEVER driver.get()s to the
    recorded URL - navigation must come from the session's own recorded
    actions, like a desktop sequence (legacy navigate actions still dispatch
    as recorded)."""
    from player.web import engine as web_engine
    from player.web.events import Event

    class FakeDriver:
        def __init__(self):
            self.current_url = "chrome://new-tab-page/"
            self.got = []

        def get(self, url):
            self.got.append(url)
            self.current_url = url

    dispatched = []

    def fake_do_click(driver, evt, config):
        dispatched.append(("click", driver.current_url))

    def fake_do_navigate(driver, evt, config):
        dispatched.append(("navigate", evt.url))

    monkeypatch.setattr(web_engine, "_DISPATCHERS",
                        {"click": fake_do_click, "navigate": fake_do_navigate})
    monkeypatch.setattr(web_engine.ReplayEngine, "_pause",
                        lambda self, *a, **k: None)

    driver = FakeDriver()
    engine = web_engine.ReplayEngine(
        driver, web_engine.ReplayConfig(native_actions=False)
    )
    stats = engine.run([
        Event(type="click", ts=1.0, url="https://www.youtube.com/"),
        Event(type="navigate", ts=2.0, url="https://www.youtube.com/feed/history"),
    ])
    # No automatic driver.get() - the click runs against the browser as-is,
    # exactly like a desktop sequence on the shared workbench browser.
    assert driver.got == []
    assert dispatched == [("click", "chrome://new-tab-page/"),
                          ("navigate", "https://www.youtube.com/feed/history")]
    assert stats["ok"] == 2


def _two_window_driver(windows, current_idx=0):
    """A fake WebDriver whose windows carry their own current_url."""
    class FakeWindow:
        def __init__(self, url):
            self.url = url

    class FakeDriver:
        def __init__(self):
            self._windows = [FakeWindow(u) for u in windows]
            self._current = current_idx

        @property
        def window_handles(self):
            return [f"w{i}" for i in range(len(self._windows))]

        @property
        def current_window_handle(self):
            return f"w{self._current}"

        @property
        def current_url(self):
            return self._windows[self._current].url

        @property
        def switch_to(self):
            class _Switcher:
                def window(sw, handle):
                    self._current = int(handle[1:])
            return _Switcher()

        def add_window(self, url):
            self._windows.append(FakeWindow(url))

        def set_url(self, idx, url):
            self._windows[idx].url = url

    return FakeDriver()


def test_chain_replay_never_moves_windows_by_recorded_url():
    """Regression: an action that HAS a recorded window ordinal must never be
    moved to another window by URL.

    The chain browser is long-lived and accumulates leftover tabs across runs,
    so the recorded url frequently matches a BACKGROUND tab - the action was
    then replayed THERE, which is why the final step of a chain clicked a button
    on the page underneath it instead of the modal it belongs to.  It works
    standalone, where only one window exists, which is what made the bug look
    like a topology problem.
    """
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "player" / "web" / "engine.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "_attach_to_recorded_window"
    ]
    assert len(calls) == 1, "expected one url-based window-attach call site"

    guarded = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        mentions_ordinal_none = any(
            isinstance(c, ast.Compare)
            and isinstance(c.left, ast.Name) and c.left.id == "ordinal"
            and any(isinstance(o, ast.Constant) and o.value is None
                    for o in c.comparators)
            for c in ast.walk(node.test)
        )
        if not mentions_ordinal_none:
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                    and sub.func.id == "_attach_to_recorded_window":
                guarded += 1
    assert guarded == 1, (
        "the url-based window attach must sit behind an `ordinal is None` guard, "
        "or a chain action gets replayed on a leftover background tab"
    )


def test_attach_to_recorded_window_moves_to_popup(monkeypatch):
    """When an action was recorded in a second window (a popup a click just
    opened), replay moves the driver to the window on the recorded host and
    stays; single-window sessions, same-host actions and url-less actions
    never switch."""
    from player.web import engine as web_engine
    from player.web.events import Event

    d = _two_window_driver(
        ["https://www.linkedin.com/", "about:blank"], current_idx=0)
    evt = Event(type="type", ts=1.0,
                url="https://accounts.google.com/v3/signin/identifier")
    # Popup still loading (about:blank): poll, find nothing, restore opener.
    assert web_engine._attach_to_recorded_window(d, evt, wait_s=0.15) is False
    assert d._current == 0

    # Popup reaches the recorded host -> switch and stay.
    d.set_url(1, "https://accounts.google.com/v3/signin/identifier?continue=x")
    assert web_engine._attach_to_recorded_window(d, evt, wait_s=0.15) is True
    assert d._current == 1

    # Already on the recorded story: no switch.
    assert web_engine._attach_to_recorded_window(d, evt, wait_s=0.1) is False
    assert d._current == 1

    # An action recorded back on the opener switches back.
    evt2 = Event(type="click", ts=2.0, url="https://www.linkedin.com/feed")
    assert web_engine._attach_to_recorded_window(d, evt2, wait_s=0.1) is True
    assert d._current == 0

    # Single window: never switches, even when the host differs (replay is
    # interaction-driven on the browser's current page).
    d1 = _two_window_driver(["chrome://new-tab-page/"])
    assert web_engine._attach_to_recorded_window(d1, evt, wait_s=0.05) is False
    assert d1._current == 0

    # No url / no host -> no switch.
    assert web_engine._attach_to_recorded_window(
        d1, Event(type="key", ts=1.0), wait_s=0.05) is False
    assert web_engine._attach_to_recorded_window(
        d1, Event(type="key", ts=1.0, url="not a url"), wait_s=0.05) is False


def test_attach_to_recorded_window_follows_same_host_popup():
    """A popup on the SAME host as its opener is followed by the recorded page
    PATH (url-host matching alone would stay on the opener)."""
    from player.web import engine as web_engine
    from player.web.events import Event

    d = _two_window_driver(
        ["https://app.example/home", "https://app.example/modal"], current_idx=0)
    # Action recorded in the same-host popup -> switch by path.
    assert web_engine._attach_to_recorded_window(
        d, Event(type="type", ts=1.0, url="https://app.example/modal"),
        wait_s=0.1) is True
    assert d._current == 1
    # Action recorded on the opener page -> switch back.
    assert web_engine._attach_to_recorded_window(
        d, Event(type="click", ts=2.0, url="https://app.example/home"),
        wait_s=0.1) is True
    assert d._current == 0


def test_engine_corrects_stale_ordinal_by_url(monkeypatch):
    """If the window handle order differs between record and replay, an ordinal
    can land on the wrong window - url matching must correct it."""
    from player.web import engine as web_engine
    from player.web.events import Event

    dispatched = []
    d = _two_window_driver(
        ["https://accounts.google.com/x", "https://www.linkedin.com/feed"])

    def fake_click(driver, evt, config):
        dispatched.append(driver.current_url)
        return {"ok": True, "method": "native", "error": None, "took": 0.0}

    monkeypatch.setattr(web_engine, "_DISPATCHERS", {"click": fake_click})
    monkeypatch.setattr(web_engine.ReplayEngine, "_pause", lambda self, *a, **k: None)

    engine = web_engine.ReplayEngine(d, web_engine.ReplayConfig(native_actions=False))
    # Ordinal 1 is the LinkedIn window, but the action was recorded on Google:
    # the mismatch is detected and url matching returns to the Google window.
    engine.run([Event(type="click", ts=1.0,
                      url="https://accounts.google.com/x", window_ordinal=1)])
    assert dispatched == ["https://accounts.google.com/x"]


def test_engine_replays_popup_actions_on_the_popup_window(monkeypatch):
    """A session recorded across windows (a click opens a sign-in popup, then
    typing lives in that popup) dispatches each action on the window its page
    ran on - the type is searched for in the popup document, not the opener."""
    from player.web import engine as web_engine
    from player.web.events import Event

    dispatched = []
    d = _two_window_driver(["https://www.linkedin.com/"])

    def fake_do_click(driver, evt, config):
        dispatched.append(("click", driver.current_url))
        driver.add_window(  # the click opens the sign-in popup (recorded flow)
            "https://accounts.google.com/v3/signin/identifier")

    def fake_do_type(driver, evt, config):
        dispatched.append(("type", driver.current_url))

    monkeypatch.setattr(web_engine, "_DISPATCHERS",
                        {"click": fake_do_click, "type": fake_do_type})
    monkeypatch.setattr(web_engine.ReplayEngine, "_pause",
                        lambda self, *a, **k: None)

    engine = web_engine.ReplayEngine(
        d, web_engine.ReplayConfig(native_actions=False))
    stats = engine.run([
        Event(type="click", ts=1.0, url="https://www.linkedin.com/"),
        Event(type="type", ts=2.0,
              url="https://accounts.google.com/v3/signin/identifier"),
    ])
    assert dispatched == [
        ("click", "https://www.linkedin.com/"),
        ("type", "https://accounts.google.com/v3/signin/identifier"),
    ]
    assert stats["ok"] == 2


def test_match_session_window_picks_driver_window():
    """CDP bounds + OS rect matching finds the session's Chrome window."""
    from player.web.session import _match_session_window

    class FakeDriver:
        def execute_cdp_cmd(self, _cmd, _params):
            return {"bounds": {"left": 0, "top": 0,
                               "width": 1000, "height": 700,
                               "windowState": "normal"}}

    windows = [
        {"pid": 11, "title": "dead", "rect": (0, 0, 200, 150)},
        {"pid": 22, "title": "recording", "rect": (0, 0, 1000, 700)},
    ]
    assert _match_session_window(FakeDriver(), windows) == 22

    # DPI-scaled display: CDP reports DIPs, GetWindowRect reports physical px.
    class FakeScaled:
        def execute_cdp_cmd(self, _cmd, _params):
            return {"bounds": {"left": 125, "top": 62,
                               "width": 250, "height": 125}}

    scaled = [{"pid": 33, "rect": (0, 0, 500, 250)}]
    assert _match_session_window(FakeScaled(), scaled) == 33

    assert _match_session_window(FakeDriver(), []) is None


def test_quiet_driver_logging_raises_http_loggers():
    """quiet_driver_logging() lifts the driver HTTP-traffic loggers to WARNING."""
    from player.web.session import quiet_driver_logging

    for name in ("urllib3", "selenium.webdriver.remote.remote_connection", "uc"):
        logging.getLogger(name).setLevel(logging.DEBUG)
    quiet_driver_logging()
    for name in ("urllib3", "urllib3.connectionpool", "uc",
                 "undetected_chromedriver", "CDP",
                 "selenium.webdriver.remote.remote_connection"):
        assert logging.getLogger(name).level >= logging.WARNING, name


# ---------------------------------------------------------------------------
# 6c. Single-window uc launch (port-wait pre-launch patch)
# ---------------------------------------------------------------------------


def test_uc_prelaunch_patch_installs_launcher_and_is_idempotent(monkeypatch):
    """_patch_uc_prelaunch swaps uc.start_detached for the port-wait launcher."""
    import undetected_chromedriver as uc
    from player.web import session as web_session

    original = uc.start_detached
    monkeypatch.setattr(uc, "_LOOPER_PRELAUNCH_PATCHED", False, raising=False)
    try:
        web_session._patch_uc_prelaunch()
        assert uc.start_detached is web_session._uc_launch_and_wait
        assert uc._LOOPER_PRELAUNCH_PATCHED is True
        # Second call must not re-patch or touch the launcher
        web_session._patch_uc_prelaunch()
        assert uc.start_detached is web_session._uc_launch_and_wait
    finally:
        monkeypatch.setattr(uc, "start_detached", original, raising=False)


def test_create_session_uses_single_launcher_and_counts_spawns(tmp_path, monkeypatch):
    """create_session forces the patched single-launcher path and bumps the
    spawn counter exactly once per session (1 = no uc double-launch)."""
    from player.web import session as web_session

    calls = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(web_session, "_SPAWNED_SESSIONS", 0)
    monkeypatch.setattr(web_session, "_cleanup_stale_looper_browsers",
                        lambda *a, **k: None)
    monkeypatch.setattr(web_session, "quiet_driver_logging", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_consolidate_windows", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_prune_extra_browser_windows",
                        lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_detect_chrome_version_main", lambda: 151)
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    web_session.create_session(headless=False, profile_dir=tmp_path / "p1")
    web_session.create_session(headless=True, profile_dir=tmp_path / "p2")

    assert len(calls) == 2  # one uc.Chrome per session, never two
    assert all(c["use_subprocess"] is False for c in calls)  # patched launcher path
    assert [c["headless"] for c in calls] == [False, True]
    assert [c["version_main"] for c in calls] == [151, 151]
    assert web_session._SPAWNED_SESSIONS == 2


def test_visible_session_opens_maximised_on_the_starting_monitor(tmp_path, monkeypatch):
    """A visible browser is positioned on the monitor the run was started from
    and maximised there; a headless one takes no window placement at all."""
    from player.web import session as web_session

    calls = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.maximised = False

        def maximize_window(self):
            self.maximised = True

    monkeypatch.setattr(web_session, "_SPAWNED_SESSIONS", 0)
    monkeypatch.setattr(web_session, "_cleanup_stale_looper_browsers",
                        lambda *a, **k: None)
    monkeypatch.setattr(web_session, "quiet_driver_logging", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_consolidate_windows", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_prune_extra_browser_windows",
                        lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_detect_chrome_version_main", lambda: 151)
    monkeypatch.setattr(web_session, "_cursor_monitor_work_area",
                        lambda: (1920, 0, 1920, 1080))
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    visible = web_session.create_session(headless=False, profile_dir=tmp_path / "v")
    headless = web_session.create_session(headless=True, profile_dir=tmp_path / "h")

    visible_args = calls[0]["options"].arguments
    headless_args = calls[1]["options"].arguments
    assert "--start-maximized" in visible_args
    assert "--window-position=1920,0" in visible_args
    assert "--start-maximized" not in headless_args
    assert not [a for a in headless_args if a.startswith("--window-position")]
    assert visible.maximised is True
    assert headless.maximised is False


def test_cursor_monitor_work_area_is_plausible():
    """The placement helper reports a real work area on Windows so the browser
    can be positioned on the monitor the run started from."""
    from player.web import session as web_session

    area = web_session._cursor_monitor_work_area()
    if area is None:
        assert os.name != "nt"
        return
    _left, _top, width, height = area
    assert width > 0 and height > 0


def test_launcher_waits_for_debug_port_before_returning(monkeypatch):
    """_uc_launch_and_wait launches Chrome detached and polls the debug port
    until chromedriver can attach - the fix that prevents the second window."""
    from player.web import session as web_session

    launched = {}
    reachable = {"count": 0}

    class FakeProc:
        pid = 4242

        def poll(self):
            return None  # Chrome stays alive while the port warms up

    def fake_popen(cmd, **kwargs):
        launched["cmd"] = cmd
        launched["kwargs"] = kwargs
        return FakeProc()

    class _FakeResponse:
        """Stand-in for the /json/version response (used via `with`)."""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_urlopen(url, timeout):
        reachable["count"] += 1
        if reachable["count"] < 3:
            raise OSError("port not ready yet")
        return _FakeResponse()

    monkeypatch.setattr(web_session.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(web_session.urllib.request, "urlopen", fake_urlopen)

    pid = web_session._uc_launch_and_wait(
        "C:/chrome.exe", "--remote-debugging-port=9222", "--user-data-dir=/tmp/x"
    )

    assert pid == 4242
    assert reachable["count"] == 3  # polled until the port answered
    assert launched["cmd"][0] == "C:/chrome.exe"
    assert launched["cmd"][1] == "--remote-debugging-port=9222"
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        assert launched["kwargs"]["creationflags"] == 0x208


def test_launcher_raises_when_chrome_exits_during_startup(monkeypatch):
    """A Chrome that dies before binding its port aborts with a clear error."""
    from player.web import session as web_session

    class FakeDeadProc:
        pid = 1
        returncode = 3

        def poll(self):
            return 3

    monkeypatch.setattr(web_session.subprocess, "Popen",
                        lambda *a, **k: FakeDeadProc())

    with pytest.raises(RuntimeError, match="exited during startup"):
        web_session._uc_launch_and_wait(
            "C:/chrome.exe", "--remote-debugging-port=9222"
        )


def test_launcher_retries_when_chrome_exits_then_succeeds(monkeypatch):
    """A Chrome that dies once during startup is retried; the second spawn wins."""
    from player.web import session as web_session

    calls = {"n": 0}

    class FakeAliveProc:
        pid = 4242

        def poll(self):
            return None  # stays alive while the port warms up

    class FakeDeadProc:
        pid = 1
        returncode = 0

        def poll(self):
            return 0

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def fake_popen(cmd, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeDeadProc()
        return FakeAliveProc()

    monkeypatch.setattr(web_session.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(web_session.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResponse())

    pid = web_session._uc_launch_and_wait(
        "C:/chrome.exe", "--remote-debugging-port=9222"
    )
    assert pid == 4242
    assert calls["n"] == 2  # first spawn died, retry spawned again


def test_cleanup_stale_looper_browsers_skips_active_profiles(monkeypatch):
    """Browsers holding profiles registered by THIS process are never killed."""
    import psutil
    from player.web import session as web_session

    killed = []

    class FakeProc:
        def __init__(self, pid, cmd):
            self.info = {"pid": pid, "name": "chrome.exe", "cmdline": cmd.split()}

        def kill(self):
            killed.append(self.info["pid"])

    active = r"C:\Users\LoOper\AppData\Local\Temp\looper-web-profile-abc"
    stale = r"C:\Users\LoOper\AppData\Local\Temp\looper-web-profile-stale1"
    procs = [
        FakeProc(1, f'"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --user-data-dir={active}'),
        FakeProc(2, f'"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --user-data-dir={stale}'),
        FakeProc(3, f"chrome.exe --type=gpu-process --user-data-dir={active}"),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs: iter(procs))
    monkeypatch.setattr(web_session, "_ACTIVE_PROFILE_DIRS", {active})

    web_session._cleanup_stale_looper_browsers()
    assert killed == [2]


def test_recorder_any_buffered_checks_only_current_window():
    """The fast dirty check reads ONLY the window the recorder is attached
    to - never switches windows (each switch_to.window RAISES the target
    window in Chrome, so a cross-window scan flips popup/opener focus and
    records spurious focus actions).  A dead window reports dirty so the
    full cycle detects the close."""
    from player.web.recorder import _any_buffered
    from selenium.common.exceptions import NoSuchWindowException

    class FakeDriver:
        def __init__(self, n):
            self._n = n

        def execute_script(self, _js):
            return self._n

    assert _any_buffered(FakeDriver(0)) is False
    assert _any_buffered(FakeDriver(1)) is True

    class DeadDriver:
        def execute_script(self, _js):
            raise NoSuchWindowException("window closed")

    assert _any_buffered(DeadDriver()) is True  # closed - run the full cycle


def test_recorder_follow_active_window_attaches_once_per_event():
    """Window changes are followed ONCE per event, never as a steady-state
    scan: a new window (a click just opened a popup) is attached to and gets
    its own CDP injection registration; a closed attached window re-anchors
    to a remaining one; an all-closed session reports window_gone."""
    from player.web.recorder import _follow_active_window
    from selenium.common.exceptions import NoSuchWindowException

    calls = []

    class FakeDriver:
        def __init__(self, handles, current=None):
            self._handles = list(handles)
            self._current = current if current is not None else (handles[0] if handles else None)

        @property
        def window_handles(self):
            return list(self._handles)

        @property
        def current_window_handle(self):
            if self._current not in self._handles:
                raise NoSuchWindowException("attached window closed")
            return self._current

        @property
        def switch_to(self):
            class _Switcher:
                def window(sw, handle):
                    calls.append(("switch", handle))
                    self._current = handle
            return _Switcher()

        def execute_cdp_cmd(self, _cmd, _params):
            calls.append(("cdp",))

    # Baseline: no change, no switch.
    d = FakeDriver(["w1"], current="w1")
    known, gone = _follow_active_window(d, "payload", {"w1"})
    assert (known, gone, calls) == ({"w1"}, False, [])

    # Popup opens: attach to it once and register its injector.
    d = FakeDriver(["w1", "w2"], current="w1")
    known, gone = _follow_active_window(d, "payload", {"w1"})
    assert known == {"w1", "w2"} and gone is False
    assert calls == [("switch", "w2"), ("cdp",)]

    # Steady state with the popup open: never switch back and forth.
    calls.clear()
    d = FakeDriver(["w1", "w2"], current="w2")
    known, gone = _follow_active_window(d, "payload", {"w1", "w2"})
    assert (known, gone, calls) == ({"w1", "w2"}, False, [])

    # Attached popup closes: re-anchor to the remaining window.
    calls.clear()
    d = FakeDriver(["w1"], current="w2")
    known, gone = _follow_active_window(d, "payload", {"w1", "w2"})
    assert (known, gone) == ({"w1"}, False)
    assert calls == [("switch", "w1"), ("cdp",)]

    # All windows closed: window_gone.
    calls.clear()
    known, gone = _follow_active_window(FakeDriver([]), "payload", {"w1"})
    assert (known, gone) == (set(), True)

    # No baseline yet (startup): remember the set, do not switch.
    calls.clear()
    d = FakeDriver(["w1", "w2"], current="w1")
    known, gone = _follow_active_window(d, "payload", set())
    assert (known, gone, calls) == ({"w1", "w2"}, False, [])


def _geometry_driver(geometries):
    """A fake WebDriver whose windows report JS geometry per window."""
    class FakeDriver:
        def __init__(self):
            self._geo = [list(g) for g in geometries]
            self._current = 0
            self.switched = []
            self.injected = 0

        @property
        def window_handles(self):
            return [f"w{i}" for i in range(len(self._geo))]

        @property
        def current_window_handle(self):
            return f"w{self._current}"

        @property
        def switch_to(self):
            class _Switcher:
                def window(sw, handle):
                    self.switched.append(handle)
                    self._current = int(handle[1:])
            return _Switcher()

        def execute_script(self, script, *args):
            if script.startswith("return [window.screenX"):
                return list(self._geo[self._current])
            self.injected += 1
            return True

        def execute_cdp_cmd(self, _cmd, _params):
            self.injected += 1

    return FakeDriver()


def test_match_handle_by_os_rect_checks_current_without_probing(monkeypatch):
    """The OS-rect -> window-handle matcher confirms the ATTACHED window first
    (no switch - probing raises windows, forbidden in a steady-state poll) and
    only probes the others when the rect belongs to a different window."""
    from player.web.recorder import _match_handle_by_os_rect

    # w0 at (0,0,1000x700), w1 at (400,100,800x500) - scale 1.0 physical == DIP.
    d = _geometry_driver([(0, 0, 1000, 700), (400, 100, 800, 500)])

    # Foreground == attached window: matched with NO probe switches.
    assert _match_handle_by_os_rect(d, (0, 0, 1000, 700)) == "w0"
    assert d.switched == []

    # Foreground == the OTHER window: probe once, switch, stay there.
    assert _match_handle_by_os_rect(d, (400, 100, 1200, 600)) == "w1"
    assert d.switched == ["w1"]
    assert d._current == 1

    # No window matches: probe all, restore the original.
    d2 = _geometry_driver([(0, 0, 1000, 700), (400, 100, 800, 500)])
    assert _match_handle_by_os_rect(d2, (3000, 3000, 3100, 3100)) is None
    assert d2.switched == ["w1", "w0"]  # probed w1, restored w0
    assert d2._current == 0

    # DPI-scaled display: geometry is DIP, the OS rect is physical px (1.5x).
    d3 = _geometry_driver([(0, 0, 1000, 700)])
    assert _match_handle_by_os_rect(d3, (0, 0, 1500, 1050)) == "w0"
    assert d3.switched == []


def test_follow_user_window_attaches_to_focused_browser_window(monkeypatch):
    """The recorder follows the user into an ALREADY-OPEN window by polling the
    OS foreground: same-pid foreground on a different window triggers one
    switch + injection; other apps, a single window, or already being there
    never switch."""
    from player.web import recorder as rec_mod

    d = _geometry_driver([(0, 0, 1000, 700), (400, 100, 800, 500)])
    monkeypatch.setattr(rec_mod, "_foreground_window",
                        lambda: (123, (400, 100, 1200, 600)))
    assert rec_mod._follow_user_window(d, "payload", 123) is True
    assert d.switched == ["w1"] and d._current == 1
    assert d.injected >= 2  # CDP registration + current-document injection

    # Already attached to the focused window: no probe, no switch.
    d2 = _geometry_driver([(0, 0, 1000, 700), (400, 100, 800, 500)])
    monkeypatch.setattr(rec_mod, "_foreground_window",
                        lambda: (123, (0, 0, 1000, 700)))  # == attached w0
    assert rec_mod._follow_user_window(d2, "payload", 123) is False
    assert d2.switched == [] and d2.injected == 0

    # Foreground window belongs to ANOTHER application: never switch.
    monkeypatch.setattr(rec_mod, "_foreground_window",
                        lambda: (999, (400, 100, 1200, 600)))
    d3 = _geometry_driver([(0, 0, 1000, 700), (400, 100, 800, 500)])
    assert rec_mod._follow_user_window(d3, "payload", 123) is False
    assert d3.switched == []

    # Single window: nothing to follow.
    monkeypatch.setattr(rec_mod, "_foreground_window",
                        lambda: (123, (0, 0, 1000, 700)))
    d4 = _geometry_driver([(0, 0, 1000, 700)])
    assert rec_mod._follow_user_window(d4, "payload", 123) is False
    assert d4.switched == []

    # No foreground / no pid: nothing to follow.
    monkeypatch.setattr(rec_mod, "_foreground_window", lambda: None)
    assert rec_mod._follow_user_window(d4, "payload", 123) is False
    assert rec_mod._follow_user_window(d4, "payload", None) is False


def test_recorder_payload_stamps_fingerprint():
    """The payload hands recorder.js its build fingerprint, and recorder.js
    copies it to __wvpRecorderFingerprint only AFTER its own guard - so merely
    re-evaluating the payload into a document that already runs an older
    recorder does not mark it current."""
    from player.web import recorder as rec_mod

    payload = rec_mod._payload()
    fp = rec_mod._recorder_fingerprint()
    assert ("window.__wvpRecorderPayloadFp = %r;" % fp) in payload

    js = (rec_mod._INJECT_DIR / "recorder.js").read_text(encoding="utf-8")
    guard = js.index("window.__wvpRecorder = true;")
    stamp = js.index("window.__wvpRecorderFingerprint = window.__wvpRecorderPayloadFp")
    assert stamp > guard  # the stamp must come after the idempotency guard


def test_inject_or_refresh_reloads_stale_recorder():
    """A stale in-page recorder in a long-lived SPA tab is replaced by ONE page
    reload (re-evaluation is a no-op thanks to the recorder's own guard); an
    up-to-date one is left alone, and an absent one is injected directly."""
    from player.web import recorder as rec_mod

    class _D:
        def __init__(self, state):
            self._state = state
            self.refreshed = 0
            self.injected = 0

        def execute_script(self, script):
            if script.strip().startswith("return {ready"):
                return dict(self._state)
            self.injected += 1
            return True

        def refresh(self):
            self.refreshed += 1

    want = rec_mod._recorder_fingerprint()

    # Current recorder -> nothing to do.
    d = _D({"ready": True, "fp": want})
    rec_mod._inject_or_refresh(d, "PAYLOAD")
    assert d.refreshed == 0 and d.injected == 0

    # No recorder present -> inject into the current document, no reload.
    d = _D({"ready": False, "fp": None})
    rec_mod._inject_or_refresh(d, "PAYLOAD")
    assert d.injected == 1 and d.refreshed == 0

    # Stale recorder -> exactly one reload.
    d = _D({"ready": True, "fp": "old"})
    rec_mod._inject_or_refresh(d, "PAYLOAD")
    assert d.refreshed == 1 and d.injected == 0


def test_recorder_consume_drained_never_truncates_on_stop():
    """A stop control event in a drained batch must not discard real actions.

    A stale stop already sitting in the persistent workbench page buffer used
    to truncate the append loop - actions after it were SIGNALED live but
    never saved ("0 actions" sessions).
    """
    from player.web.events import Event
    from player.web.recorder import _consume_drained

    actions = []
    drained = [
        Event(type="stop", ts=1.0),
        Event(type="click", ts=2.0, url="https://www.youtube.com/"),
        Event(type="navigate", ts=3.0, url="https://www.youtube.com/feed/you"),
    ]
    assert _consume_drained(actions, drained) is True
    assert [a.type for a in actions] == ["stop", "click", "navigate"]

    actions2 = []
    assert _consume_drained(actions2, [Event(type="click", ts=1.0)]) is False
    assert [a.type for a in actions2] == ["click"]


def test_replay_session_creates_fresh_browser_without_driver(monkeypatch):
    """replay_session without a driver creates its own browser and owns the
    lifecycle (headless cleans up, visible detaches); with a driver it uses
    that browser and touches nothing."""
    from player.web import engine as web_engine

    created, cleaned, detached = [], [], []

    class FakeDriver:
        pass

    monkeypatch.setattr(web_engine, "load_session",
                        lambda p: {"actions": [], "metadata": {}, "schema_version": 1})
    monkeypatch.setattr(web_engine, "create_session",
                        lambda **k: created.append(k) or object())
    monkeypatch.setattr(web_engine, "cleanup_session", lambda d: cleaned.append(1))
    monkeypatch.setattr(web_engine, "detach_session", lambda d: detached.append(1))

    # An explicit driver is used as-is - the caller owns its lifecycle.
    driver = FakeDriver()
    stats = web_engine.replay_session("s.json", driver=driver)
    assert stats["total"] == 0
    assert created == [] and cleaned == [] and detached == []

    # No driver: a fresh session is created; headless replays clean up after
    # themselves, visible ones are detached (left open for inspection).
    web_engine.replay_session("s.json", web_engine.ReplayConfig(headless=True))
    assert len(created) == 1 and len(cleaned) == 1 and detached == []


def test_sequence_executor_shares_web_driver_across_chain_nodes(monkeypatch, tmp_path):
    """The executor creates ONE browser per chain and reuses it across the
    chain's web sequence nodes; close_web_session terminates it afterwards."""
    from player.multi_sequence.sequence_executor import SequenceExecutor
    from player.web import session as web_session

    created = []
    cleaned = []

    class FakeDriver:
        def __init__(self, n):
            self.n = n

        def execute_script(self, script):
            return 1

    def fake_create(**kwargs):
        created.append(kwargs)
        return FakeDriver(len(created))

    monkeypatch.setattr(web_session, "create_session", fake_create)
    monkeypatch.setattr(web_session, "cleanup_session", lambda d: cleaned.append(d))

    cfg = type("C", (), {"headless": False})()
    ex = SequenceExecutor(chain_file_dir=str(tmp_path))

    d1 = ex._get_or_create_web_driver(cfg)
    d2 = ex._get_or_create_web_driver(cfg)  # next node -> same chain browser
    assert d1 is d2
    assert len(created) == 1

    # A dead browser is replaced, not silently reused.
    class DeadDriver:
        def execute_script(self, script):
            raise RuntimeError("session gone")

    ex._web_session_driver = DeadDriver()
    d3 = ex._get_or_create_web_driver(cfg)
    assert d3 is not d1 and len(created) == 2

    ex.close_web_session()
    assert d3 in cleaned  # dead driver cleaned on relaunch too
    assert cleaned[-1] is d3  # live one closed by close_web_session
    assert ex._web_session_driver is None
    ex.close_web_session()  # idempotent no-op


def test_run_web_replay_passes_shared_driver(monkeypatch, tmp_path):
    """_run_web_replay hands the chain's shared driver to replay_session so
    every node of the chain runs on the same browser instance."""
    from player.multi_sequence.sequence_executor import SequenceExecutor
    from player.web import engine as web_engine
    from player.web import events as web_events
    from player.web import session as web_session

    created = []

    class FakeDriver:
        def execute_script(self, script):
            return 1

    global_driver = FakeDriver()

    def fake_create(**kwargs):
        created.append(kwargs)
        return global_driver

    monkeypatch.setattr(web_session, "create_session", fake_create)
    monkeypatch.setattr(web_events, "is_web_session", lambda p: True)
    monkeypatch.setattr(web_events, "load_session",
                        lambda p: {"actions": [], "metadata": {}, "schema_version": 1})
    monkeypatch.setattr(web_engine, "replay_config_from_item",
                        lambda item: type("C", (), {"headless": False})())

    seen = []

    def fake_replay(path, config, stop_flag=None, driver=None, driver_factory=None,
                    entity_state=None):
        seen.append(driver)
        assert callable(driver_factory)  # engine can reconnect mid-run
        return {"total": 0, "attempted": 0, "ok": 0, "failed": [],
                "duration_sec": 0.0, "entity_exhausted": False, "entity_total": 0}

    monkeypatch.setattr(web_engine, "replay_session", fake_replay)

    ex = SequenceExecutor(chain_file_dir=str(tmp_path))
    item = {"session_file": "s.json", "loop_count": 1, "extra_delay": 0}
    assert ex._run_web_replay(item, str(tmp_path / "s.json"), None, 1, 0.0) is True
    assert ex._run_web_replay(item, str(tmp_path / "s.json"), None, 1, 0.0) is True

    assert len(created) == 1  # one browser for the whole chain
    assert len(seen) == 2
    assert seen[0] is seen[1]  # both nodes replayed on the same driver


def _entity_session_env(monkeypatch, tmp_path, replay):
    """Wire a SequenceExecutor whose session has ONE entity action and a fake
    replay_session (so no real browser is needed)."""
    import types
    from player.multi_sequence.sequence_executor import SequenceExecutor
    from player.web import engine as web_engine
    from player.web import events as web_events
    from player.web import session as web_session

    class FakeDriver:
        def execute_script(self, script):
            return 1

    monkeypatch.setattr(web_session, "create_session", lambda **k: FakeDriver())
    monkeypatch.setattr(web_session, "cleanup_session", lambda d: None)
    monkeypatch.setattr(web_events, "is_web_session", lambda p: True)
    monkeypatch.setattr(
        web_events, "load_session",
        lambda p: {"actions": [types.SimpleNamespace(entity={"selector": "div.card"})],
                   "metadata": {}, "schema_version": 1},
    )
    monkeypatch.setattr(web_engine, "replay_config_from_item",
                        lambda item: type("C", (), {"headless": False})())
    monkeypatch.setattr(web_engine, "replay_session", replay)
    return SequenceExecutor(chain_file_dir=str(tmp_path))


def test_web_entity_cursor_persists_across_executions(monkeypatch, tmp_path):
    """A repeating-element (Insert-marked) sequence keeps ONE cursor per
    session path across re-executions within a chain run, so each execution
    clicks the NEXT matching element; a different sequence has its own cursor,
    and close_web_session() clears them at the end of the chain run."""
    states = []

    def fake_replay(path, config, stop_flag=None, driver=None, driver_factory=None,
                    entity_state=None):
        states.append(entity_state)
        return {"total": 1, "attempted": 1, "ok": 1, "failed": [],
                "duration_sec": 0.0, "entity_exhausted": False, "entity_total": 3}

    ex = _entity_session_env(monkeypatch, tmp_path, fake_replay)
    item = {"session_file": "s.json", "loop_count": 1, "extra_delay": 0}
    assert ex._run_web_replay(item, str(tmp_path / "s.json"), None, 1, 0.0) is True
    assert ex._run_web_replay(item, str(tmp_path / "s.json"), None, 1, 0.0) is True
    # Same cursor dict carried across the two executions of the same sequence.
    assert states[0] is not None and states[0] is states[1]

    # A different sequence keeps its OWN cursor.
    assert ex._run_web_replay({"session_file": "t.json", "loop_count": 1},
                              str(tmp_path / "t.json"), None, 1, 0.0) is True
    assert states[2] is not None and states[2] is not states[0]

    # Chain run ends: the temporal buffer is forgotten.
    ex.close_web_session()
    assert ex._web_entity_states == {}


def test_web_entity_loop_stops_when_exhausted(monkeypatch, tmp_path):
    """loop_count drives the number of elements clicked per execution; the loop
    stops early the moment the engine reports the set is exhausted."""
    passes = {"n": 0}

    def fake_replay(path, config, stop_flag=None, driver=None, driver_factory=None,
                    entity_state=None):
        passes["n"] += 1
        return {"total": 1, "attempted": 1, "ok": 1, "failed": [],
                "duration_sec": 0.0, "entity_exhausted": passes["n"] >= 3,
                "entity_total": 3}

    ex = _entity_session_env(monkeypatch, tmp_path, fake_replay)
    item = {"session_file": "s.json", "loop_count": 5, "extra_delay": 0}
    assert ex._run_web_replay(item, str(tmp_path / "s.json"), None, 5, 0.0) is True
    assert passes["n"] == 3  # stopped the moment the set was exhausted


def test_entity_kind_widens_onto_the_row_identity():
    """A pair-marked kind is recorded as a WRAPPER path ending in the row's
    shared identity.  The wrapper levels differ between one link of a site and
    another while the identity does not, so the trailing compound must be
    recoverable as a set selector - otherwise the cursor matched nothing on the
    new page and replay re-clicked the ONE recorded row (the reported bug)."""
    from player.web import engine as web_engine

    # Backed by a real failing recording (LoOper/web_sequences/siguiente.json).
    recorded = ("main#workspace > div.e5d9f935.edbd809c._2442e233 > "
                "div._70a6f2dc.fbe14e35._10161c29 > div.a3507014._1ededff6._95ef5582 > "
                "div.c7b7a641._5184c5d1._6dfe9b50 > div._9fa9cfc8.d770a21d > "
                "div.e5d9f935._99080caf.edbd809c > div._9fa9cfc8.d770a21d > "
                "div._9e8b94be.fb00a85a._97f97196._5a567015._89db3a79.d770a21d")
    assert web_engine.ReplayEngine._widen_kind_selectors([recorded]) == [
        "div._9e8b94be.fb00a85a._97f97196._5a567015._89db3a79.d770a21d"]

    # A positional compound names a PLACE, not a repeating item: never widened
    # (a bare tag or an nth-of-type would match far too much).
    assert web_engine.ReplayEngine._widen_kind_selectors(
        ["main#workspace > div:nth-of-type(2)", "main#workspace > div"]) == []
    # An already-portable selector passes through unchanged (and dedupes).
    assert web_engine.ReplayEngine._widen_kind_selectors(
        ["div.card", "div.card"]) == ["div.card"]


def test_entity_cursor_advances_when_only_the_widened_kind_matches(monkeypatch):
    """Two passes must steer the click at DIFFERENT rows: the recorded
    container path is gone on the live page, so only the widened selector
    resolves the set - and the cursor still advances instead of re-clicking the
    recorded element."""
    from player.web import engine as web_engine, exec_overlay
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    container = "main#workspace > div.gone > div.row"
    widened = "div.row"  # the container's wrapper levels dropped, identity kept

    class FakeRow:
        def find_elements(self, by, selector):
            return []  # no row encloses another row

    rows = [FakeRow() for _ in range(3)]

    class FakeDriver:
        def find_elements(self, by, selector):
            return rows if selector == widened else []

    monkeypatch.setattr(exec_overlay, "enable", lambda d: None)
    monkeypatch.setattr(exec_overlay, "disable", lambda d: None)
    monkeypatch.setattr(exec_overlay, "mark", lambda d, e, a: None)
    monkeypatch.setattr(web_engine.ReplayEngine, "_pause", lambda self, *a, **k: None)
    monkeypatch.setattr(web_actions, "switch_to_window_ordinal",
                        lambda d, o, **k: False)

    steered = []
    monkeypatch.setattr(web_engine, "_DISPATCHERS",
                        {"click": lambda d, e, c: steered.append(e.locator.index)})

    engine = web_engine.ReplayEngine(FakeDriver(),
                                    web_engine.ReplayConfig(native_actions=False))
    evt = Event(type="click", ts=1.0, locator=Locator(tag="div", css=container),
                entity={"selector": container, "selectors": [container]})
    state: dict = {}
    engine.run([evt], entity_state=state)
    engine.run([evt], entity_state=state)
    assert steered == [0, 1]


def test_hover_dispatcher_registered():
    """Hover actions are dispatched by the replay engine."""
    from player.web.handlers import HANDLERS
    from player.web import engine as web_engine
    assert web_engine._DISPATCHERS["hover"].__self__ is HANDLERS["hover"]


def test_event_describe_hover():
    """A hover action summarizes its target for the recording signal."""
    from player.web.events import Event, Locator
    evt = Event(type="hover", ts=1.0, locator=Locator(tag="button", text="Menu"))
    assert evt.describe() == 'hover button "Menu"'


def test_do_hover_dispatches_enter_over_sequence(monkeypatch):
    """DOM hover dispatches the pointer/mouse enter-over sequence on the element."""
    from player.web import actions as web_actions

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))

    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: "el")

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = type("E", (), {"coordinates": None, "frame_path": [],
                          "cross_origin_frame": False, "locator": None})()
    web_actions.do_hover(FakeDriver(), evt, cfg)
    assert any("mouseover" in script for script, _args in executed)


def test_do_click_dispatches_on_element_selector_not_coordinates():
    """Clicks are element-based: the recorded selector is dispatched on and
    viewport coordinates are never used - they only match the recording window
    size/scroll and would hit a different element on a resized replay window."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))
            return True  # JS_DOM_POINTER found + dispatched a target

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = Event(type="click", ts=1.0, coordinates={"x": 12, "y": 34},
                locator=Locator(id="input", css="input#input"))
    web_actions.do_click(FakeDriver(), evt, cfg)

    assert len(executed) == 1
    script, args = executed[0]
    assert script is web_actions.JS_DOM_POINTER
    assert "elementFromPoint" not in script  # no coordinate dispatch
    assert args[0] == ["input#input", "#input"]  # css first, id fallback
    # (selectors, button, mode, modifiers, ordinal, text, label) - never x/y
    # coordinates; no label recorded here, so the in-page search falls through
    # to the recorded selectors.
    assert len(args) == 7
    assert args[3] == [] and args[4] is None and args[5] is None and args[6] is None


def test_dispatch_dom_pointer_searches_shadow_roots():
    """The unified pointer dispatch deep-searches shadow roots, so a recorded
    element that moved into a shadow root by replay time (e.g. the Chrome
    new-tab search box hydrating into ntp-app) is still dispatched on."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))
            return True

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = Event(type="click", ts=1.0, coordinates=None,
                locator=Locator(id="input", css="textarea#input"))
    assert web_actions._dispatch_dom_pointer(
        FakeDriver(), evt, cfg, "0", "click") is True
    script, args = executed[0]
    assert "shadowRoot" in script
    # the explicit css candidate wins over the bare id (which could match a
    # shadow HOST sharing that id instead of the real element)
    assert args[0] == ["textarea#input", "#input"]
    # (selectors, button, mode, modifiers, ordinal, text) - no x/y coordinates
    assert args[1] == "0" and args[2] == "click" and args[3] == []


def test_do_click_native_interaction_when_locator_resolves(monkeypatch):
    """When the recorded locator resolves, the click uses undetected-
    chromedriver's native search-and-interact (ActionChains), not JS."""
    from player.web import actions as web_actions

    performed = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def move_to_element(self, el):
            return self

        def click(self):
            performed.append("click")
            return self

        def key_down(self, key):
            performed.append("down")
            return self

        def key_up(self, key):
            performed.append("up")
            return self

        def perform(self):
            performed.append("perform")

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: "el")
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = type("E", (), {"locator": object(), "button": "0", "coordinates": None,
                          "frame_path": [], "cross_origin_frame": False,
                          "modifiers": []})()
    web_actions.do_click(object(), evt, cfg)
    assert performed == ["click", "perform"]


def test_do_click_raises_when_nothing_found(monkeypatch):
    """If neither the locator nor the JS dispatch finds a target, the failure
    is loud - never a silent no-op."""
    from player.web import actions as web_actions
    import pytest

    class FakeDriver:
        def execute_script(self, script, *args):
            return False  # dispatch found nothing

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("none")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = type("E", (), {"locator": web_actions.Locator(css="textarea#input"),
                          "button": "0", "coordinates": None,
                          "frame_path": [], "cross_origin_frame": False})()
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions.do_click(FakeDriver(), evt, cfg)


def test_do_key_native_path_chords_recorded_modifiers(monkeypatch):
    """Native key replay sends recorded modifiers as REAL chorded keys, so
    Ctrl+K drives the page's own shortcut exactly like the user's keystroke."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def send_keys(self, key):
            calls.append(("key", key))
            return self

        def perform(self):
            calls.append(("perform",))

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: None)

    cfg = type("C", (), {"native_actions": True, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "k", "modifiers": ["Ctrl"],
                          "locator": None, "context": {}})()
    web_actions.do_key(object(), evt, cfg)
    assert calls == [("down", Keys.CONTROL), ("key", "k"),
                     ("up", Keys.CONTROL), ("perform",)]


def test_locator_chain_includes_attribute_fallbacks():
    """Recorded element properties (placeholder/aria/name/type/href) become
    searchable locator candidates for the native find path."""
    from player.web.events import Locator

    loc = Locator(tag="input", placeholder="Search", aria_label="Search box",
                  name="q", input_type="text")
    kinds = [c["kind"] for c in loc.chain()]
    assert "data" in kinds
    values = {c.get("name"): c.get("value") for c in loc.chain() if c["kind"] == "data"}
    assert values.get("placeholder") == "Search"
    assert values.get("aria-label") == "Search box"
    assert values.get("name") == "q"


def test_event_describe_focus():
    """A focus action summarizes its target for the recording signal."""
    from player.web.events import Event, Locator
    evt = Event(type="focus", ts=1.0, locator=Locator(tag="input", name="q"))
    assert evt.describe() == 'focus input[name=q]'


def test_detect_chrome_version_prefers_registry_without_launching(monkeypatch):
    """Version detection never spawns chrome.exe (a running Chrome would hand
    off and open a stray window).  Pure reads - registry / folder / VERSIONINFO."""
    from player.web import session as web_session

    launched = []

    def fail_if_launched(*args, **kwargs):
        launched.append(args)
        raise AssertionError("chrome.exe must not be launched for version detection")

    monkeypatch.setattr(web_session.subprocess, "run", fail_if_launched)
    result = web_session._detect_chrome_version_main()
    assert launched == []
    assert result is None or result >= 100  # a real Chrome major version


# ---------------------------------------------------------------------------
# 6d. Persistent recording browser (workbench reuse)
# ---------------------------------------------------------------------------


def _patch_session_boilerplate(monkeypatch, tmp_path):
    """Common create_session no-ops so workbench tests never touch real Chrome."""
    from player.web import session as web_session

    monkeypatch.setattr(web_session, "_workbench_dir", lambda: tmp_path)
    monkeypatch.setattr(web_session, "_cleanup_stale_looper_browsers",
                        lambda *a, **k: None)
    monkeypatch.setattr(web_session, "quiet_driver_logging", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_detect_chrome_version_main", lambda: 151)
    return web_session


def test_ensure_workbench_relaunches_when_state_stale(tmp_path, monkeypatch):
    """Stale/dead workbench state -> fresh launch on the persistent profile."""
    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    calls = []
    consolidated = []
    state_writes = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.browser_pid = 4242

    monkeypatch.setattr(web_session, "_read_workbench_state",
                        lambda key=None: {"pid": 999999, "port": 9222})
    monkeypatch.setattr(web_session, "_find_workbench_browser", lambda key=None: None)
    monkeypatch.setattr(web_session, "_write_workbench_state",
                        lambda driver: state_writes.append(driver))
    monkeypatch.setattr(web_session, "_consolidate_windows",
                        lambda *a, **k: consolidated.append(1))
    monkeypatch.setattr(web_session, "_prune_extra_browser_windows",
                        lambda *a, **k: None)
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    driver = web_session.ensure_workbench_session()

    assert len(calls) == 1
    assert calls[0]["user_data_dir"] == str(tmp_path)  # persistent, not temp
    assert calls[0]["options"].debugger_address is None  # fresh launch, no attach
    assert calls[0]["headless"] is False
    assert len(state_writes) == 1 and state_writes[0] is driver
    assert len(consolidated) == 1  # own launch: single-window consolidation runs


def test_ensure_workbench_reuses_one_driver_per_scope(tmp_path, monkeypatch):
    """Repeated calls return the SAME driver (ONE chromedriver per scope) instead
    of spawning a new one attached to the same browser - the leak that stacked
    chromedriver.exe processes and ate RAM.  A dead wrapper is rebuilt, and
    cleanup forgets the cached wrapper."""
    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    calls = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.browser_pid = 1

        def execute_script(self, *a, **k):
            return 1

    monkeypatch.setattr(web_session, "_find_workbench_browser",
                        lambda key=None: {"pid": 1, "port": 9222})
    monkeypatch.setattr(web_session, "_save_workbench_state", lambda *a, **k: None)
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    d1 = web_session.ensure_workbench_session(chain_key="scope-r")
    d2 = web_session.ensure_workbench_session(chain_key="scope-r")
    assert d1 is d2
    assert len(calls) == 1  # ONE spawn for the whole run

    def _dead(*a, **k):
        raise RuntimeError("session gone")
    d1.execute_script = _dead
    d3 = web_session.ensure_workbench_session(chain_key="scope-r")
    assert d3 is not d1 and len(calls) == 2  # dead wrapper rebuilt

    web_session.cleanup_session(d3)
    assert web_session._WORKBENCH_DRIVERS.get("scope-r") is None


def test_workbench_attach_failure_rescans_instead_of_relaunching(
    tmp_path, monkeypatch
):
    """A failed attach to a STALE recorded port must re-scan and re-attach to
    the LIVE browser instead of launching on a profile the live browser still
    holds.

    That launch collides: Chrome hands off to the running instance and exits,
    so the launcher waits on a port that never opens — the frozen-app failure
    (state file pid dead while a live Chrome held the shared profile).
    """
    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    scans = [
        {"pid": 100, "port": 1111},   # stale recorded port (dead socket)
        {"pid": 200, "port": 2222},   # the LIVE browser, found on re-scan
    ]
    monkeypatch.setattr(
        web_session, "_find_workbench_browser",
        lambda key=None: scans.pop(0) if scans else None,
    )
    monkeypatch.setattr(web_session, "_save_workbench_state", lambda *a, **k: None)
    attach_ports = []

    class FakeChrome:
        def __init__(self, **kwargs):
            addr = kwargs["options"].debugger_address
            attach_ports.append(addr)
            if addr == "127.0.0.1:1111":
                raise ConnectionResetError("stale port")
            self.browser_pid = 200

    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    driver = web_session.ensure_workbench_session(chain_key="scope-recover")

    # The stale port was tried, then the live one — never a fresh launch.
    assert attach_ports == ["127.0.0.1:1111", "127.0.0.1:2222"]
    assert driver is not None
    assert scans == []  # both scans consumed: no relaunch path was taken


def test_ensure_workbench_attaches_when_state_alive(tmp_path, monkeypatch):
    """Live browser on the profile -> attach, never touch its windows."""
    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    calls = []
    consolidated = []
    state_refreshes = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.browser_pid = 1234

    monkeypatch.setattr(web_session, "_find_workbench_browser",
                        lambda key=None: {"pid": 1234, "port": 9222})
    monkeypatch.setattr(web_session, "_save_workbench_state",
                        lambda pid, port: state_refreshes.append((pid, port)))
    monkeypatch.setattr(web_session, "_consolidate_windows",
                        lambda *a, **k: consolidated.append(1))
    monkeypatch.setattr(web_session, "_prune_extra_browser_windows",
                        lambda *a, **k: consolidated.append(2))
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    driver = web_session.ensure_workbench_session()

    assert len(calls) == 1
    assert calls[0]["user_data_dir"] == str(tmp_path)
    assert calls[0]["options"].debugger_address == "127.0.0.1:9222"  # attached
    assert driver.browser_pid == 1234
    assert consolidated == []  # attach mode never closes/prunes workbench windows
    assert state_refreshes == [(1234, 9222)]  # state kept in sync with live pid
    assert web_session._ATTACH_ONLY is False  # flag reset after creation


def test_ensure_workbench_attaches_to_replaced_browser_process(tmp_path, monkeypatch):
    """State pid dead but the browser is alive (Chrome replaced its main
    process) -> attach to the LIVE browser, never relaunch on the locked
    profile.  Regression for the stale-pid double-launch failure."""
    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    calls = []
    state_refreshes = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.browser_pid = -1  # attach mode: launcher is a no-op

    monkeypatch.setattr(web_session, "_read_workbench_state",
                        lambda: {"pid": 19472, "port": 60747})  # stale pid
    monkeypatch.setattr(web_session, "_find_workbench_browser",
                        lambda key=None: {"pid": 31886, "port": 60747})  # live browser
    monkeypatch.setattr(web_session, "_save_workbench_state",
                        lambda pid, port: state_refreshes.append((pid, port)))
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    driver = web_session.ensure_workbench_session()

    assert len(calls) == 1
    assert calls[0]["options"].debugger_address == "127.0.0.1:60747"
    assert driver is not None
    assert state_refreshes == [(31886, 60747)]  # state updated to the live pid


def test_ensure_workbench_errors_when_browser_open_without_port(tmp_path, monkeypatch):
    """A manually started browser on the profile (no debug port) cannot be
    attached to - fail with a clear message instead of spawning a second
    instance that would hand off and exit."""
    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)

    monkeypatch.setattr(web_session, "_find_workbench_browser",
                        lambda key=None: {"pid": 4242, "port": None})

    with pytest.raises(RuntimeError, match="no debugging"):
        web_session.ensure_workbench_session()


def test_find_workbench_browser_skips_helper_processes(tmp_path, monkeypatch):
    """The profile scan finds the MAIN browser process, never renderers."""
    from player.web import session as web_session

    profile = str(tmp_path)
    monkeypatch.setattr(web_session, "_workbench_dir", lambda: tmp_path)
    fake_procs = [
        {"pid": 100, "name": "chrome.exe",
         "cmdline": ["C:/chrome.exe", f"--user-data-dir={profile}",
                      "--remote-debugging-port=9222", "--no-first-run"]},
        {"pid": 101, "name": "chrome.exe",
         "cmdline": ["C:/chrome.exe", "--type=renderer",
                      f"--user-data-dir={profile}", "--remote-debugging-port=9222"]},
        {"pid": 102, "name": "chrome.exe",
         "cmdline": ["C:/chrome.exe", "--type=gpu-process",
                      f"--user-data-dir={profile}"]},
    ]

    class FakeProc:
        def __init__(self, info):
            self.info = info

    monkeypatch.setattr(
        web_session.psutil, "process_iter",
        lambda *a, **k: [FakeProc(p) for p in fake_procs],
    )

    found = web_session._find_workbench_browser()
    assert found == {"pid": 100, "port": 9222}  # main process only

    # Only helpers on the profile -> no browser.
    monkeypatch.setattr(
        web_session.psutil, "process_iter",
        lambda *a, **k: [FakeProc(p) for p in fake_procs[1:]],
    )
    assert web_session._find_workbench_browser() is None


def test_find_workbench_browser_exact_profile_match(tmp_path, monkeypatch):
    """The profile scan matches the EXACT --user-data-dir: a chain-scoped
    browser (chains/<key> lives under the root) is never picked up by the
    shared root scan, and vice versa - chains keep their own browsers."""
    from player.web import session as web_session

    monkeypatch.setattr(web_session, "_workbench_dir", lambda: tmp_path)
    root = str(tmp_path)
    chain_a = str(tmp_path / "chains" / "chain-a")
    fake_procs = [
        {"pid": 100, "name": "chrome.exe",
         "cmdline": ["C:/chrome.exe", f"--user-data-dir={root}",
                      "--remote-debugging-port=9222", "--no-first-run"]},
        {"pid": 200, "name": "chrome.exe",
         "cmdline": ["C:/chrome.exe", f"--user-data-dir={chain_a}",
                      "--remote-debugging-port=9333"]},
        {"pid": 300, "name": "chrome.exe",
         "cmdline": ["C:/chrome.exe", f"--user-data-dir={root}\"x\""]},
    ]

    class FakeProc:
        def __init__(self, info):
            self.info = info

    monkeypatch.setattr(
        web_session.psutil, "process_iter",
        lambda *a, **k: [FakeProc(p) for p in fake_procs],
    )

    assert web_session._find_workbench_browser() == {"pid": 100, "port": 9222}
    assert web_session._find_workbench_browser("chain-a") == {"pid": 200, "port": 9333}


def test_attach_mode_neutralizes_chrome_launch(monkeypatch):
    """Attach mode must never spawn a Chrome - the launcher is a no-op."""
    from player.web import session as web_session

    popen_calls = []
    monkeypatch.setattr(web_session.subprocess, "Popen",
                        lambda *a, **k: popen_calls.append(a) or object())
    web_session._ATTACH_ONLY = True
    try:
        pid = web_session._uc_launch_and_wait(
            "C:/chrome.exe", "--remote-debugging-port=9222"
        )
    finally:
        web_session._ATTACH_ONLY = False
    assert pid == -1
    assert popen_calls == []


def test_release_workbench_leaves_browser_open():
    """release_workbench neutralizes quit - the persistent browser survives."""
    from player.web.session import release_workbench

    class FakeDriver:
        def quit(self):
            raise AssertionError("workbench browser must not be closed")

    driver = FakeDriver()
    release_workbench(driver)
    driver.quit()  # no-op after release - must not raise / must not close


def test_chain_scope_dir_per_chain_isolation(tmp_path, monkeypatch):
    """Each chain scope gets its OWN durable profile dir; no key falls back to
    the shared root (back-compat with the original single workbench)."""
    from player.web import session as web_session

    monkeypatch.setattr(web_session, "_workbench_dir", lambda: tmp_path)

    assert web_session._chain_scope_dir(None) == tmp_path
    assert web_session._chain_scope_dir("chain-a") == tmp_path / "chains" / "chain-a"
    assert web_session._chain_scope_dir("chain-b") == tmp_path / "chains" / "chain-b"
    assert web_session._chain_scope_dir("chain-a") != web_session._chain_scope_dir("chain-b")
    # Hostile keys are sanitized to filesystem-safe names (trailing dots/
    # underscores trimmed).
    assert web_session._chain_scope_dir("my/chain: 1!") == tmp_path / "chains" / "my_chain_1"


def test_ensure_workbench_uses_chain_scope_profile(tmp_path, monkeypatch):
    """A chain-scoped fresh launch uses chains/<key> as its profile and starts
    on the DEFAULT page - the last page is deliberately NOT restored, so a
    chain replays the same after an app restart (cookies persist regardless)."""
    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    calls = []
    state_writes = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.browser_pid = 777

    monkeypatch.setattr(web_session, "_find_workbench_browser", lambda key=None: None)
    monkeypatch.setattr(
        web_session, "_write_workbench_state",
        lambda driver, key=None: state_writes.append((driver, key)),
    )
    monkeypatch.setattr(web_session, "_consolidate_windows", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_prune_extra_browser_windows", lambda *a, **k: None)
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    driver = web_session.ensure_workbench_session(chain_key="chain-xyz")

    assert len(calls) == 1
    assert calls[0]["user_data_dir"] == str(tmp_path / "chains" / "chain-xyz")
    assert calls[0]["headless"] is False
    assert len(state_writes) == 1 and state_writes[0][1] == "chain-xyz"
    assert driver.browser_pid == 777


def test_workbench_launch_never_restores_last_page(tmp_path, monkeypatch):
    """Even a legacy state carrying last-page URLs must not navigate anywhere:
    the fresh browser starts on its default page.  Tab restore was the cause
    of post-restart failures (browser landed on the previous run's page while
    the sequence's actions were recorded on another)."""
    from player.web import session as web_session

    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    calls = []

    class FakeChrome:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.browser_pid = 777

    monkeypatch.setattr(web_session, "_find_workbench_browser", lambda key=None: None)
    monkeypatch.setattr(
        web_session, "_read_workbench_state",
        lambda key=None: {"pid": 1, "port": 2,
                          "tabs": ["https://example.com/last-page"]},
    )
    monkeypatch.setattr(web_session, "_write_workbench_state", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_consolidate_windows", lambda *a, **k: None)
    monkeypatch.setattr(web_session, "_prune_extra_browser_windows", lambda *a, **k: None)
    monkeypatch.setattr(web_session.uc, "Chrome", FakeChrome)

    # FakeChrome has no .get() - any navigation attempt would raise here.
    web_session.ensure_workbench_session(chain_key="chain-xyz")
    assert len(calls) == 1
    # The old restore/snapshot entry points are inert compatibility stubs.
    assert web_session._restore_workbench_tabs(object(), "k") == 0
    assert web_session.snapshot_workbench_tabs(object(), "k") is None


def test_workbench_state_keeps_pid_port_not_tabs(tmp_path, monkeypatch):
    """session_state.json persists only {pid, port} for attach/stale detection;
    page URLs are never written (the last page is not remembered)."""
    from player.web import session as web_session

    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    web_session._save_workbench_state(11, 22, chain_key="k")
    state = web_session._read_workbench_state("k")
    assert state == {"pid": 11, "port": 22}
    assert "tabs" not in state


def test_cleanup_session_keeps_chain_profiles(tmp_path, monkeypatch):
    """cleanup_session never deletes a profile under the workbench root (the
    chain's durable browser story); disposable temp profiles are removed."""
    from player.web import session as web_session

    web_session = _patch_session_boilerplate(monkeypatch, tmp_path)
    removed = []
    monkeypatch.setattr(
        web_session.shutil, "rmtree",
        lambda path, ignore_errors=False: removed.append(path),
    )

    class FakeDriver:
        def __init__(self, profile):
            self.user_data_dir = profile

        def quit(self):
            pass

    chain_profile = str(tmp_path / "chains" / "abc")
    web_session.cleanup_session(FakeDriver(chain_profile))
    assert removed == []  # chain story profile survives

    disposable = str(tmp_path / ".." / "looper-web-profile-tmp123")
    web_session.cleanup_session(FakeDriver(disposable))
    assert removed == [disposable]  # disposable profile still cleaned up


def test_sequence_executor_chain_linked_web_driver(tmp_path, monkeypatch):
    """web_chain_key on the executor routes playback to the chain's own
    persistent browser (ensure_workbench_session); without it, runs keep a
    fresh disposable browser (create_session) - isolation preserved."""
    from player.multi_sequence.sequence_executor import SequenceExecutor
    import player.web.session as web_session

    used = []

    class FakeDriver:
        def __init__(self, tag):
            self.tag = tag

        def execute_script(self, script):
            return 1

    def fake_ensure(chain_key=None):
        used.append(("ensure", chain_key))
        return FakeDriver("chain")

    def fake_create(**kwargs):
        used.append(("create", kwargs.get("headless")))
        return FakeDriver("disposable")

    monkeypatch.setattr(
        web_session, "create_session", fake_create,
    )
    # ensure_workbench_session is also imported lazily inside the method -
    # patch at the session module level so the same symbol is intercepted.
    monkeypatch.setattr(web_session, "ensure_workbench_session", fake_ensure)

    cfg = type("C", (), {"headless": False})()

    ex = SequenceExecutor(chain_file_dir=str(tmp_path))
    d1 = ex._get_or_create_web_driver(cfg)
    assert d1.tag == "disposable" and used[-1] == ("create", False)

    ex2 = SequenceExecutor(chain_file_dir=str(tmp_path))
    ex2.web_chain_key = "chain-xyz"
    d2 = ex2._get_or_create_web_driver(cfg)
    assert d2.tag == "chain" and used[-1] == ("ensure", "chain-xyz")


def test_web_profile_key_is_shared_scope():
    """Every chain run - saved or not, GUI or agent/scheduled/CLI - resolves
    to the SAME app-wide web browser scope, so cookies/logins recorded once
    survive app restarts in every mode.  (Per-chain keys could not survive
    for chains that are never saved to a file - their identity changed every
    app session, which is exactly the bug this fixes.)"""
    from player.multi_sequence_player import _web_profile_key_for

    # No file, no stamp: still the shared scope (never a fresh identity).
    assert _web_profile_key_for({}, None) == "default"
    # Legacy chain file and old per-chain stamps: ignored, shared scope wins.
    assert _web_profile_key_for({"description": "x"}, "C:/chains/gmail.json") == "default"
    assert _web_profile_key_for(
        {"description": "x", "_web_chain_scope": "unsaved-abc123"}, "moved.json"
    ) == "default"


def test_sequence_executor_web_profile_key_uses_durable_chain_profile(tmp_path, monkeypatch):
    """web_profile_key (set by the player for non-GUI runs of a chain file)
    makes the shared browser use the chain's DURABLE per-chain profile:
    headless honors headless on the durable dir, visible runs launch on the
    durable dir (no live browser) or attach to the chain's still-open
    browser, and a run without any key keeps the fresh disposable browser."""
    from player.multi_sequence.sequence_executor import SequenceExecutor
    import player.web.session as web_session

    calls = []

    class FakeDriver:
        def __init__(self, tag):
            self.tag = tag

        def execute_script(self, script):
            return 1

    def fake_create(**kwargs):
        calls.append(("create", kwargs))
        return FakeDriver("created")

    def fake_ensure(chain_key=None):
        calls.append(("ensure", chain_key))
        return FakeDriver("chain")

    def fake_find_none(chain_key=None):
        calls.append(("find", chain_key))
        return None

    monkeypatch.setattr(web_session, "create_session", fake_create)
    monkeypatch.setattr(web_session, "ensure_workbench_session", fake_ensure)
    monkeypatch.setattr(web_session, "_find_workbench_browser", fake_find_none)

    # Headless run: durable profile dir, headless honored, own quit at close.
    headless_cfg = type("C", (), {"headless": True})()
    ex = SequenceExecutor(chain_file_dir=str(tmp_path))
    ex.web_profile_key = "chain-xyz"
    d = ex._get_or_create_web_driver(headless_cfg)
    assert d.tag == "created"
    kwargs = calls[-1][1]
    assert kwargs["headless"] is True
    assert os.path.basename(str(kwargs["profile_dir"])) == "chain-xyz"
    assert ex._web_session_released is False
    ex.close_web_session()  # quits; durable profile dir is kept by cleanup

    # Visible run with no live chain browser: launches on the durable dir.
    visible_cfg = type("C", (), {"headless": False})()
    calls.clear()
    ex2 = SequenceExecutor(chain_file_dir=str(tmp_path))
    ex2.web_profile_key = "chain-xyz"
    d2 = ex2._get_or_create_web_driver(visible_cfg)
    assert d2.tag == "created"
    created = [c[1] for c in calls if c[0] == "create"]
    assert created and created[-1]["headless"] is False
    assert os.path.basename(str(created[-1]["profile_dir"])) == "chain-xyz"
    assert ex2._web_session_released is False

    # Visible run with the chain's browser already open: ATTACH, never kill it.
    def fake_find_live(chain_key=None):
        calls.append(("find", chain_key))
        return {"pid": 4242, "port": 9222}

    monkeypatch.setattr(web_session, "_find_workbench_browser", fake_find_live)
    calls.clear()
    ex3 = SequenceExecutor(chain_file_dir=str(tmp_path))
    ex3.web_profile_key = "chain-xyz"
    d3 = ex3._get_or_create_web_driver(visible_cfg)
    assert d3.tag == "chain"
    assert calls[-1] == ("ensure", "chain-xyz")
    assert ex3._web_session_released is True
    ex3.close_web_session()  # attached browser is left open (no cleanup quit)
    assert calls[-1] == ("ensure", "chain-xyz")


def test_match_session_window_prefers_top_most_equal_size_window():
    """With two same-size overlapping windows, the top-most one wins - the
    recording window is never the one killed by the prune backstop."""
    from player.web.session import _match_session_window

    class FakeDriver:
        def execute_cdp_cmd(self, _cmd, _params):
            return {"bounds": {"left": 0, "top": 0,
                               "width": 1000, "height": 700,
                               "windowState": "normal"}}

    # EnumWindows order = z-order (top-most first): the recording window is
    # the focused/top one, so it must be selected over the equal-size extra.
    windows = [
        {"pid": 22, "title": "recording (top)", "rect": (0, 0, 1000, 700)},
        {"pid": 11, "title": "extra (bottom)", "rect": (0, 0, 1000, 700)},
    ]
    assert _match_session_window(FakeDriver(), windows) == 22


# ---------------------------------------------------------------------------
# 9. Consolidation synthesis has no hard evidence cap
# ---------------------------------------------------------------------------


def test_consolidation_synthesis_no_hard_cap(monkeypatch):
    """The final ComoRAG synthesis sends the FULL accumulated facts+evidence
    on the first attempt — no hard 5000-char cap discarding verified evidence
    beyond the tail.  The shrink ladder still fires on rejection (engine 400)
    so the synthesis completes on genuinely tight windows."""
    from player.multi_sequence.llm_executor_resources import executor as exec_mod

    attempts = []

    def fake_completion(prompt, llm_config, api_url, **kw):
        attempts.append(prompt)
        return None  # always reject -> exercises the shrink ladder too

    monkeypatch.setattr(exec_mod, "_llamacpp_raw_completion", fake_completion)

    # Markers sit BEYOND the old first-attempt cap (tail 5000 chars) — with
    # the hard cap they could never reach the synthesis prompt.
    evidence = "HEAD_MARKER_EVIDENCE" + ("A" * 6000)
    facts = "HEAD_MARKER_FACTS" + ("B" * 6000)
    result = exec_mod._generate_consolidated_response(
        "goal", "intent", facts, evidence, {}, "http://localhost:8000")

    assert result is None
    assert len(attempts) >= 2
    # First attempt carries the FULL relevance-filtered pool.
    assert "HEAD_MARKER_EVIDENCE" in attempts[0]
    assert "HEAD_MARKER_FACTS" in attempts[0]
    # Rejection shrinks: the second attempt is capped to the tail 5000.
    assert "HEAD_MARKER_EVIDENCE" not in attempts[1]
    assert "HEAD_MARKER_FACTS" not in attempts[1]



# ---------------------------------------------------------------------------
# 7. Integration purity
# ---------------------------------------------------------------------------


def test_no_external_webversionpw_import():
    """LoOper/ contains no import of the external webversionpw module."""
    looper_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    offenders = []
    pattern = re.compile(r"^\s*(import\s+webversionpw\b|from\s+webversionpw\b)")
    for root, _dirs, files in os.walk(looper_dir):
        if "__pycache__" in root or ".venv" in root:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if pattern.match(line):
                            offenders.append(f"{path}: {line.strip()}")
                            break
            except Exception:
                continue
    assert not offenders, f"External webversionpw imports found:\n{chr(10).join(offenders)}"


# ---------------------------------------------------------------------------
# 8. All-actions recorder: chrome chords, key state, contenteditable, do_chrome
# ---------------------------------------------------------------------------


def test_do_key_chrome_context_routes_to_semantic_chrome_action():
    """A key captured by the OS chrome hook replays as a deterministic semantic
    action (Ctrl+T -> new tab via CDP) instead of a raw key - raw chromedriver
    keys may never reach browser chrome, and the tab-count guards must apply."""
    from player.web import actions as web_actions

    calls = []

    class FakeSwitchTo:
        def window(self, handle):
            calls.append(("switch", handle))

    class FakeDriver:
        current_url = "https://example.com/"
        window_handles = ["h1"]

        def execute_cdp_cmd(self, cmd, params):
            calls.append(("cdp", cmd, params))

        @property
        def switch_to(self):
            return FakeSwitchTo()

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "t", "modifiers": ["Ctrl"], "locator": None,
                          "context": {"chrome": True}})()
    web_actions.do_key(FakeDriver(), evt, cfg)
    assert ("cdp", "Target.createTarget", {"url": "about:blank"}) in calls


def test_do_key_chrome_chord_replays_semantic_and_plain_keys_stay_js():
    """Chrome-affecting chords (Alt+Left back/forward) replay as semantic
    driver actions even without the toggle; plain non-chrome keys keep the
    legacy JS dispatch."""
    from player.web import actions as web_actions

    calls = []

    class FakeSwitchTo:
        def window(self, handle):
            calls.append(("switch", handle))

    class FakeDriver:
        window_handles = ["h1"]

        def __init__(self):
            self.current_url = "https://example.com/"
            self.scripts = []

        def back(self):
            calls.append(("back",))
            self.current_url = "https://example.com/prev"

        def execute_script(self, script, *args):
            self.scripts.append(script)

        @property
        def switch_to(self):
            return FakeSwitchTo()

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()

    # Alt+ArrowLeft is a chrome-affecting chord -> semantic back() without toggle.
    driver = FakeDriver()
    evt_chrome = type("E", (), {"key": "ArrowLeft", "modifiers": ["Alt"],
                                 "locator": None, "context": {}})()
    web_actions.do_key(driver, evt_chrome, cfg)
    assert calls == [("back",)]

    # A plain key without chrome semantics keeps the legacy JS dispatch.
    evt_plain = type("E", (), {"key": "j", "modifiers": [], "locator": None,
                                "context": {}})()
    web_actions.do_key(driver, evt_plain, cfg)
    assert len(driver.scripts) == 1 and "KeyboardEvent" in driver.scripts[0]


def test_do_key_state_up_is_noop(monkeypatch):
    """Key release events are recorded for macro completeness but replay as
    no-ops - every tap presses AND releases its chord atomically."""
    from player.web import actions as web_actions

    called = []

    def boom(*args, **kwargs):
        called.append(1)

    monkeypatch.setattr(web_actions, "ActionChains", boom)
    cfg = type("C", (), {"native_actions": True, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "t", "modifiers": ["Ctrl"], "locator": None,
                          "state": "up", "context": {}})()
    web_actions.do_key(object(), evt, cfg)
    assert called == []


def test_do_key_editing_keys_force_native(monkeypatch):
    """Backspace/Delete/Enter/Tab replay natively even with the toggle off -
    untrusted synthetic key events never perform default actions (deletion,
    form submission, focus move), so the JS path would silently do nothing."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def send_keys(self, key):
            calls.append(("key", key))
            return self

        def perform(self):
            calls.append(("perform",))

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: None)

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    for key, selenium_key in [("Backspace", Keys.BACKSPACE), ("Delete", Keys.DELETE),
                              ("Enter", Keys.ENTER), ("Tab", Keys.TAB),
                              ("ArrowLeft", Keys.ARROW_LEFT), (" ", Keys.SPACE)]:
        calls.clear()
        evt = type("E", (), {"key": key, "modifiers": [], "locator": None,
                              "context": {}})()
        web_actions.do_key(object(), evt, cfg)
        assert calls == [("key", selenium_key), ("perform",)], key


def test_do_key_lone_modifier_replays_as_native_tap(monkeypatch):
    """A lone Shift/Ctrl press records and replays as a clean native
    press+release tap - untrusted synthetic events never register modifiers."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def perform(self):
            calls.append(("perform",))

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "Shift", "modifiers": [], "locator": None,
                          "context": {}})()
    web_actions.do_key(object(), evt, cfg)
    assert calls == [("down", Keys.SHIFT), ("up", Keys.SHIFT), ("perform",)]


def test_do_key_modifier_combo_letters_force_native(monkeypatch):
    """Ctrl+A/C/V/X/Z (select-all/copy/cut/paste) replay natively - synthetic
    events never trigger these browser defaults."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def send_keys(self, key):
            calls.append(("key", key))
            return self

        def perform(self):
            calls.append(("perform",))

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: None)

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "a", "modifiers": ["Ctrl"], "locator": None,
                          "context": {}})()
    web_actions.do_key(object(), evt, cfg)
    assert calls == [("down", Keys.CONTROL), ("key", "a"),
                     ("up", Keys.CONTROL), ("perform",)]


def test_do_key_editing_key_with_modifiers_holds_ctrl(monkeypatch):
    """Ctrl+Backspace replays with the modifier held natively (word delete)."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def send_keys(self, key):
            calls.append(("key", key))
            return self

        def perform(self):
            calls.append(("perform",))

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: None)

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "Backspace", "modifiers": ["Ctrl"],
                          "locator": None, "context": {}})()
    web_actions.do_key(object(), evt, cfg)
    assert calls == [("down", Keys.CONTROL), ("key", Keys.BACKSPACE),
                     ("up", Keys.CONTROL), ("perform",)]


def test_do_key_editing_key_never_moves_to_recorded_locator(monkeypatch):
    """Keys always replay at the element focused at replay time - like a
    desktop sequence.  Even a RESOLVABLE recorded locator never triggers
    move_to_element: web sequences compose, so a key from a later node must
    land where the PREVIOUS node left focus, not on a stale re-resolved
    target from the recording."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def send_keys(self, key):
            calls.append(("key", key))
            return self

        def move_to_element(self, el):
            calls.append(("move",))
            return self

        def perform(self):
            calls.append(("perform",))

    def ok_resolve(d, e, t):
        return object()  # the recorded element WOULD resolve - still no move

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", ok_resolve)

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "Backspace", "modifiers": [], "locator": object(),
                          "context": {}})()
    web_actions.do_key(object(), evt, cfg)
    assert calls == [("key", Keys.BACKSPACE), ("perform",)]  # no move_to_element


def test_do_key_editing_key_unresolvable_locator_still_replays(monkeypatch):
    """A locator that cannot be resolved (shadow DOM, stale page) never blocks
    the key - it replays natively to the focused element (no move)."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def send_keys(self, key):
            calls.append(("key", key))
            return self

        def move_to_element(self, el):
            calls.append(("move",))
            return self

        def perform(self):
            calls.append(("perform",))

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("shadow element")

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)

    cfg = type("C", (), {"native_actions": False, "element_timeout": 15.0})()
    evt = type("E", (), {"key": "Backspace", "modifiers": [], "locator": object(),
                          "context": {}})()
    web_actions.do_key(object(), evt, cfg)
    assert calls == [("key", Keys.BACKSPACE), ("perform",)]  # no move_to_element


def test_do_type_contenteditable_uses_exec_command(monkeypatch):
    """Typing into a contenteditable host replays through the caret-aware
    execCommand insertText branch, not the input/textarea value setter."""
    from player.web import actions as web_actions

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append(script)

    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: "el")
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)

    cfg = type("C", (), {"element_timeout": 15.0, "native_actions": False})()
    evt = type("E", (), {"value": "hello", "locator": object()})()
    web_actions.do_type(FakeDriver(), evt, cfg)
    assert executed and "execCommand" in executed[0]
    assert "insertText" in executed[0]


def test_do_type_falls_back_to_shadow_piercing_dispatch(monkeypatch):
    """When WebDriver cannot resolve the locator (elements inside shadow roots
    are invisible to find_elements), typing replays through the in-browser
    insert - preferring the currently focused element, then the recorded css
    (tag-restricted, so it cannot match a shadow HOST sharing the id)."""
    from player.web import actions as web_actions

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))
            return True

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("shadow element")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)

    cfg = type("C", (), {"element_timeout": 15.0, "native_actions": False})()
    evt = type("E", (), {"value": "aaa",
                          "locator": web_actions.Locator(id="input", css="input#input")})()
    web_actions.do_type(FakeDriver(), evt, cfg)
    assert len(executed) == 1
    script, args = executed[0]
    assert script is web_actions.JS_TYPE_DOM
    # the explicit css candidate wins over the bare id (which matches the host)
    assert args == (["input#input", "#input"], "aaa", None, None)
    assert "deepestActive" in script and "isEditable" in script


def test_do_type_types_at_focused_element_when_target_gone(monkeypatch):
    """A recorded type whose element no longer exists still writes when an
    editable has focus at replay time (desktop-sequence semantics: hover/click
    failures before the typing must not cancel the writing).  Bare trusted
    keys land in the focused element - shadow roots included."""
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def send_keys(self, text):
            calls.append(("send_keys", text))
            return self

        def perform(self):
            calls.append(("perform",))

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("recorded element gone")

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)
    monkeypatch.setattr(web_actions, "_focused_editable", lambda d: True)

    cfg = type("C", (), {"element_timeout": 15.0, "native_actions": True})()
    evt = type("E", (), {"value": "hello", "locator": object()})()
    assert web_actions.do_type(object(), evt, cfg) == "native-focus"
    assert calls == [("send_keys", "hello"), ("perform",)]
    # no move_to_element / element round-trip anywhere
    assert all(kind == "send_keys" or kind == "perform" for kind, *_ in calls)


def test_do_type_no_focused_editable_keeps_shadow_fallback(monkeypatch):
    """Native-first focused typing only applies when an editable is actually
    focused; otherwise the in-browser shadow dispatch still runs (a recorded
    element inside a shadow root resolves there even with nothing focused)."""
    from player.web import actions as web_actions

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))
            return True

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("gone")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)
    monkeypatch.setattr(web_actions, "_focused_editable", lambda d: False)

    cfg = type("C", (), {"element_timeout": 15.0, "native_actions": True})()
    evt = type("E", (), {"value": "aaa",
                          "locator": web_actions.Locator(id="input", css="input#input")})()
    assert web_actions.do_type(FakeDriver(), evt, cfg) == "js-shadow"
    assert executed and executed[0][0] is web_actions.JS_TYPE_DOM


def test_do_type_selectorless_locator_still_types_at_focus(monkeypatch):
    """A type with no css candidate used to die before the in-browser fallback
    ran; JS_TYPE_DOM types at the focused editable without a selector."""
    from player.web import actions as web_actions

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))
            return True

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("gone")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)
    monkeypatch.setattr(web_actions, "_focused_editable", lambda d: False)

    cfg = type("C", (), {"element_timeout": 15.0, "native_actions": False})()
    evt = type("E", (), {"value": "aaa", "locator": None})()
    assert web_actions.do_type(FakeDriver(), evt, cfg) == "js-shadow"
    assert executed == [(web_actions.JS_TYPE_DOM, ([], "aaa", None, None))]


def test_climb_clickable_walks_to_button_ancestor(monkeypatch):
    """After a trusted click fails on an inert inner span/div, the clickable
    ancestor (button/a/submit/role=button) is located for a retry."""
    from player.web import actions as web_actions

    recorded = []

    class FakeDriver:
        def execute_script(self, script, el):
            recorded.append((script, el))
            return "button-el"

    assert web_actions._climb_clickable(FakeDriver(), "span-el") == "button-el"
    assert recorded[0][1] == "span-el"
    assert "parentElement" in recorded[0][0]
    assert web_actions._climb_clickable(FakeDriver(), None) is None

    class Boom:
        def execute_script(self, *a):
            raise RuntimeError("driver dead")

    assert web_actions._climb_clickable(Boom(), "el") is None


def test_do_click_climbs_to_button_when_direct_click_fails(monkeypatch):
    """A recorded click on a button's inert inner element (Google Material
    buttons are span stacks - "element not interactable: has no size and
    location") retries a TRUSTED click on the clickable ancestor before the JS
    fallback, so default actions (submit/navigation) still run."""
    from selenium.common.exceptions import ElementNotInteractableException
    from player.web import actions as web_actions
    from player.web.handlers import pointer as pointer_mod

    clicked = []

    def fake_click(driver, element, modifiers, kind="click"):
        if element == "span-el":
            raise ElementNotInteractableException("no size and location")
        clicked.append(element)

    monkeypatch.setattr(pointer_mod, "_try_resolve_element",
                        lambda d, e, c: "span-el")
    monkeypatch.setattr(pointer_mod, "_click_with_modifiers", fake_click)
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)
    monkeypatch.setattr(web_actions, "_climb_clickable",
                        lambda d, e: "button-el")

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = type("E", (), {"locator": object(), "button": "0",
                          "coordinates": None, "modifiers": []})()
    assert web_actions.do_click(object(), evt, cfg) == "native"
    assert clicked == ["button-el"]

    # No clickable ancestor: the failure propagates (the ladder's next rung -
    # the JS dispatch - reports its own last error).
    monkeypatch.setattr(web_actions, "_climb_clickable", lambda d, e: None)
    import pytest
    with pytest.raises(Exception):
        web_actions.do_click(object(), evt, cfg)
    assert clicked == ["button-el"]  # only the successful ancestor click landed


def test_do_type_js_inserts_when_send_keys_fails_on_hidden_field(monkeypatch):
    """A recorded field that exists but is hidden/covered (Google keeps the
    password form hidden until the flow reveals it) makes trusted send_keys
    raise "element not interactable" - the value is inserted through the
    in-browser setter instead of losing the typing."""
    from selenium.common.exceptions import ElementNotInteractableException
    from player.web import actions as web_actions

    class FakeElement:
        def send_keys(self, value):
            raise ElementNotInteractableException("hidden input")

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))
            return True

    monkeypatch.setattr(web_actions, "resolve_element",
                        lambda d, e, t: FakeElement())
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)

    cfg = type("C", (), {"element_timeout": 15.0, "native_actions": True})()
    evt = type("E", (), {"value": "secret", "locator": object()})()
    assert web_actions.do_type(FakeDriver(), evt, cfg) == "js-hidden"
    assert executed and executed[0][0] is web_actions.JS_TYPE
    assert executed[0][1][1] == "secret"


def test_do_focus_reresolves_after_stale_render(monkeypatch):
    """A focus whose element goes stale mid-action (Google's sign-in advances
    and rebuilds the form) re-resolves ONCE against the fresh DOM instead of
    failing the whole action."""
    from selenium.common.exceptions import StaleElementReferenceException
    from player.web import actions as web_actions

    class Stale:
        def click(self):
            raise StaleElementReferenceException("stale element")

    class Fresh:
        def click(self):
            pass

    class FakeDriver:
        def execute_script(self, script, *args):
            raise StaleElementReferenceException("stale element")

    resolves = iter([Stale(), Fresh()])
    monkeypatch.setattr(web_actions, "resolve_element",
                        lambda d, e, t: next(resolves))
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = type("E", (), {"locator": object()})()
    assert web_actions.do_focus(FakeDriver(), evt, cfg) == "native-retry"


def test_first_css_selector_prefers_explicit_css_over_derived_id():
    """A bare id can match a shadow HOST sharing the id (cr-searchbox-input#input
    on the new-tab page) - the recorded explicit css must win."""
    from player.web import actions as web_actions
    from player.web.events import Event

    evt = Event(type="type", ts=1.0, locator=web_actions.Locator(id="input", css="input#input"))
    assert web_actions._first_css_selector(evt) == "input#input"

    # No explicit css: falls back to the derived id selector (unchanged path).
    evt2 = Event(type="type", ts=1.0, locator=web_actions.Locator(id="input"))
    assert web_actions._first_css_selector(evt2) == "#input"

    # No locator at all.
    assert web_actions._first_css_selector(Event(type="type", ts=1.0)) is None


def test_do_type_raises_when_shadow_fallback_finds_nothing(monkeypatch):
    """If neither the native resolve nor the shadow-piercing dispatch finds a
    target, the typing failure is loud - never a silent no-op."""
    from player.web import actions as web_actions
    import pytest

    class FakeDriver:
        def execute_script(self, script, *args):
            return False

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("none")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)

    cfg = type("C", (), {"element_timeout": 15.0, "native_actions": False})()
    evt = type("E", (), {"value": "aaa", "locator": web_actions.Locator(id="input")})()
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions.do_type(FakeDriver(), evt, cfg)


def test_do_focus_falls_back_to_shadow_piercing_dispatch(monkeypatch):
    """A focus target inside a shadow root (Google login's identifierId under
    c-wiz) is unreachable by WebDriver - focus through the in-browser deep
    find instead of failing."""
    from player.web import actions as web_actions

    executed = []

    class FakeDriver:
        def execute_script(self, script, *args):
            executed.append((script, args))
            return True

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("shadow element")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = type("E", (), {"locator": web_actions.Locator(id="identifierId",
                                                          css="input#identifierId")})()
    web_actions.do_focus(FakeDriver(), evt, cfg)
    assert len(executed) == 1
    script, args = executed[0]
    assert script is web_actions.JS_FOCUS_DOM
    assert args == (["input#identifierId", "#identifierId"],)


def test_do_focus_raises_when_shadow_fallback_finds_nothing(monkeypatch):
    """If neither the native resolve nor the shadow-piercing dispatch finds the
    focus target, the failure is loud - never a silent no-op."""
    from player.web import actions as web_actions
    import pytest

    class FakeDriver:
        def execute_script(self, script, *args):
            return False

    def fail_resolve(d, e, t):
        raise web_actions.ElementNotFoundError("none")

    monkeypatch.setattr(web_actions, "resolve_element", fail_resolve)

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = type("E", (), {"locator": web_actions.Locator(id="identifierId")})()
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions.do_focus(FakeDriver(), evt, cfg)


def test_js_dom_pointer_focuses_before_click_dispatch():
    """JS-dispatched clicks focus the element explicitly - synthetic events do
    not trigger the default focus behaviour, so a following type action would
    otherwise land on an unfocused element."""
    from player.web import actions as web_actions

    assert "el.focus({preventScroll:true})" in web_actions.JS_DOM_POINTER
    assert "mode !== 'hover'" in web_actions.JS_DOM_POINTER


def test_do_click_applies_recorded_modifiers_natively(monkeypatch):
    """Ctrl+click / Shift+click macros replay with the modifiers held around
    the native click (key_down -> move -> click -> key_up)."""
    from selenium.webdriver.common.keys import Keys
    from player.web import actions as web_actions

    calls = []

    class FakeActionChains:
        def __init__(self, driver):
            pass

        def key_down(self, key):
            calls.append(("down", key))
            return self

        def key_up(self, key):
            calls.append(("up", key))
            return self

        def move_to_element(self, el):
            calls.append(("move",))
            return self

        def click(self):
            calls.append(("click",))
            return self

        def perform(self):
            calls.append(("perform",))

    monkeypatch.setattr(web_actions, "ActionChains", FakeActionChains)
    monkeypatch.setattr(web_actions, "resolve_element", lambda d, e, t: "el")
    monkeypatch.setattr(web_actions, "_scroll_into_view", lambda d, e: None)

    cfg = type("C", (), {"element_timeout": 15.0})()
    evt = type("E", (), {"locator": object(), "button": "0", "coordinates": None,
                          "frame_path": [], "cross_origin_frame": False,
                          "modifiers": ["Ctrl"]})()
    web_actions.do_click(object(), evt, cfg)
    assert calls == [("down", Keys.CONTROL), ("move",), ("click",),
                     ("up", Keys.CONTROL), ("perform",)]


def test_do_chrome_dispatches_semantic_actions():
    """Browser-chrome interactions replay as semantic driver/CDP actions, never
    raw coordinates, so replay survives DPI and window-size drift."""
    from player.web import actions as web_actions
    from player.web.events import Event

    calls = []

    class FakeSwitchTo:
        def window(self, handle):
            calls.append(("switch", handle))

    class FakeDriver:
        window_handles = ["h1", "h2", "h3"]

        def __init__(self):
            self.current_url = "https://example.com/"

        def back(self):
            calls.append(("back",))
            self.current_url = "https://example.com/prev"

        def forward(self):
            calls.append(("forward",))
            self.current_url = "https://example.com/next"

        def refresh(self):
            calls.append(("refresh",))

        def get(self, url):
            calls.append(("get", url))

        def close(self):
            calls.append(("close",))

        def execute_cdp_cmd(self, cmd, params):
            calls.append(("cdp", cmd, params))

        @property
        def switch_to(self):
            return FakeSwitchTo()

    cfg = None

    d = FakeDriver()
    web_actions.do_chrome(d, Event(type="chrome", ts=1.0, action="back"), cfg)
    assert calls[-1] == ("back",)

    d = FakeDriver()
    web_actions.do_chrome(d, Event(type="chrome", ts=1.0, action="reload"), cfg)
    assert calls[-1] == ("refresh",)

    d = FakeDriver()
    web_actions.do_chrome(d, Event(type="chrome", ts=1.0, action="new_tab"), cfg)
    assert ("cdp", "Target.createTarget", {"url": "about:blank"}) in calls
    assert calls[-1] == ("switch", "h3")

    d = FakeDriver()
    web_actions.do_chrome(d, Event(type="chrome", ts=1.0, action="close_tab"), cfg)
    assert ("close",) in calls
    assert calls[-1] == ("switch", "h3")

    d = FakeDriver()
    web_actions.do_chrome(d, Event(type="chrome", ts=1.0, action="switch_tab", value="1"), cfg)
    assert calls[-1] == ("switch", "h2")

    # Unknown actions fail loudly (per-action error), never silently.
    import pytest

    with pytest.raises(ValueError):
        web_actions.do_chrome(FakeDriver(), Event(type="chrome", ts=1.0, action="nope"), cfg)


def test_do_chrome_back_falls_back_to_recorded_url():
    """On a fresh replay browser with no history, back/forward re-navigate to
    the recorded URL of the neighbouring action instead of silently no-opping."""
    from player.web import actions as web_actions
    from player.web.events import Event

    calls = []

    class FakeDriver:
        current_url = "https://example.com/page"

        def back(self):
            calls.append(("back",))

        def get(self, url):
            calls.append(("get", url))

    evt = Event(type="chrome", ts=1.0, action="back",
                context={"chrome": True, "fallback_url": "https://example.com/prev"})
    web_actions.do_chrome(FakeDriver(), evt, None)
    assert calls == [("back",), ("get", "https://example.com/prev")]


def test_engine_skips_release_guard_for_pure_js_run(monkeypatch):
    """A pure-JS replay (no chrome keys, native toggle off) never presses native
    modifiers, so the release guard is skipped - no extra WebDriver traffic."""
    from player.web import engine as web_engine
    from player.web.events import Event

    class FakeDriver:
        current_url = "https://example.com/"

        def get(self, url):
            pass

    release_calls = []
    monkeypatch.setattr(web_engine.actions, "release_all_keys",
                        lambda driver: release_calls.append(driver))

    def boom(driver, evt, config):
        raise RuntimeError("boom")

    engine = web_engine.ReplayEngine(
        FakeDriver(), web_engine.ReplayConfig(min_pause=0.0, max_pause=0.0, native_actions=False)
    )
    monkeypatch.setitem(web_engine._DISPATCHERS, "boom", boom)
    stats = engine.run([Event(type="boom", ts=1.0)])
    assert stats["failed"][0]["type"] == "boom"
    assert release_calls == []  # no native keys pressed -> no release traffic


def test_engine_skips_key_up_events_and_release_guard(monkeypatch):
    """The replay engine skips recorded key-release events and always releases
    native modifiers after a run that used them (aborted replays never leave
    keys stuck)."""
    from player.web import engine as web_engine
    from player.web.events import Event

    class FakeDriver:
        current_url = "https://example.com/"

        def get(self, url):
            pass

    release_calls = []
    monkeypatch.setattr(web_engine.actions, "release_all_keys",
                        lambda driver: release_calls.append(driver))

    def boom(driver, evt, config):
        raise RuntimeError("boom")

    engine = web_engine.ReplayEngine(
        FakeDriver(),
        web_engine.ReplayConfig(min_pause=0.0, max_pause=0.0, native_actions=True),
    )
    monkeypatch.setitem(web_engine._DISPATCHERS, "boom", boom)
    stats = engine.run([
        Event(type="key", ts=1.0, key="t", modifiers=["Ctrl"], state="up"),
        Event(type="boom", ts=2.0),
    ])
    assert stats["attempted"] == 1  # key-up skipped, boom attempted
    assert stats["failed"][0]["type"] == "boom"
    assert len(release_calls) == 1  # native run -> release guard fires


def test_event_roundtrip_additive_fields(tmp_path):
    """state/repeat/action survive the session round-trip; legacy dicts without
    them load with safe defaults."""
    from player.web.events import Event, load_session, save_session

    path = str(tmp_path / "web" / "additive.json")
    save_session([
        Event(type="key", ts=1.0, key="t", modifiers=["Ctrl"], state="down"),
        Event(type="chrome", ts=2.0, action="back", context={"chrome": True}),
    ], path)
    data = load_session(path)
    key_evt, chrome_evt = data["actions"]
    assert key_evt.state == "down" and key_evt.repeat is None
    assert chrome_evt.action == "back" and chrome_evt.context == {"chrome": True}

    legacy = Event.from_dict({"type": "key", "ts": 1.0, "key": "x",
                              "modifiers": ["Ctrl"]})
    assert legacy.state is None and legacy.repeat is None and legacy.action is None


# ---------------------------------------------------------------------------
# 9. Resilient interpreter: ESC cleanup, window-loss reconnect, method reports
# ---------------------------------------------------------------------------


def test_recorder_cleans_escape_and_control_events():
    """The stop chord's keyups and internal control signals never reach the
    saved session - only real user actions survive the final clean (sessions
    of pure Escape keyups used to replay as "0/0 actions OK")."""
    from player.web.events import Event
    from player.web.recorder import _clean_actions

    actions = [
        Event(type="stop", ts=1.0),
        Event(type="bridge_ready", ts=2.0),
        Event(type="key", ts=3.0, key="Escape", state="up"),
        Event(type="key", ts=4.0, key="Escape", state="down"),
        Event(type="click", ts=5.0),
        Event(type="key", ts=6.0, key="t", state="down"),
    ]
    cleaned = _clean_actions(actions)
    assert [a.type for a in cleaned] == ["click", "key"]
    assert cleaned[1].key == "t"


def test_recorder_warns_when_only_escape_noise(caplog):
    """A session that captured nothing but ESC keyups warns loudly instead of
    silently saving an empty file."""
    import logging
    from player.web.events import Event
    from player.web.recorder import _clean_actions

    with caplog.at_level(logging.WARNING, logger="player.web.recorder"):
        cleaned = _clean_actions(
            [Event(type="key", ts=1.0, key="Escape", state="up")]
        )
    assert cleaned == []
    assert any("No real actions captured" in r.message for r in caplog.records)


def test_recorder_drops_chrome_internal_page_actions(caplog):
    """Actions recorded inside Chrome-internal documents (the new-tab search
    box, tiles) never replay against a real site - they are dropped so a
    session cannot mix WebUI junk with real-page actions (which made replays
    click/type into random page state).  Real-page actions and OS-captured
    chrome events (no url) survive; an all-internal session becomes empty."""
    import logging
    from player.web.events import Event, Locator
    from player.web.recorder import _drop_chrome_internal_actions

    newtab_click = Event(
        type="click", ts=1.0, url="chrome://new-tab-page/",
        locator=Locator(tag="textarea", id="input"),
    )
    newtab_type = Event(
        type="type", ts=2.0, value="lin", url="chrome://new-tab-page/",
        locator=Locator(tag="textarea", id="input"),
    )
    site_click = Event(
        type="click", ts=3.0, url="https://www.linkedin.com/feed/",
        locator=Locator(tag="span", text="Vishal Panchal"),
    )
    os_enter = Event(
        type="key", ts=4.0, key="Enter", state="down",
        context={"chrome": True},
    )
    kept = _drop_chrome_internal_actions(
        [newtab_click, newtab_type, site_click, os_enter]
    )
    assert kept == [site_click, os_enter]

    with caplog.at_level(logging.INFO, logger="player.web.recorder"):
        empty = _drop_chrome_internal_actions([newtab_click, newtab_type])
    assert empty == []
    assert any("Chrome-internal pages" in r.message for r in caplog.records)


def _cold_start_events():
    """A recording that began on the default new-tab page: partial mirrored
    typing in the new-tab search box (the "missing keys"), an Enter submit,
    then real actions on the site the browser landed on."""
    from player.web.events import Event, Locator

    ntb = "chrome://new-tab-page/"
    return [
        Event(type="hover", ts=1000.0, url=ntb,
              locator=Locator(tag="div", text="colors")),
        Event(type="click", ts=1100.0, url=ntb,
              locator=Locator(tag="textarea", id="input")),
        Event(type="type", ts=1200.0, value="li", url=ntb,
              locator=Locator(tag="textarea", id="input")),
        Event(type="type", ts=1300.0, value="d", url=ntb,
              locator=Locator(tag="textarea", id="input")),
        Event(type="type", ts=1400.0, value=".co", url=ntb,
              locator=Locator(tag="textarea", id="input")),
        Event(type="key", ts=1500.0, key="Enter", state="down", url=ntb),
        Event(type="click", ts=6000.0, url="https://www.linkedin.com/feed/",
              locator=Locator(tag="span", text="Vishal")),
    ]


def test_recorder_collapses_cold_start_noise_into_navigate():
    """The address-bar / new-tab-search era of a recording (partial mirrored
    text - the user's "missing keys" - plus WebUI clicks) is replaced by ONE
    explicit navigate to the page the user landed on, so the session replays
    as: open site, then the real element actions."""
    from player.web.events import Event
    from player.web.recorder import _finalize_actions

    events = _cold_start_events()
    final = _finalize_actions(events, saw_chrome_enter=False, final_url=None)
    assert [e.type for e in final] == ["navigate", "click"]
    assert final[0].url == "https://www.linkedin.com/feed/"
    assert final[0].ts < final[1].ts
    assert final[1].locator.text == "Vishal"


def test_recorder_session_starting_on_real_page_never_navigates():
    """A recording that began on the parked workbench browser (first action
    already on the real site) is untouched - no implicit navigation is added,
    so element-driven chains and node-to-node continuity stay intact."""
    from player.web.events import Event, Locator
    from player.web.recorder import _finalize_actions

    events = [
        Event(type="click", ts=1000.0, url="https://www.linkedin.com/feed/",
              locator=Locator(tag="a", id="post")),
        Event(type="key", ts=2000.0, key="Enter", state="down",
              url="https://www.linkedin.com/feed/"),
    ]
    final = _finalize_actions(events, saw_chrome_enter=False, final_url=None)
    assert [e.type for e in final] == ["click", "key"]


def test_recorder_ctrl_l_prefix_does_not_fake_a_cold_start():
    """A lone Ctrl+L (address-bar focus, no navigation) before real actions on
    the same parked page is not a cold start - the session is left as-is."""
    from player.web.events import Event, Locator
    from player.web.recorder import _finalize_actions

    events = [
        Event(type="key", ts=1000.0, key="l", modifiers=["Ctrl"],
              state="down", context={"chrome": True}),
        Event(type="click", ts=2000.0, url="https://example.com/a",
              locator=Locator(tag="button", id="go")),
    ]
    final = _finalize_actions(events, saw_chrome_enter=False, final_url=None)
    assert [e.type for e in final] == ["key", "click"]


def test_finalize_synthesizes_navigation_after_chrome_enter(caplog):
    """A session that ends with only the chrome Enter of an address-bar
    navigation (typed URL invisible) becomes one navigate to the page the
    browser ended on - never an empty session."""
    import logging
    from player.web.events import Event
    from player.web.recorder import _finalize_actions

    events = [Event(type="key", ts=1000.0, key="Enter", state="down",
                    context={"chrome": True})]
    with caplog.at_level(logging.INFO, logger="player.web.recorder"):
        final = _finalize_actions(
            events, saw_chrome_enter=True,
            final_url="https://www.linkedin.com/feed/",
        )
    assert [e.type for e in final] == ["navigate"]
    assert final[0].url == "https://www.linkedin.com/feed/"


def test_finalize_empties_bare_chrome_noise_with_warning(caplog):
    """Browser-chrome noise that never reached a real page (no navigation
    destination, or no navigation at all) stays an empty session with a loud
    warning instead of replaying stray Enter/Ctrl+T presses."""
    import logging
    from player.web.events import Event
    from player.web.recorder import _finalize_actions

    with caplog.at_level(logging.WARNING, logger="player.web.recorder"):
        final = _finalize_actions(
            [Event(type="key", ts=1000.0, key="Enter", state="down",
                   context={"chrome": True})],
            saw_chrome_enter=True, final_url="chrome://new-tab-page/",
        )
    assert final == []
    assert any("No replayable actions captured" in r.message
               for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="player.web.recorder"):
        final = _finalize_actions(
            [Event(type="key", ts=1000.0, key="t", modifiers=["Ctrl"],
                   state="down", context={"chrome": True})],
            saw_chrome_enter=False, final_url=None,
        )
    assert final == []
    assert any("No replayable actions captured" in r.message
               for r in caplog.records)


def test_finalize_saves_navigation_when_started_on_default_page(caplog):
    """The user's real cold-start flow: recording starts on the browser's
    default (new-tab) page, the user navigates through the address bar (only
    Backspace / key noise is captured - the typed URL is invisible), lands on
    a site and stops.  The observable transition - started internal, ended on
    a real page - must save one navigate even though NO chrome Enter was
    captured and NO in-page action happened."""
    import logging
    from player.web.events import Event
    from player.web.recorder import _finalize_actions

    events = [
        Event(type="key", ts=1000.0, key="Backspace", state="down",
              context={"chrome": True}),
        Event(type="key", ts=2000.0, key="Backspace", state="down",
              context={"chrome": True}),
        Event(type="hover", ts=3000.0, url="chrome://new-tab-page/"),
    ]
    with caplog.at_level(logging.INFO, logger="player.web.recorder"):
        final = _finalize_actions(
            events, saw_chrome_enter=False,
            final_url="https://www.linkedin.com/feed/",
            initial_url="chrome://new-tab-page/",
        )
    assert [e.type for e in final] == ["navigate"]
    assert final[0].url == "https://www.linkedin.com/feed/"


def test_finalize_keeps_empty_when_parked_page_never_interacted(caplog):
    """Same noise, but the browser was ALREADY on a real page (parked
    workbench): no navigation happened, so the session stays empty - the
    parked-browser continuity contract must not fabricate a navigate."""
    import logging
    from player.web.events import Event
    from player.web.recorder import _finalize_actions

    events = [Event(type="key", ts=1000.0, key="Backspace", state="down",
                    context={"chrome": True})]
    with caplog.at_level(logging.WARNING, logger="player.web.recorder"):
        final = _finalize_actions(
            events, saw_chrome_enter=False,
            final_url="https://www.linkedin.com/feed/",
            initial_url="https://www.linkedin.com/feed/",
        )
    assert final == []
    assert any("No replayable actions captured" in r.message
               for r in caplog.records)


def test_recorder_collapses_mid_session_address_bar_navigation():
    """An address-bar era BETWEEN two real pages (after the session already
    started on a site) is collapsed into one navigate to the new page, so a
    multi-page session replays as: page A actions -> open page B -> page B
    actions, instead of stray native Ctrl+L/Enter keys."""
    from player.web.events import Event, Locator
    from player.web.recorder import _finalize_actions

    events = [
        Event(type="click", ts=1000.0, url="https://example.com/a",
              locator=Locator(tag="button", id="a")),
        Event(type="key", ts=2000.0, key="l", modifiers=["Ctrl"],
              state="down", context={"chrome": True}),
        Event(type="key", ts=2100.0, key="Enter", state="down",
              context={"chrome": True}),
        Event(type="click", ts=3000.0, url="https://example.com/b",
              locator=Locator(tag="button", id="b")),
    ]
    final = _finalize_actions(events)
    assert [e.type for e in final] == ["click", "navigate", "click"]
    assert final[0].url == "https://example.com/a"
    assert final[1].url == "https://example.com/b"
    assert final[2].url == "https://example.com/b"


def test_recorder_dedupes_legacy_os_key_without_state():
    """OS key events that predate the state stamp (or lost it) still match
    their in-page 'down' twin: a missing state means a press, so the twin is
    dropped instead of recording the key twice."""
    from player.web.events import Event
    from player.web.recorder import _dedupe_os_chrome_keys

    page_enter = Event(type="key", ts=1000.0, key="Enter", state="down",
                       modifiers=[])
    legacy_os = Event(type="key", ts=1005.0, key="Enter",
                      modifiers=[], context={"chrome": True})
    kept = _dedupe_os_chrome_keys([page_enter, legacy_os])
    assert kept == [page_enter]


def test_recorder_dedupes_os_chrome_key_with_page_twin():
    """An OS-captured bare key that also reached the page is dropped: the page
    recorder owns it (and may carry locator/frame info from older sessions),
    so replay presses the key once, not twice.  Dedupe matches on
    key+state+modifiers, never on the locator."""
    from player.web.events import Event, Locator
    from player.web.recorder import _dedupe_os_chrome_keys

    page_down = Event(type="key", ts=1000.0, key="Enter", state="down",
                      modifiers=[], locator=Locator(tag="textarea", id="input"))
    os_down = Event(type="key", ts=1005.0, key="Enter", state="down",
                    modifiers=[], context={"chrome": True})
    page_up = Event(type="key", ts=1100.0, key="Enter", state="up",
                    modifiers=[], locator=Locator(tag="textarea", id="input"))
    os_up = Event(type="key", ts=1105.0, key="Enter", state="up",
                  modifiers=[], context={"chrome": True})

    kept = _dedupe_os_chrome_keys([page_down, os_down, page_up, os_up])
    assert kept == [page_down, page_up]


def test_recorder_keeps_chrome_only_key_without_page_twin():
    """A bare Enter typed while the browser chrome (omnibox) has focus has NO
    page twin - it must survive dedupe so the user's recorded key replays."""
    from player.web.events import Event
    from player.web.recorder import _dedupe_os_chrome_keys

    os_down = Event(type="key", ts=1000.0, key="Enter", state="down",
                    modifiers=[], context={"chrome": True})
    kept = _dedupe_os_chrome_keys([os_down])
    assert kept == [os_down]


def test_recorder_dedupe_respects_key_state_and_window():
    """Different keys/states or events outside the time window are never
    conflated - an unmatched OS event always survives."""
    from player.web.events import Event
    from player.web.recorder import _dedupe_os_chrome_keys

    page_enter = Event(type="key", ts=1000.0, key="Enter", state="down",
                       modifiers=[])
    os_tab = Event(type="key", ts=1005.0, key="Tab", state="down",
                   modifiers=[], context={"chrome": True})
    os_up = Event(type="key", ts=1010.0, key="Enter", state="up",
                  modifiers=[], context={"chrome": True})
    os_late = Event(type="key", ts=2000.0, key="Enter", state="down",
                    modifiers=[], context={"chrome": True})

    kept = _dedupe_os_chrome_keys([page_enter, os_tab, os_up, os_late])
    assert kept == [page_enter, os_tab, os_up, os_late]


def test_recorder_dedupe_never_drops_page_events():
    """Dedupe only removes OS twins - page-captured key events always survive,
    even when no OS twin exists at all."""
    from player.web.events import Event, Locator
    from player.web.recorder import _dedupe_os_chrome_keys

    page_enter = Event(type="key", ts=1000.0, key="Enter", state="down",
                       modifiers=[], locator=Locator(tag="textarea", id="input"))
    page_up = Event(type="key", ts=1100.0, key="Enter", state="up",
                    modifiers=[], locator=Locator(tag="textarea", id="input"))
    kept = _dedupe_os_chrome_keys([page_enter, page_up])
    assert kept == [page_enter, page_up]


def test_engine_reconnects_after_window_loss(monkeypatch):
    """A dead browser window ("no such window") triggers ONE reconnect via
    the driver factory and a retry of the same action - not a cascade of
    failures for every remaining action."""
    from player.web import engine as web_engine
    from player.web.events import Event

    calls = {"dispatches": 0, "factory": 0}

    class FakeDriver:
        current_url = "https://example.com/"

        def get(self, url):
            pass

    def boom(driver, evt, config):
        calls["dispatches"] += 1
        if calls["dispatches"] == 1:
            raise web_engine.NoSuchWindowException(
                "no such window: target window already closed"
            )
        return {"ok": True, "method": "native", "error": None, "took": 0.1}

    def factory():
        calls["factory"] += 1
        return FakeDriver()

    engine = web_engine.ReplayEngine(
        FakeDriver(),
        web_engine.ReplayConfig(min_pause=0.0, max_pause=0.0),
        driver_factory=factory,
    )
    monkeypatch.setitem(web_engine._DISPATCHERS, "boom", boom)
    stats = engine.run([Event(type="boom", ts=1.0)])
    assert calls["dispatches"] == 2 and calls["factory"] == 1
    assert stats["ok"] == 1 and stats["attempted"] == 1
    assert stats["failed"] == []


def test_engine_does_not_reconnect_on_ordinary_failures(monkeypatch):
    """Only a lost window triggers the reconnect - an ordinary action failure
    must never spawn a replacement browser."""
    from player.web import engine as web_engine
    from player.web.events import Event

    calls = {"factory": 0}

    class FakeDriver:
        pass

    def boom(driver, evt, config):
        raise RuntimeError("element missing")

    def factory():
        calls["factory"] += 1
        return FakeDriver()

    engine = web_engine.ReplayEngine(
        FakeDriver(),
        web_engine.ReplayConfig(min_pause=0.0, max_pause=0.0),
        driver_factory=factory,
    )
    monkeypatch.setitem(web_engine._DISPATCHERS, "boom", boom)
    stats = engine.run([Event(type="boom", ts=1.0)])
    assert calls["factory"] == 0
    assert stats["ok"] == 0 and stats["failed"][0]["error"] == "element missing"


def test_engine_records_method_report(monkeypatch):
    """Per-action results carry the interaction method used, so the run log
    reads "OK via native/js" instead of requiring a debug hunt."""
    from player.web import engine as web_engine
    from player.web.events import Event

    class FakeDriver:
        current_url = "https://example.com/"

        def get(self, url):
            pass

    def ok_action(driver, evt, config):
        return {"ok": True, "method": "js-shadow", "error": None, "took": 0.2}

    engine = web_engine.ReplayEngine(
        FakeDriver(), web_engine.ReplayConfig(min_pause=0.0, max_pause=0.0)
    )
    monkeypatch.setitem(web_engine._DISPATCHERS, "type", ok_action)
    stats = engine.run([Event(type="type", ts=1.0, value="hi")])
    assert stats["ok"] == 1
    assert stats["failed"] == []


def test_engine_pause_ignores_recorded_gap_uses_random_jitter(monkeypatch):
    """Recorded ms gaps are NOT replayed - replay waits a short random delay
    between actions like desktop sequences (a 3-action node used to crawl at
    25s by honoring the human's 5-20s hesitations)."""
    from player.web import engine as web_engine
    from player.web.events import Event

    sleeps = []
    monkeypatch.setattr(web_engine.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_engine.random, "uniform", lambda a, b: (a + b) / 2)

    class FakeDriver:
        current_url = "https://example.com/"

    def ok_action(driver, evt, config):
        return {"ok": True, "method": "js", "error": None, "took": 0.1}

    monkeypatch.setitem(web_engine._DISPATCHERS, "click", ok_action)
    engine = web_engine.ReplayEngine(
        FakeDriver(), web_engine.ReplayConfig(min_pause=0.4, max_pause=1.6)
    )
    stats = engine.run([
        Event(type="click", ts=1.0, gap_ms=20000),
        Event(type="click", ts=2.0, gap_ms=15000),
        Event(type="click", ts=3.0, gap_ms=30000),
    ])
    assert stats["ok"] == 3
    # 1.0s random midpoint per action - NOT the recorded 20/15/30s gaps.
    assert sleeps == [1.0, 1.0, 1.0]


def test_engine_pause_fast_key_bursts_are_snappier(monkeypatch):
    """Key/type bursts use a much shorter random floor, like desktop macros."""
    from player.web import engine as web_engine

    sleeps = []
    monkeypatch.setattr(web_engine.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_engine.random, "uniform", lambda a, b: (a + b) / 2)

    engine = web_engine.ReplayEngine(
        object(), web_engine.ReplayConfig(min_pause=0.4, max_pause=1.6)
    )
    engine._pause()
    engine._pause(fast=True)
    assert sleeps[0] == pytest.approx(1.0)   # normal midpoint
    assert sleeps[1] == pytest.approx(0.32)  # fast midpoint (0.08..0.56)


def test_engine_pause_scales_with_speed(monkeypatch):
    """Speed scales the random pause (2x speed = half the wait)."""
    from player.web import engine as web_engine

    sleeps = []
    monkeypatch.setattr(web_engine.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_engine.random, "uniform", lambda a, b: b)

    engine = web_engine.ReplayEngine(
        object(), web_engine.ReplayConfig(min_pause=0.5, max_pause=1.5, speed=2.0)
    )
    engine._pause()
    assert sleeps == [0.75]  # 1.5 / 2.0


def test_events_roundtrip_window_ordinal_and_frame_index_path():
    """The recorded window/frame STRUCTURE survives save + load unchanged."""
    from player.web.events import Event

    src = Event(type="click", ts=1.0, window_ordinal=1, frame_index_path=[2, 0])
    back = Event.from_dict(src.to_dict())
    assert back.window_ordinal == 1
    assert back.frame_index_path == [2, 0]
    # Legacy events (no scope recorded) stay None / empty - never crash.
    legacy = Event.from_dict({"type": "click", "ts": 1.0})
    assert legacy.window_ordinal is None and legacy.frame_index_path == []


def test_switch_to_window_ordinal_switches_and_noops():
    from player.web import actions as web_actions

    d = _two_window_driver(["https://app.example/"], current_idx=0)
    # The popup has not appeared yet -> no switch within the window.
    assert web_actions.switch_to_window_ordinal(d, 1, wait_s=0.0) is False

    d.add_window("https://app.example/modal")  # same host as the opener
    assert web_actions.switch_to_window_ordinal(d, 1, wait_s=0.0) is True
    assert d._current == 1
    # Already on the recorded window -> no spurious switch.
    assert web_actions.switch_to_window_ordinal(d, 1, wait_s=0.0) is False
    # No ordinal recorded (legacy session) -> no-op.
    assert web_actions.switch_to_window_ordinal(d, None, wait_s=0.0) is False


def test_enter_frame_index_path():
    from player.web import actions as web_actions

    class FakeCtx:
        def __init__(self):
            self.switch_to = self
            self.path = []

        def default_content(self):
            self.path = []

        def frame(self, index):
            if index == 99:
                raise RuntimeError("no such frame")
            self.path.append(index)

    d = FakeCtx()
    assert web_actions._enter_frame_index_path(d, [0, 1]) is True
    assert d.path == [0, 1]
    assert web_actions._enter_frame_index_path(d, []) is False
    # A missing frame restores the top document and reports failure.
    assert web_actions._enter_frame_index_path(d, [0, 99]) is False
    assert d.path == []


def test_engine_switches_window_by_recorded_ordinal(monkeypatch):
    """A recording across windows - even a SAME-HOST popup - replays each
    action on ITS window via the recorded ordinal, which url-host matching
    cannot disambiguate."""
    from player.web import engine as web_engine
    from player.web.events import Event

    dispatched = []
    d = _two_window_driver(["https://app.example/", "https://app.example/modal"])

    def fake_click(driver, evt, config):
        dispatched.append(driver.current_window_handle)
        return {"ok": True, "method": "native", "error": None, "took": 0.0}

    monkeypatch.setattr(web_engine, "_DISPATCHERS", {"click": fake_click})
    monkeypatch.setattr(web_engine.ReplayEngine, "_pause", lambda self, *a, **k: None)

    engine = web_engine.ReplayEngine(d, web_engine.ReplayConfig(native_actions=False))
    stats = engine.run([
        Event(type="click", ts=1.0, url="https://app.example/", window_ordinal=0),
        Event(type="click", ts=2.0, url="https://app.example/modal", window_ordinal=1),
    ])
    assert stats["ok"] == 2
    assert dispatched == ["w0", "w1"]


class _RecorderDrainDriver:
    """Fake driver for recorder._drain_tree: per-depth events + child iframes."""

    def __init__(self, events_by_depth, frames_by_depth):
        self._events = events_by_depth
        self._frames = frames_by_depth
        self._depth = 0
        self.switch_to = self

    def execute_script(self, script, *args):
        if "__wvpRecorder" in script and "return !!" in script:
            return True  # already injected
        return self._events.get(self._depth, [])

    def find_elements(self, by, value):
        if str(value).upper() != "IFRAME":
            return []
        return [object()] * self._frames.get(self._depth, 0)

    def default_content(self):
        self._depth = 0

    def frame(self, index):
        self._depth += 1

    def parent_frame(self):
        self._depth = max(0, self._depth - 1)


def test_recorder_drain_stamps_window_and_frame_scope():
    """Every drained event carries the window ordinal and the index-based
    frame chain the drain walked, so replay can re-enter the exact context."""
    from player.web import recorder as rec

    driver = _RecorderDrainDriver(
        events_by_depth={
            0: [{"type": "click", "ts": 1.0}],
            1: [{"type": "click", "ts": 2.0}],
        },
        frames_by_depth={0: 1},
    )
    drained = rec._drain_tree(driver, "payload", 0, 1, ())
    by_ts = {int(e.ts): e for e in drained}
    assert by_ts[1].window_ordinal == 1 and by_ts[1].frame_index_path == []
    assert by_ts[2].window_ordinal == 1 and by_ts[2].frame_index_path == [0]


def test_current_window_ordinal_and_failure():
    from player.web import recorder as rec

    class D:
        window_handles = ["a", "b"]
        current_window_handle = "b"

    assert rec._current_window_ordinal(D()) == 1

    class Dead:
        @property
        def window_handles(self):
            raise RuntimeError("session lost")

    assert rec._current_window_ordinal(Dead()) is None


def test_js_dispatch_deep_find_crosses_same_origin_iframes():
    """The shadow-piercing dispatchers also descend into SAME-ORIGIN iframes,
    so an element in a shadow-hosted frame (invisible to WebDriver) can still
    be reached."""
    from player.web import actions as web_actions

    for js in (web_actions.JS_DOM_POINTER, web_actions.JS_TYPE_DOM,
               web_actions.JS_FOCUS_DOM, web_actions.JS_DRAG, web_actions.JS_SCROLL):
        assert "shadowRoot" in js and "contentDocument" in js


def test_recorder_defines_shadow_frame_drain():
    """recorder.js exposes __wvpDrainShadow and tags collected events, so the
    WebDriver drain can read frames it cannot enumerate."""
    from player.web import recorder as rec

    js = (rec._INJECT_DIR / "recorder.js").read_text(encoding="utf-8")
    assert "window.__wvpDrainShadow" in js
    assert "wvp_shadow_frame" in js


def test_recorder_shows_recording_overlay_and_cheatsheet():
    """While recording, recorder.js draws a highlight box over the element under
    the cursor plus a top-right cheat sheet of the modifiers; every overlay
    node is pointer-events:none so it is never itself recorded, and the overlay
    is torn down on stop (__wvpFlush)."""
    from player.web import recorder as rec

    js = (rec._INJECT_DIR / "recorder.js").read_text(encoding="utf-8")
    assert "data-wvp-overlay" in js
    assert "pointer-events:none" in js
    assert "RECORDING" in js
    assert "Right Ctrl + move" in js
    assert "Insert + click" in js
    assert "stop & save" in js
    # gated to recording sessions so it never lingers after stop
    assert "looper.web.overlay" in js
    # self-healing: the overlay is injected BEFORE <html> exists (document
    # start), so creation is deferred and re-asserted when the page changes.
    assert "function reassertOverlay" in js
    assert "if (!root) return;" in js
    assert "new MutationObserver(reassertOverlay)" in js
    assert "addEventListener('DOMContentLoaded', reassertOverlay)" in js
    # manual hooks so a RE-RECORD (payload not re-run) still shows it
    assert "window.__wvpOverlayOn" in js
    assert "window.__wvpOverlayOff" in js
    # the overlay is removed when recording stops
    assert "removeOverlay();" in js


def test_set_overlay_calls_in_page_hook():
    """_set_overlay toggles the sessionStorage flag AND calls the in-page hook,
    so a re-record on an already-instrumented page still (re)creates the
    overlay (the payload is not re-run then)."""
    from player.web import recorder as rec

    calls = []

    class FakeDriver:
        def execute_script(self, script, *args):
            calls.append((script, args))

    rec._set_overlay(FakeDriver(), True)
    assert calls[0][1][1] == "on" and calls[0][1][2] == "__wvpOverlayOn"
    rec._set_overlay(FakeDriver(), False)
    assert calls[1][1][1] == "off" and calls[1][1][2] == "__wvpOverlayOff"


def test_recorder_drain_collects_shadow_frames():
    import inspect

    from player.web import recorder as rec

    assert "__wvpDrainShadow" in inspect.getsource(rec._drain)


def test_resolve_element_skips_shadow_frame_events():
    """A shadow-hosted frame is not WebDriver-reachable: resolution must give up
    immediately so the caller takes the in-browser dispatch path."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    evt = Event(type="click", ts=1.0, locator=Locator(tag="button", id="x"),
                context={"wvp_shadow_frame": True})
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions.resolve_element(object(), evt, 0.5)
    assert web_actions._try_resolve_element(object(), evt, None) is None


def test_locator_roundtrips_shadow_hosts():
    """The recorder's shadow-root marker must survive save + load, or replay
    cannot tell the element is inside a shadow root."""
    from player.web.events import Locator

    loc = Locator(tag="button", id="x", shadow_hosts=[{"tag": "div", "id": "host"}])
    back = Locator.from_dict(loc.to_dict())
    assert back.shadow_hosts == [{"tag": "div", "id": "host"}]
    assert Locator.from_dict({"tag": "button"}).shadow_hosts == []


def test_resolve_element_skips_shadow_dom_events():
    """An element inside a shadow root is invisible to WebDriver, so native
    resolution must be skipped - otherwise a light-DOM lookalike is acted on
    (playback runs in the original frame instead of the shadow popup)."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    evt = Event(type="click", ts=1.0,
                locator=Locator(tag="button", id="x",
                                shadow_hosts=[{"tag": "div", "id": "host"}]))
    assert web_actions._is_shadow_dom_event(evt) is True
    with pytest.raises(web_actions.ElementNotFoundError):
        web_actions.resolve_element(object(), evt, 0.5)
    assert web_actions._try_resolve_element(object(), evt, None) is None


def test_js_type_dom_searches_recorded_selector_before_focus():
    """Typing must target the RECORDED element first (shadow-piercing); only a
    gone target falls back to whatever editable is focused at replay time."""
    from player.web import actions as web_actions

    js = web_actions.JS_TYPE_DOM
    assert "var el = __wvpDeepFindLabel(label);" in js
    assert "if (!el || !isEditable(el)) el = __wvpDeepFindAny(selectors, 0);" in js
    assert "el = deepestActive();" in js


def test_css_selectors_try_whole_candidate_list():
    """The shadow dispatch must try EVERY candidate, but a GENERATED id must not
    LEAD: ``button#ember772`` names the recording's render instance, so on any
    other instance of the page it points at a different control.  The portable
    candidates go first and the generated id trails - still tried, but as a late
    resort, so a session recorded on one job stays replayable on another."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    loc = Locator(
        tag="button", id="ember772",
        aria_label="Continue to next step",
        classes=["artdeco-button", "artdeco-button--2"],
        text="Next", css="button#ember772",
    )
    sels = web_actions._css_selectors(Event(type="click", ts=1.0, locator=loc))
    assert sels[0] == "[aria-label='Continue to next step']"   # portable identity leads
    assert sels.index("[aria-label='Continue to next step']") < sels.index("button#ember772")
    assert "#ember772" in sels                    # volatile id still tried, late
    assert ".artdeco-button.artdeco-button--2" in sels

    # An AUTHORED id is still an identity, so it keeps leading.
    stable = Locator(tag="button", id="submit-now", css="button#submit-now")
    assert web_actions._css_selectors(
        Event(type="click", ts=1.0, locator=stable))[0] == "button#submit-now"


def test_generated_id_never_outranks_portable_identity():
    """A web sequence is replayed on page instances it was never recorded on, so
    a framework-generated id (Ember's render-order emberNNN) must trail the
    portable identity instead of leading the chain - that is what makes one
    recorded piece work across jobs."""
    from player.web.events import Locator, is_volatile_id

    assert is_volatile_id("ember395") is True        # Ember, numbered per render
    assert is_volatile_id(":r7:") is True            # React useId
    assert is_volatile_id("job-4457256950") is True  # per-instance token
    assert is_volatile_id("interop-outlet") is False
    assert is_volatile_id("main") is False
    assert is_volatile_id(None) is False

    # The chain leads with what the element IS, not where it sat.
    loc = Locator(tag="button", id="ember395", text="Submit application",
                  aria_label="Submit application", css="button#ember395",
                  xpath="/html[1]/body[1]/button[1]")
    kinds = [c["kind"] for c in loc.chain()]
    assert kinds.index("data") < kinds.index("id")   # aria-label precedes the id
    assert kinds[-1] == "xpath"                      # position stays dead last

    # An AUTHORED id is still the strongest signal and keeps leading.
    authored = Locator(tag="button", id="submit-now", text="Submit",
                       aria_label="Submit")
    assert authored.chain()[0] == {"kind": "id", "value": "submit-now"}


def test_shadow_dom_detected_from_legacy_xpath():
    """Legacy recordings (no shadow_hosts) still identify shadow-root elements:
    their absolute XPath is not rooted at /html (the walk stopped at a shadow
    boundary), while a light-DOM element is always /html/..."""
    from player.web import actions as web_actions
    from player.web.events import Event, Locator

    shadow = Event(type="click", ts=1.0, locator=Locator(
        tag="button", xpath="/div[1]/div[@id='artdeco-modal-outlet']/button[1]"))
    assert web_actions._is_shadow_dom_event(shadow) is True

    light = Event(type="click", ts=1.0, locator=Locator(
        tag="button", xpath="/html[1]/body[1]/div[1]/button[1]"))
    assert web_actions._is_shadow_dom_event(light) is False

    # No xpath at all -> not classified as shadow DOM.
    assert web_actions._is_shadow_dom_event(
        Event(type="click", ts=1.0, locator=Locator(tag="button", id="x"))) is False


def test_find_by_chain_rejects_a_unique_match_that_is_not_the_record():
    """A weak/generated candidate that matches exactly ONE page element must not
    win when it does not AGREE with the recorded identity.

    Verified live on LinkedIn: a recorded `Easy Apply` click resolved to a
    filter label reading `Employment type` (one unique css match) and the click
    landed on the wrong element - the document underneath the popup.  The
    recorded TEXT is the identity, so a non-identity candidate must be deferred.
    """
    from player.web import actions as web_actions
    from player.web.events import Locator

    class El:
        def __init__(self, text):
            self.text = text

    lookalike = El("Employment type")   # the unique css match: WRONG element
    real = El("Easy Apply")             # the recorded one
    other = El("Easy Apply")

    class Driver:
        def find_elements(self, by, value):
            if "filter-pill" in str(value):
                return [lookalike]
            return [real, other]        # the text candidate: not unique

        def execute_script(self, script, element):
            return element.text

    chain = [{"kind": "css", "value": "label.filter-pill"},
             {"kind": "text", "value": "//label[normalize-space(.)=\"Easy Apply\"]"}]
    loc = Locator(tag="label", text="Easy Apply", text_index=0)

    assert web_actions._find_by_chain(Driver(), chain, 0.5, loc) is real

    # Without a recorded identity there is nothing to check: unchanged.
    bare = Locator(tag="label", text_index=0)
    assert web_actions._find_by_chain(Driver(), chain, 0.5, bare) is lookalike
