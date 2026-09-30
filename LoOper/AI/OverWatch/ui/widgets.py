#!/usr/bin/env python3
"""
OverWatch UI Widgets

Custom PyQt5 widgets for the OverWatch application.
"""

import sys
import json
import logging
from typing import Dict, List, Any, Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QSlider,
    QGroupBox, QTabWidget, QSplitter, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QScrollArea, QFrame, QDialog, QDialogButtonBox,
    QMessageBox, QFileDialog, QApplication
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, pyqtSlot
from PyQt5.QtGui import QFont, QTextCursor, QPixmap, QIcon

logger = logging.getLogger(__name__)


class ModelManagementWidget(QWidget):
    """Widget for managing vLLM models"""
    
    model_loaded = pyqtSignal(str)  # model_name
    model_unloaded = pyqtSignal(str)  # model_name
    
    def __init__(self, api_client=None, parent=None):
        super().__init__(parent)
        self.api_client = api_client
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Model loading section
        load_group = QGroupBox("Load Model")
        load_layout = QGridLayout(load_group)
        
        load_layout.addWidget(QLabel("Model Name:"), 0, 0)
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText("e.g., microsoft/DialoGPT-medium")
        load_layout.addWidget(self.model_input, 0, 1)
        
        self.load_btn = QPushButton("Load Model")
        self.load_btn.clicked.connect(self.load_model)
        load_layout.addWidget(self.load_btn, 0, 2)
        
        layout.addWidget(load_group)
        
        # Loaded models section
        models_group = QGroupBox("Loaded Models")
        models_layout = QVBoxLayout(models_group)
        
        self.models_table = QTableWidget(0, 4)
        self.models_table.setHorizontalHeaderLabels(["Model", "Status", "Memory", "Actions"])
        self.models_table.horizontalHeader().setStretchLastSection(True)
        models_layout.addWidget(self.models_table)
        
        # Refresh button
        refresh_layout = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_models)
        refresh_layout.addWidget(self.refresh_btn)
        refresh_layout.addStretch()
        models_layout.addLayout(refresh_layout)
        
        layout.addWidget(models_group)
        
    def load_model(self):
        """Load a model"""
        model_name = self.model_input.text().strip()
        if not model_name:
            QMessageBox.warning(self, "Warning", "Please enter a model name")
            return
            
        if not self.api_client:
            QMessageBox.warning(self, "Warning", "API client not connected")
            return
            
        try:
            self.load_btn.setEnabled(False)
            self.load_btn.setText("Loading...")
            
            response = self.api_client.load_model(model_name)
            if response.get('success', False):
                QMessageBox.information(self, "Success", f"Model '{model_name}' loaded successfully")
                self.model_loaded.emit(model_name)
                self.model_input.clear()
                self.refresh_models()
            else:
                error_msg = response.get('error', 'Unknown error')
                QMessageBox.critical(self, "Error", f"Failed to load model: {error_msg}")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load model: {str(e)}")
        finally:
            self.load_btn.setEnabled(True)
            self.load_btn.setText("Load Model")
    
    def unload_model(self, model_name: str):
        """Unload a model"""
        if not self.api_client:
            return
            
        try:
            response = self.api_client.unload_model(model_name)
            if response.get('success', False):
                QMessageBox.information(self, "Success", f"Model '{model_name}' unloaded successfully")
                self.model_unloaded.emit(model_name)
                self.refresh_models()
            else:
                error_msg = response.get('error', 'Unknown error')
                QMessageBox.critical(self, "Error", f"Failed to unload model: {error_msg}")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to unload model: {str(e)}")
    
    def refresh_models(self):
        """Refresh the models table"""
        if not self.api_client:
            return
            
        try:
            models = self.api_client.list_models()
            self.models_table.setRowCount(len(models))
            
            for i, model in enumerate(models):
                # Model name
                self.models_table.setItem(i, 0, QTableWidgetItem(model))
                
                # Status (placeholder)
                self.models_table.setItem(i, 1, QTableWidgetItem("Loaded"))
                
                # Memory usage (placeholder)
                self.models_table.setItem(i, 2, QTableWidgetItem("N/A"))
                
                # Actions
                unload_btn = QPushButton("Unload")
                unload_btn.clicked.connect(lambda checked, m=model: self.unload_model(m))
                self.models_table.setCellWidget(i, 3, unload_btn)
                
        except Exception as e:
            logger.error(f"Failed to refresh models: {e}")
    
    def set_api_client(self, api_client):
        """Set the API client"""
        self.api_client = api_client
        self.refresh_models()


class LogWidget(QWidget):
    """Widget for displaying application logs"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Controls
        controls_layout = QHBoxLayout()
        
        self.clear_btn = QPushButton("Clear Logs")
        self.clear_btn.clicked.connect(self.clear_logs)
        controls_layout.addWidget(self.clear_btn)
        
        self.save_btn = QPushButton("Save Logs")
        self.save_btn.clicked.connect(self.save_logs)
        controls_layout.addWidget(self.save_btn)
        
        controls_layout.addStretch()
        
        # Log level filter
        controls_layout.addWidget(QLabel("Level:"))
        self.level_combo = QComboBox()
        self.level_combo.addItems(["ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
        self.level_combo.setCurrentText("INFO")
        controls_layout.addWidget(self.level_combo)
        
        layout.addLayout(controls_layout)
        
        # Log display
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log_text)
        
    def add_log_message(self, level: str, message: str):
        """Add a log message"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # Color coding for different log levels
        color_map = {
            'DEBUG': 'gray',
            'INFO': 'black',
            'WARNING': 'orange',
            'ERROR': 'red',
            'CRITICAL': 'darkred'
        }
        
        color = color_map.get(level.upper(), 'black')
        formatted_message = f'<span style="color: {color}">[{timestamp}] {level}: {message}</span>'
        
        self.log_text.append(formatted_message)
        
        # Auto-scroll to bottom
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_text.setTextCursor(cursor)
    
    def clear_logs(self):
        """Clear all logs"""
        self.log_text.clear()
    
    def save_logs(self):
        """Save logs to file"""
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save Logs", "overwatch_logs.txt", "Text Files (*.txt);;All Files (*)"
        )
        
        if filename:
            try:
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(self.log_text.toPlainText())
                QMessageBox.information(self, "Success", f"Logs saved to {filename}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to save logs: {str(e)}")


class SettingsDialog(QDialog):
    """Settings configuration dialog"""
    
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("OverWatch Settings")
        self.setModal(True)
        self.resize(600, 500)
        self.setup_ui()
        self.load_settings()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Settings tabs
        self.tabs = QTabWidget()
        
        # API Settings
        api_tab = QWidget()
        api_layout = QGridLayout(api_tab)
        
        api_layout.addWidget(QLabel("Host:"), 0, 0)
        self.host_input = QLineEdit()
        api_layout.addWidget(self.host_input, 0, 1)
        
        api_layout.addWidget(QLabel("Port:"), 1, 0)
        self.port_input = QSpinBox()
        self.port_input.setRange(1, 65535)
        api_layout.addWidget(self.port_input, 1, 1)
        
        api_layout.addWidget(QLabel("Debug Mode:"), 2, 0)
        self.debug_cb = QCheckBox()
        api_layout.addWidget(self.debug_cb, 2, 1)
        
        self.tabs.addTab(api_tab, "API")
        
        # vLLM Settings
        vllm_tab = QWidget()
        vllm_layout = QGridLayout(vllm_tab)
        
        vllm_layout.addWidget(QLabel("GPU Memory Utilization:"), 0, 0)
        self.gpu_memory_input = QDoubleSpinBox()
        self.gpu_memory_input.setRange(0.1, 1.0)
        self.gpu_memory_input.setSingleStep(0.1)
        vllm_layout.addWidget(self.gpu_memory_input, 0, 1)
        
        vllm_layout.addWidget(QLabel("Max Model Length:"), 1, 0)
        self.max_model_len_input = QSpinBox()
        self.max_model_len_input.setRange(512, 32768)
        vllm_layout.addWidget(self.max_model_len_input, 1, 1)
        
        self.tabs.addTab(vllm_tab, "vLLM")
        
        layout.addWidget(self.tabs)
        
        # Dialog buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self.apply_settings)
        layout.addWidget(buttons)
        
    def load_settings(self):
        """Load current settings into the dialog"""
        # API settings
        self.host_input.setText(self.settings.api.host)
        self.port_input.setValue(self.settings.api.port)
        self.debug_cb.setChecked(self.settings.api.debug)
        
        # vLLM settings
        self.gpu_memory_input.setValue(self.settings.vllm.gpu_memory_utilization)
        self.max_model_len_input.setValue(self.settings.vllm.max_model_len)
        
    def apply_settings(self):
        """Apply settings without closing dialog"""
        # Update settings object
        self.settings.api.host = self.host_input.text()
        self.settings.api.port = self.port_input.value()
        self.settings.api.debug = self.debug_cb.isChecked()
        
        self.settings.vllm.gpu_memory_utilization = self.gpu_memory_input.value()
        self.settings.vllm.max_model_len = self.max_model_len_input.value()
        
        QMessageBox.information(self, "Settings", "Settings applied successfully")
        
    def accept(self):
        """Accept and apply settings"""
        self.apply_settings()
        super().accept()


class AboutDialog(QDialog):
    """About dialog"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About OverWatch")
        self.setModal(True)
        self.setFixedSize(400, 300)
        self.setup_ui()
        
    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Logo/Icon (placeholder)
        logo_label = QLabel("🔍")
        logo_label.setAlignment(Qt.AlignCenter)
        logo_label.setStyleSheet("font-size: 48px;")
        layout.addWidget(logo_label)
        
        # Title
        title_label = QLabel("OverWatch")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title_label)
        
        # Version
        version_label = QLabel("Version 1.0.0")
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)
        
        # Description
        desc_label = QLabel(
            "vLLM API with FastAPI integration\n"
            "for advanced language model inference."
        )
        desc_label.setAlignment(Qt.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)
        
        layout.addStretch()
        
        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)