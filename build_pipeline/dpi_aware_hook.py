"""PyInstaller runtime hook — set Windows DPI awareness as early as possible.

This ensures that all DPI-related Windows API calls (GetSystemMetrics,
CreateDC, BitBlt, DirectX capture, etc.) return **physical pixel**
coordinates/bitmaps, which eliminates the mismatch between:

-  pyautogui.size()        → GetSystemMetrics  (returns logical pixels when unaware)
-  pyautogui.screenshot()  → BitBlt            (returns physical pixels always)
-  mss.capture()           → DXGI              (returns physical pixels always)

With DPI awareness set, ALL three sources agree on physical pixels.
"""

import ctypes
import sys

_DPI_AWARE_SET = False


def _set_dpi_awareness() -> bool:
    """Attempt to set the process as DPI-aware.

    Tries the modern per-monitor API first (Windows 8.1+), then falls
    back to the legacy SetProcessDPIAware (Vista+).  Returns True if
    either call succeeded.

    Must be called before any Win32 GDI / USER32 calls that depend on
    DPI — the runtime hook is the ideal place for this.
    """
    global _DPI_AWARE_SET
    if _DPI_AWARE_SET:
        return True

    # --- Try modern (Windows 8.1+) API ---
    # PROCESS_PER_MONITOR_DPI_AWARE = 2  (handles DPI changes per monitor)
    # PROCESS_SYSTEM_DPI_AWARE      = 1  (primary monitor DPI at startup)
    # We use SYSTEM_DPI_AWARE so pyautogui does not need to handle
    # runtime DPI changes triggered by monitor reconfiguration.
    for aw in (1, 2):
        try:
            windll = ctypes.windll.shcore
            windll.SetProcessDpiAwareness(aw)
            _DPI_AWARE_SET = True
            if aw == 1:
                print(f"[DPI] PROCESS_SYSTEM_DPI_AWARE enabled")
            else:
                print(f"[DPI] PROCESS_PER_MONITOR_DPI_AWARE enabled")
            return True
        except AttributeError:
            # shcore not available — fall through to legacy API
            break
        except Exception:
            # shcore available but call failed — try next level
            continue

    # --- Legacy fallback (Vista / Win7) ---
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        _DPI_AWARE_SET = True
        print("[DPI] SetProcessDPIAware (legacy) enabled")
        return True
    except Exception:
        pass

    print("[DPI] WARNING — Could not set DPI awareness; coordinate "
          "spaces may be inconsistent on high-DPI displays.")
    return False


_set_dpi_awareness()
