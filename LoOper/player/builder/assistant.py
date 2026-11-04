"""assistant.py — the chain ANALYST, which may change what is selected.

It reads (``read_graph``, ``read_node``) and answers questions about the chain
from the evidence the panel hands it: the chain's own description when it has
one, the deterministic topology digest otherwise.

It may also CHANGE the canvas, but only what the user put in front of it:
``edit_node`` writes settings on a SELECTED node and ``wire_nodes`` connects or
disconnects the wire between the TWO selected nodes.  It cannot create or
delete a node, record, or run anything; it does not write the chain's
description and does not start the walkthrough — those are the panel's buttons.

The grounding is deterministic (``describe.py`` builds the digest from the
resolved canvas); the model only writes prose over it.  Every failure raises
(``errors.py``); a malformed envelope is a ``BuilderProtocolError``, not a
silently-dropped action list.
"""

import json
import re

from . import describe as _describe
from . import flow as _flow
from . import reasoning as _reasoning
from . import tools as _tools
from .errors import BuilderModelError, BuilderProtocolError
from .trace import emit as _emit

#: A reading turn needs a handful of drill-downs at most.
MAX_ROUNDS = 3
#: Chat tail carried between turns.
MAX_HISTORY = 6

#: Prompt budgets (characters).  Each part has a ceiling, and ``_format_turn``
#: clips the SUM to ``PROMPT_TURN_CHARS``: per-part caps alone let the turn grow
#: past any model's window, and an 8k model then answered a plain "show me the
#: flow" with "please reduce the length of the messages".
PROMPT_DIGEST_CHARS = 6000
PROMPT_INDEX_CHARS = 2000
PROMPT_SELECTION_CHARS = 1500
PROMPT_HISTORY_CHARS = 1000
RESULT_CLIP = 4000
#: Everything the turn sends (every block, without the system prompt).
PROMPT_TURN_CHARS = 10000
#: Topology-only block for a chain that already describes itself.
PROMPT_STRUCTURE_CHARS = 2500


_ROLE_HEAD = (
    "You are the LoOper/Arrow chain analyst, docked beside a chain canvas of a "
    "deterministic computer-use runtime.\n"
    "WHAT YOU DO\n"
    "- Answer questions about this chain from the evidence below.\n"
    "- Propose enhancements, and guide the user in building a chain — from "
    "scratch or from another chain as a base — by naming the nodes to add, "
    "their settings and the ports to wire. You cannot create or delete a node, "
    "record or run anything, so those steps are the user's to do.\n"
    "- CHANGE what the user has in front of it: the settings of the node(s) "
    "they SELECTED, and — when exactly two nodes are selected — the wire "
    "between those two (edit_node / wire_nodes). They refuse everything else, "
    "and you report their result as it stands.\n"
    "- Never write the chain's description and never start the walkthrough: "
    "the panel's Describe button and its walk button own those. Do not emit a "
    "'flow' object and do not ask for a walk.\n"
    "For a change you cannot make, name the exact place it belongs (the dialog "
    "field, the port pair to wire) and stop — never claim you changed it.\n"
    "\n"
    "THE GRAPH\n"
    "- A chain is played by a deterministic player; the model that authored it "
    "is not part of the run.\n"
    "- EXECUTION edges decide WHEN: an `output` (exec out) into an `input` (exec "
    "in). A conditional routes on `true` / `false`; a FILE-LOOP conditional "
    "exposes a single `output`.\n"
    "- DATA edges decide WHAT, and the destination port defines the meaning:\n"
    "  * input.data -> llm.prompt REPLACES the prompt template (the instruction "
    "belongs in system_message);\n"
    "  * llm.context -> context.ctx_in STORES that turn; context.ctx_out -> "
    "llm.context SERVES the whole pool (documents, skills, stored rows);\n"
    "  * code uses NAMED IO ports for its declared variables; its base `output` "
    "carries only execution and `error` is the failure branch;\n"
    "  * an orchestrator LLM takes chains on `tools`, sub-brains on `brains`, "
    "and exposes `trace` / `route`;\n"
    "  * a context pool is PASSIVE: never run, only consulted and written "
    "through its data ports.\n"
    "- An `input` carries its value on `data`; its `output` (and `true`/`false` "
    "when decision routing is on) only moves execution.\n"
    "\n"
    "ANSWERING \"WHAT DOES THIS CHAIN DO\"\n"
    "1. The goal, from the chain's own words and the first act.\n"
    "2. The start node, and why it starts.\n"
    "3. The phases in order (a CHECK or LOOP closes one): what each does, and "
    "what it leaves for the next.\n"
    "4. Each CHECK: its criterion, and where true and false go.\n"
    "5. Each LOOP: what repeats, what ends it, its cap.\n"
    "6. Data per step: which value reaches which port, and what the port does "
    "with it (replace / append / store / serve / bind).\n"
    "7. Each REPORT: the value the chain actually produces.\n"
    "8. The defects the topology contains: illegal wires, unwired branches, "
    "dead ends, unreachable steps, missing recordings, typing on the real "
    "desktop, more than one start.\n"
    "Every node's DOES line under NODE CONTENT is built from its OWN settings — "
    "the value it supplies, the criterion it decides, the instruction it writes "
    "from, the recording it replays, plus its wiring. Answer about a node from "
    "that line, never from its type or the type's purpose: the type is shared by "
    "every node of that kind. Cite ports by ROLE, e.g. \"llm.prompt (prompt "
    "in)\".\n"
    "\n"
    "A CHAIN CAN BE ONE NODE with no wires: then its DOES line IS the "
    "description. Never call such a chain a stub or a placeholder while its node "
    "points at a recording or carries settings that name real work; if the line "
    "says the artifact is missing or holds no actions, say exactly that."
)

#: The facts an answer must not contradict.  (Writing the description is the
#: Describe button's job, so the old "how to write flow metadata" block is gone.)
_GROUND = (
    "\n"
    "GROUND YOUR ANSWER (a wrong statement is worse than a vague one)\n"
    "- Only claim a value reaches a node when DATA FLOWS lists an edge into it. "
    "A REPORT (output) with no data port receives nothing — it prints the text "
    "it was configured with. Where a value lands is the port named on the edge, "
    "not the node that looks closest.\n"
    "- A node whose DOES line names a recording HAS its actions attached: never "
    "call it unattached or empty; if the line reports a missing file, quote "
    "that fact.\n"
    "- An unwired branch is a dead end: say so. Never invent an edge the walk "
    "does not show.\n"
    "THE CHAIN'S OWN WORDS come first: when its stored description is in "
    "front of you (summary and box notes), answer from it and from the "
    "topology — do not restate the wiring and do not write it again."
)

#: The mechanics of changing the canvas.  The tools enforce all of it, so a
#: refusal is a fact the model reports — never something it works around.
_CHANGES = (
    "\n"
    "CHANGING THE CANVAS (the tools enforce this; a refusal is a fact)\n"
    "- edit_node works on ONE node the user selected, and only on the fields "
    "its type declares — read_node lists them, so ask rather than guess.\n"
    "- wire_nodes works only when EXACTLY TWO nodes are selected, and only "
    "between those two. Give from_port/to_port when the source has several "
    "execution outputs (a check) or the wire carries DATA; otherwise the only "
    "execution pair is picked for you.\n"
    "- Say what you changed in one line. If a tool raised, report its message "
    "and ask for what it needs (usually a selection) — do not retry blindly."
)

_ROLE_TAIL = (
    "\n"
    "STYLE\n"
    "Answer the question that was asked. Be concrete and short: phases and "
    "wiring, not a paraphrase of node names. Use read_node for a node you were "
    "not shown. If the request is ambiguous, ask ONE question and stop."
)

_PROTOCOL = (
    'Reply with ONE JSON object and nothing else:\n'
    '{"reply": "<the answer, shown to the user>", "actions": [{"name": "...", '
    '"arguments": {...}}]}\n'
    '"actions" may be omitted or empty when the digest already answers.\n'
    "Writing a chain's description is NOT yours: the panel's Describe button "
    "does that, node by node and wire by wire. Answer questions; never emit a "
    "'flow' object.\n"
    "Tools:\n"
    + '\n'.join('- %s %s — %s' % (spec['name'], spec['arguments'],
                                  spec['description'])
                for spec in _tools.TOOL_SPECS)
    + "\n"
    "Rules: at most three rounds per user message; call a tool when the digest "
    "plus the canvas index do not carry what you need, or when the user asked "
    "for a change to what they selected; never invent a tool name (the only "
    "names are %s); the tool result comes back before your next reply. Do NOT "
    "emit tool-call markup (DSML / XML invoke blocks) — a call is an entry in "
    "the JSON 'actions' array and nothing else." % ', '.join(_tools.TOOL_NAMES)
)


def _system_prompt(registry=True):
    """Role + grounding + change rules + protocol + the node registry.

    ``registry`` is the node-type reference; the last-resort prompt drops it.
    There is no authoring variant: the chat does not write chain descriptions
    (the Describe button does), so the prompt that taught HOW to write them,
    and the ``flow`` object in the protocol, is gone.
    """
    from .runbook import capability_registry
    parts = [_ROLE_HEAD, _GROUND, _CHANGES, _ROLE_TAIL, _PROTOCOL]
    if registry:
        parts.append(capability_registry())
    return '\n\n'.join(part for part in parts if part)


def _self_answer(stored, snapshot, described):
    """The chain answering about itself, with NO model in the path.

    Deterministic-first: a described chain has words of its own (summary + box
    notes), an undescribed one has its topology.  Either is a better answer
    than an error, and neither costs a model call at all.
    """
    if described:
        lines = []
        if stored.get('summary'):
            lines.append(str(stored['summary']).strip())
        for section in stored.get('sections') or ():
            note = ' '.join(str(section.get('note') or '').split())
            lines.append('- %s%s' % (section.get('title'),
                                     (': ' + note) if note else ''))
        if lines:
            return '\n'.join(lines)
    return ('This chain has no stored description yet, so here is its shape.\n'
            + _describe.structure(snapshot, PROMPT_STRUCTURE_CHARS))


def _window_too_small(exc):
    """The provider rejected the prompt for SIZE (not for a bad key, a quota…).

    Two spellings reach here: the gateway's own ``context_length_exceeded`` and
    this module's rewording ("the prompt is bigger than this model's context
    window"), so both must be recognised or the retry never runs.
    """
    text = str(exc).lower()
    return 'context window' in text and ('bigger than' in text
                                         or 'too small' in text
                                         or 'context_length' in text)


# ------------------------------------------------------------- envelope

#: The chat-template tool-call markup some local models answer with instead of
#: the JSON envelope (DeepSeek's DSML: ``<｜｜DSML｜｜ invoke name="read_node">``).
#: The calls are unambiguous, so they are parsed — refusing them kills a turn
#: the user asked for over a formatting difference.
_DSML_INVOKE = re.compile(
    r'<[^<>]*invoke\s+name\s*=\s*"([^"]+)"[^<>]*>(.*?)</[^<>]*invoke\s*>',
    re.DOTALL)
_DSML_PARAM = re.compile(
    r'<[^<>]*parameter\s+name\s*=\s*"([^"]+)"([^<>]*)>(.*?)'
    r'</[^<>]*parameter\s*>', re.DOTALL)
_DSML_TAG = re.compile(r'</?[^<>]*DSML[^<>]*>', re.DOTALL)


def _dsml_turn(text):
    """``(reply, actions)`` from native tool-call markup; ('', []) when none.

    Parameter values stay strings unless the markup says otherwise
    (``string="false"``) or the value is itself a JSON object/array — the
    tools take plain string arguments.
    """
    invokes = _DSML_INVOKE.findall(str(text or ''))
    if not invokes:
        return '', []
    actions = []
    for name, body in invokes:
        arguments = {}
        for arg_name, attributes, raw_value in _DSML_PARAM.findall(body):
            value = raw_value.strip()
            flags = attributes.replace(' ', '')
            if 'string="false"' in flags:
                try:
                    arguments[arg_name] = json.loads(value)
                    continue
                except ValueError:
                    pass
            else:
                try:
                    parsed = json.loads(value)
                except ValueError:
                    parsed = None
                if isinstance(parsed, (dict, list)):
                    arguments[arg_name] = parsed
                    continue
            arguments[arg_name] = value
        actions.append({'name': name.strip(), 'arguments': arguments})
    # Drop whole invoke blocks FIRST (they contain the parameter tags), then the
    # leftover wrapper tags; the other order strips the delimiters and leaves
    # the raw values behind as if the model had written them as prose.
    prose = _DSML_TAG.sub(' ', _DSML_INVOKE.sub(' ', str(text)))
    return ' '.join(prose.split()), actions


def _balanced_end(text, start):
    """The index of the '{' at *start*'s matching '}' (string aware)."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index
    return -1


def _plain_text(text):
    """A model's prose as shown to the user (fences and stray braces gone)."""
    out = []
    for line in str(text or '').splitlines():
        stripped = line.strip()
        if stripped.startswith('```') or stripped in ('{', '}'):
            continue
        out.append(line.rstrip())
    return '\n'.join(out).strip()


def envelope(raw):
    """``{'reply', 'actions', 'flow', 'walk'}`` from a model reply.

    Raises when there is no envelope: a model that answers with prose has not
    followed the protocol, and silently inventing an envelope would hide it.
    """
    text = str(raw or '').strip()
    if not text:
        raise BuilderProtocolError('the model returned an empty reply')
    candidate = None
    try:
        loaded = json.loads(text, strict=False)
        if isinstance(loaded, dict):
            candidate = loaded
    except ValueError:
        candidate = None
    if candidate is None:
        start = text.find('{')
        while start >= 0:
            end = _balanced_end(text, start)
            if end > start:
                try:
                    loaded = json.loads(text[start:end + 1], strict=False)
                except ValueError:
                    loaded = None
                if isinstance(loaded, dict) and (
                        'reply' in loaded or 'actions' in loaded):
                    candidate = loaded
                    break
            start = text.find('{', start + 1)
    if not isinstance(candidate, dict) or not (
            'reply' in candidate or 'actions' in candidate):
        # Not our envelope — but a native tool-call block is still a readable
        # turn, so parse it instead of failing the message.
        prose, dsml_actions = _dsml_turn(text)
        if dsml_actions:
            return {'reply': prose, 'actions': dsml_actions, 'flow': {},
                    'walk': False}
        # A small model answers in PROSE.  That is still an answer: the panel
        # shows it, and nothing is annotated.  Failing the turn because the
        # JSON wrapper is missing punished the user for the model's shape —
        # a chain description never depends on the wrapper anyway.
        return {'reply': _plain_text(text), 'actions': [], 'flow': {},
                'walk': False}
    reply = str(candidate.get('reply') or '').strip()
    raw_actions = candidate.get('actions') or []
    if not isinstance(raw_actions, list):
        raise BuilderProtocolError('"actions" must be a list, got %r'
                                   % type(raw_actions).__name__)
    actions = []
    for item in raw_actions:
        if not isinstance(item, dict) or not str(item.get('name') or '').strip():
            raise BuilderProtocolError(
                'every action needs a name and an arguments object; got %r'
                % (item,))
        arguments = item.get('arguments') or {}
        if not isinstance(arguments, dict):
            raise BuilderProtocolError(
                'action %r arguments must be an object' % item.get('name'))
        actions.append({'name': str(item['name']).strip(),
                        'arguments': arguments})
    # A turn answers questions.  ``flow`` (the chain's description) and ``walk``
    # (the per-node highlight) are the panel's buttons now — deterministic, one
    # click — so a model that still emits them changes nothing here.
    return {'reply': reply, 'actions': actions, 'flow': {}, 'walk': False}


def extract_reply(raw):
    """``(reply, actions)`` from a model reply.  Raises when there is none."""
    env = envelope(raw)
    return env['reply'], env['actions']

def extract_turn(raw):
    """``(reply, actions, flow, walk)`` — one round's whole envelope."""
    env = envelope(raw)
    return env['reply'], env['actions'], env['flow'], env['walk']


def partial_reply(raw):
    """The user-facing reply-so-far of a still-streaming envelope.

    Only the ``reply`` string value is pulled out (with the escapes seen so
    far), so the panel never flashes braces and keys at the user.  A stream
    that has not reached the reply yet yields ''.
    """
    text = str(raw or '')
    key = text.find('"reply"')
    if key < 0:
        return ''
    colon = text.find(':', key + len('"reply"'))
    if colon < 0:
        return ''
    quote = text.find('"', colon + 1)
    if quote < 0:
        return ''
    escapes = {'n': '\n', 't': '\t', 'r': '', '"': '"', '\\': '\\',
               '/': '/', 'b': '', 'f': ''}
    out = []
    index = quote + 1
    while index < len(text):
        char = text[index]
        if char == '"':
            break
        if char == '\\':
            nxt = text[index + 1] if index + 1 < len(text) else ''
            if not nxt:
                break
            out.append(escapes.get(nxt, nxt))
            index += 2
            continue
        out.append(char)
        index += 1
    return ''.join(out)


# ---------------------------------------------------------------- turn

def _clip_part(text, limit):
    """*text* clipped to *limit* chars, saying so (never a silent cut)."""
    text = str(text or '')
    if len(text) <= limit:
        return text
    return (text[:max(0, limit - 60)].rstrip()
            + '\n… [cut to fit the model — ask for a narrower scope]')


def _fit_parts(pieces, budget):
    """``{key: text}`` clipped so the parts together fit *budget*.

    *pieces* is ``[(key, priority, text)]``; the LOWEST priority number is the
    evidence that answers the question and is served first.  Per-part ceilings
    alone let a turn grow past any model's window — the digest, the canvas
    index, the selection and the chat tail each stayed under their own cap and
    their SUM was still 34k characters, which an 8k model refuses outright.
    """
    left = budget
    kept = {}
    for key, _priority, text in sorted(pieces, key=lambda item: item[1]):
        if not text:
            kept[key] = ''
            continue
        if left <= 120:
            kept[key] = ''
            continue
        kept[key] = _clip_part(text, left)
        left -= len(kept[key]) + 2
    return kept


def _format_turn(query, digest, index, selection, history, results,
                 flow_text=''):
    """The whole turn, fitted to ``PROMPT_TURN_CHARS``.

    Order on the page: chat tail, stored flow, digest, index, selection, tool
    results, the question.  Order of IMPORTANCE (who gets the budget first) is
    different: the digest answers "what does this chain do", the index answers
    "which node", and the chat tail is a courtesy.  So the parts are clipped by
    importance and printed in reading order.
    """
    lines = []
    for item in (history or [])[-MAX_HISTORY:]:
        if not isinstance(item, dict):
            continue
        text = ' '.join(str(item.get('text') or '').split())[:400]
        if text:
            lines.append('%s: %s' % (item.get('role') or 'user', text))
    tail = ['USER: ' + str(query or '').strip()]
    if results:
        tail.insert(0, 'Now reply with the JSON object ("reply" holds the '
                       'answer; no tool-markup syntax). Call another tool only '
                       'if a value you need is still missing.')
    reserve = sum(len(part) + 2 for part in tail)

    kept = _fit_parts([
        ('digest', 0, 'CHAIN TOPOLOGY (deterministic digest):\n' + digest),
        ('index', 1, 'CANVAS INDEX (ports with their roles):\n' + index),
        ('selection', 2, "SELECTED NODES (the user's focus):\n"
                         + (selection if selection else '(none selected)')),
        ('flow', 3, flow_text),
        ('results', 4, 'TOOL RESULTS:\n' + '\n\n'.join(str(r) for r in results)
                       if results else ''),
        ('history', 5, 'RECENT CHAT:\n' + '\n'.join(lines) if lines else ''),
    ], PROMPT_TURN_CHARS - reserve)

    parts = [kept['history'], kept['flow'], kept['digest'], kept['index'],
             kept['selection'], kept['results']] + tail
    return '\n\n'.join(part for part in parts if part)


class BuilderAssistant:
    """One analyst turn: read the canvas, answer, drill down at most 3 times."""

    def __init__(self, ctx, settings=None):
        self._ctx = ctx
        self._settings = settings

    def _load_settings(self):
        if isinstance(self._settings, dict):
            return self._settings
        from . import settings as _settings
        return _settings.load()

    def _stopped(self):
        stop = self._ctx.get('stop')
        return bool(callable(stop) and stop())

    def route(self, query, progress_cb=None, trace=None, on_reply_delta=None):
        """Answer one user message -> ``{'reply', 'text', 'steps', 'stopped'}``.

        Raises every failure (configuration, model, protocol, canvas, tool).
        """
        query = str(query or '').strip()
        if not query:
            raise BuilderProtocolError('the message is empty')

        def _note(event):
            if callable(progress_cb):
                progress_cb(event)

        def _step(message):
            _emit(trace, message)

        settings = self._load_settings()
        snapshot = _tools.read_snapshot(self._ctx)
        stored = _tools.stored_flow(self._ctx)
        # A DESCRIBED chain already carries its own words.  A question about it
        # is answered from that metadata plus the order/branches — not from the
        # whole canvas, and not by writing the description again (the panel's
        # Describe button does that).  An undescribed chain has nothing to
        # answer from, so the canvas itself is the evidence.
        described = not _flow.is_empty(stored)
        if described:
            digest = '\n\n'.join(part for part in (
                # Summary + box notes answer "how does it flow"; one line per
                # node is the bulk of the metadata and read_node's job.
                _flow.digest(stored, include_nodes=False),
                _describe.structure(snapshot, PROMPT_STRUCTURE_CHARS)) if part)
            # The sections and node lines already name every node: a second,
            # id-only index would spend the budget saying it twice.
            index = ''
            flow_text = ''
        else:
            digest = _describe.overview(snapshot, PROMPT_DIGEST_CHARS)
            index = _tools.read_graph(self._ctx, limit=PROMPT_INDEX_CHARS)
            flow_text = ''
        selection = _tools.selection_text(self._ctx, snapshot,
                                          limit=PROMPT_SELECTION_CHARS)
        history = self._ctx.get('history') or []
        _step('chain: %s' % ('described — answering from its metadata'
                             if described else 'undescribed — reading the canvas'))

        _step('you: %s' % (' '.join(query.split())[:200]))
        if selection:
            first = selection.splitlines()[0]
            _step('selection: %s' % first[:180])

        settled = []
        buffer = {'text': ''}

        def _delta(increment):
            buffer['text'] += increment
            prefix = partial_reply(buffer['text'])
            shown = '\n\n'.join([part for part in settled if part]
                                + ([prefix] if prefix else []))
            if shown and callable(on_reply_delta):
                on_reply_delta(shown)

        def _generate(prompt, system, settings, on_delta=None, cancel=None):
            """The model call, with one smaller retry and a deterministic floor.

            A chain that describes itself can be answered from its goal and its
            boxes, without the topology block or the node registry.  So a model
            that cannot hold the full turn is asked something smaller; if even
            that is too big, ``None`` says "answer from the chain itself" — the
            Builder never needs a bigger model to answer a question.
            """
            try:
                return _reasoning.generate(prompt, system, settings,
                                           on_delta=on_delta, cancel=cancel)
            except BuilderModelError as exc:
                if not _window_too_small(exc):
                    raise
                _step('the prompt is bigger than this model — retrying with '
                      'the chain\'s own words only')
                lean = _format_turn(
                    query,
                    _flow.headline(stored) if described
                    else _describe.structure(snapshot, 1500),
                    '', '', history, [])
                try:
                    return _reasoning.generate(
                        lean, _system_prompt(registry=False),
                        settings, on_delta=on_delta, cancel=cancel)
                except BuilderModelError as again:
                    if not _window_too_small(again):
                        raise
                    return None

        results = []
        steps = 0
        changed = False
        for round_index in range(MAX_ROUNDS):
            if self._stopped():
                return {'reply': '\n\n'.join(settled), 'text': '(stopped)',
                        'steps': steps, 'stopped': True,
                        'flow': {}, 'walk': False, 'changed': changed}
            if round_index == 0:
                _note('reading')
            _step('round %d' % (round_index + 1))
            buffer['text'] = ''
            reply_raw = _generate(
                _format_turn(query, digest, index, selection, history, results,
                             flow_text),
                _system_prompt(), settings,
                on_delta=_delta, cancel=self._stopped)
            if reply_raw is None:
                # Deterministic-first: no model can hold this prompt, and the
                # chain already says what it does.  Answer from it.
                _step('no model answer fits — answering from the chain itself')
                settled.append(_self_answer(stored, snapshot, described))
                break
            if self._stopped():
                return {'reply': '\n\n'.join(settled), 'text': '(stopped)',
                        'steps': steps, 'stopped': True,
                        'flow': {}, 'walk': False, 'changed': changed}
            reply, actions, _flow_out, _walk = extract_turn(reply_raw)
            _step('model: %d chars, %d action(s)'
                  % (len(reply_raw), len(actions)))
            if reply:
                settled.append(reply)
                if callable(on_reply_delta):
                    on_reply_delta('\n\n'.join(settled))
            if not actions:
                break
            for action in actions:
                if self._stopped():
                    return {'reply': '\n\n'.join(settled), 'text': '(stopped)',
                            'steps': steps, 'stopped': True,
                            'flow': {}, 'walk': False, 'changed': changed}
                steps += 1
                _note('tool:' + action['name'])
                _step('tool: %s %s' % (action['name'],
                                       json.dumps(action['arguments'])[:160]))
                output = _tools.execute(action['name'], action['arguments'],
                                        self._ctx)
                if action['name'] in _tools.WRITE_TOOLS:
                    changed = True
                text = str(output)
                if len(text) > RESULT_CLIP:
                    text = (text[:RESULT_CLIP]
                            + '\n[result clipped at %d chars]' % RESULT_CLIP)
                results.append('%s %s ->\n%s'
                               % (action['name'],
                                  json.dumps(action['arguments'])[:120], text))

        reply = '\n\n'.join(part for part in settled if part).strip()
        if not reply:
            raise BuilderProtocolError(
                'the model called tools for all %d rounds but never wrote an '
                'answer — it keeps emitting tool-call markup instead of the '
                'reply/actions envelope' % MAX_ROUNDS)
        return {'reply': reply, 'text': reply, 'steps': steps, 'stopped': False,
                'flow': {}, 'walk': False, 'changed': changed}
