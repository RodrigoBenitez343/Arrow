# dialogs/ai_settings_dialog.py

import json
import os
import subprocess
import sys

import requests
from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
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

from ..constants import ACCENT_COLOR, DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR
from ..i18n import _
from .base_dialog import ModernDialog, UserGuideDialog
from .toggle_switch import ModernToggle


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
        self.resize(700, 700)
        self.setMinimumSize(650, 600)

        # Style logic moved to ModernDialog

        # Server thread
        self.server_thread = None

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.check_api_status)

        self.setup_ui()
        self.load_settings()

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
                border: 1px solid {LIGHT_GREY};
                border-radius: 12px;
                font-size: 12px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
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
