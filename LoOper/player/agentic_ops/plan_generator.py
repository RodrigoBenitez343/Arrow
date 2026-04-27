import re


class PlanGenerator:
    def __init__(self, get_llm_executor, log_llm_interaction, get_model=None):
        self._get_llm_executor = get_llm_executor
        self._log_llm_interaction = log_llm_interaction
        self._get_model = get_model

    def generate_plan(self, text, tools, known_learnings):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return [text]
        llm_executor = self._get_llm_executor(LLMExecutor)
        tool_descriptions = "\n".join(
            [
                f"- {t.get('id')}: {t.get('tool_desc') or t.get('description') or t.get('name') or ''}"
                for t in (tools or [])
                if isinstance(t, dict)
            ]
        )
        prompt = (
            "You are an expert planner for an automation agent.\n"
            "Your goal is to create a logical sequence of steps to fulfill the user's request.\n"
            "Use the provided tools for actions like opening apps, clicking buttons, or navigating.\n"
            "- If a tool description says it 'clicks' or 'focuses' something, assume it does so.\n"
            "- Use 'Write/Type Text' when you need to enter text (e.g., search queries, messages) *after* an input field is focused.\n"
            "- Use 'Reason/Think' if you need a step to analyze the current state or decide on the next move.\n"
            "- If the user asks whether something is already open or running, plan a status-check step first and do not open anything until after the check result.\n"
            "- Ground your plan in known learnings. If a prior mistake shows a missing precondition (like app open or window focus), add the missing precondition step.\n"
            "- Do not combine multiple distinct actions (like opening an app AND typing) into one step unless a single tool handles both.\n"
            "- Each step MUST start with USE_TOOL:<id> matching one of the Available Tools.\n"
            "- Format each step exactly like: '1. USE_TOOL:<id> | <short instruction>'.\n\n"
            f"User Request: {text}\n\n"
            f"Known Learnings:\n{known_learnings}\n\n"
            f"Available Tools:\n{tool_descriptions}\n\n"
            "Output ONLY a numbered list of steps. Do not include any other text."
        )
        
        # Get the Qwen model path
        model_path = None
        if self._get_model:
            model_path = self._get_model()
        
        # Configure for llama.cpp with Qwen model
        llm_config = {
            "model": "Qwen3.6-35B-A3B",
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
            # llama.cpp specific configuration
            "use_llamacpp": True,
            "llamacpp_model_path": model_path if model_path else "",
            "llamacpp_gpu_layers": 35,
            "llamacpp_threads": 8,
            "llamacpp_context_size": 4096,
        }
        node = {
            "id": "chat_planner_node",
            "type": "llm",
            "data": {"llm_configuration": llm_config},
            "inputs": [],
        }
        try:
            llm_executor.execute_llm_node(node, None, None)
            resp = llm_executor.get_variable("plan_output")
            if self._log_llm_interaction:
                self._log_llm_interaction("plan_generation", llm_config, resp)
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
        except Exception:
            return [text]
