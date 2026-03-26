import json
import os
import time


class LearningStore:
    def __init__(self, base_dir, log_event):
        self._base_dir = base_dir
        self._log_event = log_event
        self._honcho = None
        self._honcho_session = None
        self._honcho_user_peer = None
        self._honcho_assistant_peer = None
        self._honcho_session_id = os.getenv("LOOPER_HONCHO_SESSION_ID", "looper-agent-session")
        self._honcho_user_peer_id = os.getenv("LOOPER_HONCHO_USER_PEER_ID", "user")
        self._honcho_assistant_peer_id = os.getenv("LOOPER_HONCHO_ASSISTANT_PEER_ID", "looper_agent")
        self._honcho_workspace_id = os.getenv("HONCHO_WORKSPACE_ID", "looper")
        self._honcho_base_url = os.getenv("LOOPER_HONCHO_URL") or os.getenv("HONCHO_URL") or "http://localhost:8000"
        self._honcho_api_key = os.getenv("HONCHO_API_KEY")
        self._initialize_honcho()

    def load(self):
        return []

    def record(self, learning):
        if not learning:
            return
        entry = {
            "issue": str(learning.get("issue", "")).strip(),
            "cause": str(learning.get("cause", "")).strip(),
            "fix": str(learning.get("fix", "")).strip(),
            "bad_example": str(learning.get("bad_example", "")).strip(),
            "good_example": str(learning.get("good_example", "")).strip(),
            "tags": learning.get("tags", []),
            "timestamp": time.time()
        }
        if not self._honcho_session or not self._honcho_assistant_peer:
            return
        try:
            content = json.dumps(entry, ensure_ascii=False)
            msg = self._honcho_assistant_peer.message(
                content,
                metadata={
                    "looper_type": "learning",
                    "tags": entry.get("tags") or [],
                },
            )
            self._honcho_session.add_messages(msg)
            if self._log_event:
                self._log_event(
                    "learning_recorded",
                    {
                        "backend": "honcho",
                        "workspace_id": self._honcho_workspace_id,
                        "session_id": self._honcho_session_id,
                        "entry": entry,
                    },
                )
        except Exception:
            return

    def relevant(self, text, limit=5):
        if not self._honcho_session:
            return []
        query = str(text or "").strip()
        if not query:
            query = "learning"
        try:
            messages = self._honcho_session.search(
                query=query,
                filters={"metadata": {"looper_type": "learning"}},
                limit=int(limit or 5),
            )
        except Exception:
            return []
        results = []
        for msg in messages or []:
            try:
                raw = getattr(msg, "content", "") or ""
                item = json.loads(raw) if raw.strip().startswith("{") else {"issue": raw}
                if isinstance(item, dict):
                    results.append(item)
            except Exception:
                continue
        return results[: int(limit or 5)]

    def format_for_prompt(self, learnings):
        if not learnings:
            return "None"
        lines = []
        for idx, item in enumerate(learnings, start=1):
            issue = item.get("issue", "")
            fix = item.get("fix", "")
            bad_example = item.get("bad_example", "")
            good_example = item.get("good_example", "")
            lines.append(f"{idx}. Issue: {issue} | Fix: {fix} | Avoid: {bad_example} | Prefer: {good_example}")
        return "\n".join(lines)

    def _store_path(self):
        return os.path.join(self._base_dir, "AI", "agent_learnings.json")

    def _initialize_honcho(self):
        try:
            repo_root = os.path.dirname(self._base_dir.rstrip("\\/"))
            sdk_path = os.path.join(repo_root, "utils", "honcho", "sdks", "python", "src")
            if os.path.isdir(sdk_path):
                import sys
                if sdk_path not in sys.path:
                    sys.path.append(sdk_path)
            from honcho import Honcho

            self._honcho = Honcho(
                api_key=self._honcho_api_key,
                base_url=self._honcho_base_url,
                workspace_id=self._honcho_workspace_id,
            )
            self._honcho_session = self._honcho.session(self._honcho_session_id)
            self._honcho_user_peer = self._honcho.peer(self._honcho_user_peer_id)
            self._honcho_assistant_peer = self._honcho.peer(self._honcho_assistant_peer_id)
        except Exception:
            self._honcho = None
            self._honcho_session = None
            self._honcho_user_peer = None
            self._honcho_assistant_peer = None
