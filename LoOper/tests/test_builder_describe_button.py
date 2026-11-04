"""The Builder's Describe button: a real model turn that rewrites the chain.

Pressing it must actually re-describe: it sends a turn to the configured model
(the wording can change even when the canvas did not), stores the answer, and
writes the chain file.  When the model cannot answer — no gateway, no 'flow' in
the reply — the canvas derives the description instead, and says so.

Run:  python -m pytest LoOper/tests/test_builder_describe_button.py
"""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _Manager:
    def __init__(self, graph):
        self._graph = graph

    def get_all_nodes(self):
        return self._graph.all_nodes()

    def get_node_by_id(self, node_id):
        return self._graph.get_node_by_id(node_id)


def _canvas():
    """A two-node chain; its metadata is held in a dict, like the file's."""
    from NodeGraphQt import NodeGraph
    from NGUI.nodes import InputNode, OutputNode

    graph = NodeGraph()
    for cls in (InputNode, OutputNode):
        graph.register_node(cls)
    ask = graph.create_node('input.InputNode', name='ask', pos=[0, 0])
    out = graph.create_node('output.OutputNode', name='report', pos=[0, 300])
    ask.outputs()['output'].connect_to(out.inputs()['input'])
    ask.set_property('label', 'the topic')
    out.set_property('label', 'the reply')
    return graph


def _panel(monkeypatch):
    from NGUI.graph_elements.flow_overlay import FlowOverlay
    from NGUI.widgets.builder_panel import BuilderPanel

    monkeypatch.setattr(BuilderPanel, '_hard_error',
                        lambda self, title, text: pytest.fail(text))
    graph = _canvas()
    overlay = FlowOverlay(graph)
    stored = {}
    saved = []

    def _get_flow():
        return dict(stored)

    def _set_flow(metadata):
        stored.clear()
        stored.update(metadata)

    graph_view = SimpleNamespace(
        _chain_epoch=0,
        graph_manager=_Manager(graph),
        config_manager=SimpleNamespace(_current_chain_file=''),
        get_flow=_get_flow,
        set_flow=_set_flow,
        flow_overlay=lambda: overlay,
        save_chain=lambda path: saved.append(path),
    )
    panel = BuilderPanel(SimpleNamespace(graph_view=graph_view))
    panel._start_turn = lambda message: None
    return panel, graph, stored, saved


def test_the_button_describes_every_node_and_boxes_every_part(qapp, monkeypatch):
    panel, graph, stored, _saved = _panel(monkeypatch)
    assert stored == {}

    panel._describe_from_canvas()

    assert stored['nodes'], 'every node got its sentence'
    assert {entry['node'] for entry in stored['nodes']} == {
        node.id for node in graph.all_nodes()}
    assert all(entry['does'] for entry in stored['nodes'])

    # ...and the boxes cover the whole chain, not just the parts a model might
    # have mentioned: a node outside every box is invisible on the canvas.
    covered = [nid for section in stored['sections'] for nid in section['nodes']]
    assert set(covered) == {node.id for node in graph.all_nodes()}
    assert all(section['note'] for section in stored['sections'])

    # The sentence also lands on the node itself, where its dialog shows it.
    for node in graph.all_nodes():
        assert node.get_property('description'), node.name()

    # A description that names nodes this canvas does not have is dropped, not
    # raised: re-describing the chain is exactly what the button is for.
    stored['sections'] = [{'title': 'ghost', 'note': '', 'hue': '#22D3EE',
                           'nodes': ['0xdeadbeef']}]
    panel._describe_from_canvas()
    assert {nid for section in stored['sections'] for nid in section['nodes']} == {
        node.id for node in graph.all_nodes()}


def test_the_button_redescribes_an_unchanged_chain_and_writes_it(
        qapp, monkeypatch, tmp_path):
    """Pressing Describe is a RE-description, not a no-op: a stored sentence
    whose stamp says it is current is regenerated anyway, and the file on disk
    is rewritten without anyone saving."""
    from player.builder import flow as bflow
    from player.builder import snapshot as bsnapshot

    panel, graph, stored, saved = _panel(monkeypatch)
    chain_file = tmp_path / 'chain.json'
    chain_file.write_text('{}', encoding='utf-8')
    config = panel.main_window.graph_view.config_manager
    config._current_chain_file = str(chain_file)

    snap = bsnapshot.build(panel.main_window.graph_view.graph_manager)
    node = sorted(snap.nodes.values(), key=lambda n: n.position)[0]
    stamp = bflow.node_stamp(node.type, node.properties)
    stored['nodes'] = [{'node': node.id, 'does': 'OLD WORDING',
                        'stamp': stamp}]

    # A merge (a model answer) keeps the authored sentence it was given.
    panel._store_flow(dict(stored))
    assert 'OLD WORDING' in [entry['does'] for entry in stored['nodes']]

    stored['nodes'] = [{'node': node.id, 'does': 'OLD WORDING',
                        'stamp': stamp}]
    panel._describe_from_canvas()

    assert 'OLD WORDING' not in [entry['does'] for entry in stored['nodes']]
    assert saved and saved[-1] == str(chain_file), 'the file is rewritten'



# ------------------------------------------- the button runs staged pass

class _Signal:
    def connect(self, *_args):
        pass


class _FakeWorker:
    """Stands in for the staged worker: records how the button starts it."""

    instances = []

    def __init__(self, ctx, stop_event, parent=None):
        self.ctx = ctx
        self.stop_event = stop_event
        self.progress = _Signal()
        self.done = _Signal()
        self.failed = _Signal()
        self.started = False
        _FakeWorker.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return False


def test_the_button_starts_the_staged_pass(qapp, monkeypatch):
    from NGUI.widgets import builder_panel as bp

    panel, _graph, _stored, _saved = _panel(monkeypatch)
    _FakeWorker.instances = []
    monkeypatch.setattr(bp, '_DescribeWorker', _FakeWorker)

    panel._on_describe()

    assert len(_FakeWorker.instances) == 1
    worker = _FakeWorker.instances[0]
    assert worker.started, 'the pass runs off the GUI thread'
    assert worker.ctx['run_on_gui'] is not None
    assert worker.stop_event is panel._stop_event
    assert panel._worker is worker


def test_a_finished_pass_is_stored_and_written(qapp, monkeypatch, tmp_path):
    panel, graph, stored, saved = _panel(monkeypatch)
    chain_file = tmp_path / 'chain.json'
    chain_file.write_text('{}', encoding='utf-8')
    config = panel.main_window.graph_view.config_manager
    config._current_chain_file = str(chain_file)

    first = sorted(graph.all_nodes(), key=lambda n: n.name())[0]
    panel._on_describe_done({
        'flow': {'summary': 'STAGED SUMMARY',
                 'sections': [{'title': 'ask', 'note': 'WIRE NOTE',
                               'nodes': [first.id]}],
                 'nodes': [{'node': first.id, 'does': 'WIRE-AWARE LINE'}]},
        'total': 3, 'done': 3, 'failed': [],
    })

    assert stored['summary'] == 'STAGED SUMMARY'
    assert 'WIRE-AWARE LINE' in [entry['does'] for entry in stored['nodes']]
    assert saved and saved[-1] == str(chain_file)
    assert panel._worker is None


def test_unanswered_connections_are_reported_not_swallowed(qapp, monkeypatch):
    errors = []
    panel, _graph, stored, _saved = _panel(monkeypatch)
    monkeypatch.setattr(panel.__class__, '_hard_error',
                        lambda self, title, text: errors.append((title, text)))

    panel._on_describe_done({
        'flow': {'summary': 'S', 'sections': [], 'nodes': []},
        'total': 3, 'done': 2,
        'failed': [('0x1.out --execution--> 0x2.in', 'gateway is down')],
    })

    assert stored['summary'] == 'S', 'the answered wires are still stored'
    assert errors and errors[0][0] == 'Describe chain'
    assert '0x1.out' in errors[0][1] and 'gateway is down' in errors[0][1]


def test_the_walk_button_needs_no_model(qapp, monkeypatch):
    """The per-node highlight is deterministic: it must not cost a model call.

    Asking the model for a walkthrough was the request a 4k model refused
    ("please reduce the length of the messages"), while the walk itself is
    built from the canvas (`flow.steps`).
    """
    panel, _graph, _stored, _saved = _panel(monkeypatch)

    panel._on_walk()

    assert panel._walk_steps, 'the walk is built from the canvas'
    assert panel._worker is None, 'no model turn is started'
    panel._stop_walk()


def test_the_walk_button_survives_a_description_naming_dead_nodes(
        qapp, monkeypatch):
    panel, _graph, stored, _saved = _panel(monkeypatch)
    stored['nodes'] = [{'node': '0xdeadbeef', 'does': 'gone'},
                       {'node': '0xbeef', 'does': 'gone too'}]
    stored['steps'] = [{'node': '0xdeadbeef', 'does': 'gone'}]

    panel._on_walk()

    assert panel._walk_steps, 'the dead refs are dropped, the walk runs'
    panel._stop_walk()


def test_a_failed_pass_still_describes_the_canvas(qapp, monkeypatch):
    errors = []
    panel, graph, stored, _saved = _panel(monkeypatch)
    monkeypatch.setattr(panel.__class__, '_hard_error',
                        lambda self, title, text: errors.append(title))

    panel._on_describe_failed('gateway is down', 'traceback')

    assert stored['nodes'], 'no model must not mean no description'
    assert {entry['node'] for entry in stored['nodes']} == {
        n.id for n in graph.all_nodes()}
    assert errors and errors[0] == "Builder failed"
