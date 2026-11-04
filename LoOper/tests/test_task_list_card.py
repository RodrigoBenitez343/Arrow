"""The chat's task card: every pipeline step with a live, animated state."""

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from player.code_agent_ops.panel.task_list import TaskListCard  # noqa: E402


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def _text(card, name):
    return card._rows[name]['label'].text()


def test_rows_start_pending(app):
    card = TaskListCard()
    for name in ('plan', 'scheduler.py', 'script.py'):
        card.add(name)
    assert _text(card, 'plan').startswith('\u00b7')        # pending mark
    assert len(card._rows) == 3


def test_running_row_animates(app):
    card = TaskListCard()
    card.add('scheduler.py')
    card.update('scheduler.py', 'running')
    first = _text(card, 'scheduler.py')
    assert first.startswith('scheduler.py')
    assert card._timer.isActive()
    card._animate()
    assert _text(card, 'scheduler.py') != first           # dots move


def test_state_and_detail_render(app):
    card = TaskListCard()
    card.add('scheduler.py')
    card.update('scheduler.py', 'ok', '120 lines')
    assert _text(card, 'scheduler.py') == '\u2713 scheduler.py  120 lines'
    card.update('scheduler.py', 'failed', 'syntax error')
    assert _text(card, 'scheduler.py').startswith('\u2717')


def test_finish_stops_the_animation_and_fails_leftovers(app):
    card = TaskListCard()
    card.add('a.py')
    card.update('a.py', 'running')
    card.finish()
    assert not card._timer.isActive()
    assert card._rows['a.py']['state'] == 'failed'


def test_unknown_row_is_created_on_update(app):
    card = TaskListCard()
    card.update('run', 'running')
    assert 'run' in card._rows
