# graphui/app.py

import sys
import os
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from .main_window import MainWindow
from .constants import (DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR, TEXT_MUTED,
                        ACCENT_COLOR, ACCENT_HOVER, ACCENT_SOFT, HAIRLINE, BLOCK_COLOR,
                        BLOCK_HOVER, CONTROL_BG, CONTROL_HOVER, WELL_BG, BTN_PRIMARY_TEXT,
                        FONT_FAMILY, RADIUS_SM, RADIUS_MD)
from .i18n import set_language_from_os

def main():
    """Main function to run the application"""
    try:
        os.environ.setdefault('QT_ENABLE_HIGHDPI_SCALING', '1')
        os.environ.setdefault('QT_AUTO_SCREEN_SCALE_FACTOR', '1')
        os.environ.setdefault('QT_SCALE_FACTOR_ROUNDING_POLICY', 'PassThrough')
    except Exception:
        pass
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass
    try:
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    except Exception:
        pass
    try:
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    except Exception:
        pass
    app = QApplication(sys.argv)
    # Detect and set UI language from OS
    set_language_from_os()
    
    # Apply dark theme stylesheet (parity with the Arrow-online site palette)
    dark_stylesheet = f"""
    QWidget {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
        border: none;
        font-family: {FONT_FAMILY};
        font-size: 13px;
    }}

    QMainWindow {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
    }}

    /* ---- Menu bar / menus ---- */
    QMenuBar {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
        border: none;
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 5px 12px;
        border-radius: {RADIUS_SM}px;
    }}
    QMenuBar::item:selected {{
        background-color: {CONTROL_HOVER};
    }}
    QMenuBar::item:pressed {{
        background-color: {CONTROL_HOVER};
    }}
    QMenu {{
        background-color: {MEDIUM_GREY};
        color: {TEXT_COLOR};
        border: 1px solid {HAIRLINE};
        border-radius: {RADIUS_MD}px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 14px;
        border-radius: {RADIUS_SM}px;
    }}
    QMenu::item:selected {{
        background-color: {ACCENT_SOFT};
        color: {TEXT_COLOR};
    }}
    QMenu::separator {{
        height: 1px;
        background: {HAIRLINE};
        margin: 6px 8px;
    }}

    /* ---- Buttons (fallback for controls that set no style of their own) ---- */
    QPushButton {{
        background-color: {CONTROL_BG};
        color: {TEXT_COLOR};
        border: 1px solid {HAIRLINE};
        border-radius: {RADIUS_SM}px;
        padding: 6px 14px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {CONTROL_HOVER};
        border: 1px solid rgba(255, 255, 255, 0.18);
    }}
    QPushButton:disabled {{
        color: {TEXT_MUTED};
        border-color: rgba(255, 255, 255, 0.06);
    }}
    QPushButton[class="primary"] {{
        background-color: {ACCENT_COLOR};
        color: {BTN_PRIMARY_TEXT};
        border: none;
        font-weight: 600;
    }}
    QPushButton[class="primary"]:hover {{
        background-color: {ACCENT_HOVER};
    }}

    /* ---- Inputs ---- */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
        background-color: {CONTROL_BG};
        color: {TEXT_COLOR};
        border: 1px solid {HAIRLINE};
        border-radius: {RADIUS_SM}px;
        padding: 5px 8px;
        selection-background-color: {ACCENT_COLOR};
        selection-color: {BTN_PRIMARY_TEXT};
    }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 1px solid {ACCENT_COLOR};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 22px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {MEDIUM_GREY};
        border: 1px solid {HAIRLINE};
        selection-background-color: {ACCENT_COLOR};
        selection-color: {BTN_PRIMARY_TEXT};
        outline: none;
    }}

    QToolTip {{
        background-color: {MEDIUM_GREY};
        color: {TEXT_COLOR};
        border: 1px solid {HAIRLINE};
        padding: 4px 6px;
    }}

    /* ---- Dialogs / message boxes ---- */
    QDialog, QFileDialog, QInputDialog, QMessageBox {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
    }}

    /* ---- Scrollbars (neutral, thin) ---- */
    QScrollBar:vertical {{
        background: transparent;
        width: 12px;
        border: none;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: #3a3f47;
        border-radius: 6px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #4a5058;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 12px;
        border: none;
        margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: #3a3f47;
        border-radius: 6px;
        min-width: 24px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: #4a5058;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{
        border: none;
        background: none;
        width: 0;
        height: 0;
    }}
    QScrollBar::add-page, QScrollBar::sub-page {{
        background: none;
    }}
    """
    
    app.setStyleSheet(dark_stylesheet)
    
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
