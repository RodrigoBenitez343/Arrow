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
    # Fallback: draw minimal chevrons with DPR-aware pixmap
    dpr = 1.0
    try:
        scr = QApplication.primaryScreen()
        dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
    except Exception:
        dpr = 1.0
    w = int(size * dpr)
    h = int(size * dpr)
    pix = QPixmap(w, h)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = p.pen()
    pen.setColor(QColor(TEXT_COLOR))
    try:
        pen.setWidthF(2.0)
    except Exception:
        pen.setWidth(2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = float(size) * dpr
    if name == 'CHEVRON_RIGHT':
        p.drawPolyline(QPoint(int(0.35*s), int(0.25*s)), QPoint(int(0.65*s), int(0.5*s)), QPoint(int(0.35*s), int(0.75*s)))
    elif name == 'CHEVRON_LEFT':
        p.drawPolyline(QPoint(int(0.65*s), int(0.25*s)), QPoint(int(0.35*s), int(0.5*s)), QPoint(int(0.65*s), int(0.75*s)))
    elif name == 'CHEVRON_UP':
        p.drawPolyline(QPoint(int(0.25*s), int(0.65*s)), QPoint(int(0.5*s), int(0.35*s)), QPoint(int(0.75*s), int(0.65*s)))
    else:  # CHEVRON_DOWN
        p.drawPolyline(QPoint(int(0.25*s), int(0.35*s)), QPoint(int(0.5*s), int(0.65*s)), QPoint(int(0.75*s), int(0.35*s)))
    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
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
            f"QFrame#actionsContent {{ background-color: {DARK_GREY}; border: 2px solid {LIGHT_GREY}; border-radius: 10px; }}"
        )
        try:
            self._content_frame.setMinimumHeight(40)
        except Exception:
            pass
        layout = QHBoxLayout(self._content_frame)
        layout.setContentsMargins(8, 3, 8, 9)
        layout.setSpacing(6)

        layout.setAlignment(Qt.AlignVCenter)
        layout.addWidget(self._make_record_icon_button(), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_open(), self._open_chain, tooltip="Load Chain", label_text="Load"), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_save(), self._save_chain, tooltip="Save Chain", label_text="Save"), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_play(), self._run_chain, tooltip="Run Chain", label_text="Run"), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_delete(), self._delete_selected, tooltip="Delete Selected", label_text="Delete"), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_copy(), self._copy_selected, tooltip="Copy Selected", label_text="Copy"), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_paste(), self._paste_selected, tooltip="Paste", label_text="Paste"), alignment=Qt.AlignVCenter)
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

    def _make_custom_icon_button(self, icon: QIcon, handler, tooltip=None, label_text=None):
        btn = QPushButton(label_text or "", self)
        btn.clicked.connect(handler)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            btn.setFixedHeight(26)
        except Exception:
            pass
        btn.setIcon(icon)
        btn.setIconSize(QSize(22, 22))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 14px;
                padding: 2px 8px;
                min-width: 96px;
                min-height: 24px;
            }}
            QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}
            QPushButton:pressed {{ background-color: {BLOCK_HOVER}; }}
            """
        )
        return btn

    def _make_record_icon_button(self):
        btn = QPushButton("Record", self)
        btn.clicked.connect(lambda: self.graph_view.record_new_sequence())
        btn.setToolTip("Record Sequence")
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            btn.setFixedHeight(26)
        except Exception:
            pass
        pix = QPixmap(22, 22)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(220, 0, 0))
        p.setPen(Qt.NoPen)
        p.drawEllipse(3, 3, 16, 16)
        p.end()
        btn.setIcon(QIcon(pix))
        btn.setIconSize(QSize(22, 22))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 14px;
                padding: 2px 8px;
                min-width: 96px;
                min-height: 24px;
            }}
            QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}
            QPushButton:pressed {{ background-color: {BLOCK_HOVER}; }}
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

    def _delete_selected(self):
        try:
            if hasattr(self.graph_view, 'delete_selected_nodes'):
                self.graph_view.delete_selected_nodes()
        except Exception as e:
            logger.error(f"Delete selected error: {e}")

    def _copy_selected(self):
        try:
            if hasattr(self.graph_view, 'copy_selected_nodes'):
                self.graph_view.copy_selected_nodes()
        except Exception as e:
            logger.error(f"Copy selected error: {e}")

    def _paste_selected(self):
        try:
            if hasattr(self.graph_view, 'paste_copied_nodes'):
                self.graph_view.paste_copied_nodes()
        except Exception as e:
            logger.error(f"Paste error: {e}")

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

    def _icon_delete(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.TRASH, size=24, color=TEXT_COLOR, stroke_width=2.0)
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
        p.drawRect(6, 9, 12, 12)
        p.drawLine(9, 12, 9, 18)
        p.drawLine(15, 12, 15, 18)
        p.drawLine(7, 9, 17, 9)
        p.drawLine(10, 6, 14, 6)
        p.drawLine(10, 6, 8, 9)
        p.drawLine(14, 6, 16, 9)
        p.end()
        return QIcon(pix)

    def _icon_copy(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.COPY, size=24, color=TEXT_COLOR, stroke_width=2.0)
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
        p.drawRect(6, 6, 10, 10)
        p.drawRect(10, 10, 10, 10)
        p.end()
        return QIcon(pix)

    def _icon_paste(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.CLIPBOARD_TEXT, size=24, color=TEXT_COLOR, stroke_width=2.0)
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
        p.drawRoundedRect(6, 8, 12, 12, 2, 2)
        p.drawLine(8, 12, 16, 12)
        p.drawLine(8, 15, 16, 15)
        p.drawLine(8, 18, 14, 18)
        p.end()
        return QIcon(pix)
