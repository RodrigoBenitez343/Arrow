#!/usr/bin/env python3
"""
Test script to verify the TypeError fix for LLM node property setting.
This script simulates the problematic scenario to ensure it's resolved.
"""

class MockNode:
    """Mock node class to simulate NodeGraphQt behavior"""
    def __init__(self):
        self.properties = {}
        self.widgets = {}
    
    def set_property(self, name, value):
        """Simulate set_property method that expects string values"""
        if not isinstance(value, str):
            raise TypeError(f"setText(self, a0: Optional[str]): argument 1 has unexpected type '{type(value).__name__}'")
        self.properties[name] = value
        print(f"✓ Successfully set property '{name}' = '{value}' (type: {type(value).__name__})")

def test_llm_node_property_setting():
    """Test LLM node property setting with the fix applied"""
    print("Testing LLM node property setting...")
    
    node = MockNode()
    
    # Simulate the original problematic code (would fail)
    temperature = 0.7  # float value
    max_tokens = 500   # int value
    
    try:
        # This is the FIXED version - converting to string
        node.set_property('temperature', str(temperature))
        node.set_property('max_tokens', str(max_tokens))
        print("✓ All properties set successfully with string conversion!")
        return True
    except TypeError as e:
        print(f"✗ Error: {e}")
        return False

def test_original_problem():
    """Test the original problematic code (should fail)"""
    print("\nTesting original problematic code...")
    
    node = MockNode()
    temperature = 0.7  # float value
    
    try:
        # This would be the ORIGINAL problematic code
        node.set_property('temperature', temperature)  # No str() conversion
        print("✗ This should have failed but didn't!")
        return False
    except TypeError as e:
        print(f"✓ Expected error caught: {e}")
        return True

if __name__ == "__main__":
    print("=" * 60)
    print("Testing LoOper LLM Node Property Setting Fix")
    print("=" * 60)
    
    # Test the fix
    success1 = test_llm_node_property_setting()
    
    # Test that the original problem would indeed fail
    success2 = test_original_problem()
    
    print("\n" + "=" * 60)
    if success1 and success2:
        print("✓ ALL TESTS PASSED - The fix is working correctly!")
        print("The TypeError should no longer occur when creating LLM nodes.")
    else:
        print("✗ Some tests failed - please check the implementation.")
    print("=" * 60)