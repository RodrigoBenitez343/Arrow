"""agent_model_dialog.py — pick the model an agent reasons with.

The agent's reasoner is not a hidden default: it is a choice, and the app
already knows how to present that choice — an ENGINE selector (llama.cpp,
Ollama, every configured BYOK provider) and the models that engine serves.  This
dialog is that selector, reused by:

* the **MODEL** button in agent mode (per workspace, stored in
  ``workspace.json → agent.model``);
* the **New workspace** dialog, so the model is chosen when the agent is born.

It lives in ``NGUI/widgets`` on purpose: importing ``NGUI.dialogs`` pulls in
every dialog (and NodeGraphQt), which breaks the standalone agent build where
that package is excluded.

Nothing here guesses.  A model list that cannot be fetched is REPORTED in the
dialog (with the gateway's own error), never replaced by a made-up list; the
model combo stays editable so a name can still be typed when the gateway is
down, and the run reports it if the gateway cannot serve it.
"""

import logging

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
                             QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from ..constants import ACCENT_COLOR, TEXT_COLOR
from ..dialogs.engine_options import (engine_id, fill_engine_combo,
                                      models_for_engine, select_engine)
from ..i18n import _

logger = logging.getLogger(__name__)


class _ModelFetch(QThread):
    """Fetch the gateway's model list off the GUI thread."""

    done = pyqtSignal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            from AI.model_cache import get_real_cached_models
            models = [str(m) for m in (get_real_cached_models(timeout=5.0) or [])]
            self.done.emit(models, "")
        except Exception as exc:  # noqa: BLE001 - reported in the dialog
            logger.warning("could not list the gateway models", exc_info=True)
            self.done.emit([], str(exc))


class AgentModelDialog(QDialog):
    """Engine + model selector for a workspace's reasoner (or the builder's).

    The result is ``model()``: the reference every consumer already accepts —
    a GGUF path/name for the local engine, ``provider:model`` for BYOK, a bare
    tag for Ollama.  ``''`` means "no model chosen".
    """

    def __init__(self, parent=None, model="", engine="", title=""):
        super().__init__(parent)
        self.setWindowTitle(title or _("Agent model"))
        self.setMinimumWidth(420)
        self._worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        caption = QLabel(
            _("The model this agent reasons with. It decides what the request "
              "means and what is still missing; the chains do the work."), self)
        caption.setWordWrap(True)
        caption.setStyleSheet("color: rgba(255,255,255,0.72);")
        layout.addWidget(caption)

        engine_row = QHBoxLayout()
        engine_row.setSpacing(6)
        engine_label = QLabel(_("Engine"), self)
        engine_label.setStyleSheet("color: %s; font-weight: 600;" % TEXT_COLOR)
        engine_row.addWidget(engine_label)
        self.engine_combo = QComboBox(self)
        fill_engine_combo(self.engine_combo, engine)
        engine_row.addWidget(self.engine_combo, 1)
        self.refresh_btn = QPushButton(_("Refresh"), self)
        self.refresh_btn.setToolTip(_("Fetch the model list again"))
        self.refresh_btn.clicked.connect(self.reload_models)
        engine_row.addWidget(self.refresh_btn)
        layout.addLayout(engine_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(6)
        model_label = QLabel(_("Model"), self)
        model_label.setStyleSheet("color: %s; font-weight: 600;" % TEXT_COLOR)
        model_row.addWidget(model_label)
        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(260)
        model_row.addWidget(self.model_combo, 1)
        layout.addLayout(model_row)

        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            "color: rgba(255,255,255,0.55); font-size: 11px;")
        layout.addWidget(self.status)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok |
                                   QDialogButtonBox.Cancel, self)
        clear = QPushButton(_("Use no model"), self)
        clear.setToolTip(_("Clear the choice (the agent then cannot reason)"))
        clear.clicked.connect(self._clear)
        buttons.addButton(clear, QDialogButtonBox.ResetRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        self._all_models = []
        self._wanted = str(model or "")
        self._ready = False
        self.reload_models()

    # ----------------------------------------------------------- models

    def reload_models(self):
        """Ask the gateway for its models (never blocks the GUI thread)."""
        if self._worker is not None and self._worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.status.setText(_("Loading models…"))
        self._worker = _ModelFetch(self)
        self._worker.done.connect(self._models_ready)
        self._worker.start()

    def _models_ready(self, models, error):
        self._all_models = list(models or [])
        self.refresh_btn.setEnabled(True)
        if error:
            self.status.setText(
                _("Could not read the gateway's model list (%s). Type a model "
                  "name to use one the gateway already knows.") % error)
        elif not self._all_models:
            self.status.setText(
                _("The gateway serves no model yet — start the AI server, or "
                  "type a model name."))
        else:
            self.status.setText(
                _("%d model(s) available across the configured providers.")
                % len(self._all_models))
        self._ready = True
        self._fill_models()

    def _fill_models(self):
        """Models of the selected engine, plus the current choice."""
        engine = engine_id(self.engine_combo)
        models = models_for_engine(self._all_models, engine)
        current = self._wanted or str(self.model_combo.currentText() or "")
        if current and current not in models:
            models.insert(0, current)
        self.model_combo.clear()
        for name in models:
            self.model_combo.addItem(name)
        if current:
            index = self.model_combo.findText(current)
            if index < 0:
                self.model_combo.addItem(current)
                index = self.model_combo.count() - 1
            self.model_combo.setCurrentIndex(index)
        self._wanted = ""

    def _engine_changed(self, _index):
        if self._ready:
            self._wanted = str(self.model_combo.currentText() or "")
            self._fill_models()

    # ----------------------------------------------------------- result

    def _clear(self):
        self.model_combo.setEditText("")
        self.accept()

    def model(self):
        """The chosen model reference ('' when none)."""
        return str(self.model_combo.currentText() or "").strip()

    def engine(self):
        """The chosen engine id (the model's provider prefix still wins)."""
        return engine_id(self.engine_combo)


def choose_agent_model(parent, model="", engine="", title=""):
    """Run the dialog; returns the chosen model ('' on cancel/none)."""
    dialog = AgentModelDialog(parent, model=model, engine=engine, title=title)
    if dialog.exec_() != QDialog.Accepted:
        return None
    return dialog.model()
