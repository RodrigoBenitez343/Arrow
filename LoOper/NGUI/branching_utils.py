# branching_utils.py
"""
Utilities for automated branching in workflow graphs.
Handles duplication of node chains and connection management for conditional branching.
"""

import copy
import uuid
from typing import Dict, List, Any, Tuple


class AutoBranchingManager:
    """Manages automated branching for workflow graphs"""
    
    def __init__(self):
        self.node_id_mapping = {}  # Maps original node IDs to new node IDs
        
    def generate_unique_id(self, prefix="node"):
        """Generate a unique node ID"""
        return f"{prefix}_{uuid.uuid4().hex[:8]}"
    
    def duplicate_node_chain(self, chain_config: Dict, start_node_id: str, 
                           branch_suffix: str = "_branch") -> Dict:
        """
        Duplicates a chain of nodes starting from a given node.
        
        Args:
            chain_config: The complete chain configuration
            start_node_id: ID of the node to start duplication from
            branch_suffix: Suffix to add to duplicated node names
            
        Returns:
            Dict: Updated chain configuration with duplicated nodes
        """
        new_config = copy.deepcopy(chain_config)
        
        # Find all nodes that come after the start_node_id
        nodes_to_duplicate = self._find_downstream_nodes(chain_config, start_node_id)
        
        # Duplicate sequences
        if 'sequences' in new_config:
            new_sequences = []
            for seq in new_config['sequences']:
                if seq['id'] in nodes_to_duplicate:
                    duplicated_seq = self._duplicate_sequence_node(seq, branch_suffix)
                    new_sequences.append(duplicated_seq)
            new_config['sequences'].extend(new_sequences)
        
        # Duplicate conditional nodes
        if 'conditional_nodes' in new_config:
            new_conditionals = []
            for cond in new_config['conditional_nodes']:
                if cond['id'] in nodes_to_duplicate:
                    duplicated_cond = self._duplicate_conditional_node(cond, branch_suffix)
                    new_conditionals.append(duplicated_cond)
            new_config['conditional_nodes'].extend(new_conditionals)
        
        # Duplicate LLM nodes
        if 'llm_nodes' in new_config:
            new_llm_nodes = []
            for llm in new_config['llm_nodes']:
                if llm['id'] in nodes_to_duplicate:
                    duplicated_llm = self._duplicate_llm_node(llm, branch_suffix)
                    new_llm_nodes.append(duplicated_llm)
            new_config['llm_nodes'].extend(new_llm_nodes)
        
        # Update connections to point to duplicated nodes
        self._update_connections_for_duplicated_nodes(new_config)
        
        return new_config
    
    def _find_downstream_nodes(self, chain_config: Dict, start_node_id: str) -> List[str]:
        """
        Find all nodes that come after the given start node in the workflow.
        
        Args:
            chain_config: The chain configuration
            start_node_id: Starting node ID
            
        Returns:
            List[str]: List of node IDs that are downstream from start_node
        """
        visited = set()
        downstream_nodes = []
        
        def traverse(node_id):
            if node_id in visited:
                return
            visited.add(node_id)
            
            # Find the node in all node types
            node = self._find_node_by_id(chain_config, node_id)
            if not node:
                return
            
            # Add to downstream if not the start node
            if node_id != start_node_id:
                downstream_nodes.append(node_id)
            
            # Traverse connected nodes
            outputs = node.get('outputs', {})
            for output_name, connections in outputs.items():
                for connection in connections:
                    traverse(connection['node_id'])
        
        # Start traversal from the start node
        traverse(start_node_id)
        return downstream_nodes
    
    def _find_node_by_id(self, chain_config: Dict, node_id: str) -> Dict:
        """Find a node by its ID across all node types"""
        # Check sequences
        for seq in chain_config.get('sequences', []):
            if seq['id'] == node_id:
                return seq
        
        # Check conditional nodes
        for cond in chain_config.get('conditional_nodes', []):
            if cond['id'] == node_id:
                return cond
        
        # Check LLM nodes
        for llm in chain_config.get('llm_nodes', []):
            if llm['id'] == node_id:
                return llm
        
        return None
    
    def _duplicate_sequence_node(self, sequence: Dict, suffix: str) -> Dict:
        """Duplicate a sequence node"""
        new_sequence = copy.deepcopy(sequence)
        old_id = sequence['id']
        new_id = self.generate_unique_id("seq")
        
        new_sequence['id'] = new_id
        new_sequence['position'][0] += 200  # Offset position
        
        # IMPORTANT: Do NOT modify the sequence name/file reference
        # The sequence file path must remain unchanged to avoid playback failures
        # Unlike other node types, sequence nodes should preserve their original name
        # to maintain correct file references (e.g., test.json should stay test.json, not test.json 2)
        
        # Store mapping for connection updates
        self.node_id_mapping[old_id] = new_id
        
        return new_sequence
    
    def _duplicate_conditional_node(self, conditional: Dict, suffix: str) -> Dict:
        """Duplicate a conditional node"""
        new_conditional = copy.deepcopy(conditional)
        old_id = conditional['id']
        new_id = self.generate_unique_id("cond")
        
        new_conditional['id'] = new_id
        new_conditional['name'] += suffix
        new_conditional['position'][0] += 200  # Offset position
        
        # Store mapping for connection updates
        self.node_id_mapping[old_id] = new_id
        
        return new_conditional
    
    def _duplicate_llm_node(self, llm_node: Dict, suffix: str) -> Dict:
        """Duplicate an LLM node"""
        new_llm = copy.deepcopy(llm_node)
        old_id = llm_node['id']
        new_id = self.generate_unique_id("llm")
        
        new_llm['id'] = new_id
        new_llm['name'] += suffix
        new_llm['position'][0] += 200  # Offset position
        
        # Store mapping for connection updates
        self.node_id_mapping[old_id] = new_id
        
        return new_llm
    
    def _update_connections_for_duplicated_nodes(self, chain_config: Dict):
        """Update connections to point to duplicated nodes where appropriate"""
        # Update sequence connections
        for seq in chain_config.get('sequences', []):
            self._update_node_connections(seq)
        
        # Update conditional node connections
        for cond in chain_config.get('conditional_nodes', []):
            self._update_node_connections(cond)
        
        # Update LLM node connections
        for llm in chain_config.get('llm_nodes', []):
            self._update_node_connections(llm)
    
    def _update_node_connections(self, node: Dict):
        """Update a single node's connections based on the mapping"""
        # Update input connections
        if 'inputs' in node:
            for input_name, connection in node['inputs'].items():
                if connection['node_id'] in self.node_id_mapping:
                    connection['node_id'] = self.node_id_mapping[connection['node_id']]
        
        # Update output connections
        if 'outputs' in node:
            for output_name, connections in node['outputs'].items():
                for connection in connections:
                    if connection['node_id'] in self.node_id_mapping:
                        connection['node_id'] = self.node_id_mapping[connection['node_id']]
    
    def create_automatic_branches(self, chain_config: Dict, conditional_node_id: str) -> Dict:
        """
        Automatically create branches for a conditional node by duplicating
        all downstream nodes for both true and false paths.
        
        Args:
            chain_config: The complete chain configuration
            conditional_node_id: ID of the conditional node to branch from
            
        Returns:
            Dict: Updated chain configuration with automatic branches
        """
        # Find the conditional node
        conditional_node = self._find_node_by_id(chain_config, conditional_node_id)
        if not conditional_node:
            raise ValueError(f"Conditional node {conditional_node_id} not found")
        
        # Get the current outputs
        true_outputs = conditional_node.get('outputs', {}).get('true', [])
        false_outputs = conditional_node.get('outputs', {}).get('false', [])
        
        # If both true and false already have connections, create branches
        if true_outputs and false_outputs:
            # Create a branch for the false path
            self.node_id_mapping = {}  # Reset mapping
            
            # Find the first node in the false path to start duplication
            if false_outputs:
                start_node_id = false_outputs[0]['node_id']
                branched_config = self.duplicate_node_chain(
                    chain_config, start_node_id, "_false_branch"
                )
                
                # Update the conditional node's false output to point to the new branch
                for cond in branched_config.get('conditional_nodes', []):
                    if cond['id'] == conditional_node_id:
                        if start_node_id in self.node_id_mapping:
                            new_start_id = self.node_id_mapping[start_node_id]
                            cond['outputs']['false'] = [{
                                'node_id': new_start_id,
                                'input_name': 'input'
                            }]
                        break
                
                return branched_config
        
        return chain_config
    
    def auto_branch_after_conditional(self, chain_config: Dict) -> Dict:
        """
        Automatically create branches for all conditional nodes that need them.
        
        Args:
            chain_config: The complete chain configuration
            
        Returns:
            Dict: Updated chain configuration with automatic branches
        """
        updated_config = copy.deepcopy(chain_config)
        
        # Process each conditional node
        for conditional in updated_config.get('conditional_nodes', []):
            conditional_id = conditional['id']
            
            # Check if this conditional needs automatic branching
            true_outputs = conditional.get('outputs', {}).get('true', [])
            false_outputs = conditional.get('outputs', {}).get('false', [])
            
            # If only one path has connections, create a branch for the other
            if true_outputs and not false_outputs:
                # Create branch for false path
                updated_config = self.create_automatic_branches(updated_config, conditional_id)
            elif false_outputs and not true_outputs:
                # Create branch for true path
                updated_config = self.create_automatic_branches(updated_config, conditional_id)
        
        return updated_config
