import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

base_dir = os.getcwd()
looper_dir = os.path.join(base_dir, 'LoOper')
overwatch_dir = os.path.join(base_dir, 'OverWatch')

pathex = [looper_dir, overwatch_dir]

looper_pkgs = ['AI', 'NGUI', 'player', 'recorder']
overwatch_pkgs = ['api', 'config', 'ui']

hiddenimports = []
for pkg in looper_pkgs + overwatch_pkgs:
    hiddenimports += collect_submodules(pkg)
hiddenimports += [
    'Qt',
    'Qt.QtSvg',
    'PyQt5',
    'PyQt5.QtCore',
    'PyQt5.QtGui',
    'PyQt5.QtWidgets',
    'PyQt5.QtSvg',
]
hiddenimports += collect_submodules('NodeGraphQt')
hiddenimports += collect_submodules('PyQt5')
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('fastapi')
hiddenimports += collect_submodules('cv2')
hiddenimports += collect_submodules('numpy')
hiddenimports += collect_submodules('pytesseract')

datas = []
for pkg in looper_pkgs + overwatch_pkgs:
    datas += collect_data_files(pkg, include_py_files=False)
datas += collect_data_files('NodeGraphQt', include_py_files=False)
datas += collect_data_files('cv2')
datas += collect_data_files('numpy')

datas += [(os.path.join(looper_dir, 'LoOper.ico'), '.')]
datas += [(os.path.join(looper_dir, 'LoOper.png'), '.')]
datas += [(os.path.join(looper_dir, 'data'), 'data')]

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
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# One-file build (no COLLECT)
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
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=os.path.join(looper_dir, 'LoOper.ico'),
)