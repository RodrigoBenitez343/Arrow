"""web_act — the provider demonstrates a tool in the real browser, frozen.

Run:  python -m pytest LoOper/tests/test_builder_web_act.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder import reasoning  # noqa: E402
from player.builder import web_act  # noqa: E402
from player.builder import wrap as _wrap  # noqa: E402


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
        if type_key.startswith('llm'):
            self.add_input('input')
            self.add_input('prompt')
            self.add_output('output')
        elif type_key.startswith('chain_import'):
            self.add_input('input')
            self.add_output('output')
        elif type_key.startswith('output'):
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
        if key == 'orchestrator_mode' and value:
            names = {p.name() for p in self._in}
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


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A fake canvas + temp chains dir + all four web_act seams stubbed."""
    chains = tmp_path / 'chains'
    chains.mkdir()
    mgr = _Mgr()
    brain = mgr.create_node('llm.LLMNode', 'brain')
    brain.set_property('orchestrator_mode', True)
    ctx = {'graph_manager': lambda: mgr, 'config_manager': lambda: None,
           'state': {}, 'ask': None, 'chains_dir': str(chains)}

    class _Driver:
        current_url = 'https://example.test/'
        title = 'Example'
        window_handles = ['h1']
        current_window_handle = 'h1'

    monkeypatch.setattr(web_act, '_driver', lambda: _Driver())
    monkeypatch.setattr(web_act, '_page_digest',
                        lambda driver: 'URL: https://example.test/\nELEMENTS:\n- [a] "Home"')
    monkeypatch.setattr(web_act, '_replay_config', lambda: object())

    from player.web.events import Locator
    fake_locator = Locator(tag='a', text='Home',
                           text_xpath='//a[contains(normalize-space(.), "Home")]')
    monkeypatch.setattr(web_act, '_find_element',
                        lambda driver, target: (object(), fake_locator))

    results = []

    def _exec(driver, event, config):
        results.append(event)
        return {'ok': True, 'method': 'native'}

    monkeypatch.setattr(web_act, '_execute_event', _exec)
    return {'ctx': ctx, 'mgr': mgr, 'brain': brain, 'chains': chains,
            'results': results, 'tmp': tmp_path}


def _script(monkeypatch, actions):
    """act_web replays a scripted action list (None entries = no answer)."""
    seq = list(actions)

    def _act(goal, step, digest, history, settings):
        return seq.pop(0) if seq else None

    monkeypatch.setattr(reasoning, 'act_web', _act)


SETTINGS = {'reasoning': True}


# --------------------------------------------------------------------------- looks_web


def test_looks_web_matches_browser_lines():
    assert web_act.looks_web('navigate to reddit and search for new posts')
    assert web_act.looks_web('open the browser and click Log in')
    assert not web_act.looks_web('merge and summarize the extracted records')
    assert not web_act.looks_web('classify each record into themes')


# --------------------------------------------------------------------------- loops


def test_demonstration_is_recorded_and_wired(env, monkeypatch):
    _script(monkeypatch, [
        {'action': 'navigate', 'url': 'https://example.test/'},
        {'action': 'click', 'target': 'Home'},
        {'action': 'done'},
    ])
    ok, lines, note = web_act.attempt_web_tool(
        'navigate to the site and open home', 'a test goal',
        env['ctx'], env['brain'].id, settings=SETTINGS)
    assert ok and note == ''
    text = '\n'.join(lines)
    assert '[act] navigate to https://example.test/' in text
    assert '[act] click "Home"' in text
    assert 'demonstrated and wired' in text

    # The recording is a real web session (recorder schema).
    recordings = list((env['tmp'] / 'web_sequences').glob('*.json'))
    assert len(recordings) == 1
    session = json.loads(recordings[0].read_text(encoding='utf-8'))
    assert session['mode'] == 'web'
    assert [a['type'] for a in session['actions']] == ['navigate', 'click']
    click = session['actions'][1]
    assert click['locator']['text'] == 'Home'

    # The wrap is a chain file whose web_sequence points at that recording.
    wraps = list(env['chains'].glob('wrap_*.json'))
    assert len(wraps) == 1
    cfg = json.loads(wraps[0].read_text(encoding='utf-8'))
    seq = cfg['web_sequences'][0]
    assert seq['session_file'] == recordings[0].name
    assert seq['native_actions'] is True
    assert cfg['description'] == 'navigate to the site and open home'

    # The import exists on the canvas, wired to the brain's tools port.
    imports = [n for n in env['mgr'].nodes.values()
               if n.type_.startswith('chain_import')]
    assert len(imports) == 1
    tools = env['brain'].find_port('tools', True)
    assert tools.connected_ports()


def test_stuck_reports_reason_and_saves_nothing(env, monkeypatch):
    _script(monkeypatch, [
        {'action': 'navigate', 'url': 'https://example.test/'},
        {'action': 'stuck', 'reason': 'the search box never appeared'},
    ])
    ok, lines, note = web_act.attempt_web_tool(
        'search the site for widgets', 'a test goal', env['ctx'],
        env['brain'].id, settings=SETTINGS)
    assert not ok
    assert 'got stuck' in note and 'search box' in note
    assert not list((env['tmp'] / 'web_sequences').glob('*.json'))
    assert not list(env['chains'].glob('wrap_*.json'))


def test_provider_silence_fails_open(env, monkeypatch):
    _script(monkeypatch, [None])
    ok, lines, note = web_act.attempt_web_tool(
        'open the site', 'a test goal', env['ctx'], env['brain'].id,
        settings=SETTINGS)
    assert not ok
    assert 'did not answer' in note
    assert not list((env['tmp'] / 'web_sequences').glob('*.json'))


def test_stop_aborts_between_steps(env, monkeypatch):
    _script(monkeypatch, [{'action': 'navigate',
                           'url': 'https://example.test/'}])
    env['ctx']['stop'] = lambda: True
    ok, lines, note = web_act.attempt_web_tool(
        'open the site', 'a test goal', env['ctx'], env['brain'].id,
        settings=SETTINGS)
    assert not ok and 'stopped by the user' in note


def test_bad_url_ends_after_two_failures(env, monkeypatch):
    _script(monkeypatch, [
        {'action': 'navigate', 'url': 'not-a-url'},
        {'action': 'navigate', 'url': 'still-not'},
    ])
    ok, lines, note = web_act.attempt_web_tool(
        'open the site', 'a test goal', env['ctx'], env['brain'].id,
        settings=SETTINGS)
    assert not ok and 'bad URLs' in note


def test_reasoning_off_skips_the_attempt(env, monkeypatch):
    ok, lines, note = web_act.attempt_web_tool(
        'open the site', 'a test goal', env['ctx'], env['brain'].id,
        settings={'reasoning': False})
    assert not ok and 'reasoning' in note
    assert env['results'] == []


# --------------------------------------------------------------------------- parsing


def test_parse_action_is_lenient_and_validated():
    assert reasoning._parse_action(
        'Sure! {"action": "click", "target": "Log in"}') == {
        'action': 'click', 'target': 'Log in', 'seconds': 2.0}
    assert reasoning._parse_action(
        '```json\n{"action": "done"}\n```') == {
        'action': 'done', 'seconds': 2.0}
    assert reasoning._parse_action('{"action": "teleport"}') is None
    assert reasoning._parse_action('no json here') is None
    assert reasoning._parse_action('') is None


def test_parse_action_keeps_scroll_and_modifiers():
    got = reasoning._parse_action(
        '{"action": "scroll", "value": -1200, "modifiers": ["Ctrl"]}')
    assert got['action'] == 'scroll'
    assert got['value'] == '-1200'
    assert got['modifiers'] == ['Ctrl']


# --------------------------------------------------------------------------- wrap


def test_wrap_recording_writes_a_parity_chain(tmp_path):
    rec = tmp_path / 'demo.json'
    rec.write_text(json.dumps({'mode': 'web', 'actions': []}),
                   encoding='utf-8')
    chains = tmp_path / 'chains'
    chains.mkdir()
    mgr = _Mgr()
    brain = mgr.create_node('llm.LLMNode', 'brain')
    brain.set_property('orchestrator_mode', True)
    ctx = {'graph_manager': lambda: mgr, 'state': {},
           'chains_dir': str(chains)}

    lines, path = _wrap.wrap_recording(
        ctx, str(rec), 'demo routine', kind='web_sequence',
        brain_id=brain.id)
    assert path and os.path.isfile(path)
    cfg = json.loads(open(path, encoding='utf-8').read())
    seq = cfg['web_sequences'][0]
    assert seq['session_file'] == 'demo.json'          # basename, parity
    assert seq['name'] == 'demo routine'
    assert seq['loop_count'] == 1 and seq['speed'] == 1.0
    assert cfg['_web_chain_scope'] == 'default'
    # The recording file itself was never touched.
    assert json.loads(rec.read_text(encoding='utf-8'))['mode'] == 'web'

    imports = [n for n in mgr.nodes.values()
               if n.type_.startswith('chain_import')]
    assert len(imports) == 1
    assert brain.find_port('tools', True).connected_ports()


def test_wrap_recording_refuses_a_missing_file(tmp_path):
    chains = tmp_path / 'chains'
    chains.mkdir()
    ctx = {'graph_manager': lambda: _Mgr(), 'state': {},
           'chains_dir': str(chains)}
    lines, path = _wrap.wrap_recording(ctx, str(tmp_path / 'nope.json'),
                                       'x')
    assert path == '' and lines and 'does not exist' in lines[0]


# --------------------------------------------------------------------------- steps


def test_designed_step_defaults_to_the_local_engine(tmp_path):
    cfg = _wrap.step_chain_config('extract titles', kind='llm',
                                  prompt='Extract the titles.')
    llm = cfg['llm_nodes'][0]
    assert llm['use_llamacpp'] is True
    assert llm['prompt'] == 'Extract the titles.'
    assert 'llamacpp_model_path' in llm
