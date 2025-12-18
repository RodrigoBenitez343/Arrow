#!/usr/bin/env python3
"""
Test ComoRAG Streaming Endpoint
"""

import requests
import json
import sys
import time

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.markdown import Markdown
    from rich import print as rprint
    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    console = None

def test_stream(query="Why is the sky blue?", model=None, max_cycles=3):
    # Try to find server on port 8001 (LoOper integration) or 8000 (Standalone)
    ports = [8001, 8000]
    url = None
    
    print("🔍 Looking for OverWatch server...")
    for port in ports:
        try:
            test_url = f"http://localhost:{port}/health"
            requests.get(test_url, timeout=1)
            url = f"http://localhost:{port}/comorag/reason/stream"
            print(f"✅ Found server at http://localhost:{port}")
            break
        except requests.exceptions.ConnectionError:
            continue
            
    if not url:
        print("❌ Could not find running OverWatch server on ports 8001 or 8000")
        return

    payload = {
        "query": query,
        "model": model,
        "max_cycles": max_cycles,
        "memory_size": 1000
    }
    
    print(f"🚀 Sending request to {url}...")
    print(f"Query: {query}")
    print("-" * 50)
    
    try:
        start_time = time.time()
        with requests.post(url, json=payload, stream=True) as response:
            if response.status_code != 200:
                print(f"❌ Error: {response.status_code}")
                print(response.text)
                return

            for line in response.iter_lines():
                if not line:
                    continue
                
                try:
                    data = json.loads(line.decode('utf-8'))
                    msg_type = data.get("type", "unknown")
                    
                    if msg_type == "log":
                        style = data.get("style", "")
                        message = data.get("message", "")
                        if RICH_AVAILABLE:
                            console.print(f"[{style}]{message}[/{style}]")
                        else:
                            print(f"[LOG] {message}")
                            
                    elif msg_type == "cycle_start":
                        cycle = data.get("cycle")
                        total = data.get("total")
                        print(f"\n--- Cycle {cycle}/{total} ---")
                        
                    elif msg_type == "probing_queries":
                        queries = data.get("queries", [])
                        if RICH_AVAILABLE:
                            console.print(Panel("\n".join([f"- {q}" for q in queries]), title="Probing Queries", border_style="cyan"))
                        else:
                            print(f"Probing Queries: {queries}")
                            
                    elif msg_type == "evidence":
                        count = data.get("count", 0)
                        items = data.get("items", [])
                        # Only print count to avoid clutter, or print first few chars
                        # print(f"Received {count} evidence items")
                        pass

                    elif msg_type == "reasoning_output":
                        output = data.get("output", "")
                        if RICH_AVAILABLE:
                            console.print(Panel(Markdown(output), title="Internal Reasoning", border_style="yellow"))
                        else:
                            print(f"Reasoning Output: {output[:100]}...")

                    elif msg_type == "confidence":
                        score = data.get("score", 0.0)
                        # Logged by "log" type usually, but we can double check
                        pass

                    elif msg_type == "final_result":
                        result = data.get("data", {})
                        response_text = result.get("response", "")
                        if RICH_AVAILABLE:
                            console.print(Panel(Markdown(response_text), title="Final Response", border_style="green"))
                        else:
                            print(f"\nFINAL RESPONSE:\n{response_text}")
                        
                        duration = time.time() - start_time
                        print(f"\n✅ Completed in {duration:.2f}s")
                        
                    elif msg_type == "error":
                        print(f"❌ Error: {data.get('message')}")
                        
                except json.JSONDecodeError:
                    print(f"Raw line: {line}")
                    
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to server. Is OverWatch API running on port 8000?")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = sys.argv[1]
    else:
        query = "Explain how photosynthesis works in simple terms."
        
    test_stream(query)
