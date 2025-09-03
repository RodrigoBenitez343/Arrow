
"""
Test script to verify OverWatch API connection for LLM nodes
"""

import sys
import os

# Add the current directory to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from AI.consult import OllamaClient
from AI.config_loader import load_ai_config

def test_overwatch_connection():
    """Test connection to OverWatch API"""
    print("Testing OverWatch API connection...")
    
    # Load configuration
    config = load_ai_config()
    api_url = config.get('OVERWATCH_API_URL', 'http://100.125.184.115:8000')
    default_model = config.get('DEFAULT_MODEL', 'llama3.1:8b-instruct-q8_0')
    
    print(f"API URL: {api_url}")
    print(f"Default Model: {default_model}")
    
    # Create client with shorter timeout for testing
    client = OllamaClient(base_url=api_url, debug=True)
    # Reduce timeout for testing
    client.session.timeout = 30
    
    # Test health check
    print("\n1. Testing health check...")
    health = client.health_check()
    print(f"Health check result: {health}")
    
    # Test model listing
    print("\n2. Testing model listing...")
    models = client.list_models()
    print(f"Available models: {models}")
    
    # Test text generation
    print("\n3. Testing text generation...")
    
    try:
        # Use a simpler test first
        response = client.generate(
            model=default_model,
            prompt="Hello",
            temperature=0.7,
            max_tokens=50
        )
        print(f"Generation response: {response}")
        
        if 'error' in response:
            print(f"❌ Error in generation: {response['error']}")
            return False
        else:
            print("✅ Text generation successful!")
            return True
            
    except Exception as e:
        print(f"❌ Exception during generation: {e}")
        return False

def test_comorag_integration():
    """Test ComoRAG integration if enabled"""
    print("\n4. Testing ComoRAG integration...")
    
    config = load_ai_config()
    api_url = config.get('OVERWATCH_API_URL', 'http://100.125.184.115:8000')
    default_model = config.get('DEFAULT_MODEL', 'llama3.1:8b-instruct-q8_0')
    
    # Create client with ComoRAG enabled
    os.environ['USE_COMORAG'] = 'true'
    client = OllamaClient(base_url=api_url, debug=True)
    
    # Add some context
    client.add_context({"type": "test", "content": "This is test context for ComoRAG"})
    
    test_prompt = "Based on the context, what kind of test is this?"
    
    try:
        response = client.generate(
            model=default_model,
            prompt=test_prompt,
            temperature=0.7,
            max_tokens=100
        )
        print(f"ComoRAG response: {response}")
        
        if 'error' in response:
            print(f"❌ Error in ComoRAG: {response['error']}")
            return False
        else:
            print("✅ ComoRAG integration successful!")
            return True
            
    except Exception as e:
        print(f"❌ Exception during ComoRAG test: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("OverWatch API Connection Test")
    print("=" * 50)
    
    success = test_overwatch_connection()
    
    if success:
        test_comorag_integration()
    
    print("\n" + "=" * 50)
    if success:
        print("✅ OverWatch API connection test completed successfully!")
        print("The LLM nodes should now work with the OverWatch API.")
    else:
        print("❌ OverWatch API connection test failed!")
        print("Please check the API server and configuration.")
    print("=" * 50)