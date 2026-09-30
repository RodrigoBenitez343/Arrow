"""Unit tests for player/action_handlers.py -- raw key_event replay.

Verifies the generic chord reconstruction (Ctrl+Shift+Esc), stuck-modifier
self-healing, and stuck-key cleanup without sending real keyboard input.
"""

import pytest

from player.action_handlers import ActionHandlers


class FakeBot:
    sandbox_agent_url = None
    vnc_bridge = None


class FakeController:
    """Records press/release calls; resolves canonical names from pynput keys."""

    def __init__(self):
        self.pressed = []  # ('down' | 'up', canonical name)
        self.held = set()

    @staticmethod
    def _name(key):
        return (getattr(key, "char", None)
                or getattr(key, "name", None) or str(key))

    def press(self, key):
        name = self._name(key)
        self.held.add(name)
        self.pressed.append(("down", name))

    def release(self, key):
        name = self._name(key)
        self.held.discard(name)
        self.pressed.append(("up", name))


@pytest.fixture
def ah(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    handlers = ActionHandlers(FakeBot())
    handlers.keyboard_controller = FakeController()
    return handlers


def _ev(idx, key, state, mods=None):
    return {"key": key, "state": state, "modifiers": mods or []}


def test_chord_reconstruction_preserves_order(ah):
    """Ctrl+Shift+Esc replays as press ctrl, press shift, press esc, then
    release in reverse -- identical to the recorded event stream."""
    for e in (_ev(0, "ctrl", "down", []),
              _ev(1, "shift", "down", ["ctrl"]),
              _ev(2, "esc", "down", ["ctrl", "shift"]),
              _ev(3, "esc", "up", ["ctrl", "shift"]),
              _ev(4, "shift", "up", ["ctrl"]),
              _ev(5, "ctrl", "up", [])):
        ah.handle_key_event_action(0, e)
    downs = [n for (s, n) in ah.keyboard_controller.pressed if s == "down"]
    ups = [n for (s, n) in ah.keyboard_controller.pressed if s == "up"]
    assert downs == ["ctrl", "shift", "esc"]
    assert ups == ["esc", "shift", "ctrl"]
    assert ah._held_keys == set()


def test_modifier_snapshot_heals_stuck_modifier(ah):
    """A modifier whose down event was missed is auto-pressed; a chord that
    no longer lists it releases it before executing."""
    ah.handle_key_event_action(0, _ev(0, "ctrl", "down", []))
    # Recording missed the shift down; the snapshot demands it.
    ah.handle_key_event_action(1, _ev(1, "t", "down", ["ctrl", "shift"]))
    assert "shift" in ah.keyboard_controller.held
    ah.handle_key_event_action(2, _ev(2, "t", "up", ["ctrl", "shift"]))
    ah.handle_key_event_action(3, _ev(3, "shift", "up", ["ctrl"]))
    # Next chord (Ctrl+W) drops shift -- stuck modifier is released.
    ah.handle_key_event_action(4, _ev(4, "w", "down", ["ctrl"]))
    assert "shift" not in ah.keyboard_controller.held
    assert "ctrl" in ah.keyboard_controller.held
    ah.handle_key_event_action(5, _ev(5, "w", "up", ["ctrl"]))
    ah.handle_key_event_action(6, _ev(6, "ctrl", "up", []))
    assert ah._held_keys == set()


def test_truncated_sequence_release_all_keys(ah):
    """Keys held when playback stops/aborts are released by release_all_keys."""
    ah.handle_key_event_action(0, _ev(0, "ctrl", "down", []))
    ah.handle_key_event_action(1, _ev(1, "s", "down", ["ctrl"]))
    assert ah._held_keys == {"ctrl", "s"}
    ah.release_all_keys()
    assert ah.keyboard_controller.held == set()
    assert ah._held_keys == set()


def test_up_without_down_is_noop(ah):
    """Orphaned up events are idempotent -- no stuck state, no crash."""
    ah.handle_key_event_action(0, _ev(0, "esc", "up", []))
    ah.handle_key_event_action(1, _ev(1, "ctrl", "up", []))
    assert ah._held_keys == set()
    assert ah.keyboard_controller.pressed == []


def test_delta_pacing_does_not_block(ah):
    """Recorded inter-event delta is honored but capped (sleep is stubbed)."""
    ah.handle_key_event_action(0, {"key": "ctrl", "state": "down",
                                   "modifiers": [], "delta": 1.0})
    assert "ctrl" in ah.keyboard_controller.held
