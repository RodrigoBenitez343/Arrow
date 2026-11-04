"""plans.py — the agent's memory: what it ran, when, and WHY.

The agent has no hand-wired orchestrator: this file IS what it knows.  Every
run freezes one episode::

    {'goal': 'open linkedin and read the first posts',
     'steps': [{'chain': 'open linkedin',
                'why': 'the user wants linkedin open',
                'inputs': {}}, ...],
     'runs': 2, 'ok': 2, 'fail': 1, 'ask': 0, 'report': 2}

``why`` is the intent the reasoner had when that tool was chosen, so the memory
is not a list of chains — it is *when* a chain was used and *why*.  Three things
are derived from it, all deterministic:

* **replay** — an episode whose goal matches the request (and is still winning)
  runs again without a model call.  That is the frozen knowledge.
* **transitions** — after chain A, which chain came next, and how often it
  worked.  Evidence for the reasoner, never a rule.
* **when to ask / when to report** — per episode, how often the run stopped for
  the user and how often it produced an output.

Matching is Laya's same-question verdict, with an exact-text check first so a
repeated request costs no encoder pass.  An undecided engine is NOT a
mismatch: the caller keeps planning.

File: ``<workspace>/plans.json``.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

VERSION = 1
FILENAME = 'plans.json'
#: A workspace keeps its recent plans; older ones fall off (bounded file).
MAX_PLANS = 200
#: One encoder pass per candidate when looking for a similar episode.
MAX_RECALL_CANDIDATES = 4
#: The BYPASS gate: a stored episode may answer the request without any
#: reasoning pass only when the request is (almost) verbatim — same meaning at
#: this probability, or the exact same text. Measured calibration of the
#: bundled encoder on rewordings of one request is 0.84-0.93 (see
#: ``laya_hooks._SAME_QUESTION_GATE``), so 0.97 leaves only near-identical
#: phrasings; everything below it is EVIDENCE for reasoning, never a decision.
SAME_REQUEST_GATE = 0.97
#: "Accumulated through frequent use": one lucky run is not a lesson.
REPLAY_MIN_WINS = 2


def _score(plan):
    """How much an episode is worth recalling (wins beat losses)."""
    return int(plan.get('ok') or 0) - int(plan.get('fail') or 0)


class PlansError(Exception):
    """The plan file could not be read or written."""


def _norm(text):
    return ' '.join(str(text or '').split()).strip().lower()


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec='seconds')


class PlanStore:
    """The learned plans of ONE workspace."""

    def __init__(self, workspace_dir):
        self.path = os.path.join(str(workspace_dir), FILENAME)

    # -- io ------------------------------------------------------------

    def read(self):
        """``{'version', 'plans', 'transitions'}``; missing file = empty."""
        if not os.path.isfile(self.path):
            return {'version': VERSION, 'plans': [], 'transitions': {}}
        try:
            with open(self.path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            raise PlansError('could not read %s: %s' % (self.path, exc)) from exc
        if not isinstance(data, dict):
            raise PlansError('%s holds %r, not an object'
                             % (self.path, type(data).__name__))
        plans = data.get('plans')
        data['plans'] = plans if isinstance(plans, list) else []
        transitions = data.get('transitions')
        data['transitions'] = transitions if isinstance(transitions, dict) else {}
        data['version'] = int(data.get('version') or VERSION)
        return data

    def write(self, data):
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        temporary = self.path + '.tmp'
        try:
            with open(temporary, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, indent=1)
            os.replace(temporary, self.path)
        except OSError as exc:
            raise PlansError('could not write %s: %s' % (self.path, exc)) from exc
        return data

    # -- queries -------------------------------------------------------

    def plans(self):
        return self.read()['plans']

    def record(self, goal, steps):
        """Store (or refresh) the episode for *goal*; returns the entry.

        A step's ``why`` is kept: it is the intent the reasoner had when the
        tool was chosen, which is what makes the memory answer "when and why"
        instead of only "which chains".
        """
        goal = ' '.join(str(goal or '').split()).strip()
        if not goal:
            raise PlansError('a plan needs the request it answers')
        steps = [{'chain': str(step['chain']),
                  'why': ' '.join(str(step.get('why') or '').split())[:400],
                  'inputs': {str(k): str(v)
                             for k, v in (step.get('inputs') or {}).items()}}
                 for step in steps or () if step.get('chain')]
        if not steps:
            raise PlansError('a plan needs at least one chain')
        data = self.read()
        plan = self._find(data['plans'], goal)
        if plan is None:
            plan = {'goal': goal, 'steps': steps, 'runs': 0, 'ok': 0,
                    'fail': 0, 'updated': _now()}
            data['plans'].append(plan)
        else:
            plan['steps'] = steps
            plan['updated'] = _now()
        del data['plans'][:-MAX_PLANS]
        self.write(data)
        logger.info('Plan learned for %r: %s', goal[:60],
                    ' -> '.join(step['chain'] for step in steps))
        return plan

    def matched(self, goal):
        """The plan for *goal*, or None.

        Exact text first (free), then Laya's same-question verdict.  A
        confident "none of them" and an undecided engine both mean None: the
        caller plans normally.
        """
        data = self.read()
        direct = self._find(data['plans'], goal)
        if direct is not None:
            return direct
        if not data['plans']:
            return None
        goals = [plan['goal'] for plan in data['plans']]
        try:
            from AI import laya_hooks
        except Exception:
            logger.debug('Laya unavailable: no learned-plan match')
            return None
        try:
            verdict = laya_hooks.same_question_verdict(goal, goals)
        except Exception:
            logger.warning('learned-plan match failed', exc_info=True)
            return None
        if not verdict:
            return None
        return self._find(data['plans'], verdict)

    def result(self, goal, ok, asked=False, reported=False):
        """Count one finished run of the plan for *goal*.

        ``asked`` / ``reported`` are the run's own evidence of *when to ask*
        and *when to report*: the chain order decides it (a chain with a
        ``user_prompt`` input stops for the user), and it is recorded here so
        the learned behaviour is visible and can be reasoned about later.
        """
        data = self.read()
        plan = self._find(data['plans'], goal)
        if plan is None:
            return None
        plan['runs'] = int(plan.get('runs') or 0) + 1
        key = 'ok' if ok else 'fail'
        plan[key] = int(plan.get(key) or 0) + 1
        if asked:
            plan['ask'] = int(plan.get('ask') or 0) + 1
        if reported:
            plan['report'] = int(plan.get('report') or 0) + 1
        plan['updated'] = _now()
        self.write(data)
        return plan

    # -- transitions ---------------------------------------------------

    def record_transitions(self, chain_ids, ok):
        """Remember what followed what: A -> B, with the outcome."""
        ids = [str(chain) for chain in chain_ids or () if chain]
        if len(ids) < 2:
            return
        data = self.read()
        table = data['transitions']
        key = 'ok' if ok else 'fail'
        for source, target in zip(ids, ids[1:]):
            row = table.setdefault(source, {})
            cell = row.setdefault(target, {'ok': 0, 'fail': 0})
            cell[key] = int(cell.get(key) or 0) + 1
        self.write(data)

    # -- the bypass ----------------------------------------------------

    def confident(self, goal):
        """The episode that may answer *goal* WITHOUT reasoning, or None.

        The agent is semi deterministic: memory is reassurance, not a shortcut
        on everything.  A replay needs all three:

        * the request is the SAME one — exact text, or a same-question verdict
          at ``SAME_REQUEST_GATE`` (0.97): near-verbatim wording only.  A merely
          similar request is not enough; the loop must reason about it.
        * the episode kept winning (``ok > 0 and ok >= fail``);
        * it won often enough to be a lesson (``ok >= REPLAY_MIN_WINS``).

        Anything less returns None and the request goes through the reasoning
        pass, with the episode as evidence.
        """
        data = self.read()
        if not data['plans']:
            return None
        exact = self._find(data['plans'], goal)
        if exact is not None:
            return exact if self._credible(exact) else None
        ranked = sorted(data['plans'], key=_score, reverse=True)
        ranked = [plan for plan in ranked if self._credible(plan)]
        if not ranked:
            return None
        try:
            from AI import laya_client
            best, probability = None, 0.0
            for plan in ranked[:MAX_RECALL_CANDIDATES]:
                p = laya_client.noul(
                    goal, 'This text asks for the same task as: %s'
                    % str(plan.get('goal'))[:220])
                if p is not None and p > probability:
                    best, probability = plan, float(p)
        except Exception:
            logger.debug('Laya unavailable: no confident episode',
                         exc_info=True)
            return None
        if best is None or probability < SAME_REQUEST_GATE:
            if best is not None:
                logger.info('Episode for %r is only %.2f similar (< %.2f): '
                            'reasoning decides this one',
                            str(best.get('goal'))[:60], probability,
                            SAME_REQUEST_GATE)
            return None
        logger.info('Memory bypass: %r == %r at %.2f (%d ok)', goal[:60],
                    str(best.get('goal'))[:60], probability,
                    int(best.get('ok') or 0))
        return best

    @staticmethod
    def _credible(plan):
        """Won, not losing, and won often enough to be trusted."""
        won = int(plan.get('ok') or 0)
        lost = int(plan.get('fail') or 0)
        return bool(plan.get('steps')) and won >= REPLAY_MIN_WINS and won >= lost

    # -- recall --------------------------------------------------------

    def similar(self, goal, limit=3):
        """Episodes that answer a request LIKE this one, best first.

        Exact text first (free).  Otherwise Laya's same-question verdict over the
        goals the agent has already served, which is the graph retrieval: a new
        request is matched against what was actually run before, and the episode
        comes back with its step order and each step's ``why``.
        """
        data = self.read()
        if not data['plans']:
            return []
        wanted = _norm(goal)
        exact = [plan for plan in data['plans'] if _norm(plan.get('goal')) == wanted]
        if not exact:
            # Rank first (cheap, no encoder) so the bounded candidate set given
            # to the encoder holds the episodes that actually worked.
            ranked = sorted(data['plans'],
                            key=lambda plan: (_score(plan),
                                              str(plan.get('updated') or '')),
                            reverse=True)[:MAX_RECALL_CANDIDATES]
            try:
                from AI import laya_hooks
                verdict = laya_hooks.same_question_verdict(
                    goal, [plan.get('goal') for plan in ranked])
            except Exception:
                logger.debug('Laya unavailable: no similar episode',
                             exc_info=True)
                verdict = None
            exact = [plan for plan in ranked
                     if verdict and _norm(plan.get('goal')) == _norm(verdict)]
        return sorted(exact, key=_score, reverse=True)[:limit]

    def evidence(self, goal, limit=3):
        """Prompt block: past runs of a similar request, with their ``why``.

        Evidence, not instructions: the reasoner reads which tool followed
        which and the intent that chose it, and still decides the next step
        itself.  '' when nothing similar was ever run.
        """
        episodes = self.similar(goal, limit=limit)
        if not episodes:
            return ''
        lines = ['PAST RUNS WITH A SIMILAR REQUEST (evidence, not rules):']
        for episode in episodes:
            steps = []
            for step in episode.get('steps') or ():
                text = str(step.get('chain') or '')
                why = str(step.get('why') or '').strip()
                if why:
                    text += ' (why: %s)' % why[:120]
                steps.append(text)
            wins = int(episode.get('ok') or 0)
            losses = int(episode.get('fail') or 0)
            lines.append('- "%s" -> %s  [%d ok / %d fail]%s'
                         % (str(episode.get('goal'))[:120], ' -> '.join(steps),
                            wins, losses,
                            ', stopped to ask' if int(episode.get('ask') or 0)
                            else ''))
        return '\n'.join(lines)

    def hints(self, limit=6):
        """Prompt text from what the runs taught ('' when nothing is known).

        Deliberately evidence, not instructions: a small model reading this
        sees which chain usually follows which, and which stored requests keep
        failing, without a rule forcing either.
        """
        data = self.read()
        lines = []
        for source, row in sorted(data['transitions'].items()):
            if not isinstance(row, dict):
                continue
            best = None
            for target, cell in row.items():
                if not isinstance(cell, dict):
                    continue
                won = int(cell.get('ok') or 0)
                lost = int(cell.get('fail') or 0)
                if won and won >= lost:
                    if best is None or won > best[1]:
                        best = (target, won)
            if best is not None:
                lines.append('after %s, %s usually follows (%d successful '
                             'run(s))' % (source, best[0], best[1]))
        for plan in data['plans']:
            fail = int(plan.get('fail') or 0)
            ok = int(plan.get('ok') or 0)
            if fail > ok:
                lines.append('the stored plan for %r keeps failing (%d/%d) - '
                             'plan it fresh' % (str(plan.get('goal'))[:60],
                                                fail, fail + ok))
        return '; '.join(lines[:limit])

    @staticmethod
    def replayable(plan):
        """Only a plan that has won at least once and is not losing replays."""
        if not isinstance(plan, dict) or not plan.get('steps'):
            return False
        won = int(plan.get('ok') or 0)
        lost = int(plan.get('fail') or 0)
        return won > 0 and won >= lost

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _find(plans, goal):
        wanted = _norm(goal)
        for plan in plans:
            if _norm(plan.get('goal')) == wanted:
                return plan
        return None
