#!/usr/bin/env python3
"""
LocalOperator - Main Entry Point

A powerful desktop automation tool that allows you to record, edit, and replay
complex sequences of mouse clicks, keyboard inputs, scrolling, and drag-and-drop
operations with visual template matching capabilities.

This is the main entry point that launches the GUI application.
"""

import ctypes
import io
import os
import sys

if sys.platform == "win32":
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        else:
            try:
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
        else:
            try:
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    except Exception:
        pass

# Fix for Intel OpenMP runtime duplicate error
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# Force disable MKLDNN for PaddleOCR to avoid OneDNN errors
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_mkldnn"] = "0"

import atexit
import multiprocessing
import subprocess
import threading
import time

# Standard PyInstaller support for multiprocessing
if __name__ == "__main__":
    if sys.platform == "win32" and "--elevated" not in sys.argv:
        try:
            if not bool(ctypes.windll.shell32.IsUserAnAdmin()):
                params = subprocess.list2cmdline(
                    [os.path.abspath(__file__), "--elevated", *sys.argv[1:]]
                )
                ctypes.windll.shell32.ShellExecuteW(
                    None, "runas", sys.executable, params, None, 1
                )
                sys.exit(0)
        except Exception:
            pass
    if "--elevated" in sys.argv:
        try:
            sys.argv.remove("--elevated")
        except Exception:
            pass
    multiprocessing.freeze_support()

# Ensure DLL load path on Windows
if (
    os.name == "nt"
    and hasattr(os, "add_dll_directory")
    and not getattr(sys, "frozen", False)
):
    try:
        import site

        packages = []
        try:
            packages.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            if hasattr(site, "getusersitepackages"):
                packages.append(site.getusersitepackages())
        except Exception:
            pass
        # Also check current venv specifically
        try:
            venv_path = os.path.dirname(os.path.dirname(sys.executable))
            site_pkg = os.path.join(venv_path, "Lib", "site-packages")
            if os.path.isdir(site_pkg):
                packages.append(site_pkg)
        except Exception:
            pass

        for p in packages:
            torch_lib = os.path.join(p, "torch", "lib")
            if os.path.isdir(torch_lib):
                try:
                    os.add_dll_directory(torch_lib)
                    # Also prepend to PATH as a fallback for some DLL loaders
                    os.environ["PATH"] = torch_lib + os.pathsep + os.environ["PATH"]
                except Exception:
                    pass
    except Exception:
        pass

# CRITICAL: Import torch before ANY other module to avoid DLL initialization conflicts (WinError 1114)
# This is a known workaround for PyTorch 2.9+ and PyQt conflicts on Windows.
# Even with 2.8.0, this is the safest order.
try:
    import torch
except Exception:
    torch = None

# Normalize working directory and import path for frozen builds
if getattr(sys, "frozen", False):
    meipass = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    exe_dir = os.path.dirname(sys.executable)
    internal_dir = meipass
    if os.path.basename(internal_dir).lower() != "_internal":
        candidate_internal = os.path.join(exe_dir, "_internal")
        if os.path.isdir(candidate_internal):
            internal_dir = candidate_internal

    try:
        os.chdir(exe_dir)
    except Exception:
        pass

    if exe_dir and exe_dir not in sys.path:
        sys.path.insert(0, exe_dir)
    if internal_dir and internal_dir not in sys.path:
        sys.path.insert(0, internal_dir)
else:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import requests
from AI.config_loader import get_api_url, load_ai_config, should_auto_start_api
from AI.model_cache import get_model_cache, load_models_at_startup
from NGUI.app import main


def _wait_for_api(base_url: str, timeout_s: float = 60.0) -> bool:
    deadline = time.time() + float(timeout_s)
    url = f"{base_url.rstrip('/')}/"
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _verify_and_init_api(api_url: str):
    """Verify API health and initialize components"""
    if _wait_for_api(api_url, timeout_s=90):
        try:
            url = f"{api_url}/comorag/status?init_if_needed=true"
            r = requests.get(url, timeout=15)
            if r.status_code == 200 and r.json().get("ready"):
                print("✅ ComoRAG engine initialized successfully")
                try:
                    print("🔄 Refreshing models cache...")
                    get_model_cache().refresh_models(api_url)
                except Exception as e:
                    print(f"❌ Error refreshing models: {e}")
            else:
                print(f"⚠️ ComoRAG engine not ready: {r.status_code} {r.text}")
        except Exception as e:
            print(f"❌ ComoRAG initialization check failed: {e}")
    else:
        print(f"❌ API did not become ready in time: {api_url}")


def start_api_server():
    """Start the LLM API server"""
    try:
        api_url = get_api_url()
        # Kill any stale API process on our port (app runs elevated, so this works)
        try:
            port = int(os.getenv("API_PORT", "8000"))
            result = subprocess.run(
                f"netstat -ano | findstr :{port} | findstr LISTENING",
                shell=True, capture_output=True, text=True
            )
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split()
                if parts and parts[-1].isdigit():
                    pid = parts[-1]
                    if int(pid) != os.getpid():
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                        print(f"Killed stale PID {pid} on port {port}")
            time.sleep(1)
        except Exception:
            pass

        print(f"🔍 Checking if API server is already running at {api_url}...")
        if _wait_for_api(api_url, timeout_s=2):
            print("✅ LLM API server is already running")

            # We can still trigger the background initialization
            def _init_api_bg():
                _verify_and_init_api(api_url)

            threading.Thread(target=_init_api_bg, daemon=True).start()
            return None

        print("🚀 Starting LLM API server...")
        try:
            load_ai_config()
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            try:
                # In frozen mode, imports might need explicit package naming
                try:
                    from AI.api import app
                except ImportError:
                    # Fallback if _internal is the root in sys.path
                    from api import app

                import json
                import logging

                import uvicorn

                host = os.getenv("TAILSCALE_SERVER_IP", "localhost") or "localhost"
                port = int(os.getenv("API_PORT", "8000"))
                logs_dir = os.path.join(os.path.dirname(sys.executable), "logs")
                try:
                    os.makedirs(logs_dir, exist_ok=True)
                except Exception:
                    pass
                api_log = os.path.join(logs_dir, "api.log")
                log_config = {
                    "version": 1,
                    "disable_existing_loggers": False,
                    "formatters": {
                        "default": {
                            "()": "uvicorn.logging.DefaultFormatter",
                            "fmt": "%(levelprefix)s %(message)s",
                            "use_colors": False,
                        },
                        "access": {
                            "()": "uvicorn.logging.AccessFormatter",
                            "fmt": "%(levelprefix)s %(client_addr)s - '%(request_line)s' %(status_code)s",
                            "use_colors": False,
                        },
                    },
                    "handlers": {
                        "file": {
                            "class": "logging.FileHandler",
                            "filename": api_log,
                            "formatter": "default",
                            "level": "INFO",
                            "encoding": "utf-8",
                        },
                        "access_file": {
                            "class": "logging.FileHandler",
                            "filename": api_log,
                            "formatter": "access",
                            "level": "INFO",
                            "encoding": "utf-8",
                        },
                    },
                    "loggers": {
                        "uvicorn": {
                            "handlers": ["file"],
                            "level": "INFO",
                            "propagate": False,
                        },
                        "uvicorn.error": {
                            "handlers": ["file"],
                            "level": "INFO",
                            "propagate": False,
                        },
                        "uvicorn.access": {
                            "handlers": ["access_file"],
                            "level": "INFO",
                            "propagate": False,
                        },
                    },
                }

                # Try to use actual classes for formatters in frozen environment
                try:
                    import uvicorn.logging

                    log_config["formatters"]["default"]["()"] = (
                        uvicorn.logging.DefaultFormatter
                    )
                    log_config["formatters"]["access"]["()"] = (
                        uvicorn.logging.AccessFormatter
                    )
                except Exception:
                    # Fallback to standard logging formatters
                    log_config["formatters"]["default"] = {
                        "format": "%(levelname)s: %(message)s"
                    }
                    log_config["formatters"]["access"] = {
                        "format": "%(levelname)s: %(message)s"
                    }

                config = uvicorn.Config(
                    app, host=host, port=port, log_level="info", log_config=log_config
                )
                server = uvicorn.Server(config)

                def run_server():
                    try:
                        server.run()
                    except Exception as se:
                        print(f"❌ API server error: {se}")

                t = threading.Thread(target=run_server, daemon=True)
                t.start()
                api_url = get_api_url()
                print("✅ LLM API server started successfully")

                def _init_api_bg():
                    _verify_and_init_api(api_url)

                threading.Thread(target=_init_api_bg, daemon=True).start()
                return t
            except Exception as e:
                print(f"❌ Error starting in-process API server: {e}")
                return None
        else:
            api_path = os.path.join(os.path.dirname(__file__), "AI", "api.py")
            if not os.path.exists(api_path):
                print(f"❌ API server file not found at: {api_path}")
                return None
            creationflags = 0
            if sys.platform == "win32":
                creationflags = 0x08000000  # CREATE_NO_WINDOW
            process = subprocess.Popen(
                [sys.executable, api_path],
                cwd=os.path.dirname(os.path.abspath(__file__)),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=os.environ.copy(),
                creationflags=creationflags,
            )
            import time

            time.sleep(1)  # give it a moment to fail if port is bound
            if process.poll() is None:
                print("✅ LLM API server started successfully")

                # Check for API readiness in a separate thread so we don't block
                # but we can still initialize the comorag engine
                def _init_api_bg():
                    _verify_and_init_api(api_url)

                threading.Thread(target=_init_api_bg, daemon=True).start()

                def cleanup_api():
                    try:
                        print("Stopping LLM API server...")
                        # In Windows, we might need taskkill to kill the whole process tree
                        if sys.platform == "win32":
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                                capture_output=True,
                            )
                        else:
                            process.terminate()
                            process.wait(timeout=2)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass

                atexit.register(cleanup_api)

                # We also need a thread to forward the output to stdout/stderr
                # so it's visible in the console, since we used PIPE
                def _forward_output(pipe, sys_stream, prefix=""):
                    try:
                        for line in iter(pipe.readline, ""):
                            if not line:
                                break
                            sys_stream.write(f"{prefix}{line}")
                            sys_stream.flush()
                    except Exception:
                        pass

                threading.Thread(
                    target=_forward_output,
                    args=(process.stdout, sys.stdout, "[API] "),
                    daemon=True,
                ).start()
                threading.Thread(
                    target=_forward_output,
                    args=(process.stderr, sys.stderr, "[API ERR] "),
                    daemon=True,
                ).start()

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


def run_app():
    """Main function to start the application logic"""
    # Load AI configuration
    print("LocalOperator starting up...")
    print("Loading AI configuration...")
    config = load_ai_config()

    if should_auto_start_api():
        print("🔧 Auto-start API enabled - starting LLM API server...")
        start_api_server_async()
    else:
        print(
            "ℹ️  Auto-start API disabled - LLM API server will not be started automatically"
        )

    # Preload models asynchronously before starting the GUI
    load_models_at_startup()

    # Start the GUI application
    main()


def start_api_server_async():
    """Start the API server in a separate thread"""

    def run_server():
        start_api_server()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    return server_thread


if __name__ == "__main__":
    # Standard PyInstaller support for multiprocessing
    multiprocessing.freeze_support()

    # CRITICAL: Check if we should run in "interpreter mode" for code nodes in frozen builds.
    # This prevents recursive LoOper launches by allowing LoOper.exe to act as a Python
    # interpreter for its own bundled environment when passed a .py file.
    if (
        len(sys.argv) > 1
        and sys.argv[1].endswith(".py")
        and os.path.exists(sys.argv[1])
    ):
        script_path = sys.argv[1]
        # Shift arguments so the script sees itself as sys.argv[0]
        sys.argv = sys.argv[1:]

        # Add the script's directory to sys.path
        script_dir = os.path.dirname(os.path.abspath(script_path))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        try:
            with open(script_path, "rb") as f:
                code_content = f.read()
            # Execute the script in the context of the bundled environment
            exec_globals = {
                "__name__": "__main__",
                "__file__": script_path,
                "__builtins__": __builtins__,
            }
            exec(code_content, exec_globals)
            sys.exit(0)
        except Exception as e:
            print(f"Error executing script {script_path}: {e}")
            import traceback

            traceback.print_exc()
            sys.exit(1)

    # Start the application
    run_app()
