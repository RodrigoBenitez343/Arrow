"""Every node type says what it DOES, from its own settings.

Field report: the boxes and the digest were full of scaffolding — "replays the
web recording … 1 action(s), kinds: click x1", "holds the memory pool and
serves it through its data ports" — and never said what a check evaluates, what
a recording clicks, what a pool contains or what an output prints.  A reader
(and the model that writes the boxes) got the structure and none of the content.

This file pins the CONTENT, one assertion per type.  It reads like the sentence
a user should see.

Run:  python -m pytest LoOper/tests/test_builder_node_content.py
"""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


def say(node_type, **props):
    from player.builder import describe

    node = SimpleNamespace(type=node_type, name='n', properties=props)
    return describe.acting(node)


# ------------------------------------------------------------------ 1. records

def _recording(tmp_path, actions, start_url=''):
    folder = tmp_path / 'web_sequences'
    folder.mkdir(exist_ok=True)
    path = folder / 'session.json'
    payload = {'mode': 'web', 'metadata': {'name': 'my session',
                                           'start_url': start_url},
               'actions': actions}
    path.write_text(json.dumps(payload), encoding='utf-8')
    return str(path)


def test_a_web_recording_says_what_it_clicks_not_how_many_actions(tmp_path):
    from player.builder import artifacts

    locator = {'tag': 'a', 'text': 'Easy Apply',
               'aria_label': 'Easy Apply to this job'}
    path = _recording(tmp_path, [
        {'type': 'scroll', 'locator': {'text': 'CVEdit'}},
        {'type': 'scroll', 'locator': {'text': 'CVEdit'}},
        {'type': 'click', 'locator': locator}],
        start_url='https://www.linkedin.com/jobs/1?x=2&y=3')

    text = artifacts.describe('web_sequence', {'session_file': path})['text']

    assert 'on www.linkedin.com' in text, text
    assert 'scrolls the page ×2' in text, text
    assert 'clicks "Easy Apply"' in text, text
    assert 'action(s)' not in text and 'kinds:' not in text, text
    assert 'y=3' not in text, 'the query string is not content'
    # Verbs lead: the line used to open with 'replays the web recording
    # "<file>"', and readers (and the describe loop) talked about the file.
    assert text.index('scrolls') < text.index('www.linkedin.com'), text


def test_a_recording_named_after_its_file_leads_with_its_action(tmp_path):
    """The recorder stores the FILE NAME as the name ('next.json_copy' after a
    copy).  That stale identity is traceability, not behaviour: it must not
    lead the sentence, and a name that says nothing over the filename adds
    nothing at all."""
    from player.builder import artifacts

    folder = tmp_path / 'web_sequences'
    folder.mkdir(exist_ok=True)
    path = folder / 'next.json'
    path.write_text(json.dumps({
        'mode': 'web',
        'metadata': {'name': 'next.json_copy',
                     'start_url': 'https://www.linkedin.com/jobs/view/1'},
        'actions': [{'type': 'click', 'locator': {'text': 'Next'},
                     'url': 'https://www.linkedin.com/jobs/view/1'}]}),
        encoding='utf-8')

    text = artifacts.describe('web_sequence', {'session_file': str(path)})['text']

    assert text == 'clicks "Next"', text


def test_a_desktop_recording_names_the_window_and_the_key_pressed(tmp_path):
    from player.builder import artifacts

    folder = tmp_path / 'sequences'
    folder.mkdir(exist_ok=True)
    path = folder / 'desk.json'
    path.write_text(json.dumps({
        'actions': [
            {'type': 'click', 'coordinates': "{'x': 359, 'y': 880}",
             'element': "{'label': 'File Explorer', 'rect': [1, 2, 3, 4]}",
             'app_context': "{'title': 'Documents', 'process_name': 'explorer'}"},
            {'type': 'type_string', 'text': 'notepad',
             'app_context': "{'title': 'Search', 'process_name': 'Search.exe'}"},
            {'type': 'keystroke', 'key': 'enter',
             'app_context': "{'title': 'Search', 'process_name': 'Search.exe'}"},
        ]}), encoding='utf-8')

    text = artifacts.describe('sequence', {'sequence_file': str(path)})['text']

    assert 'clicks "File Explorer"' in text, text
    assert 'types "notepad"' in text, text
    assert 'presses enter' in text, text
    assert 'in "Documents", "Search"' in text, text


# ---------------------------------------------------------------- 2. decisions

def test_a_code_check_shows_the_expression_it_evaluates():
    text = say('conditional', condition_type='code',
               code='# decide\nresult = len(rows) > 0')
    assert 'runs a code test "result = len(rows) > 0"' in text, text
    assert '# decide' not in text, 'a comment is not the criterion'


def test_an_llm_check_shows_the_question_it_asks():
    text = say('conditional', condition_type='llm',
               llm_prompt='Does the page say the application was sent?')
    assert 'asks the model' in text, text
    assert 'application was sent' in text, text


def test_a_web_check_says_which_element_it_looks_for():
    locator = json.dumps({'css': 'div#root > button', 'text': 'Review',
                          'tag': 'button'})
    text = say('conditional', condition_type='web',
               web_condition_type='element_located',
               web_element_locator=locator)
    assert 'checks that the element "Review" is on the page' in text, text
    assert 'div#root' not in text, text


def test_web_condition_aliases_say_the_page_check_not_the_token():
    """The engine (web_conditions.evaluate) dispatches on alias names per
    variant (visual_trigger, text/loop, js, ai, layout).  Each must describe
    the page condition it checks, never leak the raw type token -- and a web
    check reads the live page, so no sentence may mention an image."""
    loc = json.dumps({'text': 'Review'})
    cases = {
        'visual_trigger': ('element', 'checks that the element "Review"'),
        'text': ('text_present', 'checks that the page shows the text'),
        'loop': ('text_present', 'checks that the page shows the text'),
        'js': ('browser_js', 'runs a page script'),
        'ai': ('llm', 'asks the model'),
        'layout': ('layout_match', 'scrolls the page until the element'),
    }
    for alias, (ctype, expect) in cases.items():
        props = dict(condition_type='web', web_condition_type=alias,
                     web_element_locator=loc, web_layout_locator=loc,
                     web_target_text='application was sent', web_js='return 1')
        text = say('conditional', **props)
        assert expect in text, '%s => %s' % (alias, text)
        assert 'image' not in text.lower(), 'web checks never use images'
        assert '"%s"' % alias not in text, 'leaked the raw type token'


# ------------------------------------------------------------- 3. the content

def test_a_memory_pool_says_what_it_holds():
    documents = json.dumps(['C:/Users/x/Documents/resumes/Rodrigo_Benitez.md'])
    text = say('context', label='user information', documents=documents,
               skills=json.dumps(['Python', 'Docker']))
    assert 'Rodrigo_Benitez.md' in text, text
    assert 'Python, Docker' in text, text
    assert 'serves it through its data ports' not in text, text


def test_an_empty_pool_says_it_is_empty():
    text = say('context', label='user information', documents='[]',
               skills='[]')
    assert 'nothing is attached' in text, text


def test_an_output_prints_its_own_text():
    assert say('output', label='final reply') == 'prints "final reply"'
    assert say('output', tts_text='filling the form') == \
        'prints "filling the form"'


def test_a_form_filler_says_which_fields_and_that_it_verifies():
    text = say('form_filler', instruction='Fill the form from the context.',
               fields_include='first_name,email', fields_skip='phone',
               verify='true', model='qwen:2b')
    assert 'first_name,email' in text and 'phone' in text, text
    assert 'verifies what it typed' in text, text


def test_an_mcp_node_names_the_tool_the_server_and_the_arguments():
    text = say('mcp', tool_name='browser_navigate',
               mcp_folder='C:/mcp/playwright',
               tool_args='{"url": "https://x.test"}', keep_alive=True)
    assert 'browser_navigate' in text and 'playwright' in text, text
    assert 'https://x.test' in text and 'kept alive' in text, text


def test_an_isolated_agent_node_names_the_agent_and_its_limits():
    text = say('container', iso_path='C:/agents/scraper.agent',
               memory_mb='2048', cpu_cores='2', output_variable='result')
    assert 'scraper.agent' in text, text
    assert '2048 MB of memory' in text and '2 core(s)' in text, text
    assert 'no agent file is attached' in say('container')


def test_a_single_action_node_says_what_it_does():
    assert say('action', action_type='click', details='(359, 880)') == \
        'performs the click "(359, 880)"'
    assert say('action', action_type='key', details='enter') == \
        'performs the key "enter"'


def test_a_vision_node_says_what_it_acts_on():
    assert 'the blue Apply button' in say('handle', action_type='click',
                                          goal_description='the blue Apply button')
    text = say('handle', action_type='drag', goal_description='the file icon',
               target_description='the desktop folder')
    assert 'drags "the file icon" onto "the desktop folder"' in text, text


def test_an_llm_node_says_the_model_the_instruction_and_the_variable():
    text = say('llm', model='qwen3.8:2b', prompt='Write a short polite reply',
               output_variable='support_response')
    assert 'qwen3.8:2b' in text and 'Write a short polite reply' in text, text
    assert 'support_response' in text, text


def test_an_input_says_what_it_asks_and_what_the_choices_are():
    text = say('input', label='topic', user_prompt='What is it about?',
               choices='["a", "b"]')
    assert 'asks the user "What is it about?"' in text, text
    assert '["a", "b"]' in text, text


def test_a_code_node_says_what_it_hands_on():
    text = say('code', code='rows = [r for r in input_data]',
               output_vars='rows')
    assert 'runs Python: rows = [r for r in input_data]' in text, text
    assert 'hands on rows' in text, text


@pytest.mark.parametrize('node_type', [
    'input', 'output', 'llm', 'code', 'conditional', 'sequence',
    'web_sequence', 'chain_import', 'form_filler', 'handle', 'context',
    'action', 'mcp', 'container'])
def test_no_type_falls_back_to_its_bare_purpose(node_type):
    """A type with no settings must still say something about ITSELF, and a
    type WITH settings must not answer with the generic purpose line."""
    from player.builder import describe, runbook

    generic = runbook.purpose_line(node_type) or ''
    text = describe.acting(SimpleNamespace(type=node_type, name='n',
                                           properties={}))
    assert text, node_type
    assert text != generic or not generic, (node_type, text)
