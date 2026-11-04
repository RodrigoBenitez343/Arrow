"""The app's own Open/Save chain dialogs (no OS file dialog).

The actions toolbar dropped the user into Explorer, which shows every file on
the machine and none of the app's context.  These dialogs list the chains the
workspaces actually hold, and a save names the file inside the ACTIVE folder.

Run:  python -m pytest LoOper/tests/test_chain_files_dialog.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


CHAIN = {'llm_nodes': [{'type': 'llm', 'node_id': '0x1', 'name': 'draft'}]}


def _workspace(tmp_path):
    """Two workspaces with one chain each (the second one newer)."""
    from player import workspace

    root = str(tmp_path / 'workspaces')
    first = workspace.create(root, 'Tools', agent_name='Scout')
    second = workspace.create(root, 'Inbox', agent_name='Scribe')
    for slug, name, age in ((first['slug'], 'older', 1000),
                            (second['slug'], 'newer', 2000)):
        folder = workspace.chains_dir(root, slug)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + '.json')
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(CHAIN, handle)
        os.utime(path, (age, age))
    return root, workspace.chains_dir(root, second['slug'])


def test_entries_cover_every_workspace_newest_first(tmp_path):
    from NGUI.dialogs.chain_files_dialog import chain_entries

    root, _folder = _workspace(tmp_path)
    entries = chain_entries(root)
    assert [entry['name'] for entry in entries] == ['newer', 'older']
    assert {entry['workspace'] for entry in entries} == {'Tools', 'Inbox'}
    # A folder passed twice (the active one is also a workspace) is one row.
    assert len(chain_entries(root, [os.path.dirname(entries[0]['path'])])) == 2


def test_open_dialog_lists_filters_and_answers_the_path(qapp, tmp_path):
    from NGUI.dialogs.chain_files_dialog import ChainFilesDialog, MODE_OPEN

    root, folder = _workspace(tmp_path)
    dialog = ChainFilesDialog(None, root, folder, mode=MODE_OPEN)

    assert dialog._list.count() == 2
    dialog._filter.setText('older')
    assert dialog._list.count() == 1
    assert dialog.selected_path.endswith('older.json')

    dialog._filter.setText('nothing matches')
    assert dialog._list.count() == 0
    assert not dialog._ok.isEnabled()


def test_save_dialog_names_the_file_inside_the_active_folder(qapp, tmp_path):
    from NGUI.dialogs.chain_files_dialog import ChainFilesDialog, MODE_SAVE

    root, folder = _workspace(tmp_path)
    dialog = ChainFilesDialog(None, root, folder, mode=MODE_SAVE, current='draft')

    assert dialog._name_edit.text() == 'draft'
    assert dialog.selected_path == os.path.join(folder, 'draft.json')
    assert 'Save' in dialog._target.text()

    # An existing chain is called out as an overwrite, not silently replaced.
    dialog._name_edit.setText('newer')
    assert dialog.selected_path == os.path.join(folder, 'newer.json')
    assert 'Overwrites' in dialog._target.text()

    # A name that is a path is refused: the folder is the active workspace's.
    dialog._name_edit.setText('sub/dir')
    assert not dialog._ok.isEnabled()
    dialog._name_edit.setText('')
    assert not dialog._ok.isEnabled()
