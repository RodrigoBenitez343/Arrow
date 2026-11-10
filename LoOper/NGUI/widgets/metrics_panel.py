"""Live charts of ``AI/engine_metrics.py`` inside the Logs dialog.

The recorder appends one JSON line per sample (engine load, forward, embed,
form field, context trim, index purge, ...) to ``engine_metrics.jsonl``.  This
panel tails that file and draws the same numbers as the HTML report, but live:
one small plot per recorded kind, folded into a scroll column so a kind the
engines start recording tomorrow appears with no code change here.

Clicking a point emits the SAMPLE TIME (not the index), which is what makes the
charts a log tool: the Logs dialog jumps to the record written at that moment,
so "this field took 5.8 s" and "what was the app doing" are one click apart.

The plots are drawn with QPainter on purpose: pyqtgraph is not a dependency of
this build, and a few polylines cost less than adding one.  Nothing here
asserts the app is running: with no file the panel says so, and with
``LOOPER_ENGINE_METRICS=0`` the recorder never writes.
"""

import json
import os
import time
from math import isfinite

from PyQt5.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from ..constants import (
    ACCENT_COLOR, DANGER_COLOR, LIGHT_GREY, TEXT_MUTED, TEXT_SECONDARY, WELL_BG,
)

#: One line colour per numeric field of a kind.
_PALETTE = (ACCENT_COLOR, "#f0b429", "#a78bfa", DANGER_COLOR, "#34d399",
            "#60a5fa", "#f472b6")

#: How many samples the panel keeps (the file is unbounded; the chart is not).
_MAX_ROWS = 4000

_REFRESH_MS = 1000

#: Plot margins: the left one holds the y labels, the top one the title.
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 46, 8, 26, 16
_PLOT_H = 104


def _read_between(path, offset):
    """Lines appended after *offset*; returns ``(lines, new_offset)``."""
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except OSError:
        return [], offset
    if not chunk:
        return [], offset
    text = chunk.decode("utf-8", errors="replace")
    complete = text.split("\n")
    # A sample is written in one write() call, but the tail of the read can
    # still be a line in progress: keep it for the next pass.
    trailing = complete.pop()
    lines = [line for line in complete if line.strip()]
    return lines, offset + len(chunk) - len(trailing.encode("utf-8"))


def parse_lines(lines):
    """JSON lines -> sample dicts (a corrupt line is skipped, never raised)."""
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind"):
            rows.append(row)
    return rows


#: Unit tokens a field name can end with (``load_ms`` -> ms,
#: ``kv_bytes`` -> bytes).  Fields that share a token share an axis.
_UNITS = ("ms", "bytes", "mb", "chars", "count", "turns", "forwards",
          "kept", "held", "action", "ctx", "rss", "code", "shift",
          "reuse", "keep", "embedding", "dropped", "blocks", "kb")


def _unit(name):
    """The unit a numeric field is measured in (its last known token)."""
    for token in reversed(str(name).split("_")):
        if token in _UNITS:
            return token
    return str(name)


def numeric_fields(rows):
    """``[(title, kind, [field, ...]), ...]`` -- numbers only, per unit.

    Two rules keep the column readable:

    * a field that never changes for its kind is dropped (``engine_code`` is
      the same number on every sample: a flat line that only steals height),
    * fields are grouped by UNIT, so milliseconds never share an axis with
      bytes -- ``kv_bytes`` at 50M against ``ms`` at 3000 is a chart where the
      latency is a flat line on the floor.  A kind whose fields all share one
      unit gets one plot; otherwise one plot per unit (``engine_load · bytes``
      next to ``engine_load``).  A kind with a single sample keeps its fields.
    """
    order = []
    fields = {}
    seen = {}
    counts = {}
    for row in rows:
        kind = str(row.get("kind"))
        if kind not in fields:
            fields[kind] = []
            order.append(kind)
            counts[kind] = 0
        counts[kind] += 1
        for key, value in row.items():
            if key in ("kind", "t") or not isinstance(value, (int, float)):
                continue
            if key not in fields[kind]:
                fields[kind].append(key)
                seen[(kind, key)] = set()
            seen[(kind, key)].add(float(value))

    groups = []
    for kind in order:
        keep = [field for field in fields[kind]
                if counts[kind] < 2 or len(seen[(kind, field)]) > 1]
        if not keep:
            continue
        by_unit = {}
        for field in keep:
            by_unit.setdefault(_unit(field), []).append(field)
        for unit, members in by_unit.items():
            title = kind if len(by_unit) == 1 else "%s \u00b7 %s" % (kind, unit)
            groups.append((title, kind, members))
    return groups


class KindPlot(QWidget):
    """One kind: a polyline per numeric field, click -> sample index."""

    #: Sample index under the click.
    clicked = pyqtSignal(int)

    def __init__(self, kind, fields, title="", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.title = title or kind
        self._fields = list(fields)
        self._times = []
        self._series = {}
        self._selected = None
        self.setMinimumHeight(_PLOT_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("%s: click a point to jump to the log line written "
                        "then" % self.title)

    # ---------------------------------------------------------------- data
    def set_data(self, times, series, selected=None):
        self._times = list(times)
        self._series = dict(series)
        self._selected = selected
        self.update()

    def count(self):
        return len(self._times)

    # ------------------------------------------------------------ geometry
    def _rect(self):
        return QRectF(_PAD_L, _PAD_T,
                      max(1.0, self.width() - _PAD_L - _PAD_R),
                      max(1.0, self.height() - _PAD_T - _PAD_B))

    def _top(self):
        values = [value for series in self._series.values()
                  for value in series if isfinite(value)]
        top = max(values) if values else 0.0
        return top * 1.12 if top > 0 else 1.0

    def _x_for(self, index):
        rect = self._rect()
        count = self.count()
        if count <= 1:
            return rect.left()
        return rect.left() + rect.width() * (index / float(count - 1))

    def _y_for(self, value, rect, top):
        return rect.bottom() - rect.height() * (value / top)

    def _index_at(self, x):
        """Sample index nearest screen *x* (clamped to the data)."""
        count = self.count()
        if count <= 0:
            return None
        if count == 1:
            return 0
        rect = self._rect()
        ratio = (float(x) - rect.left()) / rect.width() if rect.width() else 0.0
        return max(0, min(count - 1, int(round(ratio * (count - 1)))))

    # ------------------------------------------------------------- drawing
    def paintEvent(self, event):  # noqa: N802 - Qt name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(WELL_BG))
        rect = self._rect()
        top = self._top()

        small = QFont(self.font())
        small.setPointSizeF(7.2)
        painter.setFont(small)

        # Grid + y labels at 0, half and the top.
        painter.setPen(QPen(QColor(LIGHT_GREY), 1))
        for frac in (0.0, 0.5, 1.0):
            y = rect.bottom() - rect.height() * frac
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))
            label = _short(top * frac)
            painter.setPen(QPen(QColor(TEXT_MUTED), 1))
            painter.drawText(QRectF(0, y - 7, _PAD_L - 5, 14),
                             Qt.AlignRight | Qt.AlignVCenter, label)
            painter.setPen(QPen(QColor(LIGHT_GREY), 1))

        # Title + colour legend, so a line is never anonymous.
        painter.setPen(QPen(QColor(TEXT_SECONDARY), 1))
        painter.drawText(QRectF(_PAD_L, 2, 220, 14),
                         Qt.AlignLeft | Qt.AlignVCenter, self.title)
        x = _PAD_L + 210
        for index, field in enumerate(self._fields):
            color = QColor(_PALETTE[index % len(_PALETTE)])
            painter.setPen(QPen(color, 1))
            painter.drawText(QRectF(x, 2, 90, 14),
                             Qt.AlignLeft | Qt.AlignVCenter, field)
            x += min(90, 12 + 6 * len(field))

        # The series.  A missing value is a gap, never a zero.
        for index, field in enumerate(self._fields):
            values = self._series.get(field) or []
            if not values:
                continue
            color = QColor(_PALETTE[index % len(_PALETTE)])
            painter.setPen(QPen(color, 1.6))
            previous = None
            for position, value in enumerate(values):
                if not (isinstance(value, (int, float)) and isfinite(value)):
                    previous = None
                    continue
                point_x = self._x_for(position)
                point_y = self._y_for(float(value), rect, top)
                if previous is not None:
                    painter.drawLine(int(previous[0]), int(previous[1]),
                                     int(point_x), int(point_y))
                else:
                    painter.drawPoint(int(point_x), int(point_y))
                previous = (point_x, point_y)

        # The picked sample: a dashed line, so the log jump is visible here too.
        if self._selected is not None and 0 <= self._selected < self.count():
            picked = self._x_for(self._selected)
            dashed = QPen(QColor(TEXT_MUTED), 1, Qt.DashLine)
            painter.setPen(dashed)
            painter.drawLine(int(picked), int(rect.top()),
                             int(picked), int(rect.bottom()))

        painter.setPen(QPen(QColor(TEXT_MUTED), 1))
        painter.drawText(QRectF(rect.right() - 140, rect.bottom() + 1, 140, 14),
                         Qt.AlignRight | Qt.AlignVCenter,
                         "%d sample%s" % (self.count(),
                                          "" if self.count() == 1 else "s"))
        painter.end()

    # --------------------------------------------------------------- input
    def mousePressEvent(self, event):  # noqa: N802 - Qt name
        if event.button() != Qt.LeftButton:
            return
        index = self._index_at(event.pos().x())
        if index is None:
            return
        self._selected = index
        self.update()
        self.clicked.emit(index)


def _short(value):
    """A y-axis label: no decimals above ten, one below."""
    value = float(value)
    if abs(value) >= 1000:
        return "%.0f" % value
    if abs(value) >= 10:
        return "%.0f" % value
    return "%.1f" % value if value else "0"


class MetricsPanel(QWidget):
    """Scrollable column of live plots, one per recorded kind."""

    #: ``(epoch_seconds, kind)`` of the clicked sample.
    sample_clicked = pyqtSignal(float, str)

    def __init__(self, parent=None, path=""):
        super().__init__(parent)
        self._path = path
        self._offset = 0
        self._rows = []
        self._plots = {}
        self._signature = ()
        self._selected = {}
        self._status_text = ""
        self._click_text = ""
        self._setup_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._hint = QLabel("")
        self._hint.setStyleSheet("color: %s; font-size: 10px;" % TEXT_MUTED)
        self._hint.setWordWrap(True)
        layout.addWidget(self._hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }")
        self._host = QWidget()
        self._column = QVBoxLayout(self._host)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(4)
        self._column.addStretch(1)
        self._scroll.setWidget(self._host)
        layout.addWidget(self._scroll, 1)

    # --------------------------------------------------------------- source
    def source_path(self):
        """The JSONL being tailed (resolved lazily, so imports stay cheap)."""
        if self._path:
            return self._path
        try:
            from AI import engine_metrics
            self._path = engine_metrics.jsonl_path()
        except Exception:  # noqa: BLE001 - the panel is optional
            self._path = ""
        return self._path

    def rows(self):
        """Every sample read so far, oldest first."""
        return list(self._rows)

    def kinds(self):
        """The kinds charted right now (one kind can own two plots)."""
        return sorted({entry["kind"] for entry in self._plots.values()})

    def titles(self):
        """The plot titles in column order."""
        return list(self._plots)

    # ---------------------------------------------------------------- live
    def set_live(self, on):
        """Start / stop the tail timer (a hidden dialog stops it)."""
        if on:
            self.refresh()
            self._timer.start()
        else:
            self._timer.stop()

    def live(self):
        return self._timer.isActive()

    def _tick(self):
        if not self.isVisible():
            return
        self.refresh()

    def refresh(self):
        """Read what the recorder appended; redraw when something arrived.

        Returns the number of new samples, which is what the dialog's status
        line reports and what the tests assert on.
        """
        path = self.source_path()
        if not path or not os.path.isfile(path):
            self._set_hint("No metrics recorded yet: %s does not exist.  Run a "
                           "chain, or a benchmark." %
                           (path or "engine_metrics.jsonl"))
            return 0
        try:
            size = os.path.getsize(path)
        except OSError:
            return 0
        if size < self._offset:      # the file was rotated or truncated
            self.reset()
        lines, self._offset = _read_between(path, self._offset)
        fresh = parse_lines(lines)
        if not fresh:
            return 0
        self._rows.extend(fresh)
        if len(self._rows) > _MAX_ROWS:
            self._rows = self._rows[-_MAX_ROWS:]
        self._draw()
        return len(fresh)

    def reset(self):
        """Forget what was read (used when the log rotates)."""
        self._offset = 0
        self._rows = []
        self._signature = ()
        self._selected = {}

    # --------------------------------------------------------------- charts
    def _structure(self):
        return tuple((title, kind, tuple(fields))
                     for title, kind, fields in numeric_fields(self._rows))

    def _draw(self):
        structure = self._structure()
        if structure != self._signature:
            self._build(structure)
            self._signature = structure
        self._fill()
        self._set_hint("%d samples from %s%s" % (
            len(self._rows), os.path.basename(self.source_path()),
            "" if self.live() else " (paused)"))

    def _build(self, structure):
        while self._column.count() > 1:          # keep the trailing stretch
            item = self._column.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._plots = {}
        for title, kind, fields in structure:
            plot = KindPlot(kind, fields, title, self._host)
            plot.clicked.connect(
                lambda index, k=kind: self._on_pick(k, index))
            self._plots[title] = {"plot": plot, "kind": kind,
                                  "fields": list(fields)}
            self._column.insertWidget(self._column.count() - 1, plot)

    def _fill(self):
        grouped = {}
        for row in self._rows:
            grouped.setdefault(str(row.get("kind")), []).append(row)
        for entry in self._plots.values():
            rows = grouped.get(entry["kind"]) or []
            times = [float(r.get("t") or 0.0) for r in rows]
            series = {}
            for field in entry["fields"]:
                series[field] = [
                    float(r[field]) if isinstance(r.get(field), (int, float))
                    else float("nan") for r in rows]
            entry["plot"].set_data(times, series,
                                   self._selected.get(entry["kind"]))

    # --------------------------------------------------------------- clicks
    def _on_pick(self, kind, index):
        times = [float(r.get("t") or 0.0)
                 for r in self._rows if str(r.get("kind")) == kind]
        if not (0 <= index < len(times)):
            return
        self._selected[kind] = index
        moment = time.strftime("%H:%M:%S", time.localtime(times[index]))
        self._click_text = "%s sample %d/%d at %s" % (
            kind, index + 1, len(times), moment)
        self._refresh_hint()
        self.sample_clicked.emit(float(times[index]), kind)

    def select(self, kind, index):
        """Mark a sample without emitting (used by the tests and callers)."""
        self._selected[kind] = index
        for entry in self._plots.values():
            if entry["kind"] == kind:
                entry["plot"]._selected = index
                entry["plot"].update()

    def selected(self, kind):
        return self._selected.get(kind)

    # ---------------------------------------------------------------- hint
    def _set_hint(self, text):
        """The always-on line ("N samples from ...")."""
        self._status_text = text
        self._refresh_hint()

    def _refresh_hint(self):
        """The clicked sample wins over the status line (a timer redraw must
        not erase what the user just picked)."""
        self._hint.setText(self._click_text or self._status_text)
