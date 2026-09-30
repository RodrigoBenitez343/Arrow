"""Unit tests for AI/gguf_model_info.py — GGUF binary metadata parsing.

Builds real GGUF byte payloads in-memory (v1/v2/v3) and verifies header
parsing, metadata value decoding, model classification and context-size
extraction — all without any external model files.
"""

import os

import pytest

from conftest import build_gguf
from AI import gguf_model_info as gguf


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


def test_magic_mismatch_raises():
    with pytest.raises(ValueError):
        gguf._read_gguf_header(b"NOTGGUF" + b"\x00" * 32)


def test_fit_context_to_ram_budget_negative_floors_at_1024():
    """With nothing left for a KV cache the window floors at 1024 (usable),
    not the degenerate 512 that guaranteed prompt overflow on every real
    request."""
    kv = gguf.ModelKVParams(
        n_layer=16, n_kv_heads=8, head_dim=64, n_embd=768,
    )
    eff, capped = gguf.fit_context_to_ram(
        native_ctx=32768,
        model_path="",  # nonexistent -> model_bytes=0; budget is forced negative
        kv=kv,
        available_ram=0,  # budget = 0*0.8 - 0 - reserve < 0
    )
    assert eff == 1024
    assert capped is True


def test_fit_context_to_ram_hosts_max_window_when_weights_are_paged(monkeypatch):
    """Weights that FIT in available RAM but blow the linear budget must not
    collapse the window to the 1024 floor: mmap'd weights are evictable, so
    the context costs only its KV cache — take the largest window that fits."""
    kv = gguf.ModelKVParams(
        n_layer=32, n_kv_heads=8, head_dim=128, n_embd=1024,
    )
    fake = "fits.gguf"
    monkeypatch.setattr(os.path, "isfile", lambda p: p == fake)
    monkeypatch.setattr(os.path, "getsize", lambda p: 3 * 1024 ** 3)
    monkeypatch.setattr(gguf, "is_moe_model", lambda p: False)

    # 4 GiB available vs 3 GiB weights: 4*0.8 - 3 - 0.75 < 0, so the linear
    # budget says "no room" while the model plainly loads and runs.
    eff, capped = gguf.fit_context_to_ram(
        32768, fake, kv, available_ram=4 * 1024 ** 3,
    )
    assert capped is True
    assert 1024 < eff < 32768  # a real window, not the degenerate floor

    # More RAM must never yield a SMALLER window (a two-branch crossover did
    # exactly that: 3 GiB -> 15360 tokens, 4 GiB -> 2048).
    wider, _ = gguf.fit_context_to_ram(
        32768, fake, kv, available_ram=8 * 1024 ** 3,
    )
    assert wider >= eff


def test_fit_context_to_ram_keeps_native_when_it_fits():
    kv = gguf.ModelKVParams(
        n_layer=16, n_kv_heads=8, head_dim=64, n_embd=768,
    )
    # 8 GiB available easily fits any small-model native context.
    eff, capped = gguf.fit_context_to_ram(
        native_ctx=2048, model_path="", kv=kv, available_ram=8 * 1024 ** 3,
    )
    assert eff == 2048
    assert capped is False


def test_fit_context_to_ram_moe_ignores_offloadable_expert_weights(monkeypatch):
    """MoE models memory-map experts (--cpu-moe / mmap) — the resident set is
    only the attention/shared layers, so the RAM cap must NOT count the whole
    GGUF file, or every MoE model collapses to the 1024-token floor and real
    prompts (e.g. the coding panel chat) overflow."""
    kv = gguf.ModelKVParams(
        n_layer=16, n_kv_heads=8, head_dim=64, n_embd=768,
    )
    fake = "big_moe.gguf"
    monkeypatch.setattr(os.path, "isfile", lambda p: p == fake)
    monkeypatch.setattr(os.path, "getsize", lambda p: 40 * 1024 ** 3)  # 40 GiB file

    # Dense model: whole 40 GiB counts -> budget <= 0 -> degenerate floor.
    monkeypatch.setattr(gguf, "is_moe_model", lambda p: False)
    eff, capped = gguf.fit_context_to_ram(
        32768, fake, kv, available_ram=16 * 1024 ** 3,
    )
    assert capped is True and eff == 1024

    # MoE model: only ~20% resident -> native window fits comfortably.
    monkeypatch.setattr(gguf, "is_moe_model", lambda p: True)
    eff, capped = gguf.fit_context_to_ram(
        32768, fake, kv, available_ram=16 * 1024 ** 3,
    )
    assert capped is False and eff == 32768

def test_unsupported_version_raises():
    # Version 0 is not 1/2/>=3 -> unsupported
    with pytest.raises(ValueError):
        gguf._read_gguf_header(build_gguf(0, {"a": "b"}))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_header_roundtrip_versions(version):
    payload = build_gguf(version, {"general.architecture": "llama"})
    ver, tensor_count, kv_count, offset, metadata = gguf._read_gguf_header(payload)
    assert ver == version
    assert tensor_count == 0
    assert kv_count == 1
    assert metadata["general.architecture"] == "llama"


def test_truncated_header_does_not_crash():
    payload = build_gguf(3, {"a": "b"})
    # Exactly the 24-byte v3 header, no KV data: kv loop must stop cleanly
    ver, _tc, _kc, _off, metadata = gguf._read_gguf_header(payload[:24])
    assert ver == 3
    assert metadata == {}


def test_partial_kv_header_skipped_gracefully():
    payload = build_gguf(3, {"a": "b"})
    # Header + one byte of the first KV key length: loop breaks, no crash
    ver, _tc, _kc, _off, metadata = gguf._read_gguf_header(payload[:25])
    assert ver == 3
    assert "a" not in metadata


# ---------------------------------------------------------------------------
# Value decoding
# ---------------------------------------------------------------------------


def test_read_all_scalar_types():
    payload = build_gguf(3, {
        "u8": 200, "i8": -5, "u16": 60000, "i16": -30000,
        "u32": 4000000000, "i32": -2000000000, "f32": 1.5, "f64": 2.25,
        "bool_t": True, "bool_f": False, "u64": 2**60, "i64": -(2**60),
    })
    _ver, _tc, _kc, _off, metadata = gguf._read_gguf_header(payload)
    assert metadata["u8"] == 200
    assert metadata["i8"] == -5
    assert metadata["u16"] == 60000
    assert metadata["i16"] == -30000
    assert metadata["u32"] == 4000000000
    assert metadata["i32"] == -2000000000
    assert metadata["f32"] == pytest.approx(1.5)
    assert metadata["f64"] == pytest.approx(2.25)
    assert metadata["bool_t"] is True
    assert metadata["bool_f"] is False
    assert metadata["u64"] == 2**60
    assert metadata["i64"] == -(2**60)


def test_read_string_and_array_values():
    payload = build_gguf(3, {
        "general.name": "SmolLM3",
        "general.files": ["a.bin", "b.bin", "c.bin"],
    })
    _ver, _tc, _kc, _off, metadata = gguf._read_gguf_header(payload)
    assert metadata["general.name"] == "SmolLM3"
    assert metadata["general.files"] == ["a.bin", "b.bin", "c.bin"]


def test_string_truncated_at_buffer_end():
    import struct
    raw = struct.pack("<Q", 11) + b"hello world"
    assert gguf._read_gguf_string(raw, 0) == ("hello world", 8 + len("hello world"))


# ---------------------------------------------------------------------------
# File-level API
# ---------------------------------------------------------------------------


def test_read_architecture_and_context_size(make_gguf_file):
    path = make_gguf_file({
        "general.architecture": "qwen2",
        "qwen2.context_length": 8192,
    })
    assert gguf._read_architecture(path) == "qwen2"
    assert gguf.read_model_context_size(path) == 8192


def test_context_size_fallback_any_key(make_gguf_file):
    path = make_gguf_file({
        "general.architecture": "custom",
        "custom.context_length": 2048,
    })
    assert gguf.read_model_context_size(path) == 2048


def test_missing_file_returns_none(tmp_path):
    missing = str(tmp_path / "nope.gguf")
    assert gguf._read_architecture(missing) is None
    assert gguf.read_model_context_size(missing) is None


def test_read_model_kv_params_array_head_count_kv(make_gguf_file):
    """LFM2.5-style per-layer head_count_kv ARRAY is flattened so the
    RAM-aware context cap engages instead of being skipped (a skipped cap
    loads the 128k native window and OOMs the KV cache at startup)."""
    path = make_gguf_file({
        "general.architecture": "lfm2",
        "lfm2.block_count": 16,
        "lfm2.context_length": 128000,
        "lfm2.attention.head_count": 32,
        "lfm2.attention.head_count_kv": [0, 0, 8, 0, 0, 8, 0, 0, 8, 0, 8, 0, 8, 0, 8, 0],
        "lfm2.embedding_length": 2048,
    })
    kv = gguf.read_model_kv_params(path)
    assert kv is not None, "array head_count_kv must not be rejected"
    assert kv.n_kv_heads == 8
    assert kv.n_layer == 6  # non-zero entries = effective KV layer count
    assert kv.head_dim == 64
    # The RAM-aware cap must now engage on a tight machine: the 128k native
    # window (~1.5 GiB KV) no longer fits and is snapped down.
    eff, capped = gguf.fit_context_to_ram(
        128000, path, kv, available_ram=2 * 1024 ** 3,
    )
    assert capped is True
    assert eff < 128000


def test_truncated_gguf_file_returns_none(tmp_path):
    path = tmp_path / "tiny.gguf"
    path.write_bytes(b"GGUF\x03")
    assert gguf._read_architecture(str(path)) is None


# ---------------------------------------------------------------------------
# classify_model
# ---------------------------------------------------------------------------


def test_classify_text_model(make_gguf_file):
    path = make_gguf_file({"general.architecture": "llama"})
    cap = gguf.classify_model(path)
    assert cap.model_type == "text"
    assert cap.architecture == "llama"
    assert not cap.needs_special_tokens
    assert not cap.needs_mtmd_cli


def test_classify_grounding_from_metadata(make_gguf_file, tmp_path):
    path = make_gguf_file({"general.architecture": "locateanything"})
    cap = gguf.classify_model(path)
    assert cap.model_type == "grounding"
    assert cap.needs_special_tokens is True
    assert cap.needs_mtmd_cli is True


def test_classify_grounding_from_filename(tmp_path):
    # No valid GGUF header (bad magic) -> filename fallback
    path = tmp_path / "LocateAnything-3B-Q8_0.gguf"
    path.write_bytes(b"\x00" * 32)
    cap = gguf.classify_model(str(path))
    assert cap.model_type == "grounding"
    assert cap.architecture == "locateanything"


def test_classify_multimodal_requires_mmproj(make_gguf_file, tmp_path):
    path = make_gguf_file({"general.architecture": "llava"})
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF" + b"\x00" * 64)

    cap_with = gguf.classify_model(path, str(mmproj))
    assert cap_with.model_type == "multimodal"
    assert cap_with.has_mmproj is True

    cap_without = gguf.classify_model(path)
    assert cap_without.model_type == "text"  # mmproj required for multimodal
    assert cap_without.has_mmproj is False


def test_incompatible_mmproj_ignored(make_gguf_file, tmp_path):
    """A text-only model must NOT inherit a sibling mmproj."""
    path = make_gguf_file({"general.architecture": "llama"})
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF" + b"\x00" * 64)
    cap = gguf.classify_model(path, str(mmproj))
    assert cap.model_type == "text"
    assert cap.has_mmproj is False


def test_automatic_engine_detection_via_kv_geometry(make_gguf_file, tmp_path):
    """A multimodal GGUF with INCOMPLETE KV metadata (kv_heads missing/zero)
    must route to llama-mtmd-cli automatically — no hardcoded model names —
    while a complete-metadata multimodal model stays on the llama-server."""
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF" + b"\x00" * 64)

    incomplete = make_gguf_file({"general.architecture": "qwen2vl"})
    cap = gguf.classify_model(incomplete, str(mmproj))
    assert cap.model_type == "multimodal"
    assert cap.needs_mtmd_cli is True  # incomplete KV geometry -> mtmd-cli

    complete = make_gguf_file({
        "general.architecture": "qwen2vl",
        "qwen2vl.block_count": 16,
        "qwen2vl.attention.head_count_kv": 8,
        "qwen2vl.attention.key_length": 64,
    })
    cap2 = gguf.classify_model(complete, str(mmproj))
    assert cap2.model_type == "multimodal"
    assert cap2.needs_mtmd_cli is False  # complete geometry -> llama-server


# ---------------------------------------------------------------------------
# MoE detection
# ---------------------------------------------------------------------------


def test_is_moe_via_expert_count(make_gguf_file):
    path = make_gguf_file({
        "general.architecture": "qwen3moe",
        "qwen3moe.expert_count": 128,
    })
    assert gguf.is_moe_model(path) is True


def test_is_moe_via_arch_name(make_gguf_file):
    # No expert_count key — the "moe" architecture name is the fallback.
    path = make_gguf_file({"general.architecture": "qwen3moe"})
    assert gguf.is_moe_model(path) is True


def test_is_moe_false_for_dense_model(make_gguf_file):
    path = make_gguf_file({"general.architecture": "llama"})
    assert gguf.is_moe_model(path) is False


def test_is_moe_false_for_zero_experts(make_gguf_file):
    path = make_gguf_file({
        "general.architecture": "custom",
        "custom.expert_count": 0,
    })
    assert gguf.is_moe_model(path) is False


def test_is_moe_missing_file_returns_false(tmp_path):
    assert gguf.is_moe_model(str(tmp_path / "nope.gguf")) is False


def test_classify_moe_flag(make_gguf_file):
    moe = make_gguf_file({
        "general.architecture": "qwen3moe",
        "qwen3moe.expert_count": 128,
        "qwen3moe.context_length": 4096,
    })
    cap = gguf.classify_model(moe)
    assert cap.is_moe is True

    dense = make_gguf_file({"general.architecture": "llama"})
    assert gguf.classify_model(dense).is_moe is False


# ---------------------------------------------------------------------------
# Filename fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("LocateAnything-3B-Q8_0.gguf", "locateanything"),
    ("LFM2.5-1.2B-Thinking-Q8_0.gguf", "lfm"),
    ("Qwen3.6-35B-A3B-GGUF.gguf", "qwen2"),
    ("SmolLM3-Q4_K_M.gguf", "llama"),
    ("VibeThinker-3B.Q8_0.gguf", None),
    ("deepreinforce-ai_Ornith-1.0-9B-Q8_0.gguf", None),
])
def test_guess_architecture_from_filename(name, expected):
    assert gguf._guess_architecture_from_filename(name) == expected
