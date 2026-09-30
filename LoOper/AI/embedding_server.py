"""embedding_server.py -- Lazy process-wide llama.cpp embedding server.

Serves embedding vectors via a SEPARATE llama.cpp engine running an
embedding GGUF (``embeddinggemma-300M-Q8_0.gguf`` by default) in
``--embeddings`` mode on its own free port.  The generation server keeps
running its own model on its own port -- the two never interfere because
``start_server`` now kills only the process holding its own port.

The engine is started lazily on first use and kept alive for the whole
process; ``shutdown()`` stops it at exit (registered via atexit and
callable from the standalone-agent quit path).  Every failure returns
``None`` so callers (e.g. ``ProbeBasedConsolidator``) fall back to
lexical ranking instead of failing the node.
"""

import atexit
import logging
import os
import socket
import threading
import time
from typing import List, Optional

_emb_logger = logging.getLogger("api.llamacpp.embedding")

_emb_engine = None
_EMBED_LOCK = threading.Lock()
_EMBED_PORT = None
_BATCH_SIZE = 64
# Health checks are throttled: a live embedding POST in flight must never be
# killed by another caller's health GET timing out mid-batch.
_LAST_HEALTH_CHECK = 0.0
_HEALTH_CHECK_TTL = 5.0


def _find_free_port(start: int = 8090, max_attempts: int = 100) -> int:
    """Find an available TCP port starting from *start*."""
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start  # fallback; start_server surfaces the failure


def _resolve_embedding_model_path() -> str:
    """Resolve the embedding GGUF path from config (relative -> models dir)."""
    name = ""
    try:
        from AI.config_loader import get_llamacpp_config
        cfg = get_llamacpp_config() or {}
        name = (cfg.get("embedding_model_path") or "").strip()
    except Exception:
        pass
    if not name:
        name = "embeddinggemma-300M-Q8_0.gguf"
    if os.path.isabs(name):
        return name
    try:
        from AI.config_loader import get_models_dir
        return os.path.normpath(os.path.join(get_models_dir(), name))
    except Exception:
        return os.path.normpath(
            os.path.join(os.getcwd(), "LoOper", "AI", "models", name)
        )


def get_embed_client():
    """Return the process-wide embedding engine, starting it lazily.

    Returns ``None`` when the model file is missing or the server cannot
    start -- callers must fall back to another embedder.
    """
    global _emb_engine, _EMBED_PORT, _LAST_HEALTH_CHECK
    with _EMBED_LOCK:
        if _emb_engine is not None:
            # Throttled health-check; restart only on a confirmed stale
            # server (crashed/orphaned).  Without the TTL, a slow in-flight
            # batch makes another caller's health GET time out and kill the
            # shared child mid-request.
            now = time.time()
            if now - _LAST_HEALTH_CHECK < _HEALTH_CHECK_TTL:
                return _emb_engine
            _LAST_HEALTH_CHECK = now
            try:
                import requests
                _r = requests.get(
                    f"{_emb_engine.server_url}/health", timeout=2
                )
                if _r.status_code == 200:
                    return _emb_engine
            except Exception:
                pass
            try:
                _emb_engine.stop_server()
            except Exception:
                pass
            _emb_engine = None
            _EMBED_PORT = None  # re-scan a fresh port on next start

        model_path = _resolve_embedding_model_path()
        if not os.path.exists(model_path):
            _emb_logger.warning("Embedding model not found: %s", model_path)
            return None

        if _EMBED_PORT is None:
            _EMBED_PORT = _find_free_port()

        from AI.llama_cpp_engine import LlamaCppEngine
        engine = LlamaCppEngine({
            "model_path": model_path,
            "server_port": _EMBED_PORT,
            "gpu_layers": 0,
            # Embedding workloads are memory-bandwidth-bound; cap the thread
            # pool so a second llama-server doesn't oversubscribe every core.
            "threads": 4,
            "context_size": 2048,
            # Physical batch must cover a single input in one pass (embedding
            # tasks can't be split).  The 512 default makes any input over
            # 512 tokens fail with an HTTP 500 "input too large" error.
            "batch_size": 2048,
            "ubatch_size": 2048,
            "embedding_mode": True,
        })
        try:
            if not engine.start_server(blocking=True):
                _emb_logger.error("Embedding server failed to start")
                _EMBED_PORT = None  # allow a fresh port on the next attempt
                try:
                    engine.stop_server()
                except Exception:
                    pass
                return None
        except Exception as exc:
            _emb_logger.error("Embedding server start error: %s", exc)
            _EMBED_PORT = None
            try:
                engine.stop_server()
            except Exception:
                pass
            return None

        _emb_engine = engine
        _emb_logger.info(
            "Embedding server ready (model=%s, port=%d)",
            os.path.basename(model_path), _EMBED_PORT,
        )
        return _emb_engine


def embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed *texts* (batched) via the llama.cpp embedding server.

    Returns ``None`` on ANY failure -- never raises.  Callers fall back to
    lexical ranking or another embedder.
    """
    if not texts:
        return []
    engine = get_embed_client()
    if engine is None:
        return None
    results: List[List[float]] = []
    try:
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i:i + _BATCH_SIZE]
            vectors = engine.embed(batch)
            if vectors is None or len(vectors) != len(batch):
                _emb_logger.warning(
                    "Embedding count mismatch in batch %d/%d",
                    i // _BATCH_SIZE + 1,
                    (len(texts) + _BATCH_SIZE - 1) // _BATCH_SIZE,
                )
                return None
            results.extend(vectors)
    except Exception as exc:
        _emb_logger.debug("Embedding failed: %s", exc)
        return None
    return results


def shutdown() -> None:
    """Stop the embedding server so no llama-server.exe child survives."""
    global _emb_engine, _EMBED_PORT
    if _emb_engine is not None:
        try:
            _emb_engine.stop_server()
            _emb_logger.info("Embedding server stopped")
        except Exception as exc:
            _emb_logger.error("Failed to stop embedding server: %s", exc)
        finally:
            _emb_engine = None
            _EMBED_PORT = None


atexit.register(shutdown)
