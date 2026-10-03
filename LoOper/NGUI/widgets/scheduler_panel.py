"""scheduler_panel.py — in-overlay scheduler panel for Agent Mode.

The conversational scheduler (an agent "tool") was dropped: it bloated the
router LLM's context and interfered with System-chain execution.  Instead the
agent chat header exposes a calendar button that swaps the transcript for this
panel — the same CRUD as the graph UI's Scheduler dialog, rendered as an
overlay panel rather than a modal QDialog.

ponytail: duplicates the dialog's tiny ``_when_text``/``date`` helpers instead
of importing scheduler_dialogs (which drags ModernDialog + help overlay into
the overlay).  Fold them together if a third consumer appears.
"""

import logging
import os

from PyQt5.QtCore import QDateTime, Qt, QTime, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..constants import (
    ACCENT_COLOR,
    BLOCK_COLOR,
    BLOCK_HOVER,
    LIGHT_GREY,
    RED_PRIMARY,
    TEXT_COLOR,
)
from ..i18n import _

logger = logging.getLogger(__name__)

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_PANEL_QSS = (
    f"QFrame#schedulerPanel {{ background-color: transparent; border: 0px; }}"
    f"QLabel {{ color: {TEXT_COLOR}; }}"
)


class SchedulerPanel(QFrame):
    """Schedules list + inline editor, hosted inside the agent overlay."""

    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("schedulerPanel")
        self.setStyleSheet(_PANEL_QSS)
        self._service = None
        self._editing_id = None
        self._build_ui()

    # ── Public API ───────────────────────────────────────────────────
    def set_service(self, service):
        """Attach the live SchedulerService (may be None in standalone builds)."""
        self._service = service
        self.refresh()

    def refresh(self):
        """Rebuild the schedule rows from the service."""
        try:
            self._clear_list()
            if self._service is None:
                self._empty_label.setText(_("Scheduler is not available in this session."))
                self._empty_label.show()
                self._count_label.setText("")
                return
            schedules = self._service.list_schedules()
            self._count_label.setText(_("{} schedule(s)").format(len(schedules)))
            if not schedules:
                self._empty_label.setText(_("No schedules yet — click Add."))
                self._empty_label.show()
                return
            self._empty_label.hide()
            for schedule in schedules:
                row = self._build_row(schedule)
                self._row_widgets.append(row)
                self._list_layout.addWidget(row)
        except Exception:
            logger.exception("SchedulerPanel.refresh failed")

    # ── UI construction ──────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # header row
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        title = QLabel(_("Schedules"), self)
        title.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 13px; font-weight: 700;"
        )
        head.addWidget(title)
        self._count_label = QLabel("", self)
        self._count_label.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 10px;")
        head.addWidget(self._count_label)
        head.addStretch(1)

        self._add_btn = self._small_btn(_("Add"), ACCENT_COLOR)
        self._add_btn.clicked.connect(lambda: self._open_form(None))
        head.addWidget(self._add_btn)

        self._refresh_btn = self._small_btn(_("Refresh"), None)
        self._refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self._refresh_btn)

        self._back_btn = self._small_btn(_("Back to chat"), None)
        self._back_btn.clicked.connect(self.close_requested.emit)
        head.addWidget(self._back_btn)
        root.addLayout(head)

        # inline editor (hidden until Add/Edit)
        self._form = self._build_form()
        self._form.hide()
        root.addWidget(self._form)

        # scrollable list
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea { background-color: transparent; border: 0px; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; border: none; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.10); min-height: 20px; border-radius: 3px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.18); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )
        self._list_container = QWidget(self._scroll)
        self._list_container.setStyleSheet("background-color: transparent;")
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(6)
        self._list_layout.setAlignment(Qt.AlignTop)
        # Rows are tracked separately so refresh() never touches the persistent
        # empty-state label: deleteLater()-ing it and then calling setText() on
        # the stale wrapper (after Qt runs DeferredDelete) aborts the app.
        self._row_widgets = []
        self._empty_label = QLabel(_("No schedules yet."), self._list_container)
        self._empty_label.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 12px; padding: 16px;"
        )
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._list_layout.addWidget(self._empty_label)
        self._scroll.setWidget(self._list_container)
        root.addWidget(self._scroll, 1)

    def _build_form(self) -> QFrame:
        box = QFrame(self)
        box.setStyleSheet(
            f"QFrame {{ background-color: {BLOCK_COLOR};"
            f"  border: 1px solid {LIGHT_GREY}; border-radius: 8px; }}"
        )
        form = QVBoxLayout(box)
        form.setContentsMargins(10, 10, 10, 10)
        form.setSpacing(6)

        # name
        form.addWidget(self._field_label(_("Name")))
        self._f_name = self._line_edit()
        self._f_name.setPlaceholderText(_("Schedule name (optional)"))
        form.addWidget(self._f_name)

        # type + chain
        row = QHBoxLayout()
        row.setSpacing(8)
        type_col = QVBoxLayout()
        type_col.setSpacing(4)
        type_col.addWidget(self._field_label(_("Type")))
        self._f_type = QComboBox()
        self._f_type.addItems(["once", "daily", "weekly", "interval"])
        self._f_type.currentTextChanged.connect(self._update_fields)
        type_col.addWidget(self._f_type)
        row.addLayout(type_col, 1)

        chain_col = QVBoxLayout()
        chain_col.setSpacing(4)
        chain_col.addWidget(self._field_label(_("Chain file")))
        chain_row = QHBoxLayout()
        chain_row.setSpacing(6)
        self._f_chain = self._line_edit()
        self._f_chain.setPlaceholderText(_("chains/my_chain.json"))
        chain_row.addWidget(self._f_chain, 1)
        browse = self._small_btn(_("Browse"), None)
        browse.clicked.connect(self._browse_chain)
        chain_row.addWidget(browse)
        chain_col.addLayout(chain_row)
        row.addLayout(chain_col, 2)
        form.addLayout(row)

        # timing — one group per type
        self._g_once = self._group(self._field_label(_("Date & time")))
        self._f_once = QDateTimeEdit(QDateTime.currentDateTime())
        self._f_once.setCalendarPopup(True)
        self._f_once.setDisplayFormat("yyyy-MM-dd hh:mm:ss")
        self._g_once.layout().addWidget(self._f_once)
        form.addWidget(self._g_once)

        self._g_daily = self._group(self._field_label(_("Time of day")))
        self._f_daily = QTimeEdit(QTime.currentTime())
        self._f_daily.setDisplayFormat("hh:mm")
        self._g_daily.layout().addWidget(self._f_daily)
        form.addWidget(self._g_daily)

        self._g_weekly = self._group(self._field_label(_("Time & days")))
        self._f_weekly = QTimeEdit(QTime.currentTime())
        self._f_weekly.setDisplayFormat("hh:mm")
        self._g_weekly.layout().addWidget(self._f_weekly)
        days_row = QHBoxLayout()
        days_row.setSpacing(4)
        self._f_days = []
        for name in _DAYS:
            cb = QCheckBox(name)
            cb.setStyleSheet(f"QCheckBox {{ color: {TEXT_COLOR}; font-size: 11px; }}")
            self._f_days.append(cb)
            days_row.addWidget(cb)
        days_row.addStretch(1)
        self._g_weekly.layout().addLayout(days_row)
        form.addWidget(self._g_weekly)

        self._g_interval = self._group(self._field_label(_("Repeat every")))
        self._f_interval = QSpinBox()
        self._f_interval.setRange(1, 100000)
        self._f_interval.setSuffix(_(" minutes"))
        self._g_interval.layout().addWidget(self._f_interval)
        form.addWidget(self._g_interval)

        self._f_sandbox = QCheckBox(_("Run in sandbox (RDP session)"))
        self._f_sandbox.setStyleSheet(f"QCheckBox {{ color: {TEXT_COLOR}; font-size: 11px; }}")
        form.addWidget(self._f_sandbox)

        # actions
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = self._small_btn(_("Cancel"), None)
        cancel.clicked.connect(self._close_form)
        actions.addWidget(cancel)
        self._save_btn = QPushButton(_("Save"), box)
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setFixedHeight(26)
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: #0a0a0a;"
            f"  border: 0px; border-radius: 6px; padding: 0px 16px;"
            f"  font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: #4fdcf0; }}"
        )
        self._save_btn.clicked.connect(self._save)
        actions.addWidget(self._save_btn)
        form.addLayout(actions)

        self._update_fields(self._f_type.currentText())
        return box

    # ── small widget helpers ─────────────────────────────────────────
    @staticmethod
    def _field_label(text) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: rgba(255,255,255,0.45); font-size: 10px;")
        return lbl

    @staticmethod
    def _line_edit() -> QLineEdit:
        le = QLineEdit()
        le.setFixedHeight(26)
        le.setStyleSheet(
            f"QLineEdit {{ background-color: rgba(0,0,0,0.25); color: {TEXT_COLOR};"
            f"  border: 1px solid rgba(255,255,255,0.12); border-radius: 6px;"
            f"  padding: 2px 8px; font-size: 11px; }}"
            f"QLineEdit:focus {{ border: 1px solid {ACCENT_COLOR}; }}"
        )
        return le

    @staticmethod
    def _small_btn(text, color) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(26)
        fg = color or "rgba(255,255,255,0.72)"
        border = color or "rgba(255,255,255,0.15)"
        hover = "rgba(34,211,238,0.12)" if color == ACCENT_COLOR else "rgba(255,255,255,0.08)"
        btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {fg};"
            f"  border: 1px solid {border}; border-radius: 6px; padding: 0px 12px;"
            f"  font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {hover}; }}"
        )
        return btn

    @staticmethod
    def _group(label: QLabel) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(label)
        return w

    # ── list rows ────────────────────────────────────────────────────
    def _clear_list(self):
        """Remove only the schedule rows; the empty-state label is persistent."""
        for row in self._row_widgets:
            try:
                self._list_layout.removeWidget(row)
                row.deleteLater()
            except Exception:
                pass
        self._row_widgets = []

    def _build_row(self, schedule) -> QFrame:
        sid = schedule.get("id") or ""
        row = QFrame(self._list_container)
        row.setStyleSheet(
            "QFrame { background-color: rgba(255,255,255,0.03);"
            "  border: 1px solid rgba(255,255,255,0.07); border-radius: 8px; }"
        )
        h = QHBoxLayout(row)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(8)

        chk = QCheckBox(row)
        chk.setChecked(schedule.get("enabled", True))
        chk.setToolTip(_("Enable / disable this schedule"))
        chk.stateChanged.connect(
            lambda state, sid=sid: self._toggle_enabled(sid, state == Qt.Checked)
        )
        h.addWidget(chk)

        info = QVBoxLayout()
        info.setSpacing(2)
        name = QLabel(schedule.get("name") or str(schedule.get("id", ""))[:8])
        name.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px; font-weight: 600;")
        info.addWidget(name)
        detail = QLabel(f"{self._when_text(schedule)}  \u2022  {self._chain_name(schedule)}")
        detail.setStyleSheet("color: rgba(255,255,255,0.55); font-size: 11px;")
        info.addWidget(detail)
        nxt = QLabel(_("next: {}").format(schedule.get("next_run_readable", "\u2014")))
        nxt.setStyleSheet("color: rgba(255,255,255,0.35); font-size: 10px;")
        info.addWidget(nxt)
        h.addLayout(info, 1)

        run_btn = self._small_btn(_("Run"), None)
        run_btn.clicked.connect(lambda _, sid=sid: self._run_now(sid))
        h.addWidget(run_btn)

        edit_btn = self._small_btn(_("Edit"), None)
        edit_btn.clicked.connect(lambda _, s=schedule: self._open_form(s))
        h.addWidget(edit_btn)

        del_btn = self._small_btn(_("Del"), RED_PRIMARY)
        del_btn.clicked.connect(lambda _, sid=sid: self._delete(sid))
        h.addWidget(del_btn)
        return row

    # ── actions ──────────────────────────────────────────────────────
    def _toggle_enabled(self, sid, enabled):
        if self._service is None:
            return
        try:
            self._service.set_enabled(sid, enabled)
            self.refresh()
        except Exception as exc:
            self._error(str(exc))

    def _run_now(self, sid):
        if self._service is None:
            return
        try:
            self._service.run_now(sid)
            self.refresh()
        except Exception as exc:
            self._error(_("Run Now Failed"), str(exc))

    def _delete(self, sid):
        if self._service is None:
            return
        if QMessageBox.question(
            self, _("Delete Schedule"), _("Delete this schedule?"),
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            self._service.remove_schedule(sid)
        except Exception as exc:
            self._error(_("Delete Failed"), str(exc))
        self.refresh()

    def _browse_chain(self):
        start_dir = ""
        if self._service is not None:
            root = getattr(self._service, "project_root", "") or ""
            candidate = os.path.join(root, "chains")
            if os.path.isdir(candidate):
                start_dir = candidate
        path, _f = QFileDialog.getOpenFileName(
            self, _("Select Chain JSON File"), start_dir,
            _("JSON Files (*.json);;All Files (*.*)"),
        )
        if path:
            self._f_chain.setText(path)

    # ── inline editor ────────────────────────────────────────────────
    def _open_form(self, schedule):
        self._editing_id = schedule.get("id") if schedule else None
        self._f_name.setText((schedule or {}).get("name", "") or "")
        self._f_chain.setText((schedule or {}).get("chain_path", "") or "")
        self._f_sandbox.setChecked(bool((schedule or {}).get("run_in_sandbox", False)))

        stype = (schedule or {}).get("type", "once")
        idx = self._f_type.findText(stype)
        self._f_type.setCurrentIndex(idx if idx >= 0 else 0)

        try:
            if schedule and schedule.get("once_datetime"):
                dt = QDateTime.fromString(schedule["once_datetime"], Qt.ISODate)
                if dt.isValid():
                    self._f_once.setDateTime(dt)
            if schedule and schedule.get("daily_time"):
                h, m = [int(x) for x in schedule["daily_time"].split(":")]
                self._f_daily.setTime(QTime(h, m))
            if schedule and schedule.get("weekly_time"):
                h, m = [int(x) for x in schedule["weekly_time"].split(":")]
                self._f_weekly.setTime(QTime(h, m))
            if schedule and schedule.get("interval_minutes"):
                self._f_interval.setValue(int(schedule["interval_minutes"]))
        except Exception:
            pass
        for i, cb in enumerate(self._f_days):
            cb.setChecked(bool(schedule and i in (schedule.get("days_of_week") or [])))

        self._update_fields(self._f_type.currentText())
        self._form.show()

    def _close_form(self):
        self._editing_id = None
        self._form.hide()

    def _update_fields(self, stype):
        self._g_once.setVisible(stype == "once")
        self._g_daily.setVisible(stype == "daily")
        self._g_weekly.setVisible(stype == "weekly")
        self._g_interval.setVisible(stype == "interval")

    def _save(self):
        if self._service is None:
            return
        stype = self._f_type.currentText()
        data = {
            "name": self._f_name.text().strip() or f"{_('Schedule')} {stype}",
            "type": stype,
            "chain_path": self._f_chain.text().strip(),
            "enabled": True,
            "run_in_sandbox": self._f_sandbox.isChecked(),
            "show_sandbox_window": True,
        }
        if stype == "once":
            data["once_datetime"] = self._f_once.dateTime().toString(Qt.ISODate)
        elif stype == "daily":
            tm = self._f_daily.time()
            data["daily_time"] = f"{tm.hour():02d}:{tm.minute():02d}"
        elif stype == "weekly":
            tm = self._f_weekly.time()
            data["weekly_time"] = f"{tm.hour():02d}:{tm.minute():02d}"
            data["days_of_week"] = [i for i, cb in enumerate(self._f_days) if cb.isChecked()]
        elif stype == "interval":
            data["interval_minutes"] = int(self._f_interval.value())
            data["start_datetime"] = QDateTime.currentDateTime().toString(Qt.ISODate)

        if not data["chain_path"]:
            self._error(_("Missing chain"), _("Select a chain file to run."))
            return
        try:
            if self._editing_id:
                data["id"] = self._editing_id
                self._service.update_schedule(data)
            else:
                self._service.add_schedule(data)
        except Exception as exc:
            self._error(_("Save Failed"), str(exc))
            return
        self._close_form()
        self.refresh()

    # ── helpers ──────────────────────────────────────────────────────
    def _error(self, title, message=""):
        QMessageBox.critical(self, title, message or title)

    @staticmethod
    def _chain_name(schedule) -> str:
        path = str(schedule.get("chain_path") or "")
        return os.path.basename(path) or "\u2014"

    @staticmethod
    def _when_text(schedule) -> str:
        stype = schedule.get("type")
        if stype == "once":
            return _("once at {}").format(schedule.get("once_datetime", "\u2014"))
        if stype == "daily":
            return _("daily at {}").format(schedule.get("daily_time", "\u2014"))
        if stype == "weekly":
            days = schedule.get("days_of_week") or []
            labels = "/".join(_DAYS[d] for d in days if 0 <= d < 7) or "?"
            return _("weekly {} at {}").format(labels, schedule.get("weekly_time", "\u2014"))
        if stype == "interval":
            return _("every {} min").format(schedule.get("interval_minutes", 0))
        return str(stype or "\u2014")
