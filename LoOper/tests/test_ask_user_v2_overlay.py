"""Ask-user v2 through the agent overlay: options render, answers return.

The v2 callback posts the QUESTION into the chat ask channel and hands the
options to the surface as ``choices`` (rendered as buttons on the overlay and
on the web client), mirrors the ask to web clients, and packs the reply into
the v2 response dict (a reply naming an existing file becomes an attachment).

Run:  python -m pytest LoOper/tests/test_ask_user_v2_overlay.py
"""

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

from NGUI.widgets.agent_overlay import AgentOverlay  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def overlay(qapp):
    ov = AgentOverlay()
    ov._sent = []
    ov._web_send_json = lambda payload: ov._sent.append(payload)
    return ov


def _ask_async(ov, request):
    """Run the v2 ask callback on a worker thread; wait until it is live."""
    box = {}

    def _worker():
        try:
            box['result'] = ov._make_ask_user_v2_callback()(request)
        except Exception as e:  # pragma: no cover - failure reporting only
            box['error'] = repr(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    def _wait(cond):
        deadline = time.time() + 5
        while time.time() < deadline and not cond():
            time.sleep(0.02)
        assert cond(), 'ask never reached this step'

    _wait(lambda: ov._input_ask_active)
    _wait(lambda: any(p.get('type') == 'ask' for p in ov._sent))
    return t, box


def _last_ask(ov):
    return [p for p in ov._sent if p.get('type') == 'ask'][-1]


def test_yes_no_ask_renders_buttons_not_a_hint_and_desktop_answer_returns(overlay):
    t, box = _ask_async(overlay, {
        'question': 'Proceed?', 'kind': 'yes_no', 'choices': [],
        'accepts': {'text': True}})

    ask = _last_ask(overlay)
    # The bubble is the QUESTION only - Yes/No are rendered as buttons from
    # ``kind``, so the old '(Reply yes or no)' hint was duplicated text.
    assert 'Proceed?' in ask['question']
    assert 'yes or no' not in ask['question']
    assert ask['kind'] == 'yes_no'

    overlay._submit_text('yes')  # desktop answer path
    t.join(timeout=5)
    assert not t.is_alive()
    assert 'error' not in box, box.get('error')
    assert box['result'] == {'value': 'yes', 'kind': 'yes_no', 'attachments': []}


def test_legacy_yes_no_ask_still_offers_yes_no_buttons(overlay):
    """When a caller asks a yes/no question WITHOUT a v2 ``kind`` (legacy
    channel), the question text must not become the only tap target: the two
    answers are still offered as Yes/No buttons."""
    options = overlay._ask_option_list({'question': 'Proceed? (Reply yes or no.)'})
    assert options == [('Yes', 'yes'), ('No', 'no')]


def test_choice_ask_renders_options_not_numbered_text_and_web_answer_returns(overlay):
    t, box = _ask_async(overlay, {
        'question': 'Pick a color', 'kind': 'choice',
        'choices': [{'label': 'Red', 'value': 'r'},
                    {'label': 'Blue', 'value': 'b'}],
        'accepts': {'text': True}})

    ask = _last_ask(overlay)
    # The bubble is the QUESTION only; the choices ride as OPTIONS (the buttons
    # the overlay and the web client render), never as numbered text as well.
    assert ask['question'] == 'Pick a color'
    assert 'Red' not in ask['question'] and 'Blue' not in ask['question']
    assert [c['value'] for c in ask['choices']] == ['r', 'b']

    assert overlay._handle_web_message('b') == 'Answer received.'  # phone path
    t.join(timeout=5)
    assert not t.is_alive()
    assert 'error' not in box, box.get('error')
    assert box['result']['value'] == 'b'
    assert box['result']['kind'] == 'choice'


def test_reply_naming_a_file_becomes_an_attachment(overlay, tmp_path):
    pdf = tmp_path / 'notes.pdf'
    pdf.write_bytes(b'%PDF-1.4 not really')

    t, box = _ask_async(overlay, {
        'question': 'Send the notes file', 'kind': 'text', 'choices': [],
        'accepts': {'text': True, 'documents': True}})

    assert 'Attach a file' in _last_ask(overlay)['question']

    overlay._submit_text(str(pdf))
    t.join(timeout=5)
    assert not t.is_alive()
    assert 'error' not in box, box.get('error')
    result = box['result']
    assert result['value'] is None
    assert result['attachments'] == [
        {'kind': 'document', 'path': str(pdf), 'name': 'notes.pdf'}]
