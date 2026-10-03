import logging
logger = logging.getLogger(__name__)
# sequence_executor.py
"""
Sequence executor for multi-sequence automation.
Handles execution of sequence nodes and condition handling.
"""
import os
import sys
import json
import time
import re
import copy
from ..sequence_player import SequencePlayer
from ..json_cache import get_sequence
from ..base_bot import SeleniumBot
from ..action_handlers import CLICK_JITTER_MIN, CLICK_JITTER_MAX


def _is_sequence_shape(path: str) -> bool:
    """Return True when the JSON file at *path* has sequence shape.

    A valid sequence file carries an ``actions`` list (plus optional
    ``metadata``/``collection``).  Chain files (``chains/*.json``) carry
    chain-only fields (``llm_nodes``, ``conditional_nodes``, ...) and no
    ``actions`` — they must never be accepted as sequences.  This shape
    check is what prevents the chains/CLOSE.json-vs-sequences/CLOSE.json
    collision.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("actions"), list)


class SequenceExecutor:
    """Executes sequence nodes and handles conditions"""
    
    def __init__(self, fallback_handler=None, chain_file_dir=None):
        self.fallback_handler = fallback_handler
        self.chain_file_dir = chain_file_dir  # Directory containing the chain file
        # Shared selection memory to avoid random candidate selection across sequences in a chain run
        self.selection_memory = {}
        # ONE shared web browser PER CHAIN RUN: created by the first web
        # sequence node, reused by every subsequent node of THIS chain
        # (cookies, localStorage and page state persist across its nodes), and
        # closed by close_web_session() once the workflow completes.
        self._web_session_driver = None
        # Temporal cursor of already-clicked repeating elements (Insert-marked),
        # keyed by resolved session path: persists across the passes of a node
        # AND across re-executions of the same sequence within ONE chain run, so
        # each execution clicks the NEXT element of the kind instead of
        # repeating the first.  Reset by close_web_session() at chain end.
        self._web_entity_states = {}
        # GUI playback passes ``web_chain_key`` (the shared scope): the app's
        # durable browser is used and stays open after the run.  Non-GUI runs
        # (agent/scheduled/CLI) instead get ``web_profile_key`` - also the
        # shared scope - so the browser reuses the durable profile where the
        # recording logins live.  With neither (web_isolated runs), every run
        # gets a fresh disposable browser.
        self.web_chain_key = None
        self.web_profile_key = None
        # True when the shared driver ATTACHED to a browser the user already
        # had open (or the GUI browser): close_web_session must then leave it
        # running instead of quitting it.
        self._web_session_released = False

    def _get_expected_app_context_from_sequence(self, sequence_data):
        try:
            actions = (sequence_data or {}).get("actions", [])
            for a in actions:
                if not isinstance(a, dict):
                    continue
                ctx = a.get("app_context")
                if isinstance(ctx, dict) and any(ctx.get(k) for k in ("exe", "process_name", "title", "pid")):
                    return ctx
        except Exception:
            pass
        return None

    def _verify_app_running(self, app_context, timeout=2.0):
        """
        Passively verify that the expected app process is running, WITHOUT
        modifying window focus, z-order, or display resolution.

        Sequences must not bring windows to the foreground or call
        SetForegroundWindow / ShowWindow / BringWindowToTop, as those calls
        can trigger DPI scaling changes and browser scroll position resets
        on Windows.

        Returns True if the process is found running (or no context given),
        False if the process is not found within the timeout.
        """
        try:
            import psutil
        except Exception:
            return True

        exe = str((app_context or {}).get("exe") or "")
        proc_name = str((app_context or {}).get("process_name") or "")
        pid_hint = (app_context or {}).get("pid")

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
                continue
            time.sleep(0.05)

        return False

    def _is_app_in_foreground(self, app_context):
        try:
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
        except Exception:
            win32gui = None
            win32process = None

        exe = str((app_context or {}).get("exe") or "")
        proc_name = str((app_context or {}).get("process_name") or "")
        title_hint = str((app_context or {}).get("title") or "")
        pid_hint = (app_context or {}).get("pid")

        exe_norm = os.path.normcase(exe) if exe else ""
        proc_name_norm = proc_name.lower() if proc_name else ""

        hwnd = None
        title = ""
        try:
            if gw is not None:
                w = gw.getActiveWindow()
                if w is not None:
                    title = str(getattr(w, "title", "") or "")
                    hwnd = getattr(w, "_hWnd", None)
        except Exception:
            pass

        try:
            if hwnd is None and win32gui is not None:
                hwnd = win32gui.GetForegroundWindow()
                title = str(win32gui.GetWindowText(hwnd) or "")
        except Exception:
            pass

        fg_pid = None
        try:
            if hwnd and win32process is not None:
                _tid, fg_pid = win32process.GetWindowThreadProcessId(int(hwnd))
        except Exception:
            fg_pid = None

        if not fg_pid:
            return False

        if pid_hint and int(pid_hint) == int(fg_pid):
            return True

        try:
            p = psutil.Process(int(fg_pid))
            if exe_norm:
                try:
                    if os.path.normcase(str(p.exe() or "")) == exe_norm:
                        return True
                except Exception:
                    pass
            if proc_name_norm:
                try:
                    if str(p.name() or "").lower() == proc_name_norm:
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        if title_hint:
            try:
                if title_hint.strip().lower() in str(title or "").lower():
                    return True
            except Exception:
                pass

        return False

    def _ensure_app_open_and_focused(self, app_context, stop_flag, attempts=3):
        try:
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
        focus_timeout = float((app_context or {}).get("focus_timeout", 2.0))
        sandboxed = (app_context or {}).get("sandboxed", False)

        # Source-root for PYTHONPATH in the PowerShell launch script.
        # Only used in source (non-frozen) mode; in frozen mode we skip PYTHONPATH.
        _src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

        exe_norm = os.path.normcase(exe) if exe else ""
        proc_name_norm = proc_name.lower() if proc_name else ""

        def _extract_host_port(url):
            try:
                from urllib.parse import urlparse
                p = urlparse(str(url))
                host = p.hostname
                port = p.port
                if not host:
                    return None, None
                if not port:
                    port = 80 if (p.scheme or "").lower() == "http" else 443
                return host, int(port)
            except Exception:
                return None, None

        def _agent_reachable(url, timeout_s=0.35):
            if not url:
                return False
            host, port = _extract_host_port(url)
            if not host or not port:
                return False
            try:
                import socket
                with socket.create_connection((host, int(port)), timeout=float(timeout_s or 0.35)):
                    return True
            except Exception:
                return False

        def _clear_agent_urls():
            try:
                if isinstance(app_context, dict):
                    if "sandbox_agent_url" in app_context:
                        del app_context["sandbox_agent_url"]
                    if "rdp_agent_url" in app_context:
                        del app_context["rdp_agent_url"]
            except Exception:
                pass
            try:
                g = getattr(self, "global_app_context_override", None)
                if isinstance(g, dict):
                    if "sandbox_agent_url" in g:
                        del g["sandbox_agent_url"]
                    if "rdp_agent_url" in g:
                        del g["rdp_agent_url"]
            except Exception:
                pass

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
                        w_app = app.top_window()
                        try:
                            w_app.set_focus()
                        except Exception:
                            w_app.set_focus()
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                if win32gui is not None:
                    try:
                        if win32con is not None:
                            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                            time.sleep(0.05)
                    except Exception:
                        pass
                    
                    # Restore window geometry if captured
                    rect = (app_context or {}).get("rect")
                    if rect:
                        try:
                            l = rect.get("left")
                            t = rect.get("top")
                            w_rect = rect.get("width")
                            h_rect = rect.get("height")
                            if all(v is not None for v in (l, t, w_rect, h_rect)):
                                win32gui.MoveWindow(hwnd, int(l), int(t), int(w_rect), int(h_rect), True)
                                time.sleep(0.05)
                        except Exception:
                            pass

                    try:
                        win32gui.SetForegroundWindow(hwnd)
                        time.sleep(0.05)
                    except Exception:
                        try:
                            win32gui.BringWindowToTop(hwnd)
                            time.sleep(0.05)
                        except Exception:
                            pass
            except Exception:
                pass

            fhwnd = _foreground_hwnd()
            if fhwnd and int(fhwnd) == int(hwnd):
                return True
            return False

        def _try_launch_exe():
            # In purely sandboxed mode without an explicit exe, we STILL need to launch the RDP session.
            # We should only return False early if it's NOT sandboxed AND there's no exe.
            if not exe and not sandboxed:
                return False
            # Skip exe existence check when sandboxed — the exe path is from the
            # recording machine and the target app runs inside the RDP session, not here.
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
                    ctx = app_context if isinstance(app_context, dict) else {}
                    existing = None
                    try:
                        existing = (ctx.get("sandbox_agent_url") or ctx.get("rdp_agent_url"))
                    except Exception:
                        existing = None
                    if not existing:
                        try:
                            g = getattr(self, "global_app_context_override", None)
                            if isinstance(g, dict):
                                existing = g.get("sandbox_agent_url") or g.get("rdp_agent_url")
                        except Exception:
                            existing = None
                    if existing:
                        if not _agent_reachable(existing):
                            _clear_agent_urls()
                            existing = None
                        else:
                            try:
                                if isinstance(app_context, dict):
                                    app_context["sandbox_agent_url"] = existing
                                    app_context["rdp_agent_url"] = existing
                            except Exception:
                                pass
                            return True
                    if existing:
                        return True

                    logger.info("Initializing Concurrent RDP Agent Session...")
                    
                    temp_dir = "C:\\TempShared"
                    os.makedirs(temp_dir, exist_ok=True)
                    
                    for name in ("agent_info.json", "agent_error.txt", "agent_log.txt", "agent_status.txt"):
                        p = os.path.join(temp_dir, name)
                        try:
                            if os.path.exists(p):
                                os.remove(p)
                        except Exception:
                            pass
                        
                    # --- Resolve project root for finding bundled resources -------
                    # In frozen (compiled) mode, __file__ points inside the PyInstaller
                    # temp extraction directory (sys._MEIPASS).  Computing project_root
                    # from __file__ therefore gives a temp-path that does NOT correspond
                    # to the actual installation directory.  Use sys.executable's directory
                    # as the stable anchor instead.
                    _exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else \
                        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

                    # --- Resolve sandbox_agent.py ---------------------------------
                    # In source mode it lives at <project>/LoOper/sandbox_agent.py.
                    # In compiled mode it is bundled as a loose data-file inside the
                    # PyInstaller archive (see LoOperApp.spec).  Search in order:
                    #   1.  <exe_dir>/LoOper/sandbox_agent.py          (compiled, onedir / installer layout)
                    #   2.  <exe_dir>/sandbox_agent.py                (spec dest='.', MSI root)
                    #   3.  <exe_dir>/arrow/sandbox_agent.py          (spec dest='arrow', MSI subfolder)
                    #   4.  _MEIPASS/LoOper/sandbox_agent.py          (compiled, onefile spec with name='LoOper')
                    #   5.  _MEIPASS/_internal/LoOper/sandbox_agent.py (compiled, onefile spec with name='_internal/LoOper')
                    #   6.  _MEIPASS/sandbox_agent.py                  (root of extraction)
                    #   7.  _MEIPASS/arrow/sandbox_agent.py           (spec dest='arrow')
                    _possible_agent_paths = []
                    if getattr(sys, 'frozen', False):
                        _possible_agent_paths.extend([
                            os.path.join(_exe_dir, 'LoOper', 'sandbox_agent.py'),
                            os.path.join(_exe_dir, 'sandbox_agent.py'),
                            os.path.join(_exe_dir, 'arrow', 'sandbox_agent.py'),
                        ])
                        _meipass = getattr(sys, '_MEIPASS', None)
                        if _meipass:
                            for _cdir in ('LoOper', '_internal', 'arrow', '.'):
                                _candidate = os.path.join(_meipass, _cdir, 'sandbox_agent.py')
                                if os.path.exists(_candidate):
                                    _possible_agent_paths.insert(0, _candidate)
                                    break
                            # Also check _internal sub-structure directly
                            for _cdir in ('LoOper', 'arrow'):
                                _candidate_internal = os.path.join(_meipass, '_internal', _cdir, 'sandbox_agent.py')
                                if os.path.exists(_candidate_internal):
                                    if not _possible_agent_paths or _possible_agent_paths[0] != _candidate_internal:
                                        _possible_agent_paths.insert(0, _candidate_internal)
                                        break
                    else:
                        _possible_agent_paths.append(os.path.join(
                            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
                            "LoOper", "sandbox_agent.py"
                        ))

                    agent_script = None
                    for _ap in _possible_agent_paths:
                        if _ap and os.path.exists(_ap):
                            agent_script = _ap
                            logger.info(f"Found sandbox_agent.py at: {_ap}")
                            break

                    # Deploy a copy into the shared folder so the RDP agent user
                    # (LoOperAgent) is guaranteed read-access regardless of where
                    # the main LoOper installation lives.
                    _deployed_agent = os.path.join(temp_dir, 'sandbox_agent.py')
                    try:
                        if agent_script and os.path.exists(agent_script):
                            import shutil
                            shutil.copy2(agent_script, _deployed_agent)
                            agent_script = _deployed_agent
                        else:
                            logger.error(f"sandbox_agent.py not found. Searched: {_possible_agent_paths}")
                            return False
                    except Exception as _copy_err:
                        logger.error(f"Failed to deploy sandbox_agent.py to {temp_dir}: {_copy_err}")
                        return False

                    # --- Resolve Python interpreter --------------------------------
                    # The RDP session needs a Python that has pyautogui available.
                    # In source mode the project .venv satisfies this.
                    # In compiled mode sys.executable (LoOper.exe) works because its
                    # built-in interpreter mode runs .py files via exec() WITHOUT opening
                    # the GUI, and all bundled modules (including pyautogui) are available.
                    safe_python = ""
                    _py_candidates = []
                    if getattr(sys, 'frozen', False):
                        # Compiled build – the .venv won't exist. Use LoOper.exe whose
                        # interpreter mode runs sandbox_agent.py correctly (Looper.py).
                        _py_candidates.append(sys.executable)
                    else:
                        _src_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
                        _py_candidates.extend([
                            os.path.join(_src_root, ".venv", "Scripts", "python.exe"),
                            os.path.join(_src_root, ".venv", "scripts", "python.exe"),
                        ])
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

                    agent_username = str(ctx.get("rdp_username") or ctx.get("username") or "LoOperAgent")
                    agent_password = str(ctx.get("rdp_password") or ctx.get("password") or "LoOperPassword123!")

                    # --- Create .bat runner script for RDP alternate shell ---
                    runner_bat = os.path.join(temp_dir, f"agent_runner_{os.getpid()}.bat")
                    bat_lines = [
                        "@echo off",
                        f"set LOOPER_AGENT_PORT={int(port)}",
                    ]
                    # Only set PYTHONPATH in source mode; in compiled mode the
                    # bundled interpreter already knows where its modules are.
                    if not getattr(sys, 'frozen', False):
                        bat_lines.append(f"set PYTHONPATH={_src_root}")
                    if exe:
                        exe_dir = os.path.dirname(exe) if exe else ""
                        bat_lines.append(f'start "" /D "{exe_dir or temp_dir}" "{exe}"')
                    bat_lines.append(f'"{safe_python}" "{agent_script}" --port {int(port)}')
                    with open(runner_bat, "w", encoding="utf-8") as f:
                        f.write("\r\n".join(bat_lines) + "\r\n")

                    # Reset LoOperAgent password and enforce no-expiry to prevent
                    # Windows RDP password-expiration popup during authentication.
                    try:
                        subprocess.run(["net", "user", agent_username, agent_password, "/passwordchg:no", "/expires:never"],
                                       capture_output=True, text=True)
                    except Exception:
                        pass

                    # Grant execute permission to LoOperAgent
                    try:
                        subprocess.run(["icacls", runner_bat, "/grant", f"{agent_username}:(RX)"], capture_output=True)
                    except Exception:
                        pass

                    try:
                        q = subprocess.run(["query", "session"], capture_output=True, text=True)
                        out = (q.stdout or "") + "\n" + (q.stderr or "")
                        for line in out.splitlines():
                            if agent_username not in line:
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

                    # Add credentials to Windows Vault for passwordless MSTSC login
                    os.system(f'cmdkey /generic:TERMSRV/127.0.0.2 /user:{agent_username} /pass:{agent_password}')
                    
                    # Ensure agent account has full read/write access to the TempShared folder
                    try:
                        subprocess.run(["icacls", temp_dir, "/grant", f"{agent_username}:(OI)(CI)F", "/T"], capture_output=True)
                    except Exception:
                        pass

                    # Create scheduled task to launch agent on RDP session logon
                    # (password was reset above, so schtasks will accept it)
                    task_name = f"LoOperAgentStart_{os.getpid()}"
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
                            agent_username,
                            "/rp",
                            agent_password,
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
                    # If app_context is literally a string or None, default to a dict
                    show_window = ctx.get("show_window", False) or getattr(self, 'global_app_context_override', {}).get("show_window", False)
                    # Use normal window mode (1) if show_window is True, otherwise use hidden/minimized
                    screen_mode = "1" if show_window else "2"
                    host_w = 1920
                    host_h = 1080
                    try:
                        import ctypes
                        host_w = int(ctypes.windll.user32.GetSystemMetrics(0))
                        host_h = int(ctypes.windll.user32.GetSystemMetrics(1))
                    except Exception:
                        host_w = 1920
                        host_h = 1080
                    desktop_w = host_w
                    desktop_h = host_h
                    try:
                        dw = ctx.get("desktopwidth") or ctx.get("desktop_width") or ctx.get("screen_width")
                        dh = ctx.get("desktopheight") or ctx.get("desktop_height") or ctx.get("screen_height")
                        if dw is not None:
                            desktop_w = int(float(dw))
                        if dh is not None:
                            desktop_h = int(float(dh))
                    except Exception:
                        desktop_w = host_w
                        desktop_h = host_h
                    with open(rdp_file, "w") as f:
                        f.write(f'''screen mode id:i:{screen_mode}
use multimon:i:0
desktopwidth:i:{int(desktop_w)}
desktopheight:i:{int(desktop_h)}
smart sizing:i:1
session bpp:i:32
full address:s:127.0.0.2
username:s:{agent_username}
audiomode:i:0
redirectprinters:i:0
redirectcomports:i:0
redirectsmartcards:i:0
redirectclipboard:i:1
redirectposdevices:i:0
autoreconnection enabled:i:1
authentication level:i:2
prompt for credentials:i:0
negotiate security layer:i:1
enablecredsspsupport:i:1
''')

                    logger.info(f"Launching {exe or 'Agent Only'} via RDP Agent Session (Show Window: {show_window})")
                    # If show_window is False, we can launch it minimized or completely hidden
                    if show_window:
                        # When launching visibly, pass creation flags to make sure it spawns a fresh console/window process
                        proc = subprocess.Popen(["mstsc.exe", rdp_file], creationflags=subprocess.CREATE_NEW_CONSOLE)
                    else:
                        # Launch hidden if not requested to show
                        startupinfo = subprocess.STARTUPINFO()
                        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                        startupinfo.wShowWindow = subprocess.SW_HIDE
                        proc = subprocess.Popen(["mstsc.exe", rdp_file], startupinfo=startupinfo)
                    try:
                        if isinstance(ctx, dict) and getattr(proc, "pid", None):
                            ctx["__looper_mstsc_pid"] = int(proc.pid)
                    except Exception:
                        pass
                    try:
                        g = getattr(self, "global_app_context_override", None)
                        if isinstance(g, dict) and getattr(proc, "pid", None):
                            g["__looper_mstsc_pid"] = int(proc.pid)
                    except Exception:
                        pass
                    
                    logger.info("Waiting for RDP Agent to become reachable...")
                    agent_url = f"http://127.0.0.1:{int(port)}"
                    reachable = False
                    agent_info_path = os.path.join(temp_dir, "agent_info.json")
                    start_deadline = time.time() + float(ctx.get("agent_wait_timeout_s", 60.0) if isinstance(ctx, dict) else 60.0)
                    try:
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

                    if app_context is not None:
                        app_context["sandbox_agent_url"] = agent_url
                        app_context["rdp_agent_url"] = agent_url
                    if hasattr(self, 'global_app_context_override') and isinstance(self.global_app_context_override, dict):
                        self.global_app_context_override["sandbox_agent_url"] = agent_url
                        self.global_app_context_override["rdp_agent_url"] = agent_url

                    subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], capture_output=True, text=True)

                    return True
                except Exception as e:
                    logger.error(f"Failed to launch RDP session: {e}")
                    return False
                    
            try:
                cwd = os.path.dirname(exe) or None
                subprocess.Popen([exe], cwd=cwd)
                return True
            except Exception:
                try:
                    subprocess.Popen(exe, cwd=os.path.dirname(exe) or None)
                    return True
                except Exception:
                    return False

        for attempt in range(int(attempts or 1)):
            if stop_flag and stop_flag():
                return False

            if not sandboxed and self._is_app_in_foreground(app_context):
                return True

            # If sandboxed, we bypass foreground checks and just launch/rely on the agent
            if sandboxed:
                try:
                    existing = None
                    if isinstance(app_context, dict):
                        existing = app_context.get("sandbox_agent_url") or app_context.get("rdp_agent_url")
                    if not existing and hasattr(self, "global_app_context_override") and isinstance(self.global_app_context_override, dict):
                        existing = self.global_app_context_override.get("sandbox_agent_url") or self.global_app_context_override.get("rdp_agent_url")
                    if existing:
                        if _agent_reachable(existing):
                            return True
                        _clear_agent_urls()
                except Exception:
                    pass
                if attempt == 0:
                    ok = _try_launch_exe()
                    if not ok:
                        return False
                # Wait briefly to let the sandbox spin up
                time.sleep(1.0)
                # Assume true for sandboxed as we can't easily check foreground status inside the VM from the host
                if getattr(app_context, 'sandbox_agent_url', None) or (isinstance(app_context, dict) and app_context.get("sandbox_agent_url")):
                    return True
                
                # Check for RDP concurrent agent URL
                if getattr(app_context, 'rdp_agent_url', None) or (isinstance(app_context, dict) and app_context.get("rdp_agent_url")):
                    return True
                    
                # Also check global overrides if local context is missing it
                if hasattr(self, 'global_app_context_override') and isinstance(self.global_app_context_override, dict):
                    if self.global_app_context_override.get("rdp_agent_url") or self.global_app_context_override.get("sandbox_agent_url"):
                        return True
                    
                # If there's no URL yet, we must wait a bit longer for it to boot on the first loop
                time.sleep(1.0)
                if attempt == int(attempts or 1) - 1:
                    return False
                continue

            start = time.time()
            while time.time() - start <= focus_timeout:
                if stop_flag and stop_flag():
                    return False

                pids = _candidate_pids()
                if not pids:
                    break

                for pid in pids:
                    if stop_flag and stop_flag():
                        return False
                    wins = _window_candidates_for_pid(pid)
                    hwnd = _pick_window(wins)
                    if hwnd and _focus_hwnd(hwnd, pid):
                        if self._is_app_in_foreground(app_context):
                            return True

                time.sleep(0.05)

            pids = _candidate_pids()
            if not pids:
                _try_launch_exe()
                time.sleep(0.25)
            else:
                time.sleep(0.1)

        return self._is_app_in_foreground(app_context)

    def begin_chain_sandbox(self, app_context, stop_flag=None):
        if not isinstance(app_context, dict):
            return None
        if not app_context.get("sandboxed"):
            return None
        try:
            ref = int(app_context.get("__looper_sandbox_refcount") or 0)
        except Exception:
            ref = 0
        app_context["__looper_sandbox_refcount"] = ref + 1
        existing = app_context.get("sandbox_agent_url") or app_context.get("rdp_agent_url")
        if existing:
            try:
                from urllib.parse import urlparse
                import socket
                p = urlparse(str(existing))
                host = p.hostname
                port = p.port or (80 if (p.scheme or "").lower() == "http" else 443)
                with socket.create_connection((host, int(port)), timeout=0.35):
                    # Nested chain imports reuse the invoker's session — keep the
                    # computer-vision channel pointed at the same agent so OCR /
                    # condition evaluation observes the same desktop.
                    try:
                        from ..computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(existing)
                    except Exception:
                        pass
                    return existing
            except Exception:
                try:
                    if "sandbox_agent_url" in app_context:
                        del app_context["sandbox_agent_url"]
                except Exception:
                    pass
                try:
                    if "rdp_agent_url" in app_context:
                        del app_context["rdp_agent_url"]
                except Exception:
                    pass
        ok = True
        try:
            ok = self._ensure_app_open_and_focused(app_context, stop_flag, attempts=int(app_context.get("focus_attempts", 3) or 3))
        except Exception:
            ok = True
        if not ok:
            return None
        return app_context.get("sandbox_agent_url") or app_context.get("rdp_agent_url")

    def end_chain_sandbox(self, app_context):
        if not isinstance(app_context, dict):
            return
        if not app_context.get("sandboxed"):
            return
        try:
            ref = int(app_context.get("__looper_sandbox_refcount") or 0)
        except Exception:
            ref = 0
        ref = ref - 1
        if ref < 0:
            ref = 0
        app_context["__looper_sandbox_refcount"] = ref
        if ref > 0:
            return
        keep_open = False
        try:
            keep_open = app_context.get("keep_session_open")
            if keep_open is None:
                keep_open = app_context.get("persist_session")
            if keep_open is None:
                keep_open = app_context.get("keep_sandbox_open")
            if isinstance(keep_open, str):
                keep_open = keep_open.strip().lower() in ("true", "1", "yes", "y", "on")
            keep_open = bool(keep_open)
        except Exception:
            keep_open = False
        if keep_open:
            try:
                close_mstsc = app_context.get("close_mstsc_on_end")
                if close_mstsc is None:
                    close_mstsc = True
                if isinstance(close_mstsc, str):
                    close_mstsc = close_mstsc.strip().lower() in ("true", "1", "yes", "y", "on")
                close_mstsc = bool(close_mstsc)
            except Exception:
                close_mstsc = True
            if close_mstsc:
                try:
                    pid = app_context.get("__looper_mstsc_pid")
                    if pid:
                        import subprocess
                        subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, text=True)
                except Exception:
                    pass
            return
        agent_username = str(app_context.get("rdp_username") or app_context.get("username") or "LoOperAgent")
        try:
            import subprocess
            q = subprocess.run(["query", "session"], capture_output=True, text=True)
            out = (q.stdout or "") + "\n" + (q.stderr or "")
            for line in out.splitlines():
                if agent_username not in line:
                    continue
                m = re.search(r"\s(\d+)\s+(Active|Disc|Conn|Listen)\b", line)
                if not m:
                    continue
                sid = int(m.group(1))
                subprocess.run(["logoff", str(sid)], capture_output=True, text=True)
                time.sleep(1.0)
                break
        except Exception:
            pass
        try:
            if "sandbox_agent_url" in app_context:
                del app_context["sandbox_agent_url"]
        except Exception:
            pass
        try:
            if "rdp_agent_url" in app_context:
                del app_context["rdp_agent_url"]
        except Exception:
            pass
        try:
            g = getattr(self, "global_app_context_override", None)
            if isinstance(g, dict):
                if "sandbox_agent_url" in g:
                    del g["sandbox_agent_url"]
                if "rdp_agent_url" in g:
                    del g["rdp_agent_url"]
        except Exception:
            pass
    
    def execute_sequence_node(self, node, stop_flag):
        """
        Executes a sequence node and returns the next node ID.
        
        Args:
            node (dict): The sequence node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        sequence_data = node['data']
        logger.info(f"=== STARTING SEQUENCE NODE EXECUTION ===")
        # Support both 'id' and 'node_id' fields
        node_identifier = sequence_data.get('id') or sequence_data.get('node_id', 'unknown')
        logger.info(f"Executing sequence node: {sequence_data.get('name', node_identifier)}")
        logger.info(f"Full node data: {node}")

        # NOTE: selection memory is deliberately NOT cleared here.  It now holds
        # the repeating-element cursor (which sibling rows have been clicked), so
        # a sequence replayed later in the workflow must ADVANCE that cursor
        # instead of restarting on the first row - mirroring the web entity
        # cursor.  The cursor is reset once per chain run (play_chain).

        # Execute the sequence
        success = self.execute_sequence(sequence_data, stop_flag)
        
        if not success:
            node_identifier = sequence_data.get('id') or sequence_data.get('node_id', 'unknown')
            logger.error(f"Sequence execution failed: {sequence_data.get('name', node_identifier)}")
            return None
        
        # Find next node in the workflow
        connections = node['connections']
        if 'output' in connections and connections['output']:
            # Take the first connection
            next_connection = connections['output'][0]
            return next_connection['node_id']
        else:
            logger.info("No output connections found for sequence node, ending workflow")
            return "__done__"
    
    def execute_web_sequence_node(self, node, stop_flag):
        """
        Executes a web sequence node (DOM-based browser session) and returns
        the next node ID.
        
        Args:
            node (dict): The web sequence node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        sequence_data = node['data']
        logger.info("=== STARTING WEB SEQUENCE NODE EXECUTION ===")
        node_identifier = sequence_data.get('id') or sequence_data.get('node_id', 'unknown')
        logger.info(f"Executing web sequence node: {sequence_data.get('name', node_identifier)}")

        # Execute the web session
        success = self.execute_web_sequence(sequence_data, stop_flag)

        if not success:
            logger.error(f"Web sequence execution failed: {sequence_data.get('name', node_identifier)}")
            return None

        # Find next node in the workflow
        connections = node['connections']
        if 'output' in connections and connections['output']:
            next_connection = connections['output'][0]
            return next_connection['node_id']
        else:
            logger.info("No output connections found for web sequence node, ending workflow")
            return "__done__"
    
    def execute_web_sequence(self, item, stop_flag):
        """
        Execute a single web sequence (DOM-based browser session).
        
        The node has no start URL: the session replays on the shared web
        browser and navigation comes from the session's own recorded actions.
        A session recorded from a cold start (browser default page -> address
        bar / new-tab search) carries one explicit "Open page" action that
        positions the browser via driver.get; sessions recorded on a parked
        page carry none and run on the browser's current page - so
        button/element-driven dynamic chains replay as recorded.
        
        Args:
            item (dict): Web sequence configuration item
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if the web sequence executed successfully
        """
        session_file = item.get('session_file')
        if not session_file:
            name = item.get('name', '')
            if ': ' in name:
                # Extract filename from "Seq X: filename.json" format
                session_file = name.split(': ', 1)[1]
            else:
                session_file = name

            # Handle numbered names (e.g., "1.json 2" -> "1.json")
            if session_file and re.match(r'^(.+\.json)\s+\d+$', session_file):
                session_file = re.match(r'^(.+\.json)\s+\d+$', session_file).group(1)

        if not session_file:
            logger.error("Web sequence node has no session_file and no usable name")
            return False

        resolved = self._resolve_web_session_path(session_file)
        if not resolved:
            logger.error(f"Web session file not found: '{session_file}'")
            logger.error(f"Searched in paths: {self._web_session_search_paths(session_file)}")
            logger.error(f"Current working directory: {os.getcwd()}")
            return False

        loops = item.get('loop_count', item.get('loops', 1))
        try:
            loops = int(loops)
        except (TypeError, ValueError):
            loops = 1
        extra_delay = item.get('extra_delay', 0)
        try:
            extra_delay = float(extra_delay)
        except (TypeError, ValueError):
            extra_delay = 0.0

        logger.info(f"Executing web sequence: {resolved} (loops: {loops}, extra_delay: {extra_delay})")
        return self._run_web_replay(item, resolved, stop_flag, loops, extra_delay)

    def _run_web_replay(self, item, session_path, stop_flag, loops, extra_delay):
        """Replay a web session for the given loop count with pacing between loops.

        Imported lazily so a missing web dependency surfaces as a logged error
        here (node failure) instead of breaking module import elsewhere.
        """
        try:
            from ..web.engine import replay_config_from_item
            from ..web.events import is_web_session, load_session
        except Exception as exc:
            logger.error(f"Web automation module unavailable: {exc}")
            return False

        try:
            if not is_web_session(session_path):
                logger.error(f"File is not a web session (missing mode=web marker): {session_path}")
                return False
            data = load_session(session_path)
            logger.info(f"Web session loaded: {len(data['actions'])} actions from {session_path}")
        except Exception as exc:
            logger.error(f"Failed to load web session {session_path}: {exc}")
            return False

        config = replay_config_from_item(item)
        # A repeating element (marked by holding Insert while clicking) turns the
        # node's loop into a cursor over the matching set: each pass clicks the
        # NEXT element of that kind instead of the recorded instance, so dynamic
        # lists whose inner content changes are navigated by kind, not by a
        # fixed element.  There is no dialog toggle - the marking IS the
        # definition.
        has_entity = any(getattr(a, "entity", None) for a in data["actions"])
        # The cursor (already-clicked identities) PERSISTS across the passes of
        # this node AND across re-executions of this sequence within one chain
        # run, so the same sequence advances element by element instead of
        # re-clicking the first one every time.  close_web_session() resets it
        # when the chain run ends.
        entity_state = None
        if has_entity:
            entity_state = self._web_entity_states.setdefault(str(session_path), {})
            logger.info(
                "Web sequence has a repeating-element action - clicking the next "
                "matching element each pass"
            )

        entity_exhausted = False
        for i in range(loops):
            if stop_flag and stop_flag():
                logger.info("Web sequence execution stopped by user request")
                return False

            logger.info(f"Starting loop {i + 1}/{loops} for web sequence: {session_path}")
            stats = self._replay_web_once(session_path, config, stop_flag, entity_state)
            if stats is None:
                return False

            if stats.get("attempted", 0) > 0 and stats.get("ok", 0) == 0:
                logger.error(f"All web actions failed for {session_path}; aborting sequence")
                return False

            # Every matching element has already been clicked: stop early.
            if has_entity and stats.get("entity_exhausted"):
                logger.info(
                    f"Web sequence: no more matching element to click "
                    f"(after {i + 1} pass(es))"
                )
                entity_exhausted = True
                break

            # Add extra delay between loops if specified
            if i < loops - 1 and extra_delay > 0:
                logger.info(f"Extra delay between loops: {extra_delay} seconds")
                if not self._wait_cancellable(extra_delay, stop_flag):
                    return False

        # Capture the user-selected page data (toggled in the web sequence
        # dialog) for the data-only ctx_out port.  ponytail: the item set is
        # fixed here; a new toggle in web_sequence_dialogs.py needs one more
        # elif.  Capture failure degrades to {} — never fails the replay.
        self._web_page_data = {}
        try:
            driver = self._get_or_create_web_driver(config)
            for key in (item.get('extract_items') or []):
                if key == 'page_text':
                    self._web_page_data[key] = (driver.execute_script(
                        "return document.body ? document.body.innerText : ''") or "")
                elif key == 'page_html':
                    self._web_page_data[key] = (driver.execute_script(
                        "return document.documentElement ? document.documentElement.outerHTML : ''") or "")
                elif key == 'title':
                    self._web_page_data[key] = driver.title or ""
                elif key == 'url':
                    self._web_page_data[key] = driver.current_url or ""
        except Exception:
            self._web_page_data = {}

        # Repeating-element status is a replay fact, not a page item, so it is
        # published even when the page capture above failed.  A code or LLM
        # conditional downstream branches on it when the element set is done.
        if 'entity_exhausted' in (item.get('extract_items') or []):
            self._web_page_data['entity_exhausted'] = (
                'true' if entity_exhausted else 'false'
            )

        return True

    def _replay_web_once(self, session_path, config, stop_flag, entity_state=None):
        """One replay pass of a web session; returns stats or None on failure."""
        try:
            from ..web.engine import replay_session
        except Exception as exc:
            logger.error(f"Web automation module unavailable: {exc}")
            return None
        try:
            stats = replay_session(
                str(session_path), config, stop_flag=stop_flag,
                driver=self._get_or_create_web_driver(config),
                # Reconnect mid-run when the browser window dies ("no such
                # window") - reuse the same shared-browser factory the
                # executor consults between nodes.
                driver_factory=lambda: self._get_or_create_web_driver(config),
                entity_state=entity_state,
            )
        except Exception as exc:
            logger.error(f"Error replaying web session {session_path}: {exc}")
            return None
        logger.info(
            f"Web replay stats: {stats.get('ok')}/{stats.get('attempted')} actions OK "
            f"in {stats.get('duration_sec')}s"
        )
        for failed in stats.get("failed", []):
            logger.warning(
                f"[FAIL] action #{failed.get('index')} ({failed.get('type')}): "
                f"{failed.get('error')}"
            )
        return stats

    def _wait_cancellable(self, seconds, stop_flag):
        """Sleep in small slices, aborting early when the stop flag flips."""
        remaining = float(seconds)
        while remaining > 0:
            if stop_flag and stop_flag():
                logger.info("Web sequence execution stopped during delay")
                return False
            sleep_time = min(0.1, remaining)
            time.sleep(sleep_time)
            remaining -= sleep_time
        return True

    # ------------------------------------------------------------------
    # Shared web browser lifecycle (one Chrome instance per chain run)
    # ------------------------------------------------------------------

    def _get_or_create_web_driver(self, config):
        """Return this chain's shared web browser, creating it on first use.

        All web sequence nodes of one chain share a single Chrome instance so
        the session (cookies, localStorage, current page) persists across the
        nodes of that chain - each node continues where the previous one left
        off.  Two flavours, both on the app-wide SHARED durable profile (the
        same one recordings use, so logins recorded once are present in every
        run mode and survive app restarts):
        - ``web_chain_key`` set (GUI playback): the app's durable browser is
          used - the run continues with the recorded cookies/history and the
          browser stays open afterwards for inspection.
        - ``web_profile_key`` set (agent / scheduled / CLI runs): the same
          durable profile is used (attach to the still-open shared browser
          when there is one, else launch/relaunch it).  Visible runs close
          their self-launched browser at the end (profile kept); a still-open
          shared browser is attached, not killed.  Headless runs open the
          durable profile without a window.
        - neither (web_isolated run - playback while a web recording is
          capturing, or a chain config with no file identity): every chain
          run gets its own disposable browser, terminated by
          ``close_web_session``.
        """
        driver = getattr(self, "_web_session_driver", None)
        if driver is not None and self._web_driver_alive(driver):
            return driver
        if driver is not None:
            logger.warning("Shared web browser died mid-chain; relaunching")
            try:
                from ..web.session import cleanup_session
                cleanup_session(driver)
            except Exception:
                pass
            self._web_session_driver = None
        chain_key = getattr(self, "web_chain_key", None)
        # A web orchestrator with the Headless toggle ON forces an invisible
        # browser for its child chains: never attach to the inherently-visible
        # app workbench, launch headless on the same profile scope instead.
        force_headless = False
        try:
            from ..web.session import run_headless
            force_headless = bool(run_headless())
        except Exception:
            force_headless = False
        if chain_key and not force_headless:
            # Shared app browser (GUI playback): reuse/relaunch the app-wide
            # durable profile.  It is inherently visible (headless is
            # ignored) and stays open after the run for inspection.
            from ..web.session import ensure_workbench_session
            logger.info(
                "Using shared web browser (scope=%s; headless=%s "
                "ignored: the shared browser is visible); reused by all web "
                "sequence nodes", chain_key, config.headless,
            )
            self._web_session_driver = ensure_workbench_session(chain_key=chain_key)
            self._web_session_released = True
            return self._web_session_driver
        profile_key = getattr(self, "web_profile_key", None) or (
            chain_key if force_headless else None)
        if profile_key:
            # Shared durable profile for non-GUI runs: cookies/history
            # (recording logins) persist across runs and app restarts.  The
            # last page is never restored - launches start on the browser's
            # default page, like the recording workbench.
            from ..web.session import (
                _chain_scope_dir,
                _find_workbench_browser,
                create_session,
                ensure_workbench_session,
            )
            if not config.headless and not force_headless:
                # Visible run: attach to the still-open shared browser when
                # there is one (recording left it up - attaching keeps the
                # live login instead of failing on the locked profile); a
                # manually opened browser without a debug port cannot be
                # attached to, so fall back to an isolated browser.  Otherwise
                # launch on the durable profile; the browser is quit at the
                # end of the run so cookies are flushed to the profile.
                live = _find_workbench_browser(profile_key)
                if live is not None and live.get("port"):
                    logger.info(
                        "Attaching to open shared web browser (scope=%s)",
                        profile_key,
                    )
                    self._web_session_driver = ensure_workbench_session(
                        chain_key=profile_key
                    )
                    self._web_session_released = True
                    return self._web_session_driver
                if live is not None:
                    logger.warning(
                        "Shared web profile is open in a browser not started "
                        "by LoOper (scope=%s) - using an isolated browser for "
                        "this run", profile_key,
                    )
                    self._web_session_driver = create_session(headless=False)
                    self._web_session_released = False
                    return self._web_session_driver
                logger.info(
                    "Creating shared web browser on durable profile "
                    "(scope=%s); reused by all web sequence nodes and kept "
                    "for the next run", profile_key,
                )
                self._web_session_driver = create_session(
                    headless=False, profile_dir=_chain_scope_dir(profile_key)
                )
                self._web_session_released = False
                return self._web_session_driver
            logger.info(
                "Creating shared headless web browser on durable profile "
                "(scope=%s); reused by all web sequence nodes", profile_key,
            )
            try:
                self._web_session_driver = create_session(
                    headless=True, profile_dir=_chain_scope_dir(profile_key)
                )
            except Exception as exc:
                # Profile locked by a live browser (or otherwise unusable):
                # degrade to an isolated browser so the run still happens -
                # that run just starts logged out.
                logger.warning(
                    "Durable web profile unavailable for headless run (%s); "
                    "using an isolated browser", exc,
                )
                self._web_session_driver = create_session(headless=True)
            self._web_session_released = False
            return self._web_session_driver
        from ..web.session import create_session
        logger.info(
            "Creating isolated web browser for chain (headless=%s); "
            "reused by all web sequence nodes", config.headless,
        )
        self._web_session_driver = create_session(
            headless=bool(config.headless) or force_headless)
        self._web_session_released = False
        return self._web_session_driver

    @staticmethod
    def _web_driver_alive(driver) -> bool:
        """Cheap liveness probe - raises fast when the session/browser is gone."""
        try:
            driver.execute_script("return 1")
            return True
        except Exception:
            return False

    def close_web_session(self) -> None:
        """Terminate this chain's shared web browser after all web nodes ran.

        A chain-linked browser (GUI playback) and any browser this run
        ATTACHED to (a chain browser the user already had open) are left
        running - the next recording/playback for that chain continues on its
        persistent profile (cookies/history).  A browser this run launched on
        a durable per-chain profile (non-GUI runs) is quit - cookies are
        flushed to the profile, which is kept - and disposable per-run
        browsers are closed with their profiles removed.
        """
        driver = getattr(self, "_web_session_driver", None)
        self._web_session_driver = None
        # New chain run: forget which repeating elements were already clicked.
        self._web_entity_states = {}
        if driver is None:
            return
        chain_key = getattr(self, "web_chain_key", None)
        if chain_key or getattr(self, "_web_session_released", False):
            logger.info(
                "Left chain-linked web browser open (scope=%s)",
                chain_key or getattr(self, "web_profile_key", None) or "attached",
            )
            return
        try:
            from ..web.session import cleanup_session
            cleanup_session(driver)
            logger.info("Closed shared web browser for chain")
        except Exception as exc:
            logger.warning(f"Failed to close shared web browser: {exc}")

    def _web_session_root_candidates(self):
        """Candidate project roots that may hold a web_sequences/ folder."""
        roots = []
        try:
            # d:/LoOperV2/LoOper/player/multi_sequence/sequence_executor.py -> repo root
            roots.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        except Exception:
            pass
        roots.append(os.getcwd())
        if getattr(sys, 'frozen', False):
            roots.append(os.path.dirname(sys.executable))
        return roots

    def _web_session_search_paths(self, session_file):
        """All candidate paths for a web session file (for logging/debug)."""
        paths = [session_file]  # original path (may already be absolute)
        base_dir = self.chain_file_dir if self.chain_file_dir else os.getcwd()
        is_bare = (
            os.path.basename(session_file) == session_file
            and "/" not in session_file and "\\" not in session_file
        )
        json_name = session_file if session_file.endswith('.json') else session_file + '.json'

        if is_bare:
            for root in self._web_session_root_candidates():
                paths.append(os.path.join(root, 'web_sequences', json_name))
                paths.append(os.path.join(root, json_name))
            paths.append(os.path.join(base_dir, 'web_sequences', json_name))
            paths.append(os.path.join('web_sequences', json_name))
        else:
            paths.append(os.path.join(base_dir, json_name))
            paths.append(os.path.join(base_dir, 'web_sequences', json_name))
            paths.append(os.path.join(os.getcwd(), json_name))
            paths.append(os.path.join(os.getcwd(), 'web_sequences', json_name))

        # Dedupe preserving order
        seen = set()
        deduped = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                deduped.append(p)
        return deduped

    def _resolve_web_session_path(self, session_file):
        """Resolve a web session filename to an existing web session JSON path.

        Only files carrying the mode=web marker are accepted, so desktop
        sequences/chain files can never be picked for web replay.
        """
        for path in self._web_session_search_paths(session_file):
            try:
                if os.path.exists(path) and self._is_web_session_file(path):
                    return path
            except Exception:
                continue
        return None

    @staticmethod
    def _is_web_session_file(path):
        """Cheap mode=web marker check without importing the web subsystem."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        return (
            isinstance(data, dict)
            and isinstance(data.get("actions"), list)
            and data.get("mode") == "web"
        )
    
    def execute_sequence(self, item, stop_flag):
        """
        Execute a single sequence with all its configurations
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if sequence executed successfully
        """
        # Extract sequence file from name field if sequence_file not present
        seq_file = item.get('sequence_file')
        if not seq_file:
            name = item.get('name', '')
            if ': ' in name:
                # Extract filename from "Seq X: filename.json" format
                seq_file = name.split(': ', 1)[1]
            else:
                seq_file = name
                
            # Handle numbered sequence names (e.g., "1.json 2" -> "1.json")
            # Remove trailing space and number pattern
            if seq_file and re.match(r'^(.+\.json)\s+\d+$', seq_file):
                base_name = re.match(r'^(.+\.json)\s+\d+$', seq_file).group(1)
                logger.info(f"Detected numbered sequence name '{seq_file}', using base name '{base_name}'")
                seq_file = base_name
        
        # Enhanced path resolution with better error handling
        original_seq_file = seq_file
        
        if seq_file:
            # Determine base directory for path resolution
            base_dir = self.chain_file_dir if self.chain_file_dir else os.getcwd()
            base_dir_name = os.path.basename(os.path.normpath(base_dir)).lower() if base_dir else ""
            is_bare_filename = (
                os.path.basename(seq_file) == seq_file
                and ("/" not in seq_file and "\\" not in seq_file)
            )
            
            # Try multiple path resolution strategies
            # Determine executable directory for frozen builds
            exe_dir = None
            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(sys.executable)
            _meipass = getattr(sys, '_MEIPASS', None)
            
            looper_sequences = os.path.join(os.getcwd(), 'LoOper', 'sequences')
            possible_paths = [seq_file]  # original path (may already be absolute)

            # Frozen builds: bundled sequences live under _MEIPASS or next to
            # the exe — search THOSE FIRST (with shape validation at selection
            # time) so a chain-shaped chains/<name>.json can never win over
            # the real sequence file.
            if is_bare_filename:
                _frozen_seq_candidates = []
                if exe_dir:
                    _frozen_seq_candidates.extend([
                        os.path.join(exe_dir, 'LoOper', 'sequences', seq_file),
                        os.path.join(exe_dir, '_internal', 'LoOper', 'sequences', seq_file),
                        os.path.join(exe_dir, '_internal', 'sequences', seq_file),
                        os.path.join(exe_dir, 'sequences', seq_file),
                    ])
                if _meipass:
                    _frozen_seq_candidates.extend([
                        os.path.join(_meipass, 'LoOper', 'sequences', seq_file),
                        os.path.join(_meipass, 'sequences', seq_file),
                    ])
                possible_paths = _frozen_seq_candidates + possible_paths

            if is_bare_filename:
                possible_paths.extend([
                    os.path.join(base_dir, 'sequences', seq_file),  # sequences next to chain
                    os.path.join(os.getcwd(), 'sequences', seq_file),  # cwd/sequences
                    os.path.join('sequences', seq_file),  # sequences relative to cwd
                    os.path.join(looper_sequences, seq_file),  # LoOper/sequences
                ])
                possible_paths.append(os.path.join(os.getcwd(), seq_file))
                # NOTE: the bare base_dir candidate is intentionally omitted —
                # when base_dir is a chains/ directory it collides with
                # chain-shaped files (chains/CLOSE.json vs the real sequence).
            else:
                possible_paths.extend([
                    os.path.join(base_dir, seq_file),
                    os.path.join(base_dir, 'sequences', seq_file),
                    os.path.join('sequences', seq_file),
                    os.path.join(os.getcwd(), seq_file),
                    os.path.join(os.getcwd(), 'sequences', seq_file),
                    os.path.join(looper_sequences, seq_file),
                ])
            
            # Add .json extension if missing
            if not seq_file.endswith('.json'):
                json_name = seq_file + '.json'
                possible_paths.append(json_name)
                if is_bare_filename:
                    possible_paths.extend([
                        os.path.join(base_dir, 'sequences', json_name),
                        os.path.join(os.getcwd(), 'sequences', json_name),
                        os.path.join('sequences', json_name),
                        os.path.join(looper_sequences, json_name),
                    ])
                    possible_paths.append(os.path.join(os.getcwd(), json_name))
                else:
                    possible_paths.extend([
                        os.path.join(base_dir, json_name),
                        os.path.join(base_dir, 'sequences', json_name),
                        os.path.join('sequences', json_name),
                        os.path.join(os.getcwd(), json_name),
                        os.path.join(os.getcwd(), 'sequences', json_name),
                        os.path.join(looper_sequences, json_name),
                        os.path.join(os.getcwd(), 'LoOper', 'sequences', json_name),
                    ])
                if exe_dir and is_bare_filename:
                    possible_paths.extend([
                        os.path.join(exe_dir, 'LoOper', 'sequences', json_name),
                        os.path.join(exe_dir, 'sequences', json_name),
                    ])
                if _meipass and is_bare_filename:
                    possible_paths.extend([
                        os.path.join(_meipass, 'LoOper', 'sequences', json_name),
                        os.path.join(_meipass, 'sequences', json_name),
                    ])
            
            # Find the first existing file with SEQUENCE SHAPE.  A file that
            # exists but is chain-shaped (e.g. chains/CLOSE.json with no
            # 'actions' list) is never accepted as a sequence — the search
            # continues so the real sequences/CLOSE.json wins.
            seq_file = None
            _skipped_chain_shaped = []
            for path in possible_paths:
                if os.path.exists(path):
                    if _is_sequence_shape(path):
                        seq_file = path
                        logger.info(f"Found sequence file at: {seq_file}")
                        break
                    _skipped_chain_shaped.append(path)
                    logger.warning(
                        f"Ignoring non-sequence file at {path} (chain-shaped JSON)"
                    )
            if not seq_file and _skipped_chain_shaped:
                logger.error(
                    f"Only chain-shaped files found for '{original_seq_file}': "
                    f"{_skipped_chain_shaped}"
                )
            
            if not seq_file:
                logger.error(f"Sequence file not found: '{original_seq_file}'")
                logger.error(f"Searched in paths: {possible_paths}")
                logger.error(f"Current working directory: {os.getcwd()}")
                logger.error(f"Available files in current directory: {os.listdir(os.getcwd()) if os.path.exists(os.getcwd()) else 'N/A'}")
                
                # Check if sequences directory exists
                sequences_dir = os.path.join(os.getcwd(), 'sequences')
                if os.path.exists(sequences_dir):
                    logger.error(f"Available files in sequences directory: {os.listdir(sequences_dir)}")
                else:
                    logger.error("Sequences directory does not exist")
                
                return False
        
        # Handle both old format (loops) and new format (loop_count)
        loops = item.get('loop_count', item.get('loops', 1))
        extra_delay = item.get('extra_delay', 0)
        advanced_conditions = item.get('advanced_conditions', {})
        
        logger.info(f"Executing sequence: {seq_file} (loops: {loops}, extra_delay: {extra_delay})")
        
        # Handle pre-sequence conditions
        if self._handle_pre_sequence_conditions(item, stop_flag, advanced_conditions):
            return True  # Sequence was skipped due to conditions
        
        # Execute the sequence for the specified number of loops
        for i in range(loops):
            if stop_flag and stop_flag():
                logger.info("Sequence execution stopped by user request")
                return False
            
            logger.info(f"Starting loop {i + 1}/{loops} for sequence: {seq_file}")
            
            # NOTE: selection memory is deliberately NOT cleared per iteration -
            # it holds the repeating-element cursor, and each iteration must
            # advance to the NEXT sibling row (like the web entity cursor), which
            # only works while the already-clicked set survives the loop.

            # Load sequence data - try cache first for optimal performance
            try:
                # First try to get from cache
                sequence_data = get_sequence(seq_file, self.chain_file_dir)
                if not sequence_data and self.chain_file_dir:
                    sequence_data = get_sequence(seq_file)
                if sequence_data:
                    logger.info(f"Using cached sequence data for: {seq_file}")
                    # Create a player instance without reloading JSON but ensure base init occurs
                    player = SequencePlayer.__new__(SequencePlayer)
                    try:
                        # Initialize without loading the file to set up base bot state and handlers
                        SequencePlayer.__init__(player, seq_file, lazy_load=True)
                    except Exception as init_err:
                        # Fallback: directly initialize SeleniumBot base to provide action handlers
                        logger.debug(f"SequencePlayer lazy init failed, falling back to base init: {init_err}")
                        SeleniumBot.__init__(player)
                        player.sequence_file = seq_file
                    # Share selection memory and inject cached data
                    player.selection_memory = self.selection_memory
                    player.sequence_data = copy.deepcopy(sequence_data)
                else:
                    # Fallback to traditional loading if not in cache
                    logger.info(f"Loading sequence from disk (not in cache): {seq_file}")
                    player = SequencePlayer(seq_file)
                    # Share selection memory with the loader instance
                    player.selection_memory = self.selection_memory
                    sequence_data = copy.deepcopy(player.sequence_data)
            except Exception as e:
                logger.error(f"Failed to load sequence {seq_file}: {e}")
                return False

            # Per-sequence desktop execution settings from the node dialog
            # (Click Drift): override the player defaults when configured.
            try:
                if 'click_drift_min' in item or 'click_drift_max' in item:
                    player.click_drift_min = max(0.0, float(item.get('click_drift_min', CLICK_JITTER_MIN)))
                    player.click_drift_max = max(0.0, float(item.get('click_drift_max', CLICK_JITTER_MAX)))
            except (TypeError, ValueError):
                pass

            # Defensive routing: a web session (mode=web) attached to a desktop
            # sequence node must never feed DOM-shaped actions to the desktop
            # SequencePlayer - delegate to the web engine instead.  _run_web_replay
            # owns the loop_count loop itself, so run it ONCE and return: looping
            # here too would replay the session loop_count² times.
            if isinstance(sequence_data, dict) and sequence_data.get("mode") == "web":
                logger.info(
                    f"Sequence file {seq_file} is a web session (mode=web); "
                    "routing to the web engine"
                )
                try:
                    loops_for_web = int(item.get('loop_count', item.get('loops', 1)))
                except (TypeError, ValueError):
                    loops_for_web = 1
                try:
                    delay_for_web = float(item.get('extra_delay', 0))
                except (TypeError, ValueError):
                    delay_for_web = 0.0
                try:
                    return self._run_web_replay(
                        item, seq_file, stop_flag, loops_for_web, delay_for_web
                    )
                except Exception as _web_exc:
                    logger.error(f"Web-session routing failed for {seq_file}: {_web_exc}")
                    return False

            use_app_opened = True
            try:
                raw_use_app_opened = item.get("use_app_opened", True)
                if isinstance(raw_use_app_opened, str):
                    raw_use_app_opened = raw_use_app_opened.strip().lower() in ("true", "1", "yes", "y", "on")
                use_app_opened = bool(raw_use_app_opened)
            except Exception:
                use_app_opened = True

            expected_app = None
            try:
                if use_app_opened:
                    expected_app = self._get_expected_app_context_from_sequence(sequence_data)
                    if not expected_app and self.fallback_handler is not None:
                        expected_app = getattr(self.fallback_handler, "last_app_context", None)
                    
                # Apply global overrides (like Sandbox) if present
                if hasattr(self, 'global_app_context_override') and self.global_app_context_override:
                    if not isinstance(expected_app, dict):
                        expected_app = {}
                    expected_app.update(self.global_app_context_override)
                    logger.info(f"Applied global app context override: {self.global_app_context_override}")
            except Exception:
                expected_app = None

            # Sandbox is handled ONCE by begin_chain_sandbox() in play_chain().
            # After that sandbox_agent_url is set in the context. Subsequent calls to
            # _ensure_app_open_and_focused from here must NOT try to re-initialise
            # the RDP session — that would open a second Looper instance in the RDP.
            def _url_reachable(url):
                try:
                    from urllib.parse import urlparse
                    p = urlparse(str(url))
                    host = p.hostname
                    port = p.port or (80 if (p.scheme or "").lower() == "http" else 443)
                    with socket.create_connection((host, int(port)), timeout=0.35):
                        return True
                except Exception:
                    return False

            _already_sandboxed = False
            if expected_app and expected_app.get("sandboxed"):
                _existing_url = (expected_app.get("sandbox_agent_url") or
                                 expected_app.get("rdp_agent_url"))
                if _existing_url and _url_reachable(_existing_url):
                    _already_sandboxed = True
                if not _already_sandboxed:
                    try:
                        _g = getattr(self, 'global_app_context_override', None)
                        if isinstance(_g, dict):
                            _gu = _g.get("sandbox_agent_url") or _g.get("rdp_agent_url")
                            if _gu and _url_reachable(_gu):
                                _already_sandboxed = True
                    except Exception:
                        pass

            if expected_app and expected_app.get("sandboxed") and not _already_sandboxed:
                # Only call _ensure_app_open_and_focused if the agent is not yet reachable.
                # begin_chain_sandbox() handles the primary setup; this covers the case
                # where play_chain() was bypassed and sandbox is set directly.
                try:
                    ok = self._ensure_app_open_and_focused(expected_app, stop_flag, attempts=int(expected_app.get("focus_attempts", 3)))
                except Exception:
                    ok = True
                if not ok:
                    logger.error("Sandbox/RDP App could not be launched; aborting sequence execution")
                    return False
            elif expected_app is None and getattr(self, 'global_app_context_override', {}).get("sandboxed"):
                _gctx = getattr(self, 'global_app_context_override', {})
                _gu2 = _gctx.get("sandbox_agent_url") or _gctx.get("rdp_agent_url")
                if not _gu2 or not _url_reachable(_gu2):
                    # Primary sandbox setup not yet done (e.g., direct call without play_chain).
                    try:
                        ok = self._ensure_app_open_and_focused(_gctx, stop_flag, attempts=3)
                    except Exception:
                        ok = True
                    if not ok:
                        logger.error("Sandbox/RDP App could not be launched via global override; aborting sequence execution")
                        return False
            elif isinstance(expected_app, dict) and any(expected_app.get(k) for k in ("exe", "process_name", "title", "pid")):
                try:
                    ok = self._verify_app_running(expected_app, timeout=float(expected_app.get("focus_timeout", 2.0)))
                except Exception:
                    ok = True
                if not ok:
                    logger.error("Expected app is not running; aborting sequence execution")
                    return False
            
            if expected_app:
                try:
                    if self.fallback_handler is not None:
                        self.fallback_handler.last_app_context = expected_app
                except Exception:
                    pass
        
            # 1. Chain-level fallback sequences (applied to all click actions)
            if 'fallback_sequences' in item:
                fallback_sequences = item['fallback_sequences']
                for action in sequence_data['actions']:
                    if action.get('type') in ['click', 'double_click', 'ctrl_click', 'shift_click'] and action.get('screenshot'):
                        action['fallback_sequences'] = fallback_sequences
                        logger.info(f"Injected chain-level fallback sequences into click action: {fallback_sequences}")
        
            # 2. Action-specific fallbacks from UI configuration
            if 'action_fallbacks' in item:
                action_fallbacks = item['action_fallbacks']
                for action_idx, fallback_config in action_fallbacks.items():
                    try:
                        action_idx_int = int(action_idx)
                        if action_idx_int < len(sequence_data['actions']):
                            action = sequence_data['actions'][action_idx_int]
                            if action.get('type') in ['click', 'double_click', 'ctrl_click', 'shift_click'] and action.get('screenshot'):
                                action['fallback_sequence'] = fallback_config['sequence_file']
                                action['fallback_retry_attempts'] = fallback_config.get('retry_attempts', 3)
                                action['fallback_retry_delay'] = fallback_config.get('retry_delay', 0.3)
                                logger.info(f"Injected action-specific fallback sequence {fallback_config['sequence_file']} into action {action_idx_int}")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Invalid action index '{action_idx}' in action_fallbacks: {e}")
                        continue
        
            # Create fallback callback that handles sequences and aborts current sequence
            def fallback_callback(fallback_seq_name, action_idx):
                if stop_flag and stop_flag():
                    logger.info("Fallback execution stopped by user request")
                    return
                
                logger.info(f"Visual matching failed for action {action_idx}. Aborting current sequence and executing fallback: {fallback_seq_name}")
            
                try:
                    fallback_player = SequencePlayer(fallback_seq_name)
                    # Share selection memory with fallback player
                    fallback_player.selection_memory = self.selection_memory
                    # Same click drift as the sequence that triggered the fallback
                    if hasattr(player, 'click_drift_min'):
                        fallback_player.click_drift_min = player.click_drift_min
                    if hasattr(player, 'click_drift_max'):
                        fallback_player.click_drift_max = player.click_drift_max
                    try:
                        # A sandboxed run must replay the fallback inside the
                        # RDP session too, or it would act on the host desktop.
                        _fb_url = getattr(player, 'sandbox_agent_url', None)
                        if _fb_url:
                            fallback_player.sandbox_agent_url = _fb_url
                            fallback_player.bot.sandbox_agent_url = _fb_url
                            if hasattr(fallback_player, 'action_handlers') and fallback_player.action_handlers:
                                fallback_player.action_handlers.bot.sandbox_agent_url = _fb_url
                            from ..computer_vision import set_sandbox_agent_url
                            set_sandbox_agent_url(_fb_url)
                    except Exception:
                        pass
                    # Skip initial delay for faster fallback execution
                    fallback_player.play_sequence(stop_flag=stop_flag, skip_initial_delay=True)
                    logger.info(f"Fallback sequence completed. Current sequence was aborted.")
                
                except Exception as e:
                    logger.error(f"Fallback sequence failed: {e}")
        
            # Play the sequence with fallback support
            try:
                # We do not want to reuse a cached SequencePlayer if we need to inject the sandbox URL dynamically
                # because the base class initialization might have happened before we knew the sandbox URL
                # NOTE: Actually we SHOULD use the player instance we created/configured above (lines 723-739)
                # because it's already properly initialized with the sequence data and selection memory!
                # We just need to update its bot configurations.
                
                # Pass sandbox/RDP agent URL to bot and action handlers if available
                # Note: expected_app might be None if using global overrides, so check both
                app_ctx = expected_app or getattr(self, 'global_app_context_override', {})
                if app_ctx:
                    if app_ctx.get("sandbox_agent_url"):
                        player.sandbox_agent_url = app_ctx.get("sandbox_agent_url")
                        player.bot = player # player is already a SeleniumBot instance
                        player.bot.sandbox_agent_url = app_ctx.get("sandbox_agent_url")
                        if hasattr(player, 'action_handlers'):
                            player.action_handlers.bot.sandbox_agent_url = app_ctx.get("sandbox_agent_url")
                        # Keep the computer-vision channel in sync so condition/
                        # OCR evaluation observes the same desktop the actions drive.
                        from ..computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(app_ctx.get("sandbox_agent_url"))
                    elif app_ctx.get("rdp_agent_url"):
                        player.sandbox_agent_url = app_ctx.get("rdp_agent_url") # Map to the same variable for ActionHandlers
                        player.bot = player
                        player.bot.sandbox_agent_url = app_ctx.get("rdp_agent_url")
                        if hasattr(player, 'action_handlers'):
                            player.action_handlers.bot.sandbox_agent_url = app_ctx.get("rdp_agent_url")
                        from ..computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(app_ctx.get("rdp_agent_url"))
                    
                player.play_sequence(fallback_callback=fallback_callback, stop_flag=stop_flag)
                try:
                    if self.fallback_handler is not None:
                        lac = getattr(player, "last_app_context", None)
                        if lac:
                            self.fallback_handler.last_app_context = lac
                except Exception:
                    pass
            except Exception as e:
                logger.error(f"Error playing sequence {seq_file}: {e}")
                return False
        
            # Add extra delay between loops if specified
            if i < loops - 1 and extra_delay > 0:
                logger.info(f"Extra delay between loops: {extra_delay} seconds")
                delay_remaining = extra_delay
                while delay_remaining > 0:
                    if stop_flag and stop_flag():
                        logger.info("Sequence execution stopped during delay")
                        return False
                    sleep_time = min(0.1, delay_remaining)
                    time.sleep(sleep_time)
                    delay_remaining -= sleep_time
    
        # Handle post-sequence conditions
        self._handle_post_sequence_conditions(item, stop_flag, advanced_conditions)
        
        return True

    def _handle_pre_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle conditional checks before executing a sequence
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        
        Returns:
            bool: True if sequence should be skipped
        """
        if not self.fallback_handler:
            return False
            
        # Capture a single screenshot for all trigger checks to improve performance
        screen_cv = None
        presence_triggers = advanced_conditions.get('presence_triggers', [])
        absence_triggers = advanced_conditions.get('absence_triggers', [])
        
        # Only capture screenshot if we have triggers to check
        if presence_triggers or absence_triggers:
            screen_cv = self.fallback_handler.template_matcher.capture_screen()
            
        # Check for presence triggers that must be met before execution
        for trigger in presence_triggers:
            if stop_flag and stop_flag():
                return True
                
            if not self.fallback_handler.check_presence_trigger(trigger, screen_cv=screen_cv):
                logger.info(f"Presence trigger not met, skipping sequence: {trigger.get('image_path')}")
                return True
        
        # Check for absence triggers that must be met before execution
        for trigger in absence_triggers:
            if stop_flag and stop_flag():
                return True
                
            timeout = trigger.get('timeout', 10)
            if not self.fallback_handler.check_absence_trigger(trigger, timeout, screen_cv=screen_cv):
                logger.info(f"Absence trigger not met, skipping sequence: {trigger.get('image_path')}")
                return True
        
        # Handle conditional loops that should execute before the main sequence
        pre_conditional_loops = advanced_conditions.get('pre_conditional_loops', [])
        for loop_config in pre_conditional_loops:
            if stop_flag and stop_flag():
                return True
                
            logger.info("Executing pre-sequence conditional loop")
            self.fallback_handler.execute_conditional_loop(loop_config, None)
        
        return False
    
    def _handle_post_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle conditional actions after executing a sequence
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        """
        if not self.fallback_handler:
            return
            
        # Handle conditional loops that should execute after the main sequence
        post_conditional_loops = advanced_conditions.get('post_conditional_loops', [])
        for loop_config in post_conditional_loops:
            if stop_flag and stop_flag():
                return
                
            logger.info("Executing post-sequence conditional loop")
            self.fallback_handler.execute_conditional_loop(loop_config, None)
        
        # Handle wait conditions (wait for presence/absence of elements or OCR text)
        wait_conditions = advanced_conditions.get('wait_conditions', [])
        for condition in wait_conditions:
            if stop_flag and stop_flag():
                return
                
            condition_type = condition.get('type', 'presence')
            timeout = float(condition.get('timeout', 30))
            
            logger.info(f"Waiting for {condition_type} condition: {condition.get('image_path') or condition.get('target_text')}")
            
            if condition_type == 'presence':
                start_time = time.time()
                while time.time() - start_time < timeout:
                    if stop_flag and stop_flag():
                        return
                    # Capture fresh screenshot for each polling iteration in wait conditions
                    screen_cv = self.fallback_handler.template_matcher.capture_screen()
                    if self.fallback_handler.check_presence_trigger(condition, screen_cv=screen_cv):
                        logger.info("Presence wait condition satisfied")
                        break
                    time.sleep(0.02)  # Reduced from 0.1
            elif condition_type == 'absence':
                # For absence checks, let the method handle its own polling with shared screenshots
                self.fallback_handler.check_absence_trigger(condition, timeout)
            elif condition_type == 'ocr':
                start_time = time.time()
                while time.time() - start_time < timeout:
                    if stop_flag and stop_flag():
                        return
                    if self.fallback_handler.check_ocr_trigger(condition, stop_flag=stop_flag):
                        logger.info("OCR wait condition satisfied")
                        break
                    time.sleep(0.02)  # Reduced from 0.1
            elif condition_type == 'wait_time':
                wait_time = float(condition.get('wait_time', 5))
                logger.info(f"Waiting for {wait_time} seconds")
                time.sleep(wait_time)
