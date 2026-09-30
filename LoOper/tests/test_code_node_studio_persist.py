"""Regression: the Code Node Studio must never write the chain file.

The graph canvas is a temporal editing area.  Saving a Code node may update
the live node object (serialised later by an explicit chain Save), but it must
never overwrite the chain file the user loaded - doing so silently destroyed
chains (e.g. linkedin.json) when a code node was edited/closed after unrelated
canvas edits, and the next reopen showed the corrupted chain.

Run:  python -m pytest LoOper/tests/test_code_node_studio_persist.py
"""

from player.code_agent_ops.panel.mixin_node import NodeMixin


class _FakeNode:
    def __init__(self):
        self.props = {}

    def set_property(self, key, value):
        self.props[key] = value

    def rebuild_ports(self):
        pass


class _FakeCombo:
    def __init__(self, text=''):
        self._text = text

    def currentText(self):
        return self._text


class _FakeConfigManager:
    def __init__(self, chain_file='D:/chains/linkedin.json'):
        self._current_chain_file = chain_file
        self.saved_state = 0
        self.saved_files = []

    def save_current_state(self):
        self.saved_state += 1

    def save_chain(self, file_path=None):
        self.saved_files.append(file_path)


class _FakeGraphView:
    def __init__(self, cm):
        self.config_manager = cm


class _FakeMainWindow:
    def __init__(self, cm):
        self.graph_view = _FakeGraphView(cm)


class _FakePanel:
    """Just enough surface for NodeMixin._persist_node()."""

    def __init__(self, node, cm, workspace):
        self._node = node
        self._mw = _FakeMainWindow(cm)
        self._config = {'output_variable': 'result'}
        self._main_code = 'print("hi")'
        self._in_rows = []
        self._out_rows = []
        self._engine_combo = _FakeCombo('ollama')
        self._model_combo = _FakeCombo('llama3.2:latest')
        self._gguf_combo = _FakeCombo('')
        self._workspace = workspace

    def _workspace_root(self):
        return str(self._workspace)

    def _collect_rows(self, rows):
        return []

    def _refresh_connected_inputs(self):
        pass

    def _refresh_files(self):
        pass


def test_persist_node_never_writes_the_chain_file(tmp_path):
    node = _FakeNode()
    cm = _FakeConfigManager()
    panel = _FakePanel(node, cm, tmp_path)

    NodeMixin._persist_node(panel)

    # The live node received the edit (also proves the body actually ran,
    # so this test cannot pass vacuously on a swallowed exception)...
    assert node.props['code'] == 'print("hi")'
    assert cm.saved_state == 1
    # ...but the loaded chain file was never touched.
    assert cm.saved_files == []
