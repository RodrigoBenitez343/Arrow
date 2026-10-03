"""Full-screen Agent Mode chat overlay for LoOper.

Replaces the graph UI with a large chat interface for natural-language
interaction with the LoOper agent.  Uses the same SimpleChainRouter
dispatch pattern as the original bottom toolbar.
"""

import base64
import io
import logging
import os
import sys
import threading

from player.agentic_ops import SimpleChainRouter
from PyQt5.QtCore import QMetaObject, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
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
    BAR_BG,
)
from ..i18n import _
from .agent_web_server import AgentWebServer

from AI.model_downloads import ModelDownloadTracker

logger = logging.getLogger(__name__)


class AgentModeWidget(QWidget):
    """Full-screen chat overlay for agent-mode interaction.

    Emits *back_to_build_requested* when the user clicks the back button;
    the main window should react by hiding this widget and showing the
    normal graph UI.
    """

    back_to_build_requested = pyqtSignal()
    web_status_updated = pyqtSignal()

    def __init__(self, graph_view=None, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self.setObjectName("agentModeWidget")
        self.setStyleSheet(
            f"QWidget#agentModeWidget {{ background-color: {DARK_GREY}; }}"
        )

        self._ui_queue: list = []
        self._processing_bubble = None
        self._processing_label = None
        self._processing_timer = None
        self._processing_dots = 0
        self._web_server_started = False

        # Download progress panel (hidden until models need downloading)
        self._download_panel = None
        self._download_poll_timer = QTimer(self)
        self._download_poll_timer.setInterval(500)
        self._download_poll_timer.timeout.connect(self._poll_download_progress)
        self._download_init_done = False

        # Simple chain router — same endpoint pattern as bottom toolbar
        self._chat_controller = SimpleChainRouter(
            chains_dir=self._resolve_chains_dir()
        )

        # Web server for remote chat
        self._web_server = AgentWebServer()
        self._web_server.set_handler(self._handle_web_message)

        # Poll timer to update QR / status from server thread
        self._server_poll_timer = QTimer(self)
        self._server_poll_timer.setInterval(2000)
        self._server_poll_timer.timeout.connect(self._poll_server_status)

        self._build_ui()

        # Start download tracking in background (won't block UI)
        QTimer.singleShot(0, self._init_download_tracking)

    @staticmethod
    def _resolve_chains_dir() -> str:
        """Resolve chains directory, handling frozen (PyInstaller) builds.

        In source mode this resolves relative to this file's location.
        In frozen mode we check alongside the executable first,
        then fall back to _internal/ (bundled).
        """
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            # 1) Preferred: next to the exe (user-created chains)
            primary = os.path.join(exe_dir, "chains")
            if os.path.isdir(primary):
                return primary
            # 2) Inside _internal/ (bundled with PyInstaller)
            internal = os.path.join(exe_dir, "_internal", "chains")
            if os.path.isdir(internal):
                return internal
            return primary  # doesn't exist yet — will log a warning
        # Source mode: chains/ is sibling of LoOper/ dir
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(project_root, "chains")

    def _build_download_panel(self) -> QFrame:
        """Create the download progress panel.

        Returns a QFrame (hidden by default) containing a progress bar
        and model-name label, updated by _poll_download_progress.
        """
        panel = QFrame(self)
        panel.setObjectName("agentDownloadPanel")
        panel.setStyleSheet(
            f"QFrame#agentDownloadPanel {{ background-color: {BLOCK_COLOR}; "
            f"border-bottom: 1px solid rgba(255,255,255,0.06); }}"
        )
        panel.setFixedHeight(56)

        p_layout = QHBoxLayout(panel)
        p_layout.setContentsMargins(20, 6, 20, 6)
        p_layout.setSpacing(12)

        # Icon
        icon_label = QLabel("\U0001f4e9", panel)
        icon_label.setStyleSheet("font-size: 16px;")
        p_layout.addWidget(icon_label, 0, Qt.AlignVCenter)

        # Text column: model name + size info
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)

        self._dl_model_label = QLabel(_("Downloading model..."), panel)
        self._dl_model_label.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 11px; font-weight: 600;"
        )
        text_col.addWidget(self._dl_model_label)

        self._dl_status_label = QLabel("", panel)
        self._dl_status_label.setStyleSheet(
            f"color: rgba(255,255,255,0.45); font-size: 10px;"
        )
        text_col.addWidget(self._dl_status_label)

        p_layout.addLayout(text_col, 1)

        # Progress bar
        self._dl_progress_bar = QProgressBar(panel)
        self._dl_progress_bar.setRange(0, 100)
        self._dl_progress_bar.setValue(0)
        self._dl_progress_bar.setFixedWidth(200)
        self._dl_progress_bar.setFixedHeight(20)
        self._dl_progress_bar.setObjectName("agentDlProgress")
        self._dl_progress_bar.setTextVisible(True)
        self._dl_progress_bar.setStyleSheet(
            f"QProgressBar#agentDlProgress {{"
            f"  background-color: rgba(255,255,255,0.06);"
            f"  border: 1px solid rgba(255,255,255,0.08);"
            f"  border-radius: 6px;"
            f"  text-align: center;"
            f"  color: {TEXT_COLOR};"
            f"  font-size: 10px;"
            f"}}"
            f"QProgressBar::chunk#agentDlProgress {{"
            f"  background-color: {ACCENT_COLOR};"
            f"  border-radius: 5px;"
            f"}}"
        )
        p_layout.addWidget(self._dl_progress_bar, 0, Qt.AlignVCenter)

        return panel

    def _init_download_tracking(self) -> None:
        """Register base models and start downloads for missing ones.

        Runs once on startup in a background thread to avoid blocking UI.
        """
        if self._download_init_done:
            return
        self._download_init_done = True

        def _worker():
            try:
                tracker = ModelDownloadTracker.get_instance()
                tracker.register_base_models()
                missing = tracker.get_missing_models()
                if missing:
                    logger.info(
                        "AgentMode: %d model(s) need downloading, starting...",
                        len(missing),
                    )
                    # Show the panel on next poll cycle
                    tracker.start_all_missing()
                    # Start the poll timer to track progress
                    self._download_poll_timer.start()
                else:
                    logger.info("AgentMode: all base models already present")
            except Exception as e:
                logger.error("AgentMode: download tracking init error: %s", e)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

    def _poll_download_progress(self) -> None:
        """Poll the download tracker and update the progress panel.

        Connected to _download_poll_timer (500ms).
        """
        try:
            tracker = ModelDownloadTracker.get_instance()
            is_dl, name, pct, all_done, has_err = tracker.get_summary()

            if all_done:
                # All models complete — hide panel and stop timer
                if self._download_panel.isVisible():
                    logger.info("AgentMode: all models downloaded, hiding progress")
                self._download_panel.hide()
                self._download_poll_timer.stop()
                return

            if not is_dl and not has_err:
                # No active download but some models still pending
                # Check again later (might be about to start)
                missing = tracker.get_missing_models()
                if not missing:
                    self._download_panel.hide()
                    self._download_poll_timer.stop()
                return

            # Show panel and update progress
            self._download_panel.show()

            if name:
                self._dl_model_label.setText(
                    _("Downloading: {}").format(name)
                )

            pct_int = int(round(pct))
            self._dl_progress_bar.setValue(pct_int)
            self._dl_progress_bar.setFormat(f"{pct_int}%")

            if has_err:
                self._dl_status_label.setText(
                    _("Download failed. Check logs for details.")
                )
            else:
                # Show size info if we have it
                states = tracker.get_all_states()
                total_mb = 0
                done_mb = 0
                for s in states:
                    if s.total_bytes > 0:
                        total_mb += s.total_bytes
                        if s.status.name in ("COMPLETE",):
                            done_mb += s.total_bytes
                        else:
                            done_mb += s.downloaded_bytes
                if total_mb > 0:
                    self._dl_status_label.setText(
                        _("{:.0f} / {:.0f} MB").format(
                            done_mb / (1024 * 1024),
                            total_mb / (1024 * 1024),
                        )
                    )
        except Exception as e:
            logger.debug("AgentMode: _poll_download_progress error: %s", e)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Top bar with back button and title ──
        top_bar = QFrame(self)
        top_bar.setObjectName("agentModeTopBar")
        top_bar.setStyleSheet(
            f"QFrame#agentModeTopBar {{ background-color: {BAR_BG}; border-bottom: 1px solid rgba(255,255,255,0.06); }}"
        )
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 6, 16, 6)
        top_layout.setSpacing(10)

        self._back_btn = QPushButton(_("\u2190 Back"), top_bar)
        self._back_btn.setCursor(Qt.PointingHandCursor)
        self._back_btn.setFixedHeight(30)
        self._back_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; padding: 0px 12px; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: {BLOCK_HOVER}; border-color: {ACCENT_COLOR}; }}"
        )
        self._back_btn.clicked.connect(self.back_to_build_requested.emit)
        top_layout.addWidget(self._back_btn, 0, Qt.AlignLeft)

        title = QLabel(_("Agent Mode"), top_bar)
        title.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 14px; font-weight: 600;")
        top_layout.addWidget(title, 0, Qt.AlignLeft)

        top_layout.addStretch(1)

        # ── Clear memory button ──
        self._clear_memory_btn = QPushButton(_("Clear"), top_bar)
        self._clear_memory_btn.setToolTip(_("Clear all agent memory / learned experiences"))
        self._clear_memory_btn.setCursor(Qt.PointingHandCursor)
        self._clear_memory_btn.setFixedHeight(26)
        self._clear_memory_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {LIGHT_GREY}; border: 1px solid rgba(255,255,255,0.10); border-radius: 6px; font-size: 11px; padding: 0px 10px; }}"
            f"QPushButton:hover {{ color: #ef4444; border-color: #ef4444; }}"
        )
        self._clear_memory_btn.clicked.connect(self._clear_memory)
        top_layout.addWidget(self._clear_memory_btn, 0, Qt.AlignRight)

        models_label = QLabel(
            "vision: LFM2.5-VL-450M  |  reasoning: gemma4:4b",
            top_bar,
        )
        models_label.setStyleSheet(f"color: rgba(255,255,255,0.35); font-size: 11px;")
        top_layout.addWidget(models_label, 0, Qt.AlignRight)

        layout.addWidget(top_bar)

        # ── Web connection panel (QR code + tunnel status) ──
        self._conn_panel = QFrame(self)
        self._conn_panel.setObjectName("agentConnPanel")
        self._conn_panel.setStyleSheet(
            f"QFrame#agentConnPanel {{ background-color: {BLOCK_COLOR}; "
            f"border-bottom: 1px solid rgba(255,255,255,0.06); }}"
        )
        conn_layout = QVBoxLayout(self._conn_panel)
        conn_layout.setContentsMargins(16, 8, 16, 8)
        conn_layout.setSpacing(6)

        # ── Top row: label + server toggle ──
        conn_top = QHBoxLayout()
        conn_top.setContentsMargins(0, 0, 0, 0)

        conn_label = QLabel("\U0001f310 " + _("Web Connect"), self._conn_panel)
        conn_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px; font-weight: 600;")
        conn_top.addWidget(conn_label)

        conn_top.addStretch(1)

        self._server_status_label = QLabel(_("Stopped"), self._conn_panel)
        self._server_status_label.setStyleSheet(
            f"color: rgba(255,255,255,0.4); font-size: 11px;"
        )
        conn_top.addWidget(self._server_status_label)

        self._server_toggle_btn = QPushButton(_("Start Server"), self._conn_panel)
        self._server_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._server_toggle_btn.setFixedHeight(26)
        self._server_toggle_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #22c55e; "
            f"border: 1px solid #22c55e; border-radius: 6px; padding: 0px 12px; "
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(34,197,94,0.12); }}"
        )
        self._server_toggle_btn.clicked.connect(self._toggle_web_server)
        conn_top.addWidget(self._server_toggle_btn)

        conn_layout.addLayout(conn_top)

        # ── QR code image ──
        self._qr_label = QLabel(self._conn_panel)
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setFixedSize(180, 180)
        self._qr_label.hide()
        conn_layout.addWidget(self._qr_label, 0, Qt.AlignCenter)

        # ── Public URL ──
        self._url_label = QLabel(self._conn_panel)
        self._url_label.setAlignment(Qt.AlignCenter)
        self._url_label.setStyleSheet(
            f"color: #60a5fa; font-size: 11px; "
            f"padding: 2px 0px;"
        )
        self._url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._url_label.hide()
        conn_layout.addWidget(self._url_label, 0, Qt.AlignCenter)

        # ── Status row: dot + text + client count ──
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        status_row.addStretch(1)

        self._status_dot = QLabel(self._conn_panel)
        self._status_dot.setFixedSize(8, 8)
        self._status_dot.setStyleSheet(
            "background-color: #ef4444; border-radius: 4px;"
        )
        status_row.addWidget(self._status_dot)

        self._status_text = QLabel(_("Server not started"), self._conn_panel)
        self._status_text.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 11px;")
        status_row.addWidget(self._status_text)

        self._clients_label = QLabel(self._conn_panel)
        self._clients_label.setStyleSheet(f"color: rgba(255,255,255,0.35); font-size: 10px;")
        self._clients_label.hide()
        status_row.addWidget(self._clients_label)

        status_row.addStretch(1)
        conn_layout.addLayout(status_row)

        layout.addWidget(self._conn_panel)

        # ── Download progress panel (hidden until a model needs downloading) ──
        self._download_panel = self._build_download_panel()
        self._download_panel.hide()
        layout.addWidget(self._download_panel)

        # ── Chat area ──
        chat_frame = QFrame(self)
        chat_frame.setObjectName("agentChatFrame")
        chat_frame.setStyleSheet(
            f"QFrame#agentChatFrame {{ background-color: {DARK_GREY}; }}"
        )
        chat_layout = QVBoxLayout(chat_frame)
        chat_layout.setContentsMargins(32, 12, 32, 12)
        chat_layout.setSpacing(10)

        self._chat_scroll = QScrollArea(chat_frame)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_scroll.setStyleSheet(
            "QScrollArea { background-color: transparent; border: 0px; }"
            f"QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0px; border: none; }}"
            f"QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.10); min-height: 24px; border-radius: 4px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.18); }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
        )

        self._chat_container = QWidget(self._chat_scroll)
        self._chat_container.setObjectName("agentChatContainer")
        self._chat_container.setStyleSheet(
            f"QWidget#agentChatContainer {{ background-color: transparent; }}"
        )
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(0, 0, 0, 0)
        self._chat_layout.setSpacing(10)
        self._chat_layout.setAlignment(Qt.AlignTop)

        self._placeholder_label = QLabel(
            _("Ask me anything about LoOper automation\u2026"), self._chat_container
        )
        self._placeholder_label.setAlignment(Qt.AlignCenter)
        self._placeholder_label.setStyleSheet(
            f"color: rgba(255,255,255,0.25); font-size: 14px; padding: 24px;"
        )
        self._chat_layout.addWidget(self._placeholder_label, 0, Qt.AlignCenter)

        self._chat_scroll.setWidget(self._chat_container)
        chat_layout.addWidget(self._chat_scroll, 1)

        layout.addWidget(chat_frame, 1)

        # ── Input area ──
        input_frame = QFrame(self)
        input_frame.setObjectName("agentInputFrame")
        input_frame.setStyleSheet(
            f"QFrame#agentInputFrame {{ background-color: {BAR_BG}; "
            f"border-top: 1px solid rgba(255,255,255,0.06); }}"
        )
        input_layout = QHBoxLayout(input_frame)
        input_layout.setContentsMargins(32, 10, 32, 12)
        input_layout.setSpacing(8)

        self._input_field = QTextEdit(input_frame)
        self._input_field.setPlaceholderText(_("Type a message\u2026"))
        self._input_field.setMinimumHeight(44)
        self._input_field.setMaximumHeight(140)
        self._input_field.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._input_field.setStyleSheet(
            f"QTextEdit {{ background-color: {SIDE_PANEL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 10px; padding: 10px 14px; "
            f"font-size: 13px; }}"
            f"QTextEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        # Enter sends, Shift+Enter inserts newline
        self._input_field.installEventFilter(self)
        self._input_field.textChanged.connect(self._update_input_height)
        input_layout.addWidget(self._input_field, 1)

        self._send_btn = QPushButton(_("Send"), input_frame)
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setFixedHeight(36)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: #0a0a0a; "
            f"border: 0px; border-radius: 10px; padding: 8px 20px; font-size: 13px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: #4fdcf0; }}"
            f"QPushButton:pressed {{ background-color: #0ea5b7; }}"
        )
        self._send_btn.clicked.connect(self._handle_send)
        input_layout.addWidget(self._send_btn, 0)

        layout.addWidget(input_frame)

        QTimer.singleShot(0, self._update_input_height)

    # ── Hide / show overrides ──

    def hideEvent(self, event):
        """Auto-stop the web server when hiding (leaving agent mode)."""
        if self._web_server_started:
            self._stop_web_server()
        if self._download_poll_timer.isActive():
            self._download_poll_timer.stop()
        super().hideEvent(event)

    # ── Resize ──

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._apply_bubble_widths)
        except Exception:
            pass

    # ── Chat bubble helpers (mirror bottom toolbar style) ──

    def _add_chat_bubble(self, text, is_user, bubble_color=None):
        if self._placeholder_label is not None:
            self._placeholder_label.hide()
        bubble = QFrame(self._chat_container)
        if bubble_color is None:
            bubble_color = BLOCK_COLOR if is_user else SIDE_PANEL_BG
        bubble.setStyleSheet(
            f"QFrame {{ background-color: {bubble_color}; border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; }}"
        )
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(14, 10, 14, 10)
        bubble_layout.setSpacing(0)
        label = QLabel(text, bubble)
        label.setObjectName("chatBubbleLabel")
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        # Set font programmatically so fontMetrics() is immediately correct
        font = label.font()
        font.setPixelSize(13)
        label.setFont(font)
        label.setStyleSheet(f"color: {TEXT_COLOR};")
        max_width = self._bubble_max_width()
        label_width = self._apply_label_width(label, max_width)
        bubble_layout.addWidget(label)
        bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        bubble.setFixedWidth(label_width + 28)
        align = Qt.AlignRight if is_user else Qt.AlignLeft
        self._chat_layout.addWidget(bubble, 0, align)

    def _scroll_to_bottom(self):
        try:
            bar = self._chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

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
            text_width = fm.horizontalAdvance(text)
            target = min(max_width, text_width + 16)
            if target < 160:
                target = min(max_width, 160)
            label.setFixedWidth(target)
            return target
        except Exception:
            return 160

    def _apply_bubble_widths(self):
        try:
            max_width = self._bubble_max_width()
            for label in self._chat_container.findChildren(QLabel, "chatBubbleLabel"):
                label_width = self._apply_label_width(label, max_width)
                parent = label.parentWidget()
                if parent:
                    parent.setFixedWidth(label_width + 28)
        except Exception:
            pass

    def _update_input_height(self):
        try:
            doc = self._input_field.document()
            line_height = self._input_field.fontMetrics().lineSpacing()
            max_lines = 10
            min_height = 48
            doc_height = doc.size().height()
            target = int(doc_height + 12)
            if target < min_height:
                target = min_height
            max_height = max_lines * line_height + 20
            if target > max_height:
                target = max_height
            self._input_field.setFixedHeight(target)
        except Exception:
            pass

    def _clear_memory(self):
        try:
            self._add_chat_bubble(_("Memory system not yet implemented."), False)
            self._scroll_to_bottom()
        except Exception:
            logger.exception("_clear_memory: error")

    # ── Web server lifecycle ──

    def _toggle_web_server(self):
        if self._web_server_started:
            self._stop_web_server()
        else:
            self._start_web_server()

    def _start_web_server(self):
        try:
            self._web_server.start()
            self._web_server_started = True
            self._server_toggle_btn.setText(_("Stop Server"))
            self._server_toggle_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: #ef4444; "
                f"border: 1px solid #ef4444; border-radius: 6px; padding: 0px 12px; "
                f"font-size: 11px; font-weight: 600; }}"
                f"QPushButton:hover {{ background-color: rgba(239,68,68,0.12); }}"
            )
            self._server_status_label.setText(_("Starting..."))
            self._status_text.setText(_("Starting local server..."))
            self._server_poll_timer.start()
            logger.info("Agent web server started")
        except Exception as e:
            logger.exception("Failed to start web server")
            self._add_chat_bubble(f"Failed to start web server: {e}", False, "#451a03")

    def _stop_web_server(self):
        try:
            self._server_poll_timer.stop()
            self._web_server.stop()
        except Exception:
            pass
        self._web_server_started = False
        self._server_toggle_btn.setText(_("Start Server"))
        self._server_toggle_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #22c55e; "
            f"border: 1px solid #22c55e; border-radius: 6px; padding: 0px 12px; "
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(34,197,94,0.12); }}"
        )
        self._server_status_label.setText(_("Stopped"))
        self._status_dot.setStyleSheet("background-color: #ef4444; border-radius: 4px;")
        self._status_text.setText(_("Server stopped"))
        self._qr_label.clear()
        self._qr_label.hide()
        self._url_label.clear()
        self._url_label.hide()
        self._clients_label.hide()
        logger.info("Agent web server stopped")

    def _poll_server_status(self):
        """Periodic UI update with status from the server thread."""
        try:
            state = self._web_server.state

            if state.public_url:
                self._url_label.setText(state.public_url)
                self._url_label.show()
                if not self._qr_label.pixmap() or self._qr_label.pixmap().isNull():
                    self._update_qr_display(state.public_url)

            if state.tunnel_active and state.public_url:
                self._server_status_label.setText(_("Active"))
                self._status_dot.setStyleSheet(
                    "background-color: #22c55e; border-radius: 4px;"
                )
                self._status_text.setText(
                    _("Tunnel active \u2014 Scan QR code to connect")
                )
            elif state.tunnel_active:
                self._server_status_label.setText(_("Tunneling..."))
                self._status_dot.setStyleSheet(
                    "background-color: #f59e0b; border-radius: 4px;"
                )
                self._status_text.setText(_("Waiting for public URL..."))
            else:
                self._server_status_label.setText(_("Running"))
                self._status_dot.setStyleSheet(
                    "background-color: #f59e0b; border-radius: 4px;"
                )
                self._status_text.setText(
                    _("Local: http://localhost:8081")
                )

            if state.connected_clients > 0:
                self._clients_label.setText(
                    f"{state.connected_clients} client(s) connected"
                )
                self._clients_label.show()
            else:
                self._clients_label.hide()

        except Exception:
            logger.exception("_poll_server_status error")

    def _update_qr_display(self, url: str):
        """Generate and display a QR code from the public URL."""
        try:
            from .agent_web_server import _qr_data_uri
            data_uri = _qr_data_uri(url)
            if not data_uri:
                return
            base64_data = data_uri.split(",", 1)[1]
            img_data = base64.b64decode(base64_data)
            pixmap = QPixmap()
            pixmap.loadFromData(img_data, "PNG")
            scaled = pixmap.scaled(
                160, 160,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._qr_label.setPixmap(scaled)
            self._qr_label.show()
        except Exception:
            logger.exception("Failed to generate QR code display")

    def _handle_web_message(self, text: str) -> str:
        """Handle a message from the web chat client.

        Runs in the server's thread pool executor.  Must be thread-safe.
        """
        try:
            # Show user message in local chat
            self._post_ui(lambda t=text: (
                self._add_chat_bubble(t, True, BLOCK_COLOR)
            ))
            result = self._chat_controller.handle_request(text)
            if result:
                self._post_ui(lambda r=result: (
                    self._add_chat_bubble(r, False)
                ))
                self._post_ui(self._scroll_to_bottom)
            return result or _("(no response)")
        except Exception as e:
            logger.exception("_handle_web_message error")
            return f"Error: {e}"

    # ── Message send ──

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
            logger.exception("agent_mode: send error")

    def _dispatch_chat_request(self, text):
        """Dispatch the user's request via SimpleChainRouter (same as bottom toolbar)."""
        def worker():
            try:
                result = self._chat_controller.handle_request(text)
                if result:
                    self._post_ui(
                        lambda r=result: self._add_chat_bubble(r, False)
                    )
                    self._post_ui(self._scroll_to_bottom)
            except Exception as e:
                logger.error(f"Chat dispatch error: {e}")

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    # ── Thread-safe UI posting ──

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
                logger.exception("_drain_ui_queue: callback error")

    def _force_ui_flush(self):
        try:
            QApplication.processEvents()
        except Exception:
            pass

    # ── Processing indicator ──

    def _start_processing_indicator(self, text):
        def _start():
            if self._processing_timer:
                self._processing_timer.stop()
            self._processing_dots = 0
            self._processing_bubble = QFrame(self._chat_container)
            self._processing_bubble.setStyleSheet(
                f"QFrame {{ background-color: {SIDE_PANEL_BG}; border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; }}"
            )
            layout = QVBoxLayout(self._processing_bubble)
            layout.setContentsMargins(14, 10, 14, 10)
            layout.setSpacing(0)
            self._processing_label = QLabel(text, self._processing_bubble)
            self._processing_label.setObjectName("chatBubbleLabel")
            self._processing_label.setWordWrap(True)
            self._processing_label.setSizePolicy(
                QSizePolicy.Fixed, QSizePolicy.Preferred
            )
            self._processing_label.setStyleSheet(f"color: rgba(255,255,255,0.6);")
            max_width = self._bubble_max_width()
            label_width = self._apply_label_width(self._processing_label, max_width)
            layout.addWidget(self._processing_label)
            self._processing_bubble.setSizePolicy(
                QSizePolicy.Fixed, QSizePolicy.Preferred
            )
            self._processing_bubble.setFixedWidth(label_width + 28)
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
        label_width = self._apply_label_width(self._processing_label, max_width)
        if self._processing_bubble:
            self._processing_bubble.setFixedWidth(label_width + 28)

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

    # ── Window helpers ──

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

    # ── Event filter for Enter key ──

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent

        if obj is self._input_field and event.type() == QEvent.KeyPress:
            from PyQt5.QtGui import QKeyEvent

            ke = QKeyEvent(event)
            if ke.key() in (Qt.Key_Return, Qt.Key_Enter):
                if ke.modifiers() & Qt.ShiftModifier:
                    return False
                self._handle_send()
                return True
        return super().eventFilter(obj, event)
