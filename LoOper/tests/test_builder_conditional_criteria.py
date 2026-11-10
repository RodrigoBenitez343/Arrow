"""A check's description says WHAT it evaluates, never just that it evaluates.

Field report: the Builder boxes said things like "evaluates the condition and
returns true or false" — the mechanism, never the target.  The execution was
right; the description was blind.

Two defects made it blind:

* on the LIVE canvas the snapshot only read the properties ``NODE_FIELDS``
  declared for ``conditional``, and every payload field (the locator, the target
  text, the script, the image, the prompt) was missing from that list — so a
  web text check described itself as "the text it expects";
* the criterion itself was read carelessly: a code check quoted its first setup
  line instead of the ``result = ...`` statement, a model check quoted the
  classifier boilerplate ("You are a strict routing classifier…") and clipped
  the real condition away, a page script quoted its ``//`` comment, and the
  desktop presence/absence/OCR spellings the engine maps onto its evaluators
  fell through to ``branches on the condition "absence_trigger"``.

Every assertion below reads like the sentence a user should see.

Run:  python -m pytest LoOper/tests/test_builder_conditional_criteria.py
"""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


def say(**props):
    """The sentence for a check configured with *props* (no canvas needed)."""
    from player.builder import describe

    return describe.acting(SimpleNamespace(type='conditional', name='check',
                                           properties=props))


# --------------------------------------------------- the live canvas (the bug)

@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _live(qapp, **props):
    """``(sentence, snapshot properties)`` for one LIVE conditional node."""
    from NodeGraphQt import NodeGraph

    from NGUI.nodes import ConditionalNode
    from player.builder import describe, snapshot

    graph = NodeGraph()
    graph.register_node(ConditionalNode)
    node = graph.create_node('conditional.ConditionalNode', name='check',
                             pos=[0, 0])
    for name, value in props.items():
        node.set_property(name, value)
    snap = snapshot.build(SimpleNamespace(get_all_nodes=graph.all_nodes))
    view = snap.nodes[node.id]
    return describe.does_of(snap, view), view.properties


def test_a_live_web_check_reads_the_text_it_looks_for(qapp):
    text, props = _live(qapp, condition_type='web', web_mode='true',
                        web_condition_type='text_present',
                        web_target_text='Application sent')
    assert props.get('web_target_text') == 'Application sent', \
        'the criterion must survive the snapshot'
    assert 'shows the text "Application sent"' in text, text


def test_a_live_web_check_reads_the_element_it_waits_for(qapp):
    locator = json.dumps({'css': 'div#root > a', 'text': 'Easy Apply'})
    text, _props = _live(qapp, condition_type='web', web_mode='true',
                         web_condition_type='element_located',
                         web_element_locator=locator)
    assert 'the element "Easy Apply"' in text, text
    assert 'div#root' not in text, 'the locator is not the description'


def test_a_live_web_check_quotes_the_script_that_decides(qapp):
    text, _props = _live(qapp, condition_type='web', web_mode='true',
                         web_condition_type='browser_js',
                         web_js="// truthy once the feed is open\n"
                               "return !!document.querySelector('.feed');")
    assert 'runs a page script "return !!document' in text, text
    assert 'truthy once the feed is open' not in text, 'a comment is not the test'


def test_a_live_desktop_check_names_the_image_it_looks_for(qapp):
    text, _props = _live(qapp, condition_type='absence_trigger',
                         image_path=r'C:\pics\loading.png', confidence='0.9')
    assert 'the image "loading.png" is absent from the screen' in text, text


def test_web_mode_wins_over_a_desktop_type_token(qapp):
    """The engine decides web mode from ``web_mode`` first; the description must
    agree, or a page check reads as a screen look-up for a picture."""
    text, _props = _live(qapp, condition_type='presence', web_mode='true',
                         web_condition_type='text_present',
                         web_target_text='Applied',
                         image_path=r'C:\pics\x.png')
    assert 'shows the text "Applied"' in text, text
    assert 'image' not in text.lower(), text


# ------------------------------------------------------- the criterion itself

def test_a_code_check_quotes_the_statement_that_sets_result():
    text = say(condition_type='code',
               code='# step 1\n'
                    'data = str(input_data).lower()\n'
                    "ok = 'price' in data\n"
                    'result = ok')
    assert 'runs a code test "result = ok"' in text, text
    assert 'data = str(input_data)' not in text, \
        'the preparation is not the criterion'


def test_a_model_check_quotes_the_condition_not_the_classifier_template():
    text = say(condition_type='llm',
               llm_prompt='You are a strict routing classifier.\n'
                          'Respond with EXACTLY one word: "True" or "False".\n'
                          'No other text, explanation, punctuation.\n'
                          '\n'
                          'Condition to evaluate: the blue LinkedIn logo is '
                          'visible on the screen.')
    assert 'blue LinkedIn logo is visible' in text, text
    for noise in ('strict routing classifier', 'EXACTLY one word',
                  'No other text'):
        assert noise not in text, (noise, text)


def test_an_untouched_template_says_no_criterion_is_written():
    text = say(condition_type='llm',
               llm_prompt='Respond with EXACTLY one word: "True" or "False".\n'
                          'Condition to evaluate: [user describes the condition '
                          'here]')
    assert 'no criterion is written' in text, text
    assert 'describes the condition here' not in text, text


def test_a_page_script_check_skips_the_comments():
    text = say(condition_type='web', web_condition_type='browser_js',
               web_js='// return a truthy value to follow TRUE\n'
                      '// e.g. return 1\n'
                      'return document.title.length > 0;')
    assert 'runs a page script "return document.title.length > 0;"' in text, text
    assert 'return a truthy value' not in text, text


@pytest.mark.parametrize('kind, props, expect', [
    ('presence', {'image_path': r'C:\pics\logo.png'},
     'checks that the image "logo.png" is on the screen'),
    ('presence_trigger', {'image_path': r'C:\pics\logo.png'},
     'checks that the image "logo.png" is on the screen'),
    ('wait_presence', {'image_path': r'C:\pics\logo.png'},
     'checks that the image "logo.png" is on the screen'),
    ('absence', {'image_path': r'C:\pics\done.png'},
     'the image "done.png" is absent from the screen'),
    ('absence_trigger', {'image_path': r'C:\pics\done.png'},
     'the image "done.png" is absent from the screen'),
    ('wait_absence', {'image_path': r'C:\pics\done.png'},
     'the image "done.png" is absent from the screen'),
    ('ocr', {'ocr_text': 'shows up on screen'},
     'the text "shows up on screen" is there'),
    ('ocr_trigger', {'target_text': 'shows up on screen'},
     'the text "shows up on screen" is there'),
])
def test_every_desktop_spelling_names_its_target(kind, props, expect):
    text = say(condition_type=kind, **props)
    assert expect in text, '%s => %s' % (kind, text)
    assert '"%s"' % kind not in text, 'leaked the raw type token'


def test_a_check_with_nothing_set_says_the_settings_are_empty():
    text = say(condition_type='mystery')
    assert 'settings are empty' in text, text
    assert 'pick what it should check' in text, \
        'an empty check must ask for its criterion, not pretend to have one'


# ------------------------------------------- stored sentences heal themselves

#: A chain file with a web check, described by the OLD describer.
_STALE_CHAIN = {
    'conditional_nodes': [
        {'type': 'conditional', 'node_id': '0x1', 'name': 'apply?',
         'condition_type': 'web', 'web_mode': True,
         'web_condition_type': 'element_located',
         'web_element_locator': json.dumps({'text': 'Easy Apply'}),
         'position': [0, 0], 'connections': []}],
    'output_nodes': [
        {'type': 'output', 'node_id': '0x2', 'label': 'reply',
         'position': [0, 400], 'connections': [],
         'description': 'prints "reply"'}],
    'flow': {
        'summary': 'Applies and reports.',
        'nodes': [
            {'node': '0x1',
             'does': 'checks that the element it was picked for this check is '
                     'on the page and sends execution down true or false',
             'stamp': '0123456789abcdef'},
            {'node': '0x2', 'does': 'prints "reply"',
             'stamp': '0123456789abcdef'}],
        'stamp': '0123456789abcdef'},
    'description': 'Applies and reports.',
}


def test_a_sentence_from_an_older_describer_is_rewritten(tmp_path):
    """A better describer must reach chains that were already described.

    Without a describer version a stored sentence stood forever: the node was
    unchanged, so the wording was kept and every check kept claiming "the
    element it was picked for this check".
    """
    from player import chain_refresh
    from player.builder import flow

    path = tmp_path / 'stale.json'
    path.write_text(json.dumps(_STALE_CHAIN, indent=2), encoding='utf-8')
    assert flow.is_stale(_STALE_CHAIN) is True

    assert chain_refresh.refresh_file(str(path)) == chain_refresh.UPDATED
    data = json.loads(path.read_text(encoding='utf-8'))
    node_line = {entry['node']: entry['does']
                 for entry in data['flow']['nodes']}['0x1']
    assert 'the element "Easy Apply" is on the page' in node_line, node_line
    assert 'picked for this check' not in node_line, node_line
    # The canvas prints the NODE's own field first, so it must be refreshed too.
    assert data['conditional_nodes'][0]['description'] == node_line
    assert data['flow']['stamp'] == flow.stamp_of(data)


def test_a_current_description_is_left_byte_for_byte(tmp_path):
    """The version must not turn every sweep into a rewrite."""
    from player import chain_refresh
    from player.builder import flow

    current = flow.complete_static(json.loads(json.dumps(_STALE_CHAIN)))
    chain = dict(_STALE_CHAIN, flow=current)
    chain['conditional_nodes'][0]['description'] = 'x'   # forces a rewrite once
    path = tmp_path / 'current.json'
    path.write_text(json.dumps(chain, indent=2), encoding='utf-8')
    chain_refresh.refresh_file(str(path))
    settled = path.read_text(encoding='utf-8')

    assert chain_refresh.refresh_file(str(path)) == chain_refresh.CURRENT
    assert path.read_text(encoding='utf-8') == settled

