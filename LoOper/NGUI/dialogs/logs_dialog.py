"""Logs browser: browse, search and filter the session Markdown logs.

The app writes one Markdown session log per run (``logs/session_*.md``) where
every record is a bullet, a fenced payload block or a table.  This dialog lists
those files, renders the selected one, and lets you:

* search across records (only matching records are shown), and
* toggle log levels.  Turning off the noisy levels (DEBUG/INFO/...) hides their
  plain bullets while the MEANINGFUL records stay visible: node executions with
  their settings and the LLM / Code / Conditional inputs-outputs (which are
  rendered as ``### title`` + fenced block / table) plus every failure
  (ERROR / CRITICAL).
"""

import os
import re

from PyQt5.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QTextBrowser,
)
from PyQt5.QtCore import Qt

from .base_dialog import ModernDialog
from ..i18n import _
from ..icons import tabler_qicon
from ..widgets.rich_render import md_to_html

_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# A session log can be enormous (a runaway DEBUG run reached 500 MB), so a large
# file is read from its TAIL only and how many records get rendered is capped -
# the browser must never freeze on a big log.
_TAIL_LINES = 40000
_MAX_RENDER_RECORDS = 2000

# A plain record bullet written by logging_setup._MarkdownHandler.
_BULLET_RE = re.compile(
    r"^- `\d{2}:\d{2}:\d{2}\.\d{3}` \*\*\[(\w+)\]\*\* `[^`]+` (.*)$")


def _logs_dir():
    """Where the session Markdown logs live."""
    try:
        from ...logging_setup import resolve_logs_dir
        return resolve_logs_dir()
    except Exception:
        # source layout: LoOper/NGUI/dialogs/logs_dialog.py -> LoOper/logs
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "logs")


def _tail_lines(path, max_lines=_TAIL_LINES):
    """Read a file's last ``max_lines`` lines (bounded memory for huge logs).

    Returns ``(lines, truncated)``.  Small files are read whole.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], False
    block = 1 << 20
    if size <= 4 * block:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read().splitlines(), False
        except OSError:
            return [], False
    data = b""
    try:
        with open(path, "rb") as handle:
            pos = size
            while pos > 0 and data.count(b"\n") <= max_lines:
                read = min(block, pos)
                pos -= read
                handle.seek(pos)
                data = handle.read(read) + data
    except OSError:
        return [], False
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-max_lines:], (pos > 0 or len(lines) > max_lines)


def _parse_records(lines):
    """Split a session log into records (bullets / sections / headings).

    Fence-aware: lines inside a ``` block are never treated as a record
    boundary (an LLM prompt payload can itself contain ``###`` headings), so a
    block is never split.
    """
    records = []
    cur = None
    in_fence = False
    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            m = _BULLET_RE.match(line)
            if m:
                cur = {"kind": "bullet", "level": m.group(1),
                       "lines": [line]}
                records.append(cur)
                continue
            if line.startswith("### "):
                cur = {"kind": "section", "level": None, "lines": [line]}
                records.append(cur)
                continue
            if line.startswith("#") or line.strip() == "---":
                cur = {"kind": "heading", "level": None, "lines": [line]}
                records.append(cur)
                continue
        if cur is None:
            cur = {"kind": "misc", "level": None, "lines": [line]}
            records.append(cur)
        else:
            cur["lines"].append(line)
    return records


def _keep(record, levels, search):
    """Whether a record passes the level toggles + search filter."""
    if search and search.lower() not in "\n".join(record["lines"]).lower():
        return False
    # Settings / inputs-outputs blocks and any heading always stay.
    if record["kind"] in ("section", "heading", "misc"):
        return True
    # Failures always stay.
    if record["level"] in ("ERROR", "CRITICAL"):
        return True
    return levels.get(record["level"], True)


class LogsDialog(ModernDialog):
    """Browse / search / filter the Markdown session logs."""

    def __init__(self, parent=None):
        super().__init__(parent, title=_("Logs"))
        self.setModal(False)
        self.resize(1040, 720)
        self._levels = {lv: lv != "DEBUG" for lv in _LEVELS}
        self._records = []
        self._file_path = ""
        self._truncated = False
        self._setup_ui()
        self.reload()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        top = QHBoxLayout()
        top.addWidget(QLabel(_("Search")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search log entries..."))
        self.search.textChanged.connect(self._render)
        top.addWidget(self.search, 1)
        for level in _LEVELS:
            cb = QCheckBox(level)
            cb.setChecked(self._levels[level])
            cb.toggled.connect(lambda on, lv=level: self._set_level(lv, on))
            top.addWidget(cb)
        refresh = QPushButton(_("Refresh"))
        refresh.setIcon(tabler_qicon("REFRESH", 14))
        refresh.clicked.connect(self.reload)
        top.addWidget(refresh)
        self.content_layout.addLayout(top)

        body = QHBoxLayout()
        self.files = QListWidget()
        self.files.setFixedWidth(250)
        self.files.currentItemChanged.connect(lambda *_: self._load_current())
        body.addWidget(self.files)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        body.addWidget(self.view, 1)
        self.content_layout.addLayout(body, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #7d8187;")
        self.content_layout.addWidget(self.status)

    # --------------------------------------------------------------- data
    def reload(self):
        self.files.clear()
        folder = _logs_dir()
        entries = []
        try:
            for name in os.listdir(folder):
                if name.startswith("session_") and name.endswith(".md"):
                    path = os.path.join(folder, name)
                    entries.append((os.path.getmtime(path), name, path))
        except Exception:
            entries = []
        entries.sort(reverse=True)
        for _mtime, name, path in entries:
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, path)
            self.files.addItem(item)
        if self.files.count():
            self.files.setCurrentRow(0)
        else:
            self.view.setHtml("<p>No session logs found in <code>%s</code>.</p>"
                              % folder)

    def _set_level(self, level, on):
        self._levels[level] = bool(on)
        self._render()

    def _load_current(self):
        item = self.files.currentItem()
        if item is None:
            return
        path = item.data(Qt.UserRole)
        lines, truncated = _tail_lines(path)
        self._file_path = path
        self._truncated = truncated
        self._records = _parse_records(lines)
        self._render()

    def _render(self):
        search = self.search.text().strip() if hasattr(self, "search") else ""
        kept = [r for r in self._records if _keep(r, self._levels, search)]
        clipped = len(kept) > _MAX_RENDER_RECORDS
        if clipped:
            kept = kept[-_MAX_RENDER_RECORDS:]
        markdown = "\n".join("\n".join(r["lines"]) for r in kept)
        self.view.setHtml(md_to_html(markdown))
        # Reset the cursor to the top on a fresh render.
        self.view.verticalScrollBar().setValue(0)
        text = _("{shown} of {total} entries").format(
            shown=len(kept), total=len(self._records))
        if clipped or getattr(self, "_truncated", False):
            text += "  " + _("(newest entries only)")
        self.status.setText(text)
