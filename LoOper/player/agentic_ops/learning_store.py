"""
LearningStore — Local-first hybrid memory for LoOper.

Three-tier backend (auto-degrading):
  1. SQLite + FTS5   — always available, full-text BM25 search
  2. Qdrant (disk)   — dense vector search (when qdrant-client installed)
  3. FastEmbed (ONNX) — local embeddings, 384d (when fastembed installed)
  4. Honcho (remote)  — optional sync to Honcho server

Search fuses FTS5 + dense vectors via Reciprocal Rank Fusion (RRF).
Runs fully local with zero external services by default.

Extends the original LearningStore (which was Honcho-only) with a
local-first architecture that works even when Honcho is unavailable.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class LearningStore:
    """Local-first hybrid memory with optional Honcho sync.

    Provides:
    - save(content, tags, metadata) → row_id
    - search(query, limit, tag_filter) → list of dicts
    - record(learning_dict) → stores structured experience
    - record_experience(issue, cause, fix, tags) → row_id
    - relevant(text, limit) → backward-compat with old API
    - format_for_prompt(results) → formatted string for system prompts
    """

    def __init__(self, base_dir: str, log_event: Optional[Callable] = None):
        self._base_dir = base_dir
        self._log_event = log_event or (lambda *a, **kw: None)

        # Data directory
        self._data_dir = os.path.join(base_dir, "AI", "memory_data")
        os.makedirs(self._data_dir, exist_ok=True)

        # ── Tier 1: SQLite + FTS5 (always available) ──
        self._db_path = os.path.join(self._data_dir, "learnings.db")
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._init_db()
        self._migrate_json()

        # ── Tier 2: Qdrant vector search (optional) ──
        self._qdrant = None
        self._qdrant_available = False
        self._init_qdrant()

        # ── Tier 3: FastEmbed dense embeddings (optional) ──
        self._embedder = None
        self._embedder_available = False
        self._init_embedder()

        # ── Tier 4: Honcho remote sync (optional) ──
        self._honcho = None
        self._honcho_session = None
        self._honcho_user_peer = None
        self._honcho_assistant_peer = None
        self._honcho_session_id = os.getenv(
            "LOOPER_HONCHO_SESSION_ID", "looper-agent-session"
        )
        self._honcho_user_peer_id = os.getenv("LOOPER_HONCHO_USER_PEER_ID", "user")
        self._honcho_assistant_peer_id = os.getenv(
            "LOOPER_HONCHO_ASSISTANT_PEER_ID", "looper_agent"
        )
        self._honcho_workspace_id = os.getenv("HONCHO_WORKSPACE_ID", "looper")
        self._honcho_base_url = (
            os.getenv("LOOPER_HONCHO_URL")
            or os.getenv("HONCHO_URL")
            or "http://localhost:8000"
        )
        self._honcho_api_key = os.getenv("HONCHO_API_KEY")
        self._initialize_honcho()

        logger.info(
            "LearningStore ready: fts5=yes qdrant=%s embed=%s honcho=%s",
            self._qdrant_available,
            self._embedder_available,
            bool(self._honcho_session),
        )

    # ── Tier 1: SQLite + FTS5 ───────────────────────────────────

    def _init_db(self):
        """Initialize SQLite with FTS5 for full-text search."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")

        # Main table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tag TEXT NOT NULL DEFAULT 'raw',
                content TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        # FTS5 virtual table for full-text search
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS learnings_fts USING fts5(
                content, tag,
                content='learnings',
                content_rowid='id'
            )
        """)

        # Triggers to keep FTS5 index in sync
        for trigger_sql in [
            """CREATE TRIGGER IF NOT EXISTS learnings_ai AFTER INSERT ON learnings BEGIN
                INSERT INTO learnings_fts(rowid, content, tag)
                VALUES (new.id, new.content, new.tag);
            END""",
            """CREATE TRIGGER IF NOT EXISTS learnings_ad AFTER DELETE ON learnings BEGIN
                INSERT INTO learnings_fts(learnings_fts, rowid, content, tag)
                VALUES ('delete', old.id, old.content, old.tag);
            END""",
            """CREATE TRIGGER IF NOT EXISTS learnings_au AFTER UPDATE ON learnings BEGIN
                INSERT INTO learnings_fts(learnings_fts, rowid, content, tag)
                VALUES ('delete', old.id, old.content, old.tag);
                INSERT INTO learnings_fts(rowid, content, tag)
                VALUES (new.id, new.content, new.tag);
            END""",
        ]:
            try:
                conn.execute(trigger_sql)
            except Exception:
                pass

        conn.commit()
        conn.close()

    def _migrate_json(self):
        """One-time migration: import old agent_learnings.json into SQLite."""
        marker = os.path.join(self._data_dir, ".migrated_json")
        if os.path.exists(marker):
            return

        json_path = os.path.join(self._base_dir, "AI", "agent_learnings.json")
        if not os.path.exists(json_path):
            # Mark done even if no file
            with open(marker, "w") as f:
                f.write("done")
            return

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            if not isinstance(entries, list):
                entries = []

            conn = self._get_conn()
            count = 0
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                issue = entry.get("issue", "")
                cause = entry.get("cause", "")
                fix = entry.get("fix", "")
                tags = entry.get("tags", [])
                ts = entry.get("timestamp", time.time())

                content = f"Issue: {issue}\nCause: {cause}\nFix: {fix}"
                meta = json.dumps(
                    {
                        "type": "experience",
                        "issue": issue,
                        "cause": cause,
                        "fix": fix,
                        "source": "migrated_json",
                    }
                )
                tag = ",".join(tags) if tags else "experience"

                conn.execute(
                    "INSERT INTO learnings (tag, content, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (tag, content, meta, ts, ts),
                )
                count += 1

            conn.commit()
            logger.info("Migrated %d entries from agent_learnings.json", count)
        except Exception as e:
            logger.warning("JSON migration failed: %s", e)
        finally:
            try:
                with open(marker, "w") as f:
                    f.write("done")
            except Exception:
                pass

    # ── Tier 2: Qdrant ──────────────────────────────────────────

    def _init_qdrant(self):
        """Initialize Qdrant lazily - don't block startup."""
        self._qdrant = None
        self._qdrant_available = False
        self._qdrant_init_attempted = False

    def _ensure_qdrant(self):
        """Lazy-init Qdrant on first use. Returns True if ready."""
        if self._qdrant_available:
            return True
        if self._qdrant_init_attempted:
            return False
        self._qdrant_init_attempted = True
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
            qdrant_path = os.path.join(self._data_dir, "qdrant")
            self._qdrant = QdrantClient(path=qdrant_path)
            try:
                self._qdrant.create_collection(
                    collection_name="looper_learnings",
                    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
                )
            except Exception:
                pass
            self._qdrant_available = True
            logger.info("Qdrant initialized (disk mode)")
            return True
        except ImportError:
            logger.debug("qdrant-client not installed")
        except Exception as e:
            logger.debug("Qdrant init failed: %s", e)
        return False

    # ── Tier 3: FastEmbed ───────────────────────────────────────

    def _init_embedder(self):
        """Initialize FastEmbed lazily - don't block startup on model download."""
        self._embedder = None
        self._embedder_available = False
        self._embedder_init_attempted = False

    def _ensure_embedder(self):
        """Lazy-init FastEmbed on first use. Returns True if ready."""
        if self._embedder_available:
            return True
        if self._embedder_init_attempted:
            return False
        self._embedder_init_attempted = True
        try:
            import os as _os; _os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1'); from fastembed import TextEmbedding
            self._embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
            self._embedder_available = True
            logger.info("FastEmbed initialized (bge-small-en-v1.5, 384d)")
            return True
        except ImportError:
            logger.debug("fastembed not installed")
        except Exception as e:
            logger.warning("FastEmbed init failed: %s", e)
        return False

    # ── Tier 4: Honcho ──────────────────────────────────────────

    def _initialize_honcho(self):
        try:
            repo_root = os.path.dirname(self._base_dir.rstrip("\\/"))
            sdk_path = os.path.join(
                repo_root, "utils", "honcho", "sdks", "python", "src"
            )
            if os.path.isdir(sdk_path):
                import sys

                if sdk_path not in sys.path:
                    sys.path.append(sdk_path)
            from honcho import Honcho

            self._honcho = Honcho(
                api_key=self._honcho_api_key,
                base_url=self._honcho_base_url,
                workspace_id=self._honcho_workspace_id,
            )
            self._honcho_session = self._honcho.session(self._honcho_session_id)
            self._honcho_user_peer = self._honcho.peer(self._honcho_user_peer_id)
            self._honcho_assistant_peer = self._honcho.peer(
                self._honcho_assistant_peer_id
            )
            logger.info("Honcho connected: %s", self._honcho_base_url)
        except Exception:
            self._honcho = None
            self._honcho_session = None
            self._honcho_user_peer = None
            self._honcho_assistant_peer = None

    # ── Connection management ────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    # ── Core API: save ───────────────────────────────────────────

    def save(
        self,
        content: str,
        tags: Optional[List[str]] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        """Save an entry. Returns row ID (0 on failure)."""
        tag = "raw"
        if tags:
            tag = ",".join(tags) if len(tags) > 1 else tags[0]

        meta = json.dumps(metadata or {})
        now = time.time()

        with self._write_lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "INSERT INTO learnings (tag, content, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (tag, content, meta, now, now),
            )
            conn.commit()
            row_id = cursor.lastrowid

        # Mirror to Qdrant
        if self._ensure_qdrant() and self._ensure_embedder() and row_id:
            self._index_in_qdrant(row_id, content, tag)

        return row_id or 0

    def _index_in_qdrant(self, row_id: int, content: str, tag: str):
        try:
            embeddings = list(self._embedder.embed([content[:2000]]))
            if embeddings:
                from qdrant_client.models import PointStruct

                self._qdrant.upsert(
                    collection_name="looper_learnings",
                    points=[
                        PointStruct(
                            id=row_id,
                            vector=embeddings[0].tolist(),
                            payload={"tag": tag, "row_id": row_id},
                        )
                    ],
                )
        except Exception as e:
            logger.debug("Qdrant index skipped: %s", e)

    # ── Core API: search ─────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 5,
        tag_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Hybrid search: FTS5 BM25 + optional Qdrant dense, fused via RRF."""
        results: Dict[int, dict] = {}
        scores: Dict[int, float] = {}

        # 1. FTS5 BM25
        for i, row in enumerate(self._search_fts5(query, limit * 2, tag_filter)):
            rid = row["id"]
            results[rid] = dict(row)
            scores[rid] = scores.get(rid, 0) + (1.0 / (i + 60))

        # 2. Dense (Qdrant)
        if self._ensure_qdrant() and self._ensure_embedder():
            for i, row in enumerate(self._search_dense(query, limit * 2, tag_filter)):
                rid = row["id"]
                if rid not in results:
                    results[rid] = dict(row)
                scores[rid] = scores.get(rid, 0) + (1.0 / (i + 60))

        # Sort by RRF score
        sorted_ids = sorted(scores, key=scores.get, reverse=True)
        return [results[rid] for rid in sorted_ids[:limit]]

    def _search_fts5(
        self, query: str, limit: int, tag_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        try:
            safe_query = " ".join(f'"{w}"' for w in query.split() if w)
            if not safe_query:
                safe_query = query.replace("'", "''")

            if tag_filter:
                rows = conn.execute(
                    """SELECT m.id, m.tag, m.content, m.metadata, m.created_at
                       FROM learnings_fts f JOIN learnings m ON m.id = f.rowid
                       WHERE learnings_fts MATCH ? AND m.tag LIKE ?
                       ORDER BY rank LIMIT ?""",
                    (safe_query, f"%{tag_filter}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT m.id, m.tag, m.content, m.metadata, m.created_at
                       FROM learnings_fts f JOIN learnings m ON m.id = f.rowid
                       WHERE learnings_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (safe_query, limit),
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception:
            # Fallback to LIKE
            like_q = f"%{query}%"
            if tag_filter:
                rows = conn.execute(
                    "SELECT id, tag, content, metadata, created_at FROM learnings WHERE content LIKE ? AND tag LIKE ? ORDER BY created_at DESC LIMIT ?",
                    (like_q, f"%{tag_filter}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, tag, content, metadata, created_at FROM learnings WHERE content LIKE ? ORDER BY created_at DESC LIMIT ?",
                    (like_q, limit),
                ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def _search_dense(
        self, query: str, limit: int, tag_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        try:
            embeddings = list(self._embedder.embed([query]))
            if not embeddings:
                return []

            query_filter = None
            if tag_filter:
                from qdrant_client.models import FieldCondition, Filter, MatchValue

                query_filter = Filter(
                    must=[FieldCondition(key="tag", match=MatchValue(value=tag_filter))]
                )

            hits = self._qdrant.search(
                collection_name="looper_learnings",
                query_vector=embeddings[0].tolist(),
                limit=limit,
                query_filter=query_filter,
            )

            conn = self._get_conn()
            results = []
            for hit in hits:
                row = conn.execute(
                    "SELECT id, tag, content, metadata, created_at FROM learnings WHERE id = ?",
                    (hit.id,),
                ).fetchone()
                if row:
                    results.append(self._row_to_dict(row))
            return results
        except Exception:
            return []

    def _row_to_dict(self, row) -> Dict[str, Any]:
        if isinstance(row, dict):
            return row
        d = dict(row)
        if "metadata" in d and isinstance(d["metadata"], str):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except json.JSONDecodeError:
                d["metadata"] = {}
        return d

    # ── Experience recording ─────────────────────────────────────

    def record(self, learning: Optional[dict]) -> Optional[int]:
        """Record a structured learning. Backward-compat with old API.

        Returns row_id or None.
        """
        if not learning:
            return None

        issue = str(learning.get("issue", "")).strip()
        cause = str(learning.get("cause", "")).strip()
        fix = str(learning.get("fix", "")).strip()
        tags = learning.get("tags", [])

        # Save locally first (always works)
        row_id = self.record_experience(issue, cause, fix, tags)

        # Mirror to Honcho if available
        entry = {
            "issue": issue,
            "cause": cause,
            "fix": fix,
            "bad_example": str(learning.get("bad_example", "")).strip(),
            "good_example": str(learning.get("good_example", "")).strip(),
            "tags": tags,
            "timestamp": time.time(),
        }
        if self._honcho_session and self._honcho_assistant_peer:
            try:
                content = json.dumps(entry, ensure_ascii=False)
                msg = self._honcho_assistant_peer.message(
                    content,
                    metadata={"looper_type": "learning", "tags": tags or []},
                )
                self._honcho_session.add_messages(msg)
                self._log_event(
                    "learning_recorded",
                    {
                        "backend": "honcho+local",
                        "entry": entry,
                    },
                )
            except Exception:
                pass

        return row_id

    def record_experience(
        self,
        issue: str,
        cause: str,
        fix: str,
        tags: Optional[List[str]] = None,
    ) -> int:
        """Record a learned experience (issue → cause → fix). Returns row ID."""
        content = f"Issue: {issue}\nCause: {cause}\nFix: {fix}"
        meta = {"type": "experience", "issue": issue, "cause": cause, "fix": fix}
        all_tags = (tags or []) + ["experience"]
        return self.save(content, all_tags, meta)

    # ── Backward-compat API ──────────────────────────────────────

    def load(self) -> List[Dict[str, Any]]:
        """Load all entries. (Used by failure analyzer.)"""
        return self.get_recent(limit=200)

    def relevant(self, text: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Find entries relevant to a query. Backward-compat with old API.

        Tries local FTS5 first. Falls back to Honcho search if local has no results.
        """
        query = str(text or "").strip()
        if not query:
            return self.get_recent(limit=limit)

        # Local search first
        results = self.search(query, limit=limit)

        # If local has results, return them
        if results:
            return results

        # Fall back to Honcho
        if self._honcho_session:
            try:
                messages = self._honcho_session.search(
                    query=query,
                    filters={"metadata": {"looper_type": "learning"}},
                    limit=limit,
                )
            except Exception:
                messages = []

            honcho_results = []
            for msg in messages or []:
                try:
                    raw = getattr(msg, "content", "") or ""
                    item = (
                        json.loads(raw)
                        if raw.strip().startswith("{")
                        else {"issue": raw}
                    )
                    if isinstance(item, dict):
                        honcho_results.append(item)
                except Exception:
                    continue
            return honcho_results[:limit]

        return []

    def format_for_prompt(self, learnings: List[Dict[str, Any]]) -> str:
        """Format search results for system prompt injection."""
        if not learnings:
            return "None"
        lines = []
        for idx, item in enumerate(learnings, start=1):
            if isinstance(item, dict):
                issue = item.get("issue", "") or ""
                fix = item.get("fix", "") or ""
                content = item.get("content", "")[:200]
                tag = item.get("tag", "")
                if issue and fix:
                    lines.append(f"{idx}. Issue: {issue} | Fix: {fix}")
                elif content:
                    lines.append(f"{idx}. [{tag}] {content}")
            elif isinstance(item, str):
                lines.append(f"{idx}. {item[:200]}")
        return "\n".join(lines) if lines else "None"

    # ── Utility ──────────────────────────────────────────────────

    def get_recent(
        self, limit: int = 20, tag_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        if tag_filter:
            rows = conn.execute(
                "SELECT id, tag, content, metadata, created_at FROM learnings WHERE tag LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f"%{tag_filter}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, tag, content, metadata, created_at FROM learnings ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def count(self, tag_filter: Optional[str] = None) -> int:
        conn = self._get_conn()
        if tag_filter:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM learnings WHERE tag LIKE ?",
                (f"%{tag_filter}%",),
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) as cnt FROM learnings").fetchone()
        return row["cnt"] if row else 0

    def delete(self, memory_id: int) -> bool:
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM learnings WHERE id = ?", (memory_id,))
            conn.commit()
        return True

    def clear(self) -> int:
        """Delete all entries. Returns count deleted."""
        count = self.count()
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("DELETE FROM learnings")
            conn.commit()
        return count


    def close(self):
        """Explicitly close Qdrant/SQLite to avoid shutdown errors."""
        if self._qdrant is not None:
            try:
                self._qdrant.close()
            except Exception:
                pass
            self._qdrant = None
            self._qdrant_available = False
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _store_path(self):
        return os.path.join(self._base_dir, "AI", "agent_learnings.json")
