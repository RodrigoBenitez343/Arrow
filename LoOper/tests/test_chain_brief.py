"""The routing line is DERIVED from the chain, never typed by hand.

``player/chain_brief.py`` turns the Builder's flow metadata (summary / section
boxes / authored steps) plus the chain's own input/output nodes into one line.
These tests pin the contract:

* the summary leads, the section titles are the step flow, the ports say what
  it needs and returns;
* hand-written ``description`` prose never reaches the line;
* a chain with no flow metadata still gets an honest structural line;
* the line is bounded, so a verbose summary cannot swallow the prompt.

Run:  python -m pytest LoOper/tests/test_chain_brief.py
"""

from player import chain_brief

DESCRIBED = {
    'name': 'jobfinder',
    'description': 'OLD HAND WRITTEN TEXT that no longer matches anything',
    'flow': {
        'summary': 'Searches job boards and keeps the matching postings. '
                   'Runs on the user profile.',
        'sections': [{'title': 'search'}, {'title': 'collect'},
                     {'title': 'shortlist'}],
        'steps': [{'node': '0x1', 'does': 'queries'}],
    },
    'input_nodes': [{'label': 'search terms'},
                    {'label': 'location'},
                    {'label': 'search terms'}],
    'output_nodes': [{'label': 'shortlist'}, {'variable_name': 'count'}],
}

BARE = {
    'llm_nodes': [{'node_id': '0x1'}, {'node_id': '0x2'}],
    'conditional_nodes': [{'node_id': '0x3'}],
    'input_nodes': [{'label': 'query'}],
}


def test_the_brief_leads_with_the_summary_and_the_section_flow():
    line = chain_brief.brief(DESCRIBED)
    assert line.startswith('Searches job boards and keeps the matching '
                           'postings.')
    assert 'search → collect → shortlist' in line
    assert 'needs search terms, location' in line
    assert 'returns shortlist, count' in line


def test_hand_written_prose_never_reaches_the_line():
    line = chain_brief.brief(DESCRIBED)
    assert 'OLD HAND WRITTEN' not in line
    assert 'old hand written' not in line.lower()


def test_only_the_first_sentence_of_a_long_summary_is_used():
    line = chain_brief.brief(DESCRIBED)
    assert 'Runs on the user profile' not in line
    assert line.count('.') == 1


def test_a_chain_without_flow_gets_a_structural_line():
    line = chain_brief.brief(BARE)
    assert line.startswith('4 node(s): 1 input, 2 llm, 1 conditional'), line
    assert 'needs query' in line


def test_an_empty_object_has_no_brief():
    assert chain_brief.brief({}) == ''
    assert chain_brief.brief(None) == ''


def test_the_line_is_bounded():
    chatty = {
        'flow': {'summary': 'word ' * 400},
        'input_nodes': [{'label': 'x' * 200}],
    }
    line = chain_brief.brief(chatty)
    assert len(line) <= chain_brief.BRIEF_LIMIT
    assert line.endswith('…')


def test_a_step_only_flow_reports_its_step_count():
    line = chain_brief.brief({'flow': {'steps': [{'node': '0x1'},
                                                 {'node': '0x2'}]}})
    assert line == '2 step(s)'


def test_from_file_reads_a_chain(tmp_path):
    path = tmp_path / 'described.json'
    import json

    path.write_text(json.dumps(DESCRIBED), encoding='utf-8')
    assert chain_brief.from_file(str(path)).startswith('Searches job boards')
