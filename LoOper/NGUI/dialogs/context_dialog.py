from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QCheckBox, QComboBox,
    QScrollArea, QFrame, QWidget, QSizePolicy, QRadioButton, QButtonGroup, QGroupBox
)
from PyQt5.QtCore import Qt
from .base_dialog import ModernDialog
from ..constants import (TEXT_COLOR, LIGHT_GREY, BLOCK_COLOR, BLOCK_HOVER, HAIRLINE,
                         WELL_BG, CONTROL_BG, ACCENT_COLOR, RADIUS_SM, RADIUS_MD, RADIUS_PILL)
from ..i18n import _
import json
import datetime


class _ContextEntryCard(QFrame):
    """A single formatted context entry shown in the dialog preview."""

    def __init__(self, title: str, body: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame {{ background-color: {BLOCK_COLOR}; border: 1px solid {HAIRLINE}; "
            f"border-radius: {RADIUS_MD}px; }}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {ACCENT_COLOR}; font-weight: bold;")
        body_lbl = QLabel(body)
        body_lbl.setWordWrap(True)
        body_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body_lbl.setStyleSheet(f"color: {TEXT_COLOR};")
        lay.addWidget(title_lbl)
        lay.addWidget(body_lbl)


class ContextDialog(ModernDialog):
    """Compact dialog for configuring a Context Node — just label + max history."""

    def __init__(self, parent=None, config=None, node_id=None, chain_id=None):
        super().__init__(parent, title=_("Context Node Configuration"), help_topic="context-node-dialog")
        self.resize(500, 400)

        self.config = config or {}
        self.node_id = node_id
        self.chain_id = chain_id

        # --- Label ---
        label_lbl = QLabel(_("Label:"))
        label_lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        self.label_edit = QLineEdit()
        self.label_edit.setText(self.config.get('label', ''))
        self.label_edit.setPlaceholderText(_("e.g. User preferences, Session history"))
        self.label_edit.setStyleSheet(self._input_style())

        # --- Max history ---
        history_lbl = QLabel(_("Max entries to keep per turn:"))
        history_lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        self.history_spin = QSpinBox()
        self.history_spin.setRange(1, 100)
        self.history_spin.setValue(int(self.config.get('max_history', 10)))
        self.history_spin.setStyleSheet(self._input_style())
        self.history_spin.valueChanged.connect(self._refresh_preview)

        # --- Lifetime mode (single mutually-exclusive choice) ---
        # "Persist across chain runs" and "clear when the chain finishes" are
        # opposite meanings of the same question; the executor treats a
        # clear_on_finish node as per-run scoped no matter what the persist
        # flag says, so legacy data with both flags true is shown as
        # "clear on finish".
        lifetime_title = QLabel(_("Context lifetime:"))
        lifetime_title.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        self.lifetime_group = QButtonGroup(self)
        self.radio_persist = QRadioButton(_("Persist across chain runs"))
        self.radio_persist.setToolTip(_("Context accumulates and is kept for the next time this chain runs"))
        self.radio_clear = QRadioButton(_("Clear context when the chain finishes"))
        self.radio_clear.setToolTip(_("Context is scoped to one run: it is reset at the start and cleared when the chain finishes"))
        self.lifetime_group.addButton(self.radio_persist)
        self.lifetime_group.addButton(self.radio_clear)
        _legacy_cof = bool(self.config.get('clear_on_finish', False))
        if _legacy_cof:
            self.radio_clear.setChecked(True)
        else:
            self.radio_persist.setChecked(True)

        # --- Preview (scrollable, per-entry formatted cards) ---
        preview_lbl = QLabel(_("Stored Contents (read-only):"))
        preview_lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")

        self.entries_scroll = QScrollArea()
        self.entries_scroll.setWidgetResizable(True)
        self.entries_scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {WELL_BG}; border: 1px solid "
            f"{HAIRLINE}; border-radius: {RADIUS_SM}px; }}"
            f"QScrollArea > QWidget > QWidget {{ background-color: {WELL_BG}; }}"
        )
        self.entries_container = QWidget()
        self.entries_layout = QVBoxLayout(self.entries_container)
        self.entries_layout.setContentsMargins(8, 8, 8, 8)
        self.entries_layout.setSpacing(6)
        self.entries_layout.addStretch()
        self.entries_scroll.setWidget(self.entries_container)
        self.entries_scroll.setMinimumHeight(160)
        self.entries_scroll.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )

        refresh_btn = QPushButton(_("Refresh"))
        refresh_btn.setStyleSheet(self._btn_style())
        refresh_btn.clicked.connect(self._refresh_preview)

        # --- Layout (two filled cards) ---
        config_card = QGroupBox(_("Configuration"))
        cc = QVBoxLayout(config_card)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(8)
        cc.addWidget(label_lbl)
        cc.addWidget(self.label_edit)
        cc.addSpacing(6)
        cc.addWidget(history_lbl)

        hrow = QHBoxLayout()
        hrow.addWidget(self.history_spin)
        _hist_hint = QLabel(_("The N most recent entries are served to downstream nodes"))
        _hist_hint.setWordWrap(True)
        hrow.addWidget(_hist_hint, 1)
        cc.addLayout(hrow)

        cc.addSpacing(6)
        cc.addWidget(lifetime_title)
        cc.addWidget(self.radio_persist)
        cc.addWidget(self.radio_clear)

        # --- Scope selector ---
        scope_lbl = QLabel(_("Scope:"))
        scope_lbl.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        self.scope_combo = QComboBox()
        self.scope_combo.addItem(_("This chain only (Local)"), 'local')
        self.scope_combo.addItem(_("All chains (Global)"), 'global')
        scope_val = self.config.get('scope', 'local')
        idx = self.scope_combo.findData(scope_val)
        if idx >= 0:
            self.scope_combo.setCurrentIndex(idx)
        self.scope_combo.setStyleSheet(self._input_style())
        self.scope_combo.setToolTip(_("Global scope makes context visible across ALL chains; Local scope restricts to this chain"))
        scope_row = QHBoxLayout()
        scope_row.addWidget(scope_lbl)
        scope_row.addWidget(self.scope_combo)
        scope_row.addStretch()
        cc.addLayout(scope_row)
        self.content_layout.addWidget(config_card)

        preview_card = QGroupBox(_("Stored Contents (read-only)"))
        pc = QVBoxLayout(preview_card)
        pc.setContentsMargins(0, 0, 0, 0)
        pc.setSpacing(8)
        pc.addWidget(self.entries_scroll, 1)
        pc.addWidget(refresh_btn, 0, Qt.AlignRight)
        self.content_layout.addWidget(preview_card, 1)

        # --- Buttons ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.setStyleSheet(self._btn_style())
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton(_("Save"))
        save_btn.setStyleSheet(self._btn_style().replace(LIGHT_GREY, ACCENT_COLOR).replace(TEXT_COLOR, "#121212"))
        save_btn.clicked.connect(self.accept)

        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        self.content_layout.addLayout(btn_row)

        # Load preview
        self._refresh_preview()

    def _refresh_preview(self):
        """Show stored context entries for this node.

        Pulls the node's rows across ALL chain namespaces (the GUI runs
        chains from deterministic temp copies whose chain id differs from
        the editor's chain file, while node ids are unique), plus the
        propagated shared copy for clone nodes.  The number of entries
        shown follows the "max entries per turn" spin (max_history).
        """
        self._clear_entries()

        if not self.node_id:
            self._show_placeholder(_("Save the chain first to see stored context."))
            return

        try:
            from LoOper.AI.context_database import ContextDatabase
            db = ContextDatabase()
            limit = max(1, int(self.history_spin.value() or 10))
            entries = []

            # 1. This node's rows across all chain namespaces.
            entries += self._format_pulled(
                db.pull(chain_id=None, node_id=self.node_id, limit=limit)
            )

            # 2. Propagated shared copy (clone inheritance).
            shared_file = str(self.config.get('shared_context_chain_file') or '').strip()
            if shared_file:
                src_id = self._resolve_source_id(shared_file)
                if src_id:
                    try:
                        copy = db.pull(
                            chain_id=self._shared_copy_ns(),
                            node_id=f"{src_id}:{self.node_id}",
                            limit=limit,
                        )
                        entries += self._format_pulled(copy, title_prefix=_("shared"))
                    except Exception:
                        pass
                    try:
                        feed = db.pull(
                            chain_id=self._shared_copy_ns(),
                            node_id=src_id,
                            limit=limit,
                        )
                        entries += self._format_pulled(feed, title_prefix=_("shared feed"))
                    except Exception:
                        pass
            db.close()

            if not entries:
                self._show_placeholder(_("No stored context yet."))
                return

            # Deduplicate identical bodies (the same node may have rows in
            # both the editor chain and the GUI temp chain namespace).
            seen_bodies = set()
            shown = 0
            for title, body in entries:
                if body in seen_bodies:
                    continue
                seen_bodies.add(body)
                card = _ContextEntryCard(title, body)
                self.entries_layout.insertWidget(self.entries_layout.count() - 1, card)
                shown += 1
                if shown >= max(1, limit * 3):
                    break
        except Exception as e:
            self._show_placeholder(f"Error: {e}")

    def _clear_entries(self):
        """Remove all entry cards from the preview scroll area."""
        while self.entries_layout.count() > 1:
            item = self.entries_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _show_placeholder(self, text: str):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {TEXT_COLOR}; padding: 12px;")
        self.entries_layout.insertWidget(self.entries_layout.count() - 1, lbl)

    @staticmethod
    def _format_pulled(keys_data, title_prefix: str = "") -> list:
        """Turn {key: [values]} pull results into (title, body) pairs."""
        entries = []
        for key, values in (keys_data or {}).items():
            for v in values:
                if isinstance(v, (dict, list)):
                    body = json.dumps(v, indent=2, ensure_ascii=False)
                else:
                    body = str(v)
                title = f"[{key}]" if not title_prefix else f"[{title_prefix} / {key}]"
                entries.append((title, body))
        return entries

    def _resolve_source_id(self, shared_file: str) -> str:
        """Resolve the source context node id (reference, then label/scope)."""
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
    def _shared_copy_ns() -> str:
        """The shared-copy namespace used by the executor."""
        try:
            from ...player.multi_sequence.worflow_interpreter_modules.executor_modules.context_ops import (
                ContextMixin,
            )
            return ContextMixin._SHARED_COPY_NS
        except Exception:
            return "__shared_copy__"

    def get_config(self):
        """Return configuration dict to be saved on the node."""
        clear_on_finish = self.radio_clear.isChecked()
        return {
            'label': self.label_edit.text().strip(),
            'max_history': self.history_spin.value(),
            'persistent': not clear_on_finish,
            'clear_on_finish': clear_on_finish,
            'scope': self.scope_combo.currentData(),
        }

    def _input_style(self):
        return f"""
            QLineEdit, QSpinBox {{
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
