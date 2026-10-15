"""Planner — Laya picks the action, needle fills args, Python writes.

Run:  python -m pytest LoOper/tests/test_builder_planner.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder import devtools  # noqa: E402
from player.builder import laya_grades as lg  # noqa: E402
from player.builder.planner import BuilderPlanner  # noqa: E402


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Planner tests never touch the model gateway or the real settings file."""
    from player.builder import reasoning, settings
    monkeypatch.setattr(settings, 'load',
                        lambda: dict(settings.DEFAULTS, reasoning=False))
    monkeypatch.setattr(reasoning, 'think', lambda *a, **k: None)
    monkeypatch.setattr(reasoning, 'plan', lambda *a, **k: [])


class _Node:
    def __init__(self, nid, ntype='', name=''):
        self.id = nid
        self.type_ = ntype
        self._name = name
        self.chain_file = ''
        self._props = {}

    def name(self):
        return self._name

    def set_chain_import_data(self, chain_file, import_mode='full', prefix='',
                              loop_count=1, extra_delay=0, enabled=True):
        self.chain_file = chain_file

    def input_ports(self):
        return []

    def output_ports(self):
        return []

    def get_property(self, key):
        return self._props.get(key)

    def set_property(self, key, value):
        self._props[key] = value


class _Mgr:
    def __init__(self):
        self.nodes = []

    def get_all_nodes(self):
        return list(self.nodes)

    def get_node_by_id(self, nid):
        return next((n for n in self.nodes if n.id == nid), None)

    def create_node(self, node_type, name=None, pos=None):
        node = _Node('0x%x' % (len(self.nodes) + 1), node_type, name or '')
        self.nodes.append(node)
        return node


def _ctx(asked=None, orchestrated=False):
    mgr = _Mgr()
    if orchestrated:
        # The EDIT path (the per-step action loop) needs a brain already on
        # the canvas; a fresh canvas takes the composer path instead.
        seed = _Node('0xseed', 'llm.LLMNode', 'brain')
        seed._props['orchestrator_mode'] = True
        mgr.nodes.append(seed)
    ctx = {'graph_manager': mgr, 'config_manager': None,
           'chains_dir': '', 'state': {}}
    if asked is not None:
        def _ask(question, kind):
            asked.append((question, kind))
            return 'ok'
        ctx['ask'] = _ask
    return ctx


def test_laya_picks_action_and_needle_fills_arguments(monkeypatch):
    checks = {'n': 0}

    def _done(*_a, **_k):
        checks['n'] += 1
        return checks['n'] > 3          # stop after a couple of actions

    monkeypatch.setattr(lg, 'wants_to_ask', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'is_done', _done)
    monkeypatch.setattr(lg, 'pick_action', lambda *a, **k: 'create_node')
    monkeypatch.setattr(
        devtools, 'extract_arguments',
        lambda action, content, exe_path=None, **k: {
            'node_type': 'sequence', 'label': 'click apply'})

    events = []
    out = BuilderPlanner(_ctx(orchestrated=True)).route(
        'create a sequence node', progress_cb=events.append)

    assert out['ok'] and not out['empty']
    assert out['steps'] >= 2
    assert 'created sequence node' in out['text']
    assert 'tool:read_graph' in events
    assert 'thinking' in events
    assert 'tool:create_node' in events


def test_ask_gate_asks_the_user(monkeypatch):
    asked = []
    state = {'n': 0}

    def _wants(*_a, **_k):
        state['n'] += 1
        return state['n'] == 1          # only the first step wants input

    monkeypatch.setattr(lg, 'wants_to_ask', _wants)
    monkeypatch.setattr(lg, 'is_done', lambda *a, **k: True)
    monkeypatch.setattr(lg, 'pick_action', lambda *a, **k: None)
    monkeypatch.setattr(
        devtools, 'extract_arguments',
        lambda *a, **k: {'question': 'Where are your messages?',
                         'kind': 'answer'})

    out = BuilderPlanner(_ctx(asked=asked, orchestrated=True)).route('build a bot')
    assert asked and asked[0][0] == 'Where are your messages?'
    assert 'user said: ok' in out['text']


def test_create_node_type_is_grounded_by_laya(monkeypatch):
    """Direct creation is mandatory: when needle cannot name a node_type,
    Laya picks one from the type purposes and the node IS created."""
    checks = {'n': 0}

    def _done(*_a, **_k):
        checks['n'] += 1
        return checks['n'] > 1

    monkeypatch.setattr(lg, 'wants_to_ask', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'is_done', _done)
    monkeypatch.setattr(lg, 'pick_action', lambda *a, **k: 'create_node')
    monkeypatch.setattr(lg, 'pick_node_type', lambda *a, **k: 'llm')
    monkeypatch.setattr(devtools, 'extract_arguments',
                        lambda *a, **k: {'node_type': '', 'label': 'brain'})

    asked = []
    out = BuilderPlanner(_ctx(asked=asked, orchestrated=True)).route(
        'build a bot that logs in')
    assert 'created llm node' in out['text']     # created, not asked
    assert asked == []                           # no ask fallback


def test_ungrounded_action_asks_instead_of_retrying(monkeypatch):
    """Grounded-or-ask: an action whose arguments cannot be grounded asks
    the user once instead of retrying the same failure every loop pass."""
    asked = []
    monkeypatch.setattr(lg, 'wants_to_ask', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'is_done', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'pick_action', lambda *a, **k: 'create_node')
    monkeypatch.setattr(lg, 'pick_node_type', lambda *a, **k: None)
    monkeypatch.setattr(devtools, 'extract_arguments',
                        lambda *a, **k: {'node_type': '', 'label': 'x'})

    out = BuilderPlanner(_ctx(asked=asked, orchestrated=True)).route('build a bot')
    assert len(asked) == 1                  # asked once, no retry loop
    assert 'unknown node_type' in asked[0][0]
    assert out['steps'] == 1


def test_route_emits_a_readable_trace(monkeypatch):
    """The trace shows the goal, the canvas, each step, the stored arguments
    and the result — the same lines the log carries."""
    checks = {'n': 0}

    def _done(*_a, **_k):
        checks['n'] += 1
        return checks['n'] > 1

    monkeypatch.setattr(lg, 'wants_to_ask', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'is_done', _done)
    monkeypatch.setattr(lg, 'pick_action', lambda *a, **k: 'create_node')
    monkeypatch.setattr(lg, 'pick_node_type', lambda *a, **k: 'llm')
    monkeypatch.setattr(devtools, 'extract_arguments',
                        lambda *a, **k: {'node_type': '', 'label': 'brain'})

    lines = []
    BuilderPlanner(_ctx(orchestrated=True)).route('build a bot',
                                                  trace=lines.append)
    joined = '\n'.join(lines)
    assert lines[0] == 'goal: build a bot'
    assert 'canvas:' in joined
    assert 'step 1' in joined
    assert 'create_node arguments:' in joined
    assert 'created llm node' in joined


def test_reasoning_is_traced_and_fed_to_laya(monkeypatch):
    """The reasoning LLM's sentence is traced AND handed to Laya's pick."""
    from player.builder import reasoning
    monkeypatch.setattr(reasoning, 'think',
                        lambda *a, **k: 'create a brain node first')
    seen = {}
    steps = {'n': 0}

    def _done(*_a, **_k):
        steps['n'] += 1
        return steps['n'] > 1

    def _pick(*_a, **k):
        seen['thought'] = k.get('thought')
        return 'set_description'

    monkeypatch.setattr(lg, 'wants_to_ask', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'is_done', _done)
    monkeypatch.setattr(lg, 'pick_action', _pick)

    lines = []
    BuilderPlanner(_ctx(orchestrated=True)).route('build a bot',
                                                  trace=lines.append)
    assert any('reasoning: create a brain node first' in ln for ln in lines)
    assert seen['thought'] == 'create a brain node first'


def test_laya_unavailable_falls_back_to_the_needle_router(monkeypatch):
    monkeypatch.setattr(lg, 'wants_to_ask', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'is_done', lambda *a, **k: False)
    monkeypatch.setattr(lg, 'pick_action', lambda *a, **k: None)

    class _Router:
        def __init__(self, ctx, exe_path=None):
            pass

        def available(self):
            return True

        def route(self, query, progress_cb=None):
            return {'ok': True, 'empty': False, 'text': 'FALLBACK',
                    'steps': 1, 'error': None}

    monkeypatch.setattr(devtools, 'BuilderRouter', _Router)
    out = BuilderPlanner(_ctx(orchestrated=True)).route('do something')
    assert out['text'] == 'FALLBACK'


def test_extract_arguments_declares_one_schema(monkeypatch):
    from player.code_agent_ops.devtools import NeedleRouter
    import json

    seen = {}

    def _fake(self, tools_path, prompt):
        with open(tools_path, encoding='utf-8') as f:
            seen['schemas'] = json.load(f)
        seen['prompt'] = prompt
        return ({'function_calls': [{'name': 'create_node', 'arguments': {
            'node_type': 'sequence', 'label': 'click apply'}}]}, None)

    monkeypatch.setattr(NeedleRouter, '_prompt_once', _fake)

    args = devtools.extract_arguments('create_node', 'make a sequence node',
                                      exe_path=__file__)
    assert args == {'node_type': 'sequence', 'label': 'click apply'}
    assert len(seen['schemas']) == 1                 # exactly ONE tool
    assert seen['schemas'][0]['name'] == 'create_node'
    assert seen['prompt'] == 'make a sequence node'
    assert devtools.extract_arguments('nope', 'x', exe_path=__file__) is None

    lines = []
    devtools.extract_arguments('create_node', 'make a sequence node',
                               exe_path=__file__, trace=lines.append)
    assert lines and 'needle(create_node)' in lines[-1]


# ---------------------------------------------------------------------------
# Plan-first build (no node before a plan exists)
# ---------------------------------------------------------------------------


def test_plan_runs_before_actions_and_gates_done(monkeypatch):
    """The plan is generated BEFORE any node, traced, and the done-probe is
    skipped until the planned steps have actually been worked."""
    from player.builder import reasoning, settings
    monkeypatch.setattr(settings, 'load',
                        lambda: dict(settings.DEFAULTS, reasoning=True))
    monkeypatch.setattr(reasoning, 'plan',
                        lambda *a, **k: ['Add an input node',
                                         'Add an orchestrator LLM node',
                                         'Wire the input to the LLM node'])
    monkeypatch.setattr(reasoning, 'think', lambda *a, **k: 'next please')
    monkeypatch.setattr(lg, 'wants_to_ask', lambda *a, **k: False)
    # Would stop at once — the plan gate must override it.
    monkeypatch.setattr(lg, 'is_done', lambda *a, **k: True)
    monkeypatch.setattr(lg, 'pick_action', lambda *a, **k: 'set_description')
    monkeypatch.setattr(devtools, 'extract_arguments',
                        lambda *a, **k: {'text': 'x'})

    lines = []
    out = BuilderPlanner(_ctx(orchestrated=True)).route(
        'build a bot', trace=lines.append)
    joined = '\n'.join(lines)
    assert 'plan 1: Add an input node' in joined
    assert 'plan 3: Wire the input to the LLM node' in joined
    # Step 1 runs with the done-probe skipped: zero of three plan steps done.
    assert 'done-probe skipped — 0 of 3 plan steps done' in joined
    # One action per planned step ran before DONE was accepted.
    assert out['text'].count('[set_description]') == 3
    assert out['steps'] >= 4


def test_chain_import_requires_an_existing_file(tmp_path):
    """A chain_import with no (or a missing) file is refused — the planner
    then asks, and no empty dead node lands on the canvas."""
    ctx = _ctx()
    out = devtools.execute_builder_call(
        'create_node', {'node_type': 'chain_import', 'label': 'scraper'}, ctx)
    assert out.startswith('(')
    assert 'chain_file_path' in out
    assert ctx['graph_manager'].nodes == []          # nothing created

    chain = tmp_path / 'scraper.json'
    chain.write_text('{}', encoding='utf-8')
    out = devtools.execute_builder_call(
        'create_node', {'node_type': 'chain_import', 'label': 'scraper',
                        'chain_file_path': str(chain)}, ctx)
    assert 'created chain_import node' in out
    node = ctx['graph_manager'].nodes[0]
    assert node.chain_file == str(chain)             # file attached


def test_junk_labels_never_become_node_names():
    """A placeholder label ('empty' — latched from the canvas text) is
    dropped so the canvas names the node after its type."""
    ctx = _ctx()
    out = devtools.execute_builder_call(
        'create_node', {'node_type': 'input', 'label': 'empty'}, ctx)
    assert 'created input node' in out
    assert '"empty"' not in out
    assert ctx['graph_manager'].nodes[0]._name == ''


# ---------------------------------------------------------------------------
# Compose-first: a canvas without a brain is assembled, not chatted about
# ---------------------------------------------------------------------------


def test_fresh_canvas_composes_instead_of_looping(monkeypatch):
    """No orchestrator on the canvas → the composer builds the skeleton
    deterministically; the flat action menu never runs.  With no tool list
    (the offline fixture disables reasoning) it reports honestly instead of
    matching the whole goal as if it were a tool."""
    lines = []
    out = BuilderPlanner(_ctx()).route('build a bot for mail',
                                       trace=lines.append)
    joined = '\n'.join(lines)
    assert 'mode: compose (no orchestrator brain on the canvas)' in joined
    assert 'composing system chain for: build a bot for mail' in out['text']
    # Skeleton created; no tool list → the honest [plan] report, no match.
    assert 'created llm node' in out['text']
    assert '[plan] no tool list' in out['text']
