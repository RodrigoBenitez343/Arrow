#!/usr/bin/env python3

import json
import sys
import os

# Add the LoOper directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'LoOper'))

from LoOper.player.multi_sequence_player import MultiSequencePlayer
from LoOper.player.multi_sequence.workflow_graph import WorkflowGraphBuilder

def analyze_workflow_execution():
    """Analyze the workflow execution to identify missing nodes after window maximization"""
    
    # Load the chain configuration
    chain_file = r"c:\Users\david.benitez\OneDrive - Motivus\Documents\LoOper\LoOper\chains\testchainformeeting.json"
    
    with open(chain_file, 'r') as f:
        chain_config = json.load(f)
    
    print(f"Loaded chain with:")
    print(f"  - {len(chain_config.get('sequences', []))} sequences")
    print(f"  - {len(chain_config.get('conditional_nodes', []))} conditional nodes")
    print(f"  - {len(chain_config.get('llm_nodes', []))} LLM nodes")
    print(f"  - {len(chain_config.get('tts_nodes', []))} TTS nodes")
    
    # Build workflow graph
    builder = WorkflowGraphBuilder(
        sequences=chain_config.get('sequences', []),
        conditional_nodes=chain_config.get('conditional_nodes', []),
        llm_nodes=chain_config.get('llm_nodes', []),
        tts_nodes=chain_config.get('tts_nodes', [])
    )
    
    workflow_graph = builder.build_workflow_graph()
    print(f"\nBuilt workflow graph with {len(workflow_graph)} nodes")
    
    # Analyze sequences and their connections
    print("\n=== SEQUENCE ANALYSIS ===")
    for i, seq in enumerate(chain_config.get('sequences', [])):
        seq_id = seq.get('node_id', f'sequence_{i}')
        seq_name = seq.get('name', 'Unknown')
        print(f"\nSequence {i}: {seq_name} (ID: {seq_id})")
        
        # Check if this sequence has connections
        if seq_id in workflow_graph:
            node_data = workflow_graph[seq_id]
            inputs = node_data.get('inputs', [])
            connections = node_data.get('connections', {})
            
            print(f"  Inputs: {len(inputs)}")
            for inp in inputs:
                print(f"    <- {inp['from_node']} ({inp['output_type']})")
            
            print(f"  Outputs: {len(connections.get('output', []))}")
            for conn in connections.get('output', []):
                print(f"    -> {conn['node_id']}")
        
        # Look for window maximization actions
        actions = seq.get('actions', [])
        has_maximize = any('maximize' in str(action).lower() for action in actions)
        if has_maximize:
            print(f"  *** CONTAINS WINDOW MAXIMIZATION ***")
            # Find what should come after this sequence
            if seq_id in workflow_graph:
                connections = workflow_graph[seq_id].get('connections', {})
                if connections.get('output'):
                    next_node = connections['output'][0]['node_id']
                    print(f"  Next node should be: {next_node}")
                    
                    # Check if next node has proper inputs
                    if next_node in workflow_graph:
                        next_inputs = workflow_graph[next_node].get('inputs', [])
                        print(f"  Next node inputs: {next_inputs}")
    
    # Find starting nodes
    starting_nodes = []
    for node_id, node_data in workflow_graph.items():
        if 'inputs' not in node_data or not node_data['inputs']:
            starting_nodes.append(node_id)
    
    print(f"\n=== STARTING NODES ===")
    for node_id in starting_nodes:
        node_data = workflow_graph[node_id]
        print(f"Starting node: {node_id} (type: {node_data['type']})")
    
    # Trace execution path
    print(f"\n=== EXECUTION PATH SIMULATION ===")
    completed_nodes = set()
    ready_nodes = set(starting_nodes)
    execution_order = []
    
    while ready_nodes:
        current_node_id = ready_nodes.pop()
        if current_node_id in completed_nodes:
            continue
            
        node = workflow_graph.get(current_node_id)
        if not node:
            continue
            
        execution_order.append((current_node_id, node['type']))
        completed_nodes.add(current_node_id)
        
        # Simulate finding next nodes
        if node['type'] == 'conditional':
            # For simulation, assume true branch
            connections = node.get('connections', {})
            if 'true' in connections and connections['true']:
                next_node = connections['true'][0]['node_id']
                if next_node not in completed_nodes:
                    ready_nodes.add(next_node)
        else:
            # Check which nodes become ready
            for check_node_id, check_node_data in workflow_graph.items():
                if check_node_id in completed_nodes:
                    continue
                    
                node_inputs = check_node_data.get('inputs', [])
                if not node_inputs:
                    continue
                    
                # Check if all inputs are satisfied
                all_satisfied = True
                for input_conn in node_inputs:
                    if input_conn['from_node'] not in completed_nodes:
                        all_satisfied = False
                        break
                        
                if all_satisfied:
                    ready_nodes.add(check_node_id)
    
    print("Simulated execution order:")
    for i, (node_id, node_type) in enumerate(execution_order):
        print(f"  {i+1}. {node_id} ({node_type})")
    
    # Check for unreachable nodes
    all_nodes = set(workflow_graph.keys())
    executed_nodes = set(node_id for node_id, _ in execution_order)
    unreachable = all_nodes - executed_nodes
    
    if unreachable:
        print(f"\n=== POTENTIALLY UNREACHABLE NODES ===")
        for node_id in unreachable:
            node_data = workflow_graph[node_id]
            print(f"Unreachable: {node_id} (type: {node_data['type']})")
            inputs = node_data.get('inputs', [])
            print(f"  Required inputs: {inputs}")

if __name__ == "__main__":
    analyze_workflow_execution()