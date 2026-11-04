"""GUI code is verified by launching it and showing the window to the vision
model, instead of the agent asking the user to run and describe it."""

import sys

from player.code_agent_ops.gui_check import (
    _pids_of,
    _tail,
    _verdict,
    venv_python,
)


def test_verdict_reads_the_vision_model_answer():
    assert _verdict('VERDICT: OK\nwindow "Tasks", 3 buttons') is True
    assert _verdict('verdict: ok') is True
    assert _verdict('VERDICT:OK') is True
    assert _verdict('VERDICT: PROBLEM\nan error dialog is open') is False
    assert _verdict('') is False


def test_venv_python_from_site_packages():
    sp = r'C:\venvs\demo\Lib\site-packages'
    assert venv_python(sp).endswith(r'Scripts\python.exe') if sys.platform == 'win32' \
        else venv_python(sp).endswith('bin/python')
    # Unknown layout falls back to the running interpreter (never a crash).
    assert venv_python('') == sys.executable
    assert venv_python(r'C:\nope\Lib\site-packages') == sys.executable


def test_pids_of_includes_the_launcher():
    # The venv python.exe is a redirector: the window belongs to a CHILD pid,
    # so the launcher pid alone is never enough.
    assert __import__('os').getpid() in _pids_of(__import__('os').getpid())


def test_tail_keeps_the_end_of_a_traceback():
    assert _tail('x' * 2000).startswith('...')
    assert _tail('boom').endswith('boom')
