#!/usr/bin/env python3
"""
Context Database Module

Simple append-only key-value store for context data used by workflow nodes.
Stores entries per chain+node+key tuple, retrieves most recent N entries.
"""

import os
import json
import sys
import tempfile
import time
import sqlite3
import logging
import threading
from typing import Dict, List, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class ContextDatabase:
    """Simple append-only context store for workflow nodes."""

    @staticmethod
    def normalize_chain_id(chain_id: str) -> str:
        """Normalize chain_id to consistent short-name format.

        Accepts full path, partial path, or bare name and returns the
        filename without extension.  Ensures that all consumers (push,
        pull, clear, delete) use the same chain_id format so that
        cleanup operations never silently match zero rows.

        Examples::

            >>> ContextDatabase.normalize_chain_id(
            ...     "D:\\chains\\BASE_SYSTEM_CHAIN.json")
            'BASE_SYSTEM_CHAIN'
            >>> ContextDatabase.normalize_chain_id("BASE_SYSTEM_CHAIN")
            'BASE_SYSTEM_CHAIN'
        """
        raw = str(chain_id)
        return os.path.splitext(os.path.basename(raw))[0]

    def __init__(self, db_path: Optional[str] = None, row_cap: int = 0):
        """Initialize the context database.

        Args:
            db_path: SQLite file path (defaults to the project data dir).
            row_cap: Maximum rows kept per (chain_id, node_id) when pushing.
                0 disables the cap (UNLIMITED - the pool keeps its whole
                history so nothing a node wrote is silently evicted).
        """
        if db_path is None:
            if getattr(sys, 'frozen', False):
                # Frozen builds: _MEIPASS is ephemeral — the DB would be
                # recreated on every restart.  Use the durable runtime dir.
                try:
                    from AI.runtime_paths import get_runtime_dir
                    db_dir = get_runtime_dir()
                except Exception:
                    db_dir = os.path.join(tempfile.gettempdir(), "Arrow")
            else:
                # Full app (source tree): keep the project data directory.
                project_root = Path(__file__).parent.parent
                db_dir = project_root / 'data'
            os.makedirs(str(db_dir), exist_ok=True)
            db_path = os.path.join(str(db_dir), 'context.db')

        self.db_path = db_path
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._row_cap = max(0, int(row_cap))
        # (chain_id, node_id) -> approx row count, for amortized trimming
        self._row_counters: Dict[tuple, int] = {}
        self._init_db()

    def _init_db(self):
        """Initialize schema if needed."""
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS context_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT,
                    source_node_id TEXT,
                    source_type TEXT,
                    created_at REAL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entries_lookup
                ON context_entries(chain_id, node_id, key, created_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entries_chain
                ON context_entries(chain_id)
            """)

            conn.commit()
            try:
                cursor.execute("PRAGMA journal_mode=WAL;")
                conn.commit()
            except Exception:
                pass
            conn.close()
            logger.info(f"Initialized context database at {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")

    def _get_conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
            except Exception:
                pass
            self._local.conn = conn
        return conn

    def push(self, chain_id: str, node_id: str, key: str, value: Any,
             source_node_id: Optional[str] = None,
             source_type: Optional[str] = None) -> int:
        """Append a single entry to the store.

        Args:
            chain_id: Chain identifier (normalized internally).
            node_id: Context node ID.
            key: Semantic key (auto-derived from upstream node).
            value: Any JSON-serializable value to store.
            source_node_id: ID of the node that produced this value.
            source_type: Type of the source node ('llm', 'code', etc.).

        Returns:
            Row ID of the inserted entry.
        """
        try:
            chain_id = self.normalize_chain_id(chain_id)
            conn = self._get_conn()
            value_json = json.dumps(value) if not isinstance(value, str) else value
            with self._write_lock:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO context_entries
                    (chain_id, node_id, key, value, source_node_id, source_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (chain_id, node_id, key, value_json, source_node_id, source_type, time.time()))
                row_id = cursor.lastrowid
                # Rolling row cap: keep only the newest ``self._row_cap`` rows
                # per (chain_id, node_id).  Amortized via an in-memory counter
                # so the DELETE only runs when the cap is exceeded, not on
                # every push.  Uses the existing (chain_id, node_id) index
                # prefix of idx_entries_lookup.
                if self._row_cap > 0:
                    _key = (chain_id, node_id)
                    _count = self._row_counters.get(_key, 0) + 1
                    self._row_counters[_key] = _count
                    if _count > self._row_cap:
                        cursor.execute("""
                            DELETE FROM context_entries
                            WHERE chain_id = ? AND node_id = ? AND id NOT IN (
                                SELECT id FROM context_entries
                                WHERE chain_id = ? AND node_id = ?
                                ORDER BY created_at DESC, id DESC
                                LIMIT ?
                            )
                        """, (chain_id, node_id, chain_id, node_id, self._row_cap))
                        self._row_counters[_key] = self._row_cap
                conn.commit()
                return row_id
        except sqlite3.Error as e:
            logger.error(f"Error in push: {e}")
            return -1

    def pull(self, chain_id: Optional[str] = None,
             node_id: Optional[str] = None,
             key: Optional[str] = None,
             limit: int = 10) -> Dict[str, List[Any]]:
        """Retrieve the most recent entries for a context node.

        Args:
            chain_id: Chain identifier (normalized internally).  When None
                and *node_id* is given, entries for that node are pulled
                across ALL chains (used by the dialog preview: GUI runs
                store rows under a deterministic temp chain id that differs
                from the editor's chain file, while node ids are unique).
            node_id: Context node ID.  When None, entries from ALL nodes of
                the chain are returned (used by shared-context readers that
                consume a parent chain's store).
            key: Optional key filter. If None, returns all keys.
            limit: Max entries per key.

        Returns:
            Dict mapping key -> list of parsed values (newest first).
        """
        try:
            if chain_id:
                chain_id = self.normalize_chain_id(chain_id)
            conn = self._get_conn()
            cursor = conn.cursor()

            if chain_id and node_id and key:
                cursor.execute("""
                    SELECT key, value FROM context_entries
                    WHERE chain_id = ? AND node_id = ? AND key = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (chain_id, node_id, key, limit))
            elif chain_id and key:
                cursor.execute("""
                    SELECT key, value FROM context_entries
                    WHERE chain_id = ? AND key = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (chain_id, key, limit))
            elif node_id and key:
                cursor.execute("""
                    SELECT key, value FROM context_entries
                    WHERE node_id = ? AND key = ?
                    ORDER BY created_at DESC LIMIT ?
                """, (node_id, key, limit))
            elif chain_id and node_id:
                cursor.execute("""
                    SELECT key, value FROM context_entries
                    WHERE chain_id = ? AND node_id = ?
                    ORDER BY created_at DESC
                """, (chain_id, node_id))
            elif node_id:
                cursor.execute("""
                    SELECT key, value FROM context_entries
                    WHERE node_id = ?
                    ORDER BY created_at DESC
                """, (node_id,))
            elif chain_id:
                cursor.execute("""
                    SELECT key, value FROM context_entries
                    WHERE chain_id = ?
                    ORDER BY created_at DESC
                """, (chain_id,))
            else:
                return {}

            rows = cursor.fetchall()
            result: Dict[str, List[Any]] = {}
            for k, v_json in rows:
                if k not in result:
                    result[k] = []
                if len(result[k]) >= limit:
                    continue
                try:
                    result[k].append(json.loads(v_json) if v_json else None)
                except (json.JSONDecodeError, TypeError):
                    result[k].append(v_json)
            return result

        except sqlite3.Error as e:
            logger.error(f"Error in pull: {e}")
            return {}

    def pull_channel(self, chain_id: str, node_id: str,
                     exclude_source_node_id: Optional[str] = None,
                     limit: int = 10) -> list:
        """Pull the newest values from a shared-context feed namespace.

        The feed is keyed by (chain_id, node_id) where node_id is the shared
        identity (the source context node's id).  Every participant appends its
        locally-produced output there; readers pass *exclude_source_node_id*
        (their own node id) so they never re-read their own entries (which
        already appear in their local output) while still receiving every other
        participant's contribution - bidirectional sharing without self-echo.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            if exclude_source_node_id:
                cursor.execute("""
                    SELECT value FROM context_entries
                    WHERE chain_id = ? AND node_id = ? AND source_node_id != ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                """, (str(chain_id), str(node_id),
                       str(exclude_source_node_id), int(limit)))
            else:
                cursor.execute("""
                    SELECT value FROM context_entries
                    WHERE chain_id = ? AND node_id = ?
                    ORDER BY created_at DESC, id DESC LIMIT ?
                """, (str(chain_id), str(node_id), int(limit)))
            return [r[0] for r in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error in pull_channel: {e}")
            return []

    def delete(self, chain_id: str, node_id: str,
                key: Optional[str] = None,
                source_node_id: Optional[str] = None,
                max_age_seconds: Optional[float] = None):
        """Delete entries with optional filters.

        Args:
            chain_id: Chain identifier (normalized internally).
            node_id: Context node ID.
            key: Only delete entries with this key.
            source_node_id: Only delete entries from this source node.
            max_age_seconds: Only delete entries older than this many seconds.
        """
        try:
            chain_id = self.normalize_chain_id(chain_id)
            conn = self._get_conn()
            cursor = conn.cursor()
            wheres = ["chain_id = ?", "node_id = ?"]
            params = [chain_id, node_id]
            if key is not None:
                wheres.append("key = ?")
                params.append(key)
            if source_node_id is not None:
                wheres.append("source_node_id = ?")
                params.append(source_node_id)
            if max_age_seconds is not None:
                cutoff = time.time() - max_age_seconds
                wheres.append("created_at < ?")
                params.append(cutoff)
            with self._write_lock:
                cursor.execute(f"DELETE FROM context_entries WHERE {' AND '.join(wheres)}", params)
                conn.commit()
            # Reset the amortized counter for fully unqualified deletes so the
            # next push starts from a fresh count (stale-high counters are
            # harmless — they just trigger one extra trim DELETE).
            if key is None and source_node_id is None and max_age_seconds is None:
                self._row_counters.pop((chain_id, node_id), None)
            logger.info(f"Deleted {cursor.rowcount} entries from chain={chain_id} node={node_id}")
        except sqlite3.Error as e:
            logger.error(f"Error in delete: {e}")

    def pull_rows(self, chain_id: Optional[str] = None,
                  node_id: Optional[str] = None,
                  limit: int = 0) -> List[dict]:
        """Row-level pull: the newest entries FIRST, each WITH its row id.

        ``pull()`` returns ``{key: [values]}`` with no ids, so it can only be
        shown read-only.  The audit dialog needs the row id to edit a value or
        delete a single entry, so this returns one dict per row.  ``limit`` 0
        (default) means UNLIMITED - the whole stored history.

        When *node_id* is given without *chain_id*, rows are pulled across ALL
        chains (node ids are unique; GUI runs use a temp chain namespace).
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            wheres, params = [], []
            if chain_id:
                wheres.append("chain_id = ?")
                params.append(self.normalize_chain_id(chain_id))
            if node_id:
                wheres.append("node_id = ?")
                params.append(str(node_id))
            if not wheres:
                return []
            sql = ("SELECT id, chain_id, node_id, key, value, source_node_id, "
                   "source_type, created_at FROM context_entries WHERE "
                   + " AND ".join(wheres)
                   + " ORDER BY created_at DESC, id DESC")
            if int(limit) > 0:
                sql += " LIMIT ?"
                params.append(int(limit))
            cursor.execute(sql, params)
            rows = []
            for (rid, ch, nid, key, v_json, src, stype, created) in cursor.fetchall():
                try:
                    value = json.loads(v_json) if v_json else ""
                except (json.JSONDecodeError, TypeError):
                    value = v_json
                rows.append({
                    "id": rid, "chain_id": ch, "node_id": nid, "key": key,
                    "value": value, "source_node_id": src,
                    "source_type": stype, "created_at": created,
                })
            return rows
        except sqlite3.Error as e:
            logger.error(f"Error in pull_rows: {e}")
            return []

    def update_entry(self, row_id: int, value: Any) -> bool:
        """Correct ONE stored entry IN PLACE (the audit dialog's 'edit')."""
        try:
            conn = self._get_conn()
            value_json = json.dumps(value) if not isinstance(value, str) else value
            with self._write_lock:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE context_entries SET value = ? WHERE id = ?",
                    (value_json, int(row_id)),
                )
                conn.commit()
                return bool(cursor.rowcount)
        except sqlite3.Error as e:
            logger.error(f"Error in update_entry: {e}")
            return False

    def delete_entry(self, row_id: int) -> bool:
        """Delete ONE stored entry by row id (the audit dialog's 'delete')."""
        try:
            conn = self._get_conn()
            with self._write_lock:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM context_entries WHERE id = ?", (int(row_id),))
                conn.commit()
                return bool(cursor.rowcount)
        except sqlite3.Error as e:
            logger.error(f"Error in delete_entry: {e}")
            return False

    def clear(self, chain_id: str, node_id: Optional[str] = None) -> int:
        """Clear entries for a chain, optionally filtered by node.

        Args:
            chain_id: Chain identifier (normalized internally).
            node_id: Optional node filter. If None, clears all entries for the chain.

        Returns:
            Number of rows deleted.
        """
        try:
            chain_id = self.normalize_chain_id(chain_id)
            conn = self._get_conn()
            cursor = conn.cursor()
            with self._write_lock:
                if node_id:
                    cursor.execute("""
                        DELETE FROM context_entries WHERE chain_id = ? AND node_id = ?
                    """, (chain_id, node_id))
                    self._row_counters.pop((chain_id, node_id), None)
                else:
                    cursor.execute("""
                        DELETE FROM context_entries WHERE chain_id = ?
                    """, (chain_id,))
                    for _k in [k for k in self._row_counters if k[0] == chain_id]:
                        self._row_counters.pop(_k, None)
                conn.commit()
            logger.info(f"Cleared {cursor.rowcount} entries for chain={chain_id} node={node_id}")
            return cursor.rowcount or 0
        except sqlite3.Error as e:
            logger.error(f"Error in clear: {e}")
            return 0

    # ------------------------------------------------------------------
    # Backward-compat alias (used by GraphRAG)
    # ------------------------------------------------------------------

    def export_context(self, chain_id: str, node_id: str) -> Dict[str, Any]:
        """Alias for pull() — kept for GraphRAG compatibility.

        Returns:
            Dict in format {"keys": {key1: [values...], ...}, "node_id": node_id}
        """
        chain_id = self.normalize_chain_id(chain_id)
        keys_data = self.pull(chain_id=chain_id, node_id=node_id, limit=100)
        return {
            "keys": keys_data,
            "node_id": node_id,
        }

    def close(self):
        conn = getattr(self._local, 'conn', None)
        if conn:
            conn.close()
            self._local.conn = None

    def __del__(self):
        self.close()
