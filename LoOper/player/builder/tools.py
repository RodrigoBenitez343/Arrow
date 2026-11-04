"""tools.py — what the builder may READ, and the two things it may CHANGE.

Read: ``read_graph`` (the node index with each port's ROLE) and ``read_node``
(one node: config, ports with roles, wires).  Describing a chain is not a tool
any more: the panel's Describe button owns that.

Change is limited to what the user has put in front of the builder:
``edit_node`` writes settings on a SELECTED node, ``wire_nodes`` connects or
disconnects the wire between the TWO selected nodes.  Nothing else exists —
no creation, no deletion, no recording, no run.

Every tool **raises** (``errors.py``) when it cannot do its job; a refused
change is an error the model reports, never a silent no-op.

The chain's own flow metadata is READ here (``stored_flow``); writing it is
the panel's job, on the GUI thread, against the open chain file.
"""

from . import describe as _describe
from . import node_templates as _templates
from . import ports as _ports
from . import snapshot as _snapshot
from .errors import BuilderCanvasError, ToolArgumentError, UnknownToolError

#: The complete tool catalogue, as the assistant prompt lists it.
TOOL_SPECS = (
    {
        'name': 'read_graph',
        'arguments': '{}',
        'description': (
            'The canvas index: every node (id, type, name) and every port with '
            'its ROLE. Cheap; call it when you need the exact ids/labels or '
            'the port set of one node. The digest below already carries the '
            'walk, so you rarely need it.'),
    },
    {
        'name': 'read_node',
        'arguments': '{"node": "<id or name>"}',
        'description': (
            'One node in full: role, every descriptive property (clipped), '
            'its ports with roles and meanings, and every wire in and out.'),
    },
    {
        'name': 'edit_node',
        'arguments': '{"node": "<id>", "values": {"<property>": <new value>}}',
        'description': (
            "Change settings on ONE node the user has SELECTED: only fields the "
            "node type declares (the ones its dialog edits), everything else "
            "raises. Use it when the user asks for a change to the node they "
            "selected — never to write a description (the Describe button "
            "does that)."),
    },
    {
        'name': 'wire_nodes',
        'arguments': '{"from": "<id>", "to": "<id>", "action": '
                     '"connect|disconnect", "from_port": "<optional>", '
                     '"to_port": "<optional>"}',
        'description': (
            "Connect or disconnect the wire between the TWO nodes the user has "
            "SELECTED (exactly those two). Without ports it picks the only "
            "execution pair and refuses when that is ambiguous."),
    },
)

TOOL_NAMES = tuple(spec['name'] for spec in TOOL_SPECS)

#: Tools that CHANGE the canvas.  A turn that used one is written to the
#: chain file by the panel, and its answer must report what it changed.
WRITE_TOOLS = ('edit_node', 'wire_nodes')

#: Node index / node report size caps.
INDEX_LIMIT = 6000
NODE_LIMIT = 6000


# ------------------------------------------------------------- helpers

def _manager(ctx):
    manager = ctx.get('graph_manager')
    if callable(manager):
        manager = manager()
    return manager


def read_snapshot(ctx):
    """The live canvas as a ``Snapshot`` (GUI-marshalled, raising)."""
    run = ctx.get('run_on_gui')
    if not callable(run):
        raise BuilderCanvasError(
            'the builder has no GUI bridge, so the canvas cannot be read')
    return _snapshot.read(_manager(ctx), run)


def selected_ids(ctx):
    """Ids of the nodes the user has selected (never invented)."""
    getter = ctx.get('selection_node_ids')
    if not callable(getter):
        return []
    return [str(i) for i in (getter() or []) if str(i).strip()]


def selection_text(ctx, snapshot=None, limit=5000):
    """``selection_text`` — full reports for up to three selected nodes."""
    ids = selected_ids(ctx)
    if not ids:
        return ''
    snap = snapshot if snapshot is not None else read_snapshot(ctx)
    blocks = []
    for node_id in ids[:3]:
        blocks.append(_describe.node_detail(snap, node_id, limit=limit // 3))
    return '\n\n'.join(blocks)


# -------------------------------------------------------------- tools

def stored_flow(ctx):
    """The chain's own flow metadata ({} when the chain carries none)."""
    getter = ctx.get('stored_flow')
    if not callable(getter):
        return {}
    return getter() or {}


def read_graph(ctx, limit=INDEX_LIMIT):
    """The canvas index: nodes with their roles and port sets."""
    snap = read_snapshot(ctx)
    if not snap.nodes:
        return 'CANVAS: (empty — no nodes on the graph)'
    lines = ['CANVAS (%d node(s), %d exec edge(s), %d data edge(s))'
             % (len(snap.nodes),
                sum(1 for e in snap.edges if e.kind == 'exec'),
                sum(1 for e in snap.edges if e.kind == 'data'))]
    for node in sorted(snap.nodes.values(), key=lambda n: n.position):
        ins = ', '.join(_ports.port_role_line(p) for p in node.inputs) or 'none'
        outs = ', '.join(_ports.port_role_line(p) for p in node.outputs) or 'none'
        lines.append('%s  %-13s "%s"  [%s]'
                     % (node.id, node.type, node.name[:50], node.role or '?'))
        lines.append('    in:  %s' % ins)
        lines.append('    out: %s' % outs)
    text = '\n'.join(lines)
    if len(text) > limit:
        text = text[:limit] + '\n[canvas index clipped at %d chars]' % limit
    return text


def describe_chain(ctx, arguments):
    """The scoped digest.  NOT a chat tool any more (see TOOL_SPECS).

    Kept as the library entry point the digest tests and future callers use;
    describing a chain from the conversation is what was removed.
    """
    scope = str((arguments or {}).get('scope') or 'overview').strip().lower()
    ref = (arguments or {}).get('node') or (arguments or {}).get('id')
    if scope in ('node', 'subtree') and not ref:
        raise ToolArgumentError(
            'describe_chain scope=%r needs "node": an id or an exact name' % scope)
    snap = read_snapshot(ctx)
    return _describe.scope_digest(snap, scope=scope, ref=ref)


def read_node(ctx, arguments):
    ref = str((arguments or {}).get('node')
              or (arguments or {}).get('node_id')
              or (arguments or {}).get('id') or '').strip()
    if not ref:
        raise ToolArgumentError('read_node needs "node": an id or an exact name')
    snap = read_snapshot(ctx)
    return _describe.node_detail(snap, ref, limit=NODE_LIMIT)


# -------------------------------------------------------------- changes

def _gui(ctx, action):
    """Run *action* on the GUI thread; no bridge is a hard error."""
    run = ctx.get('run_on_gui')
    if not callable(run):
        raise BuilderCanvasError(
            'the builder has no GUI bridge, so the canvas cannot be changed')
    return run(action)


def _live_node(manager, node_id):
    """The live canvas node, or a hard error (never a silent skip)."""
    node = manager.get_node_by_id(node_id) if manager is not None else None
    if node is None:
        raise BuilderCanvasError('there is no node %s on this canvas' % node_id)
    return node


def _live_port(ports, name):
    for port in ports or ():
        if str(port.name()) == name:
            return port
    raise BuilderCanvasError('the canvas has no port %r' % name)


def _port_menu(node, direction):
    """``0x1 outputs: output (exec out), data (data out)`` — for a refusal."""
    views = node.outputs if direction == 'out' else node.inputs
    return '%s %s: %s' % (node.id, 'outputs' if direction == 'out' else 'inputs',
                          ', '.join(_ports.port_role_line(view) for view in views)
                          or 'none')


def edit_node(ctx, arguments):
    """Write settings on ONE selected node.  Every refusal raises."""
    args = arguments or {}
    ref = str(args.get('node') or args.get('node_id') or '').strip()
    values = args.get('values')
    if not ref:
        raise ToolArgumentError(
            'edit_node needs "node": the id of the selected node')
    if not isinstance(values, dict) or not values:
        raise ToolArgumentError(
            'edit_node needs "values": {"property": new value}')
    snap = read_snapshot(ctx)
    node = _describe.resolve(snap, ref)          # id or exact name; raises
    selected = selected_ids(ctx)
    if node.id not in selected:
        raise ToolArgumentError(
            'edit_node only changes a node the user SELECTED on the canvas; '
            '%s (%s) is not selected (selected: %s) — ask the user to select it'
            % (node.id, node.name or node.type,
               ', '.join(selected) or 'nothing'))
    allowed = _templates.fields_of(node.type)
    unknown = sorted(key for key in values if key not in allowed)
    if unknown:
        raise ToolArgumentError(
            'a %s node has no settable %s — its fields are: %s'
            % (node.type, ', '.join(unknown), ', '.join(allowed) or 'none'))
    manager = _manager(ctx)

    def _apply():
        live = _live_node(manager, node.id)
        lines = []
        for key, value in values.items():
            before = _templates.node_properties(live).get(key, '')
            live.set_property(key, value)
            lines.append('%s: %r -> %r' % (key, before, value))
        return lines

    changes = _gui(ctx, _apply)
    return ('edited %s (%s "%s")\n  %s'
            % (node.id, node.type, node.name, '\n  '.join(changes)))


def _pair_ports(src, dst, args):
    """``(out port, in port)`` for a connect, or a hard refusal."""
    from_name = str(args.get('from_port') or '').strip()
    to_name = str(args.get('to_port') or '').strip()
    if from_name or to_name:
        if not (from_name and to_name):
            raise ToolArgumentError(
                'wire_nodes needs BOTH from_port and to_port, or neither; %s; %s'
                % (_port_menu(src, 'out'), _port_menu(dst, 'in')))
        out = src.port('out', from_name)
        if out is None:
            raise ToolArgumentError('%s has no output %r; %s'
                                    % (src.id, from_name, _port_menu(src, 'out')))
        inp = dst.port('in', to_name)
        if inp is None:
            raise ToolArgumentError('%s has no input %r; %s'
                                    % (dst.id, to_name, _port_menu(dst, 'in')))
        return out, inp
    execs = _ports.exec_outputs(src, src.outputs)
    sink = _ports.exec_input(dst, dst.inputs)
    if len(execs) == 1 and sink is not None:
        return execs[0], sink
    raise ToolArgumentError(
        'wire_nodes cannot pick the ports by itself (the source has %d '
        'execution outputs, the target %s) — give from_port and to_port. %s; %s'
        % (len(execs), 'has an execution input' if sink is not None else 'has none',
           _port_menu(src, 'out'), _port_menu(dst, 'in')))


def wire_nodes(ctx, arguments):
    """Connect/disconnect the wire between the TWO selected nodes."""
    args = arguments or {}
    src_ref = str(args.get('from') or args.get('source') or '').strip()
    dst_ref = str(args.get('to') or args.get('target') or '').strip()
    action = str(args.get('action') or 'connect').strip().lower()
    if action not in ('connect', 'disconnect'):
        raise ToolArgumentError(
            'wire_nodes "action" must be "connect" or "disconnect", not %r'
            % action)
    if not src_ref or not dst_ref:
        raise ToolArgumentError('wire_nodes needs "from" and "to"')
    snap = read_snapshot(ctx)
    src = _describe.resolve(snap, src_ref)
    dst = _describe.resolve(snap, dst_ref)
    if src.id == dst.id:
        raise ToolArgumentError('wire_nodes needs two different nodes')
    selected = selected_ids(ctx)
    if len(selected) != 2 or set(selected) != {src.id, dst.id}:
        raise ToolArgumentError(
            'wire_nodes only changes the wire between the TWO nodes the user '
            'SELECTED; selected: %s, asked: %s -> %s — ask the user to select '
            'exactly those two'
            % (', '.join(selected) or 'nothing', src.id, dst.id))
    if action == 'connect':
        out, inp = _pair_ports(src, dst, args)
        kind = _ports.edge_kind(out.role, inp.role)
        if kind == 'illegal':
            raise ToolArgumentError(
                '%s.%s -> %s.%s: %s'
                % (src.id, out.name, dst.id, inp.name,
                   _ports.illegal_edge_reason(out.role, inp.role)))
        out_name, in_name = out.name, inp.name
    else:
        wires = [e for e in snap.edges
                 if e.src_id == src.id and e.dst_id == dst.id]
        wanted = (str(args.get('from_port') or '').strip(),
                  str(args.get('to_port') or '').strip())
        if any(wanted):
            wires = [e for e in wires
                     if (not wanted[0] or e.src_port == wanted[0])
                     and (not wanted[1] or e.dst_port == wanted[1])]
        if not wires:
            raise ToolArgumentError(
                'there is no wire %s -> %s to disconnect; %s; %s'
                % (src.id, dst.id, _port_menu(src, 'out'), _port_menu(dst, 'in')))
        if len(wires) > 1:
            raise ToolArgumentError(
                '%s -> %s carries %d wires (%s) — give from_port and to_port'
                % (src.id, dst.id, len(wires),
                   ', '.join('%s->%s' % (e.src_port, e.dst_port)
                             for e in wires)))
        out_name, in_name = wires[0].src_port, wires[0].dst_port
    manager = _manager(ctx)

    def _apply():
        out_port = _live_port(_live_node(manager, src.id).output_ports(), out_name)
        in_port = _live_port(_live_node(manager, dst.id).input_ports(), in_name)
        connected = in_port in list(out_port.connected_ports() or [])
        if action == 'connect':
            if connected:
                raise ToolArgumentError('the wire %s.%s -> %s.%s already exists'
                                        % (src.id, out_name, dst.id, in_name))
            out_port.connect_to(in_port)
            if in_port not in list(out_port.connected_ports() or []):
                raise BuilderCanvasError(
                    'the canvas refused the wire %s.%s -> %s.%s'
                    % (src.id, out_name, dst.id, in_name))
        else:
            if not connected:
                raise ToolArgumentError('the wire %s.%s -> %s.%s does not exist'
                                        % (src.id, out_name, dst.id, in_name))
            out_port.disconnect_from(in_port)
            if in_port in list(out_port.connected_ports() or []):
                raise BuilderCanvasError(
                    'the canvas kept the wire %s.%s -> %s.%s'
                    % (src.id, out_name, dst.id, in_name))

    _gui(ctx, _apply)
    return ('%s %s.%s -> %s.%s'
            % ('connected' if action == 'connect' else 'disconnected',
               src.id, out_name, dst.id, in_name))


def execute(name, arguments, ctx):
    """Run one tool call.  Raises on every failure; never returns a fake."""
    name = str(name or '').strip()
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise ToolArgumentError('tool %r arguments must be an object' % name)
    if name == 'read_graph':
        return read_graph(ctx)
    if name == 'read_node':
        return read_node(ctx, arguments)
    if name == 'edit_node':
        return edit_node(ctx, arguments)
    if name == 'wire_nodes':
        return wire_nodes(ctx, arguments)
    raise UnknownToolError(
        'unknown tool %r — available: %s' % (name, ', '.join(TOOL_NAMES)))
