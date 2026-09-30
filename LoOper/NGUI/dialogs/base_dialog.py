from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QWidget, QLabel, QPushButton, 
                             QHBoxLayout, QGraphicsDropShadowEffect, QSizeGrip,
                             QScrollArea, QFrame, QTextBrowser, QComboBox, QSpinBox,
                             QDoubleSpinBox, QAbstractButton, QShortcut)
from PyQt5.QtCore import QRect, Qt, QPoint, QTimer
from PyQt5.QtGui import QColor, QFont, QCursor, QKeySequence
from ..constants import DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR, ACCENT_COLOR
import os


_USER_GUIDE_TOPIC_TO_HEADING = {
    "getting-started": "## Getting Started",
    "playback": "## Playback and Execution",
    "schedules": "## Schedules",
    "nodes": "## Node Types",
    "node-dialogs": "## Node Dialogs Reference",
    "sandboxing": "## Sandboxing",
    "sequence-dialog": "### Sequence Node Dialog",
    "conditional-dialog": "### Conditional Node Dialog",
    "llm-dialog": "### LLM Node Dialog",
    "chain-import-dialog": "### Chain Import Dialog",
    "code-node-dialog": "### Code Node Dialog",
    "context-node-dialog": "### Context Node Dialog",
    "tts-dialog": "### TTS Node Dialog",
    "form-filler-node": "### 7. Form Filler Node",
    "container-node": "### 8. Container (VM) Node",
}


def _extract_markdown_section(markdown_text, heading_line):
    if not heading_line:
        return markdown_text

    lines = markdown_text.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == heading_line.strip():
            start_idx = i
            break

    if start_idx is None:
        return markdown_text

    current_level = len(heading_line) - len(heading_line.lstrip("#"))
    end_idx = len(lines)

    for j in range(start_idx + 1, len(lines)):
        candidate = lines[j].lstrip()
        if not candidate.startswith("#"):
            continue
        candidate_level = len(candidate) - len(candidate.lstrip("#"))
        if candidate_level <= current_level:
            end_idx = j
            break

    section = "\n".join(lines[start_idx:end_idx]).strip()
    return section if section else markdown_text


class UserGuideDialog(QDialog):
    def __init__(self, parent=None, topic="general", title="LoOper User Guide"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 700)

        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        layout.addWidget(self.browser)

        guide_text = self._load_user_guide_markdown()
        heading = _USER_GUIDE_TOPIC_TO_HEADING.get(topic) if topic else None
        section_text = _extract_markdown_section(guide_text, heading) if guide_text else ""

        set_markdown = getattr(self.browser, "setMarkdown", None)
        if callable(set_markdown):
            set_markdown(section_text)
        else:
            self.browser.setPlainText(section_text)

    def _load_user_guide_markdown(self):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        guide_path = os.path.join(project_root, "docs", "USER_GUIDE.md")
        try:
            with open(guide_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return "docs/USER_GUIDE.md not found."

# ---------------------------------------------------------------------------
# Hover help: the green element picker for dialog controls
# ---------------------------------------------------------------------------

# The same green as the desktop recorder's element picker (see
# NGUI/widgets/recording_overlay.py): a border-only box, a solid chip naming
# the control, and a dark panel carrying the text.
_HELP_BOX_QSS = f"background: transparent; border: 2px solid {ACCENT_COLOR};"
_HELP_CHIP_QSS = (
    f"background: {ACCENT_COLOR}; color: #00201a;"
    "padding: 1px 6px; border-radius: 3px; font: 11px monospace;"
)
_HELP_PANEL_QSS = (
    "background: rgba(0,32,26,0.92); color: #00e0b8;"
    "border: 1px solid rgba(0,224,184,0.55); border-radius: 6px;"
    "padding: 8px 12px; font: 11px monospace;"
)
_HELP_HINT = ("HOVER A CONTROL TO READ WHAT IT DOES \u2014 ESC OR ? CLOSES")
_HELP_POLL_MS = 30


def _control_name(widget):
    """Short name for the picker's chip: the control's own first text line."""
    getter = getattr(widget, "text", None)
    if callable(getter):
        try:
            first = (getter() or "").strip().splitlines()[0]
            if first:
                return first[:60]
        except Exception:
            pass
    return type(widget).__name__


def _control_options(widget):
    """Values a control accepts, read off the control itself so every dialog
    lists its options without authoring a word of help text."""
    try:
        if isinstance(widget, QComboBox):
            items = [widget.itemText(i) for i in range(widget.count())]
            return "options: " + ", ".join(items[:16]) if items else ""
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            return "range: %s - %s, step %s" % (
                widget.minimum(), widget.maximum(), widget.singleStep(),
            )
        if isinstance(widget, QAbstractButton) and widget.isCheckable():
            return "states: ON / OFF"
    except Exception:
        pass
    return ""


def _derived_help(widget):
    """Fallback usage text for a control that set no ``whatsThis``."""
    tip = (widget.toolTip() or "").strip()
    if tip:
        return tip
    getter = getattr(widget, "placeholderText", None)
    if callable(getter):
        try:
            placeholder = (getter() or "").strip()
            if placeholder:
                return placeholder
        except Exception:
            pass
    # The label the field is introduced by, in its parent's child order.
    parent = widget.parentWidget()
    if parent is not None:
        kids = [c for c in parent.children() if isinstance(c, QWidget)]
        try:
            index = kids.index(widget)
        except ValueError:
            index = -1
        for previous in reversed(kids[:index]):
            if isinstance(previous, QLabel):
                text = (previous.text() or "").strip()
                if text:
                    return text
    return ""


class HoverHelp(QWidget):
    """Green element picker for a dialog: outline the hovered control and pin
    its usage text beside it - the desktop picker's look, inside the dialog.

    The cursor is POLLED rather than tracked with mouse events: a child only
    receives move events while a button is held (or with mouse tracking on),
    and this must follow the cursor over every control.  The overlay is
    click-through, so the dialog stays usable while it is up, and its own
    widgets are skipped when hit-testing so hovering the panel does not
    retarget the picker.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

        self._box = QFrame(self)
        self._box.setStyleSheet(_HELP_BOX_QSS)
        self._chip = QLabel(self)
        self._chip.setStyleSheet(_HELP_CHIP_QSS)
        self._panel = QLabel(self)
        self._panel.setStyleSheet(_HELP_PANEL_QSS)
        self._panel.setWordWrap(True)
        self._hint = QLabel(_HELP_HINT, self)
        self._hint.setStyleSheet(_HELP_PANEL_QSS)
        for widget in (self._box, self._chip, self._panel, self._hint):
            widget.hide()

        self._timer = QTimer(self)
        self._timer.setInterval(_HELP_POLL_MS)
        self._timer.timeout.connect(self._follow)

    # ------------------------------------------------------------------ API

    def start(self):
        """Cover the dialog and begin following the cursor."""
        host = self.parentWidget()
        if host is not None:
            self.setGeometry(host.rect())
        self.show()
        self.raise_()
        self._hint.adjustSize()
        self._place_hint()
        self._hint.show()
        self._timer.start()
        self._follow()

    def stop(self):
        """Stop both the poll and every mark."""
        self._timer.stop()
        self._clear()
        self._hint.hide()
        self.hide()

    # --------------------------------------------------------------- internal

    def _own(self, widget):
        """True for this overlay's own widgets (they must never be targets)."""
        return widget in (self, self._box, self._chip, self._panel, self._hint)

    def _widget_at(self, widget, pos):
        """Deepest visible child of *widget* at *pos*, overlay widgets skipped."""
        for child in reversed(widget.children()):
            if not isinstance(child, QWidget) or child.isWindow():
                continue
            if self._own(child) or not child.isVisible():
                continue
            geometry = child.geometry()
            if geometry.contains(pos):
                return self._widget_at(child, pos - geometry.topLeft())
        return widget

    def _help_for(self, widget):
        """Usage text + options for *widget*, walking up to the nearest entry."""
        host = self.parentWidget()
        node = widget
        while node is not None and node is not host:
            text = (node.whatsThis() or "").strip()
            options = _control_options(node)
            if text:
                return "%s\n\n%s" % (text, options) if options else text
            if options:
                return options
            node = node.parentWidget()
        return _derived_help(widget)

    def _place_hint(self):
        self._hint.move(max(0, self.width() - self._hint.width() - 12), 12)

    def _follow(self):
        """Poll the cursor, retarget the picker, draw the marks."""
        host = self.parentWidget()
        if host is None:
            return
        if self.size() != host.size():
            self.setGeometry(host.rect())
            self._place_hint()
        pos = host.mapFromGlobal(QCursor.pos())
        if not self.rect().contains(pos):
            self._clear()
            return
        # Reading the callout must not retarget it.
        if self._panel.geometry().contains(pos) or self._chip.geometry().contains(pos):
            return
        target = self._widget_at(host, pos)
        text = self._help_for(target) if target is not host else ""
        if not text:
            self._clear()
            return
        self._draw(target, text)

    def _draw(self, target, text):
        # The overlay is NOT an ancestor of the control, so the control's rect
        # is taken in the dialog's space and shifted into the overlay's.
        host = self.parentWidget()
        top_left = target.mapTo(host, QPoint(0, 0)) - self.pos()
        rect = QRect(top_left, target.size())

        self._box.setGeometry(rect)
        self._box.show()

        self._chip.setText(_control_name(target))
        self._chip.adjustSize()
        self._chip.move(max(0, rect.left()),
                        max(0, rect.top() - self._chip.height() - 2))
        self._chip.show()

        self._panel.setText(text)
        self._panel.setFixedWidth(max(240, min(rect.width(), int(self.width() * 0.7))))
        self._panel.adjustSize()
        x = max(0, min(rect.left(), self.width() - self._panel.width()))
        y = rect.bottom() + 6
        if y + self._panel.height() > self.height():
            y = max(0, rect.top() - self._panel.height() - 6)
        self._panel.move(x, y)
        self._panel.show()

    def _clear(self):
        self._box.hide()
        self._chip.hide()
        self._panel.hide()


class ModernDialog(QDialog):
    def __init__(self, parent=None, title="Dialog", help_topic="general", show_help_button=True):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.help_topic = help_topic
        # Green hover-help picker; None while it is closed.
        self._help_overlay = None
        
        # Initialize drag position
        self.old_pos = None
        
        # Main layout for the whole dialog (including shadow margin)
        # We use a distinct name to avoid conflict if subclasses try to access 'layout'
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(10, 10, 10, 10) # Margin for shadow
        
        # Container widget (the actual visible window)
        self.container = QWidget()
        self.container.setObjectName("ModernDialogContainer")
        self.container.setStyleSheet(f"""
            #ModernDialogContainer {{
                background-color: {DARK_GREY};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
            }}
        """)
        
        # Shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(15)
        shadow.setColor(QColor(0, 0, 0, 150))
        shadow.setOffset(0, 0)
        self.container.setGraphicsEffect(shadow)
        
        self.root_layout.addWidget(self.container)
        
        # Container layout
        self.container_layout = QVBoxLayout(self.container)
        # Ensure padding inside the container respects the border radius
        self.container_layout.setContentsMargins(1, 1, 1, 1) 
        self.container_layout.setSpacing(0)
        
        # Title bar
        self.title_bar = QWidget()
        self.title_bar.setFixedHeight(40)
        self.title_bar.setStyleSheet(f"""
            QWidget {{
                background-color: {MEDIUM_GREY};
                border-top-left-radius: 9px;  /* Match container radius minus border */
                border-top-right-radius: 9px;
                border-bottom: 1px solid {LIGHT_GREY};
            }}
        """)
        
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(15, 0, 15, 0) # Increased right margin to avoid corner clip
        
        self.title_label = QLabel(title)
        self.title_label.setFont(QFont("Segoe UI", 10, QFont.Bold))
        self.title_label.setStyleSheet(f"color: {TEXT_COLOR}; border: none; background: transparent;")

        self.help_btn = None
        if show_help_button:
            self.help_btn = QPushButton("?")
            self.help_btn.setCheckable(True)
            self.help_btn.setToolTip(
                "Hover help: hover a control to read what it does and which "
                "values it accepts (ESC closes)"
            )
            self.help_btn.setFixedSize(30, 30)
            self.help_btn.setCursor(Qt.PointingHandCursor)
            # A push button inside a dialog is auto-default, so a focused title
            # bar button quietly becomes the dialog's default button and Enter
            # activates it instead of OK.  It must never take focus.
            self.help_btn.setAutoDefault(False)
            self.help_btn.setFocusPolicy(Qt.NoFocus)
            self.help_btn.setStyleSheet(f"""
                QPushButton {{
                    color: {TEXT_COLOR};
                    background: transparent;
                    border: none;
                    border-radius: 15px;
                    font-size: 14px;
                    padding: 0px;
                    margin: 0px;
                    min-width: 30px;
                    max-width: 30px;
                    min-height: 30px;
                    max-height: 30px;
                }}
                QPushButton:hover, QPushButton:checked {{
                    background-color: {ACCENT_COLOR};
                    color: {DARK_GREY};
                }}
            """)
            self.help_btn.toggled.connect(self._on_help_toggled)

        # ESC closes the picker - and only the picker - while it is up; with
        # the picker closed ESC keeps the dialog's own reject behaviour.
        self._help_esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self._help_esc.setContext(Qt.WidgetWithChildrenShortcut)
        self._help_esc.activated.connect(self.close_help)
        self._help_esc.setEnabled(False)
        
        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        # The ✕ is wired to reject().  A push button inside a dialog is
        # auto-default, so a focused ✕ becomes the dialog's default button and
        # Enter activates it: a submitted answer (or a half-edited node
        # config) is thrown away instead of accepted.  It must never take
        # focus nor the default role - Enter always belongs to OK.
        self.close_btn.setAutoDefault(False)
        self.close_btn.setFocusPolicy(Qt.NoFocus)
        # Ensure button stays on top and doesn't get clipped
        self.close_btn.raise_() 
        self.close_btn.setStyleSheet(f"""
            QPushButton {{
                color: {TEXT_COLOR};
                background: transparent;
                border: none;
                border-radius: 15px; /* Half of 30px size */
                font-size: 14px;
                padding: 0px;
                margin: 0px;
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
            }}
            QPushButton:hover {{
                background-color: #FF4B4B;
                color: white;
            }}
        """)
        self.close_btn.clicked.connect(self.reject)
        
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()
        if self.help_btn is not None:
            title_layout.addWidget(self.help_btn, 0, Qt.AlignVCenter)
        title_layout.addWidget(self.close_btn, 0, Qt.AlignVCenter) # Explicitly center vertically
        
        self.container_layout.addWidget(self.title_bar)
        
        # Content area (Scrollable)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setStyleSheet(f"""
            QScrollArea {{
                background-color: {DARK_GREY};
                border: none;
            }}
            QScrollBar:vertical {{
                border: none;
                background: {DARK_GREY};
                width: 10px;
                margin: 0px 0px 0px 0px;
            }}
            QScrollBar::handle:vertical {{
                background: {LIGHT_GREY};
                min-height: 20px;
                border-radius: 5px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar:horizontal {{
                border: none;
                background: {DARK_GREY};
                height: 10px;
                margin: 0px 0px 0px 0px;
            }}
            QScrollBar::handle:horizontal {{
                background: {LIGHT_GREY};
                min-width: 20px;
                border-radius: 5px;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0px;
            }}
        """)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet(f"""
            QWidget {{
                background-color: {DARK_GREY};
                color: {TEXT_COLOR};
            }}
            QLabel {{
                color: {TEXT_COLOR};
            }}
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QListWidget, QTableWidget {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                padding: 5px;
                selection-background-color: {ACCENT_COLOR};
                selection-color: {DARK_GREY};
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QListWidget:focus {{
                border: 1px solid {ACCENT_COLOR};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {LIGHT_GREY};
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
                color: {ACCENT_COLOR};
            }}
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border: 1px solid {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
                border: none;
            }}
            /* Primary/Action Button Style */
            QPushButton[class="primary"] {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
                border: none;
            }}
            QPushButton[class="primary"]:hover {{
                background-color: #00E0B8;
                border: 1px solid {ACCENT_COLOR};
            }}
            QPushButton[class="primary"]:pressed {{
                background-color: #008F75;
            }}
            
            QTabWidget::pane {{
                border: 1px solid {LIGHT_GREY};
                background-color: {DARK_GREY};
                border-radius: 5px;
            }}
            QTabBar::tab {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            QTabBar::tab:selected {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
            }}
            QTabBar::tab:hover {{
                background-color: {LIGHT_GREY};
            }}
        """)
        
        # Child classes should add their layout to self.content_layout
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(20, 20, 20, 20)
        self.content_layout.setSpacing(10)
        
        self.scroll_area.setWidget(self.content_widget)
        self.container_layout.addWidget(self.scroll_area)
        
        # Resize grip
        grip_layout = QHBoxLayout()
        grip_layout.setContentsMargins(0, 0, 5, 5)
        grip_layout.addStretch()
        self.size_grip = QSizeGrip(self)
        self.size_grip.setFixedSize(20, 20)
        self.size_grip.setStyleSheet("background: transparent;")
        grip_layout.addWidget(self.size_grip)
        self.container_layout.addLayout(grip_layout)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Map event pos to title bar coordinates
            title_bar_pos = self.title_bar.mapFrom(self, event.pos())
            if self.title_bar.rect().contains(title_bar_pos):
                self.old_pos = event.globalPos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.old_pos:
            delta = event.globalPos() - self.old_pos
            self.move(self.pos() + delta)
            self.old_pos = event.globalPos()
        super().mouseMoveEvent(event)

    # ------------------------------------------------- hover help ("?")

    def _on_help_toggled(self, on):
        if on:
            self.open_help()
        else:
            self.close_help()

    def open_help(self):
        """Show the green hover-help picker: hover any control to read what it
        does and which values it accepts."""
        if self._help_overlay is not None:
            return
        self._help_overlay = HoverHelp(self)
        self._help_overlay.start()
        self._help_esc.setEnabled(True)

    def close_help(self):
        """Hide the picker (ESC, or the "?" button clicked again)."""
        overlay, self._help_overlay = self._help_overlay, None
        if overlay is not None:
            overlay.stop()
            overlay.deleteLater()
        self._help_esc.setEnabled(False)
        if self.help_btn is not None and self.help_btn.isChecked():
            self.help_btn.setChecked(False)

    def mouseReleaseEvent(self, event):
        self.old_pos = None
        super().mouseReleaseEvent(event)
        
    def setWindowTitle(self, title):
        if hasattr(self, 'title_label'):
            self.title_label.setText(title)
        super().setWindowTitle(title)
