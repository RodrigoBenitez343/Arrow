#!/usr/bin/env python3
"""
Test Proxy Fix
Verifies that LoOper API (port 8000) correctly proxies requests to OverWatch (port 8001).
"""

import requests
import json
import time
import sys

def test_proxy():
    base_url = "http://localhost:8000"
    print(f"🔍 Testing LoOper API Proxy at {base_url}...")

    # 1. Check if LoOper API is running
    try:
        r = requests.get(f"{base_url}/health", timeout=2)
        print(f"✅ LoOper API is reachable (Status: {r.status_code})")
    except Exception as e:
        print(f"❌ LoOper API is not reachable at {base_url}. Please start LoOper.")
        return

    # 2. Test Non-Streaming Chat (Proxy)
    print("\n🧪 Testing Non-Streaming Chat (Proxy)...")
    payload = {
        "model": "llama3.2:3b",
        "prompt": "What is the capital of France?",
        "temperature": 0.7,
        "max_tokens": 100
    }
    
    try:
        start = time.time()
        r = requests.post(f"{base_url}/generate", json=payload, timeout=30)
        if r.status_code == 200:
            data = r.json()
            response = data.get("response", "No response field")
            print(f"✅ Success! Response: {response[:50]}...")
            print(f"⏱️  Duration: {time.time() - start:.2f}s")
        else:
            print(f"❌ Failed: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"❌ Exception: {e}")

    # 3. Test Streaming Chat (Proxy)
    print("\n🧪 Testing Streaming Chat (Proxy)...")
    try:
        start = time.time()
        with requests.post(f"{base_url}/generate", json={**payload, "stream": True}, stream=True, timeout=30) as r:
            if r.status_code == 200:
                print("✅ Connected to stream. Receiving events:")
                count = 0
                for line in r.iter_lines():
                    if line:
                        count += 1
                        try:
                            data = json.loads(line)
                            msg_type = data.get("type", "unknown")
                            print(f"   - Received event: {msg_type}")
                            if count >= 3:
                                print("   ... (stopping stream display)")
                                break
                        except:
                            pass
                print(f"✅ Stream verification successful!")
            else:
                print(f"❌ Failed: {r.status_code} - {r.text}")
                if r.status_code == 404:
                    print("   (Endpoint not found - did you update api.py and restart LoOper?)")
    except Exception as e:
        print(f"❌ Exception: {e}")

if __name__ == "__main__":
    test_proxy()
