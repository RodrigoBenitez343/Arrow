"""Execution logic for MCP Server nodes.

Spawns the MCP server as a subprocess, does JSON-RPC handshake,
calls a single tool, and stores the result.
"""

import json
import logging
import os
import subprocess
try:
    from logging_setup import log_block
except Exception:
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)

logger = logging.getLogger(__name__)

# Module-level cache: mcp_folder → {"proc": ..., "entry": ...}
# Servers live across MCP node executions in a single chain run.
_MCP_SERVER_CACHE: dict = {}

# Nesting counter — only cleanup when the outermost chain finishes.
# Chain-import nodes create sub-executors that share the module-level
# cache; we must not kill servers until the top-level chain is done.
_MCP_CLEANUP_DEPTH: int = 0


def _mcp_cleanup_enter():
    """Called when a chain executor starts. Tracks nesting depth."""
    global _MCP_CLEANUP_DEPTH
    _MCP_CLEANUP_DEPTH += 1


def _mcp_cleanup_exit():
    """Called when a chain executor finishes.

    Only actually cleans up when the outermost (top-level) chain
    finishes, so that ``keep_alive`` servers survive across
    chain-import recursions (loops).
    """
    global _MCP_CLEANUP_DEPTH
    _MCP_CLEANUP_DEPTH = max(0, _MCP_CLEANUP_DEPTH - 1)
    if _MCP_CLEANUP_DEPTH == 0:
        _cleanup_all_mcp_servers()


def _cleanup_all_mcp_servers():
    """Kill non-keep-alive cached MCP server processes.

    Servers with ``keep_alive=True`` are left running so the browser
    (or other long-lived resource) stays open across chain runs.
    They will only be killed on the next call where keep_alive
    is False or the process exits.
    """
    for folder, info in list(_MCP_SERVER_CACHE.items()):
        if info.get("keep_alive"):
            logger.info("[MCP] Keeping server alive for %s (keep_alive=True)", folder)
            continue
        proc = info.get("proc")
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        logger.info("[MCP] Shutdown cached server for %s", folder)
        del _MCP_SERVER_CACHE[folder]


class MCPMixin:
    """Execution logic for MCP Server nodes."""

    def _execute_mcp_node(self, node, stop_flag, tool_overrides=None):
        """Execute an MCP Server node.

        1. Locate the MCP server entry point in the configured folder
        2. Spawn as subprocess with stdio pipes (or reuse cached server)
        3. JSON-RPC: initialize → tools/call
        4. Store result as ``node_{id}_output``

        If ``tool_overrides`` is provided (from LLM tool selection),
        its ``tool_name`` and ``tool_args`` keys override the node's
        static properties.  This enables LLM → MCP dynamic loops
        without a Code node in between.

        If the node has ``keep_alive=True``, the server stays alive in
        ``_MCP_SERVER_CACHE`` for reuse by subsequent MCP nodes on the
        same folder. Use ``_cleanup_all_mcp_servers()`` at chain end.

        Returns:
            str: Next node ID from 'output' connections, or None on failure.
        """
        node_data = node.get('data', {})
        node_id = (node.get('id') or node.get('node_id')
                   or node_data.get('node_id') or node_data.get('id'))

        mcp_folder = node_data.get('mcp_folder', '') or ''
        keep_alive = node_data.get('keep_alive', False)

        # ── Resolve tool_name and tool_args ──
        # LLM overrides are merged ON TOP of the node's static config: the
        # LLM's tool name wins, and any argument keys the LLM omitted fall back
        # to the values configured in the node dialog. This keeps MCP tools with
        # required params callable even when the LLM emits a bare
        # USE_TOOL:<id> with no JSON arguments.
        if tool_overrides and isinstance(tool_overrides, dict):
            tool_name = tool_overrides.get('tool_name', '') or ''
            tool_args_raw = tool_overrides.get('tool_args', '{}') or '{}'
            try:
                static_args = json.loads(node_data.get('tool_args', '{}') or '{}')
            except json.JSONDecodeError:
                static_args = {}
            try:
                llm_args = json.loads(tool_args_raw) if tool_args_raw.strip() else {}
            except json.JSONDecodeError:
                llm_args = {}
            if isinstance(static_args, dict) and isinstance(llm_args, dict):
                merged = dict(static_args)
                merged.update(llm_args)
                tool_args_raw = json.dumps(merged)
            elif isinstance(llm_args, dict):
                tool_args_raw = json.dumps(llm_args)
            if not tool_name:
                tool_name = node_data.get('tool_name', '') or ''
            logger.info("[MCP] Using LLM-provided overrides (merged over static args) for node %s", node_id)
        else:
            tool_name = node_data.get('tool_name', '') or ''
            tool_args_raw = node_data.get('tool_args', '{}') or '{}'

        logger.info(
            "[MCP] Node %s: folder=%s tool=%s",
            node_id, mcp_folder, tool_name,
        )

        if not mcp_folder or not os.path.isdir(mcp_folder):
            msg = f"MCP folder not found: {mcp_folder}"
            logger.error("[MCP] %s", msg)
            self.llm_executor.set_variable(f"node_{node_id}_output",
                                           json.dumps({"error": msg}))
            return self._get_mcp_next_node(node, 'output')

        if not tool_name:
            msg = "No tool name configured"
            logger.error("[MCP] %s", msg)
            self.llm_executor.set_variable(f"node_{node_id}_output",
                                           json.dumps({"error": msg}))
            return self._get_mcp_next_node(node, 'output')

        # Parse tool args
        try:
            tool_args = json.loads(tool_args_raw)
        except json.JSONDecodeError:
            tool_args = {}

        # ── Get or spawn the MCP server ──
        entry = self._find_mcp_entry(mcp_folder)
        if not entry:
            msg = f"No MCP entry found in {mcp_folder}"
            logger.error("[MCP] %s", msg)
            self.llm_executor.set_variable(f"node_{node_id}_output",
                                           json.dumps({"error": msg}))
            return self._get_mcp_next_node(node, 'output')

        proc = None
        spawned_new = False
        try:
            # Reuse cached server if available
            if mcp_folder in _MCP_SERVER_CACHE:
                cached = _MCP_SERVER_CACHE[mcp_folder]
                proc = cached.get("proc")
                # Check if still alive
                if proc is not None and proc.poll() is not None:
                    logger.info("[MCP] Cached server for %s has died, respawning", mcp_folder)
                    proc = None
                    del _MCP_SERVER_CACHE[mcp_folder]
                elif proc is not None:
                    logger.info("[MCP] Reusing cached server for %s", mcp_folder)
                else:
                    del _MCP_SERVER_CACHE[mcp_folder]

            if proc is None:
                logger.info("[MCP] Spawning server: %s", entry["cmd"])
                proc = subprocess.Popen(
                    entry["cmd"],
                    cwd=mcp_folder,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                spawned_new = True

                # JSON-RPC: initialize
                init_request = json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "LoOper", "version": "1.0"},
                    },
                }) + "\n"
                try:
                    proc.stdin.write(init_request)
                    proc.stdin.flush()
                    init_resp = proc.stdout.readline()
                    logger.debug("[MCP] Init response: %.200s", init_resp)
                except Exception as e:
                    logger.error("[MCP] Init failed: %s", e)

                # Send initialized notification
                try:
                    proc.stdin.write(json.dumps({
                        "jsonrpc": "2.0",
                        "method": "notifications/initialized",
                    }) + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass

            # JSON-RPC: tools/call
            call_request = json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": tool_args,
                },
            }) + "\n"

            logger.info("[MCP] Calling tool: %s args=%s", tool_name, tool_args)
            proc.stdin.write(call_request)
            proc.stdin.flush()

            # Read response — may be multiple lines for SSE, grab the JSON-RPC one
            result_text = ""
            for _ in range(50):
                if stop_flag and stop_flag():
                    break
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                result_text = line
                try:
                    parsed = json.loads(line)
                    if "result" in parsed or "error" in parsed:
                        break
                except json.JSONDecodeError:
                    continue

            logger.info("[MCP] Result: %.300s", result_text)
            log_block(logger, logging.INFO, "MCP Result", result_text)

            # Parse the result
            try:
                resp = json.loads(result_text)
                if "result" in resp:
                    result = resp["result"]
                    if isinstance(result, dict) and "content" in result:
                        content_list = result["content"]
                        texts = []
                        for c in content_list:
                            if isinstance(c, dict) and c.get("type") == "text":
                                texts.append(c.get("text", ""))
                        out = "\n".join(texts) if texts else json.dumps(result)
                    else:
                        out = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
                elif "error" in resp:
                    out = json.dumps({"error": resp["error"]})
                else:
                    out = result_text
            except json.JSONDecodeError:
                out = result_text

            self.llm_executor.set_variable(f"node_{node_id}_output", out)
            logger.info("[MCP] Node %s done, stored %d chars", node_id, len(out))

            # ── Lifecycle: cache or kill ──
            if keep_alive:
                _MCP_SERVER_CACHE[mcp_folder] = {"proc": proc, "entry": entry, "keep_alive": keep_alive}
                logger.info("[MCP] Keeping server alive for %s (keep_alive=True)", mcp_folder)
                proc = None  # don't kill in finally
            # else: proc gets killed in finally

        except Exception as e:
            logger.error("[MCP] Subprocess error: %s", e, exc_info=True)
            self.llm_executor.set_variable(
                f"node_{node_id}_output",
                json.dumps({"error": str(e)}),
            )
        finally:
            if proc is not None and not keep_alive:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                # Also remove stale cache entry for this folder
                if mcp_folder in _MCP_SERVER_CACHE:
                    del _MCP_SERVER_CACHE[mcp_folder]
                    logger.debug("[MCP] Removed cache entry for %s after non-keep-alive shutdown", mcp_folder)

        return self._get_mcp_next_node(node, 'output')

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_mcp_entry(self, folder):
        """Find the MCP server entry point in a cloned repo folder.

        Checks (in order):
        1. package.json with a "main" or "bin" field → use ``node <main>``
        2. ``cli.js`` at root → use ``node cli.js``
        3. ``index.js`` at root → use ``node index.js``
        4. fallback: ``npx`` (if available on PATH)

        Returns dict with ``cmd`` (list) or None.
        """
        pkg_path = os.path.join(folder, "package.json")
        if os.path.isfile(pkg_path):
            try:
                with open(pkg_path, "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                main = pkg.get("main", "")
                if main:
                    return {"cmd": ["node", main]}
                bin_field = pkg.get("bin", {})
                if isinstance(bin_field, str):
                    return {"cmd": ["node", bin_field]}
                elif isinstance(bin_field, dict) and bin_field:
                    first = next(iter(bin_field.values()))
                    return {"cmd": ["node", first]}
            except Exception:
                pass

        # Check common entry points
        for name in ["cli.js", "index.js", "main.js", "server.js"]:
            cand = os.path.join(folder, name)
            if os.path.isfile(cand):
                return {"cmd": ["node", name]}

        # Try npx on the folder itself
        return {"cmd": ["npx", "."]}

    def _get_mcp_next_node(self, node, port):
        """Get the next node ID from the given port connections."""
        connections = node.get('connections', {})
        if port in connections and connections[port]:
            return connections[port][0].get('node_id')
        if port == 'output':
            return "__done__"
        return None
