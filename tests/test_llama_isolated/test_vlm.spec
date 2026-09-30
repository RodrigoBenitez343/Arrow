# -*- mode: python ; coding: utf-8 -*-
"""
Minimal PyInstaller spec for llama-server spawn test.

This bundles just enough to test whether llama-server.exe can start
from inside a compiled PyInstaller environment, then run a vision
inference with a screenshot.
"""
import os
import sys

_base = os.path.abspath(SPECPATH)  # d:\LoOperV2\test_llama_isolated
_test_dir = _base  # test_vlm.py is right here
_looper_dir = os.path.join(os.path.dirname(_base), "LoOper")  # d:\LoOperV2\LoOper

# ── Source paths ──────────────────────────────────────────────────
_llama_server_src = os.path.join(_looper_dir, "AI", "bin", "llama-server.exe")
_model_dir = os.path.join(_looper_dir, "AI", "models", "LFM2.5-VL-450M-GGUF")

# ── Collect binaries ──────────────────────────────────────────────
binaries = []
if os.path.exists(_llama_server_src):
    binaries.append((_llama_server_src, "bin"))
    print(f"Bundling llama-server.exe: {_llama_server_src}")
else:
    print("WARNING: llama-server.exe not found — will rely on system/WinGet")

# ── Collect model data ────────────────────────────────────────────
datas = []
if os.path.isdir(_model_dir):
    for _f in os.listdir(_model_dir):
        _fp = os.path.join(_model_dir, _f)
        if os.path.isfile(_fp):
            datas.append((_fp, os.path.join("models", "LFM2.5-VL-450M-GGUF")))
    print(f"Bundling {len(datas)} model file(s) from {_model_dir}")
else:
    print(f"WARNING: Model dir not found: {_model_dir}")

# ── VC++ runtime DLLs (ensure child process can find them) ────────
# The PyInstaller bootloader bundles these automatically from the
# Python installation, but we ensure they end up in the root of
# _internal where they're found by DLL search rule #1.
_vc_dlls = ["msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
_sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
for _dll in _vc_dlls:
    _src = os.path.join(_sys32, _dll)
    if os.path.exists(_src):
        if not any(os.path.basename(b[0]).lower() == _dll.lower() for b in binaries):
            binaries.append((_src, "."))
        print(f"Bundling {_dll} from System32")

# ── Hidden imports ────────────────────────────────────────────────
hiddenimports = [
    "requests",
    "PIL",
    "PIL.Image",
    "PIL.ImageGrab",
    "mss",
    "json",
    "base64",
    "shutil",
]

a = Analysis(
    [os.path.join(_test_dir, "test_vlm.py")],
    pathex=[_test_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="test_vlm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="test_vlm",
)
