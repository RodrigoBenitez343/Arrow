from . import status_utils


class ToolSelector:
    def __init__(self, get_llm_executor, log_event, log_llm_interaction, get_model=None):
        self._get_llm_executor = get_llm_executor
        self._log_event = log_event
        self._log_llm_interaction = log_llm_interaction
        self._get_model = get_model

    def select_tool(self, text, tools, user_text=None):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return None
        llm_executor = self._get_llm_executor(LLMExecutor)
        if status_utils.is_status_query(user_text or text) and status_utils.is_status_step(text):
            picked = status_utils.pick_status_tool(user_text or text, tools)
            if picked:
                if self._log_event:
                    self._log_event("status_tool_selected", {"query": user_text or text, "tool_id": picked})
                return picked
        tool_desc_map = {t["id"]: t.get("tool_desc", "") for t in tools}
        model_name = None
        if self._get_model:
            model_name = self._get_model()
        if not model_name:
            model_name = "cogito:latest"
        llm_config = {
            "model": model_name,
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
            selected = llm_executor.execute_llm_node(node, None, None)
            llm_output = llm_executor.get_variable("llm_output")
            if self._log_llm_interaction:
                self._log_llm_interaction("tool_selection", llm_config, llm_output)
        except Exception:
            return None
        if selected in tool_desc_map:
            return selected
        return None
