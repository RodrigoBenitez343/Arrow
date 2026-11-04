"""snapshot.py — a typed, read-only picture of the live canvas.

The builder reads the graph through this module only.  It resolves every
port to a ROLE (``ports.py``) and every edge to an ``exec`` or ``data``
edge, so the rest of the package never has to guess what a wire means.

Reading happens on the GUI thread (``ctx['run_on_gui']``); the returned
dataclasses are plain Python and safe to use on the worker thread.
"""

from dataclasses import dataclass, field

from . import ports as _ports
from .errors import BuilderCanvasError, UnknownPortError
from .node_templates import (meaningful, node_id, node_name, node_properties,
                             node_type_of, short_type)


@dataclass
class PortView:
    """One port on a snapshot node: name, direction, role, meaning."""

    name: str
    direction: str
    role: str
    meaning: str
    peers: list = field(default_factory=list)   # [(node_id, port_name)]

    @property
    def is_exec(self):
        return self.role in _ports.EXEC_ROLES or self.role in _ports.EXEC_SINK_ROLES


@dataclass
class NodeView:
    """One canvas node: identity, descriptive properties, ports."""

    id: str
    type: str
    name: str
    role: str
    position: tuple
    properties: dict
    inputs: list
    outputs: list

    @property
    def is_passive(self):
        return not any(p.is_exec for p in self.inputs + self.outputs)

    def port(self, direction, name):
        for view in (self.inputs if direction == 'in' else self.outputs):
            if view.name == name:
                return view
        return None

    def port_names(self, direction):
        return [p.name for p in (self.inputs if direction == 'in'
                                 else self.outputs)]


@dataclass
class Edge:
    """One wire: source port -> destination port, with roles and kind."""

    src_id: str
    src_port: str
    src_role: str
    dst_id: str
    dst_port: str
    dst_role: str
    kind: str            # 'exec' | 'data'


@dataclass
class Snapshot:
    """The whole canvas, resolved."""

    nodes: dict                     # id -> NodeView
    edges: list                     # [Edge]
    order: list                     # ids in canvas creation order

    # -- views ---------------------------------------------------------

    def node(self, node_id_):
        return self.nodes.get(str(node_id_))

    def exec_out_edges(self, node_id_):
        return [e for e in self.edges
                if e.kind == 'exec' and e.src_id == str(node_id_)]

    def exec_in_edges(self, node_id_):
        return [e for e in self.edges
                if e.kind == 'exec' and e.dst_id == str(node_id_)]

    def data_out_edges(self, node_id_):
        return [e for e in self.edges
                if e.kind == 'data' and e.src_id == str(node_id_)]

    def data_in_edges(self, node_id_):
        return [e for e in self.edges
                if e.kind == 'data' and e.dst_id == str(node_id_)]

    def exec_nodes(self):
        """Nodes that take part in execution (everything except pools)."""
        return [n for n in self.nodes.values() if not n.is_passive]

    def roots(self):
        """Exec nodes with no incoming execution edge."""
        out = []
        for node in self.exec_nodes():
            if not self.exec_in_edges(node.id):
                out.append(node)
        return out

    def type_counts(self):
        counts = {}
        for node in self.nodes.values():
            counts[node.type] = counts.get(node.type, 0) + 1
        return dict(sorted(counts.items()))


# ------------------------------------------------------------- reading

def _peer_node(port):
    peer = getattr(port, 'node', None)
    return peer() if callable(peer) else peer


def _peers(live_port, nid):
    """``[(node_id, port_name)]`` of one live port's connections."""
    try:
        connected = list(live_port.connected_ports() or [])
    except Exception as exc:
        raise BuilderCanvasError(
            'could not read the connections of %s.%s: %s'
            % (nid, live_port.name(), exc)) from exc
    peers = []
    for peer in connected:
        peers.append((node_id(_peer_node(peer)), str(peer.name())))
    return peers


def _read_ports(node, direction, getter, nid):
    """One direction's live ports -> snapshot ``PortView`` list."""
    views = []
    for live_port in (getter() or []):
        name = str(live_port.name())
        spec = _ports.spec_for(node, direction, name)
        views.append(PortView(name=name, direction=direction,
                              role=spec.role, meaning=spec.meaning,
                              peers=_peers(live_port, nid)))
    return views


def _read_node(node):
    """One live node -> ``NodeView`` (raises on unclassifiable ports)."""
    ntype = short_type(node_type_of(node))
    nid = node_id(node)
    try:
        inputs = _read_ports(node, 'in', node.input_ports, nid)
        outputs = _read_ports(node, 'out', node.output_ports, nid)
    except UnknownPortError:
        raise
    except BuilderCanvasError:
        raise
    except Exception as exc:
        raise BuilderCanvasError(
            'could not read the ports of node %s: %s' % (nid, exc)) from exc
    return NodeView(
        id=nid,
        type=ntype,
        name=node_name(node),
        role=_role_word(node, ntype),
        position=_position(node),
        properties=meaningful(node_properties(node)),
        inputs=inputs,
        outputs=outputs,
    )

def _role_word(node, ntype):
    """The role word, refined by the node's own mode flags."""
    from .node_templates import role_of
    role = role_of(ntype)
    props = dict(node.properties() or {})
    if ntype == 'llm':
        if str(props.get('orchestrator_mode')).strip().lower() in (
                'true', '1', 'yes', 'on'):
            return 'BRAIN'
    if ntype == 'conditional':
        loop_type = str(props.get('loop_type') or '').strip()
        seq_file = str(props.get('sequence_file') or '').strip()
        if loop_type and seq_file:
            return 'LOOP'
    return role


def _position(node):
    try:
        pos = node.pos() or (0.0, 0.0)
        return (float(pos[0]), float(pos[1]))
    except Exception as exc:
        raise BuilderCanvasError(
            'could not read the position of node %s: %s'
            % (node_id(node), exc)) from exc


def build(graph_manager):
    """Read the whole canvas into a ``Snapshot``.

    ``graph_manager`` is the live ``GraphManager`` and MUST be called on the
    GUI thread.  Every failure raises ``BuilderCanvasError`` or
    ``UnknownPortError`` — nothing is skipped.
    """
    if graph_manager is None:
        raise BuilderCanvasError('no graph manager is attached to the canvas')
    try:
        live_nodes = list(graph_manager.get_all_nodes() or [])
    except Exception as exc:
        raise BuilderCanvasError('could not list the canvas nodes: %s' % exc) from exc

    nodes = {}
    order = []
    for live in live_nodes:
        view = _read_node(live)
        nodes[view.id] = view
        order.append(view.id)

    edges = []
    seen = set()
    for node in nodes.values():
        for out_port in node.outputs:
            for dst_id, dst_name in out_port.peers:
                dst = nodes.get(dst_id)
                if dst is None:
                    raise BuilderCanvasError(
                        'port %s.%s is wired to unknown node %s'
                        % (node.id, out_port.name, dst_id))
                dst_port = dst.port('in', dst_name)
                if dst_port is None:
                    raise BuilderCanvasError(
                        'port %s.%s claims a wire to %s.%s, which does not exist'
                        % (node.id, out_port.name, dst_id, dst_name))
                key = (node.id, out_port.name, dst_id, dst_name)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(Edge(
                    src_id=node.id, src_port=out_port.name,
                    src_role=out_port.role,
                    dst_id=dst_id, dst_port=dst_name,
                    dst_role=dst_port.role,
                    kind=_ports.edge_kind(out_port.role, dst_port.role),
                ))

    return Snapshot(nodes=nodes, edges=edges, order=order)


def read(graph_manager, run_on_gui):
    """``build`` on the GUI thread (``run_on_gui`` is ``_GuiProxy.call``)."""
    return run_on_gui(lambda: build(graph_manager))
