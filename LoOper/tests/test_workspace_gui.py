"""The canvas is scoped by workspace: one agent, its own chains folder.

Opening the editor used to show one flat chains folder, grouped by a
``collection`` tag that decided nothing.  Now:

* the legacy folder is migrated ONCE into workspaces (files move, imports are
  rewritten by basename, a System brain becomes the workspace's ``legacy_brain``);
* the active workspace is what the library lists and what agent mode may route
  over;
* switching workspace re-scopes the library without closing the open chain.

Everything runs against a throwaway project tree: ``GraphViewWidget`` takes a
``project_root`` so a test never migrates the real chains folder.

Run:  python -m pytest LoOper/tests/test_workspace_gui.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _chain(project, name, **extra):
    data = {"llm_nodes": [{"node_id": "0x1"}], "name": name}
    data.update(extra)
    path = os.path.join(str(project), "chains", name + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


@pytest.fixture
def project(tmp_path):
    """A legacy tree: two collections + a System brain + a stale import."""
    for folder in ("chains", "sequences", "web_sequences"):
        (tmp_path / folder).mkdir()
    _chain(tmp_path, "tool a", collection="Tools")
    _chain(tmp_path, "tool b", collection="Tools")
    _chain(tmp_path, "brain", collection="System", is_default=True)
    _chain(tmp_path, "main", collection="Tools",
           chain_import_nodes=[{"chain_file_path":
                                r"D:\LoOperV2\LoOper\chains\tool b.json"}])
    return tmp_path


def _view(project, qapp):
    from NGUI.graph_view import GraphViewWidget

    view = GraphViewWidget(project_root=str(project))
    view.resize(1100, 700)
    view.show()
    qapp.processEvents()
    return view


def _listed(view):
    layout = view.chains_library._container_layout
    names = []
    for index in range(layout.count()):
        widget = layout.itemAt(index).widget()
        path = getattr(widget, 'file_path', '')
        if path:
            names.append(os.path.basename(path))
    return sorted(names)


def test_the_legacy_folder_becomes_workspaces(project, qapp):
    view = _view(project, qapp)
    root = view.workspaces_root

    assert os.path.isdir(root)
    left = [name for name in os.listdir(str(project / 'chains'))
            if name.endswith('.json')]
    assert left == [], 'every chain moved into a workspace'

    names = {item['name']: item for item in view.workspaces()}
    assert set(names) == {'Tools'}, 'no "System" workspace is created'
    assert view.workspace_slug(), 'a workspace is active on open'


def test_a_legacy_system_chain_is_absorbed_not_pinned(project, qapp):
    view = _view(project, qapp)
    tools = next(item for item in view.workspaces() if item['name'] == 'Tools')
    assert tools['agent']['legacy_brain'] == '', 'nothing is pinned'
    listed = [os.path.basename(path)
              for path in _ws_chain_files(view, tools['slug'])]
    assert 'brain.json' in listed, 'the old brain survives as a chain'
    assert 'collection' not in _chain_data(view, tools['slug'], 'brain.json')

    # the agent that owns the workspace composes its chains instead
    from player.agentic_ops.simple_agent import SimpleChainRouter
    router = SimpleChainRouter(chains_dir=str(project / 'chains'),
                               workspaces_root=view.workspaces_root,
                               workspace_slug=tools['slug'])
    (agent,) = router.list_agents()
    assert agent['description'] == 'routes over 4 chain(s)'
    assert agent['brain'] == ''


def _ws_chain_files(view, slug):
    from player import workspace as ws
    return ws.chain_files(view.workspaces_root, slug)


def _chain_data(view, slug, name):
    path = os.path.join(view.workspaces_root, slug, 'chains', name)
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def test_the_library_lists_only_the_active_workspace(project, qapp):
    from player import workspace as ws

    view = _view(project, qapp)
    tools = next(item for item in view.workspaces() if item['name'] == 'Tools')
    view.set_workspace(tools['slug'])
    qapp.processEvents()

    assert _listed(view) == ['brain.json', 'main.json', 'tool a.json',
                             'tool b.json']
    assert os.path.dirname(view.chains_folder) == os.path.join(
        view.workspaces_root, tools['slug'])

    empty = ws.create(view.workspaces_root, 'Writing', 'Pen')
    view._workspace_bar.refresh()
    view.set_workspace(empty['slug'])
    qapp.processEvents()
    assert _listed(view) == []


def test_switching_workspace_re_scopes_the_library(project, qapp):
    from player import workspace as ws

    view = _view(project, qapp)
    created = ws.create(view.workspaces_root, 'Writing', 'Pen')
    view._workspace_bar.refresh()
    seen = []
    view.workspace_changed.connect(seen.append)

    view.set_workspace(created['slug'])
    qapp.processEvents()

    assert seen == [created['slug']]
    assert _listed(view) == []
    bar = view._workspace_bar
    assert bar._combo.currentData() == created['slug']


def test_a_stale_import_points_at_the_moved_chain(project, qapp):
    view = _view(project, qapp)
    tools = next(item for item in view.workspaces() if item['name'] == 'Tools')
    moved = os.path.join(view.workspaces_root, tools['slug'], 'chains',
                         'main.json')
    with open(moved, encoding='utf-8') as handle:
        data = json.load(handle)
    target = data['chain_import_nodes'][0]['chain_file_path']
    assert os.path.dirname(target) == os.path.join(view.workspaces_root,
                                                  tools['slug'], 'chains')
    assert os.path.basename(target) == 'tool b.json'


def test_a_new_workspace_is_empty_and_selectable(project, qapp):
    from player import workspace as ws

    view = _view(project, qapp)
    created = ws.create(view.workspaces_root, 'Fresh', 'Neu')
    view._workspace_bar.refresh()
    view.set_workspace(created['slug'])
    qapp.processEvents()

    assert _listed(view) == []
    assert os.path.isdir(ws.chains_dir(view.workspaces_root, created['slug']))
    assert view.workspace_slug() == created['slug']
