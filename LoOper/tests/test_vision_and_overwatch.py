"""Unit tests for:
- AI/recursive_vision_reasoner.py (RecursiveVisionReasoner)
- AI/OverWatch/api/api_client.py (OverWatchAPIClient)
"""

import pytest

from AI.recursive_vision_reasoner import RecursiveVisionReasoner
from AI.OverWatch.api.api_client import (
    OverWatchAPIClient,
    ChatMessage,
    AsyncOverWatchAPIClient,
)
from conftest import FakeResponse, FakeSession


# ---------------------------------------------------------------------------
# RecursiveVisionReasoner
# ---------------------------------------------------------------------------


def make_reasoner(**overrides):
    cfg = {
        "ollama_client": object(),
        "max_cycles": 3,
        "vision_model": "minicpm-v:latest",
        "temperature": 0.3,
        "confidence_threshold": 0.85,
    }
    cfg.update(overrides)
    return RecursiveVisionReasoner(**cfg)


def test_reasoner_init_defaults():
    r = make_reasoner()
    assert r.max_cycles == 3
    assert r.confidence_threshold == 0.85
    assert r.vision_model == "minicpm-v:latest"
    assert r.reasoning_model == r.vision_model  # defaults to vision model


def test_reasoner_max_cycles_clamped():
    assert make_reasoner(max_cycles=99).max_cycles == 5
    assert make_reasoner(max_cycles=0).max_cycles == 1


def test_reasoner_custom_reasoning_model():
    r = make_reasoner(reasoning_model="smollm3")
    assert r.reasoning_model == "smollm3"


def test_reset_clears_state():
    r = make_reasoner()
    r._reasoning_history = [{"x": 1}]
    r.reset()
    assert r._reasoning_history == []


def test_format_vision_context_dict():
    r = make_reasoner()
    text = r._format_vision_context({"labels": ["cat", "dog"], "count": 2})
    assert "cat" in text and "dog" in text


def test_format_vision_context_json_string():
    r = make_reasoner()
    text = r._format_vision_context('{"labels": ["a"]}')
    assert '"labels"' in text


def test_format_vision_context_plain_string():
    r = make_reasoner()
    assert r._format_vision_context("plain vision data") == "plain vision data"


def test_format_vision_context_none():
    r = make_reasoner()
    assert r._format_vision_context(None) == "No vision context available"


def test_synthesize_results_uses_call_llm_dict(monkeypatch):
    r = make_reasoner()
    monkeypatch.setattr(r, "_call_llm", lambda *a, **k: {
        "final_plan": ["step 1"], "summary": "plan", "confidence": 0.9})
    result = r._synthesize_results("describe scene", "context text")
    assert result["summary"] == "plan"
    assert result["final_plan"] == ["step 1"]
    assert result["cycles_used"] == 0


def test_synthesize_results_parse_error_fallback(monkeypatch):
    r = make_reasoner()
    r._reasoning_history = [
        {"task_breakdown": ["b1", "b2"], "confidence": 0.7},
    ]
    monkeypatch.setattr(r, "_call_llm", lambda *a, **k: {"parse_error": True})
    result = r._synthesize_results("task", "ctx")
    assert result["final_plan"] == ["b1", "b2"]
    assert result["summary"] == "Synthesized from recursive reasoning"


def test_quick_vision_reason_module_function(monkeypatch):
    from AI import recursive_vision_reasoner as rvr

    class FakeReasoner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def reason(self, task, vision_context, images):
            return {"final_plan": ["done"], "summary": "ok"}

    monkeypatch.setattr(rvr, "RecursiveVisionReasoner", FakeReasoner)
    result = rvr.quick_vision_reason(object(), "what is here", {"labels": ["a"]})
    assert result["summary"] == "ok"


# ---------------------------------------------------------------------------
# OverWatchAPIClient
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    session = FakeSession()
    cli = OverWatchAPIClient.__new__(OverWatchAPIClient)
    cli.base_url = "http://localhost:9000"
    cli.timeout = 30
    cli.session = session
    return cli, session


def test_health_check(client):
    cli, session = client
    session.responses[("GET", "http://localhost:9000/health")] = FakeResponse(
        200, json_data={"status": "healthy"})
    assert cli.health_check() == {"status": "healthy"}


def test_list_models(client):
    cli, session = client
    session.responses[("GET", "http://localhost:9000/models")] = FakeResponse(
        200, json_data={"models": ["a", "b"]})
    assert cli.list_models() == ["a", "b"]


def test_generate_builds_payload(client):
    cli, session = client
    session.responses[("POST", "http://localhost:9000/generate")] = FakeResponse(
        200, json_data={"response": "hi"})
    cli.generate("hello", model="m", temperature=0.1, max_tokens=10, system="sys")
    method, url, kwargs = session.calls[0]
    body = kwargs["json"]
    assert body["prompt"] == "hello"
    assert body["model"] == "m"
    assert body["temperature"] == 0.1
    assert body["max_tokens"] == 10
    assert body["system"] == "sys"


def test_chat_completion_converts_chatmessage(client):
    cli, session = client
    session.responses[("POST", "http://localhost:9000/chat")] = FakeResponse(
        200, json_data={"choices": []})
    cli.chat_completion([ChatMessage(role="user", content="hi")], model="m")
    body = session.calls[0][2]["json"]
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_completion_invalid_message_raises(client):
    cli, session = client
    with pytest.raises(ValueError):
        cli.chat_completion([42])


def test_unsupported_method_wrapped_in_runtime_error(client):
    cli, session = client
    # ValueError is raised internally but the generic handler wraps it
    with pytest.raises(RuntimeError):
        cli._make_request("PATCH", "/x")


def test_connection_error_wrapped(client, monkeypatch):
    cli, session = client

    class Boom:
        def get(self, *a, **k):
            import requests
            raise requests.exceptions.ConnectionError("refused")

    cli.session = Boom()
    with pytest.raises(ConnectionError):
        cli._make_request("GET", "/health")


def test_test_connection_success(client):
    cli, session = client
    session.responses[("GET", "http://localhost:9000/health")] = FakeResponse(
        200, json_data={"status": "healthy"})
    assert cli.test_connection() is True


def test_test_connection_failure(client):
    cli, session = client
    session.responses[("GET", "http://localhost:9000/health")] = FakeResponse(
        500, json_data={})
    assert cli.test_connection() is False


def test_get_api_info_fallback(client):
    cli, session = client
    session.responses[("GET", "http://localhost:9000/info")] = FakeResponse(404)
    info = cli.get_api_info()
    assert info["name"] == "OverWatch API"


def test_set_timeout_and_close(client):
    cli, session = client
    cli.set_timeout(5)
    assert cli.timeout == 5
    closed = []
    cli.session.close = lambda: closed.append(1)
    cli.close()
    assert closed == [1]


def test_async_client_not_implemented():
    cli = AsyncOverWatchAPIClient("http://x")
    with pytest.raises(NotImplementedError):
        import asyncio
        asyncio.run(cli.health_check())
