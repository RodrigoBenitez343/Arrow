#!/usr/bin/env python3
"""
OverWatch PyQt5 Application

Main application class for the OverWatch UI.
"""

import sys
import logging
from typing import Optional
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QPalette, QColor

from .main_window import MainWindow
from ..config.settings import load_settings, setup_logging

logger = logging.getLogger(__name__)


class OverWatchApp(QApplication):
    """Main OverWatch application class"""
    
    def __init__(self, argv):
        super().__init__(argv)
        
        # Load settings
        self.settings = load_settings()
        setup_logging(self.settings)
        
        # Set application properties
        self.setApplicationName("OverWatch")
        self.setApplicationVersion("1.0.0")
        self.setOrganizationName("LoOper")
        self.setOrganizationDomain("looper.local")
        
        # Apply dark theme
        self.apply_dark_theme()
        
        # Create main window
        self.main_window: Optional[MainWindow] = None
        
        # Setup exception handling
        sys.excepthook = self.handle_exception
    
    def apply_dark_theme(self):
        """Apply dark theme to the application"""
        # Dark theme stylesheet inspired by the NGUI module
        dark_stylesheet = """
        QWidget {
            background-color: #2b2b2b;
            color: #ffffff;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 9pt;
        }
        
        QMainWindow {
            background-color: #2b2b2b;
        }
        
        QMenuBar {
            background-color: #3c3c3c;
            border-bottom: 1px solid #555555;
            padding: 2px;
        }
        
        QMenuBar::item {
            background-color: transparent;
            padding: 4px 8px;
            margin: 2px;
            border-radius: 3px;
        }
        
        QMenuBar::item:selected {
            background-color: #4a4a4a;
        }
        
        QMenu {
            background-color: #3c3c3c;
            border: 1px solid #555555;
            padding: 2px;
        }
        
        QMenu::item {
            padding: 6px 20px;
            border-radius: 3px;
        }
        
        QMenu::item:selected {
            background-color: #4a4a4a;
        }
        
        QToolBar {
            background-color: #3c3c3c;
            border: none;
            spacing: 2px;
            padding: 2px;
        }
        
        QToolButton {
            background-color: transparent;
            border: 1px solid transparent;
            padding: 4px;
            margin: 1px;
            border-radius: 3px;
        }
        
        QToolButton:hover {
            background-color: #4a4a4a;
            border: 1px solid #666666;
        }
        
        QToolButton:pressed {
            background-color: #555555;
        }
        
        QPushButton {
            background-color: #4a4a4a;
            border: 1px solid #666666;
            padding: 6px 12px;
            border-radius: 4px;
            font-weight: bold;
        }
        
        QPushButton:hover {
            background-color: #555555;
            border: 1px solid #777777;
        }
        
        QPushButton:pressed {
            background-color: #666666;
        }
        
        QPushButton:disabled {
            background-color: #333333;
            color: #666666;
            border: 1px solid #444444;
        }
        
        QLineEdit, QTextEdit, QPlainTextEdit {
            background-color: #3c3c3c;
            border: 1px solid #555555;
            padding: 4px;
            border-radius: 3px;
            selection-background-color: #0078d4;
        }
        
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
            border: 2px solid #0078d4;
        }
        
        QComboBox {
            background-color: #3c3c3c;
            border: 1px solid #555555;
            padding: 4px 8px;
            border-radius: 3px;
        }
        
        QComboBox:hover {
            border: 1px solid #777777;
        }
        
        QComboBox::drop-down {
            border: none;
            width: 20px;
        }
        
        QComboBox::down-arrow {
            image: none;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 4px solid #ffffff;
        }
        
        QComboBox QAbstractItemView {
            background-color: #3c3c3c;
            border: 1px solid #555555;
            selection-background-color: #0078d4;
        }
        
        QSpinBox, QDoubleSpinBox {
            background-color: #3c3c3c;
            border: 1px solid #555555;
            padding: 4px;
            border-radius: 3px;
        }
        
        QSpinBox:focus, QDoubleSpinBox:focus {
            border: 2px solid #0078d4;
        }
        
        QCheckBox {
            spacing: 8px;
        }
        
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border: 1px solid #555555;
            border-radius: 3px;
            background-color: #3c3c3c;
        }
        
        QCheckBox::indicator:checked {
            background-color: #0078d4;
            border: 1px solid #0078d4;
        }
        
        QRadioButton {
            spacing: 8px;
        }
        
        QRadioButton::indicator {
            width: 16px;
            height: 16px;
            border: 1px solid #555555;
            border-radius: 8px;
            background-color: #3c3c3c;
        }
        
        QRadioButton::indicator:checked {
            background-color: #0078d4;
            border: 1px solid #0078d4;
        }
        
        QSlider::groove:horizontal {
            border: 1px solid #555555;
            height: 6px;
            background-color: #3c3c3c;
            border-radius: 3px;
        }
        
        QSlider::handle:horizontal {
            background-color: #0078d4;
            border: 1px solid #0078d4;
            width: 16px;
            margin: -6px 0;
            border-radius: 8px;
        }
        
        QSlider::handle:horizontal:hover {
            background-color: #106ebe;
        }
        
        QProgressBar {
            border: 1px solid #555555;
            border-radius: 3px;
            text-align: center;
            background-color: #3c3c3c;
        }
        
        QProgressBar::chunk {
            background-color: #0078d4;
            border-radius: 2px;
        }
        
        QTabWidget::pane {
            border: 1px solid #555555;
            background-color: #2b2b2b;
        }
        
        QTabBar::tab {
            background-color: #3c3c3c;
            border: 1px solid #555555;
            padding: 6px 12px;
            margin-right: 2px;
        }
        
        QTabBar::tab:selected {
            background-color: #0078d4;
            border-bottom: 1px solid #0078d4;
        }
        
        QTabBar::tab:hover:!selected {
            background-color: #4a4a4a;
        }
        
        QGroupBox {
            border: 1px solid #555555;
            border-radius: 4px;
            margin-top: 8px;
            padding-top: 8px;
            font-weight: bold;
        }
        
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 8px;
            padding: 0 4px 0 4px;
        }
        
        QScrollBar:vertical {
            background-color: #3c3c3c;
            width: 12px;
            border-radius: 6px;
        }
        
        QScrollBar::handle:vertical {
            background-color: #666666;
            border-radius: 6px;
            min-height: 20px;
        }
        
        QScrollBar::handle:vertical:hover {
            background-color: #777777;
        }
        
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            border: none;
            background: none;
        }
        
        QScrollBar:horizontal {
            background-color: #3c3c3c;
            height: 12px;
            border-radius: 6px;
        }
        
        QScrollBar::handle:horizontal {
            background-color: #666666;
            border-radius: 6px;
            min-width: 20px;
        }
        
        QScrollBar::handle:horizontal:hover {
            background-color: #777777;
        }
        
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            border: none;
            background: none;
        }
        
        QSplitter::handle {
            background-color: #555555;
        }
        
        QSplitter::handle:horizontal {
            width: 2px;
        }
        
        QSplitter::handle:vertical {
            height: 2px;
        }
        
        QStatusBar {
            background-color: #3c3c3c;
            border-top: 1px solid #555555;
        }
        
        QLabel {
            background-color: transparent;
        }
        
        QFrame {
            border: none;
        }
        
        /* Custom styles for specific widgets */
        .error-label {
            color: #ff6b6b;
            font-weight: bold;
        }
        
        .success-label {
            color: #51cf66;
            font-weight: bold;
        }
        
        .warning-label {
            color: #ffd43b;
            font-weight: bold;
        }
        
        .info-label {
            color: #74c0fc;
            font-weight: bold;
        }
        """
        
        self.setStyleSheet(dark_stylesheet)
    
    def create_main_window(self) -> MainWindow:
        """Create and show the main window"""
        if self.main_window is None:
            self.main_window = MainWindow(self.settings)
        
        self.main_window.show()
        return self.main_window
    
    def handle_exception(self, exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions"""
        if issubclass(exc_type, KeyboardInterrupt):
            # Handle Ctrl+C gracefully
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        # Log the exception
        logger.error(
            "Uncaught exception",
            exc_info=(exc_type, exc_value, exc_traceback)
        )
        
        # Show error dialog
        error_msg = f"An unexpected error occurred:\n\n{exc_type.__name__}: {exc_value}"
        
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Critical)
        msg_box.setWindowTitle("OverWatch Error")
        msg_box.setText("An unexpected error occurred.")
        msg_box.setDetailedText(error_msg)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec_()
    
    def run(self) -> int:
        """Run the application"""
        try:
            # Create and show main window
            self.create_main_window()
            
            # Start event loop
            return self.exec_()
            
        except Exception as e:
            logger.error(f"Failed to start application: {e}")
            return 1


def main():
    """Main entry point for the OverWatch UI application"""
    app = OverWatchApp(sys.argv)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())