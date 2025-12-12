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
import atexit

# Ensure torch DLLs are loadable on Windows
# Fix for Intel OpenMP runtime duplicate error
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Ensure DLL load path on Windows
if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
    try:
        import site
        packages = []
        try:
            packages.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            if hasattr(site, 'getusersitepackages'):
                packages.append(site.getusersitepackages())
        except Exception:
            pass
        # Also check current venv specifically
        try:
            venv_path = os.path.dirname(os.path.dirname(sys.executable)) 
            site_pkg = os.path.join(venv_path, 'Lib', 'site-packages')
            if os.path.isdir(site_pkg):
                packages.append(site_pkg)
        except Exception:
            pass
            
        for p in packages:
            torch_lib = os.path.join(p, 'torch', 'lib')
            if os.path.isdir(torch_lib):
                try:
                    os.add_dll_directory(torch_lib)
                    # Also prepend to PATH as a fallback for some DLL loaders
                    os.environ['PATH'] = torch_lib + os.pathsep + os.environ['PATH']
                except Exception:
                    pass
    except Exception:
        pass

# Normalize working directory and import path for frozen builds
if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    try:
        os.chdir(exe_dir)
    except Exception:
        pass
    sys.path.insert(0, exe_dir)
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from NGUI.app import main
from AI.model_cache import load_models_at_startup
from AI.config_loader import load_ai_config, should_auto_start_api, get_api_url
import requests

def start_api_server():
    """Start the LLM API server"""
    try:
        print("🚀 Starting LLM API server...")
        if getattr(sys, 'frozen', False):
            try:
                ai_dir = os.path.join(os.getcwd(), 'AI')
                if ai_dir not in sys.path:
                    sys.path.insert(0, ai_dir)
                from AI.api import app  # type: ignore
                import uvicorn, logging, json
                host = os.getenv("TAILSCALE_SERVER_IP", "localhost") or "localhost"
                port = int(os.getenv("API_PORT", "8000"))
                logs_dir = os.path.join(os.path.dirname(sys.executable), 'logs')
                try:
                    os.makedirs(logs_dir, exist_ok=True)
                except Exception:
                    pass
                api_log = os.path.join(logs_dir, 'api.log')
                log_config = {
                    "version": 1,
                    "disable_existing_loggers": False,
                    "formatters": {
                        "default": {
                            "()": "uvicorn.logging.DefaultFormatter",
                            "fmt": "%(levelprefix)s %(message)s",
                            "use_colors": False
                        },
                        "access": {
                            "()": "uvicorn.logging.AccessFormatter",
                            "fmt": "%(levelprefix)s %(client_addr)s - '%(request_line)s' %(status_code)s",
                            "use_colors": False
                        }
                    },
                    "handlers": {
                        "file": {
                            "class": "logging.FileHandler",
                            "filename": api_log,
                            "formatter": "default",
                            "level": "INFO",
                            "encoding": "utf-8"
                        },
                        "access_file": {
                            "class": "logging.FileHandler",
                            "filename": api_log,
                            "formatter": "access",
                            "level": "INFO",
                            "encoding": "utf-8"
                        }
                    },
                    "loggers": {
                        "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
                        "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
                        "uvicorn.access": {"handlers": ["access_file"], "level": "INFO", "propagate": False}
                    }
                }
                config = uvicorn.Config(app, host=host, port=port, log_level="info", log_config=log_config)
                server = uvicorn.Server(config)

                def run_server():
                    try:
                        server.run()
                    except Exception as se:
                        print(f"❌ API server error: {se}")

                t = threading.Thread(target=run_server, daemon=True)
                t.start()
                time.sleep(3)
                print("✅ LLM API server started successfully")
                return t
            except Exception as e:
                print(f"❌ Error starting in-process API server: {e}")
                return None
        else:
            api_path = os.path.join(os.path.dirname(__file__), "AI", "api.py")
            if not os.path.exists(api_path):
                print(f"❌ API server file not found at: {api_path}")
                return None
            process = subprocess.Popen(
                [sys.executable, api_path],
                cwd=os.path.dirname(api_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            time.sleep(3)
            if process.poll() is None:
                print("✅ LLM API server started successfully")
                
                def cleanup_api():
                    try:
                        print("Stopping LLM API server...")
                        process.terminate()
                        process.wait(timeout=2)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass
                            
                atexit.register(cleanup_api)
                return process
            else:
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
    
    if should_auto_start_api():
        print("🔧 Auto-start API enabled - starting LLM API server...")
        api_thread = start_api_server_async()
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
    
    # Preload models asynchronously before starting the GUI
    load_models_at_startup()
    
    # Start the GUI application
    main()
