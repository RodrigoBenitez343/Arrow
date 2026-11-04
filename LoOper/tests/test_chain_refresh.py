"""A chain's description is kept current in the BACKGROUND, and only when it
actually changed.

A chain described once kept routing on that sentence after an edit, because
nothing looked again until someone asked the Builder.  Agent mode reloads now
sweep the workspace.  Two properties matter as much as the refresh itself:

* **nothing happens if nothing changed** — the chain-level stamp (node types +
  settings, blind to ids and order) is compared before any work, and an
  unchanged file is left byte-for-byte untouched;
* **one edit is one line** — each stored sentence carries the fingerprint of
  the node version it describes, so only the node that changed is re-derived;
  the rest keep the wording they already had.  No model is called: the sweep is
  deterministic, so "the LLM re-reads every chain" cannot happen.

Contract pinned here:

* the stamp ignores ids and list order, and changes on a real edit;
* only a DESCRIBED chain is refreshed — an undescribed one is left alone;
* a stale chain keeps authored text for unchanged nodes (even across a reload
  that renames every node id) and re-derives only the edited one;
* a current chain is not rewritten at all;
* a broken file is reported and the sweep carries on;
* binding agent mode to a workspace (``SimpleChainRouter.set_agent``) triggers
  the sweep.

Run:  python -m pytest LoOper/tests/test_chain_refresh.py
"""

import json
import os

import pytest

from player import chain_brief
from player import chain_refresh
from player import workspace as ws
from player.builder import flow as flow_module

CHAIN = {
    'llm_nodes': [
        {'type': 'llm', 'node_id': '0x1', 'name': 'draft',
         'prompt': 'Write a polite reply about {topic}',
         'model': 'llama3.2:1b', 'position': [0, 0], 'connections': []},
        {'type': 'llm', 'node_id': '0x2', 'name': 'polish',
         'semantic_description': 'polishes the reply',
         'position': [0, 200], 'connections': []},
    ],
    'output_nodes': [
        {'type': 'output', 'node_id': '0x3', 'label': 'reply',
         'position': [0, 400], 'connections': []},
    ],
}


@pytest.fixture
def workspace(tmp_path):
    root = str(tmp_path / 'workspaces')
    manifest = ws.create(root, 'Tools', agent_name='Scout')
    return root, manifest['slug']


def _write(root, slug, name, payload):
    path = os.path.join(ws.chains_dir(root, slug), name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2)
    return path


def _authored(node_key, index, ntype, text):
    """A stored sentence stamped for the node version it describes."""
    entry = CHAIN[node_key][index]
    return {'node': entry['node_id'], 'does': text,
            'stamp': flow_module.node_stamp(ntype, entry)}


def _described(nodes=None, stamped=False, **flow):
    payload = dict(CHAIN)
    payload['flow'] = dict(flow)
    if nodes is not None:
        payload['flow']['nodes'] = nodes
    if stamped:
        payload['flow']['stamp'] = flow_module.stamp_of(payload)
    return payload


# ------------------------------------------------------------------- stamps

def test_the_stamp_ignores_ids_and_list_order():
    """A reload mints new ids and may reorder: neither is a change."""
    shuffled = {'llm_nodes': list(reversed(CHAIN['llm_nodes'])),
                'output_nodes': list(CHAIN['output_nodes'])}
    assert flow_module.stamp_of(CHAIN) == flow_module.stamp_of(shuffled)

    renamed = json.loads(json.dumps(CHAIN))
    for index, entry in enumerate(renamed['llm_nodes']):
        entry['node_id'] = '0xlive%d' % index
    assert flow_module.stamp_of(CHAIN) == flow_module.stamp_of(renamed)


def test_a_changed_setting_changes_the_stamp():
    edited = json.loads(json.dumps(CHAIN))
    edited['llm_nodes'][0]['model'] = 'qwen2.5:1.5b'
    assert flow_module.stamp_of(CHAIN) != flow_module.stamp_of(edited)


def test_an_undescribed_chain_is_never_stale():
    assert flow_module.is_stale(CHAIN) is False


# ------------------------------------------------------------------- sweep

def test_a_stale_chain_is_refreshed_from_its_own_nodes(workspace):
    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(
        nodes=[_authored('llm_nodes', 0, 'llm',
                         'drafts the reply from the topic')],
        summary='Drafts and polishes a reply.',
        sections=[{'title': 'write', 'nodes': ['0x1', '0x2']}],
    ))

    assert chain_refresh.refresh_file(path) == chain_refresh.UPDATED
    data = json.loads(open(path, encoding='utf-8').read())
    flow = data['flow']

    described = {entry['node']: entry['does'] for entry in flow['nodes']}
    assert len(described) == 3, described
    assert described['0x1'] == 'drafts the reply from the topic', 'authored wins'
    assert 'polishes the reply' in described['0x2'], described
    assert 'prints "reply"' in described['0x3'], described
    # the authored parts of the metadata are not touched
    assert flow['summary'] == 'Drafts and polishes a reply.'
    assert flow['sections'][0]['title'] == 'write'
    # ...and the derived description matches the refreshed metadata
    assert data['description'] == chain_brief.brief(data)
    assert flow['stamp'] == flow_module.stamp_of(data)


def test_only_the_edited_node_is_re_derived(workspace):
    """One node changes: the other sentences must survive verbatim."""
    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(nodes=[
        _authored('llm_nodes', 0, 'llm', 'writes the first draft'),
        _authored('llm_nodes', 1, 'llm', 'polishes what the draft says'),
        _authored('output_nodes', 0, 'output', 'shows the reply to the user'),
    ], stamped=True, summary='Drafts and polishes a reply.'))
    assert chain_refresh.refresh_file(path) == chain_refresh.CURRENT, \
        'already describes this chain'

    data = json.loads(open(path, encoding='utf-8').read())
    data['llm_nodes'][0]['model'] = 'qwen2.5:1.5b'       # ONE node edited
    _write(root, slug, 'tool.json', data)

    assert chain_refresh.refresh_file(path) == chain_refresh.UPDATED
    flow = json.loads(open(path, encoding='utf-8').read())['flow']
    described = {entry['node']: entry['does'] for entry in flow['nodes']}
    assert 'qwen2.5:1.5b' in described['0x1'], described['0x1']
    assert described['0x2'] == 'polishes what the draft says', 'untouched'
    assert described['0x3'] == 'shows the reply to the user', 'untouched'


def test_a_reload_that_renames_every_id_keeps_the_wording(workspace):
    """Ids are regenerated on load: the same node must still be recognised."""
    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(nodes=[
        _authored('llm_nodes', 0, 'llm', 'writes the first draft'),
        _authored('llm_nodes', 1, 'llm', 'polishes what the draft says'),
        _authored('output_nodes', 0, 'output', 'shows the reply to the user'),
    ], summary='Drafts and polishes a reply.'))
    chain_refresh.refresh_file(path)

    data = json.loads(open(path, encoding='utf-8').read())
    for index, entry in enumerate(data['llm_nodes']):
        entry['node_id'] = '0xnew%d' % index
    data['output_nodes'][0]['node_id'] = '0xnewout'
    data['output_nodes'][0]['label'] = 'answer'   # a REAL change, so stale
    # the stored sentences still name the OLD ids, exactly as a file saved by
    # the editor leaves them: only the node fingerprint can tie them together
    data['description'] = 'stale'
    _write(root, slug, 'tool.json', data)

    assert chain_refresh.refresh_file(path) == chain_refresh.UPDATED
    flow = json.loads(open(path, encoding='utf-8').read())['flow']
    described = {entry['node']: entry['does'] for entry in flow['nodes']}
    assert described['0xnew0'] == 'writes the first draft', described
    assert described['0xnew1'] == 'polishes what the draft says', described
    assert 'answer' in described['0xnewout'], described


def test_the_llm_line_comes_from_the_SAVED_settings(workspace):
    """No canvas: the file's own prompt/model must produce the sentence."""
    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(summary='Writes a reply.'))
    chain_refresh.refresh_file(path)
    flow = json.loads(open(path, encoding='utf-8').read())['flow']
    line = next(entry['does'] for entry in flow['nodes'] if entry['node'] == '0x1')
    assert 'llama3.2:1b' in line, line
    assert 'polite reply' in line, line


def test_a_current_chain_is_not_rewritten(workspace):
    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(
        nodes=[_authored('llm_nodes', 0, 'llm', 'writes the first draft')],
        stamped=True, summary='Writes a reply.'))
    before = open(path, 'rb').read()
    assert chain_refresh.refresh_file(path) == chain_refresh.CURRENT
    assert open(path, 'rb').read() == before, 'no churn on an unchanged chain'


def test_an_undescribed_chain_is_left_alone(workspace):
    root, slug = workspace
    path = _write(root, slug, 'plain.json', dict(CHAIN))
    before = open(path, 'rb').read()
    assert chain_refresh.refresh_file(path) == chain_refresh.SKIPPED
    assert open(path, 'rb').read() == before


def test_an_edited_chain_goes_stale_and_is_caught_up(workspace):
    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(summary='Writes a reply.'))
    chain_refresh.refresh_file(path)

    data = json.loads(open(path, encoding='utf-8').read())
    data['llm_nodes'][0]['prompt'] = 'Summarise the thread instead'
    _write(root, slug, 'tool.json', data)
    assert flow_module.is_stale(data) is True

    assert chain_refresh.refresh_file(path) == chain_refresh.UPDATED
    refreshed = json.loads(open(path, encoding='utf-8').read())
    line = next(entry['does'] for entry in refreshed['flow']['nodes']
                if entry['node'] == '0x1')
    assert 'Summarise the thread instead' in line, line


def test_a_stale_chain_that_changed_nothing_visible_keeps_every_word(workspace):
    """A node added elsewhere must not reword the nodes that did not change."""
    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(nodes=[
        _authored('llm_nodes', 0, 'llm', 'writes the first draft'),
        _authored('llm_nodes', 1, 'llm', 'polishes what the draft says'),
        _authored('output_nodes', 0, 'output', 'shows the reply to the user'),
    ], stamped=True, summary='Drafts and polishes a reply.'))
    assert chain_refresh.refresh_file(path) == chain_refresh.CURRENT

    data = json.loads(open(path, encoding='utf-8').read())
    data['code_nodes'] = [{'type': 'code', 'node_id': '0x9',
                           'code': 'print(1)'}]        # a NEW node
    _write(root, slug, 'tool.json', data)

    assert chain_refresh.refresh_file(path) == chain_refresh.UPDATED
    flow = json.loads(open(path, encoding='utf-8').read())['flow']
    described = {entry['node']: entry['does'] for entry in flow['nodes']}
    assert described['0x1'] == 'writes the first draft', described
    assert described['0x3'] == 'shows the reply to the user', described
    assert 'runs Python' in described['0x9'], described


def test_a_broken_file_is_reported_and_the_sweep_continues(workspace):
    root, slug = workspace
    good = _write(root, slug, 'good.json', _described(summary='Writes a reply.'))
    broken = os.path.join(ws.chains_dir(root, slug), 'broken.json')
    with open(broken, 'w', encoding='utf-8') as handle:
        handle.write('{nope')

    report = chain_refresh.refresh_workspace(root, slug)
    assert report['checked'] == 2
    assert report['updated'] == 1
    assert report['failed'] == 1
    assert flow_module.is_stale(
        json.loads(open(good, encoding='utf-8').read())) is False


def test_the_refreshed_description_reaches_the_agent_catalog(workspace):
    from player.agentic_ops import catalog

    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(summary='Writes a reply.'))
    chain_refresh.refresh_file(path)

    entry = catalog.entry(path)
    assert entry['routable'] is True
    assert entry['nodes'], entry
    assert '| its nodes: ' in catalog.digest([entry])


# ------------------------------------------------------- agent mode reload

def test_reloading_agent_mode_refreshes_the_workspace(workspace):
    from player.agentic_ops import SimpleChainRouter

    root, slug = workspace
    path = _write(root, slug, 'tool.json', _described(summary='Writes a reply.'))
    assert 'stamp' not in json.loads(open(path, encoding='utf-8').read())['flow']

    SimpleChainRouter(model_path='', chains_dir='', workspaces_root=root,
                      workspace_slug=slug)

    flow = json.loads(open(path, encoding='utf-8').read())['flow']
    assert flow.get('stamp'), 'binding the agent caught the chain up'
    assert len(flow['nodes']) == 3


# ------------------------------------------------- named, then described
#
# Naming a chain is the moment the user says "keep this": the file is
# described right there, so the next run routes on it instead of finding a
# chain that only carries its node list.

def test_a_new_chain_is_described_when_it_is_named(workspace):
    root, slug = workspace
    path = _write(root, slug, 'fresh.json', CHAIN)
    assert 'flow' not in json.loads(open(path, encoding='utf-8').read())

    assert chain_refresh.describe_file(path) == chain_refresh.UPDATED

    saved = json.loads(open(path, encoding='utf-8').read())
    described = {entry['node']: entry['does']
                 for entry in saved['flow']['nodes']}
    assert set(described) == {'0x1', '0x2', '0x3'}
    assert all(described.values()), described
    assert 'writes text with the model "llama3.2:1b"' in described['0x1']
    # ...and the routing line is derived, never typed by the user
    assert saved['description'] == chain_brief.brief(saved)
    assert saved['description']

    # The node's OWN field is where its dialog reads it from: a chain described
    # without ever being opened still shows a sentence on every node.
    for key in ('llm_nodes', 'output_nodes'):
        for entry in saved[key]:
            assert entry['description'] == described[entry['node_id']], entry

    assert chain_refresh.describe_file(path) == chain_refresh.CURRENT


def test_a_section_naming_no_node_of_the_file_is_dropped(workspace):
    """Sections written on a canvas carry THAT session's ids; the file saved
    afterwards carries its own, so a box ended up covering none of what its
    note described.  A ref the file does not contain can never be drawn."""
    root, slug = workspace
    payload = _described([{'node': '0x1', 'does': 'drafts'}], sections=[
        {'title': 'mine', 'nodes': ['0x1']},
        {'title': 'ghost', 'nodes': ['0xdeadbeef']}])
    path = _write(root, slug, 'mixed.json', payload)

    assert chain_refresh.describe_file(path) == chain_refresh.UPDATED
    sections = json.loads(open(path, encoding='utf-8').read())['flow']['sections']
    assert [section['title'] for section in sections] == ['mine']
    assert sections[0]['nodes'] == ['0x1']


def test_describing_again_leaves_the_file_byte_for_byte(workspace):
    root, slug = workspace
    path = _write(root, slug, 'fresh.json', CHAIN)
    chain_refresh.describe_file(path)
    before = open(path, 'rb').read()

    assert chain_refresh.describe_file(path) == chain_refresh.CURRENT
    assert open(path, 'rb').read() == before


def test_describing_a_broken_file_is_reported_not_raised(tmp_path):
    path = tmp_path / 'broken.json'
    path.write_text('{nope', encoding='utf-8')
    assert chain_refresh.describe_file(str(path)) == chain_refresh.FAILED
