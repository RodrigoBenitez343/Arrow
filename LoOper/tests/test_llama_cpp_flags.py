"""llama-server command: the KV-cache footprint and window-reuse flags.

The cache is allocated once at --ctx-size, so the window size is a permanent
RAM decision, and a full window used to mean a failed request instead of a
shifted one.  These tests pin the four flags that change that, pin that they
are only emitted when the bundled binary actually supports them (an unknown
argument makes llama-server exit at startup), and pin the ``engine_start``
sample that reports what the process was really started with.

Run:  python -m pytest LoOper/tests/test_llama_cpp_flags.py
"""

import time

import AI.engine_metrics as metrics
import AI.llama_cpp_engine as lce

EXE = "llama-server.exe"


def _engine(**over):
    config = {"model_path": "m.gguf", "context_size": 4096}
    config.update(over)
    return lce.LlamaCppEngine(config)


def _all_flags_supported(monkeypatch):
    monkeypatch.setattr(lce, "_flag_supported", lambda exe, flag: True)


def _no_flags_supported(monkeypatch):
    monkeypatch.setattr(lce, "_flag_supported", lambda exe, flag: False)


def _cmd(engine):
    return engine._server_cmd(EXE)


def test_kv_type_keep_shift_and_reuse_are_emitted(monkeypatch):
    _all_flags_supported(monkeypatch)
    monkeypatch.delenv("LOOPER_LLAMACPP_KV_TYPE", raising=False)
    monkeypatch.delenv("LOOPER_LLAMACPP_CONTEXT_SHIFT", raising=False)
    monkeypatch.delenv("LOOPER_LLAMACPP_CACHE_REUSE", raising=False)
    monkeypatch.delenv("LOOPER_LLAMACPP_CONTEXT_KEEP", raising=False)
    cmd = _cmd(_engine())

    assert cmd[cmd.index("-ctk") + 1] == "q8_0"
    assert cmd[cmd.index("-ctv") + 1] == "q8_0"
    assert cmd[cmd.index("--keep") + 1] == "512", "ctx//8 capped at 512"
    assert "--context-shift" in cmd
    assert cmd[cmd.index("--cache-reuse") + 1] == "256"


def test_flags_are_withheld_from_a_binary_without_them(monkeypatch):
    _no_flags_supported(monkeypatch)
    cmd = _cmd(_engine())
    for flag in ("-ctk", "-ctv", "--keep", "--context-shift", "--cache-reuse"):
        assert flag not in cmd


def test_env_overrides(monkeypatch):
    _all_flags_supported(monkeypatch)
    monkeypatch.setenv("LOOPER_LLAMACPP_KV_TYPE", "")
    monkeypatch.setenv("LOOPER_LLAMACPP_CONTEXT_KEEP", "128")
    monkeypatch.setenv("LOOPER_LLAMACPP_CONTEXT_SHIFT", "0")
    monkeypatch.setenv("LOOPER_LLAMACPP_CACHE_REUSE", "0")
    cmd = _cmd(_engine())

    assert "-ctk" not in cmd and "-ctv" not in cmd
    assert cmd[cmd.index("--keep") + 1] == "128"
    assert "--no-context-shift" in cmd and "--context-shift" not in cmd
    assert "--cache-reuse" not in cmd


def test_keep_scales_with_a_small_window(monkeypatch):
    _all_flags_supported(monkeypatch)
    monkeypatch.delenv("LOOPER_LLAMACPP_CONTEXT_KEEP", raising=False)
    cmd = _cmd(_engine(context_size=1024))
    assert cmd[cmd.index("--keep") + 1] == "128", "1024 // 8"


def test_embedding_server_gets_no_generation_flags(monkeypatch):
    _all_flags_supported(monkeypatch)
    cmd = _cmd(_engine(embedding_mode=True))
    for flag in ("-ctk", "-ctv", "--keep", "--context-shift", "--cache-reuse"):
        assert flag not in cmd, "the embed server does not carry a KV window"
    assert "--embeddings" in cmd


def test_applied_flags_are_reported_for_the_metrics_log(monkeypatch):
    _all_flags_supported(monkeypatch)
    for name in ("LOOPER_LLAMACPP_KV_TYPE", "LOOPER_LLAMACPP_CONTEXT_KEEP",
                 "LOOPER_LLAMACPP_CONTEXT_SHIFT", "LOOPER_LLAMACPP_CACHE_REUSE"):
        monkeypatch.delenv(name, raising=False)
    engine = _engine()
    _cmd(engine)
    assert engine._kv_flags == {"kv_type": "q8_0", "keep": 512,
                                "context_shift": 1, "cache_reuse": 256}

    _no_flags_supported(monkeypatch)
    engine2 = _engine()
    _cmd(engine2)
    assert engine2._kv_flags == {"kv_type": "", "keep": 0,
                                 "context_shift": 0, "cache_reuse": 0}, \
        "an unsupported flag is never reported as applied"


def test_embedding_server_start_skips_the_sibling_release(monkeypatch):
    """An embed cold start must not free the pool *around* the scorer."""
    import AI.embedding_server as es
    import AI.laya_client as lc

    calls = []
    monkeypatch.setattr(lc, "release",
                        lambda *a, **k: calls.append("laya") or True)
    monkeypatch.setattr(es, "release",
                        lambda *a, **k: calls.append("embed") or True)

    LlamaCppEngine = lce.LlamaCppEngine
    LlamaCppEngine({"embedding_mode": True})._release_sibling_bursts()
    assert calls == [], "the embedding server is the sibling - it skips this"

    LlamaCppEngine({"embedding_mode": False})._release_sibling_bursts()
    assert calls == ["laya", "embed"], "the generation start still frees both"


def test_engine_start_sample_reports_window_and_flags(monkeypatch):
    _all_flags_supported(monkeypatch)
    monkeypatch.delenv("LOOPER_ENGINE_METRICS", raising=False)
    monkeypatch.setattr(metrics, "_append", lambda line: None)
    monkeypatch.setattr(metrics, "_RECORDS", __import__("collections").deque(
        maxlen=16))
    monkeypatch.setattr(metrics, "rss_mb", lambda name: 12.5)

    engine = _engine()
    _cmd(engine)
    engine._record_engine_start(time.perf_counter())

    rows = metrics.snapshot("engine_start")
    assert len(rows) == 1
    row = rows[0]
    assert row["ctx"] == 4096
    assert row["keep"] == 512 and row["context_shift"] == 1
    assert row["cache_reuse"] == 256
    assert "kv_bytes" in row and row["rss_mb"] == 12.5
    assert row["embedding"] == 0 and "load_ms" in row
