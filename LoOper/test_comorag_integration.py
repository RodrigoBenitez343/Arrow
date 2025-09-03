#!/usr/bin/env python3
"""
Test script for LoOper-OverWatch ComoRAG integration
Tests LLM node execution with screen capture, OCR, and context processing
"""

import sys
import os
import time
import tempfile
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from AI.consult import OllamaClient
from player.multi_sequence.llm_executor import LLMExecutor

def test_comorag_basic_connection():
    """Test basic ComoRAG connection"""
    print("=== Testing ComoRAG Basic Connection ===")
    try:
        client = OllamaClient(use_comorag=True, debug=True)
        client.set_comorag_config(max_cycles=2, memory_size=300)
        
        # Test health check
        health = client.health_check()
        print(f"Health check: {health}")
        
        # Test simple query
        response = client.generate(
            model="llama3.2:latest",
            prompt="Hello, can you see this message?",
            system="You are a helpful assistant testing ComoRAG integration."
        )
        
        print(f"Response: {response.get('response', 'No response')}")
        print(f"ComoRAG metadata: {response.get('comorag_metadata', {})}")
        return True
        
    except Exception as e:
        print(f"Basic connection test failed: {e}")
        return False

def test_comorag_with_context():
    """Test ComoRAG with context data"""
    print("\n=== Testing ComoRAG with Context ===")
    try:
        client = OllamaClient(use_comorag=True, debug=True)
        client.set_comorag_config(max_cycles=3, memory_size=500)
        
        # Add some context
        client.add_context({
            'content': 'This is a test automation workflow running on LoOper',
            'source': 'workflow_context',
            'type': 'automation',
            'timestamp': time.time()
        })
        
        client.add_context({
            'ocr_text': 'Sample OCR text from screen: Login Button, Username Field, Password Field',
            'source': 'screen_capture',
            'type': 'ocr',
            'timestamp': time.time()
        })
        
        response = client.generate(
            model="llama3.2:latest",
            prompt="Based on the screen context, what UI elements are visible?",
            system="You are analyzing screen content for automation purposes."
        )
        
        print(f"Response: {response.get('response', 'No response')}")
        print(f"ComoRAG metadata: {response.get('comorag_metadata', {})}")
        return True
        
    except Exception as e:
        print(f"Context test failed: {e}")
        return False

def test_llm_executor_integration():
    """Test LLM executor with ComoRAG integration"""
    print("\n=== Testing LLM Executor Integration ===")
    try:
        # Create a mock LLM node
        mock_node = {
            'id': 'test_llm_node',
            'type': 'llm',
            'data': {
                'model': 'llama3.2:latest',
                'prompt': 'Analyze the current screen and provide insights for automation.',
                'system_message': 'You are an automation assistant analyzing screen content.',
                'temperature': 0.7,
                'max_tokens': 200,
                'output_variable': 'test_output',
                'api_url': 'http://localhost:8000',
                'write_text': False,  # Don't actually write text during test
                'use_vision': True,
                'screenshot_enabled': True,
                'use_comorag': True,
                'max_cycles': 3,
                'memory_size': 500
            },
            'connections': {
                'output': []
            }
        }
        
        # Create LLM executor
        executor = LLMExecutor(variables={'test_var': 'test_value'})
        
        # Mock stop flag
        def stop_flag():
            return False
        
        # Execute the node (this will test the full integration)
        print("Executing LLM node with ComoRAG integration...")
        result = executor.execute_llm_node(mock_node, stop_flag)
        
        if result is not None:
            print(f"LLM executor test completed successfully")
            print(f"Variables after execution: {executor.variables}")
            return True
        else:
            print("LLM executor returned None - check logs for details")
            return False
        
    except Exception as e:
        print(f"LLM executor test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all integration tests"""
    print("Starting LoOper-OverWatch ComoRAG Integration Tests")
    print("=" * 60)
    
    tests = [
        test_comorag_basic_connection,
        test_comorag_with_context,
        test_llm_executor_integration
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
                print("✓ PASSED")
            else:
                print("✗ FAILED")
        except Exception as e:
            print(f"✗ FAILED with exception: {e}")
        
        print("-" * 40)
    
    print(f"\nTest Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! ComoRAG integration is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Check the logs above for details.")
        return 1

if __name__ == "__main__":
    sys.exit(main())