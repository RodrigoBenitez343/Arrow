# graphui/app.py

import sys
import os
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt
from .main_window import MainWindow
from .constants import DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR, RED_PRIMARY, RED_DARK
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
    
    # Apply dark theme stylesheet
    dark_stylesheet = f"""
    QMainWindow {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
    }}
    
    QWidget {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
        border: none;
    }}
    
    QMenuBar {{
        background-color: {MEDIUM_GREY};
        color: {TEXT_COLOR};
        border: 1px solid #14FFFFFF;
        border-radius: 3px;
    }}
    
    QMenuBar::item {{
        background-color: transparent;
        padding: 4px 8px;
        border-radius: 3px;
    }}
    
    QMenuBar::item:selected {{
        background-color: {RED_PRIMARY};
    }}
    
    QMenuBar::item:pressed {{
        background-color: {RED_DARK};
    }}
    
    QMenu {{
        background-color: {MEDIUM_GREY};
        color: {TEXT_COLOR};
        border: 1px solid #14FFFFFF;
        border-radius: 3px;
    }}
    
    QMenu::item {{
        padding: 6px 12px;
        border-radius: 3px;
    }}
    
    QMenu::item:selected {{
        background-color: {RED_PRIMARY};
    }}
    
    QFileDialog {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
    }}
    
    QInputDialog {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
    }}
    
    QMessageBox {{
        background-color: {DARK_GREY};
        color: {TEXT_COLOR};
    }}
    
    QScrollBar:vertical {{
        background-color: {MEDIUM_GREY};
        width: 12px;
        border-radius: 6px;
    }}
    
    QScrollBar::handle:vertical {{
        background-color: {LIGHT_GREY};
        border-radius: 6px;
        min-height: 20px;
    }}
    
    QScrollBar::handle:vertical:hover {{
        background-color: {RED_PRIMARY};
    }}
    
    QScrollBar:horizontal {{
        background-color: {MEDIUM_GREY};
        height: 12px;
        border-radius: 6px;
    }}
    
    QScrollBar::handle:horizontal {{
        background-color: {LIGHT_GREY};
        border-radius: 6px;
        min-width: 20px;
    }}
    
    QScrollBar::handle:horizontal:hover {{
        background-color: {RED_PRIMARY};
    }}
    
    QScrollBar::add-line, QScrollBar::sub-line {{
        border: none;
        background: none;
    }}
    """
    
    app.setStyleSheet(dark_stylesheet)
    
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
