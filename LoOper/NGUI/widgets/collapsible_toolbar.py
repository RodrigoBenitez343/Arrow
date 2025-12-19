import logging
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QLabel, QApplication, QStyle, QScrollArea,
    QGraphicsDropShadowEffect, QMessageBox, QDialog
)
from PyQt5.QtCore import Qt, QPoint, QSize, QFileSystemWatcher, QTimer, QPointF, QRectF
from PyQt5.QtGui import QDrag, QPixmap, QPainter, QColor, QIcon, QPainterPath, QFont, QPolygonF
from PyQt5.QtCore import QMimeData
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
    BLOCK_COLOR, BLOCK_HOVER,
    LLM_COLOR, TTS_COLOR, CONDITIONAL_COLOR, CHAIN_IMPORT_COLOR, FORM_FILLER_COLOR,
    CODE_NODE_COLOR
)
from ..dialogs.base_dialog import ModernDialog


logger = logging.getLogger(__name__)
if not logger.handlers:
    fh = logging.FileHandler('graphui_debug.log')
    ch = logging.StreamHandler()
    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)


# Module-level helper for modern chevron icons with robust fallback
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
    pen.setWidth(2)
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

def _load_tabler_square(icon_enum, size: int, pad: int = 3) -> QIcon:
    try:
        if TablerIcons and icon_enum is not None:
            img = TablerIcons.load(icon_enum, size=int(size), color=TEXT_COLOR, stroke_width=2.0)
            try:
                src = img.toqpixmap()
            except Exception:
                qimg = ImageQt(img) if ImageQt else None
                if qimg:
                    src = QPixmap.fromImage(qimg)
                else:
                    src = None
            if src is not None:
                dpr = 1.0
                try:
                    scr = QApplication.primaryScreen()
                    dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
                except Exception:
                    dpr = 1.0
                w = int(size * dpr)
                h = int(size * dpr)
                target = QPixmap(w, h)
                target.fill(Qt.transparent)
                p = QPainter(target)
                p.setRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                inner_w = max(1, int((size - 2*pad) * dpr))
                inner_h = inner_w
                scaled = src.scaled(inner_w, inner_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = (int(size * dpr) - scaled.width()) // 2
                y = (int(size * dpr) - scaled.height()) // 2
                p.drawPixmap(x, y, scaled)
                p.end()
                try:
                    target.setDevicePixelRatio(dpr)
                except Exception:
                    pass
                return QIcon(target)
    except Exception:
        pass
    return QIcon()


def _fallback_node_icon(node_type: str, size: int, pad: int = 3) -> QIcon:
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
    pen.setWidth(2)
    p.setPen(pen)
    s = float(size) * dpr - 2*pad*dpr
    ox = int(pad * dpr)
    oy = int(pad * dpr)
    if node_type == 'conditional':
        p.drawEllipse(ox + int(0.15*s), oy + int(0.15*s), int(0.7*s), int(0.7*s))
        p.drawPoint(ox + int(0.5*s), oy + int(0.75*s))
        p.drawLine(ox + int(0.45*s), oy + int(0.43*s), ox + int(0.55*s), oy + int(0.43*s))
        p.drawLine(ox + int(0.5*s), oy + int(0.43*s), ox + int(0.5*s), oy + int(0.58*s))
    elif node_type == 'llm':
        p.drawRoundedRect(ox + int(0.2*s), oy + int(0.25*s), int(0.6*s), int(0.5*s), 3, 3)
        p.drawEllipse(ox + int(0.32*s), oy + int(0.38*s), int(0.08*s), int(0.08*s))
        p.drawEllipse(ox + int(0.6*s), oy + int(0.38*s), int(0.08*s), int(0.08*s))
        p.drawLine(ox + int(0.4*s), oy + int(0.6*s), ox + int(0.6*s), oy + int(0.6*s))
        p.drawRect(ox + int(0.45*s), oy + int(0.17*s), int(0.1*s), int(0.08*s))
    elif node_type == 'tts':
        try:
            try:
                pen = p.pen()
                pen.setWidthF(2.0)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
            except Exception:
                pass
            fs = float(size) * dpr - 2*pad*dpr
            fx = float(ox)
            fy = float(oy)
            bw = fs * 0.22
            bh = fs * 0.42
            bx = fx + fs * 0.08
            by = fy + fs * 0.29
            p.drawRect(QRectF(bx, by, bw, bh))
            cone_x = bx + bw
            cone_t = by - fs * 0.06
            cone_b = by + bh + fs * 0.06
            cone_out = fs * 0.18
            p.drawPolygon(QPolygonF([
                QPointF(cone_x, cone_t),
                QPointF(cone_x + cone_out, by + bh * 0.28),
                QPointF(cone_x, cone_b)
            ]))
            aw1 = fs * 0.28
            aw2 = fs * 0.40
            p.drawArc(QRectF(cone_x + fs*0.04, by + bh*0.10 - aw1*0.5, aw1, aw1), 90*16, -180*16)
            p.drawArc(QRectF(cone_x + fs*0.04, by + bh*0.10 - aw2*0.5, aw2, aw2), 90*16, -180*16)
        except Exception:
            p.drawPolygon(QPoint(ox + int(0.25*s), oy + int(0.35*s)), QPoint(ox + int(0.45*s), oy + int(0.5*s)), QPoint(ox + int(0.25*s), oy + int(0.65*s)))
            p.drawLine(ox + int(0.55*s), oy + int(0.38*s), ox + int(0.55*s), oy + int(0.62*s))
            p.drawLine(ox + int(0.62*s), oy + int(0.34*s), ox + int(0.62*s), oy + int(0.66*s))
    elif node_type == 'chain_import':
        p.drawRoundedRect(ox + int(0.18*s), oy + int(0.18*s), int(0.64*s), int(0.64*s), 4, 4)
        p.drawRoundedRect(ox + int(0.26*s), oy + int(0.26*s), int(0.48*s), int(0.48*s), 4, 4)
    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

def _painter_icon_conditional(size: int) -> QIcon:
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
    p.setRenderHint(QPainter.HighQualityAntialiasing)
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
    cx = s * 0.5
    cy = s * 0.5
    r = s * 0.36
    p.drawEllipse(QRectF(cx - r, cy - r, 2*r, 2*r))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(s * 0.44))
    p.setFont(f)
    p.drawText(0, 0, int(s), int(s), Qt.AlignCenter, "?")
    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

def _painter_icon_llm(size: int) -> QIcon:
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
    p.setRenderHint(QPainter.HighQualityAntialiasing)
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
    cx = s * 0.5
    cy = s * 0.5
    w = s * 0.6
    h = s * 0.5
    p.drawRoundedRect(QRectF(cx - w*0.5, cy - h*0.5, w, h), 4, 4)
    eye_r = s * 0.04
    p.drawEllipse(QRectF(cx - s*0.14 - eye_r, cy - s*0.08 - eye_r, 2*eye_r, 2*eye_r))
    p.drawEllipse(QRectF(cx + s*0.14 - eye_r, cy - s*0.08 - eye_r, 2*eye_r, 2*eye_r))
    p.drawLine(QPointF(cx - s*0.12, cy + s*0.12), QPointF(cx + s*0.12, cy + s*0.12))
    p.drawRect(QRectF(cx - s*0.06, cy - h*0.5 - s*0.06, s*0.12, s*0.06))
    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

def _painter_icon_tts(size: int) -> QIcon:
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
    p.setRenderHint(QPainter.HighQualityAntialiasing)
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
    cx = s * 0.5
    cy = s * 0.5

    p.setBrush(Qt.NoBrush)
    lh = s * 0.28
    bh = s * 0.44
    lx = cx - s * 0.38
    rx = cx - s * 0.06
    p.drawPolygon(QPolygonF([
        QPointF(lx, cy - lh * 0.5),
        QPointF(rx, cy - bh * 0.5),
        QPointF(rx, cy + bh * 0.5),
        QPointF(lx, cy + lh * 0.5)
    ]))

    gap = s * 0.06
    wx = rx + gap + s * 0.02
    r1 = s * 0.18
    r2 = s * 0.28
    r3 = s * 0.38
    p.drawArc(QRectF(wx - r1, cy - r1, 2*r1, 2*r1), int(-40*16), int(80*16))
    p.drawArc(QRectF(wx - r2, cy - r2, 2*r2, 2*r2), int(-40*16), int(80*16))
    p.drawArc(QRectF(wx - r3, cy - r3, 2*r3, 2*r3), int(-40*16), int(80*16))

    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

def _painter_icon_chain(size: int) -> QIcon:
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
    p.setRenderHint(QPainter.HighQualityAntialiasing)
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
    cx = s * 0.5
    cy = s * 0.5
    w1 = s * 0.62
    h1 = w1
    w2 = s * 0.46
    h2 = w2
    rr = int(s * 0.08)
    p.drawRoundedRect(QRectF(cx - w1*0.5, cy - h1*0.5, w1, h1), rr, rr)
    p.drawRoundedRect(QRectF(cx - w2*0.5, cy - h2*0.5, w2, h2), rr, rr)
    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

def _painter_icon_code(size: int) -> QIcon:
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
    p.setRenderHint(QPainter.HighQualityAntialiasing)
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
    cx = s * 0.5
    cy = s * 0.5
    
    # Draw </> style icon
    # <
    p.drawPolyline(QPolygonF([QPointF(cx - s*0.20, cy - s*0.15), QPointF(cx - s*0.32, cy), QPointF(cx - s*0.20, cy + s*0.15)]))
    # >
    p.drawPolyline(QPolygonF([QPointF(cx + s*0.20, cy - s*0.15), QPointF(cx + s*0.32, cy), QPointF(cx + s*0.20, cy + s*0.15)]))
    # /
    p.drawLine(QPointF(cx + s*0.12, cy - s*0.25), QPointF(cx - s*0.12, cy + s*0.25))

    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

_ICON_CACHE = {}

def get_node_icon(node_type: str, size: int) -> QIcon:
    key = (node_type, size)
    cached = _ICON_CACHE.get(key)
    if cached and not cached.isNull():
        return cached
    icon = None
    if TablerIcons and OutlineIcon and node_type != 'tts':
        try:
            icon_map = {
                'conditional': ['HELP_CIRCLE'],
                'llm': ['ROBOT'],
                'chain_import': ['BOX_MULTIPLE'],
                'code': ['CODE', 'TERMINAL_2', 'TERMINAL'],
            }
            names = icon_map.get(node_type)
            if names:
                for nm in names:
                    icon_enum = getattr(OutlineIcon, nm, None)
                    if icon_enum is not None:
                        candidate = _load_tabler_square(icon_enum, size, pad=2)
                        if candidate and not candidate.isNull():
                            icon = candidate
                            break
        except Exception:
            icon = None
    if icon is None:
        painter_map = {
            'conditional': _painter_icon_conditional,
            'llm': _painter_icon_llm,
            'tts': _painter_icon_tts,
            'chain_import': _painter_icon_chain,
            'code': _painter_icon_code,
        }
        maker = painter_map.get(node_type)
        icon = maker(size) if maker else _fallback_node_icon(node_type, size, pad=2)
    _ICON_CACHE[key] = icon
    return icon


class DraggableButton(QPushButton):
    """Colored node-type button supporting drag-to-add and click-to-add."""

    def __init__(self, label: str, node_type: str, bg_color: str, click_handler, parent=None):
        super().__init__(label, parent)
        self.node_type = node_type
        self._drag_start_pos = None
        self._click_handler = click_handler
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # Colored background to reflect node type
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg_color};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 4px 10px;
                border-radius: 14px;
                min-width: 96px;
                min-height: 42px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BLOCK_HOVER};
            }}
            """
        )

    # compound rendering removed; rely on QPushButton icon+text to avoid layered backgrounds

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)

        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            mime.setData('application/x-looper-node-type', self.node_type.encode('utf-8'))

            # Create a pixmap for the drag icon
            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(self.palette().button().color()))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect(), 10, 10)
            painter.end()

            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(pixmap)
            drag.setHotSpot(event.pos() - self.rect().topLeft())

            drag.exec_(Qt.CopyAction)
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        # Treat click as add action
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if callable(self._click_handler):
                    self._click_handler()
            except Exception as e:
                logger.error(f"Toolbar click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class DraggableSequenceButton(QPushButton):
    def __init__(self, label: str, file_path: str, graph_view, parent=None):
        super().__init__(label, parent)
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 4px 10px;
                border-radius: 12px;
                min-width: 96px;
                min-height: 38px;
                font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}
            QPushButton:pressed {{ background-color: {BLOCK_HOVER}; }}
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)
        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            mime.setData('application/x-looper-node-type', b'sequence')
            mime.setText(self.file_path)
            mime.setData('application/x-looper-sequence-file', self.file_path.encode('utf-8'))
            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(self.palette().button().color()))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect(), 10, 10)
            painter.end()
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(pixmap)
            drag.setHotSpot(event.pos() - self.rect().topLeft())
            drag.exec_(Qt.CopyAction)
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if hasattr(self.graph_view, 'node_operations'):
                    self.graph_view.node_operations.add_sequence_from_file(self.file_path)
            except Exception as e:
                logger.error(f"Toolbar sequence click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


    def _icon_delete_large(self) -> QPixmap:
        """Create a large delete icon pixmap."""
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        
        # Use a soft red for the icon to match the theme
        pen = p.pen()
        pen.setColor(QColor("#FF6B6B"))
        pen.setWidth(3)
        p.setPen(pen)
        
        # Scale factor relative to 24px icon
        # Adjusted drawing to be more square and less elongated
        s = size / 24.0
        
        # Center the icon in the 64x64 area
        # Original drawing was:
        # p.drawRect(int(6*s), int(9*s), int(12*s), int(12*s)) -> 16x24 to 48x32
        
        # New dimensions for a more balanced look
        w = int(14 * s)  # Width
        h = int(14 * s)  # Height of the bin body
        x = int((size - w) / 2)
        y = int((size - h) / 2) + int(2*s) # Shift down slightly for lid
        
        # Draw trash can body
        p.drawRect(x, y, w, h)
        
        # Draw vertical lines
        line_spacing = w // 4
        p.drawLine(x + line_spacing, y + int(2*s), x + line_spacing, y + h - int(2*s))
        p.drawLine(x + 2*line_spacing, y + int(2*s), x + 2*line_spacing, y + h - int(2*s))
        p.drawLine(x + 3*line_spacing, y + int(2*s), x + 3*line_spacing, y + h - int(2*s))
        
        # Draw lid line
        lid_w = w + int(4*s)
        lid_x = x - int(2*s)
        lid_y = y
        p.drawLine(lid_x, lid_y, lid_x + lid_w, lid_y)
        
        # Draw handle
        handle_w = int(6*s)
        handle_h = int(3*s)
        handle_x = int((size - handle_w) / 2)
        handle_y = lid_y - handle_h
        p.drawPolyline(QPolygonF([
            QPointF(handle_x, lid_y),
            QPointF(handle_x, handle_y),
            QPointF(handle_x + handle_w, handle_y),
            QPointF(handle_x + handle_w, lid_y)
        ]))
        
        p.end()
        return pix

class ModernDeleteDialog(ModernDialog):
    """A custom modern dialog for delete confirmation."""
    def __init__(self, parent, filename):
        super().__init__(parent, title="Delete Sequence")
        self.resize(400, 250)  # Increased height to fit content without scrollbar
        
        # Hide the default title bar close button if desired, but base_dialog has it.
        # We can just use the content layout.
        
        # Override the scroll area from base_dialog if possible or just use it.
        # ModernDialog puts everything in a scroll area. We want to avoid scrollbars.
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Ensure scroll widget is resizable
        self.scroll_area.setWidgetResizable(True)

        layout = self.content_layout
        layout.setSpacing(10)  # Reduced spacing
        layout.setContentsMargins(10, 10, 10, 10) # Add margin around content
        
        # Message Area
        msg_widget = QWidget()
        msg_layout = QHBoxLayout(msg_widget)
        msg_layout.setContentsMargins(5, 10, 5, 10)
        msg_layout.setSpacing(15)
        
        # Icon
        icon_label = QLabel()
        icon_label.setPixmap(self._icon_delete_large())
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(64, 64)
        msg_layout.addWidget(icon_label)
        
        # Text
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 10, 0, 10) # Center vertically relative to icon
        
        title = QLabel(f"Delete '{filename}'?")
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold; font-size: 16px;")
        
        desc = QLabel("This action cannot be undone.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 13px; opacity: 0.8;")
        
        text_layout.addWidget(title)
        text_layout.addWidget(desc)
        text_layout.addStretch()
        
        msg_layout.addLayout(text_layout)
        layout.addWidget(msg_widget)
        
        layout.addStretch() 
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(10, 0, 10, 0) # Removed bottom margin
        btn_layout.setSpacing(15)
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.setFixedWidth(100)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 6px;
                padding: 6px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {LIGHT_GREY};
                border-color: {LIGHT_GREY};
            }}
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        delete_btn = QPushButton("Delete")
        delete_btn.setCursor(Qt.PointingHandCursor)
        delete_btn.setFixedWidth(100)
        delete_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #DC3545;
                color: white;
                border: 1px solid #DC3545;
                border-radius: 6px;
                padding: 6px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #BB2D3B;
                border-color: #BB2D3B;
            }}
        """)
        delete_btn.clicked.connect(self.accept)
        btn_layout.addWidget(delete_btn)
        
        layout.addLayout(btn_layout)
        
    def _icon_delete_large(self) -> QPixmap:
        """Create a large delete icon pixmap."""
        size = 64
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        
        # Use a soft red for the icon to match the theme
        pen = p.pen()
        pen.setColor(QColor("#FF6B6B"))
        pen.setWidth(3)
        p.setPen(pen)
        
        # Scale factor relative to 24px icon
        s = size / 24.0
        
        # Draw trash can
        p.drawRect(int(6*s), int(9*s), int(12*s), int(12*s))
        p.drawLine(int(9*s), int(12*s), int(9*s), int(18*s))
        p.drawLine(int(15*s), int(12*s), int(15*s), int(18*s))
        p.drawLine(int(7*s), int(9*s), int(17*s), int(9*s))
        p.drawLine(int(10*s), int(6*s), int(14*s), int(6*s))
        p.drawLine(int(10*s), int(6*s), int(8*s), int(9*s))
        p.drawLine(int(14*s), int(6*s), int(16*s), int(9*s))
        p.end()
        return pix


class TrashBinWidget(QPushButton):
    """A drop target for deleting sequences."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setText("🗑 Drop to Delete")
        self.setToolTip("Drag sequence files here to delete them")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 2px dashed {LIGHT_GREY};
                border-radius: 10px;
                padding: 8px;
                font-weight: bold;
                min-height: 40px;
            }}
            QPushButton:hover {{
                border-color: #FF6B6B;
                background-color: rgba(255, 107, 107, 0.1);
                color: #FF6B6B;
            }}
            """
        )

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/x-looper-sequence-file'):
            event.accept()
            self.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: rgba(255, 107, 107, 0.2);
                    color: #FF6B6B;
                    border: 2px dashed #FF6B6B;
                    border-radius: 10px;
                    padding: 8px;
                    font-weight: bold;
                    min-height: 40px;
                }}
                """
            )
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        # Restore original style
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 2px dashed {LIGHT_GREY};
                border-radius: 10px;
                padding: 8px;
                font-weight: bold;
                min-height: 40px;
            }}
            QPushButton:hover {{
                border-color: #FF6B6B;
                background-color: rgba(255, 107, 107, 0.1);
                color: #FF6B6B;
            }}
            """
        )
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasFormat('application/x-looper-sequence-file'):
            try:
                file_path_bytes = event.mimeData().data('application/x-looper-sequence-file')
                file_path = str(file_path_bytes, 'utf-8')
                
                if not os.path.exists(file_path):
                    return

                filename = os.path.basename(file_path)
                
                dialog = ModernDeleteDialog(self, filename)
                if dialog.exec_() == QDialog.Accepted:
                    try:
                        os.remove(file_path)
                        event.accept()
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"Failed to delete file: {e}")
            except Exception as e:
                logger.error(f"Error processing drop in trash bin: {e}")
            finally:
                # Restore style
                self.dragLeaveEvent(None)
        else:
            event.ignore()


class CollapsibleToolbar(QWidget):
    """Collapsible top toolbar that mirrors context menu actions."""

    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        try:
            self._watcher = QFileSystemWatcher(self)
            folder = getattr(self.graph_view, 'sequences_folder', None)
            if folder:
                try:
                    os.makedirs(folder, exist_ok=True)
                except Exception:
                    pass
                try:
                    success = self._watcher.addPath(folder)
                    if success:
                        logger.debug(f"CollapsibleToolbar: Watching sequences folder: {folder}")
                    else:
                        logger.warning(f"CollapsibleToolbar: Failed to add folder to watcher: {folder}")
                except Exception as e:
                    logger.error(f"CollapsibleToolbar: Error adding folder to watcher: {e}")
            try:
                self._watcher.directoryChanged.connect(self.refresh_sequences_list)
                self._watcher.fileChanged.connect(self.refresh_sequences_list)
            except Exception as e:
                logger.error(f"CollapsibleToolbar: Error connecting watcher signals: {e}")
        except Exception as e:
            self._watcher = None
            logger.error(f"CollapsibleToolbar: Failed to initialize QFileSystemWatcher: {e}")
        self._build_ui()

    def _build_ui(self):
        # Start expanded
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.setMinimumWidth(160)
        self.setMaximumWidth(160)
        main_layout = QVBoxLayout(self)
        # Minimal margins so we can shrink to near-zero
        main_layout.setContentsMargins(0, 0, 4, 0)
        main_layout.setSpacing(0)
        
        # Make the main widget transparent (content frame provides background)
        # This ensures no "black frame" when integrated in layout
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Container for content
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("toolbarContent")
        self._content_frame.setStyleSheet(
            f"QFrame#toolbarContent {{ background-color: {DARK_GREY}; border: 2px solid {LIGHT_GREY}; border-radius: 10px; }}"
        )
        content_layout = QVBoxLayout(self._content_frame)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(6)

        # Title Label
        title_label = QLabel("Sequence List")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFixedHeight(40)  # Match standard toolbar height
        title_label.setStyleSheet(
            f"""
            QLabel {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
                font-weight: bold;
                font-size: 14px;
                padding: 4px;
            }}
            """
        )
        content_layout.addWidget(title_label)

        top_cluster = QFrame(self._content_frame)
        top_cluster.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._top_layout = QVBoxLayout(top_cluster)
        self._top_layout.setContentsMargins(0, 0, 0, 0)
        self._top_layout.setSpacing(1)
        self._sequences_scroll = self._make_sequences_scroll_box()
        self._top_layout.addWidget(self._sequences_scroll)

        # Trash Bin
        self.trash_bin = TrashBinWidget(self._content_frame)
        self._top_layout.addWidget(self.trash_bin)
        self._top_layout.addSpacing(4)

        nodes_col = QVBoxLayout()
        nodes_col.setSpacing(1)
        nodes_col.setContentsMargins(0, 0, 0, 0)
        nodes_col.addWidget(self._make_node_button("LLM", "llm", LLM_COLOR, lambda: self.graph_view.add_llm_node(), tooltip="Add LLM Node"))
        nodes_col.addWidget(self._make_node_button("Chain", "chain_import", CHAIN_IMPORT_COLOR, lambda: self.graph_view.add_chain_import_node(), tooltip="Add Chain Import Node"))
        nodes_col.addWidget(self._make_node_button("Code", "code", CODE_NODE_COLOR, lambda: self.graph_view.add_code_node(), tooltip="Add Code Node"))
        nodes_col.addWidget(self._make_node_button("TTS", "tts", TTS_COLOR, lambda: self.graph_view.add_tts_node(), tooltip="Add TTS Node"))
        nodes_col.addWidget(self._make_node_button("Cond", "conditional", CONDITIONAL_COLOR, lambda: self.graph_view.add_conditional_node(), tooltip="Add Conditional Node"))
        self._top_layout.addLayout(nodes_col)
        content_layout.addWidget(top_cluster)

        content_layout.addSpacing(8)
        try:
            content_layout.setStretch(0, 1)
        except Exception:
            pass

        main_layout.addWidget(self._content_frame)

        # Floating toggle button (modern chevron), parented to graph_view to float outside
        self._toggle_btn_nodes = QToolButton(self.graph_view)
        self._toggle_btn_nodes.setToolTip("Toggle Nodes Toolbar")
        ico = load_tabler_icon('CHEVRON_RIGHT', 14)
        if ico:
            self._toggle_btn_nodes.setIcon(ico)
            self._toggle_btn_nodes.setIconSize(QSize(14, 14))
        else:
            self._toggle_btn_nodes.setText("❯")
        self._toggle_btn_nodes.setAutoRaise(True)
        self._toggle_btn_nodes.setCursor(Qt.PointingHandCursor)
        self._toggle_btn_nodes.setFixedSize(28, 28)
        self._toggle_btn_nodes.setStyleSheet(
            f"QToolButton {{ background-color: rgba(32,32,32,180); border: 1px solid {LIGHT_GREY}; border-radius: 14px; }}"
            f"QToolButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        # Shadow effect removed to prevent "floating window" artifacts during toggle
        self._toggle_btn_nodes.clicked.connect(self._toggle_nodes_toolbar)
        
        # Initial position
        QTimer.singleShot(0, self._position_nodes_toggle)

    def _toggle_nodes_toolbar(self):
        try:
            self._collapsed = not self._collapsed
            if self._collapsed:
                # Hide content and shrink toolbar to zero (visually)
                self._content_frame.setVisible(False)
                self.setMinimumWidth(0)
                self.setMaximumWidth(0)
                
                ico = load_tabler_icon('CHEVRON_LEFT', 14)
                if ico:
                    self._toggle_btn_nodes.setIcon(ico)
                else:
                    self._toggle_btn_nodes.setText("❮")
            else:
                # Show content and restore width
                self._content_frame.setVisible(True)
                self.setMinimumWidth(160)
                self.setMaximumWidth(160)
                
                ico = load_tabler_icon('CHEVRON_RIGHT', 14)
                if ico:
                    self._toggle_btn_nodes.setIcon(ico)
                else:
                    self._toggle_btn_nodes.setText("❯")
            
            # Re-position vertically and horizontally after layout update
            QTimer.singleShot(0, self._position_nodes_toggle)
            QTimer.singleShot(50, self._position_nodes_toggle)
        except Exception:
            pass

    def refresh_sequences_list(self, path=None):
        """Refresh the list of sequences in the toolbar."""
        try:
            if path:
                logger.debug(f"CollapsibleToolbar: Refreshing sequences list due to change in: {path}")
            else:
                logger.debug("CollapsibleToolbar: Refreshing sequences list")
            
            new_scroll = self._make_sequences_scroll_box()
            if hasattr(self, '_sequences_scroll') and self._sequences_scroll is not None:
                old_scroll = self._sequences_scroll
                try:
                    # Hide it first to avoid ghost window
                    old_scroll.hide()
                    # Remove from layout
                    self._top_layout.removeWidget(old_scroll)
                    old_scroll.setParent(None)
                    old_scroll.deleteLater()
                except Exception:
                    pass
                
                # Insert new scroll at top (index 0)
                self._sequences_scroll = new_scroll
                self._top_layout.insertWidget(0, self._sequences_scroll)
            else:
                self._sequences_scroll = new_scroll
                self._top_layout.insertWidget(0, self._sequences_scroll)
            try:
                self._sequences_scroll.update()
                self._top_layout.update()
                self._content_frame.update()
                self.update()
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Failed to refresh sequences list: {e}")

    def _position_nodes_toggle(self):
        # Position the button relative to the toolbar's position on the screen
        if hasattr(self, '_toggle_btn_nodes') and self._toggle_btn_nodes:
            try:
                # Map our (0,0) to the parent (graph_view)
                # If we are collapsed (width 0), mapTo parent might give the position where we would be?
                # Actually, mapTo(parent, (0,0)) gives the top-left corner in parent coords.
                
                # If collapsed (width 0), we want the button to be at the right edge of the graph view?
                # No, the toolbar is on the right. So (0,0) of the toolbar is the start of the toolbar.
                # If collapsed, width is 0, so it's at the far right.
                
                pos_in_parent = self.mapTo(self.graph_view, QPoint(0, 0))
                
                # We want the button to be to the LEFT of the toolbar.
                # Button width is 28.
                # Overlap slightly? 
                # Let's say we want it exactly adjacent to the left edge of the toolbar.
                x = pos_in_parent.x() - 28 + 2 # +2 overlap
                
                # Vertical center relative to toolbar
                y = pos_in_parent.y() + (self.height() // 2) - 14
                
                # Constrain y to be within graph view
                if y < 0: y = 0
                
                self._toggle_btn_nodes.move(x, y)
                self._toggle_btn_nodes.raise_()
            except Exception:
                pass

    def _make_sequences_scroll_box(self):
        scroll = QScrollArea(self)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        try:
            scroll.setWidgetResizable(True)
        except Exception:
            pass
        try:
            # Allow vertical scrollbar, hide horizontal
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        except Exception:
            pass
        

        container = QFrame(scroll)
        layout = QVBoxLayout(container)
        # Add right margin (12px) to make space for scrollbar without overlap
        layout.setContentsMargins(4, 4, 12, 4)
        layout.setSpacing(6)
        folder = getattr(self.graph_view, 'sequences_folder', None)
        files = []
        try:
            if folder and os.path.isdir(folder):
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])
                nested = os.path.join(folder, 'sequences')
                if os.path.isdir(nested):
                    extra = [f for f in os.listdir(nested) if f.lower().endswith('.json')]
                    files = sorted(set(files + extra))
        except Exception:
            files = []
        if not files:
            placeholder = QLabel("No sequences")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(
                f"""
                QLabel {{
                    background-color: {MEDIUM_GREY};
                    color: {TEXT_COLOR};
                    border: 1px solid {LIGHT_GREY};
                    border-radius: 10px;
                    min-height: 40px;
                }}
                """
            )
            layout.addWidget(placeholder)
            scroll.setWidget(container)
            return scroll
        for name in files:
            p1 = os.path.join(folder, name)
            p2 = os.path.join(folder, 'sequences', name)
            full_path = p1 if os.path.exists(p1) else p2
            btn = DraggableSequenceButton(name, full_path, self.graph_view, self)
            layout.addWidget(btn)
        scroll.setWidget(container)
        return scroll

    def _make_node_button(self, label, node_type, color, click_handler, tooltip=None):
        btn = DraggableButton(label, node_type, color, click_handler, self)
        icon = get_node_icon(node_type, 26)
        try:
            btn.setIcon(icon)
            btn.setIconSize(QSize(26, 26))
        except Exception:
            pass
        if tooltip:
            btn.setToolTip(tooltip)
        return btn

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._position_nodes_toggle)
        except Exception:
            pass
