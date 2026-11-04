"""Runnable check: wires bend around nodes they would otherwise cross.

Covers the patched PipeItem._bend_around_nodes: a wire drawn through a node
in the middle gets re-routed over it, and a wire leaving a node backwards
gets routed around that node's own card instead of through it.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _pipe_item(graph, src, dst):
    from NGUI.graph_elements.pipe_styling import connected_pipes
    for item in connected_pipes(graph):
        if item.output_port.node is src.view and item.input_port.node is dst.view:
            return item
    raise AssertionError("wire not found")


def test_wire_bends_around_blocking_node(qapp):
    from NodeGraphQt import NodeGraph
    from NGUI.nodes_resources.input_node import InputNode
    from NGUI.nodes_resources.sequence_node import SequenceNode

    graph = NodeGraph()
    graph.register_node(InputNode)
    graph.register_node(SequenceNode)

    inp = graph.create_node("input.InputNode", pos=[0, 0])
    blocker = graph.create_node("sequence.SequenceNode", pos=[300, 0])
    dst = graph.create_node("sequence.SequenceNode", pos=[600, 0])

    out = inp.output_ports()[0]
    dst.input_ports()[0].connect_to(out)

    item = _pipe_item(graph, inp, dst)
    path = item.path()
    assert not path.isEmpty()

    rect = blocker.view.sceneBoundingRect()
    # a plain straight run would cut through the blocker (sanity of the setup)
    from PyQt5.QtGui import QPainterPath
    from PyQt5.QtCore import QPointF
    from NodeGraphQt.qgraphics.pipe import PipeItem
    straight = QPainterPath()
    straight.moveTo(QPointF(rect.left() - 100, rect.center().y()))
    straight.lineTo(QPointF(rect.right() + 100, rect.center().y()))
    assert PipeItem._path_hits_rect(straight, rect)

    # the routed wire must clear the blocker entirely
    assert not PipeItem._path_hits_rect(path, rect)
    assert path.boundingRect().top() < rect.top()

    # hit area is the stroke, not the closed fill bowl: a point between
    # the wire and the port chord used to grab the pipe from empty space.
    from PyQt5.QtCore import QPointF
    phantom = QPointF(200.0, 5.0)
    assert path.contains(phantom)          # old default shape would hit
    assert not item.shape().contains(phantom)
    assert item.shape().contains(path.pointAtPercent(0.5))


def _port_center(port):
    from PyQt5.QtCore import QPointF
    c = port.scenePos()
    br = port.boundingRect()
    return QPointF(c.x() + br.width() / 2.0, c.y() + br.height() / 2.0)


def test_backward_wire_clears_own_card(qapp):
    """A wire leaving a node's right port towards a node on its left must
    not run through the source card (endpoint exemption bubble aside)."""
    from NodeGraphQt import NodeGraph
    from NodeGraphQt.qgraphics.pipe import PipeItem
    from NGUI.nodes_resources.sequence_node import SequenceNode

    graph = NodeGraph()
    graph.register_node(SequenceNode)
    src = graph.create_node("sequence.SequenceNode", pos=[300, 0])
    dst = graph.create_node("sequence.SequenceNode", pos=[0, 0])

    dst.input_ports()[0].connect_to(src.output_ports()[0])

    item = _pipe_item(graph, src, dst)
    path = item.path()
    assert not path.isEmpty()

    out_pt = _port_center(item.output_port)
    in_pt = _port_center(item.input_port)
    # neither endpoint card may be crossed outside its own port bubble
    assert not PipeItem._path_hits_rect(path, src.view.sceneBoundingRect(),
                                        [out_pt])
    assert not PipeItem._path_hits_rect(path, dst.view.sceneBoundingRect(),
                                        [in_pt])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
