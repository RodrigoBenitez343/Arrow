"""The platform gate must expose one working API on the running OS.

These checks fail if a backend stops dispatching (wrong platform selected, a
primitive returning the wrong shape) - the one thing every gated call site
depends on.
"""
import sys

from LoOper import platform_gate as gate


def test_backend_selected_for_this_host():
    if sys.platform == "win32":
        assert gate.IS_WINDOWS
        assert gate.PLATFORM == "windows"
        assert gate.uia_available() is True
    elif sys.platform.startswith("linux"):
        assert gate.IS_LINUX
        assert gate.PLATFORM == "linux"
        assert gate.uia_available() is False
    else:  # macOS / other: the gate must still answer, not crash on import
        assert gate.PLATFORM


def test_screen_size_is_positive():
    width, height = gate.screen_size()
    assert width > 0 and height > 0


def test_window_dicts_share_one_shape():
    for info in gate.list_windows()[:5]:
        assert set(info) == {"handle", "title", "pid", "rect", "visible"}
        assert isinstance(info["handle"], int)
        assert isinstance(info["title"], str)


def test_active_window_shape():
    info = gate.active_window()
    assert info is None or isinstance(info["handle"], int)


def test_async_key_state_answers_without_raising():
    assert gate.async_key_state(0x10) in (True, False)


def test_foreground_matches_with_empty_context():
    assert gate.foreground_matches() in (True, False)


def test_session_check_returns_a_verdict():
    ok, reason = gate.session_ok()
    assert isinstance(ok, bool)
    assert ok or reason
