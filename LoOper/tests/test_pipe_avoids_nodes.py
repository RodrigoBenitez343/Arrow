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


def _arrow_heading(item):
    """Scene-space heading of the pipe's direction pointer."""
    from PyQt5.QtCore import QPointF
    d = item._dir_pointer
    base = d.mapToScene(QPointF(0.0, 0.0))
    tip = d.mapToScene(QPointF(0.0, -9.0))
    return (tip.x() - base.x(), tip.y() - base.y())


def _angle(u, v):
    import math
    lu = math.hypot(*u) or 1e-9
    lv = math.hypot(*v) or 1e-9
    c = (u[0] * v[0] + u[1] * v[1]) / (lu * lv)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


@pytest.mark.parametrize("backwards", [False, True])
@pytest.mark.parametrize("redraw", [None, "in", "out"])
def test_direction_arrow_follows_flow(qapp, backwards, redraw):
    """The wire arrow points output -> input, including on loopback wires
    and no matter which port triggered the redraw."""
    from NodeGraphQt import NodeGraph
    from NGUI.nodes_resources.sequence_node import SequenceNode

    graph = NodeGraph()
    graph.register_node(SequenceNode)
    src = graph.create_node("sequence.SequenceNode",
                            pos=[600, 200] if backwards else [0, 0])
    dst = graph.create_node("sequence.SequenceNode",
                            pos=[0, 0] if backwards else [600, 200])
    dst.input_ports()[0].connect_to(src.output_ports()[0])

    item = _pipe_item(graph, src, dst)
    if redraw == "in":
        item.draw_path(item.input_port, item.output_port)
    elif redraw == "out":
        item.draw_path(item.output_port, item.input_port)

    out_pt = _port_center(item.output_port)
    in_pt = _port_center(item.input_port)
    flow = (in_pt.x() - out_pt.x(), in_pt.y() - out_pt.y())
    assert _angle(_arrow_heading(item), flow) < 20.0


def _render(scene, rect):
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QImage, QPainter
    img = QImage(int(rect.width()), int(rect.height()), QImage.Format_ARGB32)
    img.fill(0xFF101418)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(-rect.left(), -rect.top())
    scene.render(p, QRectF(rect), rect)
    p.end()
    return img


def _pulse_centroid(img, base):
    xs = ys = n = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            if img.pixel(x, y) != base.pixel(x, y):
                xs += x
                ys += y
                n += 1
    assert n, "no flow pulse painted"
    return xs / n, ys / n


def test_flow_pulse_travels_output_to_input(qapp):
    """The animated pulse is painted and walks from the source node to the
    target node on a loopback wire (right to left here)."""
    from NodeGraphQt import NodeGraph
    from NodeGraphQt.qgraphics import pipe as pipe_mod
    from NGUI.nodes_resources.sequence_node import SequenceNode

    graph = NodeGraph()
    graph.register_node(SequenceNode)
    src = graph.create_node("sequence.SequenceNode", pos=[600, 200])
    dst = graph.create_node("sequence.SequenceNode", pos=[0, 0])
    dst.input_ports()[0].connect_to(src.output_ports()[0])

    item = _pipe_item(graph, src, dst)
    scene = graph.scene()
    rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)

    enabled, clock = pipe_mod.FLOW_ANIM_ENABLED, pipe_mod._flow_time
    count_spacing = pipe_mod.FLOW_SPACING
    length = item.path().length()
    try:
        # one dot per wire so the centroid tracks a single travelling dot
        pipe_mod.FLOW_SPACING = length * 2.0
        # same paint path for the base and the pulsed frames
        item.setCacheMode(item.NoCache)
        pipe_mod.FLOW_ANIM_ENABLED = False
        base = _render(scene, rect)
        pipe_mod.FLOW_ANIM_ENABLED = enabled

        xs = []
        for frac in (0.15, 0.45, 0.75):
            pipe_mod._flow_time = lambda f=frac: f * length / pipe_mod.FLOW_SPEED
            xs.append(_pulse_centroid(_render(scene, rect), base)[0])
        assert xs[0] > xs[1] > xs[2], xs
    finally:
        pipe_mod._flow_time = clock
        pipe_mod.FLOW_ANIM_ENABLED = enabled
        pipe_mod.FLOW_SPACING = count_spacing


def test_flow_dots_never_stop(qapp):
    """Dots wrap around, so a wire shows the stream at every moment -
    nothing starts or stops between laps."""
    from NodeGraphQt import NodeGraph
    from NodeGraphQt.qgraphics import pipe as pipe_mod
    from NGUI.nodes_resources.sequence_node import SequenceNode

    graph = NodeGraph()
    graph.register_node(SequenceNode)
    src = graph.create_node("sequence.SequenceNode", pos=[0, 0])
    dst = graph.create_node("sequence.SequenceNode", pos=[600, 200])
    dst.input_ports()[0].connect_to(src.output_ports()[0])

    item = _pipe_item(graph, src, dst)
    scene = graph.scene()
    rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)

    enabled, clock = pipe_mod.FLOW_ANIM_ENABLED, pipe_mod._flow_time
    try:
        item.setCacheMode(item.NoCache)
        pipe_mod.FLOW_ANIM_ENABLED = False
        base = _render(scene, rect)
        pipe_mod.FLOW_ANIM_ENABLED = enabled

        gap = pipe_mod.FLOW_SPACING / pipe_mod.FLOW_SPEED
        for i in range(10):
            pipe_mod._flow_time = lambda f=i: f * gap / 10.0
            _pulse_centroid(_render(scene, rect), base)   # asserts pixels
    finally:
        pipe_mod._flow_time = clock
        pipe_mod.FLOW_ANIM_ENABLED = enabled


def test_flow_dot_count_follows_wire_length(qapp):
    """A wire twice as long carries about twice as many dots."""
    from NodeGraphQt import NodeGraph
    from NodeGraphQt.qgraphics import pipe as pipe_mod
    from NGUI.nodes_resources.sequence_node import SequenceNode

    def dots_across(spacing_x):
        graph = NodeGraph()
        graph.register_node(SequenceNode)
        src = graph.create_node("sequence.SequenceNode", pos=[0, 0])
        dst = graph.create_node("sequence.SequenceNode", pos=[spacing_x, 0])
        dst.input_ports()[0].connect_to(src.output_ports()[0])
        length = _pipe_item(graph, src, dst).path().length()
        return max(1, int(round(length / pipe_mod.FLOW_SPACING)))

    assert dots_across(1200) >= 2 * dots_across(600) - 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
