
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../LoOper')))

from player.multi_sequence.workflow_graph import WorkflowExecutor, WorkflowGraphBuilder

class TestLLMTTSConnection(unittest.TestCase):
    def setUp(self):
        # Mock executors
        self.mock_llm_executor = MagicMock()
        self.mock_sequence_executor = MagicMock()
        self.mock_fallback_handler = MagicMock()
        
        # Setup LLM executor variables
        self.mock_llm_executor.variables = {}
        self.mock_llm_executor.get_all_variables.side_effect = lambda: self.mock_llm_executor.variables
        
        # Setup execute_llm_node to mimic behavior
        def side_effect_execute_llm(node, stop_flag, action_handlers):
            # Check if write_text is passed correctly
            llm_data = node.get('data', {})
            llm_config = llm_data.get('llm_configuration', {}) or llm_data
            self.last_write_text = llm_config.get('write_text')
            
            # Simulate output
            output_var = llm_config.get('output_variable', 'llm_output')
            self.mock_llm_executor.variables[output_var] = "Hello from LLM"
            
            # Return next node id
            connections = node.get('connections', {}).get('output', [])
            if connections:
                return connections[0]['node_id']
            return None
            
        self.mock_llm_executor.execute_llm_node.side_effect = side_effect_execute_llm
        
        self.mock_llm_executor.execute_tts_standalone = MagicMock()
        self.mock_llm_executor.execute_tts_standalone.return_value = {'success': True}

    def test_llm_to_tts_redirection(self):
        # Create a graph with LLM -> TTS
        llm_node = {
            "id": "llm1",
            "prompt": "Say hello",
            "write_text": True,
            "output_variable": "my_output",
            "connections": [{"target_node_id": "tts1", "output_port": "output"}]
        }
        
        tts_node = {
            "id": "tts1",
            "type": "tts",
            "text": "Original Text",
            "connections": []
        }
        
        builder = WorkflowGraphBuilder(
            llm_nodes=[llm_node],
            tts_nodes=[tts_node]
        )
        graph = builder.build_workflow_graph()
        
        executor = WorkflowExecutor(
            graph,
            self.mock_sequence_executor,
            self.mock_llm_executor,
            self.mock_fallback_handler
        )
        
        # Run execution for the LLM node
        # We manually call _execute_llm_node to test the specific logic
        llm_graph_node = graph["llm1"]
        next_node = executor._execute_llm_node(llm_graph_node, None)
        
        # Verify write_text was disabled in the call to execute_llm_node
        self.assertFalse(self.last_write_text, "write_text should be False when connected to TTS")
        
        # Verify TTS node text was updated
        tts_graph_node = graph["tts1"]
        self.assertEqual(tts_graph_node['data']['text'], "{my_output}", "TTS text should be updated to variable")
        
        # Verify next node is TTS
        self.assertEqual(next_node, "tts1")

    def test_llm_no_tts_connection(self):
        # Create a graph with LLM -> Nothing
        llm_node = {
            "id": "llm1",
            "prompt": "Say hello",
            "write_text": True,
            "connections": []
        }
        
        builder = WorkflowGraphBuilder(
            llm_nodes=[llm_node]
        )
        graph = builder.build_workflow_graph()
        
        executor = WorkflowExecutor(
            graph,
            self.mock_sequence_executor,
            self.mock_llm_executor,
            self.mock_fallback_handler
        )
        
        llm_graph_node = graph["llm1"]
        executor._execute_llm_node(llm_graph_node, None)
        
        # Verify write_text was enabled (default)
        self.assertTrue(self.last_write_text, "write_text should remain True when not connected to TTS")

    def test_llm_write_text_false(self):
        # Create a graph with LLM -> TTS but write_text=False
        llm_node = {
            "id": "llm1",
            "prompt": "Say hello",
            "write_text": False,
            "connections": [{"target_node_id": "tts1", "output_port": "output"}]
        }
        
        tts_node = {
            "id": "tts1",
            "type": "tts",
            "text": "Original Text",
            "connections": []
        }
        
        builder = WorkflowGraphBuilder(
            llm_nodes=[llm_node],
            tts_nodes=[tts_node]
        )
        graph = builder.build_workflow_graph()
        
        executor = WorkflowExecutor(
            graph,
            self.mock_sequence_executor,
            self.mock_llm_executor,
            self.mock_fallback_handler
        )
        
        llm_graph_node = graph["llm1"]
        executor._execute_llm_node(llm_graph_node, None)
        
        # Verify write_text was False (as configured)
        self.assertFalse(self.last_write_text)
        
        # Verify TTS node text was NOT updated
        tts_graph_node = graph["tts1"]
        self.assertEqual(tts_graph_node['data']['text'], "Original Text", "TTS text should not change if write_text is False")

if __name__ == '__main__':
    unittest.main()
