# dialogs/ai_settings_dialog.py

import json
import logging
import os
import subprocess
import sys

import requests
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..constants import (ACCENT_COLOR, DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR,
                         HAIRLINE, BTN_PRIMARY_TEXT)
from ..i18n import _
from .base_dialog import ModernDialog, UserGuideDialog
from .toggle_switch import ModernToggle

logger = logging.getLogger(__name__)


def _byok_module():
    """Import AI.byok from either layout (source or frozen); None on failure."""
    try:
        from AI import byok
        return byok
    except Exception:
        pass
    try:
        ai_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "AI"))
        root = os.path.dirname(ai_dir)
        if root not in sys.path:
            sys.path.insert(0, root)
        from AI import byok
        return byok
    except Exception:
        return None


class ProviderJob(QThread):
    """Off-thread model listing / connection test (network must not block UI)."""

    done = pyqtSignal(str, str, dict)  # (task, provider_id, result)

    def __init__(self, task, provider_id, settings):
        super().__init__()
        self._task = task
        self._provider_id = provider_id
        self._settings = settings

    def run(self):
        byok = _byok_module()
        if byok is None:
            self.done.emit(self._task, self._provider_id,
                           {"ok": False,
                            "message": _("Provider runtime unavailable.")})
            return
        try:
            if self._task == "models":
                models = byok.list_models(self._provider_id, self._settings)
                self.done.emit(self._task, self._provider_id,
                               {"ok": True, "models": [m["id"] for m in models]})
            else:
                result = byok.test_connection(self._provider_id, self._settings)
                self.done.emit(self._task, self._provider_id, result or {})
        except Exception as e:
            self.done.emit(self._task, self._provider_id,
                           {"ok": False, "message": str(e)})


class APIServerThread(QThread):
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.server = None

    def run(self):
        try:
            self.running = True
            self.status_update.emit("Starting local API server...")
            # Force module reload so code changes are picked up.
            # Python caches modules; importlib.reload() must be called explicitly
            # for each module, since it does NOT cascade to sub-imports.
            import importlib

            if "AI.llama_cpp_engine" in sys.modules:
                del sys.modules["AI.llama_cpp_engine"]
            if "AI.api" in sys.modules:
                del sys.modules["AI.api"]

            from AI.api import app

            # Local-only API gateway; remote access now happens through
            # Agent Mode (Tailscale QR) instead of a Tailscale-exposed API.
            host = "127.0.0.1"
            port = int(os.getenv("API_PORT", "8000"))
            import uvicorn

            # Safe logging configuration for frozen environments
            log_config = {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "default": {
                        "()": "uvicorn.logging.DefaultFormatter",
                        "fmt": "%(levelprefix)s %(message)s",
                        "use_colors": False,
                    },
                    "access": {
                        "()": "uvicorn.logging.AccessFormatter",
                        "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                        "use_colors": False,
                    },
                },
                "handlers": {
                    "default": {
                        "formatter": "default",
                        "class": "logging.StreamHandler",
                        "stream": "ext://sys.stdout",
                    },
                    "access": {
                        "formatter": "access",
                        "class": "logging.StreamHandler",
                        "stream": "ext://sys.stdout",
                    },
                },
                "loggers": {
                    "uvicorn": {
                        "handlers": ["default"],
                        "level": "INFO",
                        "propagate": False,
                    },
                    "uvicorn.error": {"level": "INFO"},
                    "uvicorn.access": {
                        "handlers": ["access"],
                        "level": "INFO",
                        "propagate": False,
                    },
                },
            }

            # In some frozen environments, uvicorn formatters might fail to load via string.
            # We can try to provide the actual classes if they are importable.
            try:
                import uvicorn.logging

                log_config["formatters"]["default"]["()"] = (
                    uvicorn.logging.DefaultFormatter
                )
                log_config["formatters"]["access"]["()"] = (
                    uvicorn.logging.AccessFormatter
                )
            except Exception:
                # Fallback to standard logging formatters if uvicorn's are not found
                log_config["formatters"]["default"] = {
                    "format": "%(levelname)s: %(message)s"
                }
                log_config["formatters"]["access"] = {
                    "format": "%(levelname)s: %(message)s"
                }

            config = uvicorn.Config(
                app, host=host, port=port, log_level="info", log_config=log_config
            )
            self.server = uvicorn.Server(config)
            self.status_update.emit(f"Uvicorn starting on {host}:{port}")
            self.server.run()
            if self.running:
                self.error_occurred.emit("API server stopped")
        except Exception as e:
            self.error_occurred.emit(str(e))

    def stop(self):
        self.running = False
        try:
            if self.server:
                self.server.should_exit = True
        except Exception:
            pass


class AISettingsDialog(ModernDialog):
    """Dialog for configuring AI settings"""

    def __init__(self, parent=None):
        super().__init__(parent, title=_("AI Settings"), help_topic="general")
        self.setModal(True)

        # Style logic moved to ModernDialog

        # Server thread
        self.server_thread = None

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.check_api_status)

        self.setup_ui()
        self.load_settings()
        self._load_byok_into_ui()

        # Check API status
        QTimer.singleShot(100, self.check_api_status)

    def _open_user_guide(self, topic):
        dlg = UserGuideDialog(self, topic=topic)
        dlg.exec_()

    def _make_help_button(self, topic):
        btn = QPushButton("?")
        btn.setFixedSize(24, 24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                color: {TEXT_COLOR};
                background: transparent;
                border: 1px solid {HAIRLINE};
                border-radius: 12px;
                font-size: 12px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_COLOR};
                color: {BTN_PRIMARY_TEXT};
                border: 1px solid {ACCENT_COLOR};
            }}
        """)
        btn.clicked.connect(lambda: self._open_user_guide(topic))
        return btn

    def setup_ui(self):
        # Use content_layout from ModernDialog
        layout = self.content_layout

        # Create tab widget
        tab_widget = QTabWidget()
        self._tab_widget = tab_widget

        # API Configuration Tab
        api_tab = QWidget()
        api_scroll = QScrollArea()
        api_scroll.setWidgetResizable(True)
        api_scroll_widget = QWidget()
        api_layout = QVBoxLayout()

        api_help_row = QHBoxLayout()
        api_help_row.addStretch()
        api_help_row.addWidget(self._make_help_button("sandboxing"))
        api_layout.addLayout(api_help_row)

        # Local API Server
        local_group = QGroupBox(_("Local API Server"))
        local_layout = QVBoxLayout()

        info_label = QLabel(
            _("Run the API server locally if you have sufficient resources")
        )
        info_label.setWordWrap(True)
        local_layout.addWidget(info_label)

        local_form = QFormLayout()
        self.api_port_edit = QSpinBox()
        self.api_port_edit.setRange(1000, 65535)
        self.api_port_edit.setValue(8000)
        local_form.addRow(_("API Port:"), self.api_port_edit)

        self.ollama_host_edit = QLineEdit()
        self.ollama_host_edit.setPlaceholderText(_("e.g., localhost"))
        local_form.addRow(_("Ollama Host:"), self.ollama_host_edit)

        self.ollama_port_edit = QSpinBox()
        self.ollama_port_edit.setRange(1000, 65535)
        self.ollama_port_edit.setValue(11434)
        local_form.addRow(_("Ollama Port:"), self.ollama_port_edit)
        local_layout.addLayout(local_form)

        server_btn_layout = QHBoxLayout()
        self.start_server_btn = QPushButton(_("Start Local Server"))
        self.start_server_btn.clicked.connect(self.start_local_server)

        self.stop_server_btn = QPushButton(_("Stop Local Server"))
        self.stop_server_btn.clicked.connect(self.stop_local_server)
        self.stop_server_btn.setEnabled(False)

        server_btn_layout.addWidget(self.start_server_btn)
        server_btn_layout.addWidget(self.stop_server_btn)
        server_btn_layout.addStretch()

        local_layout.addLayout(server_btn_layout)
        local_group.setLayout(local_layout)
        api_layout.addWidget(local_group)

        # Status Display
        status_group = QGroupBox(_("Connection Status"))
        status_layout = QVBoxLayout()

        self.status_label = QLabel(_("Checking connection..."))
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)

        self.models_text = QTextEdit()
        self.models_text.setMaximumHeight(80)  # Reduced height
        self.models_text.setReadOnly(True)
        status_layout.addWidget(QLabel(_("Available Models:")))
        status_layout.addWidget(self.models_text)

        # Test connection button
        test_btn_layout = QHBoxLayout()
        self.test_connection_btn = QPushButton(_("Test Connection"))
        self.test_connection_btn.clicked.connect(self.test_connection)
        test_btn_layout.addWidget(self.test_connection_btn)
        test_btn_layout.addStretch()
        status_layout.addLayout(test_btn_layout)

        status_group.setLayout(status_layout)
        api_layout.addWidget(status_group)

        # Set up scroll area
        api_scroll_widget.setLayout(api_layout)
        api_scroll.setWidget(api_scroll_widget)

        # Set up the main tab layout
        api_tab_layout = QVBoxLayout()
        api_tab_layout.addWidget(api_scroll)
        api_tab.setLayout(api_tab_layout)
        tab_widget.addTab(api_tab, _("API Configuration"))

        # Advanced Tab
        advanced_tab = QWidget()
        advanced_layout = QVBoxLayout()

        adv_help_row = QHBoxLayout()
        adv_help_row.addStretch()
        adv_help_row.addWidget(self._make_help_button("sandboxing"))
        advanced_layout.addLayout(adv_help_row)

        advanced_group = QGroupBox(_("Advanced Settings"))
        advanced_form = QFormLayout()

        self.auto_start_checkbox = ModernToggle()
        advanced_form.addRow(_("Auto-start local server:"), self.auto_start_checkbox)

        self.timeout_edit = QSpinBox()
        self.timeout_edit.setRange(5, 300)
        self.timeout_edit.setValue(30)
        self.timeout_edit.setSuffix(_(" seconds"))
        advanced_form.addRow(_("Connection timeout:"), self.timeout_edit)

        advanced_group.setLayout(advanced_form)
        advanced_layout.addWidget(advanced_group)
        advanced_layout.addStretch()

        advanced_tab.setLayout(advanced_layout)
        tab_widget.addTab(advanced_tab, _("Advanced"))

        # Providers tab: BYOK cloud keys, local servers, CLI tools + the
        # per-capability routing (chat / embeddings / vision).
        tab_widget.addTab(self._build_providers_tab(), _("Providers"))

        layout.addWidget(tab_widget)

        # Dialog buttons using QDialogButtonBox for consistency
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.button_box.accepted.connect(self.save_settings)
        self.button_box.rejected.connect(self.reject)
        try:
            save_btn = self.button_box.button(QDialogButtonBox.Save)
            cancel_btn = self.button_box.button(QDialogButtonBox.Cancel)
            if save_btn:
                save_btn.setText(_("Save"))
            if cancel_btn:
                cancel_btn.setText(_("Cancel"))
        except Exception:
            pass

        layout.addWidget(self.button_box)
        # self.setLayout(layout) # Removed as we use ModernDialog's layout logic

    # ── Providers tab (BYOK) ──────────────────────────────────────────

    def _build_providers_tab(self):
        """Provider keys/servers + the per-capability routing section."""
        self._byok = {}
        self._provider_jobs = []
        self.role_provider_combos = {}
        self.role_model_edits = {}
        self.provider_rows = {}

        tab = QWidget()
        outer = QVBoxLayout(tab)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        lay = QVBoxLayout(host)

        info = QLabel(_(
            "Bring your own keys: cloud providers, local model servers or "
            "CLI tools. Toggle on every provider you want available — they "
            "work in parallel, each keeping its own key, server/command and "
            "model. Keys are stored on this machine (byok.json)."))
        info.setWordWrap(True)
        lay.addWidget(info)

        # --- one row per provider, every provider rendered at once ---
        byok = _byok_module()
        if byok is not None:
            for choice in byok.provider_choices():
                self._add_provider_row(lay, choice)

        # --- routing (fallback only; per-site pickers always win) ---
        route_group = QGroupBox(
            _("Fallback routing — used when a step doesn't pick its own model"))
        route_form = QFormLayout()
        role_labels = {
            "chat": _("Chat / generation:"),
            "embeddings": _("Embeddings:"),
            "vision": _("Vision / images:"),
        }
        if byok is not None:
            for key in ("chat", "embeddings", "vision"):
                combo = QComboBox()
                combo.addItem(_("Auto (legacy)"), "")
                for choice in byok.provider_choices():
                    combo.addItem(choice["label"], choice["id"])
                    if choice["coming_soon"]:
                        item = combo.model().item(combo.count() - 1)
                        if item is not None:
                            item.setEnabled(False)
                            item.setToolTip(_("Coming soon"))
                edit = QLineEdit()
                edit.setPlaceholderText(_("model id (optional)"))
                row = QWidget()
                row_lay = QHBoxLayout(row)
                row_lay.setContentsMargins(0, 0, 0, 0)
                row_lay.addWidget(combo, 1)
                row_lay.addWidget(edit, 1)
                route_form.addRow(QLabel(role_labels[key]), row)
                self.role_provider_combos[key] = combo
                self.role_model_edits[key] = edit

        route_note = QLabel(_(
            "Every node and picker can choose its own provider:model — the "
            "model lists now include cloud entries like \"openai:gpt-4o-mini\". "
            "This section only fills the gaps: embeddings locally while the "
            "LLM runs in the cloud → Chat = a cloud provider; everything "
            "local → leave everything on Auto. Laya and needle2 always run "
            "locally."))
        route_note.setWordWrap(True)
        route_form.addRow(route_note)
        route_group.setLayout(route_form)
        lay.addWidget(route_group)

        lay.addStretch()
        scroll.setWidget(host)
        outer.addWidget(scroll)
        return tab

    def _add_provider_row(self, parent_layout, choice):
        """One provider as its own row: enable toggle + its family fields.

        Toggling several on makes them all available in parallel — every
        model picker then lists ``provider:model`` for each enabled provider.
        """
        pid = choice.get("id")
        family = choice.get("family", "cloud")
        is_engine = pid == "llamacpp"
        is_pool = family == "pool"
        is_cloud = family == "cloud"
        is_local = family == "local-server" and not is_engine
        is_cli = family == "cli"

        box = QGroupBox(choice.get("label", pid))
        form = QFormLayout(box)

        toggle = None
        if not is_engine and not is_pool:
            toggle = ModernToggle()
            form.addRow(_("Available:"), toggle)

        key_edit = saved_label = None
        if is_cloud:
            key_edit = QLineEdit()
            key_edit.setEchoMode(QLineEdit.Password)
            key_edit.setPlaceholderText(_("Paste the API key"))
            saved_label = QLabel("")
            saved_label.setStyleSheet(f"color: {ACCENT_COLOR};")
            key_row = QWidget()
            key_lay = QHBoxLayout(key_row)
            key_lay.setContentsMargins(0, 0, 0, 0)
            key_lay.addWidget(key_edit, 1)
            key_lay.addWidget(saved_label)
            form.addRow(_("API key:"), key_row)

        url_edit = None
        if is_local:
            url_edit = QLineEdit()
            url_edit.setPlaceholderText(_("e.g. http://localhost:11434"))
            form.addRow(_("Server URL:"), url_edit)

        command_edit = None
        if is_cli:
            command_edit = QLineEdit()
            command_edit.setPlaceholderText(_("e.g. claude or codex"))
            form.addRow(_("Command (CLI):"), command_edit)

        model_combo = refresh_btn = test_btn = None
        if is_pool or is_engine:
            status_label = QLabel(
                _("Coming soon") if is_pool
                else _("Served by the built-in llama.cpp engine."))
            status_label.setWordWrap(True)
            if is_pool:
                status_label.setStyleSheet(f"color: {MEDIUM_GREY};")
            form.addRow(status_label)
        else:
            model_combo = QComboBox()
            model_combo.setEditable(True)
            refresh_btn = QPushButton(_("Refresh Models"))
            refresh_btn.clicked.connect(
                lambda _c, p=pid: self._refresh_provider_models(p))
            model_row = QWidget()
            model_lay = QHBoxLayout(model_row)
            model_lay.setContentsMargins(0, 0, 0, 0)
            model_lay.addWidget(model_combo, 1)
            model_lay.addWidget(refresh_btn)
            form.addRow(_("Model:"), model_row)

            test_btn = QPushButton(_("Test Connection"))
            test_btn.clicked.connect(lambda _c, p=pid: self._test_provider(p))
            status_label = QLabel("")
            status_label.setWordWrap(True)
            status_row = QWidget()
            status_lay = QHBoxLayout(status_row)
            status_lay.setContentsMargins(0, 0, 0, 0)
            status_lay.addWidget(test_btn)
            status_lay.addWidget(status_label, 1)
            form.addRow("", status_row)

        self.provider_rows[pid] = {
            "box": box, "family": family, "toggle": toggle,
            "key": key_edit, "saved": saved_label, "url": url_edit,
            "command": command_edit, "model": model_combo,
            "refresh": refresh_btn, "test": test_btn, "status": status_label,
        }
        parent_layout.addWidget(box)

    def _load_byok_into_ui(self):
        """Populate every provider row from byok.json (never raises)."""
        byok = _byok_module()
        if byok is None:
            return
        try:
            self._byok = byok.load_settings()
            for pid, row in self.provider_rows.items():
                stored = byok.get_provider_settings(self._byok, pid)
                if row["key"] is not None:
                    row["key"].setText(str(stored.get("api_key") or ""))
                if row["saved"] is not None:
                    row["saved"].setText(
                        _("saved") if stored.get("credential_saved") else "")
                if row["url"] is not None:
                    row["url"].setText(str(stored.get("url") or ""))
                if row["command"] is not None:
                    row["command"].setText(str(stored.get("command") or ""))
                if row["model"] is not None:
                    row["model"].clear()
                    for model_id in stored.get("available_models") or []:
                        row["model"].addItem(str(model_id))
                    if stored.get("model"):
                        row["model"].setCurrentText(str(stored["model"]))
                if row["toggle"] is not None:
                    row["toggle"].setChecked(bool(stored.get("enabled")))
                if row["refresh"] is not None:
                    row["status"].setText(
                        str(stored.get("model_refresh_message") or ""))
            roles = self._byok.get("roles") or {}
            for key, combo in self.role_provider_combos.items():
                stored = str((roles.get(key) or {}).get("provider") or "")
                idx = combo.findData(stored)
                combo.setCurrentIndex(idx if idx >= 0 else 0)
                self.role_model_edits[key].setText(
                    str((roles.get(key) or {}).get("model") or ""))
        except Exception:
            logger.warning("Failed to load BYOK settings into the UI",
                           exc_info=True)

    def _collect_byok_settings(self):
        """Every provider row + routing combos → the working settings dict."""
        providers = self._byok.setdefault("providers", {})
        for pid, row in self.provider_rows.items():
            family = row["family"]
            if pid == "llamacpp" or family == "pool":
                continue
            key = row["key"].text().strip() if row["key"] is not None else ""
            url = row["url"].text().strip() if row["url"] is not None else ""
            command = (row["command"].text().strip()
                       if row["command"] is not None else "")
            model = (row["model"].currentText().strip()
                     if row["model"] is not None else "")
            toggle_on = bool(row["toggle"] is not None
                             and row["toggle"].isChecked())
            # Skip rows the user never touched — no empty-provider bloat.
            if not (providers.get(pid) or key or url or command or model
                    or toggle_on):
                continue
            prov = providers.setdefault(pid, {})
            if key:
                prov["api_key"] = key
                prov["credential_saved"] = True
            prov["url"] = url
            prov["command"] = command
            prov["model"] = model
            if row["model"] is not None:
                models = [row["model"].itemText(i)
                          for i in range(row["model"].count())]
                if models:
                    prov["available_models"] = models
            if row["toggle"] is not None:
                prov["enabled"] = toggle_on
        roles = self._byok.setdefault("roles", {})
        for key, combo in self.role_provider_combos.items():
            roles[key] = {
                "provider": combo.currentData() or "",
                "model": self.role_model_edits[key].text().strip(),
            }
        return self._byok

    def _save_byok(self):
        """Persist providers + routing into byok.json (never raises)."""
        byok = _byok_module()
        if byok is None:
            return
        try:
            byok.save_settings(self._collect_byok_settings())
        except Exception:
            logger.warning("Failed to save BYOK settings", exc_info=True)

    def _refresh_provider_models(self, provider_id):
        if provider_id:
            self._start_provider_job("models", provider_id)

    def _test_provider(self, provider_id):
        if provider_id:
            self._start_provider_job("test", provider_id)

    def _start_provider_job(self, task, provider_id):
        """Run list/test on a worker so the dialog never blocks."""
        row = self.provider_rows.get(provider_id)
        try:
            settings = self._collect_byok_settings()
        except Exception:
            settings = self._byok
        if row is not None:
            row["status"].setText(_("Working…"))
            if row["refresh"] is not None:
                row["refresh"].setEnabled(False)
            if row["test"] is not None:
                row["test"].setEnabled(False)
        job = ProviderJob(task, provider_id, settings)
        job.done.connect(self._on_provider_job_done)
        job.finished.connect(job.deleteLater)
        job.finished.connect(
            lambda p=provider_id: self._provider_job_finished(p))
        self._provider_jobs.append(job)
        job.start()

    def _provider_job_finished(self, provider_id):
        row = self.provider_rows.get(provider_id)
        if row is None:
            return
        if row["refresh"] is not None:
            row["refresh"].setEnabled(True)
        if row["test"] is not None:
            row["test"].setEnabled(True)

    def _on_provider_job_done(self, task, provider_id, result):
        row = self.provider_rows.get(provider_id)
        if row is None:
            return
        if task == "models":
            models = [str(m) for m in (result.get("models") or [])]
            if models and row["model"] is not None:
                current = row["model"].currentText()
                row["model"].clear()
                row["model"].addItems(models)
                if current:
                    row["model"].setCurrentText(current)
                message = _("{n} model(s) found.").format(n=len(models))
                row["status"].setText(message)
                prov = self._byok.setdefault("providers", {}).setdefault(
                    provider_id, {})
                prov["available_models"] = models
                prov["has_fetched_models"] = True
                prov["model_refresh_message"] = message
            else:
                row["status"].setText(
                    str(result.get("message") or _("No models returned.")))
            return
        ok = bool(result.get("ok"))
        message = str(result.get("message") or "")
        row["status"].setText(("OK — " if ok else "") + message)
        try:
            import time as _time
            self._byok.setdefault("verification", {})[provider_id] = {
                "ok": ok,
                "message": message,
                "model_id": (row["model"].currentText().strip()
                             if row["model"] is not None else ""),
                "tested_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        except Exception:
            pass

    def load_settings(self):
        """Load settings from config file via config_loader"""
        try:
            # Defer to shared config loader to ensure consistent precedence
            ai_dir = os.path.normpath(
                os.path.join(os.path.dirname(__file__), "..", "..", "AI")
            )
            if ai_dir not in sys.path:
                sys.path.insert(0, ai_dir)
            from config_loader import load_ai_config

            cfg = load_ai_config()

            self.api_port_edit.setValue(int(cfg.get("API_PORT", "8000")))
            self.ollama_host_edit.setText(cfg.get("OLLAMA_HOST", "localhost"))
            self.ollama_port_edit.setValue(int(cfg.get("OLLAMA_PORT", "11434")))
            self.timeout_edit.setValue(int(cfg.get("API_TIMEOUT", "30")))
            self.auto_start_checkbox.setChecked(
                cfg.get("AUTO_START_API", "false").lower() == "true"
            )
        except Exception:
            # Fallback to environment variables
            self.api_port_edit.setValue(int(os.getenv("API_PORT", "8000")))
            self.ollama_host_edit.setText(os.getenv("OLLAMA_HOST", "localhost"))
            self.ollama_port_edit.setValue(int(os.getenv("OLLAMA_PORT", "11434")))
            self.timeout_edit.setValue(int(os.getenv("API_TIMEOUT", "30")))
            self.auto_start_checkbox.setChecked(
                os.getenv("AUTO_START_API", "false").lower() == "true"
            )

    def save_settings(self):
        """Save settings to environment variables and config file"""
        try:
            # Create a config file to persist settings
            config_path = os.path.join(
                os.path.dirname(__file__), "..", "..", "AI", "config.json"
            )
            config_dir = os.path.dirname(config_path)

            if not os.path.exists(config_dir):
                os.makedirs(config_dir)

            # Merge into the existing file so unrelated keys (LLAMA_CPP,
            # AGENT_TAILSCALE_IP) survive a save from this dialog.
            try:
                with open(config_path, encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                config = {}
            config.update({
                "API_PORT": str(self.api_port_edit.value()),
                "OLLAMA_HOST": self.ollama_host_edit.text(),
                "OLLAMA_PORT": str(self.ollama_port_edit.value()),
                "API_TIMEOUT": str(self.timeout_edit.value()),
                "AUTO_START_API": str(self.auto_start_checkbox.isChecked()).lower(),
            })

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            # Set environment variables for current session (flat string keys)
            for key, value in config.items():
                if isinstance(value, str):
                    os.environ[key] = value

            # After saving, refresh and cache models once using the configured API URL
            try:
                # Import lazily to avoid circular imports
                ai_dir = os.path.normpath(
                    os.path.join(os.path.dirname(__file__), "..", "..", "AI")
                )
                if ai_dir not in sys.path:
                    sys.path.insert(0, ai_dir)
                from config_loader import get_api_url
                from model_cache import get_model_cache

                api_url = get_api_url()
                cache = get_model_cache()
                cache.refresh_models(api_url)
            except Exception as cache_err:
                # Do not block saving if caching fails
                print(
                    f"Warning: failed to refresh model cache after saving settings: {cache_err}"
                )

            # Persist BYOK providers + routing into byok.json.
            self._save_byok()

            QMessageBox.information(
                self, _("Success"), _("Settings saved successfully!")
            )
            self.accept()

        except Exception as e:
            QMessageBox.critical(
                self,
                _("Error"),
                _("Failed to save settings: {error}").format(error=str(e)),
            )

    def test_connection(self):
        """Test connection to the local API server"""
        try:
            port = self.api_port_edit.value()
            url = f"http://localhost:{port}/health"

            response = requests.get(url, timeout=10)
            response.raise_for_status()

            result = response.json()
            if result.get("status") == "healthy":
                QMessageBox.information(self, _("Success"), _("Connection successful!"))
                self.check_api_status()  # Refresh status
            else:
                QMessageBox.warning(
                    self,
                    _("Warning"),
                    _("API responded but status is: {status}").format(
                        status=result.get("status")
                    ),
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                _("Connection Failed"),
                _("Failed to connect: {error}").format(error=str(e)),
            )

    def check_api_status(self):
        """Check API status and update display"""
        try:
            port = self.api_port_edit.value()

            # Check health
            health_url = f"http://localhost:{port}/health"
            health_response = requests.get(health_url, timeout=5)
            health_data = health_response.json()

            if health_data.get("status") == "healthy":
                self.status_label.setText("✅ API server is healthy and accessible")

                # Get models
                models_url = f"http://localhost:{port}/models"
                models_response = requests.get(models_url, timeout=5)
                models_data = models_response.json()

                # Handle the API response format - it returns {"models": [...]}
                if isinstance(models_data, dict) and "models" in models_data:
                    models = models_data["models"]
                elif isinstance(models_data, list):
                    models = models_data
                else:
                    models = []

                if models:
                    model_names = [
                        model.get("name", "Unknown")
                        for model in models
                        if isinstance(model, dict)
                    ]
                    self.models_text.setText("\n".join(model_names))
                else:
                    self.models_text.setText("No models available")
            else:
                self.status_label.setText(
                    f"⚠️ API server status: {health_data.get('status')}"
                )
                self.models_text.setText("Unable to fetch models")

        except Exception as e:
            self.status_label.setText(f"❌ Connection failed: {str(e)}")
            self.models_text.setText("Unable to connect to API server")

    def start_local_server(self):
        """Start the local API server"""
        try:
            if self.server_thread and self.server_thread.isRunning():
                self.stop_local_server()
                import time

                time.sleep(1)

            self.server_thread = APIServerThread()
            self.server_thread.status_update.connect(self.on_server_status_update)
            self.server_thread.error_occurred.connect(self.on_server_error)
            self.server_thread.start()

            self.start_server_btn.setEnabled(False)
            self.stop_server_btn.setEnabled(True)
            self.status_label.setText(_("🔄 Starting local API server..."))

        except Exception as e:
            QMessageBox.critical(self, _("Error"), str(e))
            self.start_server_btn.setEnabled(True)
            self.stop_server_btn.setEnabled(False)

    def stop_local_server(self):
        """Stop the local API server"""
        if self.server_thread:
            self.server_thread.stop()
            self.server_thread.wait(5000)  # Wait up to 5 seconds for thread to finish
            if self.server_thread.isRunning():
                self.server_thread.terminate()  # Force terminate if still running
                self.server_thread.wait()
            self.server_thread = None

        self.start_server_btn.setEnabled(True)
        self.stop_server_btn.setEnabled(False)
        self.status_label.setText(_("🛑 Local server stopped"))

    def on_server_status_update(self, message):
        """Handle server status updates"""
        self.status_label.setText(message)
        if (
            ("✅ Local API server started successfully" in message)
            or ("Uvicorn running" in message)
            or ("Application startup complete" in message)
        ):
            QTimer.singleShot(500, self.check_api_status)

    def on_server_error(self, error_message):
        """Handle server errors"""
        QMessageBox.critical(self, _("Server Error"), error_message)
        self.start_server_btn.setEnabled(True)
        self.stop_server_btn.setEnabled(False)

    def closeEvent(self, event):
        """Clean up when dialog is closed"""
        self.status_timer.stop()
        if self.server_thread:
            self.stop_local_server()
        super().closeEvent(event)
