"""Code-node dependency scanner must not pip-install first-party modules.

A little-coder model splits script.py into workspace siblings (main_window.py,
styles.py, ...).  Those import at runtime (the node folder is on sys.path) but
are not on PyPI - the scanner must skip them, while still pip-installing a real
package the chain venv lacks.
"""

import os

from player.multi_sequence.worflow_interpreter_modules.executor_modules.code_ops import (
    _workspace_module_names,
    _is_first_party_import,
)


def test_workspace_siblings_are_first_party(tmp_path):
    (tmp_path / "main_window.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "styles.py").write_text("y = 2", encoding="utf-8")
    names = _workspace_module_names(str(tmp_path))
    assert {"main_window", "styles"} <= names
    assert _is_first_party_import("main_window", names) is True
    assert _is_first_party_import("styles", names) is True


def test_relative_import_names_without_workspace_still_resolve(tmp_path):
    # A sibling package dir with __init__.py is a module too.
    pkg = tmp_path / "helpers"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    assert "helpers" in _workspace_module_names(str(tmp_path))


def test_real_third_party_is_not_skipped():
    # pytest lives under site-packages -> treated as a pip package, installable.
    assert _is_first_party_import("pytest") is False


def test_stdlib_and_builtins_are_first_party():
    assert _is_first_party_import("os") is True        # frozen / builtin
    assert _is_first_party_import("json") is True      # stdlib outside site-packages


def test_unknown_name_is_left_to_pip():
    assert _is_first_party_import("definitely_not_a_real_pkg_xyz123") is False
