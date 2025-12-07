import requests
import sys

API_URL = "http://localhost:8000"
OLLAMA_URL = "http://localhost:11434"

def check_api():
    print(f"Checking API at {API_URL}...")
    try:
        r = requests.get(f"{API_URL}/health", timeout=5)
        print(f"API Health: {r.status_code}")
        print(r.text)
    except Exception as e:
        print(f"API Check Failed: {e}")

def check_ollama():
    print(f"Checking Ollama at {OLLAMA_URL}...")
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        print(f"Ollama Status: {r.status_code}")
        if r.status_code == 200:
            models = r.json().get("models", [])
            print(f"Ollama Models ({len(models)}):")
            for m in models:
                print(f"  - {m.get('name')}")
    except Exception as e:
        print(f"Ollama Check Failed: {e}")

if __name__ == "__main__":
    check_api()
    print("-" * 20)
    check_ollama()
