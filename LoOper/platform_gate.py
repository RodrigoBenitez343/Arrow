"""Platform gate: one API for window / input / screen primitives.

Windows uses Win32 (ctypes) and UI Automation; Linux uses X11 (python-xlib,
X11 session only — Wayland forbids global input, window enumeration and
synthetic input by design, so it is refused loudly instead of degraded).

Every function here has a REAL implementation on both platforms. When a
required library or session type is missing the call raises
``PlatformUnsupported`` with an actionable message — never a silent no-op.

ponytail: one flat module, two backends inline. Split into a package only if a
third platform arrives.
"""
from __future__ import annotations

import collections
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"
PLATFORM = "windows" if IS_WINDOWS else "linux" if IS_LINUX else (
    "macos" if IS_MACOS else sys.platform)


class PlatformUnsupported(RuntimeError):
    """No real implementation exists on this platform/session."""


# --------------------------------------------------------------------- Windows
if IS_WINDOWS:

    _user32 = ctypes.windll.user32
    try:
        from ctypes import wintypes as _wt

        _user32.GetWindowTextW.argtypes = [
            _wt.HWND, _wt.LPWSTR, ctypes.c_int]
        _user32.GetWindowThreadProcessId.argtypes = [
            _wt.HWND, ctypes.POINTER(_wt.DWORD)]
        _user32.GetWindowRect.argtypes = [
            _wt.HWND, ctypes.POINTER(_wt.RECT)]
        _user32.IsWindowVisible.argtypes = [_wt.HWND]
        _WNDENUMPROC = ctypes.WINFUNCTYPE(
            _wt.BOOL, _wt.HWND, _wt.LPARAM)
    except Exception:  # pragma: no cover - non-wintypes ctypes build
        _wt = None
        _WNDENUMPROC = None

    def _win_title(handle) -> str:
        try:
            length = _user32.GetWindowTextLengthW(handle)
            if not length:
                return ""
            buf = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(handle, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    def _win_pid(handle):
        try:
            pid = ctypes.c_ulong()
            _user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            return int(pid.value) or None
        except Exception:
            return None

    def _win_rect(handle):
        try:
            rect = _wt.RECT()
            if not _user32.GetWindowRect(handle, ctypes.byref(rect)):
                return None
            return (int(rect.left), int(rect.top),
                    int(rect.right), int(rect.bottom))
        except Exception:
            return None

    def _win_visible(handle) -> bool:
        try:
            return bool(_user32.IsWindowVisible(handle))
        except Exception:
            return False

    def _win_info(handle):
        if not handle:
            return None
        return {
            "handle": int(handle),
            "title": _win_title(handle),
            "pid": _win_pid(handle),
            "rect": _win_rect(handle),
            "visible": _win_visible(handle),
        }

    def is_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def set_dpi_awareness() -> None:
        """Physical-pixel coordinate space for pyautogui / mss / vision."""
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                _user32.SetProcessDPIAware()
            except Exception:
                pass

    def screen_size():
        return (int(_user32.GetSystemMetrics(0)),
                int(_user32.GetSystemMetrics(1)))

    def async_key_state(vk: int) -> bool:
        try:
            return bool(_user32.GetAsyncKeyState(int(vk)) & 0x8000)
        except Exception:
            return False

    def active_window():
        return _win_info(_user32.GetForegroundWindow())

    def list_windows():
        """Visible titled top-level windows, top-most first."""
        if _WNDENUMPROC is None:
            return []
        out = []

        def _cb(hwnd, _lparam):
            if _win_visible(hwnd):
                info = _win_info(hwnd)
                if info and info["title"]:
                    out.append(info)
            return True

        try:
            _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
        except Exception:
            return out
        return out

    def window_pid(handle):
        return _win_pid(handle) if handle else None

    def window_rect(handle):
        return _win_rect(handle) if handle else None

    def is_window_visible(handle) -> bool:
        return _win_visible(handle) if handle else False

    def activate_window(handle) -> bool:
        try:
            if _user32.IsIconic(handle):
                _user32.ShowWindow(handle, 9)  # SW_RESTORE
            return bool(_user32.SetForegroundWindow(handle))
        except Exception:
            return False

    def move_window(handle, left, top, width, height) -> bool:
        try:
            return bool(_user32.MoveWindow(
                handle, int(left), int(top), int(width), int(height), True))
        except Exception:
            return False

    def cursor_work_area():
        """Work area (left, top, width, height) of the monitor under the cursor."""
        try:
            class _MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong),
                            ("rcMonitor", _wt.RECT),
                            ("rcWork", _wt.RECT),
                            ("dwFlags", ctypes.c_ulong)]

            point = _wt.POINT()
            if not _user32.GetCursorPos(ctypes.byref(point)):
                return None
            _user32.MonitorFromPoint.argtypes = [
                _wt.POINT, ctypes.c_ulong]
            _user32.MonitorFromPoint.restype = ctypes.c_void_p
            monitor = _user32.MonitorFromPoint(point, 2)  # NEAREST
            if not monitor:
                return None
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if not _user32.GetMonitorInfoW(
                    ctypes.c_void_p(monitor), ctypes.byref(info)):
                return None
            work = info.rcWork
            return (int(work.left), int(work.top),
                    int(work.right - work.left), int(work.bottom - work.top))
        except Exception:
            return None

    def error_box(title: str, message: str) -> None:
        try:
            _user32.MessageBoxW(0, str(message), str(title), 0x10)
        except Exception:
            print(f"{title}: {message}", file=sys.stderr)

    # ------------------------------------------- screen / cursor / audio
    def screenshot(region=None):
        """PIL image of the desktop, or of *region* = (left, top, w, h)."""
        import pyautogui

        if region is None:
            return pyautogui.screenshot()
        left, top, width, height = (int(v) for v in region)
        return pyautogui.screenshot(region=(left, top, width, height))

    def force_foreground(handle) -> bool:
        """Foreground *handle* around the Win32 foreground lock (ALT tap)."""
        try:
            if _user32.IsIconic(handle):
                _user32.ShowWindow(handle, 9)      # SW_RESTORE
            _user32.keybd_event(0x12, 0, 0, 0)     # ALT down: releases the lock
            _user32.keybd_event(0x12, 0, 2, 0)     # ALT up
            return bool(_user32.SetForegroundWindow(handle))
        except Exception:
            return False

    # Every OCR_* shape the pointer can take; blanking OCR_NORMAL alone leaves
    # an I-beam / hand / resize cursor on screen.
    _OCR_IDS = (32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644,
                32645, 32646, 32648, 32649, 32650, 32651)
    _cursors_blanked = False

    def _make_blank_cursor():
        from ctypes import wintypes as wt

        _user32.CreateCursor.argtypes = [
            wt.HINSTANCE, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]
        _user32.CreateCursor.restype = wt.HANDLE
        _user32.CopyIcon.argtypes = [wt.HANDLE]
        _user32.CopyIcon.restype = wt.HANDLE
        _user32.SetSystemCursor.argtypes = [wt.HANDLE, wt.DWORD]
        _user32.SetSystemCursor.restype = wt.BOOL
        _user32.DestroyCursor.argtypes = [wt.HANDLE]
        n = 32 * 32 // 8
        and_mask = (ctypes.c_ubyte * n)(*([0xFF] * n))   # keep the screen
        xor_mask = (ctypes.c_ubyte * n)(*([0x00] * n))   # no invert -> invisible
        base = _user32.CreateCursor(None, 0, 0, 32, 32, and_mask, xor_mask)
        if not base:
            return False
        try:
            for ocr in _OCR_IDS:
                copy = _user32.CopyIcon(base)   # SetSystemCursor takes ownership
                if copy:
                    _user32.SetSystemCursor(copy, ocr)
        finally:
            _user32.DestroyCursor(base)
        return True

    def set_pointer_visible(visible: bool) -> bool:
        """Blank every system cursor, or reload the user's cursor scheme."""
        global _cursors_blanked
        if not visible:
            if _cursors_blanked:
                return True
            try:
                _cursors_blanked = bool(_make_blank_cursor())
            except Exception as exc:
                logger.warning("Could not blank the system cursors: %s", exc)
            return _cursors_blanked
        if not _cursors_blanked:
            return True
        _cursors_blanked = False
        try:
            _user32.SystemParametersInfoW.argtypes = [
                ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
            _user32.SystemParametersInfoW(0x0057, 0, None, 0)  # SPI_SETCURSORS
            return True
        except Exception as exc:
            logger.warning("Could not restore the system cursors: %s", exc)
            return False

    class _Audio:
        """winsound playback handle (async, so ESC can purge it)."""

        def __init__(self, path):
            import winsound

            self._ws = winsound
            self._duration = _wav_duration(path)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)

        def stop(self):
            try:
                self._ws.PlaySound(None, self._ws.SND_PURGE)
            except Exception:
                pass

        def wait(self, stop_event=None) -> bool:
            deadline = time.monotonic() + (self._duration or 600.0)
            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    self.stop()
                    return False
                time.sleep(0.05)
            return True

    def _start_audio(path):
        return _Audio(path)

    def stop_audio() -> None:
        if _AUDIO is not None:
            _AUDIO.stop()

    def clipboard_check():
        return (True, "")

    def session_ok():
        return (True, "")

    def uia_available() -> bool:
        return True


# ----------------------------------------------------------------------- Linux
elif IS_LINUX:

    _XLIB = None

    def _x():
        """Module-level Xlib handles; raises when python-xlib is missing."""
        global _XLIB
        if _XLIB is not None:
            return _XLIB
        try:
            from Xlib import X, XK, display as _xdisplay, protocol
            _XLIB = {
                "X": X, "XK": XK, "protocol": protocol,
                "display": _xdisplay.Display(),
            }
        except Exception as exc:  # missing lib or no DISPLAY
            raise PlatformUnsupported(
                "Linux input/window control needs python-xlib on an X11 "
                f"session (pip install python-xlib): {exc}")
        return _XLIB

    def _atom(x, name: str):
        return x["display"].intern_atom(name)

    def _net_name(display, win) -> str:
        try:
            prop = win.get_full_property(display.intern_atom("_NET_WM_NAME"), 0)
            if prop is not None and prop.value:
                value = prop.value
                if isinstance(value, bytes):
                    return value.decode("utf-8", "replace")
                return str(value)
        except Exception:
            pass
        try:
            name = win.get_wm_name()
            return name if isinstance(name, str) else (name or b"").decode(
                "utf-8", "replace")
        except Exception:
            return ""

    def _net_pid(display, win):
        try:
            prop = win.get_full_property(
                display.intern_atom("_NET_WM_PID"), 0)
            if prop is not None and len(prop.value):
                return int(prop.value[0])
        except Exception:
            return None
        return None

    def _x11_info(display, win, root):
        try:
            geom = win.get_geometry()
            offset = win.translate_coords(root, 0, 0)
            visible = win.get_attributes().map_state == _x()["X"].IsViewable
            left, top = -int(offset.x), -int(offset.y)
            return {
                "handle": int(win.id),
                "title": _net_name(display, win),
                "pid": _net_pid(display, win),
                "rect": (left, top, left + int(geom.width),
                         top + int(geom.height)),
                "visible": bool(visible),
            }
        except Exception:
            return None

    def is_admin() -> bool:
        try:
            return os.geteuid() == 0
        except Exception:
            return False

    def set_dpi_awareness() -> None:
        """X11 has no process-wide DPI opt-in; Qt/Xft own scaling here."""

    def screen_size():
        x = _x()
        root = x["display"].screen().root
        geom = root.get_geometry()
        return (int(geom.width), int(geom.height))

    # X11 keymap bit for the Windows virtual-key codes call sites still pass.
    _VK_KEYSYM = {
        0x10: "Shift_L", 0x11: "Control_L", 0x12: "Alt_L",
        0x5B: "Super_L", 0x5C: "Super_R",
    }

    def _vk_keysym(vk: int) -> str:
        if vk in _VK_KEYSYM:
            return _VK_KEYSYM[vk]
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:  # digits, letters
            return chr(vk).lower()
        return ""

    def async_key_state(vk: int) -> bool:
        x = _x()
        keysym = _vk_keysym(int(vk))
        if not keysym:
            return False
        keycode = x["display"].keysym_to_keycode(
            x["XK"].string_to_keysym(keysym))
        if not keycode:
            return False
        try:
            keymap = x["display"].query_keymap()
            return bool(keymap[keycode >> 3] & (1 << (keycode & 7)))
        except Exception:
            return False

    def active_window():
        x = _x()
        display = x["display"]
        root = display.screen().root
        try:
            prop = root.get_full_property(
                _atom(x, "_NET_ACTIVE_WINDOW"), 0)
            if prop is None or not len(prop.value) or not int(prop.value[0]):
                return None
            win = display.create_resource_object("window", int(prop.value[0]))
            return _x11_info(display, win, root)
        except Exception:
            return None

    def list_windows():
        """Mapped, titled top-level windows, top-most first (_NET_CLIENT_LIST)."""
        x = _x()
        display = x["display"]
        root = display.screen().root
        out = []
        try:
            prop = root.get_full_property(
                _atom(x, "_NET_CLIENT_LIST_STACKING"), 0)
            ids = list(prop.value) if prop is not None else []
        except Exception:
            ids = []
        for wid in reversed(ids):  # EWMH lists bottom -> top
            try:
                win = display.create_resource_object("window", int(wid))
                info = _x11_info(display, win, root)
            except Exception:
                continue
            if info and info["visible"] and info["title"]:
                out.append(info)
        return out

    def window_pid(handle):
        if not handle:
            return None
        x = _x()
        display = x["display"]
        return _net_pid(
            display, display.create_resource_object("window", int(handle)))

    def window_rect(handle):
        if not handle:
            return None
        x = _x()
        display = x["display"]
        info = _x11_info(display, display.create_resource_object(
            "window", int(handle)), display.screen().root)
        return info["rect"] if info else None

    def is_window_visible(handle) -> bool:
        if not handle:
            return False
        try:
            x = _x()
            win = x["display"].create_resource_object("window", int(handle))
            return win.get_attributes().map_state == x["X"].IsViewable
        except Exception:
            return False

    def activate_window(handle) -> bool:
        """EWMH activate request to the window manager (+ raise as fallback)."""
        x = _x()
        display = x["display"]
        X = x["X"]
        try:
            root = display.screen().root
            win = display.create_resource_object("window", int(handle))
            event = x["protocol"].event.ClientMessage(
                window=win,
                client_type=_atom(x, "_NET_ACTIVE_WINDOW"),
                data=(32, [2, X.CurrentTime, 0, 0, 0]),
            )
            root.send_event(
                event,
                event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            win.configure(stack_mode=X.Above)
            display.flush()
            return True
        except Exception:
            return False

    def cursor_work_area():
        """X11 single-screen model: the whole root window."""
        try:
            width, height = screen_size()
            return (0, 0, width, height)
        except Exception:
            return None

    def move_window(handle, left, top, width, height) -> bool:
        try:
            x = _x()
            win = x["display"].create_resource_object("window", int(handle))
            win.configure(x=int(left), y=int(top),
                          width=int(width), height=int(height))
            x["display"].flush()
            return True
        except Exception:
            return False

    def error_box(title: str, message: str) -> None:
        logger.error("%s: %s", title, message)
        print(f"{title}: {message}", file=sys.stderr)
        try:  # a running Qt app can show it as a real dialog
            from PyQt5.QtWidgets import QApplication, QMessageBox
            if QApplication.instance() is not None:
                QMessageBox.critical(None, str(title), str(message))
        except Exception:
            pass

    # ------------------------------------------- screen / cursor / audio
    def screenshot(region=None):
        """PIL image of the desktop, or of *region* = (left, top, w, h).

        mss reads the X11 framebuffer directly; pyautogui's screenshot needs
        the external ``scrot``/``gnome-screenshot`` binaries on Linux.
        """
        import mss
        from PIL import Image

        with mss.mss() as sct:
            if region is None:
                shot = sct.grab(sct.monitors[0])
            else:
                left, top, width, height = (int(v) for v in region)
                shot = sct.grab({"left": left, "top": top,
                                 "width": width, "height": height})
            return Image.frombytes("RGB", shot.size, shot.rgb)

    def force_foreground(handle) -> bool:
        """X11 has no foreground lock: the EWMH activate request is enough."""
        return activate_window(handle)

    def set_pointer_visible(visible: bool) -> bool:
        """Hide/show the X pointer with the XFixes extension.

        Root-window scope, so the pointer is hidden everywhere while the
        agent arrow is up - the X11 twin of blanking the Win32 cursors.
        """
        try:
            x = _x()
            display = x["display"]
            display.xfixes_query_version(5, 0)
            if visible:
                display.xfixes_show_cursor()
            else:
                display.xfixes_hide_cursor(display.screen().root)
            display.flush()
            return True
        except Exception as exc:
            logger.warning("XFixes pointer control unavailable: %s", exc)
            return False

    class _Audio:
        """Playback handle over the system player (paplay / aplay / ffplay)."""

        def __init__(self, path):
            self._proc = subprocess.Popen(
                _audio_cmd() + [path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        def stop(self):
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                pass

        def wait(self, stop_event=None) -> bool:
            while self._proc.poll() is None:
                if stop_event is not None and stop_event.is_set():
                    self.stop()
                    return False
                time.sleep(0.05)
            return True

    def _audio_cmd():
        for name, args in (("paplay", []),
                           ("aplay", ["-q"]),
                           ("ffplay", ["-nodisp", "-autoexit",
                                       "-loglevel", "quiet"])):
            exe = shutil.which(name)
            if exe:
                return [exe] + args
        raise PlatformUnsupported(
            "No audio player for TTS playback: install pulseaudio-utils "
            "(paplay), alsa-utils (aplay) or ffmpeg (ffplay).")

    def _start_audio(path):
        return _Audio(path)

    def stop_audio() -> None:
        if _AUDIO is not None:
            _AUDIO.stop()

    def clipboard_check():
        """pyperclip needs a clipboard binary on X11 (native X11 would mean
        owning a selection with its own event loop)."""
        if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            return (False,
                    "The clipboard needs X11: wl-copy/wl-paste are not reachable "
                    "from an XWayland-only session.")
        for name in ("xclip", "xsel"):
            if shutil.which(name):
                return (True, "")
        return (False,
                "The clipboard needs `xclip` or `xsel` (sudo apt install xclip).")

    # ---------------------------------------- UI Automation twin: AT-SPI2
    # The player/recorder read a duck-typed subset of IUIAutomation;
    # platform_gate_atspi implements that SAME surface on AT-SPI2, so
    # element-first replay and element recording run on Linux too.
    _atspi_local = threading.local()

    def _atspi_module():
        try:  # LoOper/ on sys.path: the app and the test suite both run that way
            import platform_gate_atspi as atspi
        except ImportError:  # imported as LoOper.*
            from . import platform_gate_atspi as atspi
        return atspi

    def _atspi_automation():
        """Per-thread element-tree client (the ``uia()`` twin)."""
        if getattr(_atspi_local, "uia", None) is None:
            _atspi_local.uia = _atspi_module().automation()
        return _atspi_local.uia

    def session_ok():
        """Wayland has no global input / window enumeration: refuse loudly.

        AT-SPI2 is also required: element-first replay and element recording
        depend on it, and a vision-only fallback would be a silent degrade.
        """
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return (False,
                    "Arrow needs an X11 session on Linux: Wayland forbids "
                    "global input hooks, synthetic input and window "
                    "enumeration. Log in with 'Xorg' (X11) and relaunch.")
        if not os.environ.get("DISPLAY"):
            return (False,
                    "No X11 display found (DISPLAY is unset). Start Arrow "
                    "inside an X11 session.")
        try:
            _atspi_module().backend()
        except Exception as exc:
            return (False, str(exc))
        clipboard_ok, clipboard_msg = clipboard_check()
        if not clipboard_ok:
            return (False, clipboard_msg)
        return (True, "")

    def uia_available() -> bool:
        try:
            return bool(_atspi_module().available())
        except Exception:
            return False


# ------------------------------------------------------------------- Unsupported
else:

    def _unsupported(*_a, **_k):
        raise PlatformUnsupported(
            f"Arrow desktop control is implemented for Windows and Linux/X11, "
            f"not {PLATFORM!r}.")

    def session_ok():
        return (False, f"Arrow desktop control does not support {PLATFORM!r}.")

    def uia_available() -> bool:
        return False

    is_admin = set_dpi_awareness = screen_size = async_key_state = _unsupported
    active_window = list_windows = cursor_work_area = _unsupported
    window_pid = window_rect = is_window_visible = activate_window = _unsupported
    move_window = error_box = _unsupported
    screenshot = force_foreground = set_pointer_visible = _unsupported
    _start_audio = stop_audio = _unsupported

    def clipboard_check():
        return (False, f"Arrow desktop control does not support {PLATFORM!r}.")


# ------------------------------------------------------- element tree (both)
_uia_local = threading.local()


def uia():
    """Element-tree client, per thread: IUIAutomation on Windows, AT-SPI on
    Linux (the duck-typed twin of the same surface). None elsewhere.

    COM objects are apartment-bound, so the Windows instance is per-thread;
    the AT-SPI adapter is cached per thread for the same reason (one D-Bus
    round trip chain per click).
    """
    if IS_LINUX:
        return _atspi_automation()
    if not IS_WINDOWS:
        return None
    if getattr(_uia_local, "tried", False):
        return getattr(_uia_local, "uia", None)
    _uia_local.tried = True
    _uia_local.uia = None
    try:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient

        _uia_local.uia = comtypes.client.CreateObject(
            UIAutomationClient.CUIAutomation)
    except Exception as exc:
        logger.debug("UIA unavailable: %s", exc)
    return _uia_local.uia


# ------------------------------------------------------------------ convenience
def point(x, y):
    """A hit-test point in each backend's shape (POINT struct vs a pair)."""
    if IS_WINDOWS:
        from ctypes import wintypes

        return wintypes.POINT(int(x), int(y))
    return (int(x), int(y))


def foreground_matches(pid=None, exe=None, process_name=None,
                       title_hint=None) -> bool:
    """True when the active window belongs to the described app.

    One implementation for both platforms: the recorder and both executors
    used to carry three copies of this Win32 dance.
    """
    info = active_window()
    if not info:
        return False
    if pid and info.get("pid") and int(pid) == int(info["pid"]):
        return True
    try:
        import psutil

        proc = psutil.Process(int(info["pid"])) if info.get("pid") else None
        if proc is not None:
            if exe:
                try:
                    if os.path.normcase(str(proc.exe() or "")) == os.path.normcase(exe):
                        return True
                except Exception:
                    pass
            if process_name:
                try:
                    if str(proc.name() or "").lower() == process_name.lower():
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    if title_hint:
        return str(title_hint).strip().lower() in str(info.get("title") or "").lower()
    return False


def windows_for_pid(pid: int):
    """Visible titled windows owned by *pid*."""
    return [w for w in list_windows() if w.get("pid") == int(pid)]


# ------------------------------------------------------------ audio (both)
_AUDIO = None


def _wav_duration(path):
    """Seconds of PCM audio in a WAV header, or None when unreadable."""
    try:
        import wave

        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            return wf.getnframes() / float(rate) if rate else None
    except Exception:
        return None


def play_audio(path):
    """Start *path* and remember the handle, so stop_audio() can cut it."""
    global _AUDIO
    _AUDIO = _start_audio(str(path))
    return _AUDIO


# ------------------------------------------------------------- keys (both)
class KeyEvent(collections.namedtuple("KeyEvent", "name event_type")):
    """One hook event: ``keyboard``-package shape (name, "down"/"up")."""

    __slots__ = ()


# pynput member name -> the `keyboard` package's name for the same key.
_KEY_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt gr",
    "shift_l": "shift", "shift_r": "shift",
    "cmd": "windows", "cmd_l": "windows", "cmd_r": "windows",
    "esc": "escape",
    "page_up": "page up", "page_down": "page down",
}


def key_name(key) -> str:
    """A pynput key in the `keyboard` package's naming ("ctrl", "page up")."""
    char = getattr(key, "char", None)
    if char:
        return char.lower() if char.isalpha() else char
    name = getattr(key, "name", None) or str(key).rsplit(".", 1)[-1]
    return _KEY_ALIASES.get(name, name)


class _PynputKeyHook:
    """Global key hook built on pynput's listener (no `keyboard` package).

    ``keyboard`` needs root on Linux (it reads /dev/input); pynput drives the
    X11 record extension as the session user, so one implementation serves
    both platforms.
    """

    def __init__(self, on_event):
        from pynput import keyboard as _kb

        self._on_event = on_event
        self._listener = _kb.Listener(
            on_press=lambda key: self._emit(key, "down"),
            on_release=lambda key: self._emit(key, "up"),
        )
        self._listener.daemon = True
        self._listener.start()

    def _emit(self, key, event_type):
        try:
            self._on_event(KeyEvent(key_name(key), event_type))
        except Exception:  # a hook callback must never kill the listener
            logger.debug("key hook callback failed", exc_info=True)
        return True

    def stop(self):
        try:
            self._listener.stop()
        except Exception:
            pass


def key_hook(on_event):
    """Register *on_event(KeyEvent)* for every global key press/release."""
    return _PynputKeyHook(on_event)


# --------------------------------------------------------- binaries (both)
def clipboard_copy(text) -> bool:
    """Put *text* on the system clipboard.

    pyperclip owns the platform split (Win32 / X11 via xclip-xsel); the gate
    only records the requirement, which ``session_ok()`` enforces before the
    app starts, so a missing backend can never fail silently mid-run.
    """
    import pyperclip

    pyperclip.copy("" if text is None else str(text))
    return True


def clipboard_paste() -> str:
    """Current clipboard text ('' when empty)."""
    import pyperclip

    return pyperclip.paste() or ""


def exe_name(base: str) -> str:
    """'llama-server' -> 'llama-server.exe' on Windows, unchanged elsewhere."""
    return f"{base}.exe" if IS_WINDOWS else base


# Chrome for Testing / system Chrome names differ only by the suffix.
CHROME_EXE = exe_name("chrome")
CHROMEDRIVER_EXE = exe_name("chromedriver")


def venv_python(venv_dir: str) -> str:
    """Interpreter inside a venv (Scripts/python.exe vs bin/python)."""
    if IS_WINDOWS:
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def sandbox_shared_dir() -> str:
    """Folder the sandbox agent and the player exchange files through."""
    path = "C:\\TempShared" if IS_WINDOWS else os.path.join(
        os.path.expanduser("~"), ".local", "share", "arrow", "shared")
    os.makedirs(path, exist_ok=True)
    return path


def start_sandbox_agent(python, script, port, work_dir=None, extra_env=None):
    """Start ``sandbox_agent.py`` in THIS session; '' on failure.

    On Windows the sandbox runs the agent inside an RDP alternate-shell
    session (see the executors).  X11 has no such barrier - the desktop is
    already interactive - so the agent is simply spawned here and polled on
    its port, which is the same handshake the RDP path ends with.
    """
    import socket

    env = dict(os.environ)
    env.update(extra_env or {})
    env["LOOPER_AGENT_PORT"] = str(int(port))
    try:
        proc = subprocess.Popen(
            [python, script, "--port", str(int(port))],
            cwd=work_dir or None, env=env, start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        logger.error("Sandbox agent could not start: %s", exc)
        return ""
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if proc.poll() is not None:
            logger.error("Sandbox agent exited with code %s", proc.returncode)
            return ""
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
                url = f"http://127.0.0.1:{int(port)}"
                logger.info("Sandbox agent reachable at %s (pid %s)", url, proc.pid)
                return url
        except Exception:
            time.sleep(0.25)
    logger.error("Sandbox agent did not become reachable on port %s", port)
    try:
        proc.terminate()
    except Exception:
        pass
    return ""


def venv_site_packages(venv_dir: str) -> str:
    """site-packages inside a venv (Lib/site-packages vs lib/pythonX.Y/...)."""
    if IS_WINDOWS:
        return os.path.join(venv_dir, "Lib", "site-packages")
    lib_dir = os.path.join(venv_dir, "lib")
    if os.path.isdir(lib_dir):
        for name in sorted(os.listdir(lib_dir), reverse=True):
            candidate = os.path.join(lib_dir, name, "site-packages")
            if os.path.isdir(candidate):
                return candidate
    return os.path.join(lib_dir, "site-packages")
