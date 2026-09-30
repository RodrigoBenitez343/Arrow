"""
chain_executor.py — Simple chain executor via MultiSequencePlayer.

Given a chain JSON file path, loads and executes it through the existing
MultiSequencePlayer infrastructure.  After execution, invokes GraphRAG to
build a graph fragment so the agent can reason about what happened.
"""

import json
import logging
import os
import re
import uuid

logger = logging.getLogger(__name__)


class ChainExecutor:
    """Runs a chain JSON file through the MultiSequencePlayer.

    Unlike the original (which returns ``bool``), this version returns a
    human-readable string describing what the chain did, so the agent can
    decide its next action.  The graph fragment is also persisted via
    ``GraphRAG`` for later temporal queries.
    """

    def run_chain(self, chain_path: str, agent_query: str = "",
                   llamacpp_server_url: str = "",
                   ask_user_callback=None,
                   ask_user_v2_callback=None,
                   on_output_ready=None,
                   stop_flag=None) -> str:
        """Execute a chain file and return a human-readable result.

        Args:
            chain_path: Absolute path to a chain JSON file.
            agent_query: The original user query that triggered this chain execution
                         (used by Input nodes to pass through in agent mode).
            llamacpp_server_url: URL of the already-running llama.cpp server
                         (e.g. http://localhost:9083) so Input nodes can reuse
                         the same loaded model instead of starting a new one.
            ask_user_callback: Optional callable that shows a question in the chat
                         and returns the user's typed response. Used by Input nodes
                         with a ``user_prompt`` during agent-mode chain execution.
            on_output_ready: Optional callable that displays output node results
                         in the chat overlay. Fire-and-forget — does not block.
                         Receives (label, content) strings.
            stop_flag: Optional callable that returns True when the caller wants
                         execution cancelled (e.g. the agent overlay's stop event).
                         Combined with the global ESC monitor.

        Returns:
            A human-readable string (e.g."Executed 'extract_products': found
            47 products on Amazon [ctx_1]") or an error message.
        """
        if not chain_path or not os.path.exists(chain_path):
            msg = f"ChainExecutor: file not found: {chain_path}"
            logger.error(msg)
            return msg

        goal_id = str(uuid.uuid4())

        try:
            with open(chain_path, "r", encoding="utf-8") as f:
                chain_config = json.load(f)
        except Exception as e:
            msg = f"ChainExecutor: failed to load chain config: {e}"
            logger.error(msg)
            return msg

        player = None
        chain_ok = True  # assume success unless proven otherwise
        try:
            from player.multi_sequence_player import play_chain_with_tailcalls
            from player.keyboard_monitor import (
                start_global_monitoring, stop_global_monitoring, create_stop_flag,
            )

            start_global_monitoring()
            _monitor_stop = create_stop_flag()

            def _combined_stop():
                # Honor the caller's stop flag (e.g. the agent overlay's
                # _stop_event, set by the web __STOP__ / UI stop button) AND
                # the global ESC monitor.
                try:
                    if stop_flag and stop_flag():
                        return True
                except Exception:
                    pass
                return _monitor_stop()

            try:
                # Follow self-callback (tail-call) handoffs in a flat loop:
                # a chain that imports itself releases this execution and a
                # fresh run of the same chain starts in the single slot.
                chain_ok, player = play_chain_with_tailcalls(
                    chain_path,
                    agent_query=agent_query,
                    llamacpp_server_url=llamacpp_server_url,
                    ask_user_callback=ask_user_callback,
                    ask_user_v2_callback=ask_user_v2_callback,
                    on_output_ready=on_output_ready,
                    stop_flag=_combined_stop,
                    run_source="agent",
                )
                if chain_ok is False:
                    logger.error("ChainExecutor: chain FAILED (node returned error): %s", chain_path)
                else:
                    logger.info("ChainExecutor: chain completed: %s", chain_path)
            finally:
                stop_global_monitoring()
        except Exception as e:
            logger.error("ChainExecutor: execution error (non-fatal): %s", e)
            chain_ok = False
            # Do NOT return here — the context database may still have data
            # from partial execution, and we still want GraphRAG to ingest it.

        # -- Check if Output nodes already delivered response mid-execution --
        chain_id = os.path.basename(chain_path).replace(".json", "")
        executor = getattr(player, "workflow_executor", None) if player else None
        _mid_output_shown = False
        if executor is not None and hasattr(executor, 'llm_executor'):
            _mid_output_shown = bool(
                executor.llm_executor.get_variable('_agent_mid_execution_outputs_shown') or False
            )

        if _mid_output_shown:
            # Response already delivered via output node callbacks (Path 1),
            # no need for redundant post-execution bubble.
            return ""
        # A run the user stopped before it delivered its answer reports the stop
        # — not the chain's fallback chatter ("I don't know how to do that yet."
        # / "Completed using <id>."), which reads as a failure or a lie.
        if stop_flag and stop_flag():
            logger.info("ChainExecutor: chain stopped by user request: %s", chain_path)
            return _tag("Stopped.", goal_id)
        if not chain_ok:
            return _tag("I don't know how to do that yet.", goal_id)
        # Build response from agent-visible context node content
        response = _build_context_response(executor, chain_config, chain_path)
        if response:
            return _tag(response, goal_id)
        chain_graph = getattr(executor, 'workflow_graph', {}) or {}
        return _tag(_build_fallback_response(chain_config, chain_id, chain_graph), goal_id)


# ── Helper: read node output (port_store first, legacy fallback) ─────


def _get_node_output(executor, node_id: str) -> str | None:
    """Read a node's output, trying port_store first then legacy variable.

    Returns the string value or ``None`` if no output is available.
    """
    if executor is None:
        return None
    # Try port_store first (new architecture)
    port_store = getattr(executor, 'port_store', None)
    if port_store is not None:
        try:
            chain_id = getattr(executor, 'chain_id', '') or ''
            val = port_store.get_output(chain_id, node_id, 'output')
            if val is not None:
                return str(val) if not isinstance(val, str) else val
        except Exception:
            pass
    # Fall back to legacy variable
    try:
        llm_ex = getattr(executor, 'llm_executor', None)
        if llm_ex is not None:
            val = llm_ex.get_variable(f"node_{node_id}_output")
            if val is not None:
                return str(val) if not isinstance(val, str) else val
    except Exception:
        pass
    return None


def _get_node_output_meta(executor, node_id: str):
    """Read a node's ``output_meta`` dict (port_store first, then variable)."""
    if executor is None:
        return None
    port_store = getattr(executor, 'port_store', None)
    if port_store is not None:
        try:
            chain_id = getattr(executor, 'chain_id', '') or ''
            meta = port_store.get_output(chain_id, node_id, 'output_meta')
            if isinstance(meta, dict):
                return meta
        except Exception:
            pass
    try:
        llm_ex = getattr(executor, 'llm_executor', None)
        if llm_ex is not None:
            meta = llm_ex.get_variable(f"node_{node_id}_output_meta")
            if isinstance(meta, dict):
                return meta
    except Exception:
        pass
    return None


# ── Helper: preprocess raw context output into natural language ─────


def _preprocess_context_output(text: str) -> str:
    """Transform raw context node content into natural readable text.

    Strips metadata, flattens JSON structures, and returns clean text.
    Returns empty string if nothing meaningful remains.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return ""

    text = text.strip()

    # Try to parse as JSON — context node output is often JSON-encoded
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            parts = []
            for _key, value in parsed.items():
                if isinstance(value, list):
                    for entry in value:
                        if isinstance(entry, dict) and "value" in entry:
                            parts.append(str(entry["value"]))
                        elif isinstance(entry, str):
                            parts.append(entry)
                elif isinstance(value, str):
                    parts.append(value)
            if parts:
                result = "\n".join(parts).strip()
                if len(result) > 2000:
                    result = result[:2000] + "..."
                return result if result else ""
        elif isinstance(parsed, list):
            parts = []
            for entry in parsed:
                if isinstance(entry, dict) and "value" in entry:
                    parts.append(str(entry["value"]))
                elif isinstance(entry, str):
                    parts.append(entry)
            if parts:
                result = "\n".join(parts).strip()
                if len(result) > 2000:
                    result = result[:2000] + "..."
                return result if result else ""
    except (json.JSONDecodeError, TypeError):
        pass

    # Plain string — strip goal_id comments, trim, truncate
    result = re.sub(r'<!--\s*goal_id:[^\s>]+\s*-->', '', text).strip()
    if len(result) > 2000:
        result = result[:2000] + "..."
    return result if result else ""


# ── Helper: recursively discover agent-visible context from sub-chains ──


def _collect_subchain_context(executor, chain_config: dict, graph: dict,
                               chain_dir: str = "", depth: int = 0,
                               max_depth: int = 3) -> list:
    """Collect context node content from sub-chains.

    Walks ChainImport nodes in the executed graph, loads each sub-chain
    JSON config, reads ALL context node outputs from the shared variable
    store, preprocesses them, and recurses into deeper nested imports.
    """
    if depth >= max_depth:
        return []

    results = []
    for node_id, node in graph.items():
        if node.get('type') != 'chain_import':
            continue

        # Extract chain_file_path from node data
        node_data = node.get('data', {}) or {}
        sub_chain_path = (node_data.get('chain_file_path', '') or
                          node_data.get('chain_file', '') or '')
        if not sub_chain_path:
            continue

        # Resolve relative path against parent chain directory
        if not os.path.isabs(sub_chain_path) and chain_dir:
            sub_chain_path = os.path.normpath(
                os.path.join(chain_dir, sub_chain_path)
            )

        if not os.path.exists(sub_chain_path):
            logger.warning(
                "_collect_subchain_context: sub-chain not found: %s",
                sub_chain_path,
            )
            continue

        try:
            with open(sub_chain_path, "r", encoding="utf-8") as f:
                sub_config = json.load(f)
        except Exception as e:
            logger.warning(
                "_collect_subchain_context: failed to load %s: %s",
                sub_chain_path, e,
            )
            continue

        sub_chain_dir = os.path.dirname(os.path.abspath(sub_chain_path))

        # Read context nodes from sub-chain (all context nodes included)
        for ctx_node in (sub_config.get("context_nodes") or []):
            nid = ctx_node.get("node_id", "")
            if not nid:
                continue

            ctx_output = _get_node_output(executor, nid)
            if not ctx_output or not isinstance(ctx_output, str) or not ctx_output.strip():
                continue

            processed = _preprocess_context_output(ctx_output)
            if processed:
                results.append(processed)

        # Recurse into sub-chain's own ChainImport nodes
        sub_chain_imports = sub_config.get("chain_import_nodes", [])
        if sub_chain_imports:
            sub_graph = {}
            for cin in sub_chain_imports:
                cid = str(cin.get("id") or cin.get("node_id", ""))
                if cid:
                    sub_graph[cid] = {
                        "type": "chain_import",
                        "data": cin,
                    }
            results.extend(_collect_subchain_context(
                executor, sub_config, sub_graph,
                sub_chain_dir, depth + 1, max_depth,
            ))

    return results


# ── Helper: conversational fallback when no context content is available ──


def _build_fallback_response(chain_config: dict, chain_id: str,
                              graph: dict) -> str:
    """Build a natural-language fallback response.

    Used when no agent-visible context node content was found.  Prefers
    the chain's ``description`` field, then falls back to a generic
    completion message.
    """
    desc = (chain_config.get("description") or "").strip()
    if desc:
        # Use first sentence only for readability
        idx = desc.find(".")
        if idx > 0:
            return desc[:idx + 1].strip()
        return desc
    return f"Completed using {chain_id}."


def _build_context_response(executor, chain_config: dict, chain_path: str = "") -> str:
    """Build a response from agent-visible context node content.

    First tries Output Node data (which contains LLM-processed summaries,
    not raw context dumps).  Falls back to agent-visible context nodes
    only when no Output Node data is available.

    Task 12: Prefer Output Node → LLM2 conversational responses over
    raw context node data.  The Output Node aggregates data
    through its connections; when LLM2 processes context and feeds it
    to the Output Node, the Output Node stores the LLM-generated summary.
    """
    if executor is None:
        return ""
    try:
        graph = getattr(executor, 'workflow_graph', None)
        if not graph:
            return ""

        # Check if mid-execution output was already shown via on_output_ready callback
        _mid_shown = False
        if hasattr(executor, 'llm_executor'):
            _mid_shown = bool(executor.llm_executor.get_variable('_agent_mid_execution_outputs_shown') or False)

        # ── Phase 0 (Task 12): Try Output Nodes first ──
        # Output Nodes aggregate upstream data.  When LLM2 processes
        # context and feeds it to the Output Node, the stored result
        # is a conversational summary — NOT raw context node data.
        # When mid-execution output was already shown, skip mid-loop
        # output nodes (those with output connections) to avoid duplicates.
        output_node_parts = []
        output_nodes_found = False
        for node_id, node in graph.items():
            if node.get('type') != 'output':
                continue
            nid = node.get('id') or node_id
            if _mid_shown:
                # Skip mid-loop output node — already shown via callbacks
                _conns = node.get('data', {}).get('connections', []) or []
                _has_output_conns = any(c.get('output_port') == 'output' for c in _conns)
                if _has_output_conns:
                    continue
            output_val = _get_node_output(executor, nid)
            if output_val and isinstance(output_val, str) and output_val.strip():
                # Non-text render modes surface as rendered cards via
                # mid-execution callbacks — keep them out of the plain-text
                # reply so base64/paths never leak into the agent's response.
                meta = _get_node_output_meta(executor, nid) or {}
                if meta.get('render_mode', 'text') != 'text':
                    continue
                if meta.get('overlay_visible', True) is False:
                    # Node was deliberately silenced in the overlay (per-node
                    # toggle in output_ops): it must not resurface here as the
                    # final reply either.  The linear activity memory still
                    # holds its record (run_memory node events).
                    continue
                output_nodes_found = True
                processed = _preprocess_context_output(output_val)
                if processed:
                    output_node_parts.append(processed)

        if output_nodes_found and output_node_parts:
            combined = "\n".join(output_node_parts)
            if len(combined) > 500:
                combined = combined[:500] + "..."
            return combined

        # ── Phase 1: context node content (fallback — only when no Output Node data) ──
        context_parts = []
        for node_id, node in graph.items():
            if node.get('type') != 'context':
                continue

            nid = node.get('id') or node_id
            ctx_output = _get_node_output(executor, nid)
            if not ctx_output or not isinstance(ctx_output, str) or not ctx_output.strip():
                continue

            processed = _preprocess_context_output(ctx_output)
            if processed:
                context_parts.append(processed)

        # Phase 1b: context from sub-chains (all context nodes included)
        chain_dir = os.path.dirname(os.path.abspath(chain_path)) if chain_path else ""
        subchain_parts = _collect_subchain_context(
            executor, chain_config, graph, chain_dir
        )
        context_parts.extend(subchain_parts)

        if context_parts:
            combined = "\n".join(context_parts)
            if len(combined) > 2000:
                combined = combined[:2000] + "..."
            return combined

        # ── Phase 2: fall back to LLM-selected tool names ────────
        tool_names = []
        tool_desc_map = {}
        for node in graph.values():
            if node.get('type') == 'llm':
                tool_desc_map.update(
                    node.get('data', {}).get('tool_descriptions', {})
                )
        for nid, node in graph.items():
            if node.get('selected_by_llm') and node.get('type') in ('chain_import', 'code'):
                desc = tool_desc_map.get(nid, '')
                if not desc:
                    chain_path = node.get('chain_file_path', '') or \
                                 node.get('data', {}).get('chain_file_path', '')
                    if chain_path:
                        desc = os.path.basename(chain_path).replace('.json', '')
                if desc:
                    tool_names.append(desc)
        if tool_names:
            return f"I used: {', '.join(tool_names)}"
    except Exception:
        logger.exception("_build_context_response: error")
    return ""


def _tag(text: str, goal_id: str) -> str:
    """Append a hidden goal_id comment for the rating UI to extract."""
    return f"{text}<!-- goal_id:{goal_id} -->"
