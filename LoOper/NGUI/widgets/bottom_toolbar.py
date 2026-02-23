import logging
import json
import os
import threading
import time
import re
try:
    import pyautogui
except ImportError:
    pyautogui = None

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QFrame, QSizePolicy,
    QLabel, QComboBox, QPushButton, QTextEdit, QScrollArea, QApplication
)
from PyQt5.QtCore import Qt, QPoint, QSize, QTimer, QMetaObject, pyqtSlot

from ..constants import DARK_GREY, LIGHT_GREY, ACCENT_COLOR, TEXT_COLOR, BLOCK_COLOR, BLOCK_HOVER, MEDIUM_GREY
from ..i18n import _
from .actions_toolbar import load_tabler_icon

logger = logging.getLogger(__name__)


class BottomActionsToolbar(QWidget):
    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._llm_executor = None
        self._ui_queue = []
        self._processing_bubble = None
        self._processing_label = None
        self._processing_timer = None
        self._processing_dots = 0
        self._build_ui()

    def _build_ui(self):
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("bottomActionsContent")
        self._content_frame.setStyleSheet(
            f"QFrame#bottomActionsContent {{ background-color: {DARK_GREY}; border: 1px solid rgba(80, 80, 80, 0.5); border-radius: 10px; }}"
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
        model_label = QLabel(_("Model"), self._content_frame)
        model_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        header.addWidget(model_label, 0, Qt.AlignRight)
        self._model_combo = QComboBox(self._content_frame)
        self._model_combo.addItems([_("Local UI"), _("Model A"), _("Model B")])
        self._model_combo.setStyleSheet(
            f"QComboBox {{ background-color: {BLOCK_COLOR}; color: {TEXT_COLOR}; border: 1px solid {LIGHT_GREY}; border-radius: 8px; padding: 4px 10px; min-width: 120px; }}"
            f"QComboBox:hover {{ border-color: {ACCENT_COLOR}; }}"
            f"QComboBox::drop-down {{ border: 0px; }}"
        )
        header.addWidget(self._model_combo, 0, Qt.AlignRight)
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
        self._placeholder_label = QLabel(_("Start a conversation"), self._chat_container)
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
        ico = load_tabler_icon('CHEVRON_DOWN', 14)
        if ico:
            self._toggle_btn_bottom.setIcon(ico)
            self._toggle_btn_bottom.setIconSize(QSize(14, 14))
        else:
            self._toggle_btn_bottom.setText("▾")
        self._toggle_btn_bottom.setAutoRaise(True)
        self._toggle_btn_bottom.setCursor(Qt.PointingHandCursor)
        self._toggle_btn_bottom.setFixedSize(28, 28)
        self._toggle_btn_bottom.setStyleSheet(
            f"QToolButton {{ background-color: rgba(32,32,32,180); border: 1px solid {LIGHT_GREY}; border-radius: 14px; }}"
            f"QToolButton:hover {{ border-color: {ACCENT_COLOR}; }}"
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
            is_visible = self._content_frame.isVisible()
            self._content_frame.setVisible(not is_visible)
            name = 'CHEVRON_UP' if is_visible else 'CHEVRON_DOWN'
            ico = load_tabler_icon(name, 16)
            if ico:
                self._toggle_btn_bottom.setIcon(ico)
            else:
                self._toggle_btn_bottom.setText("▾" if is_visible else "▴")
            self._apply_layout_stretch(not is_visible)
            QTimer.singleShot(50, self._position_bottom_toggle)
        except Exception:
            logger.exception("Error toggling bottom toolbar")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._position_bottom_toggle)
            QTimer.singleShot(0, self._apply_bubble_widths)
        except Exception:
            pass

    def _position_bottom_toggle(self):
        try:
            if not hasattr(self, '_toggle_btn_bottom') or self._toggle_btn_bottom is None:
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
            self._processing_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
            max_width = self._bubble_max_width()
            self._apply_label_width(self._processing_label, max_width)
            self._processing_label.setStyleSheet(f"color: {TEXT_COLOR};")
            layout.addWidget(self._processing_label)
            self._processing_bubble.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
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
        base = self._processing_label.text().split("...")[0].split("..")[0].split(".")[0]
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
            text_width = fm.boundingRect(0, 0, 10_000, 10_000, Qt.TextWordWrap, text).width()
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
                self._start_processing_indicator(_("Processing request"))
                tools = self._load_chain_tools()
                tools.append({
                    "id": "sys_write_text",
                    "name": "Write/Type Text",
                    "description": "Simulates keyboard typing. Use this step whenever you need to enter text, after a previous step has focused an input field.",
                    "tool_desc": "Write/Type Text: Simulates keyboard typing. Use this step whenever you need to enter text, after a previous step has focused an input field."
                })
                
                tools.append({
                    "id": "sys_reason",
                    "name": "Reason/Think",
                    "description": "Pause to analyze the situation or context before proceeding. Useful for complex workflows.",
                    "tool_desc": "Reason/Think: Pause to analyze the situation or context before proceeding."
                })

                if not tools:
                    self._stop_processing_indicator()
                    self._post_ui(lambda: self._add_chat_bubble(_("No chains available."), False))
                    self._post_ui(self._scroll_to_bottom)
                    return
                
                self._post_ui(lambda: self._processing_label.setText(_("Generating plan...")) if self._processing_label else None)
                plan = self._generate_plan(text, tools)
                
                if len(plan) > 1:
                    plan_text = "Plan:\n" + "\n".join([f"{i+1}. {step}" for i, step in enumerate(plan)])
                    self._post_ui(lambda: self._add_chat_bubble(plan_text, False))
                    self._post_ui(self._scroll_to_bottom)
                
                for i, step in enumerate(plan):
                    if len(plan) > 1:
                         self._post_ui(lambda: self._processing_label.setText(_("Executing step {}/{}...").format(i+1, len(plan))) if self._processing_label else None)
                    
                    selected_id = self._select_chain_tool(step, tools)
                    if not selected_id:
                        self._post_ui(lambda: self._add_chat_bubble(_("No matching chain found for step: {}").format(step), False))
                        self._post_ui(self._scroll_to_bottom)
                        continue

                    if selected_id == "sys_write_text":
                        text_to_type = self._generate_text_content(text, step)
                        self._post_ui(lambda: self._add_chat_bubble(_("Typing: {}").format(text_to_type), False, ACCENT_COLOR))
                        self._post_ui(self._scroll_to_bottom)
                        self._post_ui_sync(self._force_ui_flush)
                        
                        success = self._perform_typing(text_to_type)
                        if not success:
                             self._post_ui(lambda: self._add_chat_bubble(_("Typing failed."), False))
                        continue

                    if selected_id == "sys_reason":
                        self._post_ui(lambda: self._add_chat_bubble(_("Thinking: {}").format(step), False, ACCENT_COLOR))
                        self._post_ui(self._scroll_to_bottom)
                        self._post_ui_sync(self._force_ui_flush)
                        time.sleep(1.5)
                        continue

                    tool = next((t for t in tools if t.get("id") == selected_id), None)
                    if not tool:
                        continue

                    chain_name = tool.get("name") or selected_id
                    response = self._describe_chain_action(step, tool)
                    self._post_ui_sync(lambda: self._add_chat_bubble(response, False, ACCENT_COLOR))
                    self._post_ui_sync(self._scroll_to_bottom)
                    self._post_ui_sync(self._force_ui_flush)
                    
                    time.sleep(0.6)
                    success = self._run_chain_file(tool.get("path"))
                    
                    if not success:
                         self._post_ui(lambda: self._add_chat_bubble(_("Step failed: {}").format(step), False))

                self._stop_processing_indicator()
                if len(plan) > 1:
                     self._post_ui(lambda: self._add_chat_bubble(_("All tasks completed."), False))
                self._post_ui(self._scroll_to_bottom)

            except Exception as e:
                self._stop_processing_indicator()
                logger.error(f"Chat dispatch error: {e}")
                self._post_ui(lambda: self._add_chat_bubble(_("Chat execution failed."), False))
                self._post_ui(self._scroll_to_bottom)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _generate_plan(self, text, tools):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return [text]

        if self._llm_executor is None:
            self._llm_executor = LLMExecutor()

        tool_descriptions = "\n".join([f"- {t['name']}: {t.get('description', '')}" for t in tools])
        
        prompt = (
            "You are an expert planner for an automation agent.\n"
            "Your goal is to create a logical sequence of steps to fulfill the user's request.\n"
            "Use the provided tools for actions like opening apps, clicking buttons, or navigating.\n"
            "- If a tool description says it 'clicks' or 'focuses' something, assume it does so.\n"
            "- Use 'Write/Type Text' when you need to enter text (e.g., search queries, messages) *after* an input field is focused.\n"
            "- Use 'Reason/Think' if you need a step to analyze the current state or decide on the next move.\n"
            "- Do not combine multiple distinct actions (like opening an app AND typing) into one step unless a single tool handles both.\n"
            "- Put each step on its own line, starting with a number.\n\n"
            f"User Request: {text}\n\n"
            f"Available Tools:\n{tool_descriptions}\n\n"
            "Output ONLY a numbered list of steps (e.g., '1. Step one'). Do not include any other text."
        )

        llm_config = {
            "model": "llama3.2:latest",
            "api_url": get_default_api_url(),
            "system_message": "You are a precise planner. Output only a numbered list of steps.",
            "prompt": prompt,
            "temperature": 0.2,
            "max_tokens": 500,
            "output_variable": "plan_output",
            "input_source": "none",
            "use_comorag": False,
            "use_async": False,
            "write_text": False,
        }

        node = {
            "id": "chat_planner_node",
            "type": "llm",
            "data": {"llm_configuration": llm_config},
            "inputs": [],
        }

        try:
            self._llm_executor.execute_llm_node(node, None, None)
            resp = self._llm_executor.get_variable("plan_output")
            if not resp:
                return [text]
            
            raw = str(resp)
            steps = []
            matches = list(re.finditer(r'(?:^|\n|\s)\d+[\\.\)]\s+', raw))
            if matches:
                for idx, match in enumerate(matches):
                    start = match.end()
                    end = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw)
                    step = raw[start:end].strip().strip(" -;")
                    if step:
                        steps.append(step)
            if not steps:
                for line in raw.split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    match = re.match(r'^\d+[\\.\)]\s*(.+)', line)
                    if match:
                        steps.append(match.group(1).strip())
                    elif line.startswith('- '):
                        steps.append(line[2:].strip())
            
            return steps if steps else [text]
            
        except Exception as e:
            logger.error(f"Plan generation failed: {e}")
            return [text]

    def _load_chain_tools(self):
        tools = []
        folder = getattr(self.graph_view, "chains_folder", None)
        if not folder or not os.path.isdir(folder):
            return tools
        files = sorted([f for f in os.listdir(folder) if f.lower().endswith(".json")])
        used_ids = set()
        for name in files:
            path = os.path.join(folder, name)
            base = os.path.splitext(name)[0]
            display_name = base
            description = ""
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                display_name = data.get("name", display_name)
                description = data.get("description", "")
            except Exception:
                pass
            tool_id = "".join(ch if (ch.isalnum() or ch in ("_", "-")) else "_" for ch in str(base))
            if not tool_id:
                tool_id = "chain"
            original_id = tool_id
            idx = 2
            while tool_id in used_ids:
                tool_id = f"{original_id}_{idx}"
                idx += 1
            used_ids.add(tool_id)
            if description:
                tool_desc = f"{display_name}: {description}"
            else:
                tool_desc = str(display_name)
            tools.append(
                {
                    "id": tool_id,
                    "name": str(display_name),
                    "description": str(description),
                    "tool_desc": tool_desc,
                    "path": path,
                }
            )
        return tools

    def _select_chain_tool(self, text, tools):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return None
        if self._llm_executor is None:
            self._llm_executor = LLMExecutor()
        tool_desc_map = {t["id"]: t.get("tool_desc", "") for t in tools}
        llm_config = {
            "model": "llama3.2:latest",
            "api_url": get_default_api_url(),
            "system_message": "You select tools to fulfill the user's request. Choose exactly one tool that best matches the request using the available descriptions.\n"
                              "If the request is to type, write, search, or enter text, choose the 'Write/Type Text' tool.\n"
                              "If the request is to think, reason, or analyze, choose the 'Reason/Think' tool.\n"
                              "Respond ONLY with 'USE_TOOL:<id>' and nothing else.",
            "prompt": text,
            "temperature": 0.1,
            "max_tokens": 150,
            "output_variable": "llm_output",
            "input_source": "none",
            "use_comorag": False,
            "use_async": False,
            "write_text": False,
            "tool_descriptions": tool_desc_map,
        }
        node = {
            "id": "chat_tool_selector",
            "type": "llm",
            "data": {"llm_configuration": llm_config},
            "inputs": [{"from_node": t["id"], "input_port": "tools"} for t in tools],
        }
        try:
            selected = self._llm_executor.execute_llm_node(node, None, None)
        except Exception:
            return None
        if selected in tool_desc_map:
            return selected
        return None

    def _describe_chain_action(self, user_text, tool):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return _("I will execute {name} ({chain_id}) to perform the requested action.").format(
                name=tool.get("name") or tool.get("id"),
                chain_id=tool.get("id")
            )
        if self._llm_executor is None:
            self._llm_executor = LLMExecutor()
        chain_name = tool.get("name") or tool.get("id")
        chain_id = tool.get("id")
        chain_desc = tool.get("description") or ""
        prompt = (
            "User request:\n"
            f"{user_text}\n\n"
            "Selected chain:\n"
            f"- Name: {chain_name}\n"
            f"- ID: {chain_id}\n"
            f"- Description: {chain_desc}\n\n"
            "Write one concise sentence explaining what you will do and why. "
            "Start with: I will execute {name} ({id}) to ...".format(name=chain_name, id=chain_id)
        )
        llm_config = {
            "model": "llama3.2:latest",
            "api_url": get_default_api_url(),
            "system_message": "You explain an automation plan in one complete, concise sentence. Do not end with a colon or incomplete phrase.",
            "prompt": prompt,
            "temperature": 0.2,
            "max_tokens": 220,
            "output_variable": "chat_plan_output",
            "input_source": "none",
            "use_comorag": False,
            "use_async": False,
            "write_text": False,
        }
        node = {
            "id": "chat_plan_node",
            "type": "llm",
            "data": {"llm_configuration": llm_config},
            "inputs": [],
        }
        try:
            self._llm_executor.execute_llm_node(node, None, None)
            resp = self._llm_executor.get_variable("chat_plan_output")
        except Exception:
            resp = ""
        resp = str(resp or "").strip()
        if (not resp) or resp.endswith(":") or resp.endswith(" to") or resp.endswith(" to..."):
            return _("I will execute {name} ({chain_id}) to perform the requested action.").format(
                name=chain_name,
                chain_id=chain_id
            )
        return resp

    def _run_chain_file(self, chain_path):
        if not chain_path or not os.path.exists(chain_path):
            return False
        try:
            with open(chain_path, "r", encoding="utf-8") as f:
                chain_config = json.load(f)
        except Exception:
            return False
        window = self.graph_view.window() if self.graph_view else None
        if window and hasattr(window, "showMinimized"):
            QTimer.singleShot(0, window.showMinimized)
        try:
            from player.multi_sequence_player import MultiSequencePlayer
            from player.keyboard_monitor import start_global_monitoring, stop_global_monitoring, create_stop_flag
        except Exception:
            return False
        ran_ok = True
        try:
            player = MultiSequencePlayer(chain_config, chain_file_path=chain_path)
            start_global_monitoring()
            stop_flag = create_stop_flag()
            try:
                player.play_chain(stop_flag=stop_flag)
            finally:
                stop_global_monitoring()
        except Exception:
            ran_ok = False
        if window and hasattr(window, "_restore_window"):
            QTimer.singleShot(0, window._restore_window)
        return ran_ok

    def _generate_text_content(self, user_request, step_description):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return step_description

        if self._llm_executor is None:
            self._llm_executor = LLMExecutor()

        prompt = (
            f"User Request: {user_request}\n"
            f"Current Action Step: {step_description}\n\n"
            "Based on the user request and the current action step, determine the exact text that needs to be typed.\n"
            "If the step implies searching for something, output the search term.\n"
            "If the step implies writing a message, output the full message content.\n"
            "Output ONLY the text to be typed. Do not include any explanation or quotes unless they are part of the text."
        )

        llm_config = {
            "model": "llama3.2:latest",
            "api_url": get_default_api_url(),
            "system_message": "You are a text generator for automation. Output only the raw text to be typed.",
            "prompt": prompt,
            "temperature": 0.1,
            "max_tokens": 100,
            "output_variable": "typing_content",
            "input_source": "none",
            "use_comorag": False,
            "use_async": False,
            "write_text": False,
        }

        node = {
            "id": "chat_typing_generator",
            "type": "llm",
            "data": {"llm_configuration": llm_config},
            "inputs": [],
        }

        try:
            self._llm_executor.execute_llm_node(node, None, None)
            resp = self._llm_executor.get_variable("typing_content")
            return str(resp).strip() if resp else ""
        except Exception:
            return ""

    def _perform_typing(self, text):
        if not text or pyautogui is None:
            return False
        
        window = self.graph_view.window() if self.graph_view else None
        if window and hasattr(window, "showMinimized"):
            QMetaObject.invokeMethod(window, "showMinimized", Qt.QueuedConnection)
            time.sleep(0.5)
            
        try:
            pyautogui.write(text, interval=0.05)
            time.sleep(0.5)
            return True
        except Exception:
            return False
        finally:
            if window and hasattr(window, "_restore_window"):
                 QTimer.singleShot(0, window._restore_window)
