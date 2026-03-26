from ...config import logger

class WorkflowGraphBuilder:
    """Builds and manages workflow graphs from sequences and conditional nodes"""
    
    def __init__(self, sequences=None, conditional_nodes=None, llm_nodes=None, tts_nodes=None, chain_import_nodes=None, code_nodes=None, container_nodes=None):
        self.sequences = sequences or []
        self.conditional_nodes = conditional_nodes or []
        self.llm_nodes = llm_nodes or []
        self.tts_nodes = tts_nodes or []
        self.chain_import_nodes = chain_import_nodes or []
        self.code_nodes = code_nodes or []
        self.container_nodes = container_nodes or []
    
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
                node_id = str(sequence.get('id') or sequence.get('node_id'))
                # Handle both 'outputs' and 'connections' formats
                if 'outputs' in sequence:
                    output_connections = sequence.get('outputs', {}).get('output', [])
                elif 'connections' in sequence:
                    # Convert connections format to outputs format
                    output_connections = [{
                        'node_id': str(conn['target_node_id']),
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
            node_id = str(conditional.get('id') or conditional.get('node_id'))
            
            # Handle both 'outputs' and 'connections' formats for conditionals
            if 'outputs' in conditional:
                true_connections = conditional.get('outputs', {}).get('true', [])
                false_connections = conditional.get('outputs', {}).get('false', [])
                # Ensure target IDs are strings
                for conn in true_connections:
                    if 'node_id' in conn: conn['node_id'] = str(conn['node_id'])
                for conn in false_connections:
                    if 'node_id' in conn: conn['node_id'] = str(conn['node_id'])
            elif 'connections' in conditional:
                # Convert connections format to outputs format
                true_connections = []
                false_connections = []
                for conn in conditional.get('connections', []):
                    # Check both string formats and boolean formats
                    out_port = conn.get('output_port')
                    if str(out_port).lower() == 'true':
                        true_connections.append({'node_id': str(conn['target_node_id']), 'input_port': conn.get('input_port')})
                    elif str(out_port).lower() == 'false':
                        false_connections.append({'node_id': str(conn['target_node_id']), 'input_port': conn.get('input_port')})
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
            node_id = str(llm_node.get('id') or llm_node.get('node_id'))
            connections_by_port = {}
            if isinstance(llm_node.get('outputs'), dict):
                for port_name, conns in (llm_node.get('outputs') or {}).items():
                    try:
                        # Ensure target IDs are strings
                        fixed_conns = []
                        for c in (conns or []):
                            if isinstance(c, dict):
                                c['node_id'] = str(c.get('node_id'))
                                fixed_conns.append(c)
                        connections_by_port[port_name] = fixed_conns
                    except Exception:
                        connections_by_port[port_name] = []
            elif isinstance(llm_node.get('connections'), list):
                for conn in llm_node.get('connections', []):
                    port = conn.get('output_port') or 'output'
                    lst = connections_by_port.setdefault(port, [])
                    lst.append({'node_id': str(conn.get('target_node_id')), 'input_port': conn.get('input_port')})
            else:
                connections_by_port = {'output': []}
            graph[node_id] = {
                'type': 'llm',
                'data': llm_node,
                'connections': connections_by_port
            }
        
        # Add TTS nodes to graph
        for tts_node in self.tts_nodes:
            node_id = str(tts_node.get('id') or tts_node.get('node_id'))
            # Convert connections format to outputs format for TTS nodes
            output_connections = [{
                'node_id': str(conn['target_node_id']),
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
            node_id = str(chain_import_node.get('id') or chain_import_node.get('node_id'))
            # Convert connections format to outputs format for chain import nodes
            output_connections = [{
                'node_id': str(conn['target_node_id']),
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
            node_id = str(code_node.get('id') or code_node.get('node_id'))
            # Convert connections format to outputs format for Code nodes
            output_connections = []
            error_connections = []
            
            for conn in code_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
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
            
        # Add Container nodes to graph
        for container_node in self.container_nodes:
            node_id = str(container_node.get('id') or container_node.get('node_id'))
            # Convert connections format to outputs format for Container nodes
            output_connections = []
            
            for conn in container_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                if conn.get('output_port') == 'output':
                    output_connections.append(target)
            
            graph[node_id] = {
                'type': 'container',
                'data': container_node,
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
                sequence_id = str(sequence.get('id') or sequence.get('node_id') or f'sequence_{i}')
                inputs = sequence.get('inputs', {})
                for input_name, connection_info in inputs.items():
                    source_node_id = str(connection_info['node_id'])
                    if source_node_id in graph:
                        if 'inputs' not in graph[sequence_id]:
                            graph[sequence_id]['inputs'] = []
                        graph[sequence_id]['inputs'].append({
                            'from_node': source_node_id,
                            'output_type': connection_info['output_name']
                        })
        
        # Handle LLM node inputs
        for llm_node in self.llm_nodes:
            llm_id = str(llm_node.get('id') or llm_node.get('node_id'))
            inputs = llm_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[llm_id]:
                        graph[llm_id]['inputs'] = []
                    graph[llm_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle conditional node inputs
        for conditional in self.conditional_nodes:
            conditional_id = str(conditional.get('id') or conditional.get('node_id'))
            inputs = conditional.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[conditional_id]:
                        graph[conditional_id]['inputs'] = []
                    graph[conditional_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle TTS node inputs
        for tts_node in self.tts_nodes:
            tts_id = str(tts_node.get('id') or tts_node.get('node_id'))
            inputs = tts_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[tts_id]:
                        graph[tts_id]['inputs'] = []
                    graph[tts_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle chain import node inputs
        for chain_import_node in self.chain_import_nodes:
            chain_import_id = str(chain_import_node.get('id') or chain_import_node.get('node_id'))
            inputs = chain_import_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[chain_import_id]:
                        graph[chain_import_id]['inputs'] = []
                    graph[chain_import_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle code node inputs
        for code_node in self.code_nodes:
            code_id = str(code_node.get('id') or code_node.get('node_id'))
            inputs = code_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
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
