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

# ── CRITICAL: Set Windows DPI awareness BEFORE any Win32 GDI/USER32 calls ──
# This ensures pyautogui.size(), pyautogui.screenshot(), and mss.capture()
# all agree on the coordinate space (physical pixels) on high-DPI displays.
# Without this, pyautogui.size() returns LOGICAL pixels while screenshots
# return PHYSICAL pixels, causing template matching to find coordinates in
# the wrong space and clicks to land at incorrect positions.
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Legacy fallback (Vista+)
        except Exception:
            pass

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
import logging
from logging_setup import setup_logging, uvicorn_log_config

logger = logging.getLogger("looper.main")


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
    # In compiled (frozen) mode the API can take longer to start because
    # of PyInstaller module-loading overhead, so use a generous timeout.
    timeout = 120 if getattr(sys, "frozen", False) else 90
    if _wait_for_api(api_url, timeout_s=timeout):
        try:
            logger.info("Refreshing models cache...")
            get_model_cache().refresh_models(api_url)
        except Exception as e:
            logger.error(f"Error refreshing models: {e}")
    else:
        logger.error(f"API did not become ready in time: {api_url}")


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
                        logger.info(f"Killed stale PID {pid} on port {port}")
            time.sleep(1)
        except Exception:
            pass

        logger.info(f"Checking if API server is already running at {api_url}...")
        if _wait_for_api(api_url, timeout_s=2):
            logger.info("LLM API server is already running")

            # We can still trigger the background initialization
            def _init_api_bg():
                _verify_and_init_api(api_url)

            threading.Thread(target=_init_api_bg, daemon=True).start()
            return None

        logger.info("Starting LLM API server...")
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

                import uvicorn

                # Local-only API gateway; remote access moved to Agent Mode
                host = "127.0.0.1"
                port = int(os.getenv("API_PORT", "8000"))
                # Unified telemetry: uvicorn loggers propagate to the root
                # dispatch (console + logs/automation.log + session md).
                config = uvicorn.Config(
                    app, host=host, port=port, log_level="info",
                    log_config=uvicorn_log_config(),
                )
                server = uvicorn.Server(config)

                def run_server():
                    try:
                        server.run()
                    except Exception as se:
                        logger.error(f"API server error: {se}")

                t = threading.Thread(target=run_server, daemon=True)
                t.start()
                api_url = get_api_url()
                logger.info("LLM API server started successfully")

                def _init_api_bg():
                    _verify_and_init_api(api_url)

                threading.Thread(target=_init_api_bg, daemon=True).start()
                return t
            except Exception as e:
                logger.error(f"Error starting in-process API server: {e}")
                return None
        else:
            api_path = os.path.join(os.path.dirname(__file__), "AI", "api.py")
            if not os.path.exists(api_path):
                logger.error(f"API server file not found at: {api_path}")
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
                logger.info("LLM API server started successfully")

                # Check for API readiness in a separate thread so we don't block
                def _init_api_bg():
                    _verify_and_init_api(api_url)

                threading.Thread(target=_init_api_bg, daemon=True).start()

                def cleanup_api():
                    try:
                        logger.info("Stopping LLM API server...")
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
                logger.error("LLM API server failed to start")
                if stderr:
                    logger.error(f"Error output: {stderr}")
                if stdout:
                    logger.error(f"Standard output: {stdout}")
                return None
    except Exception as e:
        logger.error(f"Error starting LLM API server: {e}")
        return None


def _show_license_error(title: str, message: str):
    """Show a Windows message box for license errors (reliable in frozen builds)."""
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        logger.error(f"ERROR: {title} - {message}")


def _check_license() -> bool:
    """Check that a valid hardware-bound license exists.

    In frozen (compiled) builds the license.bin is expected next to the
    executable.  In development mode we skip the check for convenience.
    """
    # Skip in development mode
    if not getattr(sys, "frozen", False):
        return True

    exe_dir = os.path.dirname(sys.executable)
    license_path = os.path.join(exe_dir, "license.bin")

    if not os.path.exists(license_path):
        _show_license_error(
            "License Not Found",
            "arrow license not found.\n\n"
            "Please install arrow using the official installer, or contact\n"
            "support to obtain a valid license.",
        )
        return False

    # Try to run the validator to check the license
    validator_path = os.path.join(exe_dir, "license_validator.exe")
    if os.path.exists(validator_path):
        try:
            result = subprocess.run(
                [validator_path, "--check", exe_dir],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                return True
            logger.error(f"License validation failed (exit code {result.returncode}).")
            _show_license_error(
                "License Invalid",
                "The arrow license on this machine is invalid or has been tampered with.\n\n"
                "Please reinstall arrow or contact support.",
            )
        except Exception as e:
            logger.error(f"License validator error: {e}")
    else:
        logger.warning("license_validator.exe not found, checking license.bin header...")
        try:
            with open(license_path, "rb") as f:
                data = f.read()
            if data.startswith(b"ARWLIC\x00\x01"):
                return True
        except Exception:
            pass
        _show_license_error(
            "License Invalid",
            "The arrow license file is corrupted or missing.\n\n"
            "Please reinstall arrow or contact support.",
        )
        return False

    return False


def run_app():
    """Main function to start the application logic"""
    # Install the unified telemetry dispatch (console colors + logs/ + session md)
    setup_logging()
    # Validate license before starting (skip in dev mode)
    if not _check_license():
        logger.error("Exiting due to invalid license.")
        sys.exit(1)

    # Load AI configuration
    logger.info("LocalOperator starting up...")
    logger.info("Loading AI configuration...")
    config = load_ai_config()

    if should_auto_start_api():
        logger.info("Auto-start API enabled - starting LLM API server...")
        start_api_server_async()
    else:
        logger.info(
            "Auto-start API disabled - LLM API server will not be started automatically"
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
    # Also handle -m module invocations (e.g. pip) so subprocess calls don't spawn new GUI instances.
    if (
        len(sys.argv) > 1
        and sys.argv[1] == '-m'
        and len(sys.argv) > 2
    ):
        module_name = sys.argv[2]
        sys.argv = sys.argv[2:]
        try:
            import runpy
            runpy.run_module(module_name, run_name="__main__", alter_sys=True)
            sys.exit(0)
        except Exception as e:
            print(f"Error running module {module_name}: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

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
