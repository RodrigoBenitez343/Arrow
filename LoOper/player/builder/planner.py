"""planner.py — the Laya-driven build loop.

The corrected actor split for the builder:

- **Laya** picks the next action (``choice``) and gates "ask the user" /
  "done" (``noul``).  It selects among candidates and never invents them, so
  the action menu is supplied — the same shape the runtime orchestrator uses.
- **Needle 2** fills the chosen action's arguments by single-schema
  extraction (``devtools.extract_arguments``) — the properties of the node.
- **Python** executes the call against the live canvas, the only JSON writer.

If Laya is unavailable the planner degrades to the needle-only router
(``BuilderRouter``), which still picks and fills from the full catalogue.
"""

import logging

logger = logging.getLogger(__name__)

#: Actions Laya may pick.  ``read_graph`` is absent — Python reads the canvas
#: deterministically each step; ``ask_user`` is gate-driven; ``done`` is a
#: separate probe (a "done" option in the same choice acts as a magnet).
ACTION_MENU = (
    ('create_node', 'Add one new node to the canvas.'),
    ('wire', 'Connect one existing node to another.'),
    ('edit_node', 'Change a single property of an existing node.'),
    ('set_description', 'Write the whole system chain routing description.'),
    ('save_chain', 'Save the finished canvas as a system chain.'),
)

#: Bounded per turn — a build turn is a handful of actions, not a marathon.
MAX_STEPS = 6


def _first_line(text):
    """A one-line, length-capped summary of a multi-line block."""
    body = str(text or '').strip()
    return body.splitlines()[0][:200] if body else '(empty)'


def _short(value, limit=240):
    """A length-capped rendering of a value (args dicts, results)."""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + '...'


class BuilderPlanner:
    """One build turn: Laya plans, needle fills arguments, Python writes."""

    def __init__(self, ctx, exe_path=None, max_steps=MAX_STEPS):
        self._ctx = ctx
        self._exe = exe_path
        self._max_steps = max_steps

    def available(self):
        try:
            from player.code_agent_ops.devtools import find_needle_exe
            return bool(self._exe or find_needle_exe())
        except Exception:
            return False

    def route(self, goal, progress_cb=None, trace=None):
        from player.builder import laya_grades as lg
        from player.builder import reasoning as _reasoning
        from player.builder import settings as _settings
        from player.builder.trace import emit as _emit
        from player.builder.devtools import execute_builder_call

        def _note(event):
            if callable(progress_cb):
                try:
                    progress_cb(event)
                except Exception:
                    pass

        def _step(message):
            """One readable builder line: terminal/session log + panel."""
            _emit(trace, message)

        goal = str(goal or '')
        _step('goal: %s' % (goal or '(empty)'))
        cfg = _settings.load()
        try:
            max_steps = int(cfg.get('max_steps') or self._max_steps)
        except Exception:
            max_steps = self._max_steps
        _step('settings: engine=%s model=%s reasoning=%s'
              % (cfg.get('engine'), cfg.get('model') or '(default)',
                 cfg.get('reasoning')))
        transcript = []
        steps = 0
        _note('tool:read_graph')
        canvas = execute_builder_call('read_graph', {}, self._ctx)
        _step('canvas: %s' % _first_line(canvas))
        # 0) PLAN FIRST — decompose the goal BEFORE any node is created.
        #    The plan rides in every Laya premise and gates the done-probe,
        #    so the build follows explicit steps instead of acting blind.
        plan = []
        if cfg.get('reasoning', True):
            _note('thinking')
            try:
                plan = _reasoning.plan(goal, canvas, cfg) or []
            except Exception:
                plan = []
        for _i, _item in enumerate(plan, 1):
            _step('plan %d: %s' % (_i, _item))
        if not plan:
            _step('plan: not available — proceeding step by step')
        plan_log = ['[plan] %d) %s' % (_i, _s)
                    for _i, _s in enumerate(plan, 1)]

        # Compose-first: a canvas WITHOUT an orchestrator brain is a NEW
        # system chain — assemble it deterministically (ChainComposer):
        # skeleton, then one chain_import per task that matches an existing
        # chain.  The per-step action loop below is the EDIT path (the
        # canvas already has a brain), where the flat action menu works.
        from player.builder.composer import ChainComposer, canvas_roles
        roles = canvas_roles(self._ctx)
        if not roles.get('brain'):
            _step('mode: compose (no orchestrator brain on the canvas)')
            try:
                lines = ChainComposer(self._ctx,
                                      trace=trace).compose(goal, canvas, plan)
            except Exception:
                logger.exception('composer failed')
                lines = ['(composer failed — see the log)']
            return {'ok': True, 'empty': not lines,
                    'text': '\n\n'.join(lines), 'steps': len(lines),
                    'error': None}
        _step('mode: edit (brain %s already on the canvas)' % roles['brain'])
        _step('step budget: %d (edit-mode cap; compose follows its plan)'
              % max_steps)

        def _premise_log():
            """The step log Laya reads: the plan first, then real actions.

            Needle extraction keeps the REAL transcript only — the plan
            describes future nodes and must not contaminate the fields.
            """
            return (plan_log + transcript) if plan_log else transcript

        while steps < max_steps:
            steps += 1
            _step('step %d' % steps)
            # 1) Ask gate: the user owes information no action can supply.
            if lg.wants_to_ask(goal, canvas, _premise_log(), trace=trace):
                _note('tool:ask_user')
                args = self._fill('ask_user', goal, canvas, transcript,
                                  trace) or {}
                args.setdefault('kind', 'answer')
                if not str(args.get('question') or '').strip():
                    args['question'] = goal
                _step('asking the user: %s' % args['question'])
                transcript.append('[ask_user] %s' % execute_builder_call(
                    'ask_user', args, self._ctx))
                canvas = execute_builder_call('read_graph', {}, self._ctx)
                continue
            # 2) Done gate — gated on plan progress: with an N-step plan at
            #    least N actions must have run before DONE is trusted (a
            #    single node is never "complete").
            actions_done = sum(
                1 for t in transcript
                if t.startswith('[') and not t.startswith('[ask_user]'))
            if plan and actions_done < min(len(plan), max_steps):
                _step('done-probe skipped — %d of %d plan steps done'
                      % (actions_done, len(plan)))
            elif lg.is_done(goal, canvas, _premise_log(), trace=trace):
                _step('done — the chain is complete')
                break
            # 3) The reasoning LLM plans the next step; Laya picks the action
            #    with that plan folded into its premise.
            _note('thinking')
            thought = _reasoning.think(goal, canvas, transcript, cfg)
            _step('reasoning: %s' % (thought or 'unavailable'))
            action = lg.pick_action(goal, canvas, _premise_log(), ACTION_MENU,
                                    trace=trace, thought=thought)
            if not action:
                if not transcript:
                    # Laya down / picked nothing on the first step: fall back
                    # to the needle-only router so the turn still does work.
                    _step('no action picked — needle-only fallback')
                    return self._needle_fallback(goal, progress_cb)
                break
            # 4) Needle fills the chosen action's arguments.  create_node's
            #    node_type is an enumerable choice, so Laya grounds it when
            #    needle cannot — the node must actually get created.
            _note('tool:' + action)
            args = self._fill(action, goal, canvas, transcript, trace) or {}
            if action == 'create_node':
                args = self._ground_create(args, goal, canvas, _premise_log(),
                                           trace)
            _step('%s arguments: %s' % (action, _short(args)))
            result = execute_builder_call(action, args, self._ctx)
            transcript.append('[%s] %s' % (action, result))
            _step('%s → %s' % (action, _first_line(result)))
            if action == 'save_chain':
                break
            if str(result).startswith('('):
                # Grounded-or-ask: hand the problem to the user rather than
                # retrying the same failure on the next loop pass.
                _note('tool:ask_user')
                question = ('I could not work out how to %s this: %s'
                            % (action, result))
                _step('ungrounded — asking the user: %s' % question)
                transcript.append('[ask_user] %s' % execute_builder_call(
                    'ask_user', {'question': question, 'kind': 'answer'},
                    self._ctx))
                break
            canvas = execute_builder_call('read_graph', {}, self._ctx)
        return {'ok': True, 'empty': not transcript,
                'text': '\n\n'.join(t for t in transcript if t),
                'steps': steps, 'error': None}

    def _fill(self, action, goal, canvas, transcript, trace=None):
        """The chosen action's arguments, extracted by needle."""
        from player.builder.devtools import extract_arguments
        content = goal
        if canvas:
            content += '\n\nCanvas:\n' + str(canvas)
        if transcript:
            content += '\n\nDone so far:\n' + '\n'.join(transcript)
        return extract_arguments(action, content, self._exe, trace=trace)

    def _ground_create(self, args, goal, canvas, transcript, trace=None):
        """``create_node`` must happen: if needle could not name a node_type,
        Laya picks it from the type purposes (a choice, not free text).
        """
        from player.builder import laya_grades as lg
        from player.builder.node_templates import NODE_TYPES
        args = dict(args) if isinstance(args, dict) else {}
        ntype = str(args.get('node_type') or '').strip().lower()
        if ntype not in NODE_TYPES:
            picked = lg.pick_node_type(goal, canvas, transcript, trace=trace)
            if picked in NODE_TYPES:
                ntype = picked
        if ntype in NODE_TYPES:
            args['node_type'] = ntype
        return args

    def _needle_fallback(self, goal, progress_cb):
        from player.builder.devtools import BuilderRouter
        router = BuilderRouter(self._ctx, exe_path=self._exe)
        if not router.available():
            return {'ok': False, 'empty': True, 'text': '',
                    'steps': 0, 'error': 'needle2 engine not found'}
        return router.route(goal, progress_cb=progress_cb)
