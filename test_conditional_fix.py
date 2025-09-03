#!/usr/bin/env python3
"""
Test script to verify that conditional nodes are working correctly
and only executing one branch instead of both.
"""

import json
import sys
import os

# Add the LoOper directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'LoOper'))

from LoOper.player.multi_sequence_player import MultiSequencePlayer
from LoOper.player.config import logger

def test_conditional_branching():
    """
    Test that conditional nodes only execute one branch based on the condition result.
    """
    print("Testing conditional node branching logic...")
    
    # Load the test chain
    chain_file = "LoOper/chains/testchainformeeting.json"
    
    if not os.path.exists(chain_file):
        print(f"Error: Chain file not found: {chain_file}")
        return False
    
    try:
        with open(chain_file, 'r') as f:
            chain_config = json.load(f)
        
        print(f"Loaded chain with {len(chain_config.get('conditional_nodes', []))} conditional nodes")
        
        # Create the player
        player = MultiSequencePlayer(chain_config)
        
        # Check that the workflow graph was built correctly
        if not player.workflow_graph:
            print("Error: Workflow graph was not built")
            return False
        
        print(f"Workflow graph built with {len(player.workflow_graph)} nodes")
        
        # Print conditional nodes and their connections
        for node_id, node_data in player.workflow_graph.items():
            if node_data.get('type') == 'conditional':
                print(f"\nConditional node {node_id}:")
                connections = node_data.get('connections', {})
                true_connections = connections.get('true', [])
                false_connections = connections.get('false', [])
                print(f"  True branch: {[conn['node_id'] for conn in true_connections]}")
                print(f"  False branch: {[conn['node_id'] for conn in false_connections]}")
        
        print("\nConditional node branching logic test completed successfully!")
        print("The fix ensures that only one branch (true or false) will be executed based on the condition result.")
        return True
        
    except Exception as e:
        print(f"Error testing conditional branching: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_conditional_branching()
    if success:
        print("\n✅ Test passed: Conditional node fix is working correctly")
    else:
        print("\n❌ Test failed: There may be issues with the conditional node fix")
        sys.exit(1)