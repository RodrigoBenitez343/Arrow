import logging
logger = logging.getLogger(__name__)

# Router input ports: an edge landing on one of these makes the SOURCE node a
# subroutine of the router (an LLM tool / an orchestrator mini brain) that must
# never run standalone in the main workflow loop.  The 'chains' entry is the
# future orchestrator port — absent from shipped chains today, so it is a
# no-op until that port ships (predicate-only groundwork).
_ROUTER_INPUT_PORTS = {
    'llm': ('tools',),
    'orchestrator': ('brains', 'chains'),
}
# Connection buckets that drive execution order.  Data buckets (data, ctx_out,
# trace, error, context, named data ports) never count as execution edges.
_EXEC_BUCKETS = ('output', 'true', 'false', 'route')


def _exec_edge_pairs(node):
    """(target_id, input_port) for every execution-port edge of a built node.

    Handles both connection shapes the builder produces (per-port dict, or a
    bare list) and skips malformed entries — a bad edge must never crash the
    graph build.
    """
    pairs = []
    conns = node.get('connections') or {}
    if isinstance(conns, dict):
        buckets = [(k, v) for k, v in conns.items() if k in _EXEC_BUCKETS]
    elif isinstance(conns, list):
        buckets = [('output', conns)]
    else:
        buckets = []
    for _bucket, entries in buckets:
        for conn in (entries or []):
            if not isinstance(conn, dict):
                continue
            tid = conn.get('node_id') or conn.get('target_node_id')
            if tid:
                pairs.append((str(tid), str(conn.get('input_port') or '')))
    return pairs


def _is_router_port_target(graph, target_id, input_port):
    """True when the edge lands on a router input port (llm.tools / orchestrator.brains|chains)."""
    target = graph.get(target_id) or {}
    return input_port in _ROUTER_INPUT_PORTS.get(str(target.get('type') or ''), ())


def _is_tool_provider_node(graph, node):
    """Exclusivity rule: EVERY execution edge must land on a router port.

    A node with no execution edges at all is NOT a provider — it keeps its
    standalone role (starting node).
    """
    pairs = _exec_edge_pairs(node)
    if not pairs:
        return False
    return all(_is_router_port_target(graph, tid, port) for tid, port in pairs)


class WorkflowGraphBuilder:
    """Builds and manages workflow graphs from sequences and conditional nodes"""
    
    def __init__(self, sequences=None, conditional_nodes=None, llm_nodes=None, chain_import_nodes=None, code_nodes=None, container_nodes=None, context_nodes=None, input_nodes=None, handle_nodes=None, mcp_nodes=None, output_nodes=None, web_sequences=None, form_filler_nodes=None, orchestrator_nodes=None):
        self.sequences = sequences or []
        self.conditional_nodes = conditional_nodes or []
        self.llm_nodes = llm_nodes or []
        self.chain_import_nodes = chain_import_nodes or []
        self.code_nodes = code_nodes or []
        self.container_nodes = container_nodes or []
        self.context_nodes = context_nodes or []
        self.input_nodes = input_nodes or []
        self.handle_nodes = handle_nodes or []
        self.mcp_nodes = mcp_nodes or []
        self.output_nodes = output_nodes or []
        self.web_sequences = web_sequences or []
        self.form_filler_nodes = form_filler_nodes or []
        self.orchestrator_nodes = orchestrator_nodes or []

    def _resolve_node_id(self, node, node_type, index):
        """Resolve a unique, collision-free node id for a config node (b7).

        - Missing/empty node_id or id gets a stable fallback (``{type}_{index}``)
          instead of str(None) -> 'None'.
        - Duplicate ids are disambiguated with a '#n' suffix instead of silently
          overwriting the earlier node in ``graph[node_id]``.
        - The resolved id is persisted back onto the config dict (both 'id' and
          'node_id') so every downstream normalization block that re-reads the
          node stays consistent with the graph key.
        """
        raw = str(node.get('id') or node.get('node_id') or '').strip()
        if not raw:
            raw = f"{node_type}_{index}"
        resolved = raw
        if resolved in self._used_node_ids:
            logger.error(
                "Duplicate node_id %r in chain config (type=%s, index=%d) — "
                "disambiguating; the previous node would otherwise be overwritten",
                resolved, node_type, index,
            )
            base, n = resolved, 2
            while f"{base}#{n}" in self._used_node_ids:
                n += 1
            resolved = f"{base}#{n}"
        self._used_node_ids.add(resolved)
        if resolved != raw:
            try:
                if isinstance(node, dict):
                    node['id'] = resolved
                    node['node_id'] = resolved
            except Exception:
                pass
        return resolved

    def build_workflow_graph(self):
        """
        Builds a workflow graph from sequences and conditional nodes for navigation.
        
        Returns:
            dict: A graph structure mapping node IDs to their connections
        """
        graph = {}
        # Tracks assigned node ids for collision detection (b7).
        self._used_node_ids = set()
        
        # Add sequence nodes to graph
        for i, sequence in enumerate(self.sequences):
            # Handle both old and new sequence formats
            if isinstance(sequence, dict) and ('id' in sequence or 'node_id' in sequence):
                # New format with ID and connections - support both 'id' and 'node_id'
                node_id = self._resolve_node_id(sequence, 'sequence', i)
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
                node_id = self._resolve_node_id(sequence, 'sequence', i)
                output_connections = []
            
            graph[node_id] = {
                'type': 'sequence',
                'data': sequence,
                'index': i,
                'connections': {'output': output_connections}
            }
        
        # Add web sequence nodes to graph
        for i, web_sequence in enumerate(self.web_sequences):
            # Web sequences share the sequence node shape: config dict with
            # id/node_id + connections (or legacy bare data).
            if isinstance(web_sequence, dict) and ('id' in web_sequence or 'node_id' in web_sequence):
                node_id = self._resolve_node_id(web_sequence, 'web_sequence', i)
                # Separate execution ('output') from data ('ctx_out') connections
                output_connections = []
                ctx_out_connections = []
                if 'outputs' in web_sequence:
                    output_connections = web_sequence.get('outputs', {}).get('output', []) or []
                    ctx_out_connections = web_sequence.get('outputs', {}).get('ctx_out', []) or []
                elif 'connections' in web_sequence:
                    for conn in web_sequence.get('connections', []):
                        target = {
                            'node_id': str(conn['target_node_id']),
                            'input_port': conn.get('input_port')
                        }
                        if conn.get('output_port') == 'ctx_out':
                            ctx_out_connections.append(target)
                        else:
                            output_connections.append(target)
            else:
                node_id = self._resolve_node_id(web_sequence, 'web_sequence', i)
                output_connections = []
                ctx_out_connections = []

            conns = {'output': output_connections}
            if ctx_out_connections:
                conns['ctx_out'] = ctx_out_connections
            graph[node_id] = {
                'type': 'web_sequence',
                'data': web_sequence,
                'index': i,
                'connections': conns
            }
        
        # Add conditional nodes to graph
        for _c_idx, conditional in enumerate(self.conditional_nodes):
            # Support both 'id' and 'node_id' fields
            node_id = self._resolve_node_id(conditional, 'conditional', _c_idx)
            
            # Handle both 'outputs' and 'connections' formats for conditionals.
            # Loop conditionals (loop_type + internal sequence/chain file) expose
            # a single 'output' port — their continuation after the loop finishes —
            # instead of true/false branches; keep those connections too.
            if 'outputs' in conditional:
                true_connections = conditional.get('outputs', {}).get('true', [])
                false_connections = conditional.get('outputs', {}).get('false', [])
                output_connections = conditional.get('outputs', {}).get('output', [])
                # Ensure target IDs are strings
                for conn in true_connections:
                    if 'node_id' in conn: conn['node_id'] = str(conn['node_id'])
                for conn in false_connections:
                    if 'node_id' in conn: conn['node_id'] = str(conn['node_id'])
                for conn in output_connections:
                    if 'node_id' in conn: conn['node_id'] = str(conn['node_id'])
            elif 'connections' in conditional:
                # Convert connections format to outputs format
                true_connections = []
                false_connections = []
                output_connections = []
                for conn in conditional.get('connections', []):
                    # Check both string formats and boolean formats
                    out_port = str(conn.get('output_port') or 'output').lower()
                    if out_port == 'true':
                        true_connections.append({'node_id': str(conn['target_node_id']), 'input_port': conn.get('input_port')})
                    elif out_port == 'false':
                        false_connections.append({'node_id': str(conn['target_node_id']), 'input_port': conn.get('input_port')})
                    elif out_port == 'output':
                        # Loop conditional continuation (file loops)
                        output_connections.append({'node_id': str(conn['target_node_id']), 'input_port': conn.get('input_port')})
            else:
                true_connections = []
                false_connections = []
                output_connections = []

            cond_connections = {
                'true': true_connections,
                'false': false_connections
            }
            if output_connections:
                cond_connections['output'] = output_connections
            graph[node_id] = {
                'type': 'conditional',
                'data': conditional,
                'connections': cond_connections
            }
        
        # Add LLM nodes to graph
        for _l_idx, llm_node in enumerate(self.llm_nodes):
            node_id = self._resolve_node_id(llm_node, 'llm', _l_idx)
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
        
        # Add Orchestrator nodes to graph
        for _or_idx, orchestrator_node in enumerate(self.orchestrator_nodes):
            node_id = self._resolve_node_id(orchestrator_node, 'orchestrator', _or_idx)
            # Generic per-port connections: 'output' (achieved) and 'route'
            # (unfulfilled) drive execution; 'trace' is a data-only port.
            connections_by_port = {}
            if isinstance(orchestrator_node.get('outputs'), dict):
                for port_name, conns in (orchestrator_node.get('outputs') or {}).items():
                    fixed_conns = []
                    for c in (conns or []):
                        if isinstance(c, dict):
                            c['node_id'] = str(c.get('node_id'))
                            fixed_conns.append(c)
                    connections_by_port[port_name] = fixed_conns
            elif isinstance(orchestrator_node.get('connections'), list):
                for conn in orchestrator_node.get('connections', []):
                    port = conn.get('output_port') or 'output'
                    connections_by_port.setdefault(port, []).append({
                        'node_id': str(conn.get('target_node_id')),
                        'input_port': conn.get('input_port'),
                    })
            else:
                connections_by_port = {'output': []}
            graph[node_id] = {
                'type': 'orchestrator',
                'data': orchestrator_node,
                'connections': connections_by_port
            }
        
        # Add chain import nodes to graph
        for _ci_idx, chain_import_node in enumerate(self.chain_import_nodes):
            node_id = self._resolve_node_id(chain_import_node, 'chain_import', _ci_idx)
            # Convert connections format to outputs format for chain import nodes.
            # Only the 'output' port exists now (execution + the tools wiring).
            # The bucketing below stays generic so a legacy data-port edge from
            # an old save cannot crash the build; it simply resolves to no value.
            output_connections = []
            data_buckets = {}
            for conn in chain_import_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                port_name = conn.get('output_port') or 'output'
                if port_name == 'output':
                    output_connections.append(target)
                else:
                    data_buckets.setdefault(port_name, []).append(target)
            conns = {'output': output_connections}
            conns.update(data_buckets)
            # The tool-provider flag is computed once for EVERY node type in
            # the generic post-pass at the end of this method (placed after
            # the dangling-edge drop so a ghost edge cannot suppress it).
            graph[node_id] = {
                'type': 'chain_import',
                'data': chain_import_node,
                'connections': conns,
                'selected_by_llm': False
            }
            
        # Add Code nodes to graph
        for _cd_idx, code_node in enumerate(self.code_nodes):
            node_id = self._resolve_node_id(code_node, 'code', _cd_idx)
            # Convert connections format to outputs format for Code nodes
            output_connections = []
            error_connections = []
            
            for conn in code_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port'),
                    'output_port': conn.get('output_port'),
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
        for _ct_idx, container_node in enumerate(self.container_nodes):
            node_id = self._resolve_node_id(container_node, 'container', _ct_idx)
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
            
        # Add Context nodes to graph
        for _x_idx, context_node in enumerate(self.context_nodes):
            node_id = self._resolve_node_id(context_node, 'context', _x_idx)
            # Separate execution ('output') from data ('ctx_out') connections
            output_connections = []
            ctx_out_connections = []
            
            for conn in context_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                op = conn.get('output_port')
                if op == 'output':
                    output_connections.append(target)
                elif op == 'ctx_out':
                    ctx_out_connections.append(target)
                else:
                    # Unknown port — treat as output for backward compat
                    output_connections.append(target)
            
            conns = {'output': output_connections}
            if ctx_out_connections:
                conns['ctx_out'] = ctx_out_connections
            graph[node_id] = {
                'type': 'context',
                'data': context_node,
                'connections': conns
            }
        
        # Add Input nodes to graph
        for _i_idx, input_node in enumerate(self.input_nodes):
            node_id = self._resolve_node_id(input_node, 'input', _i_idx)
            # Separate execution ('output') from explicit data ('data')
            # connections.  The base output port drives execution order like
            # any other node; the data port carries the resolved value ONLY to
            # consumers explicitly connected to it (no reachability walk).
            output_connections = []
            data_connections = []
            true_connections = []
            false_connections = []
            for conn in input_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                op = conn.get('output_port')
                if op == 'output':
                    output_connections.append(target)
                elif op == 'data':
                    data_connections.append(target)
                elif op == 'true':
                    true_connections.append(target)
                elif op == 'false':
                    false_connections.append(target)
                else:
                    # Unknown port — treat as output for backward compat
                    output_connections.append(target)
            conns = {'output': output_connections}
            if data_connections:
                conns['data'] = data_connections
            if true_connections:
                conns['true'] = true_connections
            if false_connections:
                conns['false'] = false_connections
            graph[node_id] = {
                'type': 'input',
                'data': input_node,
                'connections': conns
            }
        
        # Add Handle nodes to graph
        for _h_idx, handle_node in enumerate(self.handle_nodes):
            node_id = self._resolve_node_id(handle_node, 'handle', _h_idx)
            output_connections = []
            for conn in handle_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                if conn.get('output_port') == 'output':
                    output_connections.append(target)
            graph[node_id] = {
                'type': 'handle',
                'data': handle_node,
                'connections': {
                    'output': output_connections
                }
            }
        
        # Add Form Filling nodes to graph
        for _ff_idx, ff_node in enumerate(self.form_filler_nodes):
            node_id = self._resolve_node_id(ff_node, 'form_filler', _ff_idx)
            output_connections = []
            for conn in ff_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                if conn.get('output_port') == 'output':
                    output_connections.append(target)
            graph[node_id] = {
                'type': 'form_filler',
                'data': ff_node,
                'connections': {
                    'output': output_connections
                }
            }
        
        # Add MCP nodes to graph
        for _m_idx, mcp_node in enumerate(self.mcp_nodes):
            node_id = self._resolve_node_id(mcp_node, 'mcp', _m_idx)
            output_connections = []
            for conn in mcp_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                if conn.get('output_port') == 'output':
                    output_connections.append(target)
            graph[node_id] = {
                'type': 'mcp',
                'data': mcp_node,
                'connections': {
                    'output': output_connections
                }
            }
        
        # Add Output nodes to graph
        for _o_idx, output_node in enumerate(self.output_nodes):
            node_id = self._resolve_node_id(output_node, 'output', _o_idx)
            output_connections = []
            for conn in output_node.get('connections', []):
                target = {
                    'node_id': str(conn['target_node_id']),
                    'input_port': conn.get('input_port')
                }
                if conn.get('output_port') == 'output':
                    output_connections.append(target)
            graph[node_id] = {
                'type': 'output',
                'data': output_node,
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
                
                for output_type in ['true', 'false', 'output']:
                    if output_type in connections:
                        for connection in connections[output_type]:
                            target_node_id = connection['node_id']
                            if target_node_id in graph:
                                if 'inputs' not in graph[target_node_id]:
                                    graph[target_node_id]['inputs'] = []
                                graph[target_node_id]['inputs'].append({
                                    'from_node': conditional_id,
                                    'output_type': output_type,
                                    'input_port': connection.get('input_port')
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

        # Build connections from handle node outputs to other nodes
        for handle_node in self.handle_nodes:
            handle_id = handle_node.get('id') or handle_node.get('node_id')
            if handle_id in graph:
                handle_graph_node = graph[handle_id]
                connections = handle_graph_node['connections']
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': handle_id,
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
                            })

        # Build connections from Form Filling node outputs to other nodes
        for ff_node in self.form_filler_nodes:
            ff_id = ff_node.get('id') or ff_node.get('node_id')
            if ff_id in graph:
                ff_graph_node = graph[ff_id]
                connections = ff_graph_node['connections']
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': ff_id,
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
                            })

        # Build connections from MCP node outputs to other nodes
        for mcp_node in self.mcp_nodes:
            mcp_id = mcp_node.get('id') or mcp_node.get('node_id')
            if mcp_id in graph:
                mcp_graph_node = graph[mcp_id]
                connections = mcp_graph_node['connections']
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': mcp_id,
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
                            })

        # Build connections from Orchestrator outputs to other nodes
        for orchestrator_node in self.orchestrator_nodes:
            orchestrator_id = orchestrator_node.get('id') or orchestrator_node.get('node_id')
            if orchestrator_id in graph:
                orchestrator_graph_node = graph[orchestrator_id]
                connections = orchestrator_graph_node['connections']
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection.get('node_id')
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': orchestrator_id,
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
                            })

        # Build connections from output node inputs (upstream nodes feed into output nodes)
        for output_node in self.output_nodes:
            output_id = output_node.get('id') or output_node.get('node_id')
            if output_id in graph:
                output_graph_node = graph[output_id]
                connections = output_graph_node['connections']
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': output_id,
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
        
        # Build connections from web sequence node outputs to other nodes
        for i, web_sequence in enumerate(self.web_sequences):
            if isinstance(web_sequence, dict) and ('outputs' in web_sequence or 'connections' in web_sequence):
                web_sequence_id = web_sequence.get('id') or web_sequence.get('node_id') or f'web_sequence_{i}'
                
                if web_sequence_id in graph:
                    web_sequence_graph_node = graph[web_sequence_id]
                    connections = web_sequence_graph_node['connections']
                    for output_name, output_connections in connections.items():
                        for connection in output_connections:
                            target_node_id = connection['node_id']
                            if target_node_id in graph:
                                if 'inputs' not in graph[target_node_id]:
                                    graph[target_node_id]['inputs'] = []
                                graph[target_node_id]['inputs'].append({
                                    'from_node': web_sequence_id,
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
                                'output_type': connection.get('output_port') or output_name,
                                'input_port': connection.get('input_port')
                            })
        
        # Build connections from context node outputs to other nodes
        for context_node in self.context_nodes:
            context_id = context_node.get('id') or context_node.get('node_id')
            
            # Get connections from the graph node we just created
            if context_id in graph:
                context_graph_node = graph[context_id]
                connections = context_graph_node['connections']
                
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': context_id,
                                'output_type': output_name,
                                'input_port': connection.get('input_port')
                            })
        
        # Build connections from Input node outputs to other nodes
        for input_node in self.input_nodes:
            input_id = input_node.get('id') or input_node.get('node_id')
            
            # Get connections from the graph node we just created
            if input_id in graph:
                input_graph_node = graph[input_id]
                connections = input_graph_node['connections']
                
                for output_name, output_connections in connections.items():
                    for connection in output_connections:
                        target_node_id = connection['node_id']
                        if target_node_id in graph:
                            if 'inputs' not in graph[target_node_id]:
                                graph[target_node_id]['inputs'] = []
                            graph[target_node_id]['inputs'].append({
                                'from_node': input_id,
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
                        'output_type': connection_info['output_name'],
                        'input_port': input_name
                    })
        
        # Handle context node inputs
        for context_node in self.context_nodes:
            context_id = str(context_node.get('id') or context_node.get('node_id'))
            inputs = context_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[context_id]:
                        graph[context_id]['inputs'] = []
                    graph[context_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle Input node inputs
        for input_node in self.input_nodes:
            input_id = str(input_node.get('id') or input_node.get('node_id'))
            inputs = input_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[input_id]:
                        graph[input_id]['inputs'] = []
                    graph[input_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Handle handle node inputs
        for handle_node in self.handle_nodes:
            handle_id = str(handle_node.get('id') or handle_node.get('node_id'))
            inputs = handle_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[handle_id]:
                        graph[handle_id]['inputs'] = []
                    graph[handle_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })

        # Handle MCP node inputs
        for mcp_node in self.mcp_nodes:
            mcp_id = str(mcp_node.get('id') or mcp_node.get('node_id'))
            inputs = mcp_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[mcp_id]:
                        graph[mcp_id]['inputs'] = []
                    graph[mcp_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })

        # Handle Output node inputs
        for output_node in self.output_nodes:
            output_id = str(output_node.get('id') or output_node.get('node_id'))
            inputs = output_node.get('inputs', {})
            for input_name, connection_info in inputs.items():
                source_node_id = str(connection_info['node_id'])
                if source_node_id in graph:
                    if 'inputs' not in graph[output_id]:
                        graph[output_id]['inputs'] = []
                    graph[output_id]['inputs'].append({
                        'from_node': source_node_id,
                        'output_type': connection_info['output_name']
                    })
        
        # Normalize code-node inputs: the same edge can be registered twice —
        # once from producer-side connections (which carry input_port) and once
        # from a legacy code-node "inputs" dict (which may not).  Keep the
        # ported entry and drop exact duplicates so a data port never resolves
        # twice (code_ops injects connected non-exec ports as variables).
        for code_node in self.code_nodes:
            code_id = str(code_node.get('id') or code_node.get('node_id'))
            if code_id not in graph:
                continue
            _raw = graph[code_id].get('inputs') or []
            if not _raw:
                continue
            _keep = {}
            for _inp in _raw:
                _from = str(_inp.get('from_node') or '')
                _out = str(_inp.get('output_type') or _inp.get('output_port') or 'output')
                _port = _inp.get('input_port')
                _has_port = _port not in (None, '', 'None')
                _key = (_from, _out, str(_port) if _has_port else '')
                _prev = _keep.get(_key)
                if _prev is None or _has_port:
                    _keep[_key] = dict(_inp)
            graph[code_id]['inputs'] = list(_keep.values())

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
                        _desc = ''
                        try:
                            _label = _sdata.get('name') or ''
                            _desc = _sdata.get('description') or ''
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
                            # For chain import nodes: read the chain file's description directly
                            # (the node data has no 'description' property, only chain_file_path)
                            if _src.get('type') == 'chain_import' and not _desc:
                                try:
                                    _cf_path = _sdata.get('chain_file_path') or _sdata.get('chain_file') or ''
                                    if _cf_path:
                                        # In frozen builds, the absolute source path won't exist —
                                        # try resolving the basename against known data dirs.
                                        if not _os.path.exists(_cf_path) and getattr(__import__('sys'), 'frozen', False):
                                            _sys = __import__('sys')
                                            _meipass = getattr(_sys, '_MEIPASS', None)
                                            _exe_dir = _os.path.dirname(_sys.executable) if getattr(_sys, 'frozen', False) else None
                                            _base_name = _os.path.basename(_cf_path)
                                            for _cand in [
                                                _os.path.join(_meipass, 'LoOper', 'chains', _base_name) if _meipass else '',
                                                _os.path.join(_meipass, 'chains', _base_name) if _meipass else '',
                                                _os.path.join(_exe_dir, '_internal', 'LoOper', 'chains', _base_name) if _exe_dir else '',
                                                _os.path.join(_exe_dir, '_internal', 'chains', _base_name) if _exe_dir else '',
                                                _os.path.join(_exe_dir, 'chains', _base_name) if _exe_dir else '',
                                            ]:
                                                if _cand and _os.path.exists(_cand):
                                                    _cf_path = _cand
                                                    break
                                        if _cf_path and _os.path.exists(_cf_path):
                                            with open(_cf_path, 'r', encoding='utf-8') as _cf:
                                                import json as _json_loader
                                                _chain_data = _json_loader.load(_cf)
                                                _chain_desc = _chain_data.get('description', '')
                                                if _chain_desc:
                                                    _desc = _chain_desc
                                except Exception:
                                    pass
                            # Prefer explicit description over name for assistant tool selection
                            _final_desc = _desc if _desc else _label
                            # Enrich code node descriptions with argument hint
                            if _src.get('type') == 'code' and _final_desc:
                                if 'args' not in _final_desc.lower():
                                    _final_desc = _final_desc + ' (pass arguments as JSON dict via args)'
                            _tool_map[_src_id] = _final_desc
                    except Exception:
                        continue
                if _tool_map:
                    _ld = _ndata.get('data') if isinstance(_ndata, dict) else None
                    if isinstance(_ld, dict):
                        # Derive tool_descriptions purely from connected tools — no stale carry-over
                        _cfg = _ld.get('llm_configuration') if isinstance(_ld.get('llm_configuration'), dict) else None
                        if _cfg is not None:
                            _cfg['tool_descriptions'] = _tool_map
                        else:
                            _ld['tool_descriptions'] = _tool_map
        except Exception:
            pass
        # ── Post-build validation: dangling connection targets ──
        # A connection pointing at a node id absent from the graph (e.g. a
        # node copied/dropped from another chain whose stored edges still
        # reference the source chain, or a partial/selection run) can never
        # become an edge or an input — execution already ignores it.  Drop it
        # from the built node so scheduling never trips on a ghost target, and
        # surface it as a WARNING, never as a chain failure.  Shared-context
        # clones (shared_context_chain_file) legitimately carry foreign edges
        # (their cross-chain link is the runtime identity feed, not graph
        # edges) so those log at DEBUG only.
        _dangling = 0
        for _nid, _nd in graph.items():
            _ndata = _nd.get('data', {}) or {}
            _is_shared_ctx = bool(
                _nd.get('type') == 'context'
                and str(_ndata.get('shared_context_chain_file') or '').strip()
            )
            _conns = _nd.get('connections', {}) or {}
            if isinstance(_conns, dict):
                _port_items = list(_conns.items())
            elif isinstance(_conns, list):
                _port_items = [('output', _conns)]
            else:
                _port_items = []
            for _port, _lst in _port_items:
                _keep = []
                for _c in (_lst or []):
                    _tid = _c.get('node_id') or _c.get('target_node_id')
                    if _tid and _tid not in graph:
                        _dangling += 1
                        if _is_shared_ctx:
                            logger.debug(
                                "Dangling connection target %s from shared-context "
                                "node %s (port=%s) — foreign edge from source chain, "
                                "dropped (cross-chain link is the identity feed)",
                                _tid, _nid, _port,
                            )
                        else:
                            logger.warning(
                                "Dangling connection target %s from node %s "
                                "(type=%s, port=%s) — target absent from workflow "
                                "graph; stale edge dropped",
                                _tid, _nid, _nd.get('type'), _port,
                            )
                        continue
                    _keep.append(_c)
                _lst[:] = _keep
        if _dangling:
            logger.warning(
                "Graph build validation: %d dangling connection target(s) found "
                "and dropped", _dangling,
            )

        # ── Generic tool-provider flag (single source of truth) ──
        # A node whose EVERY execution edge lands on a router input port
        # (llm 'tools' / orchestrator 'brains'|'chains') is a SUBROUTINE: its
        # router invokes it on demand and it must never run in the main loop.
        # Computed here — after the dangling-edge drop above — so a ghost edge
        # can never suppress the flag.  Only True is ever written; absence of
        # the key means "not a provider".
        _flagged = {}
        for _tp_id, _tp_node in graph.items():
            try:
                if not _is_tool_provider_node(graph, _tp_node):
                    continue
                _tp_node['tool_provider'] = True
                _flagged[_tp_id] = str(_tp_node.get('type') or '')
                # Exclusivity was the rule; a node that ALSO has an execution
                # input is wired both ways — the subroutine role wins and its
                # execution edge(s) will never fire.  Surface it, never fail.
                _has_exec_input = any(
                    isinstance(inp, dict)
                    and inp.get('input_port', 'input') in ('input', '', None)
                    for inp in (_tp_node.get('inputs') or [])
                )
                if _has_exec_input:
                    logger.warning(
                        "Node %s (type=%s) is wired as a router subroutine AND "
                        "on the execution path — the subroutine role wins; its "
                        "execution edge(s) will never fire",
                        _tp_id, _tp_node.get('type'),
                    )
            except Exception as _tp_err:
                logger.debug("tool-provider scan failed for %s: %s", _tp_id, _tp_err)
        if _flagged:
            _by_type = {}
            for _t in _flagged.values():
                _by_type[_t] = _by_type.get(_t, 0) + 1
            logger.info(
                "Tool-provider flag: %d node(s) gated as router subroutines (%s)",
                len(_flagged),
                ", ".join(f"{t}:{c}" for t, c in sorted(_by_type.items())),
            )
        logger.info(f"Built workflow graph with {len(graph)} nodes")
        return graph
