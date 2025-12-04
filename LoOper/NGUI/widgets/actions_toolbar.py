import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QApplication, QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QPoint, QSize, QTimer
from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon

try:
    from pytablericons import TablerIcons, OutlineIcon
except Exception:
    TablerIcons = None
    OutlineIcon = None

try:
    from PIL.ImageQt import ImageQt
except Exception:
    ImageQt = None

from ..constants import (
    DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR, ACCENT_COLOR,
    BLOCK_COLOR, BLOCK_HOVER
)

logger = logging.getLogger(__name__)

def load_tabler_icon(name: str, size: int) -> QIcon:
    try:
        if TablerIcons and OutlineIcon:
            icon_enum = getattr(OutlineIcon, name, None)
            if icon_enum is not None:
                img = TablerIcons.load(icon_enum, size=size, color=TEXT_COLOR, stroke_width=2.0)
                try:
                    return QIcon(img.toqpixmap())
                except Exception:
                    qimg = ImageQt(img) if ImageQt else None
                    if qimg:
                        return QIcon(QPixmap.fromImage(qimg))
    except Exception:
        pass
    # Fallback: draw minimal chevrons
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = p.pen()
    pen.setColor(QColor(TEXT_COLOR))
    pen.setWidth(2)
    p.setPen(pen)
    s = size
    if name == 'CHEVRON_RIGHT':
        p.drawPolyline(QPoint(int(0.35*s), int(0.25*s)), QPoint(int(0.65*s), int(0.5*s)), QPoint(int(0.35*s), int(0.75*s)))
    elif name == 'CHEVRON_LEFT':
        p.drawPolyline(QPoint(int(0.65*s), int(0.25*s)), QPoint(int(0.35*s), int(0.5*s)), QPoint(int(0.65*s), int(0.75*s)))
    elif name == 'CHEVRON_UP':
        p.drawPolyline(QPoint(int(0.25*s), int(0.65*s)), QPoint(int(0.5*s), int(0.35*s)), QPoint(int(0.75*s), int(0.65*s)))
    else:  # CHEVRON_DOWN
        p.drawPolyline(QPoint(int(0.25*s), int(0.35*s)), QPoint(int(0.5*s), int(0.65*s)), QPoint(int(0.75*s), int(0.35*s)))
    p.end()
    return QIcon(pix)

class ActionsToolbar(QWidget):
    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._build_ui()

    def _build_ui(self):
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Content frame (framed)
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("actionsContent")
        self._content_frame.setStyleSheet(
            f"QFrame#actionsContent {{ border: 1px solid {LIGHT_GREY}; border-radius: 6px; }}"
        )
        layout = QHBoxLayout(self._content_frame)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        layout.addWidget(self._make_record_icon_button())
        layout.addWidget(self._make_custom_icon_button(self._icon_open(), self._open_chain, tooltip="Load Chain"))
        layout.addWidget(self._make_custom_icon_button(self._icon_save(), self._save_chain, tooltip="Save Chain"))
        layout.addWidget(self._make_custom_icon_button(self._icon_play(), self._run_chain, tooltip="Run Chain"))
        outer.addWidget(self._content_frame)

        # Floating toggle button (modern chevron) centered under the actions frame
        self._toggle_btn_actions = QToolButton(self.graph_view)
        self._toggle_btn_actions.setToolTip("Toggle Actions Toolbar")
        ico2 = load_tabler_icon('CHEVRON_DOWN', 14)
        if ico2:
            self._toggle_btn_actions.setIcon(ico2)
            self._toggle_btn_actions.setIconSize(QSize(14, 14))
        else:
            self._toggle_btn_actions.setText("▾")
        self._toggle_btn_actions.setAutoRaise(True)
        self._toggle_btn_actions.setCursor(Qt.PointingHandCursor)
        self._toggle_btn_actions.setFixedSize(28, 28)
        self._toggle_btn_actions.setStyleSheet(
            f"QToolButton {{ background-color: rgba(32,32,32,180); border: 1px solid {LIGHT_GREY}; border-radius: 14px; }}"
            f"QToolButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        # Shadow effect removed to match right toolbar and prevent artifacts
        self._toggle_btn_actions.clicked.connect(self._toggle_actions_toolbar)
        try:
            QTimer.singleShot(0, self._position_actions_toggle)
        except Exception:
            pass

    def _toggle_actions_toolbar(self):
        try:
            is_visible = self._content_frame.isVisible()
            self._content_frame.setVisible(not is_visible)
            name = 'CHEVRON_DOWN' if is_visible else 'CHEVRON_UP'
            ico = load_tabler_icon(name, 16)
            if ico:
                self._toggle_btn_actions.setIcon(ico)
            else:
                self._toggle_btn_actions.setText("▴" if is_visible else "▾")
            
            # Re-position after toggle state change
            QTimer.singleShot(50, self._position_actions_toggle)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._position_actions_toggle)
        except Exception:
            pass

    def _position_actions_toggle(self):
        try:
            if not hasattr(self, '_toggle_btn_actions') or self._toggle_btn_actions is None:
                return
            
            # Always center horizontally relative to the ActionsToolbar itself
            toolbar_top_left = self.mapTo(self.graph_view, QPoint(0, 0))
            x = toolbar_top_left.x() + self.width() // 2 - 14
            
            # Position Y just below the toolbar
            y = toolbar_top_left.y() + self.height() + 4
                
            self._toggle_btn_actions.move(x, y)
            self._toggle_btn_actions.raise_()
        except Exception:
            pass

    def _make_custom_icon_button(self, icon: QIcon, handler, tooltip=None):
        btn = QPushButton("", self)
        btn.clicked.connect(handler)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        btn.setIcon(icon)
        btn.setIconSize(QSize(24, 24))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 14px;
                padding: 6px;
                min-width: 96px;
                min-height: 44px;
            }}
            QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}
            QPushButton:pressed {{ background-color: {BLOCK_HOVER}; }}
            """
        )
        return btn

    def _make_record_icon_button(self):
        btn = QPushButton("", self)
        btn.clicked.connect(lambda: self.graph_view.record_new_sequence())
        btn.setToolTip("Record Sequence")
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pix = QPixmap(28, 28)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(220, 0, 0))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 20, 20)
        p.end()
        btn.setIcon(QIcon(pix))
        btn.setIconSize(QSize(28, 28))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
                padding: 6px;
                min-width: 90px;
                min-height: 52px;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
            QPushButton:pressed {{ background-color: {DARK_GREY}; }}
            """
        )
        return btn

    def _run_chain(self):
        try:
            window = self.graph_view.window()
            if hasattr(window, 'run_chain'):
                window.run_chain()
            else:
                logger.warning('MainWindow.run_chain not found')
        except Exception as e:
            logger.error(f"Run chain error: {e}")

    def _open_chain(self):
        try:
            window = self.graph_view.window()
            if hasattr(window, 'open_chain'):
                window.open_chain()
            else:
                logger.warning('MainWindow.open_chain not found')
        except Exception as e:
            logger.error(f"Open chain error: {e}")

    def _save_chain(self):
        try:
            window = self.graph_view.window()
            if hasattr(window, 'save_chain'):
                window.save_chain()
            else:
                logger.warning('MainWindow.save_chain not found')
        except Exception as e:
            logger.error(f"Save chain error: {e}")

    def _icon_play(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.PLAYER_PLAY, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(QColor(TEXT_COLOR))
        points = [
            QPoint(6, 5), QPoint(19, 12), QPoint(6, 19)
        ]
        p.drawPolygon(*points)
        p.end()
        return QIcon(pix)

    def _icon_open(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.FOLDER_OPEN, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        # folder outline
        p.drawRoundedRect(4, 8, 16, 10, 3, 3)
        p.drawLine(4, 8, 10, 8)
        p.drawLine(10, 8, 12, 6)
        # up arrow inside
        p.drawLine(12, 15, 12, 11)
        p.drawLine(12, 11, 9, 14)
        p.drawLine(12, 11, 15, 14)
        p.end()
        return QIcon(pix)

    def _icon_save(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.DEVICE_FLOPPY, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        # tray
        p.drawRoundedRect(5, 14, 14, 5, 2, 2)
        # down arrow
        p.drawLine(12, 6, 12, 13)
        p.drawLine(12, 13, 9, 10)
        p.drawLine(12, 13, 15, 10)
        p.end()
        return QIcon(pix)
