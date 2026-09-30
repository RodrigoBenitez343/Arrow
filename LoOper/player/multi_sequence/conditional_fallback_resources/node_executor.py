import logging
logger = logging.getLogger(__name__)
# node_executor.py
"""
Node executor module for conditional fallback handler.
Handles conditional node execution and workflow branching logic.
"""
import json
import os


class NodeExecutor:
    """Handles conditional node execution and workflow branching"""

    def _verify_app_running(self, app_context, timeout=2.0):
        """
        Passively verify that the expected app process is running, WITHOUT
        modifying window focus, z-order, or display resolution.
        
        Conditionals must only *observe* the screen — they must never bring
        windows to the foreground or call SetForegroundWindow / ShowWindow /
        BringWindowToTop, as those calls can trigger DPI scaling changes on
        Windows, especially in nested chains where app_context may leak
        from a parent chain.
        
        Returns True if the process is found running (or no context given),
        False if the process is not found within the timeout.
        """
        try:
            import time
            import psutil
        except Exception:
            return True

        exe = str((app_context or {}).get("exe") or "")
        proc_name = str((app_context or {}).get("process_name") or "")
        pid_hint = (app_context or {}).get("pid")
        sandboxed = bool((app_context or {}).get("sandboxed") or (getattr(self, "global_app_context_override", {}) or {}).get("sandboxed"))

        if sandboxed:
            return True

        if not exe and not proc_name and not pid_hint:
            return True

        exe_norm = os.path.normcase(exe) if exe else ""
        proc_name_norm = proc_name.lower() if proc_name else ""
        deadline = time.time() + float(timeout or 0.0)

        while time.time() <= deadline:
            try:
                if pid_hint:
                    try:
                        p = psutil.Process(int(pid_hint))
                        if p.is_running():
                            return True
                    except Exception:
                        pass
                for p in psutil.process_iter(["pid", "name", "exe"]):
                    try:
                        if exe_norm:
                            pexe = os.path.normcase(str(p.info.get("exe") or ""))
                            if pexe and pexe == exe_norm:
                                return True
                        if proc_name_norm:
                            pn = str(p.info.get("name") or "").lower()
                            if pn and pn == proc_name_norm:
                                return True
                    except Exception:
                        continue
            except Exception:
                pass
            time.sleep(0.1)

        logger.info(f"Conditional: expected app not running (exe={exe!r}, proc={proc_name!r}, pid={pid_hint!r})")
        return False

    def _get_expected_app_context(self, conditional_node):
        try:
            if isinstance(conditional_node, dict):
                ctx = conditional_node.get("app_context") or conditional_node.get("focus_app") or conditional_node.get("app")
                if isinstance(ctx, dict) and any(ctx.get(k) for k in ("exe", "process_name", "title", "pid")):
                    return ctx
        except Exception:
            pass
        return None

    def _ensure_app_open_and_focused(self, app_context, stop_flag, timeout=2.0):
        try:
            import time
            import psutil
        except Exception:
            return True

        try:
            import pygetwindow as gw
        except Exception:
            gw = None

        try:
            import win32gui
            import win32process
            import win32con
        except Exception:
            win32gui = None
            win32process = None
            win32con = None

        try:
            from pywinauto import Application
        except Exception:
            Application = None

        try:
            import subprocess
        except Exception:
            subprocess = None

        exe = str((app_context or {}).get("exe") or "")
        proc_name = str((app_context or {}).get("process_name") or "")
        title_hint = str((app_context or {}).get("title") or "")
        pid_hint = (app_context or {}).get("pid")
        sandboxed = bool((app_context or {}).get("sandboxed") or (getattr(self, "global_app_context_override", {}) or {}).get("sandboxed"))

        exe_norm = os.path.normcase(exe) if exe else ""
        proc_name_norm = proc_name.lower() if proc_name else ""

        deadline = time.time() + float(timeout or 0.0)

        def _foreground_hwnd():
            try:
                if gw is not None:
                    w = gw.getActiveWindow()
                    if w is not None:
                        h = getattr(w, "_hWnd", None)
                        if h:
                            return int(h)
            except Exception:
                pass
            try:
                if win32gui is not None:
                    return int(win32gui.GetForegroundWindow())
            except Exception:
                return None
            return None

        def _matches_process(p):
            try:
                if exe_norm:
                    pexe = ""
                    try:
                        pexe = os.path.normcase(str(p.info.get("exe") or ""))
                    except Exception:
                        pexe = ""
                    if pexe and pexe == exe_norm:
                        return True
                if proc_name_norm:
                    pn = str(p.info.get("name") or "").lower()
                    if pn and pn == proc_name_norm:
                        return True
                return False
            except Exception:
                return False

        def _candidate_pids():
            pids = []
            try:
                if pid_hint:
                    try:
                        p = psutil.Process(int(pid_hint))
                        if p.is_running():
                            pids.append(int(pid_hint))
                    except Exception:
                        pass
                for p in psutil.process_iter(["pid", "name", "exe"]):
                    if _matches_process(p):
                        pids.append(int(p.info["pid"]))
            except Exception:
                pass
            seen = set()
            out = []
            for p in pids:
                if p not in seen:
                    seen.add(p)
                    out.append(p)
            return out

        def _window_candidates_for_pid(pid):
            wins = []
            try:
                if gw is not None:
                    for w in gw.getAllWindows():
                        try:
                            hwnd = getattr(w, "_hWnd", None)
                            if not hwnd:
                                continue
                            hwnd = int(hwnd)
                            if win32process is None:
                                continue
                            _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
                            if int(wpid) != int(pid):
                                continue
                            if win32gui is not None and not win32gui.IsWindowVisible(hwnd):
                                continue
                            t = str(getattr(w, "title", "") or "")
                            wins.append((hwnd, t))
                        except Exception:
                            continue
            except Exception:
                wins = []

            if wins:
                return wins

            try:
                if win32gui is None or win32process is None:
                    return wins

                def cb(hwnd, _):
                    try:
                        if not win32gui.IsWindowVisible(hwnd):
                            return
                        _tid, wpid = win32process.GetWindowThreadProcessId(hwnd)
                        if int(wpid) != int(pid):
                            return
                        t = str(win32gui.GetWindowText(hwnd) or "")
                        if not t:
                            return
                        wins.append((int(hwnd), t))
                    except Exception:
                        return

                win32gui.EnumWindows(cb, None)
            except Exception:
                pass
            return wins

        def _pick_window(wins):
            if not wins:
                return None
            if title_hint:
                th = title_hint.strip().lower()
                if th:
                    for hwnd, t in wins:
                        if th in str(t or "").lower():
                            return int(hwnd)
            return int(wins[0][0])

        def _focus_hwnd(hwnd, pid):
            if not hwnd:
                return False
            try:
                if Application is not None:
                    try:
                        app = Application(backend="uia").connect(process=int(pid))
                        w = app.top_window()
                        try:
                            w.set_focus()
                        except Exception:
                            w.set_focus()
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                if win32gui is not None:
                    try:
                        if win32con is not None:
                            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    except Exception:
                        pass
                    try:
                        win32gui.SetForegroundWindow(hwnd)
                    except Exception:
                        try:
                            win32gui.BringWindowToTop(hwnd)
                        except Exception:
                            pass
            except Exception:
                pass

            fhwnd = _foreground_hwnd()
            if fhwnd and int(fhwnd) == int(hwnd):
                return True
            return False

        def _try_launch_exe():
            if not exe and not sandboxed:
                return False
            # Skip exe existence check when sandboxed — the exe is from the recording
            # machine and the target app runs inside the RDP session, not here.
            if exe and not sandboxed:
                try:
                    if not os.path.exists(exe):
                        logger.warning(f"Executable not found: {exe}")
                        return False
                except Exception as e:
                    logger.error(f"Error checking exe path: {e}")
                    return False
            if subprocess is None:
                logger.error("subprocess module is not available")
                return False
                
            if sandboxed:
                try:
                    logger.info("Launching Concurrent RDP Agent Session for fallback node...")
                    
                    temp_dir = "C:\\TempShared"
                    os.makedirs(temp_dir, exist_ok=True)

                    for name in ("agent_info.json", "agent_error.txt", "agent_log.txt", "agent_status.txt"):
                        p = os.path.join(temp_dir, name)
                        try:
                            if os.path.exists(p):
                                os.remove(p)
                        except Exception:
                            pass
                    
                    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

                    # --- Resolve sandbox_agent.py (same logic as sequence_executor.py) ---
                    import sys
                    agent_script = os.path.join(project_root, "LoOper", "sandbox_agent.py")
                    if not os.path.exists(agent_script):
                        _meipass = getattr(sys, '_MEIPASS', None)
                        if _meipass:
                            for _cdir in ('LoOper', '.'):
                                _bp = os.path.join(_meipass, _cdir, 'sandbox_agent.py')
                                if os.path.exists(_bp):
                                    agent_script = _bp
                                    break

                    _deployed_agent = os.path.join(temp_dir, 'sandbox_agent.py')
                    try:
                        if os.path.exists(agent_script):
                            import shutil
                            shutil.copy2(agent_script, _deployed_agent)
                            agent_script = _deployed_agent
                        else:
                            logger.error(f"sandbox_agent.py not found (searched {agent_script})")
                            return False
                    except Exception as _copy_err:
                        logger.error(f"Failed to deploy sandbox_agent.py to {temp_dir}: {_copy_err}")
                        return False

                    # --- Resolve Python interpreter (same logic as sequence_executor.py) ---
                    safe_python = ""
                    _py_candidates = [
                        os.path.join(project_root, ".venv", "Scripts", "python.exe"),
                        os.path.join(project_root, ".venv", "scripts", "python.exe"),
                    ]
                    if getattr(sys, 'frozen', False):
                        _py_candidates.append(sys.executable)
                    else:
                        _py_candidates.append(sys.executable)
                    for c in _py_candidates:
                        try:
                            if c and os.path.exists(c):
                                safe_python = c
                                break
                        except Exception:
                            continue
                    if not safe_python:
                        safe_python = "python"

                    port = None
                    try:
                        import socket
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.bind(("127.0.0.1", 0))
                            port = int(s.getsockname()[1])
                    except Exception:
                        port = 8000

                    # --- Create .bat runner script for RDP alternate shell ---
                    runner_bat = os.path.join(temp_dir, f"agent_runner_fallback_{os.getpid()}.bat")
                    bat_lines = [
                        "@echo off",
                        f"set LOOPER_AGENT_PORT={int(port)}",
                    ]
                    # Only set PYTHONPATH in source mode; compiled mode interpreter already knows its modules.
                    if not getattr(sys, 'frozen', False):
                        bat_lines.append(f"set PYTHONPATH={project_root}")
                    if exe:
                        exe_dir = os.path.dirname(exe) if exe else ""
                        bat_lines.append(f'start "" /D "{exe_dir or temp_dir}" "{exe}"')
                    bat_lines.append(f'"{safe_python}" "{agent_script}" --port {int(port)}')
                    with open(runner_bat, "w", encoding="utf-8") as f:
                        f.write("\r\n".join(bat_lines) + "\r\n")

                    # Reset LoOperAgent password and enforce no-expiry to prevent
                    # Windows RDP password-expiration popup during authentication.
                    try:
                        subprocess.run(["net", "user", "LoOperAgent", "LoOperPassword123!", "/passwordchg:no", "/expires:never"],
                                       capture_output=True, text=True)
                    except Exception:
                        pass

                    # Grant execute permission to LoOperAgent
                    try:
                        subprocess.run(["icacls", runner_bat, "/grant", "LoOperAgent:(RX)"], capture_output=True)
                    except Exception:
                        pass

                    try:
                        q = subprocess.run(["query", "session"], capture_output=True, text=True)
                        out = (q.stdout or "") + "\n" + (q.stderr or "")
                        for line in out.splitlines():
                            if "LoOperAgent" not in line:
                                continue
                            import re as _re
                            m = _re.search(r"\s(\d+)\s+(Active|Disc|Conn|Listen)\b", line)
                            if not m:
                                continue
                            try:
                                sid = int(m.group(1))
                            except Exception:
                                continue
                            subprocess.run(["logoff", str(sid)], capture_output=True, text=True)
                            time.sleep(1.0)
                            break
                    except Exception:
                        pass

                    # Add credentials
                    os.system('cmdkey /generic:TERMSRV/127.0.0.2 /user:LoOperAgent /pass:LoOperPassword123!')
                    
                    # Ensure agent account has full read/write access to the TempShared folder
                    try:
                        subprocess.run(["icacls", temp_dir, "/grant", "LoOperAgent:(OI)(CI)F", "/T"], capture_output=True)
                    except Exception:
                        pass
                        
                    # Create scheduled task to launch agent on RDP session logon
                    # (password was reset above, so schtasks will accept it)
                    task_name = f"LoOperAgentStartFallback_{os.getpid()}"
                    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True, text=True)
                    r = subprocess.run(
                        [
                            "schtasks",
                            "/create",
                            "/tn",
                            task_name,
                            "/sc",
                            "ONLOGON",
                            "/ru",
                            "LoOperAgent",
                            "/rp",
                            "LoOperPassword123!",
                            "/rl",
                            "HIGHEST",
                            "/it",
                            "/tr",
                            f'cmd.exe /c "{runner_bat}"',
                            "/f",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if r.returncode != 0:
                        logger.error(f"Failed to create scheduled task for agent startup: {r.stdout} {r.stderr}")
                        return False

                    # Create RDP file
                    rdp_file = os.path.join(temp_dir, "agent_session.rdp")
                    ctx = app_context if isinstance(app_context, dict) else {}
                    show_window = ctx.get("show_window", False)
                    screen_mode = "1" if show_window else "2"
                    # Get current screen resolution dynamically for RDP session
                    try:
                        import ctypes
                        desktop_w = int(ctypes.windll.user32.GetSystemMetrics(0))
                        desktop_h = int(ctypes.windll.user32.GetSystemMetrics(1))
                    except Exception:
                        desktop_w = 1920
                        desktop_h = 1080
                    with open(rdp_file, "w") as f:
                        f.write(f'''screen mode id:i:{screen_mode}
use multimon:i:0
desktopwidth:i:{desktop_w}
desktopheight:i:{desktop_h}
smart sizing:i:1
session bpp:i:32
full address:s:127.0.0.2
username:s:LoOperAgent
audiomode:i:0
redirectprinters:i:0
redirectclipboard:i:1
autoreconnection enabled:i:1
authentication level:i:2
prompt for credentials:i:0
negotiate security layer:i:1
enablecredsspsupport:i:1
''')

                    logger.info(f"Launching {exe or 'Agent Only'} inside RDP Session (Show Window: {show_window})")
                    if show_window:
                        subprocess.Popen(["mstsc.exe", rdp_file], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    else:
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        startupinfo.wShowWindow = subprocess.SW_HIDE
                        subprocess.Popen(["mstsc.exe", rdp_file], startupinfo=startupinfo)
                    # DO NOT log off the session after launching MSTSC — that closes the
                    # RDP session that was just established. Only clean stale sessions
                    # before the ONLOGON scheduled task is created (see block above).

                    try:
                        import urllib.request
                        agent_info_path = os.path.join(temp_dir, "agent_info.json")
                        agent_url = f"http://127.0.0.1:{int(port)}"
                        start_deadline = time.time() + 60.0
                        reachable = False
                        import socket
                        while time.time() < start_deadline:
                            if os.path.exists(agent_info_path):
                                try:
                                    with open(agent_info_path, "r", encoding="utf-8") as f:
                                        info = json.load(f) or {}
                                    p2 = info.get("port")
                                    if p2:
                                        port = int(p2)
                                        agent_url = f"http://127.0.0.1:{int(port)}"
                                except Exception:
                                    pass

                            try:
                                with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
                                    reachable = True
                                    break
                            except Exception:
                                pass
                            time.sleep(0.25)
                    except Exception:
                        pass

                    if not reachable:
                        # Check for agent error info from the sandbox agent
                        try:
                            _agent_err = os.path.join(temp_dir, "agent_error.txt")
                            if os.path.exists(_agent_err):
                                with open(_agent_err, "r", encoding="utf-8", errors="ignore") as f:
                                    logger.error(f"RDP agent error: {f.read()}")
                        except Exception:
                            pass
                        subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True, text=True)
                        logger.error(f"RDP Agent did not become reachable in time: {agent_url}")
                        return False

                    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True, text=True)
                    try:
                        # Persist the agent URL so child sequences and the
                        # computer-vision channel route into this session.
                        if isinstance(app_context, dict):
                            app_context["sandbox_agent_url"] = agent_url
                            app_context["rdp_agent_url"] = agent_url
                    except Exception:
                        pass
                    try:
                        g = getattr(self, "global_app_context_override", None)
                        if isinstance(g, dict):
                            g["sandbox_agent_url"] = agent_url
                            g["rdp_agent_url"] = agent_url
                    except Exception:
                        pass
                    try:
                        from player.computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(agent_url)
                    except Exception:
                        pass
                    return True
                except Exception as e:
                    logger.error(f"Failed to launch RDP Session: {e}")
                    return False
                    
            try:
                cwd = os.path.dirname(exe) or None
                subprocess.Popen(exe, cwd=cwd, shell=True)
                return True
            except Exception:
                return False

        launched = False

        while time.time() <= deadline:
            if stop_flag and stop_flag():
                return False

            pids = _candidate_pids()
            if not pids:
                if not launched:
                    ok = _try_launch_exe()
                    if not ok:
                        return False
                    launched = True
                time.sleep(0.05)
                continue

            for pid in pids:
                if stop_flag and stop_flag():
                    return False
                wins = _window_candidates_for_pid(pid)
                hwnd = _pick_window(wins)
                if hwnd and _focus_hwnd(hwnd, pid):
                    return True
            time.sleep(0.05)

        return False
    
    def __init__(self, template_matcher, validation_cache, path_resolver, presence_trigger, absence_trigger, ocr_trigger, conditional_loop, stop_flag=None):
        """
        Initialize node executor.
        
        Args:
            template_matcher: TemplateMatching instance
            validation_cache: ValidationCache instance
            path_resolver: PathResolver instance
            presence_trigger: PresenceTrigger instance
            absence_trigger: AbsenceTrigger instance
            ocr_trigger: OCRTrigger instance
            conditional_loop: ConditionalLoop instance
            stop_flag: Function to check if execution should stop
        """
        self.template_matcher = template_matcher
        self.validation_cache = validation_cache
        self.path_resolver = path_resolver
        self.presence_trigger = presence_trigger
        self.absence_trigger = absence_trigger
        self.ocr_trigger = ocr_trigger
        self.conditional_loop = conditional_loop
        self.stop_flag = stop_flag
    
    def execute_conditional_node(self, node, stop_flag=None):
        """
        Executes a conditional node and returns the next node ID based on the result.
        
        Args:
            node (dict): The conditional node from the workflow graph
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        conditional_data = node['data']
        logger.info(f"Executing conditional node: {conditional_data.get('name', conditional_data['node_id'])}")
        
        # Check if this is a loop condition
        condition_type = conditional_data.get('condition_type', 'presence')
        if condition_type == 'loop':
            result = self.conditional_loop.execute_conditional_loop_from_node(conditional_data, stop_flag)
        else:
            result = self.evaluate_conditional_from_node(conditional_data, stop_flag)
        
        return self.get_conditional_branch_node(node, result)
    
    def evaluate_conditional_from_node(self, conditional_node, stop_flag=None):
        """
        Evaluate a conditional node using the appropriate trigger handler.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
        
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        # Web mode: route on the chain's shared browser instead of the desktop
        # visual conditions (mirrors conditional_ops._evaluate_conditional).
        if self._is_web_conditional(conditional_node):
            return self._evaluate_web_condition(conditional_node, stop_flag)

        # Fix: The JSON stores 'trigger_type' with values like 'absence', 'presence', 'ocr'
        # We need to map these to the expected condition types for evaluation
        raw_trigger_type = conditional_node.get('trigger_type') or conditional_node.get('condition_type', 'presence')
        if str(raw_trigger_type).lower() in ('code', 'code_conditional', 'python'):
            code_text = conditional_node.get('code') or conditional_node.get('condition_code') or ''
            if not str(code_text or '').strip():
                return False
            try:
                import sys
                import venv
                import subprocess as _sp
                try:
                    runtime_root = os.path.join(os.getcwd(), 'runtime')
                    venv_dir = os.path.join(runtime_root, 'venvs', 'conditional_fallback')
                    os.makedirs(venv_dir, exist_ok=True)
                    py_exe = os.path.join(venv_dir, 'Scripts', 'python.exe')
                    if not os.path.exists(py_exe):
                        builder = venv.EnvBuilder(with_pip=True)
                        builder.create(venv_dir)
                    py_exe = os.path.join(venv_dir, 'Scripts', 'python.exe')
                    sp = os.path.join(venv_dir, 'Lib', 'site-packages')
                    if os.path.isdir(sp) and sp not in sys.path:
                        sys.path.insert(0, sp)

                    import ast
                    pkgs = set()
                    try:
                        tree = ast.parse(str(code_text))
                        for n in ast.walk(tree):
                            if isinstance(n, ast.Import):
                                for a in n.names:
                                    pkgs.add(a.name.split('.')[0])
                            elif isinstance(n, ast.ImportFrom):
                                if n.module:
                                    pkgs.add(n.module.split('.')[0])
                    except Exception:
                        pkgs = set()

                    stdlib = {
                        'os','sys','json','re','typing','time','subprocess','pathlib','math','random','datetime','itertools','collections','functools','threading','asyncio','io',
                        'contextlib','shutil','glob','platform','warnings','logging','uuid','socket','urllib','http','email','base64','hashlib','hmac','struct','pickle','copy',
                        'weakref','enum','types','inspect','traceback','gc','abc','numbers','decimal','fractions','statistics','operator','unittest','doctest','pdb','profile',
                        'cProfile','timeit','venv','ensurepip','zipfile','tarfile','csv','sqlite3','zlib','gzip','bz2','lzma','xml','html','mimetypes','argparse','optparse',
                        'getopt','getpass','ctypes','multiprocessing','concurrent','queue','select','mmap','signal','site','builtins','locale'
                    }
                    wanted = [p for p in pkgs if p and p not in stdlib]
                    pkg_map = {
                        'cv2': 'opencv-python',
                        'PIL': 'Pillow',
                        'bs4': 'beautifulsoup4',
                        'sklearn': 'scikit-learn',
                        'yaml': 'PyYAML',
                        'dotenv': 'python-dotenv',
                        'dateutil': 'python-dateutil'
                    }
                    wanted = [pkg_map.get(w, w) for w in wanted]
                    if wanted and os.path.exists(py_exe):
                        installed_names = set()
                        try:
                            cmd = [py_exe, "-m", "pip", "list", "--format=json"]
                            p = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, creationflags=_sp.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                            out_list, _err_list = p.communicate(timeout=10)
                            import json as _json
                            data = _json.loads(out_list or "[]")
                            for item in data:
                                try:
                                    installed_names.add(str(item.get('name', '')).lower())
                                except Exception:
                                    continue
                        except Exception:
                            installed_names = set()
                        to_install = []
                        for w in wanted:
                            if str(w).lower() not in installed_names:
                                to_install.append(w)
                        if to_install:
                            cmd = [py_exe, "-m", "pip", "install"] + to_install
                            p = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, creationflags=_sp.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                            p.communicate(timeout=180)
                except Exception:
                    pass

                import threading
                import time
                exec_scope = {'os': os, '__name__': '__main__'}
                out = {'result': None, 'error': None}
                def _runner():
                    try:
                        exec(str(code_text), exec_scope, exec_scope)
                        res = exec_scope.get('result')
                        if res is None and callable(exec_scope.get('condition')):
                            res = exec_scope.get('condition')()
                        if res is None:
                            try:
                                candidates = {}
                                for k, v in dict(exec_scope).items():
                                    if not k or str(k).startswith('_'):
                                        continue
                                    if not callable(v):
                                        continue
                                    if k in ('condition',):
                                        continue
                                    try:
                                        if getattr(v, '__module__', None) not in (None, '__main__'):
                                            continue
                                    except Exception:
                                        pass
                                    candidates[str(k)] = v
                                preferred = [
                                    'check_system_language',
                                    'evaluate',
                                    'predicate',
                                    'check',
                                    'run',
                                    'main'
                                ]
                                picked = None
                                for name in preferred:
                                    if name in candidates:
                                        picked = candidates[name]
                                        break
                                if picked is None and len(candidates) == 1:
                                    picked = next(iter(candidates.values()))
                                if picked is not None:
                                    res = picked()
                            except Exception:
                                res = None
                        out['result'] = res
                    except Exception as e:
                        out['error'] = e
                t = threading.Thread(target=_runner, daemon=True)
                t.start()
                try:
                    timeout_s = float(conditional_node.get('timeout', 5.0) or 5.0)
                except Exception:
                    timeout_s = 5.0
                start = time.time()
                while t.is_alive():
                    if stop_flag and stop_flag():
                        return False
                    if timeout_s and timeout_s > 0 and (time.time() - start) > timeout_s:
                        return False
                    time.sleep(0.01)
                if out.get('error') is not None:
                    return False
                res = out.get('result')
                if isinstance(res, bool):
                    return res
                if isinstance(res, str):
                    s = res.strip().lower()
                    if s in ('true', '1', 'yes', 'y', 'on'):
                        return True
                    if s in ('false', '0', 'no', 'n', 'off', ''):
                        return False
                    return bool(s)
                return bool(res)
            except Exception:
                return False

        if str(raw_trigger_type).strip().lower() in ('layout_match', 'layout_match_conditional', 'layout_match_trigger', 'layout_match_condition'):
            return self._evaluate_layout_match_conditional(conditional_node, stop_flag=stop_flag)
        
        # Map the raw trigger type to the expected condition type format
        condition_type_mapping = {
            'presence': 'presence_trigger',
            'absence': 'absence_trigger', 
            'ocr': 'ocr_trigger',
            'presence_trigger': 'presence_trigger',  # backward compatibility
            'absence_trigger': 'absence_trigger',    # backward compatibility
            'ocr_trigger': 'ocr_trigger'             # backward compatibility
        }
        
        condition_type = condition_type_mapping.get(raw_trigger_type, 'presence_trigger')
        
        # Build condition_config from the conditional node properties
        # The JSON stores properties directly, not nested under 'condition_data'
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'image_data': conditional_node.get('image_data', ''),
            'confidence': float(conditional_node.get('threshold', 0.8)),
            'timeout': float(conditional_node.get('wait_time', 10)),
            'max_loops': int(conditional_node.get('max_loops', 10)),
            'target_text': conditional_node.get('ocr_text', '') or conditional_node.get('target_text', ''),
            'case_sensitive': conditional_node.get('case_sensitive', True),
            'language': conditional_node.get('language', 'eng'),
            'region': conditional_node.get('region')
        }
        
        # Also check if there's a nested condition_data (for backward compatibility)
        if 'condition_data' in conditional_node:
            nested_config = conditional_node['condition_data']
            condition_config.update(nested_config)
        
        logger.info(f"Evaluating conditional with raw trigger type: {raw_trigger_type} -> mapped to: {condition_type}")
        logger.debug(f"Condition config: {condition_config}")

        expected_app = self._get_expected_app_context(conditional_node)
        if expected_app:
            try:
                ok = self._verify_app_running(expected_app, timeout=float(expected_app.get("focus_timeout", 2.0)))
            except Exception:
                ok = True
            if not ok:
                logger.info("Conditional gated: expected app not running")
                return False
        
        # Execute condition checks using appropriate trigger
        from player.computer_vision import get_sandbox_agent_url
        if get_sandbox_agent_url():
            logger.info("Using Sandbox Agent for conditional evaluation")
            
        if condition_type == 'presence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.presence_trigger.check_presence_with_timeout(
                condition_config,
                timeout
            )
        elif condition_type == 'absence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.absence_trigger.check_absence_trigger(condition_config, timeout)
        elif condition_type == 'ocr_trigger':
            return self.ocr_trigger.check_ocr_trigger(condition_config, stop_flag=stop_flag)
        else:
            logger.warning(f"Unknown condition type: {condition_type} (raw: {raw_trigger_type})")
            return False

    @staticmethod
    def _is_web_conditional(conditional_node):
        """True when the node is a web-mode conditional (routes on the browser)."""
        value = conditional_node.get('web_mode')
        if isinstance(value, bool):
            return value
        if value is None:
            return str(conditional_node.get('condition_type') or '').strip().lower() == 'web'
        return str(value).strip().lower() in ('true', '1', 'yes', 'y', 'on')

    def _evaluate_web_condition(self, conditional_node, stop_flag=None):
        """Evaluate a web-mode conditional on the chain's shared browser.

        Uses the SAME shared browser the web sequence nodes and the element
        picker use, so the condition observes the page the last web sequence
        left open.
        """
        try:
            from .web_conditions import evaluate as evaluate_web_condition
        except Exception as exc:
            logger.error("Web conditional module unavailable: %s", exc)
            return False
        try:
            from player.web.session import SHARED_WEB_SCOPE, ensure_workbench_session
            driver = ensure_workbench_session(chain_key=SHARED_WEB_SCOPE)
        except Exception as exc:
            logger.error("Web conditional: browser unavailable: %s", exc)
            return False
        if driver is None:
            logger.error(
                "Web conditional: no shared browser available - place the "
                "conditional after a web sequence that opened the page"
            )
            return False
        try:
            return bool(evaluate_web_condition(conditional_node, driver, stop_flag=stop_flag))
        except Exception as exc:
            logger.error("Web conditional evaluation failed: %s", exc)
            return False

    def _evaluate_layout_match_conditional(self, conditional_node, stop_flag=None):
        """
        Layout Match Conditional - Pixel-by-pixel alignment.
            
        Scans the screen line by line by scrolling one pixel at a time,
        trying to find the sample image at the exact expected position
        with no pixel tolerance.
            
        This is useful for aligning UI elements to specific positions
        to make automation tasks easier.
        """
        import time
        import os
        try:
            import pyautogui
        except Exception:
            pyautogui = None
        try:
            from player.computer_vision import get_sandbox_agent_url
        except Exception:
            get_sandbox_agent_url = None
        try:
            from PIL import Image
        except Exception:
            Image = None
    
        # Priority: embedded base64 image_data, then file path
        image_data = conditional_node.get('image_data', '') or ''
        image_path = conditional_node.get('image_path', '') or ''
        
        if not image_data and not str(image_path).strip():
            logger.warning("Layout match: no image data or path provided")
            return False

        if Image is None:
            logger.warning("Layout match: PIL not available")
            return False

        # Load template - Priority 1: embedded base64 data
        template_img = None
        if image_data:
            try:
                from ...image_utils import base64_to_pil
                template_img = base64_to_pil(image_data)
                if template_img:
                    logger.debug("Layout match: loaded template from base64 image_data")
            except Exception as e:
                logger.debug(f"Layout match: base64 load failed: {e}")

        # Load template - Priority 2: file path
        if template_img is None and str(image_path).strip():
            def _resolve_image(p):
                try:
                    rp = self.path_resolver.resolve_path(p, add_json=False)
                    if rp:
                        return rp
                except Exception:
                    pass
                return p

            resolved_image_path = _resolve_image(str(image_path))
            if not os.path.exists(resolved_image_path):
                logger.warning(f"Layout match: image file not found: {resolved_image_path}")
                return False
            try:
                template_img = Image.open(resolved_image_path).convert("RGB")
            except Exception as e:
                logger.warning(f"Layout match: failed to load template image: {e}")
                return False

        if template_img is None:
            logger.warning("Layout match: failed to load any template image")
            return False

        template_size = template_img.size
        try:
            template_bytes = template_img.tobytes()
        except Exception as e:
            logger.warning(f"Layout match: failed to get template bytes: {e}")
            return False

        expected_x = conditional_node.get('target_x')
        expected_y = conditional_node.get('target_y')
        expected_w = conditional_node.get('target_w')
        expected_h = conditional_node.get('target_h')
    
        try:
            expected_x_i = int(float(expected_x))
            expected_y_i = int(float(expected_y))
            expected_w_i = int(float(expected_w))
            expected_h_i = int(float(expected_h))
        except Exception:
            logger.warning("Layout match: invalid target coordinates")
            return False
        if expected_w_i <= 0 or expected_h_i <= 0:
            logger.warning("Layout match: invalid target dimensions")
            return False
    
        try:
            max_attempts = int(conditional_node.get('max_attempts', 500) or 500)
        except Exception:
            max_attempts = 500
        max_attempts = max(1, max_attempts)
    
        try:
            scroll_direction = int(conditional_node.get('scroll_direction', 1) or 1)  # 1 = up, -1 = down
        except Exception:
            scroll_direction = 1

        # Timeout is disabled by default (0) - search continues until match found
        # Set timeout > 0 to limit search time in seconds
        try:
            timeout = float(conditional_node.get('timeout', 0) or 0)
        except Exception:
            timeout = 0
        if timeout < 0:
            timeout = 0
    
        agent_url = None
        try:
            if callable(get_sandbox_agent_url):
                agent_url = get_sandbox_agent_url()
        except Exception:
            agent_url = None
    
        def _send_agent_action(payload):
            if not agent_url:
                return False
            try:
                import urllib.request
                import json
                base_url = str(agent_url).rstrip('/')
                req = urllib.request.Request(
                    f"{base_url}/action",
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=10.0) as response:
                    return int(getattr(response, 'status', 200) or 200) == 200
            except Exception:
                return False
    
        logger.info(f"Layout match: target position ({expected_x_i}, {expected_y_i}), size {template_size}")
        logger.info(f"Layout match: max_attempts={max_attempts}, scroll_direction={'up' if scroll_direction > 0 else 'down'}, timeout={'disabled' if timeout <= 0 else f'{timeout}s'}")

        start_time = time.time()
    
        def _capture_region():
            """Capture the screen region at the expected position."""
            try:
                screen = self.template_matcher.capture_screen()
            except Exception:
                screen = None
            if screen is None:
                if pyautogui is None:
                    return None
                try:
                    screen = pyautogui.screenshot()
                except Exception:
                    return None
    
            try:
                if hasattr(screen, "convert"):
                    screen_img = screen.convert("RGB")
                else:
                    try:
                        import numpy as _np
                    except Exception:
                        _np = None
                    if _np is None:
                        return None
                    arr = _np.asarray(screen)
                    if arr is None or getattr(arr, "ndim", 0) < 2:
                        return None
                    if arr.ndim == 3 and arr.shape[2] >= 3:
                        rgb = arr[:, :, :3][:, :, ::-1]
                        screen_img = Image.fromarray(rgb.astype("uint8"), "RGB")
                    else:
                        return None
            except Exception:
                return None
    
            # Calculate region bounds centered on expected position
            left = int(expected_x_i - (expected_w_i // 2))
            top = int(expected_y_i - (expected_h_i // 2))
            right = int(left + expected_w_i)
            bottom = int(top + expected_h_i)
    
            if left < 0 or top < 0:
                return None
            try:
                sw, sh = screen_img.size
            except Exception:
                return None
            if right > sw or bottom > sh:
                return None
    
            try:
                region = screen_img.crop((left, top, right, bottom))
            except Exception:
                return None
            return region
    
        def _region_matches_exactly(region_img):
            """Check if region matches template exactly (no tolerance)."""
            if region_img is None:
                return False
            try:
                if region_img.size != template_size:
                    return False
                # Exact byte comparison - no tolerance
                return region_img.tobytes() == template_bytes
            except Exception:
                return False
    
        def _do_scroll_one_pixel():
            """Scroll one pixel in the configured direction."""
            # Scroll amount: positive = scroll up (content moves down), negative = scroll down
            # scroll_direction: 1 = up, -1 = down
            # pyautogui.scroll positive = scroll up, negative = scroll down
            scroll_amt = scroll_direction  # +1 for up, -1 for down
                
            if agent_url:
                _send_agent_action({"action": "scroll", "amount": int(scroll_amt)})
                return
            if pyautogui is None:
                return
            try:
                pyautogui.scroll(int(scroll_amt))
            except Exception:
                return
    
        # Main loop: scan line by line with 1-pixel scrolling at MAX SPEED
        # No artificial delays - the system's speed depends on host PC performance
        for attempt in range(max_attempts):
            if stop_flag and stop_flag():
                logger.info("Layout match: stopped by flag")
                return False

            # Check timeout if enabled
            if timeout > 0:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    logger.info(f"Layout match: timeout ({timeout}s) reached after {attempt} attempts")
                    return False
    
            # Capture the region at expected position
            region = _capture_region()
                
            # Check for exact match
            if _region_matches_exactly(region):
                logger.info(f"Layout match: found exact match at attempt {attempt}")
                return True
    
            # Scroll one pixel and immediately retry - no delay between attempts
            _do_scroll_one_pixel()
    
        logger.info(f"Layout match: no match found after {max_attempts} attempts")
        return False
    
    def get_conditional_branch_node(self, node, result):
        """
        Get the next node ID based on conditional result.
        
        Args:
            node (dict): The conditional node
            result (bool): The conditional evaluation result
            
        Returns:
            str: Next node ID, or None if no connection found
        """
        # File-loop conditionals run an internal sequence/chain file until the
        # condition stops holding, then continue the workflow — they expose a
        # single 'output' port instead of true/false branches.
        data = node.get('data', {}) or {}
        loop_type = str(data.get('loop_type', '') or '').strip().lower()
        seq_file = str(data.get('sequence_file', '') or data.get('sequence', '') or '').strip()
        chain_file = str(data.get('chain_file', '') or data.get('chain', '') or '').strip()
        if loop_type in ('while_present', 'while_absent', 'until_present', 'until_absent') and (seq_file or chain_file):
            branch = 'output'
        else:
            # Determine which branch to follow based on result
            branch = 'true' if result else 'false'
        
        # Get connections for this branch
        connections = node.get('connections', {})
        branch_connections = connections.get(branch, [])
        if not branch_connections and isinstance(connections, list):
            # List-based connection format
            branch_connections = [
                c for c in connections
                if str(c.get('output_port')).lower() == branch
            ]
        
        target_node = None
        if branch_connections:
            # Take the first connection in the branch
            target_node = branch_connections[0].get('node_id') or branch_connections[0].get('target_node_id')
        
        if target_node:
            logger.info(f"Following '{branch}' branch to node: {target_node}")
            return target_node
        else:
            logger.info(f"No connection for '{branch}' branch, ending workflow")
            return None
    
    def handle_until_present_condition(self, conditional_node, stop_flag=None):
        """
        Handle until present condition for backward compatibility.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        logger.info("Handling until_present condition (backward compatibility)")
        
        # Convert to standard presence trigger format
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'image_data': conditional_node.get('image_data', ''),
            'confidence': float(conditional_node.get('threshold', 0.8)),
            'timeout': float(conditional_node.get('wait_time', 10))
        }
        
        # Use presence trigger with timeout
        return self.presence_trigger.check_presence_with_timeout(
            condition_config, 
            timeout=condition_config['timeout']
        )
    
    def handle_until_absent_condition(self, conditional_node, stop_flag=None):
        """
        Handle until absent condition for backward compatibility.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        logger.info("Handling until_absent condition (backward compatibility)")
        
        # Convert to standard absence trigger format
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'image_data': conditional_node.get('image_data', ''),
            'confidence': float(conditional_node.get('threshold', 0.8)),
            'timeout': float(conditional_node.get('wait_time', 10))
        }
        
        # Use absence trigger with timeout
        return self.absence_trigger.check_absence_trigger(
            condition_config, 
            timeout=condition_config['timeout']
        )
    
    def validate_conditional_node(self, node):
        """
        Validate a conditional node for completeness and correctness.
        
        Args:
            node (dict): The conditional node to validate
            
        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            if 'data' not in node:
                return False, "Node missing data section"
            
            data = node['data']
            
            # Check for required fields
            if 'node_id' not in data:
                return False, "Node missing node_id"
            
            # Check trigger type
            trigger_type = data.get('trigger_type') or data.get('condition_type')
            if not trigger_type:
                return False, "Node missing trigger_type or condition_type"
            
            valid_triggers = ['presence', 'absence', 'ocr', 'loop']
            if trigger_type not in valid_triggers:
                return False, f"Invalid trigger type: {trigger_type}. Must be one of: {valid_triggers}"
            
            # Validate based on trigger type
            if trigger_type in ['presence', 'absence']:
                image_path = data.get('image_path')
                if not image_path:
                    return False, f"Image path required for {trigger_type} trigger"
                # Only ensure a path is provided; screen presence is validated at runtime via OpenCV
                resolved_path = self.path_resolver.resolve_path(image_path, add_json=False)
                if not resolved_path:
                    return False, f"Invalid image path: {image_path}"
            
            elif trigger_type == 'ocr':
                target_text = data.get('ocr_text') or data.get('target_text')
                if not target_text:
                    return False, "Target text required for OCR trigger"
            
            elif trigger_type == 'loop':
                sequence_file = data.get('sequence_file')
                if not sequence_file:
                    return False, "Sequence file required for loop trigger"
                
                # Check if sequence file exists
                resolved_file = self.path_resolver.resolve_path(sequence_file, add_json=True)
                if not resolved_file:
                    return False, f"Sequence file not found: {sequence_file}"
            
            # Check connections
            connections = node.get('connections', {})
            if 'true' not in connections and 'false' not in connections:
                return False, "Node missing both true and false branch connections"
            
            return True, "Node is valid"
            
        except Exception as e:
            return False, f"Error validating node: {e}"
