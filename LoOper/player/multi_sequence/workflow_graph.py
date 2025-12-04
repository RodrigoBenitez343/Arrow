# workflow_graph.py
"""
Workflow graph builder and navigator for multi-sequence automation.
Handles building workflow graphs from sequences and conditional nodes for navigation.
"""

from ..config import logger
import time
from ..json_cache import get_chain


class WorkflowGraphBuilder:
    """Builds and manages workflow graphs from sequences and conditional nodes"""
    
    def __init__(self, sequences=None, conditional_nodes=None, llm_nodes=None, tts_nodes=None, chain_import_nodes=None):
        self.sequences = sequences or []
        self.conditional_nodes = conditional_nodes or []
        self.llm_nodes = llm_nodes or []
        self.tts_nodes = tts_nodes or []
        self.chain_import_nodes = chain_import_nodes or []
    
    def build_workflow_graph(self):
        """
        Builds a workflow graph from sequences and conditional nodes for navigation.
        
        Returns:
            dict: A graph structure mapping node IDs to their connections
        """
        graph = {}
        
        # Add sequence nodes to graph
        for i, sequence in enumerate(self.sequences):
            # Handle both old and new sequence formats
            if isinstance(sequence, dict) and ('id' in sequence or 'node_id' in sequence):
                # New format with ID and connections - support both 'id' and 'node_id'
                node_id = sequence.get('id') or sequence.get('node_id')
                # Handle both 'outputs' and 'connections' formats
                if 'outputs' in sequence:
                    output_connections = sequence.get('outputs', {}).get('output', [])
                elif 'connections' in sequence:
                    # Convert connections format to outputs format
                    output_connections = [{
                        'node_id': conn['target_node_id']
                    } for conn in sequence.get('connections', [])]
                else:
                    output_connections = []
            else:
                # Old format - just sequence data
                node_id = f"sequence_{i}"
                output_connections = []
            
            graph[node_id] = {
                'type': 'sequence',
                'data': sequence,
                'index': i,
                'connections': {'output': output_connections}
            }
        
        # Add conditional nodes to graph
        for conditional in self.conditional_nodes:
            # Support both 'id' and 'node_id' fields
            node_id = conditional.get('id') or conditional.get('node_id')
            
            # Handle both 'outputs' and 'connections' formats for conditionals
            if 'outputs' in conditional:
                true_connections = conditional.get('outputs', {}).get('true', [])
                false_connections = conditional.get('outputs', {}).get('false', [])
            elif 'connections' in conditional:
                # Convert connections format to outputs format
                true_connections = []
                false_connections = []
                for conn in conditional.get('connections', []):
                    if conn.get('output_port') == 'true':
                        true_connections.append({'node_id': conn['target_node_id']})
                    elif conn.get('output_port') == 'false':
                        false_connections.append({'node_id': conn['target_node_id']})
            else:
                true_connections = []
                false_connections = []
                
            graph[node_id] = {
                'type': 'conditional',
                'data': conditional,
                'connections': {
                    'true': true_connections,
                    'false': false_connections
                }
            }
        
        # Add LLM nodes to graph
        for llm_node in self.llm_nodes:
            node_id = llm_node.get('id') or llm_node.get('node_id')
            # Support both 'outputs' and legacy 'connections' formats
            # Normalize to connections['output'] = [{ 'node_id': ... }]
            output_connections = []
            if isinstance(llm_node.get('outputs'), dict):
                output_connections = llm_node.get('outputs', {}).get('output', []) or []
            elif isinstance(llm_node.get('connections'), list):
                output_connections = [{ 'node_id': conn.get('target_node_id') } for conn in llm_node.get('connections', []) if conn.get('target_node_id')]
            graph[node_id] = {
                'type': 'llm',
                'data': llm_node,
                'connections': {
                    'output': output_connections
                }
            }
        
        # Add TTS nodes to graph
        for tts_node in self.tts_nodes:
            node_id = tts_node.get('id') or tts_node.get('node_id')
            # Convert connections format to outputs format for TTS nodes
            output_connections = [{
                'node_id': conn['target_node_id']
            } for conn in tts_node.get('connections', [])]
            
            graph[node_id] = {
                'type': 'tts',
                'data': tts_node,
                'connections': {
                    'output': output_connections
                }
            }
        
        # Add chain import nodes to graph
        for chain_import_node in self.chain_import_nodes:
            node_id = chain_import_node.get('id') or chain_import_node.get('node_id')
            # Convert connections format to outputs format for chain import nodes
            output_connections = [{
                'node_id': conn['target_node_id']
            } for conn in chain_import_node.get('connections', [])]
            
            graph[node_id] = {
                'type': 'chain_import',
                'data': chain_import_node,
                'connections': {
                    'output': output_connections
                }
            }
        
        # Build connections from conditional outputs to other nodes
        for conditional in self.conditional_nodes:
            conditional_id = conditional.get('id') or conditional.get('node_id')
            
            # Get connections from the graph node we just created
            if conditional_id in graph:
                conditional_graph_node = graph[conditional_id]
                connections = conditional_graph_node['connections']
                
                for output_type in ['true', 'false']:
                    if output_type in connections:
                        for connection in connections[output_type]:
                            target_node_id = connection['node_id']
                            if target_node_id in graph:
                                if 'inputs' not in graph[target_node_id]:
                                    graph[target_node_id]['inputs'] = []
                                graph[target_node_id]['inputs'].append({
                                    'from_node': conditional_id,
                                    'output_type': output_type
                                })
        
        # Build connections from LLM outputs to other nodes (normalize to inputs for dependency tracking)
        for llm_node in self.llm_nodes:
            llm_id = llm_node.get('id') or llm_node.get('node_id')
            if llm_id in graph:
                llm_graph_node = graph[llm_id]
                connections = llm_graph_node.get('connections', {})
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection.get('node_id')
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': llm_id,
                                'output_type': output_name
                            })
        
        # Build connections from TTS node outputs to other nodes
        for tts_node in self.tts_nodes:
            tts_id = tts_node.get('id') or tts_node.get('node_id')
            
            # Get connections from the graph node we just created (which converted connections to outputs format)
            if tts_id in graph:
                tts_graph_node = graph[tts_id]
                connections = tts_graph_node['connections']
                
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': tts_id,
                                'output_type': output_name
                            })
        
        # Build connections from chain import node outputs to other nodes
        for chain_import_node in self.chain_import_nodes:
            chain_import_id = chain_import_node.get('id') or chain_import_node.get('node_id')
            
            # Get connections from the graph node we just created
            if chain_import_id in graph:
                chain_import_graph_node = graph[chain_import_id]
                connections = chain_import_graph_node['connections']
                
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': chain_import_id,
                                'output_type': output_name
                            })

        # Build connections from sequence node outputs to other nodes
        for i, sequence in enumerate(self.sequences):
            if isinstance(sequence, dict) and ('outputs' in sequence or 'connections' in sequence):
                # New format with explicit connections
                sequence_id = sequence.get('id') or sequence.get('node_id') or f'sequence_{i}'
                
                # Get connections from the graph node we just created
                if sequence_id in graph:
                    sequence_graph_node = graph[sequence_id]
                    connections = sequence_graph_node['connections']
                    
                    for output_name, output_connections in connections.items():
                        for connection in output_connections:
                            target_node_id = connection['node_id']
                            if target_node_id in graph:
                                if 'inputs' not in graph[target_node_id]:
                                    graph[target_node_id]['inputs'] = []
                                graph[target_node_id]['inputs'].append({
                                     'from_node': sequence_id,
                                     'output_type': output_name
                                 })
        
        # Process input connections for all node types
        # Handle sequence node inputs
        for i, sequence in enumerate(self.sequences):
            if isinstance(sequence, dict) and 'inputs' in sequence:
                sequence_id = sequence.get('id') or sequence.get('node_id') or f'sequence_{i}'
                inputs = sequence.get('inputs', {})
                for input_name, connection_info in inputs.items():
                    source_node_id = connection_info['node_id']
                    if source_node_id in graph:
                        if 'inputs' not in graph[sequence_id]:
                            graph[sequence_id]['inputs'] = []
                        graph[sequence_id]['inputs'].append({
                            'from_node': source_node_id,
                            'output_type': connection_info['output_name']
                        })
        
        # Handle LLM node inputs
        for llm_node in self.llm_nodes:
            llm_id = llm_node.get('id') or llm_node.get('node_id')
            inputs = llm_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = connection_info['node_id']
                if source_node_id in graph:
                    if 'inputs' not in graph[llm_id]:
                        graph[llm_id]['inputs'] = []
                    graph[llm_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle conditional node inputs
        for conditional in self.conditional_nodes:
            conditional_id = conditional.get('id') or conditional.get('node_id')
            inputs = conditional.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = connection_info['node_id']
                if source_node_id in graph:
                    if 'inputs' not in graph[conditional_id]:
                        graph[conditional_id]['inputs'] = []
                    graph[conditional_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle TTS node inputs
        for tts_node in self.tts_nodes:
            tts_id = tts_node.get('id') or tts_node.get('node_id')
            inputs = tts_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = connection_info['node_id']
                if source_node_id in graph:
                    if 'inputs' not in graph[tts_id]:
                        graph[tts_id]['inputs'] = []
                    graph[tts_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle chain import node inputs
        for chain_import_node in self.chain_import_nodes:
            chain_import_id = chain_import_node.get('id') or chain_import_node.get('node_id')
            inputs = chain_import_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = connection_info['node_id']
                if source_node_id in graph:
                    if 'inputs' not in graph[chain_import_id]:
                        graph[chain_import_id]['inputs'] = []
                    graph[chain_import_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        logger.info(f"Built workflow graph with {len(graph)} nodes")
        return graph


class WorkflowNavigator:
    """Navigates through workflow graphs and finds nodes"""
    
    def __init__(self, workflow_graph):
        self.workflow_graph = workflow_graph
    
    def find_starting_node(self):
        """
        Finds the starting node of the workflow (node with no inputs).
        
        Returns:
            str: Node ID of the starting node, or None if not found
        """
        if not self.workflow_graph:
            return None
        
        logger.debug(f"Looking for starting node in graph with {len(self.workflow_graph)} nodes")
        
        # Debug: Log all nodes and their inputs
        for node_id, node_data in self.workflow_graph.items():
            inputs = node_data.get('inputs', [])
            logger.debug(f"Node {node_id} (type: {node_data['type']}) has {len(inputs)} inputs: {inputs}")
        
        # Look for nodes that have no inputs (starting nodes)
        for node_id, node_data in self.workflow_graph.items():
            if 'inputs' not in node_data or not node_data['inputs']:
                logger.info(f"Found starting node: {node_id} (type: {node_data['type']})")
                return node_id
        
        # If no node without inputs found, default to first sequence if available
        for node_id, node_data in self.workflow_graph.items():
            if node_data['type'] == 'sequence':
                logger.info("No clear starting node found, defaulting to first sequence")
                return node_id
        
        # If no sequences, try first LLM node
        for node_id, node_data in self.workflow_graph.items():
            if node_data['type'] == 'llm':
                logger.info(f"No sequences found, defaulting to first LLM node: {node_id}")
                return node_id
        
        logger.warning("No suitable starting node found")
        return None
    
    def find_next_sequence_node(self, current_node):
        """
        Finds the next node to execute after the current sequence node.
        Looks for connected nodes (LLM, conditional, or other sequences).
        
        Args:
            current_node (dict): The current sequence node
            
        Returns:
            str: Next node ID, or None if no next node
        """
        # Get the current node ID from the workflow graph
        current_node_id = None
        for node_id, node_data in self.workflow_graph.items():
            if (node_data['type'] == 'sequence' and 
                node_data['index'] == current_node['index']):
                current_node_id = node_id
                break
        
        if not current_node_id:
            logger.error(f"Could not find current sequence node in workflow graph")
            return None
        
        # Check if this sequence has explicit output connections
        current_graph_node = self.workflow_graph.get(current_node_id, {})
        output_connections = current_graph_node.get('connections', {}).get('output', [])
        
        if output_connections:
            # Use the first output connection
            next_connection = output_connections[0]
            next_node_id = next_connection['node_id']
            logger.info(f"Found explicit connection from {current_node_id} to {next_node_id}")
            return next_node_id
        
        # Look for nodes that have this sequence as an input (fallback)
        for node_id, node_data in self.workflow_graph.items():
            if 'inputs' in node_data:
                for input_connection in node_data['inputs']:
                    if input_connection['from_node'] == current_node_id:
                        logger.info(f"Found connected node {node_id} (type: {node_data['type']}) after sequence {current_node_id}")
                        return node_id
        
        # No explicit connections found, follow linear sequence order
        current_index = current_node['index']
        next_index = current_index + 1
        
        # Find sequence with next index
        for node_id, node_data in self.workflow_graph.items():
            if (node_data['type'] == 'sequence' and 
                node_data.get('index') == next_index):
                logger.info(f"No connections found, proceeding to next sequence: {node_id}")
                return node_id
        
        logger.info("No more sequences and no connections found, ending workflow")
        return None
    
    def get_node_connections(self, node_id, output_type='output'):
        """
        Gets the connections for a specific node and output type.
        
        Args:
            node_id (str): The node ID
            output_type (str): The output type ('output', 'true', 'false')
            
        Returns:
            list: List of connection dictionaries
        """
        if node_id not in self.workflow_graph:
            return []
        
        node = self.workflow_graph[node_id]
        connections = node.get('connections', {})
        return connections.get(output_type, [])
    
    def get_node_by_id(self, node_id):
        """
        Gets a node by its ID.
        
        Args:
            node_id (str): The node ID
            
        Returns:
            dict: The node data, or None if not found
        """
        return self.workflow_graph.get(node_id)
    
    def get_nodes_by_type(self, node_type):
        """
        Gets all nodes of a specific type.
        
        Args:
            node_type (str): The node type ('sequence', 'conditional', 'llm')
            
        Returns:
            list: List of (node_id, node_data) tuples
        """
        return [(node_id, node_data) for node_id, node_data in self.workflow_graph.items() 
                if node_data.get('type') == node_type]


class WorkflowExecutor:
    """Executes workflow graphs with proper dependency resolution and node execution"""
    
    def __init__(self, workflow_graph, sequence_executor, llm_executor, fallback_handler):
        self.workflow_graph = workflow_graph
        self.sequence_executor = sequence_executor
        self.llm_executor = llm_executor
        self.fallback_handler = fallback_handler
        self.skipped_nodes = set()
        # Shared selection memory reference (to be provided by MultiSequencePlayer)
        self.selection_memory = None
    
    def execute_workflow(self, stop_flag=None, action_handlers=None):
        """
        Executes the workflow graph with proper dependency resolution.
        Nodes with multiple inputs will only execute after all their dependencies are satisfied.
        
        Args:
            stop_flag (callable): Optional function that returns True when execution should stop
            action_handlers: Action handlers for text writing and other actions
            
        Returns:
            bool: True if workflow completed successfully, False otherwise
        """
        if not self.workflow_graph:
            logger.warning("No workflow graph to execute")
            return False
            
        # Track completed nodes and nodes ready for execution
        completed_nodes = set()
        ready_nodes = set()
        
        # Find all starting nodes (nodes with no inputs)
        starting_nodes = self._find_all_starting_nodes()
        if not starting_nodes:
            logger.warning("No starting nodes found in workflow graph")
            return False
        
        logger.info(f"Starting workflow execution from nodes: {starting_nodes}")
        logger.debug(f"Workflow graph has {len(self.workflow_graph)} nodes: {list(self.workflow_graph.keys())}")
        ready_nodes.update(starting_nodes)
        
        while ready_nodes:
            # Check stop flag
            if stop_flag and stop_flag():
                logger.info("Workflow execution stopped by user request")
                return True
            
            current_node_id = self._select_next_node(ready_nodes)
            
            if current_node_id in completed_nodes:
                continue  # Skip already completed nodes
            
            if current_node_id not in self.workflow_graph:
                logger.warning(f"Node {current_node_id} not found in workflow graph")
                continue
            
            node = self.workflow_graph[current_node_id]
            logger.info(f"Executing node {current_node_id} (type: {node['type']})")
            
            # Execute the node based on its type
            success = False
            conditional_result = None
            
            if node['type'] == 'sequence':
                success = self._execute_sequence_node(node, stop_flag) is not None
            elif node['type'] == 'conditional':
                # For conditional nodes, evaluate the condition and store the result
                conditional_data = node['data']
                condition_type = conditional_data.get('condition_type', 'presence')
                if condition_type == 'loop':
                    conditional_result = self._execute_conditional_loop(conditional_data, stop_flag)
                else:
                    conditional_result = self._evaluate_conditional(conditional_data, stop_flag)
                success = True  # Conditional evaluation always succeeds
                logger.info(f"Conditional evaluation result: {conditional_result}")
                # Store the result in the node for later reference
                node['last_conditional_result'] = conditional_result
            elif node['type'] == 'llm':
                success = self._execute_llm_node(node, stop_flag, action_handlers) is not None
            elif node['type'] == 'tts':
                success = self._execute_tts_node(node, stop_flag) is not None
            elif node['type'] == 'chain_import':
                success = self._execute_chain_import_node(node, stop_flag) is not None
            else:
                logger.warning(f"Unknown node type: {node['type']}")
                success = False
            
            if success:
                # Mark node as completed
                completed_nodes.add(current_node_id)
                logger.info(f"Node {current_node_id} completed successfully")
                self._apply_extra_delay(node, stop_flag)
                
                # Handle conditional branching
                if node['type'] == 'conditional':
                    # For conditional nodes, determine which branch to follow
                    branch_node = self._get_conditional_branch_node(node, conditional_result)
                    if branch_node:
                        # Mark the chosen branch in the conditional node
                        node['chosen_branch_node'] = branch_node
                        # Add the chosen branch node to ready nodes if dependencies are satisfied
                        if self._are_all_dependencies_satisfied(branch_node, completed_nodes):
                            ready_nodes.add(branch_node)
                            logger.info(f"Conditional branch chosen: {branch_node}")
                        
                        # Mark nodes in the unchosen branch as skipped
                        unchosen_branch = 'false' if conditional_result else 'true'
                        self._mark_skipped(current_node_id, unchosen_branch)
                elif node['type'] == 'llm':
                    # LLM-specific chaining rules: only auto-chain to an LLM if directly concatenated
                    # and neither node has multiple outputs/inputs. Otherwise, let sequences drive order.
                    next_nodes = self._get_output_connected_nodes(current_node_id)
                    logger.debug(f"Output connected nodes from {current_node_id}: {next_nodes}")
                    has_pending_non_llm_outputs = False
                    for nn in next_nodes:
                        tn = self.workflow_graph.get(nn, {})
                        if tn.get('type') != 'llm' and nn not in completed_nodes:
                            has_pending_non_llm_outputs = True
                            break
                    for next_node_id in next_nodes:
                        if next_node_id in completed_nodes:
                            continue
                        # If next node is an LLM, enforce direct concatenation rule
                        next_node = self.workflow_graph.get(next_node_id, {})
                        if next_node.get('type') == 'llm':
                            if has_pending_non_llm_outputs:
                                logger.info(f"Deferring LLM {next_node_id} because non-LLM outputs remain after {current_node_id}")
                                continue
                            if self._is_direct_llm_concatenation(current_node_id, next_node_id):
                                if self._are_all_dependencies_satisfied(next_node_id, completed_nodes):
                                    ready_nodes.add(next_node_id)
                                    logger.info(f"Chained LLM {next_node_id} is now ready (direct concatenation)")
                            else:
                                logger.info(f"Skipping auto-chain to LLM {next_node_id} due to branching or multiple inputs/outputs")
                        else:
                            # Non-LLM nodes proceed normally when deps satisfied
                            if self._are_all_dependencies_satisfied(next_node_id, completed_nodes):
                                ready_nodes.add(next_node_id)
                                logger.info(f"Node {next_node_id} is now ready (output connection from {current_node_id})")
                    # Also check nodes that become ready due to satisfied dependencies
                    newly_ready = self._find_newly_ready_nodes(current_node_id, completed_nodes)
                    ready_nodes.update(newly_ready)
                    if newly_ready:
                        logger.info(f"Additional nodes now ready for execution: {newly_ready}")
                    logger.debug(f"Ready nodes after processing {current_node_id}: {ready_nodes}")
                else:
                    # Standard handling for non-conditional, non-LLM nodes
                    next_nodes = self._get_output_connected_nodes(current_node_id)
                    logger.debug(f"Output connected nodes from {current_node_id}: {next_nodes}")
                    for next_node_id in next_nodes:
                        if next_node_id not in completed_nodes:
                            if self._are_all_dependencies_satisfied(next_node_id, completed_nodes):
                                ready_nodes.add(next_node_id)
                                logger.info(f"Node {next_node_id} is now ready (output connection from {current_node_id})")
                    newly_ready = self._find_newly_ready_nodes(current_node_id, completed_nodes)
                    ready_nodes.update(newly_ready)
                    if newly_ready:
                        logger.info(f"Additional nodes now ready for execution: {newly_ready}")
                    logger.debug(f"Ready nodes after processing {current_node_id}: {ready_nodes}")
            else:
                logger.error(f"Node {current_node_id} execution failed, stopping workflow")
                return False
        
        logger.info("Workflow execution completed successfully")
        return True
    
    def _find_all_starting_nodes(self):
        """
        Find all starting nodes for workflow execution (nodes with no inputs).
        
        Returns:
            list: List of node IDs that have no input dependencies
        """
        starting_nodes = []
        
        logger.debug(f"Looking for starting nodes in graph with {len(self.workflow_graph)} nodes")
        
        # Debug: Log all nodes and their inputs
        for node_id, node_data in self.workflow_graph.items():
            inputs = node_data.get('inputs', [])
            logger.debug(f"Node {node_id} (type: {node_data['type']}) has {len(inputs)} inputs: {inputs}")
            if node_data['type'] == 'conditional':
                logger.debug(f"  Conditional node {node_id} connections: {node_data.get('connections', {})}")
            elif node_data['type'] == 'sequence':
                logger.debug(f"  Sequence node {node_id} connections: {node_data.get('connections', {})}")
        
        for node_id, node_data in self.workflow_graph.items():
            if 'inputs' not in node_data or not node_data['inputs']:
                starting_nodes.append(node_id)
                logger.debug(f"Found starting node: {node_id} (type: {node_data['type']})")
        
        return starting_nodes

    def _select_next_node(self, ready_nodes):
        priority_map = {
            'sequence': 0,
            'conditional': 1,
            'tts': 2,
            'chain_import': 3,
            'llm': 4,
        }
        candidates = list(ready_nodes)
        def _key(nid):
            nd = self.workflow_graph.get(nid, {})
            t = nd.get('type')
            p = priority_map.get(t, 5)
            idx = nd.get('index', 0)
            return (p, idx, str(nid))
        candidates.sort(key=_key)
        chosen = candidates[0]
        try:
            ready_nodes.remove(chosen)
        except Exception:
            pass
        return chosen
    
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
            has_pending_non_llm_outputs = False
            for output_type, output_connections in connections.items():
                for connection in output_connections:
                    tid = connection.get('node_id')
                    if not tid:
                        continue
                    tnode = self.workflow_graph.get(tid, {})
                    if tnode.get('type') != 'llm' and tid not in completed_nodes:
                        has_pending_non_llm_outputs = True
                        break
                if has_pending_non_llm_outputs:
                    break
            for output_type, output_connections in connections.items():
                for connection in output_connections:
                    target_node_id = connection.get('node_id')
                    if not target_node_id or target_node_id in completed_nodes:
                        continue
                    target_node = self.workflow_graph.get(target_node_id, {})
                    if completed_is_llm and has_pending_non_llm_outputs and target_node.get('type') == 'llm':
                        continue
                    if self._are_all_dependencies_satisfied(target_node_id, completed_nodes):
                        if target_node_id not in newly_ready:
                            newly_ready.append(target_node_id)
                            logger.debug(f"Node {target_node_id} is now ready (output connection from {completed_node_id})")
        
        # Then check all nodes to see if any are now ready based on input dependencies
        for node_id, node_data in self.workflow_graph.items():
            if node_id in completed_nodes or node_id in newly_ready:
                continue  # Skip already completed or already identified nodes
            
            node_inputs = node_data.get('inputs', [])
            if not node_inputs:
                continue
            
            # Skip nodes that are handled by conditional branching logic
            # Only skip if ALL inputs are from conditional nodes that haven't chosen this path
            should_skip_conditional_handling = False
            conditional_inputs = []
            for input_connection in node_inputs:
                input_node_id = input_connection['from_node']
                if input_node_id in self.workflow_graph:
                    input_node = self.workflow_graph[input_node_id]
                    if input_node.get('type') == 'conditional':
                        conditional_inputs.append(input_node_id)
            
            # If this node has conditional inputs, check if any of them have chosen this node
            if conditional_inputs:
                has_chosen_path = False
                for conditional_id in conditional_inputs:
                    conditional_node = self.workflow_graph[conditional_id]
                    chosen_branch = conditional_node.get('chosen_branch_node')
                    if chosen_branch == node_id:
                        has_chosen_path = True
                        break
                
                # If no conditional has chosen this path yet, skip it
                if not has_chosen_path:
                    should_skip_conditional_handling = True
            
            if should_skip_conditional_handling:
                continue  # Skip nodes that haven't been chosen by conditional branching
            
            all_inputs_satisfied = True
            for input_connection in node_inputs:
                input_node_id = input_connection['from_node']
                if input_node_id not in completed_nodes:
                    all_inputs_satisfied = False
                    break
            if not all_inputs_satisfied:
                continue
            if node_data.get('type') == 'llm' and self._llm_inputs_have_pending_non_llm_outputs(node_id, completed_nodes):
                logger.info(f"Deferring LLM {node_id} due to pending non-LLM outputs from its LLM inputs")
                continue
            newly_ready.append(node_id)
            logger.debug(f"Node {node_id} is now ready (all inputs satisfied)")
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
            
        node = self.workflow_graph[node_id]
        inputs = node.get('inputs', [])
        logger.debug(f"Node {node_id} inputs: {inputs}")
        
        if not inputs:
            logger.debug(f"Node {node_id} has no inputs, marking as ready")
            return True

        node_type = self.workflow_graph.get(node_id, {}).get('type')
        if node_type == 'conditional':
            for input_connection in inputs:
                input_node_id = input_connection.get('from_node')
                if input_node_id in completed_nodes:
                    logger.debug(f"Node {node_id} dependency {input_node_id} satisfied - node is ready")
                    return True
            logger.debug(f"No dependencies satisfied for conditional node {node_id}")
            return False
        else:
            # If node has conditional inputs, only require chosen conditional branch
            conditional_required = []
            non_conditional_required = []
            for input_connection in inputs:
                input_node_id = input_connection.get('from_node')
                input_node = self.workflow_graph.get(input_node_id, {})
                if input_node.get('type') == 'conditional':
                    chosen = input_node.get('chosen_branch_node')
                    if chosen == node_id:
                        conditional_required.append(input_node_id)
                else:
                    non_conditional_required.append(input_node_id)

            if conditional_required:
                # Require that at least one chosen conditional input is completed; ignore non-conditional inputs
                for cid in conditional_required:
                    if cid in completed_nodes:
                        logger.debug(f"Node {node_id} chosen conditional {cid} satisfied - node is ready")
                        return True
                logger.debug(f"No chosen conditional inputs satisfied for node {node_id}")
                return False

            # No conditional inputs: require all inputs completed
            for input_connection in inputs:
                input_node_id = input_connection.get('from_node')
                if input_node_id not in completed_nodes:
                    logger.debug(f"Dependency {input_node_id} not satisfied for node {node_id}")
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

    def _apply_extra_delay(self, node, stop_flag):
        try:
            data = node.get('data', {})
            delay = float(data.get('extra_delay', 0) or 0)
        except Exception:
            delay = 0
        if delay > 0:
            logger.info(f"Extra delay after node: {delay} seconds")
            elapsed = 0.0
            while elapsed < delay:
                if stop_flag and stop_flag():
                    logger.info("Workflow execution stopped during extra delay")
                    return
                sleep_time = min(0.1, delay - elapsed)
                time.sleep(sleep_time)
                elapsed += sleep_time
    
    def _execute_sequence_node(self, node, stop_flag):
        """
        Executes a sequence node and returns the next node ID.
        
        Args:
            node (dict): The sequence node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Use the SequenceExecutor to handle sequence execution
        next_node_id = self.sequence_executor.execute_sequence_node(node, stop_flag)
        return next_node_id
    
    def _execute_llm_node(self, node, stop_flag, action_handlers=None):
        """
        Executes an LLM node and returns the next node ID.
        
        Args:
            node (dict): The LLM node from the workflow graph
            stop_flag (callable): Stop flag function
            action_handlers: Action handlers for text writing
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Use the LLMExecutor to handle LLM execution and return its decision on next node
        next_node_id = self.llm_executor.execute_llm_node(node, stop_flag, action_handlers)
        return next_node_id
    
    def _execute_tts_node(self, node, stop_flag):
        """
        Executes a TTS node and returns the next node ID.
        
        Args:
            node (dict): The TTS node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        tts_data = node['data']
        logger.info(f"=== STARTING TTS NODE EXECUTION ===")
        node_identifier = tts_data.get('id') or tts_data.get('node_id', 'unknown')
        logger.info(f"Executing TTS node: {tts_data.get('name', node_identifier)}")
        logger.info(f"Full node data: {node}")
        
        try:
            # Execute TTS using the LLM executor's TTS functionality
            result = self.llm_executor.execute_tts_standalone(tts_data, self.llm_executor.get_all_variables())
            
            if result.get('success', False):
                logger.info(f"TTS node execution successful: {result.get('message', 'No message')}")
                
                # Find next node in the workflow
                connections = node.get('connections', {})
                if 'output' in connections and connections['output']:
                    next_connection = connections['output'][0]
                    return next_connection['node_id']
                else:
                    logger.info("No output connections found for TTS node, ending workflow")
                    return None
            else:
                error_msg = result.get('error', 'Unknown TTS error')
                logger.error(f"TTS node execution failed: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"TTS node execution failed with exception: {str(e)}")
            return None
    
    def _execute_chain_import_node(self, node, stop_flag):
        """
        Executes a chain import node with loop count support using direct chain execution.
        
        Args:
            node (dict): The chain import node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        import os
        import time
        from ..multi_sequence_player import MultiSequencePlayer
        
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
            if base_dir:
                if chain_file_path:
                    candidates.append(os.path.join(base_dir, chain_file_path))
                    if not chain_file_path.lower().endswith('.json'):
                        candidates.append(os.path.join(base_dir, chain_file_path + '.json'))
            # Also try current working directory as a fallback
            if chain_file_path:
                candidates.append(os.path.join(os.getcwd(), chain_file_path))
                if not chain_file_path.lower().endswith('.json'):
                    candidates.append(os.path.join(os.getcwd(), chain_file_path + '.json'))
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
            # Continue to next node even if chain file is missing
            connections = node.get('connections', {})
            if 'output' in connections and connections['output']:
                next_connection = connections['output'][0]
                return next_connection['node_id']
            return None
            
        if not os.path.exists(resolved_chain_path):
            logger.error(f"Chain import file not found: {resolved_chain_path}")
            # Continue to next node even if chain file is missing
            connections = node.get('connections', {})
            if 'output' in connections and connections['output']:
                next_connection = connections['output'][0]
                return next_connection['node_id']
            return None
        
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
                
                # Load and execute the imported chain directly using MultiSequencePlayer
                logger.info(f"Loading imported chain: {resolved_chain_path}")
                
                # Try to get chain config from cache first for optimal performance
                chain_config = get_chain(resolved_chain_path)
                if chain_config:
                    logger.info(f"Using cached chain config for: {resolved_chain_path}")
                else:
                    # Fallback to loading from disk if not in cache
                    logger.info(f"Loading chain config from disk (not in cache): {resolved_chain_path}")
                    import json
                    with open(resolved_chain_path, 'r', encoding='utf-8') as f:
                        chain_config = json.load(f)
                
                # Check stop flag after loading chain config
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected after loading chain config - aborting")
                    return None
                
                # Pass the resolved path so nested imports and relative assets are correctly located
                imported_player = MultiSequencePlayer(chain_config, chain_file_path=resolved_chain_path)
                # Share selection memory with imported chain player
                try:
                    # Ensure the imported player uses the shared selection memory
                    imported_player.selection_memory = self.selection_memory if self.selection_memory is not None else {}
                    # Propagate the shared memory to all modular components created during imported_player init
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
                success = imported_player.play_chain(stop_flag)
                
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
        
        return None
    
    def _evaluate_conditional(self, conditional_node, stop_flag):
        """
        Evaluate a conditional node to determine which branch to take.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        # Extract condition configuration
        raw_trigger_type = conditional_node.get('trigger_type', 'presence_trigger')
        condition_type = raw_trigger_type.lower()
        
        # Build condition configuration from node data
        condition_config = {
            'image_path': conditional_node.get('image_path'),
            'confidence': conditional_node.get('confidence', 0.8),
            'timeout': conditional_node.get('timeout', 10),
            'target_text': conditional_node.get('target_text'),
            'region': conditional_node.get('region'),
            'strict_validation': conditional_node.get('strict_validation', False)
        }
        
        # Also check if there's a nested condition_data (for backward compatibility)
        if 'condition_data' in conditional_node:
            nested_config = conditional_node['condition_data']
            condition_config.update(nested_config)
        
        logger.info(f"Evaluating conditional with raw trigger type: {raw_trigger_type} -> mapped to: {condition_type}")
        logger.debug(f"Condition config: {condition_config}")
        
        # Set stop flag for the fallback handler
        self.fallback_handler.stop_flag = stop_flag
        
        # Capture a single screenshot for visual trigger evaluations to improve performance
        screen_cv = None
        if condition_type in ['presence_trigger', 'absence_trigger']:
            screen_cv = self.fallback_handler.template_matcher.capture_screen()
        
        if condition_type == 'presence_trigger':
            return self.fallback_handler.check_presence_trigger(condition_config, screen_cv=screen_cv)
        elif condition_type == 'absence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.fallback_handler.check_absence_trigger(condition_config, timeout, screen_cv=screen_cv)
        elif condition_type == 'ocr_trigger':
            return self.fallback_handler.check_ocr_trigger(condition_config, stop_flag=stop_flag)
        else:
            logger.warning(f"Unknown condition type: {condition_type} (raw: {raw_trigger_type})")
            return False
    
    def _execute_conditional_loop(self, conditional_data, stop_flag):
        """
        Execute a conditional loop by reconstructing the loop configuration and calling the fallback handler.
        
        Args:
            conditional_data (dict): The conditional node data containing loop configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if loop completed successfully, False otherwise
        """
        try:
            import json
            from ..sequence_player import SequencePlayer
            
            # Reconstruct loop configuration from conditional node data
            loop_type = conditional_data.get('loop_type', 'while_present')
            max_iterations = int(conditional_data.get('max_loops', 10))
            sequence_file = conditional_data.get('sequence_file', '')
            iteration_delay = float(conditional_data.get('iteration_delay', 0.05))  # Reduced from 0.3
            
            # Parse the stored loop condition
            loop_condition_json = conditional_data.get('loop_condition', '{}')
            try:
                condition = json.loads(loop_condition_json) if loop_condition_json else {}
            except (json.JSONDecodeError, TypeError):
                # Fallback to basic image condition if JSON parsing fails
                condition = {
                    'trigger_type': 'presence',
                    'image_path': conditional_data.get('image_path', ''),
                    'confidence': float(conditional_data.get('threshold', 0.8))
                }
            
            # Build loop configuration
            loop_config = {
                'type': loop_type,
                'max_iterations': max_iterations,
                'sequence_file': sequence_file,
                'iteration_delay': iteration_delay,
                'condition': condition
            }
            
            logger.info(f"Executing conditional loop: {loop_type}, sequence: {sequence_file}, max_iterations: {max_iterations}")
            
            # Set stop flag for the fallback handler
            self.fallback_handler.stop_flag = stop_flag
            
            # Execute the conditional loop using the fallback handler
            # Create a temporary SequencePlayer for loop execution with the sequence file
            sequence_file = loop_config.get('sequence_file', '')
            if not sequence_file:
                logger.error(f"No sequence file specified for conditional loop")
                return False
            
            temp_sequence_player = SequencePlayer(sequence_file)
            # Share selection memory with temporary sequence player
            try:
                temp_sequence_player.selection_memory = self.selection_memory if self.selection_memory is not None else {}
            except Exception:
                pass
            return self.fallback_handler.execute_conditional_loop(loop_config, temp_sequence_player)
            
        except Exception as e:
            logger.error(f"Error executing conditional loop: {e}")
            return False
    
    def _get_conditional_branch_node(self, node, result):
        """
        Get the next node ID based on conditional result.
        
        Args:
            node (dict): The conditional node
            result (bool): The conditional evaluation result
            
        Returns:
            str: Next node ID, or None if no connection found
        """
        # Determine which branch to follow based on result
        branch = 'true' if result else 'false'
        
        # Get connections for this branch
        connections = node.get('connections', {})
        branch_connections = connections.get(branch, [])
        
        target_node = None
        if branch_connections:
            # Take the first connection in the branch
            target_node = branch_connections[0].get('node_id')
        
        if target_node:
            return target_node
        else:
            logger.info(f"No connection for '{branch}' branch, ending workflow")
            return None
    
    def _mark_skipped(self, conditional_id, unchosen_port):
        """
        Mark nodes in the unchosen branch as skipped.
        
        Args:
            conditional_id (str): The conditional node ID
            unchosen_port (str): The unchosen branch ('true' or 'false')
        """
        import json
        from collections import deque
        
        skipped = set()
        conditional_data = self.workflow_graph[conditional_id]
        connections = conditional_data.get('connections', {})
        if isinstance(connections, str):
            connections = json.loads(connections)
        
        # Handle the connections structure - it's organized by branch (true/false)
        unchosen_starts = []
        if isinstance(connections, dict) and unchosen_port in connections:
            unchosen_starts = [conn['node_id'] for conn in connections[unchosen_port]]
        
        queue = deque(unchosen_starts)
        visited = set()
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            current_data = self.workflow_graph.get(current, {})
            inputs = current_data.get('inputs', [])
            all_inputs_skippable = True
            for inp in inputs:
                from_id = inp['from_node']
                from_port = inp.get('output_type', inp.get('output_port'))
                if from_id == conditional_id and from_port == unchosen_port:
                    continue
                elif from_id in skipped:
                    continue
                else:
                    all_inputs_skippable = False
                    break
            if all_inputs_skippable:
                skipped.add(current)
                # Add connected nodes to queue
                current_connections = current_data.get('connections', {})
                for output_type, output_list in current_connections.items():
                    for conn in output_list:
                        queue.append(conn['node_id'])
        
        self.skipped_nodes.update(skipped)
        logger.info(f"Marked {len(skipped)} nodes as skipped from unchosen branch: {skipped}")
