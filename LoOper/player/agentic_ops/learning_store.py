import json
import os
import re
import time


class LearningStore:
    def __init__(self, base_dir, log_event):
        self._base_dir = base_dir
        self._log_event = log_event

    def load(self):
        path = self._store_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
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
        signature = "|".join([entry.get("issue", ""), entry.get("fix", "")]).strip().lower()
        existing = self.load()
        seen = set()
        for item in existing:
            sig = "|".join([str(item.get("issue", "")), str(item.get("fix", ""))]).strip().lower()
            seen.add(sig)
        if signature and signature in seen:
            return
        existing.append(entry)
        path = self._store_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
            if self._log_event:
                self._log_event("learning_recorded", {"path": path, "entry": entry})
        except Exception:
            pass

    def relevant(self, text, limit=5):
        learnings = self.load()
        if not text:
            return learnings[-limit:]
        tokens = re.findall(r"[a-zA-Z0-9]+", str(text).lower())
        scored = []
        for item in learnings:
            hay = " ".join([
                str(item.get("issue", "")),
                str(item.get("cause", "")),
                str(item.get("fix", "")),
                " ".join([str(t) for t in (item.get("tags") or [])])
            ]).lower()
            score = sum(1 for t in tokens if t in hay)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            return [item for _, item in scored[:limit]]
        return learnings[-limit:]

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
