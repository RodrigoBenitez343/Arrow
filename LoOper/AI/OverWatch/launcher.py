#!/usr/bin/env python3
"""
OverWatch Launcher

Launcher script for OverWatch vLLM API and GUI application.
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Add the OverWatch directory to Python path
overwatch_dir = Path(__file__).parent
sys.path.insert(0, str(overwatch_dir))


def setup_logging(level=logging.INFO):
    """Setup basic logging"""
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


def launch_api_server(args):
    """Launch the FastAPI server"""
    print("🚀 Starting OverWatch API Server...")
    
    try:
        # Import and run the main server
        from main import app
        import uvicorn
        
        # Load settings
        from config.settings import load_settings, setup_logging as setup_app_logging
        
        settings = load_settings()
        setup_app_logging(settings)
        
        # Override settings with command line arguments
        if args.host:
            settings.api.host = args.host
        if args.port:
            settings.api.port = args.port
        if args.debug is not None:
            settings.api.debug = args.debug
        
        print(f"📡 Server will start on http://{settings.api.host}:{settings.api.port}")
        print(f"🔧 Debug mode: {'ON' if settings.api.debug else 'OFF'}")
        
        # Start the server
        uvicorn.run(
            "main:app",
            host=settings.api.host,
            port=settings.api.port,
            reload=settings.api.debug,
            log_level="debug" if settings.api.debug else "info"
        )
        
    except ImportError as e:
        print(f"❌ Failed to import required modules: {e}")
        print("💡 Make sure you have installed all dependencies with: pip install -r requirements.txt")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to start API server: {e}")
        sys.exit(1)


def launch_gui(args):
    """Launch the PyQt5 GUI application"""
    print("🖥️  Starting OverWatch GUI Application...")
    
    try:
        # Check if PyQt5 is available
        from PyQt5.QtWidgets import QApplication
        from ui.app import OverWatchApp
        
        # Create QApplication
        app = QApplication(sys.argv)
        app.setApplicationName("OverWatch")
        app.setApplicationVersion("1.0.0")
        app.setOrganizationName("LoOper")
        
        # Create and show the main application
        overwatch_app = OverWatchApp()
        
        # Override settings with command line arguments
        if args.api_url:
            # This would need to be implemented in the GUI app
            print(f"🔗 Using API URL: {args.api_url}")
        
        print("✅ GUI application started successfully")
        
        # Run the application
        sys.exit(app.exec_())
        
    except ImportError as e:
        print(f"❌ Failed to import PyQt5: {e}")
        print("💡 Make sure you have installed PyQt5 with: pip install PyQt5")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to start GUI application: {e}")
        sys.exit(1)


def launch_both(args):
    """Launch both API server and GUI (in separate processes)"""
    print("🚀 Starting both OverWatch API Server and GUI...")
    
    import subprocess
    import time
    
    try:
        # Start API server in background
        print("📡 Starting API server...")
        api_cmd = [sys.executable, __file__, "api"]
        
        if args.host:
            api_cmd.extend(["--host", args.host])
        if args.port:
            api_cmd.extend(["--port", str(args.port)])
        if args.debug:
            api_cmd.append("--debug")
        
        # Use CREATE_NO_WINDOW on Windows to prevent console popup
        creationflags = 0
        if sys.platform == "win32":
            creationflags = 0x08000000
            
        api_process = subprocess.Popen(api_cmd, creationflags=creationflags)
        
        # Wait a moment for the API server to start
        print("⏳ Waiting for API server to initialize...")
        time.sleep(3)
        
        # Start GUI
        print("🖥️  Starting GUI application...")
        gui_cmd = [sys.executable, __file__, "gui"]
        
        if args.api_url:
            gui_cmd.extend(["--api-url", args.api_url])
        
        gui_process = subprocess.Popen(gui_cmd, creationflags=creationflags)
        
        print("✅ Both applications started successfully")
        print("💡 Close this terminal to stop both applications")
        
        # Wait for GUI to finish
        gui_process.wait()
        
        # Terminate API server when GUI closes
        print("🛑 Stopping API server...")
        api_process.terminate()
        api_process.wait()
        
    except KeyboardInterrupt:
        print("\n🛑 Stopping applications...")
        if 'api_process' in locals():
            api_process.terminate()
        if 'gui_process' in locals():
            gui_process.terminate()
    except Exception as e:
        print(f"❌ Failed to start applications: {e}")
        sys.exit(1)


def check_dependencies():
    """Check if required dependencies are installed"""
    print("🔍 Checking dependencies...")
    
    required_packages = {
        'fastapi': 'FastAPI',
        'uvicorn': 'Uvicorn',
        'vllm': 'vLLM',
        'transformers': 'Transformers',
        'sentence_transformers': 'Sentence Transformers',
        'PyQt5': 'PyQt5',
        'requests': 'Requests'
    }
    
    missing_packages = []
    
    for package, name in required_packages.items():
        try:
            __import__(package)
            print(f"✅ {name} - OK")
        except ImportError:
            print(f"❌ {name} - MISSING")
            missing_packages.append(package)
    
    if missing_packages:
        print(f"\n💡 Install missing packages with:")
        print(f"pip install {' '.join(missing_packages)}")
        print(f"\nOr install all dependencies with:")
        print(f"pip install -r requirements.txt")
        return False
    
    print("\n✅ All dependencies are installed!")
    return True


def show_status():
    """Show system status and configuration"""
    print("📊 OverWatch System Status")
    print("=" * 40)
    
    # Python version
    print(f"🐍 Python: {sys.version.split()[0]}")
    
    # Check GPU availability
    try:
        import torch
        if torch.cuda.is_available():
            gpu_count = torch.cuda.device_count()
            gpu_name = torch.cuda.get_device_name(0) if gpu_count > 0 else "Unknown"
            print(f"🎮 GPU: {gpu_name} ({gpu_count} device(s))")
        else:
            print("🎮 GPU: Not available (CPU only)")
    except Exception:
        print("🎮 GPU: PyTorch not installed")
    
    # Memory info
    try:
        import psutil
        memory = psutil.virtual_memory()
        print(f"💾 RAM: {memory.total // (1024**3)} GB total, {memory.available // (1024**3)} GB available")
    except ImportError:
        print("💾 RAM: psutil not installed")
    
    # Configuration
    try:
        from config.settings import load_settings
        settings = load_settings()
        print(f"⚙️  API: {settings.api.host}:{settings.api.port}")
        print(f"🧠 vLLM: GPU Memory {settings.vllm.gpu_memory_utilization*100:.0f}%")
    except Exception as e:
        print(f"⚙️  Configuration: Error loading ({e})")
    
    print("=" * 40)


def main():
    """Main launcher function"""
    parser = argparse.ArgumentParser(
        description="OverWatch vLLM API and GUI Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python launcher.py api                    # Start API server only
  python launcher.py gui                    # Start GUI only
  python launcher.py both                   # Start both API and GUI
  python launcher.py api --port 8080        # Start API on custom port
  python launcher.py gui --api-url http://localhost:8080  # GUI with custom API URL
  python launcher.py check                  # Check dependencies
  python launcher.py status                 # Show system status
        """
    )
    
    parser.add_argument(
        "mode",
        choices=["api", "gui", "both", "check", "status"],
        help="Launch mode"
    )
    
    # API server arguments
    parser.add_argument(
        "--host",
        type=str,
        help="API server host (default: from config)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        help="API server port (default: from config)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode"
    )
    
    # GUI arguments
    parser.add_argument(
        "--api-url",
        type=str,
        help="API server URL for GUI (default: from config)"
    )
    
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level"
    )
    
    args = parser.parse_args()
    
    # Setup logging
    log_level = getattr(logging, args.log_level)
    setup_logging(log_level)
    
    # Print banner
    print("")
    print("🔍 OverWatch - vLLM API")
    print("=" * 40)
    
    # Execute based on mode
    if args.mode == "api":
        launch_api_server(args)
    elif args.mode == "gui":
        launch_gui(args)
    elif args.mode == "both":
        launch_both(args)
    elif args.mode == "check":
        if not check_dependencies():
            sys.exit(1)
    elif args.mode == "status":
        show_status()
    
    print("\n👋 OverWatch launcher finished")


if __name__ == "__main__":
    main()
