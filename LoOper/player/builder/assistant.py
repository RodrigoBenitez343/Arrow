"""assistant.py — the builder chat as a building ASSISTANT.

Aligned with the user's working method: the chain is built (and audited)
MANUALLY in the canvas; the assistant helps where building is hard.  It
answers questions grounded in the live chain and the user's SELECTION, and
it acts on nodes only when asked — writing prompts, choosing models,
filling settings, wiring, and building SKILL ladders.

A skill ("thought ladder") is the decomposed working form of one process:
``input -> llm/code/conditional steps -> output`` with a shared context
pool that the llm steps write to and the last step reads back — the
``basic skill example`` shape, generalized.  Skills run on LOCAL models by
default; the orchestrator imports a saved skill as a tool.

Actor split: the configured provider model (the builder settings engine)
writes the reply and the action JSON; Python executes the actions through
``execute_builder_call`` — the only canvas writer.  One bounded turn per
user message: no composer loop, no per-step Laya/needle routing.
"""

import json
import logging

logger = logging.getLogger(__name__)

#: Hard caps — one turn is a handful of actions, never a marathon.
MAX_ACTIONS = 8
MAX_HISTORY = 6
PROMPT_CANVAS_CHARS = 3000
PROMPT_SELECTION_CHARS = 2500

#: The assistant ITERATES: edit -> test -> read -> edit again, until the
#: chain passes its own test.  There is NO tight cap: the loop is bounded
#: only by the model stopping, a stall (no change for MAX_IDLE_ROUNDS), or
#: this very generous safety ceiling (a runaway model must still end).
MAX_ROUNDS = 40
#: Consecutive rounds that change NOTHING (only reads/tests) before the turn
#: stops and asks the user — a model that only looks must not spin.
MAX_IDLE_ROUNDS = 6
#: Action results carried into the next round's prompt (bounded).
MAX_RESULTS = 12


#: Action names the assistant may execute (the whole surface).
ACTION_NAMES = ('edit_node', 'configure_node', 'create_node', 'wire',
                'set_description', 'save_chain', 'read_node', 'build_skill',
                'test_chain')

#: Actions that CHANGE the chain.  A round carrying one (and not testing
#: itself) is auto-verified: the builder must always see whether the thing
#: it just built runs, so the loop can iterate on the result.
MUTATING_ACTIONS = ('create_node', 'edit_node', 'wire', 'delete_node',
                    'build_skill', 'configure_node', 'set_description')


def _verdict(report):
    """True / False / None for a ``test_chain`` report (None = no verdict)."""
    text = str(report or '')
    if 'test: completed' in text:
        return False if 'NOT met' in text else True
    if ('test: FAILED' in text or 'test: STALLED' in text
            or 'test: STOPPED' in text):
        return False
    return None


def _clip(text, limit):
    """A length-capped rendering (never raises)."""
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '…'

_ROLE = (
    "You are the LoOper/Arrow building assistant, docked beside the chain "
    "canvas of a general computer-use automation app. The user builds the "
    "chain manually; you help when asked. You can:\n"
    "- answer questions about the chain, the SELECTED nodes, and how each "
    "node works in a workflow — the canvas and the node runbook are below; "
    "use read_node for any node's full details;\n"
    "- write or improve prompts, choose models/engines and fill node "
    "settings (edit_node accepts only the properties listed as editable);\n"
    "- build SKILL ladders (build_skill): a described process decomposed "
    "into input -> llm/code/conditional steps -> output with a shared "
    "context pool (llm steps write their turns, the last llm step reads "
    "them back). The orchestrator imports a saved skill as a tool; skills "
    "run on LOCAL models by default.\n"
    "Rules: act ONLY on what the user asks — never restructure the chain "
    "on your own. Prefer small, precise edits. Keep replies short and "
    "concrete; when the request is ambiguous, ask ONE question in your "
    "reply and emit no actions."
)

_PROTOCOL = (
    "Reply with ONE JSON object and nothing else:\n"
    '{"reply": "<text shown to the user>", "actions": [{"name": "...", '
    '"arguments": {...}}]}\n'
    '"actions" may be omitted or empty when you are only answering.\n'
    "You work in ROUNDS: the results of your actions are executed and shown "
    "back to you before your next reply, so you can edit, TEST, read what "
    "the test produced, and keep fixing until the request is satisfied — "
    "then reply with NO actions to stop.\n"
    "Available actions:\n"
    '- edit_node {"node": "<label or id>", "property": "<name>", "value": '
    "<value>}\n"
    '- configure_node {"node": "<label or id>", "ask": "<what this node "'
    'must do, e.g. read the page text / run on the local model>"} — let '
    'Laya map plain words onto the node\'s exact field (and, for a fixed '
    'set, its exact option). Prefer it over guessing a property/value; when '
    'it returns a field to set yourself, follow up with edit_node.\n'
    '- create_node {"node_type": "<type>", "label": "<name>"} — a code '
    'node starts with NAMED IO ports input_vars=[data] / output_vars=[result]; '
    'its data ALWAYS travels on those named ports (rename/extend them with '
    'edit_node), never on the base input/output exec edges.\n'
    '- wire {"from_node": "<label or id>", "to_node": "<label or id>", '
    '"input_port": "<optional>", "output_port": "<optional>"} — an '
    'Input -> LLM wire ALSO carries the value on the input\'s `data` port '
    'into the LLM\'s `prompt` port (a wired prompt REPLACES the template, '
    'so a step\'s instruction belongs in system_message).\n'
    '- set_description {"text": "<what this chain does, 1-2 sentences>"}\n'
    '- save_chain {"name": "<file name>"} — saves the WHOLE canvas as a '
    "System chain; only when the user asks to save.\n"
    '- read_node {"node": "<label or id>"}\n'
    '- test_chain {"input": "<optional value, or a {label: value} '
    'object for several Input nodes>"} — RUN the chain being edited '
    '(unsaved edits included) and get back a report: a STATUS line '
    '(completed / FAILED / STALLED), a "reason:" line naming the failing '
    'node and WHY, each Output node\'s text, and a "goal (…): MET / NOT '
    'met/not verified" verdict against the chain\'s goal. A test ALSO '
    'runs AUTOMATICALLY right after you wire or build the chain (find it '
    'in RESULTS) — always read it. Use test_chain yourself after EVERY '
    'edit. If the status is FAILED or STALLED, read the "reason:" '
    'line, fix THAT node, and test again; never stop while the test FAILS '
    'or the goal is NOT met / not verified — keep fixing and re-testing, or '
    'ask the user ONE question. Only report success when the status is '
    '"completed" (and the goal is MET).\n'
    '- build_skill {"name": "<short name>", "description": "<what it '
    'does>", "input_label": "<optional>", "output_label": "<optional>", '
    '"steps": [{"kind": "llm", "label": "<step name>", "prompt": "<full '
    'instruction>"} | {"kind": "code", "label": "...", "inputs": '
    '["data"], "outputs": ["result"], "code": "<python: reads the named '
    'input var, sets the named output var>"} | {"kind": "conditional", '
    '"label": "...", "criterion": "<what it validates — an LLM decides>"} '
    '(or "code": "<python: result = True/False>")}], "vision": true on an '
    "llm step that must read the screen}\n"
    "build_skill code steps use NAMED IO ports (the code node's IO "
    "editor): name each input/output variable; the previous step wires "
    "into the first named input and the first named output feeds the next "
    "step (defaults: data in, result out).\n"
    "build_skill rules: every llm step MUST carry a full prompt; code "
    "steps use stdlib-only Python; conditional steps validate by default "
    "with an llm criterion. One skill = one decomposed process."
)


def _system_prompt():
    """Role + action protocol + the node runbook cheat sheet (fail-open)."""
    try:
        from player.builder.runbook import cheat_sheet
        sheet = cheat_sheet()
    except Exception:
        sheet = ''
    return '\n\n'.join(part for part in (_ROLE, _PROTOCOL, sheet) if part)


def _format_turn(query, canvas, selection, history, results=None):
    """The per-turn user prompt: chat tail, canvas, selection, results."""
    parts = []
    hist = []
    for item in (history or [])[-MAX_HISTORY:]:
        if not isinstance(item, dict):
            continue
        text = ' '.join(str(item.get('text') or '').split())[:400]
        if text:
            hist.append('%s: %s' % (str(item.get('role') or 'user'), text))
    if hist:
        parts.append('RECENT CHAT:\n' + '\n'.join(hist))
    parts.append('CANVAS:\n' + str(canvas or '')[:PROMPT_CANVAS_CHARS])
    selection = str(selection or '').strip()
    parts.append('SELECTED NODES (the user\'s focus):\n'
                 + (selection[:PROMPT_SELECTION_CHARS] if selection
                    else '(none selected)'))
    if results:
        parts.append('RESULTS OF YOUR ACTIONS SO FAR:\n'
                     + '\n'.join(str(r) for r in results[-MAX_RESULTS:]))
    parts.append('USER: ' + str(query or ''))
    return '\n\n'.join(parts)


def _balanced_end(text, start):
    """The index of the '{' at ``start``'s matching '}' (strings aware)."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_reply(text):
    """(reply, actions, parsed) from a model reply; fail-open to prose.

    The model may wrap the JSON in fences or prose — every '{' is tried
    with a balanced-brace scan until one parses into a dict carrying
    "reply"/"actions".  ``parsed`` is False when no such envelope was
    found (the reply is plain prose).  Parsing is lenient (``strict=False``)
    so raw control characters a model leaves inside a string — e.g. real
    newlines in an action's embedded Python ``code`` — do NOT discard an
    otherwise-valid action set (default strict JSON rejects them and the
    builder would run zero actions, silently).
    """
    raw = str(text or '').strip()
    if not raw:
        return '', [], False
    obj = None
    try:
        candidate = json.loads(raw, strict=False)
        if isinstance(candidate, dict):
            obj = candidate
    except Exception:
        obj = None
    if obj is None:
        start = raw.find('{')
        while start >= 0:
            end = _balanced_end(raw, start)
            if end > start:
                try:
                    candidate = json.loads(raw[start:end + 1], strict=False)
                except Exception:
                    candidate = None
                if isinstance(candidate, dict) and (
                        'reply' in candidate or 'actions' in candidate):
                    obj = candidate
                    break
            start = raw.find('{', start + 1)
    if not isinstance(obj, dict) or not ('reply' in obj or 'actions' in obj):
        return raw, [], False
    reply = str(obj.get('reply') or '').strip()
    actions = [a for a in (obj.get('actions') or []) if isinstance(a, dict)]
    return reply, actions, True


class BuilderAssistant:
    """The panel's brain: one grounded, bounded assistant turn."""

    def __init__(self, ctx, settings=None):
        self._ctx = ctx
        self._settings = settings

    def _load_settings(self):
        if isinstance(self._settings, dict):
            return self._settings
        try:
            from player.builder import settings as _bs
            loaded = _bs.load()
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def route(self, query, progress_cb=None, trace=None):
        """One assistant turn -> {'ok', 'text', 'steps', 'error'}.

        A turn runs ROUNDS: the model acts, the results (including
        ``test_chain`` output) are fed back, and it keeps going until it
        replies with no actions (satisfied) or MAX_ROUNDS.
        """
        from player.builder import reasoning
        from player.builder.devtools import execute_builder_call
        from player.builder.trace import emit as _emit

        def _note(event):
            if callable(progress_cb):
                try:
                    progress_cb(event)
                except Exception:
                    pass

        def _step(message):
            _emit(trace, message)

        query = str(query or '').strip()
        _step('you: %s' % (query[:200] or '(empty)'))
        if not reasoning.gateway_url():
            return {'ok': False, 'steps': 0, 'error': 'no gateway',
                    'text': ('(the local AI gateway is not reachable — '
                             'check the AI settings)')}
        settings = self._load_settings()
        if not settings.get('reasoning', True):
            return {'ok': False, 'steps': 0, 'error': 'reasoning disabled',
                    'text': ('(the builder model is switched off — enable '
                             '"Reasoning LLM" in the builder settings gear '
                             'to chat)')}

        selection = ''
        sel_fn = self._ctx.get('selection_text')
        if callable(sel_fn):
            try:
                selection = str(sel_fn() or '')
            except Exception:
                selection = ''
        if selection:
            first = next((ln.strip() for ln in selection.splitlines()
                          if ln.strip()), '')
            _step('selection: %s' % first[:180])

        history = self._ctx.get('history')
        lines = []
        results = []          # action outcomes carried into the next round
        steps = 0
        last_sig = None
        mutated = False       # the chain changed this turn
        tested = False        # a test_chain run happened this turn
        last_ok = None        # verdict of the last test (None = no verdict)
        idle = 0              # consecutive rounds that changed nothing

        def _run_test():
            """Run test_chain, record its report, return its verdict."""
            try:
                report = execute_builder_call('test_chain', {}, self._ctx)
            except Exception as e:
                report = '(test_chain failed: %s)' % e
            _step('test: %s' % _clip(' '.join(str(report).split()), 180))
            lines.extend(str(report).splitlines())
            results.append('test_chain -> %s' % _clip(str(report), 500))
            return _verdict(report)

        for rnd in range(MAX_ROUNDS):
            # Re-read the canvas every round so edits are visible.
            canvas = execute_builder_call('read_graph', {}, self._ctx)
            _note('thinking')
            _step('round %d' % (rnd + 1))
            reply = reasoning._generate(
                _format_turn(query, canvas, selection, history, results),
                settings, _system_prompt())
            if not reply:
                if rnd == 0:
                    return {'ok': False, 'steps': 0, 'error': 'no reply',
                            'text': ('(no answer from the builder model — '
                                     'check the engine and model in the '
                                     'builder settings)')}
                lines.append('(builder model stopped answering — stopping)')
                break
            text, actions, parsed = _extract_reply(reply)
            # Always trace the round's outcome: a reply that runs no action
            # must never be a silent dead stop in the log again.
            _step('model: %d chars, %d actions%s'
                  % (len(reply), len(actions),
                     '' if parsed else ' (not JSON)'))
            if parsed:
                if text:
                    lines.extend(text.splitlines())
            else:
                # No JSON envelope: keep a bounded head (the trace holds the
                # whole reply) and flag an action JSON that failed to parse.
                lines.append(_clip(reply, 600))
                if '"actions"' in reply or '"reply"' in reply:
                    lines.append("(the builder model's action JSON did not "
                                 "parse — no actions were run; see the log)")
            if not actions:
                # Never conclude an EDITED chain without testing it: run the
                # test now and hand the report back so the model fixes what
                # fails — the loop iterates until the chain passes.
                if mutated and (not tested or last_ok is False):
                    last_ok = _run_test()
                    tested = True
                    if last_ok is False and rnd < MAX_ROUNDS - 1:
                        results.append('(the test above FAILED — fix the '
                                       'reason it names, then reply with '
                                       'actions; do not stop)')
                        continue
                break
            # No-progress guard: a model repeating the exact same calls must
            # not loop (nor re-run a chain test forever).
            sig = json.dumps([[a.get('name'), a.get('arguments')]
                              for a in actions], sort_keys=True, default=str)
            if sig == last_sig:
                lines.append('(no new actions — stopping)')
                break
            last_sig = sig
            names = []
            for action in actions[:MAX_ACTIONS]:
                name = str(action.get('name') or '').strip()
                arguments = action.get('arguments')
                if not isinstance(arguments, dict):
                    arguments = {}
                steps += 1
                if name not in ACTION_NAMES:
                    lines.append('(unknown action %r — skipped)' % name)
                    continue
                names.append(name)
                _note('tool:' + name)
                _step('tool: %s %s' % (
                    name, json.dumps(arguments, ensure_ascii=False)[:160]))
                try:
                    result = execute_builder_call(name, arguments, self._ctx)
                except Exception as e:  # execute never raises; belt + braces
                    result = '(%s failed: %s)' % (name, e)
                lines.extend(str(result).splitlines())
                results.append('%s %s -> %s' % (
                    name, json.dumps(arguments, ensure_ascii=False)[:120],
                    _clip(str(result), 400)))
                if name == 'test_chain':
                    tested = True
                    last_ok = _verdict(result)
            if any(name in MUTATING_ACTIONS for name in names):
                mutated = True
                idle = 0
            else:
                idle += 1
            # Auto-verify a chain that was just (re)wired: the builder must
            # see whether its own build runs.  An explicit test is respected.
            if ('test_chain' not in names
                    and any(name in ('wire', 'build_skill')
                            for name in names)):
                last_ok = _run_test()
                tested = True
            # A model that only reads (no change for MAX_IDLE_ROUNDS) will
            # never fix anything: stop and hand back to the user.
            if idle >= MAX_IDLE_ROUNDS:
                lines.append('(the builder made no change for %d rounds — '
                             'stopping; it is not converging on its own, ask '
                             'it directly or edit the node yourself)' % idle)
                break
        if mutated and (not tested or last_ok is False):
            lines.append('(the chain has not passed a test yet — reply '
                         '"keep going" to let the builder keep fixing it)')
        final = '\n'.join(ln for ln in lines if str(ln).strip())
        return {'ok': True, 'text': final or '(done)', 'steps': steps,
                'error': None}
