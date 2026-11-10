"""describe.py — deterministic behaviour description of a chain.

The builder does not "look at a node list" and improvise.  This module turns
the resolved canvas (``snapshot.py``: nodes, ports with ROLES, edges with
kinds) into a topology digest: start nodes, the execution walk with branches
and loops, per-step data dependencies, phases for long chains, and the
defects the wiring actually contains.

It also describes each NODE on its own (``acting`` / ``does_of``): what THAT
node does with the values it was configured with.  A type-level purpose ("a
web_sequence replays a recording") is not a description of a node, and for a
one-node chain it is the ONLY thing there is to say — so the wiring, the
config and the artifact are read together, per node, instead of being left to
the reader to infer.

Everything here is pure text built from the graph.  No model is involved, so
the description is reproducible and the model's prose can be checked against
it.
"""

import json
import os
import re

from . import artifacts as _artifacts
from . import ports as _ports
from . import runbook as _runbook
from .errors import BuilderCanvasError
from .node_templates import LONG_FIELDS

#: The digest is clipped (explicitly) at this size; drill-down scopes exist
#: for anything the clip cuts.
DIGEST_LIMIT = 12000
#: Share of the digest each block may take.  The blocks are budgeted
#: separately (``_block``), so a long walk can never squeeze out the per-node
#: lines that say what each node DOES, nor the data wires that say which
#: values actually reach which port.
WALK_SHARE = 0.22
TAIL_SHARE = 0.10
DATA_SHARE = 0.13
CONTENT_SHARE = 0.45
#: A node walk longer than this stops being readable as a tree.
MAX_WALK_STEPS = 300
#: Property values printed inline (long text fields are counted instead).
FIELD_PREVIEW = 220


# --------------------------------------------------------------- helpers

def _label(node):
    return '%s %s "%s"' % (node.id, node.type, _clip_text(node.name, 60))


def _clip_text(text, limit):
    """Whitespace-collapsed text, never longer than *limit* characters."""
    text = ' '.join(str(text or '').split())
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + '…'


def _field_line(name, value):
    if isinstance(value, (list, tuple, dict)):
        text = str(value)
    else:
        text = str(value)
    if name in LONG_FIELDS:
        return '%s: %d chars — "%s"' % (name, len(text), _clip_text(text, FIELD_PREVIEW))
    return '%s: %s' % (name, _clip_text(text, FIELD_PREVIEW))


# ------------------------------------------------------- one node's action

def _value(node, *names):
    """``(name, text)`` of the first non-empty property among *names*."""
    for name in names:
        raw = (node.properties or {}).get(name)
        text = ' '.join(str(raw).split()) if raw is not None else ''
        if text:
            return name, text
    return '', ''


def _code_head(code, limit=130, markers=('#')):
    """The first line of code that says something (comments are skipped)."""
    for line in str(code or '').splitlines():
        text = line.strip()
        if text and not text.startswith(markers):
            return _clip_text(text, limit)
    return _clip_text(str(code or '').strip(), limit)


def _js_head(code, limit=130):
    """The first line of page JavaScript that says something.

    JavaScript comments open with ``//`` (or a ``*`` continuation inside a
    block), not ``#``: the default snippet in the node is all comment, so a
    ``#``-only check quoted the explaining comment and never the test.
    """
    return _code_head(code, limit, ('//', '/*', '*', '*/'))


def _result_line(code, limit=130):
    """The statement of a code check that DECIDES: the one setting ``result``.

    A check's first line is usually preparation (``data = str(input_data)
    .lower()``); quoting that named the setup instead of the criterion, so a
    reader learned that something was computed and not what was tested.  The
    LAST assignment wins because it is the one the branch reads; a snippet
    without one falls back to its last statement, which is where a check
    returns.
    """
    lines = [line.strip() for line in str(code or '').splitlines()]
    lines = [line for line in lines if line and not line.startswith('#')]
    for line in reversed(lines):
        if re.match(r'^(result|return)\b', line):
            return _clip_text(line, limit)
    return _clip_text(lines[-1], limit) if lines else ''


#: Lines of a classification prompt that say HOW to answer, not what to decide.
_CLASSIFIER_NOISE = re.compile(
    r'^(you are|respond|answer|reply|return|output|print|do not|don.t|never|'
    r'no other|only|use |keep |follow |format|think|be |the response)',
    re.IGNORECASE)
#: An unedited criterion: the template's ``[describe the condition here]``.
_CRITERION_SLOT = re.compile(r'^\[[^\]]*\]$')


def _criterion(text, limit=140):
    """The CONDITION out of a classification prompt (never the boilerplate).

    A model check is prompted like a classifier — "You are a strict routing
    classifier. Respond with EXACTLY one word..." — and the criterion is one
    line somewhere after that.  Quoting from the top spent the whole budget on
    the instructions, so the condition itself never reached the description.
    The labelled line is taken first; otherwise the instruction lines are
    dropped and what is left is the question being asked.
    """
    raw = str(text or '')
    labelled = re.search(
        r'condition[^:\n]*[:\-]\s*(.+)', raw, re.IGNORECASE | re.DOTALL)
    if labelled:
        raw = labelled.group(1)
    keep = []
    for line in raw.splitlines():
        text_line = ' '.join(line.split())
        if not text_line or _CLASSIFIER_NOISE.match(text_line):
            continue
        keep.append(text_line)
    text = ' '.join(keep).strip()
    if _CRITERION_SLOT.match(text):
        return ''            # still the template: no criterion was written
    return _clip_text(text or '', limit)


def _template(props):
    """How a check names the picture it looks for ('' when none is attached)."""
    img = _image_label(props)
    if img:
        return '"%s"' % img
    if str((props or {}).get('image_data') or '').strip():
        return 'pasted into the node'
    return ''


def _quoted(text):
    """*text* as one value inside a sentence, in the quote style that fits.

    A criterion is often a sentence that quotes its own target ("the text \"x\"
    is visible"); wrapping that in double quotes again read as a typo, so such a
    text is quoted with single ones instead.
    """
    text = str(text or '').strip().rstrip('.')
    if '"' in text:
        return "'%s'" % text if "'" not in text else text
    return '"%s"' % text


def _spot(props):
    """A layout check's target rectangle as ``x, y, w, h`` ('' when unset)."""
    parts = [str((props or {}).get(name) or '').strip()
             for name in ('target_x', 'target_y', 'target_w', 'target_h')]
    return ', '.join(parts) if all(parts) else ''


def _locator_text(raw):
    """The readable part of a web element locator (never the raw JSON).

    A locator is a JSON blob: css, xpath, structural_xpath, classes, viewport,
    frame path.  Printing it put CSS into every description AND into every wire
    prompt, so a check read as ``decides a web check: "{"ancestor_css": ...``
    and the model, given that, described a selector instead of a condition.  The
    text the element shows is the only part a reader can use.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return _clip_text(raw, 60) if raw and not raw.startswith('{') else ''
    if not isinstance(raw, dict):
        return ''
    for key in ('text', 'aria_label', 'placeholder', 'name', 'id', 'role',
                'tag'):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return _clip_text(value, 60)
    return ''


def _json_list(value):
    """A list property a node stores as a JSON string (documents, skills)."""
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    try:
        data = json.loads(str(value or '[]'))
    except ValueError:
        return []
    return [str(item) for item in data
            if str(item).strip()] if isinstance(data, list) else []


def _image_label(props):
    """A visual check's target by the file it looks for (basename, not path)."""
    path = str((props or {}).get('image_path') or '').strip()
    if not path:
        return ''
    return _clip_text(os.path.basename(path.replace('\\', '/')), 50)


def _branch_targets(node, snapshot):
    """Where a check's answers go: ``true → "x"; false → nothing (dead end)``.

    The decision is only half the fact; what the chain does with each answer is
    the other half.  '' without a snapshot (a chain FILE carries the wiring, but
    ``acting`` is called node-wise and has not read it).
    """
    if snapshot is None:
        return ''
    legs = []
    for role, word in ((_ports.BRANCH_TRUE, 'true'),
                       (_ports.BRANCH_FALSE, 'false')):
        names = []
        for edge in snapshot.edges:
            if (edge.src_id != node.id or edge.src_role != role
                    or edge.kind != 'exec'):
                continue
            peer = snapshot.node(edge.dst_id)
            if peer is not None:
                names.append('"%s"'
                             % _clip_text(peer.name or peer.type or peer.id, 40))
        legs.append('%s → %s' % (word, ', '.join(names) if names
                                 else 'nothing (dead end)'))
    return ' — %s' % '; '.join(legs)


#: What a DESKTOP check looks for, grouped the way the engine dispatches it
#: (``conditional_ops._evaluate_conditional`` maps presence/absence/ocr onto
#: ``presence_trigger`` / ``absence_trigger`` / ``ocr_trigger``).  A token left
#: out of these groups fell through to "branches on the condition ..." — the
#: mechanism, never the thing being evaluated.
_DESKTOP_PRESENCE = ('presence', 'presence_trigger', 'wait_presence',
                     'visual', 'visual_trigger')
_DESKTOP_ABSENCE = ('absence', 'absence_trigger', 'wait_absence')
_DESKTOP_OCR = ('ocr', 'ocr_trigger', 'ocr_text', 'text', 'text_present')
_DESKTOP_LAYOUT = ('layout_match', 'layout_match_trigger',
                   'layout_match_conditional')


def acting(node, snapshot=None):
    """What THIS node does, from its own config ('' when the type is unknown).

    ``snapshot`` is optional and only used to fold a check's branch targets
    into its sentence; node-wise callers (a chain file, a test) pass none.

    The type says what a node is FOR (``runbook.purpose_line``); this says what
    the node was built to do: the criterion it decides on, the label it
    supplies, the instruction it writes from, the recording it replays.  A
    description that stops at the type is the superficiality this replaces.

    Property names are the LIVE node's own (``NGUI/nodes_resources/*.py``); a
    guessed name would silently degrade every line to the type's purpose.
    """
    ntype = str(node.type or '').strip().lower()
    props = node.properties or {}

    if ntype == 'input':
        _n, label = _value(node, 'label', 'variable_name', 'name')
        _n, asked = _value(node, 'user_prompt')
        _n, choices = _value(node, 'choices')
        bits = []
        if _truthy(props.get('decision_mode')):
            _n, criterion = _value(node, 'decision_criterion', 'web_llm_prompt')
            bits.append('asks the user and routes on the answer%s'
                        % ((': "%s"' % _clip_text(criterion, 90))
                           if criterion else ''))
        elif asked:
            bits.append('asks the user "%s"' % _clip_text(asked, 100))
        if choices:
            bits.append('one of %s' % _clip_text(choices, 120))
        if _truthy(props.get('web_mode')):
            bits.append('answered in the browser')
        bits.append('passed straight through'
                    if _truthy(props.get('passthrough'))
                    else 'TYPED on the real desktop')
        return ('supplies the value "%s" — %s'
                % (_clip_text(label or 'input', 70), '; '.join(bits)))
    if ntype == 'output':
        _n, label = _value(node, 'label', 'variable_name')
        _n, spoken = _value(node, 'tts_text')
        shown = label or spoken
        return ('prints "%s"' % _clip_text(shown, 70)) if shown \
            else 'prints the value it receives'
    if ntype == 'llm':
        if _truthy(props.get('orchestrator_mode')):
            _n, goal = _value(node, 'orch_goal')
            return ('routes the work as an orchestrator: one tool-chain or '
                    'sub-brain per turn%s'
                    % ((' towards "%s"' % _clip_text(goal, 120)) if goal
                       else ', with a trace'))
        _n, described = _value(node, 'semantic_description')
        _n, model = _value(node, 'model')
        _n, instruction = _value(node, 'prompt', 'system_message')
        _n, out_var = _value(node, 'output_variable')
        head = _clip_text(described or instruction, 130)
        bits = ['writes text with the %s'
                % (('model "%s"' % _clip_text(model, 60)) if model
                   else 'default model')]
        if head:
            bits.append('from "%s"' % head)
        if _truthy(props.get('write_text')):
            bits.append('and types it into the active window')
        elif _truthy(props.get('use_vision')):
            bits.append('reading the screen first')
        if out_var and out_var not in ('llm_output', 'result', 'output'):
            bits.append('leaves it in "%s"' % _clip_text(out_var, 40))
        return ' — '.join(bits)
    if ntype == 'conditional':
        kind = str(props.get('condition_type') or '').strip()
        lowered = kind.lower()
        where = _branch_targets(node, snapshot)
        tail = ' and sends execution down true or false%s' % where
        # The engine decides web mode from ``web_mode`` FIRST and only falls back
        # to the type (``conditional_ops._is_web_conditional``); reading the type
        # alone described a web check as a desktop look-up for a picture.
        web = _truthy(props.get('web_mode')) or lowered == 'web'
        # A file-loop repeats its inner chain while a condition holds; that
        # condition lives in ``loop_condition`` (NOT the visual fields), so it
        # is read first — otherwise every loop said 'loops while "the criterion"'.
        loop = _value(node, 'loop_type')
        if loop[0] == 'loop_type' and loop[1]:
            criterion = _value(node, 'loop_condition', 'target_text',
                               'ocr_text')[1]
            seq = _value(node, 'sequence_file')[1]
            tries = _value(node, 'max_loops')[1]
            head = ('replays "%s"' % _clip_text(os.path.basename(
                seq.replace('\\', '/')), 40)) if seq else 'loops here'
            return ('%s while "%s" holds%s%s'
                    % (head, _clip_text(criterion or 'its recorded condition',
                                        120),
                       (' (up to %s times)' % _clip_text(tries, 6))
                       if tries not in ('', '0') else '', where))
        if web:
            # ``web_condition_type`` is the MECHANISM; the engine
            # (``web_conditions.evaluate``) dispatches on several alias names per
            # variant.  Map EVERY alias to the condition it checks — a variant
            # left unmapped fell through to ``decides a web condition on "loop"``
            # and leaked the raw token where the reader wanted the page fact.
            # Web checks read the live page (element/text/script/layout), never
            # an image, so none of these mentions a picture.
            check = str(props.get('web_condition_type') or 'web').strip().lower()
            to = _value(node, 'web_timeout')[1]
            when = (' (waiting up to %s s)' % _clip_text(to, 6)) \
                if to not in ('', '0') else ''
            if check in ('element_located', 'element', 'visual',
                         'visual_trigger'):
                target = (_locator_text(props.get('web_element_locator'))
                          or _clip_text(props.get('web_target_text') or '', 60))
                if not target:
                    return ('looks for a page element, but none is picked — the '
                            'check can never match, so every pass takes the '
                            'false branch%s' % tail)
                return ('checks that the element "%s" is on the page%s%s'
                        % (target, when, tail))
            if check in ('text_present', 'text', 'loop', 'conditional_loop'):
                # The text to look for lives in ``web_target_text`` and the
                # scope in ``web_text_locator``/``web_text_source`` (page vs
                # element); ``web_element_locator`` is the WRONG field here.
                want = _value(node, 'web_target_text')[1]
                scope = _value(node, 'web_text_source')[1]
                on_el = _locator_text(props.get('web_text_locator'))
                span = (' in the element "%s"' % on_el) if on_el else (
                    ' in the selected element'
                    if str(scope or '').lower() == 'element' else '')
                if not want:
                    return ('looks for page text, but no target text is set — '
                            'the check can never match%s%s' % (span, tail))
                return ('checks that the page shows the text "%s"%s%s%s'
                        % (_clip_text(want, 60), span, when, tail))
            if check in ('browser_js', 'js', 'code', 'code_condition'):
                js = _js_head(props.get('web_js'))
                if not js:
                    return ('runs a page script, but no script is set — the '
                            'check can never match%s' % tail)
                return ('runs a page script "%s" in the browser and takes the '
                        'true branch when it returns truthy%s' % (js, tail))
            if check in ('layout_match', 'layout'):
                target = _locator_text(props.get('web_layout_locator')
                                       or props.get('web_element_locator'))
                return ('scrolls the page until the element %s reaches the '
                        'target spot%s%s'
                        % ('"%s"' % target if target else 'it was picked',
                           when, tail))
            if check in ('llm', 'llm_condition', 'ai'):
                asked = _criterion(props.get('web_llm_prompt')
                                   or props.get('llm_prompt'))
                if not asked:
                    return ('asks the model about the page, but no criterion '
                            'is written — its prompt is still the template, so '
                            'it decides nothing%s' % tail)
                return ('asks the model %s about the page and branches on '
                        'the answer%s' % (_quoted(asked), tail))
            # Unmapped/passthrough type: name what it looks at (its picked
            # element, its text, its script), only falling back to the token
            # when nothing is set — a bare type has no criterion at all.
            target = (_locator_text(props.get('web_element_locator'))
                      or _clip_text(props.get('web_target_text') or '', 60)
                      or _js_head(props.get('web_js'), 60)
                      or _criterion(props.get('web_llm_prompt'), 80))
            return ('decides a web condition on %s%s'
                    % ('"%s"' % target if target else _clip_text(check, 120),
                       tail))
        if lowered in ('code', 'code_conditional', 'python'):
            # Read the RAW code: whitespace is collapsed by ``_value``, and a
            # collapsed program is one long first line.
            detail = _result_line(props.get('code'))
            return ('runs a code test %s and branches on the value it leaves in '
                    'result%s' % (_quoted(detail) if detail else 'it holds',
                                  where))
        if 'llm' in lowered:
            _n, asked = _value(node, 'llm_prompt', 'web_llm_prompt')
            criterion = _criterion(asked)
            seen = (' after reading the screen' if _truthy(
                props.get('llm_use_vision')) else '')
            if not criterion:
                return ('asks the model whether its condition holds%s — but no '
                        'criterion is written, so the prompt still holds the '
                        'template%s' % (seen, where))
            return ('asks the model %s%s and branches on the answer%s'
                    % (_quoted(criterion), seen, where))
        # Desktop visual checks.  The engine maps several spellings onto the
        # same three evaluators (``conditional_ops._evaluate_conditional``), so
        # every spelling names its target the same way; an unmapped one used to
        # fall through to 'branches on the condition "absence_trigger"' — the
        # mechanism, never what was being evaluated.
        if lowered in _DESKTOP_PRESENCE or lowered in _DESKTOP_ABSENCE:
            absent = lowered in _DESKTOP_ABSENCE
            named = _template(props)
            verb = 'is absent from the screen' if absent else 'is on the screen'
            if not named:
                return ('checks whether a template %s, but none is attached — '
                        'the check can never match, so it always takes the '
                        'false branch%s' % (verb, tail))
            thr = str(props.get('threshold') or props.get('confidence')
                      or '').strip()
            return ('checks that the image %s %s%s%s'
                    % (named, verb, (' (match ≥ %s)' % thr) if thr
                       and named.startswith('"') else '', tail))
        if lowered in _DESKTOP_OCR:
            _n, want = _value(node, 'ocr_text', 'target_text')
            _n, region = _value(node, 'region')
            box = ' in a picked screen region' \
                if region and region != '[]' else ''
            if not want:
                return ('reads the screen%s with OCR, but no text is set — the '
                        'check can never match%s' % (box, tail))
            loose = '' if _truthy(props.get('case_sensitive')) \
                else ', ignoring case'
            return ('reads the screen%s with OCR and takes the true branch when '
                    'the text "%s" is there%s%s'
                    % (box, _clip_text(want, 120), loose, tail))
        if lowered in _DESKTOP_LAYOUT:
            named = _template(props) or 'its template'
            spot = _spot(props)
            return ('scrolls the screen until the image %s reaches the target '
                    'spot%s%s' % (named, (' (%s)' % spot) if spot else '',
                                  tail))
        if lowered == 'wait':
            _n, secs = _value(node, 'wait_time')
            return ('waits %s before continuing%s'
                    % ('%s s' % secs[1] if secs[1] else 'a moment', where))
        _n, detail = _value(node, 'target_text', 'ocr_text')
        if not detail:
            # Some checks carry nothing but the sentence saying what they ask.
            _n, detail = _value(node, 'description')
        if detail:
            _n, region = _value(node, 'region')
            where_txt = ' in a picked screen region' \
                if region and region != '[]' else ''
            return ('reads the screen%s for the text "%s"%s'
                    % (where_txt, _clip_text(detail, 120), tail))
        return ('branches on the condition "%s", whose settings are empty — '
                're-open the node and pick what it should check%s'
                % (_clip_text(kind or 'unset', 60), tail))
    if ntype in ('sequence', 'web_sequence', 'chain_import'):
        artifact = _artifacts.describe(ntype, props)
        return artifact['text'] or artifact['problem']
    if ntype == 'form_filler':
        _n, instruction = _value(node, 'instruction')
        _n, mode = _value(node, 'mode')
        _n, include = _value(node, 'fields_include')
        _n, skip = _value(node, 'fields_skip')
        _n, model = _value(node, 'model')
        bits = ['fills a web form with the user\'s data']
        if instruction:
            bits.append('"%s"' % _clip_text(instruction, 130))
        if include:
            bits.append('only the fields %s' % _clip_text(include, 100))
        if skip:
            bits.append('skipping %s' % _clip_text(skip, 100))
        if model:
            bits.append('answers written by "%s"' % _clip_text(model, 50))
        if _truthy(props.get('verify')):
            bits.append('verifies what it typed')
        if mode and mode != 'web':
            bits.append('%s mode' % _clip_text(mode, 30))
        return ' — '.join(bits)
    if ntype == 'code':
        code = props.get('code')
        _n, outs = _value(node, 'output_vars')
        tail = ('; hands on %s' % _clip_text(outs, 80)) if outs else ''
        if code:
            return 'runs Python: %s%s' % (_code_head(code), tail)
        # No code attached: the sentence about the node is all it says.
        _n, described = _value(node, 'description')
        return ('runs Python that %s%s' % (_clip_text(described, 130), tail)) \
            if described else ('runs Python code%s' % tail)
    if ntype == 'handle':
        _n, goal = _value(node, 'goal_description', 'target_description')
        _n, action = _value(node, 'action_type')
        _n, target = _value(node, 'target_description')
        where = 'in the browser DOM' if _truthy(props.get('web_mode')) \
            else 'on the screen'
        if not goal:
            return 'acts %s with vision + reasoning' % where
        if str(action).lower() == 'drag' and target:
            return ('drags "%s" onto "%s" %s with vision + reasoning'
                    % (_clip_text(goal, 90), _clip_text(target, 90), where))
        return ('clicks "%s" %s by looking at it with vision + reasoning'
                % (_clip_text(goal, 140), where))
    if ntype == 'mcp':
        _n, tool = _value(node, 'tool_name', 'label', 'mcp_tools')
        _n, folder = _value(node, 'mcp_folder')
        _n, args = _value(node, 'tool_args')
        bits = ['calls the MCP tool "%s"' % _clip_text(tool, 70)] if tool \
            else ['calls an MCP tool']
        if folder:
            bits.append('of the server in "%s"'
                        % _clip_text(os.path.basename(
                            folder.replace('\\', '/')), 50))
        if args and args not in ('{}', '[]'):
            bits.append('with %s' % _clip_text(args, 120))
        if _truthy(props.get('keep_alive')):
            bits.append('kept alive across iterations')
        return ' — '.join(bits)
    if ntype == 'container':
        _n, path = _value(node, 'iso_path')
        _n, out_var = _value(node, 'output_variable')
        if not path:
            return ('runs an isolated agent — no agent file is attached to '
                    'this node, so it does nothing')
        bits = ['runs the isolated agent "%s"'
                % _clip_text(os.path.basename(path.replace('\\', '/')), 60)]
        limits = []
        memory = str(props.get('memory_mb') or '').strip()
        cores = str(props.get('cpu_cores') or '').strip()
        if memory:
            limits.append('%s MB of memory' % memory)
        if cores:
            limits.append('%s core(s)' % cores)
        if limits:
            bits.append('with ' + ' and '.join(limits))
        if _truthy(props.get('hide_window')):
            bits.append('with no window of its own')
        if out_var:
            bits.append('returns its result as "%s"'
                        % _clip_text(out_var, 40))
        return ' — '.join(bits)
    if ntype == 'action':
        _n, kind = _value(node, 'action_type')
        _n, details = _value(node, 'details')
        if kind and details:
            return ('performs the %s "%s"'
                    % (_clip_text(kind, 30), _clip_text(details, 80)))
        return ('performs a recorded %s' % _clip_text(kind, 30)) if kind \
            else 'performs one recorded input action'
    if ntype in ('context', 'memory'):
        _n, label = _value(node, 'label', 'name')
        docs = [os.path.basename(path.replace('\\', '/'))
                for path in _json_list(props.get('documents'))]
        skills = _json_list(props.get('skills'))
        bits = []
        if docs:
            bits.append('the attached document(s) %s'
                        % ', '.join('"%s"' % _clip_text(doc, 46)
                                    for doc in docs[:4]))
            if len(docs) > 4:
                bits[-1] += ' and %d more' % (len(docs) - 4)
        if skills:
            bits.append('the listed skills %s' % ', '.join(
                _clip_text(skill, 30) for skill in skills[:8]))
        if not bits:
            return ('holds the memory pool "%s" — nothing is attached to it, '
                    'so the nodes that read it get no data'
                    % (label or 'context'))
        history = str(props.get('max_history') or '').strip()
        if history and history not in ('0', 'none'):
            bits.append('keeps the last %s exchange(s)' % _clip_text(history, 4))
        return ('holds the memory pool "%s" — %s'
                % (label or 'context', '; '.join(bits)))
    return ''


def _modes(node):
    """Behaviour switches worth saying out loud (repeat counts, headless)."""
    props = node.properties or {}
    out = []
    repeat = str(props.get('loop_count') or '').strip()
    if repeat not in ('', '0', '1'):
        out.append('repeats x%s' % repeat)
    if node.type == 'web_sequence' and _truthy(props.get('headless')):
        out.append('headless browser')
    return out


def does_of(snapshot, node):
    """One sentence: what this node does (its own config, then wiring-free).

    ``snapshot`` may be ``None``: the action comes from the node's own type and
    settings, so a chain FILE (described by ``flow.complete_static``) reads
    the same as a live canvas node.

    One wiring fact is folded in, because the type alone lies about it: an
    OUTPUT whose execution port is wired does not end the chain — it reports
    and hands execution back (a loop marker), and saying "the chain's result"
    for it made a repair loop read as a finished report.
    """
    text = acting(node, snapshot) or _runbook.purpose_line(node.type) \
        or ('%s node' % node.type)
    parts = [text] + _modes(node)
    if snapshot is not None and str(node.type) == 'output':
        reached = []
        for edge in snapshot.exec_out_edges(node.id):
            peer = snapshot.node(edge.dst_id)
            if peer is not None:
                # A form filler carries no name of its own: its type is the
                # only word for it, and "" in a sentence is worse than either.
                reached.append('"%s"' % _clip_text(peer.name or peer.type
                                                    or peer.id, 40))
        if reached:
            parts.append('its output port carries execution on to %s, so the '
                         'chain does not end here'
                         % ', '.join(sorted(set(reached))))
    return _clip_text(' — '.join(part for part in parts if part), 1500)


def wiring_brief(snapshot, node):
    """A compact wire summary: what enters this node and where it goes."""
    parts = []
    incoming = snapshot.exec_in_edges(node.id)
    if incoming:
        parts.append('runs after ' + ', '.join(
            sorted({e.src_id for e in incoming})))
    elif not node.is_passive:
        parts.append('starts the chain (no exec input)')
    outgoing = _branch_order(node, snapshot.exec_out_edges(node.id))
    if outgoing:
        parts.append('hands execution to ' + ', '.join(
            '%s%s' % ({_ports.BRANCH_TRUE: 'true→',
                       _ports.BRANCH_FALSE: 'false→',
                       _ports.LOOP_OUT: 'loop→'}.get(e.src_role, '→'), e.dst_id)
            for e in outgoing))
    elif not node.is_passive:
        parts.append('hands execution nowhere (its exec output is unwired)')
    data_in = snapshot.data_in_edges(node.id)
    if data_in:
        parts.append('reads ' + ', '.join(
            '%s.%s→%s' % (e.src_id, e.src_port, e.dst_port) for e in data_in))
    data_out = snapshot.data_out_edges(node.id)
    if data_out:
        parts.append('emits ' + ', '.join(
            '%s→%s.%s' % (e.src_port, e.dst_id, e.dst_port) for e in data_out))
    return '; '.join(parts)


def resolve(snapshot, ref):
    """A snapshot node from an id or a name (unique match required)."""
    ref = str(ref or '').strip()
    if not ref:
        raise BuilderCanvasError('a node reference is required (id or name)')
    node = snapshot.node(ref)
    if node is not None:
        return node
    exact = [n for n in snapshot.nodes.values() if n.name == ref]
    if len(exact) == 1:
        return exact[0]
    loose = [n for n in snapshot.nodes.values()
             if n.name.lower() == ref.lower()]
    if len(loose) == 1:
        return loose[0]
    if len(loose) > 1 or len(exact) > 1:
        raise BuilderCanvasError(
            'node name %r is ambiguous: %s — use the id'
            % (ref, ', '.join(sorted(n.id for n in (loose or exact)))))
    raise BuilderCanvasError('no node matches %r' % ref)


# ----------------------------------------------------------------- walk

def _branch_order(node, edges):
    """Exec edges of one node in walk order (output, true, false, loop)."""
    order = {_ports.EXEC_OUT: 0, _ports.BRANCH_TRUE: 1,
             _ports.BRANCH_FALSE: 2, _ports.LOOP_OUT: 3}
    return sorted(edges, key=lambda e: (order.get(e.src_role, 9),
                                        e.dst_port))


def walk(snapshot):
    """The execution walk as lines; also returns (lines, order, visited)."""
    lines = []
    steps = {}
    counter = [0]
    visited = set()

    def visit(node_id_, depth, path, via=None):
        node = snapshot.node(node_id_)
        if node is None:
            raise BuilderCanvasError('the walk reached unknown node %s' % node_id_)
        if node_id_ in path:
            lines.append('%s↺ loop back to step %02d (%s)'
                         % ('  ' * depth, steps[node_id_], _clip_text(node.name, 40)))
            return
        counter[0] += 1
        steps.setdefault(node_id_, counter[0])
        visited.add(node_id_)
        incoming = snapshot.exec_in_edges(node_id_)
        join = (' (join: runs after %d upstream branches)' % len(incoming)
                if len(incoming) > 1 else '')
        loop_count = str(node.properties.get('loop_count') or '').strip()
        repeat = (' repeated x%s' % loop_count
                  if loop_count not in ('', '0', '1') else '')
        trigger = (' — via %s' % via) if via else ''
        lines.append('%s%02d %s [%s]%s%s%s'
                     % ('  ' * depth, steps[node_id_], _label(node),
                        node.role or '?', repeat, join, trigger))
        outs = _branch_order(node, snapshot.exec_out_edges(node_id_))
        grouped = {}
        for edge in outs:
            grouped.setdefault(edge.src_role, []).append(edge)
        for role, group in grouped.items():
            marker = ''
            if role == _ports.BRANCH_TRUE:
                marker = 'true '
            elif role == _ports.BRANCH_FALSE:
                marker = 'false '
            elif role == _ports.LOOP_OUT:
                marker = 'loop '
            for edge in group:
                if len(group) > 1:
                    lines.append('%s  %s↓' % ('  ' * depth, marker or '→'))
                visit(edge.dst_id, depth + 1, path | {node_id_},
                      via=(marker.strip() or None))

    roots = sorted(snapshot.roots(), key=lambda n: n.position)
    for root in roots:
        visit(root.id, 0, frozenset())
        lines.append('')

    order = sorted(steps, key=lambda nid: steps[nid])
    return [ln for ln in lines if ln is not None], order, visited, steps


# ------------------------------------------------------------ sections

def header(snapshot):
    counts = snapshot.type_counts()
    edges = snapshot.edges
    exec_edges = sum(1 for e in edges if e.kind == 'exec')
    data_edges = sum(1 for e in edges if e.kind == 'data')
    illegal = sum(1 for e in edges if e.kind == 'illegal')
    return [
        'CHAIN TOPOLOGY — %d node(s), %d execution edge(s), %d data edge(s)%s'
        % (len(snapshot.nodes), exec_edges, data_edges,
           ', %d ILLEGAL edge(s)' % illegal if illegal else ''),
        'TYPES: ' + ', '.join('%s x%d' % (t, n) for t, n in counts.items()),
    ]


def pools(snapshot):
    """Context pools — passive memory, never part of the walk."""
    node_pools = [n for n in snapshot.nodes.values() if n.is_passive]
    if not node_pools:
        return []
    lines = ['MEMORY POOLS (consulted through data ports; never executed):']
    for pool in sorted(node_pools, key=lambda n: n.position):
        used_by = sorted({e.dst_id for e in snapshot.data_out_edges(pool.id)})
        written_by = sorted({e.src_id for e in snapshot.data_in_edges(pool.id)})
        lines.append(
            '  %s — served to %s; written by %s'
            % (_label(pool),
               ', '.join(used_by) or 'nobody',
               ', '.join(written_by) or 'nobody'))
        for name in ('documents', 'skills'):
            raw = str(pool.properties.get(name) or '')
            if raw.strip() and raw.strip() != '[]':
                lines.append('    %s: %s' % (name, _clip_text(raw, 160)))
    return lines


def phases(snapshot, order):
    """The main walk grouped at CHECK/LOOP boundaries (works for long chains)."""
    if not order:
        return []
    groups = []
    current = []
    for nid in order:
        node = snapshot.node(nid)
        current.append(node)
        if node.role in ('CHECK', 'LOOP'):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    lines = ['PHASES (main path; a CHECK/LOOP closes a phase):']
    for index, group in enumerate(groups, 1):
        first, last = group[0], group[-1]
        names = (('"%s"' % _clip_text(first.name, 40)) if first is last else
                 '"%s" → "%s"' % (_clip_text(first.name, 40),
                                  _clip_text(last.name, 40)))
        kinds = sorted({n.role or '?' for n in group})
        lines.append('  P%d (%d step(s), %s): %s'
                     % (index, len(group), '/'.join(kinds), names))
    return lines


def node_content(snapshot):
    """What EVERY node does: its own action, then how it is wired.

    A chain can be one node and nothing else — an atomic tool.  Its topology is
    a single node with no wires, so the walk says nothing about what it DOES.
    Reading only the nodes that point at a recording left every other type
    (llm, conditional, input, code, mcp, handle …) with nothing but its type
    name, which is why a lone node of those types came back as "a stub".
    Every node is described here, from its own settings.
    """
    rows = []
    for node in sorted(snapshot.nodes.values(), key=lambda n: n.position):
        does = does_of(snapshot, node)
        wires = wiring_brief(snapshot, node)
        rows.append('  %s [%s] — %s%s'
                    % (_label(node), node.role or '?', _clip_text(does, 320),
                       ('  | ' + _clip_text(wires, 260)) if wires else ''))
    if not rows:
        return []
    return ['NODE CONTENT (what each node does — its own settings, then how it '
            'is wired):'] + rows


def data_flows(snapshot):
    """Every data edge, with what the destination does with the value."""
    edges = [e for e in snapshot.edges if e.kind == 'data']
    if not edges:
        return ['DATA FLOWS: none — no node consumes another node\'s value '
                '(execution order only).']
    lines = ['DATA FLOWS (%d):' % len(edges)]
    for edge in sorted(edges, key=lambda e: (e.dst_id, e.dst_port)):
        dst = snapshot.node(edge.dst_id)
        dst_port = dst.port('in', edge.dst_port) if dst else None
        meaning = dst_port.meaning if dst_port else edge.dst_role
        lines.append('  %s.%s → %s.%s   %s'
                     % (edge.src_id, edge.src_port, edge.dst_id,
                        edge.dst_port, _clip_text(meaning, 140)))
    return lines


def violations(snapshot, visited=None):
    """The objective defects of this wiring."""
    problems = []
    for edge in snapshot.edges:
        if edge.kind == 'illegal':
            problems.append(
                'illegal wire %s.%s → %s.%s: %s'
                % (edge.src_id, edge.src_port, edge.dst_id, edge.dst_port,
                   _ports.illegal_edge_reason(edge.src_role, edge.dst_role)))

    # Branch ports that lead nowhere.
    for node in snapshot.nodes.values():
        if node.type != 'conditional':
            continue
        is_loop = node.port('out', 'output') is not None
        roles = {e.src_role for e in snapshot.exec_out_edges(node.id)}
        if is_loop:
            if _ports.LOOP_OUT not in roles:
                problems.append('%s [LOOP] has no continuation wire from output'
                                % _label(node))
        else:
            for role, name in ((_ports.BRANCH_TRUE, 'true'),
                               (_ports.BRANCH_FALSE, 'false')):
                if role not in roles:
                    problems.append('%s has no wire on its %r branch'
                                    % (_label(node), name))

    # Dead ends (an execution node whose outputs go nowhere).
    for node in snapshot.exec_nodes():
        if node.role == 'REPORT':
            continue
        outs = [p for p in node.outputs if p.role in _ports.EXEC_ROLES]
        if outs and not snapshot.exec_out_edges(node.id):
            problems.append('%s is a dead end: nothing follows its %s'
                            % (_label(node),
                               '/'.join(p.name for p in outs)))

    # Unreachable execution nodes / multiple starts / no report.
    roots = snapshot.roots()
    if len(roots) > 1:
        problems.append(
            '%d start nodes (expected exactly 1): %s'
            % (len(roots), ', '.join(r.id for r in roots)))
    if not roots:
        problems.append('no start node: every execution node has an incoming '
                        'execution edge (the chain has no beginning)')
    if visited is not None:
        unreachable = [n for n in snapshot.exec_nodes() if n.id not in visited]
        if unreachable:
            problems.append('not in the execution walk: %s'
                            % ', '.join(_label(n) for n in unreachable))
    output_ids = {n.id for n in snapshot.nodes.values()
                  if n.role == 'REPORT'}
    if output_ids and not any(
            e.dst_id in output_ids for e in snapshot.edges
            if e.kind in ('exec', 'data')):
        problems.append('no report node receives anything (the chain '
                        'produces no output)')

    # Per-node structural defects that change behaviour.
    for node in snapshot.nodes.values():
        # A replay/import node is reported through its ARTIFACT, so the two
        # cases stay apart: nothing attached, versus attached but missing (a
        # chain moved, a recording never saved).
        if node.type in ('sequence', 'web_sequence', 'chain_import'):
            problem = _artifacts.describe(node.type, node.properties)['problem']
            if problem:
                problems.append('%s: %s' % (_label(node), problem))
        if node.type == 'input' and not _truthy(
                node.properties.get('passthrough')):
            problems.append(
                '%s [VALUE] has passthrough OFF — it TYPES its value on the '
                'real desktop' % _label(node))
        if node.type == 'llm':
            sources = [e for e in snapshot.data_in_edges(node.id)
                       if e.dst_port == 'prompt']
            if len(sources) > 1:
                problems.append(
                    '%s has %d wires into prompt — only one value can REPLACE '
                    'the template' % (_label(node), len(sources)))
    return problems


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in ('true', '1', 'yes', 'on')


# ------------------------------------------------------------- digests

def _block(lines, budget):
    """One digest block clipped to *budget* chars, saying how much it cut.

    The blocks are budgeted SEPARATELY on purpose.  Joining them and clipping
    the result let the walk and the phases push every per-node line out of the
    digest, so the topology was readable and the nodes were not — which is a
    description of a path, not of what the chain does.
    """
    out, used = [], 0
    for line in (lines or []):
        if used + len(line) + 1 > budget:
            out.append('  … %d more line(s) cut by the digest budget — ask for '
                       'describe_chain with a narrower scope'
                       % (len(lines) - len(out)))
            break
        out.append(line)
        used += len(line) + 1
    return out


def overview(snapshot, limit=DIGEST_LIMIT):
    """The default digest: start, walk, phases, memory, nodes, defects."""
    walk_lines, order, visited, _steps = walk(snapshot)
    roots = snapshot.roots()
    lines = header(snapshot)
    lines.append('')
    lines.append('START: ' + (', '.join(_label(r) for r in roots)
                              if roots else '(none)'))
    for block, share in (
            (['EXECUTION WALK (execution order only; data edges listed '
              'separately):'] + (walk_lines or ['  (nothing to walk)']),
             WALK_SHARE),
            (phases(snapshot, order) + pools(snapshot), TAIL_SHARE),
            # Which value reaches which port — without it a description guesses
            # that an unwired REPORT is "fed" by the step before it.
            (data_flows(snapshot), DATA_SHARE),
            (node_content(snapshot), CONTENT_SHARE)):
        clipped = _block(block, int(limit * share))
        if clipped:
            lines.append('')
            lines.extend(clipped)
    problems = violations(snapshot, visited)
    lines.append('')
    lines.append('TOPOLOGY DEFECTS (%d):' % len(problems))
    lines.extend(['  - ' + p for p in problems] or ['  none'])
    return _fit(lines, limit)


def branch_facts(snapshot):
    """Where each check sends execution, one line per check.

    ``true leads to "review"`` is the decision the chain actually makes; a
    description that says only "it branches" leaves the reader guessing.
    """
    found = []
    for node_id in walk(snapshot)[1]:
        node = snapshot.node(node_id)
        if node is None or node.type != 'conditional':
            continue
        exits = []
        for role, word in ((_ports.BRANCH_TRUE, 'true'),
                           (_ports.BRANCH_FALSE, 'false'),
                           (_ports.LOOP_OUT, 'loop')):
            names = ['"%s"' % _clip_text(peer.name or peer.type or peer.id, 40)
                     for peer in (snapshot.node(e.dst_id)
                                  for e in snapshot.edges
                                  if e.src_id == node_id and e.src_role == role
                                  and e.kind == 'exec')
                     if peer is not None]
            if role == _ports.LOOP_OUT and not names:
                continue          # not a loop node: silence, not a dead end
            exits.append('%s %s' % (word, ', '.join(names) if names
                                    else 'nothing wired'))
        found.append('check "%s": %s'
                     % (_clip_text(node.name or node.type or node.id, 40),
                        '; '.join(exits)))
    return found


def structure(snapshot, limit=2500):
    """The topology alone: order, branches, defects — NO per-node content.

    What a question about an already-described chain needs: the stored flow
    metadata answers "what does it do", this answers "in what order, and where
    does each check go".  Sending the full node content instead is what made a
    plain question cost a whole-canvas prompt.
    """
    walk_lines, _order, visited, _steps = walk(snapshot)
    roots = snapshot.roots()
    lines = header(snapshot)
    lines.append('')
    lines.append('START: ' + (', '.join(_label(r) for r in roots)
                              if roots else '(none)'))
    lines.append('')
    lines.append('EXECUTION WALK:')
    lines.extend(walk_lines or ['  (nothing to walk)'])
    facts = branch_facts(snapshot)
    if facts:
        lines.append('')
        lines.append('BRANCHES:')
        lines.extend('  ' + fact for fact in facts)
    problems = violations(snapshot, visited)
    lines.append('')
    lines.append('TOPOLOGY DEFECTS (%d):' % len(problems))
    lines.extend(['  - ' + p for p in problems] or ['  none'])
    return _fit(lines, limit)


def _fit(lines, limit):
    text = '\n'.join(lines)
    if len(text) <= limit:
        return text
    return (text[:limit]
            + '\n[digest clipped at %d chars — ask for describe_chain with '
              'scope "data", "phases", "node" or "subtree" for the rest]'
              % limit)


def walk_digest(snapshot, limit=DIGEST_LIMIT):
    lines = header(snapshot) + [
        '',
        'EXECUTION WALK (execution order only; data edges listed separately):',
    ] + walk(snapshot)[0]
    return _fit(lines, limit)


def data_digest(snapshot, limit=DIGEST_LIMIT):
    letters = header(snapshot)
    letters.append('')
    letters.extend(data_flows(snapshot))
    return _fit(letters, limit)


def phases_digest(snapshot, limit=DIGEST_LIMIT):
    lines = header(snapshot)
    _, order, visited, _steps = walk(snapshot)
    lines.append('')
    lines.extend(phases(snapshot, order))
    problems = violations(snapshot, visited)
    lines.append('')
    lines.append('TOPOLOGY DEFECTS (%d):' % len(problems))
    lines.extend(['  - ' + p for p in problems] or ['  none'])
    return _fit(lines, limit)


def scope_digest(snapshot, scope='overview', ref=None, limit=DIGEST_LIMIT):
    """Dispatch one describe_chain scope."""
    scope = str(scope or 'overview').strip().lower()
    if scope == 'overview':
        return overview(snapshot, limit)
    if scope == 'walk':
        return walk_digest(snapshot, limit)
    if scope == 'data':
        return data_digest(snapshot, limit)
    if scope == 'phases':
        return phases_digest(snapshot, limit)
    if scope == 'node':
        return node_detail(snapshot, ref, limit)
    if scope == 'subtree':
        return subtree(snapshot, ref, limit)
    raise BuilderCanvasError(
        'unknown describe_chain scope %r (use overview, walk, data, phases, '
        'node, subtree)' % scope)


def node_detail(snapshot, ref, limit=DIGEST_LIMIT):
    """One node in full: role, config, ports with roles, wires."""
    node = resolve(snapshot, ref)
    lines = ['NODE %s' % _label(node),
             'ROLE: %s%s' % (node.role or '?',
                             ' (passive pool: never executed)' if node.is_passive
                             else ''),
             # What this node DOES, before anything about its wiring: a question
             # about one node is a question about its action.
             'DOES: %s' % does_of(snapshot, node),
             'POSITION: (%.0f, %.0f)' % node.position]
    purpose = _runbook.purpose_line(node.type)
    if purpose:
        lines.append('PURPOSE (what a %s node is for): %s'
                     % (node.type, _clip_text(purpose, 400)))
    # What the node points at is its behaviour when the graph is only itself.
    artifact = _artifacts.describe(node.type, node.properties)
    does = does_of(snapshot, node)
    if artifact['text'] and artifact['text'] not in does:
        lines.append('ARTIFACT: ' + _clip_text(artifact['text'], 600))
    if artifact['problem']:
        lines.append('ARTIFACT DEFECT: ' + _clip_text(artifact['problem'], 400))
    if node.properties:
        lines.append('CONFIG:')
        for name in sorted(node.properties):
            lines.append('  ' + _field_line(name, node.properties[name]))
    else:
        lines.append('CONFIG: (no declared properties with values)')
    if node.inputs:
        lines.append('PORTS IN:')
        for port in node.inputs:
            peers = ', '.join('%s.%s' % pair for pair in port.peers) or 'unwired'
            lines.append('  %s — %s   ← %s'
                         % (_ports.port_role_line(port),
                            _clip_text(port.meaning, 100), peers))
    if node.outputs:
        lines.append('PORTS OUT:')
        for port in node.outputs:
            peers = ', '.join('%s.%s' % pair for pair in port.peers) or 'unwired'
            lines.append('  %s — %s   → %s'
                         % (_ports.port_role_line(port),
                            _clip_text(port.meaning, 100), peers))
    incoming = snapshot.exec_in_edges(node.id)
    outgoing = snapshot.exec_out_edges(node.id)
    if incoming or outgoing:
        lines.append('EXECUTION:')
        for edge in incoming:
            lines.append('  ← %s.%s' % (edge.src_id, edge.src_port))
        for edge in outgoing:
            lines.append('  → %s.%s' % (edge.dst_id, edge.dst_port))
    data_in = snapshot.data_in_edges(node.id)
    data_out = snapshot.data_out_edges(node.id)
    if data_in or data_out:
        lines.append('DATA:')
        for edge in data_in:
            lines.append('  value ← %s.%s (into %s)'
                         % (edge.src_id, edge.src_port, edge.dst_port))
        for edge in data_out:
            lines.append('  value → %s.%s (from %s)'
                         % (edge.dst_id, edge.dst_port, edge.src_port))
    return _fit(lines, limit)


def subtree(snapshot, ref, limit=DIGEST_LIMIT):
    """The walk that starts at one node (its downstream behaviour)."""
    start = resolve(snapshot, ref)
    lines = ['SUBTREE FROM %s' % _label(start)]
    visited = set()
    steps = {}
    counter = [0]

    def visit(node_id_, depth, path, via=None):
        node = snapshot.node(node_id_)
        if node_id_ in path:
            lines.append('%s↺ loop back to %s' % ('  ' * depth, node_id_))
            return
        if node_id_ in visited:
            lines.append('%s… %s already shown above' % ('  ' * depth, node_id_))
            return
        if counter[0] >= MAX_WALK_STEPS:
            raise BuilderCanvasError(
                'the subtree exceeds %d steps; describe a smaller node'
                % MAX_WALK_STEPS)
        counter[0] += 1
        visited.add(node_id_)
        steps[node_id_] = counter[0]
        lines.append('%s%02d %s [%s]%s'
                     % ('  ' * depth, counter[0], _label(node),
                        node.role or '?', (' — via %s' % via) if via else ''))
        for edge in _branch_order(node, snapshot.exec_out_edges(node_id_)):
            marker = {_ports.BRANCH_TRUE: 'true', _ports.BRANCH_FALSE: 'false',
                      _ports.LOOP_OUT: 'loop'}.get(edge.src_role, '')
            visit(edge.dst_id, depth + 1, path | {node_id_},
                  marker or None)

    visit(start.id, 0, frozenset())
    return _fit(lines, limit)
