#!/usr/bin/env python3
"""
OverWatch Main Window

Main window for the OverWatch a  pplication providing API management.
"""

import logging
import asyncio
from typing import Optional, Dict, Any
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QTabWidget,
    QMenuBar, QMenu, QAction, QStatusBar, QLabel, QSplitter,
    QTextEdit, QGroupBox, QPushButton, QMessageBox, QProgressBar
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QThread, pyqtSlot
from PyQt5.QtGui import QIcon, QFont

from .widgets.api_status_widget import APIStatusWidget
from .widgets.model_management_widget import ModelManagementWidget
from .dialogs.settings_dialog import SettingsDialog
from .dialogs.about_dialog import AboutDialog
from ..api.api_client import OverWatchAPIClient
from ..config.settings import Settings

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main window for OverWatch application"""
    
    # Signals
    api_status_changed = pyqtSignal(bool, str)  # connected, status_message
    
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        
        self.settings = settings
        self.api_client: Optional[OverWatchAPIClient] = None
        
        # Initialize UI
        self.init_ui()
        self.init_api_client()
        
        # Setup status checking timer
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.check_api_status)
        self.status_timer.start(5000)  # Check every 5 seconds
        
        # Initial status check
        self.check_api_status()
    
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("OverWatch - vLLM API Management")
        self.setGeometry(100, 100, 1200, 800)
        
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Create main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)
        
        # Create menu bar
        self.create_menu_bar()
        
        # Create toolbar
        self.create_toolbar()
        
        # Create main content area
        self.create_main_content(main_layout)
        
        # Create status bar
        self.create_status_bar()
    
    def create_menu_bar(self):
        """Create the menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu('&File')
        
        # Settings action
        settings_action = QAction('&Settings...', self)
        settings_action.setShortcut('Ctrl+,')
        settings_action.triggered.connect(self.show_settings_dialog)
        file_menu.addAction(settings_action)
        
        file_menu.addSeparator()
        
        # Exit action
        exit_action = QAction('E&xit', self)
        exit_action.setShortcut('Ctrl+Q')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # API menu
        api_menu = menubar.addMenu('&API')
        
        # Connect action
        self.connect_action = QAction('&Connect to API', self)
        self.connect_action.triggered.connect(self.connect_to_api)
        api_menu.addAction(self.connect_action)
        
        # Disconnect action
        self.disconnect_action = QAction('&Disconnect', self)
        self.disconnect_action.triggered.connect(self.disconnect_from_api)
        self.disconnect_action.setEnabled(False)
        api_menu.addAction(self.disconnect_action)
        
        api_menu.addSeparator()
        
        # Refresh models action
        refresh_models_action = QAction('&Refresh Models', self)
        refresh_models_action.setShortcut('F5')
        refresh_models_action.triggered.connect(self.refresh_models)
        api_menu.addAction(refresh_models_action)
        
        # Help menu
        help_menu = menubar.addMenu('&Help')
        
        # About action
        about_action = QAction('&About OverWatch...', self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)
    
    def create_toolbar(self):
        """Create the toolbar"""
        toolbar = self.addToolBar('Main')
        toolbar.setMovable(False)
        
        # Connect button
        self.connect_button = QPushButton('Connect')
        self.connect_button.clicked.connect(self.connect_to_api)
        toolbar.addWidget(self.connect_button)
        
        toolbar.addSeparator()
        
        # Refresh button
        refresh_button = QPushButton('Refresh')
        refresh_button.clicked.connect(self.refresh_models)
        toolbar.addWidget(refresh_button)
        
    def create_main_content(self, parent_layout):
        """Create the main content area"""
        # Create horizontal splitter
        main_splitter = QSplitter(Qt.Horizontal)
        parent_layout.addWidget(main_splitter)
        
        # Left panel - API Status and Configuration
        left_panel = self.create_left_panel()
        main_splitter.addWidget(left_panel)
        
        # Right panel - Main tabs
        right_panel = self.create_right_panel()
        main_splitter.addWidget(right_panel)
        
        # Set splitter proportions
        main_splitter.setSizes([300, 900])
    
    def create_left_panel(self) -> QWidget:
        """Create the left panel with API status and configuration"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        
        # API Status Widget
        self.api_status_widget = APIStatusWidget(self.settings)
        layout.addWidget(self.api_status_widget)
        
        # Add stretch to push widgets to top
        layout.addStretch()
        
        return panel
    
    def create_right_panel(self) -> QWidget:
        """Create the right panel with main functionality tabs"""
        # Create tab widget
        self.tab_widget = QTabWidget()
        
        # Model Management Tab
        self.model_management_widget = ModelManagementWidget()
        self.tab_widget.addTab(self.model_management_widget, "Models")
        
        # Log Viewer Tab
        self.log_viewer = self.create_log_viewer()
        self.tab_widget.addTab(self.log_viewer, "Logs")
        
        return self.tab_widget
    
    def create_log_viewer(self) -> QWidget:
        """Create the log viewer widget"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        
        # Log text area
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFont(QFont('Consolas', 9))
        layout.addWidget(self.log_text)
        
        # Log controls
        controls_layout = QHBoxLayout()
        
        clear_logs_button = QPushButton('Clear Logs')
        clear_logs_button.clicked.connect(self.clear_logs)
        controls_layout.addWidget(clear_logs_button)
        
        controls_layout.addStretch()
        
        layout.addLayout(controls_layout)
        
        return widget
    
    def create_status_bar(self):
        """Create the status bar"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # API connection status
        self.connection_status_label = QLabel('Disconnected')
        self.connection_status_label.setProperty('class', 'error-label')
        self.status_bar.addWidget(self.connection_status_label)
        
        # Progress bar for operations
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.status_bar.addPermanentWidget(self.progress_bar)
        
        # Memory usage label
        self.memory_usage_label = QLabel('')
        self.status_bar.addPermanentWidget(self.memory_usage_label)
    
    def init_api_client(self):
        """Initialize the API client"""
        from ..config.settings import get_api_url
        api_url = get_api_url(self.settings)
        self.api_client = OverWatchAPIClient(api_url)
        
        # Connect widgets to API client
        if hasattr(self, 'model_management_widget'):
            self.model_management_widget.set_api_client(self.api_client)
    
    @pyqtSlot()
    def check_api_status(self):
        """Check API status asynchronously"""
        if self.api_client:
            # Use QTimer to run async operation
            QTimer.singleShot(0, self._check_api_status_async)
    
    def _check_api_status_async(self):
        """Async API status check"""
        try:
            # This would need to be adapted for async/await pattern
            # For now, we'll use a simple synchronous check
            status = self.api_client.health_check()
            
            if status.get('status') == 'healthy':
                self.update_connection_status(True, 'Connected')
                self.api_status_widget.update_status(True, status)
            else:
                self.update_connection_status(False, 'API Error')
                self.api_status_widget.update_status(False, status)
                
        except Exception as e:
            self.update_connection_status(False, f'Connection Error: {str(e)}')
            self.api_status_widget.update_status(False, {'error': str(e)})
    
    def update_connection_status(self, connected: bool, message: str):
        """Update connection status in UI"""
        if connected:
            self.connection_status_label.setText(f'Connected - {message}')
            self.connection_status_label.setProperty('class', 'success-label')
            self.connect_button.setText('Disconnect')
            self.connect_button.clicked.disconnect()
            self.connect_button.clicked.connect(self.disconnect_from_api)
            self.connect_action.setEnabled(False)
            self.disconnect_action.setEnabled(True)
        else:
            self.connection_status_label.setText(f'Disconnected - {message}')
            self.connection_status_label.setProperty('class', 'error-label')
            self.connect_button.setText('Connect')
            self.connect_button.clicked.disconnect()
            self.connect_button.clicked.connect(self.connect_to_api)
            self.connect_action.setEnabled(True)
            self.disconnect_action.setEnabled(False)
        
        # Refresh stylesheet to apply class changes
        self.connection_status_label.style().unpolish(self.connection_status_label)
        self.connection_status_label.style().polish(self.connection_status_label)
        
        # Emit signal
        self.api_status_changed.emit(connected, message)
    
    @pyqtSlot()
    def connect_to_api(self):
        """Connect to the API"""
        try:
            self.init_api_client()
            self.check_api_status()
            self.log_message('Attempting to connect to API...')
        except Exception as e:
            self.show_error_message('Connection Error', f'Failed to connect to API: {str(e)}')
    
    @pyqtSlot()
    def disconnect_from_api(self):
        """Disconnect from the API"""
        self.status_timer.stop()
        self.update_connection_status(False, 'Manually disconnected')
        self.log_message('Disconnected from API')
    
    @pyqtSlot()
    def refresh_models(self):
        """Refresh the model list"""
        if hasattr(self, 'model_management_widget'):
            self.model_management_widget.refresh_models()
        self.log_message('Refreshing model list...')
    
    @pyqtSlot()
    def show_settings_dialog(self):
        """Show the settings dialog"""
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_() == dialog.Accepted:
            # Settings were changed, reinitialize API client
            self.init_api_client()
            self.log_message('Settings updated')
    
    @pyqtSlot()
    def show_about_dialog(self):
        """Show the about dialog"""
        dialog = AboutDialog(self)
        dialog.exec_()
    
    @pyqtSlot()
    def clear_logs(self):
        """Clear the log viewer"""
        self.log_text.clear()
    
    def log_message(self, message: str, level: str = 'INFO'):
        """Add a message to the log viewer"""
        from datetime import datetime
        timestamp = datetime.now().strftime('%H:%M:%S')
        formatted_message = f'[{timestamp}] {level}: {message}'
        
        self.log_text.append(formatted_message)
        
        # Auto-scroll to bottom
        cursor = self.log_text.textCursor()
        cursor.movePosition(cursor.End)
        self.log_text.setTextCursor(cursor)
    
    def show_error_message(self, title: str, message: str):
        """Show an error message dialog"""
        QMessageBox.critical(self, title, message)
        self.log_message(f'{title}: {message}', 'ERROR')
    
    def show_info_message(self, title: str, message: str):
        """Show an info message dialog"""
        QMessageBox.information(self, title, message)
        self.log_message(f'{title}: {message}', 'INFO')
    
    def closeEvent(self, event):
        """Handle window close event"""
        # Stop timers
        if hasattr(self, 'status_timer'):
            self.status_timer.stop()
        
        # Accept the close event
        event.accept()
        
        self.log_message('Application closing')