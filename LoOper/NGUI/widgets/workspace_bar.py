"""workspace_bar.py — the workspace selector and creator on the canvas.

A workspace is one agent plus the chains it may use (``player/workspace.py``).
This bar is where you pick one and where you make a new one:

* the combo lists every workspace as ``Name · Agent`` and switching it scopes
  the chain library and agent mode to that workspace's chains folder;
* ``+`` opens the creator: workspace name, agent name, planner model.

Collections are gone: they were a per-chain string tag that grouped the
library but decided nothing.  A workspace decides what you see and what the
agent may route to.
"""

import logging
import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFrame,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton,
                             QVBoxLayout)

from ..constants import ACCENT_COLOR, HAIRLINE, SIDE_PANEL_BG, TEXT_COLOR
from ..i18n import _
from player import workspace as ws

logger = logging.getLogger(__name__)

_BAR_HEIGHT = 34


class NewWorkspaceDialog(QDialog):
    """Workspace name + agent name + the model that agent reasons with.

    The model is not typed by hand: it opens the same selector the rest of the
    app uses (engine + the models that engine serves, every configured
    provider), and it may stay empty — choosing it later is one click in agent
    mode (the MODEL button).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("New workspace"))
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.name_field = self._field(layout, _("Workspace"), _("Job hunt"))
        self.agent_field = self._field(layout, _("Agent name"), _("Scout"))

        caption = QLabel(_("Reasoner model"), self)
        caption.setStyleSheet('color: %s; font-weight: 600;' % TEXT_COLOR)
        layout.addWidget(caption)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._model_label = QLabel(_("(none)"), self)
        self._model_label.setStyleSheet(
            'color: rgba(255,255,255,0.65); font-size: 11px;')
        self._model_btn = QPushButton(_("Choose…"), self)
        self._model_btn.clicked.connect(self._choose_model)
        row.addWidget(self._model_label, 1)
        row.addWidget(self._model_btn)
        layout.addLayout(row)
        self._model = ""

        buttons = QDialogButtonBox(QDialogButtonBox.Ok |
                                   QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _field(self, layout, label, placeholder):
        caption = QLabel(label, self)
        caption.setStyleSheet('color: %s; font-weight: 600;' % TEXT_COLOR)
        field = QLineEdit(self)
        field.setPlaceholderText(placeholder)
        layout.addWidget(caption)
        layout.addWidget(field)
        return field

    def _choose_model(self):
        from .agent_model_dialog import AgentModelDialog
        dialog = AgentModelDialog(self, model=self._model,
                                  title=_("Agent model"))
        if dialog.exec_() != dialog.Accepted:
            return
        self._model = dialog.model()
        self._model_label.setText(self._model or _("(none)"))

    def values(self):
        """``(workspace_name, agent_name, model)`` with the name stripped."""
        name = self.name_field.text().strip()
        agent = self.agent_field.text().strip() or name
        return name, agent, self._model


class WorkspaceBar(QFrame):
    """Combo of workspaces + the creator button, floating on the canvas."""

    workspace_selected = pyqtSignal(str)
    workspace_created = pyqtSignal(str)

    def __init__(self, graph_view, parent=None):
        super().__init__(parent or graph_view)
        self.graph_view = graph_view
        self.setObjectName('workspaceBar')
        self.setStyleSheet(
            'QFrame#workspaceBar { background-color: %s;'
            ' border: 1px solid %s; border-radius: 10px; }'
            % (SIDE_PANEL_BG, HAIRLINE))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 6, 3)
        layout.setSpacing(6)

        self._label = QLabel(_('Workspace'), self)
        self._label.setStyleSheet('color: %s; font-weight: 600;' % TEXT_COLOR)
        layout.addWidget(self._label)

        self._combo = QComboBox(self)
        self._combo.setMinimumHeight(24)
        self._combo.setMinimumWidth(180)
        self._combo.setStyleSheet(
            'QComboBox { background: rgba(255,255,255,0.04); color: %s;'
            ' border: 1px solid %s; border-radius: 6px; padding: 2px 8px; }'
            'QComboBox QAbstractItemView { background: %s; color: %s; }'
            % (TEXT_COLOR, HAIRLINE, SIDE_PANEL_BG, TEXT_COLOR))
        self._combo.currentIndexChanged.connect(self._on_pick)
        layout.addWidget(self._combo)

        self._add_btn = QPushButton('+', self)
        self._add_btn.setFixedSize(24, 24)
        self._add_btn.setToolTip(_("New workspace (one agent + its own chains)"))
        self._add_btn.setStyleSheet(
            'QPushButton { background: rgba(255,255,255,0.06); color: %s;'
            ' border: 1px solid %s; border-radius: 6px; font-weight: 700; }'
            'QPushButton:hover { border-color: %s; }'
            % (TEXT_COLOR, HAIRLINE, ACCENT_COLOR))
        self._add_btn.clicked.connect(self.create_workspace)
        layout.addWidget(self._add_btn)

        self.setFixedHeight(_BAR_HEIGHT)
        self.refresh()

    # ------------------------------------------------------------ state

    def refresh(self):
        """Repopulate the combo from disk, keeping the active workspace."""
        try:
            workspaces = self.graph_view.workspaces()
        except Exception:
            logger.exception('the workspace list could not be read')
            workspaces = []
        active = self.graph_view.workspace_slug()
        self._combo.blockSignals(True)
        self._combo.clear()
        for manifest in workspaces:
            label = '%s · %s' % (manifest.get('name'), 
                                 (manifest.get('agent') or {}).get('name')
                                 or manifest.get('name'))
            self._combo.addItem(label, manifest.get('slug'))
            index = self._combo.count() - 1
            self._combo.setItemData(index, self._tooltip(manifest),
                                    Qt.ToolTipRole)
        index = self._combo.findData(active)
        if index >= 0:
            self._combo.setCurrentIndex(index)
        self._combo.blockSignals(False)

    def _tooltip(self, manifest):
        """What this workspace holds: its agent, chains and learned plans.

        The agent builds its own orchestrator, so how much it has learned is
        the honest state of it — shown here instead of being invisible.
        """
        agent = manifest.get('agent') or {}
        lines = ['Agent: %s' % (agent.get('name') or manifest.get('name'))]
        if agent.get('model'):
            lines.append('Planner model: %s' % agent['model'])
        if agent.get('legacy_brain'):
            lines.append('Pinned brain: %s'
                         % os.path.basename(agent['legacy_brain']))
        try:
            chains = len(ws.chain_files(self.graph_view.workspaces_root,
                                        manifest['slug']))
            lines.append('%d chain(s)' % chains)
            from player.agentic_ops import plans
            store = plans.PlanStore(ws.workspace_dir(
                self.graph_view.workspaces_root, manifest['slug']))
            learned = store.read()['plans']
            won = sum(int(plan.get('ok') or 0) for plan in learned)
            lines.append('%d learned plan(s), %d successful run(s)'
                         % (len(learned), won))
        except Exception:
            logger.debug('workspace tooltip: state unreadable', exc_info=True)
        return '\n'.join(lines)

    def _on_pick(self, _index):
        slug = self._combo.currentData()
        if slug and slug != self.graph_view.workspace_slug():
            self.workspace_selected.emit(str(slug))

    # ---------------------------------------------------------- creation

    def create_workspace(self):
        """Ask for name/agent/model, create the workspace and switch to it."""
        dialog = NewWorkspaceDialog(self)
        if dialog.exec_() != QDialog.Accepted:
            return None
        name, agent, model = dialog.values()
        if not name:
            return None
        manifest = ws.create(self.graph_view.workspaces_root, name, agent,
                             model)
        self.refresh()
        self.workspace_created.emit(manifest['slug'])
        return manifest
