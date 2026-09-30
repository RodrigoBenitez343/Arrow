import sys
import os
import subprocess
import signal
import multiprocessing
import time
import logging
import ctypes

os.environ.setdefault("YOLO_AUTOINSTALL", "false")

# CRITICAL: Interpreter mode must be checked BEFORE any other code imports.
# When safe_python = Looper.exe (compiled build), calling 
#   Looper.exe sandbox_agent.py --port 8000
# must execute sandbox_agent.py WITHOUT opening the GUI.
# In frozen mode we skip the os.path.exists() check because the script
# lives in the caller's filesystem (e.g. C:\TempShared), not inside
# the PyInstaller bundle, so the bundled LoOper.py path doesn't apply.
if (len(sys.argv) > 1
        and sys.argv[1].endswith('.py')
        and (getattr(sys, 'frozen', False) or os.path.exists(sys.argv[1]))):
    script_path = sys.argv[1]
    sys.argv = sys.argv[1:]  # script sees itself as sys.argv[0]
    script_dir = os.path.dirname(os.path.abspath(script_path))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        with open(script_path, 'rb') as _f:
            _code = _f.read()
        exec(_code, {
            '__name__': '__main__',
            '__file__': script_path,
            '__builtins__': __builtins__,
            'os': os, 'sys': sys,
        })
        sys.exit(0)
    except Exception as _e:
        import traceback
        print(f"Error executing script {script_path}: {_e}")
        traceback.print_exc()
        sys.exit(1)

# Handle -m module invocations (e.g. pip) so subprocess calls don't spawn new GUI instances.
if (len(sys.argv) > 1
        and sys.argv[1] == '-m'
        and len(sys.argv) > 2):
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

if sys.platform == "win32":
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
    except Exception:
        pass

def _venv_launcher_python() -> str:
    """Return the python executable that must launch main.py.

    The native OCR stack (paddle/torch) is only tested inside the project
    venv (see run_looper.bat).  Launching with the system Python runs an
    untested paddleocr 3.x stack that has crashed natively during OCR
    inference (WerFault / 0xc0000005 access violation), taking the whole
    app down.  When launched outside the venv, re-exec with the venv
    python so the tested stack is always used.
    """
    if getattr(sys, "frozen", False):
        return sys.executable
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        venv_py = os.path.join(root, ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_py) and os.path.normcase(
            os.path.realpath(sys.executable)
        ) != os.path.normcase(os.path.realpath(venv_py)):
            print(f"[launcher] Using venv interpreter: {venv_py}")
            return venv_py
    except Exception:
        pass
    return sys.executable


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ensure_admin() -> None:
    if sys.platform != "win32":
        return

    if "--elevated" in sys.argv:
        try:
            sys.argv.remove("--elevated")
        except Exception:
            pass
        return

    if _is_admin():
        return

    try:
        if getattr(sys, "frozen", False):
            params = subprocess.list2cmdline(["--elevated", *sys.argv[1:]])
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        else:
            script = os.path.abspath(__file__)
            params = subprocess.list2cmdline([script, "--elevated", *sys.argv[1:]])
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    except Exception:
        pass

    sys.exit(0)


_ensure_admin()

# Move win32 imports to top for better performance and reliability
try:
    import win32gui
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
if getattr(sys, 'frozen', False):
    meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    exe_dir = os.path.dirname(sys.executable)
    internal_dir = meipass
    if os.path.basename(internal_dir).lower() != '_internal':
        candidate_internal = os.path.join(exe_dir, '_internal')
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

    # CRITICAL: Add Paddle's libs directory to PATH and DLL search path
    # When paddle is excluded from PYZ, it lives in _internal/paddle
    paddle_libs = os.path.join(internal_dir, 'paddle', 'libs')
    if os.path.exists(paddle_libs):
        os.environ['PATH'] = paddle_libs + os.pathsep + os.environ['PATH']
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(paddle_libs)
            except Exception:
                pass
    
    # Also add paddle/base just in case
    paddle_base = os.path.join(internal_dir, 'paddle', 'base')
    if os.path.exists(paddle_base):
        os.environ['PATH'] = paddle_base + os.pathsep + os.environ['PATH']
        if hasattr(os, 'add_dll_directory'):
            try:
                os.add_dll_directory(paddle_base)
            except Exception:
                pass

from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout, QProgressBar
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPixmap

def _check_license() -> bool:
    """Check for a valid hardware-bound license.

    In frozen mode (compiled build), verifies license.bin exists next to
    the executable and has the expected magic header.  If missing, tries
    to launch the license_validator.exe for interactive activation.

    In development mode the check is skipped entirely.
    """
    if not getattr(sys, "frozen", False):
        return True

    exe_dir = os.path.dirname(sys.executable)
    license_path = os.path.join(exe_dir, "license.bin")
    validator = os.path.join(exe_dir, "license_validator.exe")

    # Quick magic-byte check first
    if os.path.exists(license_path):
        try:
            with open(license_path, "rb") as f:
                data = f.read(8)
            if data == b"ARWLIC\x00\x01":
                return True
        except Exception:
            pass

    # Try the validator with --check (more thorough verification)
    if os.path.exists(validator):
        try:
            result = subprocess.run(
                [validator, "--check", exe_dir],
                capture_output=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    # No valid license -- show activation dialog
    if os.path.exists(validator):
        try:
            result = subprocess.run(
                [validator, "--reinstall", exe_dir],
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    # Cannot proceed without a license
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "License Required",
            "A valid license is required to run arrow.\n\n"
            "Please reinstall the application or contact support."
        )
        root.destroy()
    except Exception:
        pass

    return False


def _run_main_app():
    """Import and run the main application logic"""
    try:
        # Import the main module. This will be bundled by PyInstaller.
        from LoOper.main import run_app
        run_app()
    except Exception as e:
        print(f"Error launching main app: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

class Splash(QWidget):
    def __init__(self, logo_path: str):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.logo_label = QLabel()
        self.logo_label.setStyleSheet("background: transparent;")
        pix = QPixmap(logo_path) if os.path.exists(logo_path) else QPixmap()
        if not pix.isNull():
            pix = pix.scaled(360, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.logo_label.setPixmap(pix)
            self._logo_w = pix.width()
            self._logo_h = pix.height()
        self.logo_label.setAlignment(Qt.AlignCenter)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar{background:rgba(0,0,0,0); border:0px;} QProgressBar::chunk{background:#14c714; border-radius:0px;}")
        self.progress.setFixedHeight(8)

        try:
            w = getattr(self, "_logo_w", 360)
            h = getattr(self, "_logo_h", 360)
            self.logo_label.setFixedSize(w, h)
            self.progress.setFixedWidth(w)
            self.setFixedSize(w, h + self.progress.sizeHint().height())
        except Exception:
            pass

        layout.addWidget(self.logo_label)
        layout.addWidget(self.progress)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)
        self._timer.start(180)
        try:
            # Ensure explicit sizing to avoid clipping
            w = getattr(self, "_logo_w", 360)
            h = getattr(self, "_logo_h", 360)
            self.logo_label.setFixedSize(w, h)
            self.progress.setFixedWidth(w)
            self.setFixedSize(w, h + self.progress.height())
        except Exception:
            pass
        try:
            g = QApplication.primaryScreen().availableGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
        except Exception:
            pass

    def _advance(self):
        v = self.progress.value()
        if v < 95:
            self.progress.setValue(min(95, v + 2))

    def set_status(self, text: str):
        pass

def _find_main_window():
    if not HAS_WIN32:
        return False
    try:
        found = False
        current_pid = os.getpid()
        def _enum(hwnd, _):
            nonlocal found
            if found: return
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                # Search for the main window title case-insensitively
                if title and "looper" in title.lower():
                    # Double check it's not this process (the splash process)
                    _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                    if int(wpid) != current_pid:
                        # Also check size to avoid catching small/utility windows
                        try:
                            rect = win32gui.GetWindowRect(hwnd)
                            w = rect[2] - rect[0]
                            h = rect[3] - rect[1]
                            if w > 200 and h > 200:
                                found = True
                        except Exception:
                            found = True # Fallback if rect fails
        win32gui.EnumWindows(_enum, None)
        return found
    except Exception:
        return False

def _find_main_window_for_pid(pid: int):
    if not HAS_WIN32:
        return False
    found = False
    def _enum(hwnd, _):
        nonlocal found
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            # Be flexible with title but ensure it contains LoOper
            if "looper" not in title.lower():
                return
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if int(wpid) != int(pid):
                return
            try:
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                # Main window should be reasonably large
                if w < 300 or h < 200:
                    return
            except Exception:
                pass
            found = True
        except Exception:
            pass
    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return False
    return found

def launch():
    app = QApplication(sys.argv)
    base = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', base)
    
    # Try multiple locations for the logo
    possible_logos = [
        os.path.join(base, "LoOper.png"),
        os.path.join(base, "LoOper.ico"),
        os.path.join(base, "LoOper", "LoOper.png"),
        os.path.join(base, "LoOper", "LoOper.ico"),
        os.path.join(base, "_internal", "LoOper", "LoOper.png"),
        os.path.join(base, "_internal", "LoOper", "LoOper.ico")
    ]
    
    logo = None
    for l in possible_logos:
        if os.path.exists(l):
            logo = l
            break
            
    splash = Splash(logo) if logo else Splash("")
    splash.show()

    main_path = os.path.join(base, "LoOper", "main.py")
    if getattr(sys, 'frozen', False):
        # Update main_path to point into _internal if it exists there
        internal_main = os.path.join(base, "_internal", "LoOper", "main.py")
        if os.path.exists(internal_main):
            main_path = internal_main
    
    if not getattr(sys, 'frozen', False) and not os.path.exists(main_path):
        QTimer.singleShot(1500, app.quit)
        return app.exec_()
    
    creationflags = 0
    if os.name == "nt":
        try:
            # CREATE_NEW_PROCESS_GROUP = 0x00000200
            # CREATE_NO_WINDOW = 0x08000000
            creationflags = 0x00000200 | 0x08000000
        except Exception:
            creationflags = 0x08000000  # Fallback to at least hide window

    # Determine command to launch main app
    if getattr(sys, 'frozen', False):
        # When frozen, launch ourselves with the --run-main flag
        main_cmd = [sys.executable, "--run-main"]
        # Use bundle root as cwd for the child process
        launch_cwd = base
    else:
        # When running as script, launch main.py — always under the venv
        # interpreter (the paddle/torch OCR stack is only tested there;
        # the system Python's paddleocr 3.x has crashed natively).
        main_cmd = [_venv_launcher_python(), main_path]
        launch_cwd = os.path.dirname(main_path)

    proc = subprocess.Popen(
        main_cmd,
        cwd=launch_cwd,
        stdout=None,
        stderr=None,
        creationflags=creationflags,
    )

    start_time = time.time()
    min_splash_time = 4.0 # Minimum 4 seconds for splash screen
    ready_seen = False
    def _kill_process_tree():
        # Kill Main Process
        try:
            import psutil
            try:
                p = psutil.Process(proc.pid)
                for child in p.children(recursive=True):
                    try:
                        child.terminate()
                    except Exception:
                        pass
                _, _ = psutil.wait_procs(p.children(recursive=True), timeout=3)
            except Exception:
                pass
        except Exception:
            pass
        try:
            if os.name == "nt":
                try:
                    # Add CREATE_NO_WINDOW for taskkill
                    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], check=False, capture_output=True, creationflags=0x08000000)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if os.name == "nt":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    pass
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def check_state():
        nonlocal ready_seen
        try:
            has_window_pid = _find_main_window_for_pid(proc.pid)
        except Exception:
            has_window_pid = False
        try:
            has_window_any = _find_main_window()
        except Exception:
            has_window_any = False

        elapsed = time.time() - start_time
        
        # Only consider ready if window exists AND minimum time has passed
        if not ready_seen and (has_window_pid or has_window_any):
            if elapsed >= min_splash_time:
                ready_seen = True
                try:
                    splash.progress.setValue(100)
                except Exception:
                    pass
                try:
                    poll.stop()
                except Exception:
                    pass
                QTimer.singleShot(500, splash.close)
            else:
                # Update progress even if not ready to close
                progress_pct = min(99, int((elapsed / min_splash_time) * 100))
                if progress_pct > splash.progress.value():
                    splash.progress.setValue(progress_pct)
                    
        elif ready_seen and not (has_window_pid or has_window_any):
            if proc.poll() is None:
                _kill_process_tree()
            try:
                poll.stop()
            except Exception:
                pass
            QTimer.singleShot(100, app.quit)

    poll = QTimer(splash)
    poll.timeout.connect(check_state)
    poll.start(250)

    QTimer.singleShot(60000, lambda: app.quit())
    return app.exec_()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    
    # CRITICAL: Check if we should run in "interpreter mode" for code nodes in frozen builds.
    # This prevents recursive LoOper launches by allowing LoOper.exe to act as a Python 
    # interpreter for its own bundled environment when passed a .py file.
    if len(sys.argv) > 1 and sys.argv[1].endswith('.py') and os.path.exists(sys.argv[1]):
        script_path = sys.argv[1]
        # Shift arguments so the script sees itself as sys.argv[0]
        sys.argv = sys.argv[1:]
        
        # Add the script's directory to sys.path
        script_dir = os.path.dirname(os.path.abspath(script_path))
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)
            
        try:
            with open(script_path, 'rb') as f:
                code_content = f.read()
            # Execute the script in the context of the bundled environment
            exec_globals = {
                '__name__': '__main__',
                '__file__': script_path,
                '__builtins__': __builtins__,
            }
            # Import modules that might be needed by the script
            import os as _os
            import sys as _sys
            exec_globals['os'] = _os
            exec_globals['sys'] = _sys
            
            exec(code_content, exec_globals)
            sys.exit(0)
        except Exception as e:
            print(f"Error executing script {script_path}: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    # Handle -m module invocations (e.g. pip) for development / subprocess guard.
    if len(sys.argv) > 1 and sys.argv[1] == '-m' and len(sys.argv) > 2:
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

    # Check if we are being called to run the main app logic
    # License check -- enforced for GUI launch only
    if "--run-main" in sys.argv:
        sys.argv.remove("--run-main")
        _run_main_app()
    elif not _check_license():
        sys.exit(1)
    elif "--install-deps" in sys.argv:
        try:
            print("Installing/Verifying PaddleOCR dependencies...")
            
            # Ensure sys.path includes the bundle directory for imports
            if getattr(sys, 'frozen', False):
                 meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
                 exe_dir = os.path.dirname(sys.executable)
                 internal_dir = meipass
                 if os.path.basename(internal_dir).lower() != '_internal':
                     candidate_internal = os.path.join(exe_dir, '_internal')
                     if os.path.isdir(candidate_internal):
                         internal_dir = candidate_internal
                     else:
                         candidate_internal = os.path.join(meipass, '_internal')
                         if os.path.isdir(candidate_internal):
                             internal_dir = candidate_internal
                 if internal_dir not in sys.path:
                     sys.path.insert(0, internal_dir)

            # Try to import using the app's own logic which handles bundled models
            try:
                # Attempt to import the ocr_engine from the package
                # We try multiple import paths to be safe
                try:
                    from LoOper.player import ocr_engine
                except ImportError:
                    try:
                        from player import ocr_engine
                    except ImportError:
                         # If we can't import the engine, fall back to direct check
                         raise ImportError("Could not import ocr_engine")

                print(f"OCR Available: {ocr_engine.ocr_available()}")
                
                if ocr_engine.ocr_available():
                    # This will trigger the bundled model check in _get_paddle_ocr
                    ocr = ocr_engine._get_paddle_ocr("en")
                    if ocr:
                        print("PaddleOCR initialized successfully using app configuration.")
                    else:
                        print("PaddleOCR initialization returned None.")
                        sys.exit(1)
                else:
                    print("OCR reported as unavailable in config.")
                    # Try to force initialization anyway to see the error
                    ocr_engine._get_paddle_ocr("en")
                    
            except Exception as e:
                print(f"Standard initialization failed: {e}")
                print("Falling back to direct initialization...")
                
                # Fallback: direct initialization (similar to old logic but cleaner)
                os.environ["FLAGS_use_mkldnn"] = "0"
                os.environ["FLAGS_enable_mkldnn"] = "0"
                
                from paddleocr import PaddleOCR
                PaddleOCR(lang='en', show_log=True, use_angle_cls=True, enable_mkldnn=False)
                
            print("PaddleOCR dependencies check completed.")
        except Exception as e:
            print(f"Error installing dependencies: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        sys.exit(0)
    else:
        launch()
