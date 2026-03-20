import json
import os
import threading
from datetime import datetime


class ChatLogger:
    def __init__(self, base_dir):
        self._base_dir = base_dir
        self._chat_log_path = None
        self._lock = threading.Lock()

    def start_log(self, user_text):
        path = self._build_chat_log_path()
        self._chat_log_path = path
        self.log_event("session_start", {"query": user_text, "path": path})
        return path

    def end_log(self, summary=None):
        self.log_event("session_end", summary or {})
        self._chat_log_path = None

    def log_event(self, event, payload=None):
        if not self._chat_log_path:
            return
        data = payload if payload is not None else {}
        record = {"event": event, "time": datetime.now().isoformat(), "data": data}
        line = json.dumps(record, ensure_ascii=False)
        try:
            with self._lock:
                with open(self._chat_log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    def _build_chat_log_path(self):
        log_dir = os.path.join(self._base_dir, "data", "agent_logs")
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return os.path.join(log_dir, f"{timestamp}_chat.log")
