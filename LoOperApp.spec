import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

try:
    base_dir = os.path.abspath(SPECPATH)
except Exception:
    base_dir = os.getcwd()
looper_dir = os.path.join(base_dir, 'LoOper')
overwatch_dir = os.path.join(looper_dir, 'AI', 'OverWatch')
computer_vision_dir = os.path.join(looper_dir, 'player', 'Computer_vision')

for p in (base_dir, looper_dir, overwatch_dir, computer_vision_dir):
    if p and p not in sys.path:
        sys.path.insert(0, p)

pathex = [looper_dir, overwatch_dir, computer_vision_dir]

hiddenimports = []
datas = []

hiddenimports += [
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
]

datas += collect_data_files('NodeGraphQt', include_py_files=False)
datas += collect_data_files('ultralytics', include_py_files=False)

datas += [(os.path.join(looper_dir, 'data'), 'data')]
datas += [(os.path.join(looper_dir, 'player', 'Computer_vision', 'Computer_Vision_1.5.3.onnx'), 'player/Computer_vision')]
datas += [(os.path.join(looper_dir, 'LoOper.ico'), '.')]
datas += [(os.path.join(looper_dir, 'LoOper.png'), '.')]

block_cipher = None

a = Analysis(
    [os.path.join(looper_dir, 'main.py')],
    pathex=pathex,
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['onnx', 'onnx.reference'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    cipher=block_cipher,
)
torch_libs = collect_dynamic_libs('torch')
a.binaries += [(dest, src, 'BINARY') for src, dest in torch_libs]
torchvision_libs = collect_dynamic_libs('torchvision')
a.binaries += [(dest, src, 'BINARY') for src, dest in torchvision_libs]
ultralytics_libs = collect_dynamic_libs('ultralytics')
a.binaries += [(dest, src, 'BINARY') for src, dest in ultralytics_libs]

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
    name='LoOper',
)
