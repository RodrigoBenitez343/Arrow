
import requests
import json
import sys
import os

def check_ollama_direct():
    base_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    print(f"Checking Ollama at {base_url}...")
    
    # 1. Check tags
    try:
        resp = requests.get(f"{base_url}/api/tags")
        print(f"Tags status: {resp.status_code}")
        if resp.status_code == 200:
            models = [m['name'] for m in resp.json().get('models', [])]
            print(f"Available models: {models}")
            if 'nomic-embed-text:latest' not in models and 'nomic-embed-text' not in models:
                print("WARNING: nomic-embed-text not found!")
        else:
            print(f"Failed to get tags: {resp.text}")
    except Exception as e:
        print(f"Ollama direct connection failed: {e}")
        return

    # 2. Test Embeddings Direct (New Endpoint)
    print("\nTesting /api/embed direct...")
    payload = {
        "model": "nomic-embed-text",
        "input": ["Hello world", "Test sentence"]
    }
    try:
        resp = requests.post(f"{base_url}/api/embed", json=payload)
        print(f"/api/embed status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            embs = data.get("embeddings", [])
            print(f"Embeddings count: {len(embs)}")
            if embs:
                print(f"First vector length: {len(embs[0])}")
            else:
                print("Response JSON:", data)
        else:
            print(f"Error: {resp.text}")
    except Exception as e:
        print(f"/api/embed failed: {e}")

    # 3. Test Embeddings Direct (Legacy Endpoint - Single)
    print("\nTesting /api/embeddings direct (single)...")
    payload = {
        "model": "nomic-embed-text",
        "prompt": "Hello world"
    }
    try:
        resp = requests.post(f"{base_url}/api/embeddings", json=payload)
        print(f"/api/embeddings status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            emb = data.get("embedding", [])
            print(f"Embedding length: {len(emb)}")
        else:
            print(f"Error: {resp.text}")
    except Exception as e:
        print(f"/api/embeddings failed: {e}")

    # 4. Test Empty Strings
    print("\nTesting empty strings direct...")
    payload = {
        "model": "nomic-embed-text",
        "input": ["", "   "]
    }
    try:
        resp = requests.post(f"{base_url}/api/embed", json=payload)
        print(f"Empty strings status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            embs = data.get("embeddings", [])
            print(f"Empty string embeddings count: {len(embs)}")
            if embs:
                print(f"First vector length: {len(embs[0])}")
    except Exception as e:
        print(f"Empty strings test failed: {e}")

def check_api_gateway():
    base_url = "http://localhost:8000"
    print(f"\nChecking API Gateway at {base_url}...")
    
    # 1. Test /embeddings endpoint
    print("Testing /embeddings via Gateway...")
    payload = {
        "model": "nomic-embed-text",
        "inputs": ["Hello world", "Test sentence"]
    }
    try:
        resp = requests.post(f"{base_url}/embeddings", json=payload)
        print(f"Gateway /embeddings status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            embs = data.get("embeddings", [])
            print(f"Gateway Embeddings count: {len(embs)}")
            if embs:
                print(f"Gateway First vector length: {len(embs[0])}")
                # Print vector lengths summary
                lengths = [len(v) if isinstance(v, list) else 'invalid' for v in embs]
                print(f"Vector lengths: {lengths}")
                
                if len(embs[0]) == 0:
                    print("WARNING: Received empty vectors from Gateway!")
                    print(f"Full Gateway Response: {json.dumps(data, indent=2)}")
            else:
                print("Gateway returned no embeddings list")
                print(f"Full Gateway Response: {json.dumps(data, indent=2)}")
        else:
            print(f"Gateway Error: {resp.text}")
    except Exception as e:
        print(f"Gateway request failed: {e}")

if __name__ == "__main__":
    check_ollama_direct()
    check_api_gateway()
