"""Modular project pipeline: plan parsing + one-call-per-file orchestration.

Small/local models answer a monolithic "write the whole app" ask with no
implementation; the pipeline splits the request into a plan, one focused call
per module, and a final entry-script call.  These tests drive the worker with
scripted replies (no HTTP).
Run:  python -m pytest LoOper/tests/test_code_node_project_pipeline.py
"""
import os

from player.code_agent_ops.project import (
    MAIN_FILE,
    entry_prompt,
    extract_code,
    module_prompt,
    parse_plan,
    pipeline_summary,
    plan_prompt,
    project_outline,
    validate_python,
)
from player.code_agent_ops.project_runner import _ProjectPipelineWorker


def test_parse_plan_tolerates_small_model_formatting():
    text = (
        'Sure, here is the plan:\n'
        '- MAIN: "read the json and print a report"\n'
        '- MODULE: `parsers.py` | parse json, expose load(path)\n'
        '* MODULE: report.py | render rows into text\n'
        'MODULE: parsers.py | duplicate, ignored\n'
        f'MODULE: {MAIN_FILE} | entry, ignored\n'
        'MODULE: notes.md | not python, ignored\n'
    )
    main_desc, modules = parse_plan(text)
    assert main_desc == '"read the json and print a report"'
    assert [m[0] for m in modules] == ['parsers.py', 'report.py']
    assert 'load(path)' in modules[0][1]


def test_extract_and_validate():
    text = 'noise\n```python\ndef f():\n    return 1\n```\nnoise'
    code = extract_code(text)
    assert code.startswith('def f')
    assert validate_python(code) == ''
    assert validate_python('def broken(') != ''


def test_outline_is_signatures_not_bodies():
    files = {'parsers.py': 'import json\n\ndef load(p):\n    return json.load(open(p))\n'}
    outline = project_outline(files)
    assert 'parsers.py' in outline
    assert 'def load(p)' in outline
    assert 'json.load' not in outline          # bodies stay out of context


def test_prompts_carry_request_path_and_ports():
    p = module_prompt('build a report', 'parsers.py', 'parse json',
                      ['parsers.py: parse json'], {}, [{'name': 'data'}],
                      [{'name': 'result'}])
    assert 'build a report' in p
    assert 'Write ONLY this file: parsers.py' in p
    assert 'Input variables: data' in p
    e = entry_prompt('build a report', 'read data', {'parsers.py': 'def load(p): pass'},
                     [], [{'name': 'result'}])
    assert MAIN_FILE in e and 'parsers.py' in e and 'Set "result"' in e
    assert 'MAIN:' in plan_prompt('build a report', [], [], [])


def test_summary_lists_modules_and_failures():
    s = pipeline_summary('read json', ['a.py'], [('b.py', 'boom')])
    assert 'read json' in s and '+ a.py' in s and 'b.py' in s and 'boom' in s


def test_worker_runs_plan_modules_entry_and_writes_files(tmp_path):
    replies = [
        'MAIN: load the json and report the row count\n'
        'MODULE: parsers.py | parse json, expose load(path)\n'
        'MODULE: report.py | render the count, expose render(n)\n',
        '```python\nimport json\n\ndef load(path):\n'
        '    return json.load(open(path))\n```',
        '```python\ndef render(n):\n    return f"rows: {n}"\n```',
        '```python\nfrom parsers import load\nfrom report import render\n'
        'result = render(len(load("data.json")))\n```',
    ]
    worker = _ProjectPipelineWorker(
        request='build a report', model='m', use_llamacpp=True,
        workspace=str(tmp_path), inputs=[], outputs=[{'name': 'result'}])
    worker._api = ''                       # never release a real engine
    worker._ask = lambda prompt, max_tokens, *a, **kw: replies.pop(0)

    result = {}
    worker.finished_pipeline.connect(lambda r: result.update(r))
    worker.run()

    assert result['ok'] and result['main_code'].startswith('from parsers')
    assert result['modules'] == ['parsers.py', 'report.py']
    for rel in ('parsers.py', 'report.py'):
        assert os.path.isfile(os.path.join(str(tmp_path), rel))
    assert not result['failed']


def test_worker_writes_a_module_then_the_entry(tmp_path):
    # One round per unit now: a bad unit is SPLIT (covered in
    # test_pipeline_split.py), so the happy path is plan -> module -> entry.
    replies = [
        'MAIN: single module\nMODULE: util.py | helpers\n',
        '```python\ndef ok():\n    return 1\n```',
        '```python\nfrom util import ok\nresult = ok()\n```',
    ]
    worker = _ProjectPipelineWorker(
        request='make util', model='m', use_llamacpp=True,
        workspace=str(tmp_path), inputs=[], outputs=[])
    worker._api = ''
    worker._ask = lambda prompt, max_tokens, *a, **kw: replies.pop(0)

    result = {}
    worker.finished_pipeline.connect(lambda r: result.update(r))
    worker.run()

    assert result['ok'] and result['modules'] == ['util.py']
    with open(os.path.join(str(tmp_path), 'util.py'), encoding='utf-8') as f:
        assert 'def ok()' in f.read()


def test_worker_stops_between_calls(tmp_path):
    stop = {'flag': False}
    worker = _ProjectPipelineWorker(
        request='build', model='m', use_llamacpp=True,
        workspace=str(tmp_path), inputs=[], outputs=[], stop_ref=stop)

    def ask(prompt, max_tokens, *a, **kw):
        stop['flag'] = True               # user presses Stop after the plan
        return 'MAIN: x\nMODULE: a.py | a\n'

    worker._api = ''
    worker._ask = ask
    result = {}
    worker.finished_pipeline.connect(lambda r: result.update(r))
    worker.run()
    assert result['stopped'] is True
    assert not result['ok']
