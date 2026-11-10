"""The chain's own flow metadata survives a GUI save/load cycle.

A chain described by the Builder (``player/builder/flow.py``: summary /
sections / steps) must keep that description when the editor saves it and give
it back when the editor loads it — otherwise a loose chain falls back to a
naive one-liner for every later reader (agent mode reads ``flow.summary`` when
``description`` is empty, see ``player/agentic_ops/system_chains.py``).

Run:  python -m pytest LoOper/tests/test_builder_flow_persist.py
"""

import json
import os

from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

FLOW = {
    'summary': 'asks the user, thinks, checks, reports',
    'sections': [{'title': 'ask', 'note': 'collect the question',
                  'nodes': ['0x1'], 'hue': '#22D3EE'},
                 {'title': 'report', 'note': '', 'nodes': ['0x4', '0x5'],
                  'hue': '#4A90E2'}],
    'steps': [{'node': '0x2', 'does': 'drafts the answer'}],
    'nodes': [{'node': '0x1', 'does': 'supplies the question'},
              {'node': '0x2', 'does': 'drafts the answer'}],
}


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


class _EmptyGraph:
    """No nodes on the canvas: save_current_state still builds the dict."""

    @staticmethod
    def get_all_nodes():
        return []


class _Parent:
    """Minimal parent widget stand-in (only attributes the paths touch)."""

    chains_folder = ''
    graph_manager = _EmptyGraph()


def _cm(monkeypatch):
    from NGUI.graph_elements import config_manager as cm_mod
    from NGUI.graph_elements.config_manager import ConfigManager

    # A failing load pops a modal dialog; the test never needs one.
    monkeypatch.setattr(cm_mod.QMessageBox, 'critical',
                        staticmethod(lambda *a, **k: None))
    cm = ConfigManager(_Parent())
    cm.build_graph_from_config = lambda: None   # no graph widget offscreen
    return cm


def test_a_new_chain_carries_no_flow_block(qapp, monkeypatch):
    cm = _cm(monkeypatch)
    assert cm.get_flow() == {}
    cm.save_current_state()
    assert 'flow' not in cm.chain_config


def test_flow_metadata_round_trips_through_the_chain_file(qapp, tmp_path,
                                                          monkeypatch):
    cm = _cm(monkeypatch)
    cm.set_flow(FLOW)
    assert cm.get_flow() == FLOW

    # the editor's own save path: harvest the canvas, then dump the dict
    cm.save_current_state()
    assert cm.chain_config['flow'] == FLOW

    out = tmp_path / 'described.json'
    assert cm.save_chain(str(out)) is True
    saved = json.loads(out.read_text(encoding='utf-8'))
    assert saved['flow'] == FLOW
    assert saved['_web_chain_scope'] == 'default'   # the normal save still ran

    # ...and loading it back hands the description to the next reader
    fresh = _cm(monkeypatch)
    assert fresh.load_chain(str(out)) is True
    assert fresh.get_flow() == FLOW
    assert fresh.chain_config['flow'] == FLOW


def test_an_undescribed_chain_saves_without_a_flow_key(qapp, tmp_path,
                                                       monkeypatch):
    cm = _cm(monkeypatch)
    out = tmp_path / 'plain.json'
    assert cm.save_chain(str(out)) is True
    assert 'flow' not in json.loads(out.read_text(encoding='utf-8'))


def test_clearing_the_flow_drops_it_from_the_next_save(qapp, tmp_path,
                                                       monkeypatch):
    cm = _cm(monkeypatch)
    cm.set_flow(FLOW)
    cm.set_flow({})
    out = tmp_path / 'cleared.json'
    assert cm.save_chain(str(out)) is True
    assert 'flow' not in json.loads(out.read_text(encoding='utf-8'))
    assert cm.get_flow() == {}


def test_saving_derives_the_description_from_the_flow(qapp, monkeypatch):
    """Manual description writing is retired: the save writes the brief."""
    cm = _cm(monkeypatch)
    cm._current_chain_description = 'HAND TYPED PROSE THAT DRIFTED'
    cm.set_flow(FLOW)
    cm.save_current_state()

    description = cm.chain_config['description']
    assert 'HAND TYPED' not in description
    assert description.startswith(FLOW['summary'].split('.')[0])
    assert 'ask' in description, 'the section titles are the chain flow'
    assert cm._current_chain_description == description


# ------------------------------------------- drawn on OPEN, without asking

def _chain_canvas():
    """A real three-node canvas; a second call is the same chain REOPENED."""
    from NodeGraphQt import NodeGraph
    from NGUI.nodes import ConditionalNode, InputNode, LLMNode, OutputNode

    graph = NodeGraph()
    for cls in (InputNode, LLMNode, ConditionalNode, OutputNode):
        graph.register_node(cls)
    inp = graph.create_node('input.InputNode', name='query', pos=[0, 0])
    llm = graph.create_node('llm.LLMNode', name='answer', pos=[0, 200])
    cond = graph.create_node('conditional.ConditionalNode', name='ok?', pos=[0, 400])
    out = graph.create_node('output.OutputNode', name='report', pos=[0, 600])
    inp.outputs()['output'].connect_to(llm.inputs()['input'])
    llm.outputs()['output'].connect_to(cond.inputs()['input'])
    cond.outputs()['true'].connect_to(out.inputs()['input'])
    cond.outputs()['false'].connect_to(out.inputs()['input'])
    return graph


def _describe_on(graph):
    from player.builder import flow as bflow, snapshot as bsnapshot

    snap = bsnapshot.build(_GraphManager(graph))
    ids = {view.name: node_id for node_id, view in snap.nodes.items()}
    return bflow.check_nodes(snap, bflow.normalise({
        'summary': 'asks, thinks, checks, reports',
        'sections': [{'title': 'ask', 'nodes': [ids['query']]},
                     {'title': 'think + check', 'nodes': [ids['answer'],
                                                          ids['ok?']]}],
        'steps': [],
    }))


class _GraphManager:
    def __init__(self, graph):
        self._graph = graph

    def get_all_nodes(self):
        return self._graph.all_nodes()


def _view(graph, stored):
    """A GraphViewWidget stand-in wired to a real overlay over *graph*."""
    from types import SimpleNamespace

    from NGUI.graph_elements.flow_overlay import FlowOverlay
    from NGUI.graph_view import GraphViewWidget

    overlay = FlowOverlay(graph)
    view = SimpleNamespace(graph_manager=_GraphManager(graph),
                           config_manager=SimpleNamespace(
                               get_flow=lambda: stored),
                           flow_overlay=lambda: overlay,
                           overlay=overlay)
    view.get_flow = lambda: GraphViewWidget.get_flow(view)
    return view


def test_the_stored_description_draws_when_the_chain_opens(qapp):
    from NGUI.graph_view import GraphViewWidget

    graph = _chain_canvas()
    stored = _describe_on(graph)
    file_ids = {node.id for node in graph.all_nodes()}
    assert set(stored['sections'][0]['nodes']).isdisjoint([])  # non-empty
    assert set(stored['sections'][0]['nodes']) <= file_ids     # ids, not names

    view = _view(graph, stored)
    GraphViewWidget._show_stored_flow(view)

    boxes = view.overlay._boxes
    # The stored boxes are drawn as stored — no box is invented for the nodes
    # they leave out (a guessed frame rounds whatever sits in between).
    assert [section['title'] for section, _frame, _label, _notes in boxes] == [
        'ask', 'think + check']
    for section, frame, label, _notes in boxes:
        assert frame.isVisible(), section['title']
        assert set(section['nodes']) <= file_ids, section['nodes']

    # The label PRINTS the note: a title-only section is a headline with no
    # body.  The height that shows the whole note has to be at least that.
    for section, _frame, label, _notes in boxes:
        assert label._note, section['title']
        assert label.head_height(400.0) > 0.0
        assert label.preferred_height(400.0) > label.head_height(400.0)


def test_get_flow_translates_the_files_ids_onto_this_sessions_nodes(
        qapp, monkeypatch):
    """The chain file names ids; a rebuilt chain has new ones.  The loader's
    id map is what makes a stored description survive a reload."""
    cm = _cm(monkeypatch)
    cm._current_chain_flow = {
        'summary': 'asks and reports',
        'sections': [{'title': 'ask', 'note': '', 'hue': '#22D3EE',
                      'nodes': ['saved-1', 'gone']}],
        'steps': [{'node': 'saved-2', 'does': 'drafts'}],
        'nodes': [{'node': 'saved-1', 'does': 'supplies the question'},
                  {'node': 'saved-2', 'does': 'drafts the reply'}],
    }
    cm._node_id_map = {'saved-1': SimpleNamespace(id='live-1'),
                       'saved-2': SimpleNamespace(id='live-2')}

    live = cm.get_flow()
    assert live['sections'][0]['nodes'] == ['live-1', 'gone']
    assert live['steps'][0]['node'] == 'live-2'
    assert [entry['node'] for entry in live['nodes']] == ['live-1', 'live-2']
    # the stored copy keeps the file's ids: the next save re-translates them
    assert cm._current_chain_flow['sections'][0]['nodes'] == ['saved-1', 'gone']

    # no map (a chain that was never loaded): nothing is invented
    cm._node_id_map = {}
    assert cm.get_flow()['sections'][0]['nodes'] == ['saved-1', 'gone']


def test_the_stored_flow_uses_the_ids_the_savers_write(qapp, monkeypatch):
    """A section must name a node the way the NEXT save writes it.

    The savers write ``node.id`` (container and context keep the id they were
    loaded with, in ``_config_node_id``), so that — never the ids of a previous
    save — is the chain's own id space.  Storing the old ones made a reopened
    chain's sections point at ids that no longer exist.
    """
    class _Node:
        def __init__(self, live, config_id=''):
            self.id = live
            self._config_id = config_id

        def properties(self):
            return ({'custom': {'_config_node_id': self._config_id}}
                    if self._config_id else {})

    class _Graph:
        def __init__(self, nodes):
            self._nodes = nodes

        def get_all_nodes(self):
            return self._nodes

    cm = _cm(monkeypatch)
    plain, kept = _Node('live-1'), _Node('live-2', 'kept-2')
    cm.parent_widget.graph_manager = _Graph([plain, kept])
    # The loader's map names the ids the FILE was read with — one id space per
    # file, translated on the way in and on the way out.
    cm._node_id_map = {'kept-2': kept}

    cm.set_flow({'summary': 'asks and reports',
                 'sections': [{'title': 'ask', 'note': '', 'hue': '#22D3EE',
                               'nodes': ['live-1']}],
                 'steps': [{'node': 'live-2', 'does': 'reports'}],
                 'nodes': [{'node': 'live-1', 'does': 'supplies'}]})

    stored = cm._current_chain_flow
    assert stored['sections'][0]['nodes'] == ['live-1']
    assert stored['steps'][0]['node'] == 'kept-2', 'the id the saver writes'
    assert stored['nodes'][0]['node'] == 'live-1'
    # ...and the panel still reads LIVE ids back
    assert cm.get_flow()['sections'][0]['nodes'] == ['live-1']

    cm.save_current_state()
    assert cm.chain_config['flow']['sections'][0]['nodes'] == ['live-1']
    assert cm.chain_config['flow']['steps'][0]['node'] == 'kept-2'


def test_an_undescribed_chain_draws_nothing_and_a_stale_one_still_opens(qapp):
    from NGUI.graph_view import GraphViewWidget

    graph = _chain_canvas()
    blank = _view(graph, {})
    GraphViewWidget._show_stored_flow(blank)
    assert blank.overlay._boxes == []

    stale = {'summary': '', 'steps': [],
             'sections': [{'title': 'old', 'hue': '#F59E0B', 'note': '',
                           'nodes': ['0xdeadbeef']}]}
    view = _view(graph, stale)
    GraphViewWidget._show_stored_flow(view)      # logs, never raises
    # Every ref was stale: nothing of that description is drawable, and a
    # description that asked for boxes must not be replaced by invented ones.
    assert view.overlay._boxes == []


def test_one_stale_section_no_longer_hides_the_others(qapp):
    """The boxes of a partly stale description are still true for the nodes
    that exist — they are drawn, and only the unknown ref is reported."""
    from NGUI.graph_view import GraphViewWidget

    graph = _chain_canvas()
    stored = _describe_on(graph)
    stored['sections'].append({'title': 'gone', 'hue': '#F59E0B', 'note': '',
                               'nodes': ['0xdeadbeef']})

    view = _view(graph, stored)
    GraphViewWidget._show_stored_flow(view)

    assert [section['title'] for section, _frame, _label, _notes
            in view.overlay._boxes] == ['ask', 'think + check']
    assert 'gone' not in [section['title']
                          for section, _frame, _label, _notes
                          in view.overlay._boxes]

