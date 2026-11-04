"""The project pipeline must not ship an entry script that imports a module
which failed to generate (the app then dies at start and the agent loop spins
on searches)."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from player.code_agent_ops.project_runner import _ProjectPipelineWorker


def _worker(tmp_path):
    return _ProjectPipelineWorker(
        request='build it', model='m', use_llamacpp=True,
        workspace=str(tmp_path), inputs=[], outputs=[])


def test_entry_importing_a_missing_module_is_repaired(tmp_path):
    w = _worker(tmp_path)
    seen = []

    def fake_repair(rel, code, missing, files, tokens):
        seen.append((rel, list(missing)))
        return 'result = 1\n', ''

    w._repair_imports = fake_repair
    result = {'main_code': "import task_list\nimport ui_components\nresult = 1\n"}
    w._repair_broken_imports(result, {}, {})
    assert seen == [('script.py', ['task_list', 'ui_components'])], seen
    assert result['main_code'] == 'result = 1\n'
    assert not result.get('error')


def test_failed_repair_is_reported_not_silent(tmp_path):
    w = _worker(tmp_path)
    w._repair_imports = lambda *a, **k: ('', 'model wrote nothing')
    result = {'main_code': 'import task_list\nresult = 1\n'}
    w._repair_broken_imports(result, {}, {})
    assert 'task_list' in (result.get('error') or ''), result
    assert result['failed'], result


def test_clean_project_is_left_alone(tmp_path):
    w = _worker(tmp_path)
    calls = []
    w._repair_imports = lambda *a, **k: calls.append(a) or ('', '')

    def boom(*a, **k):
        raise AssertionError('repair must not run')

    w._repair_imports = boom
    (tmp_path / 'helpers.py').write_text('x = 1', encoding='utf-8')
    result = {'main_code': 'import helpers\nresult = helpers.x\n'}
    w._repair_broken_imports(result, {}, {'helpers.py': 'x = 1'})
    assert result['main_code'] == 'import helpers\nresult = helpers.x\n'
