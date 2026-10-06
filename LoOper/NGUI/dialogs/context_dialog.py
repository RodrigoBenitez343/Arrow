"""Context node dialog: configuration on top, a TAB per stored entry KIND.

The Context node is a knowledge POOL.  Its entries come in kinds, each with its
own tab so the user can audit just the stream they care about:

* **Documents** - the node's document file list, editable (drag files in).
* **Skills**    - skill files / free-text notes, editable (drag files in).
* **Learned**   - the learned answers stored per entry (the fact cache).
* **LLM Turns** - turns produced by wired-in LLM nodes.
* **All**       - every stored row.

Documents and Skills are DROP TARGETS: files dragged from the desktop are added
to the list.  Learned / Turns / All rows are editable and deletable in place.
"""

from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QComboBox,
    QScrollArea, QFrame, QWidget, QRadioButton, QButtonGroup, QGroupBox,
    QInputDialog, QMessageBox, QTabWidget, QListWidget, QAbstractItemView, QFileDialog,
)
from PyQt5.QtCore import Qt
from .base_dialog import ModernDialog
from ..constants import (TEXT_COLOR, LIGHT_GREY, BLOCK_COLOR, BLOCK_HOVER, HAIRLINE,
                         WELL_BG, CONTROL_BG, ACCENT_COLOR, RADIUS_SM, RADIUS_MD, RADIUS_PILL)
from ..i18n import _
import json
import os


def _card_btn_style() -> str:
    return (f"QPushButton {{ background-color: {CONTROL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px; "
            f"padding: 3px 10px; }}"
            f"QPushButton:hover {{ background-color: {BLOCK_HOVER}; }}")


class _ContextEntryCard(QFrame):
    """A single stored entry, with optional Edit / Delete actions.

    Read-only when no callbacks are given (a shared-feed entry); editable when
    the dialog passes ``on_edit`` / ``on_delete`` (a stored row it owns).
    """

    def __init__(self, title, body, on_edit=None, on_delete=None, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {BLOCK_COLOR}; border: 1px solid {HAIRLINE}; "
            f"border-radius: {RADIUS_MD}px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {ACCENT_COLOR}; font-weight: bold;")
        head.addWidget(title_lbl, 1)
        if on_edit is not None:
            _b = QPushButton(_("Edit"))
            _b.setStyleSheet(_card_btn_style())
            _b.clicked.connect(lambda: on_edit())
            head.addWidget(_b)
        if on_delete is not None:
            _b = QPushButton(_("Delete"))
            _b.setStyleSheet(_card_btn_style())
            _b.clicked.connect(lambda: on_delete())
            head.addWidget(_b)
        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body_lbl.setStyleSheet(f"color: {TEXT_COLOR};")
        lay.addLayout(head)
        lay.addWidget(body_lbl)


class _FileDropList(QListWidget):
    """A list that ACCEPTS files dropped from the desktop (added as paths)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setStyleSheet(
            f"QListWidget {{ background-color: {WELL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px; }}"
        )

    def add_unique(self, text):
        text = str(text or "").strip()
        if not text:
            return
        for i in range(self.count()):
            if self.item(i).text() == text:
                return
        self.addItem(text)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            for u in e.mimeData().urls():
                p = u.toLocalFile()
                if p:
                    self.add_unique(p)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class ContextDialog(ModernDialog):
    """Configuration + audit for a Context node, one tab per entry kind."""

    def __init__(self, parent=None, config=None, node_id=None, chain_id=None):
        super().__init__(parent, title=_("Context Node Configuration"),
                         help_topic="context-node-dialog")
        self.resize(1120, 840)

        self.config = config or {}
        self.node_id = node_id
        self.chain_id = chain_id

        self._build_config_card()
        self._build_tabs()
        self._build_buttons()
        self._load_config_lists()
        self._refresh_rows()

    # ------------------------------------------------------------------ config

    def _build_config_card(self):
        card = QGroupBox(_("Configuration"))
        grid = QGridLayout(card)
        grid.setContentsMargins(8, 12, 8, 8)
        grid.setSpacing(6)

        grid.addWidget(QLabel(_("Label:")), 0, 0)
        self.label_edit = QLineEdit()
        self.label_edit.setText(self.config.get('label', ''))
        self.label_edit.setPlaceholderText(_("e.g. User preferences, Session history"))
        self.label_edit.setStyleSheet(self._input_style())
        grid.addWidget(self.label_edit, 0, 1, 1, 3)

        grid.addWidget(QLabel(_("Serve last N (0 = all):")), 1, 0)
        self.history_spin = QSpinBox()
        self.history_spin.setRange(0, 100000)
        self.history_spin.setSpecialValueText(_("All (unlimited)"))
        self.history_spin.setValue(int(self.config.get('max_history', 0) or 0))
        self.history_spin.setStyleSheet(self._input_style())
        grid.addWidget(self.history_spin, 1, 1)
        _hint = QLabel(_("How many recent entries are SERVED to consumers at "
                         "run time (the audit tabs below always show everything)"))
        _hint.setWordWrap(True)
        _hint.setStyleSheet("color: #888; font-size: 10px;")
        grid.addWidget(_hint, 1, 2, 1, 2)

        grid.addWidget(QLabel(_("Scope:")), 2, 0)
        self.scope_combo = QComboBox()
        self.scope_combo.addItem(_("This chain only (Local)"), 'local')
        self.scope_combo.addItem(_("All chains (Global)"), 'global')
        _idx = self.scope_combo.findData(self.config.get('scope', 'local'))
        if _idx >= 0:
            self.scope_combo.setCurrentIndex(_idx)
        self.scope_combo.setStyleSheet(self._input_style())
        grid.addWidget(self.scope_combo, 2, 1)

        grid.addWidget(QLabel(_("Lifetime:")), 3, 0)
        self.lifetime_group = QButtonGroup(self)
        self.radio_persist = QRadioButton(_("Persist across runs"))
        self.radio_clear = QRadioButton(_("Clear when the chain finishes"))
        self.lifetime_group.addButton(self.radio_persist)
        self.lifetime_group.addButton(self.radio_clear)
        if bool(self.config.get('clear_on_finish', False)):
            self.radio_clear.setChecked(True)
        else:
            self.radio_persist.setChecked(True)
        life_row = QVBoxLayout()
        life_row.addWidget(self.radio_persist)
        life_row.addWidget(self.radio_clear)
        grid.addLayout(life_row, 3, 1, 1, 3)

        self.content_layout.addWidget(card)

    # -------------------------------------------------------------------- tabs

    def _build_tabs(self):
        self.tabs = QTabWidget()

        # Documents
        doc_tab = QWidget()
        _dl = QVBoxLayout(doc_tab)
        _dl_desc = QLabel(_("Document files the pool serves - drag files here, "
                            "or use Add File…:"))
        _dl_desc.setWordWrap(True)
        _dl.addWidget(_dl_desc)
        self.docs_list = _FileDropList()
        _dl.addWidget(self.docs_list, 1)
        _dl.addLayout(self._list_buttons(self.docs_list, with_note=False))
        self.tabs.addTab(doc_tab, _("Documents"))

        # Skills
        sk_tab = QWidget()
        _sl = QVBoxLayout(sk_tab)
        _sl_desc = QLabel(_("Skill files or free-text notes shared with consumers "
                            "- drag files here, add a note, or use Add File…:"))
        _sl_desc.setWordWrap(True)
        _sl.addWidget(_sl_desc)
        self.skills_list = _FileDropList()
        _sl.addWidget(self.skills_list, 1)
        _sl.addLayout(self._list_buttons(self.skills_list, with_note=True))
        self.tabs.addTab(sk_tab, _("Skills"))

        # Learned
        learned_tab = QWidget()
        _ll = QVBoxLayout(learned_tab)
        _ll_desc = QLabel(_("Learned answers the pool serves as facts - a wired-in "
                            "form filler reads these instead of re-deriving them. "
                            "Add one to seed a field, or edit / delete any entry:"))
        _ll_desc.setWordWrap(True)
        _ll.addWidget(_ll_desc)
        self.learned_scroll, self.learned_layout = self._make_scroll()
        _ll.addWidget(self.learned_scroll, 1)
        _ll.addLayout(self._entry_buttons())
        self.tabs.addTab(learned_tab, _("Learned"))

        # LLM turns
        self.turns_scroll, self.turns_layout = self._make_scroll()
        self.tabs.addTab(self.turns_scroll, _("LLM Turns"))

        # All
        self.all_scroll, self.all_layout = self._make_scroll()
        self.tabs.addTab(self.all_scroll, _("All"))

        self.content_layout.addWidget(self.tabs, 1)

    def _list_buttons(self, widget, with_note=False):
        row = QHBoxLayout()
        add_file = QPushButton(_("Add File…"))
        add_file.setStyleSheet(self._btn_style())
        add_file.clicked.connect(lambda: self._add_file(widget))
        row.addWidget(add_file)
        if with_note:
            add_note = QPushButton(_("Add Note…"))
            add_note.setStyleSheet(self._btn_style())
            add_note.clicked.connect(lambda: self._add_note(widget))
            row.addWidget(add_note)
        rem = QPushButton(_("Remove"))
        rem.setStyleSheet(self._btn_style())
        rem.clicked.connect(lambda: self._remove_selected(widget))
        row.addWidget(rem)
        row.addStretch()
        return row

    def _entry_buttons(self):
        row = QHBoxLayout()
        add = QPushButton(_("Add Entry…"))
        add.setStyleSheet(self._btn_style())
        add.clicked.connect(self._add_learned_entry)
        row.addWidget(add)
        row.addStretch()
        return row

    def _build_buttons(self):
        row = QHBoxLayout()
        refresh = QPushButton(_("Refresh"))
        refresh.setStyleSheet(self._btn_style())
        refresh.clicked.connect(self._refresh_rows)
        row.addWidget(refresh)
        row.addStretch()

        cancel = QPushButton(_("Cancel"))
        cancel.setStyleSheet(self._btn_style())
        cancel.clicked.connect(self.reject)
        save = QPushButton(_("Save"))
        save.setStyleSheet(self._btn_style().replace(LIGHT_GREY, ACCENT_COLOR)
                          .replace(TEXT_COLOR, "#121212"))
        save.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(save)
        self.content_layout.addLayout(row)

    def _make_scroll(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {WELL_BG}; border: 1px solid "
            f"{HAIRLINE}; border-radius: {RADIUS_SM}px; }}"
            f"QScrollArea > QWidget > QWidget {{ background-color: {WELL_BG}; }}"
        )
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        lay.addStretch()
        scroll.setWidget(container)
        return scroll, lay

    # ------------------------------------------------------------ config lists

    @staticmethod
    def _as_list(raw):
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = [raw] if raw.strip() else []
        return [str(x) for x in (raw or []) if str(x).strip()]

    def _load_config_lists(self):
        for p in self._as_list(self.config.get('documents')):
            self.docs_list.add_unique(p)
        for p in self._as_list(self.config.get('skills')):
            self.skills_list.add_unique(p)

    def _add_file(self, widget):
        paths, _f = QFileDialog.getOpenFileNames(
            self, _("Select File"), os.path.expanduser("~"),
            "All Files (*.*);;Text (*.txt *.md *.csv *.json);;"
            "Documents (*.docx *.pdf)")
        for p in (paths or []):
            widget.add_unique(p)

    def _add_note(self, widget):
        text, ok = QInputDialog.getMultiLineText(
            self, _("Add note"), _("Skill note:"))
        if ok and text.strip():
            widget.add_unique(text.strip())

    def _remove_selected(self, widget):
        for it in widget.selectedItems():
            widget.takeItem(widget.row(it))

    @staticmethod
    def _collect_list(widget):
        return [widget.item(i).text() for i in range(widget.count())
                if widget.item(i).text().strip()]

    # ---------------------------------------------------------------- audit

    def _refresh_rows(self):
        self._clear(self.learned_layout)
        self._clear(self.turns_layout)
        self._clear(self.all_layout)

        if not self.node_id:
            _msg = _("Save the chain first to see stored entries.")
            for lay in (self.learned_layout, self.turns_layout, self.all_layout):
                self._placeholder(lay, _msg)
            return

        rows, shared = [], []
        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            rows = db.pull_rows(node_id=self.node_id, limit=0)
            shared_file = str(self.config.get('shared_context_chain_file') or '').strip()
            if shared_file:
                src_id = self._resolve_source_id(shared_file)
                if src_id:
                    for _cid, _nid in (
                        (self._shared_copy_ns(), f"{src_id}:{self.node_id}"),
                        (self._shared_copy_ns(), src_id),
                    ):
                        try:
                            shared += db.pull_rows(chain_id=_cid, node_id=_nid, limit=0)
                        except Exception:
                            pass
            db.close()
        except Exception as e:
            self._placeholder(self.all_layout, f"Error: {e}")
            return

        learned = [r for r in rows
                   if str(r.get('key') or '').startswith('learned/')]
        turns = [r for r in rows
                 if str(r.get('source_type') or '').lower() == 'llm'
                 and not str(r.get('key') or '').startswith('learned/')]

        self._fill(self.learned_layout, learned,
                   empty=_("No learned answers stored yet."))
        self._fill(self.turns_layout, turns,
                   empty=_("No LLM turns stored yet."))
        self._fill(self.all_layout, list(rows) + list(shared),
                   empty=_("No stored context yet."))

    def _fill(self, layout, rows, empty):
        seen, shown = set(), 0
        for row in rows:
            body = self._row_body(row)
            if body in seen:
                continue
            seen.add(body)
            card = _ContextEntryCard(
                self._row_title(row), body,
                on_edit=lambda r=row: self._edit_row(r),
                on_delete=lambda r=row: self._delete_row(r),
            )
            layout.insertWidget(layout.count() - 1, card)
            shown += 1
            if shown >= 1500:
                break
        if not shown:
            self._placeholder(layout, empty)

    def _clear(self, layout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _placeholder(self, layout, text):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {TEXT_COLOR}; padding: 12px;")
        layout.insertWidget(layout.count() - 1, lbl)

    @staticmethod
    def _row_body(row):
        v = row.get('value')
        if isinstance(v, (dict, list)):
            return json.dumps(v, indent=2, ensure_ascii=False)
        return str(v)

    @staticmethod
    def _row_title(row):
        key = row.get('key') or ''
        src = row.get('source_type') or ''
        return f"[{key}] ({src})" if src else f"[{key}]"

    def _add_learned_entry(self):
        """Seed a learned FACT into the pool (key 'learned/<label>').

        The pool re-renders learned rows into the form filler's correction
        block, so an entry added here is served as an answer WITHOUT a probe -
        the same entry the per-card Edit / Delete act on.
        """
        if not self.node_id:
            QMessageBox.information(
                self, _("Save the chain first"),
                _("Save the chain first to store entries."))
            return
        label, ok = QInputDialog.getText(
            self, _("Add learned entry"), _("Field / question:"))
        if not ok or not label.strip():
            return
        value, ok = QInputDialog.getMultiLineText(
            self, _("Add learned entry"),
            _("Answer for '%s':") % label.strip())
        if not ok or not value.strip():
            return
        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            db.push(chain_id=self.chain_id or '', node_id=self.node_id,
                    key="learned/%s" % label.strip(), value=value.strip(),
                    source_type='learned')
            db.close()
        except Exception as e:
            QMessageBox.warning(self, _("Error"), str(e))
            return
        self._refresh_rows()

    def _edit_row(self, row):
        text, ok = QInputDialog.getMultiLineText(
            self, _("Edit entry"), self._row_title(row), self._row_body(row))
        if not ok:
            return
        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            db.update_entry(row.get('id'), text)
            db.close()
        except Exception as e:
            self._placeholder(self.all_layout, f"Error: {e}")
            return
        self._refresh_rows()

    def _delete_row(self, row):
        if QMessageBox.question(
                self, _("Delete entry"),
                _("Delete this entry?\n\n%s") % self._row_body(row)[:400],
        ) != QMessageBox.Yes:
            return
        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            db.delete_entry(row.get('id'))
            db.close()
        except Exception as e:
            self._placeholder(self.all_layout, f"Error: {e}")
            return
        self._refresh_rows()

    def _resolve_source_id(self, shared_file):
        try:
            from ...player.multi_sequence.worflow_interpreter_modules.executor_modules.context_ops import (
                ContextMixin,
            )
            return ContextMixin._resolve_clone_source(
                shared_file,
                str(self.config.get('shared_context_node_id') or '').strip(),
                str(self.config.get('label') or '').strip(),
                str(self.config.get('scope') or 'local'),
            )
        except Exception:
            return str(self.config.get('shared_context_node_id') or '').strip()

    @staticmethod
    def _shared_copy_ns():
        try:
            from ...player.multi_sequence.worflow_interpreter_modules.executor_modules.context_ops import (
                ContextMixin,
            )
            return ContextMixin._SHARED_COPY_NS
        except Exception:
            return "__shared_copy__"

    # ------------------------------------------------------------------- save

    def get_config(self):
        clear_on_finish = self.radio_clear.isChecked()
        return {
            'label': self.label_edit.text().strip(),
            'max_history': self.history_spin.value(),
            'persistent': not clear_on_finish,
            'clear_on_finish': clear_on_finish,
            'scope': self.scope_combo.currentData(),
            'documents': json.dumps(self._collect_list(self.docs_list)),
            'skills': json.dumps(self._collect_list(self.skills_list)),
        }

    # ----------------------------------------------------------------- styles

    def _input_style(self):
        return f"""
            QLineEdit, QSpinBox, QComboBox {{
                background-color: {CONTROL_BG};
                color: {TEXT_COLOR};
                border: 1px solid {HAIRLINE};
                padding: 6px 8px;
                border-radius: {RADIUS_SM}px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {LIGHT_GREY};
                border: none;
            }}
        """

    def _btn_style(self):
        return f"""
            QPushButton {{
                background-color: {CONTROL_BG};
                color: {TEXT_COLOR};
                border: 1px solid {HAIRLINE};
                padding: 6px 16px;
                border-radius: {RADIUS_PILL}px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {BLOCK_HOVER};
            }}
        """
