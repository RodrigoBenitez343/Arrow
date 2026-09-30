import os
import sys
import ctypes

_dll_dir_handles = []

os.environ.setdefault("YOLO_AUTOINSTALL", "false")

exe_dir = os.path.dirname(sys.executable)
meipass = getattr(sys, "_MEIPASS", None)
internal_dir = meipass or exe_dir
if internal_dir and os.path.basename(internal_dir).lower() != "_internal":
    candidate_internal = os.path.join(exe_dir, "_internal")
    if os.path.isdir(candidate_internal):
        internal_dir = candidate_internal
    else:
        candidate_internal = os.path.join(internal_dir, "_internal")
        if os.path.isdir(candidate_internal):
            internal_dir = candidate_internal

capi_dir = os.path.join(internal_dir, "onnxruntime", "capi")
if os.path.isdir(capi_dir):
    if hasattr(os, "add_dll_directory"):
        try:
            _dll_dir_handles.append(os.add_dll_directory(capi_dir))
        except Exception:
            pass
    os.environ["PATH"] = capi_dir + os.pathsep + os.environ.get("PATH", "")
    for _name in ("onnxruntime_providers_shared.dll", "onnxruntime.dll"):
        try:
            ctypes.CDLL(os.path.join(capi_dir, _name))
        except Exception:
            pass
