from typing import List, Tuple, Optional
import os
import zipfile
import re
from xml.etree import ElementTree as ET

from ...config import logger

try:
    from langchain_ollama import OllamaEmbeddings
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False


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
    precomputed_chunks: Optional[List[str]] = None,
    precomputed_embeddings: Optional[List[List[float]]] = None,
) -> Tuple[Optional[str], Optional[str]]:
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
        # Fallback to manual if LangChain failed or not available
        if not valid_chunks:
            chunks = _chunk_text_chars(input_text, chunk_size, overlap)
            logger.info(f"RAG: chunked previous output into {len(chunks)} chunks (size={chunk_size}, overlap={overlap})")
            
            # Clean and validate chunks
            for c in chunks:
                if isinstance(c, str) and c.strip():
                    cleaned = c.replace('\x00', '').strip()
                    if cleaned:
                        valid_chunks.append(cleaned)
            
            if valid_chunks:
                logger.info(f"RAG: Requesting embeddings for {len(valid_chunks)} chunks using model '{embedding_model}' (manual)")
                chunk_embs = client.embeddings(embedding_model, valid_chunks)
                query_embs = client.embeddings(embedding_model, [prompt])
                if query_embs:
                    query_emb = query_embs[0]

        if not valid_chunks:
            logger.info("RAG: no valid chunks (empty/whitespace); skipping embeddings")
            if not use_input_text_in_prompt:
                updated_prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
            else:
                updated_prompt = None
            return None, updated_prompt
        
        if not chunk_embs:
            logger.warning("RAG: embeddings returned None or empty list")
        elif len(chunk_embs) != len(valid_chunks):
            logger.warning(f"RAG: embeddings count mismatch. Sent {len(valid_chunks)}, got {len(chunk_embs)}")

        if not chunk_embs or len(chunk_embs) != len(valid_chunks):
            logger.warning("RAG: embedding computation failed or mismatched; falling back to injecting raw previous text")
            if not use_input_text_in_prompt:
                updated_prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
            else:
                updated_prompt = None
            return None, updated_prompt

        if not query_emb:
            logger.warning("RAG: query embedding failed; reverting to raw input injection")
            if not use_input_text_in_prompt:
                updated_prompt = f"[Input Source: {input_source.upper()}]\n{input_text}\n\n[User Prompt:]\n{prompt}"
            else:
                updated_prompt = None
            return None, updated_prompt

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

        if include_raw_input:
            rag_context_text = f"{rag_context_text}\n\n[Previous node raw output (truncated)]:\n{input_text[:min(len(input_text), max_chars)]}"

        return rag_context_text, None
    except Exception as e:
        logger.warning(f"Direct RAG computation failed: {e}")
        return None, None

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
                pieces = _chunk_text_chars(t, chunk_size, overlap)
                for c in pieces:
                    if isinstance(c, str) and c.strip():
                        chunks.append(c)
            
            # Clean chunks
            for c in chunks:
                cleaned = c.replace('\x00', '').strip()
                if cleaned:
                    cleaned_chunks.append(cleaned)
            
            if cleaned_chunks:
                logger.info(f"RAG: Sending {len(cleaned_chunks)} cleaned chunks to embedding model '{embedding_model}'")
                chunk_embs = client.embeddings(embedding_model, cleaned_chunks)
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

        # Fallback path if chunk embeddings fail
        if not chunk_embs or len(chunk_embs) != len(cleaned_chunks):
            logger.warning(f"RAG: document chunk embeddings unavailable (sent {len(cleaned_chunks)}, got {len(chunk_embs) if chunk_embs else 0}), using lexical fallback")
            top_texts = _lexical_select_top(prompt, cleaned_chunks, top_k)
            if not top_texts:
                return None
            rag_context = "\n\n".join(top_texts)
            if len(rag_context) > max_chars:
                rag_context = rag_context[:max_chars]
            return f"[RAG Context from documents (Lexical Fallback)]:\n{rag_context}"
        
        # Try query embeddings, else lexical fallback
        if not query_emb:
            logger.warning("RAG: query embedding failed for documents, using lexical fallback")
            top_texts = _lexical_select_top(prompt, cleaned_chunks, top_k)
            if not top_texts:
                return None
            rag_context = "\n\n".join(top_texts)
            if len(rag_context) > max_chars:
                rag_context = rag_context[:max_chars]
            return f"[RAG Context from documents (Lexical Fallback)]:\n{rag_context}"
            
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
        logger.warning(f"RAG: failed to build context from documents: {e}")
        return None

def _lexical_select_top(query: str, chunks: List[str], top_k: int) -> List[str]:
    try:
        def _tokenize(s: str) -> List[str]:
            s = s.lower()
            s = re.sub(r"[^a-z0-9]+", " ", s)
            return [w for w in s.split() if w]
        q_words = set(_tokenize(query))
        scored = []
        for idx, ch in enumerate(chunks):
            words = set(_tokenize(ch))
            score = len(q_words & words)
            scored.append((score, idx))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [chunks[i] for _, i in scored[:max(1, top_k)]]
        if not top:
            # Fallback to first N chunks if lexical overlap is zero
            top = chunks[:max(1, top_k)]
        return top
    except Exception:
        return chunks[:max(1, top_k)]
