"""Unit tests for player/base_bot.py — SeleniumBot core behavior.

PyAutoGUI and timing are faked: ``pyautogui`` module calls are patched via
monkeypatch and ``time.sleep`` is replaced with a no-op recorder.
"""

import time

import pytest

from player.base_bot import SeleniumBot


@pytest.fixture
def bot(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    return SeleniumBot()


# ---------------------------------------------------------------------------
# Construction / state
# ---------------------------------------------------------------------------


def test_initial_state(bot):
    assert bot.default_timeout == 10
    assert bot.retry_attempts == 5
    assert bot.action_count == 0
    assert bot.selection_memory == {}
    assert bot.action_handlers is not None
    assert bot.noise_amplitude_px == 3.0


def test_clear_selection_memory(bot):
    bot.selection_memory["k"] = {"all_clicked": True}
    bot.clear_selection_memory()
    assert bot.selection_memory == {}


# ---------------------------------------------------------------------------
# Perlin noise helpers (pure math)
# ---------------------------------------------------------------------------


def test_build_perm_table_deterministic():
    bot = SeleniumBot()
    t1 = bot._build_perm_table(42)
    t2 = bot._build_perm_table(42)
    assert t1 == t2
    assert len(t1) == 512
    assert len(set(t1)) == 256  # each value appears exactly twice


def test_fade_eases():
    bot = SeleniumBot()
    assert bot._fade(0.0) == 0.0
    assert bot._fade(1.0) == 1.0
    assert 0.0 < bot._fade(0.5) < 1.0


def test_lerp():
    bot = SeleniumBot()
    assert bot._lerp(0, 10, 0.0) == 0
    assert bot._lerp(0, 10, 1.0) == 10
    assert bot._lerp(0, 10, 0.5) == 5


def test_grad2_quadrants():
    bot = SeleniumBot()
    # 4 gradient directions; value must always be finite
    for h in range(4):
        assert isinstance(bot._grad2(h, 1.0, 2.0), float)


def test_perlin2_output_range(bot):
    # Classic perlin noise is bounded in [-1, 1]
    for x in (0.0, 0.3, 1.7, 5.5):
        for y in (0.0, 100.0):
            v = bot._perlin2(x, y)
            assert -1.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# retry_on_exception decorator
# ---------------------------------------------------------------------------


def test_retry_succeeds_after_failures(bot, monkeypatch):
    calls = {"n": 0}

    @SeleniumBot.retry_on_exception
    def flaky(self):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    bot.retry_attempts = 5
    assert flaky(bot) == "ok"
    assert calls["n"] == 3


def test_retry_raises_after_exhaustion(bot):
    calls = {"n": 0}

    @SeleniumBot.retry_on_exception
    def always_fails(self):
        calls["n"] += 1
        raise ValueError("permanent")

    bot.retry_attempts = 3
    with pytest.raises(ValueError):
        always_fails(bot)
    assert calls["n"] == 3


# ---------------------------------------------------------------------------
# execute_with_timing routing
# ---------------------------------------------------------------------------


def _make_action(action_type, **extra):
    action = {"type": action_type}
    action.update(extra)
    return action


def test_execute_with_timing_routes_click(bot, monkeypatch):
    action = _make_action("click", coordinates={"x": 1, "y": 1})
    calls = []
    monkeypatch.setattr(bot.action_handlers, "handle_click_action",
                        lambda idx, a, fallback_callback=None, stop_flag=None: (
                            calls.append("click"), True)[1])
    monkeypatch.setattr(bot, "take_screenshot", lambda *a, **k: None)
    assert bot.execute_with_timing(0, action) is True
    assert calls == ["click"]


def test_execute_with_timing_routes_type_string(bot, monkeypatch):
    action = _make_action("type_string", text="hello")
    calls = []
    monkeypatch.setattr(bot.action_handlers, "handle_type_string_action",
                        lambda idx, a, stop_flag=None: calls.append("type"))
    monkeypatch.setattr(bot, "take_screenshot", lambda *a, **k: None)
    assert bot.execute_with_timing(0, action) is True
    assert calls == ["type"]


def test_execute_with_timing_routes_key_event(bot, monkeypatch):
    action = _make_action("key_event", key="esc", state="down", modifiers=["ctrl"])
    calls = []
    monkeypatch.setattr(bot.action_handlers, "handle_key_event_action",
                        lambda idx, a, stop_flag=None: (calls.append("key"), True)[1])
    monkeypatch.setattr(bot, "take_screenshot", lambda *a, **k: None)
    assert bot.execute_with_timing(0, action) is True
    assert calls == ["key"]


def test_execute_with_timing_skips_screenshot_for_key_event(bot, monkeypatch):
    """Screenshots between a chord's press and release would distort timing."""
    action = _make_action("key_event", key="a", state="down")
    shot = []
    monkeypatch.setattr(bot.action_handlers, "handle_key_event_action", lambda *a, **k: True)
    monkeypatch.setattr(bot, "take_screenshot", lambda *a, **k: shot.append(1))
    assert bot.execute_with_timing(0, action) is True
    assert shot == []


def test_execute_with_timing_click_failure_returns_false(bot, monkeypatch):
    action = _make_action("click", coordinates={"x": 1, "y": 1})
    monkeypatch.setattr(bot.action_handlers, "handle_click_action",
                        lambda *a, **k: False)  # fallback triggered
    assert bot.execute_with_timing(0, action) is False


def test_execute_with_timing_stop_flag_before_action(bot, monkeypatch):
    action = _make_action("click")
    assert bot.execute_with_timing(0, action, stop_flag=lambda: True) is True


def test_execute_with_timing_unknown_type_ignored(bot, monkeypatch):
    """Unknown action types fall through gracefully (no handler matches)."""
    monkeypatch.setattr(bot, "take_screenshot", lambda *a, **k: None)
    action = _make_action("bogus_type")
    assert bot.execute_with_timing(0, action) is True


def test_execute_with_timing_raises_action_exception(bot, monkeypatch):
    action = _make_action("keystroke", key="x")

    def boom(*a, **k):
        raise RuntimeError("handler failed")

    monkeypatch.setattr(bot.action_handlers, "handle_keystroke_action", boom)
    with pytest.raises(RuntimeError):
        bot.execute_with_timing(0, action)


# ---------------------------------------------------------------------------
# random_delay bounds
# ---------------------------------------------------------------------------


def test_random_delay_respects_bounds(bot, monkeypatch):
    delays = []
    monkeypatch.setattr("time.sleep", lambda s: delays.append(s))
    for _ in range(50):
        bot.random_delay(0.1, 0.2)
    for d in delays:
        assert 0.1 <= d <= 0.2


# ---------------------------------------------------------------------------
# take_screenshot
# ---------------------------------------------------------------------------


def test_take_screenshot_temporal_does_not_write_disk(bot, monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr("pyautogui.screenshot",
                        lambda: (captured.append(1), None)[1])
    bot.take_screenshot(directory=str(tmp_path), prefix="t", is_temporal=True)
    assert captured == [1]
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_take_screenshot_permanent_writes_file(bot, monkeypatch, tmp_path):
    class FakeImg:
        def save(self, path):
            with open(path, "wb") as f:
                f.write(b"PNG")

    monkeypatch.setattr("pyautogui.screenshot", lambda: FakeImg())
    bot.take_screenshot(directory=str(tmp_path), prefix="shot", is_temporal=False)
    files = list(tmp_path.glob("shot_*.png"))
    assert len(files) == 1


def test_human_mouse_move_records_history(bot, monkeypatch):
    monkeypatch.setattr("pyautogui.position", lambda: (0, 0))
    moves = []
    monkeypatch.setattr("pyautogui.moveTo",
                        lambda x, y, duration=0: moves.append((x, y)))
    bot.human_mouse_move(100, 50)
    assert len(moves) >= 2  # bezier points + final
    assert moves[-1] == (100, 50)
    assert len(bot.mouse_movement_history) == 1
