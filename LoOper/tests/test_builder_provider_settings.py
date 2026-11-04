"""A provider choice has ONE source of truth: the model's prefix.

Field failure: the Builder settings said ``engine=deepseek`` with
``model=groq:allam-2-7b`` (a model picked from the unfiltered list), so the call
was routed to DeepSeek with a Groq model and came back empty — every cloud turn
failed.  The prefix routes, so it wins, in the settings file and in the request.

Run:  python -m pytest LoOper/tests/test_builder_provider_settings.py
"""

import json
import os

import pytest


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    from player.builder import settings as bsettings

    target = tmp_path / 'builder_settings.json'
    monkeypatch.setattr(bsettings, 'path', lambda: str(target))
    return target


def test_a_prefixed_model_decides_the_engine_on_load(settings_file):
    from player.builder import settings as bsettings

    settings_file.write_text(json.dumps({'engine': 'deepseek',
                                         'model': 'groq:allam-2-7b'}),
                             encoding='utf-8')

    loaded = bsettings.load()
    assert loaded['engine'] == 'groq', 'the prefix is the routing'
    assert loaded['model'] == 'groq:allam-2-7b'


def test_saving_writes_the_consistent_pair(settings_file):
    from player.builder import settings as bsettings

    bsettings.save({'engine': 'deepseek', 'model': 'groq:allam-2-7b'})
    stored = json.loads(settings_file.read_text(encoding='utf-8'))
    assert stored['engine'] == 'groq'

    # A bare model keeps the engine the user picked (nothing to override it).
    bsettings.save({'engine': 'deepseek', 'model': 'deepseek-chat'})
    stored = json.loads(settings_file.read_text(encoding='utf-8'))
    assert stored['engine'] == 'deepseek'


def test_the_request_sends_the_prefix_not_a_contradicting_engine():
    from player.builder import reasoning

    assert reasoning._provider(
        {'engine': 'deepseek'}, 'groq:allam-2-7b') == 'groq'
    # No prefix: the stored engine still routes (an Ollama tag, a cloud id).
    assert reasoning._provider({'engine': 'deepseek'}, 'deepseek-chat') == \
        'deepseek'
    # A GGUF file is the local engine whatever the stored engine says.
    assert reasoning._provider({'engine': 'deepseek'}, 'Qwen3.8-2B-Q8_0.gguf') \
        == 'llamacpp'


def test_a_provider_error_in_the_stream_is_raised_not_hidden(monkeypatch):
    """The gateway can still report an error mid-stream; the Builder must say
    the provider's words instead of "the model returned no text"."""
    import json as _json

    class _Resp:
        status_code = 200

        def iter_lines(self, decode_unicode=True):
            yield _json.dumps({"text": "",
                               "error": "Groq request failed: HTTP 400: Please "
                                        "reduce the length of the messages"})

        def close(self):
            pass

    from player.builder import reasoning
    from player.builder.errors import BuilderModelError

    monkeypatch.setattr('requests.post', lambda *a, **k: _Resp())
    with pytest.raises(BuilderModelError) as err:
        reasoning.generate("hi", "sys", {'engine': 'groq',
                                         'model': 'groq:allam-2-7b'})
    assert "reduce the length" in str(err.value)
