"""The canvas always holds the chain: a loaded chain is never off in the dark.

Node positions come from the chain file, so a chain recorded on another screen
(or after a pan) could open entirely outside the visible area — the canvas then
looked empty and the user had to hunt for the graph.  These tests pin the rule:

* a chain whose nodes sit far off-canvas is framed when it is built;
* an empty canvas is left alone (no zoom to nowhere);
* a chain that already fits is not magnified into giant boxes;
* a restored zoom/pan is KEPT, unless it shows none of the chain.

Run:  python -m pytest LoOper/tests/test_canvas_focus.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

VIEW_W, VIEW_H = 900, 640


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _canvas(positions):
    """A real node graph with one node per (type, name, pos) it is given."""
    from NodeGraphQt import NodeGraph

    from NGUI.nodes import InputNode, LLMNode, OutputNode

    graph = NodeGraph()
    for cls in (InputNode, LLMNode, OutputNode):
        graph.register_node(cls)
    for identifier, name, pos in positions:
        graph.create_node(identifier, name=name, pos=pos)
    graph.widget.resize(VIEW_W, VIEW_H)
    graph.widget.show()
    return graph


def _view(graph):
    """GraphViewWidget's focus methods bound to a real canvas (no editor)."""
    from NGUI.graph_view import GraphViewWidget

    class _View:
        focus_chain = GraphViewWidget.focus_chain
        _fit_chain_now = GraphViewWidget._fit_chain_now
        ensure_chain_visible = GraphViewWidget.ensure_chain_visible

    view = _View()
    view.node_graph = graph
    return view


def _visible(graph):
    viewer = graph.viewer()
    return viewer.mapToScene(viewer.viewport().rect()).boundingRect()


FAR_AWAY = [("input.InputNode", "query", [9000, 7000]),
            ("llm.LLMNode", "answer", [9000, 7250]),
            ("output.OutputNode", "result", [9000, 7500])]


def test_a_chain_far_off_canvas_is_brought_into_view(qapp):
    graph = _canvas(FAR_AWAY)
    nodes = graph.all_nodes()
    assert not any(_visible(graph).intersects(node.view.sceneBoundingRect())
                   for node in nodes), "the point of the test: nothing in view"

    _view(graph).focus_chain()
    qapp.processEvents()

    visible = _visible(graph)
    for node in nodes:
        assert visible.contains(node.view.sceneBoundingRect()), node.name()
    # centred on the chain, not merely clipped into a corner
    boxes = [node.view.sceneBoundingRect() for node in nodes]
    chain_center_x = (min(b.left() for b in boxes) +
                      max(b.right() for b in boxes)) / 2.0
    assert abs(visible.center().x() - chain_center_x) < 60, (
        visible.center().x(), chain_center_x)


def test_an_empty_canvas_is_left_alone(qapp):
    graph = _canvas([])
    zoom_before = graph.viewer().transform().m11()
    _view(graph).focus_chain()
    assert graph.viewer().transform().m11() == zoom_before


def test_a_chain_that_already_fits_is_not_magnified(qapp):
    graph = _canvas([("input.InputNode", "a", [0, 0]),
                     ("output.OutputNode", "b", [0, 200])])
    _view(graph).focus_chain()
    qapp.processEvents()
    assert graph.viewer().transform().m11() <= 1.001


def test_a_restored_view_is_kept_while_it_shows_the_chain(qapp):
    from PyQt5.QtCore import QPointF

    graph = _canvas(FAR_AWAY)
    viewer = _view(graph)
    viewer.focus_chain()
    qapp.processEvents()
    zoom = graph.viewer().transform().m11()

    # the user panned a little: still on the chain, so nothing is touched
    node = graph.all_nodes()[0]
    graph.viewer().centerOn(QPointF(node.pos()[0], node.pos()[1]))
    zoom_after_pan = graph.viewer().transform().m11()
    viewer.ensure_chain_visible()
    assert graph.viewer().transform().m11() == zoom_after_pan
    assert zoom_after_pan == zoom


def test_a_restored_view_over_empty_canvas_is_replaced(qapp):
    from PyQt5.QtCore import QPointF

    graph = _canvas(FAR_AWAY)
    viewer = _view(graph)
    # last session ended with the canvas panned somewhere with no node at all
    graph.viewer().centerOn(QPointF(-9000, -9000))
    viewer.ensure_chain_visible()
    qapp.processEvents()

    visible = _visible(graph)
    for node in graph.all_nodes():
        assert visible.contains(node.view.sceneBoundingRect()), node.name()


def test_restoring_the_saved_view_keeps_the_chain_on_screen(qapp):
    """app_state applies the saved zoom/pan, then makes sure it shows a node."""
    from NGUI import app_state

    graph = _canvas(FAR_AWAY)
    view = _view(graph)
    called = []
    view.ensure_chain_visible = lambda: called.append(True)
    app_state._apply_view(view, {'zoom': 0.0,
                                 'center': [-9000.0, -9000.0]})
    assert called, 'the restore never checked that the chain is in view'
