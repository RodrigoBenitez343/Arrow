import time
import logging
logger = logging.getLogger(__name__)

class DependenciesMixin:
    def _find_all_starting_nodes(self):
        """
        Find all starting nodes for workflow execution (nodes with no inputs).
        
        Returns:
            list: List of node IDs that have no input dependencies
        """
        starting_nodes = []
        
        logger.info(f"=== _find_all_starting_nodes called on graph with {len(self.workflow_graph)} nodes ===")
        
        # Debug: Log all nodes and their inputs
        for node_id, node_data in self.workflow_graph.items():
            inputs = node_data.get('inputs', [])
            logger.debug(f"Node {node_id} (type: {node_data['type']}) has {len(inputs)} inputs: {inputs}")
            if node_data['type'] == 'conditional':
                logger.debug(f"  Conditional node {node_id} connections: {node_data.get('connections', {})}")
            elif node_data['type'] == 'sequence':
                logger.debug(f"  Sequence node {node_id} connections: {node_data.get('connections', {})}")
        
        for node_id, node_data in self.workflow_graph.items():
            # Check both 'inputs' field and 'connections' for input dependencies
            has_inputs = 'inputs' in node_data and node_data['inputs']
            has_input_connections = False
            
            # Check connections for input dependencies
            connections = node_data.get('connections', {})
            if connections:
                # Look for any input connections (not just output connections)
                for port_name, port_connections in connections.items():
                    if port_name.startswith('input') or port_name == 'input':
                        if port_connections:
                            has_input_connections = True
                            break
            
            if not has_inputs and not has_input_connections:
                # Router subroutines (the generic 'tool_provider' flag: sources
                # of llm 'tools' / orchestrator 'brains'|'chains' edges) are
                # never starting nodes — their router invokes them on demand.
                # The flag is unambiguous, so no node-type list is needed.
                if node_data.get('tool_provider'):
                    logger.debug(
                        "Skipping tool-provider node from starting nodes: %s "
                        "(type=%s)", node_id, node_data.get('type'),
                    )
                    continue
                # Context nodes are NEVER excluded from starting-node detection:
                # they are graph nodes with real execution semantics (b6).
                starting_nodes.append(node_id)
                logger.info(f"Found starting node: {node_id} (type: {node_data['type']})")


        if not starting_nodes:
            for node_id, node_data in self.workflow_graph.items():
                if node_data.get('type') != 'llm':
                    continue
                inputs = node_data.get('inputs', []) or []
                if not inputs:
                    continue
                all_nonblocking = True
                for inp in inputs:
                    port = str(inp.get('input_port'))
                    if port not in ('tools', 'context', 'prompt'):
                        all_nonblocking = False
                        break
                    src = self.workflow_graph.get(inp.get('from_node'), {})
                    if port == 'tools':
                        is_nonblocking = bool(src.get('tool_provider'))
                        if not is_nonblocking:
                            all_nonblocking = False
                            break
                if all_nonblocking:
                    starting_nodes.append(node_id)
                    logger.info(f"Treating LLM {node_id} as starting node due to non-blocking inputs")
        
        return starting_nodes

    def _find_newly_ready_nodes(self, completed_node_id, completed_nodes):
        """
        Find nodes that are now ready to execute after a node completes.
        
        Args:
            completed_node_id (str): The node that just completed
            completed_nodes (set): Set of all completed node IDs
        
        Returns:
            list: List of node IDs that are now ready to execute
        """
        newly_ready = []
        
        if completed_node_id in self.workflow_graph:
            completed_node = self.workflow_graph[completed_node_id]
            connections = completed_node.get('connections', {})
            completed_is_llm = completed_node.get('type') == 'llm'
            defer_outputs_due_to_typing = False
            if completed_is_llm:
                # If upstream LLM is still typing, defer scheduling of its targets (but continue dependency scan)
                try:
                    vars_snapshot = self.llm_executor.get_all_variables()
                except Exception:
                    vars_snapshot = {}
                if vars_snapshot.get(f"node_{completed_node_id}_writing"):
                    logger.info(f"Deferring targets of LLM {completed_node_id}: still typing")
                    defer_outputs_due_to_typing = True
            has_pending_non_llm_outputs = False
            for output_type, output_connections in connections.items():
                for connection in output_connections:
                    tid = connection.get('node_id')
                    if not tid:
                        continue
                    tnode = self.workflow_graph.get(tid, {})
                    if output_type == 'output' and tnode.get('type') != 'llm' and tid not in completed_nodes:
                        has_pending_non_llm_outputs = True
                        break
                if has_pending_non_llm_outputs:
                    break
            if not defer_outputs_due_to_typing:
                valid_ports = None
                try:
                    if completed_node.get('type') == 'code':
                        valid_ports = [completed_node.get('last_output_port') or 'output']
                except Exception:
                    valid_ports = None
                for output_type, output_connections in connections.items():
                    if valid_ports and output_type not in valid_ports:
                        continue
                    for connection in output_connections:
                        target_node_id = connection.get('node_id')
                        if not target_node_id or target_node_id in completed_nodes:
                            continue
                        target_node = self.workflow_graph.get(target_node_id, {})
                        if completed_is_llm and has_pending_non_llm_outputs and target_node.get('type') == 'llm':
                            continue
                        # Allow context port connections to trigger context node execution.
                        # Without this, ContextNodes are never scheduled and LLM downstream
                        # nodes read empty context (pre-initialized value).
                        if output_type not in ('output', 'true', 'false', 'context') and (not (completed_node.get('type') == 'code' and output_type == (completed_node.get('last_output_port') or 'output'))):
                            continue
                        # Context port → non-context targets (e.g. Output nodes) are data
                        # supplements already handled via their 'output' connection.
                        # Only 'context' → context-type targets need explicit scheduling
                        # so the ContextNode's _execute_context_node builds structured context
                        # before downstream LLMs read from it.
                        if output_type == 'context' and target_node.get('type') != 'context':
                            continue
                        if self._are_all_dependencies_satisfied(target_node_id, completed_nodes):
                            if target_node_id not in newly_ready:
                                newly_ready.append(target_node_id)
                                logger.debug(f"Node {target_node_id} is now ready (output connection from {completed_node_id})")
        
        # Full graph scan: after every node completion, check ALL unvisited nodes.
        # This ensures conditionals that become reachable later are always discovered.
        for node_id, node_data in self.workflow_graph.items():
            if node_id in completed_nodes or node_id in newly_ready:
                continue  # Skip already completed or already identified nodes

            if self._are_all_dependencies_satisfied(node_id, completed_nodes):
                if node_id not in newly_ready:
                    newly_ready.append(node_id)
                    logger.debug(f"Node {node_id} is now ready (full scan after {completed_node_id})")

        return newly_ready

    def _llm_inputs_have_pending_non_llm_outputs(self, node_id, completed_nodes):
        try:
            node = self.workflow_graph.get(node_id, {})
            inputs = node.get('inputs', [])
            for inp in inputs:
                from_node = inp.get('from_node')
                src = self.workflow_graph.get(from_node, {})
                if src.get('type') != 'llm':
                    continue
                outputs = src.get('connections', {}).get('output', [])
                for conn in outputs:
                    tid = conn.get('node_id')
                    if not tid:
                        continue
                    tnode = self.workflow_graph.get(tid, {})
                    if str(inp.get('input_port')) in ('tools', 'context', 'prompt'):
                        continue
                    if tnode.get('type') != 'llm' and tid not in completed_nodes:
                        return True
            return False
        except Exception:
            return False
        
    @staticmethod
    def _is_branch_source(node):
        """True when this node routes execution through branch ports.

        Conditionals always branch.  Branch-capable Input nodes (the
        ``decision_mode`` toggle or routed yes/no questions with
        ``route_on_answer``) branch only when true/false connections are
        actually wired.  Orchestrators branch on fulfilment ('output' when
        achieved, 'route' when not) only when a ``route`` connection is
        actually wired.  An unwired routing flag keeps the node on the
        normal output path.
        """
        try:
            node = node or {}
            ntype = node.get('type')
            if ntype == 'conditional':
                return True
            if ntype == 'orchestrator':
                conns = node.get('connections', {})
                return isinstance(conns, dict) and bool(conns.get('route'))
            if ntype == 'llm':
                # An LLM node with the orchestrator switch ON branches like an
                # orchestrator ('output' achieved / 'route' unfulfilled).
                data = node.get('data', {}) or {}
                cfg = data.get('llm_configuration') or {}
                on = (bool(data.get('orchestrator_mode'))
                      or bool(cfg.get('orchestrator_mode'))
                      or str(data.get('mode') or '').strip().lower() == 'orchestrator')
                if not on:
                    return False
                conns = node.get('connections', {})
                return isinstance(conns, dict) and bool(conns.get('route'))
            if ntype != 'input':
                return False
            data = node.get('data', {}) or {}

            def _on(v):
                return str(v).strip().lower() in ('true', '1', 'yes', 'on')

            if not (_on(data.get('decision_mode')) or _on(data.get('route_on_answer'))):
                return False
            conns = node.get('connections', {})
            return isinstance(conns, dict) and ('true' in conns or 'false' in conns)
        except Exception:
            return False

    def _get_output_connected_nodes(self, node_id):
        """
        Get all nodes that are connected to the execution outputs of the given node.
        
        Only follows EXECUTION ports ('output', 'true', 'false', 'error',
        'route', and 'context' when the target is a context-type node).
        Data-only ports
        ('ctx_out', 'tools', 'query', etc.) are excluded — they carry data
        between nodes but do NOT drive workflow execution order.

        IMPORTANT: Including data-only ports (especially 'ctx_out' from context
        nodes) would cause false loop-back detection when the target node is
        already in completed_nodes, resetting the entire loop and dropping
        legitimate pending nodes (e.g. Output) from the execution queue.
        
        Args:
            node_id (str): The node ID to get output connections for
            
        Returns:
            list: List of node IDs that are connected to this node's execution outputs
        """
        connected_nodes = []
        
        if node_id not in self.workflow_graph:
            return connected_nodes
            
        node = self.workflow_graph[node_id]
        connections = node.get('connections', {})
        
        # Only follow execution ports ('route' included: a route-branching
        # orchestrator drives execution through it).  Data-only ports (ctx_out,
        # tools, etc.) are explicitly excluded — they do not drive the
        # execution graph and would cause false loop-back detection (see
        # _find_newly_ready_nodes for the matching filtering logic used during
        # dependency scanning).
        EXECUTION_PORTS = {'output', 'true', 'false', 'error', 'route'}
        for output_type, output_connections in connections.items():
            if not isinstance(output_connections, list):
                continue
            # Allow 'context' port only when it targets a context-type node
            # (LLM context output → context node execution). All other
            # data ports (ctx_out, tools, query) are excluded.
            if output_type not in EXECUTION_PORTS:
                if output_type != 'context':
                    continue
            for connection in output_connections:
                target_node_id = connection.get('node_id')
                if not target_node_id:
                    continue
                # For 'context' port: only include if target is a context node
                if output_type == 'context':
                    target_node = self.workflow_graph.get(target_node_id, {})
                    if target_node.get('type') != 'context':
                        continue
                connected_nodes.append(target_node_id)
        
        return connected_nodes
    
    def _are_all_dependencies_satisfied(self, node_id, completed_nodes):
        """
        Check if dependencies for a node are satisfied.
        For conditional branching, only ONE input path needs to be satisfied.
        
        Args:
            node_id (str): The node ID to check dependencies for
            completed_nodes (set): Set of completed node IDs
            
        Returns:
            bool: True if dependencies are satisfied, False otherwise
        """
        if node_id not in self.workflow_graph:
            logger.debug(f"Node {node_id} not found in workflow graph")
            return False
            
        was_skipped = node_id in self.skipped_nodes
        if was_skipped:
            # Skip marks are advisory (execution-skip-reliability b2): a node
            # whose graph dependencies are satisfied must still execute.  The
            # real dependency logic below decides; the override is logged at
            # each ready return.
            logger.debug(f"Node {node_id} is in skipped_nodes — evaluating deps anyway (advisory skip)")

        def _ready():
            if was_skipped:
                logger.warning(
                    "[SKIP-OVERRIDE] Node %s (type=%s) skip mark overridden — "
                    "all graph dependencies satisfied",
                    node_id, node.get('type'),
                )
            return True

        node = self.workflow_graph[node_id]

        # Single chokepoint (task #1): a router subroutine NEVER becomes ready
        # for the main loop — unconditionally, regardless of selected_by_llm
        # (that key is a dispatch/reporting marker, not an execution grant).
        # Its router invokes it directly.  This also closes the re-arm window
        # (typing rescan / loop-back un-skip) for flagged nodes.
        if node.get('tool_provider'):
            logger.debug(
                "Node %s is a router subroutine (tool_provider) — not ready",
                node_id,
            )
            return False

        inputs = node.get('inputs', [])
        logger.debug(f"Node {node_id} inputs: {inputs}")
        
        if not inputs:
            logger.debug(f"Node {node_id} has no inputs, marking as ready")
            return _ready()

        # Special logic: If a node has multiple inputs (e.g. it's a merge point after a branch),
        # it is ready if ANY of its un-skipped conditional branch inputs have completed
        # AND all of its non-conditional required inputs have completed.
        
        has_conditional_input = False
        conditional_satisfied = False
        conditional_completed = False
        conditional_chosen_target = None  # target of the branch the conditional chose
        
        for input_connection in inputs:
            from_node_id = input_connection['from_node']
            if from_node_id in self.skipped_nodes:
                continue
                
            from_node = self.workflow_graph.get(from_node_id, {})
            if self._is_branch_source(from_node):
                has_conditional_input = True
                # Defensive initialization: chosen is only set when the
                # conditional is in completed_nodes.  The reference at the
                # end of this block (line ~337) must not throw UnboundLocalError
                # when the conditional hasn't completed yet.
                chosen = None
                if from_node_id in completed_nodes:
                    conditional_completed = True
                    if str(from_node.get('type')) == 'orchestrator':
                        # Fulfilment branch: the edge's bucket IS the route
                        # taken — 'route' = the run did not achieve its goal,
                        # 'output' = achieved.  Ports are multi-output, so
                        # every target of the taken bucket is satisfied (no
                        # single 'chosen' node equality).
                        _bucket = str(
                            input_connection.get('output_type')
                            or input_connection.get('output_port')
                            or 'output'
                        )
                        _fulfilled = bool(
                            from_node.get('_orchestrator_fulfilled')
                            or (from_node.get('data') or {}).get('_orchestrator_fulfilled')
                        )
                        if (_bucket == 'route') != _fulfilled:
                            conditional_satisfied = True
                            chosen = node_id
                        else:
                            chosen = from_node.get('chosen_branch_node')
                    else:
                        # Check if THIS specific conditional path was the one chosen
                        chosen = from_node.get('chosen_branch_node')
                        if not chosen and 'data' in from_node:
                            chosen = from_node['data'].get('chosen_branch_node')
                    
                        if not chosen and 'last_conditional_result' in from_node:
                            res = from_node['last_conditional_result']
                            branch_name = 'true' if res else 'false'
                            conns = from_node.get('connections', {})
                            if isinstance(conns, dict):
                                b_conns = conns.get(branch_name, [])
                                if b_conns:
                                    chosen = b_conns[0].get('target_node_id') or b_conns[0].get('node_id')
                            elif isinstance(conns, list):
                                for c in conns:
                                    if str(c.get('output_port')).lower() == branch_name:
                                        chosen = c.get('target_node_id') or c.get('node_id')
                                        break
                    
                        if node_id == chosen:
                            conditional_satisfied = True
                    logger.info(
                        "[DEP-CHECK] Node %s (type=%s): conditional %s in completed_nodes, "
                        "chosen=%s, node_id=%s, match=%s, cond_result=%s",
                        node_id, node.get('type'), from_node_id,
                        str(chosen)[:80], node_id,
                        node_id == chosen,
                        from_node.get('last_conditional_result'),
                    )
                else:
                    logger.info(
                        "[DEP-CHECK] Node %s (type=%s): conditional %s NOT in completed_nodes yet",
                        node_id, node.get('type'), from_node_id,
                    )
                # Save chosen target for reachability check (merge-point detection)
                if chosen:
                    conditional_chosen_target = chosen
        
        
        # Execution vs data port semantics:
        # Only 'input' port connections are execution-blocking — they drive
        # the execution order. All named ports (context, ctx_in, ctx_out,
        # tools, query, etc.) are data-only and NEVER gate readiness.
        # Conditional inputs (from 'conditional' type nodes) ARE execution
        # inputs even when they use the base 'input' port — the conditional
        # gating below handles their branch semantics.
        filtered_inputs = []
        for _inp in inputs:
            ip = str(_inp.get('input_port', 'input'))
            if ip != 'input':
                continue  # Named ports are data-only, never block
            if _inp['from_node'] in self.skipped_nodes:
                continue
            filtered_inputs.append(_inp)

        # A node with no execution inputs but a conditional dependency that
        # chose a different branch should be blocked.
        if not filtered_inputs:
            if has_conditional_input and not conditional_satisfied:
                return False
            return _ready()

        # Prevent readiness if any upstream LLM is still actively typing/outputting
        try:
            vars_snapshot = self.llm_executor.get_all_variables()
        except Exception:
            vars_snapshot = {}
        
        for input_connection in filtered_inputs:
            upstream_id = input_connection.get('from_node')
            upstream_node = self.workflow_graph.get(upstream_id, {})
            if upstream_node.get('type') == 'llm':
                if vars_snapshot.get(f"node_{upstream_id}_writing"):
                    logger.info(f"Deferring node {node_id}: upstream LLM {upstream_id} still typing")
                    return False

        # For LLM targets, defer readiness if upstream LLM inputs have pending non-LLM outputs
        try:
            if node.get('type') == 'llm' and self._llm_inputs_have_pending_non_llm_outputs(node_id, completed_nodes):
                logger.info(f"Deferring LLM {node_id}: pending non-LLM outputs from its LLM inputs")
                return False
        except Exception:
            pass

        # Group filtered inputs by input_port.
        # Within each port group, at least ONE source must be completed (OR semantics).
        # Across different port groups, ALL must be satisfied (AND semantics).
        # This allows natural graph loops where a node has multiple connections
        # to the same port (e.g. two TTS nodes feeding the same Input node) —
        # the node executes when ANY of those sources completes, not all of them.
        port_groups = {}
        for input_connection in filtered_inputs:
            port = str(input_connection.get('input_port', 'input'))
            port_groups.setdefault(port, []).append(input_connection)

        for port, group in port_groups.items():
            any_satisfied = False
            for input_connection in group:
                src_id = input_connection.get('from_node')
                if src_id in completed_nodes or src_id in self.skipped_nodes:
                    any_satisfied = True
                    break
            if not any_satisfied:
                logger.debug(
                    f"Dependency port '{port}' not satisfied for node {node_id}: "
                    f"none of {[c.get('from_node') for c in group]} completed or skipped"
                )
                return False

        # Ensure at least one input across all ports was actually completed
        has_at_least_one_completed = False
        for input_connection in filtered_inputs:
            if input_connection.get('from_node') in completed_nodes:
                has_at_least_one_completed = True
                break

        if not has_at_least_one_completed:
            logger.debug(f"All dependencies for {node_id} were skipped, node itself should be skipped")
            # We don't mark it as skipped here, it should be handled by _mark_skipped
            return False

        # Even when non-conditional inputs are satisfied, a conditional input
        # that chose a different branch must block this node from executing.
        # Only block if the conditional has actually completed — if it hasn't
        # run yet (e.g. first entry into a loop), don't block, since the
        # node has valid non-conditional inputs that can satisfy it.
        #
        # Exception: don't block if the chosen branch's target can reach
        # this node via the graph. This handles merge-point chains where
        # both conditional branches converge on the same downstream node
        # (e.g. a shortcut: true→node directly, false→A→B→C→node).
        if has_conditional_input and conditional_completed and not conditional_satisfied:
            # Find the conditional that didn't choose this node to exclude it
            # from reachability — prevents loop-back paths through the same
            # conditional from falsely indicating reachability.
            gating_conditional = None
            for _inp in inputs:
                _src = self.workflow_graph.get(_inp.get('from_node'), {})
                if _src.get('type') == 'conditional':
                    gating_conditional = _inp.get('from_node')
                    break
            if not self._can_reach(conditional_chosen_target, node_id, exclude_node=gating_conditional):
                logger.debug(f"Conditional dependency not satisfied for node {node_id}")
                return False

        logger.debug(f"All dependencies satisfied for node {node_id}")
        return _ready()

    def _is_dead_branch_node(self, node_id, completed_nodes):
        """True when a still-remaining node can never be armed again.

        A node is dead-branch when every one of its execution inputs is a
        COMPLETED branch source that chose a DIFFERENT path, and — excluding
        that gating conditional from the search — the node is unreachable from
        the branch the conditional actually chose.  The loop-back edge into the
        conditional would otherwise make the node look reachable and hide the
        fact that the run has left its branch for good.

        Such a node is a legitimate dead end (e.g. the terminal 'false' leg of
        a loop's 'continue?' conditional once the loop exits through another
        path), not a stalled dependency, so callers may settle it as SKIPPED
        instead of failing the run.
        """
        node = self.workflow_graph.get(node_id, {})
        if not node:
            return False
        inputs = node.get('inputs') or []
        exec_inputs = [
            _inp for _inp in inputs
            if str(_inp.get('input_port', 'input')) in ('input', '', None)
        ]
        if not exec_inputs:
            return False

        saw_gate = False
        for _inp in exec_inputs:
            src_id = _inp.get('from_node')
            if src_id in self.skipped_nodes:
                continue
            src_node = self.workflow_graph.get(src_id, {})
            if not self._is_branch_source(src_node):
                # A non-branching source that has not completed may still
                # arm this node later — not provably dead.
                if src_id not in completed_nodes:
                    return False
                continue
            if src_id not in completed_nodes:
                return False  # the conditional has not decided yet
            chosen = src_node.get('chosen_branch_node')
            if not chosen and 'data' in src_node:
                chosen = (src_node.get('data', {}) or {}).get('chosen_branch_node')
            if not chosen and src_node.get('last_conditional_result') is not None:
                branch = 'true' if src_node.get('last_conditional_result') else 'false'
                conns = src_node.get('connections', {})
                if isinstance(conns, dict):
                    b_conns = conns.get(branch, []) or []
                    if b_conns:
                        chosen = b_conns[0].get('target_node_id') or b_conns[0].get('node_id')
            if node_id == chosen:
                return False  # this conditional chose THIS node
            if self._can_reach(chosen, node_id, exclude_node=src_id):
                return False  # genuinely reachable from the chosen branch
            saw_gate = True
        return saw_gate

    def _is_direct_llm_concatenation(self, current_llm_id, next_llm_id):
        """
        Check if two LLM nodes are directly concatenated with single output/input
        and no branching.
        """
        if current_llm_id not in self.workflow_graph or next_llm_id not in self.workflow_graph:
            return False
        current_node = self.workflow_graph[current_llm_id]
        next_node = self.workflow_graph[next_llm_id]
        if current_node.get('type') != 'llm' or next_node.get('type') != 'llm':
            return False
        # Current LLM must have exactly one output connection to next LLM
        outputs = current_node.get('connections', {}).get('output', [])
        if len(outputs) != 1 or outputs[0].get('node_id') != next_llm_id:
            return False
        # Next LLM must have exactly one input and it must be from current
        inputs = next_node.get('inputs', [])
        if len(inputs) != 1 or inputs[0].get('from_node') != current_llm_id:
            return False
        return True

    def _can_reach(self, start_id, target_id, exclude_node=None):
        """BFS reachability: can start_id reach target_id via EXECUTION-only paths?

        Only follows execution output ports ('output', 'true', 'false',
        'error', 'route'). Data-only ports ('context', 'ctx_out', 'ctx_in', 'tools',
        'query') are skipped — they carry data, not execution flow.

        If exclude_node is provided, that node is skipped during traversal.
        This prevents loop-back paths through the same conditional from
        falsely indicating reachability.

        Returns True if reachable, False otherwise or if start_id is None."""
        if not start_id or not target_id or start_id == target_id:
            return start_id == target_id if start_id else False
        if start_id not in self.workflow_graph:
            return False
        # Ports that carry execution flow (not data)
        EXECUTION_PORTS = {'output', 'true', 'false', 'error', 'route'}
        visited = {start_id}
        if exclude_node:
            visited.add(exclude_node)
        queue = [start_id]
        while queue:
            current = queue.pop(0)
            node = self.workflow_graph.get(current, {})
            conns = node.get('connections', {})
            if isinstance(conns, dict):
                for port_name, port_conns in conns.items():
                    if port_name not in EXECUTION_PORTS:
                        continue  # Skip data-only ports
                    for c in (port_conns if isinstance(port_conns, list) else []):
                        nid = c.get('target_node_id') or c.get('node_id')
                        if nid and nid not in visited:
                            if nid == target_id:
                                return True
                            visited.add(nid)
                            queue.append(nid)
        return False
