"""Screen-access guard + mitosis loader contracts.

Two rules the runtime must never break:
  * the app leaves the screen ONLY while a node reads or drives the desktop
    (sequence / handle / desktop form fill / visual conditional) - thinking
    nodes (LLM, orchestrator, code, web) keep the window up with progress;
  * nested work (a chain inside a chain, a capture inside a sequence) shares
    ONE hide/restore pair, so the window cannot flicker per read.

Run:  python -m pytest LoOper/tests/test_screen_access_guard.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.execution_overlay_bus import _ExecutionBus  # noqa: E402
from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (  # noqa: E402
    WorkflowExecutor,
)


# ---------------------------------------------------------------------------
# Bus: refcounted hide/restore
# ---------------------------------------------------------------------------

def test_nested_screen_access_hides_once_and_restores_on_the_last_exit():
    bus = _ExecutionBus()
    calls = []
    bus.set_screen_hooks(lambda: calls.append("hide"), lambda: calls.append("show"))

    with bus.screen_access():
        with bus.screen_access():
            bus.hide_overlay_for_capture()  # a capture inside a sequence
        assert calls == ["hide"]

    assert calls == ["hide", "show"]

    # A capture on its own is a complete hide/restore pair too.
    bus.hide_overlay_for_capture()
    assert calls == ["hide", "show", "hide", "show"]


def test_screen_busy_tracks_the_refcount():
    bus = _ExecutionBus()
    assert bus.screen_busy is False
    with bus.screen_access():
        assert bus.screen_busy is True
        with bus.screen_access():
            assert bus.screen_busy is True
    assert bus.screen_busy is False


def test_cursor_is_the_node_animation_while_thinking(app):
    """Manual run: cursor = animation while thinking, arrow while acting."""
    from NGUI.widgets.agent_dot import ARROW_W, THINK_BOX, AgentDot

    dot = AgentDot()
    dot.set_thinking(True)
    assert (dot.width(), dot.height()) == (THINK_BOX, THINK_BOX)
    assert dot._loader is not None and dot._loader.parentWidget() is dot
    assert dot._loader.geometry().getRect() == (0, 0, THINK_BOX, THINK_BOX)

    dot.set_thinking(False)
    assert (dot.width(), dot.height()) == (ARROW_W, ARROW_W)
    assert not dot._loader.isVisible()
    assert not dot._loader._timer.isActive()
    dot.deleteLater()


def test_screen_access_without_hooks_is_silent():
    bus = _ExecutionBus()
    with bus.screen_access():
        bus.hide_overlay_for_capture()  # must not raise


def test_failing_hook_never_reaches_the_runtime():
    bus = _ExecutionBus()

    def _boom():
        raise RuntimeError("gui gone")

    bus.set_screen_hooks(_boom, _boom)
    with bus.screen_access():
        pass
    assert bus._screen_depth == 0


# ---------------------------------------------------------------------------
# Which nodes take the app off screen
# ---------------------------------------------------------------------------

def _needs(node):
    ex = WorkflowExecutor.__new__(WorkflowExecutor)
    return ex._node_needs_screen(node)


def test_desktop_nodes_need_the_screen():
    assert _needs({'type': 'sequence', 'data': {}})
    assert _needs({'type': 'handle', 'data': {}})
    assert _needs({'type': 'form_filler', 'data': {'mode': 'desktop'}})
    assert _needs({'type': 'conditional', 'data': {'condition_type': 'presence'}})
    assert _needs({'type': 'conditional', 'data': {'condition_type': 'ocr'}})
    assert _needs({'type': 'conditional', 'data': {'condition_type': 'absence'}})


@pytest.mark.parametrize('node', [
    {'type': 'llm', 'data': {}},
    {'type': 'orchestrator', 'data': {}},
    {'type': 'code', 'data': {}},
    {'type': 'context', 'data': {}},
    {'type': 'output', 'data': {}},
    {'type': 'web_sequence', 'data': {}},
    {'type': 'chain_import', 'data': {}},
    {'type': 'form_filler', 'data': {}},                        # web is the default
    {'type': 'form_filler', 'data': {'mode': 'web'}},
    {'type': 'conditional', 'data': {'condition_type': 'web'}},
    {'type': 'conditional', 'data': {'web_mode': True}},
    {'type': 'conditional', 'data': {'condition_type': 'code'}},
    {'type': 'conditional', 'data': {'condition_type': 'llm'}},
])
def test_thinking_and_web_nodes_keep_the_window_up(node):
    assert not _needs(node)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def app():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_loader_grows_rewires_and_unwinds(app, monkeypatch):
    """The test.html timeline: grow waves to the cap, live turnover at full
    (reparent + short-lived cross links), then peel back to the root."""
    from NGUI.widgets.mitosis_loader import STEP, MitosisLoader

    loader = MitosisLoader(size=56, max_nodes=8)
    # The force layout is the page's; this test walks the state machine.
    monkeypatch.setattr(loader, '_physics', lambda dt: None)
    monkeypatch.setattr(loader, '_camera', lambda dt: None)
    loader._rng.seed(7)               # one seed is one tree: stable test
    loader._reset_tree()

    peak = 0
    saw_root_only = False
    saw_cross_link = False
    for _ in range(60 * 120):          # 120 s of the page's timeline
        loader._step(STEP)
        peak = max(peak, loader._alive)
        saw_root_only = saw_root_only or loader._alive == 1
        saw_cross_link = saw_cross_link or loader._cross_live > 0

    assert peak == 8                   # grew to the cap
    assert saw_root_only               # unwound back to the root, then regrew
    assert saw_cross_link              # cross links are part of the cycle
    loader.grab()                      # paint path must not raise
    loader.deleteLater()


def test_loader_finish_goes_cyan_and_collapses_to_the_root(app, monkeypatch):
    """Turn over: the tree stops growing and peels back into the centre."""
    from NGUI.widgets.mitosis_loader import STEP, MitosisLoader

    loader = MitosisLoader(max_nodes=14)
    loader.resize(420, 300)
    monkeypatch.setattr(loader, '_physics', lambda dt: None)
    monkeypatch.setattr(loader, '_camera', lambda dt: None)
    loader._rng.seed(11)
    loader._reset_tree()

    for _ in range(60 * 40):
        loader._step(STEP)
        if loader._alive >= 8:
            break
    grown = loader._alive
    assert grown >= 8

    fired = []
    loader.finished.connect(lambda: fired.append(True))
    loader.finish()
    assert loader._finishing             # paint switches to the cyan palette

    counts = []
    for _ in range(60 * 8):
        loader._step(STEP)
        counts.append(loader._alive)
        if fired:
            break

    assert fired                        # host told to drop the widget
    assert loader._alive == 1           # only the central node is left
    assert counts == sorted(counts, reverse=True)   # never grows again
    assert counts[0] <= grown
    loader.grab()
    loader.deleteLater()


def test_loader_holds_the_next_reply_slot(app):
    """In the chat the loader always sits after the newest message."""
    from PyQt5.QtWidgets import QLabel
    from NGUI.widgets.agent_overlay import AgentOverlay

    overlay = AgentOverlay()
    overlay._show_thinking()
    loader = overlay._thinking_loader
    assert loader is not None

    bubble = QLabel("the response", overlay._chat_container)
    overlay._chat_layout.addWidget(bubble, 0)
    overlay._keep_thinking_last()

    lay = overlay._chat_layout
    assert lay.itemAt(lay.count() - 1).widget() is loader
    assert lay.itemAt(lay.count() - 2).widget() is bubble

    overlay._hide_thinking()
    assert overlay._thinking_loader is None
    overlay.deleteLater()
