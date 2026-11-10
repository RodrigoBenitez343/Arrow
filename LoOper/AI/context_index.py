"""A persistent, content-addressed block index for a Context node's documents.

The Context node keeps SERVING TEXT - the user edits and adds knowledge in its
dialog at any time, and that pool is what the app shows and what every other
consumer reads.  This store keeps the thing the model needs instead: the
embeddings of that text's retrieval blocks, so a document is chunked and embedded
ONCE per version rather than once per field per run.

Why it exists (measured on the user's own run): the form filler ranked its
corpus by embedding EVERY block for EVERY field - 53 block texts plus the query
in one round-trip, 4.2 s each, and then discarded the ranking because a candidate
budget smaller than the first two blocks cut it off.  A field therefore saw only
the document's header, no matter what it asked.  With the index here a field
embeds ONE vector (its own question), and the answer's block is reachable
wherever it sits in the document.

Keying is by (content hash, embedding model):

* content hash of a document's text means an EDIT re-indexes automatically and a
  re-run never does,
* two chains pointing at the same resume share one set of vectors,
* a different embedding model is a DIFFERENT key, so a stored vector is never
  compared against a query from a model that would not agree with it.
"""

import json
import logging
import os
import sqlite3
import struct
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_TABLE_LOCK = threading.Lock()

# The identity of the vector space, recorded on every row: swapping the
# embedding model must reindex, never silently reuse.
_LOCAL_PREFIX = "llama.cpp:"


def _disabled():
    """``LOOPER_CTX_INDEX=0`` keeps the legacy per-field embedding pass."""
    return (os.environ.get("LOOPER_CTX_INDEX") or "").strip().lower() in (
        "0", "off", "false", "no")


def _db_path():
    """The Context node's own database file (same rule as ContextDatabase)."""
    if getattr(sys, "frozen", False):
        try:
            from AI.runtime_paths import get_runtime_dir
            db_dir = get_runtime_dir()
        except Exception:
            db_dir = os.path.join(os.path.dirname(sys.executable), "data")
    else:
        db_dir = str(Path(__file__).resolve().parent.parent / "data")
    os.makedirs(str(db_dir), exist_ok=True)
    return os.path.join(str(db_dir), "context.db")


def local_embedding_model_id():
    """The GGUF the local embedding server actually loads, as a bare filename.

    The index must key on the model that PRODUCED the vectors.  The local engine
    ignores the node's generation model and loads its own embedding model from
    config, so that resolved name - not ``cfg['model']`` - is the identity.
    """
    name = ""
    try:
        from AI.config_loader import get_llamacpp_config
        cfg = get_llamacpp_config() or {}
        name = str(cfg.get("embedding_model_path") or "").strip()
    except Exception:
        name = ""
    if not name:
        name = "embeddinggemma-300M-Q8_0.gguf"
    return _LOCAL_PREFIX + os.path.basename(name.replace("\\", "/"))


def model_id_for(engine, model=""):
    """The vector-space identity the caller's embedding client will use.

    Mirrors ``_FFEmbedClient``: local llama.cpp uses the app's own embedding
    server (its model name, whatever the node generates with), Ollama and a BYOK
    provider use the model the NODE names.  An unknown engine gets ``None`` so
    no shared index is used for a space this module cannot identify.
    """
    engine = str(engine or "llamacpp").strip().lower()
    model = str(model or "").strip()
    if engine == "ollama":
        return "ollama:" + (model or "nomic-embed-text")
    try:
        from AI import byok
        is_local = byok.is_local_engine(engine)
    except Exception:
        is_local = engine in ("", "llamacpp", "llama.cpp", "llama_cpp")
    if is_local:
        return local_embedding_model_id()
    if not model:
        return None
    try:
        from AI import byok as _byok
        return "byok:" + str(_byok.join_model_ref(engine, model))
    except Exception:
        return "byok:%s:%s" % (engine, model)


def encode_vector(vec):
    """float32 little-endian blob (half the size of JSON, exact enough)."""
    try:
        clean = [float(x) for x in (vec or [])]
    except Exception:
        return b""
    return struct.pack("<%df" % len(clean), *clean) if clean else b""


def decode_vector(blob):
    try:
        if not blob:
            return []
        n = len(blob) // 4
        return list(struct.unpack("<%df" % n, bytes(blob)[:n * 4]))
    except Exception:
        return []


def cosine(a, b):
    try:
        import math
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0
    except Exception:
        return 0.0


class ContextChunkStore:
    """SQLite block store: one row per retrieval block, vector included."""

    def __init__(self, db_path=None):
        self.db_path = db_path or _db_path()
        self._local = threading.local()
        self._init_db()

    def _conn(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                pass
            self._local.conn = conn
        return conn

    def _init_db(self):
        try:
            with _TABLE_LOCK:
                conn = self._conn()
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ctx_chunks (
                        sha256 TEXT NOT NULL,
                        model  TEXT NOT NULL,
                        dim    INTEGER NOT NULL DEFAULT 0,
                        doc_path TEXT NOT NULL DEFAULT '',
                        ordinal INTEGER NOT NULL,
                        section TEXT NOT NULL DEFAULT '',
                        text TEXT NOT NULL,
                        segments TEXT NOT NULL DEFAULT '[]',
                        head INTEGER NOT NULL DEFAULT 0,
                        vec BLOB NOT NULL,
                        created_at REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (sha256, model, ordinal)
                    )
                    """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ctx_chunks_model "
                    "ON ctx_chunks(model, sha256)")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ctx_chunk_owners (
                        sha256 TEXT NOT NULL,
                        model  TEXT NOT NULL,
                        chain_id TEXT NOT NULL DEFAULT '',
                        node_id TEXT NOT NULL DEFAULT '',
                        doc_path TEXT NOT NULL DEFAULT '',
                        chars INTEGER NOT NULL DEFAULT 0,
                        updated_at REAL NOT NULL DEFAULT 0,
                        PRIMARY KEY (sha256, model, chain_id, node_id)
                    )
                    """)
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - an index is never fatal
            logger.warning("[CTX-INDEX] schema unavailable: %s", exc)

    # ------------------------------------------------------------- read
    def count(self, sha=None, model=None):
        try:
            sql, args = "SELECT COUNT(*) FROM ctx_chunks WHERE 1=1", []
            if sha:
                sql += " AND sha256=?"
                args.append(sha)
            if model:
                sql += " AND model=?"
                args.append(model)
            return int(self._conn().execute(sql, args).fetchone()[0])
        except Exception:
            return 0

    def blocks(self, shas, model):
        """Every stored block for those documents, in document order."""
        shas = [str(s) for s in (shas or []) if s]
        if not shas or not model:
            return []
        marks = ",".join("?" * len(shas))
        try:
            rows = self._conn().execute(
                "SELECT sha256, ordinal, section, text, segments, head, vec, "
                "dim FROM ctx_chunks WHERE model=? AND sha256 IN (%s) "
                "ORDER BY sha256, ordinal" % marks,
                [model] + shas).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CTX-INDEX] read failed: %s", exc)
            return []
        out = []
        for sha, ordinal, section, text, segs, head, vec, dim in rows:
            try:
                parsed = json.loads(segs or "[]")
            except Exception:
                parsed = []
            out.append({"sha": sha, "ordinal": int(ordinal),
                        "section": section or "", "text": text or "",
                        "segments": parsed, "head": bool(head),
                        "vector": decode_vector(vec), "dim": int(dim or 0)})
        return out

    # ----------------------------------------------------------- write
    def put_blocks(self, sha, model, doc_path, blocks_meta, vectors):
        """Replace one document version's blocks.  All-or-nothing."""
        if not sha or not model or len(blocks_meta) != len(vectors):
            return False
        try:
            with _TABLE_LOCK:
                conn = self._conn()
                conn.execute(
                    "DELETE FROM ctx_chunks WHERE sha256=? AND model=?",
                    (sha, model))
                now = time.time()
                dim = len(vectors[0] or [])
                for meta, vec in zip(blocks_meta, vectors):
                    conn.execute(
                        "INSERT OR REPLACE INTO ctx_chunks "
                        "(sha256, model, dim, doc_path, ordinal, section, text,"
                        " segments, head, vec, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (sha, model, dim, str(doc_path or ""),
                         int(meta.get("ordinal") or 0),
                         str(meta.get("section") or ""),
                         str(meta.get("text") or ""),
                         json.dumps(meta.get("segments") or []),
                         1 if meta.get("head") else 0,
                         encode_vector(vec), now))
                conn.commit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[CTX-INDEX] write failed for %s: %s",
                           sha[:12], exc)
            return False

    def claim(self, sha, model, chain_id, node_id, doc_path, chars):
        """Record WHO points at this document version (audit + later pruning)."""
        try:
            with _TABLE_LOCK:
                self._conn().execute(
                    "INSERT OR REPLACE INTO ctx_chunk_owners "
                    "(sha256, model, chain_id, node_id, doc_path, chars, "
                    "updated_at) VALUES (?,?,?,?,?,?,?)",
                    (sha, model, str(chain_id or ""), str(node_id or ""),
                     str(doc_path or ""), int(chars or 0), time.time()))
                self._conn().commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CTX-INDEX] owner record failed: %s", exc)

    def drop(self, sha, model=None):
        """Forget a document version (its text changed, or the user removed it)."""
        try:
            with _TABLE_LOCK:
                conn = self._conn()
                if model:
                    conn.execute("DELETE FROM ctx_chunks WHERE sha256=? AND "
                                 "model=?", (sha, model))
                else:
                    conn.execute("DELETE FROM ctx_chunks WHERE sha256=?", (sha,))
                conn.execute("DELETE FROM ctx_chunk_owners WHERE sha256=?",
                             (sha,))
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CTX-INDEX] drop failed: %s", exc)

    def purge(self, chain_id=None, node_id=None, shas=None, model=None) -> int:
        """Delete this node's claims on a corpus, then any orphaned vectors.

        A TEMPORAL corpus (scraped for one run, discarded after it) should not
        keep its vectors: they cost disk and never get a second reader.  A
        stable one (a resume, a profile) must keep them - hence the purge is
        opt-in per Context node (``index_ephemeral``) and never implicit.

        Owners go first, so a block still claimed by another node/chain keeps
        its vectors.  Returns the number of chunk rows removed; never raises.
        """
        want_shas = [str(s) for s in (shas or []) if s]
        if not (chain_id or node_id or want_shas):
            return 0
        sql = "SELECT DISTINCT sha256, model FROM ctx_chunk_owners WHERE 1=1"
        del_sql = "DELETE FROM ctx_chunk_owners WHERE 1=1"
        args = []
        if chain_id:
            sql += " AND chain_id=?"
            del_sql += " AND chain_id=?"
            args.append(str(chain_id))
        if node_id:
            sql += " AND node_id=?"
            del_sql += " AND node_id=?"
            args.append(str(node_id))
        if model:
            sql += " AND model=?"
            del_sql += " AND model=?"
            args.append(str(model))
        if want_shas:
            marks = ",".join("?" * len(want_shas))
            sql += " AND sha256 IN (%s)" % marks
            del_sql += " AND sha256 IN (%s)" % marks
            args.extend(want_shas)
        try:
            with _TABLE_LOCK:
                conn = self._conn()
                pairs = [(row[0], row[1]) for row in conn.execute(sql, args)]
                if not pairs:
                    return 0
                # Only THIS node's claims go; a sibling node's claim on the
                # same version keeps the vectors alive (checked below).
                conn.execute(del_sql, args)
                removed = 0
                for sha, mid in pairs:
                    left = conn.execute(
                        "SELECT COUNT(*) FROM ctx_chunk_owners "
                        "WHERE sha256=? AND model=?", (sha, mid)).fetchone()[0]
                    if int(left or 0) > 0:
                        continue  # another node/chain still points at it
                    removed += int(conn.execute(
                        "DELETE FROM ctx_chunks WHERE sha256=? AND model=?",
                        (sha, mid)).rowcount or 0)
                conn.commit()
            if removed:
                logger.info("[CTX-INDEX] purged %d block(s) (%d corpus "
                            "version(s))", removed, len(pairs))
            return removed
        except Exception as exc:  # noqa: BLE001 - a purge is never fatal
            logger.debug("[CTX-INDEX] purge failed: %s", exc)
            return 0

    def rank(self, shas, model, query_vector, top_k=6):
        """Position-free block ranking: cosine of ONE query against the store."""
        return self.rank_multi(shas, model, [query_vector], top_k=top_k)

    def rank_multi(self, shas, model, query_vectors, top_k=6):
        """Ranking against SEVERAL query vectors - a block keeps its BEST score.

        A form field is asked several ways (its label, the kind of value it
        wants, the control's own placeholder) because one short label embeds
        poorly against a line of contact details.  Max-over-queries is what
        keeps one phrasing from having to carry the whole question.
        """
        vectors = [v for v in (query_vectors or []) if v]
        if not vectors:
            return []
        scored = []
        for i, row in enumerate(self.blocks(shas, model)):
            score = max(cosine(vec, row["vector"]) for vec in vectors)
            scored.append((float(score), -i, row))
        scored.sort(key=lambda r: (r[0], r[1]), reverse=True)
        keep = [r[2] for r in scored if r[0] > 0.0]
        return keep[:max(1, int(top_k or 1))]


_STORE = None


def get_store():
    """One store per process (SQLite handles are thread-local internally)."""
    global _STORE
    if _STORE is None:
        _STORE = ContextChunkStore()
    return _STORE


def ensure_index(docs, *, embed, model_id, chain_id="", node_id="", store=None):
    """Make sure every document version in ``docs`` has stored block vectors.

    ``docs`` is ``[{"sha", "blocks": [...from corpus.shape...], "doc_path",
    "text"}]`` - the CALLER does the chunking with ``AI.crag.corpus``, so the
    indexed text and the scored text are built by one and the same chunker.

    One ``embed`` round-trip per NEW document version; a version already in the
    store costs one COUNT.  Returns ``{"built": {sha: n}, "reused": [sha],
    "failed": [sha]}`` - never raises, because the consumer keeps a working
    (if slower) local ranking path when nothing could be stored.
    """
    summary = {"built": {}, "reused": [], "failed": []}
    if _disabled() or not model_id or not docs:
        return summary
    store = store or get_store()
    _embed = embed or (lambda texts: [])
    for doc in docs:
        sha = str((doc or {}).get("sha") or "")
        meta = list((doc or {}).get("blocks") or [])
        if not sha or not meta:
            continue
        if store.count(sha=sha, model=model_id) >= len(meta):
            store.claim(sha, model_id, chain_id, node_id,
                        doc.get("doc_path"), len(doc.get("text") or ""))
            summary["reused"].append(sha)
            continue
        try:
            vectors = list(_embed([b.get("text") or "" for b in meta]) or [])
        except Exception as exc:  # noqa: BLE001
            logger.debug("[CTX-INDEX] embed burst failed for %s: %s",
                         sha[:12], exc)
            vectors = []
        if len(vectors) != len(meta) or not any(vectors):
            summary["failed"].append(sha)
            continue
        if store.put_blocks(sha, model_id, doc.get("doc_path"), meta, vectors):
            store.claim(sha, model_id, chain_id, node_id,
                        doc.get("doc_path"), len(doc.get("text") or ""))
            summary["built"][sha] = len(meta)
        else:
            summary["failed"].append(sha)
    if summary["built"]:
        logger.info("[CTX-INDEX] indexed %d document version(s) (%s) with %s",
                    len(summary["built"]),
                    ", ".join("%s: %d block(s)" % (s[:8], n)
                              for s, n in summary["built"].items()),
                    model_id)
    return summary


def rank_blocks(query, docs, *, embed, model_id, top_k=6, store=None):
    """``query -> ranked blocks``, read-only.

    ``query`` is one text OR a LIST of query texts (the field's label, the kind
    of value it wants, the control's own words); several queries cost ONE embed
    burst and a block keeps its best score.  BUILDING is the owning Context
    node's job (``ensure_index`` when it serves its streams); this only looks a
    ranking up.  A corpus that was never indexed returns ``None`` WITHOUT
    spending an embedding call, so a consumer keeps its own per-field path
    instead of silently turning into an indexer - and a unit test with no
    Context node writes nothing.
    """
    if _disabled() or not model_id or not docs:
        return None
    queries = [str(q).strip() for q in
               ([query] if isinstance(query, str) else list(query or []))]
    queries = [q for q in queries if q]
    if not queries:
        return None
    store = store or get_store()
    _embed = embed or (lambda texts: [])
    shas = [str((d or {}).get("sha") or "") for d in docs if d]
    shas = [s for s in shas if s]
    if not shas or not any(store.count(sha=s, model=model_id) for s in shas):
        return None
    try:
        vectors = list(_embed(queries) or [])
    except Exception:
        vectors = []
    if len(vectors) != len(queries) or not any(vectors):
        return None
    hits = store.rank_multi(shas, model_id, vectors, top_k=top_k)
    return hits or None
