"""Chain JSON encoding (2026-09-24): the GUI must load what it saves.

A chain carrying an em dash in an LLM ``system_message`` became UNLOADABLE in
the editor — "Error loading chain: 'charmap' codec can't decode byte 0x9d in
position 2845" — because ``load_chain`` opened the file with the locale codec
(cp1252 on Windows), where 0x9d is undefined; bytes it happens to define were
silently MOJIBAKED instead.  ``save_chain`` used the same locale codec, so the
pair could not round-trip non-ASCII at all.  Both paths are UTF-8 now, and the
learned-chain writer emits pure-ASCII JSON so artifacts load everywhere.

Run:  python -m pytest LoOper/tests/test_chain_config_encoding.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


class _Parent:
    """Minimal parent widget stand-in (only attributes the paths touch)."""

    chains_folder = ''


def _cm(monkeypatch):
    from NGUI.graph_elements import config_manager as cm_mod
    from NGUI.graph_elements.config_manager import ConfigManager

    # A failing load pops a modal dialog; the test never needs one.
    monkeypatch.setattr(cm_mod.QMessageBox, 'critical',
                        staticmethod(lambda *a, **k: None))
    cm = ConfigManager(_Parent())
    cm.build_graph_from_config = lambda: None   # no graph widget offscreen
    cm.save_current_state = lambda: None        # no graph to harvest
    return cm


def test_load_chain_decodes_utf8_system_messages(qapp, tmp_path, monkeypatch):
    """U+201D '”' is E2 80 9D on disk — 0x9d is UNDEFINED in cp1252, which is
    the exact byte the user's load crashed on.  UTF-8 must decode it verbatim."""
    msg = 'router — you output USE_TOOL:<id>, never “chat”'
    p = tmp_path / 'chain.json'
    p.write_text(json.dumps({
        'llm_nodes': [{'node_id': 'l1', 'type': 'llm',
                       'system_message': msg, 'connections': []}],
    }, ensure_ascii=False), encoding='utf-8')

    cm = _cm(monkeypatch)
    assert cm.load_chain(str(p)) is True
    assert cm.chain_config['llm_nodes'][0]['system_message'] == msg


def test_save_chain_writes_utf8_and_round_trips(qapp, tmp_path, monkeypatch):
    msg = 'router — USE_TOOL:<id> “quotes”'
    cm = _cm(monkeypatch)
    cm.chain_config = {'llm_nodes': [{'node_id': 'l1', 'system_message': msg}]}
    out = tmp_path / 'saved.json'
    assert cm.save_chain(str(out)) is True
    out.read_bytes().decode('utf-8')   # never locale-encoded

    cm2 = _cm(monkeypatch)
    assert cm2.load_chain(str(out)) is True
    assert cm2.chain_config['llm_nodes'][0]['system_message'] == msg
