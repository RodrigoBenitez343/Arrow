"""Builder settings + the reasoning LLM turn.

Run:  python -m pytest LoOper/tests/test_builder_settings.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder import reasoning  # noqa: E402
from player.builder import settings as bs  # noqa: E402


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, 'path', lambda: str(tmp_path / 'b.json'))
    assert bs.load() == bs.DEFAULTS
    saved = bs.save({'engine': 'ollama', 'model': 'llama3',
                     'reasoning': False, 'max_steps': 3})
    assert saved['engine'] == 'ollama' and saved['max_steps'] == 3
    assert bs.load() == {'engine': 'ollama', 'model': 'llama3',
                         'reasoning': False, 'max_steps': 3}


def test_settings_load_ignores_a_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / 'b.json'
    path.write_text('{not json', encoding='utf-8')
    monkeypatch.setattr(bs, 'path', lambda: str(path))
    assert bs.load() == bs.DEFAULTS


def test_reasoning_off_returns_none(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    assert reasoning.think('g', 'c', [],
                           {'reasoning': False, 'model': 'm'}) is None


def test_reasoning_fails_open_without_a_model(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    monkeypatch.setattr(reasoning, '_default_model', lambda engine: '')
    monkeypatch.setattr(reasoning, 'list_models', lambda: [])
    assert reasoning.think('g', 'c', [], {'reasoning': True, 'model': ''}) is None


def test_reasoning_parses_the_response(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')

    class _Resp:
        status_code = 200

        def json(self):
            return {'response': 'create the input node\nsecond line'}

    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **k: _Resp())
    out = reasoning.think('g', 'c', [], {'reasoning': True, 'model': 'm'})
    assert out == 'create the input node'      # first line only


def test_reasoning_fails_open_when_the_call_raises(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')

    import requests

    def _boom(*_a, **_k):
        raise RuntimeError('gateway down')

    monkeypatch.setattr(requests, 'post', _boom)
    assert reasoning.think('g', 'c', [], {'reasoning': True, 'model': 'm'}) is None


def test_default_model_follows_the_byok_chat_role(tmp_path, monkeypatch):
    """LOCAL PRIORITY first: with a GGUF configured the local path wins for
    the llamacpp engine; the chat role fills only when no GGUF exists."""
    from AI import byok
    from player.multi_sequence.llm_executor_resources import config_utils

    monkeypatch.setattr(byok, 'settings_path',
                        lambda: str(tmp_path / 'byok.json'))
    byok.save_settings({
        'roles': {'chat': {'provider': 'openai', 'model': 'gpt-4o-mini'}}})

    import AI.config_loader as _cl

    # A configured GGUF beats the chat role (the build artifact must stay
    # local — measured 2026-10-09: a composer-built brain ran deepseek).
    monkeypatch.setattr(_cl, 'get_llamacpp_config',
                        lambda: {'model_path': r'D:\m\local.gguf'})
    assert config_utils.get_default_llm_model('llamacpp') == \
        r'D:\m\local.gguf'

    # No GGUF → the role fills the gap.
    monkeypatch.setattr(_cl, 'get_llamacpp_config',
                        lambda: {'model_path': ''})
    assert config_utils.get_default_llm_model('llamacpp') == \
        'openai:gpt-4o-mini'

    byok.save_settings({'roles': {'chat': {'provider': 'llamacpp'}}})
    # llamacpp role, no GGUF → nothing to serve.
    assert config_utils.get_default_llm_model('llamacpp') == ""


def test_reasoning_sends_the_role_provider(monkeypatch):
    """When the panel carries no model, the request tells the gateway which
    provider the chat role elected."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    monkeypatch.setattr(reasoning, '_default_model', lambda engine: 'gpt-4o')
    monkeypatch.setattr(reasoning, '_role_provider', lambda: 'openai')
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {'response': 'ok'}

    import requests

    def _post(url, json=None, timeout=None, **kw):
        captured.update(json)
        return _Resp()

    monkeypatch.setattr(requests, 'post', _post)
    reasoning.think('g', 'c', [], {'reasoning': True, 'model': ''})
    assert captured['provider'] == 'openai'
    assert captured['model'] == 'gpt-4o'


def test_step_prompt_authors_the_llm_tool_instruction(monkeypatch):
    """The `llm` teach route's prompt comes from ONE generation turn and is
    collapsed to a single line (the chain file carries it verbatim)."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {'response': 'Read the data.\n  Extract titles.\nReturn JSON.'}

    import requests

    def _post(url, json=None, timeout=None, **kw):
        captured.update(json)
        return _Resp()

    monkeypatch.setattr(requests, 'post', _post)
    out = reasoning.step_prompt('extract titles', 'scrape google',
                                {'reasoning': True, 'model': 'm'})
    assert out == 'Read the data. Extract titles. Return JSON.'
    # The tool line and the goal both ride in the authoring prompt.
    assert 'extract titles' in captured['prompt']
    assert 'scrape google' in captured['prompt']


def test_step_prompt_fails_open(monkeypatch):
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    import requests

    def _boom(*_a, **_k):
        raise RuntimeError('gateway down')

    monkeypatch.setattr(requests, 'post', _boom)
    assert reasoning.step_prompt('t', 'g',
                                 {'reasoning': True, 'model': 'm'}) == ''
    # Reasoning disabled → no call at all.
    assert reasoning.step_prompt('t', 'g', {'reasoning': False}) == ''


def test_plan_prompt_carries_the_node_runbook(monkeypatch):
    """The builder's model knows the real building blocks: the plan system
    prompt carries the runbook cheat sheet (node types, modes and wiring
    rules), so its tool list names buildable Arrow capabilities."""
    monkeypatch.setattr(reasoning, 'gateway_url', lambda: 'http://x')
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {'response': '1. Search google for the query\n'
                                '2. Extract the titles from each page'}

    import requests

    def _post(url, json=None, timeout=None, **kw):
        captured.update(json)
        return _Resp()

    monkeypatch.setattr(requests, 'post', _post)
    steps = reasoning.plan('scrape google', '(empty)',
                           {'reasoning': True, 'model': 'm'})
    assert steps == ['Search google for the query',
                     'Extract the titles from each page']
    assert 'orchestrator' in captured['system']       # runbook cheat sheet
    assert 'passthrough' in captured['system']
    assert 'brains' in captured['system']
