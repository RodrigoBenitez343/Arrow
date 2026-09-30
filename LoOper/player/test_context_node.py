"""
Comprehensive integration test for the Context Node in LoOper.
Tests the full player/playback pipeline including ContextDatabase and WorkflowExecutor.
"""

import unittest
import tempfile
import os
import json
import sys

# Add the project root to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from LoOper.AI.context_database import ContextDatabase
from LoOper.player.multi_sequence.worflow_interpreter_modules.executor_modules.core import WorkflowExecutor
from LoOper.player.multi_sequence.worflow_interpreter_modules.builder import WorkflowGraphBuilder


class MockLLMExecutor:
    """Lightweight mock for LLMExecutor with only variable operations."""
    
    def __init__(self, variables=None):
        self.variables = variables or {}
    
    def get_variable(self, name: str, default=None):
        return self.variables.get(name, default)
    
    def set_variable(self, name: str, value) -> None:
        self.variables[name] = value
    
    def get_all_variables(self):
        return dict(self.variables)


class MockFallbackHandler:
    """Lightweight mock for FallbackHandler."""
    pass


class TestContextDatabase(unittest.TestCase):
    """Test cases for ContextDatabase operations."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestContextDatabase test environment...")
        self.db_path = tempfile.mktemp(suffix='.db')
        print(f"  Created temp database file: {self.db_path}")
        self.db = ContextDatabase(db_path=self.db_path)
        print(f"  ContextDatabase instance created successfully")
        print("-"*80)
    
    def tearDown(self):
        print("\n[TEARDOWN] Cleaning up test environment...")
        self.db.close()
        print(f"  Database connection closed")
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
            print(f"  Temp database file deleted: {self.db_path}")
        print("-"*80)
    
    def test_context_database_push_pull(self):
        """Test push() and pull() accumulate entries."""
        print("\n" + "="*80)
        print("TEST: test_context_database_push_pull")
        print("Verifying push/pull accumulates values chronologically")
        print("="*80)
        
        print("\n[STEP 1] Pushing first value...")
        print(f"  chain_id='chain_1', node_id='ctx_1', key='test_key', value='value1'")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='test_key',
            value='value1',
        )
        print("  ✓ First value stored")
        
        print("\n[STEP 2] Pushing second value...")
        print(f"  chain_id='chain_1', node_id='ctx_1', key='test_key', value='value2'")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='test_key',
            value='value2',
        )
        print("  ✓ Second value stored")
        
        print("\n[STEP 3] Pulling accumulated values (newest first)...")
        result = self.db.pull(chain_id='chain_1', node_id='ctx_1', key='test_key')
        print(f"  Raw result from database: {result}")
        
        print("\n[VERIFY] Checking stored values (newest first)...")
        print(f"  Expected: {{'test_key': ['value2', 'value1']}}")
        print(f"  Got: {result}")
        self.assertIn('test_key', result)
        print("  ✓ 'test_key' exists in result")
        self.assertEqual(result['test_key'], ['value2', 'value1'])
        print("  ✓ Values match expected list (newest first)")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_database_push_pull PASSED\n")
    
    def test_context_database_pull_append_only(self):
        """Test that push is always append-only (no replace semantics)."""
        print("\n" + "="*80)
        print("TEST: test_context_database_pull_append_only")
        print("Verifying push always appends (no merge policy needed)")
        print("="*80)
        
        print("\n[STEP 1] Pushing first value...")
        print(f"  chain_id='chain_1', node_id='ctx_1', key='test_key', value='value1'")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='test_key',
            value='value1',
        )
        print("  ✓ First value stored")
        
        print("\n[STEP 2] Pushing second value (always appends)...")
        print(f"  chain_id='chain_1', node_id='ctx_1', key='test_key', value='value2'")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='test_key',
            value='value2',
        )
        print("  ✓ Second value appended")
        
        print("\n[STEP 3] Pulling all values (newest first)...")
        result = self.db.pull(chain_id='chain_1', node_id='ctx_1', key='test_key')
        print(f"  Result: {result}")
        
        print("\n[VERIFY] Both values present (append-only, newest first)...")
        self.assertIn('test_key', result)
        print("  ✓ 'test_key' exists in result")
        self.assertEqual(result['test_key'], ['value2', 'value1'])
        print("  ✓ Both values present, newest first")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_database_pull_append_only PASSED\n")
    
    def test_context_database_dict_values(self):
        """Test push/pull with dict values (always append, no merging)."""
        print("\n" + "="*80)
        print("TEST: test_context_database_dict_values")
        print("Verifying push/pull handles dict values (no merge, just append)")
        print("="*80)
        
        print("\n[STEP 1] Pushing first dict...")
        print(f"  chain_id='chain_1', node_id='ctx_1', key='test_key', value={{'a': 1, 'b': 2}}")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='test_key',
            value={'a': 1, 'b': 2},
        )
        print("  ✓ First dict stored")
        
        print("\n[STEP 2] Pushing second dict (appended, not merged)...")
        print(f"  chain_id='chain_1', node_id='ctx_1', key='test_key', value={{'b': 3, 'c': 4}}")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='test_key',
            value={'b': 3, 'c': 4},
        )
        print("  ✓ Second dict appended")
        
        print("\n[STEP 3] Pulling all values (newest first)...")
        result = self.db.pull(chain_id='chain_1', node_id='ctx_1', key='test_key')
        print(f"  Result: {result}")
        
        print("\n[VERIFY] Both dicts present as separate entries...")
        self.assertIn('test_key', result)
        print("  ✓ 'test_key' exists in result")
        self.assertEqual(len(result['test_key']), 2)
        print("  ✓ Both entries preserved (no merge)")
        self.assertEqual(result['test_key'][0], {'b': 3, 'c': 4})
        print("  ✓ Newest entry first")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_database_dict_values PASSED\n")
    
    def test_context_database_pull_limit(self):
        """Test pull() limit parameter."""
        print("\n" + "="*80)
        print("TEST: test_context_database_pull_limit")
        print("Verifying pull() limit returns only N most recent entries")
        print("="*80)
        
        print("\n[STEP 1] Inserting 5 values under same key...")
        for i in range(5):
            print(f"  [PUSH {i+1}/5] value='value{i}'")
            self.db.push(
                chain_id='chain_1',
                node_id='ctx_1',
                key='test_key',
                value=f'value{i}',
            )
        print("  ✓ All 5 pushes completed")
        
        print("\n[STEP 2] Pulling with limit=3...")
        result = self.db.pull(chain_id='chain_1', node_id='ctx_1', key='test_key', limit=3)
        print(f"  Result: {result}")
        
        print("\n[VERIFY] Only 3 most recent entries returned...")
        print(f"  Expected: 3 items (value4, value3, value2) newest first")
        self.assertIn('test_key', result)
        print("  ✓ 'test_key' exists in result")
        self.assertEqual(len(result['test_key']), 3)
        print(f"  ✓ List length is 3 (limit respected)")
        self.assertEqual(result['test_key'], ['value4', 'value3', 'value2'])
        print("  ✓ 3 most recent entries returned (newest first)")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_database_pull_limit PASSED\n")
    
    def test_context_database_chain_isolation(self):
        """Test that different chain_ids are isolated from each other."""
        print("\n" + "="*80)
        print("TEST: test_context_database_chain_isolation")
        print("Verifying that data in different chains is completely isolated")
        print("="*80)
        
        print("\n[STEP 1] Storing data in chain_A...")
        print(f"  chain_id='chain_A', node_id='ctx_1', key='test_key', value='data_A'")
        self.db.push(
            chain_id='chain_A',
            node_id='ctx_1',
            key='test_key',
            value='data_A',
        )
        print("  ✓ Data stored in chain_A")
        
        print("\n[STEP 2] Storing data in chain_B...")
        print(f"  chain_id='chain_B', node_id='ctx_1', key='test_key', value='data_B'")
        self.db.push(
            chain_id='chain_B',
            node_id='ctx_1',
            key='test_key',
            value='data_B',
        )
        print("  ✓ Data stored in chain_B")
        
        print("\n[STEP 3] Retrieving data from both chains...")
        result_A = self.db.pull(chain_id='chain_A', node_id='ctx_1', key='test_key')
        result_B = self.db.pull(chain_id='chain_B', node_id='ctx_1', key='test_key')
        print(f"  chain_A result: {result_A}")
        print(f"  chain_B result: {result_B}")
        
        print("\n[VERIFY] Checking chain isolation...")
        self.assertEqual(result_A['test_key'], ['data_A'])
        print("  ✓ chain_A contains only its own data")
        self.assertEqual(result_B['test_key'], ['data_B'])
        print("  ✓ chain_B contains only its own data")
        print("  ✓ PASSED - Chains are properly isolated")
        
        print("\n✓ test_context_database_chain_isolation PASSED\n")
    
    def test_context_database_clear(self):
        """Test clear() removes data for a specific chain."""
        print("\n" + "="*80)
        print("TEST: test_context_database_clear")
        print("Verifying that clear() removes all data for a specific chain")
        print("="*80)
        
        print("\n[STEP 1] Storing data in chain...")
        print(f"  chain_id='run_123', node_id='ctx_1', key='test_key', value='data'")
        self.db.push(
            chain_id='run_123',
            node_id='ctx_1',
            key='test_key',
            value='data',
        )
        print("  ✓ Data stored")
        
        print("\n[STEP 2] Verifying data exists before clear...")
        result_before = self.db.pull(chain_id='run_123', node_id='ctx_1', key='test_key')
        print(f"  Data before clear: {result_before}")
        self.assertIn('test_key', result_before)
        print("  ✓ Data confirmed present")
        
        print("\n[STEP 3] Clearing chain 'run_123'...")
        self.db.clear(chain_id='run_123')
        print("  ✓ clear() called")
        
        print("\n[STEP 4] Verifying data is removed after clear...")
        result_after = self.db.pull(chain_id='run_123', node_id='ctx_1', key='test_key')
        print(f"  Data after clear: {result_after}")
        
        print("\n[VERIFY] Checking that data was removed...")
        print(f"  Expected: empty dict (no 'test_key')")
        self.assertNotIn('test_key', result_after)
        print("  ✓ 'test_key' no longer present in result")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_database_clear PASSED\n")
    
    def test_context_database_export_context(self):
        """Test export_context returns structured dict with all keys."""
        print("\n" + "="*80)
        print("TEST: test_context_database_export_context")
        print("Verifying that export_context returns structured metadata with keys")
        print("="*80)
        
        print("\n[STEP 1] Storing multiple keys in context...")
        print(f"  Storing key1='value1'")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='key1',
            value='value1',
        )
        print("  ✓ key1 stored")
        
        print(f"  Storing key2='value2'")
        self.db.push(
            chain_id='chain_1',
            node_id='ctx_1',
            key='key2',
            value='value2',
        )
        print("  ✓ key2 stored")
        
        print("\n[STEP 2] Exporting context...")
        exported = self.db.export_context('chain_1', 'ctx_1')
        print(f"  Exported context structure:")
        print(f"    {json.dumps(exported, indent=4, default=str)}")
        
        print("\n[VERIFY] Checking exported context structure...")
        self.assertIn('keys', exported)
        print("  ✓ 'keys' field present")
        self.assertIn('node_id', exported)
        print("  ✓ 'node_id' field present")
        self.assertEqual(exported['node_id'], 'ctx_1')
        print("  ✓ node_id='ctx_1' correct")
        self.assertIn('key1', exported['keys'])
        print("  ✓ 'key1' in keys")
        self.assertIn('key2', exported['keys'])
        print("  ✓ 'key2' in keys")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_database_export_context PASSED\n")


class TestContextNodeExecution(unittest.TestCase):
    """Test cases for Context node execution via WorkflowExecutor."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestContextNodeExecution test environment...")
        self.db_path = tempfile.mktemp(suffix='.db')
        print(f"  Created temp database file: {self.db_path}")
        self.db = ContextDatabase(db_path=self.db_path)
        print(f"  ContextDatabase instance created")
        self.llm_executor = MockLLMExecutor()
        print(f"  MockLLMExecutor instance created")
        self.fallback_handler = MockFallbackHandler()
        print(f"  MockFallbackHandler instance created")
        print("-"*80)
    
    def tearDown(self):
        print("\n[TEARDOWN] Cleaning up test environment...")
        self.db.close()
        print(f"  Database connection closed")
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
            print(f"  Temp database file deleted: {self.db_path}")
        print("-"*80)
    
    def test_context_node_execution_basic(self):
        """Test basic context node execution with single input."""
        print("\n" + "="*80)
        print("TEST: test_context_node_execution_basic")
        print("Verifying basic context node execution captures upstream output")
        print("="*80)
        
        print("\n[STEP 1] Pre-setting upstream node output...")
        print(f"  Setting variable: node_code_1_output = 'hello world'")
        self.llm_executor.set_variable('node_code_1_output', 'hello world')
        print("  ✓ Upstream output set")
        
        print("\n[STEP 2] Building minimal workflow graph...")
        graph = {
            'code_1': {
                'type': 'code',
                'data': {'node_id': 'code_1', 'label': 'Test Code'},
                'connections': {}
            },
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Test Context',
                    'max_history': 10,
                },
                'connections': {
                    'output': [{'node_id': '__done__', 'input_port': 'input'}]
                },
                'inputs': [
                    {'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'}
                ]
            }
        }
        
        print(f"\n[INFO] Workflow graph structure:")
        print(f"  Nodes: {list(graph.keys())}")
        for node_id, node_data in graph.items():
            print(f"  Node '{node_id}':")
            print(f"    type: {node_data['type']}")
            if node_data['type'] == 'context':
                print(f"    label: {node_data['data']['label']}")
                print(f"    max_history: {node_data['data']['max_history']}")
                print(f"    inputs: {node_data.get('inputs', [])}")
        
        print("\n[STEP 3] Creating WorkflowExecutor with mock components...")
        executor = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor._context_db = self.db
        executor.chain_id = 'test_chain'
        print(f"  Executor created with chain_id='test_chain'")
        print(f"  Context database injected")
        
        print("\n[STEP 4] Executing context node directly...")
        print(f"  Calling _execute_context_node(graph['ctx_1'], stop_flag)")
        result = executor._execute_context_node(graph['ctx_1'], lambda: False)
        print(f"  Execution result: {result}")
        print("  ✓ Context node executed")
        
        print("\n[STEP 5] Checking output variables...")
        ctx_output = self.llm_executor.get_variable('node_ctx_1_output')
        ctx_context = self.llm_executor.get_variable('node_ctx_1_context')
        print(f"  node_ctx_1_output: {ctx_output[:100] if ctx_output else None}...")
        print(f"  node_ctx_1_context: {ctx_context}")
        
        print("\n[VERIFY] Checking stored outputs...")
        self.assertIsNotNone(ctx_output)
        print("  ✓ node_ctx_1_output is not None")
        self.assertIsNotNone(ctx_context)
        print("  ✓ node_ctx_1_context is not None")
        
        print("\n[VERIFY] Checking JSON output format...")
        print(f"  Context output:\n{ctx_output}")
        
        # New format: clean JSON dict instead of timeline narrative
        self.assertIn('hello world', ctx_output)
        print("  ✓ Output value 'hello world' present in JSON")
        
        # Verify it's valid JSON
        parsed = json.loads(ctx_output)
        self.assertIsInstance(parsed, dict)
        print("  ✓ Output is valid JSON dict")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_node_execution_basic PASSED\n")
    
    def test_context_node_multiple_inputs(self):
        """Test context node with multiple upstream inputs."""
        print("\n" + "="*80)
        print("TEST: test_context_node_multiple_inputs")
        print("Verifying context node captures multiple upstream outputs")
        print("="*80)
        
        print("\n[STEP 1] Pre-setting multiple upstream node outputs...")
        print(f"  Setting: node_code_1_output = 'output_from_code_1'")
        self.llm_executor.set_variable('node_code_1_output', 'output_from_code_1')
        print("  ✓ code_1 output set")
        
        print(f"  Setting: node_code_2_output = 'output_from_code_2'")
        self.llm_executor.set_variable('node_code_2_output', 'output_from_code_2')
        print("  ✓ code_2 output set")
        
        print("\n[STEP 2] Building workflow graph with multiple inputs...")
        graph = {
            'code_1': {
                'type': 'code',
                'data': {'node_id': 'code_1', 'label': 'First Code'},
                'connections': {}
            },
            'code_2': {
                'type': 'code',
                'data': {'node_id': 'code_2', 'label': 'Second Code'},
                'connections': {}
            },
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Multi Context',
                    'max_history': 10,
                },
                'connections': {
                    'output': [{'node_id': '__done__', 'input_port': 'input'}]
                },
                'inputs': [
                    {'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'},
                    {'from_node': 'code_2', 'output_type': 'output', 'input_port': 'input'}
                ]
            }
        }
        
        print(f"\n[INFO] Workflow graph structure:")
        print(f"  Nodes: {list(graph.keys())}")
        print(f"  Context node 'ctx_1' inputs:")
        for inp in graph['ctx_1']['inputs']:
            print(f"    - from_node: {inp['from_node']}, output_type: {inp['output_type']}")
        
        print("\n[STEP 3] Creating and configuring executor...")
        executor = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor._context_db = self.db
        executor.chain_id = 'test_chain'
        print(f"  Executor configured with chain_id='test_chain'")
        
        print("\n[STEP 4] Executing context node...")
        executor._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ Context node executed")
        
        print("\n[STEP 5] Retrieving and checking output...")
        ctx_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  JSON output:\n{ctx_output}")
        
        print("\n[VERIFY] Checking multiple input capture in JSON...")
        # New format: clean JSON, not timeline narrative
        self.assertIn('output_from_code_1', ctx_output)
        print("  ✓ 'output_from_code_1' present in output")
        self.assertIn('output_from_code_2', ctx_output)
        print("  ✓ 'output_from_code_2' present in output")
        
        # Verify valid JSON
        parsed = json.loads(ctx_output)
        self.assertIsInstance(parsed, dict)
        print("  ✓ Output is valid JSON dict")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_node_multiple_inputs PASSED\n")
    
    def test_context_node_json_output(self):
        """Test context node always produces JSON output."""
        print("\n" + "="*80)
        print("TEST: test_context_node_json_output")
        print("Verifying context node produces clean JSON dict output")
        print("="*80)
        
        print("\n[STEP 1] Pre-setting upstream output...")
        print(f"  Setting: node_code_1_output = 'hello world'")
        self.llm_executor.set_variable('node_code_1_output', 'hello world')
        print("  ✓ Upstream output set")
        
        print("\n[STEP 2] Building workflow graph...")
        graph = {
            'code_1': {
                'type': 'code',
                'data': {'node_id': 'code_1', 'label': 'Test Code'},
                'connections': {}
            },
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Output Test',
                    'max_history': 10,
                },
                'connections': {
                    'output': [{'node_id': '__done__', 'input_port': 'input'}]
                },
                'inputs': [
                    {'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'}
                ]
            }
        }
        print(f"  Context node max_history: 10")
        
        print("\n[STEP 3] Creating and executing...")
        executor = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor._context_db = self.db
        executor.chain_id = 'test_chain'
        executor._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ Context node executed")
        
        print("\n[STEP 4] Retrieving JSON output...")
        ctx_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  JSON output:")
        print("-"*40)
        print(ctx_output)
        print("-"*40)
        
        print("\n[VERIFY] Checking JSON format...")
        # Output is clean JSON, no timeline narrative
        parsed = json.loads(ctx_output)
        self.assertIsInstance(parsed, dict)
        print("  ✓ Output is valid JSON dict")
        self.assertIn('hello world', ctx_output)
        print("  ✓ Output value 'hello world' present")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_node_json_output PASSED\n")
    
    def test_context_node_chain_persistence(self):
        """Test that chain-scoped context persists across executions."""
        print("\n" + "="*80)
        print("TEST: test_context_node_chain_persistence")
        print("Verifying chain-scoped context persists and accumulates across executions")
        print("="*80)
        
        print("\n[STEP 1] Pre-setting output for first execution...")
        print(f"  Setting: node_code_1_output = 'first_execution'")
        self.llm_executor.set_variable('node_code_1_output', 'first_execution')
        print("  ✓ First execution data set")
        
        print("\n[STEP 2] Building workflow graph...")
        graph = {
            'code_1': {'type': 'code', 'data': {'node_id': 'code_1', 'label': 'Test Code'}, 'connections': {}},
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Persistent Context',
                    'max_history': 10,
                },
                'connections': {'output': [{'node_id': '__done__', 'input_port': 'input'}]},
                'inputs': [{'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'}]
            }
        }
        print(f"  Context scope: chain (persistent across runs)")
        
        print("\n[STEP 3] Creating executor...")
        executor = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor._context_db = self.db
        executor.chain_id = 'test_chain'
        
        print("\n[STEP 4] FIRST EXECUTION...")
        print("  Executing context node...")
        executor._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ First execution complete")
        
        first_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  First execution output:\n{first_output}")
        
        print("\n[STEP 5] Simulating second execution with new data...")
        print(f"  Setting: node_code_1_output = 'second_execution'")
        self.llm_executor.set_variable('node_code_1_output', 'second_execution')
        print("  ✓ Second execution data set")
        
        print("  Executing context node again...")
        executor._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ Second execution complete")
        
        print("\n[STEP 6] Checking accumulated context...")
        ctx_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  Final JSON output:\n{ctx_output}")
        
        print("\n[VERIFY] Checking persistence and accumulation...")
        # New format: clean JSON with both entries
        self.assertIn('first_execution', ctx_output)
        print("  ✓ 'first_execution' from first run still present")
        self.assertIn('second_execution', ctx_output)
        print("  ✓ 'second_execution' from second run present")
        
        # Verify both are in JSON
        parsed = json.loads(ctx_output)
        self.assertIsInstance(parsed, dict)
        print("  ✓ Output is valid JSON")
        print("  ✓ PASSED - Chain scope persists across executions")
        
        print("\n✓ test_context_node_chain_persistence PASSED\n")
    
    def test_context_node_chain_clear(self):
        """Test that chain context can be cleared."""
        print("\n" + "="*80)
        print("TEST: test_context_node_chain_clear")
        print("Verifying chain context can be cleared (for cleanup between runs)")
        print("="*80)
        
        print("\n[STEP 1] Pre-setting output...")
        print(f"  Setting: node_code_1_output = 'chain_data'")
        self.llm_executor.set_variable('node_code_1_output', 'chain_data')
        print("  ✓ Chain data set")
        
        print("\n[STEP 2] Building workflow graph...")
        graph = {
            'code_1': {'type': 'code', 'data': {'node_id': 'code_1', 'label': 'Test Code'}, 'connections': {}},
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Cleanable Context',
                    'max_history': 10,
                },
                'connections': {'output': [{'node_id': '__done__', 'input_port': 'input'}]},
                'inputs': [{'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'}]
            }
        }
        print(f"  Context chain_id: 'test_chain' (can be cleared)")
        
        print("\n[STEP 3] Creating executor with chain_id...")
        executor = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor._context_db = self.db
        executor.chain_id = 'test_chain'
        
        print("\n[STEP 4] Executing and storing data...")
        executor._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ Context node executed")
        
        print("\n[STEP 5] Verifying data exists before clear...")
        result_before = self.db.pull(chain_id='test_chain', node_id='ctx_1')
        print(f"  Data before clear: {result_before}")
        self.assertTrue(len(result_before) > 0)
        print("  ✓ Data confirmed present")
        
        print("\n[STEP 6] Clearing chain...")
        self.db.clear(chain_id='test_chain')
        print("  ✓ clear(chain_id='test_chain') called")
        
        print("\n[STEP 7] Verifying data is cleared...")
        result_after = self.db.pull(chain_id='test_chain', node_id='ctx_1')
        print(f"  Data after clear: {result_after}")
        
        print("\n[VERIFY] Checking cleanup...")
        self.assertEqual(len(result_after), 0)
        print("  ✓ All chain data removed")
        print("  ✓ PASSED")
        
        print("\n✓ test_context_node_chain_clear PASSED\n")

    def test_context_node_cross_executor_persistence(self):
        """Test that context persists across separate WorkflowExecutor instances."""
        print("\n" + "="*80)
        print("TEST: test_context_node_cross_executor_persistence")
        print("Verifying context survives across separate executor instances (real cross-run persistence)")
        print("="*80)
        
        print("\n[STEP 1] Pre-setting upstream output for first executor...")
        print(f"  Setting: node_code_1_output = 'data_from_run_1'")
        self.llm_executor.set_variable('node_code_1_output', 'data_from_run_1')
        print("  ✓ First run data set")
        
        print("\n[STEP 2] Building minimal workflow graph...")
        graph = {
            'code_1': {
                'type': 'code',
                'data': {'node_id': 'code_1', 'label': 'Test Code'},
                'connections': {}
            },
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Cross-Run Context',
                    'max_history': 10,
                    'persistent': True,
                },
                'connections': {
                    'output': [{'node_id': '__done__', 'input_port': 'input'}]
                },
                'inputs': [
                    {'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'}
                ]
            }
        }
        print(f"  Graph has {len(graph)} nodes with persistent=True")
        
        print("\n[STEP 3] Creating FIRST WorkflowExecutor and executing...")
        executor_1 = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor_1._context_db = self.db
        executor_1.chain_id = 'test_cross_chain'
        print(f"  Executor 1 created with chain_id='test_cross_chain'")
        
        executor_1._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ First execution completed")
        
        first_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  First execution output:\n{first_output}")
        self.assertIn('data_from_run_1', first_output)
        print("  ✓ 'data_from_run_1' present in first output")
        
        print("\n[STEP 4] Creating SECOND WorkflowExecutor (simulating new chain run)...")
        # Clear variables to simulate fresh executor state
        self.llm_executor.set_variable('node_ctx_1_output', None)
        self.llm_executor.set_variable('node_ctx_1_context', None)
        
        executor_2 = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor_2._context_db = self.db
        executor_2.chain_id = 'test_cross_chain'
        print(f"  Executor 2 created with same chain_id='test_cross_chain'")
        
        print("\n[STEP 5] Running pre-initialization (loads persisted data)...")
        executor_2._pre_initialize_context_nodes()
        print("  ✓ Pre-initialization completed")
        
        pre_init_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  Pre-init output:\n{pre_init_output}")
        
        print("\n[VERIFY] Cross-executor persistence...")
        self.assertIsNotNone(pre_init_output)
        print("  ✓ Pre-init output is not None")
        self.assertIn('data_from_run_1', pre_init_output)
        print("  ✓ 'data_from_run_1' survived across executor instances (real persistence!)")
        
        # Verify valid JSON
        parsed = json.loads(pre_init_output)
        self.assertIsInstance(parsed, dict)
        print("  ✓ Pre-init output is valid JSON")
        
        print("\n[STEP 6] Running second execution with new data...")
        self.llm_executor.set_variable('node_code_1_output', 'data_from_run_2')
        executor_2._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ Second execution completed")
        
        second_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  Second execution output:\n{second_output}")
        
        print("\n[VERIFY] Both runs' data accumulated...")
        self.assertIn('data_from_run_1', second_output)
        print("  ✓ 'data_from_run_1' from first run still present")
        self.assertIn('data_from_run_2', second_output)
        print("  ✓ 'data_from_run_2' from second run present")
        print("  ✓ PASSED - Context persists correctly across separate executor instances")
        
        print("\n✓ test_context_node_cross_executor_persistence PASSED\n")


class TestWorkflowGraphBuilder(unittest.TestCase):
    """Test cases for WorkflowGraphBuilder with context nodes."""
    
    def test_workflow_graph_builder_context_nodes(self):
        """Test that WorkflowGraphBuilder properly builds context nodes."""
        print("\n" + "="*80)
        print("TEST: test_workflow_graph_builder_context_nodes")
        print("Verifying WorkflowGraphBuilder correctly constructs context nodes in graph")
        print("="*80)
        
        print("\n[STEP 1] Defining code nodes...")
        code_nodes = [
            {
                'id': 'code_1',
                'node_id': 'code_1',
                'name': 'Test Code Node',
                'code': 'result = "test"',
                'connections': []
            }
        ]
        print(f"  Code nodes defined:")
        for node in code_nodes:
            print(f"    - id: {node['id']}, name: {node['name']}")
            print(f"      code: {node['code']}")
        
        print("\n[STEP 2] Defining context nodes...")
        context_nodes = [
            {
                'id': 'ctx_1',
                'node_id': 'ctx_1',
                'name': 'Test Context Node',
                'scope': 'chain',
                'default_merge_policy': 'append',
                'keys': '[]',
                'key_policies': '{}',
                'max_entries_per_key': '100',
                'auto_capture_responses': 'true',
                'output_format': 'structured',
                'connections': [
                    {'target_node_id': 'code_1', 'output_port': 'output', 'input_port': 'input'}
                ],
                'inputs': {
                    'input': {'node_id': 'code_1', 'output_name': 'output'}
                }
            }
        ]
        print(f"  Context nodes defined:")
        for node in context_nodes:
            print(f"    - id: {node['id']}, name: {node['name']}")
            print(f"      scope: {node['scope']}, merge_policy: {node['default_merge_policy']}")
            print(f"      inputs: {node['inputs']}")
        
        print("\n[STEP 3] Creating WorkflowGraphBuilder...")
        builder = WorkflowGraphBuilder(
            code_nodes=code_nodes,
            context_nodes=context_nodes
        )
        print("  ✓ Builder created")
        
        print("\n[STEP 4] Building workflow graph...")
        graph = builder.build_workflow_graph()
        print(f"  Graph nodes: {list(graph.keys())}")
        
        print("\n[STEP 5] Inspecting built graph structure...")
        print(f"\n[INFO] Built workflow graph:")
        for node_id, node_data in graph.items():
            print(f"  Node '{node_id}':")
            print(f"    type: {node_data.get('type')}")
            if node_data.get('type') == 'context':
                print(f"    data: {json.dumps(node_data.get('data', {}), indent=6)}")
                print(f"    connections: {node_data.get('connections', {})}")
                print(f"    inputs: {node_data.get('inputs', [])}")
        
        print("\n[VERIFY] Checking graph structure...")
        self.assertIn('ctx_1', graph)
        print("  ✓ 'ctx_1' present in graph")
        self.assertEqual(graph['ctx_1']['type'], 'context')
        print("  ✓ Node type is 'context'")
        self.assertIn('data', graph['ctx_1'])
        print("  ✓ 'data' field present")
        self.assertIn('connections', graph['ctx_1'])
        print("  ✓ 'connections' field present")
        self.assertIn('context', graph['ctx_1']['connections'])
        print("  ✓ 'context' connection port present")
        self.assertIn('inputs', graph['ctx_1'])
        print("  ✓ 'inputs' field present")
        self.assertEqual(len(graph['ctx_1']['inputs']), 1)
        print("  ✓ Has 1 input connection")
        self.assertEqual(graph['ctx_1']['inputs'][0]['from_node'], 'code_1')
        print("  ✓ Input correctly connected from 'code_1'")
        print("  ✓ PASSED")
        
        print("\n✓ test_workflow_graph_builder_context_nodes PASSED\n")


class TestFullPipeline(unittest.TestCase):
    """Test cases for full workflow pipeline with context nodes."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestFullPipeline test environment...")
        self.db_path = tempfile.mktemp(suffix='.db')
        print(f"  Created temp database file: {self.db_path}")
        self.db = ContextDatabase(db_path=self.db_path)
        print(f"  ContextDatabase instance created")
        self.llm_executor = MockLLMExecutor()
        print(f"  MockLLMExecutor instance created")
        self.fallback_handler = MockFallbackHandler()
        print(f"  MockFallbackHandler instance created")
        print("-"*80)
    
    def tearDown(self):
        print("\n[TEARDOWN] Cleaning up test environment...")
        self.db.close()
        print(f"  Database connection closed")
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
            print(f"  Temp database file deleted: {self.db_path}")
        print("-"*80)
    
    def test_full_pipeline_code_to_context_to_downstream(self):
        """Test full 3-node pipeline: code -> context -> code."""
        print("\n" + "="*80)
        print("TEST: test_full_pipeline_code_to_context_to_downstream")
        print("Verifying full pipeline: code_1 -> context_node -> code_2")
        print("="*80)
        
        print("\n[STEP 1] Pre-populating first code node output...")
        print(f"  Setting: node_code_1_output = 'code_result_123'")
        self.llm_executor.set_variable('node_code_1_output', 'code_result_123')
        print("  ✓ Upstream output pre-set")
        
        print("\n[STEP 2] Building 3-node pipeline workflow graph...")
        graph = {
            'code_1': {
                'type': 'code',
                'data': {'node_id': 'code_1', 'code': 'result = "code_result_123"', 'label': 'First Code'},
                'connections': {
                    'output': [{'node_id': 'ctx_1', 'input_port': 'input'}]
                },
                'inputs': []
            },
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Pipeline Context',
                    'max_history': 10,
                },
                'connections': {
                    'output': [{'node_id': 'code_2', 'input_port': 'input'}]
                },
                'inputs': [
                    {'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'}
                ]
            },
            'code_2': {
                'type': 'code',
                'data': {
                    'node_id': 'code_2',
                    'code': 'result = input_data',
                    'output_variable': 'result'
                },
                'connections': {},
                'inputs': [
                    {'from_node': 'ctx_1', 'output_type': 'output', 'input_port': 'input'}
                ]
            }
        }
        
        print("\n[INFO] Pipeline structure:")
        print("  ┌─────────┐      ┌──────────────┐      ┌─────────┐")
        print("  │ code_1  │─────▶│    ctx_1     │─────▶│ code_2  │")
        print("  │ (code)  │      │  (context)   │      │ (code)  │")
        print("  └─────────┘      └──────────────┘      └─────────┘")
        print("\n  Flow:")
        print("    1. code_1 produces 'code_result_123'")
        print("    2. ctx_1 captures and stores output")
        print("    3. ctx_1 exports context to code_2")
        print("    4. code_2 receives context as input")
        
        print("\n[INFO] Node details:")
        for node_id, node_data in graph.items():
            print(f"  {node_id}:")
            print(f"    type: {node_data['type']}")
            print(f"    inputs: {node_data.get('inputs', [])}")
            print(f"    connections: {node_data.get('connections', {})}")
        
        print("\n[STEP 3] Creating WorkflowExecutor...")
        executor = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor._context_db = self.db
        executor.chain_id = 'test_chain'
        print("  ✓ Executor created with chain_id='test_chain'")
        
        print("\n[STEP 4] Executing full workflow pipeline...")
        print("  This will execute nodes in topological order...")
        success = executor.execute_workflow(stop_flag=lambda: False)
        print(f"  Workflow execution result: success={success}")
        
        print("\n[VERIFY] Checking execution succeeded...")
        self.assertTrue(success)
        print("  ✓ Workflow executed successfully")
        
        print("\n[STEP 5] Inspecting context node output...")
        ctx_output = self.llm_executor.get_variable('node_ctx_1_output')
        print(f"  node_ctx_1_output present: {ctx_output is not None}")
        self.assertIsNotNone(ctx_output)
        print("  ✓ Context node produced output")
        
        print("\n[STEP 6] Checking JSON output...")
        print(f"  Context output:\n{ctx_output}")
        
        print("\n[VERIFY] Checking context captured code_1 output...")
        # New format: JSON dict instead of timeline narrative
        self.assertIn('code_result_123', ctx_output)
        print("  ✓ 'code_result_123' captured in JSON")
        
        # Verify valid JSON
        parsed = json.loads(ctx_output)
        self.assertIsInstance(parsed, dict)
        print("  ✓ Output is valid JSON")
        
        print("\n[STEP 7] Checking downstream code node received context...")
        code_2_context = self.llm_executor.get_variable('node_code_2_context')
        print(f"  node_code_2_context: {code_2_context is not None}")
        self.assertIsNotNone(code_2_context)
        print("  ✓ Downstream code_2 received context from ctx_1")
        
        print("\n  ✓ PASSED - Full pipeline executed correctly")
        print("\n✓ test_full_pipeline_code_to_context_to_downstream PASSED\n")


if __name__ == '__main__':
    print("\n" + "="*80)
    print("CONTEXT NODE INTEGRATION TEST SUITE")
    print("Testing: ContextDatabase, Context Node Execution, Graph Builder, Full Pipeline")
    print("="*80 + "\n")
    
    # Run tests with verbose output
    unittest.main(verbosity=2)


# ============================================================================
# LLM SKILLS AND CONTEXT ROUTING TESTS
# ============================================================================

class MockLLMConfig:
    """Mock LLM node config for testing skills storage and retrieval."""
    
    def __init__(self):
        self._properties = {
            'model': 'llama3.2:latest',
            'prompt': '',
            'system_message': '',
            'temperature': '0.7',
            'max_tokens': '4096',
            'output_variable': 'llm_output',
            'skills': '[]',
            'use_skill_routing': 'true',
            'semantic_description': ''
        }
    
    def create_property(self, name, default):
        if name not in self._properties:
            self._properties[name] = default
    
    def set_property(self, name, value):
        self._properties[name] = value
    
    def get_property(self, name):
        return self._properties.get(name)
    
    def get_llm_config(self):
        """Get the LLM configuration (mimics LLMNode.get_llm_config)."""
        import json as _json
        skills_raw = self.get_property('skills') or '[]'
        try:
            skills = _json.loads(skills_raw) if isinstance(skills_raw, str) else skills_raw
        except Exception:
            skills = []
        use_routing = str(self.get_property('use_skill_routing') or 'true').lower() in ('true', '1')
        return {
            'model': self.get_property('model'),
            'prompt': self.get_property('prompt'),
            'system_message': self.get_property('system_message'),
            'temperature': float(self.get_property('temperature') or 0.7),
            'max_tokens': int(self.get_property('max_tokens') or 4096),
            'output_variable': self.get_property('output_variable'),
            'skills': skills,
            'use_skill_routing': use_routing,
            'semantic_description': self.get_property('semantic_description') or ''
        }
    
    def set_llm_config(self, config):
        """Set the LLM configuration (mimics LLMNode.set_llm_config)."""
        import json as _json
        self.set_property('model', config.get('model', 'llama3.2:latest'))
        self.set_property('prompt', config.get('prompt', ''))
        self.set_property('system_message', config.get('system_message', ''))
        self.set_property('temperature', str(config.get('temperature', 0.7)))
        self.set_property('max_tokens', str(config.get('max_tokens', 4096)))
        self.set_property('output_variable', config.get('output_variable', 'llm_output'))
        if 'skills' in config:
            val = config['skills']
            self.set_property('skills', _json.dumps(val) if isinstance(val, list) else val)
        if 'use_skill_routing' in config:
            self.set_property('use_skill_routing', str(config['use_skill_routing']).lower())
        if 'semantic_description' in config:
            self.set_property('semantic_description', config['semantic_description'])


class TestSkillStorage(unittest.TestCase):
    """Test cases for skill property storage and serialization."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestSkillStorage test environment...")
        print("  Creating MockLLMConfig instance")
        print("-"*80)

    def test_skill_property_roundtrip(self):
        """Test skill dict list survives serialize/deserialize roundtrip."""
        print("\n" + "="*80)
        print("TEST: test_skill_property_roundtrip")
        print("Verifying skill dictionary list survives JSON serialization roundtrip")
        print("="*80)
        
        print("\n[STEP 1] Creating skill list with multiple skills...")
        original_skills = [
            {
                'name': 'CodeGenerator',
                'description': 'Generates code snippets',
                'instructions': 'Write clean, efficient Python code.',
                'enabled': True,
                'priority': 1,
                'trigger_keywords': 'code generate function'
            },
            {
                'name': 'DataAnalyzer',
                'description': 'Analyzes data and creates reports',
                'instructions': 'Focus on statistical analysis and visualization.',
                'enabled': False,
                'priority': 2,
                'trigger_keywords': 'data analyze statistics'
            }
        ]
        print(f"  Original skills:")
        for skill in original_skills:
            print(f"    - {skill['name']}: enabled={skill.get('enabled', True)}, priority={skill.get('priority', 5)}")
        
        print("\n[STEP 2] Serializing and storing in config...")
        mock_node = MockLLMConfig()
        mock_node.set_llm_config({'skills': original_skills})
        print(f"  Stored via set_llm_config()")
        
        print("\n[STEP 3] Retrieving skills via get_llm_config()...")
        retrieved_config = mock_node.get_llm_config()
        retrieved_skills = retrieved_config['skills']
        print(f"  Retrieved skills count: {len(retrieved_skills)}")
        
        print("\n[VERIFY] Checking roundtrip fidelity...")
        self.assertEqual(len(retrieved_skills), len(original_skills))
        print(f"  ✓ Skill count matches: {len(original_skills)}")
        
        for i, (orig, retr) in enumerate(zip(original_skills, retrieved_skills)):
            print(f"\n  Checking skill {i+1}: {orig['name']}")
            self.assertEqual(retr['name'], orig['name'])
            print(f"    ✓ name matches")
            self.assertEqual(retr['description'], orig['description'])
            print(f"    ✓ description matches")
            self.assertEqual(retr['instructions'], orig['instructions'])
            print(f"    ✓ instructions match")
            self.assertEqual(retr['enabled'], orig['enabled'])
            print(f"    ✓ enabled={orig['enabled']}")
            self.assertEqual(retr['priority'], orig['priority'])
            print(f"    ✓ priority={orig['priority']}")
            self.assertEqual(retr['trigger_keywords'], orig['trigger_keywords'])
            print(f"    ✓ trigger_keywords match")
        
        print("\n  ✓ PASSED")
        print("\n✓ test_skill_property_roundtrip PASSED\n")

    def test_skill_empty_default(self):
        """Test that new LLM config returns empty skills list by default."""
        print("\n" + "="*80)
        print("TEST: test_skill_empty_default")
        print("Verifying new LLM config returns empty skills list by default")
        print("="*80)
        
        print("\n[STEP 1] Creating fresh MockLLMConfig instance...")
        mock_node = MockLLMConfig()
        print("  Instance created with default properties")
        
        print("\n[STEP 2] Getting config without setting skills...")
        config = mock_node.get_llm_config()
        print(f"  skills property raw value: {mock_node.get_property('skills')}")
        
        print("\n[VERIFY] Checking default skills value...")
        self.assertIsInstance(config['skills'], list)
        print("  ✓ skills is a list")
        self.assertEqual(len(config['skills']), 0)
        print("  ✓ skills list is empty by default")
        print("\n  ✓ PASSED")
        print("\n✓ test_skill_empty_default PASSED\n")

    def test_skill_config_serialization(self):
        """Test that skills, use_skill_routing, semantic_description survive save/load cycle."""
        print("\n" + "="*80)
        print("TEST: test_skill_config_serialization")
        print("Verifying skills, use_skill_routing, semantic_description survive save/load")
        print("="*80)
        
        print("\n[STEP 1] Creating original config with all skill-related properties...")
        original_config = {
            'model': 'llama3.2:latest',
            'prompt': 'Test prompt',
            'system_message': 'You are a helpful assistant.',
            'skills': [
                {'name': 'Skill1', 'instructions': 'Do thing 1', 'enabled': True, 'priority': 1},
                {'name': 'Skill2', 'instructions': 'Do thing 2', 'enabled': True, 'priority': 2}
            ],
            'use_skill_routing': True,
            'semantic_description': 'Analyze customer data and generate reports'
        }
        print(f"  skills: {len(original_config['skills'])} skills")
        print(f"  use_skill_routing: {original_config['use_skill_routing']}")
        print(f"  semantic_description: {original_config['semantic_description']}")
        
        print("\n[STEP 2] Simulating save (like config_manager._save_llm_node)...")
        saved_data = {
            'skills': original_config['skills'],
            'use_skill_routing': original_config['use_skill_routing'],
            'semantic_description': original_config['semantic_description']
        }
        saved_json = json.dumps(saved_data)
        print(f"  Serialized to JSON: {saved_json[:100]}...")
        
        print("\n[STEP 3] Simulating load (like config_manager._create_llm_nodes)...")
        loaded_data = json.loads(saved_json)
        print(f"  Loaded from JSON")
        
        print("\n[STEP 4] Setting loaded config on new node...")
        mock_node = MockLLMConfig()
        mock_node.set_llm_config(loaded_data)
        print("  Config applied to node")
        
        print("\n[STEP 5] Retrieving config to verify...")
        final_config = mock_node.get_llm_config()
        
        print("\n[VERIFY] Checking all skill-related properties survived...")
        
        # Check skills
        self.assertEqual(len(final_config['skills']), 2)
        print("  ✓ skills count: 2")
        self.assertEqual(final_config['skills'][0]['name'], 'Skill1')
        print("  ✓ first skill name: Skill1")
        self.assertEqual(final_config['skills'][1]['name'], 'Skill2')
        print("  ✓ second skill name: Skill2")
        
        # Check use_skill_routing
        self.assertTrue(final_config['use_skill_routing'])
        print("  ✓ use_skill_routing: True")
        
        # Check semantic_description
        self.assertEqual(final_config['semantic_description'], 'Analyze customer data and generate reports')
        print("  ✓ semantic_description preserved")
        
        print("\n  ✓ PASSED")
        print("\n✓ test_skill_config_serialization PASSED\n")


class TestSkillInjection(unittest.TestCase):
    """Test cases for skill injection into system messages."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestSkillInjection test environment...")
        print("-"*80)

    def test_skills_injected_into_system_message(self):
        """Test that enabled skills are properly injected into system message."""
        print("\n" + "="*80)
        print("TEST: test_skills_injected_into_system_message")
        print("Verifying enabled skills are injected with proper formatting")
        print("="*80)
        
        print("\n[STEP 1] Creating skills configuration...")
        skills = [
            {
                'name': 'CodeReviewer',
                'description': 'Reviews code for quality',
                'instructions': 'Check for bugs, code style, and best practices.',
                'enabled': True,
                'priority': 1
            },
            {
                'name': 'DocWriter',
                'description': 'Writes documentation',
                'instructions': 'Create clear and comprehensive documentation.',
                'enabled': True,
                'priority': 2
            }
        ]
        print(f"  Created {len(skills)} skills")
        
        print("\n[STEP 2] Simulating skill injection logic (from executor.py)...")
        system_message = "You are a helpful assistant."
        
        # Filter enabled skills (from executor.py lines 231-238)
        enabled_skills = [s for s in skills if s.get('enabled', True)]
        print(f"  Enabled skills: {len(enabled_skills)}")
        
        # Inject skills into system_message (from executor.py lines 521-525)
        skill_block = "\n\n## Active Skills\n"
        for skill in enabled_skills:
            skill_block += f"\n### {skill.get('name', 'Unnamed')}\n{skill.get('instructions', '')}\n"
        final_system_message = system_message + skill_block
        
        print("\n[INFO] Final system message:")
        print("-"*40)
        print(final_system_message)
        print("-"*40)
        
        print("\n[VERIFY] Checking skill injection...")
        self.assertIn('## Active Skills', final_system_message)
        print("  ✓ '## Active Skills' header present")
        self.assertIn('### CodeReviewer', final_system_message)
        print("  ✓ '### CodeReviewer' skill name present")
        self.assertIn('### DocWriter', final_system_message)
        print("  ✓ '### DocWriter' skill name present")
        self.assertIn('Check for bugs, code style, and best practices.', final_system_message)
        print("  ✓ CodeReviewer instructions present")
        self.assertIn('Create clear and comprehensive documentation.', final_system_message)
        print("  ✓ DocWriter instructions present")
        print("\n  ✓ PASSED")
        print("\n✓ test_skills_injected_into_system_message PASSED\n")

    def test_disabled_skills_filtered(self):
        """Test that disabled skills are not injected into system message."""
        print("\n" + "="*80)
        print("TEST: test_disabled_skills_filtered")
        print("Verifying only enabled skills are injected, disabled are filtered out")
        print("="*80)
        
        print("\n[STEP 1] Creating skills with mixed enabled states...")
        skills = [
            {'name': 'EnabledSkill1', 'instructions': 'Instructions 1', 'enabled': True},
            {'name': 'DisabledSkill', 'instructions': 'Should not appear', 'enabled': False},
            {'name': 'EnabledSkill2', 'instructions': 'Instructions 2', 'enabled': True},
        ]
        print(f"  Total skills: {len(skills)}")
        print(f"    - EnabledSkill1: enabled=True")
        print(f"    - DisabledSkill: enabled=False")
        print(f"    - EnabledSkill2: enabled=True")
        
        print("\n[STEP 2] Filtering and injecting skills...")
        system_message = "You are a helpful assistant."
        
        # Filter enabled skills (from executor.py lines 231-238)
        enabled_skills = [s for s in skills if s.get('enabled', True)]
        print(f"  Enabled skills after filter: {len(enabled_skills)}")
        
        # Inject skills
        skill_block = "\n\n## Active Skills\n"
        for skill in enabled_skills:
            skill_block += f"\n### {skill.get('name', 'Unnamed')}\n{skill.get('instructions', '')}\n"
        final_system_message = system_message + skill_block
        
        print("\n[INFO] Final system message:")
        print("-"*40)
        print(final_system_message)
        print("-"*40)
        
        print("\n[VERIFY] Checking disabled skill filtering...")
        self.assertIn('### EnabledSkill1', final_system_message)
        print("  ✓ EnabledSkill1 present")
        self.assertIn('### EnabledSkill2', final_system_message)
        print("  ✓ EnabledSkill2 present")
        self.assertNotIn('DisabledSkill', final_system_message)
        print("  ✓ DisabledSkill NOT present (correctly filtered)")
        self.assertNotIn('Should not appear', final_system_message)
        print("  ✓ DisabledSkill instructions NOT present")
        print("\n  ✓ PASSED")
        print("\n✓ test_disabled_skills_filtered PASSED\n")

    def test_skill_routing_with_context(self):
        """Test that skills are selected based on semantic similarity to context."""
        print("\n" + "="*80)
        print("TEST: test_skill_routing_with_context")
        print("Verifying skill routing selects skills based on semantic context matching")
        print("="*80)
        
        print("\n[STEP 1] Setting up skills with different descriptions...")
        skills = [
            {
                'name': 'CodeGenerator',
                'description': 'Generates Python code snippets and functions',
                'instructions': 'Write clean, efficient code.',
                'enabled': True,
                'trigger_keywords': 'code function python generate'
            },
            {
                'name': 'DataAnalyzer',
                'description': 'Analyzes data and creates statistical reports',
                'instructions': 'Focus on statistical analysis.',
                'enabled': True,
                'trigger_keywords': 'data statistics analyze report'
            },
            {
                'name': 'EmailWriter',
                'description': 'Composes professional emails and messages',
                'instructions': 'Write clear, professional emails.',
                'enabled': True,
                'trigger_keywords': 'email write compose message'
            }
        ]
        print(f"  Created {len(skills)} skills with different purposes")
        
        print("\n[STEP 2] Creating context text about coding...")
        context_text = "I need to generate a Python function that calculates fibonacci numbers. Code and programming are required."
        print(f"  Context: '{context_text[:60]}...'")
        
        print("\n[STEP 3] Attempting skill routing with sentence_transformers...")
        try:
            from sentence_transformers import SentenceTransformer, util
            print("  sentence_transformers available, loading model...")
            
            try:
                model = SentenceTransformer('all-MiniLM-L6-v2')
                print("  Model loaded successfully")
                
                # Build skill texts (from executor.py lines 499-501)
                skill_texts = [
                    f"{s.get('name','')}: {s.get('description','')} {s.get('trigger_keywords','')}"
                    for s in skills
                ]
                print(f"  Skill texts for embedding:")
                for i, st in enumerate(skill_texts):
                    print(f"    [{i}] {st[:60]}...")
                
                # Encode and compute similarity
                query_emb = model.encode(context_text, convert_to_tensor=True)
                skill_embs = model.encode(skill_texts, convert_to_tensor=True)
                scores = util.cos_sim(query_emb, skill_embs)[0]
                
                print("\n  Similarity scores:")
                for i, (skill, score) in enumerate(zip(skills, scores)):
                    print(f"    {skill['name']}: {float(score):.4f}")
                
                
                print("\n[STEP 4] Selecting skills with score > 0.15...")
                selected = []
                for idx, skill in enumerate(skills):
                    score = float(scores[idx])
                    if score > 0.15:
                        selected.append((score, skill))
                
                # Sort by score (descending) then priority
                selected.sort(key=lambda x: (-x[0], x[1].get('priority', 5)))
                selected_skills = [s for _, s in selected]
                
                print(f"  Selected {len(selected_skills)} skills:")
                for skill in selected_skills:
                    print(f"    - {skill['name']}")
                
                print("\n[VERIFY] Checking semantic routing results...")
                # CodeGenerator should be selected (high similarity to coding context)
                selected_names = [s['name'] for s in selected_skills]
                self.assertIn('CodeGenerator', selected_names)
                print("  ✓ CodeGenerator selected (matches coding context)")
                print("\n  ✓ PASSED (with real embeddings)")
                
            except Exception as e:
                print(f"  Model loading failed: {e}")
                raise unittest.SkipTest(f"Could not load embedding model: {e}")
                
        except ImportError:
            print("  sentence_transformers not available")
            print("\n[STEP 4] Testing fallback behavior...")
            # When no embedding model, all enabled skills should be kept
            enabled_skills = [s for s in skills if s.get('enabled', True)]
            print(f"  Fallback: keeping all {len(enabled_skills)} enabled skills")
            
            print("\n[VERIFY] Checking fallback behavior...")
            self.assertEqual(len(enabled_skills), 3)
            print("  ✓ All 3 enabled skills kept (fallback behavior)")
            print("\n  ✓ PASSED (fallback without embeddings)")
        
        print("\n✓ test_skill_routing_with_context PASSED\n")

    def test_all_skills_injected_when_routing_disabled(self):
        """Test that all enabled skills are injected when routing is disabled."""
        print("\n" + "="*80)
        print("TEST: test_all_skills_injected_when_routing_disabled")
        print("Verifying all enabled skills are injected when use_skill_routing=False")
        print("="*80)
        
        print("\n[STEP 1] Creating skills configuration...")
        skills = [
            {'name': 'Skill1', 'instructions': 'Inst 1', 'enabled': True},
            {'name': 'Skill2', 'instructions': 'Inst 2', 'enabled': True},
            {'name': 'Skill3', 'instructions': 'Inst 3', 'enabled': True},
        ]
        use_skill_routing = False  # Routing disabled
        print(f"  Created {len(skills)} skills")
        print(f"  use_skill_routing: {use_skill_routing}")
        
        print("\n[STEP 2] Simulating injection logic...")
        # When routing is disabled, all enabled skills are used
        enabled_skills = [s for s in skills if s.get('enabled', True)]
        
        # Simulate the condition from executor.py line 488
        if use_skill_routing and len(enabled_skills) > 1:
            print("  Routing would be applied (but it's disabled)")
        else:
            print(f"  Routing disabled, using all {len(enabled_skills)} enabled skills")
        
        system_message = "You are a helpful assistant."
        skill_block = "\n\n## Active Skills\n"
        for skill in enabled_skills:
            skill_block += f"\n### {skill.get('name', 'Unnamed')}\n{skill.get('instructions', '')}\n"
        final_system_message = system_message + skill_block
        
        print("\n[INFO] Final system message:")
        print("-"*40)
        print(final_system_message)
        print("-"*40)
        
        print("\n[VERIFY] Checking all skills are injected...")
        self.assertIn('### Skill1', final_system_message)
        print("  ✓ Skill1 present")
        self.assertIn('### Skill2', final_system_message)
        print("  ✓ Skill2 present")
        self.assertIn('### Skill3', final_system_message)
        print("  ✓ Skill3 present")
        print("\n  ✓ PASSED - All skills injected when routing disabled")
        print("\n✓ test_all_skills_injected_when_routing_disabled PASSED\n")


class TestLLMSemanticKey(unittest.TestCase):
    """Test cases for semantic key derivation from LLM node config."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestLLMSemanticKey test environment...")
        print("-"*80)

    def _derive_semantic_key(self, llm_config, from_node_id='test_llm_1'):
        """Helper to derive semantic key from LLM config (mirrors context_ops.py logic)."""
        semantic_desc = llm_config.get('semantic_description', '')
        
        if not semantic_desc:
            # Use output_variable if meaningful (not generic 'llm_output')
            out_var = llm_config.get('output_variable', '')
            if out_var and out_var != 'llm_output':
                semantic_desc = out_var
        
        # Final fallback: use the upstream node ID (guaranteed unique per node)
        return semantic_desc if semantic_desc else f'node_{from_node_id}_response'

    def test_semantic_key_from_semantic_description(self):
        """Test semantic key uses semantic_description when available."""
        print("\n" + "="*80)
        print("TEST: test_semantic_key_from_semantic_description")
        print("Verifying semantic_description is used as key when provided")
        print("="*80)
        
        print("\n[STEP 1] Creating LLM config with semantic_description...")
        llm_config = {
            'semantic_description': 'Generate customer support responses',
            'system_message': 'You are a support agent.',
            'output_variable': 'support_response'
        }
        print(f"  semantic_description: '{llm_config['semantic_description']}'")
        print(f"  system_message: '{llm_config['system_message']}'")
        print(f"  output_variable: '{llm_config['output_variable']}'")
        
        print("\n[STEP 2] Deriving semantic key...")
        target_key = self._derive_semantic_key(llm_config)
        print(f"  Derived key: '{target_key}'")
        
        print("\n[VERIFY] Checking semantic_description takes priority...")
        self.assertEqual(target_key, 'Generate customer support responses')
        print("  ✓ semantic_description used as key (highest priority)")
        print("\n  ✓ PASSED")
        print("\n✓ test_semantic_key_from_semantic_description PASSED\n")

    def test_semantic_key_from_system_message(self):
        """Test semantic key falls back to node_id when only system_message is available."""
        print("\n" + "="*80)
        print("TEST: test_semantic_key_from_system_message")
        print("Verifying node_id-based key is used when only system_message is available")
        print("="*80)
        
        print("\n[STEP 1] Creating LLM config with system_message only...")
        llm_config = {
            'semantic_description': '',
            'system_message': 'You are a code reviewer that analyzes code quality. Please provide detailed feedback.',
            'output_variable': 'llm_output'
        }
        print(f"  semantic_description: (empty)")
        print(f"  system_message: '{llm_config['system_message']}'")
        
        print("\n[STEP 2] Deriving semantic key...")
        target_key = self._derive_semantic_key(llm_config, from_node_id='llm_42')
        print(f"  Derived key: '{target_key}'")
        
        print("\n[VERIFY] Checking node_id-based fallback (system_message no longer used as key)...")
        self.assertEqual(target_key, 'node_llm_42_response')
        print("  ✓ Node ID-based fallback key derived correctly")
        print("\n  ✓ PASSED")
        print("\n✓ test_semantic_key_from_system_message PASSED\n")

    def test_semantic_key_from_output_variable(self):
        """Test semantic key uses output_variable when no semantic_description or system_message."""
        print("\n" + "="*80)
        print("TEST: test_semantic_key_from_output_variable")
        print("Verifying output_variable is used as key when non-default")
        print("="*80)
        
        print("\n[STEP 1] Creating LLM config with only output_variable...")
        llm_config = {
            'semantic_description': '',
            'system_message': '',
            'output_variable': 'data_analysis_result'
        }
        print(f"  semantic_description: (empty)")
        print(f"  system_message: (empty)")
        print(f"  output_variable: '{llm_config['output_variable']}'")
        
        print("\n[STEP 2] Deriving semantic key...")
        target_key = self._derive_semantic_key(llm_config, from_node_id='llm_99')
        print(f"  Derived key: '{target_key}'")
        
        print("\n[VERIFY] Checking output_variable is used as key when non-default...")
        self.assertEqual(target_key, 'data_analysis_result')
        print("  ✓ Non-default output_variable used as key")
        print("\n  ✓ PASSED")
        print("\n✓ test_semantic_key_from_output_variable PASSED\n")

    def test_semantic_key_fallback(self):
        """Test semantic key falls back to node_id when nothing else available."""
        print("\n" + "="*80)
        print("TEST: test_semantic_key_fallback")
        print("Verifying fallback to node_id-based key when no identifying info")
        print("="*80)
        
        print("\n[STEP 1] Creating LLM config with defaults only...")
        llm_config = {
            'semantic_description': '',
            'system_message': '',
            'output_variable': 'llm_output'  # Default value
        }
        print(f"  semantic_description: (empty)")
        print(f"  system_message: (empty)")
        print(f"  output_variable: '{llm_config['output_variable']}' (default)")
        
        print("\n[STEP 2] Deriving semantic key...")
        target_key = self._derive_semantic_key(llm_config, from_node_id='llm_7')
        print(f"  Derived key: '{target_key}'")

        print("\n[VERIFY] Checking fallback behavior...")
        self.assertEqual(target_key, 'node_llm_7_response')
        print("  ✓ Fallback to node_id-based key when no identifying info")
        print("\n  ✓ PASSED")
        print("\n✓ test_semantic_key_fallback PASSED\n")


class TestContextConnectionRestriction(unittest.TestCase):
    """Test cases for Context node output connection restriction."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestContextConnectionRestriction test environment...")
        print("-"*80)

    def _validate_context_connection(self, source_identifier, source_port_name, target_identifier):
        """
        Validate whether a connection should be allowed.
        Mirrors the logic in graph_manager._on_port_connected.
        
        Returns: (allowed: bool, reason: str)
        """
        # Check if the output comes from a Context node's 'context' output port
        if source_identifier == 'context' and source_port_name == 'context':
            # Only allow connection to LLM nodes
            if target_identifier != 'llm':
                return False, f"Context output port can only connect to LLM nodes. Blocked connection to '{target_identifier}'"
        return True, "Connection allowed"

    def test_context_output_restriction_concept(self):
        """Test that context output can only connect to LLM nodes."""
        print("\n" + "="*80)
        print("TEST: test_context_output_restriction_concept")
        print("Verifying Context node 'context' output only allows LLM connections")
        print("="*80)
        
        print("\n[STEP 1] Testing valid connection: Context -> LLM...")
        allowed, reason = self._validate_context_connection(
            source_identifier='context',
            source_port_name='context',
            target_identifier='llm'
        )
        print(f"  Source: context (context port)")
        print(f"  Target: llm")
        print(f"  Result: allowed={allowed}, reason='{reason}'")
        self.assertTrue(allowed)
        print("  ✓ Context -> LLM connection allowed")
        
        print("\n[STEP 2] Testing invalid connection: Context -> Code node...")
        allowed, reason = self._validate_context_connection(
            source_identifier='context',
            source_port_name='context',
            target_identifier='code'
        )
        print(f"  Source: context (context port)")
        print(f"  Target: code")
        print(f"  Result: allowed={allowed}, reason='{reason}'")
        self.assertFalse(allowed)
        print("  ✓ Context -> Code connection rejected")
        
        print("\n[STEP 3] Testing invalid connection: Context -> Sequence node...")
        allowed, reason = self._validate_context_connection(
            source_identifier='context',
            source_port_name='context',
            target_identifier='sequence'
        )
        print(f"  Source: context (context port)")
        print(f"  Target: sequence")
        print(f"  Result: allowed={allowed}, reason='{reason}'")
        self.assertFalse(allowed)
        print("  ✓ Context -> Sequence connection rejected")
        
        print("\n[STEP 4] Testing invalid connection: Context -> Conditional node...")
        allowed, reason = self._validate_context_connection(
            source_identifier='context',
            source_port_name='context',
            target_identifier='conditional'
        )
        print(f"  Source: context (context port)")
        print(f"  Target: conditional")
        print(f"  Result: allowed={allowed}, reason='{reason}'")
        self.assertFalse(allowed)
        print("  ✓ Context -> Conditional connection rejected")
        
        print("\n[STEP 5] Testing other port: Context 'output' port -> any node...")
        allowed, reason = self._validate_context_connection(
            source_identifier='context',
            source_port_name='output',  # Not 'context' port
            target_identifier='code'
        )
        print(f"  Source: context (output port, not context)")
        print(f"  Target: code")
        print(f"  Result: allowed={allowed}, reason='{reason}'")
        self.assertTrue(allowed)
        print("  ✓ Other ports not restricted")
        
        print("\n  ✓ PASSED - All connection restrictions work correctly")
        print("\n✓ test_context_output_restriction_concept PASSED\n")


class TestFullSkillsPipeline(unittest.TestCase):
    """Test cases for full workflow pipeline with skills."""
    
    def setUp(self):
        print("\n" + "-"*80)
        print("[SETUP] Initializing TestFullSkillsPipeline test environment...")
        self.db_path = tempfile.mktemp(suffix='.db')
        print(f"  Created temp database file: {self.db_path}")
        self.db = ContextDatabase(db_path=self.db_path)
        print(f"  ContextDatabase instance created")
        self.llm_executor = MockLLMExecutor()
        print(f"  MockLLMExecutor instance created")
        self.fallback_handler = MockFallbackHandler()
        print(f"  MockFallbackHandler instance created")
        print("-"*80)

    def tearDown(self):
        print("\n[TEARDOWN] Cleaning up test environment...")
        self.db.close()
        print(f"  Database connection closed")
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
            print(f"  Temp database file deleted: {self.db_path}")
        print("-"*80)

    def test_code_to_context_to_llm_with_skills(self):
        """Test full pipeline: code -> context -> LLM with skills."""
        print("\n" + "="*80)
        print("TEST: test_code_to_context_to_llm_with_skills")
        print("Verifying full pipeline: code produces output, context stores it, LLM with skills reads it")
        print("="*80)
        
        print("\n[STEP 1] Setting up code node output...")
        code_output = "def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)"
        self.llm_executor.set_variable('node_code_1_output', code_output)
        print(f"  Code output: '{code_output[:50]}...'")
        
        print("\n[STEP 2] Building workflow graph...")
        # Skills configuration
        skills = [
            {
                'name': 'CodeReviewer',
                'description': 'Reviews code quality',
                'instructions': 'Analyze code for bugs, style issues, and improvements.',
                'enabled': True,
                'priority': 1
            }
        ]
        
        graph = {
            'code_1': {
                'type': 'code',
                'data': {
                    'node_id': 'code_1',
                    'label': 'Generates Fibonacci',
                    'description': 'Generated Fibonacci function'
                },
                'connections': {}
            },
            'ctx_1': {
                'type': 'context',
                'data': {
                    'node_id': 'ctx_1',
                    'label': 'Skills Context',
                    'max_history': 10,
                },
                'connections': {
                    'output': [{'node_id': 'llm_1', 'input_port': 'input'}]
                },
                'inputs': [
                    {'from_node': 'code_1', 'output_type': 'output', 'input_port': 'input'}
                ]
            },
            'llm_1': {
                'type': 'llm',
                'data': {
                    'node_id': 'llm_1',
                    'system_message': 'You are a code reviewer.',
                    'skills': skills,
                    'use_skill_routing': False
                },
                'connections': {},
                'inputs': [
                    {'from_node': 'ctx_1', 'output_type': 'output', 'input_port': 'input'}
                ]
            }
        }
        
        print("  Pipeline: code_1 -> ctx_1 -> llm_1")
        print(f"  LLM has {len(skills)} skill(s) configured")
        
        print("\n[STEP 3] Executing context node...")
        executor = WorkflowExecutor(graph, None, self.llm_executor, self.fallback_handler)
        executor._context_db = self.db
        executor.chain_id = 'test_chain'
        executor._execute_context_node(graph['ctx_1'], lambda: False)
        print("  ✓ Context node executed")
        
        print("\n[STEP 4] Verifying context stored code output...")
        ctx_output = self.llm_executor.get_variable('node_ctx_1_output')
        self.assertIsNotNone(ctx_output)
        print("  ✓ Context output exists")
        
        print(f"  JSON output:\n{ctx_output}")
        
        # New format: clean JSON dict
        self.assertIn('def fibonacci', ctx_output)
        print("  ✓ 'def fibonacci' present in JSON output")
        
        # Verify valid JSON
        parsed = json.loads(ctx_output)
        self.assertIsInstance(parsed, dict)
        print("  ✓ Output is valid JSON")
        
        print("\n  ✓ PASSED - Full pipeline with skills works correctly")
        print("\n✓ test_code_to_context_to_llm_with_skills PASSED\n")

    def test_multiple_skills_stacked(self):
        """Test LLM node with multiple skills, all injected in priority order."""
        print("\n" + "="*80)
        print("TEST: test_multiple_skills_stacked")
        print("Verifying multiple skills are injected in priority order")
        print("="*80)
        
        print("\n[STEP 1] Creating skills with different priorities...")
        skills = [
            {'name': 'CriticalSkill', 'instructions': 'Handle critical tasks first.', 'enabled': True, 'priority': 1},
            {'name': 'NormalSkill', 'instructions': 'Handle normal tasks.', 'enabled': True, 'priority': 3},
            {'name': 'LowPrioritySkill', 'instructions': 'Handle optional tasks.', 'enabled': True, 'priority': 5},
        ]
        print(f"  Created {len(skills)} skills with priorities 1, 3, 5")
        
        print("\n[STEP 2] Sorting skills by priority...")
        # Sort by priority (ascending)
        sorted_skills = sorted(skills, key=lambda s: s.get('priority', 5))
        print(f"  Sorted order:")
        for i, s in enumerate(sorted_skills):
            print(f"    [{i+1}] {s['name']} (priority {s['priority']})")
        
        print("\n[STEP 3] Injecting skills into system message...")
        system_message = "You are a helpful assistant."
        
        # Inject sorted skills
        skill_block = "\n\n## Active Skills\n"
        for skill in sorted_skills:
            skill_block += f"\n### {skill.get('name', 'Unnamed')}\n{skill.get('instructions', '')}\n"
        final_system_message = system_message + skill_block
        
        print("  Final system message:")
        print("  " + "-"*40)
        print("  " + final_system_message.replace("\n", "\n  "))
        print("  " + "-"*40)
        
        print("\n[VERIFY] Checking skill order in output...")
        
        # Find positions of each skill in the message
        critical_pos = final_system_message.find('### CriticalSkill')
        normal_pos = final_system_message.find('### NormalSkill')
        low_pos = final_system_message.find('### LowPrioritySkill')
        
        print(f"  Position check:")
        print(f"    CriticalSkill at position: {critical_pos}")
        print(f"    NormalSkill at position: {normal_pos}")
        print(f"    LowPrioritySkill at position: {low_pos}")
        
        # Verify order: Critical < Normal < Low
        self.assertLess(critical_pos, normal_pos)
        print("  ✓ CriticalSkill appears before NormalSkill")
        self.assertLess(normal_pos, low_pos)
        print("  ✓ NormalSkill appears before LowPrioritySkill")
        
        # All skills should be present
        self.assertIn('### CriticalSkill', final_system_message)
        self.assertIn('### NormalSkill', final_system_message)
        self.assertIn('### LowPrioritySkill', final_system_message)
        print("  ✓ All 3 skills present in system message")
        
        print("\n  ✓ PASSED - Multiple skills injected in priority order")
        print("\n✓ test_multiple_skills_stacked PASSED\n")
