import base64
import ctypes
import io
import os
import random
import re
import socket
import subprocess
import sys
import time
import unittest

import requests


AGENT_USER = "LoOperAgent"
AGENT_PASSWORD = "LoOperPassword123!"
TASK_NAME = "LoOperAgentStartAgentTest"
SHARED_DIR = r"C:\TempShared"
DEBUG_PATH = os.path.join(SHARED_DIR, "rdp_click_test_debug.txt")
TEST_VERSION = "rdp_click_test_v4_schtasks_ps1_2026-03-21"


def _append_debug(text: str) -> None:
    try:
        os.makedirs(SHARED_DIR, exist_ok=True)
        with open(DEBUG_PATH, "a", encoding="utf-8") as f:
            f.write(str(text or ""))
            if not str(text or "").endswith("\n"):
                f.write("\n")
    except Exception:
        return


print(f"TEST_VERSION={TEST_VERSION}")
_append_debug(f"TEST_VERSION={TEST_VERSION}")


def _run_capture(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except Exception as e:
        _append_debug(f"RUN ERROR: {cmd} -> {e}")
        raise


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _logoff_existing_agent_session() -> None:
    try:
        q = _run_capture(["query", "session"])
        out = (q.stdout or "") + "\n" + (q.stderr or "")
        _append_debug("=== query session (before logoff) ===")
        _append_debug(out)
        for line in out.splitlines():
            if "LoOperAgent" not in line:
                continue
            m = re.search(r"\s(\d+)\s+(Active|Disc|Conn|Listen)\b", line)
            if not m:
                continue
            try:
                session_id = int(m.group(1))
            except Exception:
                continue
            lr = _run_capture(["logoff", str(session_id)])
            _append_debug(f"=== logoff {session_id} rc={lr.returncode} ===")
            _append_debug((lr.stdout or "") + "\n" + (lr.stderr or ""))
            time.sleep(1.0)
            break
    except Exception:
        return


def _get_looperagent_session() -> tuple[int | None, str | None]:
    try:
        q = _run_capture(["query", "session"])
        out = (q.stdout or "") + "\n" + (q.stderr or "")
        for line in out.splitlines():
            if AGENT_USER not in line:
                continue
            m = re.search(r"\s(\d+)\s+(Active|Disc|Conn|Listen)\b", line)
            if not m:
                continue
            return int(m.group(1)), str(m.group(2))
    except Exception:
        return None, None
    return None, None


def _ensure_looperagent_session_active(timeout_s: float = 20.0) -> None:
    sid, state = _get_looperagent_session()
    _append_debug(f"=== ensure session active: id={sid} state={state} ===")
    if sid is None:
        return
    if state == "Active":
        return

    try:
        subprocess.Popen(
            ["mstsc.exe", f"/shadow:{sid}", "/control", "/noConsentPrompt", "/v:127.0.0.2"]
        )
    except Exception as e:
        _append_debug(f"=== shadow launch failed: {e} ===")
        return

    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        sid2, state2 = _get_looperagent_session()
        if sid2 == sid and state2 == "Active":
            _append_debug("=== session became Active ===")
            return
        time.sleep(0.5)


def _pick_agent_python(project_root: str) -> str:
    python_exe = ""
    candidates = [
        os.path.join(project_root, ".venv", "Scripts", "python.exe"),
        os.path.join(project_root, ".venv", "scripts", "python.exe"),
        sys.executable,
    ]
    for c in candidates:
        try:
            if c and os.path.exists(c):
                python_exe = c
                break
        except Exception:
            continue
    if not python_exe:
        python_exe = "python"
    return python_exe


def _delete_task() -> None:
    r = _run_capture(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    _append_debug(f"=== schtasks delete rc={r.returncode} ===")
    _append_debug((r.stdout or "") + "\n" + (r.stderr or ""))


def _write_task_ps1(python_exe: str, agent_script: str, port: int) -> str:
    ps1_path = os.path.join(SHARED_DIR, "start_agent_task.ps1")
    log_path = os.path.join(SHARED_DIR, "agent_log.txt")
    task_error_path = os.path.join(SHARED_DIR, "agent_task_error.txt")
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    ps1 = f"""
$ErrorActionPreference = 'Stop'
$env:PYTHONPATH = '{project_root}'
$env:LOOPER_AGENT_PORT = '{int(port)}'
try {{
  & '{python_exe}' '{agent_script}' --port {int(port)} *>> '{log_path}'
}} catch {{
  ($_ | Out-String) | Out-File -FilePath '{task_error_path}' -Encoding utf8
  throw
}}
"""
    os.makedirs(SHARED_DIR, exist_ok=True)
    with open(ps1_path, "w", encoding="utf-8") as f:
        f.write(ps1.strip() + "\n")
    _append_debug(f"=== wrote task ps1: {ps1_path} ===")
    return ps1_path


def _create_task(python_exe: str, agent_script: str, port: int) -> None:
    ps1_path = _write_task_ps1(python_exe, agent_script, port)
    tr = f"powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"{ps1_path}\""

    r = _run_capture(
        [
            "schtasks",
            "/create",
            "/tn",
            TASK_NAME,
            "/sc",
            "ONLOGON",
            "/ru",
            AGENT_USER,
            "/rp",
            AGENT_PASSWORD,
            "/rl",
            "HIGHEST",
            "/it",
            "/tr",
            tr,
            "/f",
        ],
    )
    _append_debug(f"=== schtasks create rc={r.returncode} ===")
    _append_debug((r.stdout or "") + "\n" + (r.stderr or ""))
    if r.returncode != 0:
        raise RuntimeError(f"Failed to create scheduled task. stdout={r.stdout} stderr={r.stderr}")

    qi = _run_capture(["schtasks", "/query", "/tn", TASK_NAME, "/v", "/fo", "LIST"])
    _append_debug(f"=== schtasks query rc={qi.returncode} ===")
    _append_debug((qi.stdout or "") + "\n" + (qi.stderr or ""))


def _get_screen_resolution():
    """Get current screen resolution dynamically."""
    try:
        import ctypes
        w = int(ctypes.windll.user32.GetSystemMetrics(0))
        h = int(ctypes.windll.user32.GetSystemMetrics(1))
        return w, h
    except Exception:
        return 1920, 1080


def _write_rdp_file(shared_dir: str) -> str:
    rdp_file = os.path.join(shared_dir, "agent_session_test.rdp")
    desktop_w, desktop_h = _get_screen_resolution()
    with open(rdp_file, "w", encoding="utf-8") as f:
        f.write(
            f"""screen mode id:i:1
use multimon:i:0
desktopwidth:i:{desktop_w}
desktopheight:i:{desktop_h}
smart sizing:i:1
session bpp:i:32
full address:s:127.0.0.2
username:s:{AGENT_USER}
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
"""
        )
    return rdp_file


def _launch_rdp_session(rdp_file: str) -> None:
    subprocess.Popen(["mstsc.exe", rdp_file])


def _wait_for_agent(agent_url: str, timeout_s: float) -> dict:
    deadline = time.time() + float(timeout_s)
    last_status = None
    last_body = None
    last_exc = None
    while time.time() < deadline:
        try:
            resp = requests.post(f"{agent_url}/action", json={"action": "screenshot"}, timeout=5)
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.json()
            try:
                last_body = resp.text
            except Exception:
                last_body = None
        except Exception as e:
            last_exc = e
        time.sleep(0.5)
    raise RuntimeError(f"Agent not reachable at {agent_url}. last_status={last_status} last_body={last_body} last_exc={last_exc}")


def _decode_png_size(b64_png: str) -> tuple[int, int] | None:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        raw = base64.b64decode(b64_png)
        img = Image.open(io.BytesIO(raw))
        return int(img.size[0]), int(img.size[1])
    except Exception:
        return None


class TestRDPSessionClicks(unittest.TestCase):
    agent_url = None
    screen_width, screen_height = _get_screen_resolution()

    @classmethod
    def setUpClass(cls):
        try:
            if os.path.exists(DEBUG_PATH):
                os.remove(DEBUG_PATH)
        except Exception:
            pass

        _delete_task()
        _logoff_existing_agent_session()

        shared_dir = SHARED_DIR
        os.makedirs(SHARED_DIR, exist_ok=True)
        for name in (
            "agent_info.json",
            "agent_error.txt",
            "agent_log.txt",
            "agent_status.txt",
            "rdp_startup_test_ran.txt",
            "rdp_startup_test_python.txt",
            "agent_task_error.txt",
        ):
            p = os.path.join(shared_dir, name)
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        python_exe = _pick_agent_python(project_root)
        agent_script = os.path.join(project_root, "LoOper", "sandbox_agent.py")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = int(s.getsockname()[1])

        cls.agent_url = f"http://127.0.0.1:{port}"
        _create_task(python_exe, agent_script, port)

        cr = _run_capture(
            ["cmdkey", "/generic:TERMSRV/127.0.0.2", "/user:" + AGENT_USER, "/pass:" + AGENT_PASSWORD],
        )
        _append_debug(f"=== cmdkey rc={cr.returncode} ===")
        _append_debug((cr.stdout or "") + "\n" + (cr.stderr or ""))

        rdp_file = _write_rdp_file(shared_dir)
        _launch_rdp_session(rdp_file)

        try:
            payload = _wait_for_agent(cls.agent_url, timeout_s=60)
        except Exception as e:
            _append_debug("=== query session (after mstsc launch) ===")
            qs = _run_capture(["query", "session"])
            _append_debug((qs.stdout or "") + "\n" + (qs.stderr or ""))
            raise RuntimeError(f"{e}. Debug log at {DEBUG_PATH}") from e

        if isinstance(payload, dict) and payload.get("status") == "error" and "screen grab failed" in str(payload.get("message", "")).lower():
            _append_debug("=== screenshot failed; attempting to shadow session ===")
            _ensure_looperagent_session_active(timeout_s=25.0)
            try:
                payload = requests.post(
                    f"{cls.agent_url}/action",
                    json={"action": "screenshot"},
                    timeout=10,
                ).json()
            except Exception as e:
                _append_debug(f"=== retry screenshot failed: {e} ===")
        size = None
        if isinstance(payload, dict):
            img_b64 = payload.get("image")
            if isinstance(img_b64, str) and img_b64:
                size = _decode_png_size(img_b64)
        if size:
            cls.screen_width, cls.screen_height = size

    @classmethod
    def tearDownClass(cls):
        _delete_task()

    def test_four_random_clicks_in_remote_session(self):
        margin = 80
        x_min, x_max = margin, max(margin, int(self.screen_width) - margin)
        y_min, y_max = margin, max(margin, int(self.screen_height) - margin)

        for _ in range(4):
            x = random.randint(x_min, x_max)
            y = random.randint(y_min, y_max)
            resp = requests.post(
                f"{self.agent_url}/action",
                json={"action": "click", "x": x, "y": y, "button": "left"},
                timeout=10,
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body.get("status"), "success", msg=str(body))
            time.sleep(0.5)


if __name__ == "__main__":
    if not _is_admin():
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f"\"{os.path.abspath(__file__)}\"", None, 1
            )
        except Exception:
            pass
        raise SystemExit(0)
    unittest.main()
