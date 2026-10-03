"""Tabler outline icons rendered to QIcon, working under PyQt5.

pytablericons returns a PIL image, and PIL's ImageQt only supports the Qt6
bindings, so under PyQt5 ``Image.toqpixmap()`` raises "Qt bindings are not
installed". Fall back to a manual PIL -> QImage conversion so icons actually
render (otherwise icon-only buttons come out blank).
"""
import logging

from PyQt5.QtGui import QIcon, QImage, QPixmap

from .constants import TEXT_COLOR

logger = logging.getLogger(__name__)


def tabler_qicon(name, size=16, color=None):
    """Load the Tabler outline icon *name* (e.g. "REFRESH") as a QIcon."""
    if color is None:
        color = TEXT_COLOR
    try:
        from pytablericons import TablerIcons, OutlineIcon
        enum = getattr(OutlineIcon, name, None)
        if enum is None:
            return QIcon()
        img = TablerIcons.load(enum, size=int(size), color=color)
        try:
            return QIcon(img.toqpixmap())
        except Exception:
            rgba = img.convert("RGBA")
            data = rgba.tobytes("raw", "RGBA")
            qimg = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888)
            return QIcon(QPixmap.fromImage(qimg))
    except Exception:
        logger.debug("tabler_qicon(%s) failed", name, exc_info=True)
        return QIcon()
