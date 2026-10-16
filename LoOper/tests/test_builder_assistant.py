"""Builder assistant — selection-aware chat, node ops, skill ladders.

Run:  python -m pytest LoOper/tests/test_builder_assistant.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder import reasoning  # noqa: E402
from player.builder.assistant import (  # noqa: E402
    BuilderAssistant,
    _extract_reply,
)
from player.builder.devtools import (  # noqa: E402
    describe_node,
    execute_builder_call,
)


# --------------------------------------------------------------------------- fakes


_PORTS = {
    'input': (['input'], ['output', 'data']),
    'llm': (['input', 'prompt', 'context'], ['output', 'context']),
    'output': (['input'], ['output']),
    'code': (['input'], ['output']),
    'conditional': (['input'], ['true', 'false']),
    'context': (['ctx_in'], ['ctx_out']),
}


class _Port:
    def __init__(self, name):
        self._name = name
        self._connected = []
        self.node = None

    def name(self):
        return self._name

    def connected_ports(self):
        return list(self._connected)


class _Node:
    def __init__(self, nid, type_key, name=''):
        self.id = nid
        self.type_ = type_key
        self._name = name or ''
        self._props = {}
        ins, outs = _PORTS.get(type_key, (['input'], ['output']))
        self._in = [_Port(n) for n in ins]
        self._out = [_Port(n) for n in outs]
        for p in self._in + self._out:
            p.node = self

    def name(self):
        return self._name

    def pos(self):
        return [0.0, 0.0]

    def input_ports(self):
        return list(self._in)

    def output_ports(self):
        return list(self._out)

    def get_property(self, key):
        return self._props.get(key)

    def set_property(self, key, value):
        self._props[key] = value

    def rebuild_ports(self):
        """Mirror CodeNode.rebuild_ports: declare named IO ports."""
        for prop, ports in (('input_vars', self._in),
                            ('output_vars', self._out)):
            raw = self._props.get(prop) or '[]'
            try:
                rows = json.loads(raw) if isinstance(raw, str) else (raw or [])
            except Exception:
                rows = []
            for row in rows:
                nm = ''
                if isinstance(row, dict):
                    nm = str(row.get('name') or '').strip()
                if nm and not any(p.name() == nm for p in ports):
                    p = _Port(nm)
                    p.node = self
                    ports.append(p)


class _Mgr:
    def __init__(self):
        self.nodes = {}
        self._n = 0
        self.wires = []

    def create_node(self, node_type, name=None, pos=None):
        self._n += 1
        short = str(node_type).split('.')[0]
        node = _Node('0x%x' % self._n, short, name or '')
        self.nodes[node.id] = node
        return node

    def get_node_by_id(self, nid):
        return self.nodes.get(nid)

    def get_all_nodes(self):
        return list(self.nodes.values())

    def connect_nodes(self, src, dst, out_port, in_port):
        op = next((p for p in src.output_ports() if p.name() == out_port),
                  None)
        ip = next((p for p in dst.input_ports() if p.name() == in_port),
                  None)
        if op is None or ip is None:
            return False
        ip._connected.append(op)
        self.wires.append((src.id, op.name(), dst.id, ip.name()))
        return True

    def delete_node(self, node):
        self.nodes.pop(getattr(node, 'id', None), None)


def _ctx(mgr=None, selection='', history=None):
    return {
        'graph_manager': mgr if mgr is not None else _Mgr(),
        'config_manager': None,
        'state': {},
        'ask': None,
        'chains_dir': '',
        'selection_text': (lambda: selection),
        'history': history or [],
    }


def _by_type(mgr, node_type):
    return next(n for n in mgr.nodes.values() if n.type_ == node_type)


# --------------------------------------------------------------------------- reply parsing


def test_extract_reply_forms():
    reply, acts, parsed = _extract_reply(
        '{"reply": "hi", "actions": [{"name": "read_node", '
        '"arguments": {"node": "x"}}]}')
    assert reply == 'hi'
    assert acts and acts[0]['name'] == 'read_node'
    assert parsed is True

    # Fenced / prose-wrapped JSON still parses (balanced-brace scan).
    reply, acts, parsed = _extract_reply(
        'Sure, here it is:\n```json\n{"reply": "ok", "actions": []}\n```')
    assert reply == 'ok'
    assert acts == []
    assert parsed is True

    # Broken JSON is plain prose (answers need no JSON at all).
    raw = 'Just prose about {nodes'
    reply, acts, parsed = _extract_reply(raw)
    assert reply == raw
    assert acts == []
    assert parsed is False


def test_extract_reply_tolerates_raw_newlines_in_code():
    """A model that leaves RAW newlines inside an action's Python ``code``
    string must not lose the whole action set (default strict JSON rejects
    the control character — the builder then ran zero actions, silently)."""
    reply, acts, parsed = _extract_reply(
        '{"reply": "Building it.", "actions": [{"name": "build_skill", '
        '"arguments": {"code": "import json\nresult = str(data)"}}]}')
    assert parsed is True
    assert acts and acts[0]['name'] == 'build_skill'


# --------------------------------------------------------------------------- assistant turn


def test_route_answers_plain_text(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    monkeypatch.setattr(
        reasoning, '_generate',
        lambda prompt, settings, system: 'An LLM node runs a prompt on its '
        'input data; wire text to its prompt port to replace it.')
    out = BuilderAssistant(_ctx(), settings={'reasoning': True}).route(
        'what is an llm node?')
    assert out['ok'] is True
    assert 'LLM node runs a prompt' in out['text']


def test_route_executes_edit_action(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    mgr = _Mgr()
    node = mgr.create_node('llm.LLMNode', 'brain')
    monkeypatch.setattr(reasoning, '_generate', lambda p, s, sys: json.dumps({
        'reply': 'Improving the prompt.',
        'actions': [{'name': 'edit_node', 'arguments': {
            'node': 'brain', 'property': 'prompt',
            'value': 'Summarize the post in three bullets.'}}]}))
    out = BuilderAssistant(_ctx(mgr), settings={'reasoning': True}).route(
        'write a good prompt for this node')
    assert node.get_property('prompt') == (
        'Summarize the post in three bullets.')
    assert 'set %s.prompt' % node.id in out['text']
    assert out['steps'] == 1


def test_route_skips_unknown_actions(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    monkeypatch.setattr(reasoning, '_generate', lambda p, s, sys: json.dumps({
        'reply': 'Trying.',
        'actions': [{'name': 'explode', 'arguments': {}}]}))
    out = BuilderAssistant(_ctx(), settings={'reasoning': True}).route('go')
    assert 'unknown action' in out['text']


def test_route_prompt_carries_canvas_selection_history(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    seen = {}

    def _gen(prompt, settings, system):
        seen['prompt'] = prompt
        seen['system'] = system
        return 'ok'

    monkeypatch.setattr(reasoning, '_generate', _gen)
    mgr = _Mgr()
    mgr.create_node('llm.LLMNode', 'brain')
    ctx = _ctx(mgr, selection='0x1 llm "brain"\n  editable values:\n'
                              '    prompt: hello')
    ctx['history'] = [{'role': 'user', 'text': 'earlier question'}]
    BuilderAssistant(ctx, settings={'reasoning': True}).route('ping')
    assert 'CANVAS' in seen['prompt'] and 'brain' in seen['prompt']
    assert 'SELECTED NODES' in seen['prompt']
    assert 'prompt: hello' in seen['prompt']
    assert 'USER: ping' in seen['prompt']
    assert 'earlier question' in seen['prompt']
    # The model is told how to build skills (the action protocol).
    assert 'build_skill' in seen['system']


def test_route_reports_when_gateway_or_model_missing(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: '')
    out = BuilderAssistant(_ctx(), settings={'reasoning': True}).route('hi')
    assert out['ok'] is False
    assert 'gateway' in out['text'].lower()

    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    out = BuilderAssistant(_ctx(), settings={'reasoning': False}).route('hi')
    assert out['ok'] is False
    assert 'switched off' in out['text']


def test_route_reports_when_model_says_nothing(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    monkeypatch.setattr(reasoning, '_generate', lambda p, s, sys: '')
    out = BuilderAssistant(_ctx(), settings={'reasoning': True}).route('hi')
    assert out['ok'] is False
    assert 'no answer' in out['text']


# --------------------------------------------------------------------------- build_skill


def test_build_skill_builds_a_thought_ladder():
    mgr = _Mgr()
    result = execute_builder_call('build_skill', {
        'name': 'scrape tendencies',
        'description': 'find trends in the collected posts',
        'steps': [
            {'kind': 'llm', 'label': 'read posts',
             'prompt': 'Summarize each post in one line.'},
            {'kind': 'code', 'label': 'count topics',
             'code': 'result = len(str(data).split())'},
            {'kind': 'conditional', 'label': 'enough data',
             'criterion': 'there are at least 5 posts'},
        ]}, _ctx(mgr))
    assert 'built skill "scrape tendencies"' in result

    kinds = sorted(n.type_ for n in mgr.nodes.values())
    assert kinds == ['code', 'conditional', 'context', 'input', 'llm',
                     'output']

    inp = _by_type(mgr, 'input')
    llm = _by_type(mgr, 'llm')
    code = _by_type(mgr, 'code')
    cond = _by_type(mgr, 'conditional')
    pool = _by_type(mgr, 'context')
    out_node = _by_type(mgr, 'output')

    wires = mgr.wires
    assert (inp.id, 'output', llm.id, 'input') in wires
    # The input's value also rides its 'data' port into the step's 'prompt'.
    assert (inp.id, 'data', llm.id, 'prompt') in wires
    assert (llm.id, 'output', code.id, 'data') in wires
    # The code step also gets its EXEC edge (when it runs), and leaves via
    # its named output var + its exec edge.
    assert (llm.id, 'output', code.id, 'input') in wires
    assert (code.id, 'result', cond.id, 'input') in wires
    assert (code.id, 'output', cond.id, 'input') in wires
    assert (cond.id, 'true', out_node.id, 'input') in wires
    assert (llm.id, 'context', pool.id, 'ctx_in') in wires
    assert (pool.id, 'ctx_out', llm.id, 'context') in wires
    assert len(wires) == 9

    # Local-first llm step: authored prompt, built-in engine, no typing.
    # Properties are canonical strings ('true'/'false'), not native bools.
    assert llm.get_property('prompt') == 'Summarize each post in one line.'
    assert llm.get_property('use_llamacpp') == 'true'
    assert llm.get_property('write_text') == 'false'
    assert llm.get_property('screenshot_enabled') == 'false'

    # The check is LLM-validated on the LOCAL engine by default.
    assert cond.get_property('condition_type') == 'llm'
    assert cond.get_property('llm_prompt') == 'there are at least 5 posts'
    assert cond.get_property('llm_engine') == 'llamacpp'

    # The code step carries the authored Python + its named IO ports.
    assert code.get_property('code') == 'result = len(str(data).split())'
    assert 'data' in str(code.get_property('input_vars'))
    assert 'result' in str(code.get_property('output_vars'))

    # Input forwards data only; the pool is a per-run scratchpad.
    assert inp.get_property('passthrough') == 'true'
    assert pool.get_property('persistent') == 'false'
    assert pool.get_property('clear_on_finish') == 'true'


def test_build_skill_without_llm_steps_skips_the_pool():
    mgr = _Mgr()
    result = execute_builder_call('build_skill', {
        'name': 'parse', 'steps': [
            {'kind': 'code', 'label': 'parse json',
             'code': 'result = input_data'}]}, _ctx(mgr))
    assert 'built skill' in result
    assert sorted(n.type_ for n in mgr.nodes.values()) == [
        'code', 'input', 'output']


def test_build_skill_requires_steps():
    out = execute_builder_call('build_skill', {'name': 'x'}, _ctx())
    assert out.startswith('(build_skill needs')


def test_build_skill_warns_on_broken_code_but_still_builds():
    mgr = _Mgr()
    out = execute_builder_call('build_skill', {
        'name': 'x', 'steps': [
            {'kind': 'code', 'label': 'bad', 'code': 'def broken('}]},
        _ctx(mgr))
    assert 'did not compile' in out
    assert 'built skill' in out


def test_build_skill_code_conditional_uses_the_code_evaluator():
    mgr = _Mgr()
    execute_builder_call('build_skill', {
        'name': 'gate', 'steps': [
            {'kind': 'conditional', 'label': 'has url',
             'code': 'result = "http" in str(input_data)'}]}, _ctx(mgr))
    cond = _by_type(mgr, 'conditional')
    assert cond.get_property('condition_type') == 'code'
    assert cond.get_property('code') == 'result = "http" in str(input_data)'


def test_build_skill_mixed_kind_accepts_numbered_results():
    """llm steps are chained through every kind; kinds default sensibly."""
    mgr = _Mgr()
    out = execute_builder_call('build_skill', {
        'name': 'mixed', 'steps': [
            {'label': 'read', 'prompt': 'Read it.'},          # llm default
            {'label': 'clean', 'code': 'result = input_data.strip()'},
        ]}, _ctx(mgr))
    assert 'built skill "mixed"' in out
    assert '2 step(s)' in out
    llm = _by_type(mgr, 'llm')
    code = _by_type(mgr, 'code')
    assert (llm.id, 'output', code.id, 'data') in mgr.wires


# --------------------------------------------------------------------------- read_node


def test_describe_node_lists_values_and_ports():
    mgr = _Mgr()
    node = mgr.create_node('llm.LLMNode', 'brain')
    node.set_property('prompt', 'Summarize.')
    node.set_property('orchestrator_mode', True)
    text = describe_node(node)
    assert 'llm "brain"' in text
    assert 'prompt: Summarize.' in text
    assert 'orchestrator_mode: True' in text
    assert 'inputs:' in text
    assert 'outputs:' in text


def test_read_node_tool_resolves_by_label():
    mgr = _Mgr()
    mgr.create_node('input.InputNode', 'goal slot')
    out = execute_builder_call('read_node', {'node': 'goal slot'},
                               _ctx(mgr))
    assert 'input "goal slot"' in out
    missing = execute_builder_call('read_node', {'node': 'nope'}, _ctx(mgr))
    assert missing.startswith('(read_node: no node matches')


# --------------------------------------------------------------------------- test_chain


class _FakeLE:
    def __init__(self, variables=None):
        self._v = dict(variables or {})

    def get_variable(self, name, default=None):
        return self._v.get(name, default)

    def get_all_variables(self):
        return dict(self._v)


class _FakeExec:
    def __init__(self, le):
        self.llm_executor = le


class _FakePlayer:
    def __init__(self, le):
        self.workflow_executor = _FakeExec(le)


class _CM:
    """Stand-in ConfigManager: serializes to a fixed chain dict."""

    def __init__(self, cfg):
        self._cfg = cfg

    def get_chain_config(self):
        return dict(self._cfg)


def _stub_runner(monkeypatch, result=None, le=None):
    """Stub ``play_chain_with_tailcalls`` without importing the real player."""
    import sys
    import types
    calls = {}

    def _play(path, agent_query='', stop_flag=None, run_source='play', **kw):
        calls['path'] = path
        calls['query'] = agent_query
        calls['existed'] = os.path.exists(path)
        calls['bound'] = (kw.get('variables') or {}).get('_chain_bound_inputs')
        return (True if result is None else result), _FakePlayer(
            le or _FakeLE({'node_OUT_output': 'THE ANSWER'}))

    fake = types.ModuleType('player.multi_sequence_player')
    fake.play_chain_with_tailcalls = _play
    monkeypatch.setitem(sys.modules, 'player.multi_sequence_player', fake)
    return calls


def test_test_chain_runs_canvas_and_reports_output(monkeypatch, tmp_path):
    calls = _stub_runner(monkeypatch)
    cfg = {'output_nodes': [{'node_id': 'OUT', 'label': 'answer'}]}
    ctx = {'config_manager': _CM(cfg), 'chains_dir': str(tmp_path)}
    out = execute_builder_call('test_chain', {'input': 'hello'}, ctx)
    assert 'test: completed' in out
    assert 'output answer: THE ANSWER' in out
    assert calls['query'] == 'hello'
    assert calls['existed']                       # temp chain was written
    assert not os.path.exists(calls['path'])      # ...and cleaned up


def test_test_chain_binds_a_single_input_by_label(monkeypatch, tmp_path):
    """A bare string input binds the chain's only Input slot by its label."""
    calls = _stub_runner(monkeypatch)
    cfg = {'input_nodes': [{'node_id': 'IN', 'label': 'query'}],
           'output_nodes': [{'node_id': 'OUT', 'label': 'answer'}]}
    ctx = {'config_manager': _CM(cfg), 'chains_dir': str(tmp_path)}
    out = execute_builder_call('test_chain', {'input': 'reddit'}, ctx)
    assert calls['bound'] == {'query': 'reddit'}
    assert 'input query: reddit' in out


def test_test_chain_binds_a_label_map(monkeypatch, tmp_path):
    """A {label: value} input binds several Input nodes at once."""
    calls = _stub_runner(monkeypatch)
    cfg = {'input_nodes': [{'node_id': 'A', 'label': 'site'},
                           {'node_id': 'B', 'label': 'pages'}],
           'output_nodes': []}
    ctx = {'config_manager': _CM(cfg), 'chains_dir': str(tmp_path)}
    execute_builder_call('test_chain',
                         {'input': {'site': 'google', 'pages': '10'}}, ctx)
    assert calls['bound'] == {'site': 'google', 'pages': '10'}


def test_test_chain_reports_failure_and_node_errors(monkeypatch, tmp_path):
    _stub_runner(monkeypatch, result=False,
                 le=_FakeLE({'node_bar_error': 'kaboom'}))
    ctx = {'config_manager': _CM({'output_nodes': []}),
           'chains_dir': str(tmp_path)}
    out = execute_builder_call('test_chain', {}, ctx)
    assert 'test: FAILED' in out
    assert 'error bar: kaboom' in out


def test_test_chain_surfaces_the_recorded_failure_reason(monkeypatch, tmp_path):
    """The executor's recorded reason names the failing node and WHY —
    never a bare 'a node returned an error' with no detail."""
    _stub_runner(monkeypatch, result=False, le=_FakeLE({
        '_chain_failed': True,
        '_chain_failure_kind': 'node_error',
        '_chain_failed_node': 'bar',
        '_chain_failed_type': 'llm',
        '_chain_failure_reason': 'llama.cpp API error: 503',
    }))
    ctx = {'config_manager': _CM({'output_nodes': []}),
           'chains_dir': str(tmp_path)}
    out = execute_builder_call('test_chain', {}, ctx)
    assert 'test: FAILED' in out
    assert 'reason:' in out and 'llm bar' in out and '503' in out


def test_test_chain_reports_a_stall_not_completed(monkeypatch, tmp_path):
    import sys
    import types

    def _play(path, agent_query='', stop_flag=None, run_source='play', **kw):
        return None, _FakePlayer(_FakeLE())

    fake = types.ModuleType('player.multi_sequence_player')
    fake.play_chain_with_tailcalls = _play
    monkeypatch.setitem(sys.modules, 'player.multi_sequence_player', fake)
    ctx = {'config_manager': _CM({'output_nodes': []}),
           'chains_dir': str(tmp_path)}
    out = execute_builder_call('test_chain', {}, ctx)
    assert 'test: STALLED' in out


def test_test_chain_verdicts_the_goal(monkeypatch, tmp_path):
    import player.builder.devtools as dt
    monkeypatch.setattr(dt, '_goal_verdict', lambda goal, text: 'MET')
    _stub_runner(monkeypatch)
    cfg = {'description': 'scrape and summarize posts',
           'output_nodes': [{'node_id': 'OUT', 'label': 'answer'}]}
    ctx = {'config_manager': _CM(cfg), 'chains_dir': str(tmp_path)}
    out = execute_builder_call('test_chain', {}, ctx)
    assert 'goal (scrape and summarize posts): MET' in out


def test_test_chain_serializes_on_gui_but_runs_off_it(monkeypatch, tmp_path):
    """Only the canvas read marshals to the GUI thread — the player run
    (long, blocking) must never take it."""
    _stub_runner(monkeypatch)
    marshaled = []

    def _run(fn):
        marshaled.append(fn)
        return fn()

    ctx = {'config_manager': _CM({'output_nodes': [
        {'node_id': 'OUT', 'label': 'a'}]}),
        'chains_dir': str(tmp_path), 'run_on_gui': _run}
    out = execute_builder_call('test_chain', {}, ctx)
    assert 'test: completed' in out
    assert len(marshaled) == 1            # exactly the canvas read


def test_test_chain_forces_inputs_to_data_only(monkeypatch, tmp_path):
    """A builder test must not type on the user's screen: Input nodes are
    forced to passthrough in the throwaway test chain (live canvas untouched)."""
    import sys
    import types
    written = {}

    def _play(path, agent_query='', stop_flag=None, run_source='play', **kw):
        with open(path, encoding='utf-8') as fh:
            written['cfg'] = json.load(fh)
        return True, _FakePlayer(_FakeLE())

    fake = types.ModuleType('player.multi_sequence_player')
    fake.play_chain_with_tailcalls = _play
    monkeypatch.setitem(sys.modules, 'player.multi_sequence_player', fake)

    live_in = {'node_id': 'IN', 'label': 'q', 'passthrough': False}
    live_llm = {'node_id': 'L', 'write_text': True}
    cfg = {'input_nodes': [live_in], 'llm_nodes': [live_llm],
           'output_nodes': []}
    ctx = {'config_manager': _CM(cfg), 'chains_dir': str(tmp_path)}
    execute_builder_call('test_chain', {'input': 'hi'}, ctx)
    assert written['cfg']['input_nodes'][0]['passthrough'] is True
    assert written['cfg']['llm_nodes'][0]['write_text'] is False
    # The live canvas is untouched — only the throwaway test copy is forced.
    assert live_in['passthrough'] is False
    assert live_llm['write_text'] is True


def test_test_chain_without_a_config_manager():
    out = execute_builder_call('test_chain', {},
                               {'config_manager': None, 'chains_dir': ''})
    assert out.startswith('(test_chain: could not read the canvas)')


def test_test_chain_runs_only_the_selected_node(monkeypatch, tmp_path):
    """ONE node selected -> the test runs THAT node (the node bar's own
    single-node path), never the whole chain."""
    import sys
    import types
    written = {}

    def _play(path, agent_query='', stop_flag=None, run_source='play', **kw):
        with open(path, encoding='utf-8') as fh:
            written['cfg'] = json.load(fh)
        return True, _FakePlayer(_FakeLE({'node_0x1_output': 'NODE OUT'}))

    fake = types.ModuleType('player.multi_sequence_player')
    fake.play_chain_with_tailcalls = _play
    monkeypatch.setitem(sys.modules, 'player.multi_sequence_player', fake)

    mgr = _Mgr()
    node = mgr.create_node('llm.LLMNode', 'brain')

    class _CM2(_CM):
        def get_single_node_config(self, n):
            return {'llm_nodes': [{'node_id': n.id, 'label': 'brain'}],
                    'output_nodes': []}

    ctx = {'graph_manager': mgr,
           'config_manager': _CM2({'output_nodes': [
               {'node_id': 'OUT', 'label': 'answer'}]}),
           'chains_dir': str(tmp_path),
           'selection_node_ids': lambda: [node.id]}
    out = execute_builder_call('test_chain', {}, ctx)
    # The throwaway config carried ONLY the selected node...
    assert len(written['cfg']['llm_nodes']) == 1
    assert not written['cfg'].get('output_nodes')
    # ...and the report names the node scope, not the whole chain.
    assert 'scope: node %s alone' % node.id in out
    assert 'output %s: NODE OUT' % node.id in out


def test_test_chain_runs_the_whole_chain_for_several_selected(
        monkeypatch, tmp_path):
    """Several nodes selected is NOT a single-node focus: run the chain."""
    _stub_runner(monkeypatch)
    cfg = {'output_nodes': [{'node_id': 'OUT', 'label': 'answer'}]}
    ctx = {'config_manager': _CM(cfg), 'chains_dir': str(tmp_path),
           'selection_node_ids': lambda: ['0x1', '0x2']}
    out = execute_builder_call('test_chain', {}, ctx)
    assert 'test: completed' in out
    assert 'output answer: THE ANSWER' in out
    assert 'scope: node' not in out


def test_test_chain_scope_chain_overrides_a_single_selection(
        monkeypatch, tmp_path):
    """The build_skill path forces the whole chain even with one node selected."""
    _stub_runner(monkeypatch)
    cfg = {'output_nodes': [{'node_id': 'OUT', 'label': 'answer'}]}
    ctx = {'config_manager': _CM(cfg), 'chains_dir': str(tmp_path),
           'selection_node_ids': lambda: ['0x1']}
    out = execute_builder_call('test_chain', {'scope': 'chain'}, ctx)
    assert 'output answer: THE ANSWER' in out
    assert 'scope: node' not in out


# --------------------------------------------------------------------------- iterating rounds


def test_route_iterates_and_feeds_action_results_back(monkeypatch):
    """The turn loops: after acting it sees the results and acts again."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    prompts = []
    replies = [
        json.dumps({'reply': 'Editing then testing.', 'actions': [
            {'name': 'edit_node', 'arguments': {
                'node': 'brain', 'property': 'prompt', 'value': 'v1'}},
            {'name': 'test_chain', 'arguments': {}}]}),
        json.dumps({'reply': 'The test shows it works.', 'actions': []}),
    ]

    def _gen(prompt, settings, system):
        prompts.append(prompt)
        return replies[min(len(prompts) - 1, len(replies) - 1)]

    monkeypatch.setattr(reasoning, '_generate', _gen)
    mgr = _Mgr()
    mgr.create_node('llm.LLMNode', 'brain')
    out = BuilderAssistant(_ctx(mgr), settings={'reasoning': True}).route(
        'tighten this prompt and make sure it works')
    assert out['steps'] == 2
    assert 'The test shows it works.' in out['text']
    assert 'RESULTS OF YOUR ACTIONS' not in prompts[0]
    assert 'RESULTS OF YOUR ACTIONS SO FAR' in prompts[1]
    assert 'test_chain' in prompts[1]


def test_route_stops_when_the_model_repeats_itself(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    monkeypatch.setattr(reasoning, '_generate', lambda p, s, y: json.dumps({
        'reply': 'again',
        'actions': [{'name': 'read_node', 'arguments': {'node': 'x'}}]}))
    out = BuilderAssistant(_ctx(), settings={'reasoning': True}).route('go')
    assert out['steps'] == 1
    assert 'no new actions' in out['text']


def test_route_single_round_when_no_actions(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    calls = {'n': 0}

    def _gen(prompt, settings, system):
        calls['n'] += 1
        return 'just an answer'

    monkeypatch.setattr(reasoning, '_generate', _gen)
    out = BuilderAssistant(_ctx(), settings={'reasoning': True}).route('hi')
    assert calls['n'] == 1                 # no pointless extra rounds
    assert out['steps'] == 0


def test_route_auto_tests_the_chain_after_wiring(monkeypatch, tmp_path):
    """The builder must SEE whether the chain it just wired runs: a wiring
    round is auto-tested and the report lands in the turn."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    _stub_runner(monkeypatch)                    # the test reports 'completed'
    mgr = _Mgr()
    ctx = _ctx(mgr)
    ctx['config_manager'] = _CM({
        'input_nodes': [{'node_id': 'IN', 'label': 'q'}],
        'output_nodes': [{'node_id': 'OUT', 'label': 'answer'}]})
    ctx['chains_dir'] = str(tmp_path)
    replies = [
        json.dumps({'reply': 'Wiring it up.', 'actions': [
            {'name': 'create_node', 'arguments': {
                'node_type': 'input', 'label': 'q'}},
            {'name': 'create_node', 'arguments': {
                'node_type': 'output', 'label': 'answer'}},
            {'name': 'wire', 'arguments': {
                'from_node': 'q', 'to_node': 'answer'}}]}),
        json.dumps({'reply': 'Looks good.', 'actions': []}),
    ]
    calls = {'n': 0}

    def _gen(p, s, sys):
        i = calls['n']
        calls['n'] += 1
        return replies[min(i, len(replies) - 1)]

    monkeypatch.setattr(reasoning, '_generate', _gen)
    out = BuilderAssistant(ctx, settings={'reasoning': True}).route('make it')
    assert 'test: completed' in out['text']


def test_route_forces_the_whole_chain_test_when_building_a_skill(
        monkeypatch, tmp_path):
    """Building a skill is a WHOLE-chain turn: its auto-test asks for
    scope=chain even though the node-scoped test is otherwise the default."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    import player.builder.devtools as dt
    seen = []

    def _exec(name, arguments, ctx):
        seen.append((name, dict(arguments or {})))
        if name == 'read_graph':
            return 'CANVAS'
        if name == 'build_skill':
            return 'built skill'
        if name == 'test_chain':
            return 'test: completed'
        return '(ok)'

    monkeypatch.setattr(dt, 'execute_builder_call', _exec)
    replies = [
        json.dumps({'reply': 'Building the skill.', 'actions': [
            {'name': 'build_skill', 'arguments': {'name': 'x', 'steps': []}}]}),
        json.dumps({'reply': 'Done.', 'actions': []}),
    ]
    calls = {'n': 0}

    def _gen(p, s, sys):
        i = calls['n']
        calls['n'] += 1
        return replies[min(i, len(replies) - 1)]

    monkeypatch.setattr(reasoning, '_generate', _gen)
    BuilderAssistant(_ctx(), settings={'reasoning': True}).route('build a skill')
    tests = [a for nm, a in seen if nm == 'test_chain']
    assert tests and all(a.get('scope') == 'chain' for a in tests)


def test_route_keeps_iterating_while_the_test_fails(monkeypatch, tmp_path):
    """A failing test must not end the turn: the model is asked again."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    _stub_runner(monkeypatch, result=False,
                 le=_FakeLE({'node_bar_error': 'kaboom'}))
    mgr = _Mgr()
    ctx = _ctx(mgr)
    ctx['config_manager'] = _CM({'output_nodes': []})
    ctx['chains_dir'] = str(tmp_path)
    first = json.dumps({'reply': 'Setting the goal.', 'actions': [
        {'name': 'set_description', 'arguments': {'text': 'do the thing'}}]})
    stop = json.dumps({'reply': 'Trying to stop.', 'actions': []})
    calls = {'n': 0}

    def _gen(p, s, sys):
        i = calls['n']
        calls['n'] += 1
        return first if i == 0 else stop

    monkeypatch.setattr(reasoning, '_generate', _gen)
    out = BuilderAssistant(ctx, settings={'reasoning': True}).route('make it')
    assert calls['n'] > 2                       # it did NOT stop at round 2
    assert 'test: FAILED' in out['text']
    assert 'keep going' in out['text']


def test_wire_input_to_llm_also_carries_data_on_the_prompt_port():
    """EXEC edges don't carry data: wiring an Input to an LLM step must ALSO
    wire input.data -> llm.prompt (the standing-goal feed).  That port
    REPLACES the template, so the step's instruction is kept in
    system_message."""
    mgr = _Mgr()
    src = mgr.create_node('input.InputNode', 'Topic')
    dst = mgr.create_node('llm.LLMNode', 'Writer')
    dst.set_property('prompt', 'Write a haiku about the topic.')
    execute_builder_call('wire', {'from_node': 'Topic', 'to_node': 'Writer'},
                         _ctx(mgr))
    assert (src.id, 'output', dst.id, 'input') in mgr.wires      # exec drive
    assert (src.id, 'data', dst.id, 'prompt') in mgr.wires       # data carry
    # The instruction survives in system_message (the prompt is replaced).
    assert dst.get_property('system_message') == 'Write a haiku about the topic.'


def test_create_node_gives_a_code_node_named_io_ports():
    """A builder-made code node must SHIP its named IO ports (data in /
    result out) so the model can wire them instead of the exec edges."""
    mgr = _Mgr()
    ctx = _ctx(mgr)
    execute_builder_call(
        'create_node', {'node_type': 'code', 'label': 'Filter'}, ctx)
    code = mgr.nodes[ctx['state']['last_node_id']]
    assert 'data' in [p.name() for p in code.input_ports()]
    assert 'result' in [p.name() for p in code.output_ports()]


def test_wire_into_a_code_node_uses_its_named_data_port():
    """A wire into a code node carries the value on its named input var AND
    wires the base exec edge (what decides when the node runs)."""
    mgr = _Mgr()
    src = mgr.create_node('input.InputNode', 'CSV')
    ctx = _ctx(mgr)
    execute_builder_call(
        'create_node', {'node_type': 'code', 'label': 'Filter'}, ctx)
    dst = mgr.nodes[ctx['state']['last_node_id']]
    execute_builder_call('wire', {'from_node': 'CSV', 'to_node': 'Filter'},
                         ctx)
    assert (src.id, 'output', dst.id, 'input') in mgr.wires      # exec
    assert (src.id, 'data', dst.id, 'data') in mgr.wires         # data


def test_wire_out_of_a_code_node_uses_its_named_output_port():
    """A wire out of a code node leaves via its named output var (consumers
    read data ports) AND wires its base exec edge."""
    mgr = _Mgr()
    src = mgr.create_node('code.CodeNode', 'Filter')
    src.set_property('output_vars', json.dumps([{'name': 'result'}]))
    src.rebuild_ports()
    dst = mgr.create_node('output.OutputNode', 'CSV out')
    execute_builder_call('wire', {'from_node': 'Filter', 'to_node': 'CSV out'},
                         _ctx(mgr))
    assert (src.id, 'result', dst.id, 'input') in mgr.wires      # data
    assert (src.id, 'output', dst.id, 'input') in mgr.wires      # exec


def test_route_stops_a_model_that_only_reads(monkeypatch):
    """A model that never changes the chain is stopped by the idle guard —
    it is not spun for the whole (very large) round ceiling."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    calls = {'n': 0}

    def _gen(p, s, sys):
        calls['n'] += 1
        return json.dumps({'reply': 'looking', 'actions': [
            {'name': 'read_node', 'arguments': {'node': 'x%d' % calls['n']}}]})

    monkeypatch.setattr(reasoning, '_generate', _gen)
    mgr = _Mgr()
    mgr.create_node('llm.LLMNode', 'brain')
    out = BuilderAssistant(_ctx(mgr), settings={'reasoning': True}).route('go')
    assert calls['n'] <= 8               # stopped early, not the ceiling
    assert 'no change' in out['text']
