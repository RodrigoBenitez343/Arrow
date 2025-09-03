#!/usr/bin/env python3
"""
Vision Integration Test
Test script to verify LoOper-OverWatch vision integration with screen capture
"""

import os
import sys
import json
from pathlib import Path

# Add the AI directory to the path
sys.path.append(str(Path(__file__).parent / "AI"))
from consult import OllamaClient

def test_basic_connection():
    """Test basic connection to OverWatch"""
    print("Testing basic connection to OverWatch...")
    client = OllamaClient(debug=True)
    
    health = client.health_check()
    print(f"Health check result: {json.dumps(health, indent=2)}")
    
    if health.get("status") == "healthy":
        print("✓ Basic connection successful")
        return True
    else:
        print("✗ Basic connection failed")
        return False

def test_vision_model_availability():
    """Test if vision model is available"""
    print("\nTesting vision model availability...")
    client = OllamaClient(debug=True)
    
    models = client.list_models()
    vision_models = []
    
    for model in models:
        model_name = model.get('name', '')
        if client.is_vision_model(model_name):
            vision_models.append(model_name)
    
    print(f"Available models: {[m.get('name') for m in models]}")
    print(f"Vision models found: {vision_models}")
    
    if vision_models:
        print(f"✓ Vision models available: {', '.join(vision_models)}")
        return True, vision_models[0]
    else:
        print("✗ No vision models found")
        return False, None

def test_screen_capture():
    """Test screen capture functionality"""
    print("\nTesting screen capture functionality...")
    client = OllamaClient(debug=True)
    
    try:
        screen_data = client.capture_screen(quality=85, max_size=800)
        if screen_data:
            print(f"✓ Screen capture successful ({len(screen_data)} chars)")
            return True
        else:
            print("✗ Screen capture failed")
            return False
    except Exception as e:
        print(f"✗ Screen capture error: {e}")
        return False

def test_vision_chat():
    """Test vision-enhanced chat"""
    print("\nTesting vision-enhanced chat...")
    client = OllamaClient(debug=True)
    
    # Use minicpm-v:latest as specified in requirements
    model = "minicpm-v:latest"
    
    try:
        response = client.chat_with_screen(
            model=model,
            prompt="Describe what you can see on the current screen. What applications or windows are visible?",
            capture_screen=True,
            screen_quality=85,
            max_screen_size=800
        )
        
        if "error" in response:
            print(f"✗ Vision chat failed: {response['error']}")
            return False
        else:
            print("✓ Vision chat successful")
            print(f"Response: {response.get('response', 'No response')[:200]}...")
            return True
            
    except Exception as e:
        print(f"✗ Vision chat error: {e}")
        return False

def test_comorag_with_vision():
    """Test ComoRAG with vision capabilities"""
    print("\nTesting ComoRAG with vision...")
    client = OllamaClient(debug=True)
    
    # Enable ComoRAG for this test
    original_use_comorag = client.use_comorag
    client.use_comorag = True
    
    try:
        response = client.chat_with_screen(
            model="minicpm-v:latest",
            prompt="Analyze the current screen context and explain what type of work environment or application is being used.",
            capture_screen=True,
            screen_quality=85,
            max_screen_size=800
        )
        
        if "error" in response:
            print(f"✗ ComoRAG vision test failed: {response['error']}")
            return False
        else:
            print("✓ ComoRAG vision test successful")
            print(f"Response: {response.get('response', 'No response')[:200]}...")
            return True
            
    except Exception as e:
        print(f"✗ ComoRAG vision test error: {e}")
        return False
    finally:
        # Restore original ComoRAG setting
        client.use_comorag = original_use_comorag

def main():
    """Run all vision integration tests"""
    print("=== LoOper-OverWatch Vision Integration Tests ===")
    print("Testing vision capabilities with minicpm-v:latest\n")
    
    tests = [
        ("Basic Connection", test_basic_connection),
        ("Vision Model Availability", test_vision_model_availability),
        ("Screen Capture", test_screen_capture),
        ("Vision Chat", test_vision_chat),
        ("ComoRAG with Vision", test_comorag_with_vision)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n{'='*50}")
        print(f"Running: {test_name}")
        print(f"{'='*50}")
        
        try:
            if test_name == "Vision Model Availability":
                success, model = test_func()
                results.append((test_name, success))
            else:
                success = test_func()
                results.append((test_name, success))
        except Exception as e:
            print(f"✗ {test_name} failed with exception: {e}")
            results.append((test_name, False))
    
    # Summary
    print(f"\n\n{'='*50}")
    print("TEST SUMMARY")
    print(f"{'='*50}")
    
    passed = 0
    for test_name, success in results:
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{test_name:<30} {status}")
        if success:
            passed += 1
    
    print(f"\nTotal: {passed}/{len(results)} tests passed")
    
    if passed == len(results):
        print("\n🎉 All vision integration tests passed!")
        print("LoOper-OverWatch vision integration is working correctly.")
    else:
        print(f"\n⚠️  {len(results) - passed} test(s) failed.")
        print("Please check the error messages above.")
    
    return passed == len(results)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)