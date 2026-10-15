import os
import sys

# ponytail: prevent OpenMP DLL conflict crash in PyInstaller isolated child
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import torch
# Pre-load torch DLLs to PATH to avoid WinError 127 during import in analysis
torch_lib = os.path.join(os.path.dirname(os.path.abspath(torch.__file__)), 'lib')
if os.path.exists(torch_lib):
    os.environ['PATH'] = torch_lib + os.pathsep + os.environ['PATH']

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_all

try:
    pipeline_dir = os.path.abspath(SPECPATH)
except Exception:
    pipeline_dir = os.getcwd()
# The spec lives in build_pipeline/; project sources (LoOper/, utils/, .venv)
# stay at the repo root, one level up.
base_dir = os.path.abspath(os.path.join(pipeline_dir, os.pardir))
looper_dir = os.path.join(base_dir, 'LoOper')
ai_dir = os.path.join(looper_dir, 'AI')
ngui_dir = os.path.join(looper_dir, 'NGUI')
player_dir = os.path.join(looper_dir, 'player')
recorder_dir = os.path.join(looper_dir, 'recorder')

for p in (base_dir, looper_dir, ai_dir, ngui_dir, player_dir, recorder_dir):
    if p and p not in sys.path:
        sys.path.insert(0, p)

pathex = [looper_dir, ai_dir, ngui_dir, player_dir, recorder_dir,
         os.path.join(base_dir, 'utils', 'nano-graphrag')]

hiddenimports = [
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.QtSvg',
    'PyQt5.QtPrintSupport',
    'PyQt5.QtOpenGL',
    # Interactive HTML artifacts: the agent overlay embeds a QWebEngineView.
    'PyQt5.QtWebEngineWidgets',
    'PyQt5.QtWebEngineCore',
    'PyQt5.QtWebEngine',
    'PyQt5.QtWebChannel',
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
    'mss',
    'numpy',
    'cv2',
    'decorator',
    'astor',
    'lap',

    # Web automation (WebSequenceNode): DOM-based browser record/replay
    'LoOper.player.web',
    'undetected_chromedriver',
    'selenium',
    'keyboard',

    # Agent-mode voice (STT/TTS): agent_overlay imports player.stt_engine via
    # in-function imports that modulegraph can miss on partial graphs - pin
    # the whole chain (engine modules, vosk pyd, pyaudio) explicitly so the
    # compiled app always ships offline speech recognition.
    'NGUI.widgets.agent_overlay',
    'player.stt_engine',
    'player.tts_engine',
    'vosk',
    'pyaudio',
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
for pkg in ['torch', 'torchvision', 'ultralytics', 'NodeGraphQt', 'pynput', 'cv2', 'pyautogui', 'numpy', 'pytablericons', 'shapely', 'pyclipper', 'skimage', 'imgaug', 'lmdb', 'Cython', 'packaging', 'docx', 'onnxruntime', 'piper',
             'sentence_transformers', 'transformers', 'scikit-learn', 'tiktoken', 'undetected_chromedriver', 'vosk']:
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
# LoOper/data is shipped once under 'data' (resolved at runtime as
# _MEIPASS/data/piper_voices by tts_engine.py).  The historical duplicate
# '_internal/arrow/data' entry was removed: nothing references arrow/data at
# runtime and it doubled the MSI payload (voices + vosk models + context.db).
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
_add_data(os.path.join(looper_dir, 'sandbox_agent.py'), '.')

# Web automation (WebSequenceNode): the injected JS payload is read via
# Path(__file__).parent / "inject" at runtime, so it must land next to the
# player.web module inside _MEIPASS.  The web recorder/engine modules are
# imported as TOP-LEVEL `player.web.*` (see NGUI web_sequence.py), so in a
# frozen build __file__ is _internal/player/web/recorder.py and the inject
# dir must exist at _internal/player/web/inject.  A second copy under
# _internal/LoOper/player/web/inject covers the `LoOper.player.web` import
# style used by the rest of the player code.
_web_inject_dir = os.path.join(player_dir, 'web', 'inject')
if os.path.isdir(_web_inject_dir):
    _add_data(_web_inject_dir, os.path.join('LoOper', 'player', 'web', 'inject'))
    _add_data(_web_inject_dir, os.path.join('player', 'web', 'inject'))

# Bundle the isolated OCR worker as a loose file: paddle OCR runs in a
# subprocess started from this .py, which must exist on disk.  player/*.py are
# otherwise compiled into the PYZ (not shipped loose), so without this the
# compiled build's OCR silently returns empty.  Dest covers both import styles
# (top-level `player` and `LoOper.player`).
_add_data(os.path.join(player_dir, 'ocr_worker.py'), 'player')
_add_data(os.path.join(player_dir, 'ocr_worker.py'), os.path.join('LoOper', 'player'))

# Ensure AI/models directory exists in the installer for llama.cpp GGUF models.
# The directory ships with a README placeholder; users place .gguf files here.
_add_data(os.path.join(ai_dir, 'models', 'README.txt'), 'AI/models')

# NOTE: GGUF models are NOT bundled anymore.  They are provisioned on first
# run by the lazy worker in AI/model_downloads.py (started at app launch and
# by the MSI via `arrow.exe --install-deps`), which downloads every missing
# model one at a time into the app's AI/models dir.  This keeps the MSI small
# and avoids the WiX 2 GB single-file limit entirely.

# Bundle the Needle 2 tool-calling engine (windows-x86_64 needle.exe, ~15 MB,
# Apache-2.0, self-contained — model baked in).  The Code Node Studio uses it
# as the deterministic dev-tool router (search_code / read_code / run_node).
# SHA-256 (windows-x86_64 needle.exe):
#   93EA7AE8C9EA92B746F41668D87597463BEE0D6F6AE8AB4E855EC8B396556740
_needle2 = os.path.join(ai_dir, 'models', 'needle2', 'needle.exe')
if os.path.exists(_needle2):
    _add_data(_needle2, 'AI/models/needle2')
    print(f'Bundling needle2 tool-calling engine: {_needle2}')
else:
    print('NOTE: LoOper/AI/models/needle2/needle.exe not found — Code Node '
          'Studio dev-tool routing falls back to the text protocol')

# Bundle the embedded Laya System-1 decision engine BINARY only
# (LoOper/AI/bin/laya.exe, ~1.7 MB self-contained CPU build).  Its English
# GGUF quants are NOT bundled - the lazy worker in AI/model_downloads.py
# downloads them on first run (default ud_q4_k_m, fallbacks in
# AI/laya_client.py).
_laya_exe = os.path.join(ai_dir, 'bin', 'laya.exe')
if os.path.exists(_laya_exe):
    print(f'Bundling Laya engine: {_laya_exe}')
else:
    print('NOTE: LoOper/AI/bin/laya.exe not found — the embedded Laya '
          'engine is unavailable; every Laya consumer uses its LLM fallback')

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

# ------------------------------------------------------------------
# Bundle ALL binaries from LoOper/AI/bin/ (llama-server.exe,
# llama-cli.exe, llama-mtmd-cli.exe, their *-impl.dll dependencies,
# plus ggml*.dll, llama.dll, llama-common.dll, mtmd.dll).  The fork
# (yuuko-eth/llama.cpp @ mtmd-grounders) uses a DLL-based architecture:
# EXEs are thin stubs that load *-impl.dll, which depends on llama.dll,
# llama-common.dll, ggml*.dll, etc.  All must live in the same directory
# for Windows DLL search.
#
# llama-cli.exe is included so runtime subprocess launch via
# llama-cli --mtmd can use --mmproj directly.
# ------------------------------------------------------------------
_bin_dir = os.path.join(ai_dir, 'bin')
if os.path.isdir(_bin_dir):
    for _f in sorted(os.listdir(_bin_dir)):
        # The llama.cpp ENGINE (llama*, ggml*, mtmd*) is NOT bundled: it is built
        # ON THE HOST by setup_llama_engine.ps1 at install time, so no CPU
        # instruction set is baked at build time.  Only ship the non-engine
        # binaries (e.g. laya.exe) here.
        if _f.lower().startswith(('llama', 'ggml', 'mtmd')):
            continue
        _src = os.path.join(_bin_dir, _f)
        if os.path.isfile(_src):
            binaries.append((_src, 'bin'))
    print(f"Bundled {len([b for b in binaries if b[1] == 'bin'])} files from {_bin_dir} (engine excluded)")
else:
    print("NOTE: LoOper/AI/bin/ not found — local inference will require manual install")

# Bundle the Chrome for Testing fallback browser (web sequence nodes) when
# build_msi.ps1 staged it under LoOper/AI/bin/chrome/.  Web record/replay
# prefers this bundled browser + matching chromedriver in frozen builds, so
# web nodes work offline on machines without Chrome installed.  Lands at
# _internal/bin/chrome/ (session.py resolves _MEIPASS/bin/chrome).
_chrome_bundle_dir = os.path.join(_bin_dir, 'chrome')
if os.path.isdir(_chrome_bundle_dir) and os.path.exists(os.path.join(_chrome_bundle_dir, 'chrome.exe')):
    _add_tree(_chrome_bundle_dir, os.path.join('bin', 'chrome'))
    print(f"Bundling Chrome for Testing from {_chrome_bundle_dir}")
else:
    print("NOTE: LoOper/AI/bin/chrome not staged — web nodes use the system Chrome")

# Bundle VC++ runtime DLLs from System32 to _internal/ root.
# CRITICAL: Remove venv-bundled copies first — they are older versions
# that can't support llama-server.exe compiled with MSVC 19.44.
# The venv vcruntime140.dll (101KB) vs System32 (123KB) mismatch
# causes STATUS_ACCESS_VIOLATION (0xC0000005) on spawn.
_sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
_vc_dlls = ["msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
for _dll in _vc_dlls:
    # Remove existing ROOT entry for this DLL (venv/older version)
    # but leave torch/lib copies alone — they were added by torch hook
    binaries[:] = [b for b in binaries if not (
        os.path.basename(b[0]).lower() == _dll.lower() and b[1] == '.'
    )]
    _src = os.path.join(_sys32, _dll)
    if os.path.exists(_src):
        binaries.append((_src, '.'))
        # ALSO drop a copy beside the llama.cpp EXEs.  Those run as PLAIN child
        # processes (subprocess.Popen), so PyInstaller's os.add_dll_directory()
        # for the MAIN process does NOT apply to them: on a machine without the
        # VC++ redistributable the child cannot find vcruntime140_1.dll and
        # dies with 0xC0000135 (STATUS_DLL_NOT_FOUND).  The exe's own dir IS
        # searched first, so "bin" makes llama-server/llama-mtmd-cli start.
        binaries.append((_src, 'bin'))
        print(f"Bundling {_dll} from System32 ({os.path.getsize(_src):,} bytes)")

block_cipher = None

# --- Vulkan loader (only if a llama.cpp engine is actually bundled) --------
# ggml-vulkan.dll STATICALLY imports the Vulkan loader vulkan-1.dll (reverify).
# The engine is normally NOT bundled - it is built on the host at install time -
# so this only matters if an engine happens to be present in the payload.
_sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
if any(os.path.basename(b[0]).lower() == 'ggml-vulkan.dll' for b in binaries):
    _vk_loader = os.path.join(_sys32, "vulkan-1.dll")
    if os.path.exists(_vk_loader):
        binaries.append((_vk_loader, 'bin'))
        print(f"Bundling vulkan-1.dll from System32 ({os.path.getsize(_vk_loader):,} bytes)")
    else:
        binaries[:] = [b for b in binaries if os.path.basename(b[0]).lower() != 'ggml-vulkan.dll']
        print("WARNING: vulkan-1.dll not found - dropped ggml-vulkan.dll (CPU-only)")
else:
    print("NOTE: llama.cpp engine not bundled - Vulkan loader not needed (built on host)")

# --- Optional Windows-feature DLLs (stripped / N-edition images) ----------
# cv2.pyd STATICALLY imports the Media Foundation stack (reverify: MF.dll +
# MFReadWrite.dll) alongside dxgi/d3d11.  Stripped and "N"/KN Windows images
# routinely REMOVE Media Foundation, so cv2.pyd cannot load and the whole vision
# stack goes dark ("OpenCV not available").  MF.dll / MFReadWrite.dll /
# mfplat.dll / mfcore.dll import ONLY msvcrt.dll + Windows API sets (reverify),
# so they are self-contained and safe to ship beside the app and load from
# there.  (dxgi.dll / d3d11.dll are DirectX core and intentionally NOT bundled
# - they are present on any GUI-capable Windows and shadowing the system copy
# could disturb Qt's rendering.)
_mf_dlls = ["MF.dll", "MFReadWrite.dll", "mfplat.dll", "mfcore.dll"]
for _dll in _mf_dlls:
    _src = os.path.join(_sys32, _dll)
    if os.path.exists(_src):
        binaries.append((_src, '.'))
        print(f"Bundling optional system DLL {_dll} ({os.path.getsize(_src):,} bytes)")
    else:
        print(f"NOTE: {_dll} not present in System32 - skipping")

runtime_hooks = [
    os.path.join(pipeline_dir, 'dpi_aware_hook.py'),
    os.path.join(pipeline_dir, 'onnxruntime_runtime_hook.py'),
    os.path.join(pipeline_dir, 'torch_runtime_hook.py'),
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
    hookspath=[pipeline_dir],
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
    name='arrow',
)
