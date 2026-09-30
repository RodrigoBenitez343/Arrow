import logging
import os
import json
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QLabel, QApplication, QStyle, QScrollArea,
    QGraphicsDropShadowEffect, QMessageBox, QDialog
)
from PyQt5.QtCore import Qt, QPoint, QSize, QFileSystemWatcher, QTimer, QPointF, QRectF, pyqtSignal, QPropertyAnimation, QEasingCurve, QAbstractAnimation
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
    LLM_COLOR, CONDITIONAL_COLOR, CHAIN_IMPORT_COLOR, FORM_FILLER_COLOR,
    CODE_NODE_COLOR, CONTEXT_NODE_COLOR, INPUT_NODE_COLOR, HANDLE_NODE_COLOR, MCP_NODE_COLOR, OUTPUT_NODE_COLOR, WEB_SEQUENCE_COLOR, SIDE_PANEL_BG, GRAPH_PLANE
)
from ..dialogs.base_dialog import ModernDialog
from ..i18n import _


logger = logging.getLogger(__name__)


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


def make_toggle_triangle_icon(direction: str, size: int, color: str) -> QIcon:
    """Create a solid filled triangle icon for toolbar toggle buttons."""
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
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    s = float(size) * dpr
    if direction == 'LEFT':
        points = [QPointF(s * 0.8, s * 0.1), QPointF(s * 0.2, s * 0.5), QPointF(s * 0.8, s * 0.9)]
    elif direction == 'RIGHT':
        points = [QPointF(s * 0.2, s * 0.1), QPointF(s * 0.8, s * 0.5), QPointF(s * 0.2, s * 0.9)]
    elif direction == 'UP':
        points = [QPointF(s * 0.1, s * 0.8), QPointF(s * 0.5, s * 0.2), QPointF(s * 0.9, s * 0.8)]
    else:  # DOWN
        points = [QPointF(s * 0.1, s * 0.2), QPointF(s * 0.5, s * 0.8), QPointF(s * 0.9, s * 0.2)]
    p.drawPolygon(QPolygonF(points))
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
    elif node_type == 'chain_import':
        p.drawRoundedRect(ox + int(0.18*s), oy + int(0.18*s), int(0.64*s), int(0.64*s), 4, 4)
        p.drawRoundedRect(ox + int(0.26*s), oy + int(0.26*s), int(0.48*s), int(0.48*s), 4, 4)
    elif node_type == 'web_sequence':
        # Globe: circle + horizontal meridian + vertical line (browser/world)
        p.drawEllipse(ox + int(0.12*s), oy + int(0.12*s), int(0.76*s), int(0.76*s))
        p.drawEllipse(ox + int(0.30*s), oy + int(0.12*s), int(0.40*s), int(0.76*s))
        p.drawLine(ox + int(0.12*s), oy + int(0.5*s), ox + int(0.88*s), oy + int(0.5*s))
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

def _painter_icon_context(size: int) -> QIcon:
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

    # Draw a database/cylinder icon (stacked layers)
    # Top ellipse
    top_ry = s * 0.12
    p.drawArc(QRectF(cx - s*0.25, cy - s*0.28, s*0.50, top_ry*2), 0, 360*16)
    # Bottom ellipse
    p.drawArc(QRectF(cx - s*0.25, cy + s*0.04, s*0.50, top_ry*2), 0, 360*16)
    # Side lines
    p.drawLine(QPointF(cx - s*0.25, cy - s*0.16), QPointF(cx - s*0.25, cy + s*0.16))
    p.drawLine(QPointF(cx + s*0.25, cy - s*0.16), QPointF(cx + s*0.25, cy + s*0.16))
    # Middle line
    p.drawLine(QPointF(cx - s*0.25, cy - s*0.06), QPointF(cx + s*0.25, cy - s*0.06))

    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

def _painter_icon_input(size: int) -> QIcon:
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

    # Draw a text-input icon: rounded rect with cursor and right-arrow
    box_w = s * 0.55
    box_h = s * 0.44
    box_x = cx - box_w * 0.5 + s * 0.08
    box_y = cy - box_h * 0.5
    p.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), 4, 4)
    # Vertical cursor line inside the box
    cursor_x = box_x + s * 0.12
    p.drawLine(QPointF(cursor_x, box_y + s * 0.12), QPointF(cursor_x, box_y + box_h - s * 0.12))
    # Right-pointing arrow entering from the left
    arrow_tip_x = box_x - s * 0.02
    arrow_start_x = box_x - s * 0.26
    arrow_y = cy
    p.drawLine(QPointF(arrow_start_x, arrow_y), QPointF(arrow_tip_x, arrow_y))
    # Arrowhead
    p.drawLine(QPointF(arrow_tip_x, arrow_y), QPointF(arrow_tip_x - s * 0.08, arrow_y - s * 0.06))
    p.drawLine(QPointF(arrow_tip_x, arrow_y), QPointF(arrow_tip_x - s * 0.08, arrow_y + s * 0.06))

    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)


def _painter_icon_output(size: int) -> QIcon:
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

    # Mirrored horizontally from _painter_icon_input:
    # box shifted left (mirror of input's right shift)
    box_w = s * 0.55
    box_h = s * 0.44
    box_x = cx - box_w * 0.5 - s * 0.08
    box_y = cy - box_h * 0.5
    p.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), 4, 4)
    # Vertical cursor line on the right side of the box (mirrored)
    cursor_x = box_x + box_w - s * 0.12
    p.drawLine(QPointF(cursor_x, box_y + s * 0.12), QPointF(cursor_x, box_y + box_h - s * 0.12))
    # Right-pointing arrow exiting from the right side
    arrow_tip_x = box_x + box_w + s * 0.02
    arrow_end_x = box_x + box_w + s * 0.26
    arrow_y = cy
    p.drawLine(QPointF(arrow_tip_x, arrow_y), QPointF(arrow_end_x, arrow_y))
    # Arrowhead
    p.drawLine(QPointF(arrow_tip_x, arrow_y), QPointF(arrow_tip_x + s * 0.08, arrow_y - s * 0.06))
    p.drawLine(QPointF(arrow_tip_x, arrow_y), QPointF(arrow_tip_x + s * 0.08, arrow_y + s * 0.06))

    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)


def _painter_icon_folder(size: int) -> QIcon:
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
    cx = s * 0.5
    cy = s * 0.5
    # Folder tab at top-left
    tab_w = s * 0.25
    tab_h = s * 0.12
    tab_x = cx - s * 0.35
    tab_y = cy - s * 0.30
    p.drawRect(QRectF(tab_x, tab_y, tab_w, tab_h))
    # Folder body
    body_y = tab_y + tab_h
    body_h = s * 0.48 - tab_h
    p.drawRoundedRect(QRectF(cx - s*0.38, body_y, s*0.76, body_h), 3, 3)
    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)


def _painter_icon_handle(size: int) -> QIcon:
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

    # Draw a simplified pointing hand / cursor icon
    # Palm (rounded rect)
    palm_w = s * 0.36
    palm_h = s * 0.40
    palm_x = cx - palm_w * 0.5 + s * 0.04
    palm_y = cy - palm_h * 0.2
    path = QPainterPath()
    path.addRoundedRect(QRectF(palm_x, palm_y, palm_w, palm_h), 4, 4)
    p.drawPath(path)

    # Index finger (pointing up-left)
    finger_w = s * 0.14
    finger_h = s * 0.32
    finger_x = palm_x + s * 0.02
    finger_y = palm_y - finger_h + s * 0.08
    path2 = QPainterPath()
    path2.addRoundedRect(QRectF(finger_x, finger_y, finger_w, finger_h), 3, 3)
    p.drawPath(path2)

    # Thumb (pointing left)
    thumb_w = s * 0.22
    thumb_h = s * 0.12
    thumb_x = palm_x - thumb_w + s * 0.04
    thumb_y = palm_y + s * 0.12
    path3 = QPainterPath()
    path3.addRoundedRect(QRectF(thumb_x, thumb_y, thumb_w, thumb_h), 3, 3)
    p.drawPath(path3)

    # Click ripple (concentric small circle at fingertip)
    tip_cx = finger_x + finger_w * 0.5
    tip_cy = finger_y
    p.drawEllipse(QPointF(tip_cx, tip_cy), s * 0.06, s * 0.06)

    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)


def _painter_icon_mcp(size: int) -> QIcon:
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

    # Plug/connector icon representing MCP's connection nature
    # Plug body (rounded rectangle)
    body_w = s * 0.42
    body_h = s * 0.38
    body_x = cx - body_w * 0.5 + s * 0.02
    body_y = cy - body_h * 0.5
    p.drawRoundedRect(QRectF(body_x, body_y, body_w, body_h), 3, 3)

    # Prongs (two small rects on the right)
    prong_w = s * 0.10
    prong_h = s * 0.14
    prong_gap = s * 0.06
    prong_x = body_x + body_w
    prong1_y = cy - prong_h - prong_gap * 0.5
    prong2_y = cy + prong_gap * 0.5
    p.drawRect(QRectF(prong_x, prong1_y, prong_w, prong_h))
    p.drawRect(QRectF(prong_x, prong2_y, prong_w, prong_h))

    # Cable (line on the left)
    cable_start = body_x
    p.drawLine(QPointF(cable_start, cy), QPointF(cable_start - s * 0.18, cy))

    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)


def _painter_icon_form_filler(size: int) -> QIcon:
    """Form-filling glyph: a document with labelled input rows."""
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

    # Outer document rectangle
    rect_w = s * 0.62
    rect_h = s * 0.76
    x = s * 0.19
    y = s * 0.12
    p.drawRoundedRect(QRectF(x, y, rect_w, rect_h), s * 0.06, s * 0.06)

    # Field rows: a short label line + a small input box, top to bottom
    top = y + rect_h * 0.20
    gap = rect_h * 0.24
    for i in range(3):
        ry = top + i * gap
        p.drawLine(QPointF(x + rect_w * 0.10, ry), QPointF(x + rect_w * 0.40, ry))
        p.drawRect(QRectF(x + rect_w * 0.48, ry - s * 0.05, rect_w * 0.40, s * 0.10))

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
    if TablerIcons and OutlineIcon:
        try:
            icon_map = {
                'conditional': ['HELP_CIRCLE'],
                'llm': ['ROBOT'],
                'chain_import': ['BOX_MULTIPLE'],
                'code': ['CODE', 'TERMINAL_2', 'TERMINAL'],
                'context': ['DATABASE'],
                'input': ['CIRCLE_DOT', 'POINTER', 'ARROW_BACK_UP', 'INPUT'],
                'handle': ['HAND_CLICK', 'POINTER', 'CURSOR_OFF', 'HAND_STOP'],
                'form_filler': ['CLIPBOARD_TEXT', 'LIST_DETAILS', 'FORMS', 'CHECKBOX'],
                'mcp': ['PLUG_CONNECTED', 'API_APP', 'PLUG'],
                'output': ['ARROW_BACK', 'ARROW_BACK_UP', 'CIRCLE_DOT', 'INPUT'],
                'web_sequence': ['GLOBE', 'WORLD', 'BROWSER', 'WORLD_WWW'],
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
            'chain_import': _painter_icon_chain,
            'code': _painter_icon_code,
            'context': _painter_icon_context,
            'input': _painter_icon_input,
            'handle': _painter_icon_handle,
            'form_filler': _painter_icon_form_filler,
            'mcp': _painter_icon_mcp,
            'output': _painter_icon_output,
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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(30)
        # Colored background to reflect node type
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg_color};
                color: {TEXT_COLOR};
                border: 1px solid #14FFFFFF;
                border-top: 1px solid #33FFFFFF;
                padding: 4px 10px;
                border-radius: 12px;
                min-width: 96px;
                min-height: 30px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {bg_color};
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: #1FFFFFFF;
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
        super().__init__(parent, title=_("Delete"))
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
        
        title = QLabel(_("Delete '{filename}'?").format(filename=filename))
        title.setWordWrap(True)
        title.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold; font-size: 16px;")
        
        desc = QLabel(_("This action cannot be undone."))
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
        
        cancel_btn = QPushButton(_("Cancel"))
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
        
        delete_btn = QPushButton(_("Delete"))
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
    """A drop target for deleting saved files and shared library nodes."""

    # Drag mime types accepted by the bin; these mirror the formats set by the
    # sequence/chain/web-sequence library cards when a drag starts.
    _DROP_FORMATS = (
        'application/x-looper-sequence-file',
        'application/x-looper-chain-file',
        'application/x-looper-web-session-file',
    )

    # LLM/Code/Context library cards drag a JSON config referencing a node
    # entry inside a chain file; those drops are delegated to the registered
    # node drop handler instead of a plain file removal.
    _NODE_CONFIG_FORMATS = (
        'application/x-looper-llm-config',
        'application/x-looper-context-config',
        'application/x-looper-code-config',
    )
    _NODE_CONFIG_KIND = {
        'application/x-looper-llm-config': 'llm',
        'application/x-looper-context-config': 'context',
        'application/x-looper-code-config': 'code',
    }
    _NODE_CONFIG_KEY = {
        'application/x-looper-llm-config': 'llm_node_data',
        'application/x-looper-context-config': 'context_node_data',
        'application/x-looper-code-config': 'code_node_data',
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setText(_("🗑 Drop to Delete"))
        self.setToolTip(_("Drag sequence/chain/web session files here to delete them"))
        # Optional callback used for LLM/Code/Context library node drops.
        self._node_drop_handler = None
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
                min-height: 36px;
            }}
            QPushButton:hover {{
                border-color: #FF6B6B;
                background-color: rgba(255, 107, 107, 0.1);
                color: #FF6B6B;
            }}
            """
        )

    def set_node_drop_handler(self, handler):
        """Register a handler for deleting shared library nodes."""
        self._node_drop_handler = handler

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if any(mime.hasFormat(fmt) for fmt in self._DROP_FORMATS) or any(
            mime.hasFormat(fmt) for fmt in self._NODE_CONFIG_FORMATS
        ):
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
                    min-height: 36px;
                }}
                """
            )
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 2px dashed {LIGHT_GREY};
                border-radius: 10px;
                padding: 8px;
                font-weight: bold;
                min-height: 36px;
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
        mime = event.mimeData()
        cfg_format = next(
            (fmt for fmt in self._NODE_CONFIG_FORMATS if mime.hasFormat(fmt)), None
        )
        if cfg_format:
            if self._node_drop_handler:
                try:
                    payload = json.loads(str(mime.data(cfg_format), 'utf-8'))
                    handled = self._node_drop_handler(
                        self._NODE_CONFIG_KIND[cfg_format],
                        payload.get('chain_file', ''),
                        payload.get(self._NODE_CONFIG_KEY[cfg_format], {}) or {},
                    )
                    if handled:
                        event.accept()
                except Exception as e:
                    logger.error(f"Error processing library node drop in trash bin: {e}")
            else:
                event.ignore()
            # Restore style
            self.dragLeaveEvent(None)
            return

        file_format = next(
            (fmt for fmt in self._DROP_FORMATS if mime.hasFormat(fmt)), None
        )
        if file_format:
            try:
                file_path_bytes = mime.data(file_format)
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
                        QMessageBox.critical(self, _("Error"), _("Failed to delete file: {error}").format(error=e))
            except Exception as e:
                logger.error(f"Error processing drop in trash bin: {e}")
            finally:
                # Restore style
                self.dragLeaveEvent(None)
        else:
            event.ignore()


class CollapsibleToolbar(QWidget):
    """Collapsible right toolbar with special node creation buttons."""

    scheduler_requested = pyqtSignal()
    ai_settings_requested = pyqtSignal()
    agent_mode_requested = pyqtSignal()

    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        self._build_ui()

    def _build_ui(self):
        # Start expanded
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.setMinimumWidth(160)
        # No maximum – let the splitter resize us
        main_layout = QVBoxLayout(self)
        # Minimal margins so we can shrink to near-zero
        main_layout.setContentsMargins(0, 0, 4, 0)
        main_layout.setSpacing(0)
        
        # Make the main widget transparent (content frame provides background)
        # This ensures no "black frame" when integrated in layout
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        # Container for content
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("toolbarContent")
        self._content_frame.setStyleSheet(
            f"QFrame#toolbarContent {{ background-color: {SIDE_PANEL_BG}; border-radius: 10px; }}"
        )
        content_layout = QVBoxLayout(self._content_frame)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(6)

        # Node creation buttons – directly in content layout, no scroll area
        for label, ntype, color, handler, tip in [
            (_("LLM"), "llm", LLM_COLOR, lambda: self.graph_view.add_llm_node(), _("Add LLM Node")),
            (_("Chain"), "chain_import", CHAIN_IMPORT_COLOR, lambda: self.graph_view.add_chain_import_node(), _("Add Chain Import Node")),
            (_("Code"), "code", CODE_NODE_COLOR, lambda: self.graph_view.add_code_node(), _("Add Code Node")),
            (_("Input"), "input", INPUT_NODE_COLOR, lambda: self.graph_view.add_input_node(), _("Add Input Node")),
            (_("Handle"), "handle", HANDLE_NODE_COLOR, lambda: self.graph_view.add_handle_node(), _("Add Handle Node")),
            (_("Form"), "form_filler", FORM_FILLER_COLOR, lambda: self.graph_view.add_form_filler_node(), _("Add Form Filling Node")),
            (_("MCP"), "mcp", MCP_NODE_COLOR, lambda: self.graph_view.add_mcp_node(), _("Add MCP Server Node")),
            (_("Output"), "output", OUTPUT_NODE_COLOR, lambda: self.graph_view.add_output_node(), _("Add Output Node")),
            (_("Context"), "context", CONTEXT_NODE_COLOR, lambda: self.graph_view.add_context_node(), _("Add Context Node")),
            (_("Cond"), "conditional", CONDITIONAL_COLOR, lambda: self.graph_view.add_conditional_node(), _("Add Conditional Node")),
            (_("Web"), "web_sequence", WEB_SEQUENCE_COLOR, lambda: self.graph_view.add_web_sequence_node(), _("Add Web Sequence Node")),
        ]:
            btn = self._make_node_button(label, ntype, color, handler, tooltip=tip)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            btn.setMinimumHeight(30)
            content_layout.addWidget(btn, 1)

        # Bottom action buttons in a vertical column
        bottom_row = QVBoxLayout()
        bottom_row.setSpacing(6)
        bottom_row.setContentsMargins(0, 0, 0, 0)

        btn_specs = [
            (_("Scheduler"), OutlineIcon.CALENDAR, self.scheduler_requested),
            (_("Agent Mode"), OutlineIcon.USER, self.agent_mode_requested),
            (_("AI Settings"), OutlineIcon.SETTINGS, self.ai_settings_requested),
        ]
        for label, icon_enum, signal in btn_specs:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            try:
                if TablerIcons and icon_enum is not None:
                    ico = TablerIcons.load(icon_enum, size=16, color='#00E0B8').toqpixmap()
                    btn.setIcon(QIcon(ico))
                    btn.setIconSize(QSize(16, 16))
            except Exception:
                pass
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: {BLOCK_COLOR};
                    color: {TEXT_COLOR};
                    border: 1px solid #1AFFFFFF;
                    border-radius: 8px;
                    font-weight: bold;
                    font-size: 11px;
                    padding: 2px 4px;
                }}
                QPushButton:hover {{
                    background-color: {BLOCK_HOVER};
                    border: 1px solid {ACCENT_COLOR};
                }}
                QPushButton:pressed {{
                    background-color: {ACCENT_COLOR};
                    color: {DARK_GREY};
                }}
                """
            )
            btn.clicked.connect(signal.emit)
            bottom_row.addWidget(btn)

        content_layout.addLayout(bottom_row)

        main_layout.addWidget(self._content_frame)

        # Floating toggle button (solid triangle), parented to graph_view to float outside
        self._toggle_btn_nodes = QToolButton(self.graph_view)
        self._toggle_btn_nodes.setToolTip(_("Toggle Nodes Toolbar"))
        self._set_triangle_icon('RIGHT', SIDE_PANEL_BG)
        self._toggle_btn_nodes.setAutoRaise(True)
        self._toggle_btn_nodes.setCursor(Qt.PointingHandCursor)
        self._toggle_btn_nodes.setFixedSize(24, 28)
        self._toggle_btn_nodes.setStyleSheet(
            f"QToolButton {{ background-color: transparent; border: none; }}"
            f"QToolButton:hover {{ background-color: #0FFFFFFF; border-radius: 4px; }}"
        )
        self._toggle_btn_nodes.clicked.connect(self._toggle_nodes_toolbar)
        
        QTimer.singleShot(0, self._position_nodes_toggle)

    def _set_triangle_icon(self, direction, color):
        ico = make_toggle_triangle_icon(direction, 14, color)
        self._toggle_btn_nodes.setIcon(ico)
        self._toggle_btn_nodes.setIconSize(QSize(14, 14))

    def _toggle_nodes_toolbar(self):
        try:
            self._collapsed = not self._collapsed
            if self._collapsed:
                self.setMinimumWidth(0)
                start_w = max(0, self.width())
                self._anim = QPropertyAnimation(self, b"maximumWidth")
                self._anim.setDuration(200)
                self._anim.setStartValue(start_w)
                self._anim.setEndValue(0)
                self._anim.setEasingCurve(QEasingCurve.InOutQuad)
                self._anim.finished.connect(self._on_collapse_finished)
                self._anim.start(QAbstractAnimation.DeleteWhenStopped)
                self._set_triangle_icon('LEFT', SIDE_PANEL_BG)
            else:
                self._content_frame.setVisible(True)
                self._anim = QPropertyAnimation(self, b"maximumWidth")
                self._anim.setDuration(200)
                self._anim.setStartValue(0)
                self._anim.setEndValue(160)
                self._anim.setEasingCurve(QEasingCurve.InOutQuad)
                self._anim.finished.connect(self._on_expand_finished)
                self._anim.start(QAbstractAnimation.DeleteWhenStopped)
                self._set_triangle_icon('RIGHT', GRAPH_PLANE)

            QTimer.singleShot(0, self._position_nodes_toggle)
            QTimer.singleShot(250, self._position_nodes_toggle)
        except Exception:
            pass

    def _on_collapse_finished(self):
        self._content_frame.setVisible(False)
        self.setMinimumWidth(0)
        QTimer.singleShot(0, self._position_nodes_toggle)

    def _on_expand_finished(self):
        self.setMinimumWidth(160)
        self.setMaximumWidth(16777215)  # clear fixed max so splitter can resize
        QTimer.singleShot(0, self._position_nodes_toggle)


    def refresh_sequences_list(self, path=None):
        """Stub kept for backward compatibility - sequences moved to left sidebar."""
        pass

    def _position_nodes_toggle(self):
        # Position the button relative to the toolbar's position on the screen
        #
        # Stale-object guard: this slot is scheduled via QTimer.singleShot(0
        # and 250ms) and can fire after the widget tree was already destroyed
        # (window closed, test teardown).  Calling into a deleted C++ widget
        # raises a NATIVE access violation (not a catchable RuntimeError), so
        # bail out via sip before touching anything Qt-side.
        try:
            try:
                from PyQt5 import sip as _sip
            except Exception:
                import sip as _sip  # type: ignore
            _gv = getattr(self, 'graph_view', None)
            _btn = getattr(self, '_toggle_btn_nodes', None)
            if _gv is None or _btn is None:
                return
            if _sip.isdeleted(self) or _sip.isdeleted(_gv) or _sip.isdeleted(_btn):
                return
            # A toolbar whose window is already gone (test teardown, closed
            # main window) is not visible — never map coordinates for it:
            # Qt crashes natively mapping to a destroyed native window even
            # when the wrappers are not flagged deleted.
            if not self.isVisible():
                return
        except Exception:
            return
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

    def _make_node_button(self, label, node_type, color, click_handler, tooltip=None):
        btn = DraggableButton(label, node_type, color, click_handler, self)
        icon = get_node_icon(node_type, 22)
        try:
            btn.setIcon(icon)
            btn.setIconSize(QSize(22, 22))
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
