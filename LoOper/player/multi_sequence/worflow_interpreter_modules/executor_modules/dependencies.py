from ....config import logger
import time

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
                # Skip chain import nodes that act as tool providers
                if node_data.get('type') == 'chain_import' and node_data.get('tool_provider') and not node_data.get('selected_by_llm'):
                    logger.debug(f"Skipping tool-provider chain import from starting nodes: {node_id}")
                    continue
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
                    if port not in ('tools', 'context'):
                        all_nonblocking = False
                        break
                    src = self.workflow_graph.get(inp.get('from_node'), {})
                    if port == 'tools' and (src.get('type') != 'chain_import' or not src.get('tool_provider')):
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
                        if output_type not in ('output', 'true', 'false') and (not (completed_node.get('type') == 'code' and output_type == (completed_node.get('last_output_port') or 'output'))):
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
                    if str(inp.get('input_port')) in ('tools', 'context'):
                        continue
                    if tnode.get('type') != 'llm' and tid not in completed_nodes:
                        return True
            return False
        except Exception:
            return False
        
    def _get_output_connected_nodes(self, node_id):
        """
        Get all nodes that are connected to the outputs of the given node.
        
        Args:
            node_id (str): The node ID to get output connections for
            
        Returns:
            list: List of node IDs that are connected to this node's outputs
        """
        connected_nodes = []
        
        if node_id not in self.workflow_graph:
            return connected_nodes
            
        node = self.workflow_graph[node_id]
        connections = node.get('connections', {})
        
        # For most nodes, check 'output' connections
        # For conditional nodes, this method shouldn't be called (they use branch logic)
        if 'output' in connections and isinstance(connections.get('output'), list):
            for connection in connections.get('output'):
                target_node_id = connection.get('node_id')
                if target_node_id:
                    connected_nodes.append(target_node_id)
            return connected_nodes
        for output_type, output_connections in connections.items():
            for connection in output_connections:
                target_node_id = connection.get('node_id')
                if target_node_id:
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
            
        if node_id in self.skipped_nodes:
            logger.debug(f"Node {node_id} is in skipped_nodes, not ready")
            return False

        node = self.workflow_graph[node_id]
        inputs = node.get('inputs', [])
        logger.debug(f"Node {node_id} inputs: {inputs}")
        
        if not inputs:
            # Tool-provider chain imports must be explicitly selected by an LLM before ready
            if node.get('type') == 'chain_import' and node.get('tool_provider') and not node.get('selected_by_llm'):
                logger.debug(f"Node {node_id} is a tool-provider and not selected; not ready")
                return False
            logger.debug(f"Node {node_id} has no inputs, marking as ready")
            return True

        # Special logic: If a node has multiple inputs (e.g. it's a merge point after a branch),
        # it is ready if ANY of its un-skipped conditional branch inputs have completed
        # AND all of its non-conditional required inputs have completed.
        
        has_conditional_input = False
        conditional_satisfied = False
        
        for input_connection in inputs:
            from_node_id = input_connection['from_node']
            if from_node_id in self.skipped_nodes:
                continue
                
            from_node = self.workflow_graph.get(from_node_id, {})
            if from_node.get('type') == 'conditional':
                has_conditional_input = True
                if from_node_id in completed_nodes:
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
                                    
                    if chosen == node_id:
                        conditional_satisfied = True
        
        # If it has conditional inputs, and none of them are satisfied yet, it's not ready
        if has_conditional_input and not conditional_satisfied:
            return False

        # Filter out non-blocking inputs (tools, context, query) and inputs from skipped/conditional nodes
        filtered_inputs = []
        for _inp in inputs:
            ip = str(_inp.get('input_port'))
            if ip in ('tools', 'context', 'query'):
                continue
            if _inp['from_node'] in self.skipped_nodes:
                continue
            if self.workflow_graph.get(_inp['from_node'], {}).get('type') == 'conditional':
                continue
            filtered_inputs.append(_inp)

        if not filtered_inputs:
            return True

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

        # ALL inputs must be either completed or skipped
        for input_connection in filtered_inputs:
            src_id = input_connection.get('from_node')
            if src_id not in completed_nodes and src_id not in self.skipped_nodes:
                logger.debug(f"Dependency {src_id} not satisfied (neither completed nor skipped) for node {node_id}")
                return False
        
        # Ensure at least one input was actually completed (not all paths skipped)
        has_at_least_one_completed = False
        for input_connection in filtered_inputs:
            if input_connection.get('from_node') in completed_nodes:
                has_at_least_one_completed = True
                break
        
        if not has_at_least_one_completed:
            logger.debug(f"All dependencies for {node_id} were skipped, node itself should be skipped")
            # We don't mark it as skipped here, it should be handled by _mark_skipped
            return False

        logger.debug(f"All dependencies satisfied for node {node_id}")
        return True

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
