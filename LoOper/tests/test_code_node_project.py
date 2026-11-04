"""Project support for Code nodes: the node carries a whole workspace.

script.py is the executed entry script; every other file (modules, data)
lives beside it and is persisted in the node so a chain travels whole.
Run:  python -m pytest LoOper/tests/test_code_node_project.py
"""
import os
import subprocess
import sys

from player.code_agent_ops.parsing import (
    MAIN_FILE,
    _collect_project_files,
    _materialize_project_files,
)


def test_collect_skips_main_binary_and_caches(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, 'pkg'))
    os.makedirs(os.path.join(root, '__pycache__'))
    with open(os.path.join(root, MAIN_FILE), 'w', encoding='utf-8') as f:
        f.write('import pkg.util\n')
    with open(os.path.join(root, 'pkg', 'util.py'), 'w', encoding='utf-8') as f:
        f.write('VALUE = 21\n')
    with open(os.path.join(root, 'data.json'), 'w', encoding='utf-8') as f:
        f.write('{"n": 1}\n')
    with open(os.path.join(root, 'logo.png'), 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
    with open(os.path.join(root, '__pycache__', 'x.pyc'), 'wb') as f:
        f.write(b'\x00\x01')

    files = _collect_project_files(root)
    assert set(files) == {'pkg/util.py', 'data.json'}
    assert MAIN_FILE not in files


def test_materialize_roundtrip_and_safety(tmp_path):
    root = str(tmp_path)
    written = _materialize_project_files(root, {
        'pkg/util.py': 'VALUE = 21\n',
        '../escape.py': 'boom = True\n',     # rejected: path escape
        MAIN_FILE: 'result = 1\n',           # never touched (node 'code' owns it)
    })
    assert written == ['pkg/util.py']
    assert os.path.isfile(os.path.join(root, 'pkg', 'util.py'))
    assert not os.path.exists(os.path.join(os.path.dirname(root), 'escape.py'))

    # Existing files win unless overwrite is requested (live edits).
    _materialize_project_files(root, {'pkg/util.py': 'VALUE = 99\n'})
    with open(os.path.join(root, 'pkg', 'util.py'), encoding='utf-8') as f:
        assert f.read() == 'VALUE = 21\n'
    _materialize_project_files(root, {'pkg/util.py': 'VALUE = 99\n'},
                               overwrite=True)
    with open(os.path.join(root, 'pkg', 'util.py'), encoding='utf-8') as f:
        assert f.read() == 'VALUE = 99\n'


def test_entry_script_runs_with_project_modules(tmp_path):
    """The entry script imports a sibling module exactly like the runner
    (project folder on sys.path)."""
    root = str(tmp_path)
    _materialize_project_files(root, {'pkg/util.py': 'VALUE = 21\n'})
    with open(os.path.join(root, MAIN_FILE), 'w', encoding='utf-8') as f:
        f.write('from pkg.util import VALUE\nresult = VALUE * 2\n'
                'print(result)\n')
    proc = subprocess.run([sys.executable, MAIN_FILE], cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == '42'
