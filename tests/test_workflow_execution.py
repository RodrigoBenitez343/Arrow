
import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../LoOper')))

from player.multi_sequence.workflow_graph import WorkflowExecutor, WorkflowGraphBuilder

class TestWorkflowExecution(unittest.TestCase):
    def setUp(self):
        # Mock executors
        self.mock_llm_executor = MagicMock()
        self.mock_sequence_executor = MagicMock()
        self.mock_fallback_handler = MagicMock()
        
        # Setup mock behavior
        self.mock_llm_executor.execute_tts_standalone.return_value = {'success': True, 'message': 'Spoken'}
        self.mock_llm_executor.variables = {}
        self.mock_llm_executor.get_all_variables.return_value = {}

    def test_tts_end_of_workflow(self):
        # Create a graph with just a TTS node
        tts_node = {
            "id": "tts1",
            "type": "tts",
            "data": {
                "id": "tts1",
                "text": "Hello",
                "connections": []
            },
            "connections": []
        }
        
        # Manually build the graph dict to skip builder logic for simplicity
        graph = {
            "tts1": tts_node
        }
        
        executor = WorkflowExecutor(
            graph,
            self.mock_sequence_executor,
            self.mock_llm_executor,
            self.mock_fallback_handler
        )
        
        # Mock _find_all_starting_nodes to return our node
        executor._find_all_starting_nodes = MagicMock(return_value=["tts1"])
        
        # Mock dependency check
        executor._are_all_dependencies_satisfied = MagicMock(return_value=True)
        
        # Execute workflow
        result = executor.execute_workflow()
        
        # Verify success
        self.assertTrue(result, "Workflow should succeed even if TTS is the last node")
        
        # Verify TTS execution
        self.mock_llm_executor.execute_tts_standalone.assert_called_once()

    def test_llm_to_tts_workflow(self):
        # Create LLM -> TTS graph
        llm_node = {
            "id": "llm1",
            "type": "llm",
            "data": {
                "id": "llm1",
                "llm_configuration": {
                    "write_text": True,
                    "output_variable": "llm_out"
                }
            },
            "connections": {"output": [{"node_id": "tts1"}]}
        }
        
        tts_node = {
            "id": "tts1",
            "type": "tts",
            "data": {
                "id": "tts1",
                "text": "Placeholder",
            },
            "inputs": [{"from_node": "llm1"}],
            "connections": {}
        }
        
        graph = {
            "llm1": llm_node,
            "tts1": tts_node
        }
        
        executor = WorkflowExecutor(
            graph,
            self.mock_sequence_executor,
            self.mock_llm_executor,
            self.mock_fallback_handler
        )
        
        # Mock starting nodes
        executor._find_all_starting_nodes = MagicMock(return_value=["llm1"])
        
        # Mock dependency check logic
        # First call for LLM: True
        # Second call for TTS: True (after LLM completes)
        executor._are_all_dependencies_satisfied = MagicMock(side_effect=lambda node_id, completed: True)
        
        # Mock LLM execution
        # execute_llm_node returns next node ID
        self.mock_llm_executor.execute_llm_node.return_value = "tts1"
        
        # Execute workflow
        result = executor.execute_workflow()
        
        self.assertTrue(result, "Workflow should succeed")
        
        # Verify LLM execution called with modified node
        call_args = self.mock_llm_executor.execute_llm_node.call_args
        executed_node = call_args[0][0]
        self.assertFalse(executed_node['data']['llm_configuration']['write_text'], "write_text should be disabled")
        
        # Verify TTS node text updated in graph
        self.assertEqual(graph['tts1']['data']['text'], "{llm_out}")

if __name__ == '__main__':
    unittest.main()
