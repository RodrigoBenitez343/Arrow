#!/usr/bin/env python3
"""
Test script for LoOper-OverWatch ComoRAG integration
Tests LLM node execution with ComoRAG (headless mode for remote machines)
"""

import sys
import os
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from AI.consult import OllamaClient

def test_comorag_basic_connection():
    """Test basic ComoRAG connection"""
    print("=== Testing ComoRAG Basic Connection ===")
    try:
        client = OllamaClient(use_comorag=True, debug=True)
        client.set_comorag_config(max_cycles=2, memory_size=300)
        
        print(f"ComoRAG enabled: {client.use_comorag}")
        print(f"ComoRAG config: {client.comorag_config}")
        
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
        import traceback
        traceback.print_exc()
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
        
        print(f"Context items added: {len(client.get_context())}")
        
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
        import traceback
        traceback.print_exc()
        return False

def test_comorag_configuration():
    """Test ComoRAG configuration options"""
    print("\n=== Testing ComoRAG Configuration ===")
    try:
        client = OllamaClient(use_comorag=True, debug=True)
        
        # Test different configurations
        configs = [
            {'max_cycles': 2, 'memory_size': 200},
            {'max_cycles': 5, 'memory_size': 1000},
            {'max_cycles': 3, 'memory_size': 500}
        ]
        
        for i, config in enumerate(configs):
            print(f"\nTesting config {i+1}: {config}")
            client.set_comorag_config(**config)
            
            response = client.generate(
                model="llama3.2:latest",
                prompt=f"This is test query {i+1} with different ComoRAG settings.",
                system="You are testing different ComoRAG configurations."
            )
            
            print(f"Response {i+1}: {response.get('response', 'No response')[:100]}...")
            metadata = response.get('comorag_metadata', {})
            print(f"Reasoning cycles used: {metadata.get('reasoning_cycles', 'N/A')}")
        
        return True
        
    except Exception as e:
        print(f"Configuration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all integration tests"""
    print("Starting LoOper-OverWatch ComoRAG Integration Tests (Headless Mode)")
    print("=" * 70)
    
    tests = [
        test_comorag_basic_connection,
        test_comorag_with_context,
        test_comorag_configuration
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
        
        print("-" * 50)
    
    print(f"\nTest Results: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! ComoRAG integration is working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Check the logs above for details.")
        return 1

if __name__ == "__main__":
    sys.exit(main())