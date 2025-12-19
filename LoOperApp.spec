import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_all

try:
    base_dir = os.path.abspath(SPECPATH)
except Exception:
    base_dir = os.getcwd()
looper_dir = os.path.join(base_dir, 'LoOper')
ai_dir = os.path.join(looper_dir, 'AI')
ngui_dir = os.path.join(looper_dir, 'NGUI')
player_dir = os.path.join(looper_dir, 'player')

for p in (base_dir, looper_dir, ai_dir, ngui_dir, player_dir):
    if p and p not in sys.path:
        sys.path.insert(0, p)

pathex = [looper_dir, ai_dir, ngui_dir, player_dir]

hiddenimports = [
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.QtSvg',
    'NodeGraphQt',
    'pytablericons',
    'PIL.ImageQt',
    'model_vision',
    'object',
    'ultralytics',
    'torchvision',
    'intel_openmp',
    'mkl',
    'psutil',
    'win32gui',
    'win32process',
    'fastapi',
    'uvicorn',
    'pydantic',
    'email_validator',
    'starlette',
    'requests',
    'h11',
    'click',
    'anyio',
    'typing_extensions',
    'json',
    'base64',
    'asyncio',
    'threading',
    'logging',
    'pynput',
    'pynput.keyboard._win32',
    'pynput.mouse._win32',
    'six',
    'pyautogui',
    'pyscreeze',
    'pygetwindow',
    'pymsgbox',
    'pytweening',
    'mouseinfo',
    'numpy',
    'cv2',
    'pytesseract',
]

datas = []
binaries = []

# Use collect_all for complex packages
for pkg in ['torch', 'torchvision', 'ultralytics', 'NodeGraphQt']:
    tmp_ret = collect_all(pkg, include_py_files=False)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]

# Add specific files
datas += [(os.path.join(looper_dir, 'data'), 'data')]
datas += [(os.path.join(looper_dir, 'player', 'Computer_vision', 'Computer_Vision_1.5.3.onnx'), 'player/Computer_vision')]
datas += [(os.path.join(looper_dir, 'LoOper.ico'), '.')]
datas += [(os.path.join(looper_dir, 'LoOper.png'), '.')]

# Add Redist DLLs to multiple locations for maximum compatibility
venv_site_packages = os.path.join(base_dir, '.venv', 'Lib', 'site-packages')
redist_dlls = ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140_1.dll', 'libiomp5md.dll']
for root, dirs, files in os.walk(venv_site_packages):
    for file in files:
        if file.lower() in redist_dlls:
            src = os.path.join(root, file)
            # Add to root
            if not any(os.path.basename(b[0]).lower() == file.lower() and b[1] == '.' for b in binaries):
                binaries.append((src, '.'))
            # Add to torch/lib specifically
            if not any(os.path.basename(b[0]).lower() == file.lower() and b[1] == 'torch/lib' for b in binaries):
                binaries.append((src, 'torch/lib'))

block_cipher = None

runtime_hooks = [os.path.join(base_dir, 'torch_runtime_hook.py')]

a = Analysis(
    [os.path.join(base_dir, 'LoOper.py')],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=['onnx', 'onnx.reference'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='LoOper',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=os.path.join(looper_dir, 'LoOper.ico'),
    exclude_binaries=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='LoOper',
)
