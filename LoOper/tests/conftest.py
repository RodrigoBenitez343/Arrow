"""Shared fixtures and helpers for the LoOper unit test suite.

All tests run with the ``LoOper/`` package root on ``sys.path`` so that
absolute imports used throughout the application (``AI.*``, ``player.*``,
``recorder.*``, ``NGUI.*``, ``builder.*``) resolve exactly as they do at
runtime.
"""

import json
import os
import struct
import sys

import pytest

_LOOPER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_LOOPER_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Test hermeticity
# ---------------------------------------------------------------------------

# The linear activity timeline (player/agentic_ops/run_memory.py) records
# every real run.  The suite must not write test noise into the developer's
# timeline: disabled by default; tests that exercise it set
# LOOPER_RUN_MEMORY=on + LOOPER_RUN_MEMORY_DB per-test.
os.environ["LOOPER_RUN_MEMORY"] = "off"

# The embedded Laya engine is an EXTERNAL binary (laya.exe daemon).  The suite
# must not launch it (slow, flaky, writes real logs): pin it OFF so every
# Laya-first consumer takes its deterministic fallback.  Tests that exercise a
# Laya verdict monkeypatch the hook/primitive (or set LOOPER_LAYA) per-test.
os.environ["LOOPER_LAYA"] = "off"

# ---------------------------------------------------------------------------
# GGUF binary builders (used by test_gguf_model_info.py)
# ---------------------------------------------------------------------------

GGUF_TYPE_UINT8 = 0
GGUF_TYPE_INT8 = 1
GGUF_TYPE_UINT16 = 2
GGUF_TYPE_INT16 = 3
GGUF_TYPE_UINT32 = 4
GGUF_TYPE_INT32 = 5
GGUF_TYPE_FLOAT32 = 6
GGUF_TYPE_BOOL = 7
GGUF_TYPE_STRING = 8
GGUF_TYPE_ARRAY = 9
GGUF_TYPE_UINT64 = 10
GGUF_TYPE_INT64 = 11
GGUF_TYPE_FLOAT64 = 12


def _gguf_string(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("<Q", len(raw)) + raw


def _gguf_value(val):
    """Encode a python value as a (type, bytes) GGUF metadata value."""
    if isinstance(val, str):
        return GGUF_TYPE_STRING, _gguf_string(val)
    if isinstance(val, bool):
        return GGUF_TYPE_BOOL, struct.pack("<B", 1 if val else 0)
    if isinstance(val, int):
        return GGUF_TYPE_INT64, struct.pack("<q", val)
    if isinstance(val, float):
        return GGUF_TYPE_FLOAT64, struct.pack("<d", val)
    if isinstance(val, (list, tuple)):
        if not val:
            return GGUF_TYPE_ARRAY, struct.pack("<IQ", GGUF_TYPE_STRING, 0)
        inner_type = None
        items = b""
        for item in val:
            it, ib = _gguf_value(item)
            if inner_type is None:
                inner_type = it
            items += ib
        return GGUF_TYPE_ARRAY, struct.pack("<IQ", inner_type, len(val)) + items
    raise TypeError(f"unsupported GGUF test value: {type(val)!r}")


def build_gguf(version: int, metadata: dict) -> bytes:
    """Build a minimal valid GGUF file payload for the given version."""
    out = bytearray(b"GGUF")
    out += struct.pack("<I", version)
    tensor_count = 0
    if version == 1:
        out += struct.pack("<I", tensor_count)
        out += struct.pack("<I", len(metadata))
        offset = 16
    elif version == 2:
        out += struct.pack("<Q", tensor_count)
        out += struct.pack("<I", len(metadata))
        offset = 20
    else:
        out += struct.pack("<Q", tensor_count)
        out += struct.pack("<Q", len(metadata))
        offset = 24
    for key, val in metadata.items():
        out += _gguf_string(key)
        vtype, vbytes = _gguf_value(val)
        out += struct.pack("<I", vtype)
        out += vbytes
    return bytes(out)


@pytest.fixture
def make_gguf_file(tmp_path):
    """Factory fixture that writes a valid GGUF file and returns its path."""

    def _make(metadata: dict, version: int = 3, name: str = "model.gguf"):
        path = tmp_path / name
        path.write_bytes(build_gguf(version, metadata))
        return str(path)

    return _make


# ---------------------------------------------------------------------------
# Fake requests module (used to isolate network-dependent engine code)
# ---------------------------------------------------------------------------


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, json_data=None, text="", headers=None,
                 iter_lines=None, content=b""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self.headers = headers or {}
        self._iter_lines = iter_lines or []
        self.content = content
        self.ok = 200 <= status_code < 300
        self.url = ""
        self.reason = ""

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_lines(self, *args, **kwargs):
        return iter(self._iter_lines)


class FakeSession:
    """Recorded requests.Session: stores the last call and returns canned responses."""

    def __init__(self, responses=None):
        # responses: dict keyed by (method, path) -> FakeResponse, or a
        # callable (method, url, **kwargs) -> FakeResponse
        self.responses = responses or {}
        self.headers = {}
        self.calls = []
        self.closed = False

    def _dispatch(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if callable(self.responses):
            return self.responses(method, url, **kwargs)
        key = (method, url)
        if key in self.responses:
            return self.responses[key]
        # Fallback: match on path suffix
        for (m, u), resp in self.responses.items():
            if m == method and u in url:
                return resp
        return FakeResponse(404, json_data={}, text="not found")

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, **kwargs)

    def close(self):
        self.closed = True


def build_fake_requests_module(session=None):
    """Build a module-like object to stand in for ``requests``.

    Engine code performs ``import requests`` inside functions; installing
    this object under ``sys.modules['requests']`` lets tests control every
    HTTP call deterministically.
    """
    class _Exceptions:
        class RequestException(Exception):
            pass

        class Timeout(RequestException):
            pass

        class ConnectionError(RequestException):
            pass

    session = session or FakeSession()

    class _Module:
        Session = lambda *a, **k: session  # noqa: E731
        exceptions = _Exceptions
        get = session.get
        post = session.post

    return _Module()


@pytest.fixture
def fake_requests():
    """Install a fake `requests` module; yields the FakeSession used by it."""
    import sys as _sys

    session = FakeSession()
    module = build_fake_requests_module(session)
    _sys.modules["requests"] = module
    try:
        yield session
    finally:
        _sys.modules.pop("requests", None)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def make_sequence_file(tmp_path):
    """Write a sequence-shaped JSON file (has an 'actions' list)."""

    def _make(name="seq.json", actions=None, images=None, extra=None):
        data = {"actions": actions or [{"type": "wait", "duration": 0.1}]}
        if images is not None:
            data["images"] = images
        if extra:
            data.update(extra)
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    return _make


@pytest.fixture
def make_chain_file(tmp_path):
    """Write a chain-shaped JSON file (has node lists, no 'actions')."""

    def _make(name="chain.json", sequences=None, llm_nodes=None,
              conditional_nodes=None, chain_import_nodes=None):
        data = {}
        if sequences is not None:
            data["sequences"] = sequences
        if llm_nodes is not None:
            data["llm_nodes"] = llm_nodes
        if conditional_nodes is not None:
            data["conditional_nodes"] = conditional_nodes
        if chain_import_nodes is not None:
            data["chain_import_nodes"] = chain_import_nodes
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    return _make
