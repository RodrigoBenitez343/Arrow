#!/usr/bin/env python3
"""
Test script to verify LLM node dependencies are correctly built in the workflow graph.
"""

import sys
import os
import json

# Add the LoOper directory to the path
looper_path = os.path.join(os.path.dirname(__file__), 'LoOper')
sys.path.insert(0, looper_path)

from LoOper.player.multi_sequence.workflow_graph import WorkflowGraphBuilder

def test_llm_dependencies():
    """Test that LLM node dependencies are correctly built from TTS connections."""
    
    # Load the test chain
    chain_file = os.path.join(os.path.dirname(__file__), 'LoOper', 'chains', 'testchainformeeting.json')
    
    try:
        with open(chain_file, 'r') as f:
            chain_data = json.load(f)
    except FileNotFoundError:
        print(f"Chain file not found: {chain_file}")
        return
    
    # Extract components
    sequences = chain_data.get('sequences', [])
    conditional_nodes = chain_data.get('conditional_nodes', [])
    llm_nodes = chain_data.get('llm_nodes', [])
    tts_nodes = chain_data.get('tts_nodes', [])
    
    print(f"Loaded chain with:")
    print(f"  - {len(sequences)} sequences")
    print(f"  - {len(conditional_nodes)} conditional nodes")
    print(f"  - {len(llm_nodes)} LLM nodes")
    print(f"  - {len(tts_nodes)} TTS nodes")
    
    # Build workflow graph
    graph_builder = WorkflowGraphBuilder(
        sequences=sequences,
        conditional_nodes=conditional_nodes,
        llm_nodes=llm_nodes,
        tts_nodes=tts_nodes
    )
    
    workflow_graph = graph_builder.build_workflow_graph()
    
    print("\n=== WORKFLOW GRAPH ANALYSIS ===")
    
    # Find the LLM node
    llm_node_id = None
    for node_id, node in workflow_graph.items():
        if node['type'] == 'llm':
            llm_node_id = node_id
            break
    
    if llm_node_id:
        print(f"\nLLM Node ID: {llm_node_id}")
        llm_node = workflow_graph[llm_node_id]
        
        # Check inputs
        inputs = llm_node.get('inputs', [])
        print(f"LLM Node Inputs: {inputs}")
        
        if inputs:
            print("✓ LLM node has input dependencies - it will wait for prerequisite nodes")
            for input_dep in inputs:
                from_node = input_dep['from_node']
                output_type = input_dep['output_type']
                from_node_data = workflow_graph.get(from_node, {})
                from_node_type = from_node_data.get('type', 'unknown')
                print(f"  - Depends on {from_node} (type: {from_node_type}, output: {output_type})")
        else:
            print("⚠ LLM node has NO input dependencies - it may execute immediately!")
    else:
        print("No LLM node found in workflow graph")
    
    # Find TTS nodes that connect to LLM
    print("\n=== TTS TO LLM CONNECTIONS ===")
    for node_id, node in workflow_graph.items():
        if node['type'] == 'tts':
            connections = node.get('connections', {})
            for output_type, output_connections in connections.items():
                for conn in output_connections:
                    target_id = conn['node_id']
                    if target_id == llm_node_id:
                        tts_data = node['data']
                        tts_text = tts_data.get('text', 'Unknown')
                        print(f"TTS Node {node_id} ('{tts_text}') connects to LLM node")
    
    # Show all nodes with no inputs (starting nodes)
    print("\n=== STARTING NODES (No Input Dependencies) ===")
    for node_id, node in workflow_graph.items():
        inputs = node.get('inputs', [])
        if not inputs:
            node_type = node['type']
            if node_type == 'sequence':
                name = node['data'].get('name', 'Unknown')
                print(f"  - {node_id} (sequence: {name})")
            elif node_type == 'tts':
                text = node['data'].get('text', 'Unknown')
                print(f"  - {node_id} (TTS: '{text}')")
            elif node_type == 'llm':
                print(f"  - {node_id} (LLM node - THIS SHOULD NOT BE A STARTING NODE!)")
            else:
                print(f"  - {node_id} ({node_type})")

if __name__ == '__main__':
    test_llm_dependencies()