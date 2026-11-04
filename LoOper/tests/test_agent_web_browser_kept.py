"""The agent keeps its web browser: a chain run is one step of a session.

Field report (automation.log): an agent step replayed a web sequence, then
``Closed shared web browser for chain`` ran right after it, so the *next* step
started on a fresh page with no login — and the user saw the browser vanish
after the first order.

Contract:

* a chain run that an AGENT drives leaves the browser open
  (``keep_web_browser``), whether it was attached or self-launched;
* a run that says nothing keeps the old behaviour (a self-launched browser on a
  durable profile is quit so cookies are flushed; a chain-linked/attached one is
  never killed);
* the flag reaches ``SequenceExecutor.close_web_session`` from
  ``ChainExecutor.run_chain`` and from ``MultiSequencePlayer``.

Run:  python -m pytest LoOper/tests/test_agent_web_browser_kept.py
"""

import os

import pytest  # noqa: E402


def _executor():
    from player.multi_sequence.sequence_executor import SequenceExecutor

    return SequenceExecutor(None, None)


def _closed(monkeypatch):
    """Record whether the browser was actually quit."""
    calls = []

    def _cleanup(driver):
        calls.append(driver)

    monkeypatch.setattr("player.web.session.cleanup_session", _cleanup)
    return calls


class _Driver:
    def execute_script(self, _script):
        return 1


def test_an_agent_run_leaves_the_browser_open(monkeypatch):
    calls = _closed(monkeypatch)
    executor = _executor()
    executor._web_session_driver = _Driver()
    executor.web_profile_key = "default"
    executor.keep_web_browser = True

    executor.close_web_session()

    assert calls == [], "the browser was quit between two agent steps"
    assert executor._web_session_driver is None, "the handle is still dropped"


def test_without_the_flag_a_self_launched_browser_is_still_quit(monkeypatch):
    """The old behaviour stays for anything that asked for it (no agent run)."""
    calls = _closed(monkeypatch)
    executor = _executor()
    executor._web_session_driver = _Driver()
    executor.web_profile_key = "default"

    executor.close_web_session()

    assert len(calls) == 1


def test_a_chain_linked_browser_is_never_killed(monkeypatch):
    calls = _closed(monkeypatch)
    executor = _executor()
    executor._web_session_driver = _Driver()
    executor.web_chain_key = "default"

    executor.close_web_session()

    assert calls == []


def test_the_player_hands_the_flag_to_its_executor():
    from player.multi_sequence_player import MultiSequencePlayer

    player = MultiSequencePlayer({"sequences": [], "llm_nodes": []},
                                 keep_web_browser=True)
    assert player.sequence_executor.keep_web_browser is True

    plain = MultiSequencePlayer({"sequences": [], "llm_nodes": []})
    assert plain.sequence_executor.keep_web_browser is False


def test_the_chain_executor_asks_for_it_by_default(monkeypatch):
    """ChainExecutor is the agent's path: its default must keep the session."""
    from player.agentic_ops import chain_executor
    import player.multi_sequence_player as player_mod

    seen = {}

    def _fake_play(chain_path, **kwargs):
        seen.update(kwargs)
        return True, None

    monkeypatch.setattr(player_mod, "play_chain_with_tailcalls", _fake_play)
    # the executor imports the symbol inside run_chain, so patch it there too
    monkeypatch.setattr("player.multi_sequence_player.play_chain_with_tailcalls",
                        _fake_play, raising=False)
    monkeypatch.setattr(
        "player.keyboard_monitor.start_global_monitoring", lambda: None)
    monkeypatch.setattr(
        "player.keyboard_monitor.stop_global_monitoring", lambda: None)
    monkeypatch.setattr(
        "player.keyboard_monitor.create_stop_flag", lambda: (lambda: False))

    chain = os.path.join(os.path.dirname(__file__), "_tmp_web_chain.json")
    with open(chain, "w", encoding="utf-8") as handle:
        handle.write('{"sequences": [], "llm_nodes": []}')
    try:
        chain_executor.ChainExecutor().run_chain(chain)
    finally:
        os.unlink(chain)

    assert seen.get("keep_web_browser") is True, seen
