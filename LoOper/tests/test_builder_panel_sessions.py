"""The Builder's chat is per CHAIN, not per app session.

Switching the chain on the canvas used to leave the whole previous conversation
in view and in the model's tail, so a new chain was answered as if it were the
continuation of the old one.  The session is keyed by
``GraphViewWidget._chain_epoch`` (bumped on open / clear), the visible chat is
reset with it, and the model's tail only ever holds the current chain.

Run:  python -m pytest LoOper/tests/test_builder_panel_sessions.py
"""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _panel():
    """A panel over a stub canvas; the model call is stubbed out."""
    from NGUI.widgets.builder_panel import BuilderPanel

    graph_view = SimpleNamespace(
        _chain_epoch=0,
        node_graph=None,
        graph_manager=None,
        config_manager=SimpleNamespace(_current_chain_file=''),
        get_flow=lambda: {},
        flow_overlay=lambda: None,
    )
    panel = BuilderPanel(SimpleNamespace(graph_view=graph_view))
    panel._start_turn = lambda message: None       # never reach the model
    return panel, graph_view


def _ask(panel, text):
    panel._input.setText(text)
    panel._on_send()


def _visible(panel):
    texts = []
    for index in range(panel._chat_layout.count()):
        widget = panel._chat_layout.itemAt(index).widget()
        if widget is not None:
            texts.append(widget.text())
    return texts


def test_each_chain_gets_its_own_tail(qapp):
    panel, graph = _panel()
    _ask(panel, 'what does this chain do?')
    assert [h['text'] for h in panel._sessions[0]] == ['what does this chain do?']

    graph._chain_epoch = 1                 # the canvas now holds another chain
    _ask(panel, 'and this one?')

    assert list(panel._sessions) == [0, 1]
    assert [h['text'] for h in panel._sessions[0]] == ['what does this chain do?']
    assert [h['text'] for h in panel._sessions[1]] == ['and this one?']
    # the model sees only the chain it is being asked about
    assert [h['text'] for h in panel._make_ctx()['history']] == ['and this one?']


def test_switching_the_chain_clears_the_visible_chat(qapp):
    panel, graph = _panel()
    _ask(panel, 'first chain question')
    assert any('first chain question' in text for text in _visible(panel))

    graph._chain_epoch = 7
    panel._sync_session()

    shown = _visible(panel)
    assert not any('first chain question' in text for text in shown), shown
    assert len(shown) == 1 and 'New session' in shown[0], shown


def test_saving_an_unsaved_chain_keeps_the_session(qapp):
    """Only opening or clearing a chain rotates; naming it does not."""
    panel, graph = _panel()
    _ask(panel, 'describe it')
    before = panel._session_id
    graph.config_manager._current_chain_file = r'd:\Arrow\LoOper\chains\x.json'
    panel._sync_session()
    assert panel._session_id == before
    assert [h['text'] for h in panel._sessions[before]] == ['describe it']


def test_a_change_mid_turn_waits_for_the_turn_to_finish(qapp):
    panel, graph = _panel()
    panel._sync_session()

    class _Busy:
        def isRunning(self):
            return True

    panel._worker = _Busy()
    graph._chain_epoch = 3
    panel._sync_session()
    assert panel._session_id == 0, 'must not rotate under a running turn'

    panel._worker = None
    panel._sync_session()
    assert panel._session_id == 3
