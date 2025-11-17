from typing import List, Tuple, Optional

from ...config import logger


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


def _cosine_sim(a: List[float], b: List[float]) -> float:
    try:
        import math
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
) -> Tuple[Optional[str], Optional[str]]:
    """Compute direct RAG context from input_text using embeddings.

    Returns a tuple of (rag_context_text, updated_prompt_if_embeddings_fail).
    """
    try:
        chunks = _chunk_text_chars(input_text, chunk_size, overlap)
        logger.info(f"RAG: chunked previous output into {len(chunks)} chunks (size={chunk_size}, overlap={overlap})")

        # Filter out whitespace-only chunks to avoid pointless embeddings calls
        valid_chunks = [c for c in chunks if isinstance(c, str) and c.strip()]
        if not valid_chunks:
            logger.info("RAG: no valid chunks (empty/whitespace); skipping embeddings")
            if not use_input_text_in_prompt:
                updated_prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
            else:
                updated_prompt = None
            return None, updated_prompt

        chunk_embs = client.embeddings(embedding_model, valid_chunks)
        if not chunk_embs or len(chunk_embs) != len(valid_chunks):
            logger.warning("RAG: embedding computation failed or mismatched; falling back to injecting raw previous text")
            if not use_input_text_in_prompt:
                updated_prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
            else:
                updated_prompt = None
            return None, updated_prompt

        query_embs = client.embeddings(embedding_model, [prompt])
        if not query_embs or not query_embs[0]:
            logger.warning("RAG: query embedding failed; reverting to raw input injection")
            if not use_input_text_in_prompt:
                updated_prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
            else:
                updated_prompt = None
            return None, updated_prompt

        q = query_embs[0]
        sims = [( _cosine_sim(q, ce), idx) for idx, ce in enumerate(chunk_embs)]
        sims.sort(key=lambda x: x[0], reverse=True)
        top_indices = [idx for _, idx in sims[:max(1, top_k)]]
        top_texts = [valid_chunks[i] for i in top_indices]
        rag_context = "\n\n".join(top_texts)
        if len(rag_context) > max_chars:
            rag_context = rag_context[:max_chars]
        rag_context_text = f"[RAG Context from previous node]:\n{rag_context}"
        logger.info(f"RAG: selected top-{len(top_texts)} chunks for context (total chars={len(rag_context)})")

        if include_raw_input:
            rag_context_text = f"{rag_context_text}\n\n[Previous node raw output (truncated)]:\n{input_text[:min(len(input_text), max_chars)]}"

        return rag_context_text, None
    except Exception as e:
        logger.warning(f"Direct RAG computation failed: {e}")
        return None, None