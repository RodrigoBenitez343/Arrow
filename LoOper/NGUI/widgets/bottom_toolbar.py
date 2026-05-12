import logging
import os
import threading

from player.agentic_ops import ChatController
from PyQt5.QtCore import QMetaObject, QPoint, QSize, Qt, QTimer, pyqtSlot, QPropertyAnimation, QEasingCurve, QAbstractAnimation
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..constants import (
    ACCENT_COLOR,
    BLOCK_COLOR,
    BLOCK_HOVER,
    DARK_GREY,
    LIGHT_GREY,
    MEDIUM_GREY,
    TEXT_COLOR,
    BAR_BG,
    SIDE_PANEL_BG,
    GRAPH_PLANE,
)
from ..i18n import _
from .collapsible_toolbar import make_toggle_triangle_icon


# ---- Model helpers (simplified: fixed pipeline) ----


def _get_models():
    """Return the fixed two-model pipeline info for display."""
    return {
        "vision": "LFM2.5-VL-450M",
        "reasoning": "gemma4:4b",
    }


logger = logging.getLogger(__name__)


class BottomActionsToolbar(QWidget):
    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        self._ui_queue = []
        self._processing_bubble = None
        self._processing_label = None
        self._processing_timer = None
        self._processing_dots = 0
        self._build_ui()
        self._chat_controller = ChatController(
            graph_view=self.graph_view,
            post_ui=self._post_ui,
            post_ui_sync=self._post_ui_sync,
            add_chat_bubble=self._add_chat_bubble,
            scroll_to_bottom=self._scroll_to_bottom,
            force_ui_flush=self._force_ui_flush,
            set_processing_label=self._set_processing_label,
            start_processing_indicator=self._start_processing_indicator,
            stop_processing_indicator=self._stop_processing_indicator,
            translate=_,
            accent_color=ACCENT_COLOR,
            on_minimize=self._minimize_window,
            on_restore=self._restore_window,
        )
        # Bottom toolbar is now visible by default
        # Removed automatic hiding to enable development and testing

    def _build_ui(self):
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("bottomActionsContent")
        self._content_frame.setStyleSheet(
            f"QFrame#bottomActionsContent {{ background-color: {BAR_BG}; border-radius: 10px; }}"
        )
        self._content_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self._content_frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title = QLabel(_("Chat"), self._content_frame)
        title.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 14px; font-weight: 600;")
        header.addWidget(title, 0, Qt.AlignLeft)
        header.addStretch(1)
        # Fixed model labels — vision + reasoning always use these
        models_label = QLabel(
            "vision: LFM2.5-VL-450M  |  reasoning: gemma4:4b",
            self._content_frame,
        )
        models_label.setStyleSheet(
            f"color: {LIGHT_GREY}; font-size: 10px;"
        )
        header.addWidget(models_label, 0, Qt.AlignRight)
        # Clear memory button
        self._clear_memory_btn = QPushButton(_("Clear"), self._content_frame)
        self._clear_memory_btn.setToolTip(_("Clear all agent memory / learned experiences"))
        self._clear_memory_btn.setCursor(Qt.PointingHandCursor)
        self._clear_memory_btn.setFixedSize(42, 22)
        self._clear_memory_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {LIGHT_GREY}; border: 1px solid {LIGHT_GREY}; border-radius: 6px; font-size: 10px; padding: 0px 6px; }}"
            f"QPushButton:hover {{ color: #ff6b6b; border-color: #ff6b6b; }}"
        )
        self._clear_memory_btn.clicked.connect(self._clear_memory)
        header.addWidget(self._clear_memory_btn, 0, Qt.AlignRight)
        layout.addLayout(header)

        chat_frame = QFrame(self._content_frame)
        chat_frame.setObjectName("chatAreaFrame")
        chat_frame.setStyleSheet(
            f"QFrame#chatAreaFrame {{ background-color: {MEDIUM_GREY}; border: 1px solid {LIGHT_GREY}; border-radius: 10px; }}"
        )
        chat_layout = QVBoxLayout(chat_frame)
        chat_layout.setContentsMargins(8, 8, 8, 8)
        chat_layout.setSpacing(8)
        self._chat_scroll = QScrollArea(chat_frame)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_scroll.setStyleSheet(
            f"QScrollArea {{ background-color: transparent; border: 0px; }}"
        )
        self._chat_container = QWidget(self._chat_scroll)
        self._chat_container.setObjectName("chatContainer")
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(4, 4, 4, 4)
        self._chat_layout.setSpacing(8)
        self._chat_layout.setAlignment(Qt.AlignTop)
        self._placeholder_label = QLabel(
            _("Start a conversation"), self._chat_container
        )
        self._placeholder_label.setAlignment(Qt.AlignCenter)
        self._placeholder_label.setStyleSheet(
            f"color: {TEXT_COLOR}; background-color: {BLOCK_COLOR}; border: 1px solid {LIGHT_GREY}; border-radius: 12px; padding: 10px 14px;"
        )
        self._chat_layout.addWidget(self._placeholder_label, 0, Qt.AlignCenter)
        self._chat_scroll.setWidget(self._chat_container)
        chat_layout.addWidget(self._chat_scroll, 1)
        layout.addWidget(chat_frame, 1)

        input_frame = QFrame(self._content_frame)
        input_frame.setObjectName("chatInputFrame")
        input_frame.setStyleSheet(
            f"QFrame#chatInputFrame {{ background-color: {BLOCK_COLOR}; border: 1px solid {LIGHT_GREY}; border-radius: 12px; }}"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(8, 6, 8, 6)
        input_layout.setSpacing(8)
        self._input_field = QTextEdit(input_frame)
        self._input_field.setPlaceholderText(_("Type a message..."))
        self._input_field.setMinimumHeight(40)
        self._input_field.setMaximumHeight(200)
        self._input_field.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._input_field.setStyleSheet(
            f"QTextEdit {{ background-color: {MEDIUM_GREY}; color: {TEXT_COLOR}; border: 1px solid {LIGHT_GREY}; border-radius: 10px; padding: 6px 10px; }}"
            f"QTextEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        input_layout.addWidget(self._input_field, 1)
        self._send_button = QPushButton(_("Send"), input_frame)
        self._send_button.setCursor(Qt.PointingHandCursor)
        self._send_button.setFixedHeight(30)
        self._send_button.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: {TEXT_COLOR}; border: 0px; border-radius: 10px; padding: 6px 16px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: {BLOCK_HOVER}; }}"
        )
        input_layout.addWidget(self._send_button, 0)
        layout.addWidget(input_frame, 0)
        outer.addWidget(self._content_frame)

        self._toggle_btn_bottom = QToolButton(self.graph_view)
        self._toggle_btn_bottom.setToolTip(_("Toggle Bottom Toolbar"))
        ico = make_toggle_triangle_icon("DOWN", 14, GRAPH_PLANE)
        self._toggle_btn_bottom.setIcon(ico)
        self._toggle_btn_bottom.setIconSize(QSize(14, 14))
        self._toggle_btn_bottom.setAutoRaise(True)
        self._toggle_btn_bottom.setCursor(Qt.PointingHandCursor)
        self._toggle_btn_bottom.setFixedSize(24, 28)
        self._toggle_btn_bottom.setStyleSheet(
            f"QToolButton {{ background-color: transparent; border: none; }}"
            f"QToolButton:hover {{ background-color: rgba(255,255,255,0.06); border-radius: 4px; }}"
        )
        self._toggle_btn_bottom.clicked.connect(self._toggle_bottom_toolbar)
        self._send_button.clicked.connect(self._handle_send)
        self._input_field.textChanged.connect(self._update_input_height)
        try:
            QTimer.singleShot(0, self._position_bottom_toggle)
            QTimer.singleShot(0, lambda: self._apply_layout_stretch(True))
            QTimer.singleShot(0, self._update_input_height)
        except Exception:
            pass

    def _toggle_bottom_toolbar(self):
        try:
            self._collapsed = not self._collapsed
            if self._collapsed:
                start_h = max(0, self._content_frame.height())
                self._anim_bottom = QPropertyAnimation(self._content_frame, b"maximumHeight")
                self._anim_bottom.setDuration(200)
                self._anim_bottom.setStartValue(start_h)
                self._anim_bottom.setEndValue(0)
                self._anim_bottom.setEasingCurve(QEasingCurve.InOutQuad)
                self._anim_bottom.finished.connect(self._on_bottom_collapse)
                self._anim_bottom.start(QAbstractAnimation.DeleteWhenStopped)
            else:
                self._content_frame.setVisible(True)
                self._content_frame.setMaximumHeight(0)
                QApplication.processEvents()
                target_h = self._content_frame.minimumSizeHint().height()
                self._anim_bottom = QPropertyAnimation(self._content_frame, b"maximumHeight")
                self._anim_bottom.setDuration(200)
                self._anim_bottom.setStartValue(0)
                self._anim_bottom.setEndValue(target_h)
                self._anim_bottom.setEasingCurve(QEasingCurve.InOutQuad)
                self._anim_bottom.finished.connect(self._on_bottom_expand)
                self._anim_bottom.start(QAbstractAnimation.DeleteWhenStopped)
            direction = "UP" if self._collapsed else "DOWN"
            color = SIDE_PANEL_BG if self._collapsed else GRAPH_PLANE
            ico = make_toggle_triangle_icon(direction, 14, color)
            self._toggle_btn_bottom.setIcon(ico)
            self._toggle_btn_bottom.setIconSize(QSize(14, 14))
            self._apply_layout_stretch(not self._collapsed)
            QTimer.singleShot(250, self._position_bottom_toggle)
        except Exception:
            logger.exception("Error toggling bottom toolbar")

    def _on_bottom_collapse(self):
        self._content_frame.setVisible(False)
        self._content_frame.setMaximumHeight(16777215)
        self._apply_layout_stretch(False)

    def _on_bottom_expand(self):
        self._content_frame.setMaximumHeight(16777215)
        QTimer.singleShot(0, self._position_bottom_toggle)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._position_bottom_toggle)
            QTimer.singleShot(0, self._apply_bubble_widths)
        except Exception:
            pass

    def _position_bottom_toggle(self):
        try:
            if (
                not hasattr(self, "_toggle_btn_bottom")
                or self._toggle_btn_bottom is None
            ):
                return
            toolbar_top_left = self.mapTo(self.graph_view, QPoint(0, 0))
            x = toolbar_top_left.x() + self.width() // 2 - 14
            y = toolbar_top_left.y() - self._toggle_btn_bottom.height() - 4
            self._toggle_btn_bottom.move(x, y)
            self._toggle_btn_bottom.raise_()
        except Exception:
            logger.exception("Error positioning bottom toolbar toggle")

    def _apply_layout_stretch(self, expanded: bool):
        try:
            container = self.parentWidget()
            if container is None:
                return
            layout = container.layout()
            if layout is None:
                return
            graph_widget = getattr(self.graph_view, "node_graph_widget", None)
            if graph_widget is None:
                return
            if expanded:
                layout.setStretchFactor(graph_widget, 1)
                layout.setStretchFactor(self, 3)
            else:
                layout.setStretchFactor(graph_widget, 1)
                layout.setStretchFactor(self, 0)
        except Exception:
            logger.exception("Error applying bottom toolbar stretch")

    def _update_input_height(self):
        try:
            doc = self._input_field.document()
            line_height = self._input_field.fontMetrics().lineSpacing()
            max_lines = 10
            min_height = 40
            doc_height = doc.size().height()
            target = int(doc_height + 12)
            if target < min_height:
                target = min_height
            max_height = max_lines * line_height + 20
            if target > max_height:
                target = max_height
            self._input_field.setFixedHeight(target)
        except Exception:
            logger.exception("Error updating input height")

    # ---- Model info ----
    def _get_selected_model(self):
        """Return fixed (engine, model_name) tuple."""
        return ("Ollama", "gemma4:4b")

    def _clear_memory(self):
        try:
            store = getattr(self._chat_controller, '_learning_store', None)
            if store is None:
                return
            count = store.count()
            if count == 0:
                self._add_chat_bubble(_('Memory is already empty.'), False)
                self._scroll_to_bottom()
                return
            store.clear()
            self._add_chat_bubble(
                _('Memory cleared: {} entries removed.').format(count), False
            )
            self._scroll_to_bottom()
        except Exception:
            logger.exception('Error clearing memory')

    def _handle_send(self):
        try:
            text = self._input_field.toPlainText().strip()
            if not text:
                return
            self._add_chat_bubble(text, True)
            self._input_field.clear()
            self._update_input_height()
            QTimer.singleShot(0, self._scroll_to_bottom)
            self._dispatch_chat_request(text)
        except Exception:
            logger.exception("Error sending chat message")

    def _add_chat_bubble(self, text, is_user, bubble_color=None):
        if self._placeholder_label is not None:
            self._placeholder_label.hide()
        bubble = QFrame(self._chat_container)
        if bubble_color is None:
            bubble_color = BLOCK_COLOR if is_user else MEDIUM_GREY
        bubble.setStyleSheet(
            f"QFrame {{ background-color: {bubble_color}; border: 1px solid {LIGHT_GREY}; border-radius: 12px; }}"
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(10, 8, 10, 8)
        bubble_layout.setSpacing(0)
        label = QLabel(text, bubble)
        label.setObjectName("chatBubbleLabel")
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        max_width = self._bubble_max_width()
        self._apply_label_width(label, max_width)
        label.setStyleSheet(f"color: {TEXT_COLOR};")
        bubble_layout.addWidget(label)
        bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        bubble.setFixedWidth(label.width() + 24)
        align = Qt.AlignRight if is_user else Qt.AlignLeft
        self._chat_layout.addWidget(bubble, 0, align)

    def _scroll_to_bottom(self):
        try:
            bar = self._chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def _post_ui(self, func):
        try:
            self._ui_queue.append(func)
            QMetaObject.invokeMethod(self, "_drain_ui_queue", Qt.QueuedConnection)
        except Exception:
            pass

    def _post_ui_sync(self, func, timeout=0.5):
        try:
            done = threading.Event()

            def wrapped():
                try:
                    func()
                finally:
                    done.set()

            self._post_ui(wrapped)
            done.wait(timeout=timeout)
        except Exception:
            pass

    @pyqtSlot()
    def _drain_ui_queue(self):
        try:
            queue = list(self._ui_queue)
            self._ui_queue = []
        except Exception:
            return
        for fn in queue:
            try:
                fn()
            except Exception:
                pass

    def _force_ui_flush(self):
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _set_processing_label(self, text):
        if self._processing_label:
            self._processing_label.setText(text)

    def _minimize_window(self):
        try:
            window = self.graph_view.window() if self.graph_view else None
            if window and hasattr(window, "showMinimized"):
                QTimer.singleShot(0, window.showMinimized)
        except Exception:
            pass

    def _restore_window(self):
        try:
            window = self.graph_view.window() if self.graph_view else None
            if window and hasattr(window, "_restore_window"):
                QTimer.singleShot(0, window._restore_window)
        except Exception:
            pass

    def _start_processing_indicator(self, text):
        def _start():
            if self._processing_timer:
                self._processing_timer.stop()
            self._processing_dots = 0
            self._processing_bubble = QFrame(self._chat_container)
            self._processing_bubble.setStyleSheet(
                f"QFrame {{ background-color: {MEDIUM_GREY}; border: 1px solid {LIGHT_GREY}; border-radius: 12px; }}"
            )
            layout = QVBoxLayout(self._processing_bubble)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(0)
            self._processing_label = QLabel(text, self._processing_bubble)
            self._processing_label.setObjectName("chatBubbleLabel")
            self._processing_label.setWordWrap(True)
            self._processing_label.setSizePolicy(
                QSizePolicy.Fixed, QSizePolicy.Preferred
            )
            max_width = self._bubble_max_width()
            self._apply_label_width(self._processing_label, max_width)
            self._processing_label.setStyleSheet(f"color: {TEXT_COLOR};")
            layout.addWidget(self._processing_label)
            self._processing_bubble.setSizePolicy(
                QSizePolicy.Fixed, QSizePolicy.Preferred
            )
            self._processing_bubble.setFixedWidth(self._processing_label.width() + 24)
            self._chat_layout.addWidget(self._processing_bubble, 0, Qt.AlignLeft)
            self._scroll_to_bottom()
            self._processing_timer = QTimer(self)
            self._processing_timer.setInterval(400)
            self._processing_timer.timeout.connect(self._tick_processing_indicator)
            self._processing_timer.start()

        self._post_ui_sync(_start)

    def _tick_processing_indicator(self):
        if not self._processing_label:
            return
        self._processing_dots = (self._processing_dots + 1) % 4
        base = (
            self._processing_label.text().split("...")[0].split("..")[0].split(".")[0]
        )
        self._processing_label.setText(base + ("." * self._processing_dots))
        max_width = self._bubble_max_width()
        self._apply_label_width(self._processing_label, max_width)
        if self._processing_bubble:
            self._processing_bubble.setFixedWidth(self._processing_label.width() + 24)

    def _stop_processing_indicator(self):
        def _stop():
            try:
                if self._processing_timer:
                    self._processing_timer.stop()
                if self._processing_bubble:
                    self._processing_bubble.setParent(None)
                self._processing_timer = None
                self._processing_bubble = None
                self._processing_label = None
            except Exception:
                pass

        self._post_ui_sync(_stop)

    def _bubble_max_width(self):
        try:
            viewport = self._chat_scroll.viewport()
            if viewport:
                return max(240, int(viewport.width() * 0.78))
        except Exception:
            pass
        return 420

    def _apply_label_width(self, label, max_width):
        try:
            fm = label.fontMetrics()
            text = label.text() or ""
            text_width = fm.boundingRect(
                0, 0, 10_000, 10_000, Qt.TextWordWrap, text
            ).width()
            target = min(max_width, text_width + 6)
            if target < 160:
                target = min(max_width, 160)
            label.setFixedWidth(target)
        except Exception:
            pass

    def _apply_bubble_widths(self):
        try:
            max_width = self._bubble_max_width()
            for label in self._chat_container.findChildren(QLabel, "chatBubbleLabel"):
                self._apply_label_width(label, max_width)
                parent = label.parentWidget()
                if parent:
                    parent.setFixedWidth(label.width() + 24)
        except Exception:
            pass

    def _dispatch_chat_request(self, text):
        def worker():
            try:
                self._chat_controller.handle_request(text)
            except Exception as e:
                logger.error(f"Chat dispatch error: {e}")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
