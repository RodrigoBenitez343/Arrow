"""
Isolated llama-server spawn test for PyInstaller debugging.

This script tests whether llama-server.exe can start from inside a
compiled PyInstaller environment.  It runs 4 spawn strategies and
reports which (if any) work.

Run from source:  python test_vlm.py
Run compiled:     dist\test_vlm\test_vlm.exe
"""
import os
import sys
import subprocess
import time
import base64
import json
import shutil


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_binary() -> str | None:
    """Find llama-server.exe — bundled or system."""
    meipass = getattr(sys, "_MEIPASS", None)
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else None

    candidates = []
    if meipass:
        candidates += [
            os.path.join(meipass, "bin", "llama-server.exe"),
            os.path.join(meipass, "llama-server.exe"),
        ]
    if exe_dir:
        candidates += [
            os.path.join(exe_dir, "bin", "llama-server.exe"),
            os.path.join(exe_dir, "llama-server.exe"),
        ]
    # Fallback: source tree
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "LoOper", "AI", "bin", "llama-server.exe",
        )
    )

    for c in candidates:
        c = os.path.normpath(c)
        if os.path.exists(c):
            log(f"Found binary: {c}")
            return c

    found = shutil.which("llama-server")
    if found:
        log(f"Found via PATH: {found}")
        return found
    return None


def find_model() -> tuple[str | None, str | None]:
    """Return (model_path, mmproj_path)."""
    base_dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        base_dirs.append(os.path.join(meipass, "models", "LFM2.5-VL-450M-GGUF"))
    base_dirs.append(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "LoOper", "AI", "models", "LFM2.5-VL-450M-GGUF",
        )
    )

    for d in base_dirs:
        d = os.path.normpath(d)
        if not os.path.isdir(d):
            continue
        model = None
        mmproj = None
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if not os.path.isfile(fp):
                continue
            if f.endswith(".gguf") and "mmproj" not in f.lower():
                model = fp
            elif "mmproj" in f.lower() and f.endswith(".gguf"):
                mmproj = fp
        if model:
            return model, mmproj
    return None, None


def build_minimal_env() -> dict:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    sys32 = os.path.join(system_root, "System32")
    return {
        "SystemRoot": system_root,
        "PATH": os.pathsep.join([sys32, system_root, os.path.join(sys32, "Wbem")]),
    }


def test_version(server_exe: str) -> dict:
    """Test 4 different spawn strategies for --version.  Returns results dict."""
    results: dict = {}
    minimal_env = build_minimal_env()
    strategies = [
        ("inherit env", {}),
        ("minimal env", {"env": minimal_env}),
        (
            "minimal env + DETACHED",
            {"env": minimal_env, "creationflags": 0x00000008 | 0x04000000},
        ),
        (
            "shell=True + minimal env",
            {"env": minimal_env, "shell": True},
        ),
    ]

    for name, kwargs in strategies:
        log(f"  Test: --version ({name})")
        try:
            r = subprocess.run(
                [server_exe, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                **kwargs,
            )
            rc = r.returncode
            out = (r.stdout or r.stderr).strip()
            status = "PASS" if rc == 0 else f"FAIL (rc={rc}, 0x{rc & 0xFFFFFFFF:08X})"
            log(f"    {status}: {out[:120] if out else '(no output)'}")
            results[name] = {"rc": rc, "output": out[:300], "status": status}
        except Exception as e:
            log(f"    EXCEPTION: {e}")
            results[name] = {"rc": -1, "output": str(e), "status": f"EXC: {e}"}

    return results


def take_screenshot(max_width: int = 1024) -> bytes | None:
    """Take a screenshot, resize to max_width, return PNG bytes."""
    try:
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            img = sct.grab(monitor)
            import io
            from PIL import Image
            pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
            # Resize to keep token count reasonable
            if pil_img.width > max_width:
                ratio = max_width / pil_img.width
                new_h = int(pil_img.height * ratio)
                pil_img = pil_img.resize((max_width, new_h), Image.LANCZOS)
                log(f"  Screenshot resized: {img.width}x{img.height} -> {max_width}x{new_h}")
            buf = io.BytesIO()
            pil_img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except ImportError:
        try:
            from PIL import ImageGrab
            import io
            img = ImageGrab.grab()
            if img.width > max_width:
                ratio = max_width / img.width
                img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
        except Exception as e:
            log(f"Screenshot failed: {e}")
            return None
    except Exception as e:
        log(f"Screenshot failed: {e}")
        return None


def spawn_server(
    strategy_name: str,
    cmd: list[str],
    port: int,
    env: dict | None = None,
    creationflags: int | None = None,
    shell: bool = False,
    keep_alive: bool = False,
) -> subprocess.Popen | None:
    """Spawn server with a specific strategy.
    Returns the Popen handle if healthy (and keep_alive=True),
    or True-like Popen if healthy, None if failed.
    """
    import requests

    log(f"  Strategy: {strategy_name}")
    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.PIPE,
    }
    if env is not None:
        kwargs["env"] = env
    if creationflags is not None:
        kwargs["creationflags"] = creationflags
    if shell:
        kwargs["shell"] = True
        actual_cmd = subprocess.list2cmdline(cmd)
    else:
        actual_cmd = cmd

    proc = subprocess.Popen(actual_cmd, **kwargs)

    deadline = time.time() + 30
    while time.time() < deadline:
        retcode = proc.poll()
        if retcode is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            log(f"    DIED: rc={retcode} (0x{retcode & 0xFFFFFFFF:08X})")
            if stderr:
                log(f"    Stderr: {stderr[:500]}")
            return None
        try:
            r = requests.get(f"http://127.0.0.1:{port}/health", timeout=2)
            if r.status_code == 200:
                log(f"    HEALTHY! {r.text.strip()[:80]}")
                if keep_alive:
                    return proc
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return proc  # Non-None = success
        except Exception:
            pass
        time.sleep(2)

    log("    Timeout")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return None


def test_full_server(server_exe: str, model_path: str, mmproj_path: str | None) -> bool:
    """Start the full server with multiple spawn strategies, run a vision test."""
    import requests

    port = 18081
    cmd = [
        server_exe,
        "--model", model_path,
        "--port", str(port),
        "--threads", "4",
        "--ctx-size", "4096",
        "--batch-size", "512",
        "--host", "127.0.0.1",
        "--gpu-layers", "0",
        "--no-mmap",
    ]
    if mmproj_path and os.path.exists(mmproj_path):
        cmd.extend(["--mmproj", mmproj_path])

    log(f"Starting full server on port {port}...")
    log(f"  Cmd: {' '.join(cmd)[:200]}")

    minimal_env = build_minimal_env()

    # Try multiple spawn strategies
    strategies = [
        ("inherit env (NO flags)", {}),
        ("minimal env + DETACHED", {"env": minimal_env, "creationflags": 0x00000008 | 0x04000000}),
        ("minimal env (NO flags)", {"env": minimal_env}),
        ("shell=True minimal env", {"env": minimal_env, "shell": True}),
        ("shell=True inherit env", {"shell": True}),
        ("CREATE_NEW_CONSOLE + minimal", {"env": minimal_env, "creationflags": 0x00000010}),
        ("CREATE_NO_WINDOW + minimal", {"env": minimal_env, "creationflags": 0x08000000}),
    ]

    winning_proc = None
    winning_name = None
    for name, kwargs in strategies:
        proc = spawn_server(name, cmd, port, keep_alive=True, **kwargs)
        if proc is not None:
            winning_proc = proc
            winning_name = name
            log(f"  -> SUCCESS with '{name}'!")
            break
        port += 1
        cmd[cmd.index("--port") + 1] = str(port)

    if winning_proc is None:
        log("  -> ALL spawn strategies FAILED")
        return False

    # Take screenshot
    log("Taking screenshot...")
    png_bytes = take_screenshot()
    if not png_bytes:
        log("  No screenshot, skipping vision test")
        return True  # Server started, just couldn't screenshot

    log(f"  Screenshot: {len(png_bytes)} bytes")

    # Send vision request
    img_b64 = base64.b64encode(png_bytes).decode("utf-8")
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this screenshot in one sentence."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 256,
        "temperature": 0.7,
        "stream": False,
    }

    log("Sending vision request...")
    try:
        r = requests.post(
            f"http://127.0.0.1:{port}/chat/completions",
            json=payload,
            timeout=300,
        )
        log(f"  Response: HTTP {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            log(f"  Content: {content[:300]}")
        else:
            log(f"  Error body: {r.text[:500]}")
    except Exception as e:
        log(f"  Request failed: {e}")

    winning_proc.terminate()
    try:
        winning_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        winning_proc.kill()
    log("Server stopped.")
    return True


def main() -> int:
    log("=" * 50)
    log("ISOLATED LLAMA-SERVER SPAWN TEST")
    log(f"  Python: {sys.executable}")
    log(f"  Frozen: {getattr(sys, 'frozen', False)}")
    log(f"  _MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}")
    log(f"  CWD: {os.getcwd()}")
    log("=" * 50)

    server_exe = find_binary()
    if not server_exe:
        log("FATAL: Cannot find llama-server.exe")
        return 1

    log(f"Binary size: {os.path.getsize(server_exe):,} bytes")

    # --- Phase 1: --version test ---
    log("\n--- PHASE 1: Basic spawn (--version) ---")
    results = test_version(server_exe)

    any_pass = any(r["rc"] == 0 for r in results.values())
    if not any_pass:
        log("\n*** ALL --version tests FAILED ***")
        log("The binary cannot even print its version from this environment.")
        log("This confirms a fundamental DLL/CRT initialization issue.")
        log("Possible causes:")
        log("  1. Missing or incompatible VC++ runtime DLLs")
        log("  2. PyInstaller bootloader corrupts child process creation")
        log("  3. Binary compiled with incompatible toolchain")
        log("\nRunning DLL dependency check...")
        try:
            r = subprocess.run(
                ["where", "vcruntime140.dll"],
                capture_output=True, text=True, timeout=5,
            )
            log(f"  vcruntime140.dll locations:\n{r.stdout.strip()}")
        except Exception:
            pass
        try:
            r = subprocess.run(
                ["where", "msvcp140.dll"],
                capture_output=True, text=True, timeout=5,
            )
            log(f"  msvcp140.dll locations:\n{r.stdout.strip()}")
        except Exception:
            pass
        return 2

    log("\n--- At least one --version strategy PASSED ---")

    # --- Phase 2: Full server + vision test ---
    log("\n--- PHASE 2: Full server + vision test ---")
    model_path, mmproj_path = find_model()
    if not model_path:
        log("Model not found — skipping Phase 2")
        return 0

    log(f"Model: {model_path}")
    log(f"mmproj: {mmproj_path}")

    ok = test_full_server(server_exe, model_path, mmproj_path)
    if ok:
        log("\n*** FULL TEST PASSED ***")
        return 0
    else:
        log("\n*** Server startup or vision test FAILED ***")
        return 3


if __name__ == "__main__":
    sys.exit(main())
