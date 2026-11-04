"""The chat answers, and changes ONLY what the user selected.

Describing a chain and walking it are the panel's buttons.  The conversation is
left with: answer questions, propose enhancements, guide building a chain — and
change what is in front of it:

* ``edit_node`` writes settings on ONE node the user SELECTED;
* ``wire_nodes`` connects/disconnects the wire between the TWO selected nodes.

Both refuse everything else, and a refusal is an error the model reports, never
a silent no-op.  A turn that changed the canvas says so, and the panel writes
the chain file.

Run:  python -m pytest LoOper/tests/test_builder_chat_edit.py
"""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- canvas

class _Port:
    def __init__(self, owner, name, direction):
        self.owner = owner
        self._name = name
        self.direction = direction
        self.connections = []

    def name(self):
        return self._name

    def connected_ports(self):
        return list(self.connections)

    def node(self):
        return self.owner

    def connect_to(self, other):
        self.connections.append(other)
        other.connections.append(self)

    def disconnect_from(self, other):
        for side, peer in ((self, other), (other, self)):
            if peer in side.connections:
                side.connections.remove(peer)


class _Node:
    def __init__(self, node_id, node_type, name, props=None,
                 inputs=('input',), outputs=('output',)):
        self.id = node_id
        self.type_ = node_type
        self._name = name
        self._props = dict(props or {})
        self._in = {n: _Port(self, n, 'in') for n in inputs}
        self._out = {n: _Port(self, n, 'out') for n in outputs}

    def name(self):
        return self._name

    def properties(self):
        return dict(self._props)

    def get_property(self, key):
        if key not in self._props:
            raise KeyError(key)
        return self._props[key]

    def set_property(self, key, value):
        self._props[key] = value

    def input_ports(self):
        return list(self._in.values())

    def output_ports(self):
        return list(self._out.values())

    def pos(self):
        return (0.0, 0.0)


class _Graph:
    def __init__(self, nodes=()):
        self._nodes = {node.id: node for node in nodes}

    def get_all_nodes(self):
        return list(self._nodes.values())

    def get_node_by_id(self, node_id):
        return self._nodes.get(node_id)


def canvas():
    """input -> llm -> conditional -> output, with nothing wired yet."""
    return _Graph([
        _Node('0x1', 'input', 'ask', props={'label': 'ask'},
              inputs=('input',), outputs=('output', 'data')),
        _Node('0x2', 'llm', 'answer',
              props={'prompt': 'say hi', 'system_message': '',
                     'input_source': 'none'},
              inputs=('input', 'prompt', 'context'),
              outputs=('output', 'context')),
        _Node('0x3', 'conditional', 'ok?',
              props={'condition_type': 'llm', 'llm_prompt': 'ok?'},
              inputs=('input',), outputs=('true', 'false')),
        _Node('0x4', 'output', 'report', props={'label': 'reply'}),
    ])


def ctx_for(graph, selected=()):
    return {
        'graph_manager': graph,
        'run_on_gui': lambda fn: fn(),
        'selection_node_ids': lambda: list(selected),
        'stop': lambda: False,
        'history': [],
    }


# ------------------------------------------------------------- edit_node

def test_edit_node_writes_a_declared_field_on_the_selected_node():
    from player.builder import tools

    graph = canvas()
    text = tools.edit_node(ctx_for(graph, ['0x2']),
                           {'node': '0x2', 'values': {'prompt': 'say hello'}})

    assert graph.get_node_by_id('0x2').get_property('prompt') == 'say hello'
    assert 'say hello' in text and 'say hi' in text, text   # before -> after


def test_edit_node_refuses_a_node_the_user_did_not_select():
    from player.builder import tools
    from player.builder.errors import ToolArgumentError

    graph = canvas()
    with pytest.raises(ToolArgumentError) as err:
        tools.edit_node(ctx_for(graph, ['0x1']),
                        {'node': '0x4', 'values': {'label': 'x'}})

    assert 'is not selected' in str(err.value)
    assert graph.get_node_by_id('0x4').get_property('label') == 'reply'


def test_edit_node_refuses_a_field_the_type_does_not_declare():
    from player.builder import tools
    from player.builder.errors import ToolArgumentError

    with pytest.raises(ToolArgumentError) as err:
        tools.edit_node(ctx_for(canvas(), ['0x2']),
                        {'node': '0x2', 'values': {'no_such_field': 'x'}})

    assert 'no settable no_such_field' in str(err.value)
    assert 'prompt' in str(err.value), 'the refusal lists what IS settable'


def test_edit_node_needs_a_value_object():
    from player.builder import tools
    from player.builder.errors import ToolArgumentError

    with pytest.raises(ToolArgumentError):
        tools.edit_node(ctx_for(canvas(), ['0x2']), {'node': '0x2'})


# ------------------------------------------------------------ wire_nodes

def test_wire_nodes_connects_the_only_execution_pair():
    from player.builder import tools

    graph = canvas()
    text = tools.wire_nodes(ctx_for(graph, ['0x1', '0x2']),
                            {'from': '0x1', 'to': '0x2'})

    assert text.startswith('connected 0x1.output -> 0x2.input'), text
    assert graph.get_node_by_id('0x2')._in['input'].connections


def test_wire_nodes_needs_exactly_the_two_selected_nodes():
    from player.builder import tools
    from player.builder.errors import ToolArgumentError

    for selected in (['0x1'], ['0x1', '0x2', '0x3'], []):
        with pytest.raises(ToolArgumentError) as err:
            tools.wire_nodes(ctx_for(canvas(), selected),
                             {'from': '0x1', 'to': '0x2'})
        assert 'TWO nodes' in str(err.value)
    # ...and the pair must be the one asked for, in either direction
    with pytest.raises(ToolArgumentError):
        tools.wire_nodes(ctx_for(canvas(), ['0x3', '0x4']),
                         {'from': '0x1', 'to': '0x2'})


def test_wire_nodes_refuses_to_guess_between_two_execution_outputs():
    """A check has `true` and `false`: picking one for the user is a guess."""
    from player.builder import tools
    from player.builder.errors import ToolArgumentError

    with pytest.raises(ToolArgumentError) as err:
        tools.wire_nodes(ctx_for(canvas(), ['0x3', '0x4']),
                         {'from': '0x3', 'to': '0x4'})

    message = str(err.value)
    assert 'from_port' in message and 'to_port' in message
    assert 'true (branch true)' in message and 'false (branch false)' in message


def test_wire_nodes_carries_the_ports_it_was_given():
    from player.builder import tools

    graph = canvas()
    tools.wire_nodes(ctx_for(graph, ['0x3', '0x4']),
                     {'from': '0x3', 'to': '0x4',
                      'from_port': 'true', 'to_port': 'input'})

    assert graph.get_node_by_id('0x3')._out['true'].connections


def test_wire_nodes_refuses_an_illegal_role_pair():
    from player.builder import tools
    from player.builder.errors import ToolArgumentError

    with pytest.raises(ToolArgumentError) as err:
        tools.wire_nodes(ctx_for(canvas(), ['0x1', '0x2']),
                         {'from': '0x1', 'to': '0x2',
                          'from_port': 'data', 'to_port': 'input'})

    assert 'cannot feed' in str(err.value)


def test_wire_nodes_disconnects_the_wire_it_finds():
    from player.builder import tools

    graph = canvas()
    ctx = ctx_for(graph, ['0x1', '0x2'])
    tools.wire_nodes(ctx, {'from': '0x1', 'to': '0x2'})

    text = tools.wire_nodes(ctx, {'from': '0x1', 'to': '0x2',
                                  'action': 'disconnect'})

    assert text.startswith('disconnected'), text
    assert not graph.get_node_by_id('0x2')._in['input'].connections


def test_wire_nodes_refuses_to_disconnect_a_wire_that_does_not_exist():
    from player.builder import tools
    from player.builder.errors import ToolArgumentError

    with pytest.raises(ToolArgumentError):
        tools.wire_nodes(ctx_for(canvas(), ['0x1', '0x2']),
                         {'from': '0x1', 'to': '0x2', 'action': 'disconnect'})


def test_the_old_describe_tool_is_gone_from_the_chat():
    from player.builder import tools
    from player.builder.errors import UnknownToolError

    assert 'describe_chain' not in tools.TOOL_NAMES
    with pytest.raises(UnknownToolError):
        tools.execute('describe_chain', {'scope': 'walk'}, ctx_for(canvas()))


# -------------------------------------------------------------- a turn

def _answer(actions=(), reply='done'):
    calls = []

    def fake_generate(prompt, system, settings, on_delta=None, cancel=None):
        calls.append(prompt)
        if len(calls) == 1 and actions:
            return json.dumps({'reply': '', 'actions': list(actions)})
        return json.dumps({'reply': reply, 'actions': []})

    return fake_generate, calls


def test_a_turn_that_changed_the_canvas_reports_it(monkeypatch):
    from player.builder import assistant

    graph = canvas()
    fake, calls = _answer([{'name': 'edit_node',
                            'arguments': {'node': '0x2',
                                          'values': {'prompt': 'be kind'}}}],
                          reply='Changed the prompt.')
    monkeypatch.setattr(assistant._reasoning, 'generate', fake)

    result = assistant.BuilderAssistant(
        ctx_for(graph, ['0x2']), {'model': 'test'}).route(
        'make the prompt say be kind')

    assert result['changed'] is True
    assert graph.get_node_by_id('0x2').get_property('prompt') == 'be kind'
    assert 'TOOL RESULTS' in calls[1], 'the model sees what the tool did'
    # ...and the turn still writes no description and starts no walk.
    assert result['flow'] == {} and result['walk'] is False


def test_a_question_turn_changes_nothing(monkeypatch):
    from player.builder import assistant

    fake, _calls = _answer(reply='It asks, then checks.')
    monkeypatch.setattr(assistant._reasoning, 'generate', fake)

    result = assistant.BuilderAssistant(
        ctx_for(canvas()), {'model': 'test'}).route('what does it do?')

    assert result['changed'] is False


def test_a_refused_change_is_raised_not_swallowed(monkeypatch):
    from player.builder import assistant
    from player.builder.errors import ToolArgumentError

    fake, _calls = _answer([{'name': 'edit_node',
                             'arguments': {'node': '0x4',
                                           'values': {'label': 'x'}}}])
    monkeypatch.setattr(assistant._reasoning, 'generate', fake)

    with pytest.raises(ToolArgumentError):
        assistant.BuilderAssistant(ctx_for(canvas(), ['0x1']),
                                   {'model': 'test'}).route('rename the report')


def test_the_prompt_teaches_the_selected_node_rules_and_no_describing():
    from player.builder import assistant

    system = assistant._system_prompt()

    assert 'edit_node' in system and 'wire_nodes' in system
    assert 'exactly two' in system.lower()
    assert 'describe_chain' not in system
    assert 'Describe button' in system and "'flow'" in system


# --------------------------------------------------------------- panel

def _panel(monkeypatch, chain_file=''):
    from NodeGraphQt import NodeGraph

    from NGUI.graph_elements.flow_overlay import FlowOverlay
    from NGUI.nodes import InputNode, OutputNode
    from NGUI.widgets.builder_panel import BuilderPanel

    graph = NodeGraph()
    for cls in (InputNode, OutputNode):
        graph.register_node(cls)
    ask = graph.create_node('input.InputNode', name='ask', pos=[0, 0])
    out = graph.create_node('output.OutputNode', name='report', pos=[0, 200])
    ask.outputs()['output'].connect_to(out.inputs()['input'])

    overlay = FlowOverlay(graph)
    stored, saved = {}, []

    def _view():
        return SimpleNamespace(
            _chain_epoch=0,
            graph_manager=SimpleNamespace(get_all_nodes=graph.all_nodes,
                                          get_node_by_id=graph.get_node_by_id),
            config_manager=SimpleNamespace(_current_chain_file=chain_file),
            get_flow=lambda: dict(stored),
            set_flow=lambda meta: stored.update(meta),
            flow_overlay=lambda: overlay,
            save_chain=lambda path: saved.append(path))

    monkeypatch.setattr(BuilderPanel, '_hard_error',
                        lambda self, title, text: pytest.fail(text))
    panel = BuilderPanel(SimpleNamespace(graph_view=_view()))
    started = []
    panel._start_turn = lambda message: started.append(message)
    return panel, started, saved


def test_every_message_is_a_turn_never_a_walkthrough(qapp, monkeypatch):
    """The per-node highlight is the EYE button: the chat does not route."""
    panel, started, _saved = _panel(monkeypatch)

    panel._input.setText('how does this chain flow? show me')
    panel._on_send()

    assert started == ['how does this chain flow? show me']
    assert not panel._walk_steps


def test_a_changed_turn_writes_the_chain_file(qapp, monkeypatch, tmp_path):
    path = tmp_path / 'chain.json'
    path.write_text('{}', encoding='utf-8')
    panel, _started, saved = _panel(monkeypatch, chain_file=str(path))

    panel._annotate({'changed': True})

    assert saved == [str(path)]


def test_an_answer_writes_nothing(qapp, monkeypatch, tmp_path):
    path = tmp_path / 'chain.json'
    path.write_text('{}', encoding='utf-8')
    panel, _started, saved = _panel(monkeypatch, chain_file=str(path))

    panel._annotate({'changed': False, 'flow': {}, 'walk': False})

    assert saved == []
