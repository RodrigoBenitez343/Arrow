"""composer.py — the deterministic system-chain composer.

Why this exists: a tiny model picking "the next action" from a flat menu
could never assemble a topology — it created junk LLM nodes and never one
wire.  Structure is not a model decision.  This module composes the SAME
shape the hand-built system chains use (linkedin_system_chain.json):

    input -> orchestrator LLM -> output          (skeleton, always)
    chain_import -> orchestrator `tools`         (one per matched task)
    chain_import -> orchestrator `brains`        (when the file routes too)

The actors keep their roles: the reasoning LLM decomposes the goal into
TOOL lines (prose — capabilities the bot needs, not screen micro-actions);
Laya grades which EXISTING chain performs each tool (``grade_chain_match``,
with the orchestrator's measured framing + floor + margin + object rules);
Python creates, wires and saves — the only JSON writer.  An unmatched tool
is taught in three steps, cheapest first: the provider DEMONSTRATES it in
the real browser (``web_act`` — recorded and frozen into a wired tool),
then the teach card asks (record web/desktop, build an llm step or a
sub-brain), and a tool that survives none of it is reported.  Nothing is
ever faked with empty nodes.
"""

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

#: Hard cap on chain imports one compose pass may add.
MAX_IMPORTS = 12

#: Floor for the teach budget; compose() raises it to the plan size so
#: EVERY unmatched tool gets its own ask (a fixed cap silently dropped the
#: rest of the plan to a report line).
MAX_TEACH_ROUNDS = 3

#: Fallback floor when Laya is down: the text-overlap score a task and a
#: chain must share before the composer imports it blind.
MIN_FALLBACK_SCORE = 0.5

#: Floor for a composer-context match, measured live 2026-10-09 on the
#: real chain library with the runtime gate's framing: correct plan-tool
#: pairs read 0.706-0.894 (navigate->Linkedin_nav 0.706, home 0.862,
#: search-bar 0.894, my-network 0.883), while wrong pairs top out at 0.598
#: (a semi-plausible "search" overlap) and the google/duckduckgo sweep read
#: 0.00-0.38.  0.65 sits between the bands; a miss costs one teach prompt,
#: a false positive wires a wrong tool into the bot.
MATCH_FLOOR = 0.65

#: Margin over the runner-up, applied only when the object count does NOT
#: confirm the pick (the runtime direct-chain gate's measured tie-breaker).
MATCH_MARGIN = 0.10

#: Candidates Laya grades per tool line (the presort ranks first; the tiny
#: model reads ~3-4 s each, so grading the whole library stalls a build).
PRESORT_K = 3

#: Stopwords for the task -> chain presort (Laya makes the real pick).
_STOPWORDS = {
    'a', 'an', 'the', 'to', 'of', 'and', 'or', 'for', 'in', 'on', 'with',
    'my', 'your', 'it', 'that', 'this', 'is', 'are', 'be', 'i', 'you', 'me',
    'then', 'into', 'from', 'at', 'by', 'as', 'up', 'out', 'its',
}


def _tokens(text):
    """Lowercased alnum word set with stopwords removed."""
    words = ''.join(c if c.isalnum() else ' ' for c in str(text or '').lower())
    return {w for w in words.split() if w and w not in _STOPWORDS}


#: A plan line may NAME the node type it sketches — "llm — extract …",
#: "input (ask): resolve …", "web_sequence (dom replay, repeating set): …":
#: the type (+ any parenthesised mode) is structure info, not part of the
#: tool.  The planner's live grammar is ``type (modes): text`` (measured
#: 2026-10-09); both separators are accepted.
_PLAN_PREFIX = re.compile(
    r'^\s*(input|output|orchestrator|llm|brain|web[_ ]sequence|sequence|'
    r'conditional|code|chain[_ ]import|form[_ ]filler|handle|context|mcp|'
    r'container)'
    r'(?:\s*\(([^)]{0,80})\))?'
    r'\s*[—–\-:]\s+'
    r'(\S.*)$',
    re.IGNORECASE)

#: Skeleton roles a plan line can name — already built, never matched.
_SKELETON_ROLES = ('input', 'output', 'orchestrator')

#: Kinds the builder DESIGNS itself — never matched against the library.
#: Matching these text-heavy lines picked stale artifacts (measured
#: 2026-10-09: 'llm (vanilla): extract…' → a junk output brain at p=0.78
#: while the right step read 0.44; 'code (static): merge…' → the extract
#: step at p=0.67, margin 0.03).
_DESIGNED_KINDS = ('llm', 'brain', 'code')


def _split_plan_step(step):
    """('llm (vanilla): extract titles') -> ('llm', 'vanilla', 'extract titles').

    No prefix -> ``('', '', the line)``.  The prefix is what the planner
    sketched, not part of the tool text: an ``input — …`` line once
    GRABBED the browser-navigate chain at p=0.65 and cascaded every real
    tool line into 'already imported' skips (measured 2026-10-09).
    """
    m = _PLAN_PREFIX.match(str(step or ''))
    if not m:
        return '', '', str(step or '').strip()
    return (m.group(1).lower().replace(' ', '_'),
            str(m.group(2) or '').strip(), m.group(3).strip())


def _orch_helpers():
    """The orchestrator's measured token/gate rules, or None (never raises).

    The runtime's direct-chain gate derived its thresholds and its object
    rules from real runs (``orchestrator_ops``): the same functions classify
    the library's generic words, a step's objects and a chain's routing
    signal here, so grading means the SAME thing at build time and at run
    time.  A failed import degrades to plain stopword tokens.
    """
    try:
        from player.multi_sequence.worflow_interpreter_modules.\
            executor_modules import orchestrator_ops as oo
        return oo
    except Exception:
        return None


def _graph_manager(ctx):
    mgr = ctx.get('graph_manager')
    return mgr() if callable(mgr) else mgr


def _gui_run(ctx, fn):
    """Run a canvas-touching read on the GUI thread when the ctx hooks it.

    The panel supplies ``run_on_gui``; tests and headless runs execute
    inline.  Never raises from the hook itself — a broken hook degrades to
    inline execution rather than failing a build.
    """
    run = ctx.get('run_on_gui')
    if callable(run):
        try:
            return run(fn)
        except Exception:
            logger.debug('run_on_gui failed; inline fallback', exc_info=True)
    return fn()


def scan_chain_library(chains_dir):
    """Every importable chain file: name, path, description, signal data.

    Walks the chains folder (learned/ included — the user's own system
    chain imports from it), classifies each file with the shared rule
    (``classify_chain``: 'brain' when it contains an orchestrator, else
    'chain') and silently skips unreadable files.  ``examples`` carries the
    file's ``routing.examples`` (verbatim requests it served — the runtime
    routes learned artifacts by these, never by their freeze narration) and
    ``learned`` marks the learned/ folder.  Sorted by name so the matching
    order is deterministic.
    """
    if not chains_dir or not os.path.isdir(chains_dir):
        return []
    out = []
    for root, _dirs, files in os.walk(chains_dir):
        for fn in files:
            low = fn.lower()
            if not low.endswith('.json') or '.bak' in low:
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception:
                continue
            if not isinstance(cfg, dict):
                continue
            try:
                from player.chain_import_ports import classify_chain
                kind = classify_chain(cfg, root)
            except Exception:
                kind = 'chain'
            routing = (cfg.get('routing')
                       if isinstance(cfg.get('routing'), dict) else {})
            out.append({
                'name': os.path.splitext(fn)[0],
                'path': path,
                'description': str(cfg.get('description') or ''),
                'examples': [str(e) for e in (routing.get('examples') or [])
                             if str(e).strip()],
                'learned': (os.path.basename(root).lower() == 'learned'
                            or fn.lower().startswith('learned_')),
                'kind': kind,
            })
    out.sort(key=lambda r: r['name'].lower())
    return out


def canvas_roles(ctx):
    """Skeleton roles already on the canvas: {'input'|'brain'|'output': id}.

    'brain' is an LLM node with ``orchestrator_mode`` ON — the switch IS
    the orchestrator.  Read-only; never raises.
    """
    def _scan():
        roles = {}
        try:
            from player.builder.node_templates import short_type, node_type_of
            mgr = _graph_manager(ctx)
            if mgr is None:
                return roles
            for node in (mgr.get_all_nodes() or []):
                try:
                    ntype = short_type(node_type_of(node))
                    nid = str(getattr(node, 'id', '') or '')
                except Exception:
                    continue
                if not nid:
                    continue
                if ntype in ('input', 'output'):
                    roles.setdefault(ntype, nid)
                elif ntype == 'llm' and 'brain' not in roles:
                    try:
                        on = node.get_property('orchestrator_mode')
                    except Exception:
                        on = False
                    if on is True or str(on).strip().lower() in (
                            'true', '1', 'yes', 'on'):
                        roles['brain'] = nid
        except Exception:
            pass
        return roles

    return _gui_run(ctx, _scan)


def _connected(ctx, src_id, src_port, dst_id, dst_port):
    """True when dst's input port already receives from src's output port."""
    def _check():
        mgr = _graph_manager(ctx)
        if mgr is None:
            return False
        try:
            dst = mgr.get_node_by_id(dst_id)
            for p in dst.input_ports():
                if str(p.name()) != dst_port:
                    continue
                for cp in p.connected_ports():
                    node = getattr(cp, 'node', None)
                    node = node() if callable(node) else node
                    if str(getattr(node, 'id', '')) == src_id and \
                            str(cp.name()) == src_port:
                        return True
        except Exception:
            pass
        return False

    return bool(_gui_run(ctx, _check))


class ChainComposer:
    """One compose pass: skeleton, matched imports, description, save."""

    def __init__(self, ctx, trace=None, max_imports=MAX_IMPORTS):
        self._ctx = ctx
        self._trace = trace
        self._max_imports = max_imports
        self._lines = []
        self._teaches = 0
        self._teach_budget = MAX_TEACH_ROUNDS

    # ------------------------------------------------------------- plumbing

    def _step(self, message):
        """One readable compose line: log + panel trace + transcript."""
        from player.builder.trace import emit as _emit
        _emit(self._trace, message)
        self._lines.append(message)

    def _call(self, name, args):
        from player.builder.devtools import execute_builder_call
        return execute_builder_call(name, args, self._ctx)

    def _create(self, node_type, label, edits=None, extra_args=None):
        """Create one node (returning its id) and apply its field edits."""
        args = {'node_type': node_type, 'label': label}
        if extra_args:
            args.update(extra_args)
        result = self._call('create_node', args)
        self._step('[create_node] %s' % result)
        if str(result).startswith('('):
            return ''
        nid = str(self._ctx.get('state', {}).get('last_node_id') or '')
        for prop, value in (edits or {}).items():
            self._step('[edit_node] %s'
                       % self._call('edit_node', {'node_id': nid,
                                                  'property': prop,
                                                  'value': value}))
        return nid

    def _wire(self, src_id, dst_id, input_port, output_port=''):
        """Wire by ids; the executor resolves the ports and never guesses."""
        args = {'from_node_id': src_id, 'to_node_id': dst_id,
                'input_port': input_port}
        if output_port:
            args['output_port'] = output_port
        result = self._call('wire', args)
        self._step('[wire] %s' % result)
        return str(result).startswith('wired')

    def _imported_paths(self):
        """Normalized chain files already imported on the canvas."""
        from player.builder.node_templates import short_type, node_type_of

        def _scan():
            found = set()
            try:
                mgr = _graph_manager(self._ctx)
                for node in (mgr.get_all_nodes() or []):
                    if short_type(node_type_of(node)) != 'chain_import':
                        continue
                    path = ''
                    try:
                        path = str(node.get_property('chain_file_path') or '')
                    except Exception:
                        path = ''
                    if not path:
                        try:
                            path = str(getattr(node, 'chain_file', '') or '')
                        except Exception:
                            path = ''
                    if path:
                        found.add(os.path.normcase(os.path.abspath(path)))
            except Exception:
                pass
            return found

        return _gui_run(self._ctx, _scan) or set()

    # -------------------------------------------------------------- matching

    def _match_chain(self, step, library, goal=''):
        """The library chain that performs this tool, or None.

        Presort by the runtime gate's OWN object tokens, then Laya reads
        the top candidates with the runtime gate's framing
        (``grade_chain_match``).  The pick must clear ``MATCH_FLOOR`` AND —
        when the candidates tie on named objects — beat the runner-up by
        ``MATCH_MARGIN``.  A tool naming objects NO candidate names is
        declined outright (the runtime's rule: a miss costs one teach
        prompt, a false positive wires a wrong tool).  With Laya down only
        a strong text match is imported — never a blind guess.
        """
        if not library:
            return None
        oo = _orch_helpers()

        def _sig(rec):
            """The chain's routing text: description + served examples.

            Learned artifacts route by examples ONLY — their description is
            the freeze narration ('Learned from a verified run: ... ask the
            user ...'), which poisoned matching before (measured 2026-10-09:
            narration phrases scored 0.78-0.94 for unrelated tasks).
            """
            if oo is not None:
                return oo._orchestrator_chain_signal(
                    rec.get('description') or '',
                    rec.get('examples') or [],
                    learned=bool(rec.get('learned')))
            return str(rec.get('description') or '')

        cands = []
        for rec in library:
            sig = ' '.join(_sig(rec).split())
            if not sig:
                continue          # no signal to route by (the runtime skips too)
            cands.append((rec, sig))
        if not cands:
            self._step('match "%s": no candidate chain carries a signal'
                       % str(step)[:80])
            return None
        generic = (oo._orchestrator_library_generic([s for _r, s in cands])
                   if oo is not None else frozenset())

        def _obj(text):
            if oo is not None:
                return oo._orchestrator_objects(text, generic)
            return _tokens(text)

        def _key(text):
            if oo is not None:
                return oo._orchestrator_directive_key(text, generic)
            return _tokens(text)

        task_obj = _obj(step)
        step_tokens = _tokens(step)

        def _text_score(rec, sig):
            shared = step_tokens & _tokens(str(rec['name']) + ' ' + sig)
            return len(shared) / max(1, len(step_tokens))

        def _rank(item):
            rec, sig = item
            return (-len(task_obj & _key(sig)), -_text_score(rec, sig),
                    rec['name'].lower())

        cands.sort(key=_rank)
        top = cands[:PRESORT_K]
        from player.builder import laya_grades as lg
        scored = []
        for rec, sig in top:
            p = lg.grade_chain_match(goal, step, rec['name'], sig,
                                     trace=self._trace)
            if p is None:
                continue
            named = len(task_obj & _key(sig)) if oo is not None else 0
            scored.append((named, p, rec))
        if not scored:
            # Laya unavailable: strong text overlap only.
            best_rec, best_sig = max(
                cands, key=lambda item: _text_score(item[0], item[1]))
            score = _text_score(best_rec, best_sig)
            if score >= MIN_FALLBACK_SCORE:
                self._step('match "%s" → %s (text %.2f, Laya unavailable)'
                           % (str(step)[:80], best_rec['name'], score))
                return best_rec
            self._step('match "%s": Laya unavailable and text %.2f too low'
                       % (str(step)[:80], score))
            return None
        named_pool = [item for item in scored if item[0]]
        if oo is not None and task_obj and not named_pool:
            # The tool names objects NO candidate chain names: nothing on
            # this library demonstrably serves it — decline to teach.
            self._step('match "%s": no chain names %s of the tool — '
                       'declining' % (str(step)[:80],
                                      ', '.join(sorted(task_obj)[:6])))
            return None
        pool = named_pool or scored
        pool.sort(key=lambda item: -item[1])
        top_named, top_p, top_rec = pool[0]
        second_named = pool[1][0] if len(pool) > 1 else 0
        second_p = pool[1][1] if len(pool) > 1 else 0.0
        if top_p < MATCH_FLOOR:
            self._step('match "%s": best %s p=%.2f below %.2f — no match'
                       % (str(step)[:80], top_rec['name'], top_p,
                          MATCH_FLOOR))
            return None
        confirmed = top_named > second_named
        if named_pool and not confirmed and \
                (top_p - second_p) < MATCH_MARGIN:
            self._step('match "%s": %s p=%.2f too close to %s p=%.2f '
                       '(margin %.2f < %.2f) — no match'
                       % (str(step)[:80], top_rec['name'], top_p,
                          pool[1][2]['name'], second_p,
                          top_p - second_p, MATCH_MARGIN))
            return None
        self._step('match "%s" → %s (p=%.2f, margin %.2f%s)'
                   % (str(step)[:80], top_rec['name'], top_p,
                      top_p - second_p,
                      ', names the tool objects' if confirmed else ''))
        return top_rec

    # --------------------------------------------------------------- teaching

    def _orphan_recordings(self):
        """Ids of recorded (web/desktop) nodes with nothing wired out."""
        from player.builder.node_templates import short_type, node_type_of
        from player.builder.wrap import WRAPPABLE_TYPES

        def _scan():
            out = []
            mgr = _graph_manager(self._ctx)
            if mgr is None:
                return out
            for node in (mgr.get_all_nodes() or []):
                try:
                    if short_type(node_type_of(node)) not in WRAPPABLE_TYPES:
                        continue
                    if not (node.get_property('session_file')
                            or node.get_property('sequence_file')):
                        continue
                    out.append(str(node.id))
                except Exception:
                    continue
            return out

        return _gui_run(self._ctx, _scan) or []

    def _wrap_recorded(self, brain_id):
        """Wrap every hand-recorded orphan node into a wired tool import."""
        if not brain_id:
            return
        from player.builder import wrap as _wrap
        for nid in self._orphan_recordings():
            lines, _path = _wrap.wrap_node(self._ctx, nid, brain_id,
                                           trace=self._trace)
            self._lines.extend(lines)

    def _attempt_web(self, step, brain_id, goal):
        """Provider demonstrates the tool in the real browser.

        Returns None on success (a tool was added), '' when not applicable,
        else the failure note the teach card shows before asking the user.
        """
        if not self._ctx.get('allow_web_act'):
            return ''
        try:
            from player.builder import web_act
            if not web_act.looks_web(step):
                return ''
            ok, lines, note = web_act.attempt_web_tool(
                step, goal, self._ctx, brain_id, trace=self._trace)
            self._lines.extend(lines)
            if ok:
                return None
            return str(note or '')
        except Exception:
            logger.debug('web act attempt failed', exc_info=True)
            return ''

    def _configure_role(self, node_id, kind, text):
        """Apply a skeleton line's settings to its node (edit_node tools).

        The plan's own input/output lines are the configuration the
        skeleton nodes carry: an `input` line names the slot and its ask
        mode (agent_modifiable — advertised to the runtime by the label);
        an `output` line names the result.  Every reply is a trace line, so
        the settings the bot runs with are visible in the chat.
        """
        if not node_id:
            return
        label = ' '.join(str(text or '').split())[:60]
        if not label:
            return
        edits = {'label': label}
        if kind == 'input':
            edits['agent_modifiable'] = True
        for prop, value in edits.items():
            self._step('[edit_node] %s' % self._call(
                'edit_node', {'node_id': node_id, 'property': prop,
                              'value': value}))

    @staticmethod
    def _kind_route(kind, modifier):
        """The teach route a plan line's own type pre-decides ('' = ask).

        'llm (vanilla)' → a DESIGNED LLM step; 'code (static)' → a real
        CODE node (authored Python, validated); 'conditional' → a check
        chain (one LLM conditional over the criterion); 'brain' (or an llm
        line with the orchestrator modifier) → a sub-brain.  Everything
        else (web/desktop routines, handle, …) keeps the
        demonstrate-then-ask path.
        """
        kind = str(kind or '').lower()
        mod = str(modifier or '').lower()
        if kind == 'brain' or (kind == 'llm' and 'orchestrator' in mod):
            return 'brain'
        if kind == 'code':
            return 'code'
        if kind == 'conditional':
            return 'check'
        if kind == 'llm':
            return 'llm'
        return ''

    def _teach_step(self, step, brain_id, goal='', kind='', modifier=''):
        """Teach one unmatched tool — route by its own type, DEMONSTRATE,
        then ask.

        A plan line that names its own type routes itself: llm/code lines
        are DESIGNED on the spot (authored prompt, chain file, wired
        import — no chat needed), brain lines become sub-brains.  A web
        routine is then demonstrated by the provider in the real browser
        (``web_act``) — recorded and frozen into a wired tool.  Only what
        survives none of that reaches the teach card (its body carries what
        was tried).  Returns True when a tool was added.
        """
        if not brain_id:
            return False
        if self._teaches >= self._teach_budget:
            self._step('teach budget (%d) reached — reported instead'
                       % self._teach_budget)
            return False
        self._teaches += 1
        # Design routes and the browser demonstration are fully
        # autonomous — only the CARD needs the chat (an ask callable).
        route = self._kind_route(kind, modifier)
        if route in ('llm', 'code', 'check'):
            labels = {'llm': 'an LLM processing step',
                      'code': 'a code step (authored + validated)',
                      'check': 'a check step (LLM conditional)'}
            self._step('[plan] "%s" → designed as %s'
                       % (str(step)[:70], labels[route]))
            return self._build_step(step, route, brain_id, goal)
        if route == 'brain':
            self._step('[plan] "%s" → designed as a sub-brain'
                       % str(step)[:70])
            return self._build_step(step, 'brain', brain_id, goal)
        attempt_note = self._attempt_web(step, brain_id, goal)
        if attempt_note is None:
            return True                      # demonstrated + frozen
        ask = self._ctx.get('ask')
        if not callable(ask):
            return False
        try:
            # The chat renders this ask as an interactive card (one button
            # per route); any host that only speaks text still answers with
            # the same words (web / desktop / llm / brain / skip).
            answer = str(ask(
                {'step': str(step)[:120], 'goal': str(goal or '')[:200],
                 'attempt': attempt_note},
                'teach_card') or '').strip().lower()
        except Exception:
            return False
        if answer.startswith('llm'):
            return self._build_step(step, 'llm', brain_id, goal)
        if answer.startswith('brain'):
            return self._build_step(step, 'brain', brain_id, goal)
        if answer.startswith('web'):
            node_type, record_kind = 'web_sequence', 'record_web_sequence'
        elif answer.startswith('desk'):
            node_type, record_kind = 'sequence', 'record_sequence'
        else:
            self._step('[teach] skipped "%s"' % str(step)[:80])
            return False
        holder = self._create(node_type, str(step)[:60])
        if not holder:
            return False
        try:
            # Also a card: its Start button runs the recorder on the
            # placeholder node and auto-advances when the file lands.
            ask('Record: "%s"' % str(step)[:100], record_kind)
        except Exception:
            pass
        from player.builder import wrap as _wrap
        lines, path = _wrap.wrap_node(self._ctx, holder, brain_id,
                                      trace=self._trace)
        self._lines.extend(lines)
        if path:
            self._step('[teach] "%s" taught and wrapped into %s'
                       % (str(step)[:80], os.path.basename(path)))
            return True
        # Nothing recorded: the empty holder is junk — remove it.
        from player.builder.devtools import execute_builder_call
        self._step('[delete_node] %s' % execute_builder_call(
            'delete_node', {'node_id': holder}, self._ctx))
        return False

    @staticmethod
    def _python_ok(code):
        """Compile-check authored code: (ok, error)."""
        try:
            compile(str(code or ''), '<builder-code-step>', 'exec')
            return True, ''
        except Exception as e:
            return False, str(e)

    def _build_step(self, step, kind, brain_id, goal):
        """Design a from-scratch step: llm prompt, code, check, sub-brain.

        ``code`` authors the Python ONCE and compile-validates it (one
        repair round; a syntax-broken script never reaches the chain file —
        it falls back to an LLM step).  ``check`` writes one LLM
        conditional over the plan line's criterion.  The builder AUTHORS
        the content (prose / Python — no model writes chain JSON); Python
        writes the sub-chain file (ConfigManager field parity, ``wrap.py``)
        and wires the import: ``tools`` for steps, ``brains`` for a
        sub-brain.  Prompt/code fall back to the tool line itself — never
        an empty node.
        """
        from player.builder import wrap as _wrap
        prompt = ''
        code = ''
        if kind == 'llm':
            try:
                from player.builder import reasoning as _reasoning
                prompt = _reasoning.step_prompt(step, goal) or ''
            except Exception:
                prompt = ''
        elif kind == 'code':
            try:
                from player.builder import reasoning as _reasoning
                code = _reasoning.code_text(step, goal) or ''
                ok, err = self._python_ok(code)
                if code and not ok:
                    self._step('[code] authored script has a syntax error '
                               '(%s) — repairing' % str(err)[:90])
                    code = _reasoning.code_text(step, goal, previous=code,
                                                error=str(err)) or ''
                    ok, err = self._python_ok(code)
                if not code or not ok:
                    self._step('[code] authoring failed (%s) — building an '
                               'LLM step instead' % str(err)[:90])
                    kind = 'llm'
                    prompt = _reasoning.step_prompt(step, goal) or ''
                else:
                    self._step('[code] authored and validated (%d chars)'
                               % len(code))
            except Exception:
                kind, code = 'llm', ''
                try:
                    from player.builder import reasoning as _reasoning
                    prompt = _reasoning.step_prompt(step, goal) or ''
                except Exception:
                    prompt = ''
        labels = {'brain': 'sub-brain', 'code': 'code step',
                  'check': 'check step'}
        lines, path = _wrap.build_step_chain(
            self._ctx, step, brain_id, kind=kind, prompt=prompt, code=code,
            trace=self._trace)
        self._lines.extend(lines)
        if path:
            self._step('[teach] "%s" %s built → %s'
                       % (str(step)[:80], labels.get(kind, 'LLM step'),
                          os.path.basename(path)))
            return True
        return False

    # --------------------------------------------------------------- compose

    @staticmethod
    def _goal_label(goal, words=6):
        text = ' '.join(str(goal or '').split()[:words]).strip()
        return text[:60] or 'assistant'

    def compose(self, goal, canvas_text, plan):
        """Assemble the system chain; returns the transcript lines."""
        goal = str(goal or '').strip()
        self._step('composing system chain for: %s' % (goal or '(empty)'))
        mgr = _graph_manager(self._ctx)
        roles = canvas_roles(self._ctx)

        # 1) Skeleton — the exact shape every system chain has.  The brain
        #    defaults to the LOCAL engine: the build-time provider (the
        #    chat model) must never leak into the artifact — measured
        #    2026-10-09, a composer-built brain ran deepseek at run time
        #    because the chat role filled its empty model.
        input_id = roles.get('input') or self._create('input', '')
        if roles.get('brain'):
            brain_id = roles['brain']
        else:
            edits = {'orchestrator_mode': True, 'orch_goal': goal,
                     'use_llamacpp': True}
            try:
                from player.builder.wrap import _default_gguf as _gguf
                _local = _gguf()
                if _local:
                    edits['llamacpp_model_path'] = _local
            except Exception:
                pass
            brain_id = self._create('llm', self._goal_label(goal),
                                    edits=edits)
        output_id = roles.get('output') or self._create('output', '')
        if input_id and brain_id and mgr is not None:
            if not _connected(self._ctx, input_id, 'output', brain_id,
                              'input'):
                self._wire(input_id, brain_id, 'input', 'output')
            # input.data -> prompt is the standing-goal feed the hand-built
            # system chains use (their llm prompt is REPLACED by this port).
            if not _connected(self._ctx, input_id, 'data', brain_id, 'prompt'):
                self._wire(input_id, brain_id, 'prompt', 'data')
        if brain_id and output_id and mgr is not None:
            if not _connected(self._ctx, brain_id, 'output', output_id,
                              'input'):
                self._wire(brain_id, output_id, 'input', 'output')

        # 1b) Anything the user already recorded by hand gets wrapped first
        #     — a recording on this canvas is a tool, not a loose node.
        self._wrap_recorded(brain_id)

        # 2) Tasks — each plan line resolves to an existing chain, a taught
        #    recording (ask → record → wrap), or a report.  Nothing is faked.
        library = scan_chain_library(str(self._ctx.get('chains_dir') or ''))
        self._step('library: %d chain(s) in the chains folder' % len(library))
        steps = [str(s).strip() for s in (plan or []) if str(s).strip()]
        if not steps:
            # No tool list (the planning model did not answer): the GOAL is
            # not a tool — matching the whole bot against a chain is how
            # wrong imports happened.  Skeleton only + an honest report;
            # the user fixes the builder model and builds again.
            self._step('[plan] no tool list (the planning model did not '
                       'answer) — skeleton only; check the builder '
                       'settings model, then say build again')
        # One teach ask per unmatched tool: the plan IS the budget (every
        # capability the bot needs gets its own ask).
        self._teach_budget = max(MAX_TEACH_ROUNDS, len(steps))
        imported = self._imported_paths()
        added = 0
        for raw_step in steps:
            if added >= self._max_imports:
                self._step('import cap (%d) reached — stopping'
                           % self._max_imports)
                break
            kind, modifier, step = _split_plan_step(raw_step)
            if kind in _SKELETON_ROLES:
                # The planner sketched structure, not a tool — the skeleton
                # already IS this line (matching it stole real tools'
                # chains before; see ``_split_plan_step``).  The line's
                # settings ride onto the skeleton node (edit_node tools).
                self._step('[plan] "%s" — the skeleton covers the %s'
                           % (step[:70], kind))
                if kind == 'input':
                    self._configure_role(input_id, kind, step)
                elif kind == 'output':
                    self._configure_role(output_id, kind, step)
                continue
            rec = None
            if kind not in _DESIGNED_KINDS:
                rec = self._match_chain(step, library, goal)
            if rec is None:
                if self._teach_step(step, brain_id, goal,
                                    kind=kind, modifier=modifier):
                    added += 1
                    imported = self._imported_paths()
                    continue
                self._step(
                    '[teach] "%s" stays out of the bot for now (no match, '
                    'and the demonstration/card produced no tool) — add it '
                    'in the canvas later or build again' % step)
                continue
            key = os.path.normcase(os.path.abspath(rec['path']))
            if key in imported:
                self._step('[skip] "%s" is already imported (%s)'
                           % (str(step)[:80], rec['name']))
                continue
            nid = self._create('chain_import', rec['name'],
                               extra_args={'chain_file_path': rec['path']})
            if nid and brain_id:
                port = 'brains' if rec['kind'] == 'brain' else 'tools'
                self._wire(nid, brain_id, port, 'output')
            imported.add(key)
            added += 1

        # 3) Description + save — a chain that exists, not a promise.
        self._step('[set_description] %s'
                   % self._call('set_description', {'text': goal}))
        self._step('[save_chain] %s'
                   % self._call('save_chain',
                                {'name': self._goal_label(goal, words=8)}))
        return self._lines
