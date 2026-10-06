"""Semi-transparent draggable overlay for Agent Mode chat.

Floats at the top-right corner of the screen as a rounded rectangle.
Toggleable via Ctrl+A shortcut.  Reuses SimpleChainRouter for
agent dispatch — same backend as the full-screen agent mode.
"""

import base64
import hashlib
import json
import logging
import os
import threading

from PyQt5.QtCore import (
    QEvent,
    QMetaObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
    pyqtSlot,
)
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
    SIDE_PANEL_BG,
    TEXT_COLOR,
)
from ..i18n import _
from ..icons import tabler_qicon
from .agent_web_server import AgentWebServer, detect_tailscale_ip
from .hover_button import HoverGlowButton
from .memory_graph_panel import MemoryGraphPanel
from .scheduler_panel import SchedulerPanel

# NOTE: AgentExportDialog is imported lazily inside _open_export_dialog().
# Importing it at module level pulls in NGUI/dialogs/__init__.py, which imports
# every dialog (incl. code_node_dialog → NGUI.nodes_resources → NodeGraphQt),
# breaking the standalone agent build where NodeGraphQt is excluded.

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────
OVERLAY_WIDTH = 500          # wide horizontal for compact look
EXPANDED_WIDTH = 620         # expanded: coworker sidebar + chat side by side
SIDEBAR_WIDTH = 152          # left rail listing the System chains (coworkers)
OVERLAY_HEIGHT = 480
COMPACT_HEIGHT = 96          # header(40) + input_bar(54) + tiny margin
OVERLAY_MARGIN = 16          # pixels from screen edge
CORNER_RADIUS = 16
SERVER_PANEL_HEIGHT = 288    # enough room for IP row + status + button + QR + URL

# Semi-transparent dark background (rgba with alpha ~232/255)
BG_RGBA = "rgba(11, 14, 18, 232)"
HEADER_RGBA = "rgba(18, 22, 28, 232)"

# Chat bubble colours (modern rounded chat look)
USER_BUBBLE_BG = "rgba(34, 211, 238, 0.16)"
USER_BUBBLE_BORDER = "rgba(34, 211, 238, 0.28)"
AGENT_BUBBLE_BG = "rgba(255, 255, 255, 0.05)"
AGENT_BUBBLE_BORDER = "rgba(255, 255, 255, 0.07)"

# Vector avatars each System chain gets, so coworkers read as people rather
# than as a bare chain name. Picked deterministically (stable per chain).
_AVATAR_PX = 18
_COWORKER_AVATARS = (
    "ROBOT", "USER_CIRCLE", "STAR", "ROCKET", "BOLT", "BULB", "WAND", "PUZZLE",
    "GHOST", "PLANET", "CAMERA", "CHART_BAR", "SHIELD", "COIN", "CLOUD", "MUG",
    "LEAF", "CROWN", "SPARKLES", "BRAIN", "HEART", "CAT", "FLAME", "MOON",
    "SUN", "TREE", "DIAMOND", "BATTERY",
)


def _avatar_name(seed: str) -> str:
    """Stable pseudo-random avatar for a coworker (same chain -> same icon)."""
    try:
        idx = int(hashlib.md5(str(seed).encode("utf-8")).hexdigest(), 16)
    except Exception:
        idx = sum(ord(ch) for ch in str(seed))
    return _COWORKER_AVATARS[idx % len(_COWORKER_AVATARS)]

# ── Persistent agent-mode remote IP (Tailscale/LAN) ──────────────────────
_AGENT_IP_KEY = "AGENT_TAILSCALE_IP"


def _agent_config_file() -> str:
    """Config json the agent mode shares with the rest of the app."""
    try:
        from AI.config_loader import get_config_path
        return get_config_path()
    except Exception:
        # Source-tree fallback: <project>/LoOper/AI/config.json
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "AI", "config.json",
        )


def _load_agent_ip() -> str:
    """Load the persisted remote IP (empty string when unset)."""
    try:
        with open(_agent_config_file(), encoding="utf-8") as fh:
            cfg = json.load(fh)
        return str(cfg.get(_AGENT_IP_KEY, "") or "").strip()
    except Exception:
        return ""


def _save_agent_ip(ip: str) -> None:
    """Persist the remote IP, preserving the other config keys."""
    try:
        path = _agent_config_file()
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            cfg = {}
        cfg[_AGENT_IP_KEY] = ip.strip()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning("Agent mode: could not persist remote IP (%s)", exc)


class SpectrogramBar(QWidget):
    """Animated spectrogram for voice input / output activity.

    Procedural animation (no audio analysis) — it signals that the agent is
    listening or speaking, mirroring the web client's input/output spectrogram.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setMinimumWidth(120)
        self._levels = [0.0] * 56
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self.show()
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()
        self._levels = [0.0] * len(self._levels)
        self.update()
        self.hide()

    def _tick(self):
        import math
        self._phase += 0.25
        n = len(self._levels)
        self._levels = self._levels[1:] + [0.0]
        self._levels[-1] = min(
            1.0,
            0.25 + 0.75 * abs(math.sin(self._phase * 1.7))
            * abs(math.sin(self._phase * 0.5 + n)),
        )
        self.update()

    def paintEvent(self, event):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            w = float(self.width())
            h = float(self.height())
            n = max(1, len(self._levels))
            bw = max(1.0, w / n)
            color = QColor(ACCENT_COLOR)
            for i, level in enumerate(self._levels):
                bh = max(1.0, level * (h - 2))
                painter.fillRect(
                    QRectF(i * bw + 1, h - bh, max(1.0, bw - 2), bh), color
                )
            painter.end()
        except Exception:
            pass


class AgentOverlay(QFrame):
    """Floating, frameless, semi-transparent agent-chat overlay.

    Shows on top of other windows, draggable by its header bar,
    toggled via :meth:`toggle` (called by Ctrl+A).
    """

    closed = pyqtSignal()
    collapsed = pyqtSignal()
    execution_state_changed = pyqtSignal(bool)  # True=executing, False=done

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentOverlay")

        # ── Window flags: frameless, always-on-top, tool (no taskbar) ──
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)

        # ── Size & initial position — top-right corner ──
        self.setFixedSize(OVERLAY_WIDTH, OVERLAY_HEIGHT)
        self._position_top_right()

        # ── Expand/collapse state ──
        self._compact = True

        # ── Chat state ──
        self._chat_controller = None  # set via set_chat_controller()
        self._scheduler_service = None  # set via set_scheduler_service()
        self._ui_queue: list = []
        self._stop_event = threading.Event()
        # Per-request stop flag + generation counter.  A request owns its own
        # Event so a stop stays latched while the old worker unwinds — the
        # shared _stop_event is cleared on the next dispatch, so it alone
        # cannot carry a stop across requests.  The counter lets a stopped
        # worker detect that it is stale and skip its bookkeeping.
        self._request_stop = None
        self._request_seq = 0
        self._stop_requested = False
        self._thinking_label = None
        self._placeholder = None

        # ── Per-coworker conversations (one per System chain) ──
        # chain_id -> [{text, is_user, mode, asset}]; the sidebar switches
        # which transcript is shown and which System chain answers.
        self._conversations: dict = {}
        self._active_chain_id = ""
        # Chain that owns the in-flight request.  Bubbles produced by that
        # request (outputs, ask prompts, the final reply) are recorded in ITS
        # conversation even if the user switches coworker mid-run — otherwise
        # the old chain's messages landed in the newly selected transcript.
        self._request_chain_id = ""
        self._title_label = None

        # Input-asking mechanism: blocks chain execution until user responds
        self._input_ask_event = threading.Event()
        self._input_ask_response = None
        self._input_ask_active = False

        # ── Web server state ──
        self._web_server = AgentWebServer()
        self._web_server.set_handler(self._handle_web_message)
        self._web_server_started = False
        # True while collapsing to the dot: agent mode stays active, so the
        # web server must survive the hide (mobile clients keep working).
        self._keep_web_server = False
        self._server_poll_timer = QTimer(self)
        self._server_poll_timer.setInterval(2000)
        self._server_poll_timer.timeout.connect(self._poll_server_status)

        # ── Drag state ──
        self._dragging = False
        self._drag_offset = QPoint()

        # ── Fullscreen state ──
        self._is_fullscreen = False
        self._saved_geometry = None
        # True when presented as a child filling the main window (in-window agent
        # mode) instead of a floating always-on-top window.
        self._embedded = False

        # ── Automation hide state ──
        self._was_visible_for_automation = False
        self._window_minimize_cb = None
        self._window_restore_cb = None

        # ── Per-output rating state ──
        self._pending_output_ratings = {}  # seq → {tool_chain_path, tool_alias, content}
        self._output_seq = 0  # incrementing counter for per-output tracking
        self._last_callback_content = ""  # last mid-execution output shown via callback
        self._last_known_goal_id = ""  # goal_id from most recent chain execution
        self._last_user_request = ""  # the request text that triggered the run
        self._last_output_chain = {}  # {path, alias} of the most recent tool output

        # ── Web-aware ask-user state (for tunnel/mobile clients) ──
        self._web_ask_event = threading.Event()
        self._web_ask_response = None
        self._web_ask_active = False
        self._last_ask_question = ""  # re-announced to (re)connecting clients

        # ── Voice mode (STT) state ──
        self._voice_mode = False
        self._recording = False
        self._mic_thread = None
        self._mic_chunks: list = []
        self._executing = False

        # ── Build UI ──
        self._build_ui()
        # Start compact before first show
        self._compact = True
        self._body.hide()
        self.setFixedSize(OVERLAY_WIDTH, COMPACT_HEIGHT)

    # ── Positioning ──────────────────────────────────────────────────

    def _position_top_right(self):
        try:
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x = geo.right() - self.width() - OVERLAY_MARGIN
                y = geo.top() + OVERLAY_MARGIN
                self.move(x, y)
        except Exception:
            self.move(100, 100)

    # ── Chat controller ──────────────────────────────────────────────

    def set_chat_controller(self, controller):
        """Inject the SimpleChainRouter instance for message dispatch."""
        self._chat_controller = controller
        controller.set_ask_user_callback(self._make_ask_user_callback())
        try:
            controller.set_ask_user_v2_callback(self._make_ask_user_v2_callback())
        except AttributeError:
            # Older stub controllers predate the v2 channel.
            pass
        controller.set_output_display_callback(self._make_output_display_callback())
        self._refresh_coworkers()

    # ── Coworkers (System chains) ────────────────────────────────────

    def _refresh_coworkers(self):
        """Repopulate the sidebar from the System chains on disk.

        Also resolves a default selection (the first System chain) so a
        message always has a chain to run even before the user clicks one.
        """
        try:
            ctrl = self._chat_controller
            chains = ctrl.list_system_chains() if ctrl else []
            # Resolve a valid selection: default to the first System chain
            # when none is set or the selected chain file was removed.
            paths = {os.path.normpath(c["path"]) for c in chains}
            if ctrl and chains and (
                not ctrl._system_chain_path
                or ctrl._system_chain_path not in paths
            ):
                ctrl.set_system_chain(chains[0]["path"])
            new_active = (ctrl._system_chain_id if ctrl else "") or ""

            listw = self._coworker_list
            listw.blockSignals(True)
            listw.clear()
            if not chains:
                empty = QListWidgetItem(_("No System chains"))
                empty.setFlags(Qt.NoItemFlags)
                listw.addItem(empty)
            for c in chains:
                item = QListWidgetItem(c["name"])
                item.setData(Qt.UserRole, c["path"])
                item.setData(Qt.UserRole + 1, c["id"])
                item.setToolTip(c["description"] or c["name"])
                # A stable vector avatar so coworkers read as people.
                try:
                    _ico = tabler_qicon(_avatar_name(c["id"] or c["path"]),
                                        _AVATAR_PX, ACCENT_COLOR)
                    if not _ico.isNull():
                        item.setIcon(_ico)
                except Exception:
                    pass
                listw.addItem(item)
                if c["id"] == new_active:
                    listw.setCurrentItem(item)
            listw.blockSignals(False)

            if new_active != self._active_chain_id:
                self._active_chain_id = new_active
                self._render_active_conversation()
            self._update_title()
        except Exception:
            logger.exception("_refresh_coworkers: error")

    def _reload_system_chains(self):
        """Re-read System chains from disk and apply them to agent mode now.

        Lets chain edits (added/renamed/removed System chains, changed
        descriptions) take effect without leaving and re-entering agent mode.
        """
        self._refresh_coworkers()
        logger.info(
            "Agent mode: reloaded system chains (active: '%s')",
            self._active_chain_id or "<none>",
        )

    def _on_coworker_selected(self, item, _previous=None):
        """Switch the active System chain and show its own transcript."""
        try:
            if item is None:
                return
            path = item.data(Qt.UserRole)
            chain_id = item.data(Qt.UserRole + 1) or ""
            if not path or chain_id == self._active_chain_id:
                return
            if self._chat_controller:
                self._chat_controller.set_system_chain(path)
            self._active_chain_id = chain_id
            self._update_title()
            self._render_active_conversation()
            self._input_field.setFocus()
        except Exception:
            logger.exception("_on_coworker_selected: error")

    def current_chain_avatar(self) -> str:
        """Tabler icon name for the SELECTED system chain ("" when none).

        The compact notch shows this so the user can see who they are talking
        to.  It is the same avatar the coworker list uses, so the two always
        agree (see _avatar_name).
        """
        chain_id = getattr(self, '_active_chain_id', '') or ''
        return _avatar_name(chain_id) if chain_id else ""

    def _update_title(self):
        """Reflect the active coworker in the header title."""
        try:
            item = self._coworker_list.currentItem()
            name = item.text() if item is not None else ""
            self._title_label.setText(name or _("Agent"))
        except Exception:
            pass

    # ── UI build ─────────────────────────────────────────────────────

    def _build_ui(self):
        # Root layout — no margins, no spacing
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Container frame — provides rounded corners + bg ──
        container = QFrame(self)
        container.setObjectName("overlayContainer")
        container.setStyleSheet(
            f"QFrame#overlayContainer {{"
            f"  background-color: {BG_RGBA};"
            f"  border: 1px solid rgba(255,255,255,0.08);"
            f"  border-radius: {CORNER_RADIUS}px;"
            f"}}"
        )
        cont_layout = QVBoxLayout(container)
        cont_layout.setContentsMargins(0, 0, 0, 0)
        cont_layout.setSpacing(0)

        # ── Header bar (draggable) ──
        cont_layout.addWidget(self._build_header())

        # ── Server panel (collapsible, hidden by default) ──
        self._server_panel = self._build_server_panel()
        self._server_panel.hide()
        cont_layout.addWidget(self._server_panel)

        # ── Body: coworker sidebar + scrollable chat area ──
        cont_layout.addWidget(self._build_body(), 1)

        # ── Voice spectrogram (input + output activity) ──
        self._spectro = SpectrogramBar(container)
        cont_layout.addWidget(self._spectro)

        # ── Input bar ──
        cont_layout.addWidget(self._build_input_bar())

        root.addWidget(container)

        # ── Install event filter for Enter key and Ctrl+A on input ──
        self._input_field.installEventFilter(self)

        # The app-wide QWidget rule paints a DARK_GREY box behind labels; keep
        # every label in the overlay transparent so bubbles/text have no mat.
        self.setStyleSheet("QLabel { background: transparent; }")

    def _build_header(self) -> QFrame:
        header = QFrame(self)
        header.setObjectName("overlayHeader")
        header.setStyleSheet(
            f"QFrame#overlayHeader {{"
            f"  background-color: {HEADER_RGBA};"
            f"  border-top-left-radius: {CORNER_RADIUS}px;"
            f"  border-top-right-radius: {CORNER_RADIUS}px;"
            f"  border-bottom: 1px solid rgba(255,255,255,0.06);"
            f"}}"
        )
        header.setFixedHeight(40)
        # Capture mouse on header for dragging
        header.mousePressEvent = self._header_mouse_press
        header.mouseMoveEvent = self._header_mouse_move
        header.mouseReleaseEvent = self._header_mouse_release

        hlayout = QHBoxLayout(header)
        hlayout.setContentsMargins(14, 0, 8, 0)
        hlayout.setSpacing(8)

        # Right-side action cluster. MainWindow lifts this into the window's top
        # bar (right of the window controls), so the panel's own width can never
        # squeeze these buttons into each other.
        self._header_actions = QWidget(header)
        actions_lay = QHBoxLayout(self._header_actions)
        actions_lay.setContentsMargins(0, 0, 0, 0)
        actions_lay.setSpacing(8)

        # Back to GUI button (left side, beside title)
        self._gui_btn = QPushButton(header)
        self._gui_btn.setToolTip(_("Collapse agent mode"))
        self._gui_btn.setCursor(Qt.PointingHandCursor)
        self._gui_btn.setFixedSize(24, 24)
        _arrow_icon = self._make_icon_arrow_back(18)
        if not _arrow_icon.isNull():
            self._gui_btn.setIcon(_arrow_icon)
            self._gui_btn.setIconSize(QSize(18, 18))
        else:
            self._gui_btn.setText("\u2190")
        self._gui_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: transparent; color: rgba(255,255,255,0.5);"
            f"  border: 0px; border-radius: 12px; font-size: 16px;"
            f"}}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; background-color: rgba(255,255,255,0.06); }}"
        )
        self._gui_btn.clicked.connect(self._collapse_to_dot)
        hlayout.addWidget(self._gui_btn, 0, Qt.AlignVCenter)

        # ── Expand/collapse toggle button ──
        self._expand_btn = QPushButton(header)
        self._expand_btn.setToolTip(_("Expand overlay"))
        self._expand_btn.setCursor(Qt.PointingHandCursor)
        self._expand_btn.setFixedSize(24, 24)
        self._expand_btn.setIcon(self._make_icon_chevron(20, up=True))
        self._expand_btn.setIconSize(QSize(20, 20))
        self._expand_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: transparent; color: rgba(255,255,255,0.5);"
            f"  border: 0px; border-radius: 12px; font-size: 14px;"
            f"}}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; background-color: rgba(255,255,255,0.06); }}"
        )
        self._expand_btn.clicked.connect(self._toggle_expand)
        hlayout.addWidget(self._expand_btn, 0, Qt.AlignVCenter)

        # ── Reload system chains button ──
        self._reload_btn = QPushButton(header)
        self._reload_btn.setToolTip(_("Reload system chains"))
        self._reload_btn.setCursor(Qt.PointingHandCursor)
        self._reload_btn.setFixedSize(24, 24)
        self._reload_btn.setIcon(self._make_icon_reload(20))
        self._reload_btn.setIconSize(QSize(20, 20))
        self._reload_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 0px; border-radius: 12px; font-size: 15px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; background-color: rgba(255,255,255,0.06); }}"
        )
        self._reload_btn.clicked.connect(self._reload_system_chains)
        actions_lay.addWidget(self._reload_btn, 0, Qt.AlignVCenter)

        # Active coworker name (the System chain currently answering)
        self._title_label = QLabel(_("Agent"), header)
        self._title_label.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 13px; font-weight: 600;"
        )
        hlayout.addWidget(self._title_label, 0, Qt.AlignVCenter)

        hlayout.addStretch(1)
        hlayout.addWidget(self._header_actions)

        # ── Scheduler button (opens the full Scheduler dialog) ──
        self._scheduler_btn = QPushButton(header)
        self._scheduler_btn.setToolTip(_("Schedule chain runs"))
        self._scheduler_btn.setCursor(Qt.PointingHandCursor)
        self._scheduler_btn.setFixedSize(24, 24)
        _sched_icon = self._make_icon_calendar(20)
        if not _sched_icon.isNull():
            self._scheduler_btn.setIcon(_sched_icon)
            self._scheduler_btn.setIconSize(QSize(20, 20))
        else:
            self._scheduler_btn.setText("S")
        self._scheduler_btn.setCheckable(True)
        self._scheduler_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; padding: 2px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
            f"QPushButton:checked {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR};"
            f"background-color: rgba(34,211,238,0.10); }}"
        )
        self._scheduler_btn.clicked.connect(self._toggle_scheduler)
        self._scheduler_btn.hide()  # shown once a scheduler service is attached
        actions_lay.addWidget(self._scheduler_btn, 0, Qt.AlignVCenter)

        # ── Memory chat toggle (answers from recorded activity only) ──
        self._memory_btn = QPushButton("M", header)
        self._memory_btn.setToolTip(
            _("Memory chat — I only answer from recorded activity")
        )
        self._memory_btn.setCursor(Qt.PointingHandCursor)
        self._memory_btn.setFixedSize(24, 24)
        self._memory_btn.setCheckable(True)
        self._memory_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; padding: 2px;"
            f"font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
            f"QPushButton:checked {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR};"
            f"background-color: rgba(34,211,238,0.10); }}"
        )
        self._memory_btn.clicked.connect(self._toggle_memory_chat)
        actions_lay.addWidget(self._memory_btn, 0, Qt.AlignVCenter)

        # ── Memory graph toggle (recorded activity as a knowledge network) ──
        self._memory_graph_btn = QPushButton("G", header)
        self._memory_graph_btn.setToolTip(
            _("Knowledge graph — browse recorded memories (days, runs, chains)")
        )
        self._memory_graph_btn.setCursor(Qt.PointingHandCursor)
        self._memory_graph_btn.setFixedSize(24, 24)
        self._memory_graph_btn.setCheckable(True)
        self._memory_graph_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; padding: 2px;"
            f"font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
            f"QPushButton:checked {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR};"
            f"background-color: rgba(34,211,238,0.10); }}"
        )
        self._memory_graph_btn.clicked.connect(self._toggle_memory_graph)
        actions_lay.addWidget(self._memory_graph_btn, 0, Qt.AlignVCenter)

        # ── Export button ──
        self._gear_btn = QPushButton(header)
        self._gear_btn.setToolTip(_("Export agent as standalone executable"))
        self._gear_btn.setCursor(Qt.PointingHandCursor)
        self._gear_btn.setFixedSize(24, 24)
        _gear_icon = self._make_icon_gear(20)
        if not _gear_icon.isNull():
            self._gear_btn.setIcon(_gear_icon)
            self._gear_btn.setIconSize(QSize(20, 20))
        else:
            self._gear_btn.setText("\u2699")
        self._gear_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; padding: 2px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
        )
        self._gear_btn.clicked.connect(self._open_export_dialog)
        actions_lay.addWidget(self._gear_btn, 0, Qt.AlignVCenter)

        # ── Web server toggle button ──
        self._server_btn = QPushButton(header)
        self._server_btn.setToolTip(_("Toggle web server"))
        self._server_btn.setCursor(Qt.PointingHandCursor)
        self._server_btn.setFixedSize(24, 24)
        self._server_btn.setCheckable(True)
        _wifi_icon = self._make_icon_wifi(20)
        if not _wifi_icon.isNull():
            self._server_btn.setIcon(_wifi_icon)
            self._server_btn.setIconSize(QSize(20, 20))
        else:
            self._server_btn.setText("W")
        self._server_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; padding: 2px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
            f"QPushButton:checked {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR};"
            f"background-color: rgba(34,211,238,0.10); }} "
            f"QPushButton:checked:hover {{ background-color: rgba(34,211,238,0.18); }}"
        )
        self._server_btn.clicked.connect(self._toggle_server_panel)
        actions_lay.addWidget(self._server_btn, 0, Qt.AlignVCenter)

        # ── Fullscreen toggle button ──
        self._fullscreen_btn = QPushButton(header)
        self._fullscreen_btn.setToolTip(_("Fullscreen"))
        self._fullscreen_btn.setCursor(Qt.PointingHandCursor)
        self._fullscreen_btn.setFixedSize(24, 24)
        self._fullscreen_btn.setIcon(self._make_icon_fullscreen(20))
        self._fullscreen_btn.setIconSize(QSize(20, 20))
        self._fullscreen_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 12px; padding: 2px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
        )
        self._fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        hlayout.addWidget(self._fullscreen_btn, 0, Qt.AlignVCenter)

        # Collapse button — bigger, with a cyan fog + grow animation on hover.
        # In-window it collapses to the compact notch; floating it collapses to
        # the dot (_collapse_to_dot -> collapsed -> MainWindow).
        self._collapse_btn = HoverGlowButton(
            "CHEVRON_DOWN", size=32, icon=18, parent=header)
        self._collapse_btn.setToolTip(_("Collapse"))
        self._collapse_btn.clicked.connect(self._collapse_to_dot)
        hlayout.addWidget(self._collapse_btn, 0, Qt.AlignVCenter)

        return header

    def _build_body(self) -> QWidget:
        """Coworker sidebar (left) + chat transcript (right)."""
        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_chat_area(), 1)
        # Scheduler view shares the chat column (swapped in by the header button)
        self._scheduler_panel = SchedulerPanel(body)
        self._scheduler_panel.hide()
        self._scheduler_panel.close_requested.connect(self._toggle_scheduler)
        body_layout.addWidget(self._scheduler_panel, 1)
        # Knowledge-graph view of the recorded memory — same column swap.
        self._memory_graph_panel = MemoryGraphPanel(body)
        self._memory_graph_panel.hide()
        self._memory_graph_panel.close_requested.connect(self._toggle_memory_graph)
        body_layout.addWidget(self._memory_graph_panel, 1)
        self._body = body
        return body

    def _build_sidebar(self) -> QFrame:
        """Left rail listing the user's System chains (\"coworkers\")."""
        bar = QFrame(self)
        bar.setObjectName("agentSidebar")
        bar.setFixedWidth(SIDEBAR_WIDTH)
        bar.setStyleSheet(
            "QFrame#agentSidebar {"
            "  background-color: rgba(255,255,255,0.025);"
            "  border-right: 1px solid rgba(255,255,255,0.06);"
            "}"
        )
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(6)

        self._coworker_list = QListWidget(bar)
        self._coworker_list.setIconSize(QSize(_AVATAR_PX, _AVATAR_PX))
        self._coworker_list.setFrameShape(QFrame.NoFrame)
        self._coworker_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._coworker_list.setStyleSheet(
            "QListWidget { background: transparent; border: none; outline: 0; }"
            "QListWidget::item {"
            "  color: rgba(255,255,255,0.72); padding: 9px 10px;"
            "  border-radius: 8px; margin: 1px 0px; font-size: 12px;"
            "}"
            "QListWidget::item:hover { background: rgba(255,255,255,0.06); }"
            f"QListWidget::item:selected {{ background: rgba(34,211,238,0.15);"
            f"  color: {ACCENT_COLOR}; }}"
        )
        self._coworker_list.currentItemChanged.connect(self._on_coworker_selected)
        layout.addWidget(self._coworker_list, 1)

        return bar

    def _build_chat_area(self) -> QScrollArea:
        self._chat_scroll = QScrollArea(self)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_scroll.setStyleSheet(
            "QScrollArea { background-color: transparent; border: 0px; }"
            "QScrollBar:vertical { background: transparent; width: 6px; margin: 0px; border: none; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.10); min-height: 20px; border-radius: 3px; }"
            "QScrollBar::handle:vertical:hover { background: rgba(255,255,255,0.18); }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
        )

        self._chat_container = QWidget(self._chat_scroll)
        self._chat_container.setStyleSheet("background-color: transparent;")
        self._chat_layout = QVBoxLayout(self._chat_container)
        self._chat_layout.setContentsMargins(14, 10, 14, 10)
        self._chat_layout.setSpacing(8)
        self._chat_layout.setAlignment(Qt.AlignTop)

        # Placeholder
        self._placeholder = QLabel(
            _("Agent ready \u2014 type below to start"), self._chat_container
        )
        self._placeholder.setStyleSheet(
            f"color: rgba(255,255,255,0.35); font-size: 12px; padding: 20px;"
        )
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._chat_layout.addWidget(self._placeholder)

        self._chat_scroll.setWidget(self._chat_container)
        return self._chat_scroll

    def _build_input_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("overlayInputBar")
        bar.setStyleSheet(
            f"QFrame#overlayInputBar {{"
            f"  background-color: {HEADER_RGBA};"
            f"  border-bottom-left-radius: {CORNER_RADIUS}px;"
            f"  border-bottom-right-radius: {CORNER_RADIUS}px;"
            f"  border-top: 1px solid rgba(255,255,255,0.06);"
            f"}}"
        )
        bar.setFixedHeight(54)

        blayout = QHBoxLayout(bar)
        blayout.setContentsMargins(12, 8, 12, 8)
        blayout.setSpacing(6)

        # ── Chat / voice mode toggle ──
        self._voice_btn = QPushButton(bar)
        self._voice_btn.setCheckable(True)
        self._voice_btn.setToolTip(_("Switch to voice mode"))
        self._voice_btn.setCursor(Qt.PointingHandCursor)
        self._voice_btn.setFixedSize(30, 30)
        self._voice_btn.setIcon(self._make_icon_mic(20))
        self._voice_btn.setIconSize(QSize(20, 20))
        self._voice_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: rgba(255,255,255,0.5);"
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 15px; padding: 2px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
            f"QPushButton:checked {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR};"
            f"background-color: rgba(34,211,238,0.10); }} "
            f"QPushButton:checked:hover {{ background-color: rgba(34,211,238,0.18); }}"
        )
        self._voice_btn.clicked.connect(self._toggle_voice_mode)
        blayout.addWidget(self._voice_btn, 0)

        self._input_field = QTextEdit(bar)
        self._input_field.setPlaceholderText(_("Type a message\u2026"))
        self._input_field.setFixedHeight(36)
        self._input_field.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._input_field.setStyleSheet(
            f"QTextEdit {{"
            f"  background-color: rgba(0,0,0,0.25); color: {TEXT_COLOR};"
            f"  border: 1px solid rgba(255,255,255,0.08); border-radius: 10px;"
            f"  padding: 6px 10px; font-size: 12px;"
            f"}}"
            f"QTextEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        blayout.addWidget(self._input_field, 1)

        # ── Voice-mode controls (hidden until voice mode is on) ──
        self._mic_btn = QPushButton(_("Hold to Talk"), bar)
        self._mic_btn.setToolTip(_("Hold to record, release to transcribe"))
        self._mic_btn.setCursor(Qt.PointingHandCursor)
        self._mic_btn.setFixedHeight(30)
        self._mic_btn.hide()
        self._mic_btn.pressed.connect(self._start_recording)
        self._mic_btn.released.connect(self._stop_recording)
        blayout.addWidget(self._mic_btn, 0)

        self._voice_status = QLabel(_("Hold to talk"), bar)
        self._voice_status.setStyleSheet(
            f"color: rgba(255,255,255,0.5); font-size: 11px;"
        )
        self._voice_status.hide()
        blayout.addWidget(self._voice_status, 0)

        self._send_btn = QPushButton(_("Send"), bar)
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setFixedSize(50, 30)
        self._send_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {ACCENT_COLOR}; color: #0a0a0a;"
            f"  border: 0px; border-radius: 8px; font-size: 11px; font-weight: 700;"
            f"}}"
            f"QPushButton:hover {{ background-color: #4fdcf0; }}"
            f"QPushButton:pressed {{ background-color: #0ea5b7; }}"
        )
        self._send_btn.clicked.connect(self._handle_send)
        blayout.addWidget(self._send_btn, 0)

        # ── Stop button — hard-stops the running task (hidden while idle) ──
        self._stop_btn = QPushButton(_("Stop"), bar)
        self._stop_btn.setToolTip(_("Stop the running task"))
        self._stop_btn.setCursor(Qt.PointingHandCursor)
        self._stop_btn.setFixedSize(46, 30)
        self._stop_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #ef4444; color: #ffffff;"
            f"  border: 0px; border-radius: 8px; font-size: 11px; font-weight: 700;"
            f"}}"
            f"QPushButton:hover {{ background-color: #f87171; }}"
            f"QPushButton:pressed {{ background-color: #dc2626; }}"
        )
        self._stop_btn.clicked.connect(self._stop_execution)
        self._stop_btn.hide()
        blayout.addWidget(self._stop_btn, 0)

        return bar

    # ── Drag ─────────────────────────────────────────────────────────

    def _header_mouse_press(self, event):
        if self._embedded:
            return
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()

    def _header_mouse_move(self, event):
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_offset)

    def _header_mouse_release(self, event):
        self._dragging = False

    # ── Toggle ───────────────────────────────────────────────────────

    def toggle(self):
        """Show/hide the overlay.  Called by Ctrl+A shortcut."""
        if self.isVisible():
            self.hide_overlay()
        else:
            self.show_overlay()

    def show_overlay(self, x=None, y=None):
        """Show and raise the overlay, focus the input.

        Args:
            x: (optional) global X to place the overlay (skips _position_top_right).
            y: (optional) global Y to place the overlay.
        """
        # User explicitly showing — clear automation flag so _automation_restore
        # doesn't pop it back later.
        self._was_visible_for_automation = False
        self._keep_web_server = False
        # Always start in compact mode
        self._set_compact_mode(True)
        if x is not None and y is not None:
            self.move(x, y)
        else:
            self._position_top_right()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input_field.setFocus()
        # Second attempt after the window is fully shown + activated
        QTimer.singleShot(
            80, lambda: (self.activateWindow(), self._input_field.setFocus())
        )

    def hide_overlay(self):
        """Hide the overlay, stop any running agent loop."""
        self._stop_event.set()
        self._release_pending_asks()
        self._stop_recording()
        self._keep_web_server = False
        self._stop_web_server()
        self._was_visible_for_automation = False
        self.hide()
        self.closed.emit()

    # ── Automation hide/restore ──────────────────────────────────────

    def set_window_manager(self, minimize_cb=None, restore_cb=None):
        """Register callbacks to minimize/restore the main application window."""
        self._window_minimize_cb = minimize_cb
        self._window_restore_cb = restore_cb

    def embed(self, parent):
        """Present as a child filling *parent* (in-window agent mode) instead of
        a floating always-on-top window: drop the window flags and fixed size,
        and disable the floating-only affordances (drag, expand, fullscreen)."""
        self._embedded = True
        self.setParent(parent)
        try:
            self.setWindowFlags(Qt.Widget)
            self.setAttribute(Qt.WA_TranslucentBackground, False)
            self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        except Exception:
            pass
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        # Always expanded in-window; hide the floating-only header controls.
        self._compact = False
        try:
            self._body.show()
            self._expand_btn.hide()
            self._fullscreen_btn.hide()
            # Navigation lives in the base window's top bar, not here: hide the
            # overlay's own back button so the two chromes never compete.
            self._gui_btn.hide()
            self._collapse_btn.setToolTip(_("Collapse to the notch"))
        except Exception:
            pass
        # Keep the action wired (the base frame drives it) even though the
        # button is not shown in-window.
        try:
            self._gui_btn.clicked.disconnect()
        except Exception:
            pass
        self._gui_btn.clicked.connect(self._switch_to_gui)
        self._gui_btn.setToolTip(_("Back to build mode"))

    def detach_header_actions(self):
        """Hand the header's action cluster to the host window.

        MainWindow reparents it into the top bar (right of the window controls),
        where the layout cannot be squeezed by the panel's width. Returns the
        widget, or None if the header was never built.
        """
        widget = getattr(self, '_header_actions', None)
        if widget is None:
            return None
        widget.setParent(None)
        return widget

    def set_scheduler_service(self, service):
        """Attach the live SchedulerService and reveal the header button.

        The button stays hidden while no service is attached (e.g. the exported
        standalone agent has no scheduler).
        """
        self._scheduler_service = service
        try:
            self._scheduler_panel.set_service(service)
        except Exception:
            logger.exception("set_scheduler_service: panel init failed")
        try:
            self._scheduler_btn.setVisible(service is not None)
        except Exception:
            pass

    def _toggle_scheduler(self):
        """Swap the chat transcript for the scheduler panel (and back).

        Guarded: an exception raised inside a Qt slot aborts the whole app, so
        any panel error is logged and swallowed instead.
        """
        if self._scheduler_service is None:
            return
        try:
            # isHidden() reflects the explicit show/hide flag even when the
            # overlay is off-screen (isVisible() is always False there).
            show = self._scheduler_panel.isHidden()
            if show and self._compact:
                self._set_compact_mode(False)
            if show and not self._memory_graph_panel.isHidden():
                # one view at a time
                self._memory_graph_panel.hide()
                self._memory_graph_btn.setChecked(False)
            self._scheduler_panel.setVisible(show)
            self._chat_scroll.setVisible(not show)
            self._scheduler_btn.setChecked(show)
            if show:
                self._scheduler_panel.refresh()
        except Exception:
            logger.exception("_toggle_scheduler: error")

    def _toggle_memory_graph(self):
        """Swap the chat transcript for the recorded-memory knowledge graph.

        Views the same linear timeline the memory chat answers from
        (player/agentic_ops/run_memory.py) as a day -> run -> chain network;
        clicking a node audits its events.
        """
        try:
            show = self._memory_graph_panel.isHidden()
            if show and self._compact:
                self._set_compact_mode(False)
            if show and not self._scheduler_panel.isHidden():
                # one view at a time
                self._scheduler_panel.hide()
                self._scheduler_btn.setChecked(False)
            self._memory_graph_panel.setVisible(show)
            self._chat_scroll.setVisible(not show)
            self._memory_graph_btn.setChecked(show)
            if show:
                self._memory_graph_panel.refresh()
        except Exception:
            logger.exception("_toggle_memory_graph: error")

    def _toggle_memory_chat(self):
        """Toggle memory-only chat: requests answer from recorded activity.

        With it on, the chat never dispatches chains — it answers from the
        linear activity timeline (player/agentic_ops/run_memory.py).
        """
        on = bool(self._memory_btn.isChecked())
        try:
            if self._chat_controller is not None:
                self._chat_controller.set_memory_only(on)
        except Exception:
            logger.exception("_toggle_memory_chat: error")
        try:
            self._add_bubble(
                _("Memory chat ON — I only answer from recorded activity.") if on
                else _("Memory chat OFF — requests run chains again."),
                False,
            )
        except Exception:
            pass

    def _switch_to_gui(self):
        """Switch back to main GUI mode — hide overlay, do NOT stop server."""
        self._stop_recording()
        self._was_visible_for_automation = False
        self._keep_web_server = False
        self.hide()
        self.closed.emit()

    def _collapse_to_dot(self):
        """Collapse overlay back to minimal dot state (stay in agent mode)."""
        self._stop_recording()
        self._was_visible_for_automation = False
        # Still in agent mode: keep the web server up so a phone client is
        # not disconnected just because the desktop overlay got tucked away.
        self._keep_web_server = True
        self.hide()
        self.collapsed.emit()

    # ── Compact / Expand ──────────────────────────────────────────────

    def _set_compact_mode(self, compact: bool):
        """Toggle between compact (header+input only) and expanded (full chat)."""
        if self._embedded:
            # In-window: always expanded, the overlay fills its parent.
            self._compact = False
            try:
                self._body.show()
            except Exception:
                pass
            return
        self._compact = compact
        if compact:
            self._reset_fullscreen()
            self._body.hide()
            # Also hide any open server panel
            self._server_panel.hide()
            self._server_btn.setChecked(False)
            self.setFixedSize(OVERLAY_WIDTH, COMPACT_HEIGHT)
            self._expand_btn.setIcon(self._make_icon_chevron(20, up=True))
            self._expand_btn.setToolTip(_("Expand overlay"))
            # Show focus border on input field to indicate "ready to type"
            self._input_field.setStyleSheet(
                f"QTextEdit {{"
                f"  background-color: rgba(0,0,0,0.25); color: {TEXT_COLOR};"
                f"  border: 1px solid {ACCENT_COLOR}; border-radius: 10px;"
                f"  padding: 6px 10px; font-size: 12px;"
                f"}}"
            )
        else:
            self._body.show()
            self.setFixedSize(EXPANDED_WIDTH, OVERLAY_HEIGHT)
            self._ensure_within_bounds()
            self._expand_btn.setIcon(self._make_icon_chevron(20, up=False))
            self._expand_btn.setToolTip(_("Collapse overlay"))
            # Restore normal style (focus border only on actual focus)
            self._input_field.setStyleSheet(
                f"QTextEdit {{"
                f"  background-color: rgba(0,0,0,0.25); color: {TEXT_COLOR};"
                f"  border: 1px solid rgba(255,255,255,0.08); border-radius: 10px;"
                f"  padding: 6px 10px; font-size: 12px;"
                f"}}"
                f"QTextEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
            )
        # Re-trigger bubble width recalculation
        try:
            QTimer.singleShot(0, self._apply_bubble_widths)
        except Exception:
            pass

    def _toggle_expand(self):
        """Toggle between compact and expanded mode."""
        self._set_compact_mode(not self._compact)

    def _reset_fullscreen(self):
        """Leave fullscreen (if active) and restore the saved window position."""
        if not self._is_fullscreen:
            return
        self._is_fullscreen = False
        self._fullscreen_btn.setToolTip(_("Fullscreen"))
        if self._saved_geometry is not None:
            self.move(self._saved_geometry.topLeft())

    def _toggle_fullscreen(self):
        """Expand the overlay to fill the screen, or restore its windowed size."""
        if self._embedded:
            return
        try:
            if self._is_fullscreen:
                self._reset_fullscreen()
                self._set_compact_mode(False)
                return
            self._saved_geometry = self.geometry()
            self._set_compact_mode(False)  # make sure the chat is visible first
            screen = QApplication.primaryScreen()
            geo = screen.availableGeometry() if screen else self._saved_geometry
            self._is_fullscreen = True
            self.setFixedSize(geo.width(), geo.height())
            self.move(geo.topLeft())
            self._fullscreen_btn.setToolTip(_("Exit fullscreen"))
        except Exception:
            logger.exception("_toggle_fullscreen: error")

    def _ensure_within_bounds(self):
        """Adjust window position so it doesn't extend beyond the screen bottom."""
        try:
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                x, y = self.x(), self.y()
                w, h = self.width(), self.height()
                if y + h > geo.bottom():
                    y = max(geo.top(), geo.bottom() - h)
                if x + w > geo.right():
                    x = max(geo.left(), geo.right() - w)
                self.move(x, y)
        except Exception:
            pass

    def _automation_hide(self):
        """Hide overlay + main window for automation — no server stop side effects."""
        self._was_visible_for_automation = self.isVisible()
        if self._was_visible_for_automation:
            self.hide()
        if self._window_minimize_cb:
            self._window_minimize_cb()

    def _automation_restore(self):
        """Restore overlay expanded and restore main window if it was minimized."""
        if self._was_visible_for_automation:
            self._was_visible_for_automation = False
            # Restore expanded so the user sees the agent's response
            self._set_compact_mode(False)
            if not self._embedded:
                self._position_top_right()
            self.show()
            self.raise_()
            if not self._embedded:
                self.activateWindow()
                self._input_field.setFocus()
            # Restore main window if a restore callback was registered
            if self._window_restore_cb:
                try:
                    self._window_restore_cb()
                except Exception:
                    pass
            QTimer.singleShot(
                80, lambda: (self.activateWindow(), self._input_field.setFocus())
            )

    # ── Resize / hide events ────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._apply_bubble_widths)
        except Exception:
            pass

    def hideEvent(self, event):
        """Auto-stop the web server when hiding (unless for automation)."""
        if (not self._was_visible_for_automation and not self._keep_web_server
                and self._web_server_started):
            self._stop_web_server()
        super().hideEvent(event)

    # ── Event filter (Enter to send) ────────────────────────────────

    def eventFilter(self, obj, event):
        if obj is self._input_field and event.type() == QEvent.KeyPress:
            from PyQt5.QtGui import QKeyEvent
            ke = QKeyEvent(event)
            # Ctrl+A hides the overlay
            if ke.key() == Qt.Key_A and ke.modifiers() & Qt.ControlModifier:
                self.hide_overlay()
                return True
            if ke.key() == Qt.Key_Return or ke.key() == Qt.Key_Enter:
                if ke.modifiers() & Qt.ShiftModifier:
                    return False  # allow Shift+Enter newline
                self._handle_send()
                return True
            if ke.key() == Qt.Key_Escape:
                self._collapse_to_dot()
                return True
        return super().eventFilter(obj, event)

    # ── Send ─────────────────────────────────────────────────────────

    def _handle_send(self):
        text = self._input_field.toPlainText().strip()
        if not text:
            return
        self._input_field.clear()
        self._submit_text(text)

    def _submit_text(self, text: str):
        """Shared dispatch path for typed and voice-transcribed messages."""
        text = (text or "").strip()
        if not text:
            return

        # If the chain is currently asking for user input, capture the response
        # instead of dispatching a new request.
        if self._input_ask_active:
            self._input_ask_response = text
            self._input_ask_active = False
            self._add_bubble(text, is_user=True)
            self._scroll_to_bottom()
            self._input_ask_event.set()
            return

        self._add_bubble(text, is_user=True)
        self._scroll_to_bottom()
        self._dispatch(text)

    # ── Voice mode (STT) ─────────────────────────────────────────────

    def _toggle_voice_mode(self, checked=None):
        """Switch the input bar between chat (typed) and voice mode."""
        self._voice_mode = self._voice_btn.isChecked()
        if self._voice_mode:
            self._input_field.hide()
            self._send_btn.hide()
            self._mic_btn.show()
            self._voice_status.setText(_("Hold to talk"))
            self._voice_status.show()
        else:
            self._stop_recording()
            self._mic_btn.hide()
            self._voice_status.hide()
            self._input_field.show()
            self._send_btn.show()
            self._input_field.setFocus()

    # ── Voice-mode ask answers (talk directly to the question) ───────

    def _begin_voice_ask(self):
        """Switch the mic to tap-to-toggle and start listening.

        Runs on the UI thread when an Input node asks a question while voice
        mode is on: recording starts automatically so the user can answer by
        speaking; tapping the mic button stops and submits the answer.
        """
        try:
            self._mic_btn.pressed.disconnect(self._start_recording)
            self._mic_btn.released.disconnect(self._stop_recording)
        except Exception:
            pass
        self._mic_btn.setText(_("Tap to Stop"))
        self._mic_btn.clicked.connect(self._toggle_ask_recording)
        self._voice_status.setText(_("Listening\u2026 speak your answer"))
        self._start_recording()

    def _end_voice_ask(self):
        """Restore hold-to-talk mic behavior after the ask completes."""
        try:
            self._mic_btn.clicked.disconnect(self._toggle_ask_recording)
        except Exception:
            pass
        self._mic_btn.setText(_("Hold to Talk"))
        self._mic_btn.pressed.connect(self._start_recording)
        self._mic_btn.released.connect(self._stop_recording)
        if self._recording:
            self._stop_recording()
        self._voice_status.setText(_("Hold to talk"))

    def _toggle_ask_recording(self):
        """Tap-to-toggle used while an ask is pending in voice mode."""
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        """Begin capturing microphone audio (push-to-talk)."""
        if self._recording:
            return
        # Allow recording while the chain is asking for input (voice-mode
        # answers) — only block on a genuinely busy agent otherwise.
        if self._executing and not self._input_ask_active:
            self._voice_status.setText(_("Agent busy \u2014 please wait"))
            return
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            self._add_bubble(
                _("Voice input unavailable \u2014 pyaudio is not installed."), False
            )
            return

        self._recording = True
        self._mic_chunks = []
        try:
            self._spectro.start()
        except Exception:
            pass
        self._mic_btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: #ef4444; color: white; border: 0px;"
            f"  border-radius: 8px; padding: 0px 14px; font-size: 11px; font-weight: 700;"
            f"}}"
        )
        self._voice_status.setText(_("Listening\u2026"))
        self._mic_thread = threading.Thread(
            target=self._record_worker, daemon=True
        )
        self._mic_thread.start()

    def _record_worker(self):
        """Capture 16 kHz mono PCM16 chunks while recording is active."""
        stream = None
        pya = None
        try:
            import pyaudio
            pya = pyaudio.PyAudio()
            stream = pya.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=4096,
            )
            while self._recording:
                try:
                    data = stream.read(4096, exception_on_overflow=False)
                except Exception:
                    break
                if data:
                    self._mic_chunks.append(data)
        except Exception as e:
            logger.exception("mic recording failed")
            self._post_ui(
                lambda err=str(e): self._voice_status.setText(
                    _("Mic error: {}").format(err)
                )
            )
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pya is not None:
                try:
                    pya.terminate()
                except Exception:
                    pass

    def _stop_recording(self):
        """Stop capture and transcribe the collected audio."""
        if not self._recording:
            return
        self._recording = False
        try:
            self._spectro.stop()
        except Exception:
            pass
        if self._mic_thread:
            self._mic_thread.join(timeout=3)
            self._mic_thread = None

        chunks = self._mic_chunks
        self._mic_chunks = []
        self._mic_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {ACCENT_COLOR};"
            f"border: 1px solid {ACCENT_COLOR}; border-radius: 8px;"
            f"padding: 0px 14px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(34,211,238,0.12); }}"
        )

        # ~100 ms of audio = treat as no capture (accidental tap)
        if not chunks or sum(len(c) for c in chunks) < 3200:
            self._voice_status.setText(_("No audio captured"))
            return

        self._voice_status.setText(_("Transcribing\u2026"))
        pcm = b"".join(chunks)
        threading.Thread(
            target=self._transcribe_worker, args=(pcm,), daemon=True
        ).start()

    def _transcribe_worker(self, pcm: bytes):
        """Run Vosk transcription off the UI thread."""
        try:
            from player.stt_engine import get_stt_engine
            result = get_stt_engine().transcribe(pcm, 16000)
        except Exception as e:
            logger.exception("STT transcription failed")
            result = {"error": str(e)}
        self._post_ui(lambda r=result: self._on_transcription_done(r))

    def _on_transcription_done(self, result: dict):
        """Apply the transcription result (UI thread)."""
        self._voice_status.setText(_("Hold to talk"))
        if result.get("error"):
            self._add_bubble(
                _("Voice input error: {}").format(result["error"]), False
            )
            return
        text = (result.get("text") or "").strip()
        if not text:
            self._voice_status.setText(_("No speech detected \u2014 try again"))
            return
        self._submit_text(text)

    def _dispatch(self, text: str):
        if self._chat_controller is None:
            self._add_bubble(_("Chat controller not ready."), is_user=False)
            return

        my_seq, stop_flag = self._start_request()
        self._show_thinking()
        # Remember the request text so a low rating can quote it to the
        # description-repair prompt (player/agentic_ops/description_repair.py).
        self._last_user_request = str(text or "")

        # Pin the transcript to the chain this request actually runs on —
        # switching coworker mid-run must not re-attribute its messages.
        self._request_chain_id = (
            getattr(self._chat_controller, "_system_chain_id", "") or ""
        )

        # Notify main_window so dot turns red during execution
        self.execution_state_changed.emit(True)

        # Hide overlay so the floating window doesn't interfere with automation
        self._automation_hide()

        # Reset mid-execution tracking for this request
        self._last_callback_content = ""

        def worker():
            try:
                result = self._chat_controller.handle_request(
                    text,
                    stop_flag=lambda: stop_flag.is_set()
                    or self._stop_event.is_set(),
                )
                if result:
                    import re
                    # Extract goal_id BEFORE stripping the comment
                    goal_id = ""
                    _m = re.search(r'<!--\s*goal_id:([^\s>]+)\s*-->', result)
                    if _m:
                        goal_id = _m.group(1)
                        self._last_known_goal_id = goal_id
                    # Strip hidden goal_id comment from display
                    display = re.sub(
                        r"<!--\s*goal_id:[^\s>]+\s*-->", "", result
                    ).strip()

                    # Skip duplicate if this exact content was already shown via output callback
                    if display == self._last_callback_content:
                        logger.debug(
                            "Skipping duplicate bubble — already shown via output callback"
                        )
                    else:
                        self._post_ui(lambda r=display: self._add_bubble(r, False))
                        # Mirror the reply to mobile clients: a run started from
                        # the desktop must reach the phone too.
                        self._web_send_text(display)
                        # Overall rating row: a penalty here feeds the
                        # description-repair pass anchored at the last tool.
                        if goal_id:
                            self._post_ui(
                                lambda gid=goal_id: self._add_final_rating_widget(gid)
                            )

                    self._post_ui(self._scroll_to_bottom)

            except Exception as e:
                logger.exception("agent_overlay: dispatch error")
                self._post_ui(
                    lambda err=str(e): self._add_bubble(
                        _("Error: {}").format(err), False
                    )
                )
            finally:
                if self._finish_request(my_seq):
                    self._post_ui(self._hide_thinking)
                    self._post_ui(lambda: self.execution_state_changed.emit(False))
                    self._post_ui(self._automation_restore)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    # ── Request slot: stop flag, generation, hard stop ───────────────

    def _start_request(self):
        """Claim the request slot; returns (generation, stop_flag).

        Every request gets its OWN stop Event: the shared ``_stop_event`` is
        cleared on dispatch, so a stop latched for a still-unwinding worker
        must not depend on it.
        """
        self._stop_event.clear()
        self._stop_requested = False
        self._executing = True
        self._request_seq += 1
        flag = threading.Event()
        self._request_stop = flag
        self._post_ui(self._update_stop_btn)  # may be called off the UI thread
        # Tell every connected client a run is live (shows their Stop button);
        # runs started from the desktop used to stay invisible on the phone.
        self._web_send_json({"type": "agent_busy"})
        return self._request_seq, flag

    def _finish_request(self, seq) -> bool:
        """Bookkeeping for a finished worker — muted when it is stale.

        A stopped worker keeps unwinding after the user already started the
        next request; clearing the live request's state from here would free
        the UI of a running task or re-attribute its bubbles.  Returns True
        when this worker was still the current one.
        """
        if seq != self._request_seq:
            return False
        self._request_stop = None
        self._request_chain_id = ""
        self._executing = False
        self._post_ui(self._update_stop_btn)
        self._web_send_json({"type": "agent_done"})
        return True

    def _release_pending_asks(self):
        """Unblock Input nodes parked on an ask so the chain can abort.

        Without this a "stopped" chain sitting at "Can I do something else
        for you?" waits forever for a reply — and the user's next message
        would be consumed as that reply instead of a new query.
        """
        if self._input_ask_active:
            self._input_ask_response = ""
            self._input_ask_active = False
            self._input_ask_event.set()
        if self._web_ask_active:
            self._web_ask_response = ""
            self._web_ask_active = False
            self._web_ask_event.set()

    def _stop_execution(self):
        """Stop the running task now without waiting for a graceful end.

        Latches the request's stop flag (the executor polls it between
        actions, nodes and LLM iterations), releases a parked ask, and hands
        the UI back immediately — the user can pick another coworker or ask
        something else right away.  The old worker finishes in the
        background; its generation is invalidated so it stays silent.
        """
        self._stop_requested = True
        self._stop_event.set()
        flag = self._request_stop
        if flag is not None:
            flag.set()
        self._release_pending_asks()
        self._request_seq += 1
        self._request_stop = None
        self._request_chain_id = ""
        self._executing = False
        self._post_ui(self._hide_thinking)
        self._post_ui(self._update_stop_btn)
        self._post_ui(lambda: self.execution_state_changed.emit(False))
        self._post_ui(self._automation_restore)
        # Web clients: this turn is over even if the worker is still unwinding.
        self._web_send_json({"type": "agent_done"})
        logger.info("Agent overlay: execution stop requested")

    def _update_stop_btn(self):
        """Show the Stop button only while a task is running."""
        try:
            self._stop_btn.setVisible(bool(self._executing))
        except Exception:
            pass

    def _ask_completed_hide(self):
        """Re-hide the overlay after an ask — unless the run was stopped.

        After a stop the user is typing again, so the overlay must stay
        visible instead of being tucked away for the next tool execution.
        """
        if self._stop_requested:
            return
        self._automation_hide()

    # ── Chat bubbles ─────────────────────────────────────────────────

    def _add_bubble(self, text: str, is_user: bool, mode: str = 'text', asset=None):
        """Record the message in the running coworker's transcript, then show it."""
        chain_id = self._request_chain_id or self._active_chain_id
        self._conversations.setdefault(chain_id, []).append({
            "text": text, "is_user": bool(is_user), "mode": mode, "asset": asset,
        })
        # Live-render only into the transcript the user is looking at; a message
        # from another coworker stays recorded and shows up when switched back.
        if chain_id == self._active_chain_id:
            self._render_bubble(text, is_user, mode, asset)

    def _render_active_conversation(self):
        """Rebuild the transcript shown for the active coworker.

        Dropping the old widgets is safe: every message was recorded by
        ``_add_bubble``, so switching coworkers never loses the user's text.
        """
        try:
            for i in reversed(range(self._chat_layout.count())):
                widget = self._chat_layout.itemAt(i).widget()
                if widget is not None and widget is not self._placeholder:
                    widget.setParent(None)
            messages = self._conversations.get(self._active_chain_id, [])
            if messages:
                self._placeholder.hide()
                for m in messages:
                    self._render_bubble(
                        m.get("text"), m.get("is_user"),
                        m.get("mode", "text"), m.get("asset"),
                    )
            else:
                self._placeholder.show()
            QTimer.singleShot(0, self._scroll_to_bottom)
        except Exception:
            logger.exception("_render_active_conversation: error")

    def _render_bubble(self, text: str, is_user: bool, mode: str = 'text', asset=None):
        if self._placeholder and self._placeholder.isVisible():
            self._placeholder.hide()

        # Auto-expand when agent responds (so user sees the answer)
        if not is_user and self._compact:
            self._set_compact_mode(False)

        bubble = QFrame(self._chat_container)
        if is_user:
            bubble_color, bubble_border = USER_BUBBLE_BG, USER_BUBBLE_BORDER
        else:
            bubble_color, bubble_border = AGENT_BUBBLE_BG, AGENT_BUBBLE_BORDER
        bubble.setStyleSheet(
            f"QFrame {{ background-color: {bubble_color};"
            f"border: 1px solid {bubble_border}; border-radius: 12px; }}"
        )
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(14, 10, 14, 10)
        bl.setSpacing(0)

        max_width = self._bubble_max_width()

        if mode == 'image':
            img_label = QLabel(bubble)
            img_label.setObjectName("chatBubbleLabel")
            img_label.setStyleSheet(f"color: {TEXT_COLOR};")
            pix = self._load_pixmap(asset) if asset else QPixmap()
            if pix and not pix.isNull():
                img_label.setPixmap(
                    pix.scaled(max_width, max_width * 2,
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                bl.addWidget(img_label)
            else:
                # No usable asset: fall back to a text bubble.
                label = self._make_bubble_label(str(text or "(image unavailable)"), max_width)
                bl.addWidget(label)
        elif mode in ('html', 'markdown'):
            from .rich_render import md_to_html, sanitize_html
            html_text = md_to_html(text) if mode == 'markdown' else sanitize_html(text)
            label = self._make_bubble_label(html_text, max_width, plain=text)
            label.setTextFormat(Qt.RichText)
            bl.addWidget(label)
        else:
            # text and audio: plain-text bubble (speech already played via tts_service)
            label = self._make_bubble_label(text, max_width)
            bl.addWidget(label)
            if mode == 'audio':
                self._pulse_output_spectro(text)

        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        align = Qt.AlignRight if is_user else Qt.AlignLeft
        self._chat_layout.addWidget(bubble, 0, align)

    def _pulse_output_spectro(self, text: str):
        """Animate the spectrogram while spoken output plays (estimated)."""
        try:
            est_ms = min(20000, max(1500, int(len(str(text or "")) * 65)))
            self._spectro.start()
            QTimer.singleShot(est_ms, self._spectro.stop)
        except Exception:
            pass

    def _make_bubble_label(self, text: str, max_width: int, plain: str = None):
        label = QLabel(text, self._chat_container)
        label.setObjectName("chatBubbleLabel")
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        # Set font programmatically so fontMetrics() is immediately correct
        font = label.font()
        font.setPixelSize(13)
        label.setFont(font)
        label.setStyleSheet(f"color: {TEXT_COLOR};")
        label.setMaximumWidth(max_width)
        if plain is not None:
            # Rich text: size the bubble from the plain source, not raw HTML.
            label.setProperty("plainText", plain)
        min_width = self._apply_label_width(label, max_width)
        label.setMinimumWidth(min_width)
        return label

    def _load_pixmap(self, asset):
        """Build a QPixmap from a data URI or image file path."""
        try:
            if isinstance(asset, str) and asset.startswith("data:image/"):
                _, _, b64 = asset.partition(",")
                pix = QPixmap()
                pix.loadFromData(base64.b64decode(b64))
                return pix
            if isinstance(asset, str) and os.path.isfile(asset):
                return QPixmap(asset)
        except Exception:
            pass
        return QPixmap()

    def _bubble_max_width(self):
        try:
            viewport = self._chat_scroll.viewport()
            if viewport:
                return max(180, int(viewport.width() * 0.82))
        except Exception:
            pass
        return OVERLAY_WIDTH - 60

    def _apply_label_width(self, label, max_width):
        try:
            fm = label.fontMetrics()
            plain = label.property("plainText")
            text = plain if plain is not None else (label.text() or "")
            text_width = fm.horizontalAdvance(text)
            target = min(max_width, text_width + 16)
            if target < 100:
                target = min(max_width, 100)
            return target
        except Exception:
            return 100

    def _apply_bubble_widths(self):
        try:
            max_width = self._bubble_max_width()
            for label in self._chat_container.findChildren(QLabel, "chatBubbleLabel"):
                label.setMaximumWidth(max_width)
                min_width = self._apply_label_width(label, max_width)
                label.setMinimumWidth(min_width)
        except Exception:
            pass

    def _scroll_to_bottom(self):
        try:
            bar = self._chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    # ── Ask-user callback (for Input nodes with user_prompt) ────────────────

    def _make_ask_user_v2_callback(self):
        """Rich ask (v2 protocol) on top of the chat ask channel.

        Renders the question with numbered options (yes/no, choices) and an
        attach hint into the same chat bubbles; a reply that names an existing
        file becomes an attachment, everything else is the value.  Returns a
        v2 response dict.
        """
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
            if value and os.path.isfile(value):
                try:
                    from player.qt_input import _attachment_kind as _kind
                    attachments = [{
                        'kind': _kind(value),
                        'path': value,
                        'name': os.path.basename(value),
                    }]
                    value = ''
                except Exception:
                    attachments = []
            return {'value': value or None, 'kind': kind, 'attachments': attachments}

        return ask_user_v2

    def _make_ask_user_callback(self):
        """Create a blocking callback that asks the user in chat for input.

        The question goes to BOTH surfaces — the desktop overlay and every
        connected web client (mobile) — and either side can answer: the run
        resumes with whichever reply arrives first.  A single dual-surface
        callback (instead of a per-request swap) is what keeps a run started
        from the desktop answerable from the phone.
        """
        def ask_user(question):
            self._input_ask_event.clear()
            self._input_ask_response = None
            self._input_ask_active = True
            self._web_ask_event.clear()
            self._web_ask_response = None
            self._web_ask_active = True
            self._last_ask_question = question
            self._post_ui(lambda q=question: self._add_bubble(q, False))
            self._post_ui(self._scroll_to_bottom)
            self._post_ui(self._show_for_input)
            # Mobile clients: show the question and put the client in
            # answer mode (its next message is the reply, not a new query).
            self._web_send_json({"type": "ask", "question": question})
            # Voice-first: with voice mode on, start listening immediately so
            # the user can answer by speaking (tap the mic to stop).
            if self._voice_mode:
                self._post_ui(self._begin_voice_ask)
            # Block until either side answers (a stop releases both events).
            while not self._input_ask_event.is_set() \
                    and not self._web_ask_event.is_set():
                self._web_ask_event.wait(timeout=0.1)
            if self._web_ask_event.is_set() and not self._input_ask_event.is_set():
                resp = self._web_ask_response or ""
            else:
                resp = self._input_ask_response or ""
            self._input_ask_active = False
            self._web_ask_active = False
            if self._voice_mode:
                self._post_ui(self._end_voice_ask)
            # Post-stop the UI belongs to the user: keep the overlay visible.
            self._post_ui(self._ask_completed_hide)
            return resp
        return ask_user

    # ── Output display callback (for mid-execution output nodes) ──────

    def _make_output_display_callback(self):
        """Create a callback that shows output + per-output rating widget.

        Mirrors every mid-execution output to connected web clients as a v2
        output envelope, so the phone shows the same bubbles as the desktop
        overlay regardless of which surface started the run.
        """
        def display_output(label, content, tool_chain_path="", tool_alias="",
                           show_rating=True, mode='text', asset=None):
            msg = str(content) if content else ""
            if not msg and mode != 'image':
                return
            # Store for duplicate check in _dispatch
            self._last_callback_content = msg
            self._post_ui(self._automation_restore)  # Ensure overlay visible for rating widget
            # Store tool info in a dict keyed by a simple counter
            self._output_seq += 1
            seq = self._output_seq
            self._pending_output_ratings[seq] = {
                "tool_chain_path": tool_chain_path,
                "tool_alias": tool_alias,
                "show_rating": show_rating,
                "content": msg[:400],
            }
            if tool_chain_path:
                self._last_output_chain = {"path": tool_chain_path, "alias": tool_alias}
            # Show bubble always
            self._post_ui(lambda m=msg, md=mode, a=asset: self._add_bubble(m, False, mode=md, asset=a))
            # Show rating widget only when enabled AND tool info is available
            if tool_chain_path and show_rating:
                self._post_ui(
                    lambda s=seq, tp=tool_chain_path, ta=tool_alias:
                    self._add_rating_widget(s, tp, ta)
                )
            self._post_ui(self._scroll_to_bottom)
            # Mirror to connected web clients (same envelope shape the client
            # already renders: text / markdown / code / html / image / audio).
            self._web_send_json({
                "v": 2,
                "kind": "output",
                "mode": mode or 'text',
                "label": label or "",
                "content": msg,
                "asset": asset or "",
            })
        return display_output

    def _add_rating_widget(self, seq: int, tool_chain_path: str, tool_alias: str):
        """Add a compact 10-star rating row for a single output.

        Ratings below 9 are a penalty for that chain: the feedback box asks
        what should have happened and feeds the description-repair pass
        (player/agentic_ops/description_repair.py).  9-10 = no penalty.
        """
        row = QFrame(self._chat_container)
        row.setStyleSheet("QFrame { background-color: transparent; }")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(4)
    
        stars_container = QWidget(row)
        stars_layout = QHBoxLayout(stars_container)
        stars_layout.setContentsMargins(0, 0, 0, 0)
        stars_layout.setSpacing(2)
    
        star_labels = []
        for i in range(1, 11):
            star = QLabel("\u2606", stars_container)
            star.setCursor(Qt.PointingHandCursor)
            star.setStyleSheet("QLabel { color: #f59e0b; font-size: 16px; padding: 0 1px; }")
            star_labels.append(star)
            stars_layout.addWidget(star)
    
        row_layout.addWidget(stars_container, 0, Qt.AlignLeft)
        row_layout.addStretch(1)
    
        def _on_star_click(clicked_idx, _seq=seq, _tp=tool_chain_path, _ta=tool_alias,
                            _labels=star_labels, _row=row):
            stars_given = clicked_idx + 1
            for idx, lbl in enumerate(_labels):
                lbl.setText("\u2605" if idx <= clicked_idx else "\u2606")
            logger.info("[OVERLAY] Rating: %d stars for chain %s", stars_given, _ta)
            # Penalty zone (<= 8, i.e. below description_repair.PENALTY_MAX):
            # ask what should have happened and repair the chain description.
            if stars_given <= 8 and _tp:
                self._show_feedback_input(_seq, _tp, _ta, stars_given, _row)
    
        for idx, star in enumerate(star_labels):
            star.mousePressEvent = lambda evt, i=idx: _on_star_click(i)
    
        self._chat_layout.addWidget(row, 0, Qt.AlignLeft)
        QTimer.singleShot(0, self._scroll_to_bottom)
    
    def _add_final_rating_widget(self, goal_id: str):
        """Add a 10-star rating row below the final agent response.

        A penalty here targets the most recent tool output's chain (the
        closest available anchor for "this brain/tool should not have run").
        """
        row = QFrame(self._chat_container)
        row.setStyleSheet("QFrame { background-color: transparent; }")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(4, 2, 4, 2)
        row_layout.setSpacing(4)
    
        stars_container = QWidget(row)
        stars_layout = QHBoxLayout(stars_container)
        stars_layout.setContentsMargins(0, 0, 0, 0)
        stars_layout.setSpacing(2)
    
        star_labels = []
        for i in range(1, 11):
            star = QLabel("\u2606", stars_container)
            star.setCursor(Qt.PointingHandCursor)
            star.setStyleSheet("QLabel { color: #f59e0b; font-size: 16px; padding: 0 1px; }")
            star_labels.append(star)
            stars_layout.addWidget(star)
    
        row_layout.addWidget(stars_container, 0, Qt.AlignLeft)
        row_layout.addStretch(1)
    
        def _on_star_click(clicked_idx, _gid=goal_id, _labels=star_labels, _row=row):
            stars_given = clicked_idx + 1
            for idx, lbl in enumerate(_labels):
                lbl.setText("\u2605" if idx <= clicked_idx else "\u2606")
            logger.info("[OVERLAY] Final rating: %d stars for goal %s", stars_given, _gid)
            if stars_given <= 8:
                target = dict(self._last_output_chain or {})
                if target.get("path"):
                    self._show_feedback_input(-1, target["path"],
                                              target.get("alias", ""), stars_given, _row)
                else:
                    logger.info(
                        "[OVERLAY] Penalty on goal %s recorded \u2014 no tool output "
                        "to anchor a description repair", _gid,
                    )
    
        for idx, star in enumerate(star_labels):
            star.mousePressEvent = lambda evt, i=idx: _on_star_click(i)
    
        self._chat_layout.addWidget(row, 0, Qt.AlignLeft)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _show_feedback_input(self, seq: int, tool_chain_path: str, tool_alias: str,
                              stars: int, parent_row: QFrame):
        """Show a text input below stars for correction feedback."""
        feedback_frame = QFrame(self._chat_container)
        feedback_frame.setStyleSheet(
            f"QFrame {{ background-color: {SIDE_PANEL_BG}; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; }}"
        )
        fb_layout = QVBoxLayout(feedback_frame)
        fb_layout.setContentsMargins(10, 8, 10, 8)
        fb_layout.setSpacing(6)

        fb_label = QLabel(
            _("What did you expect the system to do instead?"), feedback_frame)
        fb_label.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 11px;")
        fb_layout.addWidget(fb_label)

        fb_input = QTextEdit(feedback_frame)
        fb_input.setPlaceholderText(_("Your feedback..."))
        fb_input.setFixedHeight(60)
        fb_input.setStyleSheet(
            f"QTextEdit {{ background-color: {BLOCK_COLOR}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 6px; padding: 6px; font-size: 12px; }}"
        )
        fb_layout.addWidget(fb_input)

        fb_submit = QPushButton(_("Submit"), feedback_frame)
        fb_submit.setCursor(Qt.PointingHandCursor)
        fb_submit.setFixedHeight(26)
        fb_submit.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: #0a0a0a; "
            f"border: 0px; border-radius: 6px; padding: 0 14px; font-size: 11px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: #4fdcf0; }}"
        )

        def _submit():
            text = fb_input.toPlainText().strip()
            feedback_frame.setParent(None)
            if not tool_chain_path:
                return
            logger.info(
                "[OVERLAY] Feedback received for chain '%s' (%d stars): %s",
                tool_alias, stars, text[:80] or "(none)",
            )
            self._start_description_repair(tool_chain_path, tool_alias, stars,
                                           text, seq)

        fb_submit.clicked.connect(_submit)
        fb_layout.addWidget(fb_submit, 0, Qt.AlignRight)

        self._chat_layout.addWidget(feedback_frame, 0, Qt.AlignLeft)
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _start_description_repair(self, chain_path: str, alias: str, stars: int,
                                  feedback: str, seq: int = -1):
        """Rewrite the chain's description from this rating (worker thread).

        Runs player/agentic_ops/description_repair.py off the UI thread and
        reports the outcome as a chat bubble.  The repaired description is
        what both routing stages read on the NEXT run (embedding ranker +
        Router LLM prompt; brains feed the Orchestrator's Laya picker) —
        recursive reinforcement through data, no training involved.
        """
        ctx = self._pending_output_ratings.get(seq, {}) if seq >= 0 else {}
        content = str(ctx.get("content") or "")
        goal = self._last_user_request

        def work():
            event = None
            try:
                from player.agentic_ops import description_repair as dr
                event = dr.repair_description(
                    chain_path, alias, stars,
                    feedback_text=feedback, goal_text=goal,
                    output_excerpt=content,
                )
            except Exception as e:  # noqa: BLE001 — feedback must never crash the UI
                logger.warning("[OVERLAY] description repair failed: %s", e)
            if event and event.get("updated"):
                msg = _("Description updated for '{}' — routing reads it on the "
                        "next run:\n{}").format(
                    alias or os.path.basename(str(event.get("target") or "")),
                    str(event.get("new") or "")[:300],
                )
            elif event:
                msg = _("Feedback recorded for '{}' ({}).").format(
                    alias, event.get("reason") or "recorded")
            else:
                msg = _("Feedback recorded for '{}'.").format(alias)
            self._post_ui(lambda m=msg: self._add_bubble(m, False))

        threading.Thread(target=work, daemon=True,
                         name="desc-repair").start()

    def _show_for_input(self):
        """Show and expand overlay when chain asks for user input.

        Called via _post_ui from the background worker thread so it
        runs on the main (UI) thread.
        """
        self._set_compact_mode(False)
        self.show()
        self.raise_()
        self.activateWindow()
        self._position_top_right()
        self._input_field.setFocus()
        QTimer.singleShot(
            80, lambda: (self.activateWindow(), self._input_field.setFocus())
        )

    def _force_ui_flush(self):
        """No-op retained for API compatibility.

        Previously called ``QApplication.processEvents()`` from the UI thread.
        That nested event processing is REMOVED on purpose: it ran inside
        ``_drain_ui_queue`` and re-entered the event loop, which let functions
        posted LATER (e.g. the output node's ``_automation_restore`` and the
        ask-user tail ``_automation_hide``) execute out of order.  The result
        was a subchain output bubble being added while a stale hide left the
        overlay invisible.  The UI thread is never blocked while ask_user
        waits (only the chain worker thread blocks), so the queued question
        and ``_show_for_input`` are rendered promptly by the normal event loop.
        """

    # ── Thinking indicator ───────────────────────────────────────────

    def _show_thinking(self):
        self._thinking_label = QLabel(_("Thinking"), self._chat_container)
        self._thinking_label.setStyleSheet(
            f"color: rgba(255,255,255,0.4); font-size: 11px; padding: 4px 10px;"
        )
        self._chat_layout.addWidget(self._thinking_label, 0, Qt.AlignLeft)
        self._scroll_to_bottom()

    def _hide_thinking(self):
        try:
            if self._thinking_label:
                self._thinking_label.setParent(None)
                self._thinking_label.deleteLater()
                self._thinking_label = None
        except Exception:
            pass

    # ── Key event at widget level (Ctrl+A from anywhere in overlay) ──

    def keyPressEvent(self, event):
        # Ctrl+Shift+A is now handled at application level (main_window QAction).
        if (
            event.key() == Qt.Key_A
            and event.modifiers() & Qt.ControlModifier
        ):
            self.hide_overlay()
            return
        if event.key() == Qt.Key_Escape:
            self._collapse_to_dot()
            return
        super().keyPressEvent(event)

    # ── Export ────────────────────────────────────────────────────

    def _open_export_dialog(self):
        """Open the agent export/build dialog."""
        try:
            from ..dialogs.agent_export_dialog import AgentExportDialog
            dlg = AgentExportDialog(self)
            dlg.exec_()
        except Exception:
            logger.exception("_open_export_dialog: error")

    # ── Icon drawing (Arrow GUI style) ─────────────────────────────

    @staticmethod
    def _make_icon_arrow_back(size=20):
        """Chevron-left matching the main GUI style (open polyline)."""
        dpr = 1.0
        try:
            scr = QApplication.primaryScreen()
            dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
        except Exception:
            pass
        w = int(size * dpr)
        h = int(size * dpr)
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.HighQualityAntialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        try:
            pen.setWidthF(2.0 * dpr)
        except Exception:
            pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        s = float(size) * dpr
        # Chevron pointing left: three-point polyline
        p.drawPolyline(
            QPoint(int(0.65 * s), int(0.25 * s)),
            QPoint(int(0.35 * s), int(0.50 * s)),
            QPoint(int(0.65 * s), int(0.75 * s)),
        )
        p.end()
        try:
            pix.setDevicePixelRatio(dpr)
        except Exception:
            pass
        return QIcon(pix)

    @staticmethod
    def _make_icon_wifi(size=20):
        """Exact TablerIcons wifi: dot at bottom, 3 arcs centered at dot, 225°→315°."""
        dpr = 1.0
        try:
            scr = QApplication.primaryScreen()
            dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
        except Exception:
            pass
        w = int(size * dpr)
        h = int(size * dpr)
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.HighQualityAntialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        try:
            pen.setWidthF(2.0 * dpr)
        except Exception:
            pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        s = float(size) * dpr
        cx, cy = s * 0.5, s * 0.5
        dot_cy = cy + s * 0.25
        dot_r = s * 0.08
        p.setBrush(QColor(TEXT_COLOR))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, dot_cy), dot_r, dot_r)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
        for r_svg in (12, 8, 4):
            r = r_svg * s / 24.0
            p.drawArc(QRectF(cx - r, dot_cy - r, 2 * r, 2 * r), 45 * 16, 90 * 16)
        p.end()
        try:
            pix.setDevicePixelRatio(dpr)
        except Exception:
            pass
        return QIcon(pix)

    @staticmethod
    def _make_icon_calendar(size=20):
        """Draw a calendar icon: rounded frame, two top ticks, header rule."""
        dpr = 1.0
        try:
            scr = QApplication.primaryScreen()
            dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
        except Exception:
            pass
        w = int(size * dpr)
        h = int(size * dpr)
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.HighQualityAntialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        try:
            pen.setWidthF(2.0 * dpr)
        except Exception:
            pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        s = float(size) * dpr
        left, top = 0.20 * s, 0.28 * s
        right, bottom = 0.80 * s, 0.82 * s
        p.drawRoundedRect(
            QRectF(left, top, right - left, bottom - top), 2.0 * dpr, 2.0 * dpr
        )
        p.drawLine(QPointF(0.34 * s, 0.16 * s), QPointF(0.34 * s, 0.34 * s))
        p.drawLine(QPointF(0.66 * s, 0.16 * s), QPointF(0.66 * s, 0.34 * s))
        p.drawLine(QPointF(left, 0.46 * s), QPointF(right, 0.46 * s))
        p.end()
        try:
            pix.setDevicePixelRatio(dpr)
        except Exception:
            pass
        return QIcon(pix)

    @staticmethod
    def _make_icon_gear(size=20):
        """Draw a gear icon: pie-slice teeth cut by centered circle removal."""
        dpr = 1.0
        try:
            scr = QApplication.primaryScreen()
            dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
        except Exception:
            pass
        w = int(size * dpr)
        h = int(size * dpr)
        pix = QPixmap(w, h)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.HighQualityAntialiasing)
        s = float(size) * dpr
        cx, cy = s * 0.5, s * 0.5
        outer_r = s * 0.44
        body_r = s * 0.32
        inner_r = s * 0.18
        p.setBrush(QColor(TEXT_COLOR))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), body_r, body_r)
        num_teeth = 8
        for i in range(num_teeth):
            ang = 360.0 / num_teeth * i
            p.drawPie(QRectF(cx - outer_r, cy - outer_r, 2 * outer_r, 2 * outer_r),
                      int((ang - 15) * 16), int(30 * 16))
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.setBrush(Qt.transparent)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), inner_r, inner_r)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        try:
            pen.setWidthF(2.0 * dpr)
        except Exception:
            pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(cx - inner_r, cy - inner_r, 2 * inner_r, 2 * inner_r))
        hole_r = s * 0.06
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(TEXT_COLOR))
        p.drawEllipse(QPointF(cx, cy), hole_r, hole_r)
        p.end()
        try:
            pix.setDevicePixelRatio(dpr)
        except Exception:
            pass
        return QIcon(pix)

    # ── Vector icon helpers (shared canvas/pen, DPR-aware) ──────────

    @staticmethod
    def _icon_canvas(size):
        """Shared canvas for the vector icons: (pixmap, painter, dpr, scale)."""
        dpr = 1.0
        try:
            scr = QApplication.primaryScreen()
            dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
        except Exception:
            pass
        pix = QPixmap(int(size * dpr), int(size * dpr))
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.HighQualityAntialiasing)
        return pix, p, dpr, float(size) * dpr

    @staticmethod
    def _stroke_pen(dpr, width=2.0):
        """Round-cap/join stroke pen in the standard icon colour."""
        pen = QPen(QColor(TEXT_COLOR))
        try:
            pen.setWidthF(width * dpr)
        except Exception:
            pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    @staticmethod
    def _finish_icon(pix, painter, dpr):
        """End painting and return a DPR-aware QIcon."""
        painter.end()
        try:
            pix.setDevicePixelRatio(dpr)
        except Exception:
            pass
        return QIcon(pix)

    @classmethod
    def _make_icon_chevron(cls, size=20, up=True):
        """Chevron up (expand hint) or down (collapse hint)."""
        pix, p, dpr, s = cls._icon_canvas(size)
        p.setPen(cls._stroke_pen(dpr))
        y_tip, y_end = (0.38, 0.62) if up else (0.62, 0.38)
        p.drawPolyline(
            QPoint(int(0.28 * s), int(y_end * s)),
            QPoint(int(0.50 * s), int(y_tip * s)),
            QPoint(int(0.72 * s), int(y_end * s)),
        )
        return cls._finish_icon(pix, p, dpr)

    @classmethod
    def _make_icon_reload(cls, size=20):
        """Circular refresh arrow with a gap + arrow head at the top-right."""
        import math
        pix, p, dpr, s = cls._icon_canvas(size)
        p.setPen(cls._stroke_pen(dpr))
        p.setBrush(Qt.NoBrush)
        c, r = s * 0.5, s * 0.33
        p.drawArc(QRectF(c - r, c - r, 2 * r, 2 * r), int(60 * 16), int(300 * 16))
        ang = math.radians(60)
        ex, ey = c + r * math.cos(ang), c - r * math.sin(ang)
        arm = s * 0.16
        p.drawPolyline(
            QPointF(ex - arm * 0.25, ey - arm),
            QPointF(ex, ey),
            QPointF(ex + arm, ey + arm * 0.25),
        )
        return cls._finish_icon(pix, p, dpr)

    @classmethod
    def _make_icon_close(cls, size=20):
        """X (close / collapse) icon."""
        pix, p, dpr, s = cls._icon_canvas(size)
        p.setPen(cls._stroke_pen(dpr))
        p.drawLine(QPointF(0.30 * s, 0.30 * s), QPointF(0.70 * s, 0.70 * s))
        p.drawLine(QPointF(0.70 * s, 0.30 * s), QPointF(0.30 * s, 0.70 * s))
        return cls._finish_icon(pix, p, dpr)

    @classmethod
    def _make_icon_mic(cls, size=20):
        """Microphone (voice input) icon."""
        pix, p, dpr, s = cls._icon_canvas(size)
        p.setPen(cls._stroke_pen(dpr))
        p.setBrush(Qt.NoBrush)
        rx = 0.15 * s
        p.drawRoundedRect(QRectF(0.5 * s - rx, 0.16 * s, 2 * rx, 0.42 * s), rx, rx)
        p.drawArc(QRectF(0.26 * s, 0.30 * s, 0.48 * s, 0.46 * s), 180 * 16, 180 * 16)
        p.drawLine(QPointF(0.5 * s, 0.76 * s), QPointF(0.5 * s, 0.88 * s))
        return cls._finish_icon(pix, p, dpr)

    @classmethod
    def _make_icon_fullscreen(cls, size=20):
        """Four corner brackets (maximize) — fill the screen with the overlay."""
        pix, p, dpr, s = cls._icon_canvas(size)
        p.setPen(cls._stroke_pen(dpr))
        lo, hi, arm = 0.24, 0.76, 0.18
        for cx, cy, dx, dy in ((lo, lo, 1, 1), (hi, lo, -1, 1),
                               (lo, hi, 1, -1), (hi, hi, -1, -1)):
            p.drawLine(QPointF(cx * s, cy * s),
                       QPointF((cx + dx * arm) * s, cy * s))
            p.drawLine(QPointF(cx * s, cy * s),
                       QPointF(cx * s, (cy + dy * arm) * s))
        return cls._finish_icon(pix, p, dpr)

    # ── Web server panel + lifecycle ─────────────────────────────────

    def _build_server_panel(self) -> QFrame:
        """Build the collapsible server-control panel."""
        panel = QFrame(self)
        panel.setObjectName("overlayServerPanel")
        panel.setStyleSheet(
            f"QFrame#overlayServerPanel {{"
            f"  background-color: {HEADER_RGBA};"
            f"  border-bottom: 1px solid rgba(255,255,255,0.06);"
            f"}}"
        )
        panel.setFixedHeight(SERVER_PANEL_HEIGHT)

        pl = QVBoxLayout(panel)
        pl.setContentsMargins(14, 10, 14, 10)
        pl.setSpacing(8)

        # ── Status row: dot + text + clients ──
        status_h = QHBoxLayout()
        status_h.setContentsMargins(0, 0, 0, 0)
        status_h.setSpacing(6)

        self._status_dot = QLabel(panel)
        self._status_dot.setFixedSize(8, 8)
        self._status_dot.setStyleSheet(
            "background-color: #ef4444; border-radius: 4px;"
        )
        status_h.addWidget(self._status_dot)

        self._status_text = QLabel(_("Server not started"), panel)
        self._status_text.setStyleSheet(
            f"color: rgba(255,255,255,0.5); font-size: 11px;"
        )
        status_h.addWidget(self._status_text)

        self._clients_label = QLabel(panel)
        self._clients_label.setStyleSheet(
            f"color: rgba(255,255,255,0.35); font-size: 10px;"
        )
        self._clients_label.hide()
        status_h.addWidget(self._clients_label)
        status_h.addStretch(1)
        pl.addLayout(status_h)

        # ── Remote-access IP (Tailscale/LAN), persisted ──
        self._ts_ip_edit = QLineEdit(panel)
        self._ts_ip_edit.setPlaceholderText(
            _("Remote IP (Tailscale/LAN) \u2014 auto-detect if empty")
        )
        self._ts_ip_edit.setText(_load_agent_ip())
        self._ts_ip_edit.setFixedHeight(26)
        self._ts_ip_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {BG_RGBA}; color: {TEXT_COLOR};"
            f"border: 1px solid rgba(255,255,255,0.12); border-radius: 6px;"
            f"padding: 2px 8px; font-size: 11px; }}"
            f"QLineEdit:focus {{ border: 1px solid {ACCENT_COLOR}; }}"
        )
        self._ts_ip_edit.editingFinished.connect(self._on_ts_ip_edited)
        pl.addWidget(self._ts_ip_edit)

        # ── Server toggle button ──
        self._panel_server_btn = QPushButton(_("Start Server"), panel)
        self._panel_server_btn.setCursor(Qt.PointingHandCursor)
        self._panel_server_btn.setFixedHeight(28)
        self._panel_server_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #22c55e;"
            f"border: 1px solid #22c55e; border-radius: 6px; padding: 0px 14px;"
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(34,197,94,0.12); }}"
        )
        self._panel_server_btn.clicked.connect(self._toggle_web_server)
        pl.addWidget(self._panel_server_btn, 0, Qt.AlignLeft)

        # ── QR code image ──
        self._qr_label = QLabel(panel)
        self._qr_label.setAlignment(Qt.AlignCenter)
        self._qr_label.setFixedSize(120, 120)
        self._qr_label.hide()
        pl.addWidget(self._qr_label, 0, Qt.AlignCenter)

        # ── Connect URL ──
        self._url_label = QLabel(panel)
        self._url_label.setAlignment(Qt.AlignCenter)
        self._url_label.setWordWrap(True)
        self._url_label.setMinimumHeight(30)
        self._url_label.setStyleSheet(
            f"color: #60a5fa; font-size: 10px; background-color: transparent; padding: 2px 4px;"
        )
        self._url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._url_label.hide()
        pl.addWidget(self._url_label, 0, Qt.AlignCenter)

        return panel

    def _on_ts_ip_edited(self):
        """Persist the typed remote IP and refresh the running server's URL."""
        ip = self._ts_ip_edit.text().strip()
        _save_agent_ip(ip)
        if not self._web_server_started:
            return
        self._web_server.set_remote_ip(ip)
        state = self._web_server.state
        if ip:
            state.remote_url = f"http://{ip}:{self._web_server.actual_port}"
            self._update_qr_display(state.remote_url)
        else:
            state.remote_url = None
            self._qr_label.clear()
            self._qr_label.hide()

    def _auto_detect_ip_and_refresh(self):
        """Background Tailscale-IP detection; refresh UI + server when found."""
        ip = detect_tailscale_ip() or ""
        if not ip:
            return
        self._post_ui(lambda: self._apply_remote_ip(ip))

    def _apply_remote_ip(self, ip: str):
        """Fill + persist an auto-detected IP and refresh a running server."""
        self._ts_ip_edit.setText(ip)
        _save_agent_ip(ip)
        if not self._web_server_started:
            return
        self._web_server.set_remote_ip(ip)
        state = self._web_server.state
        if state.remote_url is None and state.local_url:
            port = state.local_url.rsplit(":", 1)[-1]
            state.remote_url = f"http://{ip}:{port}"
            self._update_qr_display(state.remote_url)

    def _toggle_server_panel(self):
        """Show/hide the server control panel."""
        visible = self._server_panel.isVisible()
        # If in compact mode, expand first so the panel has room
        if self._compact:
            self._set_compact_mode(False)
            # Re-check visibility after expand (panel was hidden by compact mode)
            visible = False
        self._server_panel.setVisible(not visible)
        self._server_btn.setChecked(not visible)

        if self._embedded:
            # In-window the overlay FILLS its parent, so it must stay
            # layout-managed.  Sizing it here (setFixedSize to the floating
            # OVERLAY_WIDTH/HEIGHT) pins it to a fixed box: the 288px panel then
            # pushes the layout out of shape and the overlay stays glitched,
            # because a fixed size survives every later hide/show (so switching
            # to the notch or the graph and back did not clear it).  Drop any
            # stale constraint and let the parent's layout give it the space;
            # this also heals an overlay left pinned by an earlier version.
            try:
                self.setMinimumSize(0, 0)
                self.setMaximumSize(16777215, 16777215)
            except Exception:
                pass
            return

        if not visible:
            # Panel just became visible — bump overlay height to fit it,
            # adjusting Y so it stays within the screen bounds.
            new_h = OVERLAY_HEIGHT + SERVER_PANEL_HEIGHT
            screen = QApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                y = self.y()
                if y + new_h > geo.bottom():
                    y = max(geo.top(), geo.bottom() - new_h)
                self.setGeometry(self.x(), y, OVERLAY_WIDTH, new_h)
            else:
                self.setFixedSize(OVERLAY_WIDTH, new_h)
        else:
            self.setFixedSize(OVERLAY_WIDTH, OVERLAY_HEIGHT)

    def _toggle_web_server(self):
        if self._web_server_started:
            self._stop_web_server()
        else:
            self._start_web_server()

    def _start_web_server(self):
        try:
            self._web_server.set_remote_ip(self._ts_ip_edit.text().strip())
            ok = self._web_server.start()
            if not ok:
                logger.error("Agent web server failed to start")
                self._status_text.setText(_("Port conflict — try again"))
                return
            self._web_server_started = True
            self._server_btn.setChecked(True)
            self._panel_server_btn.setText(_("Stop Server"))
            self._panel_server_btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: #ef4444;"
                f"border: 1px solid #ef4444; border-radius: 6px; padding: 0px 14px;"
                f"font-size: 11px; font-weight: 600; }}"
                f"QPushButton:hover {{ background-color: rgba(239,68,68,0.12); }}"
            )
            self._status_dot.setStyleSheet(
                "background-color: #f59e0b; border-radius: 4px;"
            )
            self._status_text.setText(_("Starting local server..."))
            self._server_poll_timer.start()
            # Auto-detect + persist the Tailscale IP when the field is empty,
            # so the QR works on the very first run without typing anything.
            if not self._ts_ip_edit.text().strip():
                threading.Thread(
                    target=self._auto_detect_ip_and_refresh, daemon=True
                ).start()
            logger.info("Agent web server started")
        except Exception as e:
            logger.exception("Failed to start web server")
            self._add_bubble(_("Web server error: {}").format(e), False)

    def _stop_web_server(self):
        try:
            self._server_poll_timer.stop()
            self._web_server.stop()
        except Exception:
            pass
        self._web_server_started = False
        self._server_btn.setChecked(False)
        self._panel_server_btn.setText(_("Start Server"))
        self._panel_server_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #22c55e;"
            f"border: 1px solid #22c55e; border-radius: 6px; padding: 0px 14px;"
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(34,197,94,0.12); }}"
        )
        self._status_dot.setStyleSheet(
            "background-color: #ef4444; border-radius: 4px;"
        )
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

            # URL + QR: prefer the remote (Tailnet/LAN) URL when an IP is set.
            url = state.remote_url or state.local_url
            if url:
                self._url_label.setText(url)
                self._url_label.show()
                if state.remote_url and (
                        not self._qr_label.pixmap() or self._qr_label.pixmap().isNull()):
                    self._update_qr_display(state.remote_url)

            if state.active:
                self._status_dot.setStyleSheet(
                    "background-color: #22c55e; border-radius: 4px;"
                )
                if state.remote_url:
                    self._status_text.setText(
                        _("Server running \u2014 scan QR code to connect")
                    )
                else:
                    self._status_text.setText(
                        _("Server running locally \u2014 set a remote IP above to share")
                    )
            else:
                self._status_dot.setStyleSheet(
                    "background-color: #f59e0b; border-radius: 4px;"
                )
                self._status_text.setText(_("Server not started"))

            if state.connected_clients > 0:
                self._clients_label.setText(
                    f"{state.connected_clients} client(s) connected"
                )
                self._clients_label.show()
            else:
                self._clients_label.hide()

        except Exception:
            pass

    def _update_qr_display(self, url: str):
        """Generate and display a QR code from the connect URL."""
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
                120, 120,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            self._qr_label.setPixmap(scaled)
            self._qr_label.show()
        except Exception:
            logger.exception("Failed to generate QR code display")

    # ── Web-aware callbacks (for tunnel/mobile clients) ──────────────

    # ── Web panel commands (chains / schedules / coworkers) ────────────

    def _web_send_json(self, payload) -> None:
        """Push a JSON payload to connected web clients (thread-safe)."""
        import asyncio as _asyncio
        import json as _json
        state = self._web_server.state
        if state.ws_send is None or state.ws_loop is None:
            return
        try:
            _asyncio.run_coroutine_threadsafe(
                state.ws_send(_json.dumps(payload)), state.ws_loop
            )
        except Exception:
            pass

    def _web_send_text(self, text: str) -> None:
        """Push a plain agent reply to connected web clients (thread-safe).

        Plain text (not JSON) is the client's final-reply channel: it renders
        as a bubble and auto-plays TTS.
        """
        import asyncio as _asyncio
        state = self._web_server.state
        if state.ws_send is None or state.ws_loop is None:
            return
        try:
            _asyncio.run_coroutine_threadsafe(
                state.ws_send(str(text)), state.ws_loop
            )
        except Exception:
            pass

    def _web_history(self, limit: int = 60):
        """Recent bubbles of the active coworker's transcript (oldest first)."""
        try:
            msgs = self._conversations.get(self._active_chain_id, [])[-limit:]
        except Exception:
            msgs = []
        return [
            {
                "text": m.get("text") or "",
                "is_user": bool(m.get("is_user")),
                "mode": m.get("mode") or "text",
                "asset": m.get("asset") or "",
            }
            for m in msgs
        ]

    def _web_sync_state(self):
        """Bring a (re)connecting web client up to date.

        Sends the active coworker's transcript plus the live run state.
        Without this a phone that reloaded (or connected mid-run) sees a blank
        page, never sees the pending question, and every message it sends looks
        like the start of a brand-new conversation.
        """
        self._web_send_json({
            "type": "history",
            "chain_id": self._active_chain_id or "",
            "messages": self._web_history(),
        })
        if self._executing:
            self._web_send_json({"type": "agent_busy"})
        if self._web_ask_active and self._last_ask_question:
            # Puts the client back in answer mode for the question it missed.
            self._web_send_json(
                {"type": "ask", "question": self._last_ask_question}
            )

    def _web_chains_dir(self) -> str:
        return str(getattr(self._chat_controller, "_chains_dir", "") or "")

    def _web_list_chains(self):
        chains_dir = self._web_chains_dir()
        items = []
        try:
            for fname in sorted(os.listdir(chains_dir)):
                if fname.lower().endswith(".json"):
                    items.append(
                        {"name": os.path.splitext(fname)[0], "rel": fname}
                    )
        except Exception:
            pass
        return items

    def _web_list_coworkers(self):
        ctrl = self._chat_controller
        items = []
        try:
            active = os.path.normpath(
                str(getattr(ctrl, "_system_chain_path", "") or "")
            )
            for chain in ctrl.list_system_chains():
                path = os.path.normpath(str(chain.get("path", "")))
                items.append({
                    "id": chain.get("id", ""),
                    "name": chain.get("name") or chain.get("id", ""),
                    "active": bool(active) and path == active,
                })
        except Exception:
            pass
        return items

    def _web_select_coworker(self, chain_id: str):
        ctrl = self._chat_controller
        try:
            for chain in ctrl.list_system_chains():
                if str(chain.get("id", "")) == chain_id:
                    ctrl.set_system_chain(chain["path"])
                    break
        except Exception:
            logger.exception("select_coworker failed")
        self._active_chain_id = chain_id or self._active_chain_id
        # Follow the selection on the desktop overlay too (title, sidebar,
        # transcript) so both surfaces show the same conversation.
        self._post_ui(self._refresh_coworkers)
        self._web_send_json(
            {"type": "coworkers", "coworkers": self._web_list_coworkers()}
        )
        # Show that coworker's transcript on the client, not a blank page.
        self._web_sync_state()

    def _web_run_chain(self, rel: str):
        if not rel:
            return
        chains_dir = self._web_chains_dir()
        path = rel if os.path.isabs(rel) else os.path.join(chains_dir, rel)
        svc = self._scheduler_service
        if svc is None:
            self._web_send_json({"type": "notice", "text": "Chain runner unavailable."})
            return

        def _finished(result=None):
            self._executing = False
            self._post_ui(self._update_stop_btn)
            self._post_ui(lambda: self.execution_state_changed.emit(False))
            # Mirror the chain's final result to the phone, so a run the web
            # client started shows its Output-node content too (fixed text).
            _text = str(result or "").strip()
            if _text:
                self._post_ui(lambda r=_text: self._add_bubble(r, False))
                self._web_send_text(_text)
            self._web_send_json({"type": "agent_done"})

        try:
            self._web_send_json(
                {"type": "notice", "text": f"Running {os.path.basename(path)} ..."}
            )
            # The phone owns this run, so it asks the phone: Input-node
            # questions go to the chat surfaces (overlay + every client)
            # instead of a desktop dialog nobody is looking at.  The Stop
            # button latches this overlay's stop flag, and the run is announced
            # as busy - without that the client never shows a Stop at all.
            self._stop_event.clear()
            self._stop_requested = False
            self._executing = True
            self._post_ui(self._update_stop_btn)
            self._post_ui(lambda: self.execution_state_changed.emit(True))
            self._web_send_json({"type": "agent_busy"})
            svc.run_chain_now(
                path,
                ask_user_callback=self._make_ask_user_callback(),
                ask_user_v2_callback=self._make_ask_user_v2_callback(),
                on_output_ready=self._make_output_display_callback(),
                stop_flag=lambda: self._stop_event.is_set(),
                on_complete=_finished,
            )
        except Exception as exc:
            _finished()
            self._web_send_json({"type": "notice", "text": f"Run failed: {exc}"})

    def _web_scheduler_command(self, mtype: str, msg: dict):
        svc = self._scheduler_service
        if svc is None:
            self._web_send_json(
                {"type": "schedules", "schedules": [], "error": "Scheduler unavailable"}
            )
            return
        try:
            if mtype == "save_schedule":
                data = dict(msg.get("data") or {})
                if data.get("id"):
                    svc.update_schedule(data)
                else:
                    svc.add_schedule(data)
            elif mtype == "delete_schedule":
                svc.remove_schedule(str(msg.get("id") or ""))
            elif mtype == "toggle_schedule":
                svc.set_enabled(str(msg.get("id") or ""), bool(msg.get("enabled", True)))
            elif mtype == "run_schedule":
                svc.run_now(str(msg.get("id") or ""))
        except Exception as exc:
            logger.exception("scheduler web command failed")
            self._web_send_json({"type": "notice", "text": f"Scheduler error: {exc}"})
        self._web_send_json({"type": "schedules", "schedules": svc.list_schedules()})

    def _handle_web_command(self, msg) -> bool:
        """Handle a JSON panel command from the web client. True when consumed."""
        mtype = str(msg.get("type") or "")
        if mtype == "list_chains":
            self._web_send_json({"type": "chains", "chains": self._web_list_chains()})
            return True
        if mtype == "run_chain":
            self._web_run_chain(str(msg.get("rel") or msg.get("path") or ""))
            return True
        if mtype == "list_coworkers":
            self._web_send_json(
                {"type": "coworkers", "coworkers": self._web_list_coworkers()}
            )
            # Client syncs on connect / Refresh: replay transcript + run state.
            self._web_sync_state()
            return True
        if mtype == "select_coworker":
            self._web_select_coworker(str(msg.get("id") or ""))
            return True
        if mtype in ("list_schedules", "save_schedule", "delete_schedule",
                     "toggle_schedule", "run_schedule"):
            self._web_scheduler_command(mtype, msg)
            return True
        return False

    def _handle_web_message(self, text: str) -> str:
        """Handle a message from the web chat client (delegates to overlay chat).

        Supports:
          - "__STOP__" — stop current execution
          - JSON {"type":"answer","text":"..."} — response to ask-user prompt
          - JSON {"type":"rating"|"feedback"} — rating/feedback actions
          - Plain text — dispatched as a new agent query
        """
        import asyncio as _asyncio
        import json as _json

        if text == "__STOP__":
            self._stop_execution()
            return "Stop signal sent."

        if text.startswith("{"):
            try:
                msg = _json.loads(text)
            except Exception:
                msg = None
            if isinstance(msg, dict):
                # Answer to pending ask-user prompt (web client response)
                if msg.get("type") == "answer":
                    if self._web_ask_active:
                        self._web_ask_response = msg.get("text", "")
                        self._web_ask_event.set()
                        return "Answer received."
                    # No ask pending (stopped, or already answered): the text
                    # belongs to a NEW query — never dispatch the raw JSON.
                    text = str(msg.get("text") or "").strip()
                    if not text:
                        return "Answer ignored."
                if msg.get("type") in ("rating", "feedback"):
                    return "Received."
                # Panel commands (chains / schedules / coworkers)
                if self._handle_web_command(msg):
                    return "Handled."

        # Show user message and dispatch to chat controller
        # While an Input node is waiting, plain text IS the answer: a client
        # that missed the 'ask' frame (or reconnected) must still be able to
        # reply instead of spawning a second concurrent run.
        if self._web_ask_active and text.strip():
            self._web_ask_response = text
            self._post_ui(lambda t=text: self._add_bubble(t, True))
            self._web_ask_event.set()
            return "Answer received."

        self._post_ui(lambda t=text: self._add_bubble(t, True))
        my_seq, stop_flag = self._start_request()

        # Pin the transcript to the chain this request actually runs on.
        if self._chat_controller:
            self._request_chain_id = (
                getattr(self._chat_controller, "_system_chain_id", "") or ""
            )

        # Notify main_window so dot reflects execution state
        self.execution_state_changed.emit(True)

        state = self._web_server.state

        # Ask / output callbacks are installed once by set_chat_controller and
        # already serve both surfaces (overlay + WebSocket) — no per-request
        # swap, so a desktop-started run is just as visible/answerable on the
        # phone as a web-started one.

        def worker():
            try:
                result = ""
                if self._chat_controller:
                    result = self._chat_controller.handle_request(
                        text,
                        stop_flag=lambda: stop_flag.is_set()
                        or self._stop_event.is_set(),
                    )
                display = result or _("(no response)")
                import re
                # Extract goal_id for final rating widget
                _m = re.search(r'<!--\s*goal_id:([^\s>]+)\s*-->', display)
                _goal_id = _m.group(1) if _m else ""
                display = re.sub(
                    r"<!--\s*goal_id:[^\s>]+\s*-->", "", display
                ).strip()
                self._post_ui(lambda r=display: self._add_bubble(r, False))
                self._post_ui(self._scroll_to_bottom)
                # Push response back through the WebSocket channel
                self._web_send_text(display)
                if state.ws_send is not None and state.ws_loop is not None:
                    # Send goal_complete for rating widget on web client
                    if _goal_id:
                        _json_msg = _json.dumps({
                            "type": "goal_complete",
                            "goal_id": _goal_id,
                        })
                        _asyncio.run_coroutine_threadsafe(
                            state.ws_send(_json_msg), state.ws_loop
                        )
            except Exception as e:
                logger.exception("agent_overlay: web dispatch error")
                if state.ws_send is not None and state.ws_loop is not None:
                    _asyncio.run_coroutine_threadsafe(
                        state.ws_send(f"Error: {e}"), state.ws_loop
                    )
            finally:
                if self._finish_request(my_seq):
                    self._post_ui(self._hide_thinking)
                    self._post_ui(lambda: self.execution_state_changed.emit(False))
                    self._post_ui(self._automation_restore)  # Restore overlay + main window
                    # (agent_done was broadcast by _finish_request)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        return _("(processing...)")

    # ── Thread-safe UI posting ───────────────────────────────────────

    def _post_ui(self, func):
        try:
            self._ui_queue.append(func)
            QMetaObject.invokeMethod(
                self, "_drain_ui_queue", Qt.QueuedConnection
            )
        except Exception:
            pass

    @pyqtSlot()
    def _drain_ui_queue(self):
        queue, self._ui_queue = self._ui_queue, []
        for fn in queue:
            try:
                fn()
            except Exception:
                logger.exception("_drain_ui_queue: callback error")
