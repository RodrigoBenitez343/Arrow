"""embedding_server.py -- Lazy process-wide llama.cpp embedding server.

Serves embedding vectors via a SEPARATE llama.cpp engine running an
embedding GGUF (``embeddinggemma-300M-Q8_0.gguf`` by default) in
``--embeddings`` mode on its own free port.  The generation server keeps
running its own model on its own port -- the two never interfere because
``start_server`` now kills only the process holding its own port.

The engine is a BURST resource, exactly like the Laya scorer: it is started
lazily on first use, self-unloads once the RAG/consolidation burst that used
it is done (``LLAMA_EMBED_KEEP_ALIVE_SECONDS``, default 0 — negative pins it
resident), and the generation ``start_server`` releases it explicitly before
loading its own model, so only ONE llama.cpp model is resident at a time
(mandatory on old, low-RAM devices).  ``shutdown()`` stops it at exit
(registered via atexit and callable from the standalone-agent quit path).
Every failure returns ``None`` so callers (e.g. ``ProbeBasedConsolidator``)
fall back to lexical ranking instead of failing the node.
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
# REENTRANT, deliberately: a cold start holds this lock while it calls
# ``LlamaCppEngine.start_server``, which releases every sibling burst resource
# before sizing its own model -- including THIS one (``release()``).  With a
# plain ``Lock`` the same thread re-acquired it and deadlocked forever, silently:
# the log then ends at the last ComoRAG probe line and nothing else ever happens
# (observed: 27 minutes of total silence, no ``Enabled --embeddings`` line).  A
# cold start reports ``_emb_engine`` as None to ``release()``, so it is a no-op.
_EMBED_LOCK = threading.RLock()
_EMBED_PORT = None
_BATCH_SIZE = 64
# Health checks are throttled: a live embedding POST in flight must never be
# killed by another caller's health GET timing out mid-batch.
_LAST_HEALTH_CHECK = 0.0
_HEALTH_CHECK_TTL = 5.0
# Burst lifecycle (mirrors AI/laya_client.py): the embedding server is released
# by the generation start_server and self-unloads once its burst is done.
_EMB_SEQ = 0          # bumped per embed request; the idle unloader compares it
_EMB_INFLIGHT = 0     # embeds currently in flight — never killed mid-batch
_EMB_KEEP_ALIVE_SECONDS = float(
    os.getenv("LLAMA_EMBED_KEEP_ALIVE_SECONDS", "0") or 0
)


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


def release(wait: float = 5.0) -> bool:
    """Stop the embedding server NOW, freeing its RAM; next embed relaunches it.

    Called by the generation ``start_server`` (ONE model per memory pool) and
    by the idle unloader once a RAG/consolidation burst ends.  An embed in
    flight is never killed out from under its caller: wait up to *wait* s for
    it, then give up and return False (the server stays up; the caller keeps
    its vectors).  The port is kept so the next start reuses it and its
    ``_ensure_port_free`` clears any leftover — never a second embed server.
    """
    global _emb_engine
    deadline = time.time() + max(0.0, float(wait or 0.0))
    while _EMB_INFLIGHT > 0 and time.time() < deadline:
        time.sleep(0.02)
    if _EMB_INFLIGHT > 0:
        return False
    with _EMBED_LOCK:
        if _EMB_INFLIGHT > 0:
            return False  # an embed slipped in while we waited — keep the server
        engine = _emb_engine
        _emb_engine = None
    if engine is None:
        return False
    try:
        engine.stop_server()
        _emb_logger.info(
            "Embedding server released (load -> use -> unload per burst)"
        )
        return True
    except Exception as exc:
        _emb_logger.warning("Embedding server release failed: %s", exc)
        return False


def _unload_after_idle(seq_at_schedule: int) -> None:
    """Stop the embedding server once its burst is done (seq-guarded)."""
    if _EMB_KEEP_ALIVE_SECONDS > 0:
        time.sleep(_EMB_KEEP_ALIVE_SECONDS)
    if _EMB_SEQ != seq_at_schedule or _EMB_INFLIGHT > 0:
        return  # a newer embed is using the server — keep it loaded
    release(wait=0.0)


def _schedule_unload(seq_at_schedule: int) -> None:
    """Arm the idle unloader after every completed embed request."""
    if _EMB_KEEP_ALIVE_SECONDS < 0:
        return  # operator pinned the embedding model resident
    threading.Thread(
        target=_unload_after_idle, args=(seq_at_schedule,),
        daemon=True, name="embed-idle-unloader",
    ).start()


def embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed *texts* (batched) via the llama.cpp embedding server.

    Returns ``None`` on ANY failure -- never raises.  Callers fall back to
    lexical ranking or another embedder.  The burst is released (idle unload)
    once the call returns, so the generation model can take the pool.
    """
    global _EMB_SEQ, _EMB_INFLIGHT
    if not texts:
        return []
    _EMB_SEQ += 1
    _seq = _EMB_SEQ
    _EMB_INFLIGHT += 1
    try:
        engine = get_embed_client()
        if engine is None:
            return None
        results: List[List[float]] = []
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
        return results
    except Exception as exc:
        _emb_logger.debug("Embedding failed: %s", exc)
        return None
    finally:
        _EMB_INFLIGHT -= 1
        _schedule_unload(_seq)


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
