"""Task list card for the Code Studio chat.

One request shows its whole plan in a single card - plan, every module, the
entry script, the run - and the row that is being worked on animates, so it is
obvious where a long local run actually is instead of watching one bubble per
event scroll past.
"""
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout

from NGUI.constants import ACCENT_COLOR, DANGER_COLOR, TEXT_COLOR, TEXT_MUTED

_MARK = {'pending': '\u00b7', 'ok': '\u2713', 'failed': '\u2717'}


class TaskListCard(QFrame):
    """Rows of pipeline tasks with a live state per row."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('TaskListCard')
        self.setStyleSheet(
            'QFrame#TaskListCard { background: rgba(255,255,255,0.03);'
            ' border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; }')
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(3)
        self._rows = {}
        self._layout = lay
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.setInterval(420)
        self._timer.timeout.connect(self._animate)

    # -- public ---------------------------------------------------------
    def add(self, name, detail=''):
        if name in self._rows:
            return self._rows[name]
        row = QLabel()
        row.setTextFormat(Qt.PlainText)
        row.setWordWrap(True)
        self._layout.addWidget(row)
        self._rows[name] = {'label': row, 'state': 'pending', 'detail': detail}
        self._render(name)
        return row

    def update(self, name, state, detail=''):
        if name not in self._rows:
            self.add(name, detail)
        row = self._rows[name]
        row['state'] = state
        if detail:
            row['detail'] = detail
        self._render(name)
        if any(r['state'] == 'running' for r in self._rows.values()):
            if not self._timer.isActive():
                self._timer.start()
        elif self._timer.isActive():
            self._timer.stop()

    def finish(self):
        """Stop animating; anything still 'running' ends as failed."""
        self._timer.stop()
        for name, row in self._rows.items():
            if row['state'] == 'running':
                self.update(name, 'failed', 'stopped')

    # -- internals ------------------------------------------------------
    def _animate(self):
        self._tick += 1
        for name, row in self._rows.items():
            if row['state'] == 'running':
                self._render(name)

    def _render(self, name):
        row = self._rows[name]
        state = row['state']
        detail = row['detail'] or ''
        tail = ('  ' + detail) if detail else ''
        if state == 'running':
            dots = '.' * (1 + self._tick % 3)
            text = '%s %s%s' % (name, dots, tail)
            color = ACCENT_COLOR
        else:
            text = '%s %s%s' % (_MARK.get(state, '\u00b7'), name, tail)
            color = {'ok': TEXT_COLOR, 'failed': DANGER_COLOR}.get(
                state, TEXT_MUTED)
        label = row['label']
        label.setText(text)
        label.setStyleSheet('color: %s; background: transparent;' % color)
