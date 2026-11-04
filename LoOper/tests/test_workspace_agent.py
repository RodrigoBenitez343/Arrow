"""Workspaces and the composed agent: registry, catalog, routing, learning.

The point of the feature: a workspace owns one agent and the chains it may
use; the agent routes over those chains by their own metadata, runs them in
order and learns the plan, so no hand-built "system chain" is required.  These
tests pin the contract:

* a workspace is a folder + a manifest, and its chains are visible only there;
* moving a chain rewrites the imports that pointed at it (by basename, which
  also repairs the stale absolute paths older builds wrote);
* ``collection`` chains migrate into workspaces, and an old System brain
  becomes the workspace's pinned ``legacy_brain``;
* the catalog is built from each chain's own ``flow`` metadata, and a chain
  that says nothing about itself is not routable;
* routing order: pinned brain -> learned plan -> Laya -> only chain -> planner;
* the planner's JSON is validated against the catalog (unknown chain = error);
* a run records its plan and counts the result.

Run:  python -m pytest LoOper/tests/test_workspace_agent.py
"""

import json
import os

import pytest

from player import workspace as ws

# --------------------------------------------------------------- helpers

CHAIN = {
    'llm_nodes': [{'node_id': '0x1', 'prompt': 'go', 'model': ''}],
    'input_nodes': [{'node_id': '0x2', 'label': 'search terms'}],
    'output_nodes': [{'node_id': '0x3', 'label': 'shortlist',
                      'variable_name': 'shortlist'}],
    'flow': {'summary': 'searches job boards and writes a shortlist',
             'sections': [{'title': 'search', 'nodes': ['0x2']}],
             'steps': [{'node': '0x1', 'does': 'queries the boards'}]},
}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    return path


@pytest.fixture
def root(tmp_path):
    return str(tmp_path / 'workspaces')


@pytest.fixture
def chains_dir(tmp_path):
    folder = tmp_path / 'chains'
    folder.mkdir()
    return str(folder)


def _workspace_with_chain(root, name='Job hunt', chain_id='jobfinder',
                          data=None):
    manifest = ws.create(root, name)
    _write(os.path.join(ws.chains_dir(root, manifest['slug']),
                        chain_id + '.json'), dict(data or CHAIN))
    return manifest


# ------------------------------------------------------------- registry

def test_a_workspace_is_a_manifest_and_its_own_chains_folder(root):
    manifest = ws.create(root, 'Job hunt', agent_name='Scout')
    assert manifest['slug'] == 'job-hunt'
    assert manifest['agent']['name'] == 'Scout'
    assert os.path.isdir(ws.chains_dir(root, manifest['slug']))
    assert [item['name'] for item in ws.list_workspaces(root)] == ['Job hunt']


def test_two_workspaces_with_the_same_name_still_get_their_own_folder(root):
    first = ws.create(root, 'Job hunt')
    second = ws.create(root, 'Job hunt')
    assert first['slug'] != second['slug']
    assert ws.chains_dir(root, first['slug']) != ws.chains_dir(root,
                                                              second['slug'])


def test_the_active_workspace_is_remembered_and_validated(root):
    manifest = ws.create(root, 'Job hunt')
    ws.set_active_slug(root, manifest['slug'])
    assert ws.active_slug(root) == manifest['slug']
    with pytest.raises(ws.WorkspaceError):
        ws.set_active_slug(root, 'does-not-exist')


def test_default_workspace_is_created_on_demand(root):
    manifest = ws.default_workspace(root)
    assert manifest['slug'] == ws.UNASSIGNED_SLUG
    assert os.path.isdir(ws.chains_dir(root, manifest['slug']))


# ------------------------------------------------------------- membership

def test_moving_a_chain_rewrites_the_imports_that_pointed_at_it(root,
                                                               chains_dir):
    source = ws.create(root, 'Source')
    target = ws.create(root, 'Target')
    path = _write(os.path.join(ws.chains_dir(root, source['slug']),
                               'jobfinder.json'), dict(CHAIN))
    # a second chain imports it by an ABSOLUTE path from an older checkout
    importer = {'chain_import_nodes': [{
        'chain_file_path': r'D:\LoOperV2\LoOper\chains\jobfinder.json',
        'node_id': '0x9'}]}
    importer_path = _write(os.path.join(ws.chains_dir(root, source['slug']),
                                        'main.json'), importer)

    moved = ws.move_chain(root, path, target['slug'])

    assert os.path.dirname(moved) == ws.chains_dir(root, target['slug'])
    assert not os.path.exists(path)
    with open(importer_path, encoding='utf-8') as handle:
        rewritten = json.load(handle)
    assert rewritten['chain_import_nodes'][0]['chain_file_path'] == moved


def test_moving_onto_an_existing_name_raises_and_moves_nothing(root):
    first = ws.create(root, 'One')
    second = ws.create(root, 'Two')
    a = _write(os.path.join(ws.chains_dir(root, first['slug']),
                            'same.json'), dict(CHAIN))
    _write(os.path.join(ws.chains_dir(root, second['slug']), 'same.json'),
           dict(CHAIN))
    with pytest.raises(ws.WorkspaceError):
        ws.move_chain(root, a, second['slug'])
    assert os.path.isfile(a)


# -------------------------------------------------------------- migration

def test_collections_become_workspaces_and_system_chains_are_absorbed(
        root, chains_dir):
    """No "System" workspace: a legacy brain is absorbed where its tools are."""
    tool = _write(os.path.join(chains_dir, 'tool.json'),
                  dict(CHAIN, collection='Tools'))
    brain = _write(os.path.join(chains_dir, 'brain.json'), dict(
        CHAIN, collection='System', is_default=True,
        description='the old agent brain',
        chain_import_nodes=[{
            'chain_file_path': r'D:\LoOperV2\LoOper\chains\tool.json'}]))
    loose = _write(os.path.join(chains_dir, 'loose.json'), dict(CHAIN))
    # not chains: they must be left alone
    _write(os.path.join(chains_dir, 'chain_collections.json'), {'x': 1})

    report = ws.migrate(root, chains_dir)

    assert report['moves'] == 3
    names = {item['name']: item for item in ws.list_workspaces(root)}
    assert set(names) == {'Tools', ws.UNASSIGNED_NAME}, 'no System workspace'
    for manifest in names.values():
        assert manifest['agent']['legacy_brain'] == '', (
            'nothing is pinned automatically any more')
    # the brain followed the chains it imports
    assert report['brains'] == {'brain.json': names['Tools']['slug']}
    assert 'brain.json' in [os.path.basename(path) for path in
                            ws.chain_files(root, names['Tools']['slug'])]
    for path in (tool, brain, loose):
        assert not os.path.exists(path), 'chains must MOVE, not be copied'
    assert os.path.isfile(os.path.join(chains_dir, 'chain_collections.json'))
    assert ws.active_slug(root)


def test_migration_runs_once(root, chains_dir):
    _write(os.path.join(chains_dir, 'a.json'), dict(CHAIN, collection='X'))
    ws.migrate(root, chains_dir)
    later = _write(os.path.join(chains_dir, 'b.json'), dict(CHAIN,
                                                            collection='Y'))
    report = ws.migrate(root, chains_dir)
    assert report.get('skipped') is True
    assert os.path.isfile(later), 'a second run must not move anything'


# ---------------------------------------------------------------- catalog

def test_the_catalog_reads_each_chain_s_own_metadata(root):
    from player.agentic_ops import catalog

    manifest = _workspace_with_chain(root)
    (item,) = catalog.entries(root, manifest['slug'])
    assert item['id'] == 'jobfinder'
    assert item['summary'].startswith('searches job boards')
    assert item['sections'] == ['search']
    assert item['needs'] == ['search terms']
    assert item['returns'] == ['shortlist']
    assert item['described'] is True
    assert item['routable'] is True
    assert 'jobfinder' in catalog.digest([item])


def test_the_routing_line_is_derived_and_ignores_hand_written_prose(root):
    """The manual ``description`` is dead weight: the brief replaces it."""
    from player.agentic_ops import catalog

    manifest = _workspace_with_chain(
        root, data=dict(CHAIN, description='HAND TYPED TEXT that drifted'))
    (item,) = catalog.entries(root, manifest['slug'])
    assert 'HAND TYPED' not in item['summary']
    assert 'searches job boards' in item['summary']
    assert 'search' in item['summary']
    assert 'needs search terms' in item['summary']
    assert 'returns shortlist' in item['summary']


def test_a_chain_that_describes_nothing_is_not_routable(root):
    """A structural line is honest but is not enough to route on."""
    from player.agentic_ops import catalog

    manifest = ws.create(root, 'Quiet')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'mute.json'),
           {'llm_nodes': [{'node_id': '0x1'}]})
    (item,) = catalog.entries(root, manifest['slug'])
    assert item['described'] is False
    assert item['summary'].startswith('1 node(s)'), item['summary']
    assert catalog.routable([item]) == []


# ---------------------------------------------------------------- routing

class _FakeExecutor:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or ['done'])

    def run_chain(self, path, **kwargs):
        self.calls.append((os.path.basename(path), kwargs))
        return self.replies.pop(0) if self.replies else 'done'


def _agent(root, slug, **kwargs):
    from player.agentic_ops.agent import WorkspaceAgent

    return WorkspaceAgent(root, slug, **kwargs)


def test_a_pinned_brain_answers_alone(root):
    manifest = _workspace_with_chain(root)
    brain = ws.chain_files(root, manifest['slug'])[0]
    manifest = ws.load(root, manifest['slug'])
    manifest['agent']['legacy_brain'] = brain
    ws.save(root, manifest)

    plan = _agent(root, manifest['slug']).plan('find me a job')
    assert plan['source'] == 'brain'
    assert [step['chain'] for step in plan['steps']] == ['jobfinder']


def test_a_single_named_chain_needs_no_model(root):
    manifest = _workspace_with_chain(root)
    plan = _agent(root, manifest['slug']).plan('find me a job')
    assert plan['source'] == 'only-chain'
    assert plan['steps'] == [{'chain': 'jobfinder', 'inputs': {}}]


def _pick(text):
    """A stand-in for Laya's rerank: the index of the option naming *text*."""
    def _rerank(premise, options):
        return next((index for index, option in enumerate(options)
                     if option.lower().startswith(text)), None)
    return _rerank


def test_laya_picks_the_chain_it_is_confident_about(root, monkeypatch):
    from AI import laya_client

    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    monkeypatch.setattr(laya_client, 'rerank', _pick('form'))
    monkeypatch.setattr(laya_client, 'decision', lambda premise, crit: True)

    plan = _agent(root, manifest['slug']).plan('fill the application form')
    assert plan['source'] == 'laya'
    assert plan['steps'][0]['chain'] == 'form'


def test_a_refused_laya_pick_falls_through_to_the_planner(root, monkeypatch):
    from AI import laya_client

    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    monkeypatch.setattr(laya_client, 'rerank', _pick('form'))
    monkeypatch.setattr(laya_client, 'decision', lambda premise, crit: False)

    asked = []

    def _llm(prompt, system, model):
        asked.append(prompt)
        assert 'jobfinder' in prompt and 'form' in prompt
        return json.dumps({'steps': [{'chain': 'form', 'inputs': {}}]})

    plan = _agent(root, manifest['slug'], llm=_llm).plan('fill the form')
    assert plan['source'] == 'plan'
    assert asked, 'the planner must be asked when Laya refuses'


def test_the_planner_orders_several_chains(root):
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))

    def _llm(prompt, system, model):
        return json.dumps({'steps': [
            {'chain': 'jobfinder', 'inputs': {'search terms': 'ML'}},
            {'chain': 'form', 'inputs': {}}]})

    plan = _agent(root, manifest['slug'], llm=_llm).plan('find and apply')
    assert [step['chain'] for step in plan['steps']] == ['jobfinder', 'form']
    assert plan['steps'][0]['inputs'] == {'search terms': 'ML'}


def test_a_plan_naming_a_chain_outside_the_workspace_is_an_error(root):
    from player.agentic_ops.agent import AgentError

    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    agent = _agent(root, manifest['slug'],
                   llm=lambda prompt, system, model:
                   json.dumps({'steps': [{'chain': 'ghost'}]}))
    with pytest.raises(AgentError, match='not in workspace'):
        agent.plan('do something')


def test_a_workspace_with_no_described_chain_refuses_to_route(root):
    from player.agentic_ops.agent import AgentError

    manifest = ws.create(root, 'Empty')
    agent = _agent(root, manifest['slug'])
    with pytest.raises(AgentError, match='describes itself'):
        agent.plan('anything')


# --------------------------------------------------------------- learning

def _picker(*chain_ids):
    """A stand-in for Laya's tool choice: the next id of the script."""
    order = list(chain_ids)
    calls = []

    def _pick(intent, usable):
        calls.append(intent)
        if not order:
            return None
        from player.agentic_ops import catalog
        return catalog.find(usable, order.pop(0))

    _pick.calls = calls
    return _pick


def _brain(*replies):
    """A stand-in for the two reasoning passes, in call order.

    Call 1 is the UNDERSTANDING pass (``{understood, tasks, ask}``); each later
    call is a VERIFICATION pass (``{done, answer, next, ask}``).
    """
    prompts = []
    queue = list(replies)

    def _llm(prompt, system, model):
        prompts.append(prompt)
        if not queue:
            return json.dumps({'done': True, 'answer': ''})
        return json.dumps(queue.pop(0))

    _llm.prompts = prompts
    return _llm


def _tasks(*tasks, understood='as asked', ask=''):
    """An understanding reply (the initial reasoning pass)."""
    return {'understood': understood, 'tasks': list(tasks), 'ask': ask}


def test_the_first_pass_writes_the_task_list_and_laya_runs_it(root):
    """Understand first: every clause becomes a task, one tool each."""
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    executor = _FakeExecutor(['three postings', 'application sent'])
    pick = _picker('jobfinder', 'form')
    llm = _brain(_tasks('find the postings', 'fill the application form',
                        understood='find postings, then apply'),
                 {'done': True, 'answer': 'application sent'})
    agent = _agent(root, manifest['slug'], executor=executor, llm=llm,
                   picker=pick)

    assert agent.run('find and apply') == 'application sent'

    # the first model call is the understanding pass, and it sees the tools
    assert 'Split the request into' in llm.prompts[0]
    assert 'jobfinder' in llm.prompts[0]
    # each task became one pick, in order
    assert pick.calls == ['find the postings', 'fill the application form']
    first, second = executor.calls
    assert first[0] == 'jobfinder.json' and second[0] == 'form.json'
    # the previous step's result travels into the next step's request
    assert 'three postings' in second[1]['agent_query']

    stored = agent.store.plans()
    assert [plan['goal'] for plan in stored] == ['find and apply']
    assert stored[0]['runs'] == 1 and stored[0]['ok'] == 1
    assert [step['why'] for step in stored[0]['steps']] == [
        'find the postings', 'fill the application form']


def test_a_task_no_chain_covers_is_reported_not_dropped(root):
    """A gap in the toolbox must reach the user, not vanish."""
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    executor = _FakeExecutor(['sent'])
    agent = _agent(
        root, manifest['slug'], executor=executor,
        llm=_brain(_tasks('fill the form', 'book a flight'),
                   {'done': True, 'answer': 'sent'}),
        # only ONE pick answers: the second task has no chain
        picker=_picker('form'))

    answer = agent.run('fill the form then book a flight')
    assert 'sent' in answer
    assert 'book a flight' in answer
    assert 'No chain' in answer


def test_memory_bypasses_reasoning_only_when_it_is_near_verbatim(root,
                                                                monkeypatch):
    """Semi deterministic: memory is reassurance, not a shortcut on everything.

    The episode must be the SAME request (exact text, or ≥0.97 same-question)
    and must have won at least twice.
    """
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))

    # 1) one winning run is NOT enough to bypass reasoning
    agent = _agent(root, manifest['slug'],
                   executor=_FakeExecutor(['postings', 'sent']),
                   llm=_brain(_tasks('find postings', 'fill the form'),
                              {'done': True, 'answer': 'sent'}),
                   picker=_picker('jobfinder', 'form'))
    agent.run('find and apply')
    (stored,) = agent.store.plans()
    assert stored['ok'] == 1
    assert agent.store.confident('find and apply') is None, 'one win is luck'

    # 2) a second win makes it a lesson — for the SAME text only
    agent.store.result('find and apply', True)
    assert agent.store.confident('find and apply') is not None

    monkeypatch.setattr('AI.laya_client.noul',
                        lambda premise, instruction: 0.80, raising=False)
    assert agent.store.confident('find and apply now') is None, \
        'a merely similar request must be reasoned about'

    monkeypatch.setattr('AI.laya_client.noul',
                        lambda premise, instruction: 0.99, raising=False)
    assert agent.store.confident('find and apply now') is not None, \
        'near-verbatim may reuse the lesson'


def test_a_replay_costs_no_model_call_and_keeps_the_why(root, monkeypatch):
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    monkeypatch.setattr('AI.laya_client.noul',
                        lambda premise, instruction: 0.99, raising=False)

    agent = _agent(root, manifest['slug'],
                   executor=_FakeExecutor(['postings', 'sent']),
                   llm=_brain(_tasks('find postings', 'fill the form'),
                              {'done': True, 'answer': 'sent'}),
                   picker=_picker('jobfinder', 'form'))
    agent.run('find and apply')
    agent.store.result('find and apply', True)   # a second win: now it is a lesson

    def _boom(*_args, **_kwargs):
        raise AssertionError('a replay must not call the model')

    replay = _agent(root, manifest['slug'],
                    executor=_FakeExecutor(['postings', 'sent']), llm=_boom)
    assert replay.run('find and apply') == 'sent'
    assert [step['why'] for step in replay.store.plans()[0]['steps']] == [
        'find postings', 'fill the form'], 'the why survives the replay'


def test_the_reasoner_can_ask_the_user_instead_of_guessing(root):
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    executor = _FakeExecutor(['postings'])
    agent = _agent(
        root, manifest['slug'], executor=executor,
        llm=_brain(_tasks(ask='which posting should I apply to?'),
                   _tasks('fill the form', understood='fill it'),
                   {'done': True, 'answer': 'filled'}),
        picker=_picker('form'))
    asked = []

    def _ask(question):
        asked.append(question)
        return 'the ML one'

    agent.run('apply to a job', ask_user=_ask)
    assert asked == ['which posting should I apply to?']
    assert len(executor.calls) == 1, 'it acted only after understanding'


def test_the_loop_carries_the_memory_of_a_similar_run(root):
    """Graph recall: the reasoner reads the past order and each step's why.

    The stored episode is the SAME goal but a losing one, so it cannot replay —
    the loop runs and still gets the memory in the prompt, which is what makes
    the agent try a different order instead of the one that failed.
    """
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    agent = _agent(root, manifest['slug'], executor=_FakeExecutor(['sent']),
                   llm=_brain(_tasks('fill the form'),
                              {'done': True, 'answer': 'sent'}),
                   picker=_picker('form'))
    agent.store.record('find and apply',
                       [{'chain': 'jobfinder', 'why': 'find postings'},
                        {'chain': 'form', 'why': 'fill the form'}])
    agent.store.result('find and apply', False)

    agent.run('find and apply')
    prompt = agent._llm.prompts[0]
    assert 'PAST RUNS WITH A SIMILAR REQUEST' in prompt, prompt
    assert 'why: find postings' in prompt
    assert '1 fail' in prompt


def test_a_failed_run_is_counted_but_does_not_lose_the_plan(root,
                                                         monkeypatch):
    """A chain that raises still becomes a failed episode."""
    from player.multi_sequence.llm_executor_resources import config_utils

    monkeypatch.setattr(
        config_utils, "get_default_llm_model",
        lambda engine="llamacpp": (_ for _ in ()).throw(
            RuntimeError('no default model')))
    manifest = _workspace_with_chain(root)
    agent = _agent(root, manifest['slug'], executor=_BrokenExecutor(),
                   picker=_picker('jobfinder'))
    with pytest.raises(RuntimeError):
        agent.run('find me a job')
    stored = agent.store.plans()
    assert stored[0]['fail'] == 1 and stored[0]['ok'] == 0


def test_without_a_reasoner_the_agent_runs_one_tool(root, monkeypatch):
    """No model configured anywhere: one tool, and it says so."""
    from player.multi_sequence.llm_executor_resources import config_utils

    monkeypatch.setattr(
        config_utils, "get_default_llm_model",
        lambda engine="llamacpp": (_ for _ in ()).throw(
            RuntimeError('no default model')))
    manifest = _workspace_with_chain(root)
    executor = _FakeExecutor(['three postings'])
    pick = _picker('jobfinder', 'jobfinder')
    agent = _agent(root, manifest['slug'], executor=executor, picker=pick)
    assert 'three postings' in agent.run('find me a job')
    assert len(executor.calls) == 1
    assert agent.store.plans()[0]['ok'] == 1


def test_a_losing_learned_plan_is_not_replayed(root):
    """History says this order fails: keep it as history, run the loop."""
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    agent = _agent(
        root, manifest['slug'], executor=_FakeExecutor(['sent']),
        llm=lambda prompt, system, model:
        json.dumps({'steps': [{'chain': 'form', 'inputs': {}}]}),
        picker=_picker('form'))
    agent.store.record('find and apply',
                       [{'chain': 'jobfinder'}, {'chain': 'form'}])
    agent.store.result('find and apply', False)
    agent.store.result('find and apply', False)

    plan = agent.plan('find and apply')
    assert plan['source'] != 'learned'
    assert agent.store.plans()[0]['fail'] == 2, 'the history is kept'


def test_a_run_teaches_what_comes_next(root):
    """A -> B is learned and offered to the planner as evidence."""
    manifest = _workspace_with_chain(root, name='Two')
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))
    executor = _FakeExecutor(['three postings', 'sent'])
    agent = _agent(
        root, manifest['slug'], executor=executor,
        llm=_brain(_tasks('find postings', 'fill the form'),
                   {'done': True, 'answer': 'sent'}),
        picker=_picker('jobfinder', 'form'))
    agent.run('find and apply')

    table = agent.store.read()['transitions']
    assert table['jobfinder']['form'] == {'ok': 1, 'fail': 0}
    assert 'after jobfinder, form usually follows' in agent.store.hints()

    # a NEW request reaches the planner with that evidence in the prompt
    prompts = []

    def _llm(prompt, system, model):
        prompts.append(prompt)
        return json.dumps({'steps': [{'chain': 'form', 'inputs': {}}]})

    other = _agent(root, manifest['slug'], executor=_FakeExecutor(), llm=_llm)
    other.plan('apply to that other posting')
    assert 'WHAT EARLIER RUNS TAUGHT' in prompts[0]
    assert 'after jobfinder, form usually follows' in prompts[0]


def test_when_to_ask_and_when_to_report_are_recorded(root, monkeypatch):
    """The chain order encodes asking/reporting; the run records it."""
    from player.agentic_ops import catalog
    from player.multi_sequence.llm_executor_resources import config_utils

    monkeypatch.setattr(
        config_utils, "get_default_llm_model",
        lambda engine="llamacpp": (_ for _ in ()).throw(
            RuntimeError('no default model')))

    asking = dict(CHAIN)
    asking['input_nodes'] = [{'node_id': '0x2', 'label': 'search terms',
                              'user_prompt': 'which roles?'}]
    manifest = _workspace_with_chain(root, data=asking)
    (item,) = catalog.entries(root, manifest['slug'])
    assert item['asks'] is True and item['reports'] is True

    agent = _agent(root, manifest['slug'], executor=_FakeExecutor(['ok']),
                   picker=_picker('jobfinder'))
    agent.run('find me a job')
    (plan,) = agent.store.plans()
    assert plan['ask'] == 1, plan
    assert plan['report'] == 1, plan


class _BrokenExecutor:
    def run_chain(self, path, **kwargs):
        raise RuntimeError('chain blew up')


# ------------------------------------------- the SHORT labels the router scores

def test_the_router_scores_chain_names_not_the_long_brief(root, monkeypatch):
    """A long brief outranks the atomic tool that matched (measured: 0.80 for
    an auto-apply loop vs 0.54 for 'open linkedin').  Routing therefore hands
    Laya the chain's NAME and verifies with a same-task statement."""
    from AI import laya_client
    from player.agentic_ops import catalog

    manifest = _workspace_with_chain(root)
    (entry,) = catalog.entries(root, manifest['slug'])
    labels = catalog.task_labels(entry)
    assert labels[0] == 'jobfinder', labels
    assert all('searches job boards' not in text for text in labels), labels

    seen = {}

    def _rerank(premise, options):
        seen['options'] = list(options)
        return 0

    def _decision(premise, criterion):
        seen['criterion'] = criterion
        return True

    monkeypatch.setattr(laya_client, 'rerank', _rerank)
    monkeypatch.setattr(laya_client, 'decision', _decision)

    plan = _agent(root, manifest['slug']).plan('find me a job')
    assert plan['source'] == 'laya'
    assert seen['options'] == ['jobfinder'], seen['options']
    assert seen['criterion'] == 'This text asks for the same task as: jobfinder'


def test_a_chain_s_past_requests_are_labels_too(root, monkeypatch):
    """A request the chain already answered is user text, like the new one."""
    from AI import laya_client

    manifest = _workspace_with_chain(root, data=dict(
        CHAIN, routing={'examples': ['click on the jobs section']}))
    seen = {}

    def _rerank(premise, options):
        seen['options'] = list(options)
        return 1

    monkeypatch.setattr(laya_client, 'rerank', _rerank)
    monkeypatch.setattr(laya_client, 'decision', lambda p, c: True)

    plan = _agent(root, manifest['slug']).plan('click on the jobs section')
    assert seen['options'] == ['jobfinder', 'click on the jobs section']
    assert plan['source'] == 'laya'


def test_a_refused_verdict_means_no_pick(root, monkeypatch):
    """The verdict is a DECISION: a refused pick is not run anyway."""
    from AI import laya_client

    manifest = _workspace_with_chain(root)
    _write(os.path.join(ws.chains_dir(root, manifest['slug']), 'form.json'),
           dict(CHAIN, flow={'summary': 'fills a web form'}))

    monkeypatch.setattr(laya_client, 'rerank', lambda p, o: 0)
    monkeypatch.setattr(laya_client, 'decision', lambda p, c: False)
    with pytest.raises(Exception) as raised:
        _agent(root, manifest['slug']).plan('fill the form')
    assert 'reasoner model' in str(raised.value)


# ------------------------------------------------------ the planner's model

def test_the_reasoner_model_is_the_workspace_s_own_choice(root):
    """No implicit default: the model is what the user picked in agent mode."""
    manifest = ws.create(root, 'Job hunt')
    assert _agent(root, manifest['slug']).model == ''

    ws.set_agent_model(root, manifest['slug'], 'llama3.2:1b')
    assert _agent(root, manifest['slug']).model == 'llama3.2:1b'
    assert ws.load(root, manifest['slug'])['agent']['model'] == 'llama3.2:1b'

    # a BYOK reference is stored verbatim: the provider prefix IS the routing
    ws.set_agent_model(root, manifest['slug'], 'openai:gpt-4o-mini')
    assert _agent(root, manifest['slug']).model == 'openai:gpt-4o-mini'

    ws.set_agent_model(root, manifest['slug'], '')
    assert _agent(root, manifest['slug']).model == ''

    with pytest.raises(ws.WorkspaceError):
        ws.set_agent_model(root, 'does-not-exist', 'x')


def test_a_request_with_no_reasoner_says_so_in_the_answer(root):
    """Silently stopping after one tool reads as ignoring the order."""
    manifest = _workspace_with_chain(root)
    agent = _agent(root, manifest['slug'],
                   executor=_FakeExecutor(['three postings']),
                   picker=_picker('jobfinder'))

    answer = agent.run('open linkedin then check my network and then my jobs')
    assert 'three postings' in answer
    assert 'Stopped after one step' in answer
    assert 'MODEL button' in answer

