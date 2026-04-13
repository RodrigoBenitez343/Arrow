"""Trigger-related dialog classes for the LoOper application.

This module contains dialog classes for configuring various types of triggers:
- TriggerConfigDialog: Basic image-based trigger configuration
- OCRTriggerConfigDialog: OCR-based trigger configuration
- WaitConditionDialog: Wait condition configuration
- ConditionalLoopDialog: Conditional loop configuration
"""

import os
import time
from .base_dialog import ModernDialog
from .toggle_switch import ModernToggle
from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QFileDialog, QGridLayout, QWidget, QApplication, QRubberBand, QDialog
)
from PyQt5.QtCore import Qt, QRect, pyqtSignal, QTimer
from PyQt5.QtGui import QGuiApplication, QPainter, QColor, QCursor, QPen
from ..constants import TEXT_COLOR
from ..i18n import _


def _get_main_window_from_widget(widget):
    try:
        w = widget
        while w is not None:
            parent = w.parent()
            if parent is None:
                return w
            w = parent
    except Exception:
        pass
    try:
        aw = QApplication.instance().activeWindow()
        if aw is not None:
            return aw
    except Exception:
        pass
    return None


def _find_sequences_folder_from_widget(widget):
    w = widget
    while w is not None:
        if hasattr(w, 'sequences_folder'):
            return getattr(w, 'sequences_folder')
        if hasattr(w, 'graph_view') and hasattr(getattr(w, 'graph_view'), 'sequences_folder'):
            return getattr(getattr(w, 'graph_view'), 'sequences_folder')
        try:
            w = w.parent()
        except Exception:
            break
    try:
        aw = QApplication.instance().activeWindow()
        if aw is not None and hasattr(aw, 'graph_view') and hasattr(getattr(aw, 'graph_view'), 'sequences_folder'):
            return getattr(getattr(aw, 'graph_view'), 'sequences_folder')
    except Exception:
        pass
    return os.path.join(os.getcwd(), 'sequences')


def _get_capture_screen():
    try:
        screen = QGuiApplication.screenAt(QCursor.pos())
        if screen is not None:
            return screen
    except Exception:
        pass
    return QGuiApplication.primaryScreen()


class SnipCaptureDialog(QDialog):
    def __init__(self, frozen_pixmap, screen_geometry, parent=None):
        super().__init__(parent)
        self._frozen_pixmap = frozen_pixmap
        self._screen_geometry = screen_geometry
        self._origin = None
        self._current = None
        self._selected_rect = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowModality(Qt.ApplicationModal)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        try:
            self.setGeometry(self._screen_geometry)
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._selected_rect = None
            self.reject()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._origin = event.pos()
        self._current = event.pos()
        self.update()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        self._current = event.pos()
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        self._current = event.pos()
        rect = QRect(self._origin, self._current).normalized()
        self._origin = None
        self._current = None
        if rect.width() < 5 or rect.height() < 5:
            self._selected_rect = None
            self.reject()
            return
        self._selected_rect = rect
        self.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._frozen_pixmap is not None:
            painter.drawPixmap(0, 0, self._frozen_pixmap)

        overlay = QColor(0, 0, 0, 90)
        painter.fillRect(self.rect(), overlay)

        rect = None
        if self._origin is not None and self._current is not None:
            rect = QRect(self._origin, self._current).normalized()
        elif self._selected_rect is not None:
            rect = self._selected_rect

        if rect is not None and rect.width() > 0 and rect.height() > 0 and self._frozen_pixmap is not None:
            painter.drawPixmap(rect, self._frozen_pixmap, rect)
            pen = QPen(QColor(0, 170, 255, 255))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(rect)

    def get_result(self):
        if self._selected_rect is None or self._frozen_pixmap is None:
            return None, None
        try:
            cropped = self._frozen_pixmap.copy(self._selected_rect)
        except Exception:
            cropped = None
        geo = self._screen_geometry or QRect(0, 0, 0, 0)
        global_rect = QRect(
            int(geo.x() + self._selected_rect.x()),
            int(geo.y() + self._selected_rect.y()),
            int(self._selected_rect.width()),
            int(self._selected_rect.height())
        )
        return cropped, global_rect


class FrozenScreenSnipOverlay(QWidget):
    snip_done = pyqtSignal(object, object)
    snip_cancelled = pyqtSignal()

    def __init__(self, frozen_pixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setWindowModality(Qt.ApplicationModal)
        self._frozen_pixmap = frozen_pixmap
        self._origin = None
        self._rubber_band = QRubberBand(QRubberBand.Rectangle, self)
        self._rubber_band.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._frozen_pixmap is not None:
            painter.drawPixmap(0, 0, self._frozen_pixmap)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            try:
                self._rubber_band.hide()
            except Exception:
                pass
            self.snip_cancelled.emit()
            self.close()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._origin = event.pos()
        self._rubber_band.setGeometry(QRect(self._origin, self._origin))
        self._rubber_band.show()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        rect = QRect(self._origin, event.pos()).normalized()
        self._rubber_band.setGeometry(rect)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        rect = QRect(self._origin, event.pos()).normalized()
        self._origin = None
        try:
            self._rubber_band.hide()
        except Exception:
            pass

        if rect.width() < 5 or rect.height() < 5:
            self.snip_cancelled.emit()
            self.close()
            return

        try:
            cropped = self._frozen_pixmap.copy(rect)
            self.snip_done.emit(cropped, rect)
        except Exception:
            self.snip_cancelled.emit()
        self.close()


class TriggerConfigDialog(ModernDialog):
    """Dialog for configuring image-based triggers"""
    
    def __init__(self, parent, is_absence_trigger=False, config=None):
        title = _("Absence Trigger Configuration") if is_absence_trigger else _("Trigger Configuration")
        super().__init__(parent, title=title, help_topic="conditional-dialog")
        self.is_absence_trigger = is_absence_trigger
        self.config = config
        self.setModal(True)
        self.resize(400, 200)
        self._snip_overlay = None
        self._main_window = None
        self._capture_screen_geo = None
        self._prev_modality = None
        self._capture_original_geo = None
        self._capture_in_progress = False
        self.setup_ui()
        if self.config:
            self.load_config()
    
    def load_config(self):
        """Load existing configuration"""
        self.image_path_entry.setText(self.config.get('image_path', ''))
        self.confidence_spin.setValue(self.config.get('confidence', 0.8))
        self.timeout_spin.setValue(int(self.config.get('timeout', 10 if self.is_absence_trigger else 0)))

    def setup_ui(self):
        """Setup the UI"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        
        # Image path
        image_layout = QHBoxLayout()
        image_layout.addWidget(QLabel(_("Image Path:")))
        self.image_path_entry = QLineEdit()
        image_layout.addWidget(self.image_path_entry)

        capture_btn = QPushButton(_("Capture..."))
        capture_btn.clicked.connect(self.capture_image)
        image_layout.addWidget(capture_btn)
        
        browse_btn = QPushButton(_("Browse..."))
        browse_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(browse_btn)
        layout.addLayout(image_layout)
        
        # Confidence
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel(_("Confidence:")))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        conf_layout.addWidget(self.confidence_spin)
        conf_layout.addStretch()
        layout.addLayout(conf_layout)
        
        # Timeout (optional for presence, required for absence)
        timeout_layout = QHBoxLayout()
        timeout_label = _("Timeout (s):") if not self.is_absence_trigger else _("Timeout (s) - Required:")
        timeout_layout.addWidget(QLabel(timeout_label))
        self.timeout_spin = QSpinBox()
        
        if self.is_absence_trigger:
            # For absence triggers, enforce minimum timeout to prevent freezing
            self.timeout_spin.setRange(1, 300)
            self.timeout_spin.setValue(10)
        else:
            # For presence triggers, allow no timeout (infinite wait)
            self.timeout_spin.setRange(0, 300)
            self.timeout_spin.setValue(0)
            self.timeout_spin.setSpecialValueText(_("No timeout"))
        
        timeout_layout.addWidget(self.timeout_spin)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton(_("OK"))
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def on_wait_type_changed(self, wait_type):
        """Handle wait type change"""
        if wait_type == "ocr":
            self.image_widget.hide()
            self.wait_time_widget.hide()
            self.ocr_widget.show()
        elif wait_type == "wait_time":
            self.image_widget.hide()
            self.ocr_widget.hide()
            self.wait_time_widget.show()
        else:  # presence or absence
            self.ocr_widget.hide()
            self.wait_time_widget.hide()
            self.image_widget.show()
    
    def browse_image(self):
        """Browse for image file"""
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self, _("Select Image File"), "", _("Image Files (*.png *.jpg *.jpeg *.bmp)")
        )
        if file_path:
            self.image_path_entry.setText(file_path)

    def capture_image(self):
        if self._capture_in_progress:
            return
        self._capture_in_progress = True
        self._main_window = _get_main_window_from_widget(self.parent())
        original_geo = None
        try:
            original_geo = self.geometry()
        except Exception:
            original_geo = None
        self._capture_original_geo = original_geo
        try:
            self.move(-10000, -10000)
            QApplication.processEvents()
        except Exception:
            pass
        try:
            if self._main_window is not None:
                self._main_window.showMinimized()
                QApplication.processEvents()
        except Exception:
            pass
        QTimer.singleShot(450, self._start_capture)

    def _start_capture(self):
        accepted_after_capture = False
        try:
            screen = _get_capture_screen()
            if screen is None:
                return
            geo = screen.geometry()
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            dlg = SnipCaptureDialog(frozen, geo, parent=None)
            dlg.raise_()
            dlg.activateWindow()
            if dlg.exec_() == QDialog.Accepted:
                cropped, global_rect = dlg.get_result()
                if cropped is None or cropped.isNull():
                    return
                sequences_dir = _find_sequences_folder_from_widget(self.parent())
                screenshots_dir = os.path.join(sequences_dir, 'screenshots')
                os.makedirs(screenshots_dir, exist_ok=True)
                ts = int(time.time() * 1000)
                file_path = os.path.join(screenshots_dir, f'trigger_{ts}.png')
                try:
                    cropped.save(file_path, 'PNG')
                except Exception:
                    cropped.save(file_path)
                self.image_path_entry.setText(file_path)
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                try:
                    accepted_after_capture = True
                    self.accept()
                except Exception:
                    accepted_after_capture = False
        finally:
            if not accepted_after_capture:
                try:
                    if self._capture_original_geo is not None:
                        self.setGeometry(self._capture_original_geo)
                    self.show()
                    self.raise_()
                    self.activateWindow()
                except Exception:
                    pass
            try:
                if self._main_window is not None:
                    self._main_window.showNormal()
                    self._main_window.raise_()
                    self._main_window.activateWindow()
            except Exception:
                pass
            self._capture_in_progress = False

    def _on_snip_cancelled(self):
        try:
            if self._prev_modality is not None:
                self.setWindowModality(self._prev_modality)
        except Exception:
            pass
        try:
            self.setModal(True)
        except Exception:
            pass
        try:
            if self._main_window is not None:
                self._main_window.showNormal()
                self._main_window.raise_()
                self._main_window.activateWindow()
        except Exception:
            pass
        try:
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _on_snip_done(self, pixmap, rect):
        try:
            sequences_dir = _find_sequences_folder_from_widget(self.parent())
            screenshots_dir = os.path.join(sequences_dir, 'screenshots')
            os.makedirs(screenshots_dir, exist_ok=True)
            ts = int(time.time() * 1000)
            file_path = os.path.join(screenshots_dir, f'trigger_{ts}.png')
            try:
                pixmap.save(file_path, 'PNG')
            except Exception:
                pixmap.save(file_path)
            self.image_path_entry.setText(file_path)
        finally:
            self._on_snip_cancelled()
    
    def get_config(self):
        """Get the trigger configuration"""
        config = {
            'image_path': self.image_path_entry.text(),
            'confidence': self.confidence_spin.value()
        }
        
        # Add timeout if specified
        if self.timeout_spin.value() > 0:
            config['timeout'] = self.timeout_spin.value()
        
        return config


class LayoutMatchConfigDialog(ModernDialog):
    def __init__(self, parent, config=None):
        super().__init__(parent, title=_("Layout Match Conditional"), help_topic="conditional-dialog")
        self.config = config
        self.setModal(True)
        self.resize(560, 380)  # Increased height for new fields
        self._target_x = None
        self._target_y = None
        self._target_w = None
        self._target_h = None
        self._snip_overlay = None
        self._main_window = None
        self._capture_screen_geo = None
        self._prev_modality = None
        self._capture_original_geo = None
        self._capture_in_progress = False
        self.setup_ui()
        if self.config:
            self.load_config()

    def setup_ui(self):
        layout = self.content_layout

        image_layout = QHBoxLayout()
        image_layout.addWidget(QLabel(_("Image Path:")))
        self.image_path_entry = QLineEdit()
        image_layout.addWidget(self.image_path_entry)
        capture_btn = QPushButton(_("Capture..."))
        capture_btn.clicked.connect(self.capture_image)
        image_layout.addWidget(capture_btn)
        browse_btn = QPushButton(_("Browse..."))
        browse_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(browse_btn)
        layout.addLayout(image_layout)

        self.capture_info_label = QLabel("")
        layout.addWidget(self.capture_info_label)

        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel(_("Confidence:")))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setValue(0.8)
        conf_layout.addWidget(self.confidence_spin)
        conf_layout.addStretch()
        layout.addLayout(conf_layout)

        tol_layout = QHBoxLayout()
        tol_layout.addWidget(QLabel(_("Position Tolerance (px):")))
        self.tolerance_spin = QSpinBox()
        self.tolerance_spin.setRange(0, 500)
        self.tolerance_spin.setValue(6)
        tol_layout.addWidget(self.tolerance_spin)
        tol_layout.addStretch()
        layout.addLayout(tol_layout)

        attempts_layout = QHBoxLayout()
        attempts_layout.addWidget(QLabel(_("Max Attempts:")))
        self.max_attempts_spin = QSpinBox()
        self.max_attempts_spin.setRange(1, 10000)
        self.max_attempts_spin.setValue(500)
        attempts_layout.addWidget(self.max_attempts_spin)
        attempts_layout.addStretch()
        layout.addLayout(attempts_layout)

        delay_layout = QHBoxLayout()
        delay_layout.addWidget(QLabel(_("Delay Between Attempts (s):")))
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.0, 10.0)
        self.delay_spin.setDecimals(3)
        self.delay_spin.setSingleStep(0.01)
        self.delay_spin.setValue(0.01)
        delay_layout.addWidget(self.delay_spin)
        delay_layout.addStretch()
        layout.addLayout(delay_layout)

        # Timeout (0 = disabled, search until found)
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel(_("Timeout (s) [0=disabled]:")))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0, 3600)
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setSingleStep(1.0)
        self.timeout_spin.setValue(0)  # Disabled by default
        timeout_layout.addWidget(self.timeout_spin)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)

        # Scroll direction
        scroll_layout = QHBoxLayout()
        scroll_layout.addWidget(QLabel(_("Scroll Direction:")))
        self.scroll_combo = QComboBox()
        self.scroll_combo.addItems([_("Up"), _("Down")])
        self.scroll_combo.setCurrentIndex(0)  # Up by default
        scroll_layout.addWidget(self.scroll_combo)
        scroll_layout.addStretch()
        layout.addLayout(scroll_layout)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        ok_btn = QPushButton(_("OK"))
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

    def load_config(self):
        if not self.config:
            return
        try:
            if hasattr(self, 'image_path_entry'):
                self.image_path_entry.setText(self.config.get('image_path', ''))
        except Exception:
            pass
        try:
            if hasattr(self, 'confidence_spin'):
                self.confidence_spin.setValue(float(self.config.get('confidence', 0.8)))
        except Exception:
            if hasattr(self, 'confidence_spin'):
                self.confidence_spin.setValue(0.8)
        try:
            if hasattr(self, 'tolerance_spin'):
                self.tolerance_spin.setValue(int(self.config.get('position_tolerance', 6)))
        except Exception:
            if hasattr(self, 'tolerance_spin'):
                self.tolerance_spin.setValue(6)
        try:
            if hasattr(self, 'max_attempts_spin'):
                max_attempts_val = int(self.config.get('max_attempts', 500)) if self.config.get('max_attempts') is not None else 500
                self.max_attempts_spin.setValue(max_attempts_val)
        except Exception:
            if hasattr(self, 'max_attempts_spin'):
                self.max_attempts_spin.setValue(500)
        try:
            if hasattr(self, 'delay_spin'):
                delay_val = float(self.config.get('delay_between_attempts', 0.01)) if self.config.get('delay_between_attempts') is not None else 0.01
                self.delay_spin.setValue(delay_val)
        except Exception:
            if hasattr(self, 'delay_spin'):
                self.delay_spin.setValue(0.01)
        try:
            if hasattr(self, 'timeout_spin'):
                timeout_val = float(self.config.get('timeout', 0)) if self.config.get('timeout') is not None else 0
                self.timeout_spin.setValue(timeout_val)
        except Exception:
            if hasattr(self, 'timeout_spin'):
                self.timeout_spin.setValue(0)
        try:
            if hasattr(self, 'scroll_combo'):
                scroll_dir = int(self.config.get('scroll_direction', 1)) if self.config.get('scroll_direction') is not None else 1
                self.scroll_combo.setCurrentIndex(0 if scroll_dir >= 0 else 1)
        except Exception:
            if hasattr(self, 'scroll_combo'):
                self.scroll_combo.setCurrentIndex(0)

        try:
            self._target_x = int(self.config.get('target_x')) if self.config.get('target_x') is not None else None
        except Exception:
            self._target_x = None
        try:
            self._target_y = int(self.config.get('target_y')) if self.config.get('target_y') is not None else None
        except Exception:
            self._target_y = None
        try:
            self._target_w = int(self.config.get('target_w')) if self.config.get('target_w') is not None else None
        except Exception:
            self._target_w = None
        try:
            self._target_h = int(self.config.get('target_h')) if self.config.get('target_h') is not None else None
        except Exception:
            self._target_h = None
        try:
            self._update_capture_label()
        except Exception:
            pass

    def browse_image(self):
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self, _("Select Image File"), "", _("Image Files (*.png *.jpg *.jpeg *.bmp)")
        )
        if file_path:
            self.image_path_entry.setText(file_path)

    def _find_sequences_folder(self):
        return _find_sequences_folder_from_widget(self.parent())

    def _update_capture_label(self):
        if self._target_x is None or self._target_y is None or self._target_w is None or self._target_h is None:
            self.capture_info_label.setText(_("No capture coordinates assigned."))
            return
        self.capture_info_label.setText(
            f"{_('Captured region:')} x={self._target_x}, y={self._target_y}, w={self._target_w}, h={self._target_h}"
        )

    def capture_image(self):
        if self._capture_in_progress:
            return
        self._capture_in_progress = True
        self._main_window = _get_main_window_from_widget(self.parent())
        original_geo = None
        try:
            original_geo = self.geometry()
        except Exception:
            original_geo = None
        self._capture_original_geo = original_geo
        try:
            self.move(-10000, -10000)
            QApplication.processEvents()
        except Exception:
            pass
        try:
            if self._main_window is not None:
                self._main_window.showMinimized()
                QApplication.processEvents()
        except Exception:
            pass
        QTimer.singleShot(450, self._start_capture)

    def _start_capture(self):
        accepted_after_capture = False
        try:
            screen = _get_capture_screen()
            if screen is None:
                return
            geo = screen.geometry()
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            dlg = SnipCaptureDialog(frozen, geo, parent=None)
            dlg.raise_()
            dlg.activateWindow()
            if dlg.exec_() == QDialog.Accepted:
                cropped, global_rect = dlg.get_result()
                if cropped is None or cropped.isNull() or global_rect is None:
                    return
                sequences_dir = self._find_sequences_folder()
                screenshots_dir = os.path.join(sequences_dir, 'screenshots')
                os.makedirs(screenshots_dir, exist_ok=True)
                ts = int(time.time() * 1000)
                file_path = os.path.join(screenshots_dir, f'layout_match_{ts}.png')
                try:
                    cropped.save(file_path, 'PNG')
                except Exception:
                    cropped.save(file_path)
                self.image_path_entry.setText(file_path)

                self._target_w = int(global_rect.width())
                self._target_h = int(global_rect.height())
                self._target_x = int(global_rect.x() + (self._target_w // 2))
                self._target_y = int(global_rect.y() + (self._target_h // 2))
                self._update_capture_label()
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                try:
                    self.accept()
                except Exception:
                    pass
        finally:
            try:
                if self._capture_original_geo is not None:
                    self.setGeometry(self._capture_original_geo)
                self.show()
                self.raise_()
                self.activateWindow()
            except Exception:
                pass
            try:
                if self._main_window is not None:
                    self._main_window.showNormal()
                    self._main_window.raise_()
                    self._main_window.activateWindow()
            except Exception:
                pass
            self._capture_in_progress = False

    def _on_snip_cancelled(self):
        try:
            if self._prev_modality is not None:
                self.setWindowModality(self._prev_modality)
        except Exception:
            pass
        try:
            self.setModal(True)
        except Exception:
            pass
        try:
            if self._main_window is not None:
                self._main_window.showNormal()
                self._main_window.raise_()
                self._main_window.activateWindow()
        except Exception:
            pass
        try:
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _on_snip_done(self, pixmap, rect):
        try:
            sequences_dir = self._find_sequences_folder()
            screenshots_dir = os.path.join(sequences_dir, 'screenshots')
            os.makedirs(screenshots_dir, exist_ok=True)
            ts = int(time.time() * 1000)
            file_path = os.path.join(screenshots_dir, f'layout_match_{ts}.png')
            try:
                pixmap.save(file_path, 'PNG')
            except Exception:
                pixmap.save(file_path)
            self.image_path_entry.setText(file_path)

            self._target_w = int(rect.width())
            self._target_h = int(rect.height())
            geo = self._capture_screen_geo or QRect(0, 0, 0, 0)
            self._target_x = int(geo.x() + rect.x() + (self._target_w // 2))
            self._target_y = int(geo.y() + rect.y() + (self._target_h // 2))
            self._update_capture_label()
        finally:
            self._on_snip_cancelled()

    def get_config(self):
        try:
            config = {
                'image_path': self.image_path_entry.text() if hasattr(self, 'image_path_entry') else '',
                'confidence': float(self.confidence_spin.value()) if hasattr(self, 'confidence_spin') else 0.8,
                'position_tolerance': int(self.tolerance_spin.value()) if hasattr(self, 'tolerance_spin') else 6,
                'max_attempts': int(self.max_attempts_spin.value()) if hasattr(self, 'max_attempts_spin') else 500,
                'delay_between_attempts': float(self.delay_spin.value()) if hasattr(self, 'delay_spin') else 0.01,
                'timeout': float(self.timeout_spin.value()) if hasattr(self, 'timeout_spin') else 0,
                'scroll_direction': 1 if (hasattr(self, 'scroll_combo') and self.scroll_combo.currentIndex() == 0) else -1,
            }
            if self._target_x is not None and self._target_y is not None and self._target_w is not None and self._target_h is not None:
                config['target_x'] = int(self._target_x)
                config['target_y'] = int(self._target_y)
                config['target_w'] = int(self._target_w)
                config['target_h'] = int(self._target_h)
            return config
        except Exception as e:
            # Return minimal config if there's an error
            return {
                'image_path': '',
                'confidence': 0.8,
                'position_tolerance': 6,
                'max_attempts': 500,
                'delay_between_attempts': 0.01,
                'timeout': 0,
                'scroll_direction': 1,
            }


class OCRTriggerConfigDialog(ModernDialog):
    """Dialog for configuring OCR-based triggers"""
    
    def __init__(self, parent, config=None):
        super().__init__(parent, title=_("OCR Trigger Configuration"), help_topic="conditional-dialog")
        self.config = config
        self.setModal(True)
        self.resize(450, 350)
        self.setup_ui()
        if self.config:
            self.load_config()

    def load_config(self):
        """Load existing configuration"""
        self.target_text_entry.setText(self.config.get('target_text', ''))
        self.confidence_spin.setValue(self.config.get('confidence', 0.8))
        self.case_sensitive_check.setChecked(self.config.get('case_sensitive', False))
    
    def setup_ui(self):
        """Setup the UI"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        
        # Target text
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel(_("Target Text:")))
        self.target_text_entry = QLineEdit()
        text_layout.addWidget(self.target_text_entry)
        layout.addLayout(text_layout)
        
        # Confidence
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel(_("Confidence:")))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        conf_layout.addWidget(self.confidence_spin)
        conf_layout.addStretch()
        layout.addLayout(conf_layout)
        
        # Case sensitive
        case_layout = QHBoxLayout()
        self.case_sensitive_check = ModernToggle(text=_("Case Sensitive:"))
        case_layout.addWidget(self.case_sensitive_check)
        case_layout.addStretch()
        layout.addLayout(case_layout)
        
        # Note: Language and region removed - OCR now uses full screen with default English
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton(_("OK"))
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def get_config(self):
        """Get the OCR trigger configuration"""
        config = {
            'target_text': self.target_text_entry.text(),
            'confidence': self.confidence_spin.value(),
            'case_sensitive': self.case_sensitive_check.isChecked()
        }
        
        return config


class WaitConditionDialog(ModernDialog):
    """Dialog for configuring wait conditions"""
    
    def __init__(self, parent, config=None):
        super().__init__(parent, title=_("Wait Condition"), help_topic="conditional-dialog")
        self.config = config
        self.setModal(True)
        self.resize(400, 250)
        self._capture_in_progress = False
        self._main_window = None
        self._capture_original_geo = None
        self.setup_ui()
        if self.config:
            self.load_config()

    def load_config(self):
        """Load existing configuration"""
        wait_type = self.config.get('type', 'presence')
        self.type_combo.setCurrentText(wait_type)
        
        if wait_type == 'ocr':
            self.target_text_entry.setText(self.config.get('target_text', ''))
            self.case_sensitive_check.setChecked(self.config.get('case_sensitive', True))
            self.confidence_spin.setValue(self.config.get('confidence', 0.8))
            self.timeout_spin.setValue(self.config.get('timeout', 30))
        elif wait_type == 'wait_time':
            self.wait_duration_spin.setValue(self.config.get('duration', 5.0))
        else: # presence or absence
            self.image_path_entry.setText(self.config.get('image_path', ''))
            self.confidence_spin.setValue(self.config.get('confidence', 0.8))
            self.timeout_spin.setValue(self.config.get('timeout', 30))
    
    def setup_ui(self):
        """Setup the UI"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        
        # Wait type selection
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel(_("Wait Type:")))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["presence", "absence", "ocr", "wait_time"])
        self.type_combo.currentTextChanged.connect(self.on_wait_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)
        
        # Image path (for presence/absence)
        self.image_widget = QWidget()
        image_layout = QHBoxLayout(self.image_widget)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.addWidget(QLabel(_("Image Path:")))
        self.image_path_entry = QLineEdit()
        image_layout.addWidget(self.image_path_entry)

        capture_btn = QPushButton(_("Capture..."))
        capture_btn.clicked.connect(self.capture_image)
        image_layout.addWidget(capture_btn)
        
        browse_btn = QPushButton(_("Browse..."))
        browse_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(browse_btn)
        layout.addWidget(self.image_widget)
        
        # OCR settings (for OCR)
        self.ocr_widget = QWidget()
        ocr_layout = QVBoxLayout(self.ocr_widget)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel(_("Target Text:")))
        self.target_text_entry = QLineEdit()
        text_layout.addWidget(self.target_text_entry)
        ocr_layout.addLayout(text_layout)
        
        ocr_params_layout = QHBoxLayout()
        ocr_params_layout.addWidget(QLabel(_("Case Sensitive:")))
        self.case_sensitive_check = ModernToggle()
        self.case_sensitive_check.setChecked(True)
        ocr_params_layout.addWidget(self.case_sensitive_check)
        
        # Language removed - OCR now uses default English
        ocr_params_layout.addStretch()
        ocr_layout.addLayout(ocr_params_layout)
        
        layout.addWidget(self.ocr_widget)
        self.ocr_widget.hide()  # Initially hidden
        
        # Wait time settings (for wait_time)
        self.wait_time_widget = QWidget()
        wait_time_layout = QHBoxLayout(self.wait_time_widget)
        wait_time_layout.setContentsMargins(0, 0, 0, 0)
        wait_time_layout.addWidget(QLabel(_("Wait Duration (s):")))
        self.wait_duration_spin = QDoubleSpinBox()
        self.wait_duration_spin.setRange(0.1, 300.0)
        self.wait_duration_spin.setSingleStep(0.1)
        self.wait_duration_spin.setValue(5.0)
        wait_time_layout.addWidget(self.wait_duration_spin)
        wait_time_layout.addStretch()
        layout.addWidget(self.wait_time_widget)
        self.wait_time_widget.hide()  # Initially hidden
        
        # Confidence
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel(_("Confidence:")))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        conf_layout.addWidget(self.confidence_spin)
        conf_layout.addStretch()
        layout.addLayout(conf_layout)
        
        # Timeout
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel(_("Timeout (s):")))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(30)
        timeout_layout.addWidget(self.timeout_spin)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton(_("OK"))
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def browse_image(self):
        """Browse for image file"""
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self, _("Select Image File"), "", _("Image Files (*.png *.jpg *.jpeg *.bmp)")
        )
        if file_path:
            self.image_path_entry.setText(file_path)

    def capture_image(self):
        if self._capture_in_progress:
            return
        if self.type_combo.currentText() == 'ocr':
            return
        self._capture_in_progress = True
        self._main_window = _get_main_window_from_widget(self.parent())
        try:
            self._capture_original_geo = self.geometry()
        except Exception:
            self._capture_original_geo = None
        try:
            self.move(-10000, -10000)
            QApplication.processEvents()
        except Exception:
            pass
        try:
            if self._main_window is not None:
                self._main_window.showMinimized()
                QApplication.processEvents()
        except Exception:
            pass
        QTimer.singleShot(450, self._start_capture)

    def _start_capture(self):
        try:
            screen = _get_capture_screen()
            if screen is None:
                return
            geo = screen.geometry()
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            dlg = SnipCaptureDialog(frozen, geo, parent=None)
            dlg.raise_()
            dlg.activateWindow()
            if dlg.exec_() == QDialog.Accepted:
                cropped, global_rect = dlg.get_result()
                if cropped is None or cropped.isNull():
                    return
                sequences_dir = _find_sequences_folder_from_widget(self.parent())
                screenshots_dir = os.path.join(sequences_dir, 'screenshots')
                os.makedirs(screenshots_dir, exist_ok=True)
                ts = int(time.time() * 1000)
                file_path = os.path.join(screenshots_dir, f'wait_condition_{ts}.png')
                try:
                    cropped.save(file_path, 'PNG')
                except Exception:
                    cropped.save(file_path)
                self.image_path_entry.setText(file_path)
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                try:
                    accepted_after_capture = True
                    self.accept()
                except Exception:
                    accepted_after_capture = False
        finally:
            if not accepted_after_capture:
                try:
                    if self._capture_original_geo is not None:
                        self.setGeometry(self._capture_original_geo)
                    self.show()
                    self.raise_()
                    self.activateWindow()
                except Exception:
                    pass
            try:
                if self._main_window is not None:
                    self._main_window.showNormal()
                    self._main_window.raise_()
                    self._main_window.activateWindow()
            except Exception:
                pass
            self._capture_in_progress = False
    
    def get_config(self):
        """Get the wait condition configuration"""
        wait_type = self.type_combo.currentText()
        
        if wait_type == "ocr":
            return {
                'type': 'ocr',
                'target_text': self.target_text_entry.text(),
                'confidence': self.confidence_spin.value(),
                'case_sensitive': self.case_sensitive_check.isChecked(),
                'timeout': self.timeout_spin.value()
            }
        elif wait_type == "wait_time":
            return {
                'type': 'wait_time',
                'duration': self.wait_duration_spin.value()
            }
        else:  # presence or absence
            return {
                'type': wait_type,
                'image_path': self.image_path_entry.text(),
                'confidence': self.confidence_spin.value(),
                'timeout': self.timeout_spin.value()
            }


class ConditionalLoopDialog(ModernDialog):
    """Dialog for configuring conditional loops"""
    
    def __init__(self, parent, config=None):
        super().__init__(parent, title=_("Conditional Loop"), help_topic="conditional-dialog")
        self.config = config
        self.setModal(True)
        self.resize(500, 350)
        self._capture_in_progress = False
        self._main_window = None
        self._capture_original_geo = None
        self.setup_ui()
        if self.config:
            self.load_config()

    def load_config(self):
        """Load existing configuration"""
        self.type_combo.setCurrentText(self.config.get('type', 'while_present'))
        self.sequence_file_entry.setText(self.config.get('sequence_file', ''))
        self.max_iterations_spin.setValue(self.config.get('max_iterations', 10))
        self.iteration_delay_spin.setValue(self.config.get('iteration_delay', 1.0))
        
        condition = self.config.get('condition', {})
        trigger_type = condition.get('trigger_type', 'presence')
        
        if trigger_type == 'ocr':
            self.condition_method_combo.setCurrentText('ocr')
            self.target_text_entry.setText(condition.get('target_text', ''))
            self.case_sensitive_check.setChecked(condition.get('case_sensitive', True))
            self.confidence_spin.setValue(condition.get('confidence', 0.8))
        else:
            self.condition_method_combo.setCurrentText('image')
            self.image_path_entry.setText(condition.get('image_path', ''))
            self.confidence_spin.setValue(condition.get('confidence', 0.8))

    def setup_ui(self):
        """Setup the UI"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        
        # Condition type selection
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel(_("Loop Type:")))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["while_present", "until_present"])
        self.type_combo.currentTextChanged.connect(self.on_loop_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)
        
        # Sequence file
        seq_layout = QHBoxLayout()
        seq_layout.addWidget(QLabel(_("Sequence File:")))
        self.sequence_file_entry = QLineEdit()
        seq_layout.addWidget(self.sequence_file_entry)
        
        browse_seq_btn = QPushButton(_("Browse..."))
        browse_seq_btn.clicked.connect(self.browse_sequence)
        seq_layout.addWidget(browse_seq_btn)
        layout.addLayout(seq_layout)
        
        # Condition method (image or OCR)
        cond_method_layout = QHBoxLayout()
        cond_method_layout.addWidget(QLabel(_("Condition Method:")))
        self.condition_method_combo = QComboBox()
        self.condition_method_combo.addItems(["image", "ocr"])
        self.condition_method_combo.currentTextChanged.connect(self.on_condition_method_changed)
        cond_method_layout.addWidget(self.condition_method_combo)
        cond_method_layout.addStretch()
        layout.addLayout(cond_method_layout)
        
        # Condition image (for presence/absence)
        self.image_widget = QWidget()
        image_layout = QHBoxLayout(self.image_widget)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.addWidget(QLabel(_("Condition Image:")))
        self.image_path_entry = QLineEdit()
        image_layout.addWidget(self.image_path_entry)

        capture_img_btn = QPushButton(_("Capture..."))
        capture_img_btn.clicked.connect(self.capture_image)
        image_layout.addWidget(capture_img_btn)
        
        browse_img_btn = QPushButton(_("Browse..."))
        browse_img_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(browse_img_btn)
        layout.addWidget(self.image_widget)
        
        # OCR condition (for OCR)
        self.ocr_widget = QWidget()
        ocr_layout = QVBoxLayout(self.ocr_widget)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel(_("Target Text:")))
        self.target_text_entry = QLineEdit()
        text_layout.addWidget(self.target_text_entry)
        ocr_layout.addLayout(text_layout)
        
        ocr_params_layout = QHBoxLayout()
        ocr_params_layout.addWidget(QLabel(_("Case Sensitive:")))
        self.case_sensitive_check = ModernToggle()
        self.case_sensitive_check.setChecked(True)
        ocr_params_layout.addWidget(self.case_sensitive_check)
        
        # Language removed - OCR now uses default English
        ocr_params_layout.addStretch()
        ocr_layout.addLayout(ocr_params_layout)
        
        layout.addWidget(self.ocr_widget)
        self.ocr_widget.hide()  # Initially hidden
        
        # Parameters
        params_layout = QGridLayout()
        
        params_layout.addWidget(QLabel(_("Max Iterations:")), 0, 0)
        self.max_iterations_spin = QSpinBox()
        self.max_iterations_spin.setRange(1, 100)
        self.max_iterations_spin.setValue(10)
        params_layout.addWidget(self.max_iterations_spin, 0, 1)
        
        params_layout.addWidget(QLabel(_("Iteration Delay (s):")), 1, 0)
        self.iteration_delay_spin = QDoubleSpinBox()
        self.iteration_delay_spin.setRange(0.1, 60.0)
        self.iteration_delay_spin.setSingleStep(0.1)
        self.iteration_delay_spin.setValue(1.0)
        params_layout.addWidget(self.iteration_delay_spin, 1, 1)
        
        params_layout.addWidget(QLabel(_("Confidence:")), 2, 0)
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        params_layout.addWidget(self.confidence_spin, 2, 1)
        
        layout.addLayout(params_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton(_("OK"))
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def on_loop_type_changed(self, loop_type):
        """Handle loop type change"""
        # Update UI labels based on loop type
        # All remaining loop types are presence-based (while_present, until_present)
        # No special handling needed as we only support presence-based loops now
        pass
    
    def on_condition_method_changed(self, condition_method):
        """Handle condition method change"""
        if condition_method == "ocr":
            self.image_widget.hide()
            self.ocr_widget.show()
        else:
            self.ocr_widget.hide()
            self.image_widget.show()
    
    def browse_sequence(self):
        """Browse for sequence file"""
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self, _("Select Sequence File"), "", _("JSON Files (*.json)")
        )
        if file_path:
            self.sequence_file_entry.setText(file_path)
    
    def browse_image(self):
        """Browse for image file"""
        file_path, selected_filter = QFileDialog.getOpenFileName(
            self, _("Select Image File"), "", _("Image Files (*.png *.jpg *.jpeg *.bmp)")
        )
        if file_path:
            self.image_path_entry.setText(file_path)

    def capture_image(self):
        if self._capture_in_progress:
            return
        if self.condition_method_combo.currentText() == 'ocr':
            return
        self._capture_in_progress = True
        self._main_window = _get_main_window_from_widget(self.parent())
        try:
            self._capture_original_geo = self.geometry()
        except Exception:
            self._capture_original_geo = None
        try:
            self.move(-10000, -10000)
            QApplication.processEvents()
        except Exception:
            pass
        try:
            if self._main_window is not None:
                self._main_window.showMinimized()
                QApplication.processEvents()
        except Exception:
            pass
        QTimer.singleShot(450, self._start_capture)

    def _start_capture(self):
        accepted_after_capture = False
        try:
            screen = _get_capture_screen()
            if screen is None:
                return
            geo = screen.geometry()
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            dlg = SnipCaptureDialog(frozen, geo, parent=None)
            dlg.raise_()
            dlg.activateWindow()
            if dlg.exec_() == QDialog.Accepted:
                cropped, global_rect = dlg.get_result()
                if cropped is None or cropped.isNull():
                    return
                sequences_dir = _find_sequences_folder_from_widget(self.parent())
                screenshots_dir = os.path.join(sequences_dir, 'screenshots')
                os.makedirs(screenshots_dir, exist_ok=True)
                ts = int(time.time() * 1000)
                file_path = os.path.join(screenshots_dir, f'loop_condition_{ts}.png')
                try:
                    cropped.save(file_path, 'PNG')
                except Exception:
                    cropped.save(file_path)
                self.image_path_entry.setText(file_path)
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
                try:
                    accepted_after_capture = True
                    self.accept()
                except Exception:
                    accepted_after_capture = False
        finally:
            if not accepted_after_capture:
                try:
                    if self._capture_original_geo is not None:
                        self.setGeometry(self._capture_original_geo)
                    self.show()
                    self.raise_()
                    self.activateWindow()
                except Exception:
                    pass
            try:
                if self._main_window is not None:
                    self._main_window.showNormal()
                    self._main_window.raise_()
                    self._main_window.activateWindow()
            except Exception:
                pass
            self._capture_in_progress = False
    
    def get_config(self):
        """Get the conditional loop configuration"""
        loop_type = self.type_combo.currentText()
        condition_method = self.condition_method_combo.currentText()
        
        # Create unified condition based on loop type and method
        if condition_method == "ocr":
            # For OCR-based conditions
            condition = {
                'trigger_type': 'ocr',
                'target_text': self.target_text_entry.text(),
                'confidence': self.confidence_spin.value(),
                'case_sensitive': self.case_sensitive_check.isChecked()
            }
        else:
            # For image-based conditions, all remaining loop types are presence-based
            condition = {
                'trigger_type': 'presence',
                'image_path': self.image_path_entry.text(),
                'confidence': self.confidence_spin.value()
            }
        
        return {
            'type': loop_type,
            'sequence_file': self.sequence_file_entry.text(),
            'max_iterations': self.max_iterations_spin.value(),
            'iteration_delay': self.iteration_delay_spin.value(),
            'condition': condition
        }
