"""inprocess_transport.py — Direct in-process LLM transport for exported agents.

The exported PyInstaller agent is a frozen artifact with no dynamic model
switching, so it needs NO FastAPI server: all LLM inference runs in-process
via ``AI/llama_cpp_engine.LlamaCppEngine`` (methods ``generate``,
``generate_stream``, ``chat``, ``generate_mtmd`` — all callable in-process).

This module is the process-wide seam between the workflow executor and the
engine.  Direct mode is active when the environment variable
``ARROW_DIRECT_ENGINE=1`` (set by ``agent_standalone_entry.py`` at startup).

Semantics mirror the FastAPI ``/llamacpp/generate`` endpoint:

  - model name -> path resolution from bundled ``AI/config.json``
  - images -> multimodal chat (mtmd-cli for grounding models)
  - ``max_tokens`` clamped to ``context_size - 128`` when it would overflow
  - failures raise :class:`LLMTransportError` — never return empty dicts

Usage from ``input_ops._reason_input_via_llm`` (agent-modifiable inputs)::

    if inprocess_transport.is_direct_mode():
        result = inprocess_transport.chat(
            messages=messages, max_tokens=256, temperature=0.1,
        )
        response_text = (result.get("response") or "").strip()
    else:
        # ... existing HTTP path ...

Usage from the consolidator (``AI/comorag_engine.ProbeBasedConsolidator``):
the consolidator's model calls go through ``generate``/``chat`` in direct
mode; its embeddings are local sentence-transformers (never Ollama).
"""

import atexit
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AI.inprocess_transport")


class LLMTransportError(RuntimeError):
    """Raised when the LLM transport (in-process engine or HTTP API) fails.

    Callers must catch this and mark the node FAILED with the message —
    a transport failure is never converted into a successful empty response.
    """


# ---------------------------------------------------------------------------
# Direct-mode detection
# ---------------------------------------------------------------------------

def is_direct_mode() -> bool:
    """True when the process runs in direct engine mode (ARROW_DIRECT_ENGINE=1).

    Set by ``agent_standalone_entry.py`` before the UI starts.  The full
    app never sets this variable and keeps its HTTP API path untouched.
    """
    return os.environ.get("ARROW_DIRECT_ENGINE") == "1"


# ---------------------------------------------------------------------------
# Engine lifecycle (owned by agent_standalone_entry.py)
# ---------------------------------------------------------------------------

_engine: Optional[Any] = None
_engine_lock = threading.Lock()


def _resolve_model_path(model_name: str) -> str:
    """Resolve a model name (basename or path) to an existing file path.

    Absolute paths are returned as-is.  Basenames are searched under the
    bundled models directories (exe_dir/models, _MEIPASS/AI/models, and the
    source-tree AI/models dir).  If nothing matches, the name is returned
    unchanged so the engine surfaces the missing-file error itself.
    """
    if not model_name:
        return ""
    if os.path.isabs(model_name) and os.path.exists(model_name):
        return model_name
    base = os.path.basename(model_name)
    candidates: List[str] = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for prefix in (
            "",
            "_internal",
            "AI",
            os.path.join("_internal", "AI"),
            os.path.join("_internal", "LoOper", "AI"),
        ):
            candidates.append(os.path.join(exe_dir, prefix, "models", base))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            for sub in ("", "LoOper"):
                candidates.append(os.path.join(meipass, sub, "AI", "models", base))
    try:
        from AI.config_loader import get_models_dir
        candidates.append(os.path.join(get_models_dir(), base))
    except Exception:
        pass
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return model_name


def get_engine() -> Any:
    """Return the process-wide ``LlamaCppEngine``, created once from bundled
    ``AI/config.json`` (LLAMA_CPP section).  Thread-safe."""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        cfg: Dict[str, Any] = {}
        try:
            from AI.config_loader import get_llamacpp_config
            cfg = get_llamacpp_config() or {}
        except Exception as exc:
            logger.warning("Failed to read LLAMA_CPP config: %s", exc)
        mmproj = (cfg.get("mmproj_path") or "").strip()
        engine_config = {
            "model_path": _resolve_model_path(cfg.get("model_path", "") or ""),
            "mmproj_path": _resolve_model_path(mmproj) if mmproj else "",
            "server_port": int(cfg.get("server_port", 8081) or 8081),
            "gpu_layers": int(cfg.get("gpu_layers", 0) or 0),
            "threads": int(cfg.get("threads", -1) or -1),
            "context_size": int(cfg.get("context_size", 0) or 0),
            "batch_size": int(cfg.get("batch_size", 2048) or 2048),
        }
        from AI.llama_cpp_engine import LlamaCppEngine
        _engine = LlamaCppEngine(engine_config)
        return _engine


def probe() -> None:
    """Perform a real 1-token generation against the started server.

    A health check only proves the server is listening; a real completion
    proves the model loaded and POSTs succeed.  Raises :class:`LLMTransportError`
    when the probe fails.
    """
    engine = get_engine()
    if not engine.is_running:
        raise LLMTransportError(
            "Engine is not running — call ensure_started() before probing"
        )
    try:
        result = engine.generate("ping", max_tokens=1, temperature=0.1)
    except Exception as exc:
        raise LLMTransportError(f"Engine probe failed: {exc}")
    if result.get("error"):
        raise LLMTransportError(f"Engine probe failed: {result['error']}")


def ensure_started(max_restarts: int = 2) -> None:
    """Start the engine (if needed) and verify it with a real 1-token probe.

    On probe failure the server is stopped and restarted, up to
    ``max_restarts`` times.  On exhaustion raises :class:`LLMTransportError`
    — callers must surface a visible error instead of continuing with an
    empty response.
    """
    engine = get_engine()
    attempt = 0
    while True:
        try:
            if not engine.is_running:
                if not engine.start_server(blocking=True):
                    raise LLMTransportError("Failed to start llama.cpp server")
            probe()
            logger.info(
                "Direct engine probe OK (model: %s, port: %d)",
                engine.model_path, engine.server_port,
            )
            return
        except LLMTransportError:
            if attempt >= max_restarts:
                logger.error(
                    "Direct engine failed after %d restart attempt(s) — giving up",
                    attempt + 1,
                )
                raise
            attempt += 1
            logger.warning(
                "Direct engine start/probe failed (attempt %d/%d) — restarting",
                attempt + 1, max_restarts + 1,
            )
            try:
                engine.stop_server()
            except Exception:
                pass


def _ensure_engine_running() -> None:
    """Defensive single-attempt start when the engine is not already running."""
    engine = get_engine()
    if engine.is_running:
        return
    if not engine.start_server(blocking=True):
        raise LLMTransportError("Failed to start llama.cpp server (direct engine)")


def shutdown() -> None:
    """Stop the engine so no llama-server.exe child survives process exit."""
    global _engine
    if _engine is not None:
        try:
            _engine.stop_server()
            logger.info("Direct engine stopped")
        except Exception as exc:
            logger.error("Failed to stop direct engine: %s", exc)
        finally:
            _engine = None


atexit.register(shutdown)


# ---------------------------------------------------------------------------
# Inference calls (mirror the FastAPI /llamacpp/generate endpoint)
# ---------------------------------------------------------------------------

def _to_data_url(image: str) -> str:
    """Convert an image (file path, data URL, or raw base64) to a data URL."""
    if not image:
        return image
    if image.startswith("data:"):
        return image
    if os.path.exists(image):
        import base64
        try:
            with open(image, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return f"data:image/png;base64,{encoded}"
        except Exception as exc:
            raise LLMTransportError(f"Failed to read image {image}: {exc}")
    # Assume raw base64 payload
    return f"data:image/png;base64,{image}"


def _clamp_max_tokens(engine: Any, max_tokens: Any) -> Any:
    """Clamp max_tokens to context_size - 128 when it would overflow.

    A context_size of 0 means "model's native training context" (unknown
    here), so no clamp can be pre-computed — the value is passed through.
    """
    if isinstance(max_tokens, int) and max_tokens > 0:
        context_size = int(getattr(engine, "context_size", 0) or 0)
        if context_size <= 0:
            return max_tokens
        clamp = max(1, context_size - 128)
        if max_tokens > clamp:
            logger.info(
                "Clamped max_tokens %d -> %d (context %d - 128)",
                max_tokens, clamp, context_size,
            )
            return clamp
    return max_tokens


def _apply_engine_params(engine: Any, kwargs: Dict[str, Any]) -> None:
    """Apply per-request engine params (gpu_layers/threads/context_size).

    The shared engine is normally configured once from ``AI/config.json``.
    When a caller (e.g. the workflow executor) explicitly passes different
    values, they are written into the engine and the running server is
    restarted so the new values take effect.  Values of ``None`` or unset
    leave the current configuration untouched.
    """
    changes: Dict[str, Any] = {}
    for attr in ("gpu_layers", "threads", "context_size"):
        val = kwargs.get(attr)
        if val is None:
            continue
        try:
            val = int(val)
        except (TypeError, ValueError):
            continue
        if val != int(getattr(engine, attr, 0)):
            changes[attr] = val
    if not changes:
        return
    for attr, val in changes.items():
        setattr(engine, attr, val)
    if engine.is_running:
        logger.info(
            "Engine params changed (%s) — restarting server", changes
        )
        try:
            engine.stop_server()
        except Exception as exc:
            logger.warning("Failed to stop engine during param change: %s", exc)


def _build_messages(prompt: str, system: str, images: List[str]) -> List[Dict[str, Any]]:
    """Build the messages list the same way the API endpoint does."""
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    if images:
        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt or ""}]
        for img in images:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _to_data_url(img)},
                }
            )
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": prompt or ""})
    return messages


def generate(prompt: str, **kwargs: Any) -> Dict[str, Any]:
    """Mirror ``POST /llamacpp/generate`` in-process.

    kwargs (same names as the endpoint's ChatRequest): ``model``, ``system``,
    ``messages``, ``max_tokens``, ``temperature``, ``stream``, ``images``
    (file paths or base64), ``gpu_layers``, ``threads``, ``context_size``.

    Returns the endpoint-shaped dict with keys ``response``, ``model``,
    ``_debug``, ``created_at``, ``done``, ``usage``.  Raises
    :class:`LLMTransportError` on any engine failure — never an empty dict.
    """
    engine = get_engine()
    _apply_engine_params(engine, kwargs)
    _ensure_engine_running()
    max_tokens = _clamp_max_tokens(engine, kwargs.get("max_tokens"))
    temperature = kwargs.get("temperature", 0.7)
    images = list(kwargs.get("images") or [])
    system = kwargs.get("system") or ""
    messages = kwargs.get("messages") or []
    if not messages:
        messages = _build_messages(prompt, system, images)
    if kwargs.get("raw_completion") and not images:
        # Raw /completion (no chat template): used by probe generation so
        # the model answers immediately instead of emitting a reasoning
        # block that the chat path would strip, yielding an empty response.
        result = engine.generate(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=kwargs.get("top_p", 0.9),
            repeat_penalty=kwargs.get("repeat_penalty", 1.1),
        )
    else:
        result = engine.chat(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=kwargs.get("top_p", 0.9),
            repeat_penalty=kwargs.get("repeat_penalty", 1.1),
        )
    if result.get("error"):
        raise LLMTransportError(str(result["error"]))
    resp_text = result.get("response", "") or ""
    return {
        "response": resp_text,
        "model": result.get("model", kwargs.get("model", "")),
        "_debug": {
            "direct_engine": True,
            "images_received": bool(images),
            "images_processed": bool(images),
            "multimodal_messages": any(
                isinstance(m.get("content"), list) for m in messages
            ),
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "done": result.get("done", True),
        "truncated": result.get("truncated", False),
        "usage": result.get("usage", {}),
    }


def chat(messages: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
    """Chat completion through the in-process engine (endpoint-shaped dict).

    Raises :class:`LLMTransportError` on any engine failure.
    """
    engine = get_engine()
    _apply_engine_params(engine, kwargs)
    _ensure_engine_running()
    max_tokens = _clamp_max_tokens(engine, kwargs.get("max_tokens"))
    result = engine.chat(
        messages=messages,
        max_tokens=max_tokens,
        temperature=kwargs.get("temperature", 0.7),
        top_p=kwargs.get("top_p", 0.9),
        repeat_penalty=kwargs.get("repeat_penalty", 1.1),
    )
    if result.get("error"):
        raise LLMTransportError(str(result["error"]))
    resp_text = result.get("response", "") or ""
    return {
        "response": resp_text,
        "model": result.get("model", kwargs.get("model", "")),
        "_debug": {"direct_engine": True},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "done": result.get("done", True),
        "truncated": result.get("truncated", False),
        "usage": result.get("usage", {}),
    }


def generate_stream(prompt: str, **kwargs: Any):
    """Streaming generation through the in-process engine.

    Raises :class:`LLMTransportError` before the first chunk when the engine
    cannot be used; afterwards delegates to the engine's generator.
    """
    engine = get_engine()
    _apply_engine_params(engine, kwargs)
    _ensure_engine_running()
    max_tokens = _clamp_max_tokens(engine, kwargs.get("max_tokens"))
    return engine.generate_stream(
        prompt,
        max_tokens=max_tokens,
        temperature=kwargs.get("temperature", 0.7),
        top_p=kwargs.get("top_p", 0.9),
        repeat_penalty=kwargs.get("repeat_penalty", 1.1),
    )
