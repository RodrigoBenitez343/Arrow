import re


def is_status_query(text):
    if not text:
        return False
    t = str(text).lower()
    return bool(re.search(r"\b(is|are|check|verify|confirm)\b.+\b(open|running|active|launched|visible|already open)\b", t))


def extract_status_subject(text):
    if not text:
        return ""
    t = str(text)
    m = re.search(r"\b(?:is|are|check|verify|confirm)\b\s+(.*?)\s+\b(open|running|active|launched|visible|already open)\b", t, re.IGNORECASE)
    if not m:
        return ""
    return m.group(1).strip().strip("?").strip()


def is_status_step(text):
    if not text:
        return False
    t = str(text).lower()
    if any(k in t for k in ["reason/think", "wait for result", "wait", "typing", "write/type text"]):
        return False
    return bool(re.search(r"\b(open|running|active|launched|visible|already open)\b", t))


def normalize_status_plan(plan, user_text):
    if not isinstance(plan, list) or not plan:
        return [user_text]
    status_steps = [s for s in plan if is_status_step(s)]
    if status_steps:
        return [status_steps[0]]
    return [user_text]


def pick_status_tool(text, tools):
    subject = extract_status_subject(text).lower()
    best = None
    best_score = 0
    for tool in tools:
        desc = " ".join([
            str(tool.get("name") or ""),
            str(tool.get("description") or ""),
            str(tool.get("tool_desc") or "")
        ]).lower()
        score = 0
        if any(k in desc for k in ["check", "status", "verify", "confirm", "is open", "already open"]):
            score += 3
        if subject and subject in desc:
            score += 2
        if "open" in desc and "check" not in desc and "status" not in desc:
            score -= 1
        if score > best_score:
            best_score = score
            best = tool.get("id")
    return best


def parse_open_status(text):
    if not text:
        return None
    t = str(text)
    if re.search(r"Conditional Result:\s*True", t, re.IGNORECASE):
        return True
    if re.search(r"Conditional Result:\s*False", t, re.IGNORECASE):
        return False
    if re.search(r"Code Result:\s*True", t, re.IGNORECASE):
        return True
    if re.search(r"Code Result:\s*False", t, re.IGNORECASE):
        return False
    return None
