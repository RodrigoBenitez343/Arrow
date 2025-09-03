# workflow_graph.py
"""
Workflow graph builder and navigator for multi-sequence automation.
Handles building workflow graphs from sequences and conditional nodes for navigation.
"""

from ..config import logger


class WorkflowGraphBuilder:
    """Builds and manages workflow graphs from sequences and conditional nodes"""
    
    def __init__(self, sequences=None, conditional_nodes=None, llm_nodes=None, tts_nodes=None):
        self.sequences = sequences or []
        self.conditional_nodes = conditional_nodes or []
        self.llm_nodes = llm_nodes or []
        self.tts_nodes = tts_nodes or []
    
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
            graph[node_id] = {
                'type': 'llm',
                'data': llm_node,
                'connections': {
                    'output': llm_node.get('outputs', {}).get('output', [])
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
        
        # Build connections from LLM outputs to other nodes
        for llm_node in self.llm_nodes:
            llm_id = llm_node.get('id') or llm_node.get('node_id')
            outputs = llm_node.get('outputs', {})
            
            for output_name, connections in outputs.items():
                for connection in connections:
                    target_node_id = connection['node_id']
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