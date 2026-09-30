import logging
logger = logging.getLogger(__name__)

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
            if node_data['type'] in ('sequence', 'web_sequence'):
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
            if (node_data['type'] in ('sequence', 'web_sequence') and 
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
            if (node_data['type'] in ('sequence', 'web_sequence') and 
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
