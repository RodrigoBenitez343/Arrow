
from PyQt5.QtWidgets import QCheckBox, QSizePolicy
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, QRectF, QSize
from PyQt5.QtGui import QPainter, QColor, QBrush, QPen

from ..constants import ACCENT_COLOR, LIGHT_GREY, DARK_GREY, TEXT_COLOR

class ModernToggle(QCheckBox):
    def __init__(self, parent=None, text=""):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(True)
        
        # Dimensions
        self._width = 40
        self._height = 22
        self._thumb_radius = 8
        self._margin = 3
        
        # Animation
        self._thumb_pos = 0.0  # 0.0 to 1.0
        self._animation = QPropertyAnimation(self, b"thumb_pos", self)
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.InOutQuad)
        
        self.stateChanged.connect(self._handle_state_change)
        
        # Initialize position
        self._handle_state_change(self.checkState())

    @pyqtProperty(float)
    def thumb_pos(self):
        return self._thumb_pos

    @thumb_pos.setter
    def thumb_pos(self, pos):
        self._thumb_pos = pos
        self.update()

    def _handle_state_change(self, state):
        start = self._thumb_pos
        end = 1.0 if state == Qt.Checked else 0.0
        
        self._animation.setStartValue(start)
        self._animation.setEndValue(end)
        self._animation.start()

    def hitButton(self, pos):
        # Allow clicking anywhere on the widget, including the text
        return self.contentsRect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Determine colors
        if self.isChecked():
            bg_color = QColor(ACCENT_COLOR)
        else:
            bg_color = QColor(LIGHT_GREY)
            
        if not self.isEnabled():
            bg_color = QColor(DARK_GREY)
            
        # Draw track
        track_rect = QRectF(0, (self.height() - self._height) / 2, self._width, self._height)
        
        if self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bg_color))
        else:
            # Add border for better visibility in unchecked state
            painter.setPen(QPen(QColor("#444444"), 1))
            painter.setBrush(QBrush(bg_color))
            
        painter.drawRoundedRect(track_rect, self._height / 2, self._height / 2)
        
        # Draw thumb
        thumb_x = self._margin + (self._width - 2 * self._margin - 2 * self._thumb_radius) * self._thumb_pos
        thumb_y = track_rect.top() + self._margin
        
        painter.setBrush(QBrush(QColor(TEXT_COLOR)))
        painter.drawEllipse(QRectF(thumb_x, thumb_y, 2 * self._thumb_radius, 2 * self._thumb_radius))
        
        # Draw text
        if self.text():
            painter.setPen(QColor(TEXT_COLOR))
            font = self.font()
            painter.setFont(font)
            
            text_rect = self.contentsRect()
            text_rect.setLeft(int(track_rect.right() + 10))
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, self.text())
            
    def sizeHint(self):
        s = super().sizeHint()
        w = self._width + 10  # Toggle width + spacing
        h = max(self._height, s.height())
        if self.text():
            font_metrics = self.fontMetrics()
            w += font_metrics.horizontalAdvance(self.text()) + 5
        return QSize(int(w), int(h))
