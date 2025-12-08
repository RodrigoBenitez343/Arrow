from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QWidget, QLabel, QPushButton, 
                             QHBoxLayout, QGraphicsDropShadowEffect, QSizeGrip)
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtGui import QColor, QFont
from ..constants import DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR, ACCENT_COLOR

class ModernDialog(QDialog):
    def __init__(self, parent=None, title="Dialog"):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # Initialize drag position
        self.old_pos = None
        
        # Main layout for the whole dialog (including shadow margin)
        # We use a distinct name to avoid conflict if subclasses try to access 'layout'
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(10, 10, 10, 10) # Margin for shadow
        
        # Container widget (the actual visible window)
        self.container = QWidget()
        self.container.setObjectName("ModernDialogContainer")
        self.container.setStyleSheet(f"""
            #ModernDialogContainer {{
                background-color: {DARK_GREY};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
            }}
        """)
        
        # Shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 0)
        self.container.setGraphicsEffect(shadow)
        
        self.root_layout.addWidget(self.container)
        
        # Container layout
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(0)
        
        # Title bar
        self.title_bar = QWidget()
        self.title_bar.setFixedHeight(40)
        self.title_bar.setStyleSheet(f"""
            QWidget {{
                background-color: {MEDIUM_GREY};
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                border-bottom: 1px solid {LIGHT_GREY};
            }}
        """)
        
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(15, 0, 10, 0)
        
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.title_label.setStyleSheet(f"color: {TEXT_COLOR}; border: none; background: transparent;")
        
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {TEXT_COLOR};
                background: transparent;
                border: none;
                border-radius: 15px;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: #FF4B4B;
                color: white;
            }}
        """)
        self.close_btn.clicked.connect(self.reject)
        
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.close_btn)
        
        self.container_layout.addWidget(self.title_bar)
        
        # Content area
        self.content_widget = QWidget()
        self.content_widget.setStyleSheet(f"""
            QWidget {{
                background-color: {DARK_GREY};
                color: {TEXT_COLOR};
            }}
            QLabel {{
                color: {TEXT_COLOR};
            }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QListWidget, QTableWidget {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                padding: 5px;
                selection-background-color: {ACCENT_COLOR};
                selection-color: {DARK_GREY};
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QListWidget:focus {{
                border: 1px solid {ACCENT_COLOR};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: {ACCENT_COLOR};
            }}
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                padding: 6px 12px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {LIGHT_GREY};
                border: 1px solid {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
            }}
            /* Primary/Action Button Style */
            QPushButton[class="primary"] {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
                border: none;
            }}
            QPushButton[class="primary"]:hover {{
                background-color: #00C4A0;
            }}
            
            QTabWidget::pane {{
                border: 1px solid {LIGHT_GREY};
                background-color: {DARK_GREY};
                border-radius: 5px;
            }}
            QTabBar::tab {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QTabBar::tab:selected {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
            }}
            QTabBar::tab:hover {{
                background-color: {LIGHT_GREY};
            }}
            
            QScrollBar:vertical {{
                border: none;
                background: {DARK_GREY};
                width: 10px;
                margin: 0px 0px 0px 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {LIGHT_GREY};
                min-height: 20px;
                border-radius: 5px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
        """)
        
        # Child classes should add their layout to self.content_layout
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(20, 20, 20, 20)
        self.content_layout.setSpacing(10)
        
        self.container_layout.addWidget(self.content_widget)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Map event pos to title bar coordinates
            title_bar_pos = self.title_bar.mapFrom(self, event.pos())
            if self.title_bar.rect().contains(title_bar_pos):
                self.old_pos = event.globalPos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.old_pos:
            delta = event.globalPos() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPos()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.old_pos = None
        super().mouseReleaseEvent(event)
        
    def setWindowTitle(self, title):
        if hasattr(self, 'title_label'):
            self.title_label.setText(title)
        super().setWindowTitle(title)
