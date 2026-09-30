from typing import List, Tuple, Optional, Dict, Callable
import os
import zipfile
import re
from xml.etree import ElementTree as ET
import logging
logger = logging.getLogger(__name__)



def _multi_layer_embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed texts via the lazy llama.cpp embedding server.

    Uses ``AI.embedding_server`` (embeddinggemma GGUF in --embeddings mode,
    fully offline) — the sentence-transformers/PyTorch embedder has been
    removed.  Returns None on any failure so callers skip context injection
    instead of injecting unembedded text.
    """
    try:
        from AI import embedding_server

        vectors = embedding_server.embed_texts(list(texts))
    except Exception as exc:
        logger.debug("Multi-layer embed failed: %s", exc)
        return None
    if vectors is not None and len(vectors) == len(texts):
        return vectors
    logger.warning(
        "Multi-layer embed: server returned %s vectors for %d texts",
        len(vectors) if vectors else 0, len(texts),
    )
    return None


def _is_dense_content(chunk: str, min_word_ratio: float = 0.15) -> bool:
    """Return True if the chunk has a sufficient density of meaningful words.

    Skips chunks dominated by formatting, whitespace, ASCII art, box-drawing
    characters, or repetitive structural elements (headers, separators).
    """
    if not chunk or not chunk.strip():
        return False
    import re as _re
    total = len(chunk)
    # Count actual word characters (letters + digits)
    words = _re.findall(r"[a-zA-Z0-9]{2,}", chunk)
    word_chars = sum(len(w) for w in words)
    ratio = word_chars / total if total > 0 else 0
    return ratio >= min_word_ratio


def _chunk_text_chars(text: str, size: int, overlap: int) -> List[str]:
    chunks: List[str] = []
    if size <= 0:
        return [text]
    step = max(1, size - max(0, overlap))
    i = 0
    while i < len(text):
        chunk = text[i:i+size]
        if chunk:
            chunks.append(chunk)
        i += step
    return chunks


def _chunk_text_smart(text: str, size: int, overlap: int) -> List[str]:
    """Split *text* into chunks that respect line/sentence boundaries.

    Fixed character slicing (``_chunk_text_chars``) cuts markdown tables and
    sentences mid-word, producing fragments like ``al twin platform) ### 5.3``
    that are useless as retrieval evidence.  This chunker instead:
      1. Groups whole lines until the chunk reaches ``size`` chars;
      2. Splits a single over-long line on sentence boundaries;
      3. Falls back to character slicing only for pathological sentences.
    """
    import re as _re
    if size <= 0:
        return [text]
    step = max(1, size - max(0, overlap))
    lines = text.split("\n")
    chunks: List[str] = []

    def _split_long_line(line: str) -> List[str]:
        """Split one line that exceeds *size*: sentences first, chars last."""
        pieces: List[str] = []
        buf = ""
        for sent in _re.split(r"(?<=[.!?])\s+", line):
            if len(sent) > size:
                if buf:
                    pieces.append(buf)
                    buf = ""
                for s in range(0, len(sent), step):
                    pieces.append(sent[s:s + size])
            elif buf and len(buf) + 1 + len(sent) > size:
                pieces.append(buf)
                buf = sent
            else:
                buf = (buf + " " + sent) if buf else sent
        if buf:
            pieces.append(buf)
        return pieces

    current = ""
    for line in lines:
        if len(line) > size:
            # Long line: flush accumulated lines, then split the line itself
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(_split_long_line(line))
        elif current and len(current) + 1 + len(line) > size:
            chunks.append(current)
            current = line
        else:
            current = (current + "\n" + line) if current else line
    if current:
        chunks.append(current)
    return chunks


def _cap_chunks_by_chars(chunks: List[str], max_chars: Optional[int]) -> List[str]:
    """Greedily keep the top-ranked chunks while total chars <= max_chars.

    Always keeps at least one chunk (unless the list is empty) so a tiny
    budget still yields usable evidence for the LLM prompt.
    """
    if not max_chars or max_chars <= 0 or not chunks:
        return chunks
    selected: List[str] = []
    total = 0
    for c in chunks:
        if selected and total + len(c) > max_chars:
            break
        selected.append(c)
        total += len(c)
    return selected if selected else chunks[:1]


def _dedup_chunks(chunks: List[str]) -> List[str]:
    """Remove exact duplicates after normalizing whitespace/case.

    Overlapping coarse chunks re-chunked at fine granularity routinely
    produce the same table row or sentence in several chunks; duplicates
    waste the small model's context window.
    """
    import re as _re
    seen = set()
    out: List[str] = []
    for c in chunks:
        key = _re.sub(r"\s+", " ", c.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _cosine_sim(a: List[float], b: List[float]) -> float:
    try:
        import math
        if len(a) != len(b):
            logger.warning(
                "Cosine similarity dimension mismatch: %d vs %d — "
                "returning 0 (mixed embedding models?)",
                len(a), len(b),
            )
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def build_rag_context(
    client,
    prompt: str,
    input_text: str,
    embedding_model: str,
    chunk_size: int,
    overlap: int,
    top_k: int,
    include_raw_input: bool,
    max_chars: int,
    input_source: str,
    use_input_text_in_prompt: bool,
    precomputed_chunks: Optional[List[str]] = None,
    precomputed_embeddings: Optional[List[List[float]]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, object]]]:
    """Compute direct RAG context from input_text using embeddings.

    Returns a tuple of (rag_context_text, updated_prompt_if_embeddings_fail).
    """
    try:
        valid_chunks = []
        chunk_embs = []
        query_emb = []
        
        
        
        # Use precomputed store if provided
        if precomputed_chunks and precomputed_embeddings and len(precomputed_chunks) == len(precomputed_embeddings):
            valid_chunks = precomputed_chunks
            chunk_embs = precomputed_embeddings
            # Still need to compute query embedding for the new prompt
            logger.info(f"RAG: Computing query embedding for prompt: '{prompt[:50]}...' (client.debug={getattr(client, 'debug', 'unknown')})")
            query_embs = client.embeddings(embedding_model, [prompt])
            if query_embs:
                query_emb = query_embs[0]
            else:
                logger.warning("RAG: client.embeddings returned empty/None for query embedding")


        # Fallback to manual if LangChain failed or not available (and no precomputed chunks)
        if not valid_chunks:
            chunks = _chunk_text_chars(input_text, chunk_size, overlap)
            logger.info(f"RAG: chunked previous output into {len(chunks)} chunks (size={chunk_size}, overlap={overlap})")
            
            # Clean and validate chunks
            for c in chunks:
                if isinstance(c, str) and c.strip():
                    cleaned = c.replace('\x00', '').strip()
                    if cleaned and _is_dense_content(cleaned):
                        valid_chunks.append(cleaned)
            
            if valid_chunks:
                logger.info(f"RAG: Requesting embeddings for {len(valid_chunks)} chunks using model '{embedding_model}' (manual)")
                chunk_embs = client.embeddings(embedding_model, valid_chunks)
                query_embs = client.embeddings(embedding_model, [prompt])
                if query_embs:
                    query_emb = query_embs[0]

        if not valid_chunks:
            logger.info("RAG: no valid chunks (empty/whitespace); skipping embeddings")
            return None, None, None

        if not chunk_embs:
            logger.warning("RAG: embeddings returned None or empty list")
        elif len(chunk_embs) != len(valid_chunks):
            logger.warning(f"RAG: embeddings count mismatch. Sent {len(valid_chunks)}, got {len(chunk_embs)}")

        if not chunk_embs or len(chunk_embs) != len(valid_chunks):
            # Embeddings are the ONLY retrieval path — a missing chunk
            # embedding set is a hard break so the failure is visible.
            from AI.comorag_engine import EmbeddingFailureError
            raise EmbeddingFailureError(
                "Direct RAG: chunk embedding computation failed or mismatched "
                f"({len(valid_chunks)} chunks) — cannot retrieve previous-node "
                "context without embeddings"
            )

        if not query_emb:
            from AI.comorag_engine import EmbeddingFailureError
            raise EmbeddingFailureError(
                "Direct RAG: query embedding failed — cannot retrieve "
                "previous-node context without embeddings"
            )

        # Compute similarity on the embedded candidates
        q = query_emb
        sims = [( _cosine_sim(q, ce), idx) for idx, ce in enumerate(chunk_embs)]
        sims.sort(key=lambda x: x[0], reverse=True)
        top_indices = [idx for _, idx in sims[:max(1, top_k)]]
        top_texts = [valid_chunks[i] for i in top_indices]
        rag_context = "\n\n".join(top_texts)
        if len(rag_context) > max_chars:
            rag_context = rag_context[:max_chars]
        rag_context_text = f"[RAG Context from previous node]:\n{rag_context}"
        logger.info(f"RAG: selected top-{len(top_texts)} chunks for context (total chars={len(rag_context)})")

        

        store = {"chunks": valid_chunks, "embeddings": chunk_embs}
        return rag_context_text, None, store
    except Exception as e:
        from AI.comorag_engine import EmbeddingFailureError
        if isinstance(e, EmbeddingFailureError):
            # Hard break: never convert an embedding failure into a silent
            # None — the chain must stop with the cause visible.
            raise
        logger.warning(f"Direct RAG computation failed: {e}")
        return None, None, None

def _read_text_file(path: str) -> str:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        try:
            with open(path, 'r', encoding='latin-1') as f:
                return f.read()
        except Exception:
            return ""

def _extract_docx_text(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            with z.open('word/document.xml') as f:
                xml_bytes = f.read()
        # Parse XML and extract text from w:t nodes
        try:
            ns = {
                'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            }
            root = ET.fromstring(xml_bytes)
            texts: List[str] = []
            for t in root.findall('.//w:t', ns):
                if t.text:
                    texts.append(t.text)
            doc_text = ' '.join(texts)
            # Normalize whitespace
            doc_text = re.sub(r'\s+', ' ', doc_text).strip()
            return doc_text
        except Exception:
            # Fallback: strip tags crudely
            try:
                s = xml_bytes.decode('utf-8', errors='ignore')
            except Exception:
                s = str(xml_bytes)
            s = re.sub(r'<[^>]+>', '\n', s)
            s = re.sub(r'\s+', ' ', s)
            return s.strip()
    except Exception:
        return ""

def _extract_pdf_text(path: str) -> str:
    # PyMuPDF (fitz) extracts far cleaner text than PyPDF2 (layout-aware);
    # fall back through every library that is installed.
    try:
        import fitz  # PyMuPDF

        text_parts: List[str] = []
        with fitz.open(path) as doc:
            for page in doc:
                t = page.get_text() or ""
                if t:
                    text_parts.append(t)
        if text_parts:
            text = '\n'.join(text_parts)
            text = re.sub(r'\s+', ' ', text).strip()
            return text
    except Exception:
        pass
    try:
        import PyPDF2  # Optional: use if available
    except Exception:
        return ""
    try:
        text_parts: List[str] = []
        with open(path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in getattr(reader, 'pages', []):
                try:
                    t = page.extract_text() or ""
                    if t:
                        text_parts.append(t)
                except Exception:
                    continue
        text = '\n'.join(text_parts)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception:
        return ""

def build_rag_context_from_docs(
    client,
    prompt: str,
    documents: List[str],
    embedding_model: str,
    chunk_size: int,
    overlap: int,
    top_k: int,
    max_chars: int,
) -> Optional[str]:
    try:
        texts: List[str] = []
        for p in documents:
            if not p or not isinstance(p, str):
                continue
            if not os.path.exists(p):
                continue
            ext = os.path.splitext(p)[1].lower()
            t = ""
            if ext in ('.txt', '.md', '.csv', '.json', '.log'):
                t = _read_text_file(p)
            elif ext == '.docx':
                t = _extract_docx_text(p)
            elif ext == '.pdf':
                t = _extract_pdf_text(p)
            if t:
                texts.append(t)
        if not texts:
            logger.info("RAG: no readable documents found for context")
            return None
            
        cleaned_chunks = []
        chunk_embs = []
        query_emb = []

        
        
        # Fallback if LangChain didn't run
        if not cleaned_chunks:
            chunks = []
            for t in texts:
                pieces = _chunk_text_smart(t, chunk_size, overlap)
                for c in pieces:
                    if isinstance(c, str) and c.strip():
                        chunks.append(c)
            
            # Clean and filter chunks (reject low-density / formatting-only content)
            for c in chunks:
                cleaned = c.replace('\x00', '').strip()
                if cleaned and _is_dense_content(cleaned):
                    cleaned_chunks.append(cleaned)
            cleaned_chunks = _dedup_chunks(cleaned_chunks)
            
            # Embed ALL chunks — retrieval is embedding-only (embeddinggemma
            # via Ollama or the local llama.cpp server).  No lexical
            # pre-filter: keyword selection could exclude relevant chunks
            # before they ever reach the embedding ranker.
            filtered_chunks = cleaned_chunks

            if filtered_chunks:
                logger.info(f"RAG: Sending {len(filtered_chunks)} chunks to embedding model '{embedding_model}'")
                chunk_embs = client.embeddings(embedding_model, filtered_chunks)
                query_embs = client.embeddings(embedding_model, [prompt])
                if query_embs:
                    query_emb = query_embs[0]
        
        if not cleaned_chunks:
            logger.warning("RAG: No valid chunks after cleaning")
            return None

        # Detailed logging of embeddings result
        if chunk_embs:
            logger.info(f"RAG: Received {len(chunk_embs)} embeddings.")
            if len(chunk_embs) != len(cleaned_chunks):
                logger.error(f"RAG: Mismatch! Sent {len(cleaned_chunks)} chunks, got {len(chunk_embs)} embeddings")
        else:
            logger.error("RAG: Received None or empty list for embeddings")

        # Fallback: try the llama.cpp embedding server if Ollama unavailable
        if not chunk_embs:
            logger.info(
                "RAG: Ollama embeddings unavailable — trying llama.cpp embedding server fallback"
            )
            chunk_embs = _local_embed(filtered_chunks)
            if chunk_embs:
                q_local = _local_embed([prompt])
                if q_local:
                    query_emb = q_local[0]
                    logger.info(
                        "RAG: local embedding succeeded for %d chunks",
                        len(chunk_embs),
                    )

        # Embeddings are the ONLY retrieval path — when they are
        # unavailable the failure must stop the chain loudly (the overlay
        # shows the reason) instead of silently skipping document context.
        if not chunk_embs:
            from AI.comorag_engine import EmbeddingFailureError
            raise EmbeddingFailureError(
                "RAG: document embeddings unavailable (Ollama + local "
                "embedding server) — cannot retrieve document context"
            )
        
        # Chunk embeddings available but query embedding missing — try server
        if not query_emb:
            logger.info(
                "RAG: query embedding missing — trying llama.cpp embedding server"
            )
            q_local = _local_embed([prompt])
            if q_local:
                query_emb = q_local[0]

        # Same hard-break contract for a missing query embedding.
        if not query_emb:
            from AI.comorag_engine import EmbeddingFailureError
            raise EmbeddingFailureError(
                "RAG: document query embedding failed (Ollama + local "
                "embedding server) — cannot retrieve document context"
            )
            
        q = query_emb
        logger.info(f"RAG: Computing cosine similarity for {len(chunk_embs)} vectors...")
        sims = [( _cosine_sim(q, ce), idx) for idx, ce in enumerate(chunk_embs)]
        sims.sort(key=lambda x: x[0], reverse=True)
        
        # Log top matches
        for rank, (score, idx) in enumerate(sims[:min(3, len(sims))]):
            logger.info(f"RAG: Match #{rank+1}: Score {score:.4f}, Chunk index {idx}")

        top_indices = [idx for _, idx in sims[:max(1, top_k)]]
        top_texts = [cleaned_chunks[i] for i in top_indices]
        rag_context = "\n\n".join(top_texts)
        if len(rag_context) > max_chars:
            rag_context = rag_context[:max_chars]
        logger.info(f"RAG: selected {len(top_texts)} document chunks for context")
        return f"[RAG Context from documents]:\n{rag_context}"
    except Exception as e:
        from AI.comorag_engine import EmbeddingFailureError
        if isinstance(e, EmbeddingFailureError):
            # Hard break: never convert an embedding failure into a silent
            # None — the chain must stop with the cause visible.
            raise
        logger.warning(f"RAG: failed to build context from documents: {e}")
        return None

def _local_embed(texts: List[str]) -> Optional[List[List[float]]]:
    """Embed texts via the lazy llama.cpp embedding server.

    Previously the sentence-transformers all-MiniLM fallback; the local
    PyTorch embedder has been removed — the llama.cpp server (embeddinggemma
    GGUF) is the local backend now.  Returns None when the server is
    unavailable so callers skip context injection.
    """
    return _multi_layer_embed(texts)


# ────────────────────────────────────────────────────────────────
# Iterative document chunk cursor for small-model LLMs
# ────────────────────────────────────────────────────────────────


class GraphRAGCursor:
    """Iterative cursor over GraphRAG-embedded document chunks.

    When an LLM node has ``use_direct_rag=True`` with ``rag_documents``,
    the executor ingests the documents into GraphRAG once, then creates
    this cursor.  Each loop iteration calls ``next_chunk()`` to get one
    semantic chunk.  Returns ``("", True)`` when exhausted, so the loop
    can stop.
    """

    _SENTINEL_EXHAUSTED = -9999

    def __init__(self, chunks: list, prompt: str):
        self._chunks = list(chunks)  # list of str
        self._prompt = str(prompt)
        self._idx = 0
        self._total = len(chunks)

    @property
    def has_more(self) -> bool:
        return self._idx < self._total

    @property
    def progress(self) -> tuple:
        """Return (current, total) for UI display."""
        return (self._idx, self._total)

    def next_chunk(self) -> tuple:
        """Return (chunk_text, is_exhausted).

        Chunk text is a single document chunk formatted for the LLM
        prompt: "[Document chunk 3/15]:\n..."
        """
        if self._idx >= self._total:
            return "", True
        chunk = self._chunks[self._idx]
        self._idx += 1
        label = f"[Document chunk {self._idx}/{self._total}]:\n{chunk}"
        return label, self._idx >= self._total

    def to_dict(self) -> dict:
        """Serialize cursor state so loop iterations can resume."""
        return {"idx": self._idx, "total": self._total}

    @classmethod
    def from_dict(cls, chunks: list, state: dict, prompt: str = ""):
        c = cls(chunks, prompt)
        c._idx = state.get("idx", 0)
        return c


def build_graphrag_context(
    documents: list,
    prompt: str,
    chunk_size: int = 500,
    overlap: int = 100,
    max_chunks: int = 20,
    max_chars: Optional[int] = None,
    embed_fn: Optional[Callable[[List[str]], Optional[List[List[float]]]]] = None,
) -> Optional[GraphRAGCursor]:
    """Multi-layer embedding refinement for small-model document Q&A.

    Performs hierarchical embedding refinement so very long documents
    don't overflow the small model's context window (~4092 tokens):
      1. Layer 1 (Coarse): chunk at ``chunk_size``, embed ALL chunks,
         cosine rank → keep 4x top candidates
      2. Layer 2 (Fine):   re-chunk top candidates at ``chunk_size // 2``,
         re-embed, re-rank → keep final top-k chunks
      3. All refined chunks are injected in one shot (see executor.py)

    Retrieval is embedding-only (embeddinggemma / Ollama): if embeddings
    are unavailable the function raises EmbeddingFailureError so the chain
    stops with the failure visible — no unranked document text ever reaches
    the prompt.

    No document text is ever truncated — only the LLM's per-iteration
    input is narrowed to the most relevant chunks.

    Args:
        documents: List of file paths (str) to ingest.
        prompt: The user/LLM prompt used to rank chunks.
        chunk_size: Character size for coarse chunking (default 500).
        overlap: Character overlap between chunks (default 100).
        max_chunks: Maximum number of refined chunks to keep.
        max_chars: Optional total character budget for the final chunk set
            (e.g. the node's ``rag_max_chars``).  Chunks are ranked by
            relevance first, then greedily kept until the budget is spent.
        embed_fn: Optional ``(texts) -> vectors`` callable.  Defaults to
            the lazy llama.cpp embedding server.  The executor injects an
            Ollama-backed embedder for Ollama-engine nodes.

    Returns:
        GraphRAGCursor or None if ingestion failed.
    """
    # 1. Read all documents into raw text
    texts: list = []
    for p in documents:
        if not p or not isinstance(p, str):
            continue
        if not os.path.exists(p):
            continue
        ext = os.path.splitext(p)[1].lower()
        t = ""
        if ext in ('.txt', '.md', '.csv', '.json', '.log'):
            t = _read_text_file(p)
        elif ext == '.docx':
            t = _extract_docx_text(p)
        elif ext == '.pdf':
            t = _extract_pdf_text(p)
        if t:
            texts.append(t)

    if not texts:
        logger.info("GraphRAG: no readable documents found")
        return None

    # ── Multi-layer embedding refinement ──────────────────────────────
    # For small models (~4092 tokens), a single embedding pass produces
    # chunks that are either too coarse (missing details) or too numerous
    # (overflowing the context window).  Instead we perform hierarchical
    # refinement across two embedding layers:
    #
    #   Layer 1 (Coarse): chunk at ``chunk_size``, embed ALL chunks,
    #                     cosine rank → keep 4x top
    #   Layer 2 (Fine):   re-chunk top candidates at ``chunk_size // 2``,
    #                     re-embed, re-rank → keep final top-k chunks
    #
    # Both layers use the same query, but Layer 2 operates on a tighter
    # candidate pool (only the top coarse chunks), so it can afford a
    # second embedding pass for more precise ranking.
    #
    # Retrieval is embedding-only: no lexical pre-filter, no lexical
    # fallback — when embeddings fail the function raises
    # EmbeddingFailureError so the chain stops with the failure visible.
    #
    # All refined chunks are injected together into the prompt in one
    # shot (see executor.py).  No document text is ever truncated.

    # ── Layer 1: Coarse chunking ──
    _COARSE_CHUNK = max(100, chunk_size or 500)
    _COARSE_OVERLAP = max(10, overlap or 100)
    coarse_chunks: List[str] = []
    for t in texts:
        pieces = _chunk_text_smart(t, _COARSE_CHUNK, _COARSE_OVERLAP)
        for c in pieces:
            if isinstance(c, str) and c.strip():
                cleaned = c.replace('\x00', '').strip()
                if cleaned and _is_dense_content(cleaned):
                    coarse_chunks.append(cleaned)
    coarse_chunks = _dedup_chunks(coarse_chunks)

    if not coarse_chunks:
        logger.info("GraphRAG: no valid chunks after coarse parsing")
        return None

    logger.info(
        "GraphRAG Layer 1: %d coarse chunks from document(s)",
        len(coarse_chunks),
    )

    # No lexical pre-filter: embed ALL coarse chunks so every candidate
    # that could enter the prompt is ranked by embeddings, never keywords.
    _CANDIDATE_MULTIPLIER = 4
    candidates = coarse_chunks

    # Embed Layer 1 candidates
    embedder = embed_fn or _multi_layer_embed
    layer1_embs = embedder(candidates)
    if not layer1_embs:
        from AI.comorag_engine import EmbeddingFailureError
        raise EmbeddingFailureError(
            "GraphRAG Layer 1: embedding unavailable (embeddinggemma / "
            "Ollama) — cannot rank document chunks without embeddings"
        )

    query_emb = embedder([prompt])
    if not query_emb:
        from AI.comorag_engine import EmbeddingFailureError
        raise EmbeddingFailureError(
            "GraphRAG Layer 1: query embedding failed — cannot rank "
            "document chunks without embeddings"
        )

    q = query_emb[0]
    sims = [(_cosine_sim(q, ce), idx) for idx, ce in enumerate(layer1_embs)]
    sims.sort(key=lambda x: x[0], reverse=True)
    top_coarse_indices = [idx for _, idx in sims[:max(1, max_chunks * _CANDIDATE_MULTIPLIER)]]
    top_coarse = [candidates[i] for i in top_coarse_indices]

    logger.info(
        "GraphRAG Layer 1: top-%d coarse chunks selected by cosine similarity",
        len(top_coarse),
    )

    # ── Layer 2: Fine re-chunking & re-embedding ──
    # Re-chunk at half the coarse size for finer granularity while keeping
    # each chunk meaningful (~250-500 chars depending on config).
    _FINE_CHUNK = max(100, _COARSE_CHUNK // 2)
    _FINE_OVERLAP = max(10, _COARSE_OVERLAP // 2)
    fine_chunks: List[str] = []
    for coarse_text in top_coarse:
        fine_pieces = _chunk_text_smart(coarse_text, _FINE_CHUNK, _FINE_OVERLAP)
        for fp in fine_pieces:
            cleaned = fp.replace('\x00', '').strip()
            if cleaned and len(cleaned) > 20 and _is_dense_content(cleaned):
                fine_chunks.append(cleaned)
    fine_chunks = _dedup_chunks(fine_chunks)

    if not fine_chunks:
        logger.info("GraphRAG Layer 2: no fine chunks — using coarse top as-is")
        final_chunks = _cap_chunks_by_chars(top_coarse[:max_chunks], max_chars)
        return GraphRAGCursor(final_chunks, prompt)

    # Embed Layer 2 fine chunks and re-rank against the same query
    layer2_embs = embedder(fine_chunks)
    if not layer2_embs:
        logger.info("GraphRAG Layer 2: embedding unavailable, distributing fine chunks")
        step = max(1, len(fine_chunks) // max_chunks)
        final_chunks = _cap_chunks_by_chars(fine_chunks[::step][:max_chunks], max_chars)
        return GraphRAGCursor(final_chunks, prompt)

    sims2 = [(_cosine_sim(q, fe), idx) for idx, fe in enumerate(layer2_embs)]
    sims2.sort(key=lambda x: x[0], reverse=True)
    top_fine_indices = [idx for _, idx in sims2[:max(1, max_chunks)]]
    final_chunks = _cap_chunks_by_chars(
        [fine_chunks[i] for i in top_fine_indices], max_chars
    )

    logger.info(
        "GraphRAG Layer 2: refined %d coarse → %d fine → %d final chunks",
        len(top_coarse), len(fine_chunks), len(final_chunks),
    )

    cursor = GraphRAGCursor(final_chunks, prompt)
    logger.info(
        "GraphRAG: cursor ready with %d refined chunks "
        "(%d chars total, budget=%s, no truncation)",
        len(final_chunks), sum(len(c) for c in final_chunks), max_chars,
    )
    return cursor
