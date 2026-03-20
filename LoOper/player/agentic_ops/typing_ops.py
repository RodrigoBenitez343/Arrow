try:
    import pyautogui
except Exception:
    pyautogui = None


class TypingAssistant:
    def __init__(self, get_llm_executor, on_minimize=None, on_restore=None, get_model=None):
        self._get_llm_executor = get_llm_executor
        self._on_minimize = on_minimize
        self._on_restore = on_restore
        self._get_model = get_model

    def generate_text_content(self, user_request, step_description):
        try:
            from player.multi_sequence.llm_executor import LLMExecutor, get_default_api_url
        except Exception:
            return step_description
        llm_executor = self._get_llm_executor(LLMExecutor)
        prompt = (
            f"User Request: {user_request}\n"
            f"Current Action Step: {step_description}\n\n"
            "Based on the user request and the current action step, determine the exact text that needs to be typed.\n"
            "If the step implies searching for something, output the search term.\n"
            "If the step implies writing a message, output the full message content.\n"
            "Output ONLY the text to be typed. Do not include any explanation or quotes unless they are part of the text."
        )
        model_name = None
        if self._get_model:
            model_name = self._get_model()
        if not model_name:
            model_name = "cogito:latest"
        llm_config = {
            "model": model_name,
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
            llm_executor.execute_llm_node(node, None, None)
            resp = llm_executor.get_variable("typing_content")
            return str(resp).strip() if resp else ""
        except Exception:
            return ""

    def perform_typing(self, text):
        if not text or pyautogui is None:
            return False
        if self._on_minimize:
            try:
                self._on_minimize()
            except Exception:
                pass
        try:
            pyautogui.write(text, interval=0.05)
            return True
        except Exception:
            return False
        finally:
            if self._on_restore:
                try:
                    self._on_restore()
                except Exception:
                    pass
