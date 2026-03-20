import json
import os

from .chat_logger import ChatLogger
from .learning_store import LearningStore
from .plan_generator import PlanGenerator
from .tool_selector import ToolSelector
from .chain_runner import ChainRunner
from .typing_ops import TypingAssistant
from .failure_analyzer import FailureAnalyzer
from .execution_engine import ExecutionEngine
from . import status_utils


class ChatController:
    def __init__(self, graph_view, post_ui, post_ui_sync, add_chat_bubble, scroll_to_bottom, force_ui_flush, set_processing_label, start_processing_indicator, stop_processing_indicator, translate, accent_color, on_minimize=None, on_restore=None, get_model=None):
        self._graph_view = graph_view
        self._post_ui = post_ui
        self._post_ui_sync = post_ui_sync
        self._add_chat_bubble = add_chat_bubble
        self._scroll_to_bottom = scroll_to_bottom
        self._force_ui_flush = force_ui_flush
        self._set_processing_label = set_processing_label
        self._start_processing_indicator = start_processing_indicator
        self._stop_processing_indicator = stop_processing_indicator
        self._translate = translate
        self._accent_color = accent_color
        self._on_minimize = on_minimize
        self._on_restore = on_restore
        self._llm_executor = None
        self._get_model = get_model
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._chat_logger = ChatLogger(base_dir)
        self._learning_store = LearningStore(base_dir, self._log_event)
        self._plan_generator = PlanGenerator(self._get_llm_executor, self._log_llm_interaction, self._get_selected_model)
        self._tool_selector = ToolSelector(self._get_llm_executor, self._log_event, self._log_llm_interaction, self._get_selected_model)
        self._chain_runner = ChainRunner(self._get_llm_executor, on_minimize=on_minimize, on_restore=on_restore)
        self._typing_assistant = TypingAssistant(self._get_llm_executor, on_minimize=on_minimize, on_restore=on_restore, get_model=self._get_selected_model)
        self._failure_analyzer = FailureAnalyzer(self._get_llm_executor, self._learning_store, self._log_llm_interaction, self._get_selected_model)
        self._execution_engine = ExecutionEngine(
            tool_selector=self._tool_selector,
            chain_runner=self._chain_runner,
            typing_assistant=self._typing_assistant,
            failure_analyzer=self._failure_analyzer,
            log_event=self._log_event,
            post_ui=self._post_ui,
            post_ui_sync=self._post_ui_sync,
            add_chat_bubble=self._add_chat_bubble,
            scroll_to_bottom=self._scroll_to_bottom,
            force_ui_flush=self._force_ui_flush,
            set_processing_label=self._set_processing_label,
            translate=self._translate,
            accent_color=self._accent_color
        )

    def handle_request(self, text):
        try:
            if self._start_processing_indicator:
                self._start_processing_indicator(self._translate("Processing request"))
            self._chat_logger.start_log(text)
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
                self._log_event("no_tools_available", {"query": text})
                if self._stop_processing_indicator:
                    self._stop_processing_indicator()
                self._post_ui(lambda: self._add_chat_bubble(self._translate("No chains available."), False))
                self._post_ui(self._scroll_to_bottom)
                self._chat_logger.end_log({"success": False, "reason": "no_tools"})
                return
            self._post_ui(lambda: self._set_processing_label(self._translate("Generating plan...")) if self._set_processing_label else None)
            known_learnings = self._learning_store.format_for_prompt(self._learning_store.relevant(text))
            plan = self._plan_generator.generate_plan(text, tools, known_learnings)
            if status_utils.is_status_query(text):
                plan = status_utils.normalize_status_plan(plan, text)
            self._log_event("plan_generated", {"query": text, "plan": plan})
            if len(plan) > 1:
                plan_text = "Plan:\n" + "\n".join([f"{i + 1}. {step}" for i, step in enumerate(plan)])
                self._post_ui(lambda: self._add_chat_bubble(plan_text, False))
                self._post_ui(self._scroll_to_bottom)
            execution_result = self._execution_engine.execute_plan_steps(plan, text, tools)
            self._log_event("execution_result", execution_result)
            if execution_result and not execution_result.get("success", True):
                self._execution_engine.handle_execution_failure(text, plan, tools, execution_result)
            if status_utils.is_status_query(text):
                status_text = self._chain_runner.get_last_chain_result_text()
                status_value = status_utils.parse_open_status(status_text)
                subject = status_utils.extract_status_subject(text)
                self._log_event("status_check", {"status_text": status_text, "status_value": status_value, "subject": subject})
                if status_value is None:
                    self._log_event("status_check_failed", {"status_text": status_text})
                    failure_result = {
                        "success": False,
                        "failed_steps": ["status_check"],
                        "last_result_text": status_text
                    }
                    self._execution_engine.handle_execution_failure(text, plan, tools, failure_result)
                elif status_value is True:
                    if subject:
                        self._post_ui(lambda: self._add_chat_bubble(self._translate("{name} is already open.").format(name=subject), False))
                    else:
                        self._post_ui(lambda: self._add_chat_bubble(self._translate("The app is already open."), False))
                elif status_value is False:
                    if subject:
                        self._post_ui(lambda: self._add_chat_bubble(self._translate("{name} is not open. Opening now.").format(name=subject), False))
                    else:
                        self._post_ui(lambda: self._add_chat_bubble(self._translate("The app is not open. Opening now."), False))
                    follow_text = f"Open {subject}".strip() if subject else "Open the app"
                    follow_plan = self._plan_generator.generate_plan(follow_text, tools, known_learnings)
                    self._log_event("followup_plan", {"query": follow_text, "plan": follow_plan})
                    if len(follow_plan) > 1:
                        plan_text = "Plan:\n" + "\n".join([f"{i + 1}. {step}" for i, step in enumerate(follow_plan)])
                        self._post_ui(lambda: self._add_chat_bubble(plan_text, False))
                        self._post_ui(self._scroll_to_bottom)
                    follow_result = self._execution_engine.execute_plan_steps(follow_plan, follow_text, tools)
                    self._log_event("followup_execution_result", follow_result)
                    if follow_result and not follow_result.get("success", True):
                        self._execution_engine.handle_execution_failure(follow_text, follow_plan, tools, follow_result)
            if self._stop_processing_indicator:
                self._stop_processing_indicator()
            if len(plan) > 1:
                self._post_ui(lambda: self._add_chat_bubble(self._translate("All tasks completed."), False))
            self._post_ui(self._scroll_to_bottom)
            self._chat_logger.end_log({"success": True})
        except Exception as e:
            if self._stop_processing_indicator:
                self._stop_processing_indicator()
            self._post_ui(lambda: self._add_chat_bubble(self._translate("Chat execution failed."), False))
            self._post_ui(self._scroll_to_bottom)
            self._log_event("dispatch_error", {"error": str(e)})
            self._chat_logger.end_log({"success": False, "reason": "exception", "error": str(e)})

    def _load_chain_tools(self):
        tools = []
        folder = getattr(self._graph_view, "chains_folder", None)
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

    def _log_event(self, event, payload=None):
        self._chat_logger.log_event(event, payload)

    def _log_llm_interaction(self, name, llm_config, response):
        payload = {
            "name": name,
            "model": llm_config.get("model"),
            "system_message": llm_config.get("system_message"),
            "prompt": llm_config.get("prompt"),
            "output_variable": llm_config.get("output_variable"),
            "response": response
        }
        self._log_event("llm_interaction", payload)

    def _get_llm_executor(self, llm_executor_class):
        if self._llm_executor is None and llm_executor_class:
            self._llm_executor = llm_executor_class()
        return self._llm_executor

    def _get_selected_model(self):
        try:
            if self._get_model:
                return self._get_model()
        except Exception:
            return None
        return None
