"""engine_metrics: the shared time/compute recorder the scaling tests read.

Pins the two properties the rest of the work depends on: the ring is bounded
and numeric-only, and the KV estimate scales the way the cache types say it
does (linear in context, halved at q8_0).

Run:  python -m pytest LoOper/tests/test_engine_metrics.py
"""

from collections import deque

import AI.engine_metrics as em
import AI.gguf_model_info as gmi


def _fresh(monkeypatch, limit=16):
    monkeypatch.delenv("LOOPER_ENGINE_METRICS", raising=False)
    monkeypatch.setattr(em, "_RECORDS", deque(maxlen=limit))
    monkeypatch.setattr(em, "_append", lambda line: None)


def test_ring_is_bounded(monkeypatch):
    _fresh(monkeypatch, limit=3)
    for i in range(6):
        em.record("forward", ms=i)
    assert em.count("forward") == 3
    assert em.series("forward") == [3.0, 4.0, 5.0]


def test_only_numbers_survive(monkeypatch):
    _fresh(monkeypatch)
    row = em.record("form_field", ms=12, route="persisted index",
                    label="Email address", forwards=9)
    assert row == {"kind": "form_field", "t": row["t"],
                   "ms": 12, "forwards": 9}


def test_kill_switch(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setenv("LOOPER_ENGINE_METRICS", "0")
    assert em.enabled() is False
    assert em.record("forward", ms=1) is None
    assert em.snapshot() == []


def test_jsonl_failure_never_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("LOOPER_ENGINE_METRICS", raising=False)
    monkeypatch.setattr(em, "_RECORDS", deque(maxlen=4))
    target = tmp_path / "engine_metrics.jsonl"   # a DIRECTORY: open fails
    target.mkdir()
    monkeypatch.setattr(em, "jsonl_path", lambda: str(target))
    assert em.record("forward", ms=3) is not None


def test_engine_codes_are_numeric(monkeypatch):
    """The log cannot carry an engine NAME, so it carries its code."""
    assert em.engine_code("laya") == 1
    assert em.engine_code("EMBED") == 2
    assert em.engine_code("llama-server") == 3
    assert em.engine_code("something-else") == 0


def test_measure_records_ms(monkeypatch):
    _fresh(monkeypatch)
    with em.measure("engine_load", engine_code=em.engine_code("laya")):
        pass
    rows = em.snapshot("engine_load")
    assert len(rows) == 1
    assert rows[0]["engine_code"] == 1
    assert "ms" in rows[0] and rows[0]["ms"] >= 0.0


def test_summary_and_series(monkeypatch):
    _fresh(monkeypatch)
    for ms in (10, 20, 30):
        em.record("forward", ms=ms)
    assert em.summary("forward") == {"count": 3, "total_ms": 60.0,
                                     "mean_ms": 20.0, "max_ms": 30.0}
    assert em.summary("nothing")["count"] == 0


def test_kv_bytes_scales_with_context_and_type(monkeypatch):
    params = gmi.ModelKVParams(n_layer=4, n_kv_heads=4, head_dim=64)
    monkeypatch.setattr(gmi, "read_model_kv_params", lambda path: params)

    f16_2k = em.kv_bytes("x.gguf", 2048, "f16")
    f16_4k = em.kv_bytes("x.gguf", 4096, "f16")
    q8_2k = em.kv_bytes("x.gguf", 2048, "q8_0")

    assert f16_2k > 0
    assert f16_4k == f16_2k * 2, "the cache is linear in the window"
    assert q8_2k == f16_2k // 2, "q8_0 halves the element size"


def test_kv_bytes_without_geometry_is_zero(monkeypatch):
    monkeypatch.setattr(gmi, "read_model_kv_params", lambda path: None)
    assert em.kv_bytes("missing.gguf", 4096, "q8_0") == 0
