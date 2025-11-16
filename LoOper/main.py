#!/usr/bin/env python3
"""
LocalOperator - Main Entry Point

A powerful desktop automation tool that allows you to record, edit, and replay 
complex sequences of mouse clicks, keyboard inputs, scrolling, and drag-and-drop 
operations with visual template matching capabilities.

This is the main entry point that launches the GUI application.
"""

import sys
import os
import threading
import subprocess
import time

# Add the current directory to the Python path to ensure proper imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from NGUI.app import main
from AI.model_cache import load_models_at_startup
from AI.config_loader import load_ai_config, should_auto_start_api, get_api_url
import requests

def start_api_server():
    """Start the LLM API server in a separate process"""
    try:
        print("🚀 Starting LLM API server...")
        
        # Get the path to the AI api.py file
        api_path = os.path.join(os.path.dirname(__file__), "AI", "api.py")
        
        if not os.path.exists(api_path):
            print(f"❌ API server file not found at: {api_path}")
            return None
        
        # Start the API server as a subprocess
        process = subprocess.Popen(
            [sys.executable, api_path],
            cwd=os.path.dirname(api_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Give the server a moment to start
        time.sleep(3)
        
        # Check if the process is still running
        if process.poll() is None:
            print("✅ LLM API server started successfully")
            return process
        else:
            # Get error output if the process failed
            stdout, stderr = process.communicate()
            print("❌ LLM API server failed to start")
            if stderr:
                print(f"Error output: {stderr}")
            if stdout:
                print(f"Standard output: {stdout}")
            return None
            
    except Exception as e:
        print(f"❌ Error starting LLM API server: {e}")
        return None
            
    except Exception as e:
        print(f"❌ Failed to start LLM API server: {e}")
        return None

def start_api_server_async():
    """Start the API server in a separate thread"""
    def run_server():
        start_api_server()
    
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    return server_thread

if __name__ == "__main__":
    # Load AI configuration
    print("LocalOperator starting up...")
    print("Loading AI configuration...")
    config = load_ai_config()
    
    # Check if we should auto-start the API server
    if should_auto_start_api():
        print("🔧 Auto-start API enabled - starting LLM API server...")
        api_thread = start_api_server_async()
        # Give the API server time to initialize
        time.sleep(3)
        try:
            url = f"{get_api_url()}/health"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                print("✅ API health verified")
            else:
                print(f"⚠️ API health check returned status {r.status_code}")
        except Exception as e:
            print(f"❌ API health check failed: {e}")
    else:
        print("ℹ️  Auto-start API disabled - LLM API server will not be started automatically")
        print("   You can enable this in the AI settings or set AUTO_START_API=true in environment")
    
    # Preload models asynchronously before starting the GUI
    load_models_at_startup()
    
    # Start the GUI application
    main()
