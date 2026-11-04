"""One round per unit; anything bigger bisects into two, recursively.

Policy under test: a generation call gets exactly ONE round.  A unit that does
not come back complete and valid is split in two; each half gets its own round;
a half that still fails splits again.  Nothing is retried whole.
"""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from player.code_agent_ops.project_runner import _ProjectPipelineWorker


def _worker(tmp_path, replies, grade=None):
    w = _ProjectPipelineWorker(
        request='build it', model='m', use_llamacpp=False,
        workspace=str(tmp_path), inputs=[], outputs=[])
    w.log = []
    seq = list(replies)

    def ask(prompt, max_tokens, label='call'):
        w.log.append(label)
        return seq.pop(0) if seq else ''

    w._ask = ask
    if grade is not None:
        w._complexity = lambda label, scope: grade
    else:
        w._complexity = lambda label, scope: ''
    return w


BROKEN = '```python\ndef broken(:\n```'


def test_one_round_succeeds_and_stops(tmp_path):
    w = _worker(tmp_path, ['```python\nx = 1\n```'])
    code, err = w._generate_file('prompt', 400, 'small.py')
    assert err == '' and code.strip() == 'x = 1'
    assert w.log == ['small.py'], w.log        # exactly one round


def test_failure_bisects_into_two_units(tmp_path):
    w = _worker(tmp_path, [
        BROKEN,                                  # whole file, one round
        'PART 1: helpers\nPART 2: main',         # bisect
        '```python\ndef helper():\n    pass\n```',      # skeleton -> keep going
        '```python\ndef main():\n    return helper()\n```',
    ])
    code, err = w._generate_file('prompt', 400, 'big.py')
    assert err == '', err
    assert 'def helper' in code and 'def main' in code
    assert w.log == ['big.py', 'big.py (bisect 1)',
                     'big.py [split 1]', 'big.py [split 1]'], w.log


def test_a_failing_half_bisects_again(tmp_path):
    w = _worker(tmp_path, [
        BROKEN,                                   # whole
        'PART 1: first half\nPART 2: second half',
        BROKEN,                                   # first half fails
        'PART 1: a\nPART 2: b',                   # -> bisect it again
        '```python\ndef a():\n    pass\n```',      # skeleton
        '```python\ndef b():\n    return 2\n```',  # real body -> unit done
    ])
    code, err = w._generate_file('prompt', 400, 'deep.py')
    assert err == '', err
    assert 'def a' in code and 'def b' in code, code
    # depth 2 shows up in the label
    assert any('[split 2]' in x for x in w.log), w.log


def test_depth_cap_reports_failure_instead_of_looping(tmp_path):
    replies = [BROKEN] * 4                    # whole + two levels of bisects
    replies += ['PART 1: x\nPART 2: y']
    replies += [BROKEN] * 12                  # every leaf keeps failing
    w = _worker(tmp_path, replies)
    code, err = w._generate_file('prompt', 400, 'huge.py')
    assert code == ''
    assert 'still too big' in err or 'call budget' in err, err


def test_call_budget_is_hard(tmp_path):
    w = _worker(tmp_path, [BROKEN] * 40, grade='complex')
    code, err = w._generate_file('prompt', 400, 'runaway.py')
    assert code == ''
    assert len(w.log) <= w._MAX_CALLS + 1, w.log


def test_complex_grade_splits_before_the_first_round(tmp_path):
    """No draft yet: the halves are deterministic (declarations, bodies) -
    no extra model call, no 'reply with PART lines' format to obey."""
    w = _worker(tmp_path, [
        '```python\nclass A:\n    pass\n```',
        '```python\ndef run():\n    return 1\n```',
    ], grade='complex')
    code, err = w._generate_file('prompt', 400, 'big.py', scope='a GUI')
    assert err == '' and 'class A' in code and 'def run' in code
    assert w.log == ['big.py [split 1]', 'big.py [split 1]'], w.log


def test_simple_grade_keeps_the_single_round(tmp_path):
    w = _worker(tmp_path, ['```python\nx = 1\n```'], grade='simple')
    code, err = w._generate_file('prompt', 400, 'small.py', scope='one helper')
    assert err == '' and w.log == ['small.py'], w.log


def test_reply_without_code_fails_cleanly(tmp_path):
    w = _worker(tmp_path, ['sorry, I cannot'] + [BROKEN] * 20)
    code, err = w._generate_file('prompt', 400, 'x.py')
    assert code == '' and err


def test_bisect_uses_the_draft_symbols_not_a_part_reply(tmp_path):
    """A 2B model answers 'reply with PART lines' with more code.  The split
    must come from the draft's own class/def names, no format compliance."""
    draft = ('```python\ndef alpha():\n    pass\n\ndef beta():\n    pass\n'
             'def gamma():\n    pass\n```')
    w = _worker(tmp_path, [
        draft + '\ndef broken(:\n',        # invalid whole-file draft
        '```python\ndef alpha():\n    pass\n```',
        '```python\ndef gamma():\n    return 3\n```',
    ])
    code, err = w._generate_file('prompt', 400, 'draft.py')
    assert err == '', err
    # no '(bisect' call: the halves came from the draft
    assert not any('bisect' in c for c in w.log), w.log
    assert 'alpha' in code and 'gamma' in code


def test_duplicate_definitions_are_rejected(tmp_path):
    """A leaf that re-wrote the whole file must not ship as a valid file."""
    w = _worker(tmp_path, ['```python\ndef a():\n    pass\n```'])
    w._build = lambda prompt, tokens, label, goal, depth, acc, draft='': (
        acc.extend(['def a():\n    pass', 'def a():\n    pass']) or '')
    code, err = w._generate_file('prompt', 400, 'dup.py')
    assert code == '' and 'duplicate' in err, err


def test_plan_rejects_a_code_reply():
    from player.code_agent_ops.project import parse_plan
    assert parse_plan('```python\n# MODULE: scheduler.py\ndef f(): pass\n```') \
        == ('', [])


def test_plan_written_before_a_code_block_is_kept():
    """A 2B model often plans and then starts coding.  The plan lines count."""
    from player.code_agent_ops.project import parse_plan
    main, mods = parse_plan(
        'MAIN: runs the app\nMODULE: scheduler.py | recurrence math\n'
        '```python\nimport scheduler\n```')
    assert main == 'runs the app'
    assert mods == [('scheduler.py', 'recurrence math')], mods


def test_plan_call_does_not_use_the_coder_system_prompt(tmp_path):
    """The coder system prompt says 'reply with code only' and made the plan
    turn write the app instead of the plan."""
    import player.code_agent_ops.project_runner as pr
    w = pr._ProjectPipelineWorker(
        request='build it', model='m', use_llamacpp=False,
        workspace=str(tmp_path), inputs=[], outputs=[])
    seen = {}

    def ask_raw(prompt, tokens, system=pr.SYSTEM_PIPELINE):
        seen['system'] = system
        return 'MAIN: x\n'

    w._ask_raw = ask_raw
    w._ask('p', 100, 'plan', system=pr.SYSTEM_PLAN)
    assert seen['system'] == pr.SYSTEM_PLAN
    assert 'never write code' in pr.SYSTEM_PLAN.lower()


def test_whole_file_answered_to_a_declarations_ask_is_the_unit(tmp_path):
    """Measured loss: the 'declarations' leaf returned the whole app, the
    'implementations' leaf rewrote it, and duplicate definitions threw the
    app away.  A part with real bodies ends the split."""
    whole = ('```python\nclass A:\n    def run(self):\n        return 42\n'
             '```')
    w = _worker(tmp_path, [whole, '```python\nclass A:\n    pass\n```'])
    code, err = w._generate_file('p', 300, 'app.py', force_split=True)
    assert err == '' and 'return 42' in code, err
    assert w.log == ['app.py [split 1]'], w.log


def test_skeleton_then_bodies_still_assembles(tmp_path):
    w = _worker(tmp_path, [
        '```python\nclass A:\n    def run(self):\n        pass\n```',
        '```python\ndef helper():\n    return 1\n```',
    ])
    code, err = w._generate_file('p', 300, 'app.py', force_split=True)
    assert err == '' and 'def helper' in code and 'class A' in code, err


def test_plan_rejects_a_purpose_that_is_the_filename():
    from player.code_agent_ops.project import parse_plan
    main, mods = parse_plan('MAIN: does it\nMODULE: scheduler.py | scheduler.py')
    assert main == 'does it' and mods == [], mods


def test_plan_needs_a_main_line():
    from player.code_agent_ops.project import parse_plan
    assert parse_plan('MODULE: a.py | parses things') == ('', [])
