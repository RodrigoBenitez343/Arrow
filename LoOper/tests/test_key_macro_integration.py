"""Integration tests for the event-driven keyboard macro system.

These exercise the FULL pipeline exactly as the app uses it:

    ElementRecorder (pynput-style events) -> KeyboardHandler -> SequenceManager
    -> JSON serialization -> SequencePlayer -> base_bot.execute_with_timing
    -> ActionHandlers.handle_key_event_action -> fake keyboard controller

Random macros (arbitrary modifier chords, typing, legacy clipboard combos,
plain special keys) are recorded, serialized, replayed, and the keyboard
output must match the recorded input 1:1 -- including self-healing
reconciliation and zero stuck keys.
"""

import json
import random

import pytest
from pynput import keyboard as K

from LoOper.player.key_defs import MODIFIER_NAMES, key_to_name
from LoOper.recorder.element_recorder import ElementRecorder
from player.sequence_player import SequencePlayer


class _KeyCode:
    """Duck-typed pynput KeyCode stand-in (char + vk, as pynput provides)."""

    def __init__(self, char, vk):
        self.char = char
        self.vk = vk


class FakeController:
    """Records every press/release/type call with canonical key names."""

    def __init__(self):
        self.pressed = []  # ('down'|'up'|'type', canonical name)
        self.held = set()

    @staticmethod
    def _name(key):
        char = getattr(key, "char", None)
        if char is not None:
            return str(char)
        vk = getattr(key, "vk", None)
        if vk is not None:
            return "vk_%s" % vk
        name = getattr(key, "name", None)
        if name is not None:
            return name
        return str(key)

    def press(self, key):
        name = self._name(key)
        self.held.add(name)
        self.pressed.append(("down", name))

    def release(self, key):
        name = self._name(key)
        self.held.discard(name)
        self.pressed.append(("up", name))

    def type(self, string):
        for ch in string:
            self.pressed.append(("type", ch))


@pytest.fixture
def no_sleep(monkeypatch):
    """Stub all sleeps so playback/recording runs instantly."""
    monkeypatch.setattr("time.sleep", lambda s: None)


def _make_recorder(monkeypatch):
    monkeypatch.setattr("pyautogui.position", lambda: (0, 0))
    return ElementRecorder(sequence_name="integration_test")


def _feed(recorder, events):
    """Feed physical press/release events through the real recorder wiring."""
    for kind, key in events:  # events are (kind, key) tuples
        if kind == "press":
            recorder.handle_keypress(key)
        else:
            recorder.handle_keyrelease(key)


def _finish(recorder):
    """Mirror ElementRecorder.save_sequence's keyboard flush + text flush."""
    recorder.sequence_manager.add_action(
        recorder.keyboard_handler.flush_pending_key_events())
    recorder.flush_current_string()
    return recorder.sequence_manager.recorded_actions


# ---------------------------------------------------------------------------
# Random macro generator (physical keyboard state machine)
# ---------------------------------------------------------------------------

_MODS = [K.Key.ctrl_l, K.Key.shift_l, K.Key.alt_l, K.Key.cmd_l]
_SPECIALS = [K.Key.enter, K.Key.esc, K.Key.tab, K.Key.f5, K.Key.space,
             K.Key.up, K.Key.down, K.Key.left, K.Key.right,
             K.Key.backspace, K.Key.delete]
_LETTERS = [_KeyCode(chr(97 + i), 65 + i) for i in range(26)]  # 'a'..'z'


def _random_macro(rng, n_presses=36):
    """Random valid macro of (key, 'press'|'release') physical events.

    No key is pressed twice without being released first, so the stream is
    physically plausible (chords overlap, taps are quick, releases can be
    rapid). Modifiers are weighted so chords dominate the sample.
    """
    pool = _MODS + _SPECIALS + _LETTERS
    weights = [3.0] * len(_MODS) + [1.5] * len(_SPECIALS) + [1.0] * len(_LETTERS)
    events = []
    held = {}  # canonical name -> physical key object
    for _ in range(n_presses):
        key = rng.choices(pool, weights=weights, k=1)[0]
        name = key_to_name(key)
        if name in held:
            if rng.random() < 0.6:
                events.append(("release", held.pop(name)))
            continue
        events.append(("press", key))
        held[name] = key
    # Recording stops: everything still held is released.
    for key in held.values():
        events.append(("release", key))
    return events


# ---------------------------------------------------------------------------
# Behavioral model of the player's keyboard output
# ---------------------------------------------------------------------------

def _expected_trace(actions):
    """Exact keyboard output ActionHandlers must produce for *actions*.

    Models the real semantics: idempotent press/release (a key already held
    produces no new trace entry), modifier reconciliation against each
    key_event's snapshot, and the fixed press/release sequences emitted by
    the legacy clipboard / keystroke paths.
    """
    trace = []
    held = set()

    def press(name):
        if name not in held:
            trace.append(("down", name))
            held.add(name)

    def release(name):
        if name in held:
            trace.append(("up", name))
            held.discard(name)

    for a in actions:
        t = a.get("type")
        if t == "key_event":
            name, state = a["key"], a["state"]
            mods = set(a.get("modifiers") or [])
            if state == "down":
                if name in MODIFIER_NAMES:
                    press(name)
                else:
                    # reconcile: press mods the snapshot demands, release any
                    # held modifier the snapshot no longer lists
                    for m in mods:
                        press(m)
                    for m in list(held):
                        if m in MODIFIER_NAMES and m not in mods:
                            release(m)
                    press(name)
            else:
                release(name)
        elif t == "clipboard":
            op = a.get("operation", "c")
            # handle_clipboard_action -> _press_key_combo([ctrl], op)
            trace += [("down", "ctrl"), ("down", op), ("up", op), ("up", "ctrl")]
            held.discard("ctrl")
            held.discard(op)
        elif t == "keystroke":
            key = a["key"].replace("Key.", "")
            trace += [("down", key), ("up", key)]
        elif t == "type_string":
            trace += [("type", ch) for ch in a["text"]]
    return trace


def _assert_snapshot_consistency(actions):
    """Each key_event's modifier snapshot must match the stream's state."""
    held = set()
    for a in actions:
        if a.get("type") != "key_event":
            continue
        name, state, mods = a["key"], a["state"], set(a["modifiers"])
        if state == "down":
            if name in MODIFIER_NAMES:
                assert mods == held, \
                    f"modifier-down {name}: snapshot {mods} != held {held}"
                held.add(name)
            else:
                assert mods == held, \
                    f"chord-down {name}: snapshot {mods} != held {held}"
        else:
            if name in MODIFIER_NAMES:
                held.discard(name)
            assert mods == held, \
                f"up {name}: snapshot {mods} != held {held}"


def _replay(actions, tmp_path, monkeypatch):
    """Write *actions* as a sequence JSON and play it through SequencePlayer."""
    seq_file = tmp_path / "macro.json"
    seq_file.write_text(json.dumps({"metadata": {}, "actions": actions}),
                        encoding="utf-8")
    player = SequencePlayer(str(seq_file))
    fake = FakeController()
    player.action_handlers.keyboard_controller = fake
    monkeypatch.setattr(player, "take_screenshot", lambda *a, **k: None)
    player.play_sequence(skip_initial_delay=True)
    return fake


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_handcrafted_macro_exact_roundtrip(no_sleep, tmp_path, monkeypatch):
    """Mixed macro: legacy clipboard, typing, chords, plain special keys."""
    macro = [
        ("press", K.Key.ctrl_l),                    # Ctrl+C -> legacy clipboard
        ("press", _KeyCode("c", 67)),
        ("release", _KeyCode("c", 67)),
        ("release", K.Key.ctrl_l),
        ("press", _KeyCode("h", 72)),               # typing "hi"
        ("press", _KeyCode("i", 73)),
        ("release", _KeyCode("h", 72)),
        ("release", _KeyCode("i", 73)),
        ("press", K.Key.enter),                     # bare enter -> keystroke
        ("release", K.Key.enter),
        ("press", K.Key.ctrl_l),                    # Ctrl+Shift+Esc chord
        ("press", K.Key.shift_l),
        ("press", K.Key.esc),
        ("release", K.Key.esc),
        ("release", K.Key.shift_l),
        ("release", K.Key.ctrl_l),
        ("press", K.Key.cmd_l),                     # Win+R chord
        ("press", _KeyCode("r", 82)),
        ("release", K.Key.cmd_l),
        ("release", _KeyCode("r", 82)),
        ("press", K.Key.alt_l),                     # Alt+F4 chord
        ("press", K.Key.f4),
        ("release", K.Key.f4),
        ("release", K.Key.alt_l),
        ("press", K.Key.f5),                        # bare F5 -> keystroke
        ("release", K.Key.f5),
    ]
    recorder = _make_recorder(monkeypatch)
    _feed(recorder, macro)
    actions = _finish(recorder)

    types = {a["type"] for a in actions}
    assert {"key_event", "clipboard", "keystroke", "type_string"} <= types

    serialized = json.loads(json.dumps(actions))
    fake = _replay(serialized, tmp_path, monkeypatch)
    assert fake.pressed == _expected_trace(serialized)
    assert fake.held == set()  # no stuck keys


@pytest.mark.parametrize("seed", range(12))
def test_random_macro_exact_roundtrip(seed, no_sleep, tmp_path, monkeypatch):
    """Random macros record, serialize, and replay 1:1 with zero stuck keys."""
    rng = random.Random(seed)
    recorder = _make_recorder(monkeypatch)
    _feed(recorder, _random_macro(rng))
    actions = _finish(recorder)

    # Non-vacuous: the sample must exercise the event machinery.
    key_events = [a for a in actions if a["type"] == "key_event"]
    assert key_events, "no key events recorded -- generator too weak"
    assert any(a["state"] == "down" for a in key_events)
    assert any(a["modifiers"] for a in key_events), "no chords recorded"
    assert any(a["state"] == "up" for a in key_events)

    # Data validity: every modifier snapshot matches the event stream.
    _assert_snapshot_consistency(actions)

    # JSON round trip must preserve key events exactly.
    serialized = json.loads(json.dumps(actions))
    assert [a for a in serialized if a["type"] == "key_event"] == key_events

    # Full pipeline replay: output must equal the recorded input 1:1.
    fake = _replay(serialized, tmp_path, monkeypatch)
    assert fake.pressed == _expected_trace(serialized)
    assert fake.held == set()  # no stuck keys


def test_replay_is_idempotent(no_sleep, tmp_path, monkeypatch):
    """Replaying the same recording twice produces identical output."""
    rng = random.Random(7)
    recorder = _make_recorder(monkeypatch)
    _feed(recorder, _random_macro(rng, n_presses=30))
    actions = json.loads(json.dumps(_finish(recorder)))

    seq_file = tmp_path / "macro.json"
    seq_file.write_text(json.dumps({"actions": actions}), encoding="utf-8")
    player = SequencePlayer(str(seq_file))
    fake = FakeController()
    player.action_handlers.keyboard_controller = fake
    monkeypatch.setattr(player, "take_screenshot", lambda *a, **k: None)
    player.play_sequence(skip_initial_delay=True)
    first = fake.pressed
    assert fake.held == set()

    fake.pressed = []
    player.play_sequence(skip_initial_delay=True)
    assert fake.pressed == first
    assert fake.held == set()


def test_save_sequence_flushes_stuck_keys(no_sleep, tmp_path, monkeypatch):
    """Recording that stops mid-hold gets synthetic ups via save_sequence."""
    recorder = _make_recorder(monkeypatch)
    # Hold Ctrl+Shift, tap T, and STOP while everything is still held.
    _feed(recorder, [
        ("press", K.Key.ctrl_l),
        ("press", K.Key.shift_l),
        ("press", _KeyCode("t", 84)),
    ])
    captured = {}

    def fake_save(filename):
        captured["actions"] = list(recorder.sequence_manager.recorded_actions)

    monkeypatch.setattr(recorder.sequence_manager, "save_sequence", fake_save)
    recorder.save_sequence()  # real flush wiring, disk write stubbed

    actions = captured["actions"]
    ups = [a for a in actions if a.get("type") == "key_event" and a["state"] == "up"]
    assert [a["key"] for a in ups] == ["ctrl", "shift", "t"]
    assert [a["modifiers"] for a in ups] == [["shift"], [], []]
    # Replaying the flushed recording leaves nothing held.
    fake = _replay(actions, tmp_path, monkeypatch)
    assert fake.held == set()
    assert fake.pressed == _expected_trace(actions)


def test_clipboard_then_chord_keeps_modifier_state(no_sleep, tmp_path, monkeypatch):
    """Regression: Ctrl+C (legacy clipboard) followed by Ctrl+T -- Ctrl still
    held -- must not corrupt the player's modifier state.

    The clipboard path releases its modifiers on the physical controller; the
    player's held-modifier set must be synced, or the next chord would replay
    as a bare 'T'.
    """
    macro = [
        ("press", K.Key.ctrl_l),
        ("press", _KeyCode("c", 67)),      # Ctrl+C -> legacy clipboard
        ("release", _KeyCode("c", 67)),
        ("press", _KeyCode("t", 84)),      # Ctrl+T while Ctrl is still held
        ("release", _KeyCode("t", 84)),
        ("release", K.Key.ctrl_l),
    ]
    recorder = _make_recorder(monkeypatch)
    _feed(recorder, macro)
    actions = json.loads(json.dumps(_finish(recorder)))
    fake = _replay(actions, tmp_path, monkeypatch)
    assert fake.pressed == _expected_trace(actions)
    assert fake.held == set()
    # The 't' must be pressed with ctrl (chord), not as a bare tap.
    t_idx = fake.pressed.index(("down", "t"))
    assert ("down", "ctrl") in fake.pressed[:t_idx]


def test_empty_macro_replays_cleanly(no_sleep, tmp_path, monkeypatch):
    """An empty recording plays back without error and no keyboard output."""
    fake = _replay([], tmp_path, monkeypatch)
    assert fake.pressed == []
    assert fake.held == set()
