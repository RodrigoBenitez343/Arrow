"""agent.py — the workspace agent: an interleaved loop over chains.

One workspace, one agent, and no hand-wired orchestrator.  The agent runs a
LOOP, and each role in it is kept separate so a small model can do its part:

1. **memory** (``plans.py``) — a similar past request, the tools it used and
   the ``why`` of each step.  The agent's knowledge is that frozen graph of
   episodes, not a prompt trick.
2. **reason** (the model) — is the request answered by what already ran?  If
   not, what is the ONE next intent?  It never names a chain.
3. **pick** (Laya) — which chain carries out that intent, scored over SHORT
   labels (name, past requests).  This is the tool choice, and it is the
   encoder's job, not the model's.
4. **run** (``ChainExecutor``) — the chain, with the previous result threaded in.
5. **observe -> freeze** — the episode (goal, tools, why, outcome) is written
   back, so the next similar request can reuse or reorder it.

A request that matches a learned episode that still wins is REPLAYED — frozen
knowledge, no model call.  A request with no such memory goes through the
loop.  Anything the loop cannot resolve raises ``AgentError`` naming the
defect; there is no silent fallback.

``plan()`` remains the one-shot decomposition (learned episode, Laya pick,
single chain, or a strict-JSON planner), used by callers that want a plan
without running it.
"""

import json
import logging
import os

from player import workspace as ws

from . import catalog, plans

logger = logging.getLogger(__name__)

#: A composed plan stays short: the agent is not a workflow builder.
MAX_STEPS = 6
#: Laya only scores a bounded candidate set (one encoder pass per option).
MAX_CANDIDATES = 6
#: Labels handed to one rerank: each chain contributes its name, plus past
#: requests when it has them, so the list is longer than the chain count.
MAX_LABELS = 10
#: How much of a previous step's output is carried into the next one.
CARRY_CHARS = 1500


class AgentError(Exception):
    """The request could not be routed, planned or run."""


class WorkspaceAgent:
    """The agent that owns one workspace.

    ``executor`` (anything with ``run_chain(path, ...)``), ``llm``
    (``llm(prompt, system, model) -> str``) and ``picker``
    (``picker(intent, usable) -> catalog entry | None``) are injectable so the
    loop can be tested without the app's model gateway or the Laya daemon.
    """

    def __init__(self, root, slug, executor=None, llm=None, on_status=None,
                 picker=None):
        self.root = str(root)
        self.slug = str(slug)
        self.manifest = ws.load(self.root, self.slug)
        self.store = plans.PlanStore(ws.workspace_dir(self.root, self.slug))
        self._executor = executor
        self._llm = llm
        self._picker = picker
        self._status = on_status or (lambda _text: None)
        self._catalog = None

    # ---------------------------------------------------------- identity

    @property
    def name(self):
        return str(self.manifest.get('name') or self.slug)

    @property
    def agent_name(self):
        return str((self.manifest.get('agent') or {}).get('name') or self.name)

    @property
    def model(self):
        """The reasoner model the WORKSPACE chose ('' when none was chosen).

        There is no implicit default: the agent's reasoner is picked in agent
        mode (MODEL button: engine + model, every configured provider) and
        stored in ``workspace.json → agent.model``.  An empty value is an
        honest "not chosen yet" — the loop then runs one tool and says so —
        rather than a model the user never picked.
        """
        return str((self.manifest.get('agent') or {}).get('model')
                   or '').strip()

    @property
    def brain(self):
        """The pinned chain ('' when the agent composes chains itself)."""
        return str((self.manifest.get('agent') or {}).get('legacy_brain')
                   or '').strip()

    @property
    def max_steps(self):
        """The plan length this workspace allows (``policy.max_steps``)."""
        policy = (self.manifest.get('agent') or {}).get('policy') or {}
        try:
            return max(1, int(policy.get('max_steps') or MAX_STEPS))
        except (TypeError, ValueError):
            return MAX_STEPS

    @property
    def chains_dir(self):
        return ws.chains_dir(self.root, self.slug)

    # ----------------------------------------------------------- catalog

    def catalog(self, refresh=False):
        """The workspace's chain entries (cached until asked to refresh)."""
        if self._catalog is None or refresh:
            self._catalog = catalog.entries(self.root, self.slug)
        return self._catalog

    def candidates(self):
        """Entries the agent may route to (named chains only)."""
        return catalog.routable(self.catalog(refresh=True))

    # ------------------------------------------------------------ routing

    def plan(self, query):
        """``{'source', 'steps': [{'chain', 'inputs'}], 'goal'}`` for *query*.

        Raises ``AgentError`` when nothing in the workspace can answer it and
        no planner model is available to say so.
        """
        query = ' '.join(str(query or '').split()).strip()
        if not query:
            raise AgentError('the request is empty')

        brain = self.brain
        if brain:
            if not os.path.isfile(brain):
                raise AgentError(
                    'workspace %r pins a brain chain that no longer exists: %s'
                    % (self.name, brain))
            return {'source': 'brain', 'goal': query,
                    'steps': [{'chain': os.path.basename(brain)[:-5],
                               'inputs': {}}]}

        entries = self.catalog(refresh=True)
        usable = [item for item in entries if item.get('routable')]
        if not usable:
            silent = [item['id'] for item in entries]
            raise AgentError(
                'workspace %r has no chain that describes itself, so there is '
                'nothing to route to%s. Describe a chain with the Builder '
                '(its own flow metadata) first.'
                % (self.name,
                   ' (silent: %s)' % ', '.join(silent[:5]) if silent else ''))

        learned = self.store.matched(query)
        if learned is not None and self.store.replayable(learned):
            steps = self._validated(learned.get('steps') or [], entries)
            return {'source': 'learned', 'goal': learned.get('goal') or query,
                    'steps': steps}
        if learned is not None:
            logger.info('Learned plan for %r is losing (%d ok / %d fail): '
                        'planning it fresh', str(learned.get('goal'))[:60],
                        int(learned.get('ok') or 0),
                        int(learned.get('fail') or 0))

        picked = self._laya_pick(query, usable)
        if picked is not None:
            return {'source': 'laya', 'goal': query,
                    'steps': [{'chain': picked['id'], 'inputs': {}}]}

        if len(usable) == 1:
            return {'source': 'only-chain', 'goal': query,
                    'steps': [{'chain': usable[0]['id'], 'inputs': {}}]}

        steps = self._ask_planner(query, usable)
        return {'source': 'plan', 'goal': query, 'steps': steps}

    def _laya_pick(self, query, usable):
        """The one chain Laya is confident about (None when it is not).

        Two passes, both on SHORT text: the ``rerank`` (a forced choice among
        the labels — contrastive, so no length bias) and one same-task verdict
        on the winner.  The verdict is what makes the pick a DECISION: an
        encoder that answers "no" is a refusal, not a guess to fall through
        with, and a chain whose name does not describe the request must not be
        run just because it was the least bad of a forced list.
        """
        try:
            from AI import laya_client
        except Exception:
            logger.debug('Laya unavailable: the planner decides')
            return None
        labels = []          # [(label text, entry)]
        for item in usable[:MAX_CANDIDATES]:
            for text in catalog.task_labels(item):
                if all(text != seen for seen, _entry in labels):
                    labels.append((text, item))
            if len(labels) >= MAX_LABELS:
                break
        labels = labels[:MAX_LABELS]
        if not labels:
            return None
        try:
            index = laya_client.rerank(query, [text for text, _e in labels])
        except Exception:
            logger.warning('Laya rerank failed, the planner decides',
                           exc_info=True)
            return None
        if index is None or not 0 <= index < len(labels):
            return None
        label, best = labels[index]
        try:
            confirmed = laya_client.decision(query, _SAME_TASK % label)
        except Exception:
            confirmed = None
        if confirmed is False:
            logger.info('Laya picked %r (%s) but the same-task verdict refuses '
                        'it: the planner decides', best['id'], label[:40])
            return None
        logger.info('Laya routed the request to %r (matched %r)', best['id'],
                    label[:60])
        return best

    def _ask_planner(self, query, usable):
        """Strict-JSON plan from the planner model, validated."""
        prompt = _planner_prompt(query, usable, self.store.hints())
        raw = self._call_llm(prompt, _PLANNER_SYSTEM)
        steps = _parse_plan(raw, usable)
        return self._validated(steps, usable)

    def _validated(self, steps, usable):
        """Only chains that exist in the workspace, at most MAX_STEPS."""
        out = []
        for step in steps or ():
            if not isinstance(step, dict):
                raise AgentError('a plan step is %r, not an object'
                                 % (type(step).__name__,))
            chain_id = str(step.get('chain') or '').strip()
            found = catalog.find(usable, chain_id)
            if found is None:
                raise AgentError(
                    'the plan names chain %r, which is not in workspace %r'
                    % (chain_id, self.name))
            inputs = step.get('inputs')
            if inputs is not None and not isinstance(inputs, dict):
                raise AgentError(
                    'step %r got inputs %r, not an object'
                    % (chain_id, type(inputs).__name__))
            entry = {'chain': found['id'],
                     'inputs': {str(k): str(v)
                                for k, v in (inputs or {}).items()}}
            # A frozen episode carries the intent each tool answered; keeping
            # it is what stops a replay from erasing the memory it came from.
            why = ' '.join(str(step.get('why') or '').split())[:400]
            if why:
                entry['why'] = why
            out.append(entry)
        if not out:
            raise AgentError('the plan is empty')
        if len(out) > self.max_steps:
            raise AgentError('the plan has %d steps; this workspace allows %d'
                             % (len(out), self.max_steps))
        return out

    # -------------------------------------------------------- execution

    def run(self, query, stop_flag=None, ask_user=None, ask_user_v2=None,
            on_output_ready=None):
        """The agent LOOP: understand -> pick+run per task -> verify -> freeze.

        Each role is kept apart on purpose (semi deterministic):

        * **memory** (`plans.py`) is REASSURANCE: an episode answers alone only
          when the request is the same one (exact, or ≥0.97 same-question) and
          the episode kept winning — ``PlanStore.confident``.  Otherwise memory
          is EVIDENCE for the reasoning pass, never a decision.
        * **the model REASONS** — one initial pass writes the TASK LIST (what the
          user actually asked for) and the one-line understanding, and one
          verification pass at the end says whether it is answered, what to ask,
          or which task is still missing.  It never names a chain.
        * **Laya PICKS the tool** for each task, over short labels (name, past
          requests).  No model names a chain and no encoder is asked to plan.
        * **Python runs it** (``ChainExecutor``) and freezes the episode.

        Chains are the hands, the model is the reason, the memory is the
        reassurance.  With no reasoner configured the loop runs exactly ONE tool
        — the one Laya or the confident episode picks — and says so.
        """
        query = ' '.join(str(query or '').split()).strip()
        if not query:
            raise AgentError('the request is empty')

        brain = self.brain
        if brain:
            plan = self.plan(query)          # pinned brain: one chain answers
            self._status('brain: %s' % os.path.basename(brain))
            return self._execute(plan, query, stop_flag, ask_user, ask_user_v2,
                                 on_output_ready)

        # Memory bypass: ONLY the same request, at near-verbatim confidence,
        # from an episode that kept winning.  A merely similar request goes
        # through the reasoning pass with that episode as evidence.
        frozen = self.store.confident(query)
        if frozen is not None:
            plan = {'source': 'learned', 'goal': frozen.get('goal') or query,
                    'steps': self._validated(frozen.get('steps') or [],
                                             self.candidates())}
            self._status('replaying what worked: %s'
                         % ' -> '.join(s['chain'] for s in plan['steps']))
            logger.info('Agent %r replays the confident episode for %r: %s',
                        self.agent_name, query[:80],
                        ' -> '.join(step['chain'] for step in plan['steps']))
            try:
                text = self._execute(plan, query, stop_flag, ask_user,
                                     ask_user_v2, on_output_ready)
            except Exception:
                self._learn(plan, ok=False)
                raise
            self._learn(plan, ok=True)
            return text

        usable = [item for item in self.catalog(refresh=True)
                  if item.get('routable')]
        if not usable:
            raise AgentError(
                'workspace %r has no chain that describes itself, so there is '
                'nothing to route to. Describe a chain with the Builder first.'
                % self.name)
        return self._loop(query, usable, stop_flag, ask_user, ask_user_v2,
                          on_output_ready)

    def _loop(self, query, usable, stop_flag, ask_user, ask_user_v2,
              on_output_ready):
        """One tool per turn, a reasoning turn after each: the agentic loop."""
        try:
            return self._loop_turns(query, usable, stop_flag, ask_user,
                                    ask_user_v2, on_output_ready)
        except Exception:
            # A chain that blows up mid-episode must still be learned as a
            # failure: that is what stops the same order being tried forever.
            self._learn({'source': 'loop', 'goal': query,
                         'steps': getattr(self, '_loop_steps', [])}, ok=False)
            raise

    def _loop_turns(self, query, usable, stop_flag, ask_user, ask_user_v2,
                    on_output_ready):
        """Understand first, then one tool per TASK, then verify and answer.

        1. **understand** — one reasoning pass writes the TASK LIST (what the
           user actually asked for, in order) and restates the request in one
           line.  This is the step that stops a blind single-tool run: the model
           shows it understood, and every task has the user's own words.
        2. **pick + run, per task** — Laya maps each task onto one chain.  A
           task no chain covers is REPORTED, not silently dropped.
        3. **verify** — one reasoning pass at the end of the list: is the request
           answered?  If a task is still missing it NAMES it and the loop appends
           it (bounded by `policy.max_steps`).

        Without a model the list is the request itself: ONE tool, said out loud.
        """
        memory = self.store.evidence(query)
        self._stop_flag = stop_flag
        understood = self._understand(query, usable, memory)
        if understood and understood.get('ask') and not understood.get('tasks'):
            # The reasoning pass could not understand the request well enough to
            # act: ask, and use the reply as the request for one more pass.
            reply = self._ask(understood['ask'], ask_user)
            if not reply:
                return self._finish(query, [], understood['ask'], ok=False)
            query = '%s (%s)' % (query, reply)
            memory = self.store.evidence(query)
            understood = self._understand(query, usable, memory)
        tasks = list(understood.get('tasks') or ()) if understood else [query]
        if not tasks:
            raise AgentError('the reasoning pass produced no task for %r'
                             % query[:120])
        if understood and understood.get('understood'):
            self._status('understood: %s' % understood['understood'][:160])
            logger.info('Agent %r understood %r as: %s', self.agent_name,
                        query[:60], understood['understood'][:160])

        steps = []
        self._loop_steps = steps
        unserved = []
        answer = ''
        concluded = False
        limit = self.max_steps

        while len(steps) < limit:
            if stop_flag and stop_flag():
                return self._finish(query, steps, 'Cancelled.', ok=False)
            if not tasks:
                break
            task = tasks.pop(0)
            tool = self._pick(task, usable)
            if tool is None:
                # No chain performs this task: keep going with the rest, and
                # name the gap in the answer instead of dropping it quietly.
                logger.warning('Agent %r: no chain matches the task %r',
                               self.agent_name, task[:120])
                unserved.append(task)
                continue
            self._status('step %d/%d: %s' % (len(steps) + 1, limit, tool['id']))
            # Record the attempt BEFORE running it: a chain that blows up is
            # still an episode ("this order was tried and failed").
            steps.append({'chain': tool['id'], 'why': task, 'inputs': {},
                          'result': ''})
            text = self._run_tool(tool, query,
                                  steps[-2]['result'] if len(steps) > 1 else '',
                                  stop_flag, ask_user, ask_user_v2,
                                  on_output_ready)
            steps[-1]['result'] = str(text or '')
            logger.info('Agent %r step %d: %s (task: %s)', self.agent_name,
                        len(steps), tool['id'], task[:80])

            if tasks:
                continue
            verdict = self._compose(query, tasks, steps, memory, usable)
            if verdict is None:
                answer = str(text or '')
                break
            answer = str(verdict.get('answer') or '').strip() or str(text or '')
            if verdict.get('ask'):
                reply = self._ask(verdict['ask'], ask_user)
                if not reply:
                    answer = answer or verdict['ask']
                    concluded = True
                    break
                tasks.append(reply)
                continue
            if verdict.get('done'):
                concluded = True
                break
            nxt = str(verdict.get('next') or '').strip()
            if not nxt:
                raise AgentError(
                    'the reasoner neither concluded nor named a missing task '
                    '(after %d step(s))' % len(steps))
            tasks.append(nxt)

        if not answer:
            answer = str(steps[-1]['result'] if steps else '')
        if unserved:
            note = ('No chain in this workspace can do: %s. Describe one with '
                    'the Builder, or reword it.' % '; '.join(unserved[:4]))
            answer = (answer + '\n\n' if answer else '') + note
        if not self.model and not callable(self._llm):
            # No reasoner available: ONE tool, and the answer says why (silently
            # stopping after the first step reads as ignoring the order).
            logger.info('Agent %r: no reasoner model, single-tool run',
                        self.agent_name)
            answer = (answer + '\n\n' if answer else '') + (
                'Stopped after one step: this agent has no reasoner model '
                'configured, so it cannot decide what comes next. Set one '
                'with the MODEL button in agent mode (or in AI Settings).')
            concluded = True
        if not concluded:
            logger.warning('Agent %r stopped at the %d-step cap without '
                           'concluding', self.agent_name, limit)
            self._status('step cap reached (%d)' % limit)
        return self._finish(query, steps, answer, ok=concluded)

    def _ask(self, question, ask_user):
        """Put the reasoner's question to the user ('' when nobody answers)."""
        question = str(question or '').strip()
        if not question or not callable(ask_user):
            return ''
        try:
            return str(ask_user(question) or '').strip()
        except Exception:
            logger.warning('asking the user failed', exc_info=True)
            return ''

    def _pick(self, intent, usable):
        """The tool that carries out *intent* (Laya, or an injected picker)."""
        if callable(self._picker):
            return self._picker(intent, usable)
        return self._laya_pick(intent, usable)

    def _run_tool(self, tool, query, previous, stop_flag, ask_user, ask_user_v2,
                  on_output_ready):
        """Run ONE chain for the current intent, carrying the last result in."""
        from .chain_executor import ChainExecutor

        executor = self._executor or ChainExecutor()
        step_query = query
        if previous:
            step_query = ('%s\n\nRESULT OF THE PREVIOUS STEP:\n%s'
                          % (query, str(previous)[-CARRY_CHARS:]))
        return executor.run_chain(
            tool['path'],
            agent_query=step_query,
            ask_user_callback=ask_user,
            ask_user_v2_callback=ask_user_v2,
            on_output_ready=on_output_ready,
            stop_flag=stop_flag,
            bound_inputs=None,
        )

    def _finish(self, query, steps, answer, ok):
        """Freeze the episode (goal -> tools, each with its why) and answer."""
        self._learn({'source': 'loop', 'goal': query, 'steps': steps}, ok)
        return answer

    def _understand(self, query, usable, memory=''):
        """The INITIAL reasoning pass: what did the user actually ask for?

        Returns ``{'understood', 'tasks', 'ask'}`` or None without a model.  It
        never names a chain — the task list is the model's job, the tool choice
        is Laya's, and the two must not blur.
        """
        if not self.model and not callable(self._llm):
            return None
        prompt = _understanding_prompt(query, usable, memory, self.max_steps)
        raw = self._call_llm(prompt, _UNDERSTAND_SYSTEM)
        return _parse_tasks(raw, self.max_steps)

    def _compose(self, query, pending, steps, memory, usable):
        """The verification pass: answered? ask? which task is still missing?

        Deliberately narrow: it never names a chain (the picker does), it only
        says whether the request is answered, what to ask the user, and — when
        something is missing — the ONE task the next tool must carry out.
        """
        if not self.model and not callable(self._llm):
            return None
        prompt = _reason_prompt(query, steps, memory, usable, pending)
        raw = self._call_llm(prompt, _REASON_SYSTEM)
        return _parse_verdict(raw)

    def _execute(self, plan, query, stop_flag, ask_user, ask_user_v2,
                 on_output_ready):
        """Run a FROZEN order (learned episode or pinned brain) end to end."""
        text = ''
        previous = ''
        for step in plan['steps']:
            if stop_flag and stop_flag():
                return 'Cancelled.'
            found = catalog.find(self.catalog(), step['chain'])
            if found is None:
                raise AgentError('plan step %r is no longer in the workspace'
                                 % step['chain'])
            self._status('running %s' % found['id'])
            text = self._run_tool(found, query, previous, stop_flag, ask_user,
                                  ask_user_v2, on_output_ready)
            previous = str(text or '')
        return text

    def _learn(self, plan, ok):
        """Record the episode, its outcome and what followed what.

        ``asked`` / ``reported`` come from the chains themselves: a chain with
        a ``user_prompt`` input stops for the user, a chain with output nodes
        reports.  The chosen order is what encodes *when to ask* and *when to
        report*, so the run records it instead of a model deciding it later.
        """
        if plan['source'] == 'brain':
            return
        if not plan.get('steps'):
            return
        chain_ids = [step['chain'] for step in plan['steps']]
        try:
            entries = self.catalog()
            chosen = [catalog.find(entries, chain_id) for chain_id in chain_ids]
            asked = any(item and item.get('asks') for item in chosen)
            reported = any(item and item.get('reports') for item in chosen)
            self.store.record(plan['goal'], plan['steps'])
            self.store.result(plan['goal'], ok, asked=asked, reported=reported)
            self.store.record_transitions(chain_ids, ok)
        except Exception:
            # Learning is an optimisation: a plan file that cannot be written
            # must not lose the answer the chains already produced.
            logger.warning('the plan could not be learned', exc_info=True)

    # ------------------------------------------------------------- model

    def _call_llm(self, prompt, system):
        if callable(self._llm):
            return self._llm(prompt, system, self.model)
        return _gateway_llm(prompt, system, self.model,
                            cancel=getattr(self, '_stop_flag', None))


# ------------------------------------------------------------------ prompt

_PLANNER_SYSTEM = (
    'You are the router of a deterministic computer-use agent. You receive a '
    'request and the catalog of chains the workspace owns. Answer with ONE '
    'JSON object and nothing else: {"steps": [{"chain": "<id>", '
    '"inputs": {"<input label>": "<value>"}}]}. Use the smallest number of '
    'steps that answers the request (usually one). Chain ids must come from '
    'the catalog. Never invent a chain, never explain.'
)

#: The verdict the pick must pass.  Same statement shape that was measured
#: against the bundled encoder (see ``catalog.task_labels``): a chain whose own
#: name the request matches asks for the SAME task, which is what routing is.
_SAME_TASK = 'This text asks for the same task as: %s'


#: The INITIAL reasoning pass.  Its whole job is to understand: restate the
#: request and split it into the things the user asked for, in their order.
#: It never names a chain — the picker does that, one task at a time.
_UNDERSTAND_SYSTEM = (
    'You are the planner of a deterministic computer-use agent. Read the user '
    'request and say what it actually asks for. You do NOT choose tools.\n'
    'Answer with ONE JSON object and nothing else: '
    '{"understood": "<one line: what the user wants, in your words>", '
    '"tasks": ["<one action per thing the user asked for, in order>"], '
    '"ask": "<a question for the user, or \"\">"}.\n'
    'Rules: one task per distinct thing the user asked for, nothing repeated, '
    'each task phrased as a single action in the words the user used (a small '
    'tool picker must be able to match it). Do not invent work the request does '
    'not contain. If the request is too vague or ambiguous to act on, leave '
    '"tasks" empty and put ONE precise question in "ask". Never name a chain, '
    'never emit tool-call markup, never explain.'
)


#: The reasoning turn.  Narrow on purpose: the model decides WHEN the request
#: is answered, WHAT to ask the user, and WHAT the next intent is — never which
#: chain to call (that is the picker's job) and never a call syntax.
_REASON_SYSTEM = (
    'You are the reasoner of a deterministic computer-use agent. You receive '
    'the user request, the tools the workspace owns and what has already run. '
    'Decide ONE thing: is the request answered by what has run so far?\n'
    'Answer with ONE JSON object and nothing else: '
    '{"done": true or false, "answer": "<what to tell the user>", '
    '"next": "<the single next action, described in the words a small tool '
    'picker can match>", "ask": "<a question for the user, or \"\">"}.\n'
    'done=true means the request is answered; "answer" then carries the reply. '
    'If it cannot be carried out without the user, put the question in "ask" '
    'and leave "next" empty. Otherwise "next" names the ONE task still missing '
    'and nothing more — do not chain several actions in one sentence. '
    'Never name a chain by id, never emit tool-call markup, never explain.'
)
#: How much of each step result the reasoner reads (per step).
RESULT_PREVIEW = 400


def _understanding_prompt(query, usable, memory='', max_tasks=MAX_STEPS):
    """The initial pass's prompt: the request, the tools, and what memory knows."""
    lines = ['USER REQUEST: ' + ' '.join(str(query).split())[:900], '']
    lines.append('THE CHAINS THIS WORKSPACE OWNS (for context only — the tool '
                 'picker chooses, not you):')
    lines.append(catalog.digest(list(usable), limit=1800) or '  (none)')
    if memory:
        lines += ['', memory]
    lines += ['',
              'Split the request into AT MOST %d tasks, in order. Each task is '
              'one action the user asked for.' % int(max_tasks),
              'Answer with the JSON object only.']
    return '\n'.join(lines)


def _parse_tasks(raw, max_tasks=MAX_STEPS):
    """``{'understood','tasks','ask'}`` from the initial pass; raises on junk."""
    text = str(raw or '').strip()
    if not text:
        raise AgentError('the reasoning pass returned nothing')
    loaded = None
    try:
        loaded = json.loads(text, strict=False)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 and end > start:
            try:
                loaded = json.loads(text[start:end + 1], strict=False)
            except ValueError:
                loaded = None
    if not isinstance(loaded, dict):
        raise AgentError(
            'the reasoning pass did not answer with the task JSON; got: %s'
            % ' '.join(text.split())[:300])
    raw_tasks = loaded.get('tasks')
    if raw_tasks is None:
        raw_tasks = []
    if not isinstance(raw_tasks, list):
        raise AgentError('the task list is %r, not a list'
                         % type(raw_tasks).__name__)
    tasks = []
    for item in raw_tasks:
        task = ' '.join(str(item or '').split()).strip()
        if task and task not in tasks:
            tasks.append(task[:400])
    ask = ' '.join(str(loaded.get('ask') or '').split()).strip()
    understood = ' '.join(str(loaded.get('understood') or '').split()).strip()
    if not tasks and not ask:
        raise AgentError(
            'the reasoning pass neither listed a task nor asked a question; '
            'got: %s' % json.dumps(loaded)[:300])
    if len(tasks) > int(max_tasks):
        raise AgentError(
            'the request was split into %d tasks; this workspace allows %d '
            '(raise policy.max_steps in workspace.json)'
            % (len(tasks), int(max_tasks)))
    return {'understood': understood, 'tasks': tasks, 'ask': ask}


def _reason_prompt(query, steps, memory='', usable=(), pending=()):
    """The reasoning prompt: request, tools, memory, what was done."""
    lines = ['REQUEST: ' + ' '.join(str(query).split())[:600], '']
    lines.append('TOOLS THIS WORKSPACE OWNS (a picker maps an action to one):')
    lines.append(catalog.digest(list(usable), limit=2000) or '  (none)')
    if memory:
        lines += ['', memory]
    lines.append('')
    lines.append('WHAT HAS BEEN DONE SO FAR (in order):')
    if not steps:
        lines.append('  (nothing yet)')
    for index, step in enumerate(steps, 1):
        result = ' '.join(str(step.get('result') or '').split())[:RESULT_PREVIEW]
        lines.append('  %02d %s — task: %s — result: %s'
                     % (index, step.get('chain'),
                        ' '.join(str(step.get('why') or '').split())[:160],
                        result or '(no output)'))
    if pending:
        lines += ['', 'TASKS STILL WAITING (from the task list):']
        lines += ['  - ' + str(task)[:160] for task in pending]
    lines += ['', 'Answer with the JSON object only.']
    return '\n'.join(lines)


def _parse_verdict(raw):
    """``{'done','answer','next','ask'}`` from the reasoner; raises on junk."""
    text = str(raw or '').strip()
    if not text:
        raise AgentError('the reasoner returned nothing')
    loaded = None
    try:
        loaded = json.loads(text, strict=False)
    except ValueError:
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 and end > start:
            try:
                loaded = json.loads(text[start:end + 1], strict=False)
            except ValueError:
                loaded = None
    if not isinstance(loaded, dict):
        raise AgentError(
            'the reasoner did not answer with the verdict JSON; got: %s'
            % ' '.join(text.split())[:300])
    verdict = {'done': bool(loaded.get('done')),
               'answer': str(loaded.get('answer') or '').strip(),
               'next': str(loaded.get('next') or '').strip(),
               'ask': str(loaded.get('ask') or '').strip()}
    if not (verdict['done'] or verdict['next'] or verdict['ask']):
        raise AgentError(
            'the reasoner verdict says neither done, next nor ask; got: %s'
            % json.dumps(loaded)[:300])
    return verdict


#: The planner prompt is deterministic: same catalog + same request + same
#: learned evidence, same text.
def _planner_prompt(query, usable, learned=''):
    lines = [
        'REQUEST: ' + ' '.join(str(query).split())[:600],
        '',
        'CATALOG (id (name): what it does | needs | returns):',
        catalog.digest(usable, limit=3000),
    ]
    if learned:
        lines += ['', 'WHAT EARLIER RUNS TAUGHT (evidence, not rules):', learned]
    lines += ['', 'Answer with the JSON object only.']
    return '\n'.join(lines)


def _parse_plan(raw, usable):
    """``steps`` from the planner's reply; raises with the raw text excerpt."""
    text = str(raw or '').strip()
    if not text:
        raise AgentError('the planner model returned nothing')
    loaded = None
    try:
        loaded = json.loads(text, strict=False)
    except ValueError:
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            try:
                loaded = json.loads(text[start:end + 1], strict=False)
            except ValueError:
                loaded = None
    if not isinstance(loaded, dict):
        raise AgentError(
            'the planner did not answer with the plan JSON; got: %s'
            % ' '.join(text.split())[:300])
    steps = loaded.get('steps')
    if not isinstance(steps, list):
        raise AgentError(
            'the planner JSON has no "steps" list; got: %s'
            % json.dumps(loaded)[:300])
    return steps


# ----------------------------------------------------------------- gateway

def _gateway_llm(prompt, system, model, cancel=None):
    """One generation through the app's own gateway (raises).

    Uses the SAME path the Builder uses (``/generate_stream``, provider resolved
    from the model reference), so every provider the app is configured with
    works: a GGUF for the local engine, an Ollama tag, or ``provider:model`` for
    a BYOK provider.  The agent is not tied to one backend — it runs whatever
    model the user picked in agent mode.
    """
    if not str(model or '').strip():
        raise AgentError(
            'no reasoner model is selected for this agent: pick one with the '
            'MODEL button in agent mode (it is stored in the workspace)')
    from player.builder import reasoning
    from player.builder.errors import BuilderError
    try:
        return reasoning.generate(prompt, system, {'model': str(model)},
                                  cancel=cancel)
    except BuilderError as exc:
        raise AgentError('the agent model %r failed: %s' % (model, exc)) \
            from exc
