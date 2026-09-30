"""graph_rag.py — Temporal Graph RAG for Agent Mode.

Given a chain execution, builds a tiny graph fragment describing what happened
(which nodes ran, what data they stored, what TTS events fired). The agent
queries fragments to decide its next action — never raw execution output.

Optimised for 3B models: fragments are < 1 KB, never dump raw data, only
summaries, counts, and event signals.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Fragment cap — keep the small-model context window unclogged ──
_MAX_FRAGMENT_TEXT_LEN = 500  # chars of formatted text returned to the LLM
_SNAPSHOT_CHARS = 100         # how many chars of a value to include in a snapshot
_AGENT_VISIBLE_CONTENT_CHARS = 300  # chars of full text from agent-visible context nodes


class GraphRAG:
    """Lightweight Temporal Graph RAG for agent-mode chain execution memory.

    Usage::

        grag = GraphRAG()

        # After chain execution:
        note = grag.ingest_chain_run(chain_path, executor)
        # note is a short string like "found 47 products on Amazon [ctx_1]"

        # When the agent needs context for a user query:
        context = grag.query_relevant("what did we find on Amazon?")
        # context is formatted text for injection into the LLM prompt
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest_chain_run(
        self, chain_path: str, executor: Any
    ) -> str:
        """Build a graph fragment from a completed chain execution.

        Args:
            chain_path: Path to the chain JSON file that was executed.
            executor: The ``WorkflowExecutor`` instance after ``play_chain()``
                      (provides access to ``_context_db`` and ``chain_id``).

        Returns:
            A short human-readable note summarising what happened
            (e.g. ``"found 47 products on Amazon [ctx_1]"``).
        """
        # ── 1. Load chain metadata ────────────────────────────────
        try:
            with open(chain_path, "r", encoding="utf-8") as f:
                chain_config = json.load(f)
        except Exception as exc:
            logger.warning("GraphRAG: cannot read chain %s: %s", chain_path, exc)
            return "chain file unreadable"

        chain_id = os.path.splitext(os.path.basename(chain_path))[0]
        description = (chain_config.get("description") or "").strip()

        # ── 2. Gather node-level information ──────────────────────
        context_nodes_data = self._collect_context_nodes(
            chain_config, executor, chain_path
        )
        code_nodes_data = self._collect_code_nodes(chain_config)
        llm_nodes_data = self._collect_llm_nodes(chain_config)
        tts_nodes_data = self._collect_tts_nodes(chain_config)

        total_chars = context_nodes_data.get("total_stored_chars", 0)

        # ── 3. Build the fragment ─────────────────────────────────
        fragment: Dict[str, Any] = {
            "chain_id": chain_id,
            "description": description,
            "timestamp": time.time(),
            "context_nodes": context_nodes_data.get("entries", []),
            "code_nodes": code_nodes_data,
            "llm_nodes": llm_nodes_data,
            "tts_nodes": tts_nodes_data,
            "total_output_chars": total_chars,
            "status": "success",
        }

        # ── 4. Persist via ContextDatabase ────────────────────────
        self._store_fragment(fragment)

        # ── 5. Return a compact human-readable note ───────────────
        note_parts: List[str] = []
        if description:
            note_parts.append(description[:80])

        for ctx in fragment["context_nodes"]:
            snap = ctx.get("snapshot", "")
            if snap:
                note_parts.append(snap)

        for tts in fragment["tts_nodes"]:
            text = tts.get("spoken_text", "")
            if text:
                note_parts.append(f"signal: {text[:60]}")

        note = "; ".join(note_parts) if note_parts else "executed"
        if len(note) > 200:
            note = note[:200] + "..."

        logger.info(
            "GraphRAG: ingested chain '%s' (%d context keys, %d TTS events)",
            chain_id,
            len(fragment["context_nodes"]),
            len(fragment["tts_nodes"]),
        )
        return note

    def query_relevant(
        self, query: str, max_fragments: int = 3
    ) -> str:
        """Return relevant recent fragments formatted for a 3B LLM.

        Uses lightweight token-based matching (no sentence_transformers)
        with a temporal recency bonus.

        Args:
            query: The user's current message or request.
            max_fragments: Maximum number of fragments to include.

        Returns:
            A short block of text (≤500 chars) or empty string.
        """
        fragments = self._load_all_fragments()
        if not fragments:
            return ""

        query_lower = query.lower().strip()
        query_tokens = {
            w for w in query_lower.split() if len(w) >= 2
        }

        now = time.time()
        scored: List[tuple[Dict[str, Any], float]] = []

        for frag in fragments:
            score = 0.0

            # ── Description match ─────────────────────────────────
            desc = (frag.get("description") or "").lower()
            node_text = self._fragment_keywords(frag).lower()

            for token in query_tokens:
                if token in desc:
                    score += len(token) * 2
                if token in node_text:
                    score += len(token)

            # ── Temporal bonus (recency 1.5x) ────────────────────
            age = now - frag.get("timestamp", now)
            if age < 60:            # < 1 minute
                score *= 1.5
            elif age < 300:          # < 5 minutes
                score *= 1.2

            if score > 0:
                scored.append((frag, score))

        if not scored:
            return ""

        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:max_fragments]

        # ── Format: one line per fragment ─────────────────────────
        lines: List[str] = []
        for frag, _score in top:
            cid = frag.get("chain_id", "?")
            desc = frag.get("description", "") or ""
            ctx_hints = []
            agent_content = ""
            for ctx in frag.get("context_nodes", []):
                snap = ctx.get("snapshot", "")
                if snap:
                    ctx_hints.append(snap[:60])
                # Include agent-visible content in output
                if ctx.get("agent_visible") and ctx.get("agent_visible_content"):
                    agent_content = ctx["agent_visible_content"]
            tts_hints = []
            for tts in frag.get("tts_nodes", []):
                txt = tts.get("spoken_text", "")
                if txt:
                    tts_hints.append(txt[:60])

            parts = [cid]
            if desc:
                parts.append(desc[:80])
            if agent_content:
                parts.append(agent_content)
            elif ctx_hints:
                parts.append("; ".join(ctx_hints[:2]))
            if tts_hints:
                parts.append("; ".join(tts_hints[:2]))
            lines.append(" - " + ": ".join(parts))

        text = "Recent:\n" + "\n".join(lines)
        if len(text) > _MAX_FRAGMENT_TEXT_LEN:
            text = text[:_MAX_FRAGMENT_TEXT_LEN] + "..."

        logger.debug(
            "GraphRAG: query_relevant returned %d fragments (%d chars)",
            len(top), len(text),
        )
        return text

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _collect_context_nodes(
        self, chain_config: dict, executor: Any, chain_path: str
    ) -> dict:
        """Extract context node summaries from the ContextDatabase.

        Uses the executor's ``_context_db`` if available (inner chain execution
        creates one), otherwise falls back to a fresh ``ContextDatabase()``
        instance (they all share the same SQLite file).
        """
        result: Dict[str, Any] = {"entries": [], "total_stored_chars": 0}

        from AI.context_database import ContextDatabase
        context_db = getattr(executor, "_context_db", None)
        if context_db is None:
            # Fall back to a fresh connection — the database file is shared.
            context_db = ContextDatabase()

        chain_id_raw = getattr(executor, "chain_id", None)
        if not chain_id_raw:
            # Derive from chain_path rather than giving up.
            chain_id_raw = chain_path

        ctx_nodes = chain_config.get("context_nodes") or []
        if not ctx_nodes:
            return result

        # Possible scope_ids that may have been used during execution
        candidate_scope_ids: set = set()

        # For normal (non-temp) chains the scope_id is chain_id_raw
        if "temp_chain" not in str(chain_id_raw):
            candidate_scope_ids.add(chain_id_raw)
        else:
            candidate_scope_ids.add("persistent_chain")

        # Also try the file basename and chain file dir
        chain_basename = os.path.basename(chain_path)
        candidate_scope_ids.add(chain_basename)
        candidate_scope_ids.add(chain_id_raw)

        for ctx_node in ctx_nodes:
            node_id = ctx_node.get("node_id", "")
            if not node_id:
                continue

            data = ctx_node.get("data", {}) if isinstance(ctx_node, dict) else {}
            scope = data.get("scope", "chain")
            node_keys = json.loads(data.get("keys", "[]")) if isinstance(data.get("keys"), str) else (data.get("keys") or [])

            # Try each candidate scope_id to find stored data
            exported = None
            for sid in candidate_scope_ids:
                try:
                    exported = context_db.export_context(scope, sid, node_id)
                    if exported.get("keys"):
                        break
                except Exception:
                    continue

            if not exported or not exported.get("keys"):
                continue

            keys_data = exported["keys"]
            key_count = len(keys_data)
            total_chars = 0

            # Check if this context node is marked agent-visible
            node_cfg = ctx_node.get("data", {}) if isinstance(ctx_node, dict) else {}
            raw_visible = node_cfg.get("agent_visible", False)
            if isinstance(raw_visible, str):
                agent_visible = raw_visible.strip().lower() in ("true", "1", "yes", "on")
            else:
                agent_visible = bool(raw_visible)

            # Build a compact snapshot from the first key's data
            snapshot = ""
            # Full text content for agent-visible nodes
            agent_text_parts = []
            for key_name, key_values in keys_data.items():
                if isinstance(key_values, list):
                    total_chars += sum(
                        len(str(v)) for v in key_values
                    )
                    if not snapshot and key_values:
                        first_val = key_values[-1]  # most recent
                        if isinstance(first_val, dict):
                            raw = first_val.get("input_received", "") or json.dumps(first_val)
                        else:
                            raw = str(first_val)
                        snapshot = raw[:_SNAPSHOT_CHARS]
                        if len(raw) > _SNAPSHOT_CHARS:
                            snapshot += "..."
                    # Build agent-visible content from all entries
                    if agent_visible:
                        for v in key_values:
                            if isinstance(v, dict):
                                inp = v.get("input_received", "") or ""
                                out = v.get("output_generated", "") or ""
                                if inp:
                                    agent_text_parts.append(str(inp)[:_SNAPSHOT_CHARS])
                                if out:
                                    agent_text_parts.append(str(out)[:_SNAPSHOT_CHARS])
                            else:
                                agent_text_parts.append(str(v)[:_SNAPSHOT_CHARS])
                elif isinstance(key_values, str):
                    total_chars += len(key_values)
                    if not snapshot:
                        snapshot = key_values[:_SNAPSHOT_CHARS]
                        if len(key_values) > _SNAPSHOT_CHARS:
                            snapshot += "..."
                    if agent_visible:
                        agent_text_parts.append(key_values[:_SNAPSHOT_CHARS])

            entry = {
                "node_id": node_id,
                "keys": node_keys or list(keys_data.keys()),
                "key_count": key_count,
                "snapshot": snapshot,
            }
            if agent_visible and agent_text_parts:
                entry["agent_visible"] = True
                combined = " | ".join(agent_text_parts)
                entry["agent_visible_content"] = combined[:_AGENT_VISIBLE_CONTENT_CHARS]
            result["entries"].append(entry)
            result["total_stored_chars"] += total_chars

        return result

    def _collect_code_nodes(self, chain_config: dict) -> list:
        """Extract code node descriptions from the chain config."""
        result = []
        for node in chain_config.get("code_nodes") or []:
            data = node.get("data", {}) if isinstance(node, dict) else {}
            desc = data.get("description", "") or data.get("name", "")
            result.append({
                "node_id": node.get("node_id", ""),
                "description": desc[:80] if desc else "",
            })
        return result

    def _collect_llm_nodes(self, chain_config: dict) -> list:
        """Extract LLM node prompt hints from the chain config."""
        result = []
        for node in chain_config.get("llm_nodes") or []:
            data = node.get("data", {}) if isinstance(node, dict) else node
            prompt = data.get("prompt", "") or data.get("llm_configuration", {}).get("prompt", "")
            output_var = data.get("llm_configuration", {}).get("output_variable", "") if isinstance(data, dict) else ""
            hint = (prompt or output_var)[:80]
            result.append({
                "node_id": node.get("node_id", ""),
                "prompt_hint": hint,
            })
        return result

    def _collect_tts_nodes(self, chain_config: dict) -> list:
        """Extract TTS event signals from the chain config.

        TTS output text is captured directly because it IS the event signal.
        """
        result = []
        for node in chain_config.get("tts_nodes") or []:
            data = node.get("data", {}) if isinstance(node, dict) else {}
            text = (data.get("text") or data.get("message") or "").strip()
            result.append({
                "node_id": node.get("node_id", ""),
                "spoken_text": text,
            })
        return result

    def _fragment_keywords(self, frag: dict) -> str:
        """Build a flat keyword string from all nodes in a fragment."""
        parts: List[str] = []

        # Chain description
        desc = frag.get("description", "") or ""
        if desc:
            parts.append(desc)

        # Context node keys, snapshots, and agent-visible content
        for ctx in frag.get("context_nodes", []):
            for k in ctx.get("keys", []):
                parts.append(str(k))
            snap = ctx.get("snapshot", "")
            if snap:
                parts.append(snap)
            ag = ctx.get("agent_visible_content", "")
            if ag:
                parts.append(ag)

        # Code descriptions
        for cd in frag.get("code_nodes", []):
            d = cd.get("description", "")
            if d:
                parts.append(d)

        # LLM prompts
        for llm in frag.get("llm_nodes", []):
            h = llm.get("prompt_hint", "")
            if h:
                parts.append(h)

        # TTS signals
        for tts in frag.get("tts_nodes", []):
            t = tts.get("spoken_text", "")
            if t:
                parts.append(t)

        return " ".join(parts)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _store_fragment(self, fragment: dict) -> None:
        """Persist the fragment via ContextDatabase (agent scope)."""
        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            db.store_graph_fragment(fragment)
        except Exception as exc:
            logger.warning("GraphRAG: failed to store fragment: %s", exc)

    def _load_all_fragments(self) -> list[dict]:
        """Load all stored graph fragments from ContextDatabase."""
        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            return db.query_graph_fragments()
        except Exception as exc:
            logger.warning("GraphRAG: failed to load fragments: %s", exc)
            return []
