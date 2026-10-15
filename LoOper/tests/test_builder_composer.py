"""Composer — deterministic system-chain assembly (skeleton, matches, wires).

Run:  python -m pytest LoOper/tests/test_builder_composer.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder import laya_grades as lg  # noqa: E402
from player.builder.composer import (  # noqa: E402
    ChainComposer,
    canvas_roles,
    scan_chain_library,
)


# --------------------------------------------------------------------------- fakes


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
        self._props = {'orchestrator_mode': False}
        self._in, self._out = [], []
        self.chain_file = ''
        if type_key.startswith('input'):
            self.add_input('input')
            self.add_output('output')
            self.add_output('data')
        elif type_key.startswith('llm'):
            self.add_input('input')
            self.add_input('prompt')
            self.add_input('context')
            self.add_output('output')
            self.add_output('context')
        elif type_key.startswith('output'):
            self.add_input('input')
            self.add_output('output')
        elif type_key.startswith('chain_import'):
            self.add_input('input')
            self.add_output('output')
        elif type_key.startswith('web_sequence'):
            self.add_input('input')
            self.add_output('output')
        elif type_key.startswith('sequence'):
            self.add_input('input')
            self.add_output('output')

    def name(self):
        return self._name

    def pos(self):
        return [0.0, 0.0]

    def input_ports(self):
        return list(self._in)

    def output_ports(self):
        return list(self._out)

    def add_input(self, name):
        p = _Port(name)
        p.node = self
        self._in.append(p)
        return p

    def add_output(self, name):
        p = _Port(name)
        p.node = self
        self._out.append(p)
        return p

    def find_port(self, port_name, is_input):
        for p in (self._in if is_input else self._out):
            if p.name() == port_name:
                return p
        return None

    def get_property(self, key):
        return self._props.get(key)

    def set_property(self, key, value):
        self._props[key] = value
        if key == 'orchestrator_mode':
            self.rebuild_orchestrator_ports()

    def rebuild_orchestrator_ports(self):
        """The real node exposes tools/brains while the switch is ON."""
        names = {p.name() for p in self._in}
        if self._props.get('orchestrator_mode'):
            for extra in ('tools', 'brains'):
                if extra not in names:
                    self.add_input(extra)

    def set_chain_import_data(self, chain_file, import_mode='full', prefix='',
                              loop_count=1, extra_delay=0, enabled=True):
        self.chain_file = chain_file


class _Mgr:
    def __init__(self):
        self.nodes = {}
        self._n = 0

    def create_node(self, node_type, name=None, pos=None):
        self._n += 1
        node = _Node('0x%x' % self._n, node_type, name or '')
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


class _CM:
    def __init__(self):
        self._current_chain_collection = ''
        self._current_chain_description = ''

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


def _ctx(mgr, cm, chains_dir):
    return {'graph_manager': lambda: mgr, 'config_manager': lambda: cm,
            'state': {}, 'ask': None, 'chains_dir': chains_dir}


@pytest.fixture()
def chains_dir(tmp_path):
    d = tmp_path / 'chains'
    d.mkdir()
    (d / 'navigate.json').write_text(json.dumps({
        'description': 'navigate to linkedin and open jobs',
        'web_sequences': [{'sequence_file': 'x.json'}]}), encoding='utf-8')
    (d / 'deepbrain.json').write_text(json.dumps({
        'description': 'think deeply about a hard problem',
        'llm_nodes': [{'orchestrator_mode': True}]}), encoding='utf-8')
    (d / 'broken.json').write_text('{not json', encoding='utf-8')
    return str(d)


@pytest.fixture(autouse=True)
def _graded(monkeypatch):
    """Laya's verdicts per (goal, step, chain) — None = Laya unavailable."""
    table = {
        'navigate to linkedin': {'navigate': 0.9, 'deepbrain': 0.05},
        'think deeply': {'navigate': 0.05, 'deepbrain': 0.9},
        'navigate to linkedin now': {'navigate': 0.5},   # below MATCH_FLOOR
        'solve the linkedin problem': {'navigate': 0.9, 'deepbrain': 0.85},
        'configure the zorptron widget': {'navigate': 0.9},
    }
    monkeypatch.setattr(
        lg, 'grade_chain_match',
        lambda goal, step, name, signal, trace=None:
        table.get(step, {}).get(name))


def _setup(chains_dir):
    mgr, cm = _Mgr(), _CM()
    return _ctx(mgr, cm, chains_dir), mgr, cm


# --------------------------------------------------------------------------- library


def test_scan_library_classifies_and_skips_broken(chains_dir):
    lib = {r['name']: r for r in scan_chain_library(chains_dir)}
    assert set(lib) == {'navigate', 'deepbrain'}
    assert lib['navigate']['kind'] == 'chain'
    assert lib['deepbrain']['kind'] == 'brain'
    assert 'broken' not in lib


def test_canvas_roles_finds_an_existing_brain(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)
    node = mgr.create_node('llm.LLMNode', 'brain')
    node.set_property('orchestrator_mode', True)
    mgr.create_node('input.InputNode', 'goal')
    roles = canvas_roles(ctx)
    assert roles.get('brain') == node.id
    assert roles.get('input')
    assert 'output' not in roles


# --------------------------------------------------------------------------- compose


def test_compose_builds_skeleton_wires_and_imports(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)
    goal = 'build a linkedin bot'
    lines = ChainComposer(ctx).compose(
        goal, '(no nodes yet)', ['navigate to linkedin', 'think deeply'])
    text = '\n'.join(lines)

    types = [n.type_ for n in mgr.nodes.values()]
    assert types.count('llm.LLMNode') == 1
    assert types.count('input.InputNode') == 1
    assert types.count('output.OutputNode') == 1
    assert types.count('chain_import.ChainImportNode') == 2

    llm = next(n for n in mgr.nodes.values() if n.type_.startswith('llm'))
    assert llm.get_property('orchestrator_mode') == 'true'
    assert llm.get_property('orch_goal') == goal

    # Skeleton wires: input->input, input.data->prompt, llm->output.
    assert 'wired' in text and '.data ->' in text
    assert '%s.output -> %s.input' % (llm.id, _out_id(mgr)) in text

    nav = _import(mgr, 'navigate')
    dbrain = _import(mgr, 'deepbrain')
    assert 'wired %s.output -> %s.tools' % (nav.id, llm.id) in text
    assert 'wired %s.output -> %s.brains' % (dbrain.id, llm.id) in text
    assert '[save_chain] saved system chain' in text


def _out_id(mgr):
    return next(n.id for n in mgr.nodes.values()
                if n.type_.startswith('output'))


def _import(mgr, name):
    return next(n for n in mgr.nodes.values()
                if n.type_.startswith('chain_import') and n._name == name)


def test_compose_reuses_skeleton_and_never_duplicates(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)
    composer = ChainComposer(ctx)
    composer.compose('build a linkedin bot', '(no nodes yet)',
                     ['navigate to linkedin'])
    lines = composer.compose('build a linkedin bot', 'canvas', 
                             ['navigate to linkedin'])
    types = [n.type_ for n in mgr.nodes.values()]
    assert types.count('llm.LLMNode') == 1              # no second brain
    assert types.count('chain_import.ChainImportNode') == 1
    assert any('[skip]' in ln for ln in lines)


def test_unmatched_task_is_reported_not_faked(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose('build a mars bot', '(no nodes yet)',
                                       ['teleport to mars'])
    text = '\n'.join(lines)
    assert '[teach]' in text
    types = [n.type_ for n in mgr.nodes.values()]
    assert types.count('chain_import.ChainImportNode') == 0


def test_compose_saves_a_system_chain(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)
    goal = 'build a linkedin bot'
    ChainComposer(ctx).compose(goal, '(no nodes yet)', ['navigate to linkedin'])
    saved = os.path.join(chains_dir, 'build_a_linkedin_bot.json')
    assert os.path.isfile(saved)
    with open(saved, encoding='utf-8') as f:
        cfg = json.load(f)
    assert cfg['collection'] == 'System'
    assert cfg['description'] == goal


def test_compose_without_laya_imports_only_strong_text_matches(
        chains_dir, monkeypatch):
    """Laya down → only a strong text overlap imports; a weak step is a
    teach request, never a blind import."""
    monkeypatch.setattr(lg, 'grade_chain_match', lambda *a, **k: None)
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose(
        'goal', '(no nodes yet)', ['navigate to linkedin', 'random gibberish'])
    text = '\n'.join(lines)
    assert '(text' in text                                 # fallback pick
    assert '[teach] "random gibberish" stays out of the bot' in text
    assert len([n for n in mgr.nodes.values()
                if n.type_.startswith('chain_import')]) == 1


def test_blocked_tasks_never_block_the_save(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose('x', '(no nodes yet)', ['nothing here'])
    assert any('[save_chain]' in ln for ln in lines)


def test_empty_plan_builds_skeleton_only_and_reports(chains_dir):
    """No tool list (planning model down): the GOAL is not a tool — no
    matching runs, the skeleton still lands, and the report is honest."""
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose('make me a scraper',
                                       '(no nodes yet)', [])
    text = '\n'.join(lines)
    assert '[plan] no tool list' in text
    assert '[save_chain]' in text
    types = [n.type_ for n in mgr.nodes.values()]
    assert types.count('llm.LLMNode') == 1
    assert not [t for t in types if t.startswith('chain_import')]


def test_match_below_floor_declines_and_teaches(chains_dir):
    """A read under MATCH_FLOOR imports nothing — the tool goes to teach."""
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose('build a bot', '(no nodes yet)',
                                       ['navigate to linkedin now'])
    text = '\n'.join(lines)
    assert 'below 0.65' in text
    assert not [n for n in mgr.nodes.values()
                if n.type_.startswith('chain_import')]
    assert 'stays out of the bot' in text


def test_match_declines_when_no_chain_names_the_tool_objects(chains_dir):
    """The runtime's object rule: a tool naming objects NO candidate names
    is declined outright — never a wrong-chain import."""
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose('build a bot', '(no nodes yet)',
                                       ['configure the zorptron widget'])
    text = '\n'.join(lines)
    assert 'no chain names' in text
    assert not [n for n in mgr.nodes.values()
                if n.type_.startswith('chain_import')]


def test_match_close_tie_without_object_confirmation_declines(chains_dir):
    """Two candidates naming the same object count must separate by
    MATCH_MARGIN — a near-tie is noise, not a match."""
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose('build a bot', '(no nodes yet)',
                                       ['solve the linkedin problem'])
    text = '\n'.join(lines)
    assert 'too close' in text
    assert not [n for n in mgr.nodes.values()
                if n.type_.startswith('chain_import')]


# ---------------------------------------------------------------------------
# Teach → wrap → import
# ---------------------------------------------------------------------------

def test_compose_wraps_orphan_recordings_first(chains_dir):
    """A recording sitting on the canvas is wrapped into a wired tool
    before anything else happens — never left as a loose node."""
    ctx, mgr, _cm = _setup(chains_dir)
    rec = mgr.create_node('web_sequence.WebSequenceNode', 'manual recording')
    rec._props['session_file'] = 'man.json'
    lines = ChainComposer(ctx).compose('build a bot', '(no nodes yet)', [])
    text = '\n'.join(lines)
    assert '[wrap]' in text and 'man.json' in text
    types = [n.type_ for n in mgr.nodes.values()]
    assert 'web_sequence.WebSequenceNode' not in types   # replaced
    assert types.count('chain_import.ChainImportNode') == 1


def test_teach_cycle_wraps_a_recording_into_a_wired_import(chains_dir):
    """ask (web/desktop) → record on a fresh node → wrap: the recording
    becomes a sub-chain file and a chip wired to tools; the raw node is
    replaced."""
    ctx, mgr, _cm = _setup(chains_dir)

    def _ask(question, kind):
        if kind in ('record_web_sequence', 'record_sequence'):
            holder = mgr.get_node_by_id(ctx['state'].get('last_node_id'))
            holder._props['session_file'] = 'recorded_step.json'
            return 'DONE'
        return 'web'

    ctx['ask'] = _ask
    lines = ChainComposer(ctx).compose('build a mars bot', '(no nodes yet)',
                                       ['teleport to mars'])
    text = '\n'.join(lines)
    assert '[teach]' in text and 'taught and wrapped into' in text
    types = [n.type_ for n in mgr.nodes.values()]
    assert 'web_sequence.WebSequenceNode' not in types   # replaced
    assert types.count('chain_import.ChainImportNode') == 1
    imp = next(n for n in mgr.nodes.values()
               if n.type_.startswith('chain_import'))
    llm = next(n for n in mgr.nodes.values() if n.type_.startswith('llm'))
    assert 'wired %s.output -> %s.tools' % (imp.id, llm.id) in text

    files = [f for f in os.listdir(chains_dir) if f.startswith('wrap_')]
    assert files
    with open(os.path.join(chains_dir, files[0]), encoding='utf-8') as f:
        cfg = json.load(f)
    assert cfg['web_sequences'][0]['session_file'] == 'recorded_step.json'
    assert cfg['web_sequences'][0]['connections'][0]['input_port'] == 'input'
    assert cfg['output_nodes']


def test_teach_desktop_recording_wraps_into_a_sequences_file(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)

    def _ask(question, kind):
        if kind == 'record_sequence':
            holder = mgr.get_node_by_id(ctx['state'].get('last_node_id'))
            holder._props['sequence_file'] = 'desk_step.json'
            return 'DONE'
        return 'desktop'

    ctx['ask'] = _ask
    ChainComposer(ctx).compose('build a mars bot', '(no nodes yet)',
                               ['teleport to mars'])
    files = [f for f in os.listdir(chains_dir) if f.startswith('wrap_')]
    assert files
    with open(os.path.join(chains_dir, files[0]), encoding='utf-8') as f:
        cfg = json.load(f)
    assert cfg['sequences'][0]['sequence_file'] == 'desk_step.json'
    assert cfg['web_sequences'] == []


def test_teach_skip_reports_and_creates_nothing(chains_dir):
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda question, kind: 'skip'
    lines = ChainComposer(ctx).compose('build a mars bot', '(no nodes yet)',
                                       ['teleport to mars'])
    text = '\n'.join(lines)
    assert '[teach] skipped' in text
    assert 'stays out of the bot' in text
    types = [n.type_ for n in mgr.nodes.values()]
    assert types.count('chain_import.ChainImportNode') == 0
    assert 'web_sequence.WebSequenceNode' not in types


def test_teach_nothing_recorded_removes_the_empty_holder(chains_dir):
    """The user answers DONE without recording: the placeholder holds no
    recording, so it is removed — no junk nodes left behind."""
    ctx, mgr, _cm = _setup(chains_dir)

    def _ask(question, kind):
        if kind in ('record_web_sequence', 'record_sequence'):
            return 'DONE'                              # nothing recorded
        return 'web'

    ctx['ask'] = _ask
    lines = ChainComposer(ctx).compose('build a mars bot', '(no nodes yet)',
                                       ['teleport to mars'])
    text = '\n'.join(lines)
    assert 'no recording yet' in text
    assert '[delete_node] removed recorded' in text
    types = [n.type_ for n in mgr.nodes.values()]
    assert 'web_sequence.WebSequenceNode' not in types
    assert types.count('chain_import.ChainImportNode') == 0


# ---------------------------------------------------------------------------
# Teach → DESIGN (llm step / sub-brain from scratch)
# ---------------------------------------------------------------------------

def test_teach_llm_builds_a_prompted_step_chain(chains_dir, monkeypatch):
    """The `llm` route DESIGNS the tool: the builder AUTHORS the prompt,
    Python writes input -> LLM -> output and wires the import to tools.
    The incoming data feed (input → llm.input) never replaces the prompt."""
    from player.builder import reasoning
    monkeypatch.setattr(reasoning, 'step_prompt',
                        lambda task, goal='', settings=None:
                        'EXTRACT the titles from the data.')
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda question, kind: 'llm'
    lines = ChainComposer(ctx).compose('build a scraper', '(no nodes yet)',
                                       ['teleport to mars'])
    text = '\n'.join(lines)
    files = [f for f in os.listdir(chains_dir) if f.startswith('llm_')]
    assert files
    with open(os.path.join(chains_dir, files[0]), encoding='utf-8') as f:
        cfg = json.load(f)
    llm = cfg['llm_nodes'][0]
    assert llm['prompt'] == 'EXTRACT the titles from the data.'
    assert llm['orchestrator_mode'] is False
    assert cfg['input_nodes'][0]['passthrough'] is True
    # Only output -> llm.input: the data feed must NOT replace the prompt.
    assert cfg['input_nodes'][0]['connections'] == [
        {'output_port': 'output', 'target_node_id': llm['node_id'],
         'input_port': 'input'}]
    assert llm['connections'][0]['input_port'] == 'input'
    imp = next(n for n in mgr.nodes.values()
               if n.type_.startswith('chain_import'))
    brain = next(n for n in mgr.nodes.values()
                 if n.type_.startswith('llm') and n.id != imp.id)
    assert 'wired %s.output -> %s.tools' % (imp.id, brain.id) in text
    assert 'LLM step built' in text


def test_teach_llm_falls_back_to_the_tool_line_as_prompt(chains_dir,
                                                         monkeypatch):
    """No authored prompt (model down) → the tool line itself is the
    instruction — never an empty-prompt node."""
    from player.builder import reasoning
    monkeypatch.setattr(reasoning, 'step_prompt', lambda *a, **k: '')
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda question, kind: 'llm'
    ChainComposer(ctx).compose('build a scraper', '(no nodes yet)',
                               ['teleport to mars'])
    files = [f for f in os.listdir(chains_dir) if f.startswith('llm_')]
    with open(os.path.join(chains_dir, files[0]), encoding='utf-8') as f:
        cfg = json.load(f)
    assert cfg['llm_nodes'][0]['prompt'] == 'teleport to mars'


def test_teach_brain_builds_a_nested_system_chain(chains_dir):
    """The `brain` route writes a nested system chain (skeleton shape, the
    subtask as orch_goal) and wires the import to the parent's brains."""
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda question, kind: 'brain'
    lines = ChainComposer(ctx).compose('build a scraper', '(no nodes yet)',
                                       ['teleport to mars'])
    text = '\n'.join(lines)
    files = [f for f in os.listdir(chains_dir) if f.startswith('brain_')]
    assert files
    with open(os.path.join(chains_dir, files[0]), encoding='utf-8') as f:
        cfg = json.load(f)
    llm = cfg['llm_nodes'][0]
    assert llm['orchestrator_mode'] is True
    assert llm['orch_goal'] == 'teleport to mars'
    # The skeleton feeds: input -> llm.input AND input.data -> llm.prompt.
    ports = [c['input_port'] for c in cfg['input_nodes'][0]['connections']]
    assert ports == ['input', 'prompt']
    imp = next(n for n in mgr.nodes.values()
               if n.type_.startswith('chain_import'))
    brain = next(n for n in mgr.nodes.values()
                 if n.type_.startswith('llm') and n.id != imp.id)
    assert 'wired %s.output -> %s.brains' % (imp.id, brain.id) in text
    assert 'sub-brain built' in text


def test_every_unmatched_tool_gets_its_own_teach_ask(chains_dir):
    """The teach budget follows the plan: 4 unmatched tools → 4 asks.
    (The old fixed cap of 3 silently dropped the rest to a report line.)"""
    ctx, mgr, _cm = _setup(chains_dir)
    asks = []

    def _ask(question, kind):
        asks.append(kind)
        return 'skip'

    ctx['ask'] = _ask
    lines = ChainComposer(ctx).compose('build a mars bot', '(no nodes yet)',
                                       ['tool one', 'tool two', 'tool three',
                                        'tool four'])
    assert [k for k in asks if k == 'teach_card'] == ['teach_card'] * 4
    assert 'teach budget' not in '\n'.join(lines)


# --------------------------------------------------------------------------- act-first


def test_web_tool_is_demonstrated_before_asking(chains_dir, monkeypatch):
    """With the act loop enabled, a web tool is DEMONSTRATED (the provider
    drives the browser) and no teach card is shown at all."""
    from player.builder import web_act
    calls = []

    def _attempt(step, goal, ctx, brain_id='', trace=None):
        calls.append(step)
        return True, ['[act] demonstrated and wired'], ''

    monkeypatch.setattr(web_act, 'attempt_web_tool', _attempt)
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['allow_web_act'] = True
    asked = []
    ctx['ask'] = lambda q, k: asked.append(k) or 'skip'
    lines = ChainComposer(ctx).compose(
        'build a reddit bot', '(no nodes yet)',
        ['navigate to reddit and search'])
    assert calls == ['navigate to reddit and search']
    assert 'teach_card' not in asked
    assert '[act] demonstrated and wired' in '\n'.join(lines)


def test_failed_demonstration_rides_the_teach_card(chains_dir, monkeypatch):
    """A stuck attempt falls back to the card, carrying what was tried."""
    from player.builder import web_act
    monkeypatch.setattr(
        web_act, 'attempt_web_tool',
        lambda *a, **k: (False, ['[act] tried'], 'could not find "Search"'))
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['allow_web_act'] = True
    payloads = []

    def _ask(question, kind):
        payloads.append((question, kind))
        return 'skip'

    ctx['ask'] = _ask
    ChainComposer(ctx).compose('build a reddit bot', '(no nodes yet)',
                               ['navigate to reddit and search'])
    teach = [q for q, k in payloads if k == 'teach_card']
    assert teach and teach[0]['attempt'] == 'could not find "Search"'


def test_plan_type_prefix_is_stripped_and_skeleton_lines_skipped(
        chains_dir, monkeypatch):
    """An 'input — …' plan line is structure (skipped, never matched); a
    'web_sequence — …' line matches on its description only."""
    graded = []
    current = lg.grade_chain_match
    monkeypatch.setattr(
        lg, 'grade_chain_match',
        lambda goal, step, name, signal, trace=None: (
            graded.append(step),
            current(goal, step, name, signal, trace))[1])
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose(
        'build a linkedin bot', '(no nodes yet)',
        ['input — resolve the search query',
         'web_sequence — navigate to linkedin'])
    text = '\n'.join(lines)
    assert 'the skeleton covers the input' in text
    assert 'resolve the search query' not in ' '.join(graded)
    assert 'navigate to linkedin' in graded


def test_split_plan_step_reads_type_and_modifiers():
    """The planner's live grammar: 'type (modes): text' — and the older
    'type — text'.  Plain imperative lines carry no prefix."""
    from player.builder.composer import _split_plan_step
    assert _split_plan_step('input (ask): resolve the query') == (
        'input', 'ask', 'resolve the query')
    assert _split_plan_step(
        'web_sequence (dom replay, repeating set): scrape pages') == (
        'web_sequence', 'dom replay, repeating set', 'scrape pages')
    assert _split_plan_step('llm (vanilla): extract themes') == (
        'llm', 'vanilla', 'extract themes')
    assert _split_plan_step('llm — extract themes') == (
        'llm', '', 'extract themes')
    assert _split_plan_step('navigate to reddit') == (
        '', '', 'navigate to reddit')


def test_llm_plan_lines_build_without_asking(chains_dir, monkeypatch):
    """A plan line's own type routes it: 'llm (vanilla): …' is DESIGNED
    (authored prompt + chain file + wired import) — the card never asks and
    the library is never consulted (matching built a wrong-brain import
    at p=0.78 before)."""
    from player.builder import reasoning
    monkeypatch.setattr(
        reasoning, 'step_prompt',
        lambda task, goal='', settings=None: 'Extract themes per record.')
    asked = []
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda q, k: asked.append(k) or 'skip'
    lines = ChainComposer(ctx).compose(
        'build a scraper bot', '(no nodes yet)',
        ['llm (vanilla): extract structured post examples and classify'])
    text = '\n'.join(lines)
    assert 'teach_card' not in asked
    assert 'designed as an LLM processing step' in text
    files = [f for f in os.listdir(chains_dir) if f.startswith('llm_')]
    assert files
    cfg = json.load(open(os.path.join(chains_dir, files[0]),
                         encoding='utf-8'))
    llm = cfg['llm_nodes'][0]
    assert llm['prompt'] == 'Extract themes per record.'
    assert llm['use_llamacpp'] is True
    imports = [n for n in mgr.nodes.values()
               if n.type_.startswith('chain_import')]
    assert len(imports) == 1
    assert '.tools' in text                      # wired to the orchestrator
    assert 'Import: navigate' not in text        # never matched the library


def test_code_plan_line_builds_a_real_code_chain(chains_dir, monkeypatch):
    """'code (static): …' builds a REAL code node: the Python is authored
    once and compile-validated, the code node runs it deterministically."""
    from player.builder import reasoning
    monkeypatch.setattr(
        reasoning, 'code_text',
        lambda task, goal='', settings=None, previous='', error='':
        'import json\n\nresult = str(data).upper()')
    asked = []
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda q, k: asked.append(k) or 'skip'
    lines = ChainComposer(ctx).compose(
        'build a scraper bot', '(no nodes yet)',
        ['code (static): merge and deduplicate post records'])
    text = '\n'.join(lines)
    assert 'teach_card' not in asked
    assert 'authored and validated' in text
    files = [f for f in os.listdir(chains_dir) if f.startswith('code_')]
    assert files
    cfg = json.load(open(os.path.join(chains_dir, files[0]),
                         encoding='utf-8'))
    code_node = cfg['code_nodes'][0]
    assert 'result' in code_node['code']
    assert code_node['output_variable'] == 'result'
    # Named IO ports (the code node's IO editor), not the base exec edges.
    assert code_node['input_vars'] == [{'name': 'data', 'type': 'any'}]
    assert code_node['output_vars'] == [{'name': 'result', 'type': 'any'}]
    # Wiring: input -> code.<in> -> output, import -> tools.
    assert cfg['input_nodes'][0]['connections'][0]['target_node_id'] == \
        code_node['node_id']
    assert cfg['input_nodes'][0]['connections'][0]['input_port'] == 'data'
    assert code_node['connections'][0]['output_port'] == 'result'
    assert '.tools' in text
    assert not [f for f in os.listdir(chains_dir) if f.startswith('llm_')]


def test_broken_code_falls_back_to_an_llm_step(chains_dir, monkeypatch):
    """A script that fails compile() twice never reaches the chain file —
    the step is built as an LLM step instead, honestly traced."""
    from player.builder import reasoning
    monkeypatch.setattr(
        reasoning, 'code_text',
        lambda task, goal='', settings=None, previous='', error='':
        'def broken(:"""')
    monkeypatch.setattr(
        reasoning, 'step_prompt',
        lambda task, goal='', settings=None: 'Fallback instruction.')
    ctx, mgr, _cm = _setup(chains_dir)
    lines = ChainComposer(ctx).compose(
        'build a bot', '(no nodes yet)',
        ['code (static): do something impossible'])
    text = '\n'.join(lines)
    assert 'authoring failed' in text or 'syntax error' in text
    assert [f for f in os.listdir(chains_dir) if f.startswith('llm_')]
    assert not [f for f in os.listdir(chains_dir) if f.startswith('code_')]


def test_conditional_plan_line_builds_a_check_chain(chains_dir):
    """'conditional: …' builds a check chain: input -> LLM conditional
    (criterion = the line) -> true/false outputs, on the LOCAL engine."""
    asked = []
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda q, k: asked.append(k) or 'skip'
    lines = ChainComposer(ctx).compose(
        'build a scraper bot', '(no nodes yet)',
        ['conditional: more result pages exist to paginate'])
    text = '\n'.join(lines)
    assert 'teach_card' not in asked
    assert 'designed as a check step' in text
    files = [f for f in os.listdir(chains_dir) if f.startswith('check_')]
    assert files
    cfg = json.load(open(os.path.join(chains_dir, files[0]),
                         encoding='utf-8'))
    cond = cfg['conditional_nodes'][0]
    assert cond['condition_type'] == 'llm'
    assert cond['llm_prompt'] == 'more result pages exist to paginate'
    assert cond['llm_engine'] == 'llamacpp'
    branch_ports = {c['output_port'] for c in cond['connections']}
    assert branch_ports == {'true', 'false'}
    assert len(cfg['output_nodes']) == 2
    assert '.tools' in text


def test_output_plan_line_is_skeleton_skipped(chains_dir):
    """'output (agent_visible): …' is the skeleton's output — skipped, and
    it never reaches the teach card (it did before, with the old regex)."""
    asked = []
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda q, k: asked.append(k) or 'skip'
    lines = ChainComposer(ctx).compose(
        'build a report bot', '(no nodes yet)',
        ['output (agent_visible): show the final report'])
    text = '\n'.join(lines)
    assert 'the skeleton covers the output' in text
    assert 'teach_card' not in asked
    assert not [f for f in os.listdir(chains_dir) if f.startswith('llm_')]


def test_rebuild_reuses_and_never_duplicates_built_steps(
        chains_dir, monkeypatch):
    """Composing the same plan twice refreshes the SAME step file and
    never imports it twice."""
    from player.builder import reasoning
    monkeypatch.setattr(
        reasoning, 'step_prompt',
        lambda task, goal='', settings=None: 'Authored instruction.')
    ctx, mgr, _cm = _setup(chains_dir)
    composer = ChainComposer(ctx)
    plan = ['llm (vanilla): extract the themes']
    composer.compose('build a bot', '(no nodes yet)', plan)
    lines = composer.compose('build a bot', 'canvas', plan)
    text = '\n'.join(lines)
    files = [f for f in os.listdir(chains_dir) if f.startswith('llm_')]
    assert len(files) == 1                       # refreshed, not a _2 copy
    assert 'refreshed' in text
    imports = [n for n in mgr.nodes.values()
               if n.type_.startswith('chain_import')]
    assert len(imports) == 1                     # imported once
    assert 'already imported' in text


def test_input_and_output_lines_configure_the_skeleton(chains_dir):
    """The plan's own input/output lines ride onto the skeleton nodes as
    edit_node settings (the app-as-MCP tool surface) — visible in the
    trace, and never a question."""
    asked = []
    ctx, mgr, _cm = _setup(chains_dir)
    ctx['ask'] = lambda q, k: asked.append(k) or 'skip'
    lines = ChainComposer(ctx).compose(
        'build a scraper', '(no nodes yet)',
        ['input (ask): resolve target subreddit names and post limit',
         'output (agent_visible): show the final report'])
    text = '\n'.join(lines)
    inp = next(n for n in mgr.nodes.values() if n.type_.startswith('input'))
    out = next(n for n in mgr.nodes.values() if n.type_.startswith('output'))
    assert inp.get_property('label') == \
        'resolve target subreddit names and post limit'
    assert inp.get_property('agent_modifiable') == 'true'
    assert out.get_property('label') == 'show the final report'
    assert 'skeleton covers the input' in text
    assert 'teach_card' not in asked


def test_edit_allowlists_cover_the_node_settings():
    """Each node type is a tool whose arguments are its real settings:
    the web recording key is 'session_file', llm steps expose
    output_variable/use_async, outputs expose render/tts settings."""
    from player.builder.node_templates import allowed_fields
    web = allowed_fields('web_sequence')
    assert 'session_file' in web and 'extract_items' in web
    assert 'headless' in web and 'repeat_mode' in web
    llm = allowed_fields('llm.LLMNode')
    assert {'output_variable', 'use_async', 'orch_goal', 'prompt'} <= set(llm)
    out = allowed_fields('output')
    assert {'render_mode', 'tts_enabled', 'show_rating'} <= set(out)
