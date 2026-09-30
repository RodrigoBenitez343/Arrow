"""Trigger-related dialog classes for the LoOper application.

This module contains dialog classes for configuring various types of triggers:
- TriggerConfigDialog: Basic image-based trigger configuration
- OCRTriggerConfigDialog: OCR-based trigger configuration
- WaitConditionDialog: Wait condition configuration
- ConditionalLoopDialog: Conditional loop configuration
"""

import os
import time
import json
import logging
import threading
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

logger = logging.getLogger(__name__)


def _get_main_window_from_widget(widget):
    try:
        w = widget
        while w is not None:
            parent = w.parent()
            if parent is None:
                # If the topmost parent is a QDialog (not the real main window),
                # search for the actual QMainWindow among top-level widgets.
                from PyQt5.QtWidgets import QMainWindow, QDialog
                if isinstance(w, QDialog):
                    for tl in QApplication.topLevelWidgets():
                        if isinstance(tl, QMainWindow):
                            return tl
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
    # Last resort: search all top-level widgets for a QMainWindow
    try:
        from PyQt5.QtWidgets import QMainWindow
        for tl in QApplication.topLevelWidgets():
            if isinstance(tl, QMainWindow):
                return tl
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


def _get_virtual_desktop_geometry():
    """Get the full virtual desktop geometry across all monitors.
    
    This returns the bounding rectangle that encompasses all screens.
    Used to properly calculate coordinates when capturing the virtual desktop.
    """
    try:
        # Get all screens
        screens = QGuiApplication.screens()
        if not screens:
            primary = QGuiApplication.primaryScreen()
            return primary.geometry() if primary else QRect(0, 0, 0, 0)
        
        # Calculate the bounding rectangle of all screens
        min_x = float('inf')
        min_y = float('inf')
        max_x = float('-inf')
        max_y = float('-inf')
        
        for screen in screens:
            geo = screen.geometry()
            # Handle negative positions (monitors to the left/up of primary)
            min_x = min(min_x, geo.left())
            min_y = min(min_y, geo.top())
            max_x = max(max_x, geo.right())
            max_y = max(max_y, geo.bottom())
        
        if min_x == float('inf'):
            primary = QGuiApplication.primaryScreen()
            return primary.geometry() if primary else QRect(0, 0, 0, 0)
        
        return QRect(int(min_x), int(min_y), int(max_x - min_x), int(max_y - min_y))
    except Exception:
        primary = QGuiApplication.primaryScreen()
        return primary.geometry() if primary else QRect(0, 0, 0, 0)


def _get_screen_info_for_capture():
    """Get screen info needed for proper coordinate calculation.
    
    Returns:
        tuple: (screen, geometry, device_pixel_ratio)
        - screen: The QScreen to use for capture
        - geometry: The geometry to use for coordinate calculation (virtual desktop)
        - dpr: Device pixel ratio for the screen
    """
    screen = _get_capture_screen()
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    
    if screen is None:
        return None, QRect(0, 0, 0, 0), 1.0
    
    # Get device pixel ratio for this screen
    try:
        dpr = float(screen.devicePixelRatio())
    except Exception:
        dpr = 1.0
    
    # Get virtual desktop geometry for proper coordinate calculation
    # This ensures coordinates are relative to the full virtual desktop
    virtual_geo = _get_virtual_desktop_geometry()
    
    return screen, virtual_geo, dpr


def _pixmap_to_base64(pixmap):
    """Convert a QPixmap to a base64-encoded PNG string."""
    if pixmap is None or pixmap.isNull():
        return ''
    try:
        from io import BytesIO
        buffer = BytesIO()
        pixmap.save(buffer, 'PNG')
        import base64
        return base64.b64encode(buffer.getvalue()).decode('ascii')
    except Exception:
        return ''


class SnipCaptureDialog(QDialog):
    def __init__(self, frozen_pixmap, screen_geometry, dpr=1.0, parent=None):
        super().__init__(parent)
        self._frozen_pixmap = frozen_pixmap
        self._screen_geometry = screen_geometry
        self._dpr = dpr  # Device pixel ratio for coordinate scaling
        self._origin = None
        self._current = None
        self._selected_rect = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowModality(Qt.ApplicationModal)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        
        # Size dialog based on frozen pixmap's LOGICAL dimensions to ensure coordinates align
        # frozen_pixmap is at device resolution, so we divide by dpr to get logical size
        try:
            if frozen_pixmap is not None and not frozen_pixmap.isNull():
                pix_w = frozen_pixmap.width()
                pix_h = frozen_pixmap.height()
                # Calculate logical dimensions (what the user sees)
                if dpr > 0:
                    logical_w = int(pix_w / dpr)
                    logical_h = int(pix_h / dpr)
                else:
                    logical_w = pix_w
                    logical_h = pix_h
                # Position at screen geometry's top-left, use logical dimensions for size
                geo = screen_geometry or QRect(0, 0, 1920, 1080)
                self.setGeometry(geo.x(), geo.y(), logical_w, logical_h)
            else:
                self.setGeometry(screen_geometry)
        except Exception:
            self.setGeometry(screen_geometry)

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
        if self._frozen_pixmap is not None and not self._frozen_pixmap.isNull():
            # frozen_pixmap is at device resolution, dialog is at logical pixels
            # Scale frozen pixmap to fit the dialog (now sized to logical dimensions)
            pix_w = self._frozen_pixmap.width()
            pix_h = self._frozen_pixmap.height()
            dialog_size = self.size()
            
            # Calculate scaling: device pixmap size / dialog logical size
            if dialog_size.width() > 0 and dialog_size.height() > 0:
                scale_x = pix_w / dialog_size.width()
                scale_y = pix_h / dialog_size.height()
            else:
                scale_x, scale_y = 1.0, 1.0
            
            # Draw frozen pixmap scaled to dialog size
            painter.drawPixmap(self.rect(), self._frozen_pixmap)

        overlay = QColor(0, 0, 0, 90)
        painter.fillRect(self.rect(), overlay)

        rect = None
        if self._origin is not None and self._current is not None:
            rect = QRect(self._origin, self._current).normalized()
        elif self._selected_rect is not None:
            rect = self._selected_rect

        if rect is not None and rect.width() > 0 and rect.height() > 0 and self._frozen_pixmap is not None:
            # Scale selection rect from logical pixels to device pixels for source rect
            pix_w = self._frozen_pixmap.width()
            pix_h = self._frozen_pixmap.height()
            dialog_size = self.size()
            
            if dialog_size.width() > 0 and dialog_size.height() > 0:
                scale_x = pix_w / dialog_size.width()
                scale_y = pix_h / dialog_size.height()
            else:
                scale_x, scale_y = 1.0, 1.0
            
            # Source rect in device pixels
            src_rect = QRect(
                int(rect.x() * scale_x),
                int(rect.y() * scale_y),
                int(rect.width() * scale_x),
                int(rect.height() * scale_y)
            )
            painter.drawPixmap(rect, self._frozen_pixmap, src_rect)
            pen = QPen(QColor(0, 170, 255, 255))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawRect(rect)

    def get_result(self):
        if self._selected_rect is None or self._frozen_pixmap is None:
            return None, None
        
        # The frozen pixmap is captured at device pixel resolution
        # The dialog is sized to frozen pixmap's logical dimensions
        # Mouse events are in dialog coordinates (logical pixels)
        # We need to scale the selection rect from dialog size to device pixmap size
        
        pix_w = self._frozen_pixmap.width()
        pix_h = self._frozen_pixmap.height()
        dialog_size = self.size()
        
        # Calculate scaling factor: device pixmap size / dialog logical size
        if dialog_size.width() > 0 and dialog_size.height() > 0:
            scale_x = pix_w / dialog_size.width()
            scale_y = pix_h / dialog_size.height()
        else:
            scale_x, scale_y = 1.0, 1.0
        
        # Scale selection rect to device pixels for cropping from frozen pixmap
        scaled_rect = QRect(
            int(self._selected_rect.x() * scale_x),
            int(self._selected_rect.y() * scale_y),
            int(self._selected_rect.width() * scale_x),
            int(self._selected_rect.height() * scale_y)
        )
        
        try:
            cropped = self._frozen_pixmap.copy(scaled_rect)
        except Exception:
            cropped = None
        
        # For global coordinates: the selection is relative to the dialog
        # which is positioned at the screen's position (geo.x(), geo.y())
        # The dialog size now matches the frozen pixmap's logical size
        # So we scale the dialog position offset by the same scale factor
        geo = self._screen_geometry or QRect(0, 0, 0, 0)
        
        # Global position in device pixels:
        # - dialog position in logical pixels = geo.x(), geo.y()
        # - dialog size in logical pixels = dialog_size.width(), height()
        # - frozen pixmap is at device resolution
        # - scale selection by scale_x, scale_y to get device pixel position
        global_rect = QRect(
            int(geo.x() * scale_x + self._selected_rect.x() * scale_x),
            int(geo.y() * scale_y + self._selected_rect.y() * scale_y),
            int(self._selected_rect.width() * scale_x),
            int(self._selected_rect.height() * scale_y)
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
        self._captured_image_data = ''
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
            screen, virtual_geo, dpr = _get_screen_info_for_capture()
            if screen is None:
                return
            # Get the geometry of the screen where capture will be shown
            geo = screen.geometry()
            # grabWindow(0) captures the full virtual desktop at device pixel resolution
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            # Use virtual desktop geometry for coordinate calculation
            # This ensures coordinates are relative to the full virtual desktop
            dlg = SnipCaptureDialog(frozen, virtual_geo, dpr, parent=None)
            # Position the dialog over the screen where the cursor is
            dlg.setGeometry(geo)
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
                self._captured_image_data = _pixmap_to_base64(cropped)
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
            'image_data': self._captured_image_data or '',
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
        self._captured_image_data = ''
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

        # Note: Delay Between Attempts removed - layout match now runs at max speed
        # The system will analyze each scroll cycle as fast as the host PC allows

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
        # Note: delay_between_attempts config value is no longer used
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
            screen, virtual_geo, dpr = _get_screen_info_for_capture()
            if screen is None:
                return
            # Get the geometry of the screen where capture will be shown
            geo = screen.geometry()
            # grabWindow(0) captures the full virtual desktop at device pixel resolution
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            # Use virtual desktop geometry for coordinate calculation
            # This ensures coordinates are relative to the full virtual desktop
            dlg = SnipCaptureDialog(frozen, virtual_geo, dpr, parent=None)
            # Position the dialog over the screen where the cursor is
            dlg.setGeometry(geo)
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
                self._captured_image_data = _pixmap_to_base64(cropped)

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
            self._captured_image_data = _pixmap_to_base64(pixmap)

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


class ElementPickOverlay(QDialog):
    """Full-screen desktop element picker (UI Automation).

    Hovering spotlights the real element under the cursor - the same UI
    Automation hit-test the recorder overlay uses - and a left click captures
    its bounding box; ESC or right-click cancels.  The box is in device
    (physical) pixels, the space ``capture_screen`` normalizes to, so the OCR
    trigger can crop straight to it.

    The overlay must be the window under the cursor to receive the pick click,
    yet UI Automation must see THROUGH it to report the element beneath.
    WS_EX_TRANSPARENT is therefore toggled on for just the duration of each
    hit-test: UIA skips click-through windows (the recorder overlay relies on
    the same behaviour).
    """
    picked = pyqtSignal(object)  # (l, t, r, b) device px, or None on cancel

    _VK_ESCAPE = 0x1B
    _GWL_EXSTYLE = -20
    _WS_EX_TRANSPARENT = 0x20

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hit = None
        self._done = False
        self._dpr = 1.0
        self._hwnd = None
        self._kill = threading.Event()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        try:
            self._dpr = float(QApplication.primaryScreen().devicePixelRatio()) or 1.0
        except Exception:
            self._dpr = 1.0
        self._label = QLabel(self)
        self._label.setStyleSheet(
            "background:#00e0b8; color:#00201a; padding:1px 6px;"
            "border-radius:3px; font:11px monospace;"
        )
        self._label.hide()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        # Hit-testing runs on a worker thread so a slow or hung UI Automation
        # provider can never freeze the picker (the recorder overlay does the
        # same - ElementFromPoint on the GUI thread can stall for seconds).
        self._worker = threading.Thread(target=self._loop, daemon=True)
        try:
            self.setGeometry(_get_virtual_desktop_geometry())
        except Exception:
            pass

    # ------------------------------------------------------------- lifecycle
    def showEvent(self, event):
        super().showEvent(event)
        if self._done:
            return
        # winId() must be resolved on the GUI thread; the worker only reads it.
        if self._hwnd is None:
            try:
                self._hwnd = int(self.winId())
            except Exception:
                self._hwnd = None
            logger.info("Element picker opened")
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        if not self._worker.is_alive():
            self._worker.start()
        if not self._timer.isActive():
            self._timer.start()

    def _cleanup(self):
        try:
            self._timer.stop()
        except Exception:
            pass
        self._kill.set()

    def closeEvent(self, event):
        self._done = True
        self._cleanup()
        super().closeEvent(event)

    # ------------------------------------------------------------------ pick
    def _tick(self):
        # GUI side only: ESC and repaint.  No UI Automation here, so a slow
        # provider can never block the event loop.
        if self._done:
            return
        try:
            import ctypes
            if ctypes.windll.user32.GetAsyncKeyState(self._VK_ESCAPE) & 0x8000:
                self._finish(None)
                return
        except Exception:
            pass
        self.update()

    def _loop(self):
        """Worker: poll the cursor and publish the UIA element under it."""
        try:
            import pyautogui
        except Exception as exc:
            logger.debug("Element picker: pyautogui unavailable: %s", exc)
            return
        try:
            from ..widgets.recording_overlay import element_at
        except Exception as exc:
            logger.debug("Element picker: UI Automation unavailable: %s", exc)
            element_at = None
        last = None
        while not self._kill.is_set():
            try:
                x, y = pyautogui.position()
                xy = (int(x), int(y))
            except Exception:
                time.sleep(0.05)
                continue
            if xy != last:
                last = xy
                hit = self._element_under_cursor(*xy) if element_at is not None else None
                self._hit = hit
                if hit and hit.get("rect"):
                    logger.debug("Element picker hover %s %s",
                                 hit.get("rect"), hit.get("label"))
            time.sleep(0.02)

    def _element_under_cursor(self, x, y):
        """UIA element at (x, y) with this overlay ignored (see class docstring).

        Runs on the worker thread; the overlay HWND is resolved by the GUI
        thread in showEvent (winId() is not thread-safe).
        """
        try:
            from ..widgets.recording_overlay import element_at
        except Exception:
            return None
        hwnd, saved, toggled = self._hwnd, 0, False
        if hwnd is not None:
            try:
                import ctypes
                user32 = ctypes.windll.user32
                user32.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
                user32.GetWindowLongW.restype = ctypes.c_long
                user32.SetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long)
                user32.SetWindowLongW.restype = ctypes.c_long
                saved = user32.GetWindowLongW(hwnd, self._GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, self._GWL_EXSTYLE, saved | self._WS_EX_TRANSPARENT)
                toggled = True
            except Exception:
                toggled = False
        try:
            return element_at(x, y)
        finally:
            if toggled:
                try:
                    import ctypes
                    ctypes.windll.user32.SetWindowLongW(
                        hwnd, self._GWL_EXSTYLE, saved)
                except Exception:
                    pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Use the worker's latest hit: never run UIA on the GUI thread.
            hit = self._hit
            if hit and hit.get("rect"):
                l, t, r, b = hit["rect"]
                self._finish((int(l), int(t), int(r), int(b)))
            return
        if event.button() == Qt.RightButton:
            self._finish(None)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._finish(None)
            return
        super().keyPressEvent(event)

    def _finish(self, rect):
        if self._done:
            return
        self._done = True
        self._cleanup()
        logger.info("Element picker %s: %s",
                    "picked" if rect else "cancelled", rect)
        self.picked.emit(rect)
        self.accept()

    # --------------------------------------------------------------- painting
    def _to_logical(self, rect):
        dpr = self._dpr or 1.0
        l, t, r, b = rect
        return (int(round(l / dpr)), int(round(t / dpr)),
                int(round(r / dpr)), int(round(b / dpr)))

    def paintEvent(self, event):
        painter = QPainter(self)
        # A layered (translucent) window is click-through on fully transparent
        # pixels, and the spotlight leaves the element area at alpha 0 - exactly
        # where the user clicks to pick it.  A 1/255 base keeps every pixel
        # hit-testable for the pick click while staying visually invisible.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 1))
        dim = QColor(0, 0, 0, 110)
        hit = self._hit
        rect = None
        if hit and hit.get("rect"):
            l, t, r, b = self._to_logical(hit["rect"])
            local = QRect(l - self.x(), t - self.y(), r - l, b - t)
            if local.width() >= 2 and local.height() >= 2:
                rect = local
        if rect is None:
            painter.fillRect(self.rect(), dim)
            self._label.hide()
            return
        # Spotlight: dim everything except the element box, then border it.
        w, h = self.width(), self.height()
        painter.fillRect(QRect(0, 0, w, max(0, rect.top())), dim)
        painter.fillRect(QRect(0, rect.bottom() + 1, w, max(0, h - rect.bottom() - 1)), dim)
        painter.fillRect(QRect(0, rect.top(), max(0, rect.left()), rect.height()), dim)
        painter.fillRect(QRect(rect.right() + 1, rect.top(),
                               max(0, w - rect.right() - 1), rect.height()), dim)
        pen = QPen(QColor(0, 224, 184, 255))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(rect)
        label = (hit.get("label") or "").strip()
        if label:
            self._label.setText(label)
            self._label.adjustSize()
            ly = rect.top() - self._label.height() - 2
            if ly < 0:
                ly = rect.bottom() + 2
            self._label.move(max(0, rect.left()), ly)
            self._label.show()
        else:
            self._label.hide()


class OCRTriggerConfigDialog(ModernDialog):
    """Dialog for configuring OCR-based triggers"""
    
    def __init__(self, parent, config=None):
        super().__init__(parent, title=_("OCR Trigger Configuration"), help_topic="conditional-dialog")
        self.config = config
        self.setModal(True)
        self.resize(450, 350)
        self._region = None            # [x, y, w, h] device px, or None = full screen
        self._pick_in_progress = False
        self._main_window = None
        self._capture_original_geo = None
        self.setup_ui()
        if self.config:
            self.load_config()

    def load_config(self):
        """Load existing configuration"""
        self.target_text_entry.setText(self.config.get('target_text', ''))
        self.confidence_spin.setValue(self.config.get('confidence', 0.8))
        self.case_sensitive_check.setChecked(self.config.get('case_sensitive', False))
        region = self.config.get('region')
        if isinstance(region, str):
            try:
                region = json.loads(region)
            except Exception:
                region = None
        if isinstance(region, (list, tuple)) and len(region) == 4:
            try:
                self._region = [int(v) for v in region]
            except (TypeError, ValueError):
                self._region = None
        self._sync_area_ui()
    
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

        # Search area: whole screen (default) or a picked element's box, so OCR
        # only reads the target's text instead of scanning the whole screen.
        area_layout = QHBoxLayout()
        area_layout.addWidget(QLabel(_("Search area:")))
        self.area_combo = QComboBox()
        self.area_combo.addItems([_("Whole screen"), _("Element")])
        self.area_combo.currentIndexChanged.connect(self._on_area_changed)
        area_layout.addWidget(self.area_combo)
        area_layout.addStretch()
        layout.addLayout(area_layout)

        self.region_row = QWidget()
        region_layout = QHBoxLayout(self.region_row)
        region_layout.setContentsMargins(0, 0, 0, 0)
        self.region_label = QLabel(_("No element picked"))
        region_layout.addWidget(self.region_label, 1)
        self.pick_btn = QPushButton(_("Pick element"))
        self.pick_btn.clicked.connect(self.pick_element)
        region_layout.addWidget(self.pick_btn)
        layout.addWidget(self.region_row)
        self.region_row.setVisible(False)
        
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
        
        # Note: OCR language is fixed to English; the search area is set above.
        
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
        try:
            if self.area_combo.currentIndex() == 1 and self._region:
                config['region'] = [int(v) for v in self._region]
        except Exception:
            pass
        
        return config

    # ------------------------------------------------------- element picking
    def _sync_area_ui(self):
        """Reflect the loaded region in the search-area controls."""
        try:
            self.area_combo.setCurrentIndex(1 if self._region else 0)
        except Exception:
            pass
        try:
            self._on_area_changed(self.area_combo.currentIndex())
        except Exception:
            pass
        self._update_region_label()

    def _on_area_changed(self, index):
        try:
            self.region_row.setVisible(int(index) == 1)
        except Exception:
            pass

    def _update_region_label(self):
        try:
            if self._region:
                x, y, w, h = self._region
                self.region_label.setText(
                    _("Element region: x={x}, y={y}, w={w}, h={h}").format(
                        x=x, y=y, w=w, h=h)
                )
            else:
                self.region_label.setText(_("No element picked"))
        except Exception:
            pass

    def pick_element(self):
        """Hide the dialogs and let the user click an element on screen."""
        if self._pick_in_progress:
            return
        self._pick_in_progress = True
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
        QTimer.singleShot(450, self._start_pick)

    def _start_pick(self):
        try:
            overlay = ElementPickOverlay(parent=None)
        except Exception as exc:
            logger.error(f"Element picker unavailable: {exc}")
            self._restore_after_pick()
            return
        self._pick_overlay = overlay
        overlay.picked.connect(self._on_element_picked)
        try:
            overlay.exec_()
        except Exception as exc:
            logger.error(f"Element picker failed: {exc}")
        finally:
            # Idempotent: also covers the overlay being closed without a pick.
            self._restore_after_pick()

    def _on_element_picked(self, rect):
        if rect:
            l, t, r, b = rect
            self._region = [int(l), int(t), max(1, int(r) - int(l)), max(1, int(b) - int(t))]
        # No window restore here: that is done by _start_pick's finally, AFTER
        # the modal overlay's exec_() has fully returned.  Showing the dialog
        # while the overlay is still modal deadlocks the modal handoff.
        self._update_region_label()

    def _restore_after_pick(self):
        self._pick_in_progress = False
        try:
            if getattr(self, '_capture_original_geo', None) is not None:
                self.setGeometry(self._capture_original_geo)
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        try:
            if getattr(self, '_main_window', None) is not None:
                self._main_window.showNormal()
                self._main_window.raise_()
                self._main_window.activateWindow()
        except Exception:
            pass


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
        accepted_after_capture = False
        try:
            screen, virtual_geo, dpr = _get_screen_info_for_capture()
            if screen is None:
                return
            geo = screen.geometry()
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            dlg = SnipCaptureDialog(frozen, virtual_geo, dpr, parent=None)
            dlg.setGeometry(geo)
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
        self._captured_image_data = ''
        self.setup_ui()
        if self.config:
            self.load_config()

    def load_config(self):
        """Load existing configuration"""
        self.type_combo.setCurrentText(self.config.get('type', 'while_present'))
        self.sequence_file_entry.setText(self.config.get('sequence_file', ''))
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
        
        # Parameters — no iteration cap: the loop runs until the condition is
        # no longer met, then the workflow continues via the single output.
        params_layout = QGridLayout()
        
        params_layout.addWidget(QLabel(_("Iteration Delay (s):")), 0, 0)
        self.iteration_delay_spin = QDoubleSpinBox()
        self.iteration_delay_spin.setRange(0.1, 60.0)
        self.iteration_delay_spin.setSingleStep(0.1)
        self.iteration_delay_spin.setValue(1.0)
        params_layout.addWidget(self.iteration_delay_spin, 0, 1)
        
        params_layout.addWidget(QLabel(_("Confidence:")), 1, 0)
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        params_layout.addWidget(self.confidence_spin, 1, 1)
        
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
            screen, virtual_geo, dpr = _get_screen_info_for_capture()
            if screen is None:
                return
            geo = screen.geometry()
            frozen = screen.grabWindow(0)
            if frozen is None or frozen.isNull():
                return
            dlg = SnipCaptureDialog(frozen, virtual_geo, dpr, parent=None)
            dlg.setGeometry(geo)
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
                self._captured_image_data = _pixmap_to_base64(cropped)
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
                'image_data': self._captured_image_data or '',
                'confidence': self.confidence_spin.value()
            }
        
        return {
            'type': loop_type,
            'sequence_file': self.sequence_file_entry.text(),
            'iteration_delay': self.iteration_delay_spin.value(),
            'condition': condition
        }
