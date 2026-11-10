"""Builder analyst tests — ports, snapshot, digest, tools, content, flow.

The builder answers questions about a chain and changes only what the user
selected; these tests pin the contract:

* execution and data edges are classified by PORT ROLE, not by name;
* a node's ports come from its own table (plus code named IO);
* the digest names the start, the walk, branches, loops, phases and defects;
* every tool raises on anything it cannot answer;
* content lands in the field the node reads at run time, and anything
  unreadable/unsupported raises;
* flow metadata validates, boxes fall back to phases, and the walkthrough is
  deterministic (nothing is executed).
"""

import json
import os

import pytest


# --------------------------------------------------------------- fakes

class FakePort:
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


class FakeNode:
    def __init__(self, node_id, node_type, name, props=None,
                 inputs=('input',), outputs=('output',)):
        self.id = node_id
        self.type_ = node_type
        self._name = name
        self._props = dict(props or {})
        self._in = {n: FakePort(self, n, 'in') for n in inputs}
        self._out = {n: FakePort(self, n, 'out') for n in outputs}
        self._pos = (float(len(name)), 0.0)

    # node protocol
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
        return self._pos

    def set_chain_import_data(self, path, *args):
        self._props['chain_file_path'] = path


class FakeGraph:
    def __init__(self, nodes=()):
        self._nodes = {n.id: n for n in nodes}

    def get_all_nodes(self):
        return list(self._nodes.values())

    def get_node_by_id(self, node_id):
        return self._nodes.get(node_id)


def connect(src, src_port, dst, dst_port):
    out = src._out[src_port]
    inp = dst._in[dst_port]
    out.connections.append(inp)
    inp.connections.append(out)


def chain():
    """input -> llm -> conditional -> (true) output ; context pool."""
    inp = FakeNode('0x1', 'input', 'query', props={'label': 'query',
                                                   'data': 'x',
                                                   'passthrough': True},
                   inputs=('input',), outputs=('output', 'data'))
    llm = FakeNode('0x2', 'llm', 'answer',
                   props={'prompt': 'say hi', 'system_message': '',
                          'input_source': 'none'},
                   inputs=('input', 'prompt', 'context'),
                   outputs=('output', 'context'))
    cond = FakeNode('0x3', 'conditional', 'ok?',
                    props={'condition_type': 'llm', 'llm_prompt': 'ok?'},
                    inputs=('input',), outputs=('true', 'false'))
    good = FakeNode('0x4', 'output', 'result', props={'label': 'result'})
    bad = FakeNode('0x5', 'output', 'check failed', props={'label': 'fail'})
    pool = FakeNode('0x6', 'context', 'memory',
                    props={'label': 'memory', 'documents': '["a.md"]'},
                    inputs=('ctx_in',), outputs=('ctx_out',))
    connect(inp, 'output', llm, 'input')
    connect(inp, 'data', llm, 'prompt')
    connect(llm, 'output', cond, 'input')
    connect(cond, 'true', good, 'input')
    connect(cond, 'false', bad, 'input')
    connect(llm, 'context', pool, 'ctx_in')
    connect(pool, 'ctx_out', llm, 'context')
    return FakeGraph([inp, llm, cond, good, bad, pool])


def ctx_for(graph, selected=None):
    return {
        'graph_manager': graph,
        'run_on_gui': lambda fn: fn(),
        'selection_node_ids': lambda: list(selected or []),
        'stop': lambda: False,
        'history': [],
    }


# ---------------------------------------------------------------- ports

def test_port_roles_split_execution_from_data():
    from player.builder import ports

    graph = chain()
    llm = graph.get_node_by_id('0x2')
    ins, outs = ports.ports_of(llm)
    roles = {p.name: p.role for p in ins}
    assert roles['input'] == ports.EXEC_IN
    assert roles['prompt'] == ports.PROMPT_IN
    assert roles['context'] == ports.CONTEXT_IN
    assert {p.name: p.role for p in outs}['context'] == ports.CONTEXT_OUT
    assert ports.edge_kind(ports.EXEC_OUT, ports.EXEC_IN) == 'exec'
    assert ports.edge_kind(ports.DATA_OUT, ports.PROMPT_IN) == 'data'
    assert ports.edge_kind(ports.EXEC_OUT, ports.CONTEXT_IN) == 'illegal'


def test_code_named_ports_are_resolved_from_its_declarations():
    from player.builder import ports

    code = FakeNode('0xc', 'code', 'parse',
                    props={'input_vars': json.dumps([{'name': 'rows'}]),
                           'output_vars': json.dumps([{'name': 'clean'}]),
                           'input': '', 'output': '', 'error': ''},
                    inputs=('input', 'rows'), outputs=('output', 'error', 'clean'))
    ins, outs = ports.ports_of(code)
    assert {p.name: p.role for p in ins}['rows'] == ports.NAMED_IN
    assert {p.name: p.role for p in outs}['clean'] == ports.NAMED_OUT
    assert {p.name: p.role for p in outs}['output'] == ports.EXEC_OUT


def test_an_unclassified_port_raises():
    from player.builder import ports
    from player.builder.errors import UnknownPortError

    node = FakeNode('0xz', 'sequence', 'x', inputs=('input', 'mystery'))
    with pytest.raises(UnknownPortError):
        ports.ports_of(node)


def test_every_type_spelling_resolves_to_one_table_key():
    """A live node reports 'websequence' as readily as 'web_sequence'."""
    from player.builder import node_templates, ports

    spellings = {
        'web_sequence': ('web_sequence', 'WebSequenceNode',
                         'web_sequence.WebSequenceNode', 'websequence'),
        'chain_import': ('chain_import', 'ChainImportNode', 'chainimport'),
        'form_filler': ('form_filler', 'FormFillerNode', 'formfiller'),
        'code': ('code', 'CodeNode', 'code.CodeNode'),
        'llm': ('llm', 'LLMNode', 'llm.LLMNode'),
    }
    for canonical, variants in spellings.items():
        for raw in variants:
            assert node_templates.short_type(raw) == canonical, raw
        assert canonical in ports.PORT_TABLE, canonical
        assert node_templates.role_of(canonical), canonical


def test_a_flattened_web_sequence_type_still_describes():
    """The crash this fixes: the whole digest raised on one 'websequence'."""
    from player.builder import describe, snapshot

    web = FakeNode('0x7', 'websequence', 'scrape',
                   props={'session_file': 'scrape.json'},
                   inputs=('input',), outputs=('output', 'ctx_out'))
    done = FakeNode('0x8', 'output', 'report', props={'label': 'report'})
    connect(web, 'output', done, 'input')
    snap = snapshot.build(FakeGraph([web, done]))
    assert snap.nodes['0x7'].type == 'web_sequence'
    assert snap.nodes['0x7'].role == 'ACT'
    digest = describe.overview(snap)
    assert 'web_sequence' in digest
    assert 'TOPOLOGY DEFECTS' in digest


# ------------------------------------------------------------- snapshot

def test_snapshot_classifies_every_edge():
    from player.builder import snapshot

    snap = snapshot.build(chain())
    kinds = {(e.src_id, e.src_port): e.kind for e in snap.edges}
    assert kinds[('0x1', 'output')] == 'exec'
    assert kinds[('0x1', 'data')] == 'data'
    assert kinds[('0x3', 'true')] == 'exec'
    assert kinds[('0x6', 'ctx_out')] == 'data'
    assert snap.roots()[0].id == '0x1'
    assert snap.nodes['0x6'].is_passive is True


def test_snapshot_keeps_an_illegal_wire_as_a_defect():
    from player.builder import describe, snapshot

    graph = chain()
    connect(graph.get_node_by_id('0x2'), 'output',
            graph.get_node_by_id('0x6'), 'ctx_in')   # exec out -> data in
    snap = snapshot.build(graph)
    assert any(e.kind == 'illegal' for e in snap.edges)
    digest = describe.overview(snap)
    assert 'illegal wire' in digest


# ------------------------------------------------------------- describe

def test_overview_describes_start_walk_branches_and_data():
    from player.builder import describe, snapshot

    digest = describe.overview(snapshot.build(chain()))
    assert 'START' in digest and '0x1' in digest
    assert 'EXECUTION WALK' in digest
    assert 'true' in digest and 'false' in digest
    assert 'PHASES' in digest
    assert 'MEMORY POOLS' in digest
    assert 'REPLACES the prompt template' in describe.data_digest(
        snapshot.build(chain()))
    assert 'TOPOLOGY DEFECTS' in digest


def test_a_conditional_without_a_false_wire_is_a_defect():
    from player.builder import describe, snapshot

    graph = chain()
    cond = graph.get_node_by_id('0x3')
    bad = graph.get_node_by_id('0x5')
    cond._out['false'].connections.clear()
    bad._in['input'].connections.clear()
    problems = describe.violations(snapshot.build(graph))
    assert any("no wire on its 'false' branch" in p for p in problems)


def test_a_sequence_without_a_recording_is_a_defect():
    from player.builder import describe, snapshot

    seq = FakeNode('0x9', 'sequence', 'click apply',
                   props={'sequence_file': ''})
    other = FakeNode('0xa', 'output', 'done')
    connect(seq, 'output', other, 'input')
    problems = describe.violations(snapshot.build(FakeGraph([seq, other])))
    assert any('replays nothing' in p for p in problems)


def test_phases_work_for_long_chains():
    from player.builder import describe, snapshot

    nodes = [FakeNode('0x0', 'sequence', 'start', props={'sequence_file': 'a.json'})]
    previous = nodes[0]
    for index in range(1, 12):
        node = FakeNode('0x%x' % (index + 10), 'sequence', 'step %d' % index,
                        props={'sequence_file': 's%d.json' % index})
        connect(previous, 'output', node, 'input')
        nodes.append(node)
        previous = node
    snap = snapshot.build(FakeGraph(nodes))
    lines = describe.phases(snap, describe.walk(snap)[1])
    assert lines[0].startswith('PHASES')
    assert len(lines) > 1


def test_node_detail_prints_port_roles_and_wires():
    from player.builder import describe, snapshot

    detail = describe.node_detail(snapshot.build(chain()), '0x2')
    assert 'prompt (prompt in)' in detail
    assert 'context (context out)' in detail
    assert 'CONFIG' in detail


# ---------------------------------------------------------------- tools

def test_read_graph_lists_ports_with_roles():
    from player.builder import tools

    text = tools.read_graph(ctx_for(chain()))
    assert 'llm' in text
    assert 'prompt (prompt in)' in text
    assert 'data (data out)' in text


def test_describe_chain_scopes_dispatch():
    from player.builder import tools

    ctx = ctx_for(chain())
    assert 'DATA FLOWS' in tools.describe_chain(ctx, {'scope': 'data'})
    assert 'EXECUTION WALK' in tools.describe_chain(ctx, {'scope': 'walk'})
    assert 'answer' in tools.describe_chain(
        ctx, {'scope': 'node', 'node': 'answer'})


def test_tools_raise_on_unknown_and_unanswerable_requests():
    from player.builder import tools
    from player.builder.errors import (BuilderCanvasError, ToolArgumentError,
                                       UnknownToolError)

    ctx = ctx_for(chain())
    with pytest.raises(UnknownToolError):
        tools.execute('create_node', {}, ctx)
    with pytest.raises(ToolArgumentError):
        tools.describe_chain(ctx, {'scope': 'node'})
    with pytest.raises(ToolArgumentError):
        tools.read_node(ctx, {})
    with pytest.raises(BuilderCanvasError):
        tools.read_node(ctx, {'node': 'nope'})


def test_selection_text_reports_the_selected_nodes():
    from player.builder import tools

    text = tools.selection_text(ctx_for(chain(), selected=['0x3']))
    assert 'ok?' in text and 'CHECK' in text


# ------------------------------------------------------------ assistant

def test_extract_reply_requires_the_envelope():
    from player.builder.assistant import extract_reply
    from player.builder.errors import BuilderProtocolError

    reply, actions = extract_reply(
        '```json\n{"reply": "hi", "actions": [{"name": "read_node", '
        '"arguments": {"node": "x"}}]}\n```')
    assert reply == 'hi' and actions[0]['name'] == 'read_node'
    # Prose is an answer: a small model that cannot wrap it in JSON must not
    # fail the turn (the panel shows the text and annotates nothing).
    reply, actions = extract_reply('just prose, no envelope')
    assert reply == 'just prose, no envelope' and actions == []
    # A WRAPPER that is there but broken is still an error.
    with pytest.raises(BuilderProtocolError):
        extract_reply('{"reply": 1, "actions": "no"}')


def test_partial_reply_streams_only_the_reply_string():
    from player.builder.assistant import partial_reply

    assert partial_reply('{"reply": "the cha') == 'the cha'
    assert partial_reply('{"act') == ''


def test_route_answers_from_the_digest(monkeypatch):
    from player.builder import assistant

    seen = {}

    def fake_generate(prompt, system, settings, on_delta=None, cancel=None):
        seen['prompt'] = prompt
        seen['system'] = system
        if callable(on_delta):
            on_delta('{"reply": "It searches and reports."}')
        return '{"reply": "It searches and reports.", "actions": []}'

    monkeypatch.setattr(assistant._reasoning, 'generate', fake_generate)
    result = assistant.BuilderAssistant(
        ctx_for(chain()), settings={'engine': 'llamacpp', 'model': 'm'}).route(
        'what does this chain do?')
    assert result['reply'] == 'It searches and reports.'
    assert 'CHAIN TOPOLOGY' in seen['prompt']
    assert 'EXECUTION WALK' in seen['prompt']
    # The chat reads nodes; it does not describe the chain (that tool is gone).
    assert 'read_node' in seen['system']
    assert 'describe_chain' not in seen['system']


def test_route_runs_a_read_tool_then_answers(monkeypatch):
    from player.builder import assistant

    calls = []

    def fake_generate(prompt, system, settings, on_delta=None, cancel=None):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({'reply': '', 'actions': [
                {'name': 'read_node',
                 'arguments': {'node': 'answer'}}]})
        return json.dumps({'reply': 'The llm step answers.', 'actions': []})

    monkeypatch.setattr(assistant._reasoning, 'generate', fake_generate)
    result = assistant.BuilderAssistant(
        ctx_for(chain()), settings={'engine': 'llamacpp', 'model': 'm'},
    ).route('explain the answer node')
    assert result['steps'] == 1
    assert 'TOOL RESULTS' in calls[1]
    assert 'The llm step answers.' in result['reply']


def test_route_raises_on_an_unknown_tool(monkeypatch):
    from player.builder import assistant
    from player.builder.errors import UnknownToolError

    monkeypatch.setattr(
        assistant._reasoning, 'generate',
        lambda *a, **k: json.dumps({'reply': '', 'actions': [
            {'name': 'create_node', 'arguments': {}}]}))
    with pytest.raises(UnknownToolError):
        assistant.BuilderAssistant(
            ctx_for(chain()), settings={'engine': 'llamacpp', 'model': 'm'},
        ).route('build me a bot')


def test_dsml_markup_from_the_local_model_is_parsed_not_fatal():
    """A turn died here: the model answered with its chat-template markup."""
    from player.builder.assistant import extract_turn

    raw = (
        '<｜｜DSML｜｜ calls> '
        '<｜｜DSML｜｜ invoke name="read_node"> '
        '<｜｜DSML｜｜ parameter name="node" string="true">0x21a046a5e00'
        '</｜｜DSML｜｜ parameter> </｜｜DSML｜｜ invoke> '
        '<｜｜DSML｜｜ invoke name="read_node"> '
        '<｜｜DSML｜｜ parameter name="node" string="true">0x21a046a5cd0'
        '</｜｜DSML｜｜ parameter> </｜｜DSML｜｜ invoke> '
        '</｜｜DSML｜｜ calls>')
    reply, actions, flow, walk = extract_turn(raw)
    assert [a['name'] for a in actions] == ['read_node', 'read_node']
    assert actions[0]['arguments'] == {'node': '0x21a046a5e00'}
    assert actions[1]['arguments'] == {'node': '0x21a046a5cd0'}
    assert reply == '' and flow == {} and walk is False


def test_dsml_markup_keeps_the_prose_around_it():
    from player.builder.assistant import _dsml_turn, extract_turn

    raw = ('Let me look at both ends. <｜｜DSML｜｜ invoke name="read_node"> '
           '<｜｜DSML｜｜ parameter name="node">0x1</｜｜DSML｜｜ parameter> '
           '</｜｜DSML｜｜ invoke> Then I answer.')
    reply, actions, _flow, _walk = extract_turn(raw)
    assert actions[0]['arguments'] == {'node': '0x1'}
    assert 'Let me look at both ends.' in reply
    assert 'Then I answer.' in reply
    assert 'DSML' not in reply and 'invoke' not in reply
    assert _dsml_turn('plain prose, no markup') == ('', [])


def test_dsml_markup_reads_non_string_arguments():
    from player.builder.assistant import extract_turn

    raw = ('<｜｜DSML｜｜ invoke name="describe_chain"> '
           '<｜｜DSML｜｜ parameter name="scope" string="true">data'
           '</｜｜DSML｜｜ parameter> '
           '<｜｜DSML｜｜ parameter name="limit" string="false">12'
           '</｜｜DSML｜｜ parameter> </｜｜DSML｜｜ invoke>')
    _reply, actions, _flow, _walk = extract_turn(raw)
    assert actions[0]['arguments'] == {'scope': 'data', 'limit': 12}


def test_route_recovers_when_the_model_answers_with_markup(monkeypatch):
    from player.builder import assistant

    calls = []

    def fake_generate(prompt, system, settings, on_delta=None, cancel=None):
        calls.append(prompt)
        if len(calls) == 1:
            return ('<｜｜DSML｜｜ invoke name="read_node"> '
                    '<｜｜DSML｜｜ parameter name="node" string="true">0x2'
                    '</｜｜DSML｜｜ parameter> </｜｜DSML｜｜ invoke>')
        return json.dumps({'reply': 'It asks, thinks and reports.',
                           'actions': []})

    monkeypatch.setattr(assistant._reasoning, 'generate', fake_generate)
    result = assistant.BuilderAssistant(
        ctx_for(chain()), settings={'engine': 'llamacpp', 'model': 'm'}).route(
        'what does this chain do?')
    assert result['steps'] == 1
    assert 'TOOL RESULTS' in calls[1]
    assert 'llm' in calls[1]           # the tool answered about the node
    assert result['reply'] == 'It asks, thinks and reports.'
    assert result['flow'] == {} and result['walk'] is False


def test_check_nodes_keeps_live_ids_and_accepts_a_name():
    """A chain FILE ids differ from the canvas' — the panel translates them
    (ConfigManager.get_flow); here refs are simply resolved to live ids."""
    from player.builder import flow, snapshot

    snap = snapshot.build(chain())
    stored = flow.check_nodes(snap, flow.normalise({
        'sections': [{'title': 'ask', 'nodes': ['0x1', 'query']}],
        'steps': [{'node': 'answer', 'does': 'answers'}],
    }))
    assert stored['sections'][0]['nodes'] == ['0x1'], 'a name becomes its id'
    assert stored['steps'][0]['node'] == '0x2'


def test_a_stored_ref_resolves_by_id_or_by_name():
    from player.builder import flow, snapshot

    snap = snapshot.build(chain())
    by_id = flow.sections(snap, flow.normalise(
        {'sections': [{'title': 't', 'nodes': ['0x2']}]}))
    by_name = flow.sections(snap, flow.normalise(
        {'sections': [{'title': 't', 'nodes': ['answer']}]}))
    assert by_id[0]['nodes'] == by_name[0]['nodes'] == ['0x2']


def test_a_stored_name_that_no_longer_exists_raises_with_the_section():
    from player.builder import flow, snapshot
    from player.builder.errors import FlowMetadataError

    snap = snapshot.build(chain())
    stored = flow.normalise({'sections': [{'title': 'report',
                                           'nodes': ['ghost']}]})
    with pytest.raises(FlowMetadataError) as excinfo:
        flow.sections(snap, stored)
    assert 'report' in str(excinfo.value) and 'ghost' in str(excinfo.value)


# -------------------------------------------------------------- content

def test_parse_text_and_unsupported_extensions(tmp_path):
    from player.builder import content
    from player.builder.errors import (ContentError, UnsupportedContentError)

    parsed = content.parse_text('# Notes\nhello')
    assert parsed.kind == 'text' and parsed.chars > 0
    with pytest.raises(ContentError):
        content.parse_text('   ')
    binary = tmp_path / 'blob.bin'
    binary.write_bytes(b'\x00\x01')
    with pytest.raises(UnsupportedContentError):
        content.parse_file(str(binary))


def test_ingest_markdown_into_a_context_documents_stream(tmp_path):
    from player.builder import content

    doc = tmp_path / 'spec.md'
    doc.write_text('# Spec\nDo the thing.', encoding='utf-8')
    pool = FakeNode('0x6', 'context', 'memory',
                    props={'documents': '[]', 'skills': '[]'},
                    inputs=('ctx_in',), outputs=('ctx_out',))
    ctx = ctx_for(FakeGraph([pool]))
    results = content.ingest_file(ctx, '0x6', str(doc))
    assert results[0].slot == 'documents'
    stored = json.loads(pool.get_property('documents'))
    assert stored == [os.path.abspath(str(doc))]
    assert 'documents' in results[0].line()


def test_ingest_pasted_text_goes_to_the_skills_stream():
    from player.builder import content

    pool = FakeNode('0x6', 'context', 'memory',
                    props={'documents': '[]', 'skills': '[]'},
                    inputs=('ctx_in',), outputs=('ctx_out',))
    ctx = ctx_for(FakeGraph([pool]))
    content.ingest_text(ctx, '0x6', 'always click Apply twice')
    assert json.loads(pool.get_property('skills')) == ['always click Apply twice']


def test_ingest_writes_a_text_field_of_an_llm_node(tmp_path):
    from player.builder import content

    doc = tmp_path / 'prompt.md'
    doc.write_text('Summarize the page in five bullets.', encoding='utf-8')
    llm = FakeNode('0x2', 'llm', 'answer',
                   props={'prompt': '', 'system_message': ''},
                   inputs=('input', 'prompt', 'context'),
                   outputs=('output', 'context'))
    ctx = ctx_for(FakeGraph([llm]))
    content.ingest_file(ctx, '0x2', str(doc), slot_name='system_message')
    assert 'five bullets' in llm.get_property('system_message')


def test_ingest_refuses_a_slot_that_cannot_hold_the_source():
    from player.builder import content
    from player.builder.errors import ContentError

    seq = FakeNode('0x9', 'sequence', 'click', props={'sequence_file': ''})
    ctx = ctx_for(FakeGraph([seq]))
    # A recording property takes a file path; pasted text cannot become one.
    with pytest.raises(ContentError):
        content.ingest_text(ctx, '0x9', 'not a recording')


def test_ingest_requires_a_real_node():
    from player.builder import content
    from player.builder.errors import NodeTargetError

    with pytest.raises(NodeTargetError):
        content.ingest_text(ctx_for(FakeGraph([])), '0xdead', 'x')


def test_setting_a_recording_path_uses_the_node_setter(tmp_path):
    from player.builder import content

    rec = tmp_path / 'click.json'
    rec.write_text('{"actions": []}', encoding='utf-8')
    seq = FakeNode('0x9', 'sequence', 'click',
                   props={'sequence_file': ''})
    ctx = ctx_for(FakeGraph([seq]))
    content.ingest_file(ctx, '0x9', str(rec))
    assert seq.get_property('sequence_file') == os.path.abspath(str(rec))


# ------------------------------------------------------------- settings

def test_settings_defaults_and_corrupt_file(tmp_path, monkeypatch):
    from player.builder import settings
    from player.builder.errors import BuilderConfigError

    target = tmp_path / 'builder_settings.json'
    monkeypatch.setattr(settings, 'path', lambda: str(target))
    assert settings.load() == settings.DEFAULTS
    assert settings.save({'engine': 'ollama'})['engine'] == 'ollama'
    assert settings.load()['engine'] == 'ollama'
    target.write_text('{ not json', encoding='utf-8')
    with pytest.raises(BuilderConfigError):
        settings.load()


# ---------------------------------------------------------------- flow

def test_flow_metadata_normalises_and_fills_the_hue():
    from player.builder import flow

    normalised = flow.normalise({
        'summary': 'asks, then reports',
        'sections': [{'title': 'ask', 'note': 'the question',
                      'nodes': ['0x1', '0x2', '0x1']}],
        'steps': [{'node': '0x2', 'does': 'answers the question'}],
    })
    assert normalised['sections'][0]['nodes'] == ['0x1', '0x2']
    assert normalised['sections'][0]['hue'] == flow.HUES[0]
    assert flow.normalise(None) == flow.empty()
    assert flow.is_empty(flow.normalise({})) is True


def test_flow_metadata_rejects_what_it_cannot_draw():
    from player.builder import flow
    from player.builder.errors import FlowMetadataError

    with pytest.raises(FlowMetadataError):
        flow.normalise('not an object')
    with pytest.raises(FlowMetadataError):
        flow.normalise({'sections': [{'title': 'no nodes'}]})
    with pytest.raises(FlowMetadataError):
        flow.normalise({'sections': [{'title': 'bad hue', 'nodes': ['0x1'],
                                      'hue': 'cyan'}]})
    with pytest.raises(FlowMetadataError):
        flow.normalise({'steps': [{'does': 'no node id'}]})


def test_check_nodes_rejects_a_stale_id():
    from player.builder import flow, snapshot
    from player.builder.errors import FlowMetadataError

    snap = snapshot.build(chain())
    flow.check_nodes(snap, flow.normalise(
        {'sections': [{'title': 'ask', 'nodes': ['0x1']}]}))
    with pytest.raises(FlowMetadataError):
        flow.check_nodes(snap, flow.normalise(
            {'sections': [{'title': 'ask', 'nodes': ['0xdead']}]}))


def test_no_box_is_invented_for_the_nodes_a_section_leaves_out():
    """A box frames its members, so a guessed group rounds whatever sits in
    between them.  Only the stored sections become boxes."""
    from player.builder import flow, snapshot

    snap = snapshot.build(chain())
    boxes = flow.sections(snap, flow.normalise(
        {'sections': [{'title': 'middle', 'nodes': ['0x3']}]}))

    assert [box['nodes'] for box in boxes] == [['0x3']]


def test_a_phase_covers_a_whole_stretch_not_one_node():
    """Sections come from the TOPOLOGY: a phase is a stretch of execution.
    Splitting them by canvas distance left one box per node and no sections."""
    from player.builder import flow, snapshot

    graph = chain()
    # A wide layout is still one stretch: geometry does not cut a phase.
    graph.get_node_by_id('0x2')._pos = (6000.0, 6000.0)
    snap = snapshot.build(graph)

    boxes = flow.sections(snap)
    assert any({'0x1', '0x2', '0x3'} <= set(box['nodes']) for box in boxes), \
        boxes


def _connected_within(snap, ids):
    """True when the box's OWN wires reach every one of its members."""
    ids = set(ids)
    reached = {min(ids)} if ids else set()
    grown = True
    while grown:
        grown = False
        for edge in snap.edges:
            if edge.src_id in reached and edge.dst_id in ids \
                    and edge.dst_id not in reached:
                reached.add(edge.dst_id)
                grown = True
            elif edge.dst_id in reached and edge.src_id in ids \
                    and edge.src_id not in reached:
                reached.add(edge.src_id)
                grown = True
    return reached == ids


def test_boxes_fall_back_to_the_phases_of_the_walk():
    from player.builder import flow, snapshot

    snap = snapshot.build(chain())
    boxes = flow.sections(snap)
    assert [b['nodes'] for b in boxes] == [['0x1', '0x2', '0x3', '0x4', '0x5']]
    assert boxes[0]['title'].startswith('P1')
    stored = flow.normalise({'sections': [{'title': 'mine', 'nodes': ['0x2']}]})
    assert flow.sections(snap, stored)[0]['title'] == 'mine'


def test_no_box_collects_nodes_that_share_no_wire():
    """The walk hops between sibling branches; a box follows wires only."""
    from player.builder import flow, snapshot

    snap = snapshot.build(chain())
    for box in flow.sections(snap):
        assert _connected_within(snap, box['nodes']), box['nodes']


def test_an_outcome_of_a_check_never_stands_in_a_box_alone():
    """A leaf the check feeds directly hangs off that box, not beside it."""
    from player.builder import flow, snapshot

    snap = snapshot.build(chain())
    boxes = flow.sections(snap)

    assert [len(box['nodes']) for box in boxes] == [5], boxes


def test_an_orphan_node_keeps_a_box_of_its_own():
    from player.builder import flow, snapshot

    graph = chain()
    orphan = FakeNode('0x7', 'output', 'orphan', props={'label': 'orphan'})
    graph._nodes[orphan.id] = orphan
    snap = snapshot.build(graph)
    boxes = flow.sections(snap)

    assert ['0x7'] in [box['nodes'] for box in boxes], boxes
    for box in boxes:
        assert _connected_within(snap, box['nodes']), box['nodes']


def test_walkthrough_steps_follow_the_walk_and_name_the_wiring():
    from player.builder import flow, snapshot

    snap = snapshot.build(chain())
    steps = flow.steps(snap)
    assert [s['id'] for s in steps] == ['0x1', '0x2', '0x3', '0x4', '0x5']
    assert steps[0]['index'] == 1 and steps[0]['role'] == 'VALUE'
    llm = steps[1]
    assert any('reads 0x1.data' in line for line in llm['body'])
    assert any('hands execution to' in line for line in llm['body'])
    # the author's sentence wins over the runbook purpose
    authored = flow.normalise({'steps': [{'node': '0x2',
                                         'does': 'drafts the reply'}]})
    assert flow.steps(snap, authored)[1]['body'][0] == 'drafts the reply'


def test_flow_digest_is_prompt_text():
    from player.builder import flow

    text = flow.digest(flow.normalise({
        'summary': 'asks and reports',
        'sections': [{'title': 'ask', 'nodes': ['0x1'], 'note': 'one line'}],
        'steps': [{'node': '0x1', 'does': 'types the question'}],
    }))
    assert 'FLOW METADATA' in text
    assert 'asks and reports' in text and "'ask'" in text
    assert 'types the question' in text
    assert flow.digest({}) == ''


def test_a_flow_or_walk_a_model_still_emits_is_ignored():
    """Describing is the Describe button, the highlight is the walk button.
    A turn answers questions, so neither key changes anything."""
    from player.builder.assistant import envelope, extract_turn

    reply, actions, meta, walk = extract_turn(json.dumps({
        'reply': 'it asks', 'actions': [],
        'flow': {'summary': 'asks', 'sections': []}, 'walk': 'true'}))

    assert reply == 'it asks' and actions == []
    assert meta == {} and walk is False
    assert envelope(json.dumps({'reply': 'x'}))['walk'] is False


def test_stored_flow_is_read_from_the_chain_and_reaches_the_prompt(monkeypatch):
    from player.builder import assistant, tools

    ctx = ctx_for(chain())
    assert tools.stored_flow(ctx) == {}
    ctx['stored_flow'] = lambda: {'summary': 'asks then reports',
                                  'sections': [], 'steps': []}
    assert tools.stored_flow(ctx)['summary'] == 'asks then reports'

    seen = {}

    def fake_generate(prompt, system, settings, on_delta=None, cancel=None):
        seen['prompt'] = prompt
        return json.dumps({'reply': 'It asks and reports.', 'actions': []})

    monkeypatch.setattr(assistant._reasoning, 'generate', fake_generate)
    result = assistant.BuilderAssistant(
        ctx, settings={'engine': 'llamacpp', 'model': 'm'}).route(
        'what does this chain do?')
    assert 'asks then reports' in seen['prompt'], 'answered from its own words'
    assert result['reply'] == 'It asks and reports.'
    assert result['flow'] == {} and result['walk'] is False
