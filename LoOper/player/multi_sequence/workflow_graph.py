# workflow_graph.py
"""
Workflow graph builder and navigator for multi-sequence automation.
Handles building workflow graphs from sequences and conditional nodes for navigation.
"""

from ..config import logger
import time
import copy
from ..json_cache import get_chain
import logging
import os
import sys
import venv
import subprocess as _sp

# Setup chain run logger
chain_logger = logging.getLogger('chain_run')
chain_logger.setLevel(logging.INFO)
chain_logger.propagate = False 
if not chain_logger.handlers:
    # Save run.log in the root directory (parent of LoOper folder usually, or CWD)
    # Using CWD as per user env
    log_path = os.path.join(os.getcwd(), 'run.log')
    try:
        # Force encoding to utf-8 and ensure file creation
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"Log initialized at {time.ctime()}\n")
            
        file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        chain_logger.addHandler(file_handler)
    except Exception as e:
        logger.error(f"Failed to setup chain_run logger: {e}")



class WorkflowGraphBuilder:
    """Builds and manages workflow graphs from sequences and conditional nodes"""
    
    def __init__(self, sequences=None, conditional_nodes=None, llm_nodes=None, tts_nodes=None, chain_import_nodes=None, code_nodes=None):
        self.sequences = sequences or []
        self.conditional_nodes = conditional_nodes or []
        self.llm_nodes = llm_nodes or []
        self.tts_nodes = tts_nodes or []
        self.chain_import_nodes = chain_import_nodes or []
        self.code_nodes = code_nodes or []
    
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
                        'node_id': conn['target_node_id'],
                        'input_port': conn.get('input_port')
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
                        true_connections.append({'node_id': conn['target_node_id'], 'input_port': conn.get('input_port')})
                    elif conn.get('output_port') == 'false':
                        false_connections.append({'node_id': conn['target_node_id'], 'input_port': conn.get('input_port')})
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
            connections_by_port = {}
            if isinstance(llm_node.get('outputs'), dict):
                for port_name, conns in (llm_node.get('outputs') or {}).items():
                    try:
                        connections_by_port[port_name] = list(conns or [])
                    except Exception:
                        connections_by_port[port_name] = []
            elif isinstance(llm_node.get('connections'), list):
                for conn in llm_node.get('connections', []):
                    port = conn.get('output_port') or 'output'
                    lst = connections_by_port.setdefault(port, [])
                    lst.append({'node_id': conn.get('target_node_id'), 'input_port': conn.get('input_port')})
            else:
                connections_by_port = {'output': []}
            graph[node_id] = {
                'type': 'llm',
                'data': llm_node,
                'connections': connections_by_port
            }
        
        # Add TTS nodes to graph
        for tts_node in self.tts_nodes:
            node_id = tts_node.get('id') or tts_node.get('node_id')
            # Convert connections format to outputs format for TTS nodes
            output_connections = [{
                'node_id': conn['target_node_id'],
                'input_port': conn.get('input_port')
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
                'node_id': conn['target_node_id'],
                'input_port': conn.get('input_port')
            } for conn in chain_import_node.get('connections', [])]
            # Detect if this chain import acts purely as a tool provider to an LLM
            tool_provider = False
            try:
                if output_connections:
                    all_tools = True
                    for conn in output_connections:
                        tid = conn.get('node_id')
                        if not tid:
                            all_tools = False
                            break
                        target = graph.get(tid, {})
                        if target.get('type') != 'llm' or str(conn.get('input_port')) != 'tools':
                            all_tools = False
                            break
                    tool_provider = all_tools
            except Exception:
                tool_provider = False
            graph[node_id] = {
                'type': 'chain_import',
                'data': chain_import_node,
                'connections': {
                    'output': output_connections
                },
                'tool_provider': tool_provider,
                'selected_by_llm': False
            }
            
        # Add Code nodes to graph
        for code_node in self.code_nodes:
            node_id = code_node.get('id') or code_node.get('node_id')
            # Convert connections format to outputs format for Code nodes
            output_connections = []
            error_connections = []
            
            for conn in code_node.get('connections', []):
                target = {
                    'node_id': conn['target_node_id'],
                    'input_port': conn.get('input_port')
                }
                if conn.get('output_port') == 'error':
                    error_connections.append(target)
                else:
                    output_connections.append(target)
            
            graph[node_id] = {
                'type': 'code',
                'data': code_node,
                'connections': {
                    'output': output_connections,
                    'error': error_connections
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
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
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
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
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
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
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
                                    'output_type': output_name,
                                    'input_port': connection.get('input_port')
                                })
        
        # Build connections from code node outputs to other nodes
        for code_node in self.code_nodes:
            code_id = code_node.get('id') or code_node.get('node_id')
            
            # Get connections from the graph node we just created
            if code_id in graph:
                code_graph_node = graph[code_id]
                connections = code_graph_node['connections']
                
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': code_id,
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
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
        
        # Handle code node inputs
        for code_node in self.code_nodes:
            code_id = code_node.get('id') or code_node.get('node_id')
            inputs = code_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = connection_info['node_id']
                if source_node_id in graph:
                    if 'inputs' not in graph[code_id]:
                        graph[code_id]['inputs'] = []
                    graph[code_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Enrich LLM nodes with tool_descriptions derived from connected tool providers
        try:
            import os as _os
            for _nid, _ndata in graph.items():
                if _ndata.get('type') != 'llm':
                    continue
                _inputs = _ndata.get('inputs', []) or []
                _tool_map = {}
                for _inp in _inputs:
                    try:
                        if str(_inp.get('input_port')) != 'tools':
                            continue
                        _src_id = _inp.get('from_node')
                        _src = graph.get(_src_id, {})
                        _sdata = _src.get('data', {}) if isinstance(_src, dict) else {}
                        _label = ''
                        try:
                            _label = _sdata.get('name') or ''
                        except Exception:
                            _label = ''
                        if not _label:
                            try:
                                _cf = _sdata.get('chain_file_path') or _sdata.get('chain_file') or ''
                                if _cf:
                                    _label = _os.path.splitext(_os.path.basename(str(_cf)))[0]
                            except Exception:
                                _label = ''
                        if not _label:
                            try:
                                _label = f"{_src.get('type','tool').replace('_',' ').title()}"
                            except Exception:
                                _label = 'Tool'
                        if _src_id:
                            _tool_map[_src_id] = _label
                    except Exception:
                        continue
                if _tool_map:
                    _ld = _ndata.get('data') if isinstance(_ndata, dict) else None
                    if isinstance(_ld, dict):
                        # Merge with existing tool_descriptions wherever they are stored
                        _cfg = _ld.get('llm_configuration') if isinstance(_ld.get('llm_configuration'), dict) else None
                        if _cfg is not None:
                            _existing = _cfg.get('tool_descriptions') if isinstance(_cfg.get('tool_descriptions'), dict) else {}
                            try:
                                _existing.update(_tool_map)
                            except Exception:
                                _existing = _tool_map
                            _cfg['tool_descriptions'] = _existing
                        else:
                            _existing = _ld.get('tool_descriptions') if isinstance(_ld.get('tool_descriptions'), dict) else {}
                            try:
                                _existing.update(_tool_map)
                            except Exception:
                                _existing = _tool_map
                            _ld['tool_descriptions'] = _existing
        except Exception:
            pass
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
        self._runtime_root = None
        self._chain_label = None
        self._chain_runtime_dir = None
        self._venv_dir = None
        self._venv_python = None
        self._venv_site_packages = None
        self._code_retry_counts = {}
    
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
        self._initialize_chain_runtime()
            
        # Track completed nodes and nodes ready for execution
        completed_nodes = set()
        ready_nodes = set()
        
        # Find all starting nodes (nodes with no inputs)
        starting_nodes = self._find_all_starting_nodes()
        if not starting_nodes:
            logger.warning("No starting nodes found in workflow graph")
            return False
        
        logger.info(f"Starting workflow execution from nodes: {starting_nodes}")
        chain_logger.info(f"=== CHAIN RUN STARTED ===")
        chain_logger.info(f"Starting nodes: {starting_nodes}")
        logger.debug(f"Workflow graph has {len(self.workflow_graph)} nodes: {list(self.workflow_graph.keys())}")
        ready_nodes.update(starting_nodes)
        last_completed_node_id = None
        
        while True:
            # Check stop flag
            if stop_flag and stop_flag():
                logger.info("Workflow execution stopped by user request")
                return True
            # Defer all execution while any LLM node is actively typing
            try:
                vars_snapshot = self.llm_executor.get_all_variables()
            except Exception:
                vars_snapshot = {}
            typing_active = any(k.endswith('_writing') and vars_snapshot.get(k) for k in vars_snapshot.keys())
            if typing_active:
                try:
                    time.sleep(0.05)
                except Exception:
                    pass
                # Refresh readiness so nodes queue up but do not execute until typing ends
                for node_id, node_data in self.workflow_graph.items():
                    if node_id in completed_nodes:
                        continue
                    if self._are_all_dependencies_satisfied(node_id, completed_nodes):
                        ready_nodes.add(node_id)
                continue
            if not ready_nodes:
                try:
                    vars_snapshot = self.llm_executor.get_all_variables()
                except Exception:
                    vars_snapshot = {}
                typing_active = any(k.endswith('_writing') and vars_snapshot.get(k) for k in vars_snapshot.keys())
                if typing_active:
                    try:
                        time.sleep(0.05)
                    except Exception:
                        pass
                    for node_id, node_data in self.workflow_graph.items():
                        if node_id in completed_nodes:
                            continue
                        if self._are_all_dependencies_satisfied(node_id, completed_nodes):
                            ready_nodes.add(node_id)
                    if not ready_nodes:
                        continue
                else:
                    for node_id, node_data in self.workflow_graph.items():
                        if node_id in completed_nodes:
                            continue
                        if self._are_all_dependencies_satisfied(node_id, completed_nodes):
                            ready_nodes.add(node_id)
                    if not ready_nodes:
                        logger.info("Workflow execution completed successfully")
                        return True
            
            current_node_id = self._select_next_node(ready_nodes)
            
            if current_node_id in completed_nodes:
                continue  # Skip already completed nodes
            
            if current_node_id in self.skipped_nodes:
                logger.info(f"Skipping node {current_node_id} (in skipped_nodes)")
                continue

            if current_node_id not in self.workflow_graph:
                logger.warning(f"Node {current_node_id} not found in workflow graph")
                continue
            
            node = self.workflow_graph[current_node_id]
            logger.info(f"Executing node {current_node_id} (type: {node['type']})")
            chain_logger.info(f"EXECUTING NODE: {current_node_id} (Type: {node['type']})")
            try:
                from rich.table import Table
                from rich.panel import Panel
                con = getattr(self.llm_executor, '_rich_console', None)
                if con:
                    inf = Table(title="Node Execution", show_header=True, header_style="bold blue")
                    inf.add_column("Field", style="bold")
                    inf.add_column("Value")
                    inf.add_row("Node ID", str(current_node_id))
                    inf.add_row("Type", str(node.get('type')))
                    ins = node.get('inputs', []) or []
                    inf.add_row("Inputs", str(len(ins)))
                    con.print(inf)
                    if ins:
                        tbl = Table(title="Inputs", show_header=True, header_style="bold cyan")
                        tbl.add_column("From Node", style="bold")
                        tbl.add_column("Port")
                        tbl.add_column("Output Type")
                        for i in ins:
                            tbl.add_row(str(i.get('from_node')), str(i.get('input_port')), str(i.get('output_type')))
                        con.print(tbl)
            except Exception:
                pass
            
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
            elif node['type'] == 'code':
                success = self._execute_code_node(node, stop_flag) is not None
            else:
                logger.warning(f"Unknown node type: {node['type']}")
                success = False
            
            if success:
                # Mark node as completed
                completed_nodes.add(current_node_id)
                last_completed_node_id = current_node_id
                logger.info(f"Node {current_node_id} completed successfully")
                chain_logger.info(f"Node {current_node_id} COMPLETED successfully")
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
                    # If LLM selected a specific tool node, prioritize scheduling it
                    try:
                        sel = getattr(self, '_llm_selected_next', None)
                        self._llm_selected_next = None
                        if sel and sel in self.workflow_graph and sel not in completed_nodes:
                            if self._are_all_dependencies_satisfied(sel, completed_nodes):
                                ready_nodes.add(sel)
                                logger.info(f"Selected tool node {sel} is now ready for execution")
                                try:
                                    from rich.table import Table
                                    from rich.panel import Panel
                                    con = getattr(self.llm_executor, '_rich_console', None)
                                    if con:
                                        dmap = {}
                                        try:
                                            dmap = node.get('data', {}).get('tool_descriptions', {})
                                        except Exception:
                                            dmap = {}
                                        t = Table(title="Scheduled Tool", show_header=True, header_style="bold magenta")
                                        t.add_column("ID", style="bold")
                                        t.add_column("Description")
                                        t.add_row(str(sel), str(dmap.get(sel, ''))) 
                                        con.print(Panel(t, title="Tool Scheduling", border_style="magenta"))
                                except Exception:
                                    pass
                    except Exception:
                        pass
                else:
                    port = None
                    try:
                        if node.get('type') == 'code':
                            port = node.get('last_output_port') or 'output'
                    except Exception:
                        port = None
                    connections = node.get('connections', {})
                    next_nodes = []
                    try:
                        if port and isinstance(connections, dict):
                            for c in (connections.get(port) or []):
                                tid = c.get('node_id')
                                if tid:
                                    next_nodes.append(tid)
                        else:
                            next_nodes = self._get_output_connected_nodes(current_node_id)
                    except Exception:
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
                chain_logger.error(f"Node {current_node_id} FAILED execution. Stopping workflow.")
                return False
        
        logger.info("Workflow execution completed successfully")
        chain_logger.info("=== CHAIN RUN COMPLETED SUCCESSFULLY ===")
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
                # Skip chain import nodes that act as tool providers
                if node_data.get('type') == 'chain_import' and node_data.get('tool_provider') and not node_data.get('selected_by_llm'):
                    logger.debug(f"Skipping tool-provider chain import from starting nodes: {node_id}")
                    continue
                starting_nodes.append(node_id)
                logger.debug(f"Found starting node: {node_id} (type: {node_data['type']})")

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

    def _select_next_node(self, ready_nodes):
        priority_map = {
            'code': 0,
            'sequence': 1,
            'conditional': 2,
            'tts': 3,
            'chain_import': 4,
            'llm': 5,
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
                        if output_type != 'output' and (not (completed_node.get('type') == 'code' and output_type == (completed_node.get('last_output_port') or 'output'))):
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
            
            # Use comprehensive dependency check to include upstream-typing deferral and other rules
            if not self._are_all_dependencies_satisfied(node_id, completed_nodes):
                continue
            newly_ready.append(node_id)
            logger.debug(f"Node {node_id} is now ready (dependencies satisfied)")
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
            filtered_inputs = []
            for _inp in inputs:
                ip = str(_inp.get('input_port'))
                if ip in ('tools', 'context'):
                    continue
                filtered_inputs.append(_inp)
            conditional_required = []
            non_conditional_required = []
            has_conditional_inputs = False
            for input_connection in filtered_inputs:
                input_node_id = input_connection.get('from_node')
                input_node = self.workflow_graph.get(input_node_id, {})
                if input_node.get('type') == 'conditional':
                    has_conditional_inputs = True
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
            elif has_conditional_inputs:
                # If we have conditional inputs but none chose this node, we are not ready (likely skipped)
                logger.debug(f"Node {node_id} has conditional inputs but was not chosen by any of them")
                return False

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
                node_type = node.get('type')
                if node_type == 'llm' and self._llm_inputs_have_pending_non_llm_outputs(node_id, completed_nodes):
                    logger.info(f"Deferring LLM {node_id}: pending non-LLM outputs from its LLM inputs")
                    return False
            except Exception:
                pass

            # No conditional inputs: require all inputs completed
            for input_connection in filtered_inputs:
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
        # Check for LLM -> TTS connection with "write automatically" enabled
        # If found, disable typing and redirect output to TTS node
        llm_data = node.get('data', {})
        llm_config = llm_data.get('llm_configuration', {}) or llm_data
        write_text = llm_config.get('write_text', True)
        
        node_to_execute = node
        
        # Check for LLM -> TTS connection
        # Always redirect output to TTS if connected, regardless of write_text setting
        connections = node.get('connections', {}).get('output', [])
        for connection in connections:
            target_node_id = connection.get('node_id')
            if target_node_id and target_node_id in self.workflow_graph:
                target_node = self.workflow_graph[target_node_id]
                if target_node.get('type') == 'tts':
                    # Configure TTS node to use the LLM output variable
                    output_variable = llm_config.get('output_variable', 'llm_output')
                    
                    # Update the TTS node in the graph to use the variable
                    # This modifies the graph for the rest of this execution
                    target_node['data']['text'] = f"{{{output_variable}}}"
                    logger.info(f"Updated TTS node {target_node_id} text to use variable {{{output_variable}}}")
                    
                    # If write_text is enabled, disable it since we are redirecting to TTS
                    if write_text:
                        logger.info(f"LLM node {node.get('id', 'unknown')} connected to TTS node {target_node_id}. Disabling write_text.")
                        
                        # Create a copy of the node to modify write_text without affecting the graph permanently
                        node_to_execute = copy.deepcopy(node)
                        
                        # Disable write_text in the copy
                        node_data_copy = node_to_execute.get('data', {})
                        if 'llm_configuration' in node_data_copy:
                            node_data_copy['llm_configuration']['write_text'] = False
                        else:
                            node_data_copy['write_text'] = False
                    
                    # Only handle the first TTS connection found
                    break

        # Use the LLMExecutor to handle LLM execution and return its decision on next node
        next_node_id = self.llm_executor.execute_llm_node(node_to_execute, stop_flag, action_handlers)
        try:
            self._llm_selected_next = None
            chosen_id = next_node_id if isinstance(next_node_id, str) else None
            # Fallback: parse tool marker or scan for tool ids/descriptions
            try:
                if (not chosen_id) or (chosen_id not in self.workflow_graph):
                    out_var = None
                    llm_id = node.get('id') or node.get('data', {}).get('node_id') or node.get('data', {}).get('id')
                    if llm_id:
                        out_var = self.llm_executor.get_variable(f"node_{llm_id}_output") or ""
                    s = str(out_var or "")
                    if s:
                        for mk in ("USE_TOOL:", "CALL_TOOL:", "TOOL:"):
                            idx = s.find(mk)
                            if idx >= 0:
                                val = s[idx+len(mk):].strip()
                                val = val.split()[0].strip()
                                while val and val[-1] in (":", ",", ";", ".", ")", "]", "}"):
                                    val = val[:-1]
                                if val:
                                    chosen_id = val
                                    break
                    if not chosen_id:
                        tools = []
                        for inp in (node.get('inputs', []) or []):
                            if str(inp.get('input_port')) == 'tools':
                                tools.append(str(inp.get('from_node')))
                        for tid in tools:
                            if tid and (tid in s):
                                chosen_id = tid
                                break
                        if not chosen_id:
                            try:
                                dmap = node.get('data', {}).get('tool_descriptions', {})
                            except Exception:
                                dmap = {}
                            sl = s.lower()
                            for tid, desc in (dmap.items() if isinstance(dmap, dict) else []):
                                d = str(desc or '').lower()
                                if d and d in sl:
                                    chosen_id = tid
                                    break
                                tail = str(desc or '').split(":")[-1].strip().lower()
                                if tail and tail in sl:
                                    chosen_id = tid
                                    break
                        # Semantic fallback based on previous/context values
                        if not chosen_id:
                            try:
                                prev_text = ""
                                # prefer context value
                                for inp in (node.get('inputs', []) or []):
                                    if str(inp.get('input_port')) == 'context':
                                        pid = str(inp.get('from_node'))
                                        v = self.llm_executor.get_variable(f"node_{pid}_context") or ""
                                        if v:
                                            prev_text = str(v)
                                            break
                                if not prev_text:
                                    for inp in (node.get('inputs', []) or []):
                                        if str(inp.get('input_port')) == 'input':
                                            pid = str(inp.get('from_node'))
                                            v = self.llm_executor.get_variable(f"node_{pid}_output") or ""
                                            if v:
                                                prev_text = str(v)
                                                break
                                pl = prev_text.lower()
                                want_open = False
                                want_close = False
                                for w in ("no browser", "not open", "closed", "no visible", "none open"):
                                    if w in pl:
                                        want_open = True
                                        break
                                if not want_open:
                                    for w in ("browser is open", "is open", "open window", "visible browser"):
                                        if w in pl:
                                            want_close = True
                                            break
                                if want_open:
                                    for tid, desc in (dmap.items() if isinstance(dmap, dict) else []):
                                        dl = str(desc or '').lower()
                                        if ("open_browser" in dl) or ("open browser" in dl) or dl.endswith("open_browser"):
                                            chosen_id = tid
                                            break
                                elif want_close:
                                    for tid, desc in (dmap.items() if isinstance(dmap, dict) else []):
                                        dl = str(desc or '').lower()
                                        if ("close_browser" in dl) or ("close browser" in dl) or dl.endswith("close_browser"):
                                            chosen_id = tid
                                            break
                            except Exception:
                                pass
            except Exception:
                pass
            if chosen_id and chosen_id in self.workflow_graph:
                target_node = self.workflow_graph.get(chosen_id, {})
                if target_node.get('type') == 'chain_import' and target_node.get('tool_provider'):
                    target_node['selected_by_llm'] = True
                self._llm_selected_next = chosen_id
                logger.info(f"LLM selected next node: {chosen_id}")
        except Exception:
            pass
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
                    return "__done__"
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
            return "__done__"
            
        if not os.path.exists(resolved_chain_path):
            logger.error(f"Chain import file not found: {resolved_chain_path}")
            # Continue to next node even if chain file is missing
            connections = node.get('connections', {})
            if 'output' in connections and connections['output']:
                next_connection = connections['output'][0]
                return next_connection['node_id']
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
                # Also pass shared variables to allow aggregation of apps
                imported_player = MultiSequencePlayer(
                    chain_config, 
                    chain_file_path=resolved_chain_path,
                    variables=self.llm_executor.variables
                )
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
    
    def _execute_code_node(self, node, stop_flag):
        """
        Executes a Code node and returns the next node ID.
        
        Args:
            node (dict): The Code node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        logger.info(f"Executing Code node: {node.get('id')}")
        code_data = node.get('data', {})
        
        # Get properties
        code = code_data.get('code', '')
        file_path = code_data.get('file_path', '')
        output_variable = code_data.get('output_variable', 'result')
        
        # Resolve inputs
        input_data = []
        args = {}
        runtime_code = None
        code_src_llm_id = None
        
        inputs = node.get('inputs', [])
        for inp in inputs:
            from_node_id = inp.get('from_node')
            input_port = str(inp.get('input_port'))
            output_type = str(inp.get('output_type') or inp.get('output_port') or 'output')
            
            # Get value from previous node respecting source output port
            if output_type == 'context':
                val = self.llm_executor.get_variable(f"node_{from_node_id}_context")
            else:
                val = self.llm_executor.get_variable(f"node_{from_node_id}_output")
            
            if input_port == 'input':
                input_data.append(val)
            elif input_port == 'args':
                if isinstance(val, dict):
                    args.update(val)
            elif input_port == 'code':
                if val:
                    runtime_code = str(val)
                    code_src_llm_id = from_node_id
                    
                    # Strip markdown code blocks if present (LLM output often contains these)
                    if "```" in runtime_code:
                        try:
                            import re
                            # Find ALL code blocks, not just the first one
                            # This handles cases where LLM splits code into multiple blocks (e.g. imports, then functions, then main)
                            matches = re.findall(r"```(?:python)?\s*(.*?)```", runtime_code, re.DOTALL | re.IGNORECASE)
                            if matches:
                                # Join all blocks with newlines
                                runtime_code = "\n\n".join(m.strip() for m in matches)
                                logger.info(f"Extracted {len(matches)} code blocks from LLM output")
                            else:
                                # Fallback: Just strip first and last line if they look like fences
                                # This handles cases where regex might fail or format is slightly off
                                lines = runtime_code.strip().split('\n')
                                if lines and lines[0].strip().startswith("```"):
                                    lines = lines[1:]
                                if lines and lines[-1].strip().startswith("```"):
                                    lines = lines[:-1]
                                runtime_code = '\n'.join(lines)
                        except Exception as e:
                            logger.warning(f"Failed to strip markdown code blocks: {e}")
                    # Extract alias from code comments if present
                    try:
                        import re as _re
                        alias = None
                        for line in (runtime_code.splitlines() if runtime_code else []):
                            m = _re.match(r"\s*#\s*ALIAS\s*:\s*(.+)", line, _re.IGNORECASE)
                            if m:
                                alias = m.group(1).strip()
                                break
                            m2 = _re.match(r"\s*ALIAS\s*:\s*(.+)", line, _re.IGNORECASE)
                            if m2:
                                alias = m2.group(1).strip()
                                break
                        if alias:
                            node_alias = alias
                            try:
                                node_id = node.get('id') or node.get('node_id')
                                self.llm_executor.set_variable(f"node_{node_id}_module_alias", node_alias)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    # If file_path is set, save the code to file
                    if file_path:
                        try:
                            # Resolve path again to be safe
                            save_path = resolved_path if 'resolved_path' in locals() and resolved_path else file_path
                
                            real_save_path = file_path
                            if hasattr(self.fallback_handler, 'path_resolver') and self.fallback_handler.path_resolver:
                                if hasattr(self.fallback_handler.path_resolver, 'resolve_code_path'):
                                    try:
                                        real_save_path = self.fallback_handler.path_resolver.resolve_code_path(file_path)
                                    except:
                                        pass

                            logger.info(f"Saving generated code to: {real_save_path}")
                            with open(real_save_path, 'w', encoding='utf-8') as f:
                                f.write(runtime_code)
                            
                        except Exception as e:
                            logger.error(f"Failed to save code to {file_path}: {e}")
                            # Continue with runtime execution even if save fails
        
        # Simplify input_data if single item
        if len(input_data) == 1:
            input_data = input_data[0]
        elif len(input_data) == 0:
            input_data = None

        # Determine code to run
        code_to_run = ""
        if runtime_code:
            code_to_run = runtime_code
        elif file_path:
            # Use path resolver if available
            resolved_path = None
            if hasattr(self.fallback_handler, 'path_resolver') and self.fallback_handler.path_resolver:
                # Use resolve_code_path if available, otherwise resolve_path
                if hasattr(self.fallback_handler.path_resolver, 'resolve_code_path'):
                    resolved_path = self.fallback_handler.path_resolver.resolve_code_path(file_path)
                else:
                    resolved_path = self.fallback_handler.path_resolver.resolve_path(file_path)
            
            # Fallback to direct check
            if not resolved_path and os.path.exists(file_path):
                resolved_path = file_path
                
            if resolved_path and os.path.exists(resolved_path):
                try:
                    logger.info(f"Reading code from file: {resolved_path}")
                    with open(resolved_path, 'r', encoding='utf-8') as f:
                        code_to_run = f.read()
                except Exception as e:
                    logger.error(f"Failed to read code file {resolved_path}: {e}")
                    self._set_code_node_error(node, str(e))
                    return self._get_code_next_node(node, 'error')
            else:
                 logger.warning(f"Code file not found: {file_path}")
                 # If code content is provided in node, use it as fallback?
                 # Current logic prefers file, but if file fails, should it use 'code'?
                 # The 'else' block below handles 'code' if runtime_code and file_path are NOT used.
                 # But here we are inside 'elif file_path'.
                 # Let's fallback to inline code if file not found but inline code exists
                 if code:
                     logger.info("Fallback to inline code")
                     code_to_run = code
                 else:
                     self._set_code_node_error(node, f"File not found: {file_path}")
                     return self._get_code_next_node(node, 'error')
        else:
            code_to_run = code

        if not code_to_run:
             logger.warning("No code to execute in Code node")
             return self._get_code_next_node(node, 'output')

        node_id = node.get('id') or node.get('node_id')
        try:
            base_dir = self._chain_runtime_dir or os.path.join(os.getcwd(), 'runtime')
            tmp_dir = os.path.join(base_dir, 'code_nodes', str(node_id))
            os.makedirs(tmp_dir, exist_ok=True)
            script_path = os.path.join(tmp_dir, 'script.py')
            with open(script_path, 'w', encoding='utf-8') as _f:
                _f.write(code_to_run)
            self.llm_executor.set_variable(f"node_{node_id}_code_path", script_path)
            alias_candidate = None
            desc_candidate = None
            try:
                if code_src_llm_id:
                    alias_candidate = self.llm_executor.get_variable(f"node_{code_src_llm_id}_artifact_alias")
                    desc_candidate = self.llm_executor.get_variable(f"node_{code_src_llm_id}_artifact_desc")
            except Exception:
                alias_candidate = None
                desc_candidate = None
            try:
                safe_alias = self._sanitize_alias(alias_candidate or f"module_{node_id}")
                if not safe_alias.lower().startswith("module_"):
                    safe_alias = f"module_{safe_alias}"
                self.llm_executor.set_variable(f"node_{node_id}_module_alias", safe_alias)
            except Exception:
                self.llm_executor.set_variable(f"node_{node_id}_module_alias", f"module_{node_id}")
            # Update a global index of available code modules
            try:
                idx = self.llm_executor.get_variable('code_modules') or []
                alias = self.llm_executor.get_variable(f"node_{node_id}_module_alias") or f"module_{node_id}"
                desc = desc_candidate or ""
                entry = {'id': str(node_id), 'alias': str(alias), 'path': str(script_path), 'desc': str(desc)}
                # de-duplicate by id
                if not any(e.get('id') == str(node_id) for e in (idx if isinstance(idx, list) else [])):
                    (idx if isinstance(idx, list) else []).append(entry)
                else:
                    # update existing
                    for e in (idx if isinstance(idx, list) else []):
                        if e.get('id') == str(node_id):
                            e.update(entry)
                self.llm_executor.set_variable('code_modules', idx)
                try:
                    # Update preferred framework for the chain based on this code
                    fw = self._detect_framework(code_to_run)
                    if fw:
                        prev = self.llm_executor.get_variable('chain_framework') or ''
                        if not prev:
                            self.llm_executor.set_variable('chain_framework', fw)
                        used = self.llm_executor.get_variable('chain_frameworks_used') or []
                        if isinstance(used, list):
                            if fw not in used:
                                used.append(fw)
                                self.llm_executor.set_variable('chain_frameworks_used', used)
                        else:
                            self.llm_executor.set_variable('chain_frameworks_used', [fw])
                except Exception:
                    pass
                try:
                    lines = []
                    for e in (idx if isinstance(idx, list) else []):
                        a = str(e.get('alias') or '')
                        d = str(e.get('desc') or '')
                        p = str(e.get('path') or '')
                        lines.append(f"- {a}: {d} [{p}]")
                    hdr = ""
                    try:
                        cf = self.llm_executor.get_variable('chain_framework') or ''
                        cu = self.llm_executor.get_variable('chain_frameworks_used') or []
                        if cf:
                            hdr += f"Preferred Framework: {cf}\n"
                        if cu:
                            if isinstance(cu, list) and cu:
                                hdr += "Frameworks seen: " + ", ".join([str(x) for x in cu]) + "\n"
                    except Exception:
                        pass
                    ctx = (hdr + "AVAILABLE MODULES:\n" + "\n".join(lines)).strip()
                    self.llm_executor.set_variable('chain_modules_context', ctx)
                except Exception:
                    pass
            except Exception:
                pass
        except Exception:
            pass

        try:
            self._ensure_chain_venv()
            if self._venv_site_packages and os.path.isdir(self._venv_site_packages):
                if self._venv_site_packages not in sys.path:
                    sys.path.insert(0, self._venv_site_packages)
            self._install_code_dependencies(code_to_run)
        except Exception:
            pass
        try:
            for inp in (inputs or []):
                src_id = inp.get('from_node')
                p = self.llm_executor.get_variable(f"node_{src_id}_code_path")
                if p:
                    d = os.path.dirname(str(p))
                    if d and os.path.isdir(d) and (d not in sys.path):
                        sys.path.insert(0, d)
        except Exception:
            pass

        # Execute (choose strategy: in-process or subprocess for persistent loops)
        import io
        import contextlib
        
        persistent = self._is_persistent_loop(code_to_run)
        timeout_s = 0
        try:
            timeout_s = int(code_data.get('timeout', 30) or 30)
        except Exception:
            timeout_s = 30

        if persistent:
            leave_running = False
            try:
                leave_running = bool(self._is_last_code_node(node))
            except Exception:
                leave_running = False
            ok, result, stdout_str, stderr_str, terminal_output, pid = self._run_persistent_script(script_path, timeout_s, os.path.dirname(script_path), leave_running=leave_running)
            if ok:
                node_id = node.get('id') or node.get('node_id')
                self.llm_executor.set_variable(f"node_{node_id}_output", result)
                self.llm_executor.set_variable(f"node_{node_id}_result", result)
                self.llm_executor.set_variable(f"node_{node_id}_stdout", stdout_str)
                self.llm_executor.set_variable(f"node_{node_id}_stderr", stderr_str)
                self.llm_executor.set_variable(f"node_{node_id}_terminal_output", terminal_output)
                try:
                    if pid:
                        self.llm_executor.set_variable(f"node_{node_id}_pid", pid)
                except Exception:
                    pass
                if leave_running:
                    logger.info("Persistent loop script started and left running.")
                else:
                    logger.info("Persistent loop script executed and closed successfully.")
                try:
                    node['last_output_port'] = 'output'
                except Exception:
                    pass
                try:
                    code_text = ""
                    if os.path.exists(script_path):
                        with open(script_path, 'r', encoding='utf-8') as _f:
                            code_text = _f.read()
                    ctx_success = ("TERMINAL OUTPUT:\n" + terminal_output + "\n\n" + "CODE FILE: " + str(script_path) + "\n```python\n" + code_text + "\n```")
                    self.llm_executor.set_variable(f"node_{node_id}_context", ctx_success)
                except Exception:
                    pass
                return self._get_code_next_node(node, 'output')
            # fallthrough to error handling below

        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        try:
            # Prepare scope
            local_scope = {
                'input_data': input_data,
                'args': args,
                'os': os,
                'print': print,
                '__name__': '__main__'
            }
            try:
                self._preload_chain_modules(local_scope)
            except Exception:
                pass
            
            # Execute code with capture
            # Use llm_executor.variables as globals to allow scripts to share state (aggregation)
            with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                exec(code_to_run, self.llm_executor.variables, local_scope)
            
            # Get captured output
            stdout_str = stdout_capture.getvalue()
            stderr_str = stderr_capture.getvalue()
            terminal_output = stdout_str + stderr_str
            
            if stdout_str:
                logger.info(f"Code node stdout:\n{stdout_str}")
            if stderr_str:
                logger.warning(f"Code node stderr:\n{stderr_str}")

            # Extract result
            result = local_scope.get(output_variable)
            if result is None and output_variable == 'result':
                 result = local_scope.get('output')
            
            # If result is still None, use terminal_output as the main output
            if result is None:
                result = terminal_output
            
            # Store result and terminal output
            node_id = node.get('id') or node.get('node_id')
            self.llm_executor.set_variable(f"node_{node_id}_output", result)
            self.llm_executor.set_variable(f"node_{node_id}_result", result)
            self.llm_executor.set_variable(f"node_{node_id}_stdout", stdout_str)
            self.llm_executor.set_variable(f"node_{node_id}_stderr", stderr_str)
            self.llm_executor.set_variable(f"node_{node_id}_terminal_output", terminal_output)
            
            logger.info(f"Code node executed successfully.")
            try:
                node['last_output_port'] = 'output'
            except Exception:
                pass
            # Provide success context payload for downstream LLMs connected via 'context'
            try:
                code_text = ""
                script_path = self.llm_executor.get_variable(f"node_{node_id}_code_path") or ""
                if script_path and os.path.exists(script_path):
                    with open(script_path, 'r', encoding='utf-8') as _f:
                        code_text = _f.read()
                ctx_success = ("TERMINAL OUTPUT:\n" + terminal_output + "\n\n" + "CODE FILE: " + str(script_path) + "\n```python\n" + code_text + "\n```")
                self.llm_executor.set_variable(f"node_{node_id}_context", ctx_success)
            except Exception:
                pass
            
            return self._get_code_next_node(node, 'output')
            
        except Exception as e:
            # Capture what we have so far
            stdout_str = stdout_capture.getvalue()
            stderr_str = stderr_capture.getvalue()
            
            if stdout_str:
                logger.info(f"Code node stdout (before error):\n{stdout_str}")
            if stderr_str:
                logger.warning(f"Code node stderr (before error):\n{stderr_str}")

            logger.error(f"Code execution failed: {e}")
            import traceback
            error_trace = traceback.format_exc()
            logger.error(error_trace)
            
            # Combined error output
            full_error_output = f"{stdout_str}\n{stderr_str}\nError:\n{error_trace}"
            
            node_id = node.get('id') or node.get('node_id')
            self._set_code_node_error(node, error_trace)
            self.llm_executor.set_variable(f"node_{node_id}_terminal_output", full_error_output)
            self.llm_executor.set_variable(f"node_{node_id}_stdout", stdout_str)
            self.llm_executor.set_variable(f"node_{node_id}_stderr", stderr_str + "\n" + error_trace)
            try:
                script_path = self.llm_executor.get_variable(f"node_{node_id}_code_path") or ""
                code_text = ""
                if script_path and os.path.exists(script_path):
                    with open(script_path, 'r', encoding='utf-8') as _f:
                        code_text = _f.read()
                ctx = ("TERMINAL OUTPUT:\n" + full_error_output + "\n\n" + "CODE FILE: " + str(script_path) + "\n```python\n" + code_text + "\n```")
                self.llm_executor.set_variable(f"node_{node_id}_context", ctx)
            except Exception:
                pass
            try:
                node['last_output_port'] = 'error'
            except Exception:
                pass
            
            # Check if error output is connected
            # Retry control: allow up to 3 patch cycles
            try:
                node_id = node.get('id') or node.get('node_id')
                cnt = int(self._code_retry_counts.get(node_id, 0) or 0)
                cnt += 1
                self._code_retry_counts[node_id] = cnt
                if cnt > 3:
                    logger.error(f"Code node {node_id} exceeded max retries (3). Failing workflow.")
                    return None
            except Exception:
                pass
            next_node = self._get_code_next_node(node, 'error')
            if next_node and next_node != "__done__":
                return next_node
            return None # Fail workflow

    def _get_code_next_node(self, node, port):
        connections = node.get('connections', {})
        if port in connections and connections[port]:
             return connections[port][0]['node_id']
        if port == 'output':
            return "__done__"
        return None

    def _set_code_node_error(self, node, error_msg):
        node_id = node.get('id') or node.get('node_id')
        self.llm_executor.set_variable(f"node_{node_id}_error", error_msg)

    def _sanitize_alias(self, alias):
        try:
            import re
            s = str(alias or '').strip()
            s = re.sub(r"\W+", "_", s)
            if not s:
                return "mod"
            if not re.match(r"^[A-Za-z_]", s):
                s = "mod_" + s
            return s
        except Exception:
            return "mod"

        def _preload_chain_modules(self, scope):
            try:
                idx = self.llm_executor.get_variable('code_modules') or []
                import importlib.util as _ilu
                import sys as _sys
                for entry in (idx if isinstance(idx, list) else []):
                    try:
                        p = entry.get('path')
                        a = entry.get('alias') or entry.get('id')
                        sid = entry.get('id')
                        safe = self._sanitize_alias(a or sid)
                        varname = safe if str(safe).startswith("module_") else f"module_{safe}"
                        if not p or not os.path.exists(p):
                            continue
                        spec = _ilu.spec_from_file_location(varname, p)
                        if not spec or not spec.loader:
                            continue
                        mod = _ilu.module_from_spec(spec)
                        spec.loader.exec_module(mod)
                        scope[varname] = mod
                        try:
                            _sys.modules[varname] = mod
                        except Exception:
                            pass
                        try:
                            self.llm_executor.set_variable(f"module_{safe}_path", p)
                        except Exception:
                            pass
                    except Exception:
                        continue
            except Exception:
                pass

    def _is_persistent_loop(self, code_str):
        try:
            s = (code_str or '')
            sl = s.lower()
            # Broad GUI detection
            gui_indicators = (
                'import tkinter', 'from tkinter', 'tkinter.', 'Tk(', 'mainloop(',
                'import pyqt5', 'from pyqt5', 'pyside2', 'pyside6', 'qtcore', 'qtwidgets', 'exec_',
                'import wx', 'from wx', 'wx.App(',
                'import pygame', 'from pygame', 'pygame.display',
                'import dearpygui', 'from dearpygui', 'dpg.create_context',
                'import kivy', 'from kivy', 'kivy.app',
                'matplotlib.pyplot.show(', 'plt.show(',
                'cefpython', 'pywebview'
            )
            for g in gui_indicators:
                if g in sl:
                    return True
            # Generic infinite loop hints
            if ('while True' in s) or ('while\tTrue' in s):
                return True
            return False
        except Exception:
            return False

    def _run_persistent_script(self, script_path, timeout_s, cwd, leave_running=False):
        try:
            # Use the venv python if available, otherwise find a valid python executable
            python_exe = self._venv_python
            if not python_exe or not os.path.exists(python_exe):
                self._ensure_chain_venv()
                python_exe = self._venv_python

            # CRITICAL: In frozen mode (PyInstaller), sys.executable points to LoOper.exe.
            # We have updated LoOper's main.py to act as a Python interpreter if passed a .py file.
            # This allows us to run persistent scripts even if a separate venv isn't available.
            if getattr(sys, 'frozen', False):
                if not python_exe or not os.path.exists(python_exe):
                    # Use LoOper.exe itself as the interpreter
                    python_exe = sys.executable
            
            cmd = [python_exe or sys.executable, script_path]
            env = os.environ.copy()
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            proc = _sp.Popen(cmd, cwd=cwd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, creationflags=CREATE_NEW_PROCESS_GROUP)
            if leave_running:
                try:
                    pid = proc.pid
                except Exception:
                    pid = None
                try:
                    return True, '', '', '', (f"Process started PID {pid}" if pid else ''), pid
                except Exception:
                    return True, '', '', '', '', pid
            start = time.time()
            stdout_chunks = []
            stderr_chunks = []
            ok = False
            # Allow short warm-up, then close if still running (treat as success)
            while True:
                rc = proc.poll()
                if rc is not None:
                    ok = (rc == 0)
                    break
                if (time.time() - start) >= min(2.0, float(timeout_s or 2.0)):
                    # Consider persistent; close it and mark success
                    try:
                        proc.terminate()
                        try:
                            proc.wait(timeout=0.5)
                        except Exception:
                            pass
                        if proc.poll() is None:
                            proc.kill()
                            try:
                                # Windows fallback: kill process tree
                                _sp.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, text=True, timeout=2)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    ok = True
                    break
                try:
                    time.sleep(0.05)
                except Exception:
                    break
            stdout_str = ''
            stderr_str = ''
            try:
                out, err = proc.communicate(timeout=0.2)
                stdout_str = out or ''
                stderr_str = err or ''
            except Exception:
                # Fallback: read whatever is available
                try:
                    if proc.stdout:
                        stdout_str = (proc.stdout.read() or '')
                except Exception:
                    pass
                try:
                    if proc.stderr:
                        stderr_str = (proc.stderr.read() or '')
                except Exception:
                    pass
            terminal_output = (stdout_str or '') + (stderr_str or '')
            result = terminal_output or ''
            try:
                return ok, result, stdout_str, stderr_str, terminal_output, None
            except Exception:
                return ok, result, stdout_str, stderr_str, terminal_output
        except Exception:
            try:
                return False, '', '', 'subprocess failed', 'subprocess failed', None
            except Exception:
                return False, '', '', 'subprocess failed', 'subprocess failed'

    def _initialize_chain_runtime(self):
        try:
            root = os.path.join(os.getcwd(), 'runtime')
            self._runtime_root = root
            lbl = None
            try:
                base = getattr(self.sequence_executor, 'chain_file_dir', None)
                if base:
                    lbl = os.path.basename(str(base))
            except Exception:
                lbl = None
            if not lbl:
                lbl = 'chain'
            self._chain_label = lbl
            chain_dir = os.path.join(root, 'chains', lbl)
            venv_dir = os.path.join(root, 'venvs', lbl)
            os.makedirs(chain_dir, exist_ok=True)
            os.makedirs(venv_dir, exist_ok=True)
            self._chain_runtime_dir = chain_dir
            self._venv_dir = venv_dir
        except Exception:
            pass

    def _ensure_chain_venv(self):
        try:
            if not self._venv_dir:
                self._initialize_chain_runtime()
            py_exe = os.path.join(self._venv_dir, 'Scripts', 'python.exe')
            if not os.path.exists(py_exe):
                builder = venv.EnvBuilder(with_pip=True)
                builder.create(self._venv_dir)
            self._venv_python = os.path.join(self._venv_dir, 'Scripts', 'python.exe')
            self._venv_site_packages = os.path.join(self._venv_dir, 'Lib', 'site-packages')
        except Exception:
            pass

    def _is_last_code_node(self, node):
        """
        Determines if a code node should be left running after execution.
        
        For GUI applications and persistent processes, we want to keep them running
        even if there are subsequent nodes, so those nodes can interact with the UI.
        """
        try:
            # Get the code content to check if it's a GUI application
            code_data = node.get('data', {})
            code = code_data.get('code', '')
            
            # If it's a GUI application, always keep it running regardless of subsequent nodes
            if self._is_persistent_loop(code):
                logger.info(f"Code node {node.get('id') or node.get('node_id')} detected as GUI/persistent, will keep running")
                return True
            
            # Original logic for non-GUI code
            nid = node.get('id') or node.get('node_id')
            for tid, tnode in (self.workflow_graph.items() if isinstance(self.workflow_graph, dict) else []):
                for inp in (tnode.get('inputs', []) or []):
                    if str(inp.get('from_node')) == str(nid):
                        # If the consumer is a code/tts or anything non-LLM, this node is upstream and should not be left running
                        if tnode.get('type') != 'llm':
                            return False
            
            # Otherwise, inspect explicit output connections: if they exist and point only to LLMs, it's last
            connections = node.get('connections', {}) or {}
            outs = connections.get('output') or []
            if not outs:
                # No explicit output edges; if nobody consumes it in inputs, treat as last
                return True
            for c in outs:
                tid = c.get('node_id')
                if not tid:
                    continue
                t = self.workflow_graph.get(tid, {})
                if t.get('type') and t.get('type') != 'llm':
                    return False
            return True
        except Exception:
            return False

    def _detect_framework(self, code_str):
        try:
            s = (code_str or '').lower()
            if 'tkinter' in s:
                return 'tkinter'
            if ('pyqt5' in s) or ('from pyqt5' in s) or ('qtwidgets' in s):
                return 'pyqt5'
            if 'kivy' in s:
                return 'kivy'
            if ('wxpython' in s) or ('import wx' in s):
                return 'wxpython'
            if 'pywebview' in s:
                return 'pywebview'
            return ''
        except Exception:
            return ''

    def _install_code_dependencies(self, code_str):
        try:
            self._ensure_chain_venv()
            if not self._venv_python:
                return
            import re
            pkgs = set()
            for line in (code_str or '').splitlines():
                m1 = re.match(r"\s*import\s+([a-zA-Z0-9_\., ]+)", line)
                if m1:
                    names = [n.strip() for n in m1.group(1).split(',') if n.strip()]
                    for n in names:
                        pkgs.add(n.split('.')[0])
                    continue
                m2 = re.match(r"\s*from\s+([a-zA-Z0-9_\.]+)\s+import\s+", line)
                if m2:
                    pkgs.add(m2.group(1).split('.')[0])
            stdlib = {
                'os','sys','json','re','typing','time','subprocess','pathlib','math','random','datetime','itertools','collections','functools','threading','asyncio','tkinter'
            }
            pkgs = [p for p in pkgs if p and p not in stdlib]
            for p in pkgs:
                try:
                    _sp.run([self._venv_python, '-m', 'pip', 'install', p], capture_output=True, text=True, timeout=60)
                except Exception:
                    continue
        except Exception:
            pass

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
