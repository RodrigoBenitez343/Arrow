"""orchestrator_ops.py — Orchestrator node executor: a goal loop over mini brains.

The Orchestrator node turns a chain into a goal-seeking brain: it connects to
several system chains (mini brains) through its ``brains`` port and loops —
pick a worker, SCOPE the next step for it, run it in-player, read its result,
repeat — until the goal is achieved, no worker fits, the step cap hits, no
progress is made, or the user stops it.

A run that does NOT end 'done' (cap / no-progress / unparseable / parked
awaiting user) activates the ``route`` port instead of ``output`` when one is
wired, and publishes the unfulfilled goal on it — a sibling orchestrator can
take the goal over.

Per iteration:
  0. State digest (one read, ``_orchestrator_observe_state``): the observed
     world NOW — the chain's live web page (URL, title, visible text with
     shadow roots and frames) and the desktop foreground window (title +
     process).  It rides into the chains gate, the picker, the scoper and the
     verifier, so the model judges by what the world shows, not only by the
     brain's own prose.  Read-only and bounded; empty when nothing is
     observable (``LOOPER_ORCH_STATE=off`` disables it).
  1. Routing ladder (chains port): first "is there a LEARNED chain that does
     this job?" — judged on INTENT COVERAGE, never on matching a description:
     the artifact's composition is expanded at routing time (step names +
     descriptions, in order) and every step's salient word must be asked for
     while every requested action must belong to a step; a partial or larger
     request is refused with the exact gap in the log.  A request no artifact
     covers but whose PARTS do is ASSEMBLED: one deterministic unit per
     consecutive run of directives (whole artifact > its step file > atom),
     run as one continuous chain and — when verified — persisted as the new
     variation artifact (learned chains composed of learned chains).  Otherwise
     the goal is split into directives by one planning turn (verb-first lines,
     measured
     2026-09-24) and the UNUSED step-sized chains are scored one directive at a
     time as its NEXT STEP (learned artifacts never compete here — they
     compose several steps).  Anything unclear falls through to the brains.
     The plan is the goal decomposition: once EVERY directive was executed and
     verified the
     level ends 'done' structurally (structure beats the probe — a probe on
     an achieved goal read 0.394 and the picker then fabricated extra work,
     2026-09-24); the probe only runs while the plan can still be unfinished.
  2. Brains = nodes wired to this node's ``brains`` input port: either
     ``chain_import`` nodes (sub-chain files; descriptions derived at runtime
     from the chain files, listing a router brain's atomic tool names as
     context) or single flagged nodes (``tool_provider`` — the builder gates
     any node whose ONLY execution edges land on router ports).  A brain that
     is ITSELF an orchestrator (its file contains one, transitively) is
     NESTED and receives the remaining directive LIST — it re-runs this whole
     ladder at its level and hands the unaccomplished work back up.  The
     handback is a two-way contract: the marker lists what it could NOT do
     (the parent re-routes only that tail) and marker ABSENCE means the
     sub-run ended 'done' — the whole delegated span is then complete by
     construction, no probe needed.  Any other brain is a SPECIALIST: it gets
     ONE scoped step and its own router picks the tool.
  3. Picker: ``llm`` (numbered brains, strict one-token answer) or ``laya``
     (typed-question choice via the embedded Laya engine, when available).
     Falls back to the LLM picker whenever the engine is unreachable.
  4. Step scoping: one short turn turns goal + progress + state into a single
     imperative instruction for the picked worker ("Your next step: ...") —
     the orchestrator scopes, the brain picks the tool.  When that turn
     fails, the worker's description is the task (previous behavior).
  5. The chosen worker is invoked in-player via ``_orchestrator_invoke_brain``
     (chain imports through ``_execute_chain_import_node``, single nodes
     through their own in-player executor) with the composed prompt written
     into ``_chain_input_context`` (the established channel every Input node
     resolves from).
  6. The result is collected via ``_orchestrator_collect_brain_result``,
     judged by one Laya forward (``_orchestrator_verify_step``: did the step
     do what it was asked?  True/False/None when the engine is down) against
     the step's result AND the observed state, and the verdict lands on the
     trace, which drives the next scoping turn.  A step the node itself
     reported as failed is False without a forward.

Final output: one single-shot LLM synthesis over the trace (the node's own
small model; the worker list is hidden so the model cannot re-enter routing).
Fallback: joined trace text.  Guards: ``max_steps`` (default 15), no-progress
(identical worker + identical result twice), the done-gate (a 'done' pick is
believed only once the goal's sequenced actions each had a step — see
``_orchestrator_done_is_believable``), stop flag.

ponytail: the LLM picker ships first; the Laya engine is a drop-in picker
upgrade behind ``picker=laya`` and degrades to the LLM picker on ``None``.
"""

import hashlib
import json
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

try:
    from logging_setup import log_block
except Exception:
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)

# Trace shaping (kept small — the prompt must fit 1-3B model windows).
_RESULT_EXCERPT_CHARS = 400
_TRACE_PROMPT_CHARS = 4000
_DEFAULT_MAX_STEPS = 15
# A goal is split into its single-action directives before routing; the cap
# keeps a rambling request from turning into a long march.
_ORCH_MAX_DIRECTIVES = 6
# The longest a single directive may be; a piece beyond this is a paragraph,
# not an action.  The MODEL writes the steps (see _orchestrator_plan_directives:
# the request is never cut into pieces by patterns), so a reply whose line is
# bigger than this is refused as a paragraph.
_ORCH_DIRECTIVE_CHARS = 240
# NOTE (measured twice, 2026-09-25): the plan prompt carries NO worker list
# and NO task-specific examples.  A 2B planner does not treat reference
# material as reference — it copies it INTO the plan: worker descriptions
# ('go to linkedin.com', 'click on my network button') and a learned chain's
# own description ('Learned from a verified run: ...') all became plan lines,
# and the run then executed the template instead of the request.  The worked
# examples below use task-NEUTRAL vocabulary for the same reason.
# Machine-readable handback: an orchestrator that ends with work outstanding
# appends this marker (JSON list of the directives it could NOT finish) to its
# published output, so a parent orchestrator advances past the prefix that WAS
# accomplished and re-routes only the untouched tail instead of the whole span.
_ORCH_REMAINING_MARKER = '[ORCH-REMAINING]'
# Machine-readable OUTCOME, emitted WITH the handback marker: the child's own
# step list and stop reason.  Observability for the parent (trace + logs) —
# the parent's cursor/verdict logic stays structure-based, never claim-based.
_ORCH_OUTCOME_MARKER = '[ORCH-OUTCOME]'
# Object-level comparison of actions.  NOTHING here decides WHICH tools exist
# or what they are called — the tools are human-made and wired at will.  The
# only fixed vocabulary is LANGUAGE GLUE: the function words and the list
# separators every request shares.  The words that NAME things are classified
# per library at routing time from the library's own corpus (a term its signals
# share across many chains is non-discriminating BY CONSTRUCTION — "click",
# "button", "page" in an English UI set — while a term only some chains carry
# is an object): see _orchestrator_library_generic.
_ORCH_STOP_WORDS = frozenset((
    'a', 'an', 'and', 'at', 'by', 'for', 'from', 'in', 'into', 'my', 'of',
    'on', 'onto', 'our', 'the', 'then', 'to', 'up', 'with', 'your',
    # Pronouns, auxiliaries and wh-words are grammar, not objects: 'are' in
    # a step used to nominate a brain whose description carried '...they are
    # open' (measured 2026-09-25: 'what are the videos presented here about
    # in general?' picked the linkedin brain deterministically over it).
    'i', 'you', 'we', 'they', 'he', 'she', 'me', 'him', 'her', 'us', 'them',
    'it', 'its', 'this', 'that', 'these', 'those', 'is', 'are', 'was',
    'were', 'am', 'be', 'been', 'do', 'does', 'did', 'have', 'has', 'had',
    'will', 'shall', 'should', 'may', 'might', 'must', 'can',
    'what', 'which', 'who', 'when', 'where', 'why', 'how', 'here', 'there',
    'about', 'if', 'so', 'or', 'not', 'any', 'also', 'just',
))
# Reaching verbs (action glue, not domain vocabulary): a step that OPENS a
# place the observed state already shows is already true — the planner is
# told to skip satisfied steps, and this is the deterministic enforcement
# (measured 2026-09-25: 'get context from the website i opened...' planned
# 'Open Chrome browser', the step executed, and its chains gate navigated
# the user's OPEN tab to linkedin.com).
_ORCH_STATE_DONE_VERBS = frozenset((
    'open', 'go', 'navigate', 'visit', 'launch', 'start',
))
# Planner meta-commentary never becomes a directive: a line OPENING with one of
# these words, or one ending in ':' (a lead-in), is narration.  Measured
# 2026-09-24: SmolLM3 planned "1) navigate to linkedin.com… | 2) However,
# following the specific format requested: | 3) open linkedin.com…" — the
# narration line ALSO broke the object-dedupe between the two real steps
# around it, leaving a garbage directive that killed the step gate's margin.
_ORCH_META_WORDS = frozenset((
    'however', 'but', 'note', 'explanation', 'sure', 'okay', 'wait',
    'following', 'remember', 'anyway', 'therefore', 'thus',
    # Request preamble: after splitting, "i need you to take the browser"
    # becomes its own clause — it is not an action.
    'i', 'please', 'can', 'could', 'would', 'you',
))


def _orchestrator_stem(word):
    """Language glue: the plural/third-person 's' must not split one object in
    two ("messages" vs "message", "navigates" vs "navigate").  Deliberately
    crude — a stemmer is not the point, the LIBRARY classifies meaning."""
    w = str(word or '')
    if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    return w


def _orchestrator_words(text):
    """Tokens of a text: letters, digits and the in-word apostrophe.

    Regex-free on purpose — nothing on this path splits a request with
    patterns — and a list NUMBER falls away (digits followed by '.' or ')')
    while digits that are part of the task ("3 jobs") stay.
    """
    out = []
    buf = []
    s = str(text or '').lower()
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isdigit():
            j = i
            while j < len(s) and s[j].isdigit():
                j += 1
            if j < len(s) and s[j] in '.)':
                i = j + 1
                if buf:
                    out.append(''.join(buf))
                    buf = []
                continue
            buf.append(s[i:j])
            i = j
            continue
        if ch.isalnum() or ch == "'":
            buf.append(ch)
        elif buf:
            out.append(''.join(buf))
            buf = []
        i += 1
    if buf:
        out.append(''.join(buf))
    return out


def _orchestrator_library_generic(signals):
    """Terms this LIBRARY treats as generic — derived from its own corpus.

    A word the wired chains' signals share across many of them names no
    specific object FOR THIS LIBRARY, so it must not let one action pass for
    another; a word only some signals carry does.  No vocabulary is assumed:
    rename a chain, add a language, rewire the port — the classification
    follows the data (an empty or one-chain library classifies nothing).
    """
    texts = [str(s or '').lower() for s in (signals or [])]
    texts = [t for t in texts if t.strip()]
    if len(texts) < 2:
        return frozenset()
    counts = {}
    for t in texts:
        for w in {_orchestrator_stem(tok) for tok in _orchestrator_words(t)}:
            if w in _ORCH_STOP_WORDS:
                continue
            counts[w] = counts.get(w, 0) + 1
    limit = max(2, (len(texts) + 1) // 2)
    return frozenset(w for w, n in counts.items() if n >= limit)


def _orchestrator_directive_key(text, generic=frozenset()):
    """The OBJECT words of a text: stems minus glue minus library generics.

    Two texts with the same key are the same action re-worded.  List bullets
    and numbering fall away in _orchestrator_words (the routing reader flattens
    examples to one line), digits that are part of the task ("3 jobs") stay.
    """
    words = {_orchestrator_stem(w) for w in _orchestrator_words(text)}
    return {w for w in words
            if w not in _ORCH_STOP_WORDS and w not in (generic or frozenset())}


def _orchestrator_strip_prefix(line):
    """Numbering and bullets off a line — character walks, no patterns."""
    s = str(line or '').strip()
    changed = True
    while changed:
        changed = False
        for mark in ('-', '*', '\u2022'):
            if s.startswith(mark):
                s = s[1:].lstrip()
                changed = True
        i = 0
        while i < len(s) and s[i].isdigit():
            i += 1
        if i > 0 and i < len(s) and s[i] in '.)':
            s = s[i + 1:].lstrip()
            changed = True
    return s


def _orchestrator_list_items(text):
    """The request is ALREADY a discrete list -> its items; prose -> [].

    A numbered/bulleted block is structured input — the step list a parent
    level hands down — so its lines ARE steps and are read as such; re-planning
    one is exactly how an item got dropped (measured 2026-09-24: the nested
    planner re-planned a clean four-item list and lost 'jobs').  Anything else
    — the human's prose — returns [] and goes to the model: nothing here cuts a
    request into pieces, which is what disconnects the steps from the chains
    the library offers.
    """
    lines = [l for l in str(text or '').splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    items = []
    for line in lines:
        item = _orchestrator_strip_prefix(line)
        if item == line.strip():
            return []  # a line with no marker -> prose, not a list
        if item:
            items.append(item)
    return items


def _orchestrator_looks_compound(text):
    """Plausibility, NEVER a cut: does the request join clauses?

    A request with a comma, a semicolon, " and " or " then " probably carries
    more than one action, which is what the protocol's re-ask is triggered by
    (and what the scope guard uses to refuse a scope that drops part of a
    compound step).  The LIST itself is always the model's job — measuring
    intent here is forbidden, cutting it is what broke the steps.
    """
    low = ' ' + ' '.join(str(text or '').lower().split()) + ' '
    return (',' in low or ';' in low
            or ' and ' in low or ' then ' in low)


def _orchestrator_inherit_verbs(clauses):
    """A verbless line continues the action before it.

    "navigate to linkedin, click on my messages, then on my network" — the
    model may write the third step without a verb of its own, the ellipse
    English resolves from the coordination: the shared verb is the PREVIOUS
    step's first word, so "on my network" becomes "click on my network" (the
    tool is described "clicks on my network button"; measured live 2026-09-24,
    the verbless chunk read 0.578 — under the gate's floor — and the action
    stayed unserved, while the named one read 0.641).

    The test is pure LANGUAGE GLUE: a line that OPENS with a function word
    ("on", "to", "the", "my") is a continuation; one that opens with anything
    else keeps its own verb ("press the home button" is untouched), and a first
    step has nothing to inherit from.  No domain vocabulary is assumed.
    """
    out = []
    for clause in clauses:
        text = str(clause or '').strip()
        words = text.split()
        first = words[0].strip('",.;:').lower() if words else ''
        if out and first and first in _ORCH_STOP_WORDS:
            text = f'{out[-1].split(" ", 1)[0]} {text}'.strip()
        out.append(text)
    return out


def _orchestrator_lines_to_directives(text, generic=frozenset()):
    """The model's list -> directives: ONE line, ONE directive.

    The steps are the MODEL's work (see _orchestrator_plan_directives); this
    only shapes what came back, and it never cuts a line into pieces — a cut
    step is a fragment the gate cannot connect to the chain that serves it.
    Reasoning blocks are dropped by the caller, numbering and bullets come off
    (character walks — see _orchestrator_strip_prefix), narration and request
    preamble are dropped, duplicates go (by object across the whole plan), each
    directive is capped and the plan is capped.  Returns [] when nothing usable
    was found — the caller decides the fallback.
    """
    out = []
    seen = set()
    seen_keys = []
    for raw_line in str(text or '').splitlines():
        line = _orchestrator_strip_prefix(raw_line).strip(' \t-*"\'`')
        if not line or len(line) > _ORCH_DIRECTIVE_CHARS:
            continue
        # Scaffolding guard: model boilerplate ('[Analyze the current state,
        # ...') is reasoning shape, never an action — and its bracket
        # characters are what corrupted the handback marker downstream
        # (measured 2026-09-25: the marker's JSON array was cut at the first
        # ']' inside a directive, the unaccomplished list was silently
        # dropped, and the parent closed its plan as done over a no-op
        # sub-run).
        if '[' in line or ']' in line:
            continue
        # Narration guard (see _ORCH_META_WORDS): a lead-in or an opinion
        # is not an action, and letting one through corrupts the dedupe of
        # the real steps around it.
        if (line.endswith(':')
                or line.split(' ', 1)[0].rstrip(',:').lower()
                in _ORCH_META_WORDS):
            continue
        low = line.lower()
        if low in seen:
            continue
        key = _orchestrator_directive_key(line, generic)
        # Same object, re-worded action — checked against EVERY directive so
        # far, not only the previous one: a plan that lists the same action
        # twice (measured 2026-09-24) collapses instead of duplicating work.
        if key and any(key <= k for k in seen_keys):
            continue
        seen.add(low)
        out.append(line)
        if key:
            seen_keys.append(key)
        if len(out) >= _ORCH_MAX_DIRECTIVES:
            break
    return out


def _orchestrator_marker_array(text, pos, open_ch='[', close_ch=']'):
    """The JSON span after a marker — a bracket-BALANCED scan.

    A regex cannot read this: the directive strings may contain ']' (they
    are arbitrary model text), so brackets must be counted, not matched —
    and inside a JSON string a bracket is content, not structure.  The two
    bracket chars select the shape: arrays ('[]', the handback marker) and
    objects ('{}', the outcome marker).  Returns ``(parsed, end_index)``
    for the first span at/after ``pos``, or None when there is none.
    """
    text = str(text)
    start = text.find(open_ch, int(pos))
    if start == -1:
        return None
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
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1]), i + 1
                except Exception:
                    return None
    return None


def _orchestrator_split_negation(text):
    """(asserted, refused) — a description split at its first negation.

    A worker description says what the worker DOES and what it never does
    ("It does not open, navigate, or type into websites.").  Word matching
    must respect that split, or the refusal nominates the worker for the
    very action it refuses (measured 2026-09-25: the vision brain was handed
    'Open LinkedIn' while its own description reads 'does not open').
    """
    import re as _re
    parts = _re.split(
        r"\b(?:not|never|cannot|'?n't)\b", str(text or ''),
        maxsplit=1, flags=_re.IGNORECASE)
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], parts[1]


def _orchestrator_owner_id(node):
    """The orchestrator node's id — the builder keeps it in ``data.node_id``.

    A built orchestrator node carries NO top-level ``id`` key (verified
    2026-09-24: keys are connections/data/inputs/type), so reading only
    ``node['id']`` silently resolved to '' — which disabled the freeze's host
    guard and made every attach log "no chain file to edit" without wiring.
    """
    data = node.get('data') or {}
    return str(
        node.get('id') or node.get('node_id')
        or data.get('node_id') or data.get('id') or ''
    )


# The done-probe: p(the goal has already been fully achieved) over a
# steps-only premise.  Measured on real failing traces (2026-09-23): genuinely
# covered 0.587 vs false 0.506 / 0.144 — threshold 0.55.
_DONE_PROBE_MIN = 0.55

# State digest (the loop's third input: GOAL + what WAS DONE + the world NOW).
# A step result is the brain's own prose — it reads "opened LinkedIn" whether
# or not the screen moved (measured 2026-09-23: a failed web sequence reported
# success, Laya verified it, and the loop closed as done).  The digest is the
# observed answer, bounded and read-only.
_STATE_DIGEST_CHARS = 1200
_STATE_WEB_TEXT_CHARS = 600
_STATE_IN_PROMPT_CHARS = 700

# Mini-brain node types the orchestrator may dispatch as ONE step.  Every
# listed executor accepts ``(node, stop_flag)`` (its extras are keyword /
# optional).  ``conditional`` is dispatched inline (evaluate once, no branch
# navigation); ``input`` / ``output`` / ``context`` / ``container`` are
# deliberately excluded — they block on user dialogs or carry no step
# semantics.  Anything else hits a warning and is skipped, never a crash.
_BRAIN_EXECUTORS = {
    'chain_import': '_execute_chain_import_node',
    'code': '_execute_code_node',
    'mcp': '_execute_mcp_node',
    'llm': '_execute_llm_node',
    'sequence': '_execute_sequence_node',
    'web_sequence': '_execute_web_sequence_node',
    'handle': '_execute_handle_node',
    'form_filler': '_execute_form_filling_node',
}
# A single-node brain costs ZERO file I/O: its description is the node label +
# its type + ONE structural hint, capped so the picker prompt stays small.
_NODE_BRAIN_DESC_CAP = 120
# Result cascade for node brains: ports consulted in order, str-only, and the
# whole text capped — a full port dump (e.g. a web-page payload) must never
# reach the trace.
_BRAIN_RESULT_PORTS = ('output', 'data', 'context', 'ctx_out', 'decision')
_BRAIN_RESULT_CAP = 2000
# Frozen chains carry at most this many verbatim past requests as routing
# signal (they are the criteria text of ONE step read — keep it small).
_CHAIN_ROUTING_EXAMPLES = 5
# Deterministic-chain gate (measured 2026-09-23 on the real LinkedIn library,
# 6 step-sized chains + a compound goal): every candidate is scored as the
# NEXT STEP ("The next step toward the goal is to <capability>."); the argmax
# routes only when it is plausible AND clearly better than the runner-up.
# Correct picks measured 0.625-0.81 with margins 0.13-0.58; the tiny model's
# NOISE band sits at or below ~0.51 (measured 2026-09-24: a wrong 'messages'
# read scored 0.506 for a home-button directive and ran because the floor was
# 0.5).  A miss costs one brain round; a false positive runs the wrong chain —
# bias strict.
_CHAIN_GATE_MIN = 0.6
_CHAIN_GATE_MARGIN = 0.10

# Learned-artifact lifecycle (measured 2026-09-25: the routing counters were
# written as 0 at freeze time and never updated — no usage bookkeeping, no
# decay, bad artifacts stayed matchable for the life of the install).  An
# artifact with this many runs and a losing record retires in place.
_LEARNED_EVICT_MIN_RUNS = 3
_LEARNED_EVICT_WIN_RATE = 0.5


class OrchestratorMixin:
    """Goal loop over mini-brain chains (see module docstring)."""

    # ------------------------------------------------- LLM node 'orchestrator'
    # Additive bridge: an LLM node whose ``mode`` is ``orchestrator`` runs this
    # same goal loop over the chains wired to its ``tools`` port.  Those chains
    # are treated as ONE candidate set (plain chains); the separate
    # Orchestrator node is untouched.

    @staticmethod
    def _llm_node_is_orchestrator(node):
        """True when this LLM node has the orchestrator switch ON."""
        data = node.get('data', {}) or {}
        if bool(data.get('orchestrator_mode')):
            return True
        if bool((data.get('llm_configuration') or {}).get('orchestrator_mode')):
            return True
        # Legacy dropdown value still honoured.
        mode = data.get('mode')
        if mode is None:
            mode = (data.get('llm_configuration') or {}).get('mode')
        return str(mode or 'chat').strip().lower() == 'orchestrator'

    def _is_orchestrator_like(self, node):
        """A real Orchestrator node OR an LLM node with the switch ON."""
        if str(node.get('type') or '') == 'orchestrator':
            return True
        return str(node.get('type') or '') == 'llm' and self._llm_node_is_orchestrator(node)

    def _orchestrator_from_llm_node(self, node):
        """A synthetic orchestrator node for an LLM node in orchestrator mode.

        The LLM node's wired tools (its candidate chains) are re-tagged as the
        loop's ``chains`` input — one candidate set, treated as plain chains.
        Nothing else about the node changes; the loop is reused unchanged.
        """
        data = node.get('data', {}) or {}
        cfg = dict(data.get('llm_configuration') or {})
        cfg.update({k: v for k, v in data.items() if k != 'llm_configuration'})
        node_id = node.get('id') or node.get('node_id') or data.get('node_id')
        inputs = []
        for inp in (node.get('inputs') or []):
            if not isinstance(inp, dict):
                continue
            if str(inp.get('input_port') or '') in ('tools', 'chains', 'brains'):
                merged = dict(inp)
                merged['input_port'] = 'chains'
                inputs.append(merged)
        orch_data = {
            'node_id': node_id,
            'description': cfg.get('description') or cfg.get('label') or '',
            # Fixed goal: the node's 'Goal' field, used when nothing is wired to
            # the 'prompt' port (the port WINS when both are present).
            'goal': cfg.get('orch_goal') or cfg.get('goal') or '',
            'max_steps': cfg.get('orch_max_steps') or data.get('orch_max_steps') or _DEFAULT_MAX_STEPS,
            'picker': 'laya',  # switch ON = Laya, no choice
            'engine': cfg.get('engine') or '',
            'model': cfg.get('model') or '',
            'synthesize': cfg.get('orch_synthesize', True),
            'synthesis_system': cfg.get('orch_synthesis_system') or '',
            'use_goal_ledger': cfg.get('orch_use_goal_ledger', False),
        }
        return {
            'type': 'orchestrator',
            'id': node_id,
            'data': orch_data,
            'inputs': inputs,
            'connections': node.get('connections') or {},
        }

    # ------------------------------------------------------------------ entry

    def _execute_orchestrator_node(self, node, stop_flag):
        """Run the orchestrator loop; returns the next node id (or None/__done__)."""
        node_data = node.get('data', {}) or {}
        node_id = (
            node.get('id') or node.get('node_id')
            or node_data.get('node_id') or node_data.get('id')
        )
        try:
            max_steps = int(node_data.get('max_steps', _DEFAULT_MAX_STEPS) or _DEFAULT_MAX_STEPS)
        except Exception:
            max_steps = _DEFAULT_MAX_STEPS
        picker = str(node_data.get('picker') or 'llm').strip().lower()
        synthesize = str(node_data.get('synthesize', 'true')).strip().lower() in (
            'true', '1', 'yes', 'on',
        )

        goal = self._orchestrator_resolve_goal(node)
        # Long-term goals (Phase 6): optional wake-driven ledger when the
        # input carries no goal — the ledger supplies the active one.
        _use_ledger = str(node_data.get('use_goal_ledger', False)).strip().lower() in (
            'true', '1', 'yes', 'on',
        )
        _ledger_goal = None
        if _use_ledger:
            try:
                from ....agentic_ops import goal_ledger as _ledger
                if not (goal and str(goal).strip()):
                    _ledger_goal = _ledger.get_active_goal()
                    if _ledger_goal:
                        goal = _ledger_goal.get('text') or ''
                        logger.info(
                            "[ORCH] Node %s: using ledger goal %s (%s)",
                            node_id, _ledger_goal.get('id'), (goal or '')[:80],
                        )
                elif not _ledger.get_active_goal():
                    _ledger_goal = _ledger.start_goal(str(goal))
            except Exception as e:
                logger.warning("[ORCH] goal ledger unavailable: %s", e)

        brains = self._orchestrator_collect_brains(node)
        chains = self._orchestrator_collect_chains(node)
        # The LIBRARY classifies its own vocabulary (see
        # _orchestrator_library_generic): which words name things and which are
        # generics comes from the wired chains, never from a fixed list.
        _generic = _orchestrator_library_generic(
            [c.get('signal') for c in chains])

        logger.info(
            "[ORCH] Node %s start: goal=%r brains=%d chains=%d max_steps=%d picker=%s",
            node_id, (goal or '')[:120], len(brains), len(chains), max_steps, picker,
        )
        # Shadow (plan Phase 2): render every wired worker's DERIVED action
        # graph into the log — observability only, nothing routes on it yet.
        self._orchestrator_log_action_graph(brains, chains)
        if not brains and not chains:
            logger.error(
                "[ORCH] Node %s has no mini brains wired to its 'brains' port "
                "and no deterministic chains on its 'chains' port — "
                "failing node", node_id,
            )
            return None
        if not goal or not str(goal).strip():
            logger.error(
                "[ORCH] Node %s received an empty goal — failing node "
                "(wire an Input/Code/Conditional source into its input port)",
                node_id,
            )
            return None

        goal = str(goal).strip()
        # Relay detection must happen BEFORE the loop: the loop overwrites
        # ``_chain_input_context`` with this node's own composed prompt.
        # EITHER scaffolding marker counts (see _ORCH_RELAY_MARKERS): the
        # composer writes 'Your mission:' while this check once read only the
        # legacy marker, so every nested brain was judged a TOP-LEVEL run, its
        # handback marker was suppressed, and a sub-run that did NOTHING
        # (no_worker, ZERO steps) was believed finished — the parent closed
        # 'plan complete' over an empty sub-run (measured 2026-09-25).
        _relayed = False
        try:
            _ctx = str(self.llm_executor.get_variable("_chain_input_context") or '')
            _relayed = any(m in _ctx for m in self._ORCH_RELAY_MARKERS)
        except Exception:
            _relayed = False
        # The propagation packet's ROOT: at top level this IS the goal; a
        # relayed run inherits the ancestor's root (chain_ops copies the
        # variable into every imported chain).  Republished on every
        # activation — a top-level rerun in the same executor must not
        # inherit the previous run's root.
        try:
            _inherited_root = (
                str(self.llm_executor.get_variable("_chain_root_goal") or '').strip()
                if _relayed else '')
        except Exception:
            _inherited_root = ''
        _root_goal = _inherited_root or goal
        try:
            self.llm_executor.set_variable(
                "_chain_root_goal", str(_root_goal)[:400])
        except Exception:
            pass
        trace = []
        last_signature = None
        stop_reason = 'cap'
        # Fresh activation: clear the previous run's verdict marks.
        for _flag in ('_orchestrator_blocked', '_orchestrator_fulfilled'):
            try:
                node_data.pop(_flag, None)
                node.pop(_flag, None)
            except Exception:
                pass

        # ------------------------------------------------------- directives
        # Every level used to re-ask the WHOLE compound request at every step,
        # so a step-sized chain was judged against a sentence that needs
        # several actions (measured 2026-09-24: the gate's best candidate for
        # "open linkedin.com and click on my messages" was 'my network' at
        # 0.414 — under the floor, so the loop fell back to a fat brain and did
        # something else entirely).  The ladder is checked in the loop: first
        # "can ONE chain do the whole job", then — only if not — this split.
        directives = None  # planned lazily: only when no chain does the whole job
        _plan_is_split = False  # does the plan DEFINE completion? (see below)
        _dir_idx = 0
        _dir_start = 0  # trace index where the current directive began

        # ------------------------------------------------------------- loop
        _last_step_pair = ('', '')  # (normalized step, brain_id) of the last invocation
        _used_direct = set()        # chain_ids dispatched in THIS activation
        _whole_job_checked = False  # the whole-job gate runs once per activation
        _assembly_tried = False     # the variation assembly runs once per activation
        _tried_brain_spans = set()  # (brain_id, directive index) that came back empty
        _reported_remaining = False  # a brain REPORTED unaccomplished work
        for step in range(1, max_steps + 1):
            if stop_flag and stop_flag():
                logger.info("[ORCH] Node %s stopped by user at step %d", node_id, step)
                return None

            # ------------------------------------------------ observe the world
            # One read per iteration, shared by the planner, the gate, the
            # picker, the scoper and the verifier: the model gets GOAL + what
            # was done + what the world looks like now, so it can confirm
            # instead of assume.  '' when nothing is observable (read-only,
            # bounded).
            state = self._orchestrator_observe_state()
            if not str(state or '').strip() and _relayed:
                # A nested run can observe NOTHING (the foreground is the
                # player window, the web engine is not this chain's).  The
                # parent's digest — also observed, one level up — fills the
                # gap; a fresh observation always wins.
                try:
                    _inherited = str(
                        self.llm_executor.get_variable("_chain_state") or ''
                    ).strip()
                except Exception:
                    _inherited = ''
                if _inherited:
                    state = "Parent-observed: " + _inherited[:600]
            # Logged because the digest is prompt input with no other trace:
            # after a plan that names things the request never mentioned, 'where
            # did that come from' is unanswerable without this line (measured
            # 2026-09-25: the plan carried 'Feed'/'LinkedIn' that appear in no
            # request and no earlier log line — the foreground-window title in
            # this digest is the only unlogged prompt input).
            logger.info(
                "[ORCH] Node %s step %d observed state: %s",
                node_id, step,
                state.replace('\n', ' | ')[:400] if state else '(nothing observable)',
            )

            # ------------------------------------- the learned chain for it
            # A learned artifact whose content words ARE the request's runs as
            # the whole answer: deterministic match (see the rung), no planning
            # turn, no picker.  Atomic chains are never candidates here, and a
            # request covering only PART of a learned chain (or more than it)
            # never triggers it — over/under-execution falls to the ladder.
            if chains and not _whole_job_checked:
                _whole_job_checked = True
                _whole = self._orchestrator_route_whole_job(goal, chains)
                if _whole is not None:
                    _used_direct.add(_whole['chain_id'])
                    _entry = self._orchestrator_run_direct_chain(
                        goal, _whole, step, stop_flag, state)
                    if stop_flag and stop_flag():
                        return None
                    self._orchestrator_bump_learned_stats(
                        _whole, bool(_entry and _entry.get('ok') is True))
                    if _entry is not None:
                        trace.append(_entry)
                        if _entry.get('ok') is not False:
                            stop_reason = 'done'
                            break
                    logger.warning(
                        "[ORCH] Node %s: whole-job chain %s ran but failed "
                        "verification — splitting the goal into directives",
                        node_id, _whole['name'],
                    )

            # ------------------------------------------------ split the goal
            # One planning turn: the single actions the goal is made of.  The
            # unit of routing is the DIRECTIVE, never the whole request — its
            # own steps are its progress, and a single-action premise is what
            # the step models are calibrated on (measured 2026-09-24: a
            # compound premise scored its best candidate 0.414 — 'my network'
            # for an "open linkedin and click messages" request).
            if directives is None:
                directives = self._orchestrator_plan_directives(
                    goal, node_data, stop_flag, _generic, state=state)
                # The plan names ACTIONS, never workers — a 'name:' prefix a
                # weak planner still emits is stripped before anything routes
                # on it (the worker list it once copied from is gone; the
                # poison it produced may outlive it).
                directives = self._orchestrator_strip_worker_prefixes(
                    directives, list(brains) + list(chains))
                # A plan DEFINES completion only when it is a real
                # decomposition: several directives, or a single one that is
                # not an echo of the goal.  A verbatim echo is the planner's
                # "already one action" / "could not split" answer (and the
                # no-planner fallback) — a multi-step goal such as "find the
                # cheapest monitor" must keep looping past its first verified
                # step, so for those the picker / done-probe owns the end of
                # the run exactly as before.
                _plan_is_split = not (
                    len(directives) == 1
                    and _orchestrator_directive_key(directives[0], _generic)
                    == _orchestrator_directive_key(goal, _generic)
                )
                logger.info(
                    "[ORCH] Node %s: goal split into %d directive(s): %s",
                    node_id, len(directives),
                    ' | '.join(f"{i + 1}) {d[:60]}"
                              for i, d in enumerate(directives)),
                )
                # Shadow (plan Phase 3): project the plan onto the wired
                # workers' DERIVED action lines — measurement only.
                self._orchestrator_log_plan_projection(
                    directives, brains, chains, state, _generic)
            # -------------------------------------------------- plan complete
            # Every directive was executed and VERIFIED — the strongest
            # completion signal this level has.  Falling back to the whole
            # goal here let the picker/scoper invent work on an already
            # achieved goal (measured 2026-09-24: both LinkedIn directives
            # verified, the done-probe read 0.394 on a steps-only premise, and
            # the scoper fabricated "select 'Send a Message'" for jobsearch,
            # which ran and looped until ESC).  The plan IS the goal
            # decomposition — covering it covers the goal.  An unverified
            # directive never advances the cursor, so nothing is skipped
            # through this door.
            if _dir_idx < len(directives):
                directive = directives[_dir_idx]
                dir_trace = trace[_dir_start:]
            elif _plan_is_split:
                stop_reason = 'done'
                logger.info(
                    "[ORCH] Node %s: plan complete — all %d directive(s) "
                    "executed and verified",
                    node_id, len(directives),
                )
                break
            else:
                # Unsplit plan (its single directive is the whole goal): one
                # verified step does not complete it — the picker / done-probe
                # owns the end of the run, the pre-split behavior.
                directive = goal
                dir_trace = trace

            # ------------------------------------------- assemble a variation
            # No single artifact fits, but its PARTS can: pick one unit per
            # consecutive run of directives (learned artifacts first, then
            # their step files, then atoms), assemble them into ONE continuous
            # chain and run it as the whole answer.  A verified assembly is
            # renamed + wired as the new learned artifact for this variation —
            # learned chains made of learned chains.  Deterministic: the
            # scorer is never consulted, and an ambiguous plan declines.
            #
            # The plan must CONTAIN an artifact: that is the reuse this rung
            # exists for.  A plain sequence of atomic steps is left to the
            # per-directive gate — that path verifies every step separately
            # and already freezes the variation at the end of the run, so
            # assembling it here would only coarsen the verification for no
            # gain (measured margins on the real library stay green there).
            if chains and not _assembly_tried:
                _assembly_tried = True
                _units = self._orchestrator_plan_units(directives, chains)
                if (_units and len(_units) >= 2
                        and any(u['kind'] == 'artifact' for u in _units)):
                    _entry = self._orchestrator_assemble_variation(
                        goal, _units, node, step, stop_flag, state)
                    if stop_flag and stop_flag():
                        return None
                    if _entry is not None:
                        trace.append(_entry)
                        if _entry.get('ok') is not False:
                            stop_reason = 'done'
                            break

            # ---------------------------------------- step-sized chains
            # Orchestrator-free chains on the 'chains' port are the STEP-SIZED
            # tool set: every iteration offers the UNUSED ones to the Laya gate
            # (no picker, no scoping, no repeats), judged as the NEXT STEP of
            # the current directive.  A step served this way costs one Laya
            # read set instead of a full brain run — the learned library this
            # whole design optimizes for.  A chain that ran but failed
            # verification is never retried; the brain loop takes over.
            if chains:
                _direct = self._orchestrator_route_chain_direct(
                    directive, chains, dir_trace, _used_direct, state=state)
                if _direct is not None:
                    _used_direct.add(_direct['chain_id'])
                    _entry = self._orchestrator_run_direct_chain(
                        directive, _direct, step, stop_flag, state)
                    if stop_flag and stop_flag():
                        return None
                    if _entry is not None:
                        trace.append(_entry)
                        if _entry.get('ok') is not False:
                            # A CHAIN IS NOT ONE ACTION: the cerebellum runs
                            # whole routines ("opens linkedin and clicks on
                            # messages") without per-action routing, so the
                            # span this chain's own description names advances
                            # the plan — computed, never assumed (see
                            # _orchestrator_chain_span; 1 for a step-sized
                            # chain).
                            _span = self._orchestrator_chain_span(
                                _direct, directives, _dir_idx, _generic)
                            _done_upto = min(
                                _dir_idx + _span, len(directives))
                            if _span > 1:
                                logger.info(
                                    "[ORCH] Node %s: %s serves %d "
                                    "consecutive step(s) of the plan — "
                                    "advancing %d -> %d",
                                    node_id, _direct['name'], _span,
                                    _dir_idx, _done_upto,
                                )
                            _dir_idx = _done_upto
                            _dir_start = len(trace)
                            _last_step_pair = ('', '')
                            if _dir_idx < len(directives):
                                logger.info(
                                    "[ORCH] Node %s: directive %d/%d verified "
                                    "— next: %s", node_id, _dir_idx,
                                    len(directives), directives[_dir_idx][:100],
                                )
                            continue  # step done — replan the rest
                        logger.warning(
                            "[ORCH] Node %s: deterministic chain %s ran but "
                            "failed verification — handing this step to the "
                            "brain loop", node_id, _direct['name'],
                        )

            if not brains:
                # Chains-only level — and that is a legitimate shape, not a
                # crippled one: brains are OPTIONAL (dynamic processing), the
                # chains port is the mechanical worker (the cerebellum), and a
                # level with chains alone does its whole job there.  Reaching
                # this point means the port is exhausted or this step matched
                # nothing.  With steps on the trace ask the done question
                # first — a goal served entirely by deterministic chains must
                # end 'done', because the freeze refuses any other stop reason
                # (this is exactly the all-chains path the library grows from).
                if self._orchestrator_goal_reached(directive, dir_trace):
                    logger.info(
                        "[ORCH] Node %s: goal achieved with the chains port "
                        "alone (%d step(s))", node_id, len(dir_trace),
                    )
                    stop_reason = 'done'
                else:
                    stop_reason = 'no_worker'
                    logger.warning(
                        "[ORCH] Node %s: this step is unserved at this level "
                        "(%r) — no chain on the port matched it and no brain "
                        "is wired to take it (brains are optional; chains are "
                        "the mechanical worker)",
                        node_id, str(directive)[:80],
                    )
                break

            trace_text = self._orchestrator_render_trace(dir_trace)
            # Re-evaluation after a handback: a brain that came back with work
            # unaccomplished for THIS span is not offered again until every
            # other route (other brains, then chains) has been tried.
            _pick_pool = [
                b for b in brains
                if (b['brain_id'], _dir_idx) not in _tried_brain_spans
            ] or brains
            pick = None
            if picker == 'laya':
                pick = self._orchestrator_laya_pick(
                    directive, trace_text, _pick_pool, dir_trace, state,
                    remaining_reported=_reported_remaining)
                if pick is None:
                    try:
                        from AI.laya_client import status as _laya_status
                        _why = _laya_status()
                    except Exception:
                        _why = 'unavailable'
                    if _why == 'loading':
                        logger.info(
                            "[ORCH] Laya engine is still loading the model — "
                            "using the LLM picker for this step"
                        )
                    else:
                        logger.info(
                            "[ORCH] Laya picker unavailable (%s) — falling back "
                            "to LLM picker", _why,
                        )
            if pick is None:
                pick = self._orchestrator_llm_pick(
                    directive, trace_text, _pick_pool, node_data, stop_flag,
                    trace=dir_trace, state=state,
                    remaining_reported=_reported_remaining,
                )

            if stop_flag and stop_flag():
                return None

            # ------------------------------------------------ route the pick
            if pick == 'done':
                stop_reason = 'done'
                logger.info("[ORCH] Node %s: picker says the goal is achieved (step %d)", node_id, step)
                break
            if pick == 'ask':
                asked = self._orchestrator_ask_user(node, directive, trace, node_data)
                if not asked:
                    stop_reason = 'blocked'
                    try:
                        node_data['_orchestrator_blocked'] = True
                        self.llm_executor.set_variable(f"node_{node_id}_blocked", True)
                        self.port_store.set_output(self.chain_id, node_id, 'blocked', True)
                    except Exception:
                        pass
                    logger.info("[ORCH] Node %s: no ask-user channel — goal parked as blocked", node_id)
                    break
                trace.append({
                    'n': len(trace) + 1,
                    'brain': 'user',
                    'brain_id': '',
                    'result': str(asked)[:_RESULT_EXCERPT_CHARS],
                })
                continue
            if not isinstance(pick, int) or not (0 <= pick < len(_pick_pool)):
                stop_reason = 'unparseable'
                logger.warning(
                    "[ORCH] Node %s: picker returned no usable choice (pick=%r) — stopping",
                    node_id, pick,
                )
                break

            brain = _pick_pool[pick]

            # ------------------------------------- hand off the SCOPE
            # A worker receives the tasks IT covers, never the whole request:
            # the mission handed over is the current directive (a SPECIALIST
            # brain serves one scoped step — its own router picks one tool per
            # dispatch, so a longer list would go under-served) or, for a
            # NESTED brain (itself an orchestrator, which re-plans a list), the
            # directives its DESCRIPTION announces it covers.  The original
            # request never rides along: a 40-page goal must not enter every
            # instance.
            delegated_span = 0
            mission = directive
            if brain.get('nested'):
                _remaining = (directives[_dir_idx:]
                              if _dir_idx < len(directives) else [goal])
                _group = self._orchestrator_scope_group(
                    pick, _remaining, _pick_pool, picker, trace_text, state)
                delegated_span = len(_group)
                mission = '\n'.join(
                    f"{i + 1}) {d}" for i, d in enumerate(_group))
                step_text = mission[:_RESULT_EXCERPT_CHARS]
                logger.info(
                    "[ORCH] Node %s step %d: delegating %d directive(s) of "
                    "%s's scope (%d left in the plan) | %s",
                    node_id, step, delegated_span, brain['name'],
                    len(_remaining), step_text.replace('\n', ' | ')[:200],
                )
            else:
                # -------------------------------------- scope the next step
                # The orchestrator does not name tools: it scopes the DIRECTIVE
                # into ONE step for this worker; the worker's own router then
                # chooses whichever of its tools serves that step (USE_TOOL).
                step_text = self._orchestrator_scope_step(
                    directive, dir_trace, brain, node_data, stop_flag, state,
                )
                # Scope integrity, for COMPOUND directives only: the planner
                # normally makes every directive one action, but a directive
                # that still carries several actions must not have one silently
                # dropped by the scope (measured 2026-09-24: "Open LinkedIn.com
                # and click on your network." for a three-action directive —
                # 'home' vanished, a job-search brain ran).  A single-action
                # directive is NOT checked here: paraphrasing is the scoper's
                # job ("home button" -> "the house icon"), and
                # _orchestrator_looks_compound — which never cuts anything — is
                # only the plausibility test this guard keys on (a directive the
                # planner emitted as one action is not policed).
                if step_text and _orchestrator_looks_compound(directive):
                    _missing = (_orchestrator_directive_key(directive, _generic)
                                - _orchestrator_directive_key(step_text,
                                                              _generic))
                    if _missing:
                        stop_reason = 'unserved'
                        logger.warning(
                            "[ORCH] Node %s: scope dropped %s from the "
                            "directive (%r -> %r) — refusing the dispatch, "
                            "handing it back",
                            node_id, sorted(_missing), directive[:80],
                            step_text[:80],
                        )
                        break

            # ---------------------------------- repeated-step stall guard
            # A byte-identical (worker, step) pair repeating right after
            # itself means the scoping turn had nothing new to say for this
            # worker — re-running the same action burns a full brain for
            # nothing (measured 2026-09-23: 3 identical LinkedIn re-runs,
            # ~90 s each, until the picker finally rotated to vision).
            # Stop honestly instead; synthesis reports what remains.
            # ponytail: first repeat is refused — loosen to a counter if a
            # legit workflow ever needs consecutive identical steps.
            _norm_step = ' '.join(str(step_text or '').split()).lower()
            if (_norm_step and _norm_step == _last_step_pair[0]
                    and _last_step_pair[1] == brain['brain_id']):
                stop_reason = 'no_progress'
                logger.warning(
                    "[ORCH] Node %s: same worker + identical step repeated "
                    "(%s | %s) — stopping instead of re-running it",
                    node_id, brain['name'], _norm_step[:80],
                )
                break
            _last_step_pair = (_norm_step, brain['brain_id'])

            # --------------------------------------------- invoke the worker
            prompt = self._orchestrator_compose_prompt(
                mission, dir_trace, brain, step_text)
            try:
                self.llm_executor.set_variable("_chain_input_context", prompt)
                # Structured scoping context for the worker's Input nodes
                # (input_ops._reason_input_via_llm): a clean scope + step is
                # the tiny task the extractor needs — the raw composed prompt
                # made small models paste question chunks.  The MISSION is the
                # scope this worker covers; the original request is not part
                # of what it receives.
                self.llm_executor.set_variable("_chain_goal", str(mission or ''))
                self.llm_executor.set_variable("_chain_step", str(step_text or ''))
                # Propagation packet: the plan cursor and the parent's
                # observed state travel beside the pair (chain_ops copies
                # all of them into imported chains).  The root goal is
                # published once per activation, above.
                try:
                    if directives:
                        self.llm_executor.set_variable(
                            "_chain_plan",
                            json.dumps({
                                'index': int(_dir_idx),
                                'total': len(directives),
                                'remaining': [str(d)[:120]
                                              for d in directives[_dir_idx:]][:8],
                            }, ensure_ascii=False))
                except Exception:
                    pass
                if state:
                    self.llm_executor.set_variable(
                        "_chain_state", str(state)[:400])
            except Exception:
                pass
            logger.info(
                "[ORCH] Node %s step %d: invoking brain %s (%s, type=%s) | %s",
                node_id, step, brain['name'], brain['chain_file'],
                (brain.get('node') or {}).get('type'),
                (step_text or '(no scoped step - description task)')[:120],
            )
            self._orchestrator_invoke_brain(brain, stop_flag)
            if stop_flag and stop_flag():
                return None
            result = ""
            try:
                result = self._orchestrator_collect_brain_result(brain)
            except Exception as e:
                logger.warning("[ORCH] Result collection failed for %s: %s", brain['name'], e)
                result = ""
            # Per-step verification (one Laya forward when the engine is up):
            # did this step do what it was asked?  Recorded on the trace; the
            # routing gates that consume it (chains port / freeze) land later.
            # A step the node itself REPORTED as failed needs no scorer — the
            # verdict is False by construction (and no Laya load is paid).
            if brain.get('_ok') is False:
                verdict = False
                logger.info(
                    "[ORCH] Node %s step %d: %s reported failure — ok=False",
                    node_id, step, brain['name'],
                )
            else:
                verdict = self._orchestrator_verify_step(
                    directive, step_text, result, allow_start=(picker == 'laya'),
                    state=state,
                )
                if verdict is not None:
                    logger.info(
                        "[ORCH] Node %s step %d: verify verdict=%s (%s)",
                        node_id, step, verdict, brain['name'],
                    )
            log_block(
                logger, logging.INFO,
                f"Orchestrator step {step} → {brain['name']}",
                f"scoped step: {step_text or '(description task)'}\n\n{result}",
            )

            _entry = {
                'n': len(trace) + 1,
                'brain': brain['name'],
                'brain_id': brain['brain_id'],
                'step': (step_text or '')[:_RESULT_EXCERPT_CHARS],
                'result': self._orchestrator_clean_result(result)[:_RESULT_EXCERPT_CHARS],
                # Laya per-step verdict: True / False / None (engine down).
                'ok': verdict,
            }
            if brain.get('_outcome'):
                # The child's structured outcome (steps + stop reason),
                # stripped from the prose by _orchestrator_split_outcome.
                _entry['outcome'] = brain.get('_outcome')
            trace.append(_entry)

            # A verified step completes the directive it served: move on (a
            # DELEGATED list completes the whole remaining span — that list WAS
            # the step).  A False or unknown verdict keeps the directive and
            # the parent re-evaluates; the no-progress guard bounds that.  The
            # end of the run is NOT decided here — the picker / done-probe owns
            # that (an unsplit single directive must keep the old behavior).
            if verdict is True and not (delegated_span
                                        and brain.get('_remaining')):
                _dir_idx = len(directives) if delegated_span else _dir_idx + 1
                _dir_start = len(trace)
                _last_step_pair = ('', '')
                last_signature = None
                _tried_brain_spans = {t for t in _tried_brain_spans
                                      if t[1] >= _dir_idx}
                if _dir_idx < len(directives):
                    logger.info(
                        "[ORCH] Node %s: directive %d/%d verified — next: %s",
                        node_id, _dir_idx, len(directives),
                        directives[_dir_idx][:100],
                    )
            elif delegated_span:
                _tried_brain_spans.add((brain['brain_id'], _dir_idx))
                if (not brain.get('_remaining')
                        and brain.get('_ok') is not False):
                    # NO marker + no reported failure: the sub-run ended 'done'
                    # (the marker is emitted ONLY by a relayed run that did NOT
                    # finish), so the whole delegated span is accomplished —
                    # advance structurally instead of trusting the probe, which
                    # otherwise re-dispatched finished work (2026-09-24: the
                    # parent had no completion signal of its own).
                    _dir_idx = len(directives)
                    _dir_start = len(trace)
                    last_signature = None
                    _tried_brain_spans = {t for t in _tried_brain_spans
                                          if t[1] >= _dir_idx}
                    logger.info(
                        "[ORCH] Node %s: brain %s finished its delegated "
                        "list (nothing reported remaining) — span complete",
                        node_id, brain['name'],
                    )
                else:
                    # Finer-grained handback: advance past the prefix this
                    # brain DID accomplish and keep only the untouched tail
                    # for re-routing.
                    _new_idx, _left = self._orchestrator_apply_handback(
                        directives, _dir_idx, brain.get('_remaining'), goal)
                    if _new_idx != _dir_idx:
                        logger.info(
                            "[ORCH] Node %s: brain %s accomplished "
                            "directive(s) %d-%d — re-routing only what is left",
                            node_id, brain['name'], _dir_idx + 1, _new_idx,
                        )
                        _dir_idx = _new_idx
                        _dir_start = len(trace)
                        _tried_brain_spans = {t for t in _tried_brain_spans
                                              if t[1] >= _dir_idx}
                    if _left:
                        # Machine-readable truth from the sub-run: this tail is
                        # NOT done.  Two consequences — the brain is not
                        # re-offered for that span before the other workers had
                        # their turn (measured 2026-09-25: it was re-picked for
                        # the very next directive and stalled again), and the
                        # done-probe may not close the run over it (structure
                        # beats the probe: the probe read 0.587 over five
                        # reported-unaccomplished directives).
                        for _i in range(_dir_idx, len(directives)):
                            _tried_brain_spans.add((brain['brain_id'], _i))
                        _reported_remaining = True
                    logger.info(
                        "[ORCH] Node %s: brain %s came back with %d "
                        "directive(s) unaccomplished — handed back for "
                        "re-evaluation: %s",
                        node_id, brain['name'], len(_left),
                        ' | '.join(d[:50] for d in _left),
                    )

            # -------------------------------------------- no-progress guard
            # Same brain + same scoped step + identical result twice = no
            # progress; a different step for the same brain is a new attempt.
            signature = hashlib.md5(
                (str(brain['brain_id']) + '|' + (step_text or '')
                 + '|' + result[:1000]).encode('utf-8', 'replace')
            ).hexdigest()
            if signature == last_signature:
                stop_reason = 'no_progress'
                logger.warning(
                    "[ORCH] Node %s: no progress (same worker + identical result twice) — stopping",
                    node_id,
                )
                break
            last_signature = signature
        else:
            stop_reason = 'cap'
            logger.info("[ORCH] Node %s: step cap (%d) reached", node_id, max_steps)

        # -------------------------------------------------------- final output
        # Machine-readable handback for a PARENT orchestrator: what this level
        # could NOT accomplish.  Only a relayed run (invoked by a parent, i.e.
        # its goal arrived as the parent's composed prompt) carries the marker
        # — a top-level answer is never decorated with it.
        # A wired 'route' port owns the retry: the canvas's next orchestrator
        # takes the unfulfilled remainder, so the parent-handback marker stands
        # down (both would re-route the same tail).
        _route_wired = False
        try:
            _conns = node.get('connections') or {}
            if isinstance(_conns, dict):
                _route_wired = bool(_conns.get('route'))
            elif isinstance(_conns, list):
                _route_wired = any(
                    str(c.get('output_port') or '').lower() == 'route'
                    for c in _conns if isinstance(c, dict)
                )
        except Exception:
            _route_wired = False

        _handback = []
        if _relayed and stop_reason != 'done' and not _route_wired:
            if _dir_idx < len(directives or []):
                _handback = [str(d) for d in directives[_dir_idx:]]
            else:
                _handback = [goal]

        final_text = ""
        if synthesize and trace:
            # The honest ending rides into the synthesis: for any non-'done'
            # stop, the unfinished directive tail (or the relayed handback) is
            # REPORTED — the model must not narrate the goal list as done work
            # (measured 2026-09-25: the confabulated 'logged into your
            # account' summary came from exactly this gap).
            _unfinished = []
            if stop_reason != 'done':
                if directives and _dir_idx < len(directives):
                    _unfinished = [str(d) for d in directives[_dir_idx:]]
                elif _handback:
                    _unfinished = list(_handback)
            final_text = self._orchestrator_synthesize(
                goal, trace, node_data, stop_flag, remaining=_unfinished,
            ) or ""
        if not final_text:
            final_text = self._orchestrator_render_trace(trace)
            if not final_text:
                final_text = "The goal could not be advanced."
        if _handback:
            try:
                final_text += (f"\n{_ORCH_REMAINING_MARKER} "
                               + json.dumps(_handback, ensure_ascii=False))
            except Exception:
                pass
            # The structured outcome rides WITH the handback (same condition:
            # relayed runs without a wired route port): the child's own step
            # list and stop reason, so the parent records what actually ran
            # instead of a collapsed prose blob.  Observability only — the
            # parent's cursor/verdict logic never reads it.
            try:
                final_text += (f"\n{_ORCH_OUTCOME_MARKER} " + json.dumps({
                    'stop_reason': stop_reason,
                    'steps': [
                        {'step': str(t.get('step') or '')[:120],
                         'ok': t.get('ok')}
                        for t in (trace or [])[-8:]
                    ],
                    'observed': str(state)[:200] if state else '',
                }, ensure_ascii=False))
            except Exception:
                pass

        try:
            self.port_store.set_output(self.chain_id, node_id, 'output', final_text)
            self.llm_executor.set_variable(f"node_{node_id}_output", final_text)
            trace_json = json.dumps(trace, ensure_ascii=False)
            self.port_store.set_output(self.chain_id, node_id, 'trace', trace_json)
            self.llm_executor.set_variable(f"node_{node_id}_trace", trace_json)
            _blocked_now = bool(node_data.get('_orchestrator_blocked'))
            self.port_store.set_output(self.chain_id, node_id, 'blocked', _blocked_now)
            self.llm_executor.set_variable(f"node_{node_id}_blocked", _blocked_now)
            # Failure verdict for the 'route' branch: 'done' is the only
            # achieved ending; the unfulfilled goal rides on the port so a
            # wired next orchestrator resolves it as its upstream input.
            _fulfilled_now = (stop_reason == 'done')
            node['_orchestrator_fulfilled'] = _fulfilled_now
            node_data['_orchestrator_fulfilled'] = _fulfilled_now
            _route_value = '' if _fulfilled_now else str(goal)
            self.port_store.set_output(self.chain_id, node_id, 'route', _route_value)
            self.llm_executor.set_variable(f"node_{node_id}_route", _route_value)
        except Exception as e:
            logger.warning("[ORCH] Failed to publish outputs for %s: %s", node_id, e)

        logger.info(
            "[ORCH] Node %s finished: steps=%d stop_reason=%s blocked=%s",
            node_id, len(trace), stop_reason,
            bool(node_data.get('_orchestrator_blocked')),
        )
        # Persist the activation outcome into the goal ledger (Phase 6).
        if _ledger_goal and _ledger_goal.get('id'):
            try:
                from ....agentic_ops import goal_ledger as _ledger
                _status = {
                    'done': 'done',
                    'blocked': 'blocked',
                }.get(stop_reason, 'open')
                _ledger.record_activation(_ledger_goal.get('id'), trace, _status)
            except Exception as e:
                logger.warning("[ORCH] goal ledger write-back failed: %s", e)
        # Compile a verified successful run into a deterministic chain
        # (LOOPER_LEARN=off disables; see _orchestrator_freeze_learned_chain).
        try:
            self._orchestrator_freeze_learned_chain(
                goal, trace, brains, node, stop_reason, chains)
        except Exception as e:
            logger.warning("[ORCH] freeze step failed: %s", e)
        return self._get_input_next_node(node, 'output')

    # ------------------------------------------------------------- resolution

    def _orchestrator_resolve_goal(self, node):
        """Goal priority: 'prompt' port > the node's fixed goal > shared prompt.

        The 'prompt' port is the dynamic goal channel: a goal wired there WINS
        over the node's fixed 'Goal' field (the field is the fallback used when
        the port carries nothing).  The legacy 'input' edge and the shared
        ``_chain_input_context`` remain as last-resort fallbacks for relays and
        pre-prompt-port wiring.

        A NESTED orchestrator (inside a chain a parent orchestrator invoked)
        receives the parent's composed prompt as its input context
        ("Long-term goal: ...\nProgress so far: ...\nYour next step: X").
        Its LOCAL goal is X — the part the parent scoped for this sub-brain —
        never the whole relay (measured 2026-09-23: with the blob as the
        goal, the nested done-probe read the relay's own action text as
        completed work — p_done 0.916 with ZERO steps — and the sub-brain
        stopped instantly with "The goal could not be advanced.").
        """
        # 1) 'prompt' port — dynamic goal (wins over the fixed goal).
        try:
            goal = self._resolve_input_upstream_value(node, port='prompt')
        except Exception:
            goal = None
        # 2) The node's fixed goal.
        if goal is None or not str(goal).strip():
            try:
                goal = (node.get('data') or {}).get('goal') or ''
            except Exception:
                goal = ''
        # 3) Legacy 'input' edge (pre-prompt-port wiring).
        if goal is None or not str(goal).strip():
            try:
                goal = self._resolve_input_upstream_value(node)
            except Exception:
                goal = None
        # 4) The shared prompt channel (relay / nested run).
        if goal is None or not str(goal).strip():
            try:
                goal = self.llm_executor.get_variable("_chain_input_context") or ""
            except Exception:
                goal = ""
        # Relayed runs carry the CLEAN structured pair beside the prompt
        # (chain_ops copies it into this player): prefer it over parsing the
        # composed text.  The unwrap below stays the fallback for relays from
        # older builds, manual runs and non-orchestrator callers.
        try:
            if any(m in str(goal or '') for m in self._ORCH_RELAY_MARKERS):
                _pair_goal = str(
                    self.llm_executor.get_variable("_chain_goal") or ''
                ).strip()
                if _pair_goal:
                    return _pair_goal
        except Exception:
            pass
        return self._orchestrator_unwrap_local_goal(goal)

    # The scaffolding a composed worker prompt opens with — the marker that
    # tells a nested orchestrator its input is a relay, never a goal of its
    # own.  'Your mission:' is current; 'Long-term goal:' is kept readable so
    # relays composed by older builds still unwrap.
    _ORCH_RELAY_MARKERS = ('Your mission:', 'Long-term goal:')

    @staticmethod
    def _orchestrator_unwrap_local_goal(goal):
        """Local task when the input is a parent orchestrator's relay.

        Composed prompts carry the parent's scaffolding; the local goal is
        the step the parent scoped for this worker ("Your next step: X"),
        the scope it handed over ("Your mission: X"), or the description-task
        fallback ("Your task: X").  Text without the scaffolding passes
        through untouched.
        """
        text = str(goal or '')
        marker = next((m for m in OrchestratorMixin._ORCH_RELAY_MARKERS
                       if m in text), '')
        if not marker:
            return goal
        for stop in ('Your next step:', 'Your task:'):
            idx = text.rfind(stop)
            if idx != -1:
                local = text[idx + len(stop):].strip()
                if local:
                    return local
        idx = text.find(marker)
        local = text[idx + len(marker):]
        for stop in ('Progress so far:', 'Your next step:', 'Your task:'):
            cut = local.find(stop)
            if cut != -1:
                local = local[:cut]
        local = local.strip()
        return local or goal

    def _orchestrator_collect_brains(self, node):
        """Mini brains = nodes wired to this node's 'brains' port.

        Two kinds are accepted:
          * ``chain_import`` (kind='chain') — a sub-chain file, resolved and
            described at runtime exactly as before, or
          * ANY node flagged ``tool_provider`` (kind='node') — a single node
            dispatched directly as one step: zero file I/O, minimal derived
            description (the rich per-node description field is a later task).
        """
        brains = []
        seen = set()
        _node_brain_types = set(_BRAIN_EXECUTORS) | {'conditional'}
        for inp in (node.get('inputs') or []):
            if str(inp.get('input_port') or '') != 'brains':
                continue
            from_id = inp.get('from_node')
            if not from_id or from_id in seen:
                continue
            seen.add(from_id)
            brain_node = (self.workflow_graph or {}).get(from_id)
            ntype = str((brain_node or {}).get('type') or '')
            is_chain = ntype == 'chain_import'
            # chain_import is accepted unconditionally (hand-built graphs and
            # test harnesses predate the flag); every other type must be gated
            # as a router subroutine by the builder's generic post-pass.
            if not brain_node or (not is_chain and not brain_node.get('tool_provider')):
                logger.warning(
                    "[ORCH] brains port entry %s is not usable as a brain "
                    "(type=%s, tool_provider=%s) — skipped",
                    from_id, ntype, (brain_node or {}).get('tool_provider'),
                )
                continue
            if not is_chain and ntype not in _node_brain_types:
                logger.warning(
                    "[ORCH] brains port entry %s has unsupported node type %r — skipped",
                    from_id, ntype,
                )
                continue
            data = brain_node.get('data', {}) or {}
            nested = False
            if is_chain:
                chain_file = self._orchestrator_resolve_chain_path(data)
                name = (
                    data.get('label') or data.get('name')
                    or (os.path.splitext(os.path.basename(chain_file))[0] if chain_file else from_id)
                )
                tools = self._orchestrator_collect_brain_tools(chain_file)
                description = self._orchestrator_brain_description(
                    chain_file, name, tools,
                )
                # A brain that is ITSELF an orchestrator takes a task LIST and
                # re-runs this same ladder at its level (chains → its brains),
                # handing the unaccomplished work back up.
                nested = self._orchestrator_chain_is_nested(chain_file)
            else:
                chain_file = ''
                name = str(data.get('label') or data.get('name') or from_id)
                tools = []
                description = self._orchestrator_node_brain_description(
                    brain_node, name,
                )
            brains.append({
                'brain_id': from_id,
                'node': brain_node,
                'name': str(name),
                'chain_file': chain_file or '',
                'description': description,
                'kind': 'chain' if is_chain else 'node',
                # Nested orchestrator: the parent hands it a task LIST, not a
                # single scoped step (the recursion the architecture is built
                # on).  Specialist brains keep the one-step contract.
                'nested': nested,
                # Router brain tools ([] for plain chains): context for the
                # picker and the step scoper — the brain itself still routes.
                'tools': tools,
            })
        return brains

    @staticmethod
    def _orchestrator_chain_is_nested(chain_file):
        """Does this chain brain contain an orchestrator (transitively)?"""
        if not chain_file or not os.path.exists(chain_file):
            return False
        try:
            try:
                from ....chain_import_ports import chain_contains_orchestrator
            except ImportError:
                from player.chain_import_ports import chain_contains_orchestrator
            with open(chain_file, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            return bool(
                chain_contains_orchestrator(cfg, os.path.dirname(chain_file)))
        except Exception:
            return False

    # ------------------------------------------- deterministic chains (port)

    def _orchestrator_collect_chains(self, node):
        """Deterministic chains = chain_import nodes on this node's 'chains'.

        Re-validated at runtime with the SAME rule the GUI uses: a file that
        contains an orchestrator (directly or through its imports) can never
        run as a deterministic chain — warned and skipped; it belongs on the
        'brains' port.
        """
        chains = []
        seen = set()
        for inp in (node.get('inputs') or []):
            if str(inp.get('input_port') or '') != 'chains':
                continue
            from_id = inp.get('from_node')
            if not from_id or from_id in seen:
                continue
            seen.add(from_id)
            cnode = (self.workflow_graph or {}).get(from_id)
            if not cnode or str(cnode.get('type') or '') != 'chain_import':
                logger.warning(
                    "[ORCH] chains port entry %s is not a chain_import node "
                    "(%s) — skipped", from_id, (cnode or {}).get('type'),
                )
                continue
            data = cnode.get('data', {}) or {}
            chain_file = self._orchestrator_resolve_chain_path(data)
            if not chain_file or not os.path.exists(chain_file):
                logger.warning(
                    "[ORCH] chains port entry %s resolves to no file (%r) — "
                    "skipped", from_id, chain_file,
                )
                continue
            try:
                try:
                    from ....chain_import_ports import chain_contains_orchestrator
                except ImportError:
                    from player.chain_import_ports import chain_contains_orchestrator
                with open(chain_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                if chain_contains_orchestrator(cfg, os.path.dirname(chain_file)):
                    logger.warning(
                        "[ORCH] chains port entry %s (%s) needs inference "
                        "(contains an orchestrator) — skipped; wire it to "
                        "'brains' instead", from_id, os.path.basename(chain_file),
                    )
                    continue
            except Exception as e:
                logger.warning(
                    "[ORCH] chains port entry %s unreadable (%s) — skipped",
                    from_id, e,
                )
                continue
            name = (
                data.get('label') or data.get('name')
                or os.path.splitext(os.path.basename(chain_file))[0]
            )
            description = self._orchestrator_brain_description(
                chain_file, name, [])
            examples = self._orchestrator_read_chain_routing(chain_file)
            chains.append({
                'chain_id': from_id,
                'node': cnode,
                'kind': 'chain',
                'name': str(name),
                'chain_file': chain_file,
                'description': description,
                'examples': examples,
                # The text this chain is ROUTED by (and the corpus the library
                # profile is computed from).
                'signal': ' | '.join(examples) or str(description or ''),
                # A freeze artifact (learned: true) is a WHOLE-JOB candidate
                # only — it composes several steps and must never compete in
                # the per-step gate (measured 2026-09-24: it scored 0.756 on
                # the step question because its example IS the goal text).
                # Its COMPOSITION (the descriptions of the library chains it
                # is made of) rides along so the whole-job rung judges the
                # artifact by what it DOES, not by a frozen string.
                'learned': bool(cfg.get('learned')),
                # Lifecycle counters (see the eviction policy in the
                # whole-job rung): written as 0 at freeze time and bumped on
                # every whole-job execution.
                'runs': int((cfg.get('routing') or {}).get('runs') or 0),
                'wins': int((cfg.get('routing') or {}).get('wins') or 0),
                'retired': bool(cfg.get('retired')),
                'created_at': float(
                    (cfg.get('routing') or {}).get('created_at') or 0),
                'composition': '',
                'steps_keys': [],
                'steps_files': [],
                '_cfg': cfg,
            })
        # The LIBRARY classifies its own generic vocabulary FIRST, then every
        # key/expansion is computed against it — the corpus is the wired set.
        generic = _orchestrator_library_generic([c.get('signal')
                                                 for c in chains])
        for c in chains:
            cfg = c.pop('_cfg', None) or {}
            if c.get('learned'):
                try:
                    comp, keys, step_files = (
                        self._orchestrator_expand_learned_chain(
                            cfg, c['chain_file'], generic))
                    c['composition'] = comp
                    c['steps_keys'] = keys
                    c['steps_files'] = step_files
                except Exception as e:
                    logger.warning(
                        "[ORCH] could not expand learned chain %s: %s",
                        os.path.basename(c['chain_file']), e,
                    )
        return chains

    def _orchestrator_log_action_graph(self, brains, chains):
        """Shadow: log the DERIVED action graph of every wired worker.

        Plan Phase 2: the graph is rendered from what the executor actually
        reads (web-sequence actions, sequence names, handle goals, node
        labels) — never from hand-written descriptions — and nothing routes
        on it yet.  The per-activation block (coverage + rendered lines) is
        the measurement substrate for the planner projection that follows.
        ``LOOPER_ORCH_GRAPH=off`` silences it.
        """
        if str(os.environ.get("LOOPER_ORCH_GRAPH", "")).strip().lower() in (
                '0', 'off', 'false', 'no', 'disabled'):
            return
        try:
            from .action_graph import build_action_graph, graph_summary_lines
        except Exception:
            return
        lines = []
        seen = set()
        for item in list(brains) + list(chains):
            chain_file = str(item.get('chain_file') or '')
            if (not chain_file or chain_file in seen
                    or not os.path.exists(chain_file)):
                continue
            seen.add(chain_file)
            try:
                graph = build_action_graph(chain_file)
            except Exception as e:
                logger.debug("[ORCH] action graph failed for %s: %s", chain_file, e)
                continue
            if not graph:
                continue
            lines.append(f"{item.get('name') or os.path.basename(chain_file)}:")
            lines.extend(graph_summary_lines(graph))
        if lines:
            log_block(logger, logging.INFO, "Action graph (shadow)", '\n'.join(lines))

    def _orchestrator_log_plan_projection(self, directives, brains, chains,
                                          state, generic):
        """Shadow (plan Phase 3): project each planned directive onto the
        wired workers' DERIVED action lines.

        Measurement only — nothing routes on it.  For every directive the
        object words (the same stemmed vocabulary the picker names workers
        by, the step's verb and the ambient state words excluded) are
        intersected with each worker's rendered action lines (own entities
        plus one-level import children, see graph_candidate_lines); the block
        logs ``directive -> worker: line (shared: …) | UNMATCHED`` and a
        matched count, so the funnel — plan wording no wired action can
        serve — becomes a number instead of a post-mortem replay.
        ``LOOPER_ORCH_GRAPH=off`` silences it (same switch as the graph
        block).
        """
        if str(os.environ.get("LOOPER_ORCH_GRAPH", "")).strip().lower() in (
                '0', 'off', 'false', 'no', 'disabled'):
            return
        if not directives:
            return
        try:
            from .action_graph import build_action_graph, graph_candidate_lines
        except Exception:
            return
        ambient = (_orchestrator_directive_key(state, frozenset())
                   if state else set())
        workers = []
        seen = set()
        for item in list(brains) + list(chains):
            chain_file = str(item.get('chain_file') or '')
            if (not chain_file or chain_file in seen
                    or not os.path.exists(chain_file)):
                continue
            seen.add(chain_file)
            try:
                graph = build_action_graph(chain_file)
            except Exception:
                graph = None
            workers.append((
                str(item.get('name') or os.path.basename(chain_file)),
                graph_candidate_lines(graph),
            ))
        if not workers:
            return
        out = []
        unmatched = 0
        for directive in directives:
            obj = _orchestrator_directive_key(directive, generic) - ambient
            _w = str(directive or '').split()
            _verb = (_orchestrator_stem(_w[0].strip('\",.;:').lower())
                     if _w else '')
            obj = obj - {_verb}
            best = []
            for name, lines in workers:
                for line in lines:
                    shared = obj & _orchestrator_directive_key(line, generic)
                    if shared:
                        best.append((len(shared), name, line, shared))
            if best:
                best.sort(key=lambda t: (-t[0], t[1], t[2]))
                top = best[0]
                out.append(
                    f"- {str(directive)[:90]} -> {top[1]}: "
                    f"{str(top[2])[:80]} (shared: "
                    f"{', '.join(sorted(top[3])[:4])})")
            else:
                unmatched += 1
                out.append(f"- {str(directive)[:90]} -> UNMATCHED")
        out.append(
            f"(projection: {len(directives) - unmatched}/{len(directives)} "
            f"directive(s) matched at least one action line)")
        log_block(logger, logging.INFO, "Plan projection (shadow)",
                  '\n'.join(out))

    def _orchestrator_read_chain_routing(self, chain_path, limit=None):
        """Verbatim past requests that succeeded with this chain ([] on fail).

        The routing signal is the request text itself — an entailment model
        matches real phrasing better than any paraphrase.  The prose
        ``description`` stays for humans and the rating/repair loop.
        """
        limit = int(limit or _CHAIN_ROUTING_EXAMPLES)
        try:
            with open(chain_path, 'r', encoding='utf-8') as f:
                routing = (json.load(f) or {}).get('routing') or {}
            examples = routing.get('examples') or []
            if isinstance(examples, str):
                examples = [examples]
            out = []
            for ex in examples:
                s = ' '.join(str(ex or '').split())
                if s:
                    out.append(s[:200])
                if len(out) >= limit:
                    break
            return out
        except Exception:
            return []

    def _orchestrator_expand_learned_chain(self, cfg, chain_file, generic=frozenset()):
        """The COMPOSITION as the humans wrote it: ordered step name + description.

        A learned chain is not opaque: it is an ordered list of library chains,
        each with a description of what it does.  The artifact PERSISTS that
        list (``steps``, derived from the atomic descriptions at write time), so
        the expansion uses it directly when present — no file reads, and it
        survives the step files moving.  Older artifacts (and hand-written
        ones) fall back to reading each imported file.

        Returns (composition_text, [step_key_words...], [step_file...]); keys
        come from each step's description.  An unkeyable step is kept as [] so
        the caller can treat the whole composition as unverifiable instead of
        silently covering nothing.
        """
        persisted = chain_file if isinstance(chain_file, dict) else None
        steps = []
        keys = []
        step_files = []
        if persisted is None and isinstance(cfg, dict) and cfg.get('steps'):
            # The artifact itself carries the step descriptions.
            persisted = [s for s in (cfg.get('steps') or [])
                         if isinstance(s, dict)]
        if persisted is not None:
            for s in persisted:
                name = str(s.get('name') or '')
                desc = str(s.get('description') or '')
                label = desc or name or 'step'
                steps.append(
                    f"{len(steps) + 1}) {name} — {label}" if name and desc
                    else f"{len(steps) + 1}) {label}"
                )
                keys.append(sorted(_orchestrator_directive_key(label, generic)))
                step_files.append(str(s.get('file') or ''))
            return '; '.join(steps)[:600], keys, step_files
        base = os.path.dirname(chain_file or '')
        for n in (cfg.get('chain_import_nodes') or []):
            if not isinstance(n, dict):
                continue
            p = str(n.get('chain_file_path') or '')
            if p and not os.path.isabs(p):
                p = os.path.join(base, p)
            name = os.path.splitext(os.path.basename(p))[0] if p else ''
            desc = ''
            try:
                if p and os.path.exists(p):
                    with open(p, 'r', encoding='utf-8') as f:
                        sub = json.load(f)
                    desc = str((sub or {}).get('description') or '')
            except Exception:
                desc = ''
            label = desc or name or 'step'
            steps.append(
                f"{len(steps) + 1}) {name} — {label}" if name and desc
                else f"{len(steps) + 1}) {label}"
            )
            keys.append(sorted(_orchestrator_directive_key(label, generic)))
            step_files.append(p)
        return '; '.join(steps)[:600], keys, step_files

    def _orchestrator_learned_covers(self, goal_key, chain, generic=frozenset()):
        """Why this learned chain covers the request ('' when it does not).

        Two deterministic routes, both intent-based:
          * 'example' — the frozen past request IS this request (same content
            words whatever the wording/order/numbering), or
          * 'composition' — the request and the artifact's STEPS cover each
            other: every step's salient word is asked for (nothing would run
            unasked) and every asked word belongs to some step (nothing would
            be left undone).
        """
        for example in (chain.get('examples') or []):
            if _orchestrator_directive_key(example, generic) == goal_key:
                return 'example'
        step_keys = chain.get('steps_keys') or []
        if not step_keys or not all(step_keys):
            return ''
        union = set()
        for key in step_keys:
            if not (set(key) & goal_key):
                return ''          # a step the request never asks for
            union |= set(key)
        if goal_key - union:
            return ''              # an action no step serves
        return 'composition'

    def _orchestrator_learned_gap(self, goal_key, chain):
        """One line explaining why the composition does NOT cover the request."""
        step_keys = chain.get('steps_keys') or []
        if not step_keys:
            return 'no readable steps'
        union = set()
        for key in step_keys:
            union |= set(key)
        parts = []
        unasked = [str(k[0]) for k in step_keys if k and not (set(k) & goal_key)]
        if unasked:
            parts.append('steps nobody asked for: ' + ', '.join(unasked[:4]))
        missing = sorted(goal_key - union)
        if missing:
            parts.append('actions no step serves: ' + ', '.join(missing[:4]))
        return '; '.join(parts) or 'covered'

    def _orchestrator_source_hosts_orchestrator(self, node):
        """True when the source chain file actually CONTAINS this orchestrator.

        Artifacts (and the wires they need) belong next to the chain that HOSTS
        the orchestrator.  When the runtime id is not in the source file — a
        harness, a node recreated in the editor, an unresolvable copy — writing
        there would drop the composition into a chain that never ran it
        (measured 2026-09-23/24, twice: learned files and dangling imports
        leaked into the shipped ORCHESTRATOR.json).  An empty orchestrator list
        means the file cannot be judged and is accepted.
        """
        orch_id = _orchestrator_owner_id(node)
        source = self._orchestrator_source_chain_file()
        if not orch_id or not source:
            return False
        try:
            with open(source, 'r', encoding='utf-8') as f:
                host = json.load(f)
            host_ids = [
                str(n.get('node_id') or '')
                for n in (host.get('orchestrator_nodes') or [])
                if isinstance(n, dict)
            ]
        except Exception:
            host_ids = []
        return not host_ids or orch_id in host_ids

    def _orchestrator_plan_units(self, directives, chains):
        """One deterministic UNIT per consecutive run of directives (None = none).

        A unit is the coarsest thing that serves a run, learned artifacts
        first: a whole artifact (its composition covers the run exactly — the
        same coverage rule as the whole-job rung), else one of its STEP files,
        else an atomic chain on the port.  Runs are taken longest-first, so an
        artifact is reused as ONE unit whenever it fully serves a consecutive
        span — which is how an assembled variation ends up composed of learned
        chains and atoms side by side.

        Ambiguity is never a guess: more than one candidate for a run declines
        the whole assembly (the normal ladder handles it), as does any run no
        unit can serve, or a unit needed twice.
        """
        units = []
        seen_files = set()
        for c in chains:
            if c.get('learned'):
                keys = c.get('steps_keys') or []
                union = set()
                for k in keys:
                    union |= set(k)
                units.append({'kind': 'artifact', 'chain': c,
                              'file': c.get('chain_file') or '',
                              'name': c.get('name') or '', 'key': union})
                for f, k in zip(c.get('steps_files') or [], keys):
                    base = os.path.basename(f or '')
                    if f and k and base not in seen_files:
                        seen_files.add(base)
                        units.append({'kind': 'step', 'chain': c, 'file': f,
                                      'name': os.path.splitext(base)[0],
                                      'key': set(k)})
            else:
                f = c.get('chain_file') or ''
                base = os.path.basename(f)
                if f and base not in seen_files:
                    seen_files.add(base)
                    units.append({
                        'kind': 'step', 'chain': c, 'file': f,
                        'name': c.get('name') or '',
                        'key': _orchestrator_directive_key(
                            c.get('description') or c.get('name')),
                    })
        if not units or not directives:
            return None
        generic = _orchestrator_library_generic(
            [c.get('signal') for c in chains])
        plan = []
        used = set()
        i = 0
        while i < len(directives):
            chosen = None
            for j in range(len(directives), i, -1):
                run_key = _orchestrator_directive_key(
                    ' '.join(directives[i:j]), generic)
                if not run_key:
                    continue
                cands = []
                for u in units:
                    if u['file'] in used:
                        continue
                    if u['kind'] == 'artifact':
                        if self._orchestrator_learned_covers(run_key,
                                                             u['chain'],
                                                             generic):
                            cands.append(u)
                    elif run_key <= u['key']:
                        cands.append(u)
                if len(cands) == 1:
                    chosen = (j, cands[0])
                    break
                if len(cands) > 1:
                    continue   # ambiguous at this length — try shorter
            if chosen is None:
                return None
            i, unit = chosen
            used.add(unit['file'])
            plan.append(unit)
        return plan

    def _orchestrator_assemble_variation(self, goal, units, node, step,
                                         stop_flag, state):
        """Assemble units into ONE continuous chain, run it, keep it if it works.

        The variation is written as a PENDING artifact ('.pending_' — collides
        with no existing file), dispatched as the whole answer through the same
        learned-chain path, and only a verified run renames it into
        ``learned_…`` and wires it to the 'chains' port.  A failed run deletes
        the pending file and the normal ladder takes over; an unverified run
        (engine down) serves the request but is NOT persisted — the freeze and
        the artifact gates only ever trust verified compositions.
        """
        files = [(u['file'], u['name']) for u in units if u.get('file')]
        if len(files) < 2:
            return None
        # Same host invariant the freeze enforces: never write a composition
        # into a chain that does not contain this orchestrator.
        if not self._orchestrator_source_hosts_orchestrator(node):
            logger.info(
                "[ORCH] Assembly skipped: the source chain does not host "
                "orchestrator %r — the variation would land in the wrong file",
                _orchestrator_owner_id(node),
            )
            return None
        pending = self._orchestrator_write_learned_chain(
            goal, files, pending=True)
        if not pending:
            return None
        names = ' -> '.join(u['name'] for u in units)[:160]
        logger.info(
            "[ORCH] Assembly: %d unit(s) serving this request as ONE chain: %s",
            len(files), names,
        )
        unit_chain = {
            'chain_id': 'assembled:' + os.path.basename(pending),
            'node': {'type': 'chain_import',
                     'data': {'chain_file_path': pending,
                              'import_mode': 'full'}},
            'name': 'assembled: ' + names,
            'chain_file': pending,
            'description': 'variation assembled from ' + names,
            'kind': 'chain',
        }
        entry = self._orchestrator_run_direct_chain(
            goal, unit_chain, step, stop_flag, state)
        if (stop_flag and stop_flag()) or entry is None:
            try:
                os.remove(pending)
            except OSError:
                pass
            return None
        if entry.get('ok') is False:
            try:
                os.remove(pending)
            except OSError:
                pass
            logger.warning(
                "[ORCH] Assembly ran but FAILED verification — pending "
                "variation removed, falling back to the split ladder",
            )
            return None
        if entry.get('ok') is None:
            logger.info(
                "[ORCH] Assembly ran unverified (engine down) — variation "
                "served but not persisted",
            )
            return entry
        final = os.path.join(
            os.path.dirname(pending),
            os.path.basename(pending).replace('.pending_', 'learned_', 1),
        )
        try:
            os.replace(pending, final)
        except OSError as e:
            logger.warning("[ORCH] could not persist the variation: %s", e)
            return entry
        logger.info(
            "[LEARN] variation frozen: %s (%d unit(s)) — wiring it to the "
            "'chains' port", os.path.basename(final), len(files),
        )
        self._orchestrator_attach_learned_chain(final, node)
        return entry

    def _orchestrator_route_whole_job(self, goal, chains):
        """The LEARNED chain that does exactly this job (deterministic match).

        Candidates are freeze artifacts only (``learned: true``).  Two routes,
        both deterministic and both about INTENT, never about matching a
        description verbatim (see ``_orchestrator_learned_covers``): the frozen
        example, or — the interesting one — the artifact's own COMPOSITION:
        the descriptions of the atomic chains it is made of.

        Coverage is the safety property: a request missing one of the steps
        ("open linkedin and click messages" vs a 3-step artifact) would make it
        do an action nobody asked for, and a request adding one would leave
        that action undone — both decline here, log exactly what did not line
        up, and fall to the split ladder (which serves them step by step and
        freezes the bigger composition when the run verifies).

        No scorer is consulted: measured 2026-09-24 the neural version of this
        rung picked an ATOMIC chain as "the whole answer" (0.869 vs the
        learned chain's 0.765; the state digest flipped the order again).
        """
        cands = []
        for c in chains:
            if not (c.get('learned') and (c.get('examples')
                                          or c.get('description'))):
                continue
            if c.get('retired'):
                continue
            # Eviction policy (measured 2026-09-25: the counters were written
            # as 0 and never updated, so bad artifacts were immortal).  An
            # artifact with enough runs and a losing record retires in place
            # — marked on its own file, skipped from now on.
            _runs = int(c.get('runs') or 0)
            _wins = int(c.get('wins') or 0)
            if (_runs >= _LEARNED_EVICT_MIN_RUNS
                    and _wins < _LEARNED_EVICT_WIN_RATE * _runs):
                self._orchestrator_retire_learned_chain(c)
                continue
            cands.append(c)
        if not cands:
            return None
        generic = _orchestrator_library_generic(
            [c.get('signal') for c in chains])
        goal_key = _orchestrator_directive_key(goal, generic)
        if not goal_key:
            return None
        matches = []
        for c in cands:
            why = self._orchestrator_learned_covers(goal_key, c, generic)
            if why:
                matches.append((why, c))
        if not matches:
            for c in cands[:3]:
                logger.info(
                    "[ORCH] Whole-job rung: learned chain %s does not cover "
                    "this request (%s) — steps: %s",
                    c['name'], self._orchestrator_learned_gap(goal_key, c),
                    (c.get('composition') or '')[:200],
                )
            return None
        # Two artifacts can only both match by describing the same job; the
        # newest is the one rebuilt last (the writer reuses a goal's file).
        matches.sort(key=lambda item: -float(item[1].get('created_at') or 0))
        why, top = matches[0]
        logger.info(
            "[ORCH] Whole-job rung: learned chain %s covers this request "
            "(%s match) — running it as the whole answer | %s",
            top['name'], why, (top.get('composition') or '')[:300],
        )
        return top

    # The planning protocol: one short question, a strict output shape, and a
    # worked example.  It asks the model to LIST the steps — the thing small
    # models can do — and the code validates every line, so no reply is ever
    # trusted on its own.  Nothing here measures or cuts the request: the
    # request is never split into pieces by this file (measured 2026-09-24:
    # fragments disconnected from the chains the library offers — a verbless
    # "on my network" read 0.578 against the tool "clicks on my network
    # button" and the action stayed unserved).
    _ORCH_PLAN_PROMPT = (
        "List the steps this request is made of, in order.\n"
        "REQUEST: {request}\n"
        "{state}"
        "Rules: list ONLY what the REQUEST asks for. The state is "
        "information, never a task: never plan to open, navigate, log in, "
        "type or click unless the request itself asks for it. A request that "
        "only asks to look at, check or report what is on the screen has "
        "observation steps, never navigation.\n"
        "One step per line. A step is a verb and the thing it acts on "
        "('open the file', 'click the save button') - never 'file open'. "
        "A request that is already ONE action stays one line. No numbering, "
        "no explanation, nothing else.\n"
        "STEPS:\n"
    )
    # Asked once, only when the first reply can not be a plan: the request
    # joins clauses (see _orchestrator_looks_compound) and one line came back.
    # The worked example is kept as content (shape training), it is NAMED
    # here so the parser can refuse a reply that copies it verbatim, and its
    # vocabulary is TASK-NEUTRAL on purpose: it once read 'open linkedin /
    # click on my messages' and a weak model copied it into plans of
    # unrelated requests (measured 2026-09-25).
    _ORCH_PLAN_EXAMPLE = ('open the file', 'click the save button')
    _ORCH_PLAN_RETRY_PROMPT = (
        "The request has more than one step and your answer had only one. "
        "List each step on its own line again.\n"
        "REQUEST: {request}\n"
        "{state}"
        "Example of the shape wanted:\n"
        "{example}\n\n"
        "STEPS:\n"
    )

    @staticmethod
    def _orchestrator_drop_satisfied_directives(directives, state):
        """Drop plan lines the OBSERVED state already satisfies.

        The planner is told to skip steps the state shows are done, and a 2B
        model does not comply — measured 2026-09-25: 'get context from the
        website i opened and tell me what you see in it' planned 'Open
        Chrome browser', which ran and whose gate then navigated the user's
        OPEN tab to linkedin.com.  The rule is deterministic: a REACHING verb
        (see _ORCH_STATE_DONE_VERBS) whose thing is already visible in the
        state is dropped before anything routes — the step is satisfied by
        definition.  Blind runs (no state) keep the plan untouched, and a
        plan that would empty completely is kept as-is (the ladder decides).
        """
        items = [str(d) for d in (directives or [])]
        if not state or not items:
            return directives
        ambient = _orchestrator_directive_key(state, frozenset())
        out = []
        for d in items:
            words = [w.strip('",.;:').lower() for w in d.split()]
            verb = _orchestrator_stem(words[0]) if words else ''
            key = _orchestrator_directive_key(d, frozenset())
            if verb in _ORCH_STATE_DONE_VERBS and key and (key & ambient):
                logger.info(
                    "[ORCH] planner: dropped %r — the observed state already "
                    "satisfies it", d[:80],
                )
                continue
            out.append(d)
        return out or directives

    @staticmethod
    def _orchestrator_strip_worker_prefixes(directives, workers):
        """A leading '<worker name>:' is planning noise, not an action.

        The planner reads the WORKERS block, and a weak model copies its
        'name:' shape into the directive — measured 2026-09-25: 'web_check:
        check if linkedin is currently open in the browser' rode into the
        gate with the tool's name inside the step (and thereby into the
        tool's own Input node).  Stripped only when the prefix matches a
        WIRED worker; anything else passes untouched.
        """
        names = sorted(
            {str(w.get('name') or '').strip().lower()
             for w in (workers or []) if str(w.get('name') or '').strip()},
            key=len, reverse=True,
        )
        out = []
        for d in directives:
            text = str(d)
            for nm in names:
                low = text.lower()
                cut = ''
                for sep in (f"{nm}:", f"{nm} -", f"{nm} —", f"{nm},"):
                    if low.startswith(sep):
                        cut = sep
                        break
                if cut:
                    text = text[len(cut):].strip()
                    break
            out.append(text or str(d))
        return out

    def _orchestrator_plan_directives(self, goal, node_data, stop_flag,
                                      generic=frozenset(), state=None):
        """Split the goal into the ordered single actions it is made of.

        THE LIST IS THE MODEL'S WORK, and the protocol is what makes a small
        model able to do it: one short question ("list the steps this request
        is made of"), one step per line, a worked example, and a single re-ask
        when a request that joins clauses comes back as ONE line.  The code
        then validates — every line is shaped, capped, deduped by object and
        completed by _orchestrator_inherit_verbs when the model left its verb
        out — so nothing depends on the model being capable, and NOTHING here
        splits the request into pieces: regex cut "...then on my network" into
        a verbless fragment the gate could not connect to the chain described
        "clicks on my network button", and the action stayed unserved
        (measured 2026-09-24).

        Two shortcuts stay deterministic because the input is already a list:
        the numbered/bulleted handoff a parent level writes (its lines ARE
        steps — re-planning one is how a clean four-item list lost 'jobs') and
        the model-unavailable fallback (the goal becomes one directive and the
        ladder's picker / done-probe owns the run exactly as before).

        The OBSERVED STATE rides in — the same digest the gate, the picker,
        the scoper and the verifier read: the plan is the one turn that
        decides what the WHOLE run will do, and it used to be the only turn
        that could not see the world (measured 2026-09-25: 'check on linkedin,
        i have the page open' planned 'Open LinkedIn' first).  The state
        block asks the model to leave out a step the state shows is done.

        NOT the workers: a worker list rode in once and was removed
        (measured 2026-09-25, two runs): the 2B planner copied the reference
        material INTO the plan — worker descriptions became plan lines ('go
        to linkedin.com', 'click on my network button') and a learned
        chain's own description ('Learned from a verified run: ...') became
        a step the run then executed.  The request, the state and the intent
        rules are the only inputs planning gets.
        """
        handoff = _orchestrator_list_items(goal)
        if len(handoff) >= 2:
            logger.info(
                "[ORCH] planner: the request arrives as a %d-step list — its "
                "lines are the directives", len(handoff),
            )
            return _orchestrator_inherit_verbs(
                self._orchestrator_drop_satisfied_directives(handoff, state))
        state_text = ''
        if state:
            state_text = (
                "CURRENT STATE (observed, not claimed):\n"
                f"{str(state)[:_STATE_IN_PROMPT_CHARS]}\n"
                "Do not list a step the state shows is already done.\n"
            )
        # Parent plan cursor (propagation packet): a relayed level plans WITH
        # the parent's decomposition in view instead of re-deriving it from
        # the scoped fragment alone.  Absent for top-level runs — the prompt
        # is then identical to before.
        try:
            _raw_plan = str(
                self.llm_executor.get_variable("_chain_plan") or '').strip()
        except Exception:
            _raw_plan = ''
        if _raw_plan:
            try:
                _plan_ctx = json.loads(_raw_plan)
                _remaining = ' | '.join(
                    str(r) for r in (_plan_ctx.get('remaining') or []))
                state_text += (
                    f"PARENT PLAN: {int(_plan_ctx.get('index') or 0)}/"
                    f"{int(_plan_ctx.get('total') or 0)} done; "
                    f"remaining: {_remaining[:300]}\n"
                )
            except Exception:
                pass
        prompt = self._ORCH_PLAN_PROMPT.format(
            request=str(goal or '')[:1200], state=state_text)
        raw = self._orchestrator_llm_call(
            prompt, node_data, stop_flag, max_tokens=160)
        directives = self._orchestrator_parse_directives(raw, goal, generic)
        if (_orchestrator_looks_compound(goal) and len(directives) < 2
                and raw is not None):
            logger.info(
                "[ORCH] planner: the request joins clauses and the reply was "
                "one line — asking once more with the protocol's example",
            )
            raw = self._orchestrator_llm_call(
                self._ORCH_PLAN_RETRY_PROMPT.format(
                    request=str(goal or '')[:1200], state=state_text,
                    example='\n'.join(self._ORCH_PLAN_EXAMPLE)),
                node_data, stop_flag, max_tokens=160)
            again = self._orchestrator_parse_directives(raw, None, generic)
            # Example echo: a weak model answers the retry by COPYING the
            # protocol's example verbatim (measured 2026-09-25: 'check this
            # web page i have open and tell me what it has' planned 'open
            # linkedin / click on my messages' — the example lines).  A reply
            # that echoes the example is discarded whenever the request does
            # not itself ask for the example's objects; the first reply
            # stands as the plan.
            if len(again) >= 2:
                _ex_keys = [_orchestrator_directive_key(e, generic)
                            for e in self._ORCH_PLAN_EXAMPLE]
                _goal_key = _orchestrator_directive_key(goal, generic)
                if ([_orchestrator_directive_key(d, generic) for d in again]
                        == _ex_keys
                        and not all(k and k <= _goal_key for k in _ex_keys)):
                    logger.warning(
                        "[ORCH] planner: the retry reply is a verbatim echo "
                        "of the protocol's example (%s) — discarded, the "
                        "first reply stands", ' | '.join(again),
                    )
                else:
                    directives = again
        if len(directives) < 2:
            logger.info(
                "[ORCH] planner: one directive for this request (%s)",
                'turn unavailable' if raw is None else 'single action',
            )
        directives = self._orchestrator_drop_satisfied_directives(
            directives, state)
        return _orchestrator_inherit_verbs(directives)

    @staticmethod
    def _orchestrator_parse_directives(raw, goal, generic=frozenset()):
        """The model's reply -> directives; never a paragraph, never empty.

        The reply is parsed line by line (see _orchestrator_lines_to_directives)
        — reasoning blocks are dropped first — and when it yields nothing (or
        the turn failed) the GOAL becomes ONE directive: the ladder's picker,
        scoper and done-probe own it from there, exactly as before planning
        existed.  Nothing decomposes the goal by patterns.
        """
        import re as _re
        text = str(raw or '')
        try:
            text = _re.sub(r'<[^>]*think[^>]*>.*?</[^>]*think[^>]*>', ' ',
                           text, flags=_re.DOTALL)
            text = _re.sub(r'<[^>]*thinking[^>]*>.*?</[^>]*thinking[^>]*>', ' ',
                           text, flags=_re.DOTALL)
        except Exception:
            pass
        out = _orchestrator_lines_to_directives(text, generic)
        if out:
            return out
        fallback = str(goal or '').strip()
        return [fallback] if fallback else []

    def _orchestrator_chain_span(self, chain, directives, start, generic):
        """How many CONSECUTIVE directives this chain's description serves.

        A chain is the cerebellum: fast, predictable, mechanical — and not
        necessarily one action.  It does whole routines without per-action
        routing ("opens linkedin and clicks on messages"), so once the gate
        routed it for one directive the plan advances by what its own
        DESCRIPTION names and nothing more: the longest run of following
        directives whose object words the chain already names.  A step-sized
        chain therefore advances 1, as always, and a directive the description
        does not name stops the walk — the count is computed, never assumed.
        """
        signal = ' | '.join(chain.get('examples') or []) \
            or str(chain.get('description') or '')
        chain_key = _orchestrator_directive_key(signal, generic)
        span = 1
        for i in range(int(start) + 1, len(directives)):
            key = _orchestrator_directive_key(directives[i], generic)
            if not key or not (key <= chain_key):
                break
            span += 1
        return span

    def _orchestrator_scope_group(self, brain_at, candidates, brains, picker,
                                  trace_text, state):
        """The tasks this brain receives: the prefix ITS DESCRIPTION covers.

        Each following directive is put to the SAME typed question that picked
        this brain for the current step ("which worker serves this step?" —
        one short choice over the brains' descriptions), and the group grows
        while the answer is THIS brain: the description is the scope, nothing
        is measured by word soup, nothing beyond the plan is read.  This is how
        a sub-brain receives the tasks it covers instead of the whole remaining
        list — each instance gets the scope it needs, never every task of the
        request.

        The read needs the Laya engine; with the LLM picker there is no cheap
        way to judge a description against a directive, so the whole remaining
        tail is handed over as before and the sub-brain partitions it itself
        (that is what its handback marker is for).
        """
        if picker != 'laya':
            return list(candidates)
        out = [candidates[0]]
        for directive in candidates[1:]:
            if self._orchestrator_laya_worker(directive, brains, trace_text,
                                              state) != brain_at:
                break
            out.append(directive)
        return out

    def _orchestrator_route_chain_direct(self, goal, chains, trace=None,
                                         used=None, state=None):
        """Which chain serves the NEXT STEP of this directive (Laya read)?

        Chains are STEP-sized ("clicks on the jobs button"), so the question is
        the step question, never "does one chain cover the whole request" —
        measured 2026-09-23: that one-shot reading rejected a perfectly
        serviceable chain (jobs, p=0.323).  Every unused candidate gets one noul
        forward; the argmax routes only when it clears ``_CHAIN_GATE_MIN`` AND
        beats the runner-up by ``_CHAIN_GATE_MARGIN``.  Ambiguity is never a
        guess: it returns None and the brain loop handles that step.

        The premise is goal + steps ONLY — exactly the shape the thresholds
        were measured on.  The state digest is deliberately NOT added here: it
        shifts a small model's scores (2026-09-24: a compound premise with the
        digest put 'my network' on top at 0.414 for an "open linkedin and click
        messages" request); the caller keeps the state for the whole-job gate,
        the planner, the scoper and the verifier.

        Laya is a burst resource (load -> use -> unload), so the daemon is
        normally DOWN between steps; this gate therefore STARTS it (~2 s) — the
        chains port is Laya-gated by design.  A machine where the engine is
        disabled for the session (kill switch, no exe, no model) keeps this
        closed without probing.

        The state digest is NOT in the premise but its words still matter:
        a word the observed state already shows is AMBIENT (the environment,
        not the task) and is excluded from the object-naming rule; and when
        the step names a non-ambient object that NO chain's description names,
        the gate declines instead of scoring the whole field.
        """
        used = used or set()
        # Learned artifacts are excluded: they compose several steps, so they
        # are WHOLE-JOB candidates, never a "next step" (measured 2026-09-24:
        # a learned chain scored 0.756 on the step question because its example
        # is the goal text, collapsing the margin under the correct atomic
        # step's 0.809 and sending the run to the brain loop).
        candidates = [c for c in chains
                      if c.get('chain_id') not in used and not c.get('learned')]
        if not candidates:
            logger.info(
                "[ORCH] Direct-chain gate: every chain on the port was used "
                "in this activation — falling back to the brain loop",
            )
            return None
        try:
            from AI import laya_client as _lc
            if not _lc.available() and not _lc.ensure_running():
                return None
        except Exception:
            return None
        steps_text = self._orchestrator_render_steps(trace)
        premise = (
            f"Goal: {str(goal or '')[:300]}\n"
            f"Steps taken so far:\n{steps_text or '(none yet)'}"
        )
        scored = []
        generic = _orchestrator_library_generic(
            [c.get('signal') for c in chains])
        goal_key = _orchestrator_directive_key(goal, generic)
        # Ambient words: what the observed state ALREADY shows describes the
        # environment, not the task — it must not confirm a chain (measured
        # 2026-09-25: 'Open Chrome browser' matched 'go to linkedin' on the
        # single word 'chrome' from the window title, and the chain navigated
        # the user's open tab to linkedin.com).
        ambient = (_orchestrator_directive_key(state, frozenset())
                   if state else set())
        for c in candidates:
            signal = ' | '.join(c.get('examples') or []) \
                or str(c.get('description') or '')
            signal = ' '.join(signal.split())[:240]
            if not signal:
                logger.warning(
                    "[ORCH] Direct-chain gate: %s carries no description or "
                    "routing examples — no signal to route by, skipped",
                    c['name'],
                )
                continue
            # EVERY wired chain is scored: the port is the human's action
            # space and the scorer navigates it.  Nothing is withheld
            # (measured 2026-09-24: for 'click on the home button' the
            # messages chain read 0.506 — the tiny model's noise band — and
            # the run followed it while the floor was 0.5).
            p = _lc.noul(premise, f"The next step toward the goal is to {signal}.")
            if p is None:
                return None  # engine died mid-read — never a guess
            named = ((goal_key
                      & _orchestrator_directive_key(signal, generic))
                     - ambient)
            scored.append((len(named), p, c))
        if not scored:
            return None
        # The step's OWN objects decide who MAY serve it, the scorer decides
        # WHO does: of the candidates whose human description names at least
        # one of the step's NON-AMBIENT objects, the best score wins.  Measured
        # live 2026-09-24 on the 7 wired chains: for "press my network button"
        # the scorer picked the WRONG chain by 0.001 — home 0.656 over 'clicks
        # on "my network" button' 0.655 — while only the right chain named the
        # step's object at all; and for "go to linkedin.com" the right chain
        # won the argmax (0.808 vs 0.754, margin 0.054) but the margin rule
        # declined it although its description named MORE of the step (2
        # objects vs 1).  When NOTHING names any non-ambient object the whole
        # field is scored only if the step itself names nothing non-ambient;
        # otherwise no chain demonstrably serves this step and the gate
        # declines — a miss costs one brain round, a false positive runs the
        # wrong chain (bias strict).
        named = [item for item in scored if item[0]]
        if (goal_key - ambient) and not named:
            logger.info(
                "[ORCH] Direct-chain gate: no chain names %s of the step — "
                "falling back to the brain loop",
                ', '.join(sorted(goal_key - ambient)[:6]),
            )
            return None
        pool = named or scored
        pool.sort(key=lambda item: -item[1])
        top_named, top_p, top_c = pool[0]
        second_named = pool[1][0] if len(pool) > 1 else 0
        second_p = pool[1][1] if len(pool) > 1 else 0.0
        # A richer description CONFIRMS the pick and the runner-up's proximity
        # is noise; an equal one is a real tie, where the margin keeps the gate
        # honest (the same rule the margin was written for: 'click on my
        # network' at 0.641 vs an unrelated 0.620 was a perfect match declined
        # by 0.021).
        confirmed = top_named > second_named
        if top_p < _CHAIN_GATE_MIN or (
                not confirmed and (top_p - second_p) < _CHAIN_GATE_MARGIN):
            logger.info(
                "[ORCH] Direct-chain gate: %s not convincing (p=%.3f, next=%.3f, "
                "margin %.3f, objects named %d vs %d) — falling back to the "
                "brain loop",
                top_c['name'], top_p, second_p, top_p - second_p,
                top_named, second_named,
            )
            return None
        logger.info(
            "[ORCH] Direct-chain gate: next step is %s (p=%.3f, margin %.3f%s)",
            top_c['name'], top_p, top_p - second_p,
            ', description names the step\'s objects' if confirmed else '',
        )
        return top_c

    def _orchestrator_run_direct_chain(self, goal, chain, step, stop_flag,
                                       state=None):
        """Execute one frozen chain as the whole answer (one trace entry)."""
        brain = {
            'brain_id': chain['chain_id'],
            'node': chain['node'],
            'name': chain['name'],
            'chain_file': chain['chain_file'],
            'description': chain['description'],
            'kind': 'chain',
        }
        try:
            # The goal rides the established channel; the chain's own Input
            # nodes extract whatever they declare (agent_modifiable slots).
            self.llm_executor.set_variable("_chain_input_context", str(goal or ''))
            self.llm_executor.set_variable("_chain_goal", str(goal or ''))
            self.llm_executor.set_variable("_chain_step", "")
        except Exception:
            pass
        logger.info(
            "[ORCH] Direct chain step %d: %s (%s)",
            step, chain['name'], chain['chain_file'],
        )
        self._orchestrator_invoke_brain(brain, stop_flag)
        if stop_flag and stop_flag():
            return None
        result = ""
        try:
            result = self._orchestrator_collect_brain_result(brain)
        except Exception as e:
            logger.warning("[ORCH] Direct chain result failed: %s", e)
        # The step a direct chain serves IS its directive — recorded as the
        # step text, not a placeholder.  '(frozen chain)' told the done-probe
        # NOTHING about what ran, so an accomplished observation still read
        # p_done 0.039 and the level kept marching (measured 2026-09-25: the
        # page analysis was produced, the probe rejected it, and the run
        # continued until it closed falsely).
        _step_text = str(goal or '') or '(frozen chain)'
        verdict = False
        if brain.get('_ok') is not False:
            verdict = self._orchestrator_verify_step(
                goal, _step_text, result, allow_start=True, state=state)
        log_block(
            logger, logging.INFO,
            f"Orchestrator direct chain → {chain['name']}",
            f"result: {str(result)[:600]}\nverify: {verdict}",
        )
        return {
            'n': step,
            'brain': chain['name'],
            'brain_id': chain['chain_id'],
            'step': _step_text,
            'result': self._orchestrator_clean_result(result)[:_RESULT_EXCERPT_CHARS],
            'ok': verdict,
        }

    def _orchestrator_node_brain_description(self, node, fallback_name=''):
        """Minimal derived description for a single-node brain (zero file I/O).

        The picker needs label + type + ONE structural hint to discriminate
        between workers; the rich per-node description field is a later task.
        """
        data = node.get('data', {}) or {}
        ntype = str(node.get('type') or '')
        label = str(
            data.get('label') or data.get('name') or fallback_name
            or node.get('id') or ntype
        ).strip()
        # A hand-written description (the dialogs' description field) always
        # wins — it is the one text a human authored FOR routing.
        authored = str(data.get('description') or '').strip()
        if authored:
            return authored[:_NODE_BRAIN_DESC_CAP]
        hint = ''
        try:
            if ntype == 'sequence':
                hint = os.path.basename(str(data.get('sequence_file') or '').strip())
            elif ntype == 'web_sequence':
                hint = ' '.join(x for x in (
                    os.path.basename(str(data.get('session_file') or '').strip()),
                    str(data.get('repeat_mode') or '').strip(),
                ) if x)
            elif ntype == 'conditional':
                hint = str(data.get('condition_type') or '').strip()
            elif ntype == 'handle':
                hint = str(
                    data.get('goal_description') or data.get('action_type') or ''
                ).strip()
            elif ntype == 'mcp':
                hint = str(data.get('tool_name') or '').strip() or os.path.basename(
                    str(data.get('mcp_folder') or '').strip())
            elif ntype == 'form_filler':
                hint = str(data.get('mode') or '').strip()
            elif ntype == 'llm':
                hint = str(
                    data.get('model') or data.get('llamacpp_model_path') or ''
                ).strip()
            elif ntype == 'code':
                hint = 'code'
        except Exception:
            hint = ''
        text = f"{label} ({ntype}{': ' + hint if hint and hint != ntype else ''})"
        return text[:_NODE_BRAIN_DESC_CAP]

    def _orchestrator_invoke_brain(self, brain, stop_flag):
        """Dispatch ONE orchestrator step to the picked brain (chain or node).

        Node brains run in-player through the same executors the main loop
        uses.  ``selected_by_llm`` stays set as the historical dispatch marker
        (reporting only — scheduling eligibility is the builder's generic
        ``tool_provider`` flag).  Returns True when a step actually ran.
        """
        node = brain.get('node') or {}
        ntype = str(node.get('type') or '')
        try:
            node['selected_by_llm'] = True
        except Exception:
            pass
        try:
            if ntype == 'conditional':
                # A conditional brain is a CHECK: evaluate once, publish the
                # verdict, never navigate branches or loop (graph ownership
                # stays with the main loop).
                node['last_conditional_result'] = self._evaluate_conditional(
                    node.get('data', {}) or {}, stop_flag, node.get('inputs', []),
                )
                brain['_ok'] = node.get('last_conditional_result') is not None
                return True
            method_name = _BRAIN_EXECUTORS.get(ntype)
            method = getattr(self, method_name, None) if method_name else None
            if method is None:
                logger.error(
                    "[ORCH] Brain %s has unsupported node type %r — step skipped",
                    brain.get('name'), ntype,
                )
                brain['_ok'] = False
                return False
            if ntype == 'llm':
                ret = method(node, stop_flag, getattr(self, 'action_handlers', None))
            else:
                ret = method(node, stop_flag)
            # The engine's own failure convention: a node executor returns the
            # next node id on success and None on failure (core logs "Node X
            # execution failed" on exactly this return).  A router-port brain
            # always HAS an output edge — the port it is wired to — so None
            # means the node aborted, and the step is reported as failed
            # instead of a fabricated success.
            brain['_ok'] = ret is not None
            return True
        except Exception as e:
            logger.error("[ORCH] Brain %s execution error: %s", brain.get('name'), e)
            brain['_ok'] = False
            return False

    def _orchestrator_collect_brain_result(self, brain):
        """The picked brain's result text; always sets _last_tool_result_context.

        Chain brains keep the historical path (``_collect_tool_result``);
        single-node brains read their own channels with a bounded cascade —
        a full port dump (e.g. a 200 KB page payload) must never reach the
        trace.  Returns the same string it stores.
        """
        node = brain.get('node') or {}
        if str(brain.get('kind') or 'chain') == 'chain':
            try:
                self._collect_tool_result(node)
            except Exception as e:
                logger.warning(
                    "[ORCH] Result collection failed for %s: %s",
                    brain.get('name'), e,
                )
            try:
                text = str(self.llm_executor.get_variable("_last_tool_result_context") or "")
            except Exception:
                text = ""
            if brain.get('_ok') is False:
                # Honest failure marker (the fresh-run convention): a chain
                # that reported failure is never dressed as a result.
                text = (text + " — " if text else "") + self._orchestrator_failure_line(
                    'chain', brain)
        else:
            text = self._orchestrator_node_brain_result(node, brain)
        # A nested orchestrator reports what it could NOT finish; the marker is
        # machine-readable (the trace keeps the prose) and the parent uses it
        # to re-route only the untouched tail.
        text, remaining = self._orchestrator_split_remaining(text)
        brain['_remaining'] = remaining
        # The structured outcome (child's step list + stop reason) rides with
        # the handback marker; stripped from the prose and recorded on the
        # brain so the trace entry can carry it.  Refreshed on every collect,
        # so a later step never inherits a stale one.
        text, outcome = self._orchestrator_split_outcome(text)
        brain['_outcome'] = outcome
        if outcome:
            logger.info(
                "[ORCH] %s outcome: stop_reason=%s steps=%d",
                brain.get('name'), outcome.get('stop_reason'),
                len(outcome.get('steps') or []),
            )
        text = text[:_BRAIN_RESULT_CAP]
        if str(brain.get('kind') or 'chain') != 'chain':
            try:
                self.llm_executor.set_variable("_last_tool_result_context", text)
            except Exception:
                pass
        return text

    @staticmethod
    def _orchestrator_split_remaining(text):
        """(prose, reported-remaining) — strips the handback marker(s).

        Bracket-BALANCED scan, never a regex: the directive strings may
        contain ']' themselves, and a composed tool result carries the marker
        TWICE ('Result:' and 'Output:' sections of the same sub-run) — an
        end-anchored greedy match merged the two arrays into one unparseable
        blob and the handback was silently dropped, so a no-op sub-run closed
        the parent's plan as done (measured 2026-09-25 live).  Every marker
        occurrence is stripped from the prose; the first valid array is the
        report.
        """
        prose = str(text or '')
        reported = []
        while True:
            idx = prose.find(_ORCH_REMAINING_MARKER)
            if idx == -1:
                break
            arr = _orchestrator_marker_array(
                prose, idx + len(_ORCH_REMAINING_MARKER))
            if arr is None:
                # Marker without a readable array: drop the marker text only.
                prose = (prose[:idx]
                         + prose[idx + len(_ORCH_REMAINING_MARKER):]).strip()
                continue
            data, end = arr
            if not reported and isinstance(data, list):
                reported = [str(d).strip() for d in data if str(d).strip()]
            prose = (prose[:idx] + prose[end:]).strip()
        return prose, reported

    @staticmethod
    def _orchestrator_split_outcome(text):
        """(prose, outcome) — strips the outcome marker(s).

        Same rules as _orchestrator_split_remaining (bracket-BALANCED scan,
        every occurrence stripped, the first valid object wins); a malformed
        payload drops its marker text only, never the prose around it.
        """
        prose = str(text or '')
        outcome = None
        while True:
            idx = prose.find(_ORCH_OUTCOME_MARKER)
            if idx == -1:
                break
            parsed = _orchestrator_marker_array(
                prose, idx + len(_ORCH_OUTCOME_MARKER), '{', '}')
            if parsed is None:
                prose = (prose[:idx]
                         + prose[idx + len(_ORCH_OUTCOME_MARKER):]).strip()
                continue
            data, end = parsed
            if outcome is None and isinstance(data, dict):
                outcome = data
            prose = (prose[:idx] + prose[end:]).strip()
        return prose, outcome

    @staticmethod
    def _orchestrator_apply_handback(directives, dir_idx, reported, goal):
        """Advance the cursor past the prefix the brain DID accomplish.

        The brain reports the directives it could NOT do; when that list lines
        up with a TAIL of the parent's remaining span, everything before it is
        done and the parent re-routes exactly what is left.  A report that does
        not line up is ignored (the parent keeps its own view of the work).
        """
        if not reported:
            return dir_idx, []
        remaining = (list(directives[dir_idx:])
                     if dir_idx < len(directives) else [str(goal or '')])
        norm = lambda t: ' '.join(str(t or '').split()).lower()
        norm_remaining = [norm(d) for d in remaining]
        norm_reported = [norm(d) for d in reported]
        for start in range(len(norm_remaining) + 1):
            if norm_remaining[start:] == norm_reported:
                return dir_idx + start, remaining[start:]
        return dir_idx, remaining

    @staticmethod
    def _orchestrator_failure_line(ntype, brain):
        """The honest 'this step did not run' line, shared by both brain kinds."""
        text = f"Outcome: FAILED ({ntype}) - the node aborted before completing"
        desc = str((brain or {}).get('description') or '').strip()
        if desc:
            text += f" ({desc[:160]})"
        return text

    def _orchestrator_node_brain_result(self, node, brain):
        """Bounded result cascade for a single-node brain ('' paths handled)."""
        ntype = str(node.get('type') or '')
        nid = str(
            node.get('id') or node.get('node_id')
            or (node.get('data') or {}).get('node_id') or ''
        )
        # A node that reported failure is never a success, whatever stale
        # channels it left behind (a blanket "ran successfully" line once
        # turned a failed web sequence into a verified, 'done' step).
        if brain.get('_ok') is False:
            return self._orchestrator_failure_line(ntype, brain)[:_BRAIN_RESULT_CAP]
        # A conditional brain's verdict IS its result (bool, not text).
        if ntype == 'conditional' and node.get('last_conditional_result') is not None:
            return f"decision={bool(node.get('last_conditional_result'))}"
        # Canonical per-node channels first (one read, no dump).
        for var in (f"node_{nid}_output", f"node_{nid}_context", f"node_{nid}_data"):
            if not nid:
                break
            try:
                val = self.llm_executor.get_variable(var)
            except Exception:
                val = None
            if isinstance(val, str) and val.strip():
                return val.strip()[:_BRAIN_RESULT_CAP]
        # Published ports, priority order, str-only (bounded by construction:
        # the first non-empty value wins; nothing is joined then truncated).
        ports = {}
        if nid:
            try:
                ports = self.port_store.get_node_ports(self.chain_id, nid) or {}
            except Exception:
                ports = {}
        for port in _BRAIN_RESULT_PORTS:
            val = ports.get(port)
            if isinstance(val, str) and val.strip():
                return val.strip()[:_BRAIN_RESULT_CAP]
        # Recorded-action brains (sequence / web / handle / ...) have no
        # result slot — say what happened, mirroring chain_ops' convention.
        desc = str(brain.get('description') or '').strip()
        text = f"Outcome: ran successfully ({ntype})"
        if desc:
            text += " - " + desc[:200]
        return text[:_BRAIN_RESULT_CAP]

    # --------------------------------------------------------- state digest

    @staticmethod
    def _orchestrator_state_enabled():
        return str(os.environ.get("LOOPER_ORCH_STATE", "")).strip().lower() not in (
            '0', 'off', 'false', 'no', 'disabled',
        )

    def _orchestrator_observe_state(self):
        """Where things stand RIGHT NOW — the loop's third input.

        The picker/scope get the GOAL and the verify gets the step RESULT, but
        a result is the brain's own prose: it reads "opened LinkedIn" whether
        or not the screen moved (measured 2026-09-23 — a failed web sequence
        reported success, Laya verified it, and the loop closed as done).  This
        digest is the OBSERVED answer, and it is what lets the model confirm a
        step instead of assuming it.

        Sources are optional, bounded and read-only: the chain's own live web
        page (never launched, never navigated) and the desktop foreground
        window (title + process).  '' when nothing is observable — a blind run
        behaves exactly as before.  ``LOOPER_ORCH_STATE=off`` disables it.
        """
        if not self._orchestrator_state_enabled():
            return ''
        parts = []
        for probe in (self._orchestrator_web_state,
                      self._orchestrator_desktop_state):
            try:
                text = probe()
            except Exception as exc:
                logger.debug("[ORCH] state probe failed: %s", exc)
                text = ''
            if text:
                parts.append(text)
        return '\n'.join(parts)[:_STATE_DIGEST_CHARS]

    def _orchestrator_web_state(self):
        """Live browser page: URL, title, visible text (shadow roots + frames).

        Reads the driver the CHAIN already holds — no session is created and
        no navigation happens, so observing can never change what is observed.
        """
        executor = getattr(self, 'sequence_executor', None)
        driver = getattr(executor, '_web_session_driver', None) if executor else None
        if executor is None or driver is None:
            return ''
        try:
            if not executor._web_driver_alive(driver):
                return ''
        except Exception:
            return ''
        try:
            try:
                from ....web.actions import JS_VISIBLE_TEXT
            except ImportError:
                from player.web.actions import JS_VISIBLE_TEXT
            url = str(getattr(driver, 'current_url', '') or '')
            title = str(getattr(driver, 'title', '') or '')
            text = ' '.join(str(driver.execute_script(JS_VISIBLE_TEXT) or '').split())
        except Exception as exc:
            logger.debug("[ORCH] web state read failed: %s", exc)
            return ''
        line = f"Browser now: {url[:120]} | {title[:80]}"
        if text:
            line += f"\nVisible text: {text[:_STATE_WEB_TEXT_CHARS]}"
        return line

    def _orchestrator_desktop_state(self):
        """Foreground window + its process (win32 only — no COM/UIA here).

        A UIA element sample was the natural next source, but walking the tree
        on a short-lived thread made comtypes tear its cached UIA object down
        inside a dying apartment (Windows fatal 0x80010108 + access violation
        in tests, 2026-09-23).  It needs a persistent UIA worker thread first;
        until then this probe stays on win32/psutil, which never blocks.
        """
        try:
            import win32gui
        except Exception:
            return ''
        try:
            hwnd = int(win32gui.GetForegroundWindow() or 0)
            if not hwnd:
                return ''
            title = str(win32gui.GetWindowText(hwnd) or '')
        except Exception:
            return ''
        process = ''
        try:
            import psutil
            import win32process
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid:
                process = str(psutil.Process(int(pid)).name() or '')
        except Exception:
            process = ''
        return f"Desktop now: '{title[:80]}' (process: {process or 'unknown'})"

    def _orchestrator_verify_step(self, goal, step_text, result, allow_start=False,
                                  state=None):
        """Laya verdict: did this step accomplish what it was asked to do?

        True / False, or None when the engine is unavailable.  Since Laya
        became a burst resource (load -> use -> unload), ``available()`` can be
        False because the daemon was just unloaded after its last forward:
        ``allow_start`` runs (the picker=laya runs, and the direct-chain path)
        may relaunch it (~2 s) — a picker=llm run must not pay a model load
        per step for a verdict it did not ask for, and the ``LOOPER_LAYA=0``
        kill switch keeps working on top of both rules.

        Unlike the done-probe (steps only), the premise here INCLUDES the
        result and the observed STATE: the question is about the step just run,
        so its output AND the world it left behind are the evidence, not the
        brain's self-reported claim.
        """
        try:
            from AI import laya_client as _lc
            if not _lc.available():
                if not (allow_start and _lc.ensure_running()):
                    return None
            premise = (
                f"Goal: {str(goal or '')[:300]}\n"
                f"Step: {str(step_text or '(no scoped step)')[:300]}\n"
                f"Result: {str(result or '(no result)')[:400]}"
            )
            if state:
                premise += (
                    f"\nState after the step: {str(state)[:_STATE_IN_PROMPT_CHARS]}"
                )
            p = _lc.noul(premise, "The step accomplished what it was asked to do.")
            if p is None:
                return None
            return bool(p > 0.5)
        except Exception:
            return None

    # ------------------------------------------------- learned-chain freeze

    @staticmethod
    def _orchestrator_learn_enabled():
        return str(os.environ.get("LOOPER_LEARN", "")).strip().lower() not in (
            '0', 'off', 'false', 'no', 'disabled',
        )

    @staticmethod
    def _orchestrator_runtime_chains_dir():
        """Writable chains dir for frozen builds ('' outside them).

        A packed build cannot write next to its bundled chains and loses them
        on update, so learned artifacts (and their resolution) live under the
        durable runtime dir there.
        """
        try:
            import sys as _sys
            if not getattr(_sys, 'frozen', False):
                return ''
            from AI.runtime_paths import get_runtime_dir
            return os.path.join(str(get_runtime_dir()), 'chains')
        except Exception:
            return ''

    def _orchestrator_source_chain_file(self):
        """The chain FILE a learned artifact belongs next to ('' when unknown).

        ``chain_identity`` names the REAL chain (GUI playback may run from a
        deterministic temp copy) and is trusted as-is; the runtime path is used
        only when it is a real file outside the temp dir.  Falls back to the
        shipped entry chain so the artifact still lands somewhere durable.
        """
        identity = str(getattr(self, 'chain_identity', '') or '')
        if identity and os.path.isfile(identity):
            return identity
        import tempfile
        try:
            tmp = os.path.normcase(os.path.normpath(tempfile.gettempdir()))
        except Exception:
            tmp = ''
        for cand in (
            getattr(self, 'chain_file_path', '') or '',
            getattr(getattr(self, 'sequence_executor', None), 'chain_file_path', '') or '',
        ):
            cand = str(cand or '')
            if not cand or not os.path.isfile(cand):
                continue
            norm = os.path.normcase(os.path.normpath(cand))
            if tmp and norm.startswith(tmp):
                continue
            return cand
        here = os.path.dirname(os.path.abspath(__file__))
        fallback_dir = os.path.normpath(
            os.path.join(here, '..', '..', '..', '..', 'chains'))
        fallback = os.path.join(fallback_dir, 'ORCHESTRATOR.json')
        return fallback if os.path.isfile(fallback) else ''

    def _orchestrator_freeze_learned_chain(self, goal, trace, brains, node,
                                            stop_reason, chains=None):
        """Compile a verified successful run into a deterministic chain.

        Gates (ALL required): not disabled by ``LOOPER_LEARN``, the run ended
        naturally (``stop_reason == 'done'`` — never after ESC / stop), EVERY
        step verified True, every executed step was a library chain whose file
        is orchestrator-free on disk (re-validated) — whether it was dispatched
        as a BRAIN or as a step-sized chain from the ``chains`` port — and at
        least TWO steps (a single step IS the library chain already — composing
        it adds nothing).

        Returns the written path ('' when skipped or impossible).
        """
        try:
            if not self._orchestrator_learn_enabled():
                return ''
            if stop_reason != 'done' or not trace:
                return ''
            # The artifact belongs next to the chain that HOSTS this
            # orchestrator: when the runtime node id is not in the source file
            # (a harness, a node recreated in the editor, a stale temp copy we
            # cannot resolve), writing there would drop a learned chain into a
            # chain that never ran it — measured twice on 2026-09-23/24, when a
            # test leaked learned files and imports into the shipped
            # ORCHESTRATOR.json.  Skip instead; the run still answers.
            if not self._orchestrator_source_hosts_orchestrator(node):
                logger.info(
                    "[LEARN] freeze skipped: orchestrator %r is not in the "
                    "source chain — the artifact would land in the wrong chain",
                    _orchestrator_owner_id(node),
                )
                return ''
            # Brain steps and direct-chain steps live in two lists; both kinds
            # carry kind='chain' and a chain_file, so one lookup covers them
            # (brains key their id as 'brain_id', collected chains as
            # 'chain_id' — the trace always writes brain_id).
            by_id = {(b.get('brain_id') or b.get('chain_id')): b
                     for b in list(brains or []) + list(chains or [])}
            # A step whose text copies a wired chain's own DESCRIPTION means
            # the planner echoed reference material instead of decomposing
            # the request — the run executed the library's prose, not the
            # user's goal, and must never be frozen under that goal (measured
            # 2026-09-25: 'Learned from a verified run: 1) go to linkedin ...'
            # became a plan line; the detached run was frozen and the artifact
            # then matched the same goal as the whole job).
            _norm = lambda s: ' '.join(str(s or '').split()).lower()
            _desc_norms = [
                _norm(w.get('description'))
                for w in list(brains or []) + list(chains or [])
                if _norm(w.get('description'))
            ]
            steps = []
            for t in trace:
                b = by_id.get(t.get('brain_id'))
                if not b or str(b.get('kind') or '') != 'chain':
                    logger.info(
                        "[LEARN] freeze skipped: step %r was not a chain brain",
                        t.get('brain'),
                    )
                    return ''
                if t.get('ok') is not True:
                    logger.info(
                        "[LEARN] freeze skipped: step %r verdict=%r",
                        t.get('brain'), t.get('ok'),
                    )
                    return ''
                if not b.get('chain_file'):
                    return ''
                _step_norm = _norm(t.get('step'))
                if _step_norm and any(
                        _step_norm == d
                        or (len(_step_norm) >= 30
                            and (_step_norm in d or d in _step_norm))
                        for d in _desc_norms):
                    logger.info(
                        "[LEARN] freeze skipped: step %r copies a wired "
                        "chain's own description — the plan did not decompose "
                        "the request", str(t.get('step'))[:80],
                    )
                    return ''
                steps.append(b)
            if len(steps) < 2:
                logger.info(
                    "[LEARN] freeze skipped: a single chain step IS the library "
                    "chain — nothing to compose",
                )
                return ''
            try:
                try:
                    from ....chain_import_ports import chain_contains_orchestrator
                except ImportError:
                    from player.chain_import_ports import chain_contains_orchestrator
            except Exception:
                return ''
            files = []
            labels = []
            for b in steps:
                path = str(b.get('chain_file') or '')
                if not os.path.exists(path):
                    logger.info("[LEARN] freeze skipped: %s missing on disk", path)
                    return ''
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                if chain_contains_orchestrator(cfg, os.path.dirname(path)):
                    logger.info(
                        "[LEARN] freeze skipped: %s needs inference (orchestrator)",
                        os.path.basename(path),
                    )
                    return ''
                files.append((path, str(b.get('name') or '')))
                labels.append(
                    str(cfg.get('description') or '').strip()
                    or str(b.get('name') or ''))
            # Coverage gate: freeze only when every executed step BELONGS to
            # the request — the object-word rule the whole-job rung enforces
            # at MATCH time, applied at CREATION time.  The completeness
            # direction is deliberately not required ('open' vs 'navigates'
            # is a legitimate synonym gap); the unasked direction is what a
            # detached plan fails, and that is the class that used to get
            # frozen and then hijack the goal it claimed (measured
            # 2026-09-25: 'go to linkedin / my network / homelinkedin' as the
            # artifact for 'get context off the website ...').
            generic = _orchestrator_library_generic(
                [c.get('signal') for c in (chains or [])])
            goal_key = _orchestrator_directive_key(goal, generic)
            step_keys = [sorted(_orchestrator_directive_key(lbl, generic))
                         for lbl in labels]
            _covered = bool(goal_key) and bool(step_keys) and all(step_keys)
            if _covered:
                for k in step_keys:
                    if not (set(k) & goal_key):
                        _covered = False
                        break
            if not _covered:
                logger.info(
                    "[LEARN] freeze skipped: the executed steps do not "
                    "belong to the request (%s)",
                    self._orchestrator_learned_gap(
                        goal_key, {'steps_keys': step_keys}),
                )
                return ''
            written = self._orchestrator_write_learned_chain(goal, files)
            if written:
                self._orchestrator_attach_learned_chain(written, node)
            return written
        except Exception as e:
            logger.warning("[LEARN] freeze failed: %s", e)
            return ''

    def _orchestrator_bump_learned_stats(self, chain, ok):
        """Usage bookkeeping on the artifact file (see the eviction policy).

        ``runs`` on every whole-job execution, ``wins`` when that execution
        verified.  Written atomically; failures are non-fatal — bookkeeping
        must never break a run.
        """
        path = str(chain.get('chain_file') or '')
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            routing = cfg.get('routing') or {}
            routing['runs'] = int(routing.get('runs') or 0) + 1
            if ok:
                routing['wins'] = int(routing.get('wins') or 0) + 1
            routing['last_run'] = time.time()
            cfg['routing'] = routing
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning(
                "[LEARN] stats write failed for %s: %s",
                os.path.basename(path), e,
            )

    def _orchestrator_retire_learned_chain(self, chain):
        """Mark a losing artifact retired, in place.

        It is skipped from now on (the eviction policy in the whole-job
        rung).  The file and its wire stay — inert — so a human can inspect
        what was retired and why; only automatic matching stops.
        """
        path = str(chain.get('chain_file') or '')
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            cfg['retired'] = True
            cfg['retired_at'] = time.time()
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, path)
            logger.warning(
                "[LEARN] retired learned chain %s (runs=%s wins=%s) — it "
                "will not be matched again",
                os.path.basename(path), chain.get('runs'), chain.get('wins'),
            )
        except Exception as e:
            logger.warning(
                "[LEARN] retire failed for %s: %s", os.path.basename(path), e,
            )

    def _orchestrator_write_learned_chain(self, goal, files, pending=False):
        """Persist the composition: one chain_import per step, in run order.

        Composition only — never copies of nodes: the human layer keeps owning
        the actions, a library fix propagates to every learned chain, and the
        file stays tiny (imports + routing).  ``input → imports → output`` is
        the execution shape; the goal feeds the first import's Input nodes so
        their ``agent_modifiable`` slots extract whatever varies per request.
        """
        import re as _re
        import uuid as _uuid
        source = self._orchestrator_source_chain_file()
        _base_candidates = []
        if source:
            _base_candidates.append(
                os.path.join(os.path.dirname(source), 'learned'))
        _rt_chains = self._orchestrator_runtime_chains_dir()
        if _rt_chains:
            _base_candidates.append(os.path.join(_rt_chains, 'learned'))
        base_dir = ''
        for _cand in _base_candidates:
            try:
                os.makedirs(_cand, exist_ok=True)
                _probe = os.path.join(_cand, '.write_test')
                with open(_probe, 'w', encoding='utf-8') as f:
                    f.write('')
                os.remove(_probe)
                base_dir = _cand
                break
            except Exception:
                continue
        if not base_dir:
            logger.warning(
                "[LEARN] no writable learned dir (tried %s)", _base_candidates,
            )
            return ''
        slug = _re.sub(
            r'[^a-z0-9]+', '_', str(goal or '').lower()).strip('_')[:40] or 'task'
        # ONE artifact per goal: a repeat run of the same task REBUILDS its
        # learned chain in place instead of piling up timestamped twins (the
        # wiring then sees the same file and never adds a second import).
        # ``pending=True`` is the ASSEMBLY case: a unique '.pending_' name that
        # collides with nothing and gets renamed only after verification.
        out_path = ''
        if pending:
            out_path = os.path.join(
                base_dir,
                ".pending_%s_%s.json" % (time.strftime('%Y%m%d-%H%M%S'), slug),
            )
        else:
            try:
                for _name in sorted(os.listdir(base_dir)):
                    if (_name.startswith('learned_')
                            and _name.endswith('_' + slug + '.json')):
                        out_path = os.path.join(base_dir, _name)
                        break
            except OSError:
                out_path = ''
            if not out_path:
                out_path = os.path.join(
                    base_dir,
                    "learned_%s_%s.json" % (time.strftime('%Y%m%d-%H%M%S'), slug),
                )

        def _new_id():
            return '0xl' + _uuid.uuid4().hex[:9]

        in_id, out_id = _new_id(), _new_id()
        import_ids = [_new_id() for _ in files]
        imports = []
        for i, (path, _name) in enumerate(files):
            target = import_ids[i + 1] if i + 1 < len(files) else out_id
            imports.append({
                'type': 'chain_import',
                'chain_file_path': path,
                'import_mode': 'full',
                'import_kind': 'chain',
                'prefix': os.path.splitext(os.path.basename(path))[0],
                'loop_count': 1,
                'extra_delay': 0.0,
                'enabled': True,
                'run_in_sandbox': False,
                'show_sandbox_window': True,
                'emit_data': False,
                'data_output_nodes': [],
                'node_id': import_ids[i],
                'position': [420.0 + 60.0 * i, 200.0 + 40.0 * i],
                'connections': [
                    {'output_port': 'output',
                     'target_node_id': target, 'input_port': 'input'},
                ],
            })
        run_id = ''
        try:
            from player.agentic_ops import run_memory as _rm
            run_id = _rm.current_run_id()
        except Exception:
            run_id = ''
        # The artifact SAYS IN STEPS WHAT IT DOES, each step described by the
        # library chain it imports — that is what the whole-job rung matches
        # intent against (no file reads), what a human reads in the editor, and
        # what a LATER artifact gets as the description of this step when it
        # composes this artifact in turn.
        steps = []
        for path, sname in files:
            desc = ''
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    desc = str((json.load(f) or {}).get('description') or '')
            except Exception:
                desc = ''
            steps.append({
                'name': str(sname or os.path.splitext(
                    os.path.basename(path))[0]),
                'description': desc,
                'file': str(path),
            })
        steps_text = '; '.join(
            f"{i + 1}) {s['name']} — {s['description']}" if s['description']
            else f"{i + 1}) {s['name']}"
            for i, s in enumerate(steps)
        )
        payload = {
            'sequences': [], 'conditional_nodes': [], 'llm_nodes': [],
            'chain_import_nodes': imports, 'form_filler_nodes': [],
            'code_nodes': [], 'container_nodes': [], 'context_nodes': [],
            'input_nodes': [{
                'type': 'input', 'label': 'goal', 'default_value': '',
                'user_prompt': '', 'passthrough': True, 'web_mode': False,
                'agent_modifiable': True, 'decision_mode': False,
                'node_id': in_id, 'position': [220.0, 200.0],
                'connections': [
                    {'output_port': 'output',
                     'target_node_id': import_ids[0], 'input_port': 'input'},
                    {'output_port': 'data',
                     'target_node_id': import_ids[0], 'input_port': 'input'},
                ],
            }],
            'orchestrator_nodes': [], 'handle_nodes': [], 'mcp_nodes': [],
            'output_nodes': [{
                'type': 'output', 'label': 'Result', 'variable_name': '',
                'agent_visible': True, 'show_rating': True,
                'render_mode': 'text', 'node_id': out_id,
                'position': [420.0 + 60.0 * len(files),
                             200.0 + 40.0 * len(files)],
                'connections': [],
            }],
            'web_sequences': [],
            'description': "Learned from a verified run: " + steps_text[:600],
            'steps': steps,
            'routing': {
                'examples': [str(goal or '').strip()[:200]],
                'wins': 0, 'runs': 0,
                'created_at': time.time(), 'run_id': run_id,
            },
            'learned': True,
            'source_goal': str(goal or '')[:300],
        }
        tmp = out_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            # Pure ASCII on purpose: a learned artifact must load through EVERY
            # reader, including the GUI's (locale-codec) and any tool that
            # predates the utf-8 load fix (measured 2026-09-24: a single '→' in
            # the description made the file unloadable).
            json.dump(payload, f, indent=2)
        with open(tmp, 'r', encoding='utf-8') as f:
            json.load(f)  # validate before publishing
        os.replace(tmp, out_path)
        logger.info(
            "[LEARN] wrote learned chain: %s (%d steps)", out_path, len(files),
        )
        return out_path

    def _orchestrator_attach_learned_chain(self, learned_path, node):
        """Wire a fresh learned chain into this orchestrator's 'chains' port.

        Edits the chain file the run belongs to (identity first — never a temp
        copy), post-run so json_cache cannot serve a stale copy mid-run, and
        atomically (temp + replace) with a one-time .bak.  Failures leave the
        file untouched and are logged: the artifact itself is already safe on
        disk and can be wired by hand.
        """
        source = self._orchestrator_source_chain_file()
        orch_id = _orchestrator_owner_id(node)
        if not source or not orch_id or not os.path.exists(source):
            logger.info(
                "[LEARN] learned chain written but not wired (no chain file "
                "to edit): %s", learned_path,
            )
            return False
        try:
            with open(source, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception as e:
            logger.warning("[LEARN] cannot read %s: %s", source, e)
            return False
        if not isinstance(cfg, dict):
            return False
        # The edge must land on an orchestrator THAT EXISTS IN THIS FILE: the
        # runtime node id can be stale (the node was recreated in the editor,
        # or a harness ran without a chain identity) and a dangling target is
        # silently DROPPED by the builder — which turns the learned chain into
        # an orphan starting node and empties the 'chains' port (measured
        # 2026-09-23).  Match by id, else use the file's only orchestrator,
        # else refuse and leave the artifact on disk to wire by hand.
        _file_orch_ids = [
            str(n.get('node_id') or n.get('id') or '')
            for n in (cfg.get('orchestrator_nodes') or [])
            if isinstance(n, dict)
        ]
        if orch_id in _file_orch_ids:
            target_id = orch_id
        elif len(_file_orch_ids) == 1 and _file_orch_ids[0]:
            target_id = _file_orch_ids[0]
        else:
            logger.warning(
                "[LEARN] learned chain kept but NOT wired: orchestrator %r "
                "is not in %s (found %s)",
                orch_id, os.path.basename(source), _file_orch_ids,
            )
            return False
        imports = cfg.setdefault('chain_import_nodes', [])
        if not isinstance(imports, list):
            return False
        _learned_base = os.path.basename(learned_path)
        for n in imports:
            if isinstance(n, dict) and os.path.basename(str(
                    n.get('chain_file_path') or '')) == _learned_base:
                return False  # already wired — never twice
        import uuid as _uuid
        nid = '0xl' + _uuid.uuid4().hex[:9]
        imports.append({
            'type': 'chain_import',
            'chain_file_path': learned_path,
            'import_mode': 'full',
            'import_kind': 'chain',
            'prefix': os.path.splitext(_learned_base)[0],
            'loop_count': 1,
            'extra_delay': 0.0,
            'enabled': True,
            'run_in_sandbox': False,
            'show_sandbox_window': True,
            'emit_data': False,
            'data_output_nodes': [],
            'node_id': nid,
            'position': [200.0, 400.0 + 80.0 * len(imports)],
            'connections': [
                {'output_port': 'output',
                 'target_node_id': target_id, 'input_port': 'chains'},
            ],
        })
        try:
            backup = source + '.bak'
            if not os.path.exists(backup):
                with open(source, 'r', encoding='utf-8') as f:
                    original = f.read()
                with open(backup, 'w', encoding='utf-8') as f:
                    f.write(original)
            tmp = source + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                # Same ASCII-only rule as the artifact writer — the host chain
                # must stay loadable by every reader.
                json.dump(cfg, f, indent=2)
            with open(tmp, 'r', encoding='utf-8') as f:
                json.load(f)  # validate before replacing
            os.replace(tmp, source)
        except Exception as e:
            logger.warning("[LEARN] wiring failed (learned chain kept): %s", e)
            try:
                if os.path.exists(source + '.tmp'):
                    os.remove(source + '.tmp')
            except Exception:
                pass
            return False
        logger.info(
            "[LEARN] wired learned chain %s → %s ('chains' port)",
            _learned_base, os.path.basename(source),
        )
        return True

    def _orchestrator_resolve_chain_path(self, data):
        """Resolve the chain file path of a chain_import node (source + frozen)."""
        chain_file = (
            data.get('chain_file_path') or data.get('chain_file')
            or (data.get('import_data') or {}).get('chain_file_path')
            or (data.get('import_data') or {}).get('chain_file')
            or ''
        )
        if not chain_file:
            return ''
        candidates = [chain_file]
        if not str(chain_file).lower().endswith('.json'):
            candidates.append(str(chain_file) + '.json')
        # Frozen builds: learned chains (and their imports) resolve by basename
        # from the durable runtime dir — the recorded absolute source path does
        # not exist inside the bundle.
        _rt_chains = self._orchestrator_runtime_chains_dir()
        if _rt_chains:
            _base = os.path.basename(str(chain_file))
            for _cand in (
                os.path.join(_rt_chains, _base),
                os.path.join(_rt_chains, 'learned', _base),
                os.path.join(_rt_chains, _base + '.json'),
                os.path.join(_rt_chains, 'learned', _base + '.json'),
            ):
                candidates.append(_cand)
        try:
            base_dir = getattr(self.sequence_executor, 'chain_file_dir', None)
        except Exception:
            base_dir = None
        if base_dir:
            candidates.append(os.path.join(base_dir, os.path.basename(str(chain_file))))
            candidates.append(os.path.join(
                base_dir, os.path.basename(str(chain_file)) + '.json',
            ))
        for cand in candidates:
            try:
                if cand and os.path.exists(cand):
                    return cand
            except Exception:
                continue
        return str(chain_file)

    def _orchestrator_brain_description(self, chain_path, fallback_name, tools=None):
        """Runtime-derived brain description: chain description, input labels,
        and (informational) a router brain's atomic tool names — the worker
        still chooses which of its tools to run for the requested step."""
        desc = ''
        inputs_note = ''
        try:
            if chain_path and os.path.exists(chain_path):
                with open(chain_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                desc = str(cfg.get('description') or '').strip()
                labels = []
                for inode in (cfg.get('input_nodes') or []):
                    if not isinstance(inode, dict):
                        continue
                    if str(inode.get('agent_modifiable', '')).strip().lower() in ('true', '1', 'yes'):
                        lbl = str(inode.get('label') or '').strip()
                        if lbl:
                            labels.append(lbl)
                if labels:
                    inputs_note = ' [inputs: ' + '; '.join(labels[:5]) + ']'
        except Exception as e:
            logger.debug("[ORCH] description read failed for %s: %s", chain_path, e)
        tools_note = ''
        try:
            names = [str(t.get('alias') or '').strip() for t in (tools or [])]
            names = [n for n in names if n]
            if names:
                tools_note = ' [tools: ' + ', '.join(names) + ']'
        except Exception:
            tools_note = ''
        return (desc or str(fallback_name)) + inputs_note + tools_note

    def _orchestrator_read_chain_description(self, chain_path):
        """Description of a chain file, read at runtime ('' on any failure)."""
        try:
            if chain_path and os.path.exists(chain_path):
                with open(chain_path, 'r', encoding='utf-8') as f:
                    return str((json.load(f) or {}).get('description') or '').strip()
        except Exception:
            pass
        return ''

    def _orchestrator_collect_brain_tools(self, chain_path):
        """Routing tools of a brain, at the boundary — LEVEL-LOCAL, never a walk.

        A brain routes with EITHER router style:
          - an LLM node with chain_imports on its ``tools`` port (USE_TOOL), or
          - a NESTED Orchestrator node with chain_imports on its ``brains``
            port (recursive routing: the brain orchestrates its own brains).
        Both are discovered from the brain's chain FILE — the runtime always
        loads the brain from disk (a parent chain's embedded copy is UI-only) —
        and each entry carries the node's own alias (its ``prefix``, exactly
        what the brain's ``_tool_alias_map`` will hold) plus the tool chain's
        runtime description.  Plain chains (no router) yield [].

        The nested orchestrator's deterministic ``chains`` port is deliberately
        NOT scanned.  Those chains are the ACTION SPACE OF THAT LEVEL (and
        learned artifacts keep accumulating there), and each level re-runs the
        whole ladder with its own discovery: flattening a sub-orchestrator's
        chains into its parent's description would make every ancestor re-list
        its descendants' inventory on every activation, so a deep agent (3
        brains per level, ten levels) would OOM the context before it ever ran.
        Discovery stops at the boundary: what a brain ROUTES WITH, not what a
        brain RUNS.
        """
        tools = []
        if not chain_path or not os.path.exists(chain_path):
            return tools
        try:
            with open(chain_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception as e:
            logger.debug("[ORCH] tool scan failed for %s: %s", chain_path, e)
            return tools
        router_ids = {
            str(n.get('node_id'))
            for n in (list(cfg.get('llm_nodes') or [])
                      + list(cfg.get('orchestrator_nodes') or []))
            if isinstance(n, dict) and n.get('node_id')
        }
        if not router_ids:
            return tools
        seen = set()
        for n in (cfg.get('chain_import_nodes') or []):
            if not isinstance(n, dict):
                continue
            tid = str(n.get('node_id') or '')
            if not tid or tid in seen:
                continue
            if not any(
                isinstance(c, dict)
                and str(c.get('input_port') or '') in ('tools', 'brains')
                and str(c.get('target_node_id') or '') in router_ids
                for c in (n.get('connections') or [])
            ):
                continue
            seen.add(tid)
            raw_path = str(n.get('chain_file_path') or n.get('chain_file') or '')
            tool_file = self._orchestrator_resolve_chain_path(
                {'chain_file_path': raw_path}) if raw_path else ''
            base = os.path.splitext(os.path.basename(tool_file))[0] if tool_file else ''
            alias = str(n.get('prefix') or '').strip() or base or tid
            tools.append({
                'node_id': tid,
                'alias': alias,
                'chain_file': tool_file or raw_path,
                'description': self._orchestrator_read_chain_description(tool_file),
            })
        return tools

    # ---------------------------------------------------------------- picking

    def _orchestrator_goal_reached(self, goal, trace):
        """One Laya read: is the goal already achieved (steps-only premise)?

        The same measured probe the picker runs (``_DONE_PROBE_MIN``, steps
        only — result prose reads as a completed claim), pulled out for the
        chains-only orchestrator: that loop never reaches the picker, and a
        goal served entirely by deterministic chains must still be able to end
        `done` — the freeze refuses every other stop reason.
        """
        if not trace:
            return False
        try:
            from AI import laya_client as _lc
            if not _lc.available() and not _lc.ensure_running():
                return False
            premise = (
                f"Goal: {goal}\nSteps taken so far:\n"
                f"{self._orchestrator_render_steps(trace)}"
            )
            p = _lc.noul(premise, "The goal has already been fully achieved.")
            if p is None:
                return False
            logger.info(
                "[ORCH] Chains-port done-probe: %.3f (done when > %.2f)",
                p, _DONE_PROBE_MIN,
            )
            if p <= _DONE_PROBE_MIN:
                return False
            return self._orchestrator_done_is_believable(goal, trace)
        except Exception:
            return False

    def _orchestrator_done_is_believable(self, goal, trace):
        """Gate: believe a 'done' pick only once the steps can cover the goal.

        Laya's noul separates single-action goals well, but on a COMPOUND
        goal it reads "achieved" as soon as the most distinctive clause
        appears in the steps — the sequenced remainder ("...and then check
        the screen and tell me what you see") scored p_done 0.905 after ONE
        step on the real 2026-09-23 run, so the loop stopped before the
        vision brain ever ran (the nested relay blob had the same shape:
        0.916 with zero steps).  Claim wording, inverted claims, per-part
        probing and an LLM supervisor were all measured and none separated
        the trap — the honest fix is structural: a goal that explicitly
        sequences N actions must have had at least N steps before 'done' is
        taken at face value.  Beyond the floor the probe still decides.
        """
        return len(trace or []) >= self._orchestrator_action_floor(goal)

    @staticmethod
    def _orchestrator_action_floor(goal, cap=4):
        """Minimum steps a goal's own sequencing asks for ('and then', ';').

        Only TEMPORAL connectors split: a bare 'and' joins one action with
        its object ("open linkedin and search jobs"), not two actions.
        Capped so a rambling goal cannot force a long march.
        """
        import re as _re
        text = str(goal or '').lower()
        parts = [
            p for p in _re.split(
                r'\b(?:and then|then|after that|afterwards|afterward|followed by)\b|;',
                text,
            ) if p.strip()
        ]
        return max(1, min(len(parts), cap))

    def _orchestrator_llm_pick(self, goal, trace_text, brains, node_data, stop_flag,
                               trace=None, state=None, remaining_reported=False):
        """One short LLM turn choosing the next worker. int | 'done' | 'ask' | None."""
        lines = []
        for i, b in enumerate(brains):
            lines.append(f"{i}: {b['description']}")
        candidates_text = "\n".join(lines)
        state_text = (
            f"\nCURRENT STATE (observed, not claimed):\n"
            f"{str(state)[:_STATE_IN_PROMPT_CHARS]}\n" if state else ""
        )
        prompt = (
            "You are routing a goal across available workers.\n"
            f"GOAL: {goal}\n"
            f"PROGRESS SO FAR:\n{trace_text or '(nothing yet)'}\n"
            f"{state_text}"
            f"AVAILABLE WORKERS:\n{candidates_text}\n\n"
            "Which worker should run next to make progress toward the goal?\n"
            "Answer with EXACTLY one word: the worker number, or DONE if the goal "
            "is already achieved, or ASK if you need the user's input.\n"
            "ANSWER:"
        )
        blocked_done = False
        for attempt in (1, 2):
            retry_prompt = prompt + "\n(One word only. No explanation.)"
            if blocked_done:
                retry_prompt += (
                    "\nThe goal still requires more steps — answer with a "
                    "worker number, not DONE."
                )
            raw = self._orchestrator_llm_call(
                prompt if attempt == 1 else retry_prompt,
                node_data, stop_flag, max_tokens=32,
            )
            if raw is None:
                return None
            pick = self._orchestrator_parse_pick(raw, len(brains))
            log_block(
                logger, logging.INFO,
                f"Orchestrator picker turn (attempt {attempt})", f"raw={raw!r} → {pick!r}",
            )
            if pick == 'done' and (
                    remaining_reported
                    or not self._orchestrator_done_is_believable(goal, trace)):
                logger.info(
                    "[ORCH] Picker said DONE at %d step(s) but %s — asking "
                    "for a worker instead",
                    len(trace or []),
                    ('a brain reported work still unaccomplished'
                     if remaining_reported
                     else 'the goal sequences %d action(s)'
                     % self._orchestrator_action_floor(goal)),
                )
                blocked_done = True
                continue
            if pick is not None:
                return pick
        return None

    def _orchestrator_laya_pick(self, goal, trace_text, brains, trace=None,
                                state=None, remaining_reported=False):
        """Laya-native pick: done-probe, worker choice, conservative ask. int | 'done' | 'ask' | None.

        Deliberately NOT one mixed choice: an option text like "Nothing — the
        goal is already achieved." acts as a magnet in zero-shot choice and
        collapses the pick (measured 9/22).  The composition — two noul
        probes around a worker-only choice — matches the model's trained
        shapes and probes cleanly (0.03 unfinished vs 0.64 finished goals).
        Known limit (measured): the compound-goal HANDOFF (after worker A
        finished its part, a different worker B must take over — e.g.
        LinkedIn opened → vision peek) still favours the already-used
        worker (0.577 vs 0.423).  Free-text "next action" hints flip it
        (0.537) but small-model hints were net-negative (0.60–0.67) and
        anonymous numbered options break continue-same-brain (0.489 vs
        0.511) — description repair, not picker tricks, is the remedy.
        Done-probe accuracy (measured on real failing traces, 2026-09-23):
        judging with the result prose included reads LLM-authored claims
        ("I have opened LinkedIn and observed ...") and tool-description
        echoes as completion — p_done 0.89 stopped the loop after one step;
        the STEPS-ONLY premise separates deterministically (genuinely
        covered 0.587 vs false 0.506 / 0.144), threshold 0.55.
        """
        try:
            from AI.laya_client import choice as _choice, noul as _noul
        except Exception:
            return None
        # Done-probe premise: STEPS ONLY — never the result prose (see the
        # docstring; claims read as facts and end the loop prematurely).
        steps_text = (
            self._orchestrator_render_steps(trace)
            if trace is not None else (trace_text or '')
        )
        done_premise = (
            f"Goal: {goal}\nSteps taken so far:\n{steps_text or '(none yet)'}"
        )
        p_done = _noul(done_premise, "The goal has already been fully achieved.")
        if p_done is None:
            return None
        logger.info(
            "[ORCH] Laya done-probe (steps-only premise): %.3f (done when > 0.55)",
            p_done,
        )
        if p_done > _DONE_PROBE_MIN:
            if remaining_reported:
                logger.info(
                    "[ORCH] Laya done-probe fired (%.3f) but a brain reported "
                    "directives still unaccomplished — the report wins "
                    "(structure beats the probe); continuing to a worker",
                    p_done,
                )
            elif self._orchestrator_done_is_believable(goal, trace):
                return 'done'
            else:
                logger.info(
                    "[ORCH] Laya done-probe fired (%.3f) at %d step(s) but the "
                    "goal sequences %d action(s) — continuing to a worker",
                    p_done, len(trace or []),
                    self._orchestrator_action_floor(goal),
                )

        premise = f"Goal: {goal}\nProgress so far:\n{trace_text or '(nothing yet)'}"
        if state:
            premise += f"\nCurrent state: {str(state)[:_STATE_IN_PROMPT_CHARS]}"
        picked_at = self._orchestrator_laya_worker(goal, brains, trace_text,
                                                   state)
        if picked_at is None:
            return None
        # Conservative ask: only override a worker pick on a strong signal.
        p_ask = _noul(premise, "The user must be asked for input before any worker can proceed.")
        if p_ask is not None and p_ask > 0.8:
            logger.info("[ORCH] Laya ask-probe: %.3f — asking the user", p_ask)
            return 'ask'
        return picked_at

    def _orchestrator_laya_worker(self, goal, brains, trace_text, state):
        """ONE typed question: which brain's scope covers this step? (index|None)

        The step's OWN objects decide who MAY serve it (the same rule as the
        chains gate): among the brains whose description names at least one
        of the step's NON-AMBIENT objects, the one naming the MOST objects
        wins deterministically — no engine, no load — and a TIE goes to the
        typed question restricted to the tied namers.  Only when NO
        description names any object does the whole field go to the question
        (the pre-objects behavior).  Ambient words come from the observed
        state: what the current screen already shows describes the
        environment, not the task (measured 2026-09-25: 'Open Chrome browser'
        went to the linkedin brain over the word 'chrome', whose chains gate
        then navigated the user's open tab to linkedin.com).  The
        descriptions stay the options — the read that assigns a step to a
        worker, and the same read that groups the following directives by
        that worker's scope (see _orchestrator_scope_group).
        """
        try:
            from AI.laya_client import choice as _choice
        except Exception:
            return None
        ambient = (_orchestrator_directive_key(state, frozenset())
                   if state else set())
        goal_key = _orchestrator_directive_key(goal, frozenset()) - ambient
        # The FIRST word of a step is its VERB (the planning protocol's
        # shape): a brain that merely shares the verb ('opens LinkedIn' vs
        # 'Open Chrome browser') does not name the step's OBJECT — measured
        # 2026-09-25, where 'open' alone kept the linkedin brain in the pool
        # for a browser step.  The namer rule reads the objects only.
        _w = str(goal or '').split()
        _verb = _orchestrator_stem(_w[0].strip('",.;:').lower()) if _w else ''
        obj_key = goal_key - {_verb}
        scored = []
        refused = []
        for i, b in enumerate(brains):
            # The composer appends machine notes AFTER the prose (' [inputs:
            # …]', ' [tools: …]').  A note is not a claim the brain makes,
            # so the split reads the description proper: 'screen_check' in
            # vision's tool note tokenized to 'check' inside its refused
            # tail and the brain was excluded from every 'Check … the
            # screen' step its own prose names (measured 2026-09-25 — the
            # step fell to a blind choice and ran the linkedin brain).
            desc = str(b.get('description') or '')
            for _mark in (' [inputs:', ' [tools:'):
                desc = desc.split(_mark)[0]
            asserted, negated = _orchestrator_split_negation(desc)
            if _verb and _verb in _orchestrator_directive_key(
                    negated, frozenset()):
                # The brain says it does NOT do this action — never a
                # candidate for this step (see _orchestrator_split_negation).
                refused.append(i)
                continue
            if not obj_key:
                continue
            # Only the ASSERTED part of the description may nominate.
            shared = obj_key & _orchestrator_directive_key(
                asserted, frozenset())
            if shared:
                scored.append((len(shared), i, shared))
        if refused:
            logger.info(
                "[ORCH] Worker pick: excluded %s — their description refuses "
                "the step's verb",
                ', '.join(str(brains[i].get('name') or i) for i in refused[:4]),
            )
        namers = []
        if scored:
            scored.sort(key=lambda t: (-t[0], t[1]))
            top_n = scored[0][0]
            tops = [t for t in scored if t[0] == top_n]
            if len(tops) == 1:
                logger.info(
                    "[ORCH] Worker pick (deterministic): %s names %s — no "
                    "engine read needed", brains[tops[0][1]].get('name'),
                    ', '.join(sorted(tops[0][2])[:4]),
                )
                return tops[0][1]
            namers = [t[1] for t in tops]
        if not namers:
            _refused = set(refused)
            namers = [i for i in range(len(brains)) if i not in _refused]
        pool = [brains[i] for i in namers] if namers else brains
        premise = f"Goal: {goal}\nProgress so far:\n{trace_text or '(nothing yet)'}"
        if state:
            premise += f"\nCurrent state: {str(state)[:_STATE_IN_PROMPT_CHARS]}"
        worker_opts = [f"Run the worker '{b['name']}': {b['description']}" for b in pool]
        picked = _choice(premise, "Which option makes progress toward the goal?",
                         {o: "" for o in worker_opts})
        if picked is None:
            return None
        try:
            return brains.index(pool[worker_opts.index(picked)])
        except ValueError:
            return None

    @staticmethod
    def _orchestrator_parse_pick(text, n_candidates):
        """Strict one-token parse of a picker answer. No prose scanning."""
        if text is None:
            return None
        clean = str(text)
        # Strip reasoning blocks — the only regex use on model output here is a
        # reasoning-tag strip, never content matching.
        import re as _re
        try:
            clean = _re.sub(r'<[^>]*think[^>]*>.*?</[^>]*think[^>]*>', ' ', clean, flags=_re.DOTALL)
            clean = _re.sub(r'<[^>]*thinking[^>]*>.*?</[^>]*thinking[^>]*>', ' ', clean, flags=_re.DOTALL)
        except Exception:
            pass
        tokens = clean.strip().split()
        if not tokens:
            return None
        token = tokens[0].strip('.,;:!?\'"()[]{}*`#').lower()
        if token.isdigit():
            idx = int(token)
            if 0 <= idx < n_candidates:
                return idx
            if 1 <= idx <= n_candidates:
                # Models often answer 1-based; accept it as a fallback.
                return idx - 1
            return None
        if token in ('done', 'finished', 'complete', 'completed', 'achieved'):
            return 'done'
        if token in ('ask', 'user', 'question'):
            return 'ask'
        return None

    # ---------------------------------------------------------- step scoping

    def _orchestrator_scope_step(self, goal, trace, brain, node_data, stop_flag,
                                 state=None):
        """ONE short turn: turn goal + progress + state into the next step.

        This is the orchestrator's core job — scope, don't dictate tools.  The
        returned instruction rides into ``_chain_input_context`` and the
        worker's own router maps it to whichever tool serves it (USE_TOOL).
        None on any failure; the caller falls back to the description task.
        """
        state_text = (
            f"CURRENT STATE (observed, not claimed):\n"
            f"{str(state)[:_STATE_IN_PROMPT_CHARS]}\n" if state else ""
        )
        prompt = (
            "You are the orchestrator of a goal loop. Scope the goal into ONE next step.\n"
            f"GOAL: {str(goal or '')[:1200]}\n"
            f"PROGRESS SO FAR:\n{self._orchestrator_render_trace(trace) or '(nothing yet)'}\n"
            f"{state_text}"
            f"WORKER: {brain['name']} - {brain['description']}\n\n"
            "Write ONE short imperative sentence telling this worker exactly "
            "what to do next to advance the goal, given the progress above — "
            "one concrete action; do not restate the goal.\n"
            "STEP:"
        )
        raw = self._orchestrator_llm_call(prompt, node_data, stop_flag, max_tokens=128)
        scoped = self._orchestrator_parse_step(raw)
        log_block(
            logger, logging.INFO,
            f"Orchestrator step scoping → {brain['name']}",
            f"raw={raw!r} → {scoped!r}",
        )
        return scoped

    @staticmethod
    def _orchestrator_parse_step(raw):
        """First non-empty line, reasoning/label/bullet-stripped, capped.

        No content matching: the step is free text by design (the worker's
        router does the semantic routing); only formatting noise is removed.
        """
        if raw is None:
            return None
        text = str(raw)
        import re as _re
        try:
            text = _re.sub(r'<[^>]*think[^>]*>.*?</[^>]*think[^>]*>', ' ', text, flags=_re.DOTALL)
            text = _re.sub(r'<[^>]*thinking[^>]*>.*?</[^>]*thinking[^>]*>', ' ', text, flags=_re.DOTALL)
        except Exception:
            pass
        for line in text.splitlines():
            if line.strip():
                text = line.strip()
                break
        else:
            return None
        for marker in ('NEXT STEP:', 'STEP:'):
            if text.upper().startswith(marker):
                text = text[len(marker):].strip()
                break
        text = text.strip(' \t-*"\'`')
        if not text:
            return None
        return text[:300]

    # -------------------------------------------------------------- ask-user

    def _orchestrator_ask_user(self, node, goal, trace, node_data):
        """Ask the user for guidance through the richest available channel.

        Prefers the ask-user v2 protocol (choices/attachments-capable) and
        falls back to the legacy text callback; returns the reply string, or
        None when no channel exists / the reply is empty.
        """
        try:
            has_legacy = self.llm_executor.get_variable("_ask_user_callback") is not None
            has_rich = self.llm_executor.get_variable("_ask_user_v2_callback") is not None
        except Exception:
            has_legacy = False
            has_rich = False
        if not (has_legacy or has_rich):
            return None
        question = (
            f"The agent is working toward: {goal}\n"
            f"Progress so far:\n{self._orchestrator_render_trace(trace) or '(nothing yet)'}\n"
            "What should it do next?"
        )
        reply = None
        if hasattr(self, '_ask_user_v2'):
            try:
                reply, _atts = self._ask_user_v2(
                    node_data, question, 'text',
                    {'text': True, 'images': False, 'documents': False}, '',
                )
            except Exception as e:
                logger.warning("[ORCH] ask-user v2 failed: %s", e)
                reply = None
        if reply is None and has_legacy:
            try:
                reply = self.llm_executor.get_variable("_ask_user_callback")(question)
            except Exception as e:
                logger.warning("[ORCH] ask-user callback failed: %s", e)
                reply = None
        if reply is None or not str(reply).strip():
            return None
        return str(reply)

    # ----------------------------------------------------------- invocation

    def _orchestrator_compose_prompt(self, mission, trace, brain, step_text=None):
        """The prompt the mini brain receives through _chain_input_context.

        The MISSION, not the original request: a worker gets the tasks inside
        its scope (one scoped step for a specialist, the covered directives for
        a nested brain) plus the progress so far — passing the whole goal to
        every instance is what makes a long request unaffordable.  The
        orchestrator scopes the next STEP (it never names tools): the worker's
        own router then picks the tool that serves that step via USE_TOOL.
        When the scoping turn failed, the worker's description is the task
        (previous behavior).
        """
        head = f"Your mission: {mission}\n"
        # Intent anchor: the ROOT goal rides above the mission (capped) unless
        # it IS the mission — a worker, and a nested planner reading this
        # prompt, never loses the request the run began with.
        try:
            _root = str(
                self.llm_executor.get_variable("_chain_root_goal") or ''
            ).strip()
        except Exception:
            _root = ''
        _norm = lambda _t: ' '.join(str(_t or '').lower().split())
        if _root and _norm(_root) != _norm(mission):
            head += f"Root goal: {_root[:200]}\n"
        head += (
            f"Progress so far:\n{self._orchestrator_render_trace(trace) or '(nothing yet)'}\n"
        )
        step_text = str(step_text or '').strip()
        if step_text:
            return head + f"Your next step: {step_text}"
        return head + f"Your task: {brain['description']}"

    @staticmethod
    def _orchestrator_clean_result(result):
        """Strip router-only headers from a collected tool result.

        ``chain_tool_last_result_text`` starts with ``Tool ID: <hex>`` /
        ``Chain Name`` / ``Chain Description`` lines — they exist so the
        brain's OWN router can see which tool just ran.  Carried into the
        next brain's context they become routing poison: the router is
        told to answer ``USE_TOOL:<id>`` and copies the nearest hex id it
        sees (measured 2026-09-23: the LinkedIn brain emitted the
        ORCHESTRATOR's brain-import id as its tool choice).  The trace
        keeps only what the tool actually produced.
        """
        text = str(result or '')
        if not text.lstrip().startswith(('Tool ID:', 'Chain Name:', 'Chain Description:')):
            return text
        kept = []
        for line in text.splitlines():
            s = line.lstrip()
            if s.startswith(('Tool ID:', 'Chain Name:', 'Chain Description:')):
                continue
            kept.append(line)
        cleaned = '\n'.join(kept).strip()
        return cleaned or text

    def _orchestrator_render_trace(self, trace):
        """Compact newest-biased trace text for prompts and synthesis."""
        if not trace:
            return ''
        lines = []
        for t in trace:
            who = str(t.get('brain') or '')
            step = str(t.get('step') or '').strip()
            if step:
                who += f' ({step})'
            lines.append(f"{t['n']}. {who}: {t['result'] or '(no result)'}")
        text = "\n".join(lines)
        if len(text) > _TRACE_PROMPT_CHARS:
            text = '...' + text[-_TRACE_PROMPT_CHARS:]
        return text

    def _orchestrator_render_steps(self, trace):
        """Steps-only rendering (NO result prose) for the done-probe.

        Measured (2026-09-23, real failing traces): with results included,
        LLM-authored completion claims and tool-description echoes read as
        done (p_done 0.89 → the loop stopped after one step); goal coverage
        judged from the steps alone separates deterministically.
        """
        if not trace:
            return ''
        lines = []
        for t in trace:
            who = str(t.get('brain') or '')
            step = str(t.get('step') or '').strip()
            lines.append(f"{t['n']}. {who}: {step or '(no step recorded)'}")
        text = "\n".join(lines)
        if len(text) > _TRACE_PROMPT_CHARS:
            text = '...' + text[-_TRACE_PROMPT_CHARS:]
        return text

    # ------------------------------------------------------------ synthesis

    def _orchestrator_synthesize(self, goal, trace, node_data, stop_flag,
                                 remaining=None):
        """Final single-shot LLM synthesis; None on failure (caller falls back).

        The trace is framed as the ONLY evidence of what ran: a small model
        reads the goal (a list of tasks) as a list of accomplished tasks and
        reports victory over a no-op run (measured 2026-09-25: 'the task of
        opening Chrome browser, ... has been completed successfully' — over a
        sub-run whose only executed step was 'go to linkedin').  The remaining
        list is handed in so the honest ending gets REPORTED, not invented.
        """
        system = (
            str(node_data.get('synthesis_system') or '').strip()
            or "You are an assistant reporting the outcome of a completed task."
        )
        remaining = [str(d).strip() for d in (remaining or []) if str(d).strip()]
        remaining_text = ''
        if remaining:
            remaining_text = (
                "Still NOT done (report these as remaining):\n"
                + '\n'.join(f"- {d}" for d in remaining[:6]) + "\n\n"
            )
        prompt = (
            f"{system}\n\n"
            f"The goal was: {goal}\n"
            f"Steps that ACTUALLY ran (the ONLY evidence anything happened):\n"
            f"{self._orchestrator_render_trace(trace)}\n\n"
            f"{remaining_text}"
            "Write the final answer for the user in plain text. Only claim an "
            "action happened if it appears under 'Steps that ACTUALLY ran' — "
            "the goal text is what was ASKED, never evidence it was done. If "
            "work remains, say what was done and what is still to do. Do not "
            "mention worker numbers or internal tooling.\nANSWER:"
        )
        reply = self._orchestrator_llm_call(prompt, node_data, stop_flag, max_tokens=512)
        text = str(reply or '').strip()
        # Prompt-echo guard: small models sometimes answer with bracketed
        # scaffolding instead of an answer (measured 2026-09-23: Qwen3.8-2B
        # returned "[Your complete step-by-step reasoning…]" as the final
        # reply).  A bracketed-only echo is never a real answer — return
        # None so the caller falls back to the recorded trace.
        if text and text.startswith('[') and text.endswith(']') and text.count('[') == 1:
            logger.warning(
                "[ORCH] synthesis reply looks like a prompt echo — "
                "falling back to the trace"
            )
            return None
        return text or None

    # ------------------------------------------------------------ LLM calls

    def _orchestrator_model_config(self, node_data):
        """(engine, model) — node props, else the chain's first LLM node,
        else the app default from AI/config.json (mirrors llm_ops)."""
        engine = str(node_data.get('engine') or 'llamacpp').strip().lower()
        model = str(node_data.get('model') or '').strip()
        if model:
            return engine, model
        try:
            for wnode in (self.workflow_graph or {}).values():
                if wnode.get('type') != 'llm':
                    continue
                wdata = wnode.get('data', {}) or {}
                conf = wdata.get('llm_configuration', {}) or {}
                merged = dict(wdata)
                merged.update(conf)
                if engine in ('llamacpp', 'llama.cpp', 'llama_cpp'):
                    m = str(merged.get('llamacpp_model_path') or '').strip()
                else:
                    m = str(merged.get('model') or '').strip()
                if m:
                    return engine, m
        except Exception:
            pass
        # No node model and no chain LLM node to borrow from (the shipped
        # orchestrator root has neither) — fall back to the app default so the
        # picker/synthesis always have a model.
        if not model:
            try:
                from ...llm_executor_resources.config_utils import get_default_llm_model
                model = get_default_llm_model(engine)
                if model:
                    logger.info(
                        "[ORCH] No node/chain model configured — using the app default model %s",
                        model,
                    )
            except Exception:
                model = ''
        return engine, model

    def _orchestrator_llm_call(self, prompt, node_data, stop_flag, max_tokens=256):
        """One short LLM turn (threaded, no timeout, stop-flag polled). None on failure."""
        engine, model = self._orchestrator_model_config(node_data)
        if not model:
            logger.warning("[ORCH] No model configured and no chain LLM node to borrow from")
            return None

        out = {'text': None, 'error': None}

        def _runner():
            try:
                if engine in ('llamacpp', 'llama.cpp', 'llama_cpp'):
                    import requests as _requests
                    from ...llm_executor_resources.config_utils import (
                        get_default_api_url, resolve_llamacpp_model_path,
                    )
                    api_url = (get_default_api_url() or '').rstrip('/')
                    payload = {
                        "model": resolve_llamacpp_model_path(model),
                        "prompt": prompt,
                        "system": "",
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                        "stream": False,
                        "use_llamacpp": True,
                        # Chat mode with thinking DISABLED: strict one-token
                        # callers must not spend the budget on a reasoning
                        # block (verified against Qwen3.x/SmolLM3: with
                        # thinking on, a 32-token budget returns an empty
                        # content + full reasoning_content; raw completion
                        # returns an immediate EOS on chat-trained models).
                        "chat_template_kwargs": {"enable_thinking": False},
                        "gpu_layers": 0,
                        "threads": -1,
                        "context_size": 0,
                    }
                    resp = _requests.post(
                        f"{api_url}/llamacpp/generate", json=payload, timeout=None,
                    )
                    if resp.status_code == 200:
                        out['text'] = (resp.json() or {}).get('response', '')
                    else:
                        out['error'] = f"llama.cpp error ({resp.status_code}): {resp.text[:200]}"
                else:
                    from AI.consult import OllamaClient
                    from ...llm_executor_resources.config_utils import get_default_api_url
                    client = OllamaClient(base_url=get_default_api_url())
                    response = client.chat(
                        model=model, prompt=prompt, temperature=0.1,
                        max_tokens=max_tokens,
                    )
                    if isinstance(response, dict):
                        out['text'] = response.get('response') or response.get('text') or ''
                    else:
                        out['text'] = str(response)
            except Exception as e:
                out['error'] = str(e)

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        while t.is_alive():
            if stop_flag and stop_flag():
                logger.info("[ORCH] LLM call stopped by flag")
                return None
            time.sleep(0.05)
        if out['error'] is not None:
            logger.error("[ORCH] LLM call failed: %s", out['error'])
            return None
        text = (out.get('text') or '').strip()
        return text or None
