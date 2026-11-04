"""Procedurally generated vector avatars for System chains (coworkers).

Each chain id hashes to a deterministic identicon-like SVG face: a mirrored
pattern plus two "eyes" on a hashed row, on a dark disk tinted with the chain's
own hue.  Same chain -> same avatar everywhere (coworker list, overlay header,
notch); different chains are distinguishable even at the notch's 26 px slot.
Pure stdlib hashing + QtSvg vector rendering — no image files, no new deps.
"""

import hashlib

from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPixmap, QBrush

_GRID = 6  # half-width of the mirrored cell pattern (visually 6 x 6)


def _bits(seed: str) -> int:
    try:
        return int(hashlib.md5(str(seed).encode("utf-8")).hexdigest(), 16)
    except Exception:
        return sum(ord(ch) for ch in str(seed))


def _hex(color) -> str:
    return QColor(color).name()


def _pattern(seed: str, fg: QColor) -> tuple:
    """(cells, eyes) SVG bodies for the identicon-like face, drawn in *fg*."""
    h = _bits(seed)
    f = _hex(fg)
    cells, r = [], h >> 8
    for row in range(_GRID):
        for col in range(_GRID):
            if r & 1:
                x0, y0 = col * 2, row * 2
                cells.append(
                    f'<rect x="{x0}" y="{y0}" width="2" height="2" fill="{f}"/>'
                    f'<rect x="{12 - x0 - 2}" y="{y0}" width="2" height="2" fill="{f}"/>'
                )
            r >>= 1
    # Eyes: symmetric pair on a hashed row, always drawn so every avatar reads
    # as a face even when the random pattern is sparse.
    eye_cy = (1 + (h >> 60) % (_GRID - 2)) * 2 + 1
    eyes = (
        f'<circle cx="4.4" cy="{eye_cy}" r="0.85" fill="{f}"/>'
        f'<circle cx="9.6" cy="{eye_cy}" r="0.85" fill="{f}"/>'
    )
    return "".join(cells), eyes


def _svg(seed: str, color=None) -> str:
    """Full avatar SVG string; *color* tints the pattern, else the chain's hue."""
    h = _bits(seed)
    accent = QColor.fromHsv(h % 360, 190, 235)
    fg = QColor(color) if color else accent.lighter(150)
    cells, eyes = _pattern(seed, fg)
    disc = f'<circle cx="7" cy="7" r="7" fill="{_hex(accent.darker(300))}"/>'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 14 14">'
        f'{disc}<g opacity="0.9">{cells}</g>{eyes}</svg>'
    )


def chain_avatar_svg(seed: str) -> str:
    """Standalone SVG string (e.g. for HTML views or export)."""
    return _svg(seed)


def chain_avatar_qicon(seed: str, size: int = 18, color=None) -> QIcon:
    """Render chain *seed*'s procedural avatar as a QIcon.

    *color* tints the pattern (the notch passes its accent so the avatar still
    carries the idle/executing signal); empty/None falls back to the hue baked
    into the chain id.
    """
    svg = _svg(seed, color)
    h = _bits(seed)
    accent = QColor.fromHsv(h % 360, 190, 235)
    fg = QColor(color) if color else accent.lighter(150)
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    try:
        from PyQt5.QtSvg import QSvgRenderer
        painter = QPainter(pm)
        try:
            QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
        finally:
            painter.end()
    except Exception:
        # No QtSvg in the build: fall back to a raster-drawn avatar so chains
        # stay distinguishable rather than showing a blank slot.
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(accent.darker(300)))
        painter.drawEllipse(0, 0, size, size)
        painter.setBrush(QBrush(fg))
        cell = max(1, size // _GRID)
        for row in range(_GRID):
            for col in range(_GRID):
                if (h >> (8 + row * _GRID + col)) & 1:
                    x = col * cell
                    painter.drawRect(x, row * cell, cell, cell)
                    painter.drawRect(size - x - cell, row * cell, cell, cell)
        painter.end()
    return QIcon(pm)
