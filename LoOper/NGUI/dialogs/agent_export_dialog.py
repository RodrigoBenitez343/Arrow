"""agent_export_dialog.py — Export dialog for building standalone agent executables.

Shows agent configuration summary, model selection, output path config,
and live PyInstaller build log.  Build runs in a QThread to keep the
UI responsive.
"""

import logging
import os
import threading

from PyQt5.QtCore import QMetaObject, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..constants import ACCENT_COLOR, DARK_GREY, LIGHT_GREY, TEXT_COLOR
from ..i18n import _
from .base_dialog import ModernDialog

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Build Worker Thread
# ═══════════════════════════════════════════════════════════════


class BuildWorker(QThread):
    """Runs the PyInstaller build in a background thread."""

    log_line = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)
    progress_update = pyqtSignal(int)

    def __init__(self, exporter, parent=None):
        super().__init__(parent)
        self._exporter = exporter

    def run(self):
        try:
            # First collect dependencies
            self.log_line.emit("Collecting dependencies...\n")
            deps = self._exporter.collect_dependencies()
            self.log_line.emit(
                f"  Chains:     {len(deps['chains'])}\n"
                f"  Sequences:  {len(deps['sequences'])}\n"
                f"  Screenshots:{len(deps['screenshots'])}\n"
                f"  Models:     {len(deps['models'])}\n\n"
            )

            # Generate spec
            self.log_line.emit("Generating PyInstaller spec...\n")
            spec_path = self._exporter.generate_spec()
            self.log_line.emit(f"  Spec: {spec_path}\n\n")

            # Run build
            self.log_line.emit("Starting PyInstaller build...\n")
            self.progress_update.emit(10)

            success = self._exporter.run_build(
                progress_callback=lambda msg: self.log_line.emit(msg + "\n")
            )

            self.progress_update.emit(100)
            exe_path = os.path.join(
                self._exporter.output_dir, f"{self._exporter.exe_name}.exe"
            )
            if success:
                self.finished_signal.emit(True, exe_path)
            else:
                self.finished_signal.emit(False, "")

        except Exception as e:
            logger.exception("Build worker failed")
            self.log_line.emit(f"\nFATAL: {e}\n")
            self.finished_signal.emit(False, "")

    def cancel(self):
        if self._exporter:
            self._exporter.cancel()


# ═══════════════════════════════════════════════════════════════
# Export Dialog
# ═══════════════════════════════════════════════════════════════


class AgentExportDialog(ModernDialog):
    """Dialog for configuring and running an agent standalone build."""

    def __init__(self, parent=None):
        super().__init__(
            parent=parent,
            title=_("Export Agent"),
            help_topic="agent-export",
            show_help_button=False,
        )

        self._system_chain_path = ""
        self._exporter = None
        self._worker = None
        self._build_running = False

        # Detect system chain
        self._detect_system_chain()

        # Build the UI
        self._build_ui()

        # Set dialog size
        self.resize(640, 560)

    # ── System chain detection ──────────────────────────────────────

    def _detect_system_chain(self):
        """Find the system chain (collection='System') in the chains directory."""
        try:
            from ..i18n import _
            from ..constants import ACCENT_COLOR

            # Find chains directory
            project_root = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..")
            )
            chains_dir = os.path.join(project_root, "chains")
            if not os.path.isdir(chains_dir):
                # Try from project root
                chains_dir = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "chains",
                )

            if os.path.isdir(chains_dir):
                for fname in os.listdir(chains_dir):
                    if not fname.endswith(".json"):
                        continue
                    fpath = os.path.join(chains_dir, fname)
                    try:
                        import json

                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if (data.get("collection") or "").lower() == "system":
                            self._system_chain_path = fpath
                            break
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("Failed to detect system chain: %s", e)

    # ── UI Build ────────────────────────────────────────────────────

    def _build_ui(self):
        """Build the dialog content."""
        # ── Agent Info Section ──
        info_group = QFrame()
        info_group.setStyleSheet(
            f"QFrame {{ background-color: {DARK_GREY};"
            f"border: 1px solid {LIGHT_GREY}; border-radius: 8px; }}"
        )
        info_layout = QVBoxLayout(info_group)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(6)

        info_title = QLabel(_("Agent Configuration"))
        info_title.setStyleSheet(
            f"color: {ACCENT_COLOR}; font-size: 13px; font-weight: 700; border: none;"
        )
        info_layout.addWidget(info_title)

        if self._system_chain_path:
            chain_name = os.path.basename(self._system_chain_path)
            info_layout.addWidget(
                self._make_info_row(_("System Chain:"), chain_name)
            )
            # Count tool chains
            tool_count = self._count_tool_chains()
            info_layout.addWidget(
                self._make_info_row(_("Tool Chains:"), str(tool_count))
            )
            # Count models
            model_count = len(self._detect_models())
            info_layout.addWidget(
                self._make_info_row(_("Models Found:"), str(model_count))
            )
        else:
            info_layout.addWidget(
                self._make_info_row(
                    _("System Chain:"),
                    _("Not detected — create a chain with collection='System'"),
                )
            )

        self.content_layout.addWidget(info_group)

        # ── Model Selection Section ──
        models_group = QFrame()
        models_group.setStyleSheet(
            f"QFrame {{ background-color: {DARK_GREY};"
            f"border: 1px solid {LIGHT_GREY}; border-radius: 8px; }}"
        )
        models_layout = QVBoxLayout(models_group)
        models_layout.setContentsMargins(16, 12, 16, 12)
        models_layout.setSpacing(6)

        models_label = QLabel(_("Models to Bundle"))
        models_label.setStyleSheet(
            f"color: {ACCENT_COLOR}; font-size: 13px; font-weight: 700; border: none;"
        )
        models_layout.addWidget(models_label)

        self._model_list = QListWidget()
        self._model_list.setMaximumHeight(120)
        self._model_list.setStyleSheet(
            f"QListWidget {{"
            f"  background-color: #1A1A1A; color: {TEXT_COLOR};"
            f"  border: 1px solid {LIGHT_GREY}; border-radius: 6px;"
            f"  font-size: 11px;"
            f"}}"
            f"QListWidget::item {{ padding: 4px 8px; }}"
            f"QListWidget::item:selected {{"
            f"  background-color: {ACCENT_COLOR}; color: {DARK_GREY};"
            f"}}"
        )

        detected_models = self._detect_models()
        for m in detected_models:
            item = QListWidgetItem(os.path.basename(m))
            item.setData(Qt.UserRole, m)
            item.setCheckState(Qt.Checked)
            self._model_list.addItem(item)

        if not detected_models:
            self._model_list.addItem(_("No models detected in chain"))
            self._model_list.item(0).setFlags(
                self._model_list.item(0).flags() & ~Qt.ItemIsUserCheckable
            )

        models_layout.addWidget(self._model_list)

        # Include vision checkbox
        self._vision_cb = QCheckBox(_("Include vision/OCR support (torch, paddle)"))
        self._vision_cb.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 11px; border: none;"
        )
        self._vision_cb.setChecked(False)
        models_layout.addWidget(self._vision_cb)

        self.content_layout.addWidget(models_group)

        # ── Output Config Section ──
        output_group = QFrame()
        output_group.setStyleSheet(
            f"QFrame {{ background-color: {DARK_GREY};"
            f"border: 1px solid {LIGHT_GREY}; border-radius: 8px; }}"
        )
        output_layout = QVBoxLayout(output_group)
        output_layout.setContentsMargins(16, 12, 16, 12)
        output_layout.setSpacing(6)

        output_label = QLabel(_("Output Configuration"))
        output_label.setStyleSheet(
            f"color: {ACCENT_COLOR}; font-size: 13px; font-weight: 700; border: none;"
        )
        output_layout.addWidget(output_label)

        # Output dir row
        dir_row = QHBoxLayout()
        dir_row.setContentsMargins(0, 0, 0, 0)
        dir_row.setSpacing(8)

        dir_label = QLabel(_("Output Folder:"))
        dir_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; border: none;")
        dir_row.addWidget(dir_label)

        default_output = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "dist",
            "agent_export",
        )
        self._output_path = QLabel(default_output)
        self._output_path.setStyleSheet(
            f"color: rgba(255,255,255,0.6); font-size: 10px; border: none; padding: 4px;"
        )
        self._output_path.setWordWrap(True)
        dir_row.addWidget(self._output_path, 1)

        browse_btn = QPushButton(_("Browse..."))
        browse_btn.setFixedHeight(24)
        browse_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #1A1A1A; color: {TEXT_COLOR};"
            f"  border: 1px solid {LIGHT_GREY}; border-radius: 4px;"
            f"  font-size: 10px; padding: 0 12px;"
            f"}}"
            f"QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        browse_btn.clicked.connect(self._browse_output)
        dir_row.addWidget(browse_btn)

        output_layout.addLayout(dir_row)

        # Exe name row
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(8)

        name_label = QLabel(_("Executable Name:"))
        name_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; border: none;")
        name_row.addWidget(name_label)

        self._exe_name_input = QTextEdit()
        self._exe_name_input.setFixedHeight(28)
        self._exe_name_input.setPlainText(
            os.path.splitext(os.path.basename(self._system_chain_path))[0]
            if self._system_chain_path
            else "MyAgent"
        )
        self._exe_name_input.setStyleSheet(
            f"QTextEdit {{"
            f"  background-color: #1A1A1A; color: {TEXT_COLOR};"
            f"  border: 1px solid {LIGHT_GREY}; border-radius: 4px;"
            f"  font-size: 12px; padding: 2px 6px;"
            f"}}"
            f"QTextEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        name_row.addWidget(self._exe_name_input, 1)

        output_layout.addLayout(name_row)

        self.content_layout.addWidget(output_group)

        # ── Build Log Section ──
        log_group = QFrame()
        log_group.setStyleSheet(
            f"QFrame {{ background-color: {DARK_GREY};"
            f"border: 1px solid {LIGHT_GREY}; border-radius: 8px; }}"
        )
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(16, 12, 16, 12)
        log_layout.setSpacing(6)

        log_label = QLabel(_("Build Log"))
        log_label.setStyleSheet(
            f"color: {ACCENT_COLOR}; font-size: 13px; font-weight: 700; border: none;"
        )
        log_layout.addWidget(log_label)

        self._log_output = QTextEdit()
        self._log_output.setReadOnly(True)
        self._log_output.setFixedHeight(120)
        self._log_output.setStyleSheet(
            f"QTextEdit {{"
            f"  background-color: #0D1117; color: #C9D1D9;"
            f"  border: 1px solid {LIGHT_GREY}; border-radius: 6px;"
            f"  font-size: 10px; padding: 8px;"
            f"  font-family: Consolas, monospace;"
            f"}}"
        )
        log_layout.addWidget(self._log_output)

        self.content_layout.addWidget(log_group)

        # ── Progress bar ──
        self._progress = QProgressBar()
        self._progress.setFixedHeight(8)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet(
            f"QProgressBar {{"
            f"  background-color: #1A1A1A; border: none; border-radius: 4px;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"  background-color: {ACCENT_COLOR}; border-radius: 4px;"
            f"}}"
        )
        self._progress.hide()
        self.content_layout.addWidget(self._progress)

        # ── Action Buttons ──
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)
        btn_row.setSpacing(8)

        self._open_folder_btn = QPushButton(_("Open Output Folder"))
        self._open_folder_btn.setFixedHeight(32)
        self._open_folder_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #1A1A1A; color: {TEXT_COLOR};"
            f"  border: 1px solid {LIGHT_GREY}; border-radius: 6px;"
            f"  font-size: 11px; padding: 0 16px;"
            f"}}"
            f"QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._open_folder_btn.clicked.connect(self._open_output_folder)
        self._open_folder_btn.hide()
        btn_row.addWidget(self._open_folder_btn)

        btn_row.addStretch()

        self._build_btn = QPushButton(_("Build Agent"))
        self._build_btn.setFixedHeight(32)
        self._build_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {ACCENT_COLOR}; color: #0D1117;"
            f"  border: none; border-radius: 6px;"
            f"  font-size: 12px; font-weight: 700; padding: 0 20px;"
            f"}}"
            f"QPushButton:hover {{ background-color: #00f0c6; }}"
            f"QPushButton:pressed {{ background-color: #00b898; }}"
            f"QPushButton:disabled {{ background-color: #2E2E2E; color: #666; }}"
        )
        self._build_btn.clicked.connect(self._start_build)
        btn_row.addWidget(self._build_btn)

        self._cancel_btn = QPushButton(_("Cancel"))
        self._cancel_btn.setFixedHeight(32)
        self._cancel_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: transparent; color: {TEXT_COLOR};"
            f"  border: 1px solid {LIGHT_GREY}; border-radius: 6px;"
            f"  font-size: 11px; padding: 0 16px;"
            f"}}"
            f"QPushButton:hover {{ border-color: #FF4B4B; color: #FF4B4B; }}"
        )
        self._cancel_btn.clicked.connect(self._cancel_build)
        btn_row.addWidget(self._cancel_btn)

        self.content_layout.addLayout(btn_row)

    def _make_info_row(self, label: str, value: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet("border: none;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        lbl = QLabel(label)
        lbl.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 11px; border: none;")
        rl.addWidget(lbl)

        val = QLabel(value)
        val.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; border: none;")
        rl.addWidget(val, 1)

        return row

    # ── Data helpers ────────────────────────────────────────────────

    def _count_tool_chains(self) -> int:
        """Count chain_import_nodes in the system chain."""
        try:
            import json
            with open(self._system_chain_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return len(data.get("chain_import_nodes", []))
        except Exception:
            return 0

    def _detect_models(self) -> list:
        """Detect model files from system chain and all transitive tool chains."""
        try:
            from builder.agent_exporter import collect_models_recursive

            if not self._system_chain_path or not os.path.exists(self._system_chain_path):
                return []

            found = collect_models_recursive(self._system_chain_path)
            return sorted(found)
        except Exception as e:
            logger.warning("Failed to detect models: %s", e)
            return []

    # ── Actions ─────────────────────────────────────────────────────

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(
            self, _("Select Output Folder"), self._output_path.text()
        )
        if folder:
            self._output_path.setText(folder)

    def _open_output_folder(self):
        folder = self._output_path.text()
        if os.path.isdir(folder):
            try:
                os.startfile(folder)
            except Exception:
                pass

    def _start_build(self):
        if self._build_running:
            return

        if not self._system_chain_path or not os.path.exists(self._system_chain_path):
            self._log_output.append(
                _("ERROR: System chain not found. Create a chain with collection='System'.")
            )
            return

        exe_name = self._exe_name_input.toPlainText().strip()
        if not exe_name:
            exe_name = "MyAgent"

        output_dir = self._output_path.text().strip()
        if not output_dir:
            output_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "dist",
                "agent_export",
            )

        os.makedirs(output_dir, exist_ok=True)

        # Collect selected models
        selected_models = []
        for i in range(self._model_list.count()):
            item = self._model_list.item(i)
            if item.checkState() == Qt.Checked:
                path = item.data(Qt.UserRole)
                if path:
                    selected_models.append(path)

        # Create exporter
        try:
            from builder.agent_exporter import AgentExporter

            self._exporter = AgentExporter(
                system_chain_path=self._system_chain_path,
                output_dir=output_dir,
                exe_name=exe_name,
                model_files=selected_models,
                include_vision=self._vision_cb.isChecked(),
                parent_widget=self,
            )
        except Exception as e:
            self._log_output.append(_("ERROR creating exporter: {}").format(e))
            logger.exception("Failed to create AgentExporter")
            return

        # Set UI state
        self._build_running = True
        self._build_btn.setEnabled(False)
        self._build_btn.setText(_("Building..."))
        self._open_folder_btn.hide()
        self._log_output.clear()
        self._progress.show()
        self._progress.setValue(0)

        # Start worker thread
        self._worker = BuildWorker(self._exporter)
        self._worker.log_line.connect(self._on_log_line)
        self._worker.finished_signal.connect(self._on_build_finished)
        self._worker.progress_update.connect(self._progress.setValue)
        self._worker.start()

    def _cancel_build(self):
        if self._worker and self._worker.isRunning():
            self._log_output.append(_("\nCancelling build..."))
            self._worker.cancel()

    def _on_log_line(self, line: str):
        self._log_output.append(line)
        # Auto-scroll
        scrollbar = self._log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_build_finished(self, success: bool, exe_path: str):
        self._build_running = False
        self._build_btn.setEnabled(True)
        self._build_btn.setText(_("Build Agent"))
        self._progress.hide()

        if success:
            self._open_folder_btn.show()
            self._log_output.append(
                _("\nBuild successful! Executable at:\n{}").format(exe_path)
            )
        else:
            self._log_output.append(
                _("\nBuild failed. Check the log above for details.")
            )

        self._worker = None
