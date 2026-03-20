import json


class FailureAnalyzer:
    def __init__(self, get_llm_executor, learning_store, log_llm_interaction, get_model=None):
        self._get_llm_executor = get_llm_executor
        self._learning_store = learning_store
        self._log_llm_interaction = log_llm_interaction
        self._get_model = get_model

    def analyze_failure(self, user_text, plan, execution_result):
        last_result = execution_result.get("last_result_text") if execution_result else ""
        failed_steps = execution_result.get("failed_steps") if execution_result else []
        reason_hint = self._detect_focus_issue(last_result, failed_steps)
        learnings = self._learning_store.format_for_prompt(self._learning_store.relevant(user_text))
        llm_payload = {
            "user_request": user_text,
            "plan": plan,
            "failed_steps": failed_steps,
            "last_result": last_result,
            "reason_hint": reason_hint,
            "known_learnings": learnings
        }
        llm_response = self._analyze_failure_with_llm(llm_payload)
        if not llm_response and reason_hint:
            analysis = {
                "issue": "Actions failed because the browser was not open or focused",
                "cause": "The plan attempted to type or click before ensuring the browser window was open and focused",
                "fix": "Open the browser, bring it to the foreground, then retry the original action",
                "bad_example": "Type in the address bar without opening or focusing the browser",
                "good_example": "Open the browser, focus it, then type the address and press Enter",
                "tags": ["browser", "focus", "precondition"],
                "recovery_plan": [
                    "Open the browser",
                    "Bring the browser window to the foreground",
                    "Retry the original action"
                ]
            }
            self._learning_store.record(analysis)
            return analysis
        if not llm_response:
            return None
        recovery_plan = llm_response.get("recovery_plan") or []
        analysis = {
            "issue": llm_response.get("issue", ""),
            "cause": llm_response.get("cause", ""),
            "fix": llm_response.get("fix", ""),
            "bad_example": llm_response.get("bad_example", ""),
            "good_example": llm_response.get("good_example", ""),
            "tags": llm_response.get("tags", []),
            "recovery_plan": recovery_plan
        }
        self._learning_store.record(analysis)
        return analysis

    def _detect_focus_issue(self, last_result, failed_steps):
        text = " ".join([str(last_result or ""), " ".join([str(s) for s in (failed_steps or [])])]).lower()
        keywords = ["focus", "foreground", "active window", "not open", "not opened", "window not", "not running", "browser", "address bar"]
        return any(k in text for k in keywords)

    def _analyze_failure_with_llm(self, payload):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return None
        llm_executor = self._get_llm_executor(LLMExecutor)
        prompt = (
            "You are a recovery planner for desktop automation.\n"
            "Follow a learning loop: identify the failure cause, propose a corrected plan, and extract a learning.\n"
            "If the issue is missing preconditions (app not open, window not focused), include steps to open and focus.\n"
            "Return a JSON object with keys: issue, cause, fix, bad_example, good_example, tags, recovery_plan.\n"
            "bad_example and good_example should be short single-sentence examples.\n"
            "recovery_plan should be a list of numbered steps as plain strings.\n\n"
            f"Context:\n{json.dumps(payload, ensure_ascii=False)}"
        )
        model_name = None
        if self._get_model:
            model_name = self._get_model()
        if not model_name:
            model_name = "cogito:latest"
        llm_config = {
            "model": model_name,
            "api_url": get_default_api_url(),
            "system_message": "Return only valid JSON. No extra text.",
            "prompt": prompt,
            "temperature": 0.2,
            "max_tokens": 500,
            "output_variable": "failure_analysis_output",
            "input_source": "none",
            "use_comorag": False,
            "use_async": False,
            "write_text": False,
        }
        node = {
            "id": "chat_failure_analyzer",
            "type": "llm",
            "data": {"llm_configuration": llm_config},
            "inputs": [],
        }
        try:
            llm_executor.execute_llm_node(node, None, None)
            resp = llm_executor.get_variable("failure_analysis_output")
            if self._log_llm_interaction:
                self._log_llm_interaction("failure_analysis", llm_config, resp)
            if not resp:
                return None
            raw = str(resp).strip()
            try:
                return json.loads(raw)
            except Exception:
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    return json.loads(raw[start:end + 1])
        except Exception:
            return None
        return None
