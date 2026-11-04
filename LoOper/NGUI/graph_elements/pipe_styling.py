"""Unique pipe colors per connection type, for easy visual debugging.

Every wire on the canvas is classified from its port names (view layer, first
match wins) and drawn with its own color/pen:

  1. tools / brains / chains input ......... orange   (orchestrator wiring)
  2. conditional 'true' output ............. green dashed
     conditional 'false' output ............ red dashed
     code 'error' output ................... red solid
  3. 'data' output (Input node passthrough
     prompt/information) .................. green solid
  4. ctx_in / ctx_out / context / prompt ... blue solid
  5. plain execution 'output' .............. green solid, RED when the target
     node sits earlier in the walked execution order (loopback)

Colors are (r, g, b, a) tuples (PipeEnum.COLOR shape); styles are PipeEnum
DRAW_TYPE values (0 solid, 1 dashed, 2 dotted).  Setting ``pipe._color`` /
``pipe._style`` makes hover/selection highlights revert to the type color.
"""

import logging

from NodeGraphQt.constants import PipeEnum
from NodeGraphQt.qgraphics.pipe import PipeItem

logger = logging.getLogger(__name__)

GREEN = (76, 187, 76, 255)     # execution / true branch / passthrough data
RED = (239, 68, 68, 255)       # loopback / false branch / error
BLUE = (74, 144, 226, 255)     # context / prompt
ORANGE = (255, 152, 0, 255)    # tools / brains / chains (orchestrator)

_SOLID = PipeEnum.DRAW_TYPE_DEFAULT.value
_DASHED = PipeEnum.DRAW_TYPE_DASHED.value

_CONTEXT_PORTS = {"ctx_in", "ctx_out", "context", "prompt"}
_ORCH_PORTS = {"tools", "brains", "chains"}
_EXEC_OUTPUTS = {"output", "true", "false"}


def _pname(port):
    """Port name on the view item; tolerates the wrapper's method API too."""
    try:
        name = port.name
        return str(name() if callable(name) else name)
    except Exception:
        return ""


def connected_pipes(graph):
    """Every finished pipe in the graph's scene (excludes live/slicer pipes)."""
    try:
        scene = graph.viewer().scene()
    except Exception:
        return []
    return [i for i in scene.items()
            if isinstance(i, PipeItem) and i.input_port and i.output_port]


def exec_order(pipes):
    """Walk execution wires from the chain root(s); view node -> visit index."""
    adjacency, targets = {}, set()
    for pipe in pipes:
        if _pname(pipe.output_port) not in _EXEC_OUTPUTS:
            continue
        src, dst = pipe.output_port.node, pipe.input_port.node
        adjacency.setdefault(src, []).append(dst)
        targets.add(dst)

    order = {}
    queue = [src for src in adjacency if src not in targets]  # roots = never a target
    while queue:
        node = queue.pop(0)
        if id(node) in order:
            continue
        order[id(node)] = len(order)
        queue.extend(adjacency.get(node, []))
    return order


def classify_pipe(pipe, order=None):
    """Return ((r, g, b, a), style) for a connected pipe."""
    out_name = _pname(pipe.output_port)
    in_name = _pname(pipe.input_port)

    if in_name in _ORCH_PORTS:
        return ORANGE, _SOLID
    if out_name == "true":
        return GREEN, _DASHED
    if out_name == "false":
        return RED, _DASHED
    if out_name == "error":
        return RED, _SOLID
    if out_name == "data":
        return GREEN, _SOLID
    if out_name in _CONTEXT_PORTS or in_name in _CONTEXT_PORTS:
        return BLUE, _SOLID

    # execution wire — green, unless it loops back into an earlier node
    src, dst = pipe.output_port.node, pipe.input_port.node
    if order and id(src) in order and id(dst) in order and order[id(dst)] <= order[id(src)]:
        return RED, _SOLID
    return GREEN, _SOLID


def restyle_pipes(graph):
    """Color every pipe by connection type. Never raises (cosmetic pass)."""
    try:
        pipes = connected_pipes(graph)
        if not pipes:
            return
        order = exec_order(pipes)
        for pipe in pipes:
            color, style = classify_pipe(pipe, order)
            pipe._color = color
            pipe._style = style
            pipe.set_pipe_styling(color=color, width=2, style=style)
    except Exception as e:
        logger.debug(f"Pipe restyle skipped: {e}")
