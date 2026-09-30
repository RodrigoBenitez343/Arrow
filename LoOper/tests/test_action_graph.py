"""Action-graph substrate — renderer, edges, import collapse, cache.

Run:  python -m pytest LoOper/tests/test_action_graph.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.multi_sequence.worflow_interpreter_modules.executor_modules import (  # noqa: E402
    action_graph as ag,
)


def _write(path, cfg):
    path.write_text(json.dumps(cfg), encoding='utf-8')
    return str(path)


def test_web_sequence_actions_render_literal_targets(tmp_path):
    """The richest material renders verbatim: action type + the locator's own
    text — what the executor replays, never a description."""
    seq = _write(tmp_path / 'apply.json', {
        'schema_version': 1, 'mode': 'web',
        'actions': [
            {'type': 'click', 'locator': {'tag': 'a', 'text': 'Easy Apply'}},
            {'type': 'navigate'},
        ],
    })
    chain = _write(tmp_path / 'chain.json', {
        'web_sequences': [{'node_id': 'w1', 'name': 'apply.json',
                           'session_file': seq, 'connections': []}],
    })

    graph = ag.build_action_graph(chain)

    assert graph['coverage'] == {'nodes': 1, 'rendered': 1,
                                 'skipped_by_kind': {}}
    ent = graph['entities'][0]
    assert ent['kind'] == 'web_sequence'
    assert ent['line'] == 'click "Easy Apply"; navigate'
    assert ent['modality'] == 'web'
    assert 'apply' in ent['objects']


def test_graph_renders_node_kinds_edges_and_collapsed_imports(tmp_path):
    """One entity per executable node (label-less ones skipped and counted);
    edges follow output connections; imports collapse one level."""
    child = _write(tmp_path / 'child.json', {
        'handle_nodes': [{'node_id': 'h1', 'action_type': 'click',
                          'goal_description': 'the login button'}],
    })
    chain = _write(tmp_path / 'chain.json', {
        'input_nodes': [{
            'node_id': 'i1', 'label': 'the search query',
            'connections': [{'output_port': 'output',
                             'target_node_id': 'l1', 'input_port': 'input'}],
        }],
        'llm_nodes': [{
            'node_id': 'l1', 'label': '', 'input_source': 'ocr',
            'connections': [{'output_port': 'output',
                             'target_node_id': 'c1', 'input_port': 'input'}],
        }],
        'conditional_nodes': [{
            'node_id': 'c1', 'condition_type': 'ocr',
            'connections': [{'output_port': 'output', 'target_node_id': 'k1',
                             'input_port': 'input'}],
        }],
        'code_nodes': [{'node_id': 'k1'}],          # no label -> skipped
        'chain_import_nodes': [{
            'node_id': 'ci1', 'prefix': 'child_tools',
            'chain_file_path': child, 'connections': [],
        }],
    })

    graph = ag.build_action_graph(chain)

    assert [e['kind'] for e in graph['entities']] == [
        'input', 'llm', 'conditional']
    assert graph['coverage']['rendered'] == 3
    assert graph['coverage']['skipped_by_kind'] == {'code': 1}
    assert graph['edges'] == [['i1', 'l1'], ['l1', 'c1']]
    assert graph['order'] == ['i1', 'l1', 'c1']
    assert graph['imports'][0]['name'] == 'child_tools'
    assert graph['imports'][0]['child_lines'] == ['click "the login button"']


def test_import_cycles_and_self_imports_are_guarded(tmp_path):
    """Collapse is ONE level, and a cycle (or self-import) yields no child
    lines instead of recursing."""
    a = tmp_path / 'a.json'
    b = tmp_path / 'b.json'
    _write(a, {'chain_import_nodes': [
        {'node_id': 'ia', 'prefix': 'b_chain',
         'chain_file_path': str(b), 'connections': []},
        {'node_id': 'ia2', 'prefix': 'self_chain',
         'chain_file_path': str(a), 'connections': []},
    ]})
    _write(b, {'chain_import_nodes': [
        {'node_id': 'ib', 'prefix': 'a_chain',
         'chain_file_path': str(a), 'connections': []},
    ]})

    graph = ag.build_action_graph(str(a))

    assert [im['name'] for im in graph['imports']] == ['b_chain', 'self_chain']
    assert graph['imports'][0]['child_lines'] == []
    assert graph['imports'][1]['child_lines'] == []


def test_content_hash_cache_rebuilds_only_on_change(tmp_path):
    path = tmp_path / 'chain.json'
    _write(path, {'input_nodes': [{'node_id': 'i1', 'label': 'query'}]})

    g1 = ag.build_action_graph(str(path))
    g2 = ag.build_action_graph(str(path))
    assert g1 is g2, 'an unchanged file is served from the content-hash cache'

    _write(path, {'input_nodes': [{'node_id': 'i1', 'label': 'other'}]})
    g3 = ag.build_action_graph(str(path))

    assert g3 is not g1
    assert g3['content_hash'] != g1['content_hash']
    assert g3['entities'][0]['line'] == 'needs "other"'


def test_web_ref_never_resolves_to_a_chain_of_the_same_name(tmp_path):
    """A bare web ref searches web_sequences/ FIRST and only a mode=web
    file resolves: a CHAIN sharing the sequence's name must never win.
    (Measured 2026-09-25: 'linkedin.json' resolved to the chain and every
    child line degraded to the bare fallback.)"""
    chains = tmp_path / 'chains'
    webseq = chains / 'web_sequences'
    webseq.mkdir(parents=True)
    # A chain with the SAME name as the web sequence (no web marker).
    _write(chains / 'zzz_unique.json',
           {'input_nodes': [{'node_id': 'i1', 'label': 'x'}]})
    # The real web sequence next to it.
    _write(webseq / 'zzz_unique.json', {
        'schema_version': 1, 'mode': 'web',
        'actions': [{'type': 'click', 'locator': {'text': 'Home'}}]})
    chain = _write(chains / 'user.json', {
        'web_sequences': [{'node_id': 'w1', 'name': 'zzz_unique.json',
                           'session_file': 'zzz_unique.json',
                           'connections': []}],
    })

    graph = ag.build_action_graph(chain)
    assert graph['entities'][0]['line'] == 'click "Home"'

    # The asset-folder copy loses its web marker: nothing resolves now (the
    # chain-dir twin is rejected too) and the fallback line stands.
    _write(webseq / 'zzz_unique.json', {
        'schema_version': 1, 'mode': 'desktop',
        'actions': [{'type': 'click', 'locator': {'text': 'Home'}}]})
    ag._CACHE.clear()
    graph = ag.build_action_graph(chain)
    assert graph['entities'][0]['line'] == 'run web sequence "zzz_unique"'


def test_summary_lines_report_coverage_and_skips(tmp_path):
    chain = _write(tmp_path / 'chain.json', {
        'input_nodes': [{'node_id': 'i1', 'label': 'query'}],
        'code_nodes': [{'node_id': 'k1'}],
    })

    graph = ag.build_action_graph(chain)
    lines = ag.graph_summary_lines(graph)

    assert lines[0].startswith('chain.json: 1/2 node(s) rendered')
    assert any('skipped: code x1' in l for l in lines)
    assert any('needs "query"' in l for l in lines)
    assert ag.graph_summary_lines(None) == []


def test_candidate_lines_include_import_children(tmp_path):
    """The projection matches against own lines AND one-level import
    children — a nested worker with no own rendered nodes still exposes
    its leaf actions."""
    child = _write(tmp_path / 'child.json', {
        'handle_nodes': [{'node_id': 'h1', 'action_type': 'click',
                          'goal_description': 'the login button'}],
    })
    chain = _write(tmp_path / 'chain.json', {
        'input_nodes': [{'node_id': 'i1', 'label': 'the query'}],
        'chain_import_nodes': [{'node_id': 'ci1', 'prefix': 'tools',
                                'chain_file_path': child, 'connections': []}],
    })

    graph = ag.build_action_graph(chain)
    lines = ag.graph_candidate_lines(graph)

    assert 'needs "the query"' in lines
    assert 'tools: click "the login button"' in lines
    assert ag.graph_candidate_lines(None) == []
