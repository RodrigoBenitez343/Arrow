"""End-to-end tests for the Android phone client (Holepunch/Pear stack).

These drive a real emulator: the APK, the Bare worklet, the hyperdht sidecar and
the bundled chat page in a WebView.  Nothing is mocked except the LoOper host:
``_stub_host`` answers ``/ws`` with the few frames the UI reacts to.

Opt-in only, so the normal suite stays fast and hermetic::

    $env:LOOPER_PHONE_E2E = "1"
    .venv\\Scripts\\python.exe -m pytest LoOper\\tests\\test_phone_client_e2e.py -v

Prerequisites (the module skips with instructions when one is missing):
  * an emulator is running            emulator -avd <name> -gpu off
  * the debug APK is built            .\\build_pipeline\\build_phone_apk.ps1
  * Node + LoOper/agent_sidecar/node_modules
  * port 8081 is free (no real LoOper host running)
"""

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from aiohttp import WSMsgType, web

E2E_ENV = "LOOPER_PHONE_E2E"
PKG = "to.looper.phone"
ACTIVITY = f"{PKG}/.MainActivity"

_REPO = Path(__file__).resolve().parents[2]
APK = _REPO / "LoOper" / "phone_client" / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
SIDECAR = _REPO / "LoOper" / "agent_sidecar" / "looper-agent-sidecar.js"
SIDECAR_DEPS = SIDECAR.parent / "node_modules"

PING_EVERY = 3.0

pytestmark = pytest.mark.skipif(
    os.environ.get(E2E_ENV) != "1",
    reason=f"phone emulator e2e: set {E2E_ENV}=1 to run",
)


# ---------------------------------------------------------------------------
# Stub host — the smallest aiohttp surface the phone talks to
# ---------------------------------------------------------------------------

_PEERS = []
_HELD_FRAMES = []
_PING = 0


def _dbg(msg):
    print(f"[phone-e2e] {msg}", file=sys.stderr, flush=True)


async def _ws_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    _PEERS.append(ws)
    _dbg("ws connected")
    await ws.send_str(json.dumps({"kind": "output", "mode": "text", "content": "Connected."}))
    asyncio.ensure_future(_ask_later(ws))
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                _HELD_FRAMES.append(msg.data)
    finally:
        if ws in _PEERS:
            _PEERS.remove(ws)
        _dbg("ws closed")
    return ws


async def _ask_later(ws):
    """Every fresh connection gets one ask — the frame that used to open the mic."""
    await asyncio.sleep(1.0)
    if not ws.closed:
        await ws.send_str(json.dumps({
            "type": "ask", "question": "Proceed with the plan?", "kind": "yes_no",
        }))


async def _pusher(app):
    """One output frame every PING_EVERY seconds: proof of delivery in background."""
    global _PING
    while True:
        await asyncio.sleep(PING_EVERY)
        _PING += 1
        payload = json.dumps({"kind": "output", "mode": "text", "content": f"Ping {_PING}"})
        for ws in list(_PEERS):
            if not ws.closed:
                await ws.send_str(payload)


async def _start_pusher(app):
    asyncio.ensure_future(_pusher(app))


def _run_stub_host():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = web.Application()
    app.router.add_get("/ws", _ws_handler)
    app.router.add_post("/stt", lambda r: web.json_response({"success": True, "text": "yes"}))
    app.router.add_get("/tts", lambda r: web.Response(status=204))
    app.router.add_get("/status", lambda r: web.json_response({"ok": True}))
    app.on_startup.append(_start_pusher)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    loop.run_until_complete(web.TCPSite(runner, "127.0.0.1", 8081).start())
    loop.run_forever()


# ---------------------------------------------------------------------------
# adb helpers
# ---------------------------------------------------------------------------

def _find_adb() -> str | None:
    sdk = os.environ.get("LOCALAPPDATA")
    if sdk:
        exe = Path(sdk) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
        if exe.is_file():
            return str(exe)
    return shutil.which("adb")


def adb(*args, timeout=120):
    return subprocess.run(
        [ADB, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
    )


def _max_ping() -> int:
    out = adb("shell", "dumpsys", "notification", "--noredact").stdout
    return max((int(n) for n in re.findall(r"Ping (\d+)", out)), default=0)


def _port_open(port=8081) -> bool:
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _logcat() -> str:
    return adb("shell", "logcat", "-d", "-s", "LoOperWeb").stdout


def _wait_for(predicate, timeout, what, interval=1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def _wait_log(needle, timeout):
    return _wait_for(lambda: needle in _logcat(), timeout, f"logcat line {needle!r}")


def _wait_new_ping(after, timeout=40):
    def fresh():
        current = _max_ping()
        return current if current > after else 0

    return _wait_for(fresh, timeout, f"a Ping after {after}")


def _launch(retries=2):
    """Cold start the app and wait for the page's WebSocket.

    force-stop also clears the app's notifications.  A fresh install / first
    DHT dial can take longer than the page load, so one retry is allowed.
    """
    for attempt in range(retries):
        adb("shell", "am", "force-stop", PKG)
        time.sleep(1)
        adb("shell", "logcat", "-c")
        adb("shell", "am", "start", "-n", ACTIVITY)
        try:
            _wait_log("[probe] secure=", timeout=60)
            _wait_for(lambda: _PEERS, 30, "the page's WebSocket")
            return
        except AssertionError:
            if attempt == retries - 1:
                raise
            _dbg("no WebSocket yet, relaunching the app")


def _home():
    adb("shell", "input", "keyevent", "3")


def _swipe_away():
    adb("shell", "input", "keyevent", "187")   # recents
    time.sleep(2)
    adb("shell", "input", "swipe", "540", "1400", "540", "150", "250")


# ---------------------------------------------------------------------------
# Session rig
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rig():
    global ADB
    ADB = _find_adb()
    if not ADB:
        pytest.skip("adb not found — install the Android SDK platform-tools")
    if "device" not in adb("devices").stdout:
        pytest.skip("no emulator/device attached — run: emulator -avd <name> -gpu off")
    if not APK.is_file():
        pytest.skip(f"APK missing ({APK}) — run build_pipeline/build_phone_apk.ps1")
    if not SIDECAR.is_file() or not SIDECAR_DEPS.is_dir():
        pytest.skip("sidecar or its node_modules missing — npm install in LoOper/agent_sidecar")
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    with socket.socket() as sock:
        if sock.connect_ex(("127.0.0.1", 8081)) == 0:
            pytest.skip("port 8081 is busy — stop the LoOper host before running these tests")

    threading.Thread(target=_run_stub_host, daemon=True).start()
    _wait_for(_port_open, 15, "stub host on 8081")

    work = Path(tempfile.mkdtemp(prefix="phone_e2e_"))
    identity = work / "identity.txt"
    sidecar_log = open(work / "sidecar.err", "w", encoding="utf-8")
    sidecar = subprocess.Popen(
        ["node", str(SIDECAR), "--port", "8081", "--identity", str(identity)],
        stdout=subprocess.PIPE, stderr=sidecar_log, text=True,
    )
    pubkey = None
    deadline = time.time() + 30
    while time.time() < deadline and pubkey is None:
        line = sidecar.stdout.readline()
        if not line:
            break
        if '"type":"ready"' in line:
            pubkey = json.loads(line)["pubkey"]
    if not pubkey:
        sidecar.kill()
        pytest.skip("sidecar never reported ready (hyperdht bootstrap failed)")

    # Pair without a QR scan: write the host key straight into SharedPreferences.
    prefs = work / "looper.xml"
    prefs.write_text(
        "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
        f'<map>\n    <string name="host_key">{pubkey}</string>\n</map>\n',
        encoding="utf-8",
    )
    adb("push", str(prefs), "/data/local/tmp/looper.xml")
    adb("shell", "run-as", PKG, "mkdir", "-p", "shared_prefs")
    adb("shell", "run-as", PKG, "cp", "/data/local/tmp/looper.xml", "shared_prefs/looper.xml")

    installed = adb("install", "-r", str(APK), timeout=300)
    assert "Success" in installed.stdout, f"adb install failed: {installed.stdout}{installed.stderr}"
    adb("shell", "pm", "grant", PKG, "android.permission.RECORD_AUDIO")
    adb("shell", "pm", "grant", PKG, "android.permission.POST_NOTIFICATIONS")

    print(f"\n[phone-e2e] pubkey={pubkey} workdir={work}", file=sys.stderr, flush=True)
    try:
        yield {"pubkey": pubkey, "work": work, "sidecar": sidecar}
    finally:
        sidecar.terminate()
        try:
            sidecar.wait(timeout=10)
        except subprocess.TimeoutExpired:
            sidecar.kill()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_ask_does_not_start_the_microphone(rig):
    """Regression: answering an ask must never auto-record the question."""
    _launch()
    _wait_log("needs an answer", timeout=45)
    time.sleep(6)  # let the one-shot startup self-test finish its own capture

    log = _logcat()
    assert "requesting getUserMedia" not in log, "startRecording() ran without a tap:\n" + log[-3000:]
    assert "[mic] recording @" not in log, "the mic captured audio without a tap:\n" + log[-3000:]


def test_notification_arrives_in_background(rig):
    """HOME (task still alive) -> every agent frame posts a system notification."""
    _launch()
    base = _max_ping()
    _home()

    ping = _wait_new_ping(base, timeout=40)
    assert f"Ping {ping}" in adb("shell", "dumpsys", "notification", "--noredact").stdout
    assert "notify fg=false" in _logcat(), "bridge posted nothing while backgrounded"


def test_notification_survives_swipe_away(rig):
    """Removing the task keeps the foreground service (and the pipe) alive."""
    _launch()
    base = _max_ping()
    _home()
    first = _wait_new_ping(base, timeout=40)

    _swipe_away()
    time.sleep(3)
    assert PKG in adb("shell", "ps", "-A").stdout, "process died on task removal"
    services = adb("shell", "dumpsys", "activity", "services", PKG).stdout
    assert "isForeground=true" in services, "NotifyService is no longer a foreground service"

    assert _wait_new_ping(first, timeout=40) > first, "no frames after the swipe"


def test_force_stop_ends_delivery(rig):
    """Force stop = no process, no pipe: nothing can arrive, by design."""
    _launch()
    base = _max_ping()
    _home()
    _wait_new_ping(base, timeout=40)

    adb("shell", "am", "force-stop", PKG)
    time.sleep(3)
    assert PKG not in adb("shell", "ps", "-A").stdout
    assert _max_ping() == 0, "notifications survived a force stop"
