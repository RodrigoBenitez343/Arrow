from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QWidget, QLabel, QPushButton, 
                             QHBoxLayout, QGraphicsDropShadowEffect, QSizeGrip,
                             QScrollArea, QFrame)
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
        # Ensure padding inside the container respects the border radius
        self.container_layout.setContentsMargins(1, 1, 1, 1) 
        self.container_layout.setSpacing(0)
        
        # Title bar
        self.title_bar = QWidget()
        self.title_bar.setFixedHeight(40)
        self.title_bar.setStyleSheet(f"""
            QWidget {{
                background-color: {MEDIUM_GREY};
                border-top-left-radius: 9px;  /* Match container radius minus border */
                border-top-right-radius: 9px;
                border-bottom: 1px solid {LIGHT_GREY};
            }}
        """)
        
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(15, 0, 15, 0) # Increased right margin to avoid corner clip
        
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.title_label.setStyleSheet(f"color: {TEXT_COLOR}; border: none; background: transparent;")
        
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        # Ensure button stays on top and doesn't get clipped
        self.close_btn.raise_() 
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {TEXT_COLOR};
                background: transparent;
                border: none;
                border-radius: 15px; /* Half of 30px size */
                font-size: 14px;
                padding: 0px;
                margin: 0px;
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
            }}
            QPushButton:hover {{
                background-color: #FF4B4B;
                color: white;
            }}
        """)
        self.close_btn.clicked.connect(self.reject)
        
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        title_layout.addWidget(self.close_btn, 0, Qt.AlignVCenter) # Explicitly center vertically
        
        self.container_layout.addWidget(self.title_bar)
        
        # Content area (Scrollable)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background-color: {DARK_GREY};
                border: none;
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
            QScrollBar:horizontal {{
                border: none;
                background: {DARK_GREY};
                height: 10px;
                margin: 0px 0px 0px 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: {LIGHT_GREY};
                min-width: 20px;
                border-radius: 5px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
        """)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

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
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border: 1px solid {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
                border: none;
            }}
            /* Primary/Action Button Style */
            QPushButton[class="primary"] {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
                border: none;
            }}
            QPushButton[class="primary"]:hover {{
                background-color: #00E0B8;
                border: 1px solid {ACCENT_COLOR};
            }}
            QPushButton[class="primary"]:pressed {{
                background-color: #008F75;
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
        """)
        
        # Child classes should add their layout to self.content_layout
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(20, 20, 20, 20)
        self.content_layout.setSpacing(10)
        
        self.scroll_area.setWidget(self.content_widget)
        self.container_layout.addWidget(self.scroll_area)
        
        # Resize grip
        grip_layout = QHBoxLayout()
        grip_layout.setContentsMargins(0, 0, 5, 5)
        grip_layout.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(20, 20)
        self.size_grip.setStyleSheet("background: transparent;")
        grip_layout.addWidget(self.size_grip)
        self.container_layout.addLayout(grip_layout)

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
