"""reasoning.py — the builder's reasoning LLM turn.

Given the goal and the canvas, ask the configured model for the NEXT step in
one sentence.  This is what gives the builder actual reasoning: the sentence
is traced and handed to Laya as part of its premise, so the pick is informed
by a plan instead of reading the raw goal alone.

Fail-open by design: no gateway / no model / timeout -> ``None`` and the build
continues on Laya + needle (a planner aid is never a blocker).
"""

import json
import logging

logger = logging.getLogger(__name__)

#: No timeout on the builder's model calls.  A build turn may legitimately
#: wait as long as the work takes — a busy gateway (e.g. a chain test holding
#: the local engine) or a long local generation must never be cut off
#: (measured 2026-10-11: turns died on "Read timed out"; the user asked for
#: truly unlimited).  ``None`` = wait indefinitely; only Stop ends a turn.
TIMEOUT = None

SYSTEM = (
    "You are the planner of an automation-chain builder. In ONE short "
    "sentence, say what the next single step should be. Do not list several "
    "steps, do not explain the plan."
)

PLAN_SYSTEM = (
    "You are the planner of an automation bot. Break the goal into 3 to 6 "
    "TOOLS the bot needs, ONE per line, numbered. A tool is ONE reusable "
    "capability: a browser or desktop routine (navigate, search, click, "
    "fill, scrape, read) OR one processing step an LLM can do (extract, "
    "merge, summarize, classify). Do not describe screen micro-steps, do "
    "not list asking the user for input, do not explain, do not write code."
)

#: The instruction an `llm` taught tool's node carries — the builder AUTHORS
#: it (prose; the model never writes chain JSON).
PROMPT_SYSTEM = (
    "You write the instruction for one automation step done by an LLM. "
    "Write ONE short paragraph telling the LLM exactly what to do with the "
    "data it receives, including the exact output format to return. No "
    "preamble, no explanations, no markdown."
)

#: The act loop's contract: the provider drives a REAL browser, ONE action
#: per turn, as a single JSON object (parsed leniently by ``_parse_action``).
ACT_SYSTEM = (
    "You drive a REAL Chrome browser to perform ONE routine for an "
    "automation bot. Reply with EXACTLY ONE JSON object per turn — no "
    "prose, no markdown. Schema: {\"action\": \"navigate|click|type|"
    "scroll|press|wait|done|stuck\", \"target\": \"visible element text "
    "or CSS selector (click, type)\", \"value\": \"text to type, or "
    "pixels for scroll\", \"url\": \"http(s) URL (navigate)\", \"key\": "
    "\"Enter|Escape|Tab|PageDown|ArrowDown (press)\", \"seconds\": 2 "
    "(wait), \"reason\": \"one short line\"}. Targets use the visible "
    "text exactly as listed in ELEMENTS. Use done the moment the routine's "
    "goal is achieved; use stuck with the reason when you cannot continue."
)

#: The act loop's action allowlist (mirrors ACT_SYSTEM).
_ACT_ACTIONS = ('navigate', 'click', 'type', 'scroll', 'press', 'wait',
                'done', 'stuck')

#: The contract for a code step's Python: authored ONCE (the coder runs
#: deterministically afterwards), stdlib only, `data` in / `result` out.
CODE_SYSTEM = (
    "You write ONE self-contained Python script for one automation step. "
    "The incoming value arrives in the variable `data` (a string); assign "
    "the final answer to `result` (a string). Use only the standard "
    "library (json, re, csv, math, datetime, collections...). No file or "
    "network access. Output ONLY the Python code — no markdown fences, no "
    "explanations, no prints."
)


def _resolve_settings(settings):
    """The caller's settings dict, else the builder's own settings."""
    if isinstance(settings, dict):
        return settings
    try:
        from player.builder import settings as _bs
        loaded = _bs.load()
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _planner_system(base):
    """The system prompt plus the node runbook cheat sheet (fail-open).

    The builder's model must know the real Arrow building blocks — node
    types, what each mode changes, and the wiring rules — or its tool list
    is generic automation words the composer cannot map.  Bounded text; a
    missing runbook never blocks the turn.
    """
    try:
        from player.builder.runbook import cheat_sheet
        extra = cheat_sheet()
    except Exception:
        extra = ''
    return (base + '\n\n' + extra) if extra else base


def gateway_url():
    """The local AI gateway base URL, or '' (never raises)."""
    try:
        from AI.config_loader import get_api_url
        return str(get_api_url()).rstrip('/')
    except Exception:
        return ''


def list_models():
    """Model names the gateway serves (never raises; [] when unavailable)."""
    url = gateway_url()
    if not url:
        return []
    try:
        import requests
        resp = requests.get(url + '/models', timeout=TIMEOUT)
        data = resp.json()
        items = data.get('models') if isinstance(data, dict) else data
        names = []
        for item in items or []:
            name = item.get('name') if isinstance(item, dict) else str(item)
            if name:
                names.append(str(name))
        return names
    except Exception:
        logger.debug('builder model list failed', exc_info=True)
        return []


def _default_model(engine):
    """The app's configured default LLM for the engine (or '')."""
    try:
        from player.multi_sequence.llm_executor_resources.config_utils import (
            get_default_llm_model)
        return str(get_default_llm_model(engine or 'llamacpp') or '').strip()
    except Exception:
        return ''


def _role_provider():
    """The BYOK chat-role provider id, or '' (never raises)."""
    try:
        from AI import byok
        role = byok.get_role(byok.load_settings(), 'chat')
        return str(role.get('provider') or '').strip()
    except Exception:
        return ''


def _generate(prompt, settings, system):
    """One gateway generation with engine/provider routing. '' on failure."""
    url = gateway_url()
    if not url:
        return ''
    model = str(settings.get('model') or '').strip()
    if not model:
        model = _default_model(settings.get('engine'))
    if not model:
        models = list_models()
        model = models[0] if models else ''
    if not model:
        return ''
    try:
        import requests
        payload = {'model': model, 'prompt': prompt, 'system': system,
                   'stream': False}
        # Engine routing: the gateway dispatches explicit provider ids
        # (llamacpp = the built-in engine, manifest ids = BYOK providers),
        # so the selected engine routes even when the model has no prefix.
        engine = str(settings.get('engine') or '').strip().lower()
        if engine:
            try:
                from AI import byok
                if byok.is_resolvable_provider_id(engine):
                    payload['provider'] = engine
            except Exception:
                pass
        # The chat role can name a provider whose model the panel does not
        # carry; let the gateway route by role in that case (a raw model
        # name has no prefix to dispatch on).
        if 'provider' not in payload:
            provider = _role_provider()
            if provider and ':' not in model and not settings.get('model'):
                payload['provider'] = provider
        # A bare GGUF path is the LOCAL engine's model: route it explicitly
        # so the gateway never falls to the legacy Ollama default.
        if 'provider' not in payload and \
                str(model).lower().endswith('.gguf'):
            payload['provider'] = 'llamacpp'
        resp = requests.post(
            url + '/generate',
            json=payload,
            timeout=TIMEOUT,
        )
        data = resp.json() if resp.status_code == 200 else {}
        text = ''
        if isinstance(data, dict):
            text = str(data.get('response') or data.get('text') or '')
        return text.strip()
    except Exception:
        logger.debug('builder reasoning call failed', exc_info=True)
        return ''


def think(goal, canvas, transcript, settings=None):
    """One reasoning sentence for the next build step, or None (fail-open)."""
    settings = _resolve_settings(settings)
    if not settings.get('reasoning', True):
        return None
    prompt = (
        "Goal: %s\nCanvas:\n%s\nSteps so far:\n%s\n\nNext step:"
        % (str(goal or '')[:400], str(canvas or '(empty)')[:800],
           '\n'.join(transcript) if transcript else '(none yet)')
    )
    text = _generate(prompt, settings, _planner_system(SYSTEM))
    return text.splitlines()[0][:300] if text else None


def _clean_plan_line(line):
    """'1. Add an input node' -> 'Add an input node' (or '' to drop)."""
    text = str(line or '').strip()
    # Strip leading numbering / bullets: "1.", "1)", "- ".
    while text and (text[0].isdigit() or text[0] in '-*•'):
        text = text[1:].lstrip()
    # "Step 2: add ..." form.
    low = text.lower()
    if low.startswith('step'):
        head, sep, rest = text.partition(':')
        if sep and head[4:].strip().rstrip('.)').isdigit():
            text = rest.strip()
    text = text.lstrip('.):-').strip()
    if text.lower() in ('', 'none', 'n/a'):
        return ''
    return text[:160]


def plan(goal, canvas, settings=None):
    """A 3-6 TOOL plan (list of short strings), or [] (fail-open).

    Generated BEFORE any node is created, so the build follows an explicit
    plan instead of acting step-blind: the tool lines ride in every Laya
    premise and gate the done-probe (an empty canvas is never "complete").
    """
    settings = _resolve_settings(settings)
    if not settings.get('reasoning', True):
        return []
    prompt = (
        "Goal: %s\nCanvas:\n%s\n\nNumbered tool list (one tool per line):"
        % (str(goal or '')[:400], str(canvas or '(empty)')[:800])
    )
    text = _generate(prompt, settings, _planner_system(PLAN_SYSTEM))
    steps = []
    for line in text.splitlines():
        cleaned = _clean_plan_line(line)
        if cleaned and cleaned not in steps:
            steps.append(cleaned)
        if len(steps) >= 6:
            break
    return steps


def step_prompt(task, goal='', settings=None):
    """An authored instruction for an LLM tool step, or '' (fail-open).

    One generation turn: the builder writes the node's prompt as PROSE —
    Python puts it into the chain file (no model ever writes JSON).  An
    empty result is the caller's cue to fall back to the tool line itself.
    """
    settings = _resolve_settings(settings)
    if not settings.get('reasoning', True):
        return ''
    goal_text = str(goal or '').strip()
    prompt = "Tool the bot needs: %s\n" % str(task or '')[:300]
    if goal_text:
        prompt += "Bot goal: %s\n" % goal_text[:300]
    prompt += "\nThe instruction:"
    text = _generate(prompt, settings, PROMPT_SYSTEM)
    if not text:
        return ''
    text = ' '.join(text.split())
    # The provider can open a drafted instruction with a stray "continue"
    # token (a drafting quirk seen live 2026-10-09, saved into a built
    # step's prompt); it is never part of the instruction.
    if text.lower().startswith('continue') and len(text) > 40:
        rest = text[len('continue'):].lstrip(' .,:;-').strip()
        if rest:
            text = rest
    return text[:2000]


def _strip_code_fences(text):
    """Model code reply -> bare Python (fences and stray prose removed)."""
    raw = str(text or '').strip()
    if not raw:
        return ''
    fence = raw.find('```')
    if fence >= 0:
        rest = raw[fence + 3:]
        end = rest.find('```')
        if end >= 0:
            rest = rest[:end]
        # Drop a language tag on the fence line (```python).
        first_nl = rest.find('\n')
        if 0 <= first_nl <= 12:
            head = rest[:first_nl].strip().lower()
            if head in ('', 'python', 'py', 'python3'):
                rest = rest[first_nl + 1:]
        raw = rest.strip()
    # Drop leading prose lines before the first code-looking line.
    lines = raw.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (stripped.startswith(('import ', 'from ', 'def ', 'class ',
                                 '#', '"""', "'''", 'result'))
                or '=' in stripped):
            if i:
                lines = lines[i:]
            break
    return '\n'.join(lines).strip()


def code_text(task, goal='', settings=None, previous='', error=''):
    """The Python for a code step, or '' (fail-open).

    One generation turn (plus an optional repair turn when ``previous`` +
    ``error`` are given): the builder WRITES the code once — the code node
    then runs it deterministically, no model in the loop at run time.
    """
    settings = _resolve_settings(settings)
    if not settings.get('reasoning', True):
        return ''
    prompt = "Step to implement: %s\n" % str(task or '')[:400]
    goal_text = str(goal or '').strip()
    if goal_text:
        prompt += "Bot goal: %s\n" % goal_text[:300]
    if previous:
        prompt += ("\nYour previous code:\n%s\n\nIt failed with: %s\n"
                   "Fix it and output the corrected code:\n"
                   % (str(previous)[:3000], str(error)[:300]))
    else:
        prompt += "\nThe Python code:"
    text = _generate(prompt, settings, CODE_SYSTEM)
    return _strip_code_fences(text)[:8000]


def _parse_action(text):
    """The first valid action object in a model reply, or None.

    Lenient on purpose: the provider may wrap the JSON in prose or code
    fences (the call is validated against the allowlist, so junk can never
    become an action).
    """
    raw = str(text or '').strip()
    if not raw:
        return None
    start = raw.find('{')
    end = raw.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start:end + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    action = str(obj.get('action') or '').strip().lower()
    if action not in _ACT_ACTIONS:
        return None
    out = {'action': action}
    for key in ('target', 'value', 'url', 'key', 'reason'):
        value = obj.get(key)
        if value is not None and str(value).strip():
            out[key] = str(value)
    if isinstance(obj.get('modifiers'), list):
        out['modifiers'] = [str(m) for m in obj['modifiers']]
    try:
        out['seconds'] = float(obj.get('seconds'))
    except Exception:
        out['seconds'] = 2.0
    return out


def act_web(goal, step, digest, history, settings=None):
    """ONE browser action for the act loop, or None (fail-open).

    ``digest`` is the current page state (URL/title/elements/text) and
    ``history`` the actions already performed — both produced by
    ``web_act``.  The reply is parsed leniently; None means the model did
    not answer (the act loop then freezes what worked, or reports).
    """
    settings = _resolve_settings(settings)
    if not settings.get('reasoning', True):
        return None
    done = '\n'.join(str(h) for h in (history or [])[-8:]) or '(nothing yet)'
    prompt = (
        "Bot goal: %s\nRoutine to perform: %s\nActions so far:\n%s\n\n"
        "Current page:\n%s\n\nNext action (one JSON object):"
        % (str(goal or '')[:300], str(step or '')[:300], done,
           str(digest or '')[:3000])
    )
    text = _generate(prompt, settings, ACT_SYSTEM)
    return _parse_action(text)
