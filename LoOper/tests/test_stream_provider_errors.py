"""A provider error must not look like an empty reply.

The gateway reports a BYOK failure mid-stream as {"error": ...}; the stream
reader used to swallow that line, so the coding agent saw a 0-char reply,
retried six times and ended with "the agent did not produce anything".
"""

import json

from AI.consult import OllamaClient
from player.code_agent_ops.chat_stream import _failure_kind


class _Resp:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=True):
        for line in self._lines:
            yield line

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    def __init__(self, lines):
        self._lines = lines

    def post(self, *a, **kw):
        return _Resp(self._lines)

    def mount(self, *a, **kw):
        pass


def _client(lines):
    c = OllamaClient(base_url='http://127.0.0.1:1')
    c.session = _Session(lines)
    return c


def test_error_line_raises_instead_of_silence():
    c = _client([json.dumps({'error': 'groq: invalid api key'})])
    try:
        list(c._post_stream_iter('/generate_stream', {}))
    except RuntimeError as e:
        assert 'invalid api key' in str(e)
    else:
        raise AssertionError('error line was swallowed')


def test_chat_stream_returns_the_provider_error():
    c = _client([json.dumps({'error': 'provider down'})])
    out = c.chat_stream(model='groq:allam-2-7b', prompt='hi')
    assert out.get('error') and 'provider down' in out['error'], out


def test_text_deltas_still_stream():
    c = _client([json.dumps({'text': 'a'}), json.dumps({'text': 'ab'})])
    out = c.chat_stream(model='m', prompt='hi')
    assert out.get('response') == 'ab', out


def test_empty_reply_is_its_own_failure_kind():
    assert _failure_kind('The model returned an empty reply (x)') == 'empty'
    assert _failure_kind('max_tokens reached') == 'trunc'
