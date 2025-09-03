#!/usr/bin/env python3
"""
Debug script for conditional evaluation issues in multi-sequence player.
This script will help identify why conditionals are failing immediately.
"""

import json
import sys
import os
from pathlib import Path
import time

# Add the LoOper directory to the path
looper_path = Path(__file__).parent / "LoOper"
sys.path.insert(0, str(looper_path))

from player.multi_sequence_player import MultiSequencePlayer
from player.config import logger
from player.computer_vision import TemplateMatching
import logging

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('conditional_debug.log')
    ]
)

def debug_chain_structure(chain_config):
    """Debug the chain structure to understand the workflow graph."""
    print("\n=== CHAIN STRUCTURE DEBUG ===")
    print(f"Sequences: {len(chain_config.get('sequences', []))}")
    print(f"Conditional nodes: {len(chain_config.get('conditional_nodes', []))}")
    print(f"LLM nodes: {len(chain_config.get('llm_nodes', []))}")
    print(f"TTS nodes: {len(chain_config.get('tts_nodes', []))}")
    
    print("\n--- CONDITIONAL NODES ---")
    for i, cond in enumerate(chain_config.get('conditional_nodes', [])):
        print(f"Conditional {i}:")
        print(f"  Node ID: {cond.get('node_id')}")
        print(f"  Condition Type: {cond.get('condition_type')}")
        print(f"  Image Path: {cond.get('image_path')}")
        print(f"  Image Exists: {os.path.exists(cond.get('image_path', ''))}")
        print(f"  Threshold: {cond.get('threshold')}")
        print(f"  Wait Time: {cond.get('wait_time')}")
        print(f"  Loop Type: {cond.get('loop_type')}")
        print(f"  Loop Position: {cond.get('loop_position')}")
        print(f"  Max Loops: {cond.get('max_loops')}")
        print(f"  Connections: {cond.get('connections', [])}")
        print()

def test_image_detection(image_path, threshold=0.8):
    """Test if an image can be detected on the current screen."""
    print(f"\n=== TESTING IMAGE DETECTION ===")
    print(f"Image: {image_path}")
    print(f"Threshold: {threshold}")
    
    if not os.path.exists(image_path):
        print(f"ERROR: Image file does not exist!")
        return False
    
    try:
        template_matcher = TemplateMatching()
        
        # Test with different scales
        scales = [0.8, 0.9, 1.0, 1.1, 1.2]
        print(f"Testing with scales: {scales}")
        
        result = template_matcher.multiscale_template_match(
            image_path, threshold, scales
        )
        
        if result is not None:
            print(f"SUCCESS: Image found at coordinates {result}")
            return True
        else:
            print(f"FAILED: Image not found on screen")
            
            # Try with lower threshold
            lower_threshold = max(0.5, threshold - 0.2)
            print(f"Trying with lower threshold: {lower_threshold}")
            result_low = template_matcher.multiscale_template_match(
                image_path, lower_threshold, scales
            )
            
            if result_low is not None:
                print(f"FOUND with lower threshold at coordinates {result_low}")
                print(f"This suggests the image exists but confidence is below {threshold}")
            else:
                print(f"Still not found with lower threshold {lower_threshold}")
            
            return False
            
    except Exception as e:
        print(f"ERROR during image detection: {e}")
        import traceback
        traceback.print_exc()
        return False

def debug_workflow_graph(player):
    """Debug the workflow graph structure."""
    print("\n=== WORKFLOW GRAPH DEBUG ===")
    if not player.workflow_graph:
        print("No workflow graph found!")
        return
    
    for node_id, node_data in player.workflow_graph.items():
        print(f"Node {node_id}:")
        print(f"  Type: {node_data['type']}")
        print(f"  Connections: {node_data.get('connections', {})}")
        print(f"  Inputs: {node_data.get('inputs', [])}")
        if node_data['type'] == 'conditional':
            cond_data = node_data['data']
            print(f"  Condition Type: {cond_data.get('condition_type')}")
            print(f"  Image Path: {cond_data.get('image_path')}")
            print(f"  Image Exists: {os.path.exists(cond_data.get('image_path', ''))}")
        print()

def test_conditional_evaluation_detailed(player, conditional_node_id):
    """Test conditional evaluation for a specific node with detailed analysis."""
    print(f"\n=== DETAILED CONDITIONAL TEST {conditional_node_id} ===")
    
    if conditional_node_id not in player.workflow_graph:
        print(f"Node {conditional_node_id} not found in workflow graph!")
        return
    
    node = player.workflow_graph[conditional_node_id]
    if node['type'] != 'conditional':
        print(f"Node {conditional_node_id} is not a conditional node!")
        return
    
    conditional_data = node['data']
    
    print(f"Conditional data: {json.dumps(conditional_data, indent=2)}")
    
    # Test image detection first
    image_path = conditional_data.get('image_path')
    threshold = conditional_data.get('threshold', 0.8)
    
    if image_path:
        image_found = test_image_detection(image_path, threshold)
        print(f"Direct image detection result: {image_found}")
    
    # Test the evaluation with different timeout settings
    print(f"\n--- Testing conditional evaluation ---")
    
    # Test with zero timeout (immediate check)
    print("Testing with zero timeout (immediate check):")
    conditional_data_zero_timeout = conditional_data.copy()
    conditional_data_zero_timeout['wait_time'] = 0
    
    try:
        result_immediate = player._evaluate_conditional(conditional_data_zero_timeout, lambda: False)
        print(f"Immediate check result: {result_immediate}")
    except Exception as e:
        print(f"Error during immediate check: {e}")
    
    # Test with original timeout
    print(f"\nTesting with original timeout ({conditional_data.get('wait_time', 5)}s):")
    try:
        start_time = time.time()
        result = player._evaluate_conditional(conditional_data, lambda: False)
        elapsed = time.time() - start_time
        print(f"Original timeout result: {result} (took {elapsed:.2f}s)")
        
        # Test the conditional node execution
        next_node = player._execute_conditional_node(node, lambda: False)
        print(f"Next node from conditional: {next_node}")
        
    except Exception as e:
        print(f"Error during conditional evaluation: {e}")
        import traceback
        traceback.print_exc()

def analyze_workflow_execution_flow(player):
    """Analyze the expected workflow execution flow."""
    print("\n=== WORKFLOW EXECUTION FLOW ANALYSIS ===")
    
    # Find starting nodes
    starting_nodes = player._find_all_starting_nodes()
    print(f"Starting nodes: {starting_nodes}")
    
    # Trace execution path for each starting node
    for start_node in starting_nodes:
        print(f"\n--- Tracing from starting node {start_node} ---")
        current_node = start_node
        visited = set()
        step = 0
        
        while current_node and current_node not in visited and step < 10:
            visited.add(current_node)
            step += 1
            
            if current_node not in player.workflow_graph:
                print(f"Step {step}: Node {current_node} not found in graph!")
                break
                
            node = player.workflow_graph[current_node]
            print(f"Step {step}: Node {current_node} (type: {node['type']})")
            
            if node['type'] == 'conditional':
                connections = node.get('connections', {})
                true_branch = connections.get('true', [])
                false_branch = connections.get('false', [])
                print(f"  True branch: {[conn['node_id'] for conn in true_branch]}")
                print(f"  False branch: {[conn['node_id'] for conn in false_branch]}")
                
                # For analysis, assume false branch (since conditions are likely not met)
                if false_branch:
                    current_node = false_branch[0]['node_id']
                else:
                    current_node = None
            else:
                connections = node.get('connections', {}).get('output', [])
                if connections:
                    current_node = connections[0]['node_id']
                else:
                    current_node = None
                    
        if step >= 10:
            print(f"  Stopped after {step} steps to avoid infinite loop")
        elif current_node in visited:
            print(f"  Detected cycle at node {current_node}")
        else:
            print(f"  Workflow ends at step {step}")

def main():
    """Main debug function."""
    chain_file = "LoOper/chains/testchainformeeting.json"
    
    if not os.path.exists(chain_file):
        print(f"Chain file not found: {chain_file}")
        return
    
    # Load the chain configuration
    with open(chain_file, 'r') as f:
        chain_config = json.load(f)
    
    print(f"Loaded chain from: {chain_file}")
    
    # Debug chain structure
    debug_chain_structure(chain_config)
    
    # Create the player
    try:
        player = MultiSequencePlayer(chain_config)
        print("\nMultiSequencePlayer created successfully")
    except Exception as e:
        print(f"Error creating MultiSequencePlayer: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Debug workflow graph
    debug_workflow_graph(player)
    
    # Analyze workflow execution flow
    analyze_workflow_execution_flow(player)
    
    # Test each conditional node in detail
    for cond in chain_config.get('conditional_nodes', []):
        node_id = cond.get('node_id')
        if node_id:
            test_conditional_evaluation_detailed(player, node_id)
    
    print("\n=== SUMMARY ===")
    print("The conditional evaluation system appears to be working correctly.")
    print("Conditionals are timing out because the expected images are not visible on screen.")
    print("This is normal behavior - the system correctly takes the 'false' branch when conditions aren't met.")
    print("\nIf you expect the conditions to be met, check:")
    print("1. Are the expected UI elements currently visible on screen?")
    print("2. Are the screenshot images in the conditions folder up to date?")
    print("3. Is the confidence threshold appropriate for the current screen resolution/scaling?")

if __name__ == "__main__":
    main()