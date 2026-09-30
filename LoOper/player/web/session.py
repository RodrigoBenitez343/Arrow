from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
"""undetected_chromedriver session factory shared by web recorder and replay.

Replay runs headless only when its node opts in (visible by default).  A
session gets a DISPOSABLE profile only when no chain identity is available
(standalone engine calls); chain-driven runs reuse the chain's DURABLE
profile so cookies/history (e.g. a login recorded once) persist across runs
and app restarts while each launch starts on the browser's default page.

Dependency guard: the heavy imports (undetected_chromedriver -> selenium) are
resolved lazily so the rest of LoOper can import this package even when the
web dependencies are not installed; a clear error is raised at use time.
"""

import json
import os
import psutil
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Iterable, Optional


try:
    import undetected_chromedriver as uc

    _UC_AVAILABLE = True
    _UC_ERROR: Optional[BaseException] = None
except Exception as _exc:  # pragma: no cover - dependency guard
    uc = None
    _UC_AVAILABLE = False
    _UC_ERROR = _exc


class WebDriverUnavailableError(RuntimeError):
    """Raised when undetected_chromedriver/selenium cannot be imported."""


def _require_driver_backend() -> None:
    if not _UC_AVAILABLE:
        raise WebDriverUnavailableError(
            "Web automation requires 'undetected-chromedriver' and 'selenium'. "
            f"Install them via requirements.txt (import error: {_UC_ERROR})."
        )


# Cumulative count of uc.Chrome initializations in this process, bumped around
# the real spawn.  The log shows how many browser instances each recording or
# replay actually created: 1 means the single-window launch is working, 2 means
# the uc double-launch quirk is back (the whole point of the port-wait patch).
_SPAWNED_SESSIONS = 0

# When True, _uc_launch_and_wait does NOT spawn Chrome and returns -1: the
# session is attaching to an already-running browser (the persistent recording
# workbench), so no second instance may ever be started.  Set around the
# uc.Chrome() call in attach mode only (recordings are serialized).
_ATTACH_ONLY = False


_VERSION_FOLDER_RE = re.compile(r"\d+\.\d+\.\d+\.\d+")
_DEBUG_PORT_RE = re.compile(r"--remote-debugging-port=(\d+)")


_PROFILE_MARKER = "looper-web-profile-"

# The ONE web browser scope shared by the whole app.  Every chain - saved or
# not, GUI or agent/scheduler/CLI - records and plays on this same durable
# profile, so cookies/logins made once (e.g. signing into Google) survive
# app restarts everywhere.  Per-chain scopes could not work for chains that
# are never saved to a file: their identity changed every app session, so
# the session was lost on every restart.  Pages are never restored - only
# cookies/history persist.
SHARED_WEB_SCOPE = "default"

# Profiles created by THIS process (fresh sessions and browsers left open for
# inspection after a visible replay).  Stale-browser cleanup must never kill
# them, even without an explicit exclude_dir - consecutive web nodes in one
# chain run in the same process, and each spawn would otherwise kill the
# previous node's still-open browser (its profile is not stale: it belongs to
# this run).
_ACTIVE_PROFILE_DIRS: set = set()

# ONE driver wrapper per scope, reused by EVERY caller of
# ensure_workbench_session (web sequences, web conditionals, form filler,
# OCR/page-text, recorder, picker).  Without this, each call spawned a NEW
# chromedriver attached to the same running browser and never quit it, so a
# long chain run stacked hundreds of chromedriver.exe processes and ate RAM
# (logged as an ever-growing "uc.Chrome spawn #N").  Keyed by scope; a dead
# wrapper is dropped and rebuilt.
_WORKBENCH_DRIVERS: dict = {}
_WORKBENCH_DRIVERS_LOCK = threading.Lock()

# Loggers that emit per-HTTP-request traffic (selenium POST /session/...,
# urllib3 connection churn, uc's debug chatter).  With LoOper's root logger at
# DEBUG these flood the terminal while the recorder polls the browser every
# 200 ms - raising them to WARNING keeps the per-action WEB ACTION signal the
# only visible output during recording.
_QUIET_LOGGERS = (
    "uc",
    "undetected_chromedriver",
    "CDP",
    "selenium",
    "selenium.webdriver.remote.remote_connection",
    "selenium.webdriver.common.selenium_manager",
    "urllib3",
    "urllib3.connectionpool",
    "urllib3.connection",
    "urllib3.util.retry",
)


def quiet_driver_logging(level: int = logging.WARNING) -> None:
    """Raise the WebDriver HTTP-traffic loggers above DEBUG.

    The recording poll loop talks to the browser every 200 ms (window handles
    + per-tab script execution), and replay does the same per action - with the
    root logger at DEBUG that means dozens of selenium/urllib3 lines per
    second.  ``level`` defaults to WARNING so only real problems surface.
    """
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(level)


def _cleanup_stale_looper_browsers(
    exclude_dir: Optional[str] = None,
    exclude_dirs: Optional[Iterable[str]] = None,
) -> None:
    """Kill orphaned Chrome processes from earlier LoOper web sessions.

    Every LoOper web session uses a disposable profile directory named
    ``looper-web-profile-*``.  A Chrome process still holding one of those
    dirs is either a zombie from an interrupted recording (its completion
    callback never ran) or a leftover from a crashed run - it can never be the
    user's default browser, so it is safe to terminate.  ``exclude_dir`` /
    ``exclude_dirs`` (plus every profile registered in ``_ACTIVE_PROFILE_DIRS``)
    skip the browsers of THIS process, so a freshly launched browser - or one
    left open by an earlier web node in the same run - is never killed.
    """
    try:
        import psutil
    except Exception:
        return
    excludes = set()
    if exclude_dir:
        excludes.add(exclude_dir)
    if exclude_dirs:
        excludes.update(exclude_dirs)
    excludes.update(_ACTIVE_PROFILE_DIRS)
    excludes_norm = tuple(os.path.normcase(d) for d in excludes if d)
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or [])
            if name == "chrome.exe" and _PROFILE_MARKER in cmd:
                if any(ex in os.path.normcase(cmd) for ex in excludes_norm):
                    continue  # this is one of our own browsers (fresh or detached)
                logger.info("Killing stale LoOper web browser (pid=%s)", proc.info.get("pid"))
                proc.kill()
        except Exception:
            continue


def _consolidate_windows(driver) -> None:
    """Keep a single browser window for the session.

    undetected_chromedriver's startup can leave an extra blank window on
    screen (the user sees it open right after the driver is prepared, before
    recording actually starts).  Everything in our session is the same browser
    process, so extra window handles can be closed safely - the last one
    (the real recording window) is kept and focused.
    """
    try:
        handles = driver.window_handles
    except Exception as exc:
        logger.debug("Window consolidation skipped (no handles yet): %s", exc)
        return
    if not handles:
        return
    if len(handles) > 1:
        logger.warning(
            "Web session opened %d windows; closing extras, keeping the last one",
            len(handles),
        )
        for handle in handles[:-1]:
            try:
                driver.switch_to.window(handle)
                driver.close()
            except Exception:
                continue
    try:
        driver.switch_to.window(handles[-1])
    except Exception:
        pass


def _enumerate_top_windows() -> list[dict]:
    """Visible top-level windows on the desktop (Win32 only).

    Returns ``[{"hwnd", "pid", "title", "rect": (l, t, r, b)}]`` ordered by
    z-order (top-most first).  Empty list on non-Windows or when the Win32
    API is unavailable.
    """
    if os.name != "nt":
        return []
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return []
    user32 = ctypes.windll.user32
    results: list[dict] = []

    def _callback(hwnd, _unused):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        results.append({
            "hwnd": hwnd,
            "pid": pid.value,
            "title": buf.value,
            "rect": (rect.left, rect.top, rect.right, rect.bottom),
        })
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return results


def _cursor_monitor_work_area() -> Optional[tuple[int, int, int, int]]:
    """Work area ``(left, top, width, height)`` of the monitor the cursor is on.

    A run is STARTED from the screen the user is looking at, so that is where
    the browser is opened and maximised - rather than on whichever monitor
    Windows cascades the new window to.  None when the Win32 API is
    unavailable (non-Windows host).
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    try:
        user32 = ctypes.windll.user32

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        MONITOR_DEFAULTTONEAREST = 2
        monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        return (work.left, work.top,
                work.right - work.left, work.bottom - work.top)
    except Exception:
        return None


def _match_session_window(driver, windows: list[dict]) -> Optional[int]:
    """PID of the Chrome window the WebDriver session controls, if identifiable.

    Compares the CDP window bounds (device-independent pixels) against the OS
    window rectangles (physical pixels), sweeping common DPI scale factors -
    Chrome's CDP bounds and Win32 GetWindowRect disagree on scaled displays.
    Returns None when no window can be positively matched (caller must then
    never kill anything).
    """
    try:
        target = driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
    except Exception as exc:
        logger.debug("Session window match unavailable (CDP bounds): %s", exc)
        return None
    bounds = target.get("bounds") or {}
    left, top = bounds.get("left"), bounds.get("top")
    width, height = bounds.get("width"), bounds.get("height")
    if None in (left, top, width, height):
        return None
    cx_dip, cy_dip = left + width / 2.0, top + height / 2.0
    # EnumWindows lists top-most first and the window WebDriver just focused
    # is the top-most one - so the FIRST match wins.  Never override on equal
    # area: with two same-size windows (the uc double-launch case) a
    # largest-area scan can end on the bottom-most window and kill the wrong
    # (recording) window.
    for w in windows:
        l, t, r, b = w["rect"]
        if (r - l) * (b - t) <= 0:
            continue
        for scale in (1.0, 1.25, 1.5, 2.0, 0.75):
            cx, cy = cx_dip * scale, cy_dip * scale
            if l <= cx <= r and t <= cy <= b:
                return w["pid"]
    return None


def _prune_extra_browser_windows(driver, profile_dir: str) -> None:
    """Close every visible Chrome window on *profile_dir* except the session's own.

    undetected_chromedriver double-launches: it starts Chrome itself
    (start_detached) and then starts ChromeDriver, which can end up driving a
    second Chrome instance on the same disposable profile.  The extra window
    records nothing and only confuses the user, and it is NOT part of the
    WebDriver session so window-handle consolidation cannot see it.  The
    session's window is identified by matching the CDP window bounds against
    the OS window rectangles; every other visible chrome.exe window on the
    profile is terminated.

    Always logs a diagnostic line; only kills when the session window is
    positively identified (a failed match leaves everything alone so the
    recording window can never be killed).
    """
    if os.name != "nt":
        return
    try:
        import psutil
    except Exception:
        logger.debug("Window prune skipped: psutil unavailable")
        return

    windows = _enumerate_top_windows()
    profile_norm = os.path.normcase(str(profile_dir))
    chrome_pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (proc.info.get("name") or "").lower() == "chrome.exe":
                cmd = " ".join(proc.info.get("cmdline") or [])
                if profile_norm in os.path.normcase(cmd):
                    chrome_pids.add(proc.info["pid"])
        except Exception:
            continue
    ours = [w for w in windows if w["pid"] in chrome_pids]

    handles: list[str] = []
    titles: list[str] = []
    try:
        handles = list(driver.window_handles)
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                titles.append(driver.execute_script("return document.title") or "")
            except Exception:
                titles.append("?")
    except Exception:
        pass

    session_pid = _match_session_window(driver, ours)
    closed: list[int] = []
    if session_pid is not None:
        for w in ours:
            if w["pid"] == session_pid:
                continue
            try:
                logger.warning(
                    "Closing extra web browser window pid=%s title=%r "
                    "(session window pid=%s)",
                    w["pid"], w["title"], session_pid,
                )
                os.kill(w["pid"], signal.SIGTERM)
                closed.append(w["pid"])
            except Exception as exc:
                logger.debug(
                    "Could not close extra browser window pid=%s: %s", w["pid"], exc
                )
    else:
        logger.debug(
            "Session window not identifiable (windows=%r) - leaving all windows alone",
            [w["pid"] for w in ours],
        )

    logger.info(
        "WEB WINDOWS: driver_handles=%d titles=%r chrome_windows=%r "
        "session_pid=%s closed=%r",
        len(handles),
        titles,
        [{"pid": w["pid"], "title": w["title"]} for w in ours],
        session_pid,
        closed,
    )


def _bundled_chrome_dir() -> Optional[Path]:
    """Chrome for Testing bundled next to the frozen app, if any.

    build_msi.ps1 stages chrome.exe + chromedriver.exe under
    <_MEIPASS>/bin/chrome (see LoOperApp.spec).  When present, web sessions
    prefer this pair so record/replay work offline on machines without an
    installed Chrome.  Source runs have no bundle and use the system Chrome.
    """
    if not getattr(sys, "frozen", False):
        return None
    base = Path(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
    cand = base / "bin" / "chrome"
    if (cand / "chrome.exe").is_file():
        return cand
    return None


def _version_main_from_exe(exe_path: Optional[str]) -> Optional[int]:
    """Major Chrome version read from an exe's VERSIONINFO resource.

    A pure file read - never launches the browser (on Windows a
    ``chrome --version`` subprocess hands off to a running Chrome and opens a
    stray window).
    """
    if not exe_path or not os.path.isfile(exe_path):
        return None
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(exe_path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(exe_path, 0, size, buf):
            return None
        value = ctypes.c_void_p()
        length = wintypes.UINT()
        if not ver.VerQueryValueW(
            buf, "\\VarFileInfo\\Translation",
            ctypes.byref(value), ctypes.byref(length),
        ):
            return None
        lang_cp = ctypes.cast(value, ctypes.POINTER(wintypes.DWORD))[0]
        subblock = "\\StringFileInfo\\%04x%04x\\ProductVersion" % (
            lang_cp & 0xFFFF, (lang_cp >> 16) & 0xFFFF,
        )
        if not ver.VerQueryValueW(
            buf, subblock, ctypes.byref(value), ctypes.byref(length),
        ):
            return None
        match = re.search(r"(\d+)\.", ctypes.wstring_at(value, length.value))
        return int(match.group(1)) if match else None
    except Exception:
        return None


def _detect_chrome_version_main() -> Optional[int]:
    """Major version of the installed Chrome, so the matching ChromeDriver is used.

    undetected_chromedriver defaults to the newest driver, which fails when the
    installed Chrome is one minor release behind (e.g. driver 152 vs Chrome 151).
    Detection order avoids launching ``chrome.exe`` while Chrome is already
    running: on Windows a ``chrome --version`` subprocess hands off to the
    running browser, which opens a stray "New Tab" window (the orphan window
    seen at recording start).  Registry, the version-named install folder and
    the exe's VERSIONINFO resource are pure reads; ``chrome --version`` is only
    the last resort (and the primary path on POSIX, where no handoff occurs).
    """
    _require_driver_backend()

    # A bundled Chrome for Testing sets the driver version: the shipped
    # chromedriver matches it, so pin the pair and never consult the registry.
    bundled = _bundled_chrome_dir()
    if bundled is not None:
        major = _version_main_from_exe(str(bundled / "chrome.exe"))
        if major:
            return major

    if os.name == "nt":
        # 1. BLBeacon registry value - pure read, no process launch.
        try:
            import winreg

            for hive, path in (
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon"),
                (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            ):
                try:
                    with winreg.OpenKey(hive, path) as key:
                        value = winreg.QueryValueEx(key, "version")[0]
                    match = re.search(r"(\d+)\.", value or "")
                    if match:
                        return int(match.group(1))
                except OSError:
                    continue
        except Exception:
            pass

    chrome_path = None
    try:
        chrome_path = uc.find_chrome_executable()
    except Exception:
        pass

    if chrome_path:
        if os.name == "nt":
            # 2. Version-named install folder - pure read.
            try:
                app_dir = Path(chrome_path).parent
                versions = [p.name for p in app_dir.iterdir() if _VERSION_FOLDER_RE.fullmatch(p.name)]
                if versions:
                    latest = sorted(versions, key=lambda v: [int(x) for x in v.split(".")])[-1]
                    return int(latest.split(".")[0])
            except Exception:
                pass
            # 3. chrome.exe VERSIONINFO resource - pure file read, no launch.
            major = _version_main_from_exe(chrome_path)
            if major:
                return major

        # 4. Last resort: `chrome --version` subprocess.  On Windows this can
        #    hand off to a running Chrome and open a stray window, so it only
        #    runs when every launch-free source above failed.
        try:
            result = subprocess.run(
                [chrome_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            match = re.search(r"(\d+)\.", result.stdout or "")
            if match:
                return int(match.group(1))
        except Exception:
            pass

    return None


def _uc_launch_and_wait(executable: str, *args: str) -> int:
    """Launch Chrome detached and wait for its debugging port to come up.

    Replaces uc's own pre-launch (``start_detached``).  uc 3.5.5 starts a
    Chrome process itself and immediately hands off to chromedriver, which
    connects to ``--remote-debugging-port``.  When the pre-launched Chrome has
    not bound that port yet, chromedriver launches a SECOND Chrome on the same
    profile - the two recording windows quirk (only one of them is driven by
    the driver).  Waiting here removes that race: chromedriver always attaches
    to the single warmed instance, so exactly one window opens.

    Returns the launched Chrome PID (uc stores it to kill the browser on
    quit).  Raises when Chrome exits during startup or the port never opens.
    """
    if _ATTACH_ONLY:
        # Attaching to an already-running browser: never spawn another Chrome.
        return -1
    port = None
    for arg in args:
        match = _DEBUG_PORT_RE.match(arg)
        if match:
            port = int(match.group(1))
            break
    creationflags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP - no console window and
        # the browser survives the parent; uc's own dprocess uses the same.
        creationflags = 0x00000008 | 0x00000200
    last_exit = None
    for attempt in (1, 2):
        proc = subprocess.Popen(
            [executable, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=os.name != "nt",
            creationflags=creationflags,
        )
        if port is None:
            return proc.pid  # no port to wait for - plain detached start
        deadline = time.time() + 30.0
        port_up = False
        exited = None
        while time.time() < deadline:
            if proc.poll() is not None:
                exited = proc.returncode
                break
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1
                ):
                    port_up = True
                    break
            except Exception:
                time.sleep(0.2)
        if port_up:
            return proc.pid
        if exited is None:
            raise RuntimeError(
                f"Chrome debugging port {port} did not come up in 30s"
            )
        # Chrome died before binding its port.  This is usually a transient
        # race (port grabbed between free_port() and Chrome's bind, AV hiccup,
        # one-shot handoff) - a second attempt with the same args almost always
        # succeeds, so retry once before giving up.
        last_exit = exited
        logger.warning(
            "Chrome exited during startup (code %s) on attempt %d of 2; retrying",
            exited, attempt,
        )
    raise RuntimeError(
        f"Chrome exited during startup (code {last_exit}); web session aborted"
    )


def _patch_uc_prelaunch() -> None:
    """Replace uc's pre-launch with the port-wait launcher (idempotent).

    undetected_chromedriver routes its unconditional pre-launch through
    ``start_detached`` when ``use_subprocess=False``; swapping that symbol for
    ``_uc_launch_and_wait`` makes every session single-window without forking
    the package.  Safe to call from every ``create_session``.
    """
    if getattr(uc, "_LOOPER_PRELAUNCH_PATCHED", False):
        return
    uc.start_detached = _uc_launch_and_wait
    uc._LOOPER_PRELAUNCH_PATCHED = True
    logger.debug("uc pre-launch patched: single-window launch via port wait")


# --- Shared persistent browser profile ------------------------------------
# The whole app uses ONE durable Chrome profile so cookies, history and
# logins (e.g. signing into Google once while recording) survive across
# runs and app restarts - in the GUI, agent, scheduler and CLI alike.  The
# profile lives under web_recorder_profile/chains/<SHARED_WEB_SCOPE> and is
# deliberately NEVER page-restored: every fresh launch starts on the
# browser's default page, never on the URL where the previous run ended
# (tab restore made replays depend on wherever the last run parked the
# browser).  Sequences must carry their own navigation: the recorder
# captures clicks/typing/keys, so a sequence that needs a specific page
# starts by navigating there through recorded actions.
#
# Recording always uses this profile (ensure_workbench_session).  Playback
# also uses it: GUI playback and visible runs attach to the shared browser
# when it is still open (recording left it up), else relaunch it; headless
# runs open the durable profile without a window.  Only genuinely isolated
# contexts (playback while a recording is actively capturing, standalone
# engine calls) fall back to a disposable looper-web-profile-* temp dir.
# The durable profile deliberately avoids the looper-web-profile-* marker
# so _cleanup_stale_looper_browsers never kills it as a zombie.


def _workbench_dir() -> Path:
    """Writable, durable location for the persistent recording profile."""
    try:
        from AI.runtime_paths import get_runtime_dir  # source + frozen builds
        return Path(get_runtime_dir()) / "web_recorder_profile"
    except Exception:
        return Path(tempfile.gettempdir()) / "looper-web-workbench"


def _chain_scope_dir(chain_key: Optional[str] = None) -> Path:
    """Profile dir for a chain scope: root profile, or chains/<key> per chain.

    A chain scope is a stable identity derived by the caller (e.g. from the
    chain file path for saved chains, or a per-graph id while the chain is
    unsaved).  Each scope gets its own Chrome user-data-dir, so cookies and
    history are local to that chain and survive browser restarts.
    """
    root = _workbench_dir()
    if not chain_key:
        return root
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(chain_key)).strip("._")
    return root / "chains" / (safe[:64] or "chain")


def _workbench_state_path(chain_key: Optional[str] = None) -> Path:
    return _chain_scope_dir(chain_key) / "session_state.json"


def _read_workbench_state(chain_key: Optional[str] = None) -> Optional[dict]:
    """{pid, port} of the last workbench launch, or None when unknown."""
    try:
        data = json.loads(_workbench_state_path(chain_key).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("pid") and data.get("port"):
            return data
    except Exception:
        pass
    return None


def _find_workbench_browser(chain_key: Optional[str] = None) -> Optional[dict]:
    """Locate the LIVE browser on a persistent profile, if any.

    Scans every chrome.exe process for the main browser process holding the
    scope profile (helper processes carry ``--type=`` and are skipped).
    Returns ``{"pid", "port"}`` where ``port`` is the remote-debugging port
    when the browser was started by LoOper, or None when it was started
    manually (no debugging port - it cannot be attached to).  Scanning the
    profile instead of trusting the state-file pid matters because Chrome can
    replace its main browser process (pid goes stale) while the browser - and
    its debug port - stay alive.  Each chain scope is scanned separately, so
    browsers of different chains never collide.
    """
    try:
        import psutil
    except Exception:
        return None
    profile = os.path.normcase(str(_chain_scope_dir(chain_key)))
    candidates: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if (proc.info.get("name") or "").lower() != "chrome.exe":
                continue
            cmd = " ".join(proc.info.get("cmdline") or [])
            if "--type=" in cmd:
                continue  # renderer/utility helper, never the main process
            # Match the EXACT profile (--user-data-dir), not a substring:
            # per-chain profiles live under the root profile, so a root scan
            # must never attach to a chain's browser (and vice versa).
            udir = re.search(r"--user-data-dir=(\"[^\"]+\"|[^\s]+)", cmd)
            if not udir:
                continue
            if os.path.normcase(udir.group(1).strip('"')) != profile:
                continue
            match = re.search(r"--remote-debugging-port=(\d+)", cmd)
            candidates.append({
                "pid": proc.info["pid"],
                "port": int(match.group(1)) if match else None,
            })
        except Exception:
            continue
    # Prefer a browser that still exposes its debugging port.
    for candidate in candidates:
        if candidate["port"] is not None:
            return candidate
    return candidates[0] if candidates else None


def _save_workbench_state(pid, port, chain_key: Optional[str] = None) -> None:
    """Persist {pid, port} of a workbench browser for the next launch.

    Only pid/port are stored - page URLs are deliberately not remembered, so
    a relaunch starts fresh instead of parking the chain on the last page
    (which made replays depend on where the previous run ended).
    """
    try:
        state = _read_workbench_state(chain_key) or {}
        state["pid"] = pid
        state["port"] = port
        state_path = _workbench_state_path(chain_key)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        logger.info(
            "Web recorder browser state saved (scope=%r): %s", chain_key, state
        )
    except Exception as exc:
        logger.debug("Could not save web recorder state: %s", exc)


def _write_workbench_state(driver, chain_key: Optional[str] = None) -> None:
    """Persist {pid, port} of a freshly launched workbench browser."""
    try:
        port = int(str(driver.options.debugger_address).split(":")[-1])
    except Exception:
        port = None
    _save_workbench_state(
        getattr(driver, "browser_pid", None), port, chain_key=chain_key
    )


def snapshot_workbench_tabs(driver, chain_key: Optional[str] = None) -> None:
    """No-op kept for API compatibility: the last page is NOT remembered.

    This used to persist the open tab URLs so a relaunch resumed the chain's
    story; it made replays depend on wherever the previous run parked the
    browser, so after an app restart sequences landed on the wrong page and
    failed.  The chain's cookies/sessions still persist in its profile.
    """
    return


def _restore_workbench_tabs(driver, chain_key: Optional[str] = None) -> int:
    """No-op kept for API compatibility: a fresh launch starts on the default
    page, never on the last open tab.
    """
    return 0


def _driver_alive(driver) -> bool:
    """Cheap probe - raises fast when the browser/driver session is gone."""
    try:
        driver.execute_script("return 1")
        return True
    except Exception:
        return False


def ensure_workbench_session(chain_key: Optional[str] = None) -> "uc.Chrome":
    """Return ONE persistent browser driver per scope, reused by all callers.

    Web sequences, web conditionals, the form filler, OCR/page-text, the
    recorder and the picker all resolve their browser here, so the driver
    wrapper is memoized per scope: the SAME chromedriver serves the whole run
    instead of a fresh one being spawned (and leaked) on every call.  A dead
    wrapper (browser closed, session gone) is dropped and rebuilt.
    """
    key = chain_key or ""
    # The cache is read and published under the lock, but the lock is NEVER
    # held while launching: an attach can burn its full 30s timeout and a
    # relaunch can retry for another minute, and every other web caller
    # (conditionals, form filler, OCR/page-text, picker, recorder) would block
    # behind the lock for that whole time — one bad browser state froze the
    # whole app instead of failing once.  ponytail: unsynchronised launches can
    # race into two drivers; the loser is dropped from the cache (worst case one
    # extra chromedriver) which beats serialising every caller behind a hang.
    with _WORKBENCH_DRIVERS_LOCK:
        cached = _WORKBENCH_DRIVERS.get(key)
    if cached is not None and _driver_alive(cached):
        return cached
    if cached is not None:
        with _WORKBENCH_DRIVERS_LOCK:
            if _WORKBENCH_DRIVERS.get(key) is cached:
                _WORKBENCH_DRIVERS.pop(key, None)
    driver = _launch_workbench_session(chain_key)
    with _WORKBENCH_DRIVERS_LOCK:
        _WORKBENCH_DRIVERS[key] = driver
    return driver


def _launch_workbench_session(chain_key: Optional[str] = None) -> "uc.Chrome":
    """Return a persistent recording browser for a chain scope, reusing the
    live one if any.

    ``chain_key`` scopes the browser to ONE chain: its own durable profile,
    so chains never share or clobber each other's story.  Recording-only by
    default; GUI playback of an in-progress chain also uses it (the executor
    passes the same key).  CLI/headless/scheduled playback does NOT: those
    chains run in their own disposable browsers.  The live browser on the
    scope profile is located by scanning chrome processes (NOT by trusting
    the state-file pid, which goes stale when Chrome replaces its main
    process).  A live browser with a debugging port is ATTACHED to (same
    profile, same port - chromedriver connects to the running instance);
    without a port it was started manually and cannot be attached to, so this
    fails with a clear message instead of spawning a doomed second instance on
    the locked profile.  Only when NO browser is open is the scope profile
    launched fresh - on the browser's DEFAULT page: cookies and sessions
    persist in the profile, but the last open page is deliberately NOT
    restored, so every run starts from the same place.
    """
    profile_dir = _chain_scope_dir(chain_key)
    try:
        profile_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    browser = _find_workbench_browser(chain_key)
    if browser is not None:
        if browser["port"] is None:
            raise RuntimeError(
                "A browser window is already open on the Looper shared web "
                "profile, but it was not started by LoOper (no debugging "
                "port).  Close it and start the recording again."
            )
        try:
            logger.info(
                "Reusing open web recorder browser (scope=%r pid=%s port=%s)",
                chain_key, browser["pid"], browser["port"],
            )
            driver = create_session(
                headless=False,
                profile_dir=profile_dir,
                attach_to_port=int(browser["port"]),
            )
        except Exception as exc:
            logger.warning(
                "Attach to web recorder browser failed (%s) — re-scanning for "
                "a live browser", exc
            )
            # The recorded port died (Chrome replaced its main process, or a
            # half-dead CDP socket).  RE-SCAN before relaunching: launching on
            # a profile that a LIVE browser still holds makes Chrome hand off
            # and exit, and the launcher then waits on a port that never opens.
            live = _find_workbench_browser(chain_key)
            if live is not None and live.get("port"):
                logger.info(
                    "Re-attaching to the live browser on the shared profile "
                    "(scope=%r pid=%s port=%s)", chain_key, live["pid"],
                    live["port"],
                )
                driver = create_session(
                    headless=False,
                    profile_dir=profile_dir,
                    attach_to_port=int(live["port"]),
                )
                if chain_key:
                    _save_workbench_state(
                        live["pid"], live["port"], chain_key=chain_key
                    )
                else:
                    _save_workbench_state(live["pid"], live["port"])
                release_workbench(driver)
                return driver
            if live is not None:
                # A window on the profile that LoOper did not start: launching
                # would collide with it, so fail fast and say what to do.
                raise RuntimeError(
                    "A browser window is already open on the Looper shared "
                    "web profile, but it was not started by LoOper (no "
                    "debugging port), so it cannot be attached to.  Close "
                    "that browser and run again."
                )
        else:
            # Refresh the state with the live pid/port so it stays accurate
            # even when Chrome replaced its main process since the last save.
            if chain_key:
                _save_workbench_state(
                    browser["pid"], browser["port"], chain_key=chain_key
                )
            else:
                _save_workbench_state(browser["pid"], browser["port"])
            release_workbench(driver)
            return driver
    # Reached only when NO live browser holds the profile (a fresh launch
    # cannot collide).
    state = _read_workbench_state(chain_key)
    if state:
        logger.info(
            "Web recorder browser state stale (pid=%s) - relaunching on the "
            "persistent profile", state.get("pid"),
        )
    driver = create_session(headless=False, profile_dir=profile_dir)
    # Fresh start on the chain's persistent profile: cookies/sessions carry
    # over, but the LAST PAGE is deliberately NOT restored - every chain run
    # (and recording) begins from the browser's default page, so a sequence
    # replays the same way whether the app was just opened or ran before.
    if chain_key:
        _write_workbench_state(driver, chain_key)
    else:
        _write_workbench_state(driver)
    release_workbench(driver)
    return driver


def release_workbench(driver) -> None:
    """Neutralize quit so the recording browser outlives its driver wrappers.

    Every attach/fresh wrapper is released this way: Selenium auto-quits the
    browser when a driver is garbage-collected at process exit, so without
    this the recording browser would die with the first wrapper.  The browser
    stays open so the user continues where they left off - the state file
    written at launch keeps pointing at the live browser; closing the window
    manually ends the session (the next call then relaunches on the same
    profile).
    """
    try:
        driver.quit = lambda: None  # type: ignore[method-assign]
    except Exception:
        pass


def create_session(
    headless: bool = True,
    profile_dir: Optional[Path] = None,
    window_size: str = "1280,800",
    version_main: Optional[int] = None,
    attach_to_port: Optional[int] = None,
) -> "uc.Chrome":
    """Start an undetected Chrome session with an isolated browser profile.

    ``version_main`` pins the ChromeDriver major version; when omitted it is
    detected from the installed Chrome binary.  ``attach_to_port`` connects to
    an ALREADY-RUNNING Chrome debugging port instead of launching one (used
    to reuse the persistent recording browser): the pre-launch is neutralized
    and chromedriver attaches to the running instance, whose windows are
    never touched.
    """
    _require_driver_backend()
    # Replace uc's pre-launch with the port-wait launcher before any session
    # is created (idempotent - re-patching is a no-op).
    _patch_uc_prelaunch()
    if profile_dir is None:
        profile_dir = Path(tempfile.mkdtemp(prefix="looper-web-profile-"))
    # Register the profile so stale-browser cleanup never kills this session -
    # and never kills browsers left open by EARLIER web nodes in the same run.
    _ACTIVE_PROFILE_DIRS.add(str(profile_dir))
    # Remove any leftover LoOper web browsers first so the new session is the
    # only visible Chrome and the fresh profile dir cannot be locked.
    _cleanup_stale_looper_browsers()

    quiet_driver_logging()

    options = uc.ChromeOptions()
    options.add_argument(f"--window-size={window_size}")
    # A VISIBLE, freshly launched browser opens maximised on the monitor the
    # run was started from: a new window otherwise lands wherever Windows
    # cascades it, possibly straddling two screens.
    if not headless and attach_to_port is None:
        work_area = _cursor_monitor_work_area()
        if work_area is not None:
            options.add_argument(f"--window-position={work_area[0]},{work_area[1]}")
        options.add_argument("--start-maximized")
    # Chrome's own console chatter ("DevTools listening...", GPU/GL noise) is
    # not ours to log - only ERROR+ survives.
    options.add_argument("--log-level=3")
    if attach_to_port is not None:
        # Reuse the running browser: uc routes the session to this debug port
        # (already bound by the live workbench) so chromedriver attaches
        # instead of launching a new instance.
        options.debugger_address = "127.0.0.1:%d" % attach_to_port

    kwargs = {
        "options": options,
        "headless": headless,
        "user_data_dir": str(profile_dir),
        # Route uc's unconditional pre-launch through our patched launcher
        # (use_subprocess=False -> start_detached) so chromedriver attaches to
        # the single warmed instance instead of racing a second Chrome onto
        # the same profile.  In attach mode the launcher is a no-op.
        "use_subprocess": False,
    }
    if version_main is None:
        # A bundled Chrome for Testing sets the driver version; otherwise the
        # installed Chrome's major version pins the driver uc downloads.
        version_main = _detect_chrome_version_main()
    if version_main:
        kwargs["version_main"] = version_main

    bundled = _bundled_chrome_dir()
    if bundled is not None:
        # Ship the browser+driver pair next to the app: deterministic and
        # offline-capable web nodes (no dependence on an installed Chrome).
        bundled_exe = bundled / "chrome.exe"
        bundled_driver = bundled / "chromedriver.exe"
        if bundled_exe.is_file():
            kwargs["browser_executable_path"] = str(bundled_exe)
            options.binary_location = str(bundled_exe)
            logger.info("Web session uses bundled Chrome: %s", bundled_exe)
        if bundled_driver.is_file():
            kwargs["driver_executable_path"] = str(bundled_driver)
            logger.info("Web session uses bundled chromedriver: %s", bundled_driver)

    global _SPAWNED_SESSIONS, _ATTACH_ONLY
    _SPAWNED_SESSIONS += 1
    logger.info(
        "uc.Chrome spawn #%d (headless=%s, profile=%s, attach_port=%s)",
        _SPAWNED_SESSIONS, headless, profile_dir, attach_to_port,
    )
    _ATTACH_ONLY = attach_to_port is not None
    try:
        driver = uc.Chrome(**kwargs)
    finally:
        _ATTACH_ONLY = False
    # uc.Chrome() re-tunes some selenium loggers during startup - re-apply so
    # the quiet state sticks for the whole session.
    quiet_driver_logging()
    # Kill any OTHER stale LoOper web browser that is not this session, then
    # close extra windows our own launch may have produced - so recording and
    # replay always see exactly one browser window.
    try:
        _cleanup_stale_looper_browsers(exclude_dir=str(profile_dir))
    except Exception:
        pass
    # Window consolidation/pruning is for our OWN launches.  In attach mode
    # the browser is the user's persistent workbench - never touch its
    # windows (they may have several tabs/windows open on purpose).
    if attach_to_port is None:
        try:
            _consolidate_windows(driver)
        except Exception as exc:
            logger.debug("Window consolidation failed: %s", exc)
        if not headless:
            try:
                # --start-maximized is not always honoured by uc's launcher,
                # so maximise once the window exists: the browser then fills
                # the monitor it was positioned on.
                driver.maximize_window()
            except Exception as exc:
                logger.debug("Maximise failed: %s", exc)
            # Backstop: if a second window ever still appears on this profile,
            # close it (the session window is matched by CDP bounds; the extra
            # is never part of the WebDriver session).  No sleep needed - the
            # port-wait launcher made startup synchronous, so there is no
            # late-spawn race left to give it time.
            try:
                _prune_extra_browser_windows(driver, str(profile_dir))
            except Exception as exc:
                logger.debug("Window prune failed: %s", exc)
    return driver


def cleanup_session(driver: "uc.Chrome") -> None:
    """Quit the driver and remove its disposable profile directory.

    Profiles under the workbench root (the shared recording profile and every
    per-chain scope under web_recorder_profile/chains/*) are durable by
    design - they hold a chain's browser story and must survive so the next
    recording/playback relaunches at its last state, so they are NEVER
    deleted here.  Only disposable looper-web-profile-* temp dirs are removed.
    """
    profile_dir: Optional[str] = None
    try:
        profile_dir = getattr(driver, "user_data_dir", None)
    except Exception:
        profile_dir = None
    try:
        driver.quit()
    except Exception:
        pass
    # Forget the cached wrapper so the next call re-attaches instead of
    # handing back a quit session.
    try:
        with _WORKBENCH_DRIVERS_LOCK:
            for _k, _d in list(_WORKBENCH_DRIVERS.items()):
                if _d is driver:
                    _WORKBENCH_DRIVERS.pop(_k, None)
    except Exception:
        pass
    if profile_dir:
        try:
            root = _workbench_dir()
            root_r = str(root.resolve())
            prof_r = str(Path(profile_dir).resolve())
            if prof_r == root_r or prof_r.startswith(root_r + os.sep):
                logger.debug(
                    "Keeping durable workbench profile %s (chain browser story)",
                    profile_dir,
                )
                return
        except Exception:
            pass
        shutil.rmtree(profile_dir, ignore_errors=True)


def detach_session(driver: "uc.Chrome") -> None:
    """Leave the browser open after a replay.

    Selenium auto-quits the browser when the driver is garbage-collected at
    process exit, so the instance ``quit`` is neutralised here.  The browser
    keeps running so the user can inspect the result and close it themselves;
    the disposable profile dir is left in place because it is still in use.
    """
    try:
        driver.quit = lambda: None  # type: ignore[method-assign]
    except Exception:
        pass
