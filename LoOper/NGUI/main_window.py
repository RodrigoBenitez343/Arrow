# graphui/main_window.py

import sys
import json
import os
import time
import threading
import logging
import traceback
import subprocess
import ctypes
from pynput import keyboard as pynput_keyboard

# Ensure torch DLLs are loadable on Windows
# Fix for Intel OpenMP runtime duplicate error
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Ensure DLL load path on Windows
if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
    try:
        import site
        packages = []
        try:
            packages.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            if hasattr(site, 'getusersitepackages'):
                packages.append(site.getusersitepackages())
        except Exception:
            pass
        # Also check current venv specifically
        try:
            venv_path = os.path.dirname(os.path.dirname(sys.executable)) 
            site_pkg = os.path.join(venv_path, 'Lib', 'site-packages')
            if os.path.isdir(site_pkg):
                packages.append(site_pkg)
        except Exception:
            pass
            
        for p in packages:
            torch_lib = os.path.join(p, 'torch', 'lib')
            if os.path.isdir(torch_lib):
                try:
                    os.add_dll_directory(torch_lib)
                    # Also prepend to PATH as a fallback for some DLL loaders
                    os.environ['PATH'] = torch_lib + os.pathsep + os.environ['PATH']
                except Exception:
                    pass
    except Exception:
        pass

from datetime import datetime
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QMenuBar, QAction, QFileDialog, QInputDialog, QMessageBox, QLineEdit
from PyQt5.QtCore import QObject, pyqtSignal, QTimer, Qt, QEvent
from PyQt5.QtGui import QIcon, QKeySequence, QCursor
from .constants import DARK_GREY, MEDIUM_GREY, TEXT_COLOR, GRAPH_PLANE
from .i18n import _
from pynput import mouse, keyboard
from .graph_view import GraphViewWidget
from recorder.element_recorder import ElementRecorder
from .scheduler_service import SchedulerService
from .dialogs.scheduler_dialogs import SchedulerManagerDialog
from .dialogs import AISettingsDialog
from .dialogs.base_dialog import UserGuideDialog
from .widgets.agent_overlay import AgentOverlay
from .widgets.agent_dot import AgentDot
from .widgets.agent_notch import AgentNotch
from .widgets.code_node_panel import CodeNodePanel

# Unified telemetry (see logging_setup.py): records propagate to the root
# dispatch — colorized console + logs/automation.log + per-session md log.
logger = logging.getLogger('LoOper.NGUI')
logger.setLevel(logging.DEBUG)


# Tabler outline icon -> QIcon (shared helper; PyQt5-safe conversion).
from .icons import tabler_qicon as _tabler_qicon


# ── Dialog helper: marshals QInputDialog to the main thread from a bg thread ──
# QTimer.singleShot(0, ...) from a background thread creates the timer in that
# thread's event loop (which doesn't run), so it never fires.  Instead we use a
# QObject signal connected with BlockingQueuedConnection, which guarantees the
# slot executes on the receiver's thread (the main Qt thread).
class _BgInputDialogHelper(QObject):
    _sig = pyqtSignal(str)
    _sig_rich = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._result_container = []
        self._sig.connect(self._show, Qt.BlockingQueuedConnection)
        self._sig_rich.connect(self._show_rich, Qt.BlockingQueuedConnection)
        self.moveToThread(QApplication.instance().thread())

    def ask(self, question):
        self._result_container.clear()
        self._sig.emit(question)
        if self._result_container:
            _r, _ok = self._result_container[0]
            return _r if _ok else ''
        return ''

    def ask_rich(self, request):
        """Rich ask (v2 protocol): JSON payload in, (value, attachments) out."""
        self._result_container.clear()
        self._sig_rich.emit(json.dumps(request, ensure_ascii=False))
        if self._result_container:
            return self._result_container[0]
        return (None, [])

    def _show(self, question):
        # Always-on-top: the main window is minimized during playback, so a
        # plain QInputDialog (no owner, no top-most flag) can open behind a
        # browser window opened by the chain and swallow the user's Enter/OK.
        from player.qt_input import ask_text
        self._result_container.append(ask_text(question))

    def _show_rich(self, payload):
        from player.qt_input import ask_question
        try:
            request = json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            request = {'question': str(payload)}
        self._result_container.append(ask_question(request))


class MainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        logger.info("Initializing MainWindow")
        try:
            super().__init__()
            self.setWindowTitle(_("Arrow"))
            self.setGeometry(100, 100, 1200, 800)
            # Frameless "standalone" window: no native title bar. The menu bar
            # doubles as the draggable top bar and hosts our own window controls.
            self.setWindowFlag(Qt.FramelessWindowHint, True)
            self.setMinimumSize(860, 560)
            self._drag_offset = None
            
            # Set window icon — search exe dir first in frozen builds
            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(sys.executable)
                icon_path = os.path.join(exe_dir, "LoOper.ico")
                if not os.path.exists(icon_path):
                    icon_path = os.path.join(exe_dir, "_internal", "LoOper.ico")
            else:
                icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "LoOper.ico")
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
                logger.debug(f"Window icon set from: {icon_path}")
            else:
                logger.warning(f"Icon file not found at: {icon_path}")
            
            logger.debug("MainWindow basic setup complete, calling setup_ui")
            self.setup_ui()
            
            # Initialize Scheduler Service
            try:
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
                self.scheduler_service = SchedulerService(project_root=project_root)
                self.scheduler_service.start()
                # Hand the live scheduler to the agent chat's Scheduler panel
                try:
                    self._agent_overlay.set_scheduler_service(self.scheduler_service)
                except Exception as _se:
                    logger.warning(f"Could not attach scheduler to agent overlay: {_se}")
                logger.info("SchedulerService started successfully")
            except Exception as se:
                logger.error(f"Failed to start SchedulerService: {se}")
            
            # Snapshot of the most recent chain run — Code Node Studio replays
            # a selected code node against its real runtime data.
            self._last_player = None
            logger.info("MainWindow initialization completed successfully")
        except Exception as e:
            logger.error(f"Error initializing MainWindow: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def setup_ui(self):
        """Setup the UI for the main window"""
        logger.debug("Setting up UI components")
        try:
            self.central_widget = QWidget()
            self.central_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {GRAPH_PLANE};
                    color: {TEXT_COLOR};
                    border: none;
                }}
            """)
            self.setCentralWidget(self.central_widget)
            
            self.layout = QVBoxLayout(self.central_widget)
            self.layout.setContentsMargins(0, 0, 0, 0)
            self.layout.setSpacing(0)
            
            logger.debug("Creating GraphViewWidget")
            self.graph_view = GraphViewWidget(self)
            self.layout.addWidget(self.graph_view, 1)

            # Corner grip so the frameless window can still be resized.
            try:
                from PyQt5.QtWidgets import QSizeGrip
                self._size_grip = QSizeGrip(self.central_widget)
                self._size_grip.setFixedSize(16, 16)
                self._size_grip.raise_()
            except Exception:
                self._size_grip = None

            # Agent overlay — the system-chain-driven agent UI. Embedded in the
            # window as a switch: it REPLACES the builder (the graph is hidden)
            # while agent mode is active; its GUI/back button returns.
            self._agent_overlay = AgentOverlay()
            self._agent_overlay.embed(self.central_widget)
            self._agent_overlay.closed.connect(self._on_overlay_closed)
            self._agent_overlay.collapsed.connect(self._on_overlay_collapsed)
            self._agent_overlay.set_window_manager(
                minimize_cb=self.showMinimized,
                restore_cb=self.showNormal,
            )
            self.layout.addWidget(self._agent_overlay, 1)
            self._agent_overlay.hide()
            self._agent_inwindow = True
            logger.debug("AgentOverlay embedded (in-window agent mode)")

            # Agent dot (minimal floating indicator, hidden by default)
            self._agent_dot = AgentDot()
            self._agent_dot.hide()
            self._agent_dot.clicked.connect(self._on_dot_clicked)
            self._agent_overlay.execution_state_changed.connect(self._on_execution_state)
            logger.debug("AgentDot created")

            # Agent notch — the summonable compact agent state: a small
            # semi-transparent tab at the top-center of the screen. The agent
            # hotkey raises it (instead of forcing the fullscreen panel);
            # clicking it opens the in-window agent panel.
            self._agent_notch = AgentNotch()
            self._agent_notch.hide()
            self._agent_notch.clicked.connect(self._on_notch_clicked)
            self._agent_notch.submitted.connect(self._on_notch_submitted)
            self._agent_notch.voice_submitted.connect(self._on_notch_submitted)
            # The notch stays on screen while a chain runs, so it must leave the
            # screen for EVERY screen read - the agent only ever sees captures.
            # Register it on the same hook the execution overlay uses.
            try:
                from player.execution_overlay_bus import bus as _exec_bus
                _exec_bus.extra_capture_hooks.append(
                    self._agent_notch.hide_for_capture)
            except Exception:
                logger.exception("Could not register the notch capture hook")
            logger.debug("AgentNotch created")

            self._agent_mode_active = False
            self._notch_active = False   # the compact notch is the active agent UI
            self._overlay_was_visible = False
            self._dot_was_visible = False
            self._notch_was_visible = False
            self._toggle_throttle = 0.0  # debounce toggle_agent_mode calls

            # Connect toolbar scheduler button to open manager
            try:
                toolbar = getattr(self.graph_view, '_toolbar_widget', None)
                if toolbar:
                    toolbar.scheduler_requested.connect(self.open_scheduler_manager)
                    toolbar.ai_settings_requested.connect(self.open_ai_settings)
                    toolbar.agent_mode_requested.connect(self.toggle_agent_mode)
            except Exception as e:
                logger.error(f"Failed to connect toolbar scheduler: {e}")
            
            logger.debug("Creating menu bar")
            self.create_menu_bar()

            # pynput global hotkey (works even when app is minimized/not focused)
            self._ensure_global_hotkey()

            # ── Code Node Studio (non-modal code editor + AI chat) ──
            # Hosted inside the graph's CENTER column (between the left/right bars)
            # so its width adapts to them and it rises into the center's free space
            # without pushing the left/right bars.
            try:
                self.code_panel = CodeNodePanel(self)
                self.code_panel.hide()
                cl = getattr(self.graph_view, '_center_layout', None)
                if cl is not None:
                    cl.addWidget(self.code_panel)
                    try:
                        cl.setStretch(cl.indexOf(self.graph_view.node_graph_widget), 1)
                    except Exception:
                        pass
                self.graph_view.code_node_selected.connect(self._on_code_node_selected)
            except Exception as e:
                logger.error(f"Failed to initialize Code Node Studio: {e}")

            logger.debug("UI setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up UI: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise
    
    def create_menu_bar(self):
        """Create the application menu bar"""
        menu_bar = self.menuBar()
        try:
            from .constants import (DARK_GREY, MEDIUM_GREY, TEXT_COLOR, BLOCK_HOVER,
                                    HAIRLINE, ACCENT_SOFT, RADIUS_SM, RADIUS_MD)
            menu_bar.setStyleSheet(f"""
                QMenuBar {{
                    background-color: {DARK_GREY};
                    color: {TEXT_COLOR};
                }}
                QMenuBar::item {{
                    background: transparent;
                    padding: 5px 12px;
                    border-radius: {RADIUS_SM}px;
                }}
                QMenuBar::item:selected {{
                    background: {BLOCK_HOVER};
                }}
                QMenuBar::item:pressed {{
                    background: {BLOCK_HOVER};
                }}
                QMenu {{
                    background-color: {MEDIUM_GREY};
                    color: {TEXT_COLOR};
                    border: 1px solid {HAIRLINE};
                    border-radius: {RADIUS_MD}px;
                }}
                QMenu::item {{
                    padding: 6px 14px;
                    border-radius: {RADIUS_SM}px;
                }}
                QMenu::item:selected {{
                    background-color: {ACCENT_SOFT};
                }}
            """)
        except Exception:
            pass
        
        # The Exit / Documentation / View menu items are gone — their space now
        # hosts the agent-mode controls and the graph's auxiliary controls (the
        # top-bar clusters built below). The two QActions are kept, attached to
        # the window, so the Ctrl+Shift+A shortcut and the Code Node Studio
        # toggle keep working.
        toggle_agent_action = QAction(_("&Agent Mode"), self)
        toggle_agent_action.setIcon(_tabler_qicon("MESSAGE_2", 16))
        toggle_agent_action.setShortcut(QKeySequence("Ctrl+Shift+A"))
        toggle_agent_action.setShortcutContext(Qt.ApplicationShortcut)
        toggle_agent_action.setCheckable(True)
        toggle_agent_action.triggered.connect(self.toggle_agent_mode)
        self.addAction(toggle_agent_action)
        self._toggle_agent_action = toggle_agent_action

        code_studio_action = QAction(_("Code Node Studio"), self)
        code_studio_action.setCheckable(True)
        code_studio_action.triggered.connect(self._toggle_code_studio)
        self.addAction(code_studio_action)
        self._code_studio_action = code_studio_action

        # Modern top bar: our own controls at both ends (the native title bar is
        # gone): mode switch + graph/agent controls on the left, window controls
        # on the right.
        try:
            menu_bar.setFixedHeight(40)
            menu_bar.setCornerWidget(self._build_top_bar_left(), Qt.TopLeftCorner)
            menu_bar.setCornerWidget(self._build_window_controls(), Qt.TopRightCorner)
            menu_bar.installEventFilter(self)
        except Exception:
            pass

    def _make_top_button(self, parent, icon_name, tip, slot, danger=False):
        """One icon button for the window's top bar (same scheme everywhere)."""
        from PyQt5.QtWidgets import QToolButton
        from PyQt5.QtCore import QSize
        from .constants import RADIUS_SM, DANGER_COLOR

        btn = QToolButton(parent)
        btn.setToolTip(tip)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedSize(30, 24)
        try:
            btn.setIcon(_tabler_qicon(icon_name, 16))
            btn.setIconSize(QSize(16, 16))
        except Exception:
            pass
        hover = DANGER_COLOR if danger else "rgba(255,255,255,0.10)"
        btn.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none;"
            f" border-radius: {RADIUS_SM}px; }}"
            f"QToolButton:hover {{ background-color: {hover}; }}"
        )
        btn.clicked.connect(slot)
        return btn

    def _build_window_controls(self):
        """Minimize / maximize-restore / close (top-right corner)."""
        from PyQt5.QtWidgets import QHBoxLayout, QWidget

        bar = QWidget()
        lay = QHBoxLayout(bar)
        # A little padding from the top so the buttons never hug the window /
        # screen top border when the window is maximized.
        lay.setContentsMargins(2, 5, 8, 5)
        lay.setSpacing(2)
        lay.addWidget(self._make_top_button(
            bar, "MINUS", _("Minimize"), self.showMinimized))
        lay.addWidget(self._make_top_button(
            bar, "SQUARE", _("Maximize / Restore"), self._toggle_maximize))
        lay.addWidget(self._make_top_button(
            bar, "X", _("Close"), self.close, danger=True))
        return bar

    def _build_top_bar_left(self):
        """Top-bar cluster on the LEFT — where the menus used to be.

        Holds the mode switch, the graph's auxiliary controls (scheduler, AI
        settings) and the agent-mode controls (collapse-to-notch plus the panel's
        own action buttons, lifted out of the panel header).
        """
        from PyQt5.QtWidgets import QHBoxLayout, QWidget

        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 5, 2, 5)
        lay.setSpacing(2)

        # Mode switch — always visible; icon and action flip with the mode.
        self._agent_mode_btn = self._make_top_button(
            bar, "MESSAGE_2", _("Agent mode"), self._on_agent_mode_btn)
        lay.addWidget(self._agent_mode_btn)

        # Graph auxiliary controls (shown only in build/graph mode).
        self._graph_aux = QWidget(bar)
        gal = QHBoxLayout(self._graph_aux)
        gal.setContentsMargins(0, 0, 0, 0)
        gal.setSpacing(2)
        gal.addWidget(self._make_top_button(
            self._graph_aux, "CALENDAR", _("Scheduler"),
            self.open_scheduler_manager))
        gal.addWidget(self._make_top_button(
            self._graph_aux, "SETTINGS", _("AI Settings"), self.open_ai_settings))
        gal.addWidget(self._make_top_button(
            self._graph_aux, "FILE_TEXT", _("Logs"), self.open_logs))
        lay.addWidget(self._graph_aux)

        # Agent-mode controls (shown only while the panel is open).
        self._agent_nav = QWidget(bar)
        nav_lay = QHBoxLayout(self._agent_nav)
        nav_lay.setContentsMargins(0, 0, 0, 0)
        nav_lay.setSpacing(2)
        self._agent_notch_btn = self._make_top_button(
            self._agent_nav, "CHEVRON_UP", _("Collapse to the agent notch"),
            self.collapse_agent_to_notch)
        nav_lay.addWidget(self._agent_notch_btn)
        try:
            overlay = getattr(self, '_agent_overlay', None)
            actions = overlay.detach_header_actions() if overlay else None
            if actions is not None:
                nav_lay.addWidget(actions)
        except Exception:
            logger.exception("Relocating agent header actions failed")
        self._agent_nav.hide()
        lay.addWidget(self._agent_nav)
        return bar

    def _relayout_top_bar(self):
        """Re-run the top bar's corner layouts after toggling agent controls.

        A corner widget's size hint changes with the agent controls, which the
        menu bar does not pick up by itself — so a cluster would otherwise be
        squeezed into the old (hidden) width.
        """
        try:
            menu = self.menuBar()
            for corner in (Qt.TopLeftCorner, Qt.TopRightCorner):
                bar = menu.cornerWidget(corner)
                if bar is not None:
                    bar.adjustSize()
                    bar.updateGeometry()
        except Exception:
            pass

    def _on_agent_mode_btn(self):
        """Top-bar mode switch: graph <-> agent."""
        if self._agent_mode_active:
            self.exit_agent_mode()
        else:
            self.enter_agent_mode()

    def _sync_agent_mode_button(self):
        """Reflect the current mode in the mode switch and the top-bar clusters."""
        aux = getattr(self, '_graph_aux', None)
        if aux is not None:
            aux.setVisible(not self._agent_mode_active)
        btn = getattr(self, '_agent_mode_btn', None)
        if btn is not None:
            if self._agent_mode_active:
                btn.setIcon(_tabler_qicon("LAYOUT_GRID", 16))
                btn.setToolTip(_("Back to build mode"))
            else:
                btn.setIcon(_tabler_qicon("MESSAGE_2", 16))
                btn.setToolTip(_("Agent mode"))
        self._relayout_top_bar()

    def open_logs(self):
        """Open the session-log browser (search + level filters)."""
        try:
            from .dialogs.logs_dialog import LogsDialog
            self._logs_dialog = LogsDialog(self)
            self._logs_dialog.show()
        except Exception:
            logger.exception("Failed to open the logs dialog")

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def eventFilter(self, obj, event):
        """Drag the frameless window by its top bar; double-click to maximize."""
        try:
            if obj is self.menuBar():
                et = event.type()
                if et == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                    if self.menuBar().actionAt(event.pos()) is None:
                        self._toggle_maximize()
                        return True
                elif et == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                    if self.menuBar().actionAt(event.pos()) is None:
                        handle = self.windowHandle()
                        if handle is not None and hasattr(handle, "startSystemMove"):
                            handle.startSystemMove()
                        else:
                            self._drag_offset = event.globalPos() - self.frameGeometry().topLeft()
                elif et == QEvent.MouseMove and self._drag_offset is not None:
                    if event.buttons() & Qt.LeftButton:
                        self.move(event.globalPos() - self._drag_offset)
                elif et == QEvent.MouseButtonRelease:
                    self._drag_offset = None
        except Exception:
            pass
        return super().eventFilter(obj, event)
    
    def _on_code_node_selected(self, node):
        """Load the selected Code node into the Code Node Studio."""
        try:
            if hasattr(self, 'code_panel') and self.code_panel is not None:
                self.code_panel.load_node(node)
            self._show_code_studio()
        except Exception as e:
            logger.error(f"Code Node Studio load failed: {e}")

    def _toggle_code_studio(self, checked=None):
        """Show/hide the Code Node Studio (View menu)."""
        panel = getattr(self, 'code_panel', None)
        if panel is None:
            return
        if checked:
            self._show_code_studio()
        else:
            self._hide_code_studio()
            return
        action = getattr(self, '_code_studio_action', None)
        if action is not None:
            action.setChecked(bool(checked))
        if checked:
            try:
                sel = self.graph_view.node_graph.selected_nodes() or []
            except Exception:
                sel = []
            code_sel = [n for n in sel
                        if str(getattr(n, '__identifier__', '') or '').lower().endswith('codencode')]
            if code_sel and hasattr(self, 'code_panel'):
                self._on_code_node_selected(code_sel[0])

    def _show_code_studio(self):
        """Open the Code Node Studio as a full-canvas takeover: the node graph
        hides and the studio fills the whole center column (top to bottom,
        below the actions toolbar), so the coding panel gets real space."""
        panel = getattr(self, 'code_panel', None)
        if panel is None:
            return
        try:
            cl = getattr(self.graph_view, '_center_layout', None)
            gw = getattr(self.graph_view, 'node_graph_widget', None)
            if cl is not None:
                gi = cl.indexOf(gw) if gw is not None else -1
                pi = cl.indexOf(panel)
                if gw is not None:
                    try:
                        gw.hide()
                    except Exception:
                        pass
                if gi != -1:
                    cl.setStretch(gi, 0)
                if pi != -1:
                    cl.setStretch(pi, 1)
            panel.setMaximumHeight(16777215)  # no 55% cap — reach the top
        except Exception:
            pass
        # Collapse the floating bars while the studio owns the canvas: they
        # otherwise float over it and cover the studio's own header buttons.
        try:
            self.graph_view.ui_components.set_floating_bars_visible(False)
        except Exception:
            pass
        panel.show()
        panel.raise_()
        try:
            from PyQt5.QtCore import QEasingCurve, QPropertyAnimation
            from PyQt5.QtWidgets import QGraphicsOpacityEffect
            panel.setGraphicsEffect(None)
            eff = QGraphicsOpacityEffect(panel)
            eff.setOpacity(0.0)
            panel.setGraphicsEffect(eff)
            anim = QPropertyAnimation(eff, b'opacity', panel)
            anim.setDuration(160)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda: panel.setGraphicsEffect(None))
            anim.start(QPropertyAnimation.DeleteWhenStopped)
            self._studio_fade_anim = anim
        except Exception:
            pass
        action = getattr(self, '_code_studio_action', None)
        if action is not None:
            action.setChecked(True)

    def _hide_code_studio(self):
        """Collapse the studio back to the graph (restores the canvas)."""
        panel = getattr(self, 'code_panel', None)
        if panel is not None:
            panel.hide()
        try:
            cl = getattr(self.graph_view, '_center_layout', None)
            gw = getattr(self.graph_view, 'node_graph_widget', None)
            if cl is not None:
                gi = cl.indexOf(gw) if gw is not None else -1
                pi = cl.indexOf(panel) if panel is not None else -1
                if gi != -1:
                    cl.setStretch(gi, 1)
                if pi != -1:
                    cl.setStretch(pi, 0)
                if gw is not None:
                    gw.show()
        except Exception:
            pass
        # Bring the floating bars back now that the canvas is visible again.
        try:
            self.graph_view.ui_components.set_floating_bars_visible(True)
        except Exception:
            pass
        action = getattr(self, '_code_studio_action', None)
        if action is not None:
            action.setChecked(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Keep the frameless window's corner grip pinned to the bottom-right.
        grip = getattr(self, '_size_grip', None)
        if grip is not None:
            try:
                grip.move(self.central_widget.width() - grip.width() - 2,
                          self.central_widget.height() - grip.height() - 2)
                grip.raise_()
            except Exception:
                pass
        # The Code Node Studio takes over the whole canvas — no height cap
        # needed (it spans top to bottom while open).

    def enter_agent_mode(self):
        """Show the system-chain agent overlay in place of the builder (one window)."""
        overlay = getattr(self, '_agent_overlay', None)
        if overlay is None:
            return
        # Opening the panel replaces the compact notch.
        notch = getattr(self, '_agent_notch', None)
        if notch is not None:
            notch.hide()
        # The base frame's top bar carries agent navigation while the panel is
        # open (the overlay no longer draws its own nav buttons).
        nav = getattr(self, '_agent_nav', None)
        if nav is not None:
            nav.show()
            self._relayout_top_bar()
        if overlay._chat_controller is None:
            self._init_overlay_chat_controller()
        try:
            self.graph_view.hide()
        except Exception:
            pass
        overlay._set_compact_mode(False)
        overlay.show()
        overlay.raise_()
        self._notch_active = False   # the maximized panel is the agent UI now
        try:
            overlay._input_field.setFocus()
        except Exception:
            pass
        self._agent_mode_active = True
        self._sync_agent_mode_button()
        self._overlay_was_visible = False
        self._dot_was_visible = False
        dot = getattr(self, '_agent_dot', None)
        if dot is not None:
            dot.hide()
        if hasattr(self, '_toggle_agent_action'):
            self._toggle_agent_action.setChecked(True)
        logger.info("Agent mode entered (in-window overlay)")

    def exit_agent_mode(self):
        """Hide the agent overlay/notch and show the builder again."""
        overlay = getattr(self, '_agent_overlay', None)
        if overlay is not None:
            overlay._was_visible_for_automation = False
            overlay._keep_web_server = False
            overlay.hide()
        # The graph UI and the agent UI are never shown at the same time.
        notch = getattr(self, '_agent_notch', None)
        if notch is not None:
            notch.hide()
        nav = getattr(self, '_agent_nav', None)
        if nav is not None:
            nav.hide()
            self._relayout_top_bar()
        try:
            self.graph_view.show()
        except Exception:
            pass
        try:
            if self.isMinimized():
                self.showNormal()
            self.raise_()
        except Exception:
            pass
        self._agent_mode_active = False
        self._notch_active = False
        self._sync_agent_mode_button()
        if hasattr(self, '_toggle_agent_action'):
            self._toggle_agent_action.setChecked(False)
        logger.info("Agent mode exited (back to builder)")

    def toggle_agent_mode(self, checked=None):
        """Toggle agent mode (Ctrl+Shift+A).

        Not active → enter agent mode (show dot, minimize GUI).
        Active, main GUI focused → exit agent mode (restore GUI).
        Active, main GUI NOT focused → toggle dot ↔ overlay.
        """
        # Debounce: both QAction and pynput can fire for the same press
        now = time.time()
        if now - self._toggle_throttle < 0.3:
            return
        self._toggle_throttle = now

        # In-window switch: summoning agent mode raises the compact notch (the
        # quick call does not force the fullscreen panel); clicking the notch
        # opens the panel, and the panel's left chevron returns to the builder.
        if getattr(self, '_agent_inwindow', False):
            if self._agent_mode_active:
                self.exit_agent_mode()
            else:
                self.toggle_agent_notch()
            return

        try:
            overlay = getattr(self, '_agent_overlay', None)
            dot = getattr(self, '_agent_dot', None)
            if overlay is None:
                return

            if self._agent_mode_active:
                # Agent mode is active
                if self.isActiveWindow():
                    # Main GUI was restored by user → exit agent mode
                    self._agent_mode_active = False
                    if dot:
                        dot.hide()
                    overlay.hide()
                    overlay._stop_event.set()
                    self._restore_window()
                    if hasattr(self, '_toggle_agent_action'):
                        self._toggle_agent_action.setChecked(False)
                    logger.info("Agent mode deactivated")
                    return

                # Toggle between dot and overlay
                if overlay.isVisible():
                    overlay._collapse_to_dot()
                elif dot and dot.isVisible():
                    dot.hide()
                    if overlay._chat_controller is None:
                        self._init_overlay_chat_controller()
                    cursor = QCursor.pos()
                    overlay.show_overlay(
                        cursor.x() - overlay.width() // 2,
                        cursor.y() - 20,
                    )
            else:
                # Enter agent mode
                if overlay._chat_controller is None:
                    self._init_overlay_chat_controller()
                self.showMinimized()
                if dot:
                    dot.set_execute_mode(False)
                    dot.show()
                self._agent_mode_active = True
                if hasattr(self, '_toggle_agent_action'):
                    self._toggle_agent_action.setChecked(True)
                logger.info("Agent mode activated")
        except Exception:
            logger.exception("toggle_agent_mode: error")

    def _ensure_global_hotkey(self):
        """Start a pynput listener for Ctrl+Shift+A when app is not focused.

        QAction shortcuts only fire when the Qt app has focus.
        pynput hooks into the OS input stack and works globally.
        Uses GetAsyncKeyState (not modifier tracking) so key-repeat races
        can't desync the modifier state from the physical keyboard.
        """
        if getattr(self, '_hotkey_listener', None) is not None:
            return

        _vk_ctrl = 0x11
        _vk_shift = 0x10
        _get_key = ctypes.windll.user32.GetAsyncKeyState

        def on_press(key):
            # Match 'A' via char or virtual-key code (0x41)
            ch = getattr(key, 'char', None)
            vk = getattr(key, 'vk', None)
            if not ((ch and (ch.lower() == 'a' or ch == '\x01')) or vk == 0x41):
                return
            try:
                if _get_key(_vk_ctrl) & 0x8000 and _get_key(_vk_shift) & 0x8000:
                    logger.info("Global hotkey Ctrl+Shift+A detected via pynput")
                    from PyQt5.QtCore import QTimer
                    QTimer.singleShot(0, self.toggle_agent_mode)
            except Exception:
                logger.exception("pynput on_press error")

        self._hotkey_listener = pynput_keyboard.Listener(on_press=on_press)
        self._hotkey_thread = threading.Thread(
            target=self._hotkey_listener.start, daemon=True
        )
        self._hotkey_thread.start()
        logger.debug("Global hotkey listener (Ctrl+Shift+A) started via pynput")

    def _init_overlay_chat_controller(self):
        """Create the SimpleChainRouter and inject it into the overlay."""
        try:
            overlay = self._agent_overlay
            # Resolve chains dir — chains/ is sibling of LoOper/ dir.
            # main_window.py lives at LoOper/NGUI/, so two dirname calls
            # reach LoOper/ (one fewer than agent_mode.py at NGUI/widgets/).
            import sys as _sys
            if getattr(_sys, "frozen", False):
                exe_dir = os.path.dirname(_sys.executable)
                chains_dir = os.path.join(exe_dir, "chains")
                if not os.path.isdir(chains_dir):
                    chains_dir = os.path.join(exe_dir, "_internal", "chains")
            else:
                project_root = os.path.dirname(
                    os.path.dirname(os.path.abspath(__file__))
                )
                chains_dir = os.path.join(project_root, "chains")

            from player.agentic_ops import SimpleChainRouter
            controller = SimpleChainRouter(chains_dir=chains_dir)

            # Auto-detect system chain: first chain with collection="system"
            # becomes the agent's cognitive architecture.
            for c in controller._chains:
                coll = c.get("collection", "") or ""
                if coll.lower() == "system":
                    controller.set_system_chain(c["path"])
                    logger.info(
                        "MainWindow: auto-selected system chain '%s'", c["id"]
                    )
                    break

            overlay.set_chat_controller(controller)
            logger.debug("Overlay chat controller initialized")
        except Exception as e:
            logger.error(f"Failed to init overlay chat controller: {e}")

    def _on_overlay_closed(self):
        """Overlay closed — in-window that means 'back to the builder'."""
        if getattr(self, '_agent_inwindow', False):
            self.exit_agent_mode()
            return
        # Legacy floating-overlay close: exit agent mode and restore the window.
        self._agent_mode_active = False
        if hasattr(self, '_toggle_agent_action'):
            self._toggle_agent_action.setChecked(False)
        if hasattr(self, '_agent_dot'):
            self._agent_dot.hide()
        self._overlay_was_visible = False
        self._dot_was_visible = False
        self._restore_window()

    def _on_dot_clicked(self):
        """Expand the minimal dot into the full agent overlay."""
        try:
            dot = getattr(self, '_agent_dot', None)
            overlay = self._agent_overlay
            if dot:
                dot.hide()
            if overlay._chat_controller is None:
                self._init_overlay_chat_controller()
            cursor = QCursor.pos()
            overlay.show_overlay(
                cursor.x() - overlay.width() // 2,
                cursor.y() - 20,
            )
        except Exception:
            logger.exception("_on_dot_clicked: error")

    def toggle_agent_notch(self):
        """Summon/hide the compact agent notch at the top-center of the screen.

        This is the quick agent call from the hotkey. The builder (graph) is
        hidden while the notch is up: the graph UI and the agent UI are never
        shown at the same time.
        """
        notch = getattr(self, '_agent_notch', None)
        if notch is None:
            return
        if notch.isVisible():
            self.exit_agent_mode()
            return
        # An open panel gives way to the notch (stay in agent mode).
        overlay = getattr(self, '_agent_overlay', None)
        if overlay is not None and overlay.isVisible():
            overlay._keep_web_server = True
            overlay.hide()
        self._show_agent_notch()

    def _show_agent_notch(self):
        """Show the compact agent notch: graph hidden, app tucked away."""
        notch = getattr(self, '_agent_notch', None)
        if notch is None:
            return
        overlay = getattr(self, '_agent_overlay', None)
        if overlay is not None and overlay._chat_controller is None:
            self._init_overlay_chat_controller()
        # The base frame's agent nav belongs to the maximized panel only.
        nav = getattr(self, '_agent_nav', None)
        if nav is not None:
            nav.hide()
            self._relayout_top_bar()
        try:
            self.graph_view.hide()
        except Exception:
            pass
        # Only the notch stays on screen: the builder is tucked away so the
        # graph UI and the agent UI are never visible together.
        try:
            self.showMinimized()
        except Exception:
            pass
        # Left slot of the notch: the selected system chain's vector avatar, so
        # the user can see who they are talking to.
        try:
            name = overlay.current_chain_avatar() if overlay is not None else ""
            notch.set_chain_avatar(name)
        except Exception:
            pass
        notch.reposition()
        notch.show()
        notch.raise_()
        self._agent_mode_active = True
        self._notch_active = True
        self._sync_agent_mode_button()
        if hasattr(self, '_toggle_agent_action'):
            self._toggle_agent_action.setChecked(True)
        logger.info("Agent notch shown (compact agent mode)")

    def collapse_agent_to_notch(self):
        """Collapse the maximized agent panel back to the compact notch.

        One-click counterpart to the hotkey workaround: takes the panel down
        to the notch (still agent mode; the web server is kept alive).
        """
        overlay = getattr(self, '_agent_overlay', None)
        if overlay is not None:
            overlay._was_visible_for_automation = False
            overlay._keep_web_server = True
            overlay.hide()
        self._show_agent_notch()

    def _on_notch_clicked(self):
        """Notch clicked — open the full in-window agent panel."""
        notch = getattr(self, '_agent_notch', None)
        if notch is not None:
            notch.hide()
        # The in-window panel needs the main window on screen (the notch floats
        # even while the app is minimized), so restore it first.
        try:
            if self.isMinimized():
                self.showNormal()
            else:
                self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            logger.exception("_on_notch_clicked: window restore failed")
        self.enter_agent_mode()

    def _on_notch_submitted(self, text):
        """Enter in the notch input — dispatch the request and KEEP the notch.

        The compact notch is the agent UI in use, so it stays on screen while the
        run processes (it turns red, and only leaves for each screen read). It is
        NOT swapped for the maximized panel here — clicking the notch opens that,
        to read the reply.
        """
        overlay = getattr(self, '_agent_overlay', None)
        if overlay is None:
            return
        if overlay._chat_controller is None:
            self._init_overlay_chat_controller()
        if text:
            try:
                overlay._submit_text(text)
            except Exception:
                logger.exception("_on_notch_submitted: dispatch failed")

    def _on_execution_state(self, executing: bool):
        """Toggle dot/notch appearance between idle (cyan) and executing (red)."""
        notch = getattr(self, '_agent_notch', None)
        if notch is not None:
            notch.set_execute(executing)
        dot = getattr(self, '_agent_dot', None)
        if not dot:
            return
        dot.set_execute_mode(executing)
        if executing:
            # The compact notch is the agent UI in use: keep IT on screen during
            # the run instead of replacing it with the cursor dot.  _notch_active
            # is set while the notch is the active agent UI (it survives the
            # per-capture hide); _notch_was_visible covers a graph run started
            # while the notch was up.
            if notch is not None and (self._notch_active or self._notch_was_visible):
                if not notch.isVisible():
                    notch.reposition()
                    notch.show()
                notch.raise_()
                if dot.isVisible():
                    dot.hide()
                return
            # Show red dot during chain execution (overlay hides for automation)
            if self._agent_mode_active and not dot.isVisible():
                dot.show()
        else:
            # Execution finished — hide dot, automation_restore shows overlay with response
            if dot.isVisible():
                dot.hide()

    def _on_overlay_collapsed(self):
        """Overlay collapsed by its header button."""
        if getattr(self, '_agent_inwindow', False):
            # In-window: collapse to the compact notch (one clear state) instead
            # of leaving the maximized window open but empty.
            self.collapse_agent_to_notch()
            return
        if self._agent_mode_active:
            dot = getattr(self, '_agent_dot', None)
            if dot:
                dot.set_execute_mode(False)
                dot.show()

    def _hide_agent_overlay(self):
        """Hide overlay/dot before chain execution (record what was visible)."""
        overlay = getattr(self, '_agent_overlay', None)
        dot = getattr(self, '_agent_dot', None)
        notch = getattr(self, '_agent_notch', None)
        self._overlay_was_visible = False
        self._dot_was_visible = False
        # The COMPACT NOTCH is deliberate user-only UI and stays on screen while
        # a run processes (it is hidden for each screen read instead, so the
        # agent never sees it).  Taking it down only hid who the user talks to.
        self._notch_was_visible = bool(notch is not None and notch.isVisible())
        if overlay and overlay.isVisible():
            self._overlay_was_visible = True
            overlay.hide()
        elif dot and dot.isVisible():
            self._dot_was_visible = True
            dot.hide()
    
    def new_chain(self):
        """Clears the graph to start a new chain."""
        self.graph_view.clear_graph()
        # A new chain is a different chain: give it its own fresh web browser
        # scope so its recordings never reuse the previous chain's story.
        try:
            cfg_mgr = getattr(self.graph_view, 'config_manager', None)
            if cfg_mgr is not None and hasattr(cfg_mgr, 'reset_chain_scope'):
                cfg_mgr.reset_chain_scope()
            # Workspace organization: a chain created while a collection
            # filter is active belongs to that collection (ChainsLibrary).
            lib = getattr(self, 'chains_library', None)
            if cfg_mgr is not None and lib is not None and hasattr(lib, 'active_collection'):
                cfg_mgr._current_chain_collection = lib.active_collection()
        except Exception:
            pass

    def record_sequence(self):
        """Starts the recording process with user input for sequence name"""
        logger.info("Record sequence requested")
        try:
            # Get sequence name from user
            logger.debug("Showing sequence name input dialog")
            sequence_name, ok = QInputDialog.getText(
                self,
                _("Record New Sequence"),
                _("Enter sequence name:"),
                text=_("new_sequence")
            )
            
            if ok and sequence_name.strip():
                logger.info(f"Starting recording for sequence: {sequence_name.strip()}")
                self.start_recording(sequence_name.strip())
            elif ok:
                logger.warning("User provided empty sequence name")
                QMessageBox.warning(self, _("Invalid Input"), _("Please enter a valid sequence name."))
            else:
                logger.info("User cancelled sequence recording")
        except Exception as e:
            logger.error(f"Error in record_sequence: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, _("Recording Error"), f"Failed to start recording: {str(e)}")

    def execute_chain_config(self, chain_config):
        """Execute a specific chain configuration."""
        try:
            # A new run invalidates the previous run's snapshot used by the
            # Code Node Studio run-replay.
            self._last_player = None
            # Create a deterministic temporary file name based on chain_config md5 hash
            # so that the same chain always gets the same chain_id for context persistence.
            import hashlib
            config_json = json.dumps(chain_config, sort_keys=True, ensure_ascii=False)
            config_hash = hashlib.md5(config_json.encode('utf-8')).hexdigest()[:12]
            
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_filename = os.path.join(temp_dir, f"temp_chain_{config_hash}.json")
            
            logger.info(f"Saving chain configuration to temporary file: {temp_filename}")
            with open(temp_filename, 'w') as f:
                json.dump(chain_config, f, indent=4)
            logger.debug("Chain configuration saved successfully")
            
            # Store temp file path for context node dialog chain_id derivation
            # (only when no real chain file is open - a real file keeps its
            # own identity, so web browser scopes stay durable).
            try:
                cfg_mgr = getattr(self.graph_view, 'config_manager', None)
                if cfg_mgr:
                    if not getattr(cfg_mgr, '_current_chain_file', ''):
                        cfg_mgr._current_chain_file = temp_filename
            except Exception:
                pass

            # Shared web browser scope for playback: the chain runs on the
            # app-wide durable browser (the same one recordings use), so
            # cookies/history recorded once - e.g. a Google login - are
            # present in every run mode and survive app restarts.  While a
            # web recording is actively capturing on that browser, playback
            # must NOT drive it (its actions would be recorded as the
            # user's): it runs on an isolated browser instead.
            web_chain_key = None
            web_isolated = False
            try:
                cfg_mgr = getattr(self.graph_view, 'config_manager', None)
                if cfg_mgr:
                    web_chain_key = getattr(cfg_mgr, '_chain_scope_key', None) or None
                node_ops = getattr(self.graph_view, 'node_operations', None)
                if web_chain_key and getattr(node_ops, '_web_recording_active', False):
                    logger.info(
                        "Web recording in progress - chain playback uses an "
                        "isolated browser instead of the shared browser"
                    )
                    web_chain_key = None
                    web_isolated = True
            except Exception:
                web_chain_key = None
                web_isolated = False
            
            # Hide the floating agent overlay so it doesn't block automation
            self._hide_agent_overlay()

            # Minimize window BEFORE starting playback (similar to recording)
            logger.debug("Minimizing main window for playback")
            self.showMinimized()
            
            # Use QTimer to delay chain execution start, allowing window to minimize properly
            def start_playback_delayed():
                # Execute the chain directly using MultiSequencePlayer
                logger.info(f"Starting direct chain execution with config: {temp_filename}")

                # Orange replay overlay: element boxes, cursor trail + click
                # rings, and a banner naming the action about to run.  Created
                # here (GUI thread, this is a QTimer callback) and torn down in
                # _restore_window when the run ends.
                self._execution_overlay = None
                self._exec_stop = threading.Event()
                try:
                    from .widgets.execution_overlay import ExecutionOverlay
                    from player.execution_overlay_bus import bus as _exec_bus
                    _exec_bus.reset()
                    _ov = ExecutionOverlay(self._exec_stop)
                    _ov.start()
                    self._execution_overlay = _ov
                except Exception as _ov_err:
                    logger.warning(f"Execution overlay unavailable: {_ov_err}")
                try:
                    # Import MultiSequencePlayer for direct execution
                    from player.multi_sequence_player import MultiSequencePlayer
                    from PyQt5.QtWidgets import QInputDialog, QLineEdit

                    # ── Helper: determine if an Input node is a true starting node ──
                    # Scan all nodes' connections in the raw config to see if any
                    # other node connects TO this input (making it mid-chain).
                    def _is_entry_input(node_id):
                        for _list_key in (
                            'sequences', 'conditional_nodes', 'llm_nodes',
                            'input_nodes', 'code_nodes', 'context_nodes',
                            'chain_import_nodes', 'container_nodes', 'handle_nodes', 'mcp_nodes',
                            'form_filler_nodes', 'web_sequences',
                        ):
                            for _node in chain_config.get(_list_key, []):
                                for _conn in _node.get('connections', []):
                                    if _conn.get('target_node_id') == node_id:
                                        return False
                        return True

                    # ── Pre-collect input for Input nodes (main thread safe) ──
                    # QInputDialog must NOT be called from background threads (Qt crash).
                    # We scan chain_config for Input nodes with user_prompt set
                    # and collect their values HERE on the main thread before the
                    # background execution thread starts.
                    # Only collect from TRULY starting nodes — Input nodes that sit
                    # mid-chain (targeted by another node's connection) will prompt
                    # at runtime via ask_user_callback.
                    # Entry carry nodes (no prompt, no default, not agent-modifiable)
                    # cannot resolve in a manual run — there is no agent query to
                    # carry and no upstream — so their value is asked here too;
                    # cancelling keeps the old hard-fail behavior.
                    _collected_inputs = {}
                    try:
                        _input_nodes = chain_config.get('input_nodes', [])
                        if _input_nodes:
                            for _inp in _input_nodes:
                                _node_id = _inp.get('node_id') or _inp.get('id')
                                if _node_id and not _is_entry_input(_node_id):
                                    logger.info(
                                        '[INPUT] Skipping pre-collect for mid-chain '
                                        'Input node %s (will prompt at runtime)',
                                        _node_id,
                                    )
                                    continue
                                _user_prompt = _inp.get('user_prompt', '') or ''
                                _label = _inp.get('label', '') or ''
                                _default = _inp.get('default_value', '') or ''
                                if not _user_prompt:
                                    # Value-holder node (agent query passthrough).
                                    # Defaults and agent-modifiable nodes resolve
                                    # without help — only true carry nodes ask.
                                    if _default or bool(_inp.get('agent_modifiable', False)):
                                        continue
                                    _prompt = (
                                        f"Enter value for '{_label}':"
                                        if _label else 'Enter input value:'
                                    )
                                else:
                                    _prompt = _user_prompt or _label or 'Enter input value:'
                                from player.qt_input import ask_text
                                _result, _ok = ask_text(
                                    _prompt, default=_default,
                                )
                                if _ok and _result is not None:
                                    if _node_id:
                                        _collected_inputs[_node_id] = _result
                                        logger.info(
                                            '[INPUT] Pre-collected input for node %s (%d chars)',
                                            _node_id, len(_result),
                                        )
                    except Exception as _e:
                        logger.warning('[INPUT] Failed to pre-collect input: %s', _e)

                    # ── Ask-user callback (marshals QInputDialog to main thread) ──
                    # Used at runtime when mid-chain Input nodes are reached.
                    # Uses _BgInputDialogHelper with BlockingQueuedConnection to route
                    # the dialog to the main thread (QTimer.singleShot won't work from
                    # a bg thread because there's no Qt event loop on that thread).
                    _bg_dialog_helper = _BgInputDialogHelper()
                    def _ask_user_from_bg_thread(question):
                        return _bg_dialog_helper.ask(question)
                    def _ask_user_v2_from_bg_thread(request):
                        return _bg_dialog_helper.ask_rich(request)
                    
                    # Execute the chain in a separate thread to avoid blocking the
                    # UI.  Module-level `threading` is used: a nested import here
                    # would make `threading` LOCAL to this whole function and break
                    # the overlay's threading.Event() above (UnboundLocalError).
                    
                    def execute_chain(collected=_collected_inputs):
                        try:
                            logger.info("Starting chain execution in background thread")
                            
                            # Load the chain configuration from the temporary file inside the thread
                            logger.debug(f"Reading chain configuration from {temp_filename}")
                            with open(temp_filename, 'r') as f:
                                thread_chain_config = json.load(f)
                            
                            # Import and start keyboard monitoring for ESC key abort
                            from player.keyboard_monitor import start_global_monitoring, stop_global_monitoring, create_stop_flag
                            from player.multi_sequence_player import play_chain_with_tailcalls
                            
                            # Start ESC key monitoring
                            logger.info("Starting keyboard monitoring...")
                            start_global_monitoring()
                            stop_flag = create_stop_flag()
                            
                            # Create callback for visual error reporting when a node fails
                            def _on_node_failed(node_id, node_type, node_data):
                                """Called from background thread when a node fails"""
                                QTimer.singleShot(0, lambda: self._highlight_failed_node(node_id, node_type))

                            # Create callback for mid-execution output node popups (e.g. calculator loop)
                            def _on_output_ready(label, content, **kwargs):
                                """Called from background thread when an output node fires with popup_on_finish.
                                New render_mode/asset kwargs are tolerated; the manual-mode popup
                                shows text mode only."""
                                logger.info('[OUTPUT] on_output_ready scheduled from bg thread (label="%s", %d chars)', label, len(content))
                                def _show():
                                    logger.info('[OUTPUT] _show executing on main thread')
                                    self._restore_window()
                                    self._show_chain_output_popup(label, content)
                                QTimer.singleShot(0, _show)
                            
                            try:
                                # Execute the chain with stop flag for ESC key abort.
                                # Self-callback handoffs (a chain importing itself)
                                # are followed in a flat loop — each handoff
                                # releases the previous execution and starts a
                                # fresh run of the same chain.
                                logger.info("Executing chain...")
                                result, player = play_chain_with_tailcalls(
                                    temp_filename,
                                    ask_user_callback=_ask_user_from_bg_thread,
                                    ask_user_v2_callback=_ask_user_v2_from_bg_thread,
                                    pre_collected_inputs=collected,
                                    on_node_failed=_on_node_failed,
                                    on_output_ready=_on_output_ready,
                                    stop_flag=stop_flag,
                                    web_chain_key=web_chain_key,
                                    web_isolated=web_isolated,
                                )
                                logger.info(f"Chain execution completed with result: {result}")
                                try:
                                    # Retain the finished player (workflow_executor +
                                    # port_store + llm_executor) for the Code Node
                                    # Studio to replay a node against real data.
                                    self._last_player = player
                                except Exception:
                                    pass
                                
                                # After chain execution, check for output nodes with popup_on_finish
                                thread_output_nodes = thread_chain_config.get("output_nodes", [])
                                if thread_output_nodes and hasattr(player, 'llm_executor'):
                                    for _onode in thread_output_nodes:
                                        if _onode.get("popup_on_finish", True):
                                            _nid = _onode.get("node_id", "")
                                            if _nid:
                                                try:
                                                    _oval = player.llm_executor.get_variable(f"node_{_nid}_output")
                                                    if _oval and isinstance(_oval, str) and _oval.strip():
                                                        _label = _onode.get("label", "") or "Chain Output"
                                                        QTimer.singleShot(0, lambda v=_oval, l=_label: self._show_chain_output_popup(l, v))
                                                except Exception:
                                                    pass
                            finally:
                                # Always stop monitoring when done
                                logger.info("Stopping keyboard monitoring...")
                                stop_global_monitoring()
                            
                            # Clean up temporary file
                            try:
                                if os.path.exists(temp_filename):
                                    os.remove(temp_filename)
                                    logger.debug(f"Temporary file {temp_filename} cleaned up")
                            except OSError as cleanup_error:
                                logger.warning(f"Failed to clean up temporary file: {cleanup_error}")
                            
                            # Restore window after playback completes
                            QTimer.singleShot(0, lambda: self._restore_window())
                            
                        except Exception as e:
                            logger.error(f"Error during chain execution: {e}")
                            import traceback
                            logger.error(f"Chain execution traceback: {traceback.format_exc()}")
                            
                            # Stop monitoring on error
                            try:
                                from player.keyboard_monitor import stop_global_monitoring
                                stop_global_monitoring()
                            except:
                                pass
                            
                            # Restore window and show error
                            error_msg = str(e)
                            QTimer.singleShot(0, lambda: self._handle_playback_error(error_msg))
                            
                            # Clean up temporary file even on error
                            try:
                                if os.path.exists(temp_filename):
                                    os.remove(temp_filename)
                            except OSError:
                                pass
                        finally:
                            # Tear the execution overlay down from THIS thread.
                            # QTimer.singleShot(0, _restore_window) is created in
                            # this event-loop-less worker thread, so it NEVER
                            # fires (recording_overlay.py documents the same
                            # trap) - the overlay reads this thread-safe stop
                            # event on its own GUI timer instead.
                            _es = getattr(self, "_exec_stop", None)
                            if _es is not None:
                                try:
                                    _es.set()
                                except Exception:
                                    pass
                    
                    # Start execution in background thread
                    execution_thread = threading.Thread(target=execute_chain, daemon=True)
                    execution_thread.start()
                    logger.info("Chain execution thread started")
                            
                except Exception as e:
                    logger.error(f"Error initializing chain execution: {e}")
                    import traceback
                    logger.error(f"Chain initialization traceback: {traceback.format_exc()}")
                    
                    # Restore window and show error
                    error_msg = str(e)
                    QTimer.singleShot(0, lambda: self._handle_playback_error(error_msg))
                    
                    # Clean up temporary file even on error
                    try:
                        if os.path.exists(temp_filename):
                            os.remove(temp_filename)
                    except OSError:
                        pass
            
            # Use QTimer to start playback after window minimizes
            QTimer.singleShot(500, start_playback_delayed)  # 500ms delay
                    
        except Exception as e:
            logger.error(f"Error in execute_chain_config: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, _("Chain Error"), f"Failed to run chain: {str(e)}")

    def run_chain(self):
        """Runs the current automation chain by saving it to a temporary file and passing it to the player."""
        logger.info("Run chain requested")
        try:
            # Clear the JSON cache to ensure we have the most updated version of all chains
            logger.info("Clearing JSON cache before chain execution")
            try:
                from player.json_cache import clear_cache
                clear_cache()
                logger.info("JSON cache cleared successfully")
            except ImportError as e:
                logger.warning(f"Failed to import cache clearing function: {e}")
            except Exception as e:
                logger.warning(f"Failed to clear cache: {e}")
            
            logger.debug("Getting chain configuration from graph view")
            chain_config = self.graph_view.get_chain_config()
            if not chain_config:
                logger.warning("No chain configuration available")
                QMessageBox.warning(self, _("No Chain"), _("No chain configuration available to run."))
                return

            # Check if there are any nodes to run (sequences, conditionals, LLM, or chain imports)
            sequences = chain_config.get('sequences', [])
            conditional_nodes = chain_config.get('conditional_nodes', [])
            llm_nodes = chain_config.get('llm_nodes', [])
            chain_import_nodes = chain_config.get('chain_import_nodes', [])
            code_nodes = chain_config.get('code_nodes', [])
            container_nodes = chain_config.get('container_nodes', [])
            context_nodes = chain_config.get('context_nodes', [])
            input_nodes = chain_config.get('input_nodes', [])
            handle_nodes = chain_config.get('handle_nodes', [])
            mcp_nodes = chain_config.get('mcp_nodes', [])
            output_nodes = chain_config.get('output_nodes', [])
            web_sequences = chain_config.get('web_sequences', [])
            form_filler_nodes = chain_config.get('form_filler_nodes', [])
            
            total_nodes = len(sequences) + len(conditional_nodes) + len(llm_nodes) + len(chain_import_nodes) + len(code_nodes) + len(container_nodes) + len(context_nodes) + len(input_nodes) + len(handle_nodes) + len(mcp_nodes) + len(output_nodes) + len(web_sequences) + len(form_filler_nodes)
            if total_nodes == 0:
                logger.warning("No nodes in chain configuration")
                QMessageBox.warning(self, _("Empty Chain"), _("No nodes found in the current chain."))
                return

            self.execute_chain_config(chain_config)

        except Exception as e:
            logger.error(f"Error in run_chain: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            QMessageBox.critical(self, _("Chain Error"), f"Failed to run chain: {str(e)}")

    def open_chain(self):
        """Open a chain configuration file."""
        fileName, selected_filter = QFileDialog.getOpenFileName(
            self, _("Open Chain"), "", "JSON Files (*.json);;All Files (*)"
        )
        if fileName:
            try:
                # Check if graph view is ready
                if not self.graph_view.is_ready():
                    QMessageBox.warning(
                        self,
                        _("Graph Not Ready"),
                        _("The graph view is not fully initialized yet. Please wait a moment and try again.")
                    )
                    return
                
                self.graph_view.load_chain(fileName)
            except RuntimeError as e:
                QMessageBox.critical(self, _("Error"), f"Graph not ready: {str(e)}")
            except Exception as e:
                QMessageBox.critical(self, _("Error"), f"Failed to load chain: {str(e)}")

    def save_chain(self):
        """Saves the current chain to a file"""
        options = QFileDialog.Options()
        fileName, selected_filter = QFileDialog.getSaveFileName(self, _("Save Chain File"), "", "JSON Files (*.json)", options=options)
        if fileName:
            try:
                self.graph_view.save_chain(fileName)
                QMessageBox.information(self, _("Success"), _("Chain saved successfully!"))
            except Exception as e:
                QMessageBox.critical(self, _("Save Error"), f"Failed to save chain: {str(e)}")
    
    def start_recording(self, sequence_name):
        """Start recording a sequence using ElementRecorder with full capabilities"""
        logger.info(f"Starting recording process for sequence: {sequence_name}")
        
        # Minimize window BEFORE starting the thread (on main thread)
        logger.debug("Minimizing main window on main thread")
        self.showMinimized()
        
        # Use QTimer to delay thread start, allowing window to minimize properly
        def start_recording_delayed():
            # Shared state for the cosmetic recording overlay, read on the GUI
            # thread by its timer (the overlay is a QWidget - GUI thread only).
            #   rec_stop       - set when recording ends, so the overlay tears
            #                    itself down.  It must NOT rely on
            #                    _restore_window: that is scheduled via
            #                    QTimer.singleShot(0, lambda) from the worker
            #                    thread below, and PyQt creates that timer in the
            #                    event-loop-less worker thread, so it never fires.
            #   rec_mouse_down - set while a button is held, so the overlay hides
            #                    its marks and never bakes into the 35x35 click
            #                    template the replayer matches on.
            rec_stop = threading.Event()
            rec_mouse_down = threading.Event()

            element_provider = None
            overlay = None
            try:
                from .widgets.recording_overlay import RecordingOverlay
                overlay = RecordingOverlay(rec_stop, rec_mouse_down)
                overlay.start()
                # Hit-test at the exact click/drag point (not the last hovered
                # element) so a drag's FROM row is captured correctly.  It MUST
                # go through the overlay: the recorder calls this from pynput's
                # low-level mouse-hook thread, where a direct UIA/COM call is
                # illegal (RPC_E_CANTCALLOUT_ININPUTSYNCCALL) and hard-killed the
                # app mid-recording.  The overlay answers on its own UIA-owning
                # worker thread (see RecordingOverlay.element_descriptor).
                element_provider = overlay.element_descriptor
            except Exception as overlay_err:
                logger.warning(f"Recording overlay unavailable: {overlay_err}")
                overlay = None
            self._recording_overlay = overlay

            def record_thread():
                thread_logger = logging.getLogger(f"{__name__}.record_thread")
                thread_logger.info(f"Recording thread started for sequence: {sequence_name}")
                
                try:
                    target_dir = getattr(self.graph_view, 'sequences_folder', os.path.join(os.getcwd(), 'sequences'))
                    thread_logger.debug(f"Creating sequences directory: {target_dir}")
                    os.makedirs(target_dir, exist_ok=True)
                    original_cwd = os.getcwd()
                    os.chdir(target_dir)
                    
                    # Test pynput import first
                    thread_logger.info("Testing pynput import before ElementRecorder initialization")
                    try:
                        from pynput import mouse, keyboard
                        thread_logger.info("pynput imported successfully")
                    except Exception as e:
                        thread_logger.error(f"Failed to import pynput: {str(e)}")
                        thread_logger.error(f"pynput import traceback: {traceback.format_exc()}")
                        raise
                    
                    # Initialize ElementRecorder with sequence name
                    thread_logger.info(f"Initializing ElementRecorder with sequence name: {sequence_name}")
                    screenshots_dir = os.path.join(target_dir, 'screenshots')
                    recorder = ElementRecorder(
                        sequence_name,
                        screenshots_dir=screenshots_dir,
                        # Size each click's replay template to the element it was
                        # on (UIA bbox from the overlay), not a 35px patch.
                        element_rect_provider=(overlay.element_rect if overlay is not None else None),
                        # Store the element's UIA identity on the action too, so
                        # replay re-finds it directly instead of template matching.
                        element_provider=element_provider,
                    )
                    thread_logger.info("ElementRecorder initialized successfully")

                    # The overlay previews each click's template-crop region
                    # (dashed box) and captions it while the right-Ctrl
                    # visual-match marker is held; the marker state lives in
                    # the recorder.
                    if overlay is not None:
                        try:
                            overlay.set_visual_provider(recorder.visual_marker_active)
                        except Exception:
                            pass
                    
                    print(f"\n🟢 Recording '{sequence_name}'. Try all actions now!")
                    print("🖱 Drag >10px to trigger drag, else click")
                    print("🡅 Scroll actions are grouped for accurate replay")
                    print("⌨ Any key combo works (Ctrl+Alt+Shift+X, Win+R, F-keys...)")
                    print("⏹ ESC to save and exit")
                    
                    # Set up event listeners with proper callbacks
                    thread_logger.debug("Setting up event listener callbacks")
                    def on_click(x, y, button, pressed):
                        try:
                            if pressed:
                                rec_mouse_down.set()
                                recorder.on_mouse_press(x, y, button, pressed)
                            else:
                                # The 35x35 template crop is captured inside
                                # on_mouse_release; clear the flag only AFTER it,
                                # so the overlay stays hidden across the whole
                                # press -> capture window.
                                recorder.on_mouse_release(x, y, button)
                                rec_mouse_down.clear()
                        except Exception as e:
                            thread_logger.error(f"Error in on_click callback: {str(e)}")
                    
                    def on_scroll(x, y, dx, dy):
                        try:
                            recorder.record_scroll(x, y, dx, dy)
                        except Exception as e:
                            thread_logger.error(f"Error in on_scroll callback: {str(e)}")

                    def on_move(x, y):
                        try:
                            recorder.on_mouse_move(x, y)
                        except Exception as e:
                            thread_logger.error(f"Error in on_move callback: {str(e)}")
                    
                    def on_press(key):
                        try:
                            if key == keyboard.Key.esc:
                                thread_logger.info("ESC key pressed, saving sequence")
                                rec_stop.set()  # overlay hides at once
                                recorder.save_sequence()
                                return False  # Stop listener
                            recorder.handle_keypress(key)
                        except Exception as e:
                            thread_logger.error(f"Error in on_press callback: {str(e)}")
                    
                    def on_release(key):
                        try:
                            recorder.handle_keyrelease(key)
                        except Exception as e:
                            thread_logger.error(f"Error in on_release callback: {str(e)}")
                    
                    # Start listeners
                    thread_logger.debug("Creating mouse and keyboard listeners")
                    try:
                        mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click, on_scroll=on_scroll)
                        keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
                        thread_logger.info("Listeners created successfully")
                    except Exception as e:
                        thread_logger.error(f"Failed to create listeners: {str(e)}")
                        thread_logger.error(f"Listener creation traceback: {traceback.format_exc()}")
                        raise
                    
                    thread_logger.info("Starting mouse and keyboard listeners")
                    try:
                        mouse_listener.start()
                        keyboard_listener.start()
                        thread_logger.info("Listeners started, waiting for ESC key")
                        keyboard_listener.join()  # Will block until ESC
                        mouse_listener.stop()
                        thread_logger.info("Listeners stopped successfully")
                        
                        try:
                            # Clear JSON cache to ensure new recording is picked up
                            from player.json_cache import clear_cache
                            clear_cache()
                            logger.info("Cleared JSON cache after recording")
                        except Exception as cache_err:
                            logger.warning(f"Failed to clear cache after recording: {cache_err}")

                        # Refresh sequences list in toolbar on the main thread
                        try:
                            QTimer.singleShot(0, lambda: self.graph_view.refresh_sequences_toolbar())
                            QTimer.singleShot(300, lambda: self.graph_view.refresh_sequences_toolbar())
                        except Exception as refresh_err:
                            thread_logger.error(f"Failed to request sequences refresh: {refresh_err}")
                    except Exception as e:
                        thread_logger.error(f"Error during listener operation: {str(e)}")
                        thread_logger.error(f"Listener operation traceback: {traceback.format_exc()}")
                        raise
                    
                    print(f"\n✅ Recording '{sequence_name}' saved successfully!")
                    thread_logger.info(f"Recording completed successfully for sequence: {sequence_name}")
                    
                    # Restore window using Qt's thread-safe mechanism
                    thread_logger.debug("Requesting window restoration on main thread")
                    QTimer.singleShot(0, lambda: self._restore_window())
                except Exception as e:
                    thread_logger.error(f"Failed to initialize recording: {str(e)}")
                    thread_logger.error(f"Recording initialization traceback: {traceback.format_exc()}")
                    # Show error dialog and restore window using Qt's thread-safe mechanism
                    QTimer.singleShot(0, lambda: self._handle_recording_error(str(e)))
                    raise
                finally:
                    rec_stop.set()  # overlay teardown on every exit path
                    try:
                        os.chdir(original_cwd)
                    except Exception:
                        pass
            
            # Start recording in a separate thread
            logger.debug("Creating recording thread")
            try:
                recording_thread = threading.Thread(target=record_thread, daemon=True)
                recording_thread.start()
                logger.info("Recording thread started successfully")
            except Exception as e:
                logger.error(f"Failed to start recording thread: {str(e)}")
                logger.error(f"Thread creation traceback: {traceback.format_exc()}")
        
        # Use QTimer to start recording after window minimizes
        QTimer.singleShot(500, start_recording_delayed)  # 500ms delay
    
    def test_conditional_node(self, node):
        """Test a conditional node by evaluating it and showing the result"""
        logger.info(f"Testing conditional node: {node.name()}")
        
        try:
            # Extract configuration
            conditional_data = {
                "condition_type": node.get_property('condition_type') or 'presence',
                "image_path": node.get_property('image_path') or '',
                "threshold": float(node.get_property('threshold') or 0.8),
                "wait_time": float(node.get_property('wait_time') or 5),
                "max_loops": int(node.get_property('max_loops') or 10),
                "ocr_text": node.get_property('ocr_text') or '',
                "region": node.get_property('region') or '',
                "timeout": float(node.get_property('timeout') or 5.0),
                "max_attempts": int(node.get_property('max_attempts') or 3),
                "delay_between_attempts": float(node.get_property('delay_between_attempts') or 1.0),
                "case_sensitive": node.get_property('case_sensitive') or 'true',
                "node_id": node.id
            }

            # Web-mode conditionals route on the shared browser, so the web_*
            # properties must travel with the node (see node_executor).
            for _k, _v in (node.properties().get('custom') or {}).items():
                if _k.startswith('web_') and _v not in (None, ''):
                    conditional_data[_k] = _v
            
            # Minimize window to allow seeing the screen
            self.showMinimized()
            QApplication.processEvents()
            time.sleep(0.5)  # Wait for minimize animation
            
            # Evaluate
            from player.multi_sequence.conditional_fallback_handler import ConditionalFallbackHandler
            handler = ConditionalFallbackHandler()
            
            # Use chain configuration global override to support testing inside sandbox if applicable
            sandbox_agent_url = None
            try:
                chain_config = self.graph_view.get_chain_config()
                if chain_config and chain_config.get("sequences"):
                    for seq in chain_config.get("sequences", []):
                        app_ctx = seq.get("app_context")
                        if isinstance(app_ctx, dict) and app_ctx.get("sandboxed"):
                            # The node testing uses the global URL so we must set it
                            from player.computer_vision import set_sandbox_agent_url
                            
                            # Determine the correct URL based on context or running session
                            url = app_ctx.get("sandbox_agent_url") or app_ctx.get("rdp_agent_url")
                            if url:
                                set_sandbox_agent_url(url)
                                break
                            
                            # Fallback check if agent is running and we have URL file
                            import os
                            import json
                            agent_info_path = "C:\\TempShared\\agent_info.json"
                            if os.path.exists(agent_info_path):
                                try:
                                    with open(agent_info_path, "r", encoding="utf-8") as f:
                                        info = json.load(f) or {}
                                    port = info.get("port")
                                    if port:
                                        url = f"http://127.0.0.1:{int(port)}"
                                        set_sandbox_agent_url(url)
                                        break
                                except Exception:
                                    pass
            except Exception:
                pass
                
            try:
                result = handler.evaluate_conditional_from_node(conditional_data)
            finally:
                # Always restore window even if evaluation fails
                self._restore_window()
                QApplication.processEvents()
                time.sleep(0.2)
            
            # Show result
            msg = "TRUE" if result else "FALSE"
            QMessageBox.information(self, _("Condition Result"), _("Evaluation Result: {msg}").format(msg=msg))
            
        except Exception as e:
            logger.error(f"Error testing conditional node: {e}", exc_info=True)
            self._restore_window()
            QMessageBox.critical(self, _("Error"), _("Failed to test condition: {error}").format(error=str(e)))

    def _restore_window(self):
        """Restore the UI that was visible before chain execution.

        Respects one-UI-at-a-time: restores overlay, dot, or main window
        depending on what was visible before the chain ran.
        """
        # Tear down the recording overlay first (created in start_recording).
        ov = getattr(self, '_recording_overlay', None)
        if ov is not None:
            try:
                ov.stop()
            except Exception:
                pass
            self._recording_overlay = None
        # And the execution overlay (created in execute_chain_config).
        ex = getattr(self, '_execution_overlay', None)
        if ex is not None:
            try:
                ex.stop()
            except Exception:
                pass
            self._execution_overlay = None
        _ex_stop = getattr(self, '_exec_stop', None)
        if _ex_stop is not None:
            try:
                _ex_stop.set()
            except Exception:
                pass
            self._exec_stop = None
        if self._notch_was_visible:
            # Compact agent mode was on screen (kept up through the run): keep the
            # notch on top instead of restoring the builder or the cursor dot.
            self._notch_was_visible = False
            notch = getattr(self, '_agent_notch', None)
            if notch is not None:
                if not notch.isVisible():
                    notch.reposition()
                    notch.show()
                notch.raise_()
            dot = getattr(self, '_agent_dot', None)
            if dot is not None and dot.isVisible():
                dot.hide()
            return

        if self._overlay_was_visible:
            # Agent overlay was active — restore overlay, keep main window minimized
            self._overlay_was_visible = False
            overlay = getattr(self, '_agent_overlay', None)
            if overlay and not overlay.isVisible():
                overlay._position_top_right()
                overlay.show()
                overlay.raise_()
                try:
                    overlay._input_field.setFocus()
                    QTimer.singleShot(50, overlay._input_field.setFocus)
                except Exception:
                    pass
            return

        if self._dot_was_visible:
            # Minimal dot was active — restore dot, keep main window minimized
            self._dot_was_visible = False
            dot = getattr(self, '_agent_dot', None)
            if dot and not dot.isVisible():
                dot.show()
            return

        # Main graph mode — restore main window
        self.showNormal()
        self.raise_()
        self.activateWindow()

        # Force re-application of stylesheet to fix a known Qt-on-Windows bug
        # where minimized windows restored via showNormal() lose their
        # QApplication-level dark theme and render in the default boxy style.
        app = QApplication.instance()
        if app:
            ss = app.styleSheet()
            app.setStyleSheet("")
            app.setStyleSheet(ss)
        self.update()
    
    def _handle_recording_error(self, error_msg):
        """Handle recording error and restore window (thread-safe)"""
        self._restore_window()
        QMessageBox.critical(self, _("Recording Error"), _("Recording failed: {error}").format(error=error_msg))

    def _show_chain_output_popup(self, label, content):
        """Show a popup with the collected chain output.

        Called via QTimer.singleShot from the background thread after
        manual chain execution completes.
        """
        if not content or not isinstance(content, str) or not content.strip():
            return
        from .dialogs.base_dialog import ModernDialog
        from PyQt5.QtWidgets import QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QGroupBox
        dlg = ModernDialog(self, title=label or "Chain Output", show_help_button=False)
        dlg.resize(640, 440)
        card = QGroupBox(_("Chain Output"))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(content)
        layout.addWidget(text_edit)
        dlg.content_layout.addWidget(card)
        btn_layout = QHBoxLayout()
        close_btn = QPushButton(_("Close"))
        close_btn.setProperty("class", "primary")
        close_btn.clicked.connect(dlg.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        dlg.content_layout.addLayout(btn_layout)
        dlg.exec_()
    
    def _highlight_failed_node(self, node_id, node_type):
        """
        Highlight a failed node with a thick red border and bring GUI to front.
        Thread-safe: must be called from the main thread (via QTimer.singleShot).
        """
        logger.info(f"Highlighting failed node: {node_id} (type: {node_type})")
        self._restore_window()
        
        try:
            # Find the GUI node by its NodeGraphQt ID
            node = self.graph_view.graph_manager.get_node_by_id(node_id)
            if node:
                # Apply thick red border to visually indicate failure
                node.set_border_color(255, 0, 0)
                node.set_selected(True)
                # Attempt to set border width (NodeGraphQt doesn't expose this directly,
                # but we try common patterns)
                try:
                    node_view = getattr(node, 'view', None)
                    if node_view and hasattr(node_view, 'set_border_width'):
                        node_view.set_border_width(4)
                except Exception:
                    pass
                logger.info(f"Failed node highlighted: {node_id} ({node.name()})")
            else:
                logger.warning(f"Failed node {node_id} not found in GUI graph - may be in a sub-chain")
        except Exception as e:
            logger.error(f"Error highlighting failed node {node_id}: {e}")

    def _handle_playback_error(self, error_msg):
        """Handle playback error and restore window (thread-safe)"""
        self._restore_window()
        QMessageBox.critical(self, _("Playback Error"), _("Failed to run chain: {error}").format(error=error_msg))

    def open_scheduler_manager(self):
        """Open the Scheduler Manager dialog"""
        try:
            dialog = SchedulerManagerDialog(self, self.scheduler_service)
            dialog.exec_()
        except Exception as e:
            logger.error(f"Failed to open SchedulerManagerDialog: {e}")
            QMessageBox.critical(self, _("Scheduler Error"), _("Failed to open scheduler: {error}").format(error=e))
    
    def open_ai_settings(self):
        """Open the AI Settings dialog"""
        try:
            dialog = AISettingsDialog(self)
            dialog.exec_()
        except Exception as e:
            logger.error(f"Failed to open AISettingsDialog: {e}")
            QMessageBox.critical(self, _("AI Settings Error"), _("Failed to open AI settings: {error}").format(error=e))

    def open_user_guide(self):
        try:
            dialog = UserGuideDialog(self, topic="general", title=_("LoOper User Guide"))
            dialog.exec_()
        except Exception as e:
            logger.error(f"Failed to open UserGuideDialog: {e}")
            QMessageBox.critical(self, _("Documentation Error"), _("Failed to open user guide: {error}").format(error=e))

    def closeEvent(self, event):
        """Ensure background services stop on app close"""
        try:
            if hasattr(self, 'scheduler_service') and self.scheduler_service:
                self.scheduler_service.stop()
        except Exception as e:
            logger.error(f"Error stopping SchedulerService: {e}")
        try:
            if hasattr(self, '_hotkey_listener') and self._hotkey_listener:
                self._hotkey_listener.stop()
        except Exception:
            pass
        super().closeEvent(event)
