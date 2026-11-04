"""QThread runner for the modular project pipeline (small-model friendly).

One request = a bounded sequence of SMALL model calls (plan -> one call per
module -> assemble the entry script).  The worker writes the modules into the
node workspace itself; the panel persists them onto the node and auto-runs
``script.py`` when the pipeline finishes.

Blocking (non-streaming) calls on purpose: the orchestrator owns the turn
order, so there is no UI-thread re-entrancy.  llama.cpp goes through
``/llamacpp/chat`` (keeps the model warm between calls); Ollama through
``OllamaClient.chat``.
"""
import ast
import logging
import os
import re

from PyQt5.QtCore import QThread, pyqtSignal

from NGUI.i18n import _
from player.code_agent_ops.constants import MAIN_FILE, STEER_TEMP_BASE
from player.code_agent_ops.parsing import (
    _clip,
    _safe_join,
    _unresolved_imports,
)
from player.code_agent_ops.project import (
    entry_prompt,
    extract_code,
    module_prompt,
    parse_plan,
    plan_prompt,
    validate_python,
)

logger = logging.getLogger(__name__)

__all__ = ['_ProjectPipelineWorker', 'SYSTEM_PIPELINE']

SYSTEM_PIPELINE = (
    'You are a precise Python code generator. Reply with code only, inside a '
    'single ```python block. No explanations, no apologies, no markdown '
    'tables.'
)

# The planning call MUST NOT inherit SYSTEM_PIPELINE.  A small model follows
# the system prompt over the user prompt, so "reply with code only" made it
# try to build the whole app in the plan turn (and the plan was lost, leaving
# the pipeline to fall back to one giant script).
SYSTEM_PLAN = (
    'You are a software architect. You never write code. You answer with the '
    'plan lines only: one MAIN: line, then MODULE: lines.'
)

_MAX_MODULE_TOKENS = 1400
_MAX_ENTRY_TOKENS = 1800
_MAX_PLAN_TOKENS = 320

_TOP_LEVEL_RE = re.compile(r'^(?:class|def|async def)\s+(\w+)', re.M)


def _top_level_names(code):
    """Top-level class/function names, in source order (regex: works on
    half-written code that does not parse)."""
    return _TOP_LEVEL_RE.findall(str(code or ''))


def _duplicate_names(code):
    """Names defined more than once (a split leaf re-wrote the whole file)."""
    names = _top_level_names(code)
    seen, dup = set(), set()
    for n in names:
        if n in seen:
            dup.add(n)
        seen.add(n)
    return dup


def _is_skeleton(code):
    """True when the part is declarations only (no function has a real body).

    A split asks for \"declarations first, bodies second\".  A small model
    usually answers the first ask with the WHOLE file, so asking for the
    second half only produced a duplicate file (\"repeated definitions\") and
    the whole app was thrown away.  A part with real bodies IS the unit.
    """
    try:
        tree = ast.parse(str(code or ''))
    except Exception:
        return False

    def _stub(body):
        for stmt in body:
            if isinstance(stmt, ast.Pass):
                continue
            if isinstance(stmt, ast.Expr) and isinstance(
                    stmt.value, (ast.Constant, ast.Str)):
                continue                    # docstring / ...
            if isinstance(stmt, ast.Raise):
                continue                    # NotImplementedError stub
            return False
        return True

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not _stub(node.body):
                return False
    return True


class _PipelineCancelled(RuntimeError):
    """Stop was pressed between two pipeline calls."""


class _ProjectPipelineWorker(QThread):
    """plan -> per-module -> entry script, all off the UI thread."""

    stage = pyqtSignal(str)               # human-readable progress line
    trace = pyqtSignal(str)               # request/response of every call
    tasks_planned = pyqtSignal(list)      # the full task list of this request
    task = pyqtSignal(str, str, str)      # name, state, detail
    module_done = pyqtSignal(str, bool, str)   # rel, ok, note
    finished_pipeline = pyqtSignal(object)     # result dict

    def __init__(self, request, model, use_llamacpp, workspace, inputs,
                 outputs, existing_files=None, stop_ref=None):
        super().__init__()
        self._request = str(request or '')
        self._model = model
        self._use_llamacpp = bool(use_llamacpp)
        self._workspace = workspace
        self._inputs = list(inputs or [])
        self._outputs = list(outputs or [])
        self._existing = dict(existing_files or {})
        self._stop_ref = stop_ref
        try:
            from NGUI.nodes_resources.base_node import get_default_api_url
            self._api = (get_default_api_url() or '').strip().rstrip('/')
        except Exception:
            self._api = ''

    # -- plumbing ------------------------------------------------------
    def _stopped(self):
        try:
            return bool(self._stop_ref and self._stop_ref.get('flag'))
        except Exception:
            return False

    def _ask(self, prompt, max_tokens, label='call',
             system=SYSTEM_PIPELINE):
        """One blocking generation call.  Raises on stop / server error.

        Every call is traced to the Terminal page: with N calls per project
        (plan + one per module + entry) a silent failure is undiagnosable.
        """
        if self._stopped():
            raise _PipelineCancelled()
        self.trace.emit('\u226b {}{} | {:,} chars | max {} tok'.format(
            label, '' if self._use_llamacpp else ' [ollama]',
            len(prompt), max_tokens))
        self.trace.emit(prompt)
        logger.info('[PIPELINE] %s request chars=%d max_tokens=%d',
                    label, len(prompt), max_tokens)
        text = ''
        try:
            text = self._ask_raw(prompt, max_tokens, system)
        except Exception as e:
            self.trace.emit('\u2717 {} failed: {}'.format(label, e))
            logger.warning('[PIPELINE] %s failed: %s', label, e)
            raise
        self.trace.emit('\u226a {} reply {:,} chars'.format(label, len(text)))
        self.trace.emit(text if text.strip() else '(empty reply)')
        logger.info('[PIPELINE] %s reply chars=%d', label, len(text))
        return text

    def _ask_raw(self, prompt, max_tokens, system=SYSTEM_PIPELINE):
        """The transport half of :meth:`_ask`."""
        if self._stopped():
            raise _PipelineCancelled()
        text = ''
        if self._use_llamacpp:
            import requests as _req
            url = self._api + '/llamacpp/chat'
            resp = _req.post(url, json={
                'model': self._model,
                'prompt': prompt,
                'system': system,
                'temperature': STEER_TEMP_BASE,
                'max_tokens': max_tokens,
                # ONE round per unit.  A reply that needs a continuation is
                # by definition too big: the pipeline bisects it into two
                # smaller units instead of re-feeding the model its own
                # output (quadratic prompt growth, no progress).
                'max_rounds': 1,
            }, timeout=None)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and data.get('error'):
                raise RuntimeError(str(data.get('error')))
            text = str((data or {}).get('response') or '')
        else:
            from AI.consult import OllamaClient
            client = OllamaClient(base_url=self._api)
            data = client.chat(model=self._model, prompt=prompt,
                               system=system,
                               temperature=STEER_TEMP_BASE,
                               max_tokens=max_tokens, stream=False) or {}
            if data.get('error'):
                raise RuntimeError(str(data.get('error')))
            text = str(data.get('response') or data.get('content') or '')
        if self._stopped():
            raise _PipelineCancelled()
        return text

    @staticmethod
    def _coerce_code(text):
        """Fenced block, else the raw reply when it already parses as Python."""
        code = extract_code(text)
        if code:
            return code
        raw = str(text or '').strip()
        if raw and not validate_python(raw):
            return raw
        return ''

    def _generate_file(self, prompt, max_tokens, label='file', scope='',
                       force_split=False):
        """ONE round per unit; a unit that needs more splits in two, recursively.

        Policy: a generation call gets exactly one round (the engine runs no
        continuation rounds).  If that single round does not produce a
        complete, valid unit, the unit is bisected into two halves and each
        half gets its own single round - recursively, until every leaf is
        small enough for the model to finish in one shot.  This keeps each
        ask local and bounded instead of re-feeding the model its own output
        round after round.

        Laya's grade only picks WHERE the first split happens: 'complex' is
        bisected before the first round, everything else is tried whole once.
        """
        self._unit_calls = 0
        self._unit_complete = False
        acc = []
        level = 'complex' if force_split else self._complexity(label, scope)
        if level == 'complex':
            self.trace.emit('! {} graded COMPLEX - splitting before the first '
                            'round'.format(label))
            for half in self._bisect(prompt, '', label, 0):
                err = self._build(prompt, max_tokens, label, half, 1, acc)
                if err:
                    return '', err
                if self._unit_complete:
                    break                   # the first part was the whole unit
        else:
            err = self._build(prompt, max_tokens, label, '', 0, acc)
            if err:
                return '', err
        code = '\n\n'.join(p for p in acc if p.strip())
        err = validate_python(code)
        if err:
            self.trace.emit('\u2717 {} did not assemble: {}'.format(label, err))
            return '', err
        dups = _duplicate_names(code)
        if dups:
            # A split leaf that re-wrote the whole file shows up here: every
            # symbol twice.  Shipping it hides the failure.
            self.trace.emit('\u2717 {} repeated definitions: {}'.format(
                label, ', '.join(sorted(dups))))
            return '', _('duplicate definitions: {}').format(
                ', '.join(sorted(dups)))
        return code, ''

    # -- recursive bisection --------------------------------------------
    _MAX_DEPTH = 3          # 1 unit -> 2 -> 4 -> 8 leaves
    _MAX_CALLS = 12         # hard call ceiling per file, split calls included

    def _complexity(self, label, scope):
        """Laya's grade for one file, or '' when it cannot answer."""
        if not scope:
            return ''
        try:
            from AI.laya_hooks import task_complexity
            level = task_complexity('%s: %s' % (label, scope))
        except Exception:
            return ''
        if level:
            self.trace.emit('  {} graded {} by Laya'.format(label, level))
        return level or ''

    def _build(self, prompt, max_tokens, label, goal, depth, acc, draft=''):
        """Generate ONE unit in ONE round, or bisect it.  '' on success."""
        if self._stopped():
            raise _PipelineCancelled()
        if self._unit_calls >= self._MAX_CALLS:
            return _('{}: call budget exhausted').format(label)
        tag = label if not goal else '%s [split %d]' % (label, depth)
        body = self._ask(self._unit_prompt(prompt, goal, acc, draft),
                         max_tokens, tag)
        self._unit_calls += 1
        code = self._coerce_code(body)
        err = validate_python(code) if code else _('no Python code block')
        if not err:
            acc.append(code)
            # One round per unit: a part that already carries real bodies IS
            # the unit.  Asking for the remaining halves only makes the model
            # rewrite the file (duplicate definitions -> nothing ships).
            self._unit_complete = not _is_skeleton(code)
            return ''
        if depth >= self._MAX_DEPTH:
            return _('{}: still too big after {} splits ({})').format(
                label, depth, err)
        # Bisect the WORK, not the wording: the reply itself names the
        # top-level items, so the split never depends on the model obeying a
        # "reply with PART lines" format (a small model answers that with
        # another wall of code - measured).
        halves = self._halves(code or str(body or ''), goal, label, depth)
        self.trace.emit('  ! {} one round was not enough ({}) - splitting into '
                        '2 at depth {}'.format(tag, err, depth + 1))
        self.task.emit(label, 'running', 'split %d' % (depth + 1))
        for half in halves:
            e = self._build(prompt, max_tokens, label, half, depth + 1, acc,
                            code or str(body or ''))
            if e:
                return e
            if self._unit_complete:
                break
        return ''

    def _halves(self, draft, goal, label, depth):
        """Two goals covering the draft's top-level items (or its halves)."""
        names = _top_level_names(draft)
        if len(names) >= 2:
            mid = len(names) // 2
            return ['ONLY these top-level items, complete: '
                    + ', '.join(names[:mid]),
                    'ONLY these top-level items, complete: '
                    + ', '.join(names[mid:])]
        # No structure to split on: with no draft there is nothing to ask
        # about, so split deterministically (one less model call).
        if not str(draft or '').strip():
            return [_('declarations: imports, classes and function signatures / '
                      'skeletons'),
                    _('the implementations: the body of every function and '
                      'method')]
        # No structure to split on: ask once, fall back to a plain bisection.
        if self._unit_calls < self._MAX_CALLS:
            outline = self._ask(
                '### Request\n' + str(draft)[:1200] +
                '\n### Task\nReply with EXACTLY two lines, no code, no prose:\n'
                'PART 1: <the first half%s>\nPART 2: <the second half%s>\n'
                % ((' for: ' + goal) if goal else '',
                   (' for: ' + goal) if goal else ''),
                160, '%s (bisect %d)' % (label, depth + 1))
            self._unit_calls += 1
            parts = [ln.split(':', 1)[1].strip()
                     for ln in str(outline or '').splitlines()
                     if ln.strip().upper().startswith('PART') and ':' in ln]
            if len(parts) >= 2:
                return parts[:2]
        return [_('declarations: imports, classes and function signatures / '
                  'skeletons'),
                _('the implementations: the body of every function and method')]

    def _unit_prompt(self, prompt, goal, acc, draft=''):
        """Prompt for one unit: the whole file, a named item set, or a half."""
        prev = ''
        if acc:
            prev = ('\n### Already written (continue after it, never repeat '
                    'it)\n```python\n' + '\n\n'.join(acc)[-3000:] +
                    '\n```\n')
        ref = ''
        if draft:
            ref = ('\n### Previous draft (incomplete or invalid - reuse it, do '
                   'not copy it whole)\n```python\n' + str(draft)[-3000:] +
                   '\n```\n')
        if not goal:
            task = ('### Task\nWrite the WHOLE file in ONE ```python block. It '
                    'must be valid Python on its own.\n')
        else:
            task = ('### Task\nWrite %s\n'
                    '- Output Python that continues the file; import only what '
                    'this part uses.\n'
                    '- Do not repeat code already written, no prose.\n'
                    '- ONE ```python block.\n' % goal)
        return prompt + prev + ref + task

    def _bisect(self, prompt, goal, label, depth):
        return self._halves('', goal, label, depth)

    # -- import repair --------------------------------------------------
    def _missing_imports(self, rel, code, files):
        """Modules that file imports but the project does not contain.

        The plan often lists a module whose generation failed (invalid
        syntax twice) - the entry script still imports it and the project
        ships broken.  One targeted re-ask closes that hole before the run.
        """
        missing = _unresolved_imports(code, files)
        if not missing:
            return []
        self.trace.emit('! {} imports missing modules: {}'.format(
            rel, ', '.join(missing)))
        return missing

    def _repair_imports(self, rel, code, missing, files, max_tokens):
        """Re-ask for one file with the broken imports spelled out."""
        existing = sorted(str(p) for p in files if p != rel)
        prompt = (
            '### Request\n' + self._request.strip() + '\n\n'
            '### File to rewrite: ' + rel + '\n```python\n' +
            _clip(code, 4000) + '\n```\n\n'
            '### Broken imports\nThese modules do not exist in this project: '
            + ', '.join(missing) + '\n\n'
            'Return the WHOLE file again in one ```python block:\n'
            '- Delete those imports and inline what you needed (or use the '
            'stdlib).\n'
            '- Import ONLY these project modules: '
            + (', '.join(existing) or '(none)') + '\n'
            '- No prose.\n'
        )
        fixed, err = self._generate_file(prompt, max_tokens,
                                         rel + ' (import fix)')
        return fixed, err

    def _repair_broken_imports(self, result, written, files):
        """One repair call per file that imports a module the project lacks.

        Runs at the END of the pipeline: only then does the project know
        which planned modules actually made it to disk."""
        seen = dict(files)
        failed = result.setdefault('failed', [])
        for rel in list(written):
            code = written.get(rel) or ''
            missing = self._missing_imports(rel, code, seen)
            if not missing:
                continue
            if self._stopped():
                raise _PipelineCancelled()
            fixed, err = self._repair_imports(rel, code, missing, seen,
                                              _MAX_MODULE_TOKENS)
            if not fixed:
                failed.append((rel, err or _('imports missing: ')
                               + ', '.join(missing)))
                continue
            try:
                with open(_safe_join(self._workspace, rel), 'w',
                          encoding='utf-8') as f:
                    f.write(fixed)
            except Exception as e:
                failed.append((rel, str(e)))
                continue
            written[rel] = fixed
            seen[rel] = fixed
            left = self._missing_imports(rel, fixed, seen)
            if left:
                failed.append((rel, _('still imports missing: ')
                               + ', '.join(left)))
            else:
                self.trace.emit('\u2713 {} imports repaired'.format(rel))

        main_code = result.get('main_code') or ''
        if not main_code:
            return
        missing = self._missing_imports(MAIN_FILE, main_code, seen)
        if not missing:
            return
        if self._stopped():
            raise _PipelineCancelled()
        fixed, err = self._repair_imports(MAIN_FILE, main_code, missing, seen,
                                          _MAX_ENTRY_TOKENS)
        if not fixed:
            result['error'] = _('{} imports missing modules: {}').format(
                MAIN_FILE, ', '.join(missing))
            failed.append((MAIN_FILE, err or result['error']))
            return
        result['main_code'] = fixed
        seen[MAIN_FILE] = fixed
        left = self._missing_imports(MAIN_FILE, fixed, seen)
        if left:
            result['error'] = _('{} imports missing modules: {}').format(
                MAIN_FILE, ', '.join(left))
            failed.append((MAIN_FILE, result['error']))
        else:
            self.trace.emit('\u2713 {} imports repaired'.format(MAIN_FILE))

    # -- pipeline ------------------------------------------------------
    def run(self):
        result = {'ok': False, 'main_code': '', 'main_desc': '',
                  'modules': [], 'failed': [], 'error': '', 'stopped': False}
        try:
            self.stage.emit(_('Planning the project...'))
            plan_text = self._ask(
                plan_prompt(self._request, self._inputs, self._outputs,
                            sorted(self._existing)), _MAX_PLAN_TOKENS, 'plan',
                system=SYSTEM_PLAN)
            main_desc, modules = parse_plan(plan_text)
            plan_ok = bool(main_desc)
            if not plan_ok:
                # The model answered the plan ask with code or prose.  Do NOT
                # ask again: measured, the retry burned a whole generation and
                # answered with an unrelated file.  Fall back deterministically
                # - one entry script, pre-split before the first round - and let
                # the run loop drive the fixes.
                self.trace.emit('  ! the plan reply was not a plan - writing '
                                'the entry script in parts')
                main_desc = self._request.strip().splitlines()[0][:160]
            result['main_desc'] = main_desc
            logger.info('Code studio plan: main=%r modules=%s',
                        main_desc, [m[0] for m in modules])
            self.tasks_planned.emit(['plan'] + [p for p, _d in modules] +
                                    [MAIN_FILE])
            self.task.emit('plan', 'ok', main_desc[:60])

            written = {}
            plan_lines = [f'{p}: {d}' for p, d in modules]
            for i, (path, desc) in enumerate(modules, 1):
                if self._stopped():
                    raise _PipelineCancelled()
                self.stage.emit(_('Module {}/{}: {}').format(
                    i, len(modules), path))
                self.task.emit(path, 'running', '')
                prompt = module_prompt(
                    self._request, path, desc, plan_lines,
                    {**self._existing, **written}, self._inputs, self._outputs,
                    existing_code=self._existing.get(path, ''))
                code, err = self._generate_file(prompt, _MAX_MODULE_TOKENS,
                                                path, scope=desc)
                if err:
                    result['failed'].append((path, err))
                    self.task.emit(path, 'failed', err[:80])
                    self.module_done.emit(path, False, err)
                    continue
                try:
                    full = _safe_join(self._workspace, path)
                    d = os.path.dirname(full)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    with open(full, 'w', encoding='utf-8') as f:
                        f.write(code)
                except Exception as e:
                    result['failed'].append((path, str(e)))
                    self.module_done.emit(path, False, str(e))
                    continue
                written[path] = code
                result['modules'].append(path)
                self.task.emit(path, 'ok',
                               _('{} lines').format(len(code.splitlines())))
                self.module_done.emit(
                    path, True, _('{} lines').format(len(code.splitlines())))

            if self._stopped():
                raise _PipelineCancelled()
            self.stage.emit(_('Assembling {}').format(MAIN_FILE))
            self.task.emit(MAIN_FILE, 'running', '')
            files_now = {**self._existing, **written}
            main_code, err = self._generate_file(
                entry_prompt(self._request, main_desc, files_now,
                             self._inputs, self._outputs), _MAX_ENTRY_TOKENS,
                MAIN_FILE, scope=main_desc, force_split=not plan_ok)
            if err:
                result['failed'].append((MAIN_FILE, err))
                result['error'] = _('Entry script: {}').format(err)
                self.task.emit(MAIN_FILE, 'failed', err[:80])
            else:
                result['main_code'] = main_code
                result['ok'] = True
                self.task.emit(MAIN_FILE, 'ok',
                               _('{} lines').format(
                                   len(main_code.splitlines())))

            # The plan can list a module that failed to generate twice; the
            # entry script then imports a file that does not exist and the
            # project dies at import time.  Repair each broken file ONCE with
            # the exact missing names, while the model context is still small.
            self._repair_broken_imports(result, written,
                                        {**self._existing, **written})
        except _PipelineCancelled:
            result['stopped'] = True
        except Exception as e:                    # never raise into Qt
            logger.exception('project pipeline failed')
            result['error'] = str(e)
        finally:
            if self._use_llamacpp and self._api:
                try:
                    import requests as _req
                    _req.post(self._api + '/llamacpp/release', timeout=5)
                except Exception:
                    pass
            self.finished_pipeline.emit(result)
