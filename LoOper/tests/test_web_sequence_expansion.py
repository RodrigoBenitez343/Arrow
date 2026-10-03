"""Web sequence expansion — one overlay edits a web session's actions.

The Web Sequence node reuses the Sequence node's expansion overlay (the dialog
infers the web kind from the node), so a browser session's individual actions
can be inspected, reordered, removed and saved back to its session file.

Run:  python -m pytest LoOper/tests/test_web_sequence_expansion.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _Node:
    def __init__(self, ident):
        self.__identifier__ = ident
        self.type_ = ident   # NodeGraphQt's fallback attribute


def test_node_kind_detects_a_web_sequence(qapp):
    import NGUI.graph_elements  # noqa: F401
    from NGUI.dialogs.sequence_expansion_dialog import SequenceExpansionDialog as D

    assert D._node_kind(_Node('web_sequence')) == 'web'
    assert D._node_kind(_Node('web_sequence.WebSequenceNode')) == 'web'
    assert D._node_kind(_Node('sequence')) == 'desktop'


def test_web_sequence_shows_the_expand_button(qapp):
    import NGUI.graph_elements  # noqa: F401
    from NGUI.graph_elements.node_button_bar import NodeButtonBarManager

    class _Bar:
        def __init__(self):
            self.vis = {}

        def set_button_visible(self, btn_id, visible):
            self.vis[btn_id] = visible

    bar = _Bar()
    NodeButtonBarManager._apply_type_rules(None, _Node('web_sequence'), bar)
    assert bar.vis.get('expand') is True
    assert bar.vis.get('record') is True

    # A desktop sequence still expands too (no regression).
    bar2 = _Bar()
    NodeButtonBarManager._apply_type_rules(None, _Node('sequence'), bar2)
    assert bar2.vis.get('expand') is True


def test_web_action_label_names_the_target(qapp):
    """A recorded click is identified by the element it touches — the very thing
    that lets a user spot a stray action (e.g. a click baked onto a page's
    typeahead history) and drop it."""
    import NGUI.graph_elements  # noqa: F401
    from NGUI.dialogs.sequence_expansion_dialog import SequenceExpansionDialog as D

    label = D._web_action_label(0, {
        'type': 'click',
        'locator': {'tag': 'div',
                    'text': 'AI ANGEL recent entity history Benjamin Somebody'}})
    assert label.startswith('1. click')
    assert 'AI ANGEL' in label

    assert D._web_action_label(
        1, {'type': 'key', 'key': 'Enter', 'state': 'down'}) \
        == '2. key Enter (down)'
    assert D._web_action_label(2, {'type': 'type', 'value': 'python jobs'}) \
        == '3. type python jobs'
    assert D._web_action_label(
        3, {'type': 'click',
            'locator': {'tag': 'input', 'placeholder': 'Search'}}) \
        == '4. click Search'
    assert D._web_action_label(
        4, {'type': 'navigate', 'url': 'https://www.linkedin.com/x'}) \
        == '5. navigate https://www.linkedin.com/x'


def test_write_actions_file_preserves_session_keys(tmp_path):
    """Saving an edited session keeps schema_version/mode/start_url — a
    metadata+actions-only write would silently corrupt the file."""
    import NGUI.graph_elements  # noqa: F401
    from NGUI.dialogs.sequence_expansion_dialog import _write_actions_file

    p = tmp_path / 'enter.json'
    p.write_text(json.dumps({
        'schema_version': 1,
        'mode': 'web',
        'metadata': {'created_at': '2026-09-17', 'total_actions': 4,
                     'start_url': 'https://www.linkedin.com/in/womenofai/',
                     'name': 'enter'},
        'actions': [{'type': 'click'}, {'type': 'key'},
                    {'type': 'key'}, {'type': 'click'}],
    }), encoding='utf-8')

    # The user removes one stray action and saves.
    count = _write_actions_file(
        str(p), [{'type': 'key'}, {'type': 'key'}, {'type': 'click'}])
    assert count == 3
    out = json.loads(p.read_text(encoding='utf-8'))
    assert out['schema_version'] == 1 and out['mode'] == 'web'
    assert out['metadata']['start_url'] == \
        'https://www.linkedin.com/in/womenofai/'
    assert out['metadata']['name'] == 'enter'
    assert out['metadata']['total_actions'] == 3
    assert [a['type'] for a in out['actions']] == ['key', 'key', 'click']


def test_crop_preview_clamps_to_the_image(qapp):
    """The element thumbnail is a padded crop that is clamped to the screenshot,
    so an element at the viewport edge still yields a usable preview."""
    from PyQt5.QtGui import QPixmap
    import NGUI.graph_elements  # noqa: F401
    from NGUI.dialogs.sequence_expansion_dialog import SequenceExpansionDialog as D

    shot = QPixmap(100, 50)
    pm = D._crop_preview(shot, [10, 10, 20, 15], pad=4)
    assert pm is not None and (pm.width(), pm.height()) == (28, 23)

    edge = D._crop_preview(shot, [95, 45, 20, 15], pad=4)
    assert edge is not None and edge.width() <= 100 and edge.height() <= 50

    # A degenerate box yields nothing (never a zero-size glyph).
    assert D._crop_preview(shot, [0, 0, 0, 0]) is None
