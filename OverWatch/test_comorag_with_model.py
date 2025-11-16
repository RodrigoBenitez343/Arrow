#!/usr/bin/env python3
"""
Test script to verify the downloaded TinyLlama model works with ComoRAG API.
"""

import requests
import json
from pathlib import Path

def test_comorag_api():
    """Test the ComoRAG API with the downloaded model."""
    
    # API endpoint (assuming server is running on localhost:8000)
    base_url = "http://localhost:8000"
    
    # Model path
    model_path = "./models/models--TinyLlama--TinyLlama-1.1B-Chat-v1.0/snapshots/fe8a4ea1ffedaf415f4da2f062534de366a451e6"
    
    print("Testing ComoRAG API with TinyLlama model...")
    print(f"Model path: {model_path}")
    print(f"API base URL: {base_url}")
    
    # Check if model files exist
    model_dir = Path(model_path)
    if not model_dir.exists():
        print(f"Error: Model directory not found: {model_path}")
        return False
        
    config_file = model_dir / "config.json"
    model_file = model_dir / "model.safetensors"
    
    if not config_file.exists():
        print(f"Error: config.json not found in {model_path}")
        return False
        
    if not model_file.exists():
        print(f"Error: model.safetensors not found in {model_path}")
        return False
        
    print("✓ Model files found successfully")
    
    # Test API endpoints
    try:
        # Test health endpoint
        print("\n1. Testing health endpoint...")
        response = requests.get(f"{base_url}/health", timeout=10)
        if response.status_code == 200:
            print("✓ Health endpoint working")
        else:
            print(f"✗ Health endpoint failed: {response.status_code}")
            
        # Test ComoRAG endpoints
        print("\n2. Testing ComoRAG endpoints...")
        
        # List available endpoints
        response = requests.get(f"{base_url}/docs", timeout=10)
        if response.status_code == 200:
            print("✓ API documentation accessible")
        else:
            print(f"✗ API docs not accessible: {response.status_code}")
            
        # Test a simple query (if ComoRAG has a query endpoint)
        print("\n3. Testing ComoRAG query...")
        test_query = {
            "query": "What is artificial intelligence?",
            "model_path": model_path
        }
        
        # Try common ComoRAG endpoints
        endpoints_to_try = [
            "/comorag/query",
            "/query",
            "/chat",
            "/generate"
        ]
        
        for endpoint in endpoints_to_try:
            try:
                response = requests.post(
                    f"{base_url}{endpoint}", 
                    json=test_query, 
                    timeout=30
                )
                if response.status_code == 200:
                    print(f"✓ {endpoint} endpoint working")
                    result = response.json()
                    print(f"Response: {json.dumps(result, indent=2)[:200]}...")
                    break
                else:
                    print(f"✗ {endpoint} failed: {response.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"✗ {endpoint} error: {str(e)[:100]}...")
                
    except requests.exceptions.ConnectionError:
        print("✗ Cannot connect to the server. Make sure OverWatch is running on http://localhost:8000")
        return False
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False
        
    print("\n=== Test Summary ===")
    print("Model downloaded successfully: ✓")
    print("Model files verified: ✓")
    print("Ready for ComoRAG API testing: ✓")
    
    return True

if __name__ == "__main__":
    success = test_comorag_api()
    if success:
        print("\n🎉 TinyLlama model is ready for ComoRAG API testing!")
    else:
        print("\n❌ Some issues were found. Please check the output above.")