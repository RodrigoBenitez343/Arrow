from ....json_cache import get_chain
import json
import os
import time
import logging
logger = logging.getLogger(__name__)

class ChainImportMixin:
    def _execute_chain_import_node(self, node, stop_flag):
        """
        Executes a chain import node with loop count support using direct chain execution.
        
        Args:
            node (dict): The chain import node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Get sandbox configuration
        chain_data = node.get('data', {})
        run_in_sandbox = str(chain_data.get('run_in_sandbox', 'false')).lower() == 'true'
        show_sandbox_window = str(chain_data.get('show_sandbox_window', 'true')).lower() == 'true'
        
        # Build app context override for sandboxing if needed
        app_context_override = None
        if run_in_sandbox:
            app_context_override = {
                "sandboxed": True,
                "show_window": show_sandbox_window
            }
            # Reuse the parent's agent URL when present so the imported chain
            # joins the existing RDP session instead of spawning a second one.
            try:
                g = getattr(self, 'global_app_context_override', None) or {}
                if isinstance(g, dict):
                    _parent_url = g.get('sandbox_agent_url') or g.get('rdp_agent_url')
                    if _parent_url:
                        app_context_override['sandbox_agent_url'] = _parent_url
            except Exception:
                pass

        try:
            from ....multi_sequence_player import MultiSequencePlayer
        except ImportError:
            # Fallback for different directory structures
            try:
                from ...multi_sequence_player import MultiSequencePlayer
            except ImportError:
                from player.multi_sequence_player import MultiSequencePlayer
        
        # Check stop flag before starting chain import
        if stop_flag and stop_flag():
            logger.info("Stop flag detected before chain import execution - aborting")
            return None
        
        chain_import_data = node.get('data', {})
        logger.debug(f"Chain import node data: {chain_import_data}")
        
        # Try both property names for backward compatibility
        chain_file_path = chain_import_data.get('chain_file_path', '') or chain_import_data.get('chain_file', '')
        loop_count = chain_import_data.get('loop_count', 1)
        extra_delay = chain_import_data.get('extra_delay', 0)
        import_mode = chain_import_data.get('import_mode', 'full')
        prefix = chain_import_data.get('prefix', '')
        
        logger.debug(f"Initial chain file path: '{chain_file_path}'")
        
        # If we still don't have a chain file path, try to get it from import_data
        if not chain_file_path and 'import_data' in chain_import_data:
            import_data = chain_import_data['import_data']
            logger.debug(f"Found import_data: {import_data}")
            chain_file_path = import_data.get('chain_file_path', '') or import_data.get('chain_file', '')
            loop_count = import_data.get('loop_count', loop_count)
            extra_delay = import_data.get('extra_delay', extra_delay)
            import_mode = import_data.get('import_mode', import_mode)
            prefix = import_data.get('prefix', prefix)
            logger.debug(f"Updated chain file path from import_data: '{chain_file_path}'")
        
        # Robustly resolve the chain file path (support nested imports and relative paths)
        resolved_chain_path = chain_file_path
        try:
            base_dir = getattr(self.sequence_executor, 'chain_file_dir', None)
            candidates = []
            if chain_file_path:
                candidates.append(chain_file_path)
                if not chain_file_path.lower().endswith('.json'):
                    candidates.append(chain_file_path + '.json')
                # In frozen builds, the absolute source path won't exist —
                # try resolving the basename against known data dirs.
                if not os.path.exists(chain_file_path) and getattr(__import__('sys'), 'frozen', False):
                    _sys = __import__('sys')
                    _meipass = getattr(_sys, '_MEIPASS', None)
                    _exe_dir = os.path.dirname(_sys.executable) if getattr(_sys, 'frozen', False) else None
                    _base_name = os.path.basename(chain_file_path)
                    for _cand in [
                        os.path.join(_meipass, 'LoOper', 'chains', _base_name) if _meipass else '',
                        os.path.join(_meipass, 'chains', _base_name) if _meipass else '',
                        os.path.join(_exe_dir, '_internal', 'LoOper', 'chains', _base_name) if _exe_dir else '',
                        os.path.join(_exe_dir, '_internal', 'chains', _base_name) if _exe_dir else '',
                        os.path.join(_exe_dir, 'chains', _base_name) if _exe_dir else '',
                    ]:
                        if _cand and os.path.exists(_cand):
                            candidates.append(_cand)
            if base_dir:
                if chain_file_path:
                    candidates.append(os.path.join(base_dir, os.path.basename(chain_file_path)))
                    if not chain_file_path.lower().endswith('.json'):
                        candidates.append(os.path.join(base_dir, os.path.basename(chain_file_path) + '.json'))
            # Frozen builds: learned chains (and their library imports) live
            # under the durable runtime dir, not in the bundle.
            try:
                if chain_file_path and getattr(__import__('sys'), 'frozen', False):
                    from AI.runtime_paths import get_runtime_dir
                    _rt_chains = os.path.join(str(get_runtime_dir()), 'chains')
                    _rt_base = os.path.basename(str(chain_file_path))
                    for _rt_cand in (
                        os.path.join(_rt_chains, _rt_base),
                        os.path.join(_rt_chains, 'learned', _rt_base),
                    ):
                        candidates.append(_rt_cand)
            except Exception:
                pass
            # Also try current working directory as a fallback
            if chain_file_path:
                candidates.append(os.path.join(os.getcwd(), os.path.basename(chain_file_path)))
                if not chain_file_path.lower().endswith('.json'):
                    candidates.append(os.path.join(os.getcwd(), os.path.basename(chain_file_path) + '.json'))
            for candidate in candidates:
                if os.path.exists(candidate):
                    resolved_chain_path = candidate
                    break
            logger.debug(f"Resolved chain import path: '{resolved_chain_path}' (base_dir={base_dir})")
        except Exception as e:
            logger.warning(f"Error resolving chain import path '{chain_file_path}': {e}")
        
        logger.info(f"Executing chain import node: {resolved_chain_path} (loops: {loop_count}, extra_delay: {extra_delay})")
        
        if not resolved_chain_path:
            logger.error(f"No chain file path specified in chain import node data: {chain_import_data}")
            # Fail the node instead of continuing: an unresolvable import is a
            # BROKEN STEP, and "continue to the next node" made a whole run of
            # missing imports report success end to end (measured 2026-09-23:
            # four unresolvable learned imports logged ERRORs, the inner
            # workflow "completed successfully" and the parent logged
            # "Imported chain executed successfully").  None is the engine's
            # failure convention — core stops with "Node X execution failed".
            return None

        if not os.path.exists(resolved_chain_path):
            logger.error(f"Chain import file not found: {resolved_chain_path}")
            return None

        # ── Self-callback handoff (tail-call) ──
        # A chain that imports ITSELF is a callback, not a nested subprocess:
        # the current execution completes at this node and a fresh execution
        # of the same chain starts in the single execution slot (no stacking).
        # Perpetual agents are built on this (unbounded callbacks, ESC stops).
        # Non-self-referential imports keep the nesting behavior below.
        try:
            _current_path = os.path.normcase(os.path.abspath(
                str(getattr(self, 'chain_file_path', '') or '')
            ))
        except Exception:
            _current_path = ""
        if _current_path and _current_path == os.path.normcase(
            os.path.abspath(resolved_chain_path)
        ):
            logger.info(
                "Self-callback: '%s' imports itself — completing this "
                "execution and handing off to a fresh run of the same chain.",
                resolved_chain_path,
            )
            try:
                self.llm_executor.set_variable(
                    "_chain_tailcall_path", resolved_chain_path
                )
            except Exception:
                pass
            return "__done__"

        # Execute the imported chain for the specified number of loops using direct execution
        for i in range(loop_count):
            if stop_flag and stop_flag():
                logger.info("Chain import execution stopped by user request")
                return None
            
            logger.info(f"Starting chain import loop {i + 1}/{loop_count}")
            
            try:
                # Check stop flag before loading chain
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected before loading imported chain - aborting")
                    return None
                
                # Always load the imported chain directly from disk to ensure we have the full, up-to-date workflow
                # The embedded nodes in the JSON are only for UI representation and may be incomplete or stale.
                logger.info(f"Loading imported chain from disk: {resolved_chain_path}")
                import json
                with open(resolved_chain_path, 'r', encoding='utf-8') as f:
                    chain_config = json.load(f)
                
                # Check stop flag after loading chain config
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected after loading chain config - aborting")
                    return None
                
                # Pass the resolved path so nested imports and relative assets are correctly located
                # Also pass shared variables to allow aggregation of apps
                # ── Collect only connected upstream data for sub-chain (NOT all vars) ──
                # Instead of dumping ALL parent variables into the sub-chain (which
                # leaks every node output, _writing flags, and accumulated context),
                # resolve only the data that is connected to this chain_import node's
                # input ports via the graph topology.
                sub_vars = {}
                for _inp in (node.get('inputs') or []):
                    _from = _inp.get('from_node')
                    if not _from:
                        continue
                    _out_port = (_inp.get('output_type')
                                 or _inp.get('output_port')
                                 or 'output')
                    _val = self.port_store.get_output(
                        self.chain_id, _from, _out_port,
                    )
                    if _val is not None:
                        # Store as node_{from}_output for sub-chain Input node consumption
                        sub_vars[f"node_{_from}_output"] = _val
                        # Also set as the input context for agent-modifiable nodes
                        sub_vars[f"node_{_from}_input_context"] = {
                            "type": "upstream",
                            "input": str(_val),
                            "output": str(_val),
                            "label": _inp.get('input_port', ''),
                        }
                # Inject input context for sub-chain Input nodes — use _chain_input_context
                # from port connection if available, otherwise from parent variable.
                _chain_input_ctx = str(sub_vars.get("node_{}_output".format(
                    # Try the first connected input as fallback
                    (node.get('inputs') or [{}])[0].get('from_node', '')
                ) or "")) if node.get('inputs') else ""
                try:
                    _parent_ctx = self.llm_executor.get_variable("_chain_input_context") or ""
                    if _parent_ctx and str(_parent_ctx).strip():
                        _chain_input_ctx = str(_parent_ctx)
                except Exception:
                    pass

                _ask_cb = self.llm_executor.get_variable("_ask_user_callback")
                _ask_v2_cb = self.llm_executor.get_variable("_ask_user_v2_callback")
                _llama_url = self.llm_executor.get_variable("_llamacpp_server_url") or ""
                _output_cb = self.llm_executor.get_variable("_on_output_ready_callback")

                # Inject input context for the sub-chain's agent-modifiable Input nodes
                if _chain_input_ctx:
                    sub_vars["_chain_input_context"] = _chain_input_ctx

                # Include runtime callbacks (NOT node outputs or accumulation)
                sub_vars["_ask_user_callback"] = _ask_cb
                sub_vars["_ask_user_v2_callback"] = _ask_v2_cb
                sub_vars["_llamacpp_server_url"] = _llama_url
                sub_vars["_on_output_ready_callback"] = _output_cb

                # Orchestrator scoping pair + propagation packet: travels with
                # the input context so a worker's Input extractors get the tiny
                # task and a nested orchestrator starts from its parent's root
                # goal, plan cursor and world — input_ops/the nested goal
                # resolver read them from ITS executor, this sub-player.
                for _pair_name in ("_chain_goal", "_chain_step",
                                   "_chain_root_goal", "_chain_plan",
                                   "_chain_state"):
                    try:
                        _pair_val = self.llm_executor.get_variable(_pair_name)
                    except Exception:
                        _pair_val = None
                    if _pair_val:
                        sub_vars[_pair_name] = _pair_val

                # Mark the imported player as nested so per-execution setup
                # (e.g. Ollama runner cleanup) runs once at the top level.
                sub_vars["_is_imported_chain"] = True

                # Export each parent context node's output as a per-node-id
                # COPY (shared_context_<node_id>) so a sub-chain context node
                # with the SAME node id (inserted from the left bar) can pick
                # up the source's context.  A context node's output is ONLY
                # what its own connected input produced — fine context
                # management, NOT a concatenation of every LLM in the chain
                # (context is appended node-to-node by chaining LLM nodes).
                # Each chain instance holds its own copy and cleans it when
                # IT finishes; the source keeps its copy until the source
                # chain itself finishes.
                try:
                    _parent_chain_id = getattr(self, 'chain_id', '') or ''
                    for _ctx_nid, _ctx_node in (self.workflow_graph or {}).items():
                        if _ctx_node.get('type') != 'context':
                            continue
                        _ctx_val = self.port_store.get_output(
                            _parent_chain_id, _ctx_nid, 'context',
                        )
                        if _ctx_val is None:
                            _ctx_val = self.llm_executor.get_variable(
                                f"node_{_ctx_nid}_context"
                            )
                        if _ctx_val and str(_ctx_val).strip():
                            sub_vars[f"shared_context_{_ctx_nid}"] = str(_ctx_val)
                            logger.info(
                                "Exported context copy for node %s to "
                                "imported chain (%d chars)",
                                _ctx_nid, len(str(_ctx_val)),
                            )
                except Exception as _scx_err:
                    logger.warning(
                        "Failed to export parent shared context: %s", _scx_err,
                    )

                imported_player = MultiSequencePlayer(
                    chain_config,
                    chain_file_path=resolved_chain_path,
                    variables=sub_vars,
                    agent_query=_chain_input_ctx or "",  # Pass context to subchain
                    ask_user_callback=_ask_cb,
                    ask_user_v2_callback=_ask_v2_cb,
                    llamacpp_server_url=_llama_url,
                    on_output_ready=_output_cb,
                )
                
                # Apply sandbox context if configured
                if app_context_override:
                    logger.info(f"Applying sandbox override to imported chain: {app_context_override}")
                    if hasattr(imported_player, 'workflow_executor') and imported_player.workflow_executor:
                        imported_player.workflow_executor.global_app_context_override = app_context_override
                    if hasattr(imported_player, 'sequence_executor') and imported_player.sequence_executor:
                        imported_player.sequence_executor.global_app_context_override = app_context_override
                else:
                    # Inherit parent's sandbox context if the imported chain is not explicitly sandboxed
                    parent_sandbox_ctx = None
                    try:
                        if hasattr(self, 'workflow_executor') and self.workflow_executor:
                            parent_sandbox_ctx = getattr(self.workflow_executor, 'global_app_context_override', None)
                        elif hasattr(self, 'sequence_executor') and self.sequence_executor:
                            parent_sandbox_ctx = getattr(self.sequence_executor, 'global_app_context_override', None)
                    except Exception:
                        pass
                    
                    if parent_sandbox_ctx and isinstance(parent_sandbox_ctx, dict) and parent_sandbox_ctx.get("sandboxed"):
                        logger.info(f"Inheriting parent sandbox override to imported chain: {parent_sandbox_ctx}")
                        # Share the parent's override dict BY REFERENCE. Nested chain
                        # imports must join the invoker's RDP session and share ONE
                        # sandbox lifecycle: begin/end_chain_sandbox refcount this
                        # single dict, so the session is only torn down when the
                        # outermost chain finishes. Copying would give every nested
                        # level an independent refcount — the first nested chain to
                        # complete would hit refcount 0 and log off the shared
                        # LoOperAgent session while the parent still runs.
                        if hasattr(imported_player, 'workflow_executor') and imported_player.workflow_executor:
                            imported_player.workflow_executor.global_app_context_override = parent_sandbox_ctx
                        if hasattr(imported_player, 'sequence_executor') and imported_player.sequence_executor:
                            imported_player.sequence_executor.global_app_context_override = parent_sandbox_ctx

                # Propagate chain description to the imported player so its context
                # nodes can derive semantic keys from their chain's description.
                try:
                    child_desc = ""
                    try:
                        if isinstance(chain_config, dict):
                            child_desc = chain_config.get("description", "") or ""
                    except Exception:
                        pass
                    if child_desc:
                        if hasattr(imported_player, 'workflow_executor') and imported_player.workflow_executor:
                            imported_player.workflow_executor._chain_description = child_desc
                        self.llm_executor.set_variable("_chain_description", child_desc)
                except Exception:
                    pass
                        
                # Share selection memory with imported chain player
                try:
                    # Do not share selection memory to allow fresh runs on recursive chains/loops.
                    # This fixes the issue where conditionals keep hitting the same cached elements in loops.
                    imported_player.selection_memory = {}
                    
                    # Propagate the fresh memory to all modular components
                    try:
                        if hasattr(imported_player, 'fallback_handler') and imported_player.fallback_handler:
                            imported_player.fallback_handler.selection_memory = imported_player.selection_memory
                    except Exception:
                        pass
                    try:
                        if hasattr(imported_player, 'sequence_executor') and imported_player.sequence_executor:
                            imported_player.sequence_executor.selection_memory = imported_player.selection_memory
                    except Exception:
                        pass
                    try:
                        if hasattr(imported_player, 'workflow_executor') and imported_player.workflow_executor:
                            imported_player.workflow_executor.selection_memory = imported_player.selection_memory
                    except Exception:
                        pass
                except Exception:
                    pass
                
                # Execute the imported chain using the same method as normal chains
                logger.info(f"Executing imported chain: {resolved_chain_path}")
                success = imported_player.play_chain(stop_flag, preserve_selection_memory=False)

                # ── Absorb a self-callback handoff from the imported chain ──
                # A chain that imports ITSELF completes with a tail-call handoff
                # (_chain_tailcall_path set).  Absorb it HERE by re-running the
                # imported chain until it completes WITHOUT a handoff: the
                # recursive loop stays inside this import node, so when it
                # eventually cancels (e.g. a condition validates true) control
                # returns to the ENCLOSING chain and its remaining nodes keep
                # executing.  The enclosing chain must always survive the
                # import — never bubble the handoff upward (that would release
                # the enclosing chain and drop its remaining nodes).
                while True:
                    _child_tail = ""
                    try:
                        _child_we = getattr(imported_player, "workflow_executor", None)
                        _child_le = (
                            getattr(_child_we, "llm_executor", None)
                            if _child_we is not None else None
                        )
                        _child_tail = (
                            _child_le.get_variable("_chain_tailcall_path")
                            if _child_le is not None else ""
                        ) or ""
                    except Exception:
                        pass
                    if not str(_child_tail).strip():
                        break
                    logger.info(
                        "Self-callback from imported chain '%s' — re-running it "
                        "until the recursive loop cancels (control returns to "
                        "the enclosing chain)", _child_tail,
                    )
                    if stop_flag and stop_flag():
                        logger.info(
                            "Stop flag detected in self-callback loop - aborting"
                        )
                        return None
                    # play_chain() resets component state at the start of every
                    # run (variables restored to the constructor snapshot, so
                    # _chain_tailcall_path is cleared), making same-player
                    # re-runs safe for the flat recursive loop.
                    success = imported_player.play_chain(
                        stop_flag, preserve_selection_memory=False,
                    )
                    if not success:
                        logger.error(
                            "Self-callback re-run of imported chain failed: %s",
                            resolved_chain_path,
                        )
                        break

                # ── Propagate agent-mode mid-execution output flag upward ──
                # When an output node inside this imported (sub)chain fired the
                # on_output_ready callback in agent mode, the flag is stored on
                # the SUBCHAIN's own LLM executor.  The top-level agent
                # (ChainExecutor.run_chain) only checks the top-level executor,
                # so without this copy it would emit a redundant final bubble
                # AFTER the subchain's mid-execution output was already shown.
                # Mirror the flag onto the parent executor so subchain output
                # nodes behave exactly like system-chain output nodes (single
                # display, no duplicate).  play_chain() resets variables at the
                # start of every run, so reading after the self-callback loop
                # reflects the FINAL run's state.
                try:
                    _child_we = getattr(imported_player, "workflow_executor", None)
                    _child_le = (
                        getattr(_child_we, "llm_executor", None)
                        if _child_we is not None else None
                    )
                    if _child_le is not None:
                        _child_mid_shown = bool(
                            _child_le.get_variable(
                                "_agent_mid_execution_outputs_shown"
                            ) or False
                        )
                        if _child_mid_shown:
                            self.llm_executor.set_variable(
                                "_agent_mid_execution_outputs_shown", True
                            )
                            logger.info(
                                "[CHAIN-IMPORT] Propagated mid-execution output "
                                "flag from imported chain '%s' to parent",
                                resolved_chain_path,
                            )
                except Exception as _mid_exc:
                    logger.warning(
                        "Failed to propagate mid-execution output flag: %s",
                        _mid_exc,
                    )

                # Check stop flag after chain execution
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected after imported chain execution - aborting")
                    return None
                
                if not success:
                    logger.error(f"Imported chain execution failed: {resolved_chain_path}")
                    # Continue to next loop iteration
                    continue
                else:
                    logger.info(f"Imported chain executed successfully: {resolved_chain_path}")
                    summary = None
                    if hasattr(imported_player, "workflow_executor") and imported_player.workflow_executor:
                        summary = getattr(imported_player.workflow_executor, "last_node_summary", None)
                    if summary and hasattr(self, "llm_executor") and self.llm_executor:
                        tool_id = node.get('id') or node.get('node_id') or ""
                        if not tool_id and isinstance(node.get("data"), dict):
                            tool_id = node.get("data", {}).get("id") or node.get("data", {}).get("node_id") or ""
                        if not tool_id and resolved_chain_path:
                            tool_id = os.path.splitext(os.path.basename(resolved_chain_path))[0]
                        chain_name = ""
                        chain_desc = ""
                        try:
                            if isinstance(chain_config, dict):
                                chain_name = chain_config.get("name") or ""
                                chain_desc = chain_config.get("description") or ""
                        except Exception:
                            pass
                        # What the tool RETURNED: every Output node the
                        # sub-chain executed.  An Output node stores its
                        # content unconditionally — declaring a
                        # ``variable_name`` / enabling ``emit_data`` only
                        # exposes it as a PORT for other nodes — so the
                        # result is read here regardless, for the agent
                        # context (the summariser used to see the tool's
                        # description instead of anything it produced).
                        _tool_result_text = self._collect_imported_outputs(
                            imported_player, chain_config, node)
                        # Monotonic execution stamp.  A record OUTLIVES the
                        # turn that produced it and the turn text is built from
                        # these records, so without a stamp every later turn
                        # re-reports every tool that ever ran — the summariser
                        # then describes tools it did not use in that turn.
                        _seq = 0
                        try:
                            _seq = int(self.llm_executor.get_variable(
                                "_tool_exec_seq") or 0) + 1
                            self.llm_executor.set_variable(
                                "_tool_exec_seq", _seq)
                        except Exception:
                            _seq = 0
                        record = {
                            "tool_id": str(tool_id),
                            "chain_path": str(resolved_chain_path),
                            "chain_name": str(chain_name),
                            "chain_description": str(chain_desc),
                            "tool_output": _tool_result_text,
                            "last_node": summary,
                            "seq": _seq
                        }
                        try:
                            self.llm_executor.set_variable(f"node_{tool_id}_last_chain_result", record)
                        except Exception:
                            pass
                        try:
                            results = self.llm_executor.get_variable("chain_tool_results") or {}
                            if not isinstance(results, dict):
                                results = {}
                            results[str(tool_id)] = record
                            self.llm_executor.set_variable("chain_tool_results", results)
                        except Exception:
                            pass

                        try:
                            parts = []
                            if tool_id:
                                parts.append(f"Tool ID: {tool_id}")
                            if chain_name:
                                parts.append(f"Chain Name: {chain_name}")
                            if chain_desc:
                                parts.append(f"Chain Description: {chain_desc}")
                            if _tool_result_text:
                                parts.append(f"Result: {_tool_result_text}")
                            if summary:
                                parts.append(f"Last Node: {summary.get('node_id')} ({summary.get('type')})")
                                if summary.get("type") == "code":
                                    parts.append(f"Code Result: {summary.get('result')}")
                                elif summary.get("type") == "output":
                                    parts.append(f"Output: {summary.get('result')}")
                                elif summary.get("type") == "conditional":
                                    parts.append(f"Conditional Result: {summary.get('result')}")
                                    if summary.get("criteria"):
                                        parts.append(f"Criteria: {summary.get('criteria')}")
                                elif summary.get("type") == "llm":
                                    llm_result = summary.get("result") or {}
                                    if isinstance(llm_result, dict):
                                        q = llm_result.get("query")
                                        r = llm_result.get("response")
                                        if q:
                                            parts.append(f"LLM Query: {q}")
                                        if r is not None:
                                            parts.append(f"LLM Response: {r}")
                                elif summary.get("type"):
                                    # Recorded-action tools (sequence, web, form
                                    # filler, handle) have no "result" slot, so
                                    # this text used to end at "Last Node: 0x…
                                    # (sequence)" and the model saw ONLY the
                                    # tool's DESCRIPTION — which it then
                                    # recited back as if it had done the work.
                                    # Reaching this point means the chain
                                    # COMPLETED, so say that much.
                                    _sdesc = str(
                                        summary.get("description") or ""
                                    ).strip()
                                    parts.append(
                                        "Outcome: ran successfully (%s)%s"
                                        % (summary.get("type"),
                                           " - " + _sdesc[:200] if _sdesc else "")
                                    )
                            result_text = "\n".join([p for p in parts if p])
                            if result_text:
                                self.llm_executor.set_variable("chain_tool_last_result_text", result_text)
                        except Exception:
                            pass
                
            except Exception as e:
                logger.error(f"Error during chain import execution: {e}")
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                # Continue to next loop iteration
                continue
            
            # Add extra delay between loops if specified
            if extra_delay > 0 and i < loop_count - 1:  # Don't delay after the last loop
                logger.info(f"Adding extra delay of {extra_delay} seconds between loops")
                delay_remaining = extra_delay
                while delay_remaining > 0:
                    if stop_flag and stop_flag():
                        logger.info("Chain import execution stopped during delay")
                        return None
                    sleep_time = min(0.1, delay_remaining)
                    time.sleep(sleep_time)
                    delay_remaining -= sleep_time
        
        logger.info("Chain import node execution completed")
        
        # Continue to next node in the workflow
        connections = node.get('connections', {})
        if 'output' in connections and connections['output']:
            next_connection = connections['output'][0]
            return next_connection['node_id']
        
        return "__done__"

    def _collect_imported_outputs(self, imported_player, chain_config, node=None):
        """Content of the imported chain's Output nodes, in chain order.

        Delivered DIRECTLY to the outer connection (the tool consumer / agent
        context) — no ports involved.  The node's ``data_output_nodes``
        selection picks WHICH Output nodes are included; ``emit_data`` ON
        restricts delivery to that selection, OFF delivers every Output node.
        An Output node always stores its content (port store + the legacy
        ``node_<id>_output`` variable).  Returns "" when there is nothing to
        say.
        """
        try:
            out_nodes = chain_config.get('output_nodes') or []
        except Exception:
            out_nodes = []

        # Optional selection: restrict to the chosen Output node ids.
        node_data = {}
        if isinstance(node, dict):
            node_data = node.get('data', {}) or {}
        emit = node_data.get('emit_data', False)
        if isinstance(emit, str):
            emit = emit.strip().lower() == 'true'
        spec = node_data.get('data_output_nodes') or []
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except Exception:
                spec = [s for s in spec.split(',') if s.strip()]
        wanted = {str(s).strip() for s in (spec or []) if str(s).strip()}
        if emit and wanted:
            out_nodes = [
                o for o in out_nodes if isinstance(o, dict)
                and str(o.get('node_id') or o.get('id') or '').strip() in wanted
            ]

        if not out_nodes:
            return ""
        sub_executor = getattr(imported_player, 'workflow_executor', None)
        sub_store = getattr(sub_executor, 'port_store', None)
        sub_chain = getattr(sub_executor, 'chain_id', '') or ''
        sub_llm = getattr(sub_executor, 'llm_executor', None)
        parts = []
        for out in out_nodes:
            if not isinstance(out, dict):
                continue
            out_id = out.get('node_id') or out.get('id')
            if not out_id:
                continue
            val = None
            try:
                if sub_store is not None and sub_chain:
                    val = sub_store.get_output(sub_chain, out_id, 'output')
            except Exception:
                val = None
            if val is None and sub_llm is not None:
                try:
                    val = sub_llm.get_variable(f"node_{out_id}_output")
                except Exception:
                    val = None
            if val is None or not str(val).strip():
                continue
            text = str(val).strip()[:400]
            label = str(out.get('label') or out.get('name') or '').strip()
            parts.append("%s: %s" % (label, text) if label else text)
        if not parts:
            return ""
        result = "\n".join(parts)
        logger.info(
            "[CHAIN-IMPORT] tool result: %d Output node(s), %d chars",
            len(parts), len(result),
        )
        return result
