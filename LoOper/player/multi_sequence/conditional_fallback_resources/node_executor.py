# node_executor.py
"""
Node executor module for conditional fallback handler.
Handles conditional node execution and workflow branching logic.
"""
import json
import os
from ...config import logger


class NodeExecutor:
    """Handles conditional node execution and workflow branching"""

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
        sandboxed = (app_context or {}).get("sandboxed", False)

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
            if exe:
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
                    agent_script = os.path.join(project_root, "LoOper", "sandbox_agent.py")

                    import sys
                    safe_python = ""
                    candidates = [
                        os.path.join(project_root, ".venv", "Scripts", "python.exe"),
                        os.path.join(project_root, ".venv", "scripts", "python.exe"),
                        sys.executable,
                    ]
                    for c in candidates:
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

                    task_name = f"LoOperAgentStartAgentFallback_{os.getpid()}"
                    ps1_path = os.path.join(temp_dir, f"start_agent_task_fallback_{os.getpid()}.ps1")
                    agent_log = os.path.join(temp_dir, "agent_log.txt")
                    agent_task_error = os.path.join(temp_dir, "agent_task_error.txt")
                    exe_dir = os.path.dirname(exe) if exe else ""

                    ps_lines = [
                        "$ErrorActionPreference = 'Stop'",
                        f"$env:PYTHONPATH = '{project_root}'",
                        f"$env:LOOPER_AGENT_PORT = '{int(port)}'",
                        "try {",
                    ]
                    if exe:
                        ps_lines.append(f"  Start-Process -FilePath '{exe}' -WorkingDirectory '{exe_dir or temp_dir}'")
                    ps_lines.append(f"  & '{safe_python}' '{agent_script}' --port {int(port)} *>> '{agent_log}'")
                    ps_lines.extend(
                        [
                            "} catch {",
                            f"  ($_ | Out-String) | Out-File -FilePath '{agent_task_error}' -Encoding utf8",
                            "  throw",
                            "}",
                        ]
                    )
                    with open(ps1_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(ps_lines) + "\n")

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
                            f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{ps1_path}"',
                            "/f",
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if r.returncode != 0:
                        logger.error(f"Failed to create scheduled task for agent startup: {r.stdout} {r.stderr}")
                        return False

                    # Add credentials
                    os.system('cmdkey /generic:TERMSRV/127.0.0.2 /user:LoOperAgent /pass:LoOperPassword123!')
                    
                    # Ensure agent account has full read/write access to the TempShared folder
                    try:
                        subprocess.run(["icacls", temp_dir, "/grant", "LoOperAgent:(OI)(CI)F", "/T"], capture_output=True)
                    except Exception:
                        pass
                        
                    # Create RDP file
                    rdp_file = os.path.join(temp_dir, "agent_session.rdp")
                    ctx = app_context if isinstance(app_context, dict) else {}
                    show_window = ctx.get("show_window", False)
                    screen_mode = "1" if show_window else "2"
                    with open(rdp_file, "w") as f:
                        f.write(f'''screen mode id:i:{screen_mode}
use multimon:i:0
desktopwidth:i:1920
desktopheight:i:1080
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

                    try:
                        import urllib.request
                        agent_info_path = os.path.join(temp_dir, "agent_info.json")
                        agent_url = f"http://127.0.0.2:{int(port)}"
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
                                        agent_url = f"http://127.0.0.2:{int(port)}"
                                except Exception:
                                    pass

                            try:
                                with socket.create_connection(("127.0.0.2", int(port)), timeout=0.25):
                                    reachable = True
                                    break
                            except Exception:
                                pass
                            time.sleep(0.25)
                    except Exception:
                        pass

                    if not reachable:
                        try:
                            if os.path.exists(agent_task_error):
                                with open(agent_task_error, "r", encoding="utf-8", errors="ignore") as f:
                                    logger.error(f"RDP agent task error: {f.read()}")
                        except Exception:
                            pass
                        try:
                            if os.path.exists(agent_log):
                                with open(agent_log, "r", encoding="utf-8", errors="ignore") as f:
                                    tail = f.read()[-5000:]
                                    if tail:
                                        logger.error(f"RDP agent log tail: {tail}")
                        except Exception:
                            pass
                        subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True, text=True)
                        logger.error(f"RDP Agent did not become reachable in time: {agent_url}")
                        return False

                    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True, text=True)
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
                ok = self._ensure_app_open_and_focused(expected_app, stop_flag, timeout=float(expected_app.get("focus_timeout", 2.0)))
            except Exception:
                ok = True
            if not ok:
                logger.info("Conditional gated: expected app not open/focused")
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

    def _evaluate_layout_match_conditional(self, conditional_node, stop_flag=None):
        import time
        try:
            import pyautogui
        except Exception:
            pyautogui = None
        try:
            from player.computer_vision import get_sandbox_agent_url
        except Exception:
            get_sandbox_agent_url = None

        image_path = conditional_node.get('image_path', '') or ''
        if not str(image_path).strip():
            return False

        try:
            confidence = float(conditional_node.get('threshold', conditional_node.get('confidence', 0.8)))
        except Exception:
            confidence = 0.8

        try:
            timeout_s = float(conditional_node.get('timeout', 10) or 10)
        except Exception:
            timeout_s = 10.0
        if timeout_s <= 0:
            timeout_s = 10.0

        try:
            max_attempts = int(conditional_node.get('max_attempts', 30) or 30)
        except Exception:
            max_attempts = 30
        max_attempts = max(1, max_attempts)

        try:
            attempt_delay = float(conditional_node.get('delay_between_attempts', 0.05) or 0.05)
        except Exception:
            attempt_delay = 0.05

        try:
            scroll_amount = int(conditional_node.get('scroll_amount', 400) or 400)
        except Exception:
            scroll_amount = 400
        scroll_amount = max(1, scroll_amount)

        def _resolve_image(p):
            try:
                rp = self.path_resolver.resolve_path(p, add_json=False)
                if rp:
                    return rp
            except Exception:
                pass
            return p

        resolved_image_path = _resolve_image(str(image_path))

        expected_x = conditional_node.get('target_x')
        expected_y = conditional_node.get('target_y')
        expected_w = conditional_node.get('target_w')
        expected_h = conditional_node.get('target_h')
        use_expected_position = False
        try:
            expected_w_i = int(float(expected_w))
            expected_h_i = int(float(expected_h))
            use_expected_position = expected_w_i > 0 and expected_h_i > 0
        except Exception:
            use_expected_position = False
        if use_expected_position:
            try:
                expected_x_i = int(float(expected_x))
                expected_y_i = int(float(expected_y))
            except Exception:
                use_expected_position = False
                expected_x_i = 0
                expected_y_i = 0
        else:
            expected_x_i = 0
            expected_y_i = 0

        try:
            position_tolerance = int(conditional_node.get('position_tolerance', conditional_node.get('tolerance', 6)) or 6)
        except Exception:
            position_tolerance = 6
        position_tolerance = max(0, position_tolerance)
        try:
            region_expand_factor = float(conditional_node.get('region_expand_factor', 2.0) or 2.0)
        except Exception:
            region_expand_factor = 2.0
        region_expand_factor = max(1.0, region_expand_factor)

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

        def _check_match(screen_cv=None):
            if stop_flag and stop_flag():
                return False
            try:
                if use_expected_position and hasattr(self.template_matcher, 'region_based_search'):
                    loc = self.template_matcher.region_based_search(
                        resolved_image_path,
                        expected_x_i,
                        expected_y_i,
                        confidence,
                        region_expand_factor,
                        True,
                        screen_cv
                    )
                    if not loc:
                        loc = self.template_matcher.region_based_search(
                            resolved_image_path,
                            expected_x_i,
                            expected_y_i,
                            confidence,
                            region_expand_factor,
                            False,
                            screen_cv
                        )
                else:
                    loc = None
                if not loc:
                    loc = self.template_matcher.multiscale_template_match(
                        resolved_image_path,
                        confidence=confidence,
                        scales=[1.0],
                        grayscale=True,
                        screen_cv=screen_cv
                    )
                    if not loc:
                        loc = self.template_matcher.multiscale_template_match(
                            resolved_image_path,
                            confidence=confidence,
                            scales=[1.0],
                            grayscale=False,
                            screen_cv=screen_cv
                        )
            except Exception:
                loc = None
            if not loc:
                return False
            if not use_expected_position:
                return True
            try:
                dx = abs(int(loc[0]) - expected_x_i)
                dy = abs(int(loc[1]) - expected_y_i)
                return dx <= position_tolerance and dy <= position_tolerance
            except Exception:
                return False

        def _do_scroll(amount):
            if agent_url:
                _send_agent_action({"action": "scroll", "amount": int(amount)})
                return
            if pyautogui is None:
                return
            try:
                pyautogui.scroll(int(amount))
            except Exception:
                return

        def _do_hscroll(amount):
            if agent_url:
                key = 'right' if amount > 0 else 'left'
                try:
                    reps = max(1, abs(int(amount)) // 100)
                except Exception:
                    reps = 1
                for _ in range(reps):
                    if stop_flag and stop_flag():
                        return
                    _send_agent_action({"action": "hotkey", "keys": [key]})
                return
            if pyautogui is None:
                return
            try:
                if hasattr(pyautogui, 'hscroll'):
                    pyautogui.hscroll(int(amount))
                    return
            except Exception:
                pass
            try:
                pyautogui.keyDown('shift')
                pyautogui.scroll(int(amount))
                pyautogui.keyUp('shift')
            except Exception:
                try:
                    pyautogui.keyUp('shift')
                except Exception:
                    pass

        def _do_zoom(zoom_in):
            if agent_url:
                if _send_agent_action({"action": "hotkey", "keys": ["ctrl", "=" if zoom_in else "-"]}):
                    return
                _send_agent_action({"action": "hotkey", "keys": ["ctrl", "+" if zoom_in else "-"]})
                return
            if pyautogui is None:
                return
            try:
                pyautogui.hotkey('ctrl', '=' if zoom_in else '-')
            except Exception:
                try:
                    pyautogui.hotkey('ctrl', '+' if zoom_in else '-')
                except Exception:
                    return

        deadline = time.time() + timeout_s

        try:
            screen_cv0 = self.template_matcher.capture_screen()
        except Exception:
            screen_cv0 = None
        if _check_match(screen_cv0):
            return True
        try:
            import random
        except Exception:
            random = None

        try:
            zoom_prob = float(conditional_node.get('zoom_prob', 0.12) or 0.12)
        except Exception:
            zoom_prob = 0.12
        zoom_prob = max(0.0, min(1.0, zoom_prob))

        try:
            hscroll_prob = float(conditional_node.get('hscroll_prob', 0.10) or 0.10)
        except Exception:
            hscroll_prob = 0.10
        hscroll_prob = max(0.0, min(1.0, hscroll_prob))

        try:
            flip_prob = float(conditional_node.get('scroll_flip_prob', 0.06) or 0.06)
        except Exception:
            flip_prob = 0.06
        flip_prob = max(0.0, min(1.0, flip_prob))

        try:
            hold_steps = int(conditional_node.get('direction_hold_steps', 12) or 12)
        except Exception:
            hold_steps = 12
        hold_steps = max(1, hold_steps)

        try:
            scroll_step_min = int(conditional_node.get('scroll_step_min', 30) or 30)
        except Exception:
            scroll_step_min = 30
        scroll_step_min = max(1, scroll_step_min)
        try:
            scroll_step_max = int(conditional_node.get('scroll_step_max', scroll_amount) or scroll_amount)
        except Exception:
            scroll_step_max = scroll_amount
        scroll_step_max = max(scroll_step_min, scroll_step_max)

        try:
            settle_min = float(conditional_node.get('settle_min', attempt_delay) or attempt_delay)
        except Exception:
            settle_min = attempt_delay
        settle_min = max(0.01, settle_min)
        try:
            settle_max = float(conditional_node.get('settle_max', max(settle_min, 0.18)) or max(settle_min, 0.18))
        except Exception:
            settle_max = max(settle_min, 0.18)
        settle_max = max(settle_min, settle_max)

        if attempt_delay <= 0:
            attempt_delay = 0.02

        try:
            min_steps_from_timeout = int((timeout_s / max(0.02, attempt_delay)) * 3)
        except Exception:
            min_steps_from_timeout = max_attempts
        max_attempts = max(max_attempts, min_steps_from_timeout)

        vdir = 1
        hdir = 1
        if random is not None:
            try:
                vdir = 1 if random.random() < 0.5 else -1
                hdir = 1 if random.random() < 0.5 else -1
            except Exception:
                vdir = 1
                hdir = 1
        v_hold = 0
        h_hold = 0

        step = 0
        while step < max_attempts and time.time() <= deadline:
            if stop_flag and stop_flag():
                return False

            do_zoom = False
            if random is None:
                do_zoom = (step % 9 == 0)
            else:
                try:
                    do_zoom = random.random() < zoom_prob
                except Exception:
                    do_zoom = False

            if do_zoom:
                zoom_in = True
                if random is not None:
                    try:
                        zoom_in = random.random() < 0.5
                    except Exception:
                        zoom_in = True
                _do_zoom(bool(zoom_in))
            else:
                do_h = False
                if random is not None:
                    try:
                        do_h = random.random() < hscroll_prob
                    except Exception:
                        do_h = False

                amt = scroll_step_max
                if random is not None:
                    try:
                        amt = random.randint(scroll_step_min, scroll_step_max)
                    except Exception:
                        amt = scroll_step_max

                if do_h:
                    if h_hold >= hold_steps and random is not None:
                        try:
                            if random.random() < flip_prob:
                                hdir *= -1
                                h_hold = 0
                        except Exception:
                            pass
                    _do_hscroll(int(hdir * amt))
                    h_hold += 1
                else:
                    if v_hold >= hold_steps and random is not None:
                        try:
                            if random.random() < flip_prob:
                                vdir *= -1
                                v_hold = 0
                        except Exception:
                            pass
                    _do_scroll(int(vdir * amt))
                    v_hold += 1

            settle = settle_min
            if random is not None:
                try:
                    settle = random.uniform(settle_min, settle_max)
                except Exception:
                    settle = settle_min
            elapsed = 0.0
            while elapsed < settle:
                if stop_flag and stop_flag():
                    return False
                st = min(0.05, settle - elapsed)
                time.sleep(st)
                elapsed += st

            try:
                screen_cv = self.template_matcher.capture_screen()
            except Exception:
                screen_cv = None
            if _check_match(screen_cv):
                return True

            step += 1

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
        # Determine which branch to follow based on result
        branch = 'true' if result else 'false'
        
        # Get connections for this branch
        connections = node.get('connections', {})
        branch_connections = connections.get(branch, [])
        
        target_node = None
        if branch_connections:
            # Take the first connection in the branch
            target_node = branch_connections[0].get('node_id')
        
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
