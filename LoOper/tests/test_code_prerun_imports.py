"""Pre-run import check: invented modules are caught before the run.

A small model split one project into 'parsers' / 'task_manager' imports that
were never written as files.  The chain then tried ``pip install parsers``,
failed ("no matching distribution"), and the agent never heard about it.
``_unresolved_imports`` is the static gate that names those imports first.
"""

import json

from player.code_agent_ops.parsing import (
    _looks_like_tool_request,
    _materialize_project_files,
    _project_module_names,
    _unresolved_imports,
)


def test_invented_modules_are_reported():
    code = (
        "import os\n"
        "from parsers import parse_line\n"
        "import task_manager\n"
        "result = parse_line('x')\n"
    )
    assert _unresolved_imports(code, {}) == ['parsers', 'task_manager']


def test_project_files_count_as_real():
    code = "from timer import Timer\nimport ui\nresult = Timer\n"
    files = {'timer.py': 'class Timer: pass', 'ui.py': ''}
    assert _unresolved_imports(code, files) == []


def test_package_folder_counts_as_real():
    code = "from helpers.text import clean\nresult = clean('x')\n"
    assert _unresolved_imports(code, {'helpers/text.py': ''}) == []
    assert _project_module_names({'helpers/text.py': ''}) == {'helpers'}


def test_stdlib_and_installed_are_not_reported():
    code = "import json, sys, re\nresult = json.dumps({})\n"
    assert _unresolved_imports(code, {}) == []


def test_relative_imports_are_ignored():
    code = "from . import sibling\nfrom .pkg import thing\nresult = 1\n"
    assert _unresolved_imports(code, {}) == []


def test_syntax_error_is_not_an_import_problem():
    assert _unresolved_imports("def broken(:\n", {}) == []


def test_prose_is_not_a_tool_request():
    """A rambling plan must not be routed to the dev tools (that ate the
    whole tool budget and ended with 'the agent did not produce anything')."""
    assert _looks_like_tool_request("search for 'def main'") is True
    assert _looks_like_tool_request('show the whole code') is True
    assert _looks_like_tool_request('run the node') is True
    plan = ('Here is how I would structure this app.\nFirst a window class, '
            'then a task model, and finally a main entry point that wires them '
            'together without any third-party libraries.')
    assert _looks_like_tool_request(plan) is False
    assert _looks_like_tool_request('') is False
    assert _looks_like_tool_request('```python\nprint(1)\n```') is False


def test_materialize_accepts_the_nodes_json_string(tmp_path):
    """The chain stores project_files as a JSON string; siblings must land."""
    files = json.dumps({'timer.py': 'class Timer:\n    pass\n',
                        'ui.py': 'x = 1\n'})
    written = _materialize_project_files(str(tmp_path), files)
    assert sorted(written) == ['timer.py', 'ui.py']
    assert (tmp_path / 'timer.py').read_text(encoding='utf-8').startswith(
        'class Timer')


def test_materialize_bad_json_is_a_noop(tmp_path):
    assert _materialize_project_files(str(tmp_path), 'not json') == []


if __name__ == '__main__':
    import sys
    import tempfile
    from pathlib import Path
    test_invented_modules_are_reported()
    test_project_files_count_as_real()
    test_package_folder_counts_as_real()
    test_stdlib_and_installed_are_not_reported()
    test_relative_imports_are_ignored()
    test_syntax_error_is_not_an_import_problem()
    with tempfile.TemporaryDirectory() as d:
        test_materialize_accepts_the_nodes_json_string(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_materialize_bad_json_is_a_noop(Path(d))
    print('prerun import check OK', file=sys.stderr)
