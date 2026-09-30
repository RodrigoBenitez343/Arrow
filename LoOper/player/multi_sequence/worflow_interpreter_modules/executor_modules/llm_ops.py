import copy
import json
import os
import re
import time
import logging
logger = logging.getLogger(__name__)

# Transport seam for typed LLM failures (direct engine mode).  A failure to
# import is non-fatal in the full app — failures then surface as RuntimeError.
try:
    from AI import inprocess_transport as _direct_transport
except Exception:
    _direct_transport = None

class LLMMixin:
    def _execute_llm_node(self, node, stop_flag, action_handlers=None):
        """
        Executes an LLM node with atomic tool execution.

        ALL tools connected via the `tools` input port are presented to the LLM
        as on-demand subroutines. The LLM node is atomic from the graph's
        perspective — tool execution happens inline within this method.

        Flow:
          1. LLM responds with USE_TOOL:<id> or a direct answer
          2. If tool selected: execute tool inline (chain_import/code/mcp),
             feed result back into LLM context, loop back to step 1
          3. If done: return __done__, execution follows output port
        """
        # Lazy initialization
        if not self._runtime_initialized:
            self._initialize_chain_runtime()
            self._runtime_initialized = True

        # Check for LLM -> TTS connection (redirect logic)
        llm_data = node.get('data', {})
        llm_config = llm_data.get('llm_configuration', {}) or llm_data
        write_text = llm_config.get('write_text', True)

        # Check for TTS redirection
        output_variable, node_to_execute = self._handle_tts_redirection(node, llm_config, write_text)

        # --- Tool Discovery: only Chain Import nodes are valid LLM tools ---
        # MCP/code logic must be wrapped inside a chain and imported as the
        # tool.  Anything else connected to the tools port is ignored — this
        # guards chains saved before the restriction or hand-edited JSON.
        _llm_nid = (node_to_execute.get('id') or node_to_execute.get('node_id')
                    or node_to_execute.get('data', {}).get('node_id') or 'unknown')
        all_tool_ids = []
        inputs = node_to_execute.get('inputs', [])
        for inp in inputs:
            if str(inp.get('input_port')) == 'tools':
                from_id = inp.get('from_node')
                if from_id and from_id in self.workflow_graph:
                    _tool_node = self.workflow_graph[from_id]
                    if _tool_node.get('type') == 'chain_import':
                        all_tool_ids.append(from_id)
                    else:
                        logger.warning(
                            "[LLM] Node %s: ignoring tools-port connection from "
                            "%s (type=%s) — only Chain Import nodes can be used "
                            "as LLM tools; wrap MCP/code logic in a chain and "
                            "import it", _llm_nid, from_id, _tool_node.get('type'),
                        )

        # Determine which tool IDs are code nodes (they accept JSON arguments)
        code_tool_ids = []
        mcp_tool_ids = []
        for tid in all_tool_ids:
            tool_node = self.workflow_graph.get(tid, {})
            if tool_node.get('type') == 'code':
                code_tool_ids.append(tid)
            elif tool_node.get('type') == 'mcp':
                code_tool_ids.append(tid)
                mcp_tool_ids.append(tid)

        # Make a copy EARLY to avoid mutating the original graph node.
        # Injection functions (chain, MCP descriptions) mutate node.data
        # and would create a partial llm_configuration sub-object that
        # shadows top-level config fields on loop re-execution.
        node_copy = copy.deepcopy(node_to_execute)

        # Inject chain descriptions for chain-type tools (into the copy) — ONCE
        self._inject_chain_descriptions(node_copy, all_tool_ids)

        # Inject MCP tool descriptions for MCP-type tools (into the copy) — ONCE
        self._inject_mcp_descriptions(node_copy, all_tool_ids)

        # Inject tool-type IDs so the executor can differentiate usage rules:
        # code tools take optional JSON args, MCP tools take REQUIRED JSON args
        # (chain-import tools take no args at all).
        if code_tool_ids:
            ndata = node_copy.get('data', {})
            ndata['_code_tool_ids'] = code_tool_ids
        if mcp_tool_ids:
            ndata = node_copy.get('data', {})
            ndata['_mcp_tool_ids'] = mcp_tool_ids

        # ── Log comprehensive LLM configuration for debugging ──
        _exec_node_id = (
            node_copy.get('id') or node_copy.get('node_id')
            or node_copy.get('data', {}).get('node_id')
            or node_copy.get('data', {}).get('id') or 'unknown'
        )
        _exec_conf = node_copy.get('data', {}).get('llm_configuration', {}) or node_copy.get('data', {})
        logger.info(
            "[LLM] === LLM NODE %s STARTING (type=%s) ===",
            _exec_node_id, node_copy.get('type'),
        )
        logger.info(
            "[LLM] LLM config for node %s: prompt_len=%d, system_len=%d, "
            "model=%s, temperature=%s, max_tokens=%s, use_llamacpp=%s",
            _exec_node_id,
            len(str(_exec_conf.get('prompt', ''))),
            len(str(_exec_conf.get('system_message', ''))),
            _exec_conf.get('model', '?'),
            _exec_conf.get('temperature', '?'),
            _exec_conf.get('max_tokens', '?'),
            _exec_conf.get('use_llamacpp', '?'),
        )
        # Log tool descriptions if present
        _tool_descs = _exec_conf.get('tool_descriptions', {})
        if _tool_descs:
            logger.info(
                "[LLM] Tool descriptions for node %s: %d tool(s): %s",
                _exec_node_id, len(_tool_descs),
                {k: str(v)[:100] for k, v in _tool_descs.items()},
            )
        else:
            logger.info("[LLM] No tool descriptions for node %s", _exec_node_id)
        # Log all upstream variables available to this LLM
        _upstream_vars = {}
        for inp in node_copy.get('inputs', []) or []:
            _fid = inp.get('from_node')
            if _fid:
                _out = self.llm_executor.get_variable(f"node_{_fid}_output")
                _ctx_val = self.llm_executor.get_variable(f"node_{_fid}_context")
                _upstream_vars[_fid] = {
                    'output_len': len(str(_out or '')),
                    'context_len': len(str(_ctx_val or '')),
                    'input_port': inp.get('input_port'),
                    'output_type': inp.get('output_type'),
                }
                if _out:
                    _upstream_vars[_fid]['output_preview'] = str(_out)[:300]
        logger.info(
            "[LLM] Upstream variables for node %s: %s",
            _exec_node_id, _upstream_vars,
        )
        # Save base system message (without tool result) for reset between iterations
        _llm_conf_copy = node_copy.get('data', {}).get('llm_configuration', {})
        _base_system_message = copy.deepcopy(_llm_conf_copy.get('system_message', ''))

        # Reset tool result context for this LLM cycle
        self.llm_executor.set_variable("_last_tool_result_context", "")

        # ── Atomic LLM execution loop: inline tool subroutines ──
        _last_executed_tool_id = None  # Guards against re-selecting the same tool
        _loop_iteration = 0
        _loop_start_time = time.time()
        while True:
            _loop_iteration += 1
            _current_time = time.time()
            _elapsed = _current_time - _loop_start_time
            logger.info(
                "[LLM] === Atomic loop iteration %d for node %s (elapsed: %.1fs) ===",
                _loop_iteration, _exec_node_id, _elapsed,
            )
            if stop_flag and stop_flag():
                logger.info("[LLM] Node %s execution stopped by user request at iteration %d", _exec_node_id, _loop_iteration)
                return None

            # Inject tool result from previous iteration into system message
            _tool_result = self.llm_executor.get_variable("_last_tool_result_context") or ""
            if _tool_result:
                logger.info(
                    "[LLM] Node %s: injecting tool result context into iteration %d (%d chars): %.200s",
                    _exec_node_id, _loop_iteration, len(str(_tool_result)), str(_tool_result),
                )
                _llm_conf_copy['system_message'] = (
                    _base_system_message
                    + f"\n\n[Tool Result Context Begin]\n{str(_tool_result)[:2000]}\n[Tool Result Context End]"
                )
            else:
                logger.debug(
                    "[LLM] Node %s iteration %d: no tool result context, using base system message",
                    _exec_node_id, _loop_iteration,
                )
                _llm_conf_copy['system_message'] = _base_system_message

            # Log the system message (with tool result if present) at debug level
            logger.debug(
                "[LLM] Node %s iteration %d: system_message (%d chars)",
                _exec_node_id, _loop_iteration,
                len(str(_llm_conf_copy.get('system_message', ''))),
            )

            # Execute via executor.py which handles tool selection
            logger.info(
                "[LLM] Node %s iteration %d: calling execute_llm_node",
                _exec_node_id, _loop_iteration,
            )
            try:
                next_node_id = self.llm_executor.execute_llm_node(node_copy, stop_flag, action_handlers)
            except Exception as _llm_exc:
                # Transport/engine failure — fail the node LOUDLY.  Never
                # canonicalize a failed call as 'LLM done' (GUARD 1/2) or
                # fabricate an empty success; the chain must terminate with
                # the engine error visible in the overlay.
                _fail_msg = str(_llm_exc) or type(_llm_exc).__name__
                logger.error(
                    "[LLM] Node %s FAILED (transport/engine error): %s",
                    _exec_node_id, _fail_msg,
                )
                self._surface_node_failure(_exec_node_id, node_copy, _fail_msg)
                return None

            # Check if the LLM selected a tool
            if next_node_id and next_node_id != "__done__":
                tool_node = self.workflow_graph.get(next_node_id)
                if not tool_node:
                    logger.warning(
                        "[LLM] Node %s: LLM selected tool node %s not found in workflow graph — continuing",
                        _exec_node_id, next_node_id,
                    )
                    continue

                tool_type = tool_node.get('type')
                _tool_data = tool_node.get('data', {}) or {}
                _tool_label = _tool_data.get('label') or _tool_data.get('name') or next_node_id
                logger.info(
                    "[LLM] Node %s iteration %d: TOOL SELECTED: %s (type=%s, label=%s)",
                    _exec_node_id, _loop_iteration,
                    next_node_id, tool_type, _tool_label,
                )

                # ── GUARD 1: Non-tool node type ──
                # execute_llm_node (executor.py) returns the next graph node ID
                # from output connections when no tool marker is found in the
                # LLM's response.  Treating that as a tool selection would cause
                # an infinite loop ("Unknown tool type: <type> — skipping").
                if tool_type not in ('chain_import', 'code', 'mcp'):
                    logger.info(
                        "[LLM] Node %s: LLM returned %s (type=%s) — not a tool type, "
                        "considering LLM done",
                        _exec_node_id, next_node_id, tool_type,
                    )
                    break

                # ── GUARD 2: Same tool re-selected ──
                # A small LLM may not understand the tool result context and
                # hallucinate the same USE_TOOL:<id> again.  Its response likely
                # contains token overlap with the tool description, which
                # executor.py's fallback matching (lines 2306-2327) picks up as
                # a tool selection.  Prevent re-execution of the same tool.
                if next_node_id == _last_executed_tool_id:
                    logger.info(
                        "[LLM] Node %s: LLM re-selected SAME tool %s (type=%s) — "
                        "treating as done (guard 2)",
                        _exec_node_id, next_node_id, tool_type,
                    )
                    break

                _last_executed_tool_id = next_node_id
                logger.info(
                    "[LLM] Node %s iteration %d: executing tool %s (type=%s)",
                    _exec_node_id, _loop_iteration, next_node_id, tool_type,
                )

                # Execute selected tool inline, synchronously
                try:
                    if tool_type == 'chain_import':
                        self._execute_chain_import_node(tool_node, stop_flag)
                    elif tool_type == 'code':
                        cn_id = (tool_node.get('id') or tool_node.get('node_id')
                                 or tool_node.get('data', {}).get('node_id'))
                        _code_arg_overrides = None
                        if cn_id:
                            _stored_args = self.llm_executor.get_variable(f"_tool_args_{cn_id}")
                            if _stored_args and isinstance(_stored_args, dict):
                                _code_arg_overrides = _stored_args
                                logger.info(f"Injecting LLM args into code node {cn_id}: {_stored_args}")
                                self.llm_executor.set_variable(f"_tool_args_{cn_id}", {})
                        self._execute_code_node(tool_node, stop_flag, arg_overrides=_code_arg_overrides)
                    elif tool_type == 'mcp':
                        mn_id = (tool_node.get('id') or tool_node.get('node_id')
                                 or tool_node.get('data', {}).get('node_id'))
                        _mcp_overrides = None
                        if mn_id:
                            _stored = self.llm_executor.get_variable(f"_tool_args_{mn_id}")
                            if _stored and isinstance(_stored, dict):
                                if 'tool_name' in _stored and 'tool_args' not in _stored:
                                    tn = _stored.pop('tool_name')
                                    _mcp_overrides = {'tool_name': tn, 'tool_args': json.dumps(_stored)}
                                else:
                                    _mcp_overrides = _stored
                                logger.info(f"Injecting LLM args into MCP node {mn_id}: {_mcp_overrides}")
                                self.llm_executor.set_variable(f"_tool_args_{mn_id}", {})
                        self._execute_mcp_node(tool_node, stop_flag, tool_overrides=_mcp_overrides)
                    else:
                        logger.warning(f"Unknown tool type: {tool_type} — skipping")
                        continue
                except Exception as e:
                    logger.error(f"Error executing tool {next_node_id} ({tool_type}): {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    continue

                # Collect tool result for next LLM call
                self._collect_tool_result(tool_node)
                _tool_result_context = self.llm_executor.get_variable("_last_tool_result_context") or ""
                logger.info(
                    "[LLM] Node %s iteration %d: tool result context collected (%d chars): %.300s",
                    _exec_node_id, _loop_iteration,
                    len(str(_tool_result_context)), str(_tool_result_context),
                )
                continue

            # No tool selected — LLM is done
            logger.info(
                "[LLM] Node %s: no tool selected after iteration %d — LLM execution complete "
                "(elapsed: %.1fs)",
                _exec_node_id, _loop_iteration, time.time() - _loop_start_time,
            )
            break

        logger.info(
            "[LLM] === LLM NODE %s DONE after %d iterations (%.1fs) ===",
            _exec_node_id, _loop_iteration, time.time() - _loop_start_time,
        )
        return "__done__"

    def _surface_node_failure(self, node_id, node, message):
        """Deliver a visible chat bubble in the overlay for a failed node.

        Uses the existing ``_on_output_ready_callback`` (output_display
        callback on the router) so the exported agent shows the node label
        and engine error instead of a silent empty response.
        """
        try:
            _out_cb = self.llm_executor.get_variable("_on_output_ready_callback")
            if _out_cb:
                _nd = (node or {}).get('data', {}) or {}
                _label = _nd.get('name') or _nd.get('label') or str(node_id)
                _out_cb(
                    str(_label),
                    f"[Node failed] {message}",
                )
        except Exception as e:
            logger.warning(
                "[LLM] Failed to surface node failure bubble: %s", e
            )

    def _collect_tool_result(self, tool_node):
        """Collect tool execution result for injection into next LLM call.

        ALWAYS sets ``_last_tool_result_context`` so the LLM's next call
        sees a non-empty context and knows the tool was executed, preventing
        an infinite loop where the LLM re-selects the same tool because it
        didn't see any result from the previous call.
        """
        tool_type = tool_node.get('type', '')
        _result = ""
        if tool_type == 'chain_import':
            _result = self.llm_executor.get_variable("chain_tool_last_result_text") or ""
        if not _result:
            _nd = tool_node.get('data', {})
            _nid = (tool_node.get('id') or tool_node.get('node_id')
                    or _nd.get('node_id') or _nd.get('id'))
            if _nid:
                _result = self.llm_executor.get_variable(f"node_{_nid}_output") or ""
        # Always set a non-empty marker so the LLM knows the tool ran
        if not _result:
            _result = "[Tool executed — no textual result captured]"
        _result = str(_result)[:2000]
        self.llm_executor.set_variable("_last_tool_result_context", _result)

    def _inject_chain_descriptions(self, node, routing_tool_ids):
        """Inject chain file descriptions into tool_descriptions for routing tools."""
        try:
            tool_descriptions_update = {}
            alias_map_update = {}
            for tid in routing_tool_ids:
                if tid in self.workflow_graph:
                    tool_node = self.workflow_graph[tid]
                    if tool_node.get('type') == 'chain_import':
                        chain_data = tool_node.get('data', {})
                        chain_file = (
                            chain_data.get('chain_file_path')
                            or chain_data.get('chain_file')
                            or chain_data.get('chain_import_config', {}).get('chain_file_path')
                            or chain_data.get('chain_import_config', {}).get('chain_file')
                        )
                        if chain_file:
                            desc = ""
                            try:
                                resolved_path = chain_file
                                if not os.path.exists(resolved_path):
                                    # In frozen builds, the absolute source path won't exist —
                                    # try resolving the basename against known data dirs.
                                    if getattr(__import__('sys'), 'frozen', False):
                                        _sys = __import__('sys')
                                        _meipass = getattr(_sys, '_MEIPASS', None)
                                        _exe_dir = os.path.dirname(_sys.executable) if getattr(_sys, 'frozen', False) else None
                                        _base_name = os.path.basename(chain_file)
                                        for _cand in [
                                            os.path.join(_meipass, 'LoOper', 'chains', _base_name) if _meipass else '',
                                            os.path.join(_meipass, 'chains', _base_name) if _meipass else '',
                                            os.path.join(_exe_dir, '_internal', 'LoOper', 'chains', _base_name) if _exe_dir else '',
                                            os.path.join(_exe_dir, '_internal', 'chains', _base_name) if _exe_dir else '',
                                            os.path.join(_exe_dir, 'chains', _base_name) if _exe_dir else '',
                                        ]:
                                            if _cand and os.path.exists(_cand):
                                                resolved_path = _cand
                                                break
                                    # Fallback: join basename with chain_file_dir
                                    if not os.path.exists(resolved_path):
                                        base_dir = getattr(self.sequence_executor, 'chain_file_dir', None)
                                        if base_dir:
                                            candidate = os.path.join(base_dir, os.path.basename(chain_file))
                                            if os.path.exists(candidate):
                                                resolved_path = candidate
                                if os.path.exists(resolved_path):
                                    with open(resolved_path, 'r', encoding='utf-8') as f:
                                        cdata = json.load(f)
                                        desc = cdata.get('description') or ""
                                        # Surface the sub-chain's agent-modifiable
                                        # Input node labels so the top LLM knows
                                        # what the chain consumes from context.
                                        # Informational only — the call stays a
                                        # bare USE_TOOL:<id>; the Input nodes
                                        # infer their values internally from
                                        # _chain_input_context.
                                        try:
                                            _fields = [
                                                (n.get('label') or '').strip()
                                                for n in (cdata.get('input_nodes') or [])
                                                if isinstance(n, dict)
                                                and (n.get('label') or '').strip()
                                                and n.get('agent_modifiable')
                                            ]
                                            if _fields:
                                                _fields_txt = "[fields: " + ", ".join(_fields) + "]"
                                                desc = (desc + "  " + _fields_txt).strip() if desc else _fields_txt
                                        except Exception:
                                            pass
                                else:
                                    # Chain file configured but not resolvable on
                                    # this machine (moved/renamed/other install).
                                    # Say so instead of leaving an empty
                                    # description: the Router LLM otherwise picks
                                    # this dead tool blind and the query dies in
                                    # chain_ops' "no chain file path" error.
                                    desc = (f"[chain file not found: "
                                            f"{os.path.basename(chain_file)}]")
                            except Exception as e:
                                logger.warning("[LLM] Failed to read chain file %s for tool description: %s", chain_file, e)
                            # Always inject tool ID entry (even with empty description)
                            tool_descriptions_update[tid] = desc
                            # Register a human-readable alias from the chain prefix.
                            # Small LLMs struggle to reproduce 13-char hex IDs —
                            # aliases like <brave>, <CLOSE>, <calculator> are far
                            # more reliable.  The alias is recorded in a separate
                            # map (NOT as a second tool_descriptions entry) so the
                            # ToolRetriever index holds exactly one entry per tool;
                            # executor.py resolves USE_TOOL:<alias> via this map.
                            alias = chain_data.get('prefix') or os.path.splitext(os.path.basename(chain_file))[0]
                            if alias:
                                alias_map_update[f"<{alias}>"] = tid

            if tool_descriptions_update:
                ndata = node.get('data', {})
                if 'llm_configuration' not in ndata:
                    ndata['llm_configuration'] = {}
                llm_conf = ndata['llm_configuration']
                existing_td = llm_conf.get('tool_descriptions', {})
                if isinstance(existing_td, str):
                    try:
                        existing_td = json.loads(existing_td)
                    except:
                        existing_td = {}
                if not isinstance(existing_td, dict):
                    existing_td = {}
                # Drop any stale alias keys (<brave>) that may have leaked in from
                # an older runtime — descriptions must stay single-entry per tool.
                existing_td = {k: v for k, v in existing_td.items()
                               if not str(k).startswith('<')}
                existing_td.update(tool_descriptions_update)
                llm_conf['tool_descriptions'] = existing_td
            if alias_map_update:
                ndata = node.get('data', {})
                if 'llm_configuration' not in ndata:
                    ndata['llm_configuration'] = {}
                llm_conf = ndata['llm_configuration']
                existing_am = llm_conf.get('_tool_alias_map', {})
                if isinstance(existing_am, str):
                    try:
                        existing_am = json.loads(existing_am)
                    except Exception:
                        existing_am = {}
                if not isinstance(existing_am, dict):
                    existing_am = {}
                existing_am.update(alias_map_update)
                llm_conf['_tool_alias_map'] = existing_am
        except Exception as e:
            logger.error(f"Error injecting chain descriptions: {e}")

    def _inject_mcp_descriptions(self, node, routing_tool_ids):
        """Inject MCP tool schemas into tool_descriptions so the LLM can
        select a specific MCP tool and provide typed arguments.

        For each MCP node connected as a tool, reads the cached ``mcp_tools``
        JSON and creates ONE tool_descriptions entry per sub-tool with a
        composite ID ``{node_id}__{tool_name}``.  This way the LLM sees each
        MCP tool as an individually selectable option instead of one giant
        blob, which is critical for small-context LLMs.

        The LLM responds with:

            USE_TOOL:{node_id}__{tool_name} {"<param>": <value>, ...}

        The executor decomposes the composite ID, injects ``tool_name`` into
        the args dict, and routes back to the real MCP node.
        """
        try:
            tool_descriptions_update = {}
            for tid in routing_tool_ids:
                if tid in self.workflow_graph:
                    tool_node = self.workflow_graph[tid]
                    if tool_node.get('type') != 'mcp':
                        continue
                    node_data = tool_node.get('data', {})
                    mcp_tools_raw = node_data.get('mcp_tools', '') or ''
                    if not mcp_tools_raw:
                        logger.warning(
                            "[MCP-LLM] MCP node %s has no cached mcp_tools — "
                            "open the node dialog, run Discover Tools and pick "
                            "a tool so sub-tools can be injected for LLM routing",
                            tid,
                        )
                        continue
                    try:
                        mcp_tools = json.loads(mcp_tools_raw) if isinstance(mcp_tools_raw, str) else mcp_tools_raw
                    except Exception:
                        continue
                    if not isinstance(mcp_tools, list) or not mcp_tools:
                        continue

                    # Create ONE entry per sub-tool using a composite ID
                    for t in mcp_tools:
                        tname = t.get('name', '?')
                        tdesc = t.get('description', '')
                        schema = t.get('inputSchema', {})
                        props = schema.get('properties', {})
                        required = schema.get('required', [])
                        param_strs = []
                        for pname, pschema in props.items():
                            ptype = pschema.get('type', 'string')
                            req_mark = ' (required)' if pname in required else ''
                            param_strs.append(f"{pname}:{ptype}{req_mark}")
                        params = ', '.join(param_strs) if param_strs else 'no params'

                        composite_id = f"{tid}__{tname}"
                        desc = f"MCP: {tname} - {tdesc}  [params: {params}]"
                        tool_descriptions_update[composite_id] = desc

                    logger.info("[MCP-LLM] Injected %d individual sub-tools for MCP node %s", len(mcp_tools), tid)

            if tool_descriptions_update:
                ndata = node.get('data', {})
                if 'llm_configuration' not in ndata:
                    ndata['llm_configuration'] = {}
                llm_conf = ndata['llm_configuration']
                existing_td = llm_conf.get('tool_descriptions', {})
                if isinstance(existing_td, str):
                    try:
                        existing_td = json.loads(existing_td)
                    except Exception:
                        existing_td = {}
                if not isinstance(existing_td, dict):
                    existing_td = {}
                existing_td.update(tool_descriptions_update)
                llm_conf['tool_descriptions'] = existing_td
        except Exception as e:
            logger.error(f"Error injecting MCP descriptions: {e}")

    def _handle_tts_redirection(self, node, llm_config, write_text):
        node_to_execute = node
        output_variable = llm_config.get('output_variable', 'llm_output')
        connections = node.get('connections', {}).get('output', [])
        for connection in connections:
            target_node_id = connection.get('node_id')
            if target_node_id and target_node_id in self.workflow_graph:
                target_node = self.workflow_graph[target_node_id]
                if target_node.get('type') == 'tts':
                    target_node['data']['text'] = f"{{{output_variable}}}"
                    if write_text:
                        node_to_execute = copy.deepcopy(node)
                        node_data_copy = node_to_execute.get('data', {})
                        if 'llm_configuration' in node_data_copy:
                            node_data_copy['llm_configuration']['write_text'] = False
                        else:
                            node_data_copy['write_text'] = False
                    break
        return output_variable, node_to_execute


