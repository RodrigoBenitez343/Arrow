import base64
import contextlib
import io
import json
import os
import socket
import sys
import threading
import time
import traceback

import pyautogui
from http.server import BaseHTTPRequestHandler, HTTPServer

# Disable failsafe since the agent might move the mouse to the corner
pyautogui.FAILSAFE = False

# Canonical key names (see player/key_defs.py) -> pyautogui key names.
# 'cmd' is the macOS-style canonical name; on Windows the equivalent is 'win'.
# 'alt_gr' is not a pyautogui name, approximate with 'alt' (best effort).
_AGENT_KEY_MAP = {
    "cmd": "win",
    "alt_gr": "alt",
}

# Keys currently held via key_down (so release_all_keys / key_up can unwind them).
_AGENT_HELD_KEYS = set()


def _agent_key_name(name):
    if not name:
        return None
    s = str(name)
    return _AGENT_KEY_MAP.get(s, s)


def _set_clipboard_text(text):
    """Set the clipboard to *text* (Unicode-safe) using whatever works."""
    text = "" if text is None else str(text)
    try:
        import pyperclip
        pyperclip.copy(text)
        return
    except Exception:
        pass
    try:
        import ctypes
        u32 = ctypes.windll.user32
        CF_UNICODETEXT = 13
        u32.OpenClipboard(None)
        try:
            u32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)
            h = ctypes.windll.kernel32.GlobalAlloc(0x0042, ctypes.sizeof(buf))
            if h:
                locked = ctypes.windll.kernel32.GlobalLock(h)
                if locked:
                    ctypes.memmove(locked, buf, ctypes.sizeof(buf))
                    ctypes.windll.kernel32.GlobalUnlock(h)
                # On success the system owns the handle; do NOT free it.
                u32.SetClipboardData(CF_UNICODETEXT, h)
        finally:
            u32.CloseClipboard()
        return
    except Exception:
        pass
    try:
        import subprocess
        p = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::InputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8; Set-Clipboard -Value $input"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.communicate(text.encode("utf-8"), timeout=10)
    except Exception:
        pass


def _press_modifiers(mods):
    """Press canonical modifier names; returns the keys actually pressed."""
    pressed = []
    for m in mods or []:
        k = _agent_key_name(m)
        if not k:
            continue
        try:
            pyautogui.keyDown(k)
            pressed.append(k)
        except Exception:
            pass
    return pressed


def _release_keys(keys):
    for k in reversed(keys or []):
        try:
            pyautogui.keyUp(k)
        except Exception:
            pass


def _click_at(x, y, button, jitter=True):
    """Click with a human-like press-hold interval (mirrors base_bot.human_click)."""
    if jitter and x is not None and y is not None:
        try:
            import random
            pyautogui.moveRel(random.uniform(-1.5, 1.5), random.uniform(-1.5, 1.5),
                              duration=random.uniform(0.001, 0.01))
        except Exception:
            pass
    pyautogui.mouseDown(button=button)
    try:
        import random
        time.sleep(random.uniform(0.035, 0.12))
    except Exception:
        time.sleep(0.05)
    pyautogui.mouseUp(button=button)


def _jsonable(value):
    """Best-effort JSON round-trip; fall back to repr for opaque objects."""
    if value is None:
        return None
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)


def _process_session_id():
    """Return the Windows session ID of this agent process (diagnostics).

    Lets the host verify the agent really lives in the RDP session rather
    than the user's console session — actions only land where this process
    lives.
    """
    try:
        import ctypes
        sid = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
            return int(sid.value)
    except Exception:
        pass
    return None


def _run_code(code, timeout_s, input_data, args, output_variable="result"):
    """Execute *code* in the agent (RDP session) process.

    Runs in a worker thread so the HTTP server stays responsive while
    long-running / GUI scripts keep executing inside the session.
    Returns a dict with ok / stdout / stderr / result / error / timed_out.
    """
    out = {"ok": False, "stdout": "", "stderr": "", "result": None, "error": "", "timed_out": False}

    def _worker():
        scope = {}
        stdout_c = io.StringIO()
        stderr_c = io.StringIO()
        try:
            scope["input_data"] = input_data
            scope["args"] = args or {}
            scope["os"] = os
            scope["print"] = print
            scope["__name__"] = "__main__"
            with contextlib.redirect_stdout(stdout_c), contextlib.redirect_stderr(stderr_c):
                exec(compile(code, "<sandbox_code>", "exec"), scope, scope)
            out["ok"] = True
        except Exception:
            out["ok"] = False
            out["error"] = traceback.format_exc()
        finally:
            out["stdout"] = stdout_c.getvalue()
            out["stderr"] = stderr_c.getvalue()
            out["result"] = _jsonable(scope.get(output_variable))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=max(1.0, float(timeout_s or 30)))
    if t.is_alive():
        out["ok"] = True
        out["timed_out"] = True
        out["stdout"] = (out.get("stdout") or "") + (
            f"\n[sandbox] code still running after {int(timeout_s or 30)}s — "
            "kept alive in the RDP session")
    return out


class AgentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Always return 200 for action to ensure the client doesn't get 404s
        if not self.path.startswith('/action') and self.path != '/':
            # Just log it but try to process it anyway to be ultra-forgiving
            print(f"Warning: Received POST to unexpected path: {self.path}")

        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            content_length = 0
        if content_length <= 0 or content_length > 16 * 1024 * 1024:
            self.send_response(400)
            self.end_headers()
            return

        post_data = self.rfile.read(content_length)

        try:
            req = json.loads(post_data.decode('utf-8'))
            action = req.get('action')
            result = {"status": "success", "session_id": _process_session_id()}

            if action == 'click':
                x, y = req.get('x'), req.get('y')
                button = req.get('button', 'left')
                duration = req.get('duration', 0.0)
                points = req.get('points')
                if x is not None and y is not None:
                    if points:
                        for px, py in points:
                            pyautogui.moveTo(float(px), float(py), duration=0.001)
                        pyautogui.moveTo(float(x), float(y), duration=0.001)
                    else:
                        pyautogui.moveTo(x, y, duration=duration)
                    pressed = _press_modifiers(req.get('modifiers'))
                    try:
                        _click_at(x, y, button)
                    finally:
                        _release_keys(pressed)
                else:
                    pressed = _press_modifiers(req.get('modifiers'))
                    try:
                        _click_at(None, None, button, jitter=False)
                    finally:
                        _release_keys(pressed)

            elif action == 'type':
                text = req.get('text')
                interval = req.get('interval', 0.001)
                if text:
                    pyautogui.write(text, interval=interval)

            elif action == 'clipboard_paste':
                _set_clipboard_text(req.get('text'))
                time.sleep(0.05)
                pyautogui.hotkey('ctrl', 'v')

            elif action == 'hotkey':
                keys = [_agent_key_name(k) for k in req.get('keys', [])]
                keys = [k for k in keys if k]
                pressed = []
                if keys:
                    try:
                        for k in keys:
                            pyautogui.keyDown(k)
                            pressed.append(k)
                        for k in reversed(keys):
                            pyautogui.keyUp(k)
                    except Exception:
                        # Never leave a modifier stuck down on a failed chord
                        _release_keys(pressed)
                        raise

            elif action == 'key_down':
                k = _agent_key_name(req.get('key'))
                if k:
                    pyautogui.keyDown(k)
                    _AGENT_HELD_KEYS.add(k)

            elif action == 'key_up':
                k = _agent_key_name(req.get('key'))
                if k:
                    pyautogui.keyUp(k)
                    _AGENT_HELD_KEYS.discard(k)

            elif action == 'release_all_keys':
                for k in list(_AGENT_HELD_KEYS):
                    try:
                        pyautogui.keyUp(k)
                    except Exception:
                        pass
                _AGENT_HELD_KEYS.clear()

            elif action == 'get_position':
                px, py = pyautogui.position()
                result["x"] = int(px)
                result["y"] = int(py)
                try:
                    sw, sh = pyautogui.size()
                    result["screen_width"] = int(sw)
                    result["screen_height"] = int(sh)
                except Exception:
                    pass

            elif action == 'screenshot':
                import uuid
                img = pyautogui.screenshot()
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                # Adding a timestamp/UUID prevents aggressive caching proxies or
                # OS-level network stack caching from returning stale responses
                result["image"] = img_str
                result["timestamp"] = time.time()
                result["id"] = str(uuid.uuid4())
                try:
                    sw, sh = pyautogui.size()
                    result["width"] = int(img.width)
                    result["height"] = int(img.height)
                    result["screen_width"] = int(sw)
                    result["screen_height"] = int(sh)
                except Exception:
                    pass

            elif action == 'move':
                x, y = req.get('x'), req.get('y')
                duration = req.get('duration', 0.2)
                points = req.get('points')
                if x is not None and y is not None:
                    if points:
                        for px, py in points:
                            pyautogui.moveTo(float(px), float(py), duration=0.001)
                        pyautogui.moveTo(float(x), float(y), duration=0.001)
                    else:
                        pyautogui.moveTo(x, y, duration=duration)

            elif action == 'scroll':
                amount = req.get('amount', 0)
                if amount != 0:
                    pyautogui.scroll(amount)

            elif action == 'drag_start':
                x, y = req.get('x'), req.get('y')
                duration = req.get('duration', 0.0)
                pyautogui.moveTo(x, y, duration=duration)
                pyautogui.mouseDown()

            elif action == 'drag_end':
                x, y = req.get('x'), req.get('y')
                duration = req.get('duration', 0.0)
                pyautogui.moveTo(x, y, duration=duration)
                pyautogui.mouseUp()

            elif action == 'drag_drop':
                start_x, start_y = req.get('start_x'), req.get('start_y')
                end_x, end_y = req.get('end_x'), req.get('end_y')
                duration = req.get('duration', 0.2)
                pyautogui.moveTo(start_x, start_y, duration=duration)
                pyautogui.mouseDown()
                pyautogui.moveTo(end_x, end_y, duration=duration)
                pyautogui.mouseUp()

            elif action == 'exec_code':
                result.update(_run_code(
                    req.get('code') or '',
                    float(req.get('timeout', 30) or 30),
                    req.get('input_data'),
                    req.get('args') or {},
                    req.get('output_variable') or 'result',
                ))
                try:
                    sw, sh = pyautogui.size()
                    result["screen_width"] = int(sw)
                    result["screen_height"] = int(sh)
                except Exception:
                    pass

            else:
                result = {"status": "error", "message": f"Unknown action: {action}"}

        except Exception as e:
            result = {"status": "error", "message": str(e)}

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode('utf-8'))

    def log_message(self, format, *args):
        # Suppress default HTTP logging to keep stdout clean
        pass


def run():
    # Write a quick status file to show we're starting
    shared_dir = "C:\\TempShared"
    if not os.path.exists(shared_dir):
        os.makedirs(shared_dir, exist_ok=True)
    with open(os.path.join(shared_dir, "agent_status.txt"), "w") as f:
        f.write("starting")

    try:
        hostname = socket.gethostname()
        ip_address = socket.gethostbyname(hostname)
    except Exception as e:
        with open(os.path.join(shared_dir, "agent_error.txt"), "w") as f:
            f.write(str(e))
        ip_address = "127.0.0.1"

    port = None
    try:
        env_port = os.environ.get("LOOPER_AGENT_PORT")
        if env_port:
            port = int(env_port)
    except Exception:
        port = None

    if port is None:
        try:
            if "--port" in sys.argv:
                idx = sys.argv.index("--port")
                if idx + 1 < len(sys.argv):
                    port = int(sys.argv[idx + 1])
        except Exception:
            port = None

    if port is None:
        port = 8000

    info_path = os.path.join(shared_dir, "agent_info.json")
    with open(info_path, "w") as f:
        json.dump({"ip": ip_address, "port": port}, f)

    print(f"Agent listening on 127.0.0.1:{port}")
    try:
        # Bind loopback only: the client always connects via 127.0.0.x on the
        # same host, and the agent drives an interactive high-privilege desktop
        # — exposing it on 0.0.0.0 would let any LAN host inject input.
        server_address = ('127.0.0.1', port)
        httpd = HTTPServer(server_address, AgentHandler)
        httpd.serve_forever()
    except Exception as e:
        with open(os.path.join(shared_dir, "agent_error.txt"), "w") as f:
            f.write(str(e))


if __name__ == '__main__':
    run()
