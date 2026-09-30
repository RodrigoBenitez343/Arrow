"""
tool_retriever.py — Cosine-similarity tool index for LLM routing.

Pre-computes sentence-transformer embeddings for all tool descriptions
so the LLM router only receives the top-K most relevant tools instead of
the full list (which would overflow a small model's context window).

Usage:
    retriever = ToolRetriever()
    retriever.build_index(tool_desc_map)   # one-time, pre-compute embeddings
    top_k_ids = retriever.retrieve(query)  # at inference time
"""

import hashlib
import json
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class _ServerEmbeddingModel:
    """Minimal duck-type of a sentence-transformer for ToolRetriever.

    Backed by the lazy llama.cpp embedding server (embeddinggemma GGUF) —
    no PyTorch, no huggingface.co.  ``encode()`` returns a normalized
    float32 matrix so the numpy cosine path in ``ToolRetriever.retrieve``
    keeps working unchanged.
    """

    def encode(self, texts, convert_to_tensor=False, **kwargs):
        try:
            import numpy as np
            from AI import embedding_server

            texts = list(texts) if isinstance(texts, (list, tuple)) else [texts]
            vectors = embedding_server.embed_texts(texts)
            if vectors is None or len(vectors) != len(texts):
                return None
            embs = np.asarray(vectors, dtype=np.float32)
            norms = np.linalg.norm(embs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            return embs / norms
        except Exception as exc:
            logger.warning("ServerEmbeddingModel encode failed: %s", exc)
            return None


class ToolRetriever:
    """Indexes tool descriptions and retrieves top-K by cosine similarity.

    Maintains a content-addressed cache so re-indexing is skipped when the
    tool descriptions haven't changed between calls.

    Thread-safe for read operations (retrieve) after build_index completes.
    """

    def __init__(self, model_name: str = "embeddinggemma-300M-Q8_0.gguf"):
        self._model_name = model_name
        self._model = None
        self._tool_ids: List[str] = []
        self._tool_embeddings = None
        self._tool_texts: List[str] = []
        self._cache_key: str = ""

    def _lazy_load_model(self):
        if self._model is not None:
            return
        try:
            from AI import embedding_server

            if embedding_server.get_embed_client() is None:
                raise RuntimeError("embedding server unavailable")
            self._model = _ServerEmbeddingModel()
        except Exception as e:
            logger.warning(
                "Failed to init embedding server for tool retrieval: %s", e
            )

    def build_index(self, tool_desc_map: Dict[str, str],
                    display_names: Optional[Dict[str, str]] = None) -> bool:
        """Pre-compute embeddings for every tool in *tool_desc_map*.

        The embedded text is the tool's DESCRIPTION — that is what ranking and
        the Router LLM decide on.  *display_names* (alias -> id) is used only
        as a non-empty fallback token for tools with no description; it never
        replaces or prefixes the description in the corpus.

        Returns True if the index was (re)built, False if the cached index
        is still current (tool_desc_map unchanged since last call).
        """
        # Content-addressed cache key: skip re-indexing when nothing changed
        try:
            raw = json.dumps(tool_desc_map, sort_keys=True, ensure_ascii=False)
        except Exception:
            raw = str(tool_desc_map)
        new_key = hashlib.md5(raw.encode("utf-8")).hexdigest()
        if new_key == self._cache_key and self._tool_embeddings is not None:
            return False

        if not tool_desc_map:
            self.clear()
            return True

        self._lazy_load_model()
        if self._model is None:
            # Embedding model unavailable (not installed or torch DLL failure).
            # Don't clear — store tool IDs as-is so retrieve_scored passes
            # through the top-K in declaration order (score 0.0).  The caller's
            # confidence ladder turns that into a bounded candidate list, never
            # a full-catalog dump.
            self._tool_ids = list(tool_desc_map.keys())
            self._tool_texts = list(tool_desc_map.values())
            self._tool_embeddings = None
            self._cache_key = new_key
            logger.warning(
                "ToolRetriever: embedding model unavailable — "
                "%d tools stored without embeddings (scored passthrough only)",
                len(self._tool_ids),
            )
            return True

        self._tool_ids = []
        self._tool_texts = []
        for tid, desc in tool_desc_map.items():
            desc = str(desc or "").strip()
            if desc:
                # Description-only text: ranking is decided by what the tool
                # does, never by its (arbitrary) alias/hex label.
                text = desc.replace("_", " ")
            else:
                # No description — fall back to the readable label so the
                # corpus has no empty entries.
                display = (display_names or {}).get(tid) or str(tid)
                text = f"{display}: [no description]"
            self._tool_ids.append(tid)
            self._tool_texts.append(text)

        if self._tool_texts:
            self._tool_embeddings = self._model.encode(
                self._tool_texts, convert_to_tensor=True
            )
        else:
            self._tool_embeddings = None

        self._cache_key = new_key
        logger.info(
            "ToolRetriever: indexed %d tools (model=%s, cache=%s)",
            len(self._tool_ids),
            self._model_name,
            new_key[:8],
        )
        return True

    def retrieve(self, query: str, top_k: int = 10, score_threshold: float = 0.0) -> List[str]:
        """Return up to *top_k* tool IDs ranked by cosine similarity, filtered by *score_threshold*.

        Thin wrapper over :meth:`retrieve_scored` that drops the scores.
        """
        return [tid for tid, _score in self.retrieve_scored(
            query, top_k=top_k, score_threshold=score_threshold,
        )]

    def retrieve_scored(self, query: str, top_k: int = 10,
                        score_threshold: float = 0.0) -> List:
        """Return up to *top_k* ``(tool_id, score)`` pairs ranked by cosine
        similarity, filtered by *score_threshold*.

        Only tools whose cosine similarity >= score_threshold are returned.
        Returns an empty list when no tools meet the threshold, the index is
        empty, or the model is not available.

        When the embedding model is unavailable, threshold <= 0 returns the
        first *top_k* tools in declaration order with score 0.0 (a bounded
        passthrough — the caller still decides what to do with them).
        """
        if not self._tool_ids:
            return []
        self._lazy_load_model()
        if self._model is None:
            # Embedding model unavailable: threshold <= 0 means "no filter"
            # (bounded to top_k).
            if score_threshold <= 0.0:
                return [(tid, 0.0) for tid in self._tool_ids[:top_k]]
            return []

        try:
            import numpy as np

            query_emb = self._model.encode(query)
            if query_emb is None or self._tool_embeddings is None:
                # Embeddings unavailable: threshold <= 0 means "no filter"
                # (bounded to top_k).
                if score_threshold <= 0.0:
                    return [(tid, 0.0) for tid in self._tool_ids[:top_k]]
                return []

            scores = (query_emb @ self._tool_embeddings.T)[0]

            # Sort all by score descending
            sorted_indices = np.argsort(scores)[::-1]

            # Threshold filter first, then top-K cap
            filtered = []
            score_parts = []
            for i in sorted_indices:
                idx = int(i)
                score = float(scores[idx])
                tid = self._tool_ids[idx]
                score_parts.append(f"{tid}={score:.4f}")
                if score >= score_threshold:
                    filtered.append((tid, score))
                    if len(filtered) >= top_k:
                        break

            if not filtered:
                logger.info(
                    "ToolRetriever: all %d tools below threshold %.2f [%s]",
                    len(self._tool_ids), score_threshold, ", ".join(score_parts),
                )
            else:
                logger.info(
                    "ToolRetriever: %d/%d tools kept (threshold=%.2f, top_k=%d) [%s]",
                    len(filtered), len(self._tool_ids), score_threshold, top_k,
                    ", ".join(score_parts),
                )
            return filtered
        except Exception as e:
            logger.warning("ToolRetriever.retrieve_scored failed: %s", e)
            return []

    def clear(self):
        """Drop the index and cached model."""
        self._model = None
        self._tool_ids = []
        self._tool_embeddings = None
        self._tool_texts = []
        self._cache_key = ""


# Module-level singleton (reused across LLM executor instances)
# to avoid redundant model loading and index rebuilds.
_RETRIEVER = ToolRetriever()


def select_candidates_by_confidence(ranked, top_k: int = 10,
                                     very_high: float = 0.75,
                                     high: float = 0.55,
                                     mid: float = 0.35,
                                     margin: float = 0.10):
    """Cap retrieval results into a short candidate list for the Router LLM.

    ``ranked`` is a list of ``(tool_id, score)`` pairs sorted by cosine
    similarity descending (already threshold-filtered by the caller).  The
    Router LLM context must stay small so a small model can decide:

    - very high (best >= *very_high* AND clear margin) -> up to 2 candidates
    - high / mid / low                                   -> up to 3 candidates
    - low (best < *mid*) additionally allows a natural
      (no-USE_TOOL) reply

    Returns ``(candidate_ids, allow_natural_reply)``.
    """
    if not ranked:
        return [], True
    best = float(ranked[0][1])
    second = float(ranked[1][1]) if len(ranked) > 1 else best - 1.0
    if best >= very_high and (best - second) >= margin:
        count = 2
        allow_natural = False
    elif best >= high:
        count = 3
        allow_natural = False
    elif best >= mid:
        count = 3
        allow_natural = False
    else:
        count = 3
        allow_natural = True
    count = max(1, min(int(count), len(ranked), int(top_k)))
    return [tid for tid, _sc in ranked[:count]], allow_natural
