import logging
import os
import threading

from player.agentic_ops import SimpleChainRouter
from PyQt5.QtCore import QMetaObject, Qt, QTimer, pyqtSlot
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
    SIDE_PANEL_BG,
    GRAPH_PLANE,
)
from ..i18n import _


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
        logger.debug("== BottomActionsToolbar.__init__ start ==")
        super().__init__(parent)
        logger.debug("super().__init__ done")
        self.graph_view = graph_view
        self._ui_queue = []
        self._processing_bubble = None
        self._processing_label = None
        self._processing_timer = None
        self._processing_dots = 0
        logger.debug("BottomActionsToolbar: calling _build_ui")
        self._build_ui()
        logger.debug("BottomActionsToolbar: _build_ui done")
        # Simple chain router using SmolLM3 via llama.cpp
        self._chat_controller = SimpleChainRouter(
            chains_dir=os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "chains"
            )
        )
        # Wire the ask-user callback so Input nodes with a user_prompt can query the user
        self._input_ask_event = threading.Event()
        self._input_ask_response = None
        self._input_ask_active = False
        self._chat_controller.set_ask_user_callback(self._make_ask_user_callback())
        try:
            self._chat_controller.set_ask_user_v2_callback(self._make_ask_user_v2_callback())
        except AttributeError:
            pass
        self._chat_stop_event = threading.Event()
        logger.debug("== BottomActionsToolbar.__init__ end ==")
        # Bottom toolbar is now visible by default
        # Removed automatic hiding to enable development and testing

    def _build_ui(self):
        logger.debug("_build_ui: start")
        try:
            self.setAttribute(Qt.WA_TranslucentBackground)
            logger.debug("_build_ui: WA_TranslucentBackground set")
            self.setStyleSheet("background: transparent;")
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            outer = QVBoxLayout(self)
            outer.setContentsMargins(5, 0, 5, 5)
            outer.setSpacing(0)

            self._content_frame = QFrame(self)
            logger.debug("_build_ui: _content_frame created")
            self._content_frame.setObjectName("bottomActionsContent")
            self._content_frame.setStyleSheet(
                f"QFrame#bottomActionsContent {{ background-color: {SIDE_PANEL_BG}; border: 1px solid rgba(0, 0, 0, 0.2); border-radius: 10px; }}"
            )
            self._content_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            layout = QVBoxLayout(self._content_frame)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(10)

            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(8)
            title = QLabel(_("Chat"), self._content_frame)
            logger.debug("_build_ui: title label created")
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
            logger.debug("_build_ui: clear button created")
            self._clear_memory_btn.setToolTip(_("Clear all agent memory / learned experiences"))
            self._clear_memory_btn.setCursor(Qt.PointingHandCursor)
            self._clear_memory_btn.setFixedSize(42, 22)
            self._clear_memory_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {LIGHT_GREY}; border: 1px solid {LIGHT_GREY}; border-radius: 6px; font-size: 10px; padding: 0px 6px; }}"
                f"QPushButton:hover {{ color: #ef4444; border-color: #ef4444; }}"
            )
            self._clear_memory_btn.clicked.connect(self._clear_memory)
            header.addWidget(self._clear_memory_btn, 0, Qt.AlignRight)
            layout.addLayout(header)

            chat_frame = QFrame(self._content_frame)
            logger.debug("_build_ui: chat_frame created")
            chat_frame.setObjectName("chatAreaFrame")
            chat_frame.setStyleSheet(
                f"QFrame#chatAreaFrame {{ background-color: {MEDIUM_GREY}; border: 1px solid {LIGHT_GREY}; border-radius: 10px; }}"
            )
            chat_layout = QVBoxLayout(chat_frame)
            chat_layout.setContentsMargins(8, 8, 8, 8)
            chat_layout.setSpacing(8)
            self._chat_scroll = QScrollArea(chat_frame)
            logger.debug("_build_ui: QScrollArea created")
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
            logger.debug("_build_ui: placeholder label created")
            self._placeholder_label.setAlignment(Qt.AlignCenter)
            self._placeholder_label.setStyleSheet(
                f"color: {TEXT_COLOR}; background-color: {BLOCK_COLOR}; border: 1px solid {LIGHT_GREY}; border-radius: 12px; padding: 10px 14px;"
            )
            self._chat_layout.addWidget(self._placeholder_label, 0, Qt.AlignCenter)
            self._chat_scroll.setWidget(self._chat_container)
            chat_layout.addWidget(self._chat_scroll, 1)
            layout.addWidget(chat_frame, 1)

            input_frame = QFrame(self._content_frame)
            logger.debug("_build_ui: input_frame created")
            input_frame.setObjectName("chatInputFrame")
            input_frame.setStyleSheet(
                f"QFrame#chatInputFrame {{ background-color: {BLOCK_COLOR}; border: 1px solid {LIGHT_GREY}; border-radius: 12px; }}"
            )
            input_layout = QHBoxLayout(input_frame)
            input_layout.setContentsMargins(8, 6, 8, 6)
            input_layout.setSpacing(8)
            self._input_field = QTextEdit(input_frame)
            logger.debug("_build_ui: QTextEdit created")
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
            logger.debug("_build_ui: send button created")
            self._send_button.setCursor(Qt.PointingHandCursor)
            self._send_button.setFixedHeight(30)
            self._send_button.setStyleSheet(
                f"QPushButton {{ background-color: {ACCENT_COLOR}; color: {TEXT_COLOR}; border: 0px; border-radius: 10px; padding: 6px 16px; font-weight: 600; }}"
                f"QPushButton:hover {{ background-color: {BLOCK_HOVER}; }}"
            )
            input_layout.addWidget(self._send_button, 0)
            layout.addWidget(input_frame, 0)
            outer.addWidget(self._content_frame)

            self._send_button.clicked.connect(self._handle_send)
            self._input_field.textChanged.connect(self._update_input_height)
            logger.debug("_build_ui: signals connected, scheduling _update_input_height timer")
            QTimer.singleShot(0, self._update_input_height)

            logger.debug("_build_ui: end")
        except Exception:
            logger.exception("_build_ui: FATAL error")

    def resizeEvent(self, event):
        """Override to reapply bubble widths on resize."""
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._apply_bubble_widths)
        except Exception:
            logger.exception("resizeEvent: error")

    def keyPressEvent(self, event):
        """ESC stops the running agent loop."""
        if event.key() == Qt.Key_Escape:
            self._chat_stop_event.set()
            logger.info("[AGENT] ESC pressed (toolbar) — stop requested")
        super().keyPressEvent(event)

    def _apply_layout_stretch(self, expanded: bool):
        pass

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
            # Memory not yet implemented in simplified agent
            self._add_chat_bubble(_('Memory system not yet implemented.'), False)
            self._scroll_to_bottom()
        except Exception:
            logger.exception("_clear_memory: error")

    def _handle_send(self):
        try:
            text = self._input_field.toPlainText().strip()
            if not text:
                return

            # If there's a pending input ask, fulfill it instead of dispatching
            if self._input_ask_active:
                self._input_ask_response = text
                self._input_ask_active = False
                self._add_chat_bubble(text, True)
                self._input_field.clear()
                self._update_input_height()
                self._input_ask_event.set()
                QTimer.singleShot(0, self._scroll_to_bottom)
                return

            self._add_chat_bubble(text, True)
            self._input_field.clear()
            self._update_input_height()
            QTimer.singleShot(0, self._scroll_to_bottom)
            self._dispatch_chat_request(text)
        except Exception:
            logger.exception("_handle_send: Error sending chat message")

    def _make_ask_user_v2_callback(self):
        """Rich ask (v2 protocol): numbered options + attach-hint in the chat;
        a reply naming an existing file becomes an attachment."""
        legacy = self._make_ask_user_callback()

        def ask_user_v2(request):
            try:
                request = dict(request or {})
            except Exception:
                request = {'question': str(request)}
            question = str(request.get('question') or '')
            kind = str(request.get('kind') or 'text')
            choices = list(request.get('choices') or [])
            accepts = dict(request.get('accepts') or {})
            lines = [question]
            if kind == 'yes_no':
                lines.append('(Reply yes or no)')
            elif kind == 'choice' and choices:
                for i, c in enumerate(choices):
                    try:
                        label = c.get('label') or c.get('value')
                    except Exception:
                        label = str(c)
                    lines.append(f"{i + 1}) {label}")
            if accepts.get('images') or accepts.get('documents'):
                lines.append('(Attach a file by typing or pasting its path)')
            reply = legacy('\n'.join(p for p in lines if p))
            value = str(reply or '').strip()
            attachments = []
            import os as _os
            if value and _os.path.isfile(value):
                try:
                    from player.qt_input import _attachment_kind as _kind
                    attachments = [{
                        'kind': _kind(value),
                        'path': value,
                        'name': _os.path.basename(value),
                    }]
                    value = ''
                except Exception:
                    attachments = []
            return {'value': value or None, 'kind': kind, 'attachments': attachments}

        return ask_user_v2

    def _make_ask_user_callback(self):
        """Create a blocking callback that asks the user in chat for input."""
        def ask_user(question):
            self._input_ask_event.clear()
            self._input_ask_response = None
            self._input_ask_active = True
            self._post_ui(lambda q=question: self._add_chat_bubble(q, False))
            self._post_ui(self._scroll_to_bottom)
            self._post_ui(self._force_ui_flush)
            self._input_ask_event.wait()
            resp = self._input_ask_response or ""
            self._input_ask_active = False
            return resp
        return ask_user

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
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(0)
        label = QLabel(text, bubble)
        label.setObjectName("chatBubbleLabel")
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        label.setStyleSheet(f"color: {TEXT_COLOR};")
        max_width = self._bubble_max_width()
        label.setMaximumWidth(max_width)
        min_width = self._apply_label_width(label, max_width)
        label.setMinimumWidth(min_width)
        bubble_layout.addWidget(label)
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        align = Qt.AlignRight if is_user else Qt.AlignLeft
        self._chat_layout.addWidget(bubble, 0, align)

    def _scroll_to_bottom(self):
        try:
            bar = self._chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            logger.exception("_scroll_to_bottom: error")

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
            logger.exception("_drain_ui_queue: queue copy error")
            return
        for fn in queue:
            try:
                fn()
            except Exception:
                logger.exception("_drain_ui_queue: callback error")

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
            layout.setContentsMargins(14, 10, 14, 10)
            layout.setSpacing(0)
            self._processing_label = QLabel(text, self._processing_bubble)
            self._processing_label.setObjectName("chatBubbleLabel")
            self._processing_label.setWordWrap(True)
            self._processing_label.setSizePolicy(
                QSizePolicy.Preferred, QSizePolicy.Preferred
            )
            self._processing_label.setStyleSheet(f"color: {TEXT_COLOR};")
            max_width = self._bubble_max_width()
            self._processing_label.setMaximumWidth(max_width)
            min_width = self._apply_label_width(self._processing_label, max_width)
            self._processing_label.setMinimumWidth(min_width)
            layout.addWidget(self._processing_label)
            self._processing_bubble.setSizePolicy(
                QSizePolicy.Preferred, QSizePolicy.Preferred
            )
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
        self._processing_label.setMaximumWidth(max_width)
        min_width = self._apply_label_width(self._processing_label, max_width)
        self._processing_label.setMinimumWidth(min_width)

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
                logger.exception("_stop_processing_indicator: error")

        self._post_ui_sync(_stop)

    def _bubble_max_width(self):
        try:
            viewport = self._chat_scroll.viewport()
            if viewport:
                return max(240, int(viewport.width() * 0.78))
        except Exception:
            logger.exception("_bubble_max_width: error")
        return 420

    def _apply_label_width(self, label, max_width):
        try:
            fm = label.fontMetrics()
            text = label.text() or ""
            text_width = fm.horizontalAdvance(text)
            target = min(max_width, text_width + 16)
            if target < 160:
                target = min(max_width, 160)
            return target
        except Exception:
            logger.exception("_apply_label_width: error")
            return 160

    def _apply_bubble_widths(self):
        try:
            max_width = self._bubble_max_width()
            for label in self._chat_container.findChildren(QLabel, "chatBubbleLabel"):
                label.setMaximumWidth(max_width)
                min_width = self._apply_label_width(label, max_width)
                label.setMinimumWidth(min_width)
        except Exception:
            logger.exception("_apply_bubble_widths: error")

    def _dispatch_chat_request(self, text):
        import uuid as _uuid
        bt_id = _uuid.uuid4().hex[:8]
        logger.info("[AGENT-UI] [%s] bottom_toolbar _dispatch_chat_request: text='%s'", bt_id, text[:80])
        self._chat_stop_event.clear()
        self._start_processing_indicator("Thinking")

        def _stop():
            return self._chat_stop_event.is_set()

        def worker():
            try:
                logger.info("[AGENT-UI] [%s] bottom_toolbar worker: calling handle_request...", bt_id)
                result = self._chat_controller.handle_request(text, stop_flag=_stop)
                logger.info("[AGENT-UI] [%s] bottom_toolbar worker: handle_request returned (%d chars)", bt_id, len(result) if result else 0)
                if result:
                    self._post_ui(
                        lambda r=result: self._add_chat_bubble(r, False)
                    )
                    self._post_ui(self._scroll_to_bottom)
            except Exception as e:
                logger.error(f"Chat dispatch error: {e}")
            finally:
                self._post_ui_sync(self._stop_processing_indicator)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
