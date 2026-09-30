#!/usr/bin/env python3
"""
GGUF Model Information Reader

Lightweight pure-Python reader for GGUF model file headers.
Extracts metadata (architecture, file type, etc.) without requiring
any external dependencies beyond the Python standard library.

GGUF format (v3):
  - Magic:    4 bytes "GGUF"
  - Version:  uint32 LE
  - Tensor count: uint64 LE (v3) or uint32 LE (v1/v2)
  - Metadata KV count: uint64 LE (v3) or uint32 LE (v1/v2)
  - Metadata KV pairs...

Only the first few KB of the file are read to find the architecture key.
"""

from __future__ import annotations

import struct
import os
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Literal, Tuple

_logger = logging.getLogger("api.llamacpp.gguf")

# ---------------------------------------------------------------------------
# GGUF binary parsing helpers
# ---------------------------------------------------------------------------

_GGUF_MAGIC = b"GGUF"

# Value type constants from the GGUF spec
_GGUF_TYPE_UINT8 = 0
_GGUF_TYPE_INT8 = 1
_GGUF_TYPE_UINT16 = 2
_GGUF_TYPE_INT16 = 3
_GGUF_TYPE_UINT32 = 4
_GGUF_TYPE_INT32 = 5
_GGUF_TYPE_FLOAT32 = 6
_GGUF_TYPE_BOOL = 7
_GGUF_TYPE_STRING = 8
_GGUF_TYPE_ARRAY = 9
_GGUF_TYPE_UINT64 = 10
_GGUF_TYPE_INT64 = 11
_GGUF_TYPE_FLOAT64 = 12


def _read_gguf_header(data: bytes) -> Tuple[int, int, int, int, Dict[str, object]]:
    """Parse GGUF file header from raw bytes.

    Returns:
        (version, tensor_count, metadata_kv_count, offset, metadata_dict)
    """
    if data[:4] != _GGUF_MAGIC:
        raise ValueError("Not a valid GGUF file (bad magic)")

    (version,) = struct.unpack_from("<I", data, 4)

    # Version-dependent field sizes
    if version == 1:
        (tensor_count,) = struct.unpack_from("<I", data, 8)
        (kv_count,) = struct.unpack_from("<I", data, 12)
        offset = 16
    elif version == 2:
        (tensor_count,) = struct.unpack_from("<Q", data, 8)
        (kv_count,) = struct.unpack_from("<I", data, 16)
        offset = 20
    elif version >= 3:
        (tensor_count,) = struct.unpack_from("<Q", data, 8)
        (kv_count,) = struct.unpack_from("<Q", data, 16)
        offset = 24
    else:
        raise ValueError(f"Unsupported GGUF version: {version}")

    metadata: Dict[str, object] = {}
    for _ in range(kv_count):
        if offset + 4 > len(data):
            break
        # Read key string
        key, offset = _read_gguf_string(data, offset)
        # Read value type
        if offset + 4 > len(data):
            break
        (val_type,) = struct.unpack_from("<I", data, offset)
        offset += 4
        # Read value
        val, offset = _read_gguf_value(data, offset, val_type)
        metadata[key] = val

    return version, tensor_count, kv_count, offset, metadata


def _read_gguf_string(data: bytes, offset: int) -> Tuple[str, int]:
    """Read a GGUF string value at offset. Returns (string, new_offset)."""
    if offset + 8 > len(data):
        return "", len(data)
    (str_len,) = struct.unpack_from("<Q", data, offset)
    offset += 8
    actual_avail = max(0, len(data) - offset)
    read_len = min(str_len, actual_avail)
    raw = data[offset : offset + read_len]
    # GGUF strings are UTF-8 encoded
    return raw.decode("utf-8", errors="replace"), offset + str_len


def _read_gguf_value(
    data: bytes, offset: int, val_type: int
) -> Tuple[object, int]:
    """Read a GGUF metadata value at offset. Returns (value, new_offset)."""
    _min_size = {
        _GGUF_TYPE_UINT8: 1, _GGUF_TYPE_INT8: 1,
        _GGUF_TYPE_UINT16: 2, _GGUF_TYPE_INT16: 2,
        _GGUF_TYPE_UINT32: 4, _GGUF_TYPE_INT32: 4,
        _GGUF_TYPE_FLOAT32: 4, _GGUF_TYPE_BOOL: 1,
        _GGUF_TYPE_UINT64: 8, _GGUF_TYPE_INT64: 8,
        _GGUF_TYPE_FLOAT64: 8,
    }
    needed = _min_size.get(val_type)
    if needed is not None and offset + needed > len(data):
        return None, len(data)

    if val_type == _GGUF_TYPE_UINT8:
        (v,) = struct.unpack_from("<B", data, offset)
        return v, offset + 1
    elif val_type == _GGUF_TYPE_INT8:
        (v,) = struct.unpack_from("<b", data, offset)
        return v, offset + 1
    elif val_type == _GGUF_TYPE_UINT16:
        (v,) = struct.unpack_from("<H", data, offset)
        return v, offset + 2
    elif val_type == _GGUF_TYPE_INT16:
        (v,) = struct.unpack_from("<h", data, offset)
        return v, offset + 2
    elif val_type == _GGUF_TYPE_UINT32:
        (v,) = struct.unpack_from("<I", data, offset)
        return v, offset + 4
    elif val_type == _GGUF_TYPE_INT32:
        (v,) = struct.unpack_from("<i", data, offset)
        return v, offset + 4
    elif val_type == _GGUF_TYPE_FLOAT32:
        (v,) = struct.unpack_from("<f", data, offset)
        return v, offset + 4
    elif val_type == _GGUF_TYPE_BOOL:
        (v,) = struct.unpack_from("<B", data, offset)
        return bool(v), offset + 1
    elif val_type == _GGUF_TYPE_STRING:
        return _read_gguf_string(data, offset)
    elif val_type == _GGUF_TYPE_ARRAY:
        return _read_gguf_array(data, offset)
    elif val_type == _GGUF_TYPE_UINT64:
        (v,) = struct.unpack_from("<Q", data, offset)
        return v, offset + 8
    elif val_type == _GGUF_TYPE_INT64:
        (v,) = struct.unpack_from("<q", data, offset)
        return v, offset + 8
    elif val_type == _GGUF_TYPE_FLOAT64:
        (v,) = struct.unpack_from("<d", data, offset)
        return v, offset + 8
    else:
        _logger.warning("Unknown GGUF value type %d at offset %d", val_type, offset)
        return None, offset


def _read_gguf_array(data: bytes, offset: int) -> Tuple[list, int]:
    """Read a GGUF array value. Returns (list, new_offset)."""
    if offset + 12 > len(data):
        return [], len(data)
    (val_type,) = struct.unpack_from("<I", data, offset)
    offset += 4
    (arr_len,) = struct.unpack_from("<Q", data, offset)
    offset += 8
    items = []
    for _ in range(arr_len):
        if offset >= len(data):
            break
        val, offset = _read_gguf_value(data, offset, val_type)
        items.append(val)
    return items, offset


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class ModelCapability:
    """Describes a GGUF model's capabilities inferred from its metadata."""

    model_type: Literal["text", "multimodal", "grounding"]
    """High-level model category."""

    architecture: str
    """Architecture name from GGUF metadata (e.g. 'llama', 'qwen2', 'LFM')."""

    has_mmproj: bool
    """Whether a companion mmproj GGUF file was found for this model."""

    needs_special_tokens: bool = False
    """True if the server needs --special to emit control tokens
    (grounding models like LocateAnything)."""

    needs_mtmd_cli: bool = False
    """True if inference must run through llama-mtmd-cli (one-shot) instead
    of the llama-server: grounding models (LocateAnything), and any
    multimodal GGUF whose KV geometry metadata is incomplete (the server's
    attention init hangs on those).  Detected automatically from the
    model's own metadata — never from hardcoded model names."""

    context_size: int = 0
    """The model's native training context size (n_ctx_train) from GGUF
    metadata.  0 means it could not be determined and the API should fall
    back to the caller-provided value or a safe default."""

    is_moe: bool = False
    """True for Mixture-of-Experts (MoE) models (e.g. qwen3moe, deepseek2).
    The engine uses this to keep expert weights on the CPU (--cpu-moe) when
    GPU offload is enabled, so the device-memory budget only holds attention
    layers + KV cache instead of the expert-dominated weight set."""


@dataclass
class ModelKVParams:
    """KV-cache geometry of a model, read from GGUF metadata.

    Used to estimate the memory cost of the KV cache for a given context
    length (2 bytes/element, f16):

        kv_bytes(ctx) = 2 * n_layer * ctx * n_kv_heads * head_dim * 2
    """

    n_layer: int = 0
    """Number of transformer blocks (llama.block_count)."""

    n_kv_heads: int = 0
    """Number of KV attention heads (attention.head_count_kv)."""

    head_dim: int = 0
    """Per-head key/value dimension (attention.key_length, or
    embedding_length / head_count when not stored explicitly)."""

    n_embd: int = 0
    """Embedding/hidden dimension (embedding_length)."""


# Architecture names known to be multimodal (vision-language) from GGUF metadata.
# These models can accept image inputs via the server when an mmproj is loaded.
_KNOWN_MULTIMODAL_ARCHS = frozenset({
    "lfm",           # LFM vision variants (original LFM)
    "lfm2",          # LFM2 and LFM2.5 vision variants
    "llava",         # LLaVA family
    "llava-llama3",  # LLaVA-Llama3
    "bakllava",      # BAKLLaVA
    "moondream",     # Moondream
    "minicpmv",      # MiniCPM-V
    "qwen2-vl",      # Qwen2-VL
    "qwen2vl",       # Qwen2-VL (alternate naming)
    "qwen2.5-vl",    # Qwen2.5-VL
    "florence2",     # Florence-2
    "paligemma",     # PaliGemma
    "internvl",      # InternVL
    "internvl2",     # InternVL2
    "deepseek-vl",   # DeepSeek-VL
    "deepseek-vl2",  # DeepSeek-VL2
    "phi3-v",        # Phi-3-vision
    "phi3v",         # Phi-3-vision (alternate)
    "xcomposer",     # XComposer
    "xcomposer2",    # XComposer2
})

# Architecture names that ARE grounding/detection models.
_KNOWN_GROUNDING_ARCHS = frozenset({
    "locateanything",
    "groundingdino",
    "florence2",  # Florence-2 can do both VLM and grounding
})


def _read_architecture(model_path: str, max_header_bytes: int = 16384) -> Optional[str]:
    """Read the 'general.architecture' metadata key from a GGUF file.

    Only reads the first `max_header_bytes` bytes of the file (typically
    well under 16 KB for metadata).
    """
    if not os.path.isfile(model_path):
        _logger.warning("Model file not found: %s", model_path)
        return None

    try:
        with open(model_path, "rb") as f:
            header = f.read(max_header_bytes)

        if len(header) < 16:
            _logger.warning("GGUF file too small: %s", model_path)
            return None

        _ver, _tc, _kc, _off, metadata = _read_gguf_header(header)
        arch = metadata.get("general.architecture")
        if arch is not None:
            return str(arch).lower()
        _logger.warning("No 'general.architecture' in GGUF metadata: %s", model_path)
        return None
    except Exception as e:
        _logger.warning("Failed to read GGUF header from %s: %s", model_path, e)
        return None


def read_model_context_size(model_path: str, max_header_bytes: int = 16384) -> Optional[int]:
    """Read the model's training context size (n_ctx_train) from GGUF metadata.

    The llama.cpp server enforces the model's native training context length
    and caps all KV cache slots at that value regardless of the --ctx-size
    CLI parameter.  This function reads the authoritative limit so the API
    can configure the engine correctly for each model.

    Args:
        model_path: Absolute path to the .gguf model file.
        max_header_bytes: Max bytes to read from the file header.

    Returns:
        The training context size in tokens, or None if it cannot be read.
    """
    if not os.path.isfile(model_path):
        _logger.warning("Model file not found: %s", model_path)
        return None

    try:
        with open(model_path, "rb") as f:
            header = f.read(max_header_bytes)

        if len(header) < 16:
            _logger.warning("GGUF file too small: %s", model_path)
            return None

        _ver, _tc, _kc, _off, metadata = _read_gguf_header(header)

        # Architecture-specific key takes priority (e.g. llama.context_length)
        arch = metadata.get("general.architecture")
        if arch:
            arch_key = f"{str(arch).lower()}.context_length"
            ctx = metadata.get(arch_key)
            if ctx is not None:
                return int(ctx)

        # Fallback: search any key ending in .context_length
        for key, val in metadata.items():
            if key.endswith(".context_length") and val is not None:
                return int(val)

        _logger.warning(
            "No context_length metadata in GGUF header: %s", model_path
        )
        return None
    except Exception as e:
        _logger.warning(
            "Failed to read context size from %s: %s", model_path, e
        )
        return None


def read_model_kv_params(
    model_path: str, max_header_bytes: int = 262144
) -> Optional[ModelKVParams]:
    """Read the KV-cache geometry of a model from GGUF metadata.

    Reads ``block_count``, ``attention.head_count_kv`` and the per-head
    key/value dimension (``attention.key_length``, falling back to
    ``embedding_length / attention.head_count``) so callers can estimate
    KV-cache memory for arbitrary context lengths.

    Args:
        model_path: Absolute path to the .gguf model file.
        max_header_bytes: Max bytes to read from the file header.  Larger
            than the context-size reader because some GGUFs store the
            tokenizer arrays after the architecture params.

    Returns:
        A ModelKVParams instance, or None when the metadata is unavailable.
    """
    if not os.path.isfile(model_path):
        _logger.warning("Model file not found: %s", model_path)
        return None

    try:
        with open(model_path, "rb") as f:
            header = f.read(max_header_bytes)

        if len(header) < 16:
            _logger.warning("GGUF file too small: %s", model_path)
            return None

        _ver, _tc, _kc, _off, metadata = _read_gguf_header(header)

        arch = metadata.get("general.architecture")
        if not arch:
            _logger.warning(
                "No 'general.architecture' in GGUF metadata: %s", model_path
            )
            return None
        arch_key = str(arch).lower()
        prefix = f"{arch_key}."

        def _get(name: str) -> Optional[int]:
            val = metadata.get(prefix + name)
            return int(val) if isinstance(val, (int, float)) else None

        n_layer = _get("block_count") or 0
        n_kv_heads = _get("attention.head_count_kv") or 0
        n_head = _get("attention.head_count") or 0
        n_embd = _get("embedding_length") or 0
        head_dim = _get("attention.key_length") or _get("attention.value_length") or 0
        if head_dim <= 0 and n_embd > 0 and n_head > 0:
            head_dim = n_embd // n_head

        # Some architectures (e.g. LFM2.5 hybrid attention) store
        # head_count_kv as a PER-LAYER array ([0,0,8,0,0,8,...]) where 0
        # marks layers without KV attention.  A scalar read yields None and
        # the RAM-aware context cap is then skipped — the model loads with
        # its full native window (up to 128k) and OOMs the KV cache at
        # startup.  Flatten the array: max non-zero entry = KV heads, count
        # of non-zero entries = the effective KV layer count.
        if not n_kv_heads:
            _kv_arr = metadata.get(prefix + "attention.head_count_kv")
            if isinstance(_kv_arr, (list, tuple)):
                _kv_nz = [
                    int(v) for v in _kv_arr
                    if isinstance(v, (int, float)) and int(v) > 0
                ]
                if _kv_nz:
                    n_kv_heads = max(_kv_nz)
                    n_layer = len(_kv_nz)

        if not (n_layer and n_kv_heads and head_dim):
            _logger.warning(
                "Incomplete KV geometry in GGUF metadata for %s "
                "(layer=%d, kv_heads=%d, head_dim=%d)",
                model_path, n_layer, n_kv_heads, head_dim,
            )
            return None

        return ModelKVParams(
            n_layer=n_layer, n_kv_heads=n_kv_heads, head_dim=head_dim, n_embd=n_embd,
        )
    except Exception as e:
        _logger.warning("Failed to read KV params from %s: %s", model_path, e)
        return None


def is_moe_model(model_path: str, max_header_bytes: int = 262144) -> bool:
    """Detect whether a GGUF model uses a Mixture-of-Experts (MoE) layout.

    MoE GGUFs expose an arch-specific ``*.expert_count`` metadata key (e.g.
    ``qwen3moe.expert_count``); a value > 0 is authoritative.  As a fallback,
    an architecture name containing "moe" (``qwen3moe``, ``hunyuan-moe``,
    ...) also classifies as MoE.  Dense models return False.

    Returns False when the metadata cannot be read (missing file, truncated
    header) so callers can safely default to dense-model behavior.
    """
    if not os.path.isfile(model_path):
        _logger.warning("Model file not found: %s", model_path)
        return False

    try:
        with open(model_path, "rb") as f:
            header = f.read(max_header_bytes)

        if len(header) < 16:
            return False

        _ver, _tc, _kc, _off, metadata = _read_gguf_header(header)

        # Explicit expert count is authoritative (any architecture prefix).
        for key, val in metadata.items():
            if key.endswith(".expert_count"):
                try:
                    if int(val) > 0:
                        return True
                except (TypeError, ValueError):
                    pass

        # Fallback: architecture name carries the MoE marker.
        arch = metadata.get("general.architecture")
        if arch and "moe" in str(arch).lower():
            return True
        return False
    except Exception as e:
        _logger.warning("Failed to detect MoE layout from %s: %s", model_path, e)
        return False


def estimate_kv_cache_bytes(
    n_ctx: int, kv: ModelKVParams, bytes_per_elem: int = 2
) -> int:
    """Estimate the KV-cache size in bytes for a given context length.

    CPU backends default to f16 KV cache (2 bytes/element); K and V each
    store ``n_layer * ctx * n_kv_heads * head_dim`` elements.
    """
    if not n_ctx or not kv or not (kv.n_layer and kv.n_kv_heads and kv.head_dim):
        return 0
    return 2 * kv.n_layer * n_ctx * kv.n_kv_heads * kv.head_dim * bytes_per_elem


def fit_context_to_ram(
    native_ctx: int,
    model_path: str,
    kv: ModelKVParams,
    available_ram: Optional[int] = None,
    safety_ratio: float = 0.8,
    reserved_bytes: int = 768 * 1024 * 1024,
    moe_resident_ratio: float = 0.2,
) -> Tuple[int, bool]:
    """Cap a model's native context to what fits in available RAM.

    When the native context would exceed the budget, the largest context that
    fits is returned (snapped down to a multiple of 512 tokens).  The budget
    is ``available_ram * safety_ratio`` minus a fixed reserve, minus the
    resident weights when those do not fit in RAM at all.

    MoE models memory-map their weights and keep the expert tensors OFF the
    resident set (--cpu-moe / mmap page-in): only the attention/shared layers
    + KV cache occupy RAM.  Counting the whole GGUF file here shrinks every
    MoE window to the degenerate floor even when plenty of RAM is free, so
    MoE models contribute ``file_size * moe_resident_ratio`` instead.

    A model whose weights fit in available RAM is memory-mapped, so the
    context is the only incremental cost and the KV cache is budgeted against
    the free RAM alone.  A constrained machine therefore gets the largest
    window it can host instead of a fixed 1024-token floor.

    Returns ``(effective_ctx, capped)``.  ``capped`` is False when the
    native context fits (or when the estimate cannot be computed — the
    caller then keeps the native value).
    """
    if available_ram is None:
        try:
            import psutil
            available_ram = psutil.virtual_memory().available
        except Exception:
            _logger.debug("Available RAM lookup failed — skipping RAM cap")
            return native_ctx, False

    model_bytes = (
        os.path.getsize(model_path) if model_path and os.path.isfile(model_path) else 0
    )
    per_token = estimate_kv_cache_bytes(1, kv)
    if per_token <= 0:
        return native_ctx, False

    try:
        _moe = is_moe_model(model_path) if model_path else False
    except Exception:
        _moe = False
    resident = int(model_bytes * moe_resident_ratio) if _moe else model_bytes

    usable = int(available_ram * safety_ratio)
    if resident <= available_ram:
        # The weights fit, and llama.cpp mmaps them read-only: their clean
        # pages are evictable, so a WIDER CONTEXT costs the KV cache and
        # nothing else.  Budget that cache against the RAM genuinely free.
        # Charging the weights here too made the window SHRINK as more RAM
        # freed up, and collapsed it to the 1024-token floor whenever the
        # estimate dipped below zero — on a box where the model loads and runs
        # fine.  ponytail: optimistic by design; if the KV cache still will
        # not allocate, the crash-retry ladder halves the window.
        budget = usable - reserved_bytes
    else:
        # The weights do not fit in RAM at all, so charge them and expect the
        # budget to be negative (the model cannot run comfortably anyway).
        budget = usable - resident - reserved_bytes
    if budget <= 0:
        # Nothing is left for a KV cache (or the weights do not fit in RAM at
        # all and the model cannot run): the window cannot be widened.  Floor
        # at 1024 (or the native context if smaller) — the previous
        # "minimal 512-token fallback" made EVERY real prompt a guaranteed
        # context overflow while saving almost no memory.  Warn loudly so the
        # user knows the window is constrained.
        _floor = min(native_ctx, 1024)
        _logger.warning(
            "RAM budget <= 0 (avail=%d, model=%d, reserve=%d) — "
            "capping effective context to the %d-token floor; prompts "
            "larger than this will be rejected. Free memory or set "
            "llamacpp_context_size explicitly for a larger window.",
            available_ram, model_bytes, reserved_bytes, _floor,
        )
        return _floor, _floor < native_ctx

    max_by_ram = int(budget / per_token)
    effective = min(native_ctx, max_by_ram)
    effective = max(512, (effective // 512) * 512)
    return effective, effective < native_ctx


def classify_model(
    model_path: str,
    mmproj_path: Optional[str] = None,
) -> ModelCapability:
    """Determine the capability of a GGUF model from its file metadata.

    Args:
        model_path: Absolute path to the .gguf model file.
        mmproj_path: Absolute path to an associated mmproj .gguf file, if any.

    Returns:
        A ModelCapability instance.
    """
    has_mmproj = bool(mmproj_path and os.path.isfile(mmproj_path))
    basename = os.path.basename(model_path).lower()

    # Try GGUF metadata first
    architecture = _read_architecture(model_path)
    context_size = read_model_context_size(model_path) or 0
    is_moe = is_moe_model(model_path)
    if not architecture:
        # Fallback: guess from filename for well-known models
        architecture = _guess_architecture_from_filename(basename)

    # Determine model type
    is_grounding_arch = architecture in _KNOWN_GROUNDING_ARCHS
    is_grounding_name = "locateanything" in basename

    if is_grounding_arch or is_grounding_name:
        return ModelCapability(
            model_type="grounding",
            architecture=architecture or "locateanything",
            has_mmproj=has_mmproj,
            needs_special_tokens=True,
            needs_mtmd_cli=True,
            context_size=context_size,
            is_moe=is_moe,
        )

    is_multimodal_arch = architecture in _KNOWN_MULTIMODAL_ARCHS
    if has_mmproj and is_multimodal_arch:
        # AUTOMATIC engine detection (no model-name hardcoding): the
        # llama-server initializes the full attention geometry at model
        # load, which HANGS for GGUFs whose metadata lacks it (observed:
        # "incomplete KV geometry" + mmproj stall, order-dependent).  A
        # one-shot llama-mtmd-cli process loads the model without the
        # server's attention init, so ANY multimodal GGUF with incomplete
        # KV metadata routes there automatically — the engine follows the
        # model's own metadata, never the chain's execution order.
        _kv_params = read_model_kv_params(model_path)
        return ModelCapability(
            model_type="multimodal",
            architecture=architecture or "unknown",
            has_mmproj=True,
            needs_special_tokens=False,
            needs_mtmd_cli=_kv_params is None,
            context_size=context_size,
            is_moe=is_moe,
        )

    # mmproj found but architecture is NOT multimodal — the mmproj
    # belongs to a DIFFERENT model in the same directory.  Do NOT
    # force it onto this text-only model (n_embd mismatch will crash
    # the server).
    if has_mmproj:
        _logger.warning(
            "mmproj file found but model architecture '%s' is not multimodal "
            "— ignoring incompatible mmproj",
            architecture,
        )

    return ModelCapability(
        model_type="text",
        architecture=architecture or "unknown",
        has_mmproj=False,
        needs_special_tokens=False,
        needs_mtmd_cli=False,
        context_size=context_size,
        is_moe=is_moe,
    )


def _guess_architecture_from_filename(basename: str) -> Optional[str]:
    """Fallback: guess architecture from model filename when GGUF metadata is
    unavailable."""
    name_lower = basename.lower()

    if "locateanything" in name_lower:
        return "locateanything"
    if "lfm" in name_lower:
        return "lfm"
    if "qwen" in name_lower:
        return "qwen2"
    if "smollm" in name_lower:
        return "llama"  # SmolLM uses llama architecture
    if "llama" in name_lower:
        return "llama"
    if "phi" in name_lower:
        return "phi3"
    if "deepseek" in name_lower:
        return "deepseek"
    if "mistral" in name_lower:
        return "mistral"

    return None
