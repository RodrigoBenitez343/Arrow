"""Composed CodeNodePanel (UiMixin + QWidget + small mixins).

App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import os
import tempfile

from PyQt5.QtWidgets import QWidget

from player.code_agent_ops.panel.mixin_agent import AgentMixin
from player.code_agent_ops.panel.mixin_files_run import FilesRunMixin
from player.code_agent_ops.panel.mixin_node import NodeMixin
from player.code_agent_ops.panel.mixin_tools import ToolsMixin
from player.code_agent_ops.panel.mixin_ui import UiMixin
from player.code_agent_ops.parsing import (
    _apply_hunks,
    _collapse_reasoning,
    _parse_agent_reply,
    _parse_hunks,
    _resolve_workspace,
    _run_grep,
    _safe_join,
)


class CodeNodePanel(UiMixin, QWidget, NodeMixin, FilesRunMixin, ToolsMixin,
                    AgentMixin):
    """The Code Node Studio panel: a thin composition.  Every method lives in
    one of the small mixin submodules under player/code_agent_ops/panel -
    see those for the logic.  The agent loop is the flat, little-coder-style
    loop in mixin_agent; dev-tool routing (needle2 / deterministic tools)
    lives in mixin_tools."""
    pass


def _selfcheck():
    """Pure-function self-check (python -m LoOper.NGUI.widgets.code_node_panel)."""
    root = tempfile.mkdtemp(prefix='cns_selfcheck_')

    assert _safe_join(root, 'a.py') == os.path.join(root, 'a.py')
    assert _safe_join(root, 'pkg/util.py') == os.path.join(root, 'pkg', 'util.py')
    for bad in ('../x.py', '/etc/passwd', 'C:/x.py', '', '..', 'a/../../b.py'):
        try:
            _safe_join(root, bad)
            raise AssertionError(f'safe_join allowed {bad!r}')
        except ValueError:
            pass

    h = _parse_hunks('wrap\n<<<<<<< SEARCH\nold line\n=======\nnew line\n'
                     '>>>>>>> REPLACE\n')
    assert h == [('old line', 'new line')], h
    assert _parse_hunks('no markers here') is None
    try:
        _parse_hunks('<<<<<<< SEARCH\nold\n')
        raise AssertionError('unbalanced hunk must raise')
    except ValueError:
        pass
    out_h, ok_h, _ = _apply_hunks('a\nold line\nb', h)
    assert ok_h and out_h == 'a\nnew line\nb', out_h
    bad_h = _apply_hunks('a\nold line\nold line\nb', [('old line', 'x')])
    assert bad_h[1] is False

    reply = ('Here is a fix plus a helper.\n'
             '```python\nresult = double(21)\n```\n'
             '### FILE: helpers.py\n'
             '```python\ndef double(x):\n    return x * 2\n```\n'
             '### FILE: data/seed.json\n'
             '```json\n{"n": 1}\n```\n')
    main_code, files = _parse_agent_reply(reply)
    assert main_code == 'result = double(21)', main_code
    assert files.get('helpers.py', '').strip() == 'def double(x):\n    return x * 2'
    assert 'data/seed.json' in files

    q, f2 = _parse_agent_reply('just a question, no code')
    assert q is None and f2 == {}

    c1, n1 = _collapse_reasoning('Here.\n<think>hmm\nlet me think</think>\n'
                                 'result = 1\n')
    assert 'result = 1' in c1 and n1 and 'reasoning hidden' in n1
    c3, n3 = _collapse_reasoning('plain answer, no tags')
    assert c3 == 'plain answer, no tags' and n3 is None

    groot = tempfile.mkdtemp(prefix='cns_grep_')
    with open(os.path.join(groot, 'helpers.py'), 'w', encoding='utf-8') as _f:
        _f.write('def note(x):\n    return x\n')
    gres = _run_grep('return', '*', groot, 'result = 1\n', ['helpers.py'])
    assert 'helpers.py:2' in gres and 'script.py' not in gres, gres
    gres4 = _run_grep('result', '*', groot, 'result = 1\n', ['helpers.py'])
    assert 'script.py:1' in gres4, gres4
    assert 'no matches' in _run_grep('missing_thing', '*', groot, 'x = 1\n',
                                     ['helpers.py'])

    ws = _resolve_workspace(None, 'n_selfcheck')
    assert ws.endswith(os.path.join('code_nodes', 'n_selfcheck')), ws
    print('code_node_panel self-check OK')

