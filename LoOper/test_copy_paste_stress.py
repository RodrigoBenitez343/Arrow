#!/usr/bin/env python3
"""
Stress test for copy/paste functionality to reproduce crashes.
This test simulates repeated copy/paste operations to identify memory leaks or infinite loops.
"""

import sys
import os
import time
import traceback
import gc

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_copy_paste_stress():
    """Test copy/paste functionality under stress conditions."""
    print("Starting copy/paste stress test...")
    
    try:
        from PyQt5.QtWidgets import QApplication
        from PyQt5.QtCore import QTimer
        from NGUI.graph_view import GraphViewWidget
        from NGUI.graph_elements.node_operations import NodeOperations
        
        # Create QApplication if it doesn't exist
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        # Create a test graph view
        graph_view = GraphViewWidget()
        
        # Test parameters
        max_iterations = 50
        nodes_per_test = 3
        
        print(f"Testing {max_iterations} iterations with {nodes_per_test} nodes each...")
        
        for iteration in range(max_iterations):
            print(f"Iteration {iteration + 1}/{max_iterations}")
            
            try:
                # Clear any existing selection
                if hasattr(graph_view, 'node_graph') and graph_view.node_graph:
                    graph_view.node_graph.clear_selection()
                
                # Create some test nodes
                test_nodes = []
                for i in range(nodes_per_test):
                    if hasattr(graph_view, 'node_operations'):
                        # Create different types of nodes
                        if i % 3 == 0:
                            node = graph_view.node_operations.add_llm_node([100 + i*50, 100])
                        elif i % 3 == 1:
                            node = graph_view.node_operations.add_conditional_node([100 + i*50, 100])
                        else:
                            node = graph_view.node_operations.add_tts_node([100 + i*50, 100])
                        
                        if node:
                            test_nodes.append(node)
                            node.set_selected(True)
                
                # Test copy operation
                if test_nodes:
                    print(f"  Copying {len(test_nodes)} nodes...")
                    graph_view.copy_selected_nodes()
                    
                    # Check memory usage of copied_nodes
                    if hasattr(graph_view, 'copied_nodes'):
                        copied_count = len(graph_view.copied_nodes)
                        print(f"  Copied nodes in memory: {copied_count}")
                        
                        # Test multiple paste operations
                        for paste_iter in range(5):
                            print(f"    Paste iteration {paste_iter + 1}/5")
                            graph_view.paste_copied_nodes()
                            
                            # Force garbage collection
                            gc.collect()
                            
                            # Check for memory growth
                            if hasattr(graph_view, 'node_graph') and graph_view.node_graph:
                                total_nodes = len(graph_view.node_graph.all_nodes())
                                print(f"    Total nodes in graph: {total_nodes}")
                                
                                # Prevent excessive node accumulation
                                if total_nodes > 100:
                                    print("    Clearing graph to prevent memory overflow...")
                                    graph_view.clear_graph()
                
                # Clear the graph periodically
                if iteration % 10 == 9:
                    print("  Clearing graph for memory management...")
                    if hasattr(graph_view, 'clear_graph'):
                        graph_view.clear_graph()
                    
                    # Clear copied nodes
                    if hasattr(graph_view, 'copied_nodes'):
                        graph_view.copied_nodes = []
                    
                    gc.collect()
                
            except Exception as e:
                print(f"  ERROR in iteration {iteration + 1}: {str(e)}")
                print(f"  Traceback: {traceback.format_exc()}")
                return False
        
        print("Stress test completed successfully!")
        return True
        
    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return False

if __name__ == "__main__":
    success = test_copy_paste_stress()
    if success:
        print("✓ Copy/paste stress test passed")
        sys.exit(0)
    else:
        print("✗ Copy/paste stress test failed")
        sys.exit(1)