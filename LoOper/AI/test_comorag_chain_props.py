import json
import os
import time
import sys
import requests

def load_chain_props():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "chains", "agenticchain1.json")
    with open(os.path.abspath(p), "r", encoding="utf-8") as f:
        data = json.load(f)
    nodes = data.get("llm_nodes") or []
    node = nodes[0] if nodes else {}
    return node

def make_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Accept": "application/json", "Connection": "close"})
    return s

def request(s, base_url, method, path, **kwargs):
    url = f"{base_url}{path if path.startswith('/') else '/' + path}"
    r = s.request(method, url, **kwargs)
    return r

def main():
    node = load_chain_props()
    base_url = str(node.get("api_url") or "http://localhost:8000").rstrip("/")
    s = make_session()

    print("Base URL:", base_url)

    try:
        r = request(s, base_url, "GET", "/health")
        print("health", r.status_code, r.text)
    except Exception as e:
        print("health_error", str(e))

    try:
        r = request(s, base_url, "GET", "/comorag/status")
        print("status", r.status_code, r.text)
    except Exception as e:
        print("status_error", str(e))

    try:
        r = request(s, base_url, "POST", "/comorag/init", json={"memory_size": int(node.get("memory_size", 1000))}, timeout=15)
        print("init", r.status_code, r.text)
    except Exception as e:
        print("init_error", str(e))
    try:
        r = request(s, base_url, "GET", "/comorag/status?init_if_needed=true", timeout=10)
        print("status_after_init", r.status_code, r.text)
    except Exception as e:
        print("status_after_init_error", str(e))

    try:
        r = request(s, base_url, "DELETE", "/comorag/memory", timeout=10)
        print("clear", r.status_code, r.text)
    except Exception as e:
        print("clear_error", str(e))

    try:
        mem_payload = {
            "content": "Test vision memory entry",
            "source": "advanced_vision",
            "metadata": {"timestamp": time.time(), "type": "test_entry", "monitor": 1},
            "image_data": None
        }
        r = request(s, base_url, "POST", "/comorag/memory", json=mem_payload, timeout=10)
        print("add_memory", r.status_code, r.text)
    except Exception as e:
        print("add_memory_error", str(e))

    try:
        r = request(s, base_url, "GET", "/comorag/memory", timeout=10)
        print("memory", r.status_code, r.text)
    except Exception as e:
        print("memory_error", str(e))

    try:
        reason_payload = {
            "query": node.get("prompt") or "what is the user doing?",
            "model": node.get("model") or "llama3.2:1b",
            "max_cycles": int(node.get("max_cycles", 5)),
            "memory_size": node.get("memory_size", 1000),
            "max_tokens": node.get("max_tokens"),
            "temperature": node.get("temperature")
        }
        r = request(s, base_url, "POST", "/comorag/reason", json=reason_payload, timeout=20)
        print("reason", r.status_code, r.text)
    except Exception as e:
        print("reason_error", str(e))

    try:
        reason_payload = {
            "query": node.get("prompt") or "what is the user doing?",
            "model": node.get("model") or "llama3.2:1b",
            "max_cycles": int(node.get("max_cycles", 5)),
            "memory_size": node.get("memory_size", 1000),
            "max_tokens": node.get("max_tokens"),
            "temperature": node.get("temperature")
        }
        r = request(s, base_url, "POST", "/comorag/reason/stream", json=reason_payload, timeout=20, stream=True)
        print("reason_stream", r.status_code)
        try:
            for line in r.iter_lines(decode_unicode=True):
                if line:
                    print("stream", line)
                    break
        except Exception as se:
            print("stream_read_error", str(se))
    except Exception as e:
        print("reason_stream_error", str(e))

if __name__ == "__main__":
    main()
