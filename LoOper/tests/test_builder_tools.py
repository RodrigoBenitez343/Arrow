"""Builder tool execution — the canvas-tool contract (no GUI needed).

Run:  python -m pytest LoOper/tests/test_builder_tools.py
"""

import json
import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder.devtools import (  # noqa: E402
    execute_builder_call,
    parse_legacy_directives,
)
from player.builder.node_templates import (  # noqa: E402
    NODE_TYPES,
    NODE_REGISTRY_KEY,
    DECLINED_TYPES,
    allowed_fields,
    short_type,
    slugify,
)


# ---------------------------------------------------------------------------
# Fakes — the executor only needs the duck-typed canvas contract
# ---------------------------------------------------------------------------

class _FakePort:
    def __init__(self, name):
        self._name = name
        self._connected = []

    def name(self):
        return self._name

    def connected_ports(self):
        return list(self._connected)


class _FakeNode:
    def __init__(self, nid, ntype, name=''):
        self.id = nid
        self.type_ = ntype
        self._name = name or ''
        self._props = {}
        self._in = []
        self._out = []

    def name(self):
        return self._name

    def input_ports(self):
        return list(self._in)

    def output_ports(self):
        return list(self._out)

    def add_input(self, name):
        p = _FakePort(name)
        p.node = self
        self._in.append(p)
        return p

    def add_output(self, name):
        p = _FakePort(name)
        p.node = self
        self._out.append(p)
        return p

    def get_property(self, key):
        return self._props.get(key)

    def set_property(self, key, value):
        self._props[key] = value

    def find_port(self, port_name, is_input):
        ports = self._in if is_input else self._out
        for p in ports:
            if p.name() == port_name:
                return p
        return None


class _FakeGraphManager:
    def __init__(self):
        self.nodes = {}
        self._n = 0
        self.created_keys = []      # exactly what the canvas was asked to make

    def create_node(self, node_type, name=None, pos=None):
        self.created_keys.append(node_type)
        self._n += 1
        node = _FakeNode('0x%x' % self._n, node_type, name or '')
        self.nodes[node.id] = node
        return node

    def get_node_by_id(self, nid):
        return self.nodes.get(nid)

    def get_all_nodes(self):
        return list(self.nodes.values())

    def connect_nodes(self, output_node, input_node,
                      output_port=None, input_port=None):
        op = output_node.find_port(output_port, False)
        ip = input_node.find_port(input_port, True)
        if op is None or ip is None:
            return False
        ip._connected.append(op)
        return True

    def delete_node(self, node):
        self.nodes.pop(getattr(node, 'id', None), None)


class _FakeConfigManager:
    def __init__(self):
        self._current_chain_collection = ''
        self._current_chain_description = ''
        self._saved = None

    def get_chain_config(self):
        return {
            'sequences': [], 'conditional_nodes': [], 'llm_nodes': [],
            'chain_import_nodes': [], 'form_filler_nodes': [],
            'code_nodes': [], 'container_nodes': [], 'context_nodes': [],
            'input_nodes': [], 'handle_nodes': [], 'mcp_nodes': [],
            'output_nodes': [], 'web_sequences': [],
            'collection': self._current_chain_collection,
            'description': self._current_chain_description,
        }


def _ctx(mgr=None, cm=None, tmpdir=None):
    return {
        'graph_manager': lambda: mgr,
        'config_manager': lambda: cm,
        'state': {},
        'ask': None,
        'chains_dir': tmpdir or '',
    }


# ---------------------------------------------------------------------------
# create_node
# ---------------------------------------------------------------------------

def test_create_node_records_last_node_and_label():
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    out = execute_builder_call(
        'create_node', {'node_type': 'input', 'label': 'goal'}, ctx)
    assert out.startswith('created input node')
    nid = ctx['state']['last_node_id']
    assert nid in mgr.nodes
    assert short_type(mgr.nodes[nid].type_) == 'input'
    assert mgr.nodes[nid].name() == 'goal'


def test_create_node_passes_the_canvas_registry_key():
    """GraphManager.create_node needs the registered '<module>.<ClassName>' —
    passing the short name fails with 'Can't find node: "code"'."""
    mgr = _FakeGraphManager()
    out = execute_builder_call(
        'create_node', {'node_type': 'code', 'label': 'scrape'}, _ctx(mgr))
    assert out.startswith('created code node')
    assert mgr.created_keys == ['code.CodeNode']


def test_create_node_accepts_a_registry_key():
    mgr = _FakeGraphManager()
    out = execute_builder_call(
        'create_node', {'node_type': 'code.CodeNode', 'label': 'x'},
        _ctx(mgr))
    assert out.startswith('created code node')
    assert mgr.created_keys == ['code.CodeNode']


def test_create_node_gives_an_input_node_data_only_passthrough():
    """A builder-made Input node forwards data — it must NOT type on the
    user's screen (the node default is passthrough OFF = real typing)."""
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    execute_builder_call(
        'create_node', {'node_type': 'input', 'label': 'q'}, ctx)
    node = mgr.nodes[ctx['state']['last_node_id']]
    assert node.get_property('passthrough') is True
    # The 'label' PROPERTY (not just the node name) is what the chain config
    # and the test's input-binding read.
    assert node.get_property('label') == 'q'


def test_create_node_gives_an_llm_node_a_local_model_default():
    """A builder-made LLM node must not be a dead 'no model' node, and it
    must not type its answer onto the screen (write_text defaults ON)."""
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    execute_builder_call(
        'create_node', {'node_type': 'llm', 'label': 'brain'}, ctx)
    node = mgr.nodes[ctx['state']['last_node_id']]
    assert node.get_property('use_llamacpp') == 'true'
    assert node.get_property('write_text') == 'false'


def test_every_creatable_type_has_a_registry_key():
    assert set(NODE_TYPES) <= set(NODE_REGISTRY_KEY)


def test_short_type_normalises_every_form():
    assert short_type('code.CodeNode') == 'code'
    assert short_type('web_sequence.WebSequenceNode') == 'web_sequence'
    assert short_type('CodeNode') == 'code'
    assert short_type('llm') == 'llm'


def test_create_node_declines_mcp_and_container():
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    for declined in DECLINED_TYPES:
        out = execute_builder_call(
            'create_node', {'node_type': declined, 'label': 'x'}, ctx)
        assert out.startswith('(the builder does not create')
    assert not mgr.nodes


def test_create_node_rejects_unknown_type():
    mgr = _FakeGraphManager()
    out = execute_builder_call(
        'create_node', {'node_type': 'nonsense', 'label': 'x'}, _ctx(mgr))
    assert out.startswith('(unknown node_type')
    assert not mgr.nodes


def test_create_node_accepts_every_listed_type(tmp_path):
    mgr = _FakeGraphManager()
    chain = tmp_path / 'sub.json'
    chain.write_text('{}', encoding='utf-8')
    for node_type in NODE_TYPES:
        args = {'node_type': node_type, 'label': 'n'}
        if node_type == 'chain_import':
            # Requires a real file — an empty import node is a dead node.
            args['chain_file_path'] = str(chain)
        out = execute_builder_call('create_node', args, _ctx(mgr))
        assert out.startswith('created'), (node_type, out)


# ---------------------------------------------------------------------------
# wire
# ---------------------------------------------------------------------------

def test_wire_validates_input_port_and_never_falls_back():
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    src = mgr.create_node('input', 'a')
    src.add_output('output')
    dst = mgr.create_node('llm', 'b')
    dst.add_input('input')

    out = execute_builder_call(
        'wire', {'from_node_id': src.id, 'to_node_id': dst.id,
                 'input_port': 'brains'}, ctx)
    assert out.startswith('(wire: node')
    assert 'available: input' in out
    # Nothing connected — the silent port-0 fallback never happens.
    assert not dst.find_port('input', True)._connected


def test_wire_connects_on_valid_ports():
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    src = mgr.create_node('input', 'a')
    src.add_output('output')
    dst = mgr.create_node('llm', 'b')
    dst.add_input('tools')
    dst.add_input('brains')

    out = execute_builder_call(
        'wire', {'from_node_id': src.id, 'to_node_id': dst.id,
                 'input_port': 'brains'}, ctx)
    assert out == 'wired %s.output -> %s.brains' % (src.id, dst.id)
    assert dst.find_port('brains', True)._connected


def test_wire_reports_missing_nodes():
    mgr = _FakeGraphManager()
    out = execute_builder_call(
        'wire', {'from_node_id': 'nope', 'to_node_id': 'nope2',
                 'input_port': 'input'}, _ctx(mgr))
    assert out == "(wire: no node matches 'nope')"


def test_wire_resolves_nodes_by_label_and_source_port():
    """Labels are what read_graph shows — wiring by label (with an
    explicit source port, e.g. an input's 'data') must work."""
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    src = mgr.create_node('input.InputNode', 'goal slot')
    src.add_output('output')
    src.add_output('data')
    dst = mgr.create_node('llm.LLMNode', 'brain')
    dst.add_input('input')
    dst.add_input('prompt')

    out = execute_builder_call(
        'wire', {'from_node': 'goal slot', 'to_node': 'brain',
                 'input_port': 'prompt', 'output_port': 'data'}, ctx)
    # The data edge, PLUS the exec edge that decides when the node runs.
    assert out.startswith('wired %s.data -> %s.prompt' % (src.id, dst.id))
    assert '%s.output -> %s.input (exec)' % (src.id, dst.id) in out
    assert dst.find_port('prompt', True)._connected


def test_wire_auto_port_classifies_chain_import(tmp_path):
    """input_port omitted: chain_import -> LLM lands on tools for a plain
    chain and on brains for a chain that contains an orchestrator."""
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    plain = tmp_path / 'plain.json'
    plain.write_text(json.dumps(
        {'web_sequences': [{'sequence_file': 'x'}]}), encoding='utf-8')
    brain_file = tmp_path / 'brainy.json'
    brain_file.write_text(json.dumps(
        {'llm_nodes': [{'orchestrator_mode': True}]}), encoding='utf-8')

    brain = mgr.create_node('llm.LLMNode', 'brain')
    brain.add_input('input')
    brain.add_input('tools')
    brain.add_input('brains')

    for chain_file, expected in ((plain, 'tools'), (brain_file, 'brains')):
        imp = mgr.create_node('chain_import.ChainImportNode', chain_file.stem)
        imp.add_output('output')
        imp._props['chain_file_path'] = str(chain_file)
        out = execute_builder_call(
            'wire', {'from_node': chain_file.stem, 'to_node': 'brain'}, ctx)
        assert out.startswith('wired %s.output -> %s.%s' % (
            imp.id, brain.id, expected))


# ---------------------------------------------------------------------------
# run_on_gui hook + recorded-node removal
# ---------------------------------------------------------------------------

def test_execute_canvas_ops_go_through_the_gui_hook_but_ask_user_does_not():
    """The panel's run_on_gui hook wraps every canvas op; ask_user is
    excluded (it blocks on the user — running it on the GUI thread would
    freeze the window it waits in)."""
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    calls = []
    ctx['run_on_gui'] = lambda fn: calls.append(1) or fn()
    ctx['ask'] = lambda q, k: 'ok'

    out = execute_builder_call(
        'create_node', {'node_type': 'input', 'label': 'x'}, ctx)
    assert out.startswith('created input node')
    assert len(calls) == 1

    out = execute_builder_call('ask_user',
                               {'question': 'q', 'kind': 'answer'}, ctx)
    assert out == 'user said: ok'
    assert len(calls) == 1                      # ask_user skipped the hook


def test_delete_node_removes_only_recorded_orphans():
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)

    rec = mgr.create_node('web_sequence.WebSequenceNode', 'rec')
    rec.add_output('output')
    out = execute_builder_call('delete_node', {'node_id': rec.id}, ctx)
    assert out.startswith('removed recorded web_sequence')
    assert rec.id not in mgr.nodes

    # A wired recording is refused — the wrap flow never pulls a live
    # wiring apart.
    wired = mgr.create_node('sequence.SequenceNode', 'wired')
    port = wired.add_output('output')
    port._connected.append(object())
    out = execute_builder_call('delete_node', {'node_id': wired.id}, ctx)
    assert 'still has outgoing connections' in out
    assert wired.id in mgr.nodes

    # Only recorded kinds are removable through this op.
    brain = mgr.create_node('llm.LLMNode', 'brain')
    out = execute_builder_call('delete_node', {'node_id': brain.id}, ctx)
    assert 'not removable here' in out
    assert brain.id in mgr.nodes


# ---------------------------------------------------------------------------
# edit_node
# ---------------------------------------------------------------------------

def test_edit_node_allowlist_refuses_and_lists_allowed():
    mgr = _FakeGraphManager()
    node = mgr.create_node('sequence', 's')
    out = execute_builder_call(
        'edit_node', {'node_id': node.id, 'property': 'prompt',
                      'value': 'x'}, _ctx(mgr))
    assert out.startswith('(edit_node: property')
    assert 'sequence_file' in out
    assert 'prompt' not in node._props


def test_edit_node_sets_allowed_property():
    mgr = _FakeGraphManager()
    node = mgr.create_node('input', 'i')
    out = execute_builder_call(
        'edit_node', {'node_id': node.id, 'property': 'agent_modifiable',
                      'value': True}, _ctx(mgr))
    assert out.startswith('set %s.agent_modifiable' % node.id)
    # Canonicalised to the string the node + executor read (not the native bool).
    assert node.get_property('agent_modifiable') == 'true'


def test_edit_node_normalizes_bool_and_enum():
    mgr = _FakeGraphManager()
    node = mgr.create_node('llm', 'l')
    execute_builder_call(
        'edit_node', {'node_id': node.id, 'property': 'use_llamacpp',
                      'value': True}, _ctx(mgr))
    assert node.get_property('use_llamacpp') == 'true'
    execute_builder_call(
        'edit_node', {'node_id': node.id, 'property': 'input_source',
                      'value': 'Page Text'}, _ctx(mgr))
    assert node.get_property('input_source') == 'page_text'


# ---------------------------------------------------------------------------
# configure_node (Laya field/option resolver)
# ---------------------------------------------------------------------------

def test_configure_node_writes_resolved_enum(monkeypatch):
    import player.builder.devtools as dt
    mgr = _FakeGraphManager()
    node = mgr.create_node('llm', 'l')

    def fake_choice(state, instructions, criteria):
        if 'field' in instructions.lower():
            return 'input_source' if 'input_source' in criteria else None
        return 'page_text' if 'page_text' in criteria else None

    monkeypatch.setattr(dt, '_laya_choice', fake_choice)
    out = execute_builder_call(
        'configure_node', {'node_id': node.id, 'ask': 'read the page text'},
        _ctx(mgr))
    assert out.startswith('configured %s.input_source' % node.id)
    assert node.get_property('input_source') == 'page_text'


def test_configure_node_reports_the_field_for_open_kinds(monkeypatch):
    import player.builder.devtools as dt
    mgr = _FakeGraphManager()
    node = mgr.create_node('llm', 'l')
    monkeypatch.setattr(dt, '_laya_choice',
                        lambda state, instructions, criteria: 'prompt')
    out = execute_builder_call(
        'configure_node', {'node_id': node.id, 'ask': 'tell it to summarize'},
        _ctx(mgr))
    assert 'resolved field' in out and 'prompt' in out
    assert node.get_property('prompt') is None


def test_configure_node_fails_open_without_laya(monkeypatch):
    import player.builder.devtools as dt
    mgr = _FakeGraphManager()
    node = mgr.create_node('llm', 'l')
    monkeypatch.setattr(dt, '_laya_choice',
                        lambda state, instructions, criteria: None)
    out = execute_builder_call(
        'configure_node', {'node_id': node.id, 'ask': 'do something'},
        _ctx(mgr))
    assert out.startswith('(configure_node: could not map')
    assert node._props == {}


def test_configure_node_cannot_flip_the_orchestrator_switch(monkeypatch):
    """A content ask must never turn a plain LLM step into an orchestrator:
    the structural switch is kept out of the field menu Laya picks from."""
    import player.builder.devtools as dt
    mgr = _FakeGraphManager()
    node = mgr.create_node('llm', 'l')
    seen = {}

    def fake_choice(state, instructions, criteria):
        seen['criteria'] = dict(criteria)
        return None

    monkeypatch.setattr(dt, '_laya_choice', fake_choice)
    execute_builder_call(
        'configure_node', {'node_id': node.id, 'ask': 'summarize the post'},
        _ctx(mgr))
    assert 'orchestrator_mode' not in seen['criteria']
    assert 'prompt' in seen['criteria']
    assert node.get_property('orchestrator_mode') is None


def test_edit_node_unknown_node_reports():
    mgr = _FakeGraphManager()
    out = execute_builder_call(
        'edit_node', {'node_id': '0xzz', 'property': 'label', 'value': 'x'},
        _ctx(mgr))
    assert out == "(edit_node: no node matches '0xzz')"


# ---------------------------------------------------------------------------
# set_description / save_chain / read_graph
# ---------------------------------------------------------------------------

def test_set_description_lands_in_state_and_config_manager():
    cm = _FakeConfigManager()
    ctx = _ctx(_FakeGraphManager(), cm)
    out = execute_builder_call('set_description', {'text': 'applies to jobs'},
                               ctx)
    assert out.startswith('chain description set')
    assert ctx['state']['description'] == 'applies to jobs'
    assert cm._current_chain_description == 'applies to jobs'


def test_save_chain_writes_system_collection(tmp_path):
    mgr = _FakeGraphManager()
    cm = _FakeConfigManager()
    ctx = _ctx(mgr, cm, str(tmp_path))
    ctx['state']['description'] = 'my bot'
    out = execute_builder_call('save_chain', {'name': 'My Bot'}, ctx)
    assert out.startswith('saved system chain')
    saved = tmp_path / 'my_bot.json'
    assert saved.exists()
    cfg = json.loads(saved.read_text(encoding='utf-8'))
    assert cfg['collection'] == 'System'
    assert cfg['description'] == 'my bot'


def test_save_chain_requires_writable_dir():
    ctx = _ctx(_FakeGraphManager(), _FakeConfigManager(), '')
    out = execute_builder_call('save_chain', {'name': 'x'}, ctx)
    assert out.startswith('(save_chain:')


def test_read_graph_renders_nodes_and_connections():
    mgr = _FakeGraphManager()
    ctx = _ctx(mgr)
    a = mgr.create_node('input', 'goal')
    a.add_output('output')
    b = mgr.create_node('llm', 'brain')
    b.add_input('input')
    mgr.connect_nodes(a, b, 'output', 'input')
    out = execute_builder_call('read_graph', {}, ctx)
    assert 'input' in out and 'llm' in out
    assert '%s.output -> input' % a.id in out


def test_read_graph_empty_canvas():
    out = execute_builder_call('read_graph', {}, _ctx(_FakeGraphManager()))
    assert out == '(no nodes yet)'


# ---------------------------------------------------------------------------
# ask_user / legacy fallback
# ---------------------------------------------------------------------------

def test_ask_user_forwards_to_callback():
    captured = {}

    def ask(question, kind):
        captured['q'], captured['k'] = question, kind
        return 'linkedin.com'

    ctx = {'graph_manager': None, 'config_manager': None, 'state': {},
           'ask': ask, 'chains_dir': ''}
    out = execute_builder_call(
        'ask_user', {'question': 'where are your messages?', 'kind': 'answer'},
        ctx)
    assert captured['q'] == 'where are your messages?'
    assert captured['k'] == 'answer'
    assert out == 'user said: linkedin.com'


def test_ask_user_rejects_unknown_kind():
    ctx = {'graph_manager': None, 'config_manager': None, 'state': {},
           'ask': lambda q, k: '', 'chains_dir': ''}
    out = execute_builder_call(
        'ask_user', {'question': 'x', 'kind': 'teleport'}, ctx)
    assert out.startswith('(ask_user: unknown kind')


def test_parse_legacy_directives_subset():
    text = (
        '### BUILDER: create_node type=input label=goal\n'
        '### BUILDER: wire 0x1 0x2 input\n'
        '### BUILDER: edit 0x1 label hello world\n'
        'random prose\n'
        '### BUILDER: save_chain name=bot\n'  # not in the safe subset
    )
    calls = parse_legacy_directives(text)
    names = [c[0] for c in calls]
    assert names == ['create_node', 'wire', 'edit']
    assert calls[0][1] == {'node_type': 'input', 'label': 'goal'}
    assert calls[1][1] == {'from_node_id': '0x1', 'to_node_id': '0x2',
                           'input_port': 'input'}
    assert calls[2][1] == {'node_id': '0x1', 'property': 'label',
                           'value': 'hello world'}


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

def test_allowlists_cover_every_creatable_type():
    for node_type in NODE_TYPES:
        assert allowed_fields(node_type), node_type


def test_slugify_filenames():
    assert slugify('My Bot!') == 'my_bot'
    assert slugify('') == 'built_chain'
