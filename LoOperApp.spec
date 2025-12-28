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
recorder_dir = os.path.join(looper_dir, 'recorder')

for p in (base_dir, looper_dir, ai_dir, ngui_dir, player_dir, recorder_dir):
    if p and p not in sys.path:
        sys.path.insert(0, p)

pathex = [looper_dir, ai_dir, ngui_dir, player_dir, recorder_dir]

hiddenimports = [
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.QtSvg',
    'PyQt5.QtPrintSupport',
    'PyQt5.QtOpenGL',
    'NodeGraphQt',
    'pytablericons',
    'PIL.ImageQt',
    'aiohttp',
    'ollama',
    'LoOper',
    'LoOper.AI',
    'LoOper.NGUI',
    'LoOper.player',
    'LoOper.recorder',
    'model_vision',
    'object',
    'LoOper.player.Computer_vision.model_vision',
    'LoOper.player.Computer_vision.object',
    'ultralytics',
    'torchvision',
    'intel_openmp',
    'mkl',
    'psutil',
    'win32gui',
    'win32process',
    'fastapi',
    'uvicorn',
    'uvicorn.logging',
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
    'paddle',
    'paddleocr',
]

datas = []
binaries = []

def _add_data(src, dest):
    if os.path.exists(src):
        datas.append((src, dest))

def _add_pkg_data(pkg_name, src_relative, dest_relative):
    import importlib.util
    try:
        spec = importlib.util.find_spec(pkg_name)
        if spec and spec.origin:
            pkg_path = os.path.dirname(spec.origin)
            src_path = os.path.join(pkg_path, src_relative)
            if os.path.exists(src_path):
                datas.append((src_path, os.path.join('_internal', pkg_name, dest_relative)))
    except Exception:
        pass

# Use collect_all for complex packages
# Note: paddleocr and paddle need to include python files because they do dynamic imports of source files
for pkg in ['torch', 'torchvision', 'ultralytics', 'NodeGraphQt', 'pynput', 'cv2', 'pyautogui', 'numpy', 'pytablericons', 'shapely', 'pyclipper', 'skimage', 'imgaug', 'lmdb', 'Cython', 'packaging', 'docx']:
    try:
        tmp_ret = collect_all(pkg, include_py_files=False)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except ImportError:
        # Ignore if package is not installed (e.g. some optional deps)
        pass

# Handle paddle packages specifically to include source files
# paddleocr MUST be excluded from PYZ (zipped) because it uses __file__ and spec_from_file_location
# which requires real files on disk. We force it to be a regular directory in _internal.
paddleocr_ret = collect_all('paddleocr', include_py_files=True)
datas += paddleocr_ret[0]
binaries += paddleocr_ret[1]
# Add dependencies but NOT paddleocr itself to hiddenimports
hiddenimports += [h for h in paddleocr_ret[2] if not h.startswith('paddleocr')]

# Handle paddle (keep it in PYZ for now unless issues arise, but include sources)
try:
    tmp_ret = collect_all('paddle', include_py_files=True)
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]
except ImportError:
    pass

# Remove manual _add_pkg_data calls as collect_all(include_py_files=True) should handle it
# _add_pkg_data('paddleocr', 'tools', 'tools')
# _add_pkg_data('paddleocr', 'ppocr', 'ppocr')
# _add_pkg_data('paddleocr', 'ppstructure', 'ppstructure')

# Add specific files
_add_data(os.path.join(looper_dir, 'data'), '_internal/LoOper/data')
_add_data(os.path.join(looper_dir, 'data'), 'data')
_add_data(os.path.join(player_dir, 'Computer_vision', 'Computer_Vision_1.5.3.onnx'), '_internal/LoOper/player/Computer_vision')
_add_data(os.path.join(player_dir, 'Computer_vision', 'label_description.json'), '_internal/LoOper/player/Computer_vision')
_add_data(os.path.join(player_dir, 'Computer_vision', 'view_output.json'), '_internal/LoOper/player/Computer_vision')
_add_data(os.path.join(ai_dir, 'config.json'), '_internal/LoOper/AI')
_add_data(os.path.join(ai_dir, 'OverWatch', 'config', 'config.json'), '_internal/LoOper/AI/OverWatch/config')
_add_data(os.path.join(looper_dir, 'schedules.json'), 'schedules.json')
_add_data(os.path.join(looper_dir, 'schedules.json'), '_internal/LoOper/schedules.json')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), '.')
_add_data(os.path.join(looper_dir, 'LoOper.png'), '.')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), '_internal')
_add_data(os.path.join(looper_dir, 'LoOper.png'), '_internal')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), '_internal/LoOper')
_add_data(os.path.join(looper_dir, 'LoOper.png'), '_internal/LoOper')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), 'LoOper')
_add_data(os.path.join(looper_dir, 'LoOper.png'), 'LoOper')

# Add Redist DLLs to multiple locations for maximum compatibility
candidate_site_packages = [
    os.path.join(base_dir, '.venv', 'Lib', 'site-packages'),
    os.path.join(base_dir, 'LoOper', 'runtime', 'venvs', 'Temp', 'Lib', 'site-packages'),
    os.path.join(sys.prefix, 'Lib', 'site-packages'),
]
redist_dlls = ['msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140_1.dll']
for venv_site_packages in candidate_site_packages:
    if not venv_site_packages or not os.path.isdir(venv_site_packages):
        continue
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

def _remove_binary_by_basename(name: str):
    name_l = str(name).lower()
    return [b for b in binaries if os.path.basename(b[0]).lower() != name_l]

try:
    import torch as _torch
    _torch_lib_dir = os.path.join(os.path.dirname(_torch.__file__), 'lib')
    _torch_libiomp = os.path.join(_torch_lib_dir, 'libiomp5md.dll')
except Exception:
    _torch_libiomp = None

binaries[:] = _remove_binary_by_basename('libiomp5md.dll')
if _torch_libiomp and os.path.exists(_torch_libiomp):
    binaries.append((_torch_libiomp, '.'))
    binaries.append((_torch_libiomp, 'torch/lib'))

block_cipher = None

runtime_hooks = [os.path.join(base_dir, 'torch_runtime_hook.py')]

a = Analysis(
    [os.path.join(looper_dir, 'main.py')],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=['onnx', 'onnx.reference', 'paddleocr'],
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
