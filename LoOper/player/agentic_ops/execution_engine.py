import time


class ExecutionEngine:
    def __init__(self, tool_selector, chain_runner, typing_assistant, failure_analyzer, log_event, post_ui, post_ui_sync, add_chat_bubble, scroll_to_bottom, force_ui_flush, set_processing_label, translate, accent_color):
        self._tool_selector = tool_selector
        self._chain_runner = chain_runner
        self._typing_assistant = typing_assistant
        self._failure_analyzer = failure_analyzer
        self._log_event = log_event
        self._post_ui = post_ui
        self._post_ui_sync = post_ui_sync
        self._add_chat_bubble = add_chat_bubble
        self._scroll_to_bottom = scroll_to_bottom
        self._force_ui_flush = force_ui_flush
        self._set_processing_label = set_processing_label
        self._translate = translate
        self._accent_color = accent_color

    def execute_plan_steps(self, plan, text, tools):
        success_all = True
        failed_steps = []
        selections = []
        for step in plan:
            selected_id = self._tool_selector.select_tool(step, tools, text)
            if not selected_id:
                selections.append({"step": step, "kind": "none"})
                continue
            if selected_id in ("sys_write_text", "sys_reason"):
                selections.append({"step": step, "kind": selected_id})
                continue
            tool = next((t for t in tools if t.get("id") == selected_id), None)
            if not tool:
                selections.append({"step": step, "kind": "none"})
                continue
            selections.append({"step": step, "kind": "chain", "tool": tool})
        if self._log_event:
            self._log_event("selections", {"plan": plan, "selections": selections})

        for i, item in enumerate(selections):
            if len(plan) > 1:
                if self._set_processing_label:
                    self._post_ui(lambda: self._set_processing_label(self._translate("Executing step {}/{}...").format(i + 1, len(plan))))
            kind = item.get("kind")
            if kind == "chain":
                tool = item.get("tool") or {}
                step_text = item.get("step", "")
                self._post_ui_sync(lambda: self._add_chat_bubble(self._translate("Task: {}").format(step_text), False, self._accent_color))
                self._post_ui_sync(self._scroll_to_bottom)
                self._post_ui_sync(self._force_ui_flush)
                if self._log_event:
                    self._log_event("task_start", {"step": step_text, "tool_id": tool.get("id")})
                time.sleep(0.4)
                success = self._chain_runner.run_chain_batch([tool])
                task_result = {
                    "success": success,
                    "last_result_text": self._chain_runner.get_last_chain_result_text(),
                    "chain_tool_results": self._chain_runner.get_chain_tool_results()
                }
                if self._log_event:
                    self._log_event("task_result", {"step": step_text, **task_result})
                if success:
                    self._post_ui(lambda: self._add_chat_bubble(self._translate("Done: {}").format(step_text), False, self._accent_color))
                else:
                    success_all = False
                    if step_text:
                        failed_steps.append(step_text)
                    self._post_ui(lambda: self._add_chat_bubble(self._translate("Failed: {}").format(step_text), False))
                self._post_ui(self._scroll_to_bottom)
                self._post_ui_sync(self._force_ui_flush)
                reason_payload = {
                    "step": step_text,
                    "success": success,
                    "last_result_text": task_result.get("last_result_text"),
                    "chain_tool_results": task_result.get("chain_tool_results")
                }
                if self._log_event:
                    self._log_event("reason_step", reason_payload)
                self._post_ui(lambda: self._add_chat_bubble(self._translate("Thinking about result for: {}").format(step_text), False, self._accent_color))
                self._post_ui(self._scroll_to_bottom)
                self._post_ui_sync(self._force_ui_flush)
                time.sleep(1.0)
                if not success:
                    failure_result = {
                        "success": False,
                        "failed_steps": [step_text] if step_text else ["step"],
                        "last_result_text": task_result.get("last_result_text")
                    }
                    self.handle_execution_failure(text, [step_text] if step_text else [], tools, failure_result)
                continue

            if kind == "sys_write_text":
                text_to_type = self._typing_assistant.generate_text_content(text, item.get("step", ""))
                if self._log_event:
                    self._log_event("typing_start", {"step": item.get("step", ""), "text": text_to_type})
                self._post_ui(lambda: self._add_chat_bubble(self._translate("Typing: {}").format(text_to_type), False, self._accent_color))
                self._post_ui(self._scroll_to_bottom)
                self._post_ui_sync(self._force_ui_flush)
                success = self._typing_assistant.perform_typing(text_to_type)
                if self._log_event:
                    self._log_event("typing_result", {"success": success})
                if not success:
                    success_all = False
                    failed_steps.append(item.get("step", ""))
                    self._post_ui(lambda: self._add_chat_bubble(self._translate("Typing failed."), False))
                continue

            if kind == "sys_reason":
                if self._log_event:
                    self._log_event("reason_step", {"step": item.get("step", "")})
                self._post_ui(lambda: self._add_chat_bubble(self._translate("Thinking: {}").format(item.get("step", "")), False, self._accent_color))
                self._post_ui(self._scroll_to_bottom)
                self._post_ui_sync(self._force_ui_flush)
                time.sleep(1.5)
                continue

            if kind == "none":
                success_all = False
                failed_steps.append(item.get("step", ""))
                if self._log_event:
                    self._log_event("no_matching_tool", {"step": item.get("step", "")})
                self._post_ui(lambda: self._add_chat_bubble(self._translate("No matching chain found for step: {}").format(item.get("step", "")), False))
                self._post_ui(self._scroll_to_bottom)
        return {
            "success": success_all,
            "failed_steps": failed_steps,
            "last_result_text": self._chain_runner.get_last_chain_result_text()
        }

    def handle_execution_failure(self, user_text, plan, tools, execution_result):
        analysis = self._failure_analyzer.analyze_failure(user_text, plan, execution_result, tools)
        if self._log_event:
            self._log_event("failure_analysis", {"analysis": analysis, "execution_result": execution_result})
        if analysis and analysis.get("recovery_plan"):
            recovery_plan = analysis.get("recovery_plan")
            if isinstance(recovery_plan, str):
                recovery_plan = [s.strip(" -") for s in recovery_plan.split("\n") if s.strip()]
            if not isinstance(recovery_plan, list):
                recovery_plan = []
            if len(recovery_plan) > 1:
                plan_text = "Plan:\n" + "\n".join([f"{i + 1}. {step}" for i, step in enumerate(recovery_plan)])
                self._post_ui(lambda: self._add_chat_bubble(plan_text, False))
                self._post_ui(self._scroll_to_bottom)
            self.execute_plan_steps(recovery_plan, user_text, tools)
