import os
import sys
import torch
# Pre-load torch DLLs to PATH to avoid WinError 127 during import in analysis
torch_lib = os.path.join(os.path.dirname(os.path.abspath(torch.__file__)), 'lib')
if os.path.exists(torch_lib):
    os.environ['PATH'] = torch_lib + os.pathsep + os.environ['PATH']

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
    'decorator',
    'astor',
    'lap',
]

datas = []
binaries = []

def _add_data(src, dest):
    if os.path.exists(src):
        datas.append((src, dest))

def _add_tree(src_dir, dest_root, exclude_rel_paths=None):
    if not src_dir or not os.path.isdir(src_dir):
        return
    exclude = {e.lower() for e in (exclude_rel_paths or [])}
    for root, dirs, files in os.walk(src_dir):
        rel_dir = os.path.relpath(root, src_dir)
        dest_dir = dest_root if rel_dir in ('.', '') else os.path.join(dest_root, rel_dir)
        for f in files:
            rel_file = f if rel_dir in ('.', '') else os.path.join(rel_dir, f)
            if rel_file.lower() in exclude:
                continue
            datas.append((os.path.join(root, f), dest_dir))

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
for pkg in ['torch', 'torchvision', 'ultralytics', 'NodeGraphQt', 'pynput', 'cv2', 'pyautogui', 'numpy', 'pytablericons', 'shapely', 'pyclipper', 'skimage', 'imgaug', 'lmdb', 'Cython', 'packaging', 'docx', 'onnxruntime']:
    try:
        tmp_ret = collect_all(pkg, include_py_files=False)
        datas += tmp_ret[0]
        binaries += tmp_ret[1]
        hiddenimports += tmp_ret[2]
    except ImportError:
        # Ignore if package is not installed (e.g. some optional deps)
        pass

def _add_binary(src, dest):
    if os.path.exists(src) and not any(os.path.normcase(b[0]) == os.path.normcase(src) and b[1] == dest for b in binaries):
        binaries.append((src, dest))

try:
    import onnxruntime as _onnxruntime
    _ort_dir = os.path.dirname(_onnxruntime.__file__)
    _ort_capi_dir = os.path.join(_ort_dir, 'capi')
    for _name in ('onnxruntime_pybind11_state.pyd', 'onnxruntime_providers_shared.dll', 'onnxruntime.dll'):
        _add_binary(os.path.join(_ort_capi_dir, _name), os.path.join('onnxruntime', 'capi'))
except Exception:
    pass

try:
    import sys as _sys
    _ort_dest = os.path.join('onnxruntime', 'capi')
    _ort_needed = [
        'onnxruntime_pybind11_state.pyd',
        'onnxruntime_providers_shared.dll',
        'onnxruntime.dll',
    ]
    _vc_needed = [
        'msvcp140.dll',
        'msvcp140_1.dll',
        'vcruntime140.dll',
        'vcruntime140_1.dll',
    ]

    for _name in _ort_needed:
        _src = os.path.join(_ort_capi_dir, _name)
        _add_binary(_src, _ort_dest)
        _add_binary(_src, '.')

    _bin_src_by_base = {}
    for _src, _dst, *_rest in binaries:
        _bin_src_by_base.setdefault(os.path.basename(_src).lower(), _src)

    _search_roots = [
        getattr(_sys, 'base_prefix', None),
        getattr(_sys, 'exec_prefix', None),
        os.path.dirname(_sys.executable),
    ]
    _search_dirs = []
    for _r in _search_roots:
        if not _r:
            continue
        _search_dirs.append(_r)
        _search_dirs.append(os.path.join(_r, 'DLLs'))
        _search_dirs.append(os.path.join(_r, 'Library', 'bin'))
    try:
        _sysroot = os.environ.get('SystemRoot') or r'C:\Windows'
        _search_dirs.append(os.path.join(_sysroot, 'System32'))
    except Exception:
        pass

    for _name in _vc_needed:
        _key = _name.lower()
        _src = _bin_src_by_base.get(_key)
        if not _src:
            for _d in _search_dirs:
                _cand = os.path.join(_d, _name)
                if os.path.exists(_cand):
                    _src = _cand
                    break
        if _src:
            _add_binary(_src, _ort_dest)
except Exception:
    pass

import importlib.util

paddleocr_spec = importlib.util.find_spec('paddleocr')
if paddleocr_spec and paddleocr_spec.origin:
    paddleocr_dir = os.path.dirname(paddleocr_spec.origin)
    paddleocr_init_src = os.path.join(paddleocr_dir, '__init__.py')
    paddleocr_patched_dir = os.path.join(base_dir, 'build', '_patched', 'paddleocr')
    os.makedirs(paddleocr_patched_dir, exist_ok=True)
    paddleocr_patched_init = os.path.join(paddleocr_patched_dir, '__init__.py')
    try:
        with open(paddleocr_init_src, 'r', encoding='utf-8') as f:
            paddleocr_init_txt = f.read()
        if "import torch" not in paddleocr_init_txt:
            paddleocr_init_txt = paddleocr_init_txt.replace(
                "from .paddleocr import (",
                "import torch\nfrom .paddleocr import (",
                1,
            )
        with open(paddleocr_patched_init, 'w', encoding='utf-8') as f:
            f.write(paddleocr_init_txt)
        _add_tree(paddleocr_dir, 'paddleocr', exclude_rel_paths={'__init__.py'})
        datas.append((paddleocr_patched_init, 'paddleocr'))
    except Exception:
        _add_tree(paddleocr_dir, 'paddleocr')

paddle_spec = importlib.util.find_spec('paddle')
if paddle_spec and paddle_spec.origin:
    paddle_dir = os.path.dirname(paddle_spec.origin)
    paddle_init_src = os.path.join(paddle_dir, '__init__.py')
    paddle_pir_init_src = os.path.join(paddle_dir, 'pir', '__init__.py')
    patched_dir = os.path.join(base_dir, 'build', '_patched', 'paddle')
    os.makedirs(patched_dir, exist_ok=True)
    patched_paddle_init = os.path.join(patched_dir, '__init__.py')
    patched_paddle_pir_dir = os.path.join(patched_dir, 'pir')
    os.makedirs(patched_paddle_pir_dir, exist_ok=True)
    patched_paddle_pir_init = os.path.join(patched_paddle_pir_dir, '__init__.py')
    pb2_rel_paths = {
        os.path.join('base', 'proto', 'distributed_strategy_pb2.py'),
        os.path.join('distributed', 'fleet', 'proto', 'distributed_strategy_pb2.py'),
    }
    def _patch_pb2_addserializedfile(pb2_src: str, pb2_dst: str, proto_name: str) -> bool:
        try:
            with open(pb2_src, 'r', encoding='utf-8') as f:
                txt = f.read()
            marker = "DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile("
            if marker in txt and "FindFileByName(" not in txt:
                lines = txt.splitlines(True)
                for i, line in enumerate(lines):
                    if marker in line:
                        add_line = line.rstrip('\n')
                        replacement = (
                            "try:\n"
                            f"    {add_line}\n"
                            "except TypeError as _e:\n"
                            "    if 'duplicate file name' in str(_e):\n"
                            f"        DESCRIPTOR = _descriptor_pool.Default().FindFileByName('{proto_name}')\n"
                            "    else:\n"
                            "        raise\n"
                        )
                        lines[i] = replacement
                        txt = ''.join(lines)
                        break
            os.makedirs(os.path.dirname(pb2_dst), exist_ok=True)
            with open(pb2_dst, 'w', encoding='utf-8') as f:
                f.write(txt)
            return True
        except Exception:
            return False
    try:
        with open(paddle_init_src, 'r', encoding='utf-8') as f:
            paddle_init_txt = f.read()
        base_needle = "from .base import core  # noqa: F401\n"
        base_insert = base_needle + "from . import base as base\n"
        if "from . import base as base" not in paddle_init_txt and base_needle in paddle_init_txt:
            paddle_init_txt = paddle_init_txt.replace(base_needle, base_insert, 1)
        needle = "from .pir import monkey_patch_dtype, monkey_patch_program, monkey_patch_value\n\n"
        insert = needle + "from . import framework as framework\nfrom . import tensor as tensor\n\n"
        if "from . import framework as framework" not in paddle_init_txt:
            paddle_init_txt = paddle_init_txt.replace(needle, insert, 1)
        with open(patched_paddle_init, 'w', encoding='utf-8') as f:
            f.write(paddle_init_txt)
        try:
            with open(paddle_pir_init_src, 'r', encoding='utf-8') as f:
                paddle_pir_init_txt = f.read()
            pir_needle = "from paddle.base.libpaddle.pir import (  # noqa: F401\n"
            pir_insert = (
                "import sys as _sys\n"
                "from paddle.base import libpaddle as _libpaddle\n"
                "if 'paddle.base.libpaddle.pir' not in _sys.modules and hasattr(_libpaddle, 'pir'):\n"
                "    _sys.modules['paddle.base.libpaddle.pir'] = _libpaddle.pir\n\n"
                + pir_needle
            )
            if "paddle.base.libpaddle.pir' not in _sys.modules" not in paddle_pir_init_txt and pir_needle in paddle_pir_init_txt:
                paddle_pir_init_txt = paddle_pir_init_txt.replace(pir_needle, pir_insert, 1)
            with open(patched_paddle_pir_init, 'w', encoding='utf-8') as f:
                f.write(paddle_pir_init_txt)
            _add_tree(
                paddle_dir,
                'paddle',
                exclude_rel_paths={'__init__.py', os.path.join('pir', '__init__.py')} | pb2_rel_paths,
            )
            datas.append((patched_paddle_pir_init, os.path.join('paddle', 'pir')))
        except Exception:
            _add_tree(paddle_dir, 'paddle', exclude_rel_paths={'__init__.py'} | pb2_rel_paths)
        datas.append((patched_paddle_init, 'paddle'))
    except Exception:
        _add_tree(paddle_dir, 'paddle', exclude_rel_paths=pb2_rel_paths)
    try:
        proto_name = 'paddle/fluid/framework/distributed_strategy.proto'
        src_base_pb2 = os.path.join(paddle_dir, 'base', 'proto', 'distributed_strategy_pb2.py')
        dst_base_pb2 = os.path.join(patched_dir, 'base', 'proto', 'distributed_strategy_pb2.py')
        if _patch_pb2_addserializedfile(src_base_pb2, dst_base_pb2, proto_name):
            datas.append((dst_base_pb2, os.path.join('paddle', 'base', 'proto')))
        elif os.path.exists(src_base_pb2):
            datas.append((src_base_pb2, os.path.join('paddle', 'base', 'proto')))
        src_fleet_pb2 = os.path.join(paddle_dir, 'distributed', 'fleet', 'proto', 'distributed_strategy_pb2.py')
        dst_fleet_pb2 = os.path.join(patched_dir, 'distributed', 'fleet', 'proto', 'distributed_strategy_pb2.py')
        if _patch_pb2_addserializedfile(src_fleet_pb2, dst_fleet_pb2, proto_name):
            datas.append((dst_fleet_pb2, os.path.join('paddle', 'distributed', 'fleet', 'proto')))
        elif os.path.exists(src_fleet_pb2):
            datas.append((src_fleet_pb2, os.path.join('paddle', 'distributed', 'fleet', 'proto')))
    except Exception:
        pass
    paddle_libs_dir = os.path.join(paddle_dir, 'libs')
    _add_tree(paddle_libs_dir, os.path.join('paddle', 'libs'), exclude_rel_paths={'libiomp5md.dll'})

setuptools_spec = importlib.util.find_spec('setuptools')
if setuptools_spec and setuptools_spec.origin:
    _add_tree(os.path.dirname(setuptools_spec.origin), 'setuptools')

pkg_resources_spec = importlib.util.find_spec('pkg_resources')
if pkg_resources_spec and pkg_resources_spec.origin:
    _add_tree(os.path.dirname(pkg_resources_spec.origin), 'pkg_resources')

# Remove manual _add_pkg_data calls as collect_all(include_py_files=True) should handle it
# _add_pkg_data('paddleocr', 'tools', 'tools')
# _add_pkg_data('paddleocr', 'ppocr', 'ppocr')
# _add_pkg_data('paddleocr', 'ppstructure', 'ppstructure')

# Add specific files
_add_data(os.path.join(looper_dir, 'data'), '_internal/arrow/data')
_add_data(os.path.join(looper_dir, 'data'), 'data')
_add_data(os.path.join(ai_dir, 'config.json'), '_internal/arrow/AI')
_add_data(os.path.join(ai_dir, 'OverWatch', 'config', 'config.json'), '_internal/arrow/AI/OverWatch/config')
_add_data(os.path.join(looper_dir, 'schedules.json'), 'schedules.json')
_add_data(os.path.join(looper_dir, 'schedules.json'), '_internal/arrow/schedules.json')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), '.')
_add_data(os.path.join(looper_dir, 'LoOper.png'), '.')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), '_internal')
_add_data(os.path.join(looper_dir, 'LoOper.png'), '_internal')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), '_internal/arrow')
_add_data(os.path.join(looper_dir, 'LoOper.png'), '_internal/arrow')
_add_data(os.path.join(looper_dir, 'LoOper.ico'), 'arrow')
_add_data(os.path.join(looper_dir, 'LoOper.png'), 'arrow')

# Bundle the lightweight sandbox agent bridge so it is available as a loose file
# in compiled builds.  The RDP session runs ONLY this script (via a scheduled task)
# – never the full arrow GUI.
_add_data(os.path.join(looper_dir, 'sandbox_agent.py'), 'arrow')

# Add the entire Computer_vision directory as a tree to ensure all files (including .py) are present
_add_tree(os.path.join(player_dir, 'Computer_vision'), '_internal/arrow/player/Computer_vision')
_add_tree(os.path.join(player_dir, 'Computer_vision'), 'arrow/player/Computer_vision')

# Bundle the models found in the local user's .paddleocr directory
user_home = os.path.expanduser('~')
paddle_whl = os.path.join(user_home, '.paddleocr', 'whl')

# Detection model (usually language-independent or English default)
det_src = os.path.join(paddle_whl, 'det', 'en', 'en_PP-OCRv3_det_infer')
if os.path.exists(det_src):
    _add_data(det_src, '_internal/paddle_models/det')

# Recognition model (English)
rec_src = os.path.join(paddle_whl, 'rec', 'en', 'en_PP-OCRv4_rec_infer')
if os.path.exists(rec_src):
    _add_data(rec_src, '_internal/paddle_models/rec')

# Classification model (Angle classifier)
cls_src = os.path.join(paddle_whl, 'cls', 'ch_ppocr_mobile_v2.0_cls_infer')
if os.path.exists(cls_src):
    _add_data(cls_src, '_internal/paddle_models/cls')


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

runtime_hooks = [
    os.path.join(base_dir, 'onnxruntime_runtime_hook.py'),
    os.path.join(base_dir, 'torch_runtime_hook.py'),
]

# PyInstaller 6+ uses a dedicated contents directory (default: "_internal").
# Many entries in this spec already prefix destinations with "_internal/...".
# That results in a duplicated layout like "_internal/_internal/..." at runtime.
def _strip_contents_dir_prefix(dest: str) -> str:
    if not dest:
        return dest
    d = str(dest).replace("\\", "/")
    while d.startswith("./"):
        d = d[2:]
    if d.lower() == "_internal":
        return "."
    if d.lower().startswith("_internal/"):
        d = d[len("_internal/"):]
    return d.replace("/", os.sep)

# Ensure libiomp5md.dll is NOT in paddle/libs to avoid conflict with Torch's version
print("DEBUG: Filtering datas for libiomp5md.dll...")
pre_len = len(datas)
datas = [d for d in datas if not (os.path.basename(d[0]).lower() == 'libiomp5md.dll' and 'paddle' in d[1].lower())]
post_len = len(datas)
print(f"DEBUG: Filtered {pre_len - post_len} entries. Remaining datas len: {post_len}")

# Normalize destinations to avoid "_internal/_internal" nesting in the final dist.
datas = [(src, _strip_contents_dir_prefix(dst)) for (src, dst) in datas]

_normalized_binaries = []
for b in binaries:
    try:
        if len(b) == 2:
            _normalized_binaries.append((b[0], _strip_contents_dir_prefix(b[1])))
        elif len(b) >= 3:
            _normalized_binaries.append((b[0], _strip_contents_dir_prefix(b[1]), b[2]))
        else:
            _normalized_binaries.append(b)
    except Exception:
        _normalized_binaries.append(b)
binaries = _normalized_binaries

a = Analysis(
    [os.path.join(base_dir, 'LoOper.py')],
    pathex=pathex,
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[base_dir],
    hooksconfig={},
    runtime_hooks=runtime_hooks,
    excludes=['onnx', 'onnx.reference', 'paddleocr', 'paddle'],
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
    name='arrow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    name='arrow',
)
