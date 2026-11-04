"""The editor resumes where it was left.

Closing the app used to lose the open chain (and any edits not saved to disk),
the Builder panel, and the canvas position.  ``NGUI/app_state.py`` writes that
state on close and puts it back at start; these tests pin the contract:

* the LIVE chain config is what gets saved (unsaved edits included);
* restoring rebuilds from that config, never just from the file;
* an emptied canvas does not resurrect the file's chain;
* a corrupt state file is reported, never silently applied.

Run:  python -m pytest LoOper/tests/test_app_state.py
"""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

CHAIN = {
    'llm_nodes': [{'node_id': 'l1', 'type': 'llm', 'name': 'answer'}],
    'input_nodes': [{'node_id': 'i1', 'type': 'input', 'name': 'query'}],
    'description': 'asks and answers',
    'flow': {'summary': 'asks and answers', 'sections': [], 'steps': []},
    'workspace': 'job-hunt',
}


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    from NGUI import app_state

    target = tmp_path / 'app_state.json'
    monkeypatch.setattr(app_state, 'path', lambda: str(target))
    return target


def _window(config=None, chain_file='', panel_visible=False):
    """A window whose canvas is a stub; nothing Qt is touched."""
    graph_view = SimpleNamespace(
        _chain_epoch=4,
        config_manager=SimpleNamespace(
            _current_chain_file=chain_file,
            chain_config=dict(config or {}),
            get_chain_config=lambda: dict(config or {}),
            loaded=[], built=[],
        ),
        node_graph=None,
    )
    graph_view.build_graph_from_config = (
        lambda *a, **k: graph_view.config_manager.built.append('build'))
    graph_view.load_chain = (
        lambda path: graph_view.config_manager.loaded.append(path))
    window = SimpleNamespace(graph_view=graph_view,
                             builder_panel=SimpleNamespace(
                                 isVisible=lambda: panel_visible),
                             toggles=[])
    window._toggle_builder = lambda on: window.toggles.append(bool(on))
    return window


# ------------------------------------------------------------------ save

def test_the_live_chain_is_what_gets_saved(state_file):
    from NGUI import app_state

    window = _window(CHAIN, chain_file=r'd:\Arrow\LoOper\chains\X.json')
    data = app_state.save(window)

    assert data['chain_config']['llm_nodes'][0]['name'] == 'answer'
    assert data['chain_file'].endswith('X.json')
    stored = json.loads(state_file.read_text(encoding='utf-8'))
    assert stored == json.loads(json.dumps(data))
    assert app_state.load()['chain_config']['description'] == 'asks and answers'


def test_an_emptied_canvas_saves_no_chain(state_file):
    from NGUI import app_state

    state = app_state.save(_window({}, chain_file=r'd:\Arrow\chain.json'))
    assert app_state.has_chain(state['chain_config']) is False


def test_a_corrupt_state_file_is_reported(state_file):
    from NGUI import app_state

    state_file.write_text('{ not json', encoding='utf-8')
    with pytest.raises(app_state.AppStateError):
        app_state.load()
    state_file.unlink()
    assert app_state.load() == {}


# --------------------------------------------------------------- restore

def test_restore_rebuilds_from_the_live_config(state_file):
    from NGUI import app_state

    window = _window()
    restored = app_state.restore(window, state={
        'version': 1, 'chain_file': r'd:\Arrow\LoOper\chains\X.json',
        'chain_config': dict(CHAIN), 'builder_panel_open': True, 'view': {}})

    cfg = window.graph_view.config_manager
    assert cfg.built == ['build'], 'the canvas is rebuilt from the config'
    assert cfg.loaded == [], 'the file is not reopened over the live state'
    assert cfg.chain_config['llm_nodes'][0]['name'] == 'answer'
    assert cfg._current_chain_file.endswith('X.json')
    assert cfg._current_chain_flow['summary'] == 'asks and answers'
    assert cfg._current_chain_description == 'asks and answers'
    assert cfg._current_chain_workspace == 'job-hunt'
    assert window.graph_view._chain_epoch == 5, 'a new session for the Builder'
    assert restored['nodes'] == 2
    assert window.toggles == [True], 'the Builder panel comes back open'


def test_an_emptied_canvas_does_not_resurrect_the_file(state_file, tmp_path):
    from NGUI import app_state

    chain = tmp_path / 'on_disk.json'
    chain.write_text(json.dumps(CHAIN), encoding='utf-8')
    window = _window()
    restored = app_state.restore(window, state={
        'version': 1, 'chain_file': str(chain), 'chain_config': {},
        'builder_panel_open': False, 'view': {}})

    cfg = window.graph_view.config_manager
    assert cfg.built == [] and cfg.loaded == []
    assert restored == {}


def test_a_path_only_state_still_opens_the_chain(state_file, tmp_path):
    from NGUI import app_state

    chain = tmp_path / 'legacy.json'
    chain.write_text(json.dumps(CHAIN), encoding='utf-8')
    window = _window()
    # no chain_config key at all: an older state file
    app_state.restore(window, state={'version': 1, 'chain_file': str(chain)})
    assert window.graph_view.config_manager.loaded == [str(chain)]


def test_no_state_restores_nothing(state_file):
    from NGUI import app_state

    window = _window()
    assert app_state.restore(window, state={}) == {}
    assert window.graph_view.config_manager.built == []
    assert window.toggles == []


# -------------------------------------------------------------- geometry

def test_the_window_frame_round_trips(state_file, qapp):
    from PyQt5.QtWidgets import QWidget

    from NGUI import app_state

    first = QWidget()
    first.resize(912, 655)
    hexed = app_state._geometry(first)
    assert hexed and all(ch in '0123456789abcdef' for ch in hexed)

    state_file.write_text(json.dumps({'version': 1, 'geometry': hexed}),
                          encoding='utf-8')
    second = QWidget()
    assert app_state.apply_geometry(second) is True
    assert (second.width(), second.height()) == (912, 655)

    state_file.unlink()
    assert app_state.apply_geometry(second) is False, 'no state, no jump'


def test_geometry_is_skipped_when_the_window_cannot_report_it(state_file):
    from NGUI import app_state

    class _NoGeometry:
        def saveGeometry(self):
            raise RuntimeError('no frame yet')

    assert app_state._geometry(_NoGeometry()) == ''

