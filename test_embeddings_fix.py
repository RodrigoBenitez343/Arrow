import requests
import json
import sys

def test_embeddings():
    url = "http://localhost:8000/embeddings"
    inputs = [f"test chunk {i}" for i in range(15)]
    payload = {
        "model": "nomic-embed-text",
        "inputs": inputs
    }
    
    print(f"Sending request to {url} with {len(inputs)} inputs...")
    try:
        response = requests.post(url, json=payload, timeout=60)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if "embeddings" in data:
                embs = data["embeddings"]
                print(f"Received {len(embs)} embeddings")
                if embs:
                    print(f"First embedding length: {len(embs[0])}")
                    print(f"First embedding sample: {embs[0][:5]}")
                    
                    # Check for empty embeddings
                    empty_count = sum(1 for e in embs if not e)
                    print(f"Empty embeddings count: {empty_count}")
            else:
                print("Response JSON does not contain 'embeddings'")
                print(data.keys())
        else:
            print("Error response:")
            print(response.text)
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_embeddings()
