"""chain_files_dialog.py — the app's own Open/Save chain dialogs.

The actions toolbar used the OS file dialog, which drops the user into
Explorer: every file on the machine, no workspace, no chain context, and a
modal window the app cannot theme.  These dialogs list the chains the app
actually has (every workspace, newest first) with a filter and, when saving,
the exact folder the file lands in.

Nothing here reads or writes a chain: it answers "which file?", and the
caller loads or saves it by the existing paths.
"""

import os
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QPushButton, QVBoxLayout,
                             QWidget)

from ..constants import (ACCENT_COLOR, BLOCK_HOVER, HAIRLINE, RADIUS_MD,
                         RADIUS_SM, TEXT_COLOR, TEXT_MUTED, WELL_BG)
from ..i18n import _
from .base_dialog import ModernDialog

MODE_OPEN = 'open'
MODE_SAVE = 'save'

#: Longest list shown at once.  A picker with more chains than this needs the
#: filter, and drawing thousands of rows costs more than it helps.
MAX_ROWS = 400

_LIST_STYLE = (
    "QListWidget {{ background: {well}; border: 1px solid {line};"
    " border-radius: {radius}px; color: {text}; padding: 4px; }}"
    "QListWidget::item {{ padding: 6px 8px; border-radius: {small}px; }}"
    "QListWidget::item:selected {{ background: {hover}; color: {accent}; }}"
    "QListWidget::item:hover {{ background: {hover}; }}"
).format(well=WELL_BG, line=HAIRLINE, radius=RADIUS_MD, small=RADIUS_SM,
         text=TEXT_COLOR, hover=BLOCK_HOVER, accent=ACCENT_COLOR)


def chain_entries(workspaces_root, extra_folders=()):
    """``[{'path', 'name', 'workspace', 'when', 'mtime'}]``, newest first."""
    folders = []
    try:
        from player import workspace

        for manifest in workspace.list_workspaces(workspaces_root or '') or ():
            slug = str(manifest.get('slug') or '')
            if not slug:
                continue
            folders.append((workspace.chains_dir(workspaces_root, slug),
                            str(manifest.get('name') or slug)))
    except Exception:
        pass
    for folder in extra_folders:
        if folder:
            folders.append((str(folder), os.path.basename(str(folder))))

    entries, seen = [], set()
    for folder, label in folders:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.lower().endswith('.json'):
                continue
            path = os.path.join(folder, name)
            real = os.path.normcase(os.path.abspath(path))
            if real in seen:
                continue
            seen.add(real)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            entries.append({
                'path': path,
                'name': name[:-5],
                'workspace': label,
                'mtime': mtime,
                'when': time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime)),
            })
    entries.sort(key=lambda entry: entry['mtime'], reverse=True)
    return entries[:MAX_ROWS]


class ChainFilesDialog(ModernDialog):
    """One dialog, two modes: pick a chain to open, or name one to save."""

    def __init__(self, parent, workspaces_root, folder, mode=MODE_OPEN,
                 current=''):
        title = _("Open chain") if mode == MODE_OPEN else _("Save chain")
        super().__init__(parent, title=title, help_topic='', 
                         show_help_button=False)
        self.setModal(True)
        self.resize(560, 620)
        self._mode = mode
        self._folder = str(folder or '')
        self._current = str(current or '')
        self._entries = chain_entries(workspaces_root, (self._folder,))
        self.selected_path = ''
        self._build_ui()
        self._fill()
        if mode == MODE_SAVE:
            self._name_edit.setText(self._current)
            self._sync_target()

    # -- layout ---------------------------------------------------------

    def _build_ui(self):
        layout = self.content_layout

        self._filter = QLineEdit()
        self._filter.setPlaceholderText(_("Filter by chain or workspace…"))
        self._filter.textChanged.connect(self._fill)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setStyleSheet(_LIST_STYLE)
        self._list.itemActivated.connect(self._on_activated)
        self._list.itemClicked.connect(self._on_clicked)
        self._list.currentItemChanged.connect(
            lambda *_args: self._on_row_changed())
        layout.addWidget(self._list, 1)

        if self._mode == MODE_SAVE:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            row_layout.addWidget(QLabel(_("File name:")))
            self._name_edit = QLineEdit()
            self._name_edit.setPlaceholderText(_("chain name (no .json)"))
            self._name_edit.textChanged.connect(self._sync_target)
            row_layout.addWidget(self._name_edit, 1)
            layout.addWidget(row)

        self._target = QLabel('')
        self._target.setWordWrap(True)
        self._target.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(self._target)

        buttons = QWidget()
        button_layout = QHBoxLayout(buttons)
        button_layout.setContentsMargins(0, 8, 0, 0)
        button_layout.addStretch(1)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        button_layout.addWidget(cancel)
        self._ok = QPushButton(_("Open") if self._mode == MODE_OPEN
                               else _("Save"))
        self._ok.setProperty("class", "primary")
        self._ok.setDefault(True)
        self._ok.clicked.connect(self.accept)
        button_layout.addWidget(self._ok)
        layout.addWidget(buttons)

    # -- contents -------------------------------------------------------

    def _fill(self, *_args):
        needle = self._filter.text().strip().lower()
        keep = self._current_path()
        self._list.blockSignals(True)
        try:
            self._list.clear()
            for entry in self._entries:
                haystack = ('%s %s' % (entry['name'], entry['workspace'])).lower()
                if needle and needle not in haystack:
                    continue
                item = QListWidgetItem('%s\n%s · %s' % (
                    entry['name'], entry['workspace'], entry['when']))
                item.setData(Qt.UserRole, entry['path'])
                item.setToolTip(entry['path'])
                self._list.addItem(item)
                if keep and entry['path'] == keep:
                    self._list.setCurrentItem(item)
            if self._list.currentItem() is None and self._list.count():
                self._list.setCurrentRow(0)
        finally:
            self._list.blockSignals(False)
        self._on_row_changed()

    def _current_path(self):
        item = self._list.currentItem() if hasattr(self, '_list') else None
        return str(item.data(Qt.UserRole)) if item is not None else ''

    def _on_row_changed(self):
        """Keep ``selected_path`` on the highlighted row (open mode)."""
        if self._mode != MODE_SAVE:
            self.selected_path = self._current_path()
            self._ok.setEnabled(bool(self.selected_path))

    def _on_activated(self, _item):
        if self._mode == MODE_OPEN:
            self.accept()

    def _on_clicked(self, item):
        """Save mode: clicking an existing chain fills its name in."""
        if self._mode != MODE_SAVE or item is None:
            return
        path = str(item.data(Qt.UserRole) or '')
        if path:
            self._name_edit.setText(os.path.basename(path)[:-5])

    def _sync_target(self):
        """Name → the file it will write, saying plainly when it overwrites."""
        name = self._name_edit.text().strip()
        if not name or any(ch in name for ch in '\\/:*?"<>|'):
            self._target.setText(_("Type a file name (no path, no .json)."))
            self._ok.setEnabled(False)
            return
        path = os.path.join(self._folder, name + '.json')
        if name.lower().endswith('.json'):
            path = os.path.join(self._folder, name)
        self.selected_path = path
        self._ok.setEnabled(True)
        if os.path.exists(path):
            self._target.setText(
                _("Overwrites %s") % path)
            self._target.setStyleSheet(
                f"color: {ACCENT_COLOR}; font-size: 11px;")
        else:
            self._target.setText(_("Saved to %s") % path)
            self._target.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 11px;")


def _dialog(parent, graph_view, mode, current=''):
    root = str(getattr(graph_view, 'workspaces_root', '') or '')
    folder = str(getattr(graph_view, 'chains_folder', '') or '')
    if not root and folder:
        root = os.path.dirname(os.path.abspath(folder))
    return ChainFilesDialog(parent, root, folder, mode=mode, current=current)


def ask_open_chain(parent, graph_view):
    """The chain file to open, or '' when the user cancels."""
    dialog = _dialog(parent, graph_view, MODE_OPEN)
    if dialog.exec_() != dialog.Accepted:
        return ''
    return dialog.selected_path


def ask_save_chain(parent, graph_view, current=''):
    """The chain file to save into, or '' when the user cancels."""
    dialog = _dialog(parent, graph_view, MODE_SAVE, current=current)
    if dialog.exec_() != dialog.Accepted:
        return ''
    return dialog.selected_path
