from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QWidget, QComboBox, QSpinBox, QCheckBox, QTimeEdit, QDateTimeEdit, QFileDialog, QMessageBox, QLineEdit,
    QGroupBox, QFrame
)
from PyQt5.QtCore import QTime, QDateTime, Qt
from ..constants import (
    DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR, BLOCK_COLOR, BLOCK_HOVER, RED_PRIMARY
)
from ..i18n import _


class SchedulerManagerDialog(QDialog):
    def __init__(self, parent, scheduler_service):
        super().__init__(parent)
        self.setWindowTitle(_("Manage Schedules"))
        self.scheduler = scheduler_service
        self.resize(720, 420)
        self.setMinimumSize(600, 400)
        
        # Apply consistent dark theme styling
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {DARK_GREY};
                color: {TEXT_COLOR};
            }}
            QTableWidget {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                gridline-color: {LIGHT_GREY};
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                selection-background-color: {BLOCK_HOVER};
            }}
            QTableWidget::item {{
                padding: 8px;
                border-bottom: 1px solid {LIGHT_GREY};
            }}
            QTableWidget::item:selected {{
                background-color: {BLOCK_HOVER};
            }}
            QHeaderView::section {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                padding: 8px;
                border: 1px solid {LIGHT_GREY};
                font-weight: bold;
            }}
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: bold;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background-color: {BLOCK_HOVER};
                border-color: {TEXT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {LIGHT_GREY};
            }}
            QCheckBox {{
                color: {TEXT_COLOR};
                spacing: 5px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 2px solid {LIGHT_GREY};
                border-radius: 3px;
                background-color: {MEDIUM_GREY};
            }}
            QCheckBox::indicator:checked {{
                background-color: {RED_PRIMARY};
                border-color: {RED_PRIMARY};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Table
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            _("Enabled"), _("Name"), _("Type"), _("When"), _("Next Run"), _("Chain"), _("Actions")
        ])
        # Set column widths for better visibility
        self.table.setColumnWidth(0, 80)   # Enabled
        self.table.setColumnWidth(1, 120)  # Name
        self.table.setColumnWidth(2, 80)   # Type
        self.table.setColumnWidth(3, 150)  # When
        self.table.setColumnWidth(4, 130)  # Next Run
        self.table.setColumnWidth(5, 200)  # Chain
        self.table.setColumnWidth(6, 200)  # Actions
        
        # Enable alternating row colors for better readability
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # Buttons
        btn_frame = QFrame()
        btn_frame.setFrameStyle(QFrame.StyledPanel)
        btn_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {BLOCK_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                padding: 5px;
            }}
        """)
        btn_row = QHBoxLayout(btn_frame)
        btn_row.setContentsMargins(10, 8, 10, 8)
        btn_row.setSpacing(10)
        
        add_btn = QPushButton(f"➕ {_("Add Schedule")}")
        add_btn.clicked.connect(self.add_schedule)
        btn_row.addWidget(add_btn)

        refresh_btn = QPushButton(f"🔄 {_("Refresh")}")
        refresh_btn.clicked.connect(self.reload)
        btn_row.addWidget(refresh_btn)

        btn_row.addStretch(1)
        
        close_btn = QPushButton(f"✖ {_("Close")}")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addWidget(btn_frame)

        self.reload()

    def reload(self):
        schedules = self.scheduler.list_schedules()
        self.table.setRowCount(0)
        for s in schedules:
            self._add_schedule_row(s)

    def _add_schedule_row(self, s):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # Enabled checkbox
        enabled_cb = QCheckBox()
        enabled_cb.setChecked(s.get('enabled', True))
        enabled_cb.stateChanged.connect(lambda state, sid=s['id']: self._toggle_enabled(sid, state == 2))
        self.table.setCellWidget(row, 0, enabled_cb)

        # Name
        name_item = QTableWidgetItem(s.get('name') or s.get('id')[:8])
        self.table.setItem(row, 1, name_item)

        # Type
        type_item = QTableWidgetItem(s.get('type', '—'))
        self.table.setItem(row, 2, type_item)

        # When
        when_item = QTableWidgetItem(self._when_text(s))
        self.table.setItem(row, 3, when_item)

        # Next Run
        next_item = QTableWidgetItem(s.get('next_run_readable', '—'))
        self.table.setItem(row, 4, next_item)

        # Chain path
        chain_item = QTableWidgetItem(s.get('chain_path', '—'))
        self.table.setItem(row, 5, chain_item)

        # Actions
        actions_widget = QWidget()
        actions_widget.setStyleSheet(f"""
            QWidget {{
                background-color: transparent;
            }}
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 3px;
                padding: 4px 8px;
                font-size: 11px;
                min-width: 50px;
                max-width: 60px;
            }}
            QPushButton:hover {{
                background-color: {BLOCK_HOVER};
                border-color: {TEXT_COLOR};
            }}
        """)
        actions_layout = QHBoxLayout(actions_widget)
        actions_layout.setContentsMargins(2, 2, 2, 2)
        actions_layout.setSpacing(3)

        run_btn = QPushButton(f"▶ {_("Run")}")
        run_btn.setToolTip(_("Run this schedule now"))
        run_btn.clicked.connect(lambda _, sid=s['id']: self._run_now(sid))
        actions_layout.addWidget(run_btn)

        edit_btn = QPushButton(f"✏ {_("Edit")}")
        edit_btn.setToolTip(_("Edit this schedule"))
        edit_btn.clicked.connect(lambda _, sched=s: self._edit_schedule(sched))
        actions_layout.addWidget(edit_btn)

        del_btn = QPushButton(f"🗑 {_("Del")}")
        del_btn.setToolTip(_("Delete this schedule"))
        del_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {RED_PRIMARY};
                color: {TEXT_COLOR};
                border: 1px solid {RED_PRIMARY};
            }}
            QPushButton:hover {{
                background-color: #FF6B6B;
                border-color: #FF6B6B;
            }}
        """)
        del_btn.clicked.connect(lambda _, sid=s['id']: self._delete_schedule(sid))
        actions_layout.addWidget(del_btn)

        actions_layout.addStretch(1)
        self.table.setCellWidget(row, 6, actions_widget)

    def _toggle_enabled(self, sid, enabled):
        try:
            self.scheduler.set_enabled(sid, enabled)
            self.reload()
        except Exception as e:
            QMessageBox.critical(self, _("Error"), str(e))

    def _run_now(self, sid):
        try:
            self.scheduler.run_now(sid)
        except Exception as e:
            QMessageBox.critical(self, _("Run Now Failed"), str(e))

    def _delete_schedule(self, sid):
        self.scheduler.remove_schedule(sid)
        self.reload()

    def _edit_schedule(self, sched):
        dlg = EditScheduleDialog(self, sched)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            data['id'] = sched['id']
            try:
                self.scheduler.update_schedule(data)
                self.reload()
            except Exception as e:
                QMessageBox.critical(self, _("Update Failed"), str(e))

    def add_schedule(self):
        dlg = EditScheduleDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            data = dlg.get_data()
            try:
                self.scheduler.add_schedule(data)
                self.reload()
            except Exception as e:
                QMessageBox.critical(self, _("Add Failed"), str(e))

    @staticmethod
    def _when_text(s):
        st = s.get('type')
        if st == 'once':
            return s.get('once_datetime', '—')
        if st == 'daily':
            return s.get('daily_time', '—')
        if st == 'weekly':
            days = s.get('days_of_week', [])
            return f"{s.get('weekly_time','—')} | {days}"
        if st == 'interval':
            return f"{_("every")} {s.get('interval_minutes', 0)} {_("min")}"
        return '—'


class EditScheduleDialog(QDialog):
    def __init__(self, parent, schedule=None):
        super().__init__(parent)
        self.setWindowTitle(_("Schedule Configuration"))
        self.resize(580, 450)
        self.setMinimumSize(500, 400)
        self._schedule = schedule or {}
        
        # Apply consistent dark theme styling
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {DARK_GREY};
                color: {TEXT_COLOR};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 2px solid {LIGHT_GREY};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                background-color: {MEDIUM_GREY};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: {TEXT_COLOR};
            }}
            QLabel {{
                color: {TEXT_COLOR};
                font-weight: normal;
                padding: 2px;
            }}
            QLineEdit, QComboBox, QSpinBox, QTimeEdit, QDateTimeEdit {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 6px;
                min-height: 20px;
            }}
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
                border-color: {RED_PRIMARY};
                background-color: {BLOCK_HOVER};
            }}
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background-color: {BLOCK_HOVER};
                border-color: {TEXT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {LIGHT_GREY};
            }}
            QCheckBox {{
                color: {TEXT_COLOR};
                spacing: 5px;
                padding: 2px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 2px solid {LIGHT_GREY};
                border-radius: 3px;
                background-color: {MEDIUM_GREY};
            }}
            QCheckBox::indicator:checked {{
                background-color: {RED_PRIMARY};
                border-color: {RED_PRIMARY};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)

        # Basic Configuration Group
        basic_group = QGroupBox(_("📋 Basic Configuration"))
        basic_layout = QVBoxLayout(basic_group)
        basic_layout.setSpacing(10)
        
        # Name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(_("Schedule Name:")))
        self.name_edit = QLineEdit(self._schedule.get('name', ''))
        self.name_edit.setPlaceholderText(_("Enter a descriptive name for this schedule"))
        name_row.addWidget(self.name_edit)
        basic_layout.addLayout(name_row)

        # Type
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel(_("Schedule Type:")))
        self.type_cb = QComboBox()
        self.type_cb.addItems(["once", "daily", "weekly", "interval"])
        if schedule:
            t = schedule.get('type')
            if t:
                idx = self.type_cb.findText(t)
                if idx >= 0:
                    self.type_cb.setCurrentIndex(idx)
        self.type_cb.currentTextChanged.connect(self._update_fields)
        type_row.addWidget(self.type_cb)
        type_row.addStretch()
        basic_layout.addLayout(type_row)

        # Chain file
        chain_row = QHBoxLayout()
        chain_row.addWidget(QLabel(_("Chain File:")))
        self.chain_btn = QPushButton(f"📁 {_("Select Chain JSON...")}")
        self.chain_btn.clicked.connect(self._select_chain)
        chain_row.addWidget(self.chain_btn)
        basic_layout.addLayout(chain_row)
        
        self.chain_lbl = QLabel(self._schedule.get('chain_path', _("No file selected")))
        self.chain_lbl.setStyleSheet(f"""
            QLabel {{
                background-color: {BLOCK_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 6px;
                font-style: italic;
            }}
        """)
        basic_layout.addWidget(self.chain_lbl)
        
        layout.addWidget(basic_group)

        # Timing Configuration Group
        timing_group = QGroupBox(_("⏰ Timing Configuration"))
        timing_layout = QVBoxLayout(timing_group)
        timing_layout.setSpacing(10)
        
        # Once datetime
        self.once_row_w = QWidget()
        once_layout = QVBoxLayout(self.once_row_w)
        once_layout.setContentsMargins(0, 0, 0, 0)
        once_layout.addWidget(QLabel(_("📅 Run at specific date and time:")))
        self.once_dt = QDateTimeEdit(QDateTime.currentDateTime())
        self.once_dt.setCalendarPopup(True)
        self.once_dt.setDisplayFormat("yyyy-MM-dd hh:mm:ss")
        once_layout.addWidget(self.once_dt)
        timing_layout.addWidget(self.once_row_w)

        # Daily time
        self.daily_row_w = QWidget()
        daily_layout = QVBoxLayout(self.daily_row_w)
        daily_layout.setContentsMargins(0, 0, 0, 0)
        daily_layout.addWidget(QLabel(_("🌅 Daily at time:")))
        self.daily_time = QTimeEdit(QTime.currentTime())
        self.daily_time.setDisplayFormat("hh:mm")
        daily_layout.addWidget(self.daily_time)
        timing_layout.addWidget(self.daily_row_w)

        # Weekly time and days
        self.weekly_row_w = QWidget()
        weekly_layout = QVBoxLayout(self.weekly_row_w)
        weekly_layout.setContentsMargins(0, 0, 0, 0)
        weekly_layout.addWidget(QLabel(_("📅 Weekly schedule:")))
        
        time_row = QHBoxLayout()
        time_row.addWidget(QLabel(_("Time:")))
        self.weekly_time = QTimeEdit(QTime.currentTime())
        self.weekly_time.setDisplayFormat("hh:mm")
        time_row.addWidget(self.weekly_time)
        time_row.addStretch()
        weekly_layout.addLayout(time_row)
        
        days_label = QLabel(_("Days of week:"))
        weekly_layout.addWidget(days_label)
        days_row = QHBoxLayout()
        self.weekly_days = [QCheckBox(_(d)) for d in ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]]
        for cb in self.weekly_days:
            days_row.addWidget(cb)
        days_row.addStretch()
        weekly_layout.addLayout(days_row)
        timing_layout.addWidget(self.weekly_row_w)

        # Interval minutes
        self.interval_row_w = QWidget()
        interval_layout = QVBoxLayout(self.interval_row_w)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        interval_layout.addWidget(QLabel(_("🔄 Repeat every (minutes):")))
        interval_row = QHBoxLayout()
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(1, 100000)
        self.interval_spin.setSuffix(_(" minutes"))
        interval_row.addWidget(self.interval_spin)
        interval_row.addStretch()
        interval_layout.addLayout(interval_row)
        timing_layout.addWidget(self.interval_row_w)
        
        layout.addWidget(timing_group)

        # Buttons
        btn_frame = QFrame()
        btn_frame.setFrameStyle(QFrame.StyledPanel)
        btn_frame.setStyleSheet(f"""
            QFrame {{
                background-color: {BLOCK_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                padding: 5px;
            }}
        """)
        btn_row = QHBoxLayout(btn_frame)
        btn_row.setContentsMargins(10, 8, 10, 8)
        btn_row.setSpacing(10)
        
        btn_row.addStretch(1)
        
        cancel_btn = QPushButton(f"✖ {_("Cancel")}")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        
        save_btn = QPushButton(f"💾 {_("Save Schedule")}")
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {RED_PRIMARY};
                color: {TEXT_COLOR};
                border: 1px solid {RED_PRIMARY};
                font-weight: bold;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background-color: #FF6B6B;
                border-color: #FF6B6B;
            }}
        """)
        save_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        
        layout.addWidget(btn_frame)

        # Initialize with schedule data
        self._apply_schedule()
        self._update_fields(self.type_cb.currentText())



    def _apply_schedule(self):
        s = self._schedule
        if 'name' in s:
            self.name_edit.setText(s.get('name', ''))
        if s.get('chain_path'):
            import os
            chain_path = s['chain_path']
            filename = os.path.basename(chain_path)
            self.chain_lbl.setText(f"📄 {filename}")
            self.chain_lbl.setToolTip(_("Full path: {path}").format(path=chain_path))
            self._selected_chain_path = chain_path
        t = s.get('type')
        if t == 'once' and s.get('once_datetime'):
            try:
                dt = QDateTime.fromString(s['once_datetime'], Qt.ISODate)
                if dt.isValid():
                    self.once_dt.setDateTime(dt)
            except Exception:
                pass
        if t == 'daily' and s.get('daily_time'):
            try:
                h, m = [int(x) for x in s['daily_time'].split(':')]
                self.daily_time.setTime(QTime(h, m))
            except Exception:
                pass
        if t == 'weekly':
            if s.get('weekly_time'):
                try:
                    h, m = [int(x) for x in s['weekly_time'].split(':')]
                    self.weekly_time.setTime(QTime(h, m))
                except Exception:
                    pass
            for idx in s.get('days_of_week', []):
                if 0 <= idx < 7:
                    self.weekly_days[idx].setChecked(True)
        if t == 'interval' and s.get('interval_minutes'):
            try:
                self.interval_spin.setValue(int(s['interval_minutes']))
            except Exception:
                pass

    def _update_fields(self, t):
        self.once_row_w.setVisible(t == 'once')
        self.daily_row_w.setVisible(t == 'daily')
        self.weekly_row_w.setVisible(t == 'weekly')
        self.interval_row_w.setVisible(t == 'interval')

    def _select_chain(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 
            _("Select Chain JSON File"), 
            "", 
            _("JSON Files (*.json);;All Files (*.*)")
        )
        if path:
            # Show just the filename for better readability, but store full path
            import os
            filename = os.path.basename(path)
            self.chain_lbl.setText(f"📄 {filename}")
            self.chain_lbl.setToolTip(_("Full path: {path}").format(path=path))
            # Store the full path for later use
            self._selected_chain_path = path
        else:
            self.chain_lbl.setText(_("No file selected"))
            self.chain_lbl.setToolTip("")
            self._selected_chain_path = None

    def get_data(self):
        t = self.type_cb.currentText()
        # Use the stored full path or fall back to the label text
        chain_path = getattr(self, '_selected_chain_path', None) or self._schedule.get('chain_path', '')
        
        data = {
            'name': self.name_edit.text().strip() or f"{_("Schedule")} {t}",
            'type': t,
            'chain_path': chain_path,
            'enabled': True,
        }
        if t == 'once':
            dt = self.once_dt.dateTime().toString(Qt.ISODate)
            data['once_datetime'] = dt
        elif t == 'daily':
            tm = self.daily_time.time()
            data['daily_time'] = f"{tm.hour():02d}:{tm.minute():02d}"
        elif t == 'weekly':
            tm = self.weekly_time.time()
            data['weekly_time'] = f"{tm.hour():02d}:{tm.minute():02d}"
            data['days_of_week'] = [i for i, cb in enumerate(self.weekly_days) if cb.isChecked()]
        elif t == 'interval':
            data['interval_minutes'] = int(self.interval_spin.value())
            data['start_datetime'] = QDateTime.currentDateTime().toString(Qt.ISODate)
        return data