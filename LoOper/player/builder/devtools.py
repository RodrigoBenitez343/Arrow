"""devtools.py — needle2 grammar-constrained tools for the chain builder.

Build side (``BUILDER_TOOL_SCHEMAS`` / ``execute_builder_call`` /
``BuilderRouter``): the builder LLM's prose is constrained to canvas tool
calls which Python executes against the LIVE graph — the only writer of
chain JSON.

Runtime side (``RUNTIME_TOOL_SCHEMAS`` / ``execute_runtime_call`` /
``RuntimeRouter``): chat-mode execution of a built system chain binds
arguments into advertised agent_modifiable Input slots, reads Output nodes
and appends Context memory — one deterministic step instead of per-field
LLM reasoning.

Both executors NEVER raise: every failure becomes a deterministic error
string (the ``execute_call`` contract in code_agent_ops/devtools.py).
"""

import copy
import json
import logging
import os
import re
import tempfile

from player.code_agent_ops.devtools import NeedleRouter, find_needle_exe
from player.builder.node_templates import (
    NODE_TYPES,
    DECLINED_TYPES,
    KIND_BOOL,
    KIND_ENUM,
    allowed_fields,
    field_menu,
    field_spec,
    normalize_value,
    option_menu,
    registry_key,
    short_type,
    slugify,
    node_type_of,
)

logger = logging.getLogger(__name__)

__all__ = [
    'BUILDER_TOOL_SCHEMAS',
    'RUNTIME_TOOL_SCHEMAS',
    'execute_builder_call',
    'execute_runtime_call',
    'BuilderRouter',
    'RuntimeRouter',
    'parse_legacy_directives',
    'agent_modifiable_labels',
    'resolve_chain_import_file',
    'extract_arguments',
    'describe_node',
]

SYSTEM_COLLECTION = "System"


# ---------------------------------------------------------------------------
# Build-side schemas (byte-level grammar compiled by needle.exe)
# ---------------------------------------------------------------------------

BUILDER_TOOL_SCHEMAS = [
    {
        'name': 'read_graph',
        'description': (
            'Return the current canvas: every node (id, type, name) and its '
            'connections. Call this FIRST each turn so you always build on '
            'the actual canvas state, never on your memory of it.'
        ),
        'parameters': {'type': 'object', 'properties': {}, 'required': []},
    },
    {
        'name': 'create_node',
        'description': (
            'Create one node of the given type in the canvas and return its '
            'node id. Available types: ' + ', '.join(NODE_TYPES) + '. '
            'The types mcp and container must NOT be created: ask the user '
            'to add them manually.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'node_type': {
                    'type': 'string',
                    'minLength': 1,
                    'description': 'one of: ' + ', '.join(NODE_TYPES),
                },
                'label': {
                    'type': 'string',
                    'minLength': 1,
                    'description': 'short display name, e.g. "click apply"',
                },
                'chain_file_path': {
                    'type': 'string',
                    'minLength': 1,
                    'description': (
                        'chain_import only: the chain file to import. The '
                        'sub-chain must exist first — a chain_import without '
                        'a file cannot be created.'
                    ),
                },
            },
            'required': ['node_type', 'label'],
        },
    },
    {
        'name': 'wire',
        'description': (
            'Connect one node to another. Name the nodes by their LABEL as ' \
            'read_graph shows it (an id also works). input_port is optional: ' \
            'a chain_import wired to an orchestrator LLM lands on "tools" ' \
            'automatically ("brains" when the chain file contains an ' \
            'orchestrator); every other target defaults to "input".'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'from_node': {
                    'type': 'string',
                    'minLength': 1,
                    'description': 'source node label (or id)',
                },
                'to_node': {
                    'type': 'string',
                    'minLength': 1,
                    'description': 'target node label (or id)',
                },
                'input_port': {
                    'type': 'string',
                    'description': 'optional; e.g. input, prompt, tools, brains',
                },
                'output_port': {
                    'type': 'string',
                    'description': 'optional; defaults to output (use "data" ' \
                                   'for an input node)',
                },
            },
            'required': ['from_node', 'to_node'],
        },
    },
    {
        'name': 'edit_node',
        'description': (
            'Set ONE property of an existing node. The property must be '
            'editable for that node type; read_graph reports the types, '
            'and an edit attempt on a non-editable property returns the '
            'allowed list.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'node': {
                    'type': 'string', 'minLength': 1,
                    'description': 'node label (or id)',
                },
                'node_id': {'type': 'string'},
                'property': {'type': 'string', 'minLength': 1},
                'value': {'type': 'string', 'minLength': 0},
            },
            'required': ['node', 'property', 'value'],
        },
    },
    {
        'name': 'configure_node',
        'description': (
            'Set a node field from a NATURAL-LANGUAGE description of what '
            'the node must do (e.g. "read the page text", "run on the local '
            'model", "show the answer as an image"). Laya maps the ask onto '
            'the node\'s exact field and, for a fixed set, its exact option — '
            'use it when you know the node\'s job but not the property/value. '
            'For free text / numbers / model or file paths it returns the '
            'field to set with edit_node.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'node': {
                    'type': 'string', 'minLength': 1,
                    'description': 'node label (or id)',
                },
                'ask': {
                    'type': 'string', 'minLength': 1,
                    'description': 'what this node must do, in plain words',
                },
            },
            'required': ['node', 'ask'],
        },
    },
    {
        'name': 'set_description',
        'description': (
            'Set the chain-level routing description (what this whole '
            'system chain does, one or two sentences).'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'text': {'type': 'string', 'minLength': 1},
            },
            'required': ['text'],
        },
    },
    {
        'name': 'ask_user',
        'description': (
            'Pause the build and ask the user. kind=answer: a free-text '
            'question. kind=confirm: yes/no. kind=record_sequence: ask the '
            'user to demonstrate a desktop step (a sequence node must '
            'already exist for the recording). kind=record_web_sequence: '
            'the same for a web step.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'question': {'type': 'string', 'minLength': 1},
                'kind': {
                    'type': 'string',
                    'minLength': 1,
                    'description': 'answer | confirm | record_sequence | '
                                   'record_web_sequence',
                },
            },
            'required': ['question', 'kind'],
        },
    },
    {
        'name': 'save_chain',
        'description': (
            'Save the canvas as a system chain (collection=System) under '
            'the given name. Call this when the user confirms the structure.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'minLength': 1},
            },
            'required': ['name'],
        },
    },
]

# Action name -> its schema, for single-tool extraction (needle's
# documented extraction mode: with ONE declared tool the grammar admits
# exactly that call, so the returned arguments ARE the extracted fields).
_SCHEMA_BY_NAME = {t['name']: t for t in BUILDER_TOOL_SCHEMAS}


# ---------------------------------------------------------------------------
# Runtime-side schemas (chat-mode execution of a built system chain)
# ---------------------------------------------------------------------------

RUNTIME_TOOL_SCHEMAS = [
    {
        'name': 'bind_input',
        'description': (
            'Write a value into one advertised agent-modifiable Input slot '
            'of a chain tool before running it (e.g. the scraping chain '
            'fields site/query/pages). alias is the tool alias the fields '
            'list was advertised for.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'alias': {'type': 'string', 'minLength': 1},
                'label': {'type': 'string', 'minLength': 1},
                'value': {'type': 'string', 'minLength': 1},
            },
            'required': ['alias', 'label', 'value'],
        },
    },
    {
        'name': 'read_output',
        'description': (
            'Read the value an Output node produced (by its label or '
            'variable_name) after a chain ran.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'alias': {'type': 'string', 'minLength': 1},
                'label': {'type': 'string', 'minLength': 1},
            },
            'required': ['alias', 'label'],
        },
    },
    {
        'name': 'write_context',
        'description': (
            'Append a note into the chain context memory so the next turn '
            'carries it.'
        ),
        'parameters': {
            'type': 'object',
            'properties': {
                'text': {'type': 'string', 'minLength': 1},
            },
            'required': ['text'],
        },
    },
]


# ---------------------------------------------------------------------------
# Canvas access helpers (thin, GUI-agnostic)
# ---------------------------------------------------------------------------

def _graph_manager(ctx):
    mgr = ctx.get('graph_manager')
    if mgr is None:
        return None
    return mgr() if callable(mgr) else mgr


def _resolve_ctx_value(ctx, key):
    """A ctx entry, resolved through a callable when given one."""
    value = ctx.get(key)
    if callable(value):
        try:
            return value()
        except Exception:
            return None
    return value


def _input_port_names(node):
    """Names of a canvas node's input ports (never raises)."""
    try:
        return [str(p.name()) for p in node.input_ports()]
    except Exception:
        return []


def _output_port_names(node):
    """Names of a canvas node's output ports (never raises)."""
    try:
        return [str(p.name()) for p in node.output_ports()]
    except Exception:
        return []


def _resolve_port(node, name, ports_fn):
    try:
        for p in ports_fn(node):
            if str(p.name()) == name:
                return p
    except Exception:
        return None
    return None


def _node_id(node):
    """A node's canvas id, or '' (never raises)."""
    try:
        return str(getattr(node, 'id', '') or '')
    except Exception:
        return ''


def _node_name(node):
    """A node's display name, or '' (never raises)."""
    try:
        return str(node.name() or '')
    except Exception:
        return ''


def _resolve_node_ref(mgr, ref):
    """A canvas node from an id OR a label (exact, then case-insensitive).

    Labels are what read_graph shows and what a model can actually see;
    ids are what the canvas returns from create_node.  Both resolve here,
    so ``wire``/``edit_node`` never depend on id memory.  An ambiguous
    label (several nodes share it) resolves to nothing on purpose.
    """
    ref = str(ref or '').strip()
    if not ref:
        return None
    try:
        node = mgr.get_node_by_id(ref)
    except Exception:
        node = None
    if node is not None:
        return node
    try:
        nodes = list(mgr.get_all_nodes() or [])
    except Exception:
        return None
    exact = [n for n in nodes if _node_name(n) == ref]
    if len(exact) == 1:
        return exact[0]
    low = ref.lower()
    loose = [n for n in nodes if _node_name(n).lower() == low]
    return loose[0] if len(loose) == 1 else None


def _auto_input_port(src, dst, dst_type):
    """The port a wire lands on when the caller named none.

    chain_import -> orchestrator LLM is classified from the chain FILE with
    the shared rule (``classify_chain``): a deterministic chain goes to
    "tools", a chain that contains an orchestrator to "brains".
    Everything else lands on "input".
    """
    if dst_type == 'llm' and short_type(node_type_of(src)) == 'chain_import':
        chain_file = ''
        for getter in ('get_property', 'get_chain_import_file'):
            try:
                fn = getattr(src, getter, None)
                if callable(fn):
                    chain_file = str(fn('chain_file_path') if getter ==
                                     'get_property' else fn() or '').strip()
                    if chain_file:
                        break
            except Exception:
                continue
        if not chain_file:
            try:
                chain_file = str(getattr(src, 'chain_file', '') or '').strip()
            except Exception:
                chain_file = ''
        if chain_file and os.path.isfile(chain_file):
            try:
                from player.chain_import_ports import classify_chain
                with open(chain_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                kind = classify_chain(cfg, os.path.dirname(chain_file))
                return 'brains' if kind == 'brain' else 'tools'
            except Exception:
                pass
        return 'tools'
    return 'input'


def _declared_code_ports(node, prop):
    """The declared input/output var names of a code node (never raises)."""
    try:
        raw = node.get_property(prop) or '[]'
        data = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []
    names = []
    for item in (data or []):
        name = ''
        if isinstance(item, dict):
            name = str(item.get('name') or '').strip()
        if name:
            names.append(name)
    return names


def _code_port_names(value, default):
    """Sanitised port-name list from a step field; [default] when empty."""
    if isinstance(value, str):
        value = [value]
    names = []
    for n in (value or []):
        n = str(n or '').strip()
        if n.isidentifier() and n not in names:
            names.append(n)
    return names or [default]


def describe_node(node):
    """A readable detail block for ONE canvas node (never raises).

    The building assistant's context: the node's editable property values
    (exactly the fields ``edit_node`` accepts) and its wired ports — so
    questions ("what does this do?", "how is it wired?") and prompt/model
    edits are grounded in the LIVE canvas.
    """
    ntype = short_type(node_type_of(node))
    lines = ['%s %s "%s"' % (_node_id(node) or '?', ntype or '?',
                             _node_name(node))]
    values = []
    for field in allowed_fields(ntype):
        try:
            value = node.get_property(field)
        except Exception:
            continue
        if value is None or value == '' or value == [] or value == {}:
            continue
        text = str(value)
        if len(text) > 200:
            text = text[:200] + '...'
        values.append('    %s: %s' % (field, text))
    if values:
        lines.append('  editable values:')
        lines.extend(values)

    def _peers(port):
        names = []
        try:
            for other in port.connected_ports():
                peer = getattr(other, 'node', None)
                peer = peer() if callable(peer) else peer
                try:
                    other_name = str(other.name())
                except Exception:
                    other_name = '?'
                names.append('%s.%s' % (_node_id(peer) or '?', other_name))
        except Exception:
            pass
        return names

    ins = []
    for p in (node.input_ports() or []):
        try:
            name = str(p.name())
        except Exception:
            continue
        peers = _peers(p)
        ins.append(name + (' <- ' + ', '.join(peers) if peers else ''))
    outs = []
    for p in (node.output_ports() or []):
        try:
            name = str(p.name())
        except Exception:
            continue
        peers = _peers(p)
        outs.append(name + (' -> ' + ', '.join(peers) if peers else ''))
    if ins:
        lines.append('  inputs:  ' + '; '.join(ins))
    if outs:
        lines.append('  outputs: ' + '; '.join(outs))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Build-side execution
# ---------------------------------------------------------------------------

def execute_builder_call(name, arguments, ctx):
    """Execute ONE builder tool call against the live canvas.

    ctx contract:
      ctx['graph_manager']   GraphManager instance (or callable returning it)
      ctx['config_manager']  ConfigManager instance (or None)
      ctx['state']           mutable dict shared across the turn
      ctx['ask']             callable(question, kind) -> str
      ctx['chains_dir']      chains folder path (str)
      ctx['position_hint']   optional callable() -> [x, y]
      ctx['run_on_gui']      optional callable(fn) that runs fn on the GUI
                             thread.  The panel supplies it so EVERY canvas
                             op — node and port objects included — is
                             thread-safe.  ``ask_user`` is excluded on
                             purpose: it BLOCKS on the user's answer, and
                             running it on the GUI thread would freeze the
                             window it waits in.

    Never raises: every failure becomes a deterministic error string.
    """
    run = ctx.get('run_on_gui')
    # ask_user blocks on the user; test_chain runs the WHOLE player (long,
    # blocking).  Neither may take the GUI thread — test_chain marshals only
    # its own canvas read inside.
    if callable(run) and name not in ('ask_user', 'test_chain'):
        try:
            return run(lambda: _dispatch_builder_call(name, arguments, ctx))
        except Exception:
            logger.debug('run_on_gui failed; running inline', exc_info=True)
    return _dispatch_builder_call(name, arguments, ctx)


def _dispatch_builder_call(name, arguments, ctx):
    try:
        args = arguments or {}
        if name == 'read_graph':
            return _read_graph(ctx)
        if name == 'create_node':
            return _create_node(ctx, args)
        if name == 'wire':
            return _wire(ctx, args)
        if name == 'edit_node':
            return _edit_node(ctx, args)
        if name == 'configure_node':
            return _configure_node(ctx, args)
        if name == 'set_description':
            return _set_description(ctx, args)
        if name == 'ask_user':
            return _ask_user(ctx, args)
        if name == 'save_chain':
            return _save_chain(ctx, args)
        if name == 'delete_node':
            return _delete_node(ctx, args)
        if name == 'read_node':
            return _read_node(ctx, args)
        if name == 'build_skill':
            return _build_skill(ctx, args)
        if name == 'test_chain':
            return _test_chain(ctx, args)
        return '(unknown builder tool: %s)' % name
    except Exception as e:
        logger.exception('builder tool %s failed', name)
        return '(builder tool %s failed: %s)' % (name, e)


def extract_arguments(action, content, exe_path=None, trace=None):
    """Fill one builder action's arguments from prose (needle extraction).

    Declares ONLY ``action``'s schema, so the grammar admits exactly that
    call and its ``arguments`` are the extracted fields — the per-node
    property filling needle is built for.  Every outcome is traced, so the
    panel and the log both show what needle stored.  Returns the arguments
    dict, or None when the engine is missing / produced no call.
    """
    from player.builder.trace import emit as _emit
    schema = _SCHEMA_BY_NAME.get(str(action or ''))
    if schema is None:
        return None
    exe = exe_path or find_needle_exe()
    if not exe:
        _emit(trace, 'needle missing — cannot fill %s arguments' % action)
        return None
    fd, tools_path = tempfile.mkstemp(prefix='needle_extract_', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump([schema], f)
        obj, err = NeedleRouter({}, exe_path=exe)._prompt_once(
            tools_path, str(content or ''))
    except Exception:
        logger.exception('extract_arguments %s failed', action)
        return None
    finally:
        try:
            os.remove(tools_path)
        except Exception:
            pass
    if err or not isinstance(obj, dict):
        _emit(trace, 'needle(%s): %s' % (action, err or 'no response'))
        return None
    calls = obj.get('function_calls') or []
    args = None
    if calls:
        candidate = calls[0].get('arguments')
        args = candidate if isinstance(candidate, dict) else None
    _emit(trace, 'needle(%s): confidence=%s reasoning=%s → %s' % (
        action, obj.get('confidence'),
        str(obj.get('reasoning') or '')[:200], args))
    return args


def _read_graph(ctx):
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    try:
        nodes = mgr.get_all_nodes() or []
    except Exception as e:
        return '(read_graph failed: %s)' % e
    if not nodes:
        return '(no nodes yet)'
    lines = []
    for node in nodes:
        try:
            nid = getattr(node, 'id', '?')
        except Exception:
            nid = '?'
        try:
            name = node.name()
        except Exception:
            name = ''
        ntype = short_type(node_type_of(node))
        lines.append('%s %s "%s"' % (nid, ntype, str(name or '')))
        for p in node.input_ports():
            try:
                for cp in p.connected_ports():
                    src = getattr(cp, 'node', None)
                    src = src() if callable(src) else src
                    try:
                        src_id = getattr(src, 'id', '?')
                    except Exception:
                        src_id = '?'
                    try:
                        src_name = cp.name()
                    except Exception:
                        src_name = '?'
                    lines.append(
                        '    <- %s.%s -> %s' % (src_id, src_name, p.name()))
            except Exception:
                continue
    return 'Canvas (%d nodes):\n%s' % (len(nodes), '\n'.join(lines))


#: Placeholder words a weak extraction latches onto from the canvas text —
#: never a real node name.
_JUNK_LABELS = {
    'empty', 'none', 'null', 'nil', 'n/a', 'na', 'no nodes', 'no nodes yet',
    'canvas', 'unknown', '-', '--', '(empty)', '(none)',
}


def _clean_label(value):
    """A usable node label, or '' (the canvas then uses the type name)."""
    text = str(value or '').strip().strip('"\'()')
    if text.lower() in _JUNK_LABELS or text.lower().startswith('the canvas'):
        return ''
    return text[:80]


def _create_node(ctx, args):
    # Accept a short name, a registry key ('code.CodeNode') or a class name —
    # a live node / the model may report any of them.
    node_type = short_type(args.get('node_type'))
    label = _clean_label(args.get('label'))
    if node_type in DECLINED_TYPES:
        return ('(the builder does not create %s nodes — ask the user to '
                'add it manually)' % node_type)
    if node_type not in NODE_TYPES:
        return '(unknown node_type %r — available: %s)' % (
            node_type, ', '.join(NODE_TYPES))
    chain_file = ''
    if node_type == 'chain_import':
        # A chain import without a real file is a dead node — the sub-chain
        # must exist first (the empty node also crashed the chain editor).
        chain_file = str(args.get('chain_file_path') or '').strip()
        if not chain_file or not os.path.isfile(chain_file):
            return ('(chain_import needs an EXISTING chain_file_path — build '
                    'and save the sub-chain first, then import it here)')
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    pos = None
    hint = ctx.get('position_hint')
    if callable(hint):
        try:
            pos = hint()
        except Exception:
            pos = None
    # The canvas registers '<module>.<ClassName>'; the short name fails with
    # 'Can't find node: "code"'.
    node = mgr.create_node(registry_key(node_type), name=label or None, pos=pos)
    if node is None:
        return '(create_node %r failed on the canvas)' % node_type
    try:
        nid = node.id
    except Exception:
        nid = '?'
    if chain_file:
        try:
            node.set_chain_import_data(chain_file, 'full', '', 1, 0, True)
        except Exception:
            pass
    if node_type == 'input':
        # A builder-made Input node forwards its value as DATA — it must not
        # type onto the user's screen (the node default is passthrough OFF =
        # real keyboard typing, which a builder test must never trigger).
        try:
            node.set_property('passthrough', True)
        except Exception:
            pass
    if node_type in ('input', 'output', 'context') and label:
        # The node NAME is not the 'label' PROPERTY: input/output/context
        # carry a separate 'label' that the chain config and the test's
        # input-binding read.  Set both, or a built chain has an unnamed
        # Input (and its test input can never bind by label).
        try:
            node.set_property('label', label)
        except Exception:
            pass
    if node_type == 'code':
        # A code node's DATA rides its NAMED IO ports (never the base exec
        # edges).  Declare the working defaults up front so the named ports
        # exist to wire to — the model can rename/extend them with edit_node
        # ('input_vars' / 'output_vars').
        try:
            node.set_property('input_vars',
                              json.dumps([{'name': 'data', 'type': 'any'}]))
            node.set_property('output_vars',
                              json.dumps([{'name': 'result', 'type': 'any'}]))
            rebuilder = getattr(node, 'rebuild_ports', None)
            if callable(rebuilder):
                rebuilder()
        except Exception:
            pass
    if node_type == 'llm':
        # A builder-made LLM node must never be a dead "no model" node (it
        # then runs nothing): give it the SAME local default the skill
        # builder uses — editable afterwards.
        try:
            from player.builder.wrap import _default_gguf
            gguf = str(_default_gguf() or '')
        except Exception:
            gguf = ''
        try:
            node.set_property('use_llamacpp', 'true')
            # A processing step must not TYPE its answer onto the screen (the
            # LLM node default is write_text ON = real keyboard typing).
            node.set_property('write_text', 'false')
            if gguf:
                node.set_property('llamacpp_model_path', gguf)
        except Exception:
            pass
    try:
        final_name = str(node.name() or label or '')
    except Exception:
        final_name = label
    # The panel's record flow reads the newest node's recorded file —
    # remember it in the shared turn state.
    ctx.setdefault('state', {})['last_node_id'] = nid
    return 'created %s node %s "%s"' % (node_type, nid, final_name)


def _wire(ctx, args):
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    from_ref = str(args.get('from_node')
                   or args.get('from_node_id') or '').strip()
    to_ref = str(args.get('to_node')
                 or args.get('to_node_id') or '').strip()
    if not (from_ref and to_ref):
        return '(wire needs from_node and to_node — label or id)'
    src = _resolve_node_ref(mgr, from_ref)
    dst = _resolve_node_ref(mgr, to_ref)
    if src is None:
        return '(wire: no node matches %r)' % from_ref
    if dst is None:
        return '(wire: no node matches %r)' % to_ref
    dst_type = short_type(node_type_of(dst))
    input_port = str(args.get('input_port') or '').strip()
    if not input_port:
        input_port = _auto_input_port(src, dst, dst_type)
    in_port = _resolve_port(dst, input_port, lambda n: n.input_ports())
    if in_port is None:
        return ('(wire: node %s has no input port %r — available: %s)' % (
            _node_id(dst), input_port,
            ', '.join(_input_port_names(dst)) or 'none'))
    # Explicit source port when given; else 'output' when present, else first.
    # Named code IO ports are DATA ports: the EXEC edge still leaves through
    # the base 'output' (a separate data edge is added below).
    out_names = _output_port_names(src)
    src_type = short_type(node_type_of(src))
    out_port_name = str(args.get('output_port') or '').strip()
    if out_port_name not in out_names and src_type == 'code':
        # A code node's RESULT leaves through its NAMED output var (consumers
        # read data ports; the base 'output' exec edge is added separately).
        _decl = [n for n in _declared_code_ports(src, 'output_vars')
                 if n in out_names]
        if _decl:
            out_port_name = _decl[0]
    if out_port_name not in out_names:
        out_port_name = 'output' if 'output' in out_names else (
            out_names[0] if out_names else '')
    if not out_port_name:
        return '(wire: node %s has no output ports)' % _node_id(src)
    try:
        ok = mgr.connect_nodes(src, dst, out_port_name, input_port)
    except Exception as e:
        return '(wire failed: %s)' % e
    if not ok:
        return '(wire: connect_nodes refused %s.%s -> %s.%s)' % (
            _node_id(src), out_port_name, _node_id(dst), input_port)
    # A wire ALSO needs its EXEC edge (base 'output' -> base 'input'): DATA
    # ports transfer information between nodes, but the EXEC edge is what
    # decides WHEN a node runs.  Add it whenever the main edge was not it.
    exec_edge = ''
    _exec_in_names = _input_port_names(dst)
    _exec_in = 'input' if 'input' in _exec_in_names else ''
    _exec_out = 'output' if 'output' in out_names else ''
    if (_exec_in and _exec_out
            and (out_port_name != _exec_out or input_port != _exec_in)):
        try:
            if mgr.connect_nodes(src, dst, _exec_out, _exec_in):
                exec_edge = ('; wired %s.%s -> %s.%s (exec)'
                             % (_node_id(src), _exec_out,
                                _node_id(dst), _exec_in))
        except Exception:
            pass
    # EXEC edges never carry data: an Input node's value lives on its 'data'
    # output port, and it feeds the step's 'prompt' port (which REPLACES the
    # step's prompt template — the hand-built system chains' standing-goal
    # feed).  Without this the step runs on its template alone and the value
    # never arrives, so no amount of re-testing can fix the chain.
    data_edge = ''
    if not str(args.get('output_port') or '').strip():
        _src_data = ''
        if 'data' in out_names:
            _src_data = 'data'
        elif src_type == 'code':
            _decl = [n for n in _declared_code_ports(src, 'output_vars')
                     if n in out_names]
            _src_data = _decl[0] if _decl else ''
        _in_names = _input_port_names(dst)
        _dst_data = ''
        if dst_type == 'code':
            _decl = [n for n in _declared_code_ports(dst, 'input_vars')
                     if n in _in_names]
            _dst_data = _decl[0] if _decl else ''
        elif 'prompt' in _in_names:
            _dst_data = 'prompt'
        elif dst_type == 'output' and 'input' in _in_names:
            # An Output node skips exec-only source ports; its data arrives
            # on its 'input' port.
            _dst_data = 'input'
        if (_src_data and _dst_data
                and (_src_data, _dst_data) != (out_port_name, input_port)):
            try:
                if mgr.connect_nodes(src, dst, _src_data, _dst_data):
                    if _dst_data == 'prompt':
                        # The wired value REPLACES the prompt template, so
                        # keep the step's instruction in system_message.
                        try:
                            _p = dst.get_property('prompt')
                            _s = dst.get_property('system_message')
                            if _p and not str(_s or '').strip():
                                dst.set_property('system_message', _p)
                        except Exception:
                            pass
                    data_edge = ('; wired %s.%s -> %s.%s (data port)'
                                 % (_node_id(src), _src_data,
                                    _node_id(dst), _dst_data))
            except Exception:
                pass
    return 'wired %s.%s -> %s.%s%s%s' % (
        _node_id(src), out_port_name, _node_id(dst), input_port,
        exec_edge, data_edge)


def _edit_node(ctx, args):
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    node_ref = str(args.get('node') or args.get('node_id') or '').strip()
    prop = str(args.get('property') or '').strip()
    value = args.get('value')
    if not (node_ref and prop):
        return '(edit_node needs node (label or id) and property)'
    node = _resolve_node_ref(mgr, node_ref)
    if node is None:
        return '(edit_node: no node matches %r)' % node_ref
    ntype = node_type_of(node)
    allowed = allowed_fields(ntype)
    if prop not in allowed:
        return ('(edit_node: property %r is not editable on a %s node — '
                'allowed: %s)' % (prop, ntype or '?', ', '.join(allowed)))
    # Deterministic canonicalisation (bool -> 'true'/'false', number -> str,
    # enum -> matching option): the builder writes native bools and the model
    # writes prose, while the node + executor read the canonical form.
    value = normalize_value(ntype, prop, value)
    try:
        node.set_property(prop, value)
    except Exception as e:
        return '(edit_node: set_property failed: %s)' % e
    # The orchestrator switch rebuilds its ports on toggle — keep the canvas
    # truthful when the builder flips it.
    if prop == 'orchestrator_mode':
        try:
            rebuilder = getattr(node, 'rebuild_orchestrator_ports', None)
            if callable(rebuilder):
                rebuilder()
        except Exception:
            pass
    # Code IO editor: declaring input/output vars must materialise the named
    # ports on the canvas NOW, or a wire to them cannot resolve.
    if prop in ('input_vars', 'output_vars'):
        try:
            rebuilder = getattr(node, 'rebuild_ports', None)
            if callable(rebuilder):
                rebuilder()
        except Exception:
            pass
    shown = str(value)
    if len(shown) > 120:
        shown = shown[:120] + '...'
    return 'set %s.%s = %s' % (_node_id(node), prop, shown)


# --------------------------------------------------------- Laya resolution
#
# Laya is a System-1 encoder selector: it PICKS one key from a supplied menu,
# never authors text.  ``configure_node`` asks it WHICH field an ask sets and,
# for a closed set, WHICH option.  Every read is fail-open: None means Laya is
# unavailable and the caller keeps the LLM / raw path.

def _laya_client():
    """The embedded Laya client, or None (never raises)."""
    try:
        from AI import laya_client as _lc
        if _lc.available() or _lc.ensure_running():
            return _lc
    except Exception:
        pass
    return None


def _laya_choice(state, instructions, criteria):
    """Laya pick among *criteria* keys, or None (unavailable / no pick)."""
    if not criteria:
        return None
    _lc = _laya_client()
    if _lc is None:
        return None
    try:
        picked = _lc.choice(
            str(state)[:1200], str(instructions)[:300],
            {str(k): str(v) for k, v in criteria.items()})
    except Exception:
        return None
    return picked if picked in criteria else None


def _laya_noul(state, instructions):
    """P(statement|state) from Laya, or None (unavailable / read failed)."""
    _lc = _laya_client()
    if _lc is None:
        return None
    try:
        return _lc.noul(str(state)[:1200], str(instructions)[:300])
    except Exception:
        return None


def _configure_node(ctx, args):
    """Set a node field from a natural-language ask, resolved by Laya.

    The LLM says WHAT the node must do; Laya picks WHICH editable field of
    that node the ask sets (a ``choice`` over ``node_templates.field_menu``)
    and, for a closed enum, WHICH option (``option_menu``) — then the field is
    written with the canonical value.  Open kinds (text / number / model /
    path) are reported back so the LLM writes the value with edit_node.
    Fail-open: an unavailable Laya refuses with the field menu, never a
    wrong write.
    """
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    node_ref = str(args.get('node') or args.get('node_id') or '').strip()
    ask = str(args.get('ask') or args.get('wants') or '').strip()
    if not (node_ref and ask):
        return '(configure_node needs node (label or id) and ask)'
    node = _resolve_node_ref(mgr, node_ref)
    if node is None:
        return '(configure_node: no node matches %r)' % node_ref
    ntype = short_type(node_type_of(node))
    menu = field_menu(ntype)
    # The orchestrator switch is a structural MODE, not a "what this node
    # must do" field: a content ask must never silently turn a plain LLM
    # step into an orchestrator brain (an explicit edit_node can).
    menu.pop('orchestrator_mode', None)
    if not menu:
        return '(configure_node: a %s node has no editable fields)' % (
            ntype or '?')
    state = 'Node type: %s. What this node must do: %s' % (ntype, ask)
    picked = _laya_choice(
        state, 'Which editable field of this node does the ask set?', menu)
    if not picked:
        return ('(configure_node: could not map the ask onto a %s field — '
                'set one explicitly with edit_node; fields: %s)'
                % (ntype, ', '.join(list(menu)[:14])))
    spec = field_spec(ntype, picked) or {}
    kind = spec.get('kind')
    if kind == KIND_ENUM:
        options = option_menu(ntype, picked)
        option = _laya_choice(state, 'Which option for %r?' % picked, options)
        if not option:
            return ('(configure_node: resolved field %r — pick its value with '
                    'edit_node; options: %s)' % (picked, ', '.join(options)))
        value = option
    elif kind == KIND_BOOL:
        on = _laya_noul(state, '%s should be ON.' % (spec.get('help') or picked))
        if on is None:
            return ('(configure_node: resolved flag %r — set it with '
                    'edit_node true/false)' % picked)
        value = 'true' if on > 0.5 else 'false'
    else:
        return ('(configure_node: resolved field %r (%s) — write its value '
                'with edit_node %r)' % (picked, kind or 'text', picked))
    value = normalize_value(ntype, picked, value)
    try:
        node.set_property(picked, value)
    except Exception as e:
        return '(configure_node: set_property failed: %s)' % e
    if picked == 'orchestrator_mode':
        try:
            rebuilder = getattr(node, 'rebuild_orchestrator_ports', None)
            if callable(rebuilder):
                rebuilder()
        except Exception:
            pass
    return 'configured %s.%s = %s' % (_node_id(node), picked, value)


def _set_description(ctx, args):
    text = str(args.get('text') or '').strip()
    if not text:
        return '(set_description needs text)'
    ctx.setdefault('state', {})['description'] = text
    cm = _resolve_ctx_value(ctx, 'config_manager')
    if cm is not None:
        try:
            cm._current_chain_description = text
        except Exception:
            pass
    return 'chain description set (%d chars)' % len(text)


def _ask_user(ctx, args):
    question = str(args.get('question') or '').strip()
    kind = str(args.get('kind') or 'answer').strip().lower()
    if kind not in ('answer', 'confirm', 'record_sequence',
                    'record_web_sequence'):
        return '(ask_user: unknown kind %r)' % kind
    ask = ctx.get('ask')
    if not callable(ask):
        return '(ask_user unavailable here)'
    try:
        answer = ask(question, kind)
    except Exception as e:
        return '(ask_user failed: %s)' % e
    if not answer or not str(answer).strip():
        return '(the user gave no answer)'
    return 'user said: %s' % str(answer).strip()


def _delete_node(ctx, args):
    """Remove a RECORDED placeholder node (composer-internal).

    Only the recorded kinds (web_sequence / sequence), and only when
    nothing is wired OUT of the node — the wrap flow replaces a bare
    recording with its chain_import, it never pulls a live wiring apart.
    The recording FILES are never touched.  Uses GraphManager.delete_node
    (the no-dialog path with the hover-bar/effect/ghost cleanup); the
    type-dispatch helper would raise a modal for sequence nodes, which
    must never happen off the GUI thread.
    """
    node_id = str(args.get('node_id') or '').strip()
    if not node_id:
        return '(delete_node needs node_id)'
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    node = _resolve_node_ref(mgr, node_id)
    if node is None:
        return '(delete_node: no node matches %r)' % node_id
    ntype = short_type(node_type_of(node))
    if ntype not in ('web_sequence', 'sequence'):
        return ('(delete_node: %s nodes are not removable here — allowed: '
                'web_sequence, sequence)' % (ntype or '?'))
    try:
        for port in node.output_ports():
            if port.connected_ports():
                return ('(delete_node refused: %s still has outgoing '
                        'connections — unwire it first)' % node_id)
    except Exception:
        pass
    try:
        mgr.delete_node(node)
    except Exception as e:
        return '(delete_node failed: %s)' % e
    return ('removed recorded %s node %s (recording file untouched)'
            % (ntype, node_id))


def _read_node(ctx, args):
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    ref = str(args.get('node') or args.get('node_id') or '').strip()
    if not ref:
        return '(read_node needs node — label or id)'
    node = _resolve_node_ref(mgr, ref)
    if node is None:
        return '(read_node: no node matches %r)' % ref
    try:
        return describe_node(node)
    except Exception as e:
        return '(read_node failed: %s)' % e


#: Hard cap on the steps one skill ladder may carry.
MAX_SKILL_STEPS = 10

#: Ladder layout on the canvas: one horizontal row of steps, the shared
#: context pool under the middle, the output last.
_SKILL_DX = 380.0
_SKILL_DY = 260.0


def _skill_base(mgr):
    """A clear spot to the RIGHT of everything currently on the canvas."""
    base_x, base_y = 40.0, 40.0
    try:
        xs = []
        for n in (mgr.get_all_nodes() or []):
            try:
                pos = n.pos()
                if pos:
                    xs.append(float(pos[0]))
            except Exception:
                continue
        if xs:
            base_x = max(xs) + 460.0
    except Exception:
        pass
    return base_x, base_y


def _compiles(code):
    """(True, '') or (False, error) — syntax gate for authored Python."""
    try:
        compile(str(code or ''), '<skill-step>', 'exec')
        return True, ''
    except Exception as e:
        return False, str(e)


def _build_skill(ctx, args):
    """Build a SKILL ladder on the canvas: input -> steps -> output + pool.

    A skill ("thought ladder") is the working decomposition of ONE process:
    a chain of llm steps (authored prompts, LOCAL models by default), code
    steps (deterministic transforms) and conditional steps (validation by
    code or an LLM), with a shared context node — every llm step writes its
    turn into the pool, the last llm step reads it back.  The assistant
    supplies the decomposition; this executor owns the topology (Python is
    the only chain writer).  The user audits and tunes the nodes (OCR
    triggers, layout match, loops) and saves the chain for the orchestrator
    to import as a tool.
    """
    mgr = _graph_manager(ctx)
    if mgr is None:
        return '(no graph manager available)'
    name = _clean_label(args.get('name')) or 'skill'
    description = str(args.get('description') or '').strip()
    steps = args.get('steps')
    if not isinstance(steps, list) or not steps:
        return '(build_skill needs a non-empty steps list)'
    steps = [s for s in steps if isinstance(s, dict)][:MAX_SKILL_STEPS]
    if not steps:
        return '(build_skill: no usable step objects)'

    def _kind_of(step):
        kind = str(step.get('kind') or '').strip().lower()
        if kind in ('llm', 'code', 'conditional'):
            return kind
        if step.get('code') and not step.get('prompt'):
            return 'code'
        if (step.get('criterion') and not step.get('prompt')
                and not step.get('code')):
            return 'conditional'
        return 'llm'

    kinds = [_kind_of(s) for s in steps]
    n = len(steps)
    has_llm = 'llm' in kinds

    # Creation order: input, steps..., context pool (when llm steps exist),
    # output — with matching positions so the ladder reads left-to-right.
    base_x, base_y = _skill_base(mgr)
    positions = [[base_x, base_y]]
    positions += [[base_x + _SKILL_DX * (i + 1), base_y] for i in range(n)]
    if has_llm:
        positions.append([base_x + _SKILL_DX * (n // 2 + 1),
                          base_y + _SKILL_DY])
    positions.append([base_x + _SKILL_DX * (n + 1), base_y])

    out = []

    def _log(result):
        out.append(str(result))
        return str(result)

    pos_iter = iter(positions)
    old_hint = ctx.get('position_hint')
    ctx['position_hint'] = lambda: next(pos_iter, None)
    try:
        try:
            from player.builder.wrap import _default_gguf
            gguf = str(_default_gguf() or '')
        except Exception:
            gguf = ''

        def _create(node_type, label):
            result = _create_node(ctx, {'node_type': node_type,
                                        'label': label})
            _log(result)
            if str(result).startswith('('):
                return ''
            return str(ctx.get('state', {}).get('last_node_id') or '')

        # 1) the ladder's input — forwarded as data, never typed.
        input_label = _clean_label(args.get('input_label')) or name
        input_id = _create('input', input_label)
        if not input_id:
            return '\n'.join(out)
        _log(_edit_node(ctx, {'node_id': input_id, 'property': 'label',
                              'value': input_label}))
        _log(_edit_node(ctx, {'node_id': input_id, 'property':
                              'passthrough', 'value': True}))

        # 2) the steps — llm / code / conditional, chained in order.
        prev_id, prev_out = input_id, 'output'
        llm_ids = []
        for i, (step, kind) in enumerate(zip(steps, kinds), 1):
            label = _clean_label(step.get('label')) or ('step %d' % i)
            code_in = code_out = ''
            if kind == 'llm':
                nid = _create('llm', label)
                if not nid:
                    return '\n'.join(out)
                prompt = str(step.get('prompt') or step.get('instruction')
                             or '').strip()
                if not prompt:
                    prompt = ('%s. %s' % (label, description)).strip('. ')
                    _log('(note: llm step %d got no prompt — using its '
                         'label; edit the node to write the instruction)' % i)
                _log(_edit_node(ctx, {'node_id': nid, 'property': 'prompt',
                                      'value': prompt[:4000]}))
                model = str(step.get('model') or '').strip()
                if model.lower().endswith('.gguf'):
                    _log(_edit_node(ctx, {'node_id': nid,
                                          'property':
                                          'llamacpp_model_path',
                                          'value': model}))
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'use_llamacpp', 'value': True}))
                elif model:
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'model', 'value': model}))
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'use_llamacpp', 'value': False}))
                else:
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'use_llamacpp', 'value': True}))
                    if gguf:
                        _log(_edit_node(ctx, {'node_id': nid,
                                              'property':
                                              'llamacpp_model_path',
                                              'value': gguf}))
                # A processing step neither types nor screenshots...
                _log(_edit_node(ctx, {'node_id': nid, 'property':
                                      'write_text', 'value': False}))
                vision = bool(step.get('vision'))
                _log(_edit_node(ctx, {'node_id': nid, 'property':
                                      'screenshot_enabled',
                                      'value': vision}))
                if vision:
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'use_vision', 'value': True}))
                llm_ids.append(nid)
            elif kind == 'code':
                nid = _create('code', label)
                if not nid:
                    return '\n'.join(out)
                in_names = _code_port_names(step.get('inputs'), 'data')
                out_names = _code_port_names(step.get('outputs'), 'result')
                code_in, code_out = in_names[0], out_names[0]
                code = str(step.get('code') or '').strip()
                if not code:
                    code = '%s = %s' % (code_out, code_in)
                    _log('(note: code step %d got no code — placeholder '
                         'passthrough; edit the node)' % i)
                else:
                    ok, err = _compiles(code)
                    if not ok:
                        _log('(warning: code step %d did not compile: %s)'
                             % (i, err))
                _log(_edit_node(ctx, {'node_id': nid, 'property': 'code',
                                      'value': code[:8000]}))
                # Named IO ports (the code node's IO editor): the wire lands on
                # the named input, the code publishes the named output.
                _log(_edit_node(ctx, {
                    'node_id': nid, 'property': 'input_vars',
                    'value': json.dumps([{'name': n, 'type': 'any'}
                                         for n in in_names])}))
                _log(_edit_node(ctx, {
                    'node_id': nid, 'property': 'output_vars',
                    'value': json.dumps([{'name': n, 'type': 'any'}
                                         for n in out_names])}))
            else:  # conditional
                nid = _create('conditional', label)
                if not nid:
                    return '\n'.join(out)
                code = str(step.get('code') or '').strip()
                criterion = str(step.get('criterion')
                                or step.get('condition')
                                or step.get('description') or label).strip()
                if code:
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'condition_type',
                                          'value': 'code'}))
                    ok, err = _compiles(code)
                    if not ok:
                        _log('(warning: check step %d code did not compile: '
                             '%s)' % (i, err))
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'code', 'value': code[:8000]}))
                else:
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'condition_type', 'value': 'llm'}))
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'llm_prompt',
                                          'value': criterion[:2000]}))
                    _log(_edit_node(ctx, {'node_id': nid, 'property':
                                          'llm_engine',
                                          'value': 'llamacpp'}))
                    if gguf:
                        _log(_edit_node(ctx, {'node_id': nid,
                                              'property': 'llm_model',
                                              'value': gguf}))
                _log(_edit_node(ctx, {'node_id': nid, 'property':
                                      'description', 'value': label}))
                _log('(check step rides its true port; the false branch '
                     'stays open — wire a fallback if the ladder needs one)')
            _in_port = code_in if kind == 'code' else 'input'
            _wire_args = {'from_node_id': prev_id, 'to_node_id': nid,
                          'input_port': _in_port, 'output_port': prev_out}
            if prev_id == input_id:
                # The first hop must ALSO carry the input value on the input
                # node's 'data' port (it replaces the step's prompt) — leave
                # the output port to the auto data-edge rule in _wire.
                _wire_args.pop('output_port', None)
            _log(_wire(ctx, _wire_args))
            prev_id = nid
            if kind == 'conditional':
                prev_out = 'true'
            elif kind == 'code':
                prev_out = code_out
            else:
                prev_out = 'output'

        # 3) the shared context pool — llm steps write, the last one reads.
        if llm_ids:
            pool_id = _create('context', name + ' context')
            if pool_id:
                _log(_edit_node(ctx, {'node_id': pool_id, 'property':
                                      'label',
                                      'value': name + ' context'}))
                _log(_edit_node(ctx, {'node_id': pool_id, 'property':
                                      'persistent', 'value': False}))
                _log(_edit_node(ctx, {'node_id': pool_id, 'property':
                                      'clear_on_finish', 'value': True}))
                for lid in llm_ids:
                    _log(_wire(ctx, {'from_node_id': lid,
                                     'to_node_id': pool_id,
                                     'input_port': 'ctx_in',
                                     'output_port': 'context'}))
                _log(_wire(ctx, {'from_node_id': pool_id,
                                 'to_node_id': llm_ids[-1],
                                 'input_port': 'context',
                                 'output_port': 'ctx_out'}))

        # 4) the output — what the skill produces.
        output_label = (_clean_label(args.get('output_label'))
                        or (name + ' result'))
        out_id = _create('output', output_label)
        if not out_id:
            return '\n'.join(out)
        _log(_edit_node(ctx, {'node_id': out_id, 'property': 'label',
                              'value': output_label}))
        _log(_wire(ctx, {'from_node_id': prev_id, 'to_node_id': out_id,
                         'input_port': 'input', 'output_port': prev_out}))
    finally:
        if old_hint is None:
            ctx.pop('position_hint', None)
        else:
            ctx['position_hint'] = old_hint

    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    _log('built skill "%s": %d step(s) (%s)%s'
         % (name, n, ', '.join('%d %s' % (v, k)
                               for k, v in sorted(counts.items())),
            ', shared context pool' if llm_ids else ''))
    return '\n'.join(out)


def _save_chain(ctx, args):
    name = str(args.get('name') or '').strip()
    if not name:
        return '(save_chain needs a name)'
    cm = _resolve_ctx_value(ctx, 'config_manager')
    if cm is None:
        return '(no config manager available)'
    chains_dir = str(ctx.get('chains_dir') or '').strip()
    if not chains_dir or not os.path.isdir(chains_dir):
        return '(save_chain: chains dir %r is not writable)' % chains_dir
    try:
        cm._current_chain_collection = SYSTEM_COLLECTION
        desc = ctx.get('state', {}).get('description') or ''
        if desc:
            cm._current_chain_description = str(desc)
        cfg = cm.get_chain_config()
    except Exception as e:
        return '(save_chain: reading canvas failed: %s)' % e
    if not isinstance(cfg, dict):
        return '(save_chain: canvas produced no chain config)'
    cfg['collection'] = SYSTEM_COLLECTION
    if not cfg.get('description'):
        cfg['description'] = ctx.get('state', {}).get('description') or ''
    out_path = os.path.join(chains_dir, slugify(name) + '.json')
    try:
        tmp = out_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, out_path)
    except Exception as e:
        return '(save_chain: write failed: %s)' % e
    return 'saved system chain "%s" to %s' % (name, out_path)


# ---------------------------------------------------------------------------
# Test the current canvas (the builder verifies its own edits)
# ---------------------------------------------------------------------------

#: Cap the report so one test never floods the chat / the next prompt.
_TEST_REPORT_CHARS = 1800


def _clip(text, limit):
    """A one-line, length-capped rendering (never raises)."""
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '…'


def _serialize_canvas(ctx):
    """The live canvas as a chain dict, read on the GUI thread (else None)."""
    cm = _resolve_ctx_value(ctx, 'config_manager')
    if cm is None:
        return None

    def _read():
        try:
            cfg = cm.get_chain_config()
        except Exception:
            return None
        return cfg if isinstance(cfg, dict) else None

    run = ctx.get('run_on_gui')
    if callable(run):
        try:
            return run(_read)
        except Exception:
            return None
    return _read()


def _collect_test_errors(player):
    """[('node_id', 'error text')] from the run's variables (never raises).

    Reads BOTH a node's ``_error`` and its ``_stderr`` — an LLM/web/handle
    failure surfaces on the latter, so the report is never a bare
    "a node returned an error" with no reason.
    """
    out = []
    seen = set()
    try:
        executor = getattr(player, 'workflow_executor', None)
        le = getattr(executor, 'llm_executor', None)
        variables = le.get_all_variables() if le is not None else {}
    except Exception:
        variables = {}
    for key, val in (variables or {}).items():
        key = str(key)
        if not key.startswith('node_') or not val or not str(val).strip():
            continue
        for _suffix in ('_error', '_stderr'):
            if key.endswith(_suffix):
                nid = key[5:-len(_suffix)]
                if nid and nid not in seen:
                    seen.add(nid)
                    out.append((nid, str(val)))
                break
    return out


def _single_input_label(cfg):
    """The label of the chain's ONLY Input node (else '').

    Lets a bare-string ``test_chain.input`` be bound to the single slot
    (``_chain_bound_inputs`` keys by label — priority 0 in input_ops).
    """
    nodes = [n for n in (cfg.get('input_nodes') or [])
             if isinstance(n, dict)]
    if len(nodes) != 1:
        return ''
    return str(nodes[0].get('label') or '').strip()


def _selected_node_ids(ctx):
    """The ids of the canvas nodes the user has selected (never raises).

    The builder's test TARGET: with exactly ONE node selected the panel
    reports it here, so ``test_chain`` verifies that node instead of the
    whole chain.
    """
    try:
        raw = _resolve_ctx_value(ctx, 'selection_node_ids') or []
    except Exception:
        return []
    ids = []
    for item in raw:
        nid = str(getattr(item, 'id', item) or '').strip()
        if nid and nid not in ids:
            ids.append(nid)
    return ids


def _config_node_id(cfg):
    """(node_id, list_key) of the ONE node in a single-node config, else ('','')."""
    for key in ('sequences', 'conditional_nodes', 'llm_nodes',
                'chain_import_nodes', 'form_filler_nodes', 'code_nodes',
                'container_nodes', 'context_nodes', 'input_nodes',
                'handle_nodes', 'mcp_nodes', 'output_nodes', 'web_sequences'):
        for node in (cfg.get(key) or []):
            if isinstance(node, dict):
                nid = node.get('node_id') or node.get('id')
                if nid:
                    return str(nid), key
    return '', ''


def _single_node_config(ctx, node_ref):
    """A throwaway ONE-node chain config for ``node_ref`` (GUI-marshaled).

    Verifying a single node reuses the node bar's own "run this node" path
    (``ConfigManager.get_single_node_config``) instead of serializing the
    whole canvas.  Returns None when the node or config cannot be resolved.
    """
    cm = _resolve_ctx_value(ctx, 'config_manager')
    mgr = _graph_manager(ctx)
    if cm is None or mgr is None:
        return None

    def _build():
        try:
            node = _resolve_node_ref(mgr, node_ref)
        except Exception:
            node = None
        if node is None:
            return None
        builder = getattr(cm, 'get_single_node_config', None)
        if not callable(builder):
            return None
        try:
            cfg = builder(node)
        except Exception:
            return None
        return cfg if isinstance(cfg, dict) else None

    run = ctx.get('run_on_gui')
    if callable(run):
        try:
            return run(_build)
        except Exception:
            return None
    return _build()


def _node_result(executor, node_id):
    """A single node's output text ('' when it produced none)."""
    if not node_id:
        return ''
    try:
        from player.agentic_ops.chain_executor import _get_node_output
    except Exception:
        _get_node_output = None
    if _get_node_output is not None:
        try:
            val = _get_node_output(executor, node_id)
        except Exception:
            val = None
        if val:
            return str(val)
    le = getattr(executor, 'llm_executor', None)
    for key in ('result', 'llm_output', 'output', 'stdout'):
        try:
            val = le.get_variable('node_%s_%s' % (node_id, key)) if le else None
        except Exception:
            val = None
        if val:
            return str(val)
    return ''


def _test_chain(ctx, args):
    """RUN the node the user selected — or the whole chain — and report it.

    The builder's only way to verify its own edits.  With exactly ONE node
    selected (the user's focus) the run is scoped to THAT NODE, built the
    same way the node bar's "run this node" button builds it; the whole
    chain runs otherwise — nothing (or several nodes) selected, or an
    explicit ``scope="chain"`` (a skill IS a whole chain).  The CURRENT
    canvas (unsaved edits included) is serialized, written beside the other
    chains (so relative chain imports still resolve), and executed through
    the player.  The run is UNBOUNDED — a real chain can take hours; the
    only stop is the panel's Stop (Esc).  The report carries the status, the
    failure reason, produced output and any node errors, so the assistant
    can judge the result and iterate.
    """
    scope = str(args.get('scope') or '').strip().lower()
    node_ref = str(args.get('node') or args.get('node_id') or '').strip()
    if scope != 'chain':
        if not node_ref:
            sel = _selected_node_ids(ctx)
            if len(sel) == 1:
                node_ref = sel[0]
        if node_ref:
            node_cfg = _single_node_config(ctx, node_ref)
            if isinstance(node_cfg, dict) and node_cfg:
                node_id, _key = _config_node_id(node_cfg)
                if node_id:
                    return _run_test_config(ctx, args, node_cfg, node_id)
    cfg = _serialize_canvas(ctx)
    if not isinstance(cfg, dict):
        return '(test_chain: could not read the canvas)'
    return _run_test_config(ctx, args, cfg, '')


def _run_test_config(ctx, args, cfg, node_id=''):
    """Serialize one test chain (whole canvas or one node) and run it.

    ``node_id`` non-empty selects the single-node report; else the chain
    report (status + every Output node + goal verdict).
    """
    # A TEST must NEVER inject real screen input: run a throwaway COPY with
    # every Input node forced to passthrough (data-only), so verifying a chain
    # can never hijack the user's keyboard.  The live canvas is not touched.
    cfg = copy.deepcopy(cfg)
    for _in in (cfg.get('input_nodes') or []):
        if isinstance(_in, dict):
            _in['passthrough'] = True
    for _llm in (cfg.get('llm_nodes') or []):
        if isinstance(_llm, dict):
            _llm['write_text'] = False
    chains_dir = str(_resolve_ctx_value(ctx, 'chains_dir') or '').strip()
    base_dir = chains_dir if (chains_dir and os.path.isdir(chains_dir)) \
        else tempfile.gettempdir()
    path = os.path.join(base_dir, '__builder_test__.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return '(test_chain: could not write the test chain: %s)' % e

    raw_input = args.get('input')
    if raw_input is None:
        raw_input = args.get('query')
    bound = {}
    query = ''
    if isinstance(raw_input, dict):
        # {label: value} — bind each advertised Input slot by label.
        bound = {str(k): str(v) for k, v in raw_input.items()}
    elif raw_input is not None and str(raw_input) != '':
        query = str(raw_input)
        slot = _single_input_label(cfg)
        if slot:
            bound[slot] = query
    run_vars = {'_chain_bound_inputs': bound} if bound else None

    stop = ctx.get('stop')

    def _stop():
        # No timeout: a chain may legitimately run for hours.  The user's
        # Stop (Esc) is the only bound; without a hook the run is unbounded.
        try:
            return bool(callable(stop) and stop())
        except Exception:
            return False

    ok = None
    player = None
    error = ''
    try:
        from player.multi_sequence_player import play_chain_with_tailcalls
        ok, player = play_chain_with_tailcalls(
            path, agent_query=query, stop_flag=_stop,
            variables=run_vars, run_source='play')
    except Exception as e:
        error = str(e)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass
    if error:
        return '(test_chain: run failed: %s)' % error
    if node_id:
        return _format_node_report(ok, player, _stop(), bound, node_id)
    goal = str(args.get('goal')
               or ctx.get('state', {}).get('description')
               or cfg.get('description') or '').strip()
    return _format_test_report(cfg, ok, player, _stop(), bound, goal)


def _format_node_report(ok, player, stopped, bound, node_id):
    """The bounded result of ONE node's test run (status + its own output)."""
    lines = []
    failure = _chain_failure(player)
    if stopped:
        lines.append('test: STOPPED by the user')
    elif ok is False:
        lines.append('test: FAILED (the node returned an error)')
    elif ok is None:
        lines.append('test: STALLED (execution could not continue)')
    else:
        lines.append('test: completed')
    lines.append('scope: node %s alone (the whole chain was NOT run)'
                 % node_id)
    if failure:
        kind, nid, ntype, reason = failure
        who = ('%s %s' % (ntype, nid)).strip() or 'the node'
        lines.append('reason: %s -> %s' % (who, _clip(reason, 500)))
    for label, value in (bound or {}).items():
        lines.append('input %s: %s'
                     % (_clip(str(label), 40),
                        _clip(' '.join(str(value).split()), 200)))
    executor = getattr(player, 'workflow_executor', None) if player else None
    val = _node_result(executor, node_id)
    if val:
        lines.append('output %s: %s' % (node_id, _clip(val, 600)))
    for nid, err in _collect_test_errors(player)[:6]:
        lines.append('error %s: %s' % (nid, _clip(err, 300)))
    if not val and not stopped and ok is not False:
        lines.append('(the node produced no output — check its settings)')
    return _clip('\n'.join(lines), _TEST_REPORT_CHARS)


def _chain_failure(player):
    """(kind, node_id, node_type, reason) the executor recorded, or None."""
    try:
        le = getattr(getattr(player, 'workflow_executor', None),
                     'llm_executor', None)
    except Exception:
        le = None
    if le is None:
        return None

    def _g(name):
        try:
            return le.get_variable(name)
        except Exception:
            return None

    if not _g('_chain_failed'):
        return None
    return (_g('_chain_failure_kind') or '',
            _g('_chain_failed_node') or '',
            _g('_chain_failed_type') or '',
            _g('_chain_failure_reason') or '')


def _goal_verdict(goal, outputs_text):
    """Laya noul: does the run's OUTPUT accomplish the GOAL? '' if unknown.

    Fail-open: an unavailable Laya (or a failed read) yields '' and the report
    simply marks the goal 'not verified' — it never blocks the builder.
    """
    goal = str(goal or '').strip()
    text = str(outputs_text or '').strip()
    if not goal or not text:
        return ''
    try:
        from AI import laya_client as _lc
        if not (_lc.available() or _lc.ensure_running()):
            return ''
        p = _lc.noul(
            'Goal: %s\nResult: %s' % (goal[:400], text[:1200]),
            'The result accomplishes the goal.')
    except Exception:
        return ''
    if p is None:
        return ''
    return 'MET' if p > 0.5 else 'NOT met'


def _format_test_report(cfg, ok, player, stopped, bound, goal=''):
    """The bounded, readable result of one test run (status + WHY + goal)."""
    lines = []
    failure = _chain_failure(player)
    if stopped:
        lines.append('test: STOPPED by the user')
    elif ok is False:
        lines.append('test: FAILED (a node returned an error)')
    elif ok is None:
        lines.append('test: STALLED (execution could not continue)')
    else:
        lines.append('test: completed')
    if failure:
        kind, nid, ntype, reason = failure
        who = ('%s %s' % (ntype, nid)).strip() or 'the chain'
        lines.append('reason: %s -> %s' % (who, _clip(reason, 500)))
    for label, value in (bound or {}).items():
        lines.append('input %s: %s'
                     % (_clip(str(label), 40),
                        _clip(' '.join(str(value).split()), 200)))

    executor = getattr(player, 'workflow_executor', None) if player else None
    try:
        from player.agentic_ops.chain_executor import _get_node_output
    except Exception:
        _get_node_output = None
    collected = []
    shown = 0
    for node in (cfg.get('output_nodes') or []):
        if not isinstance(node, dict):
            continue
        nid = node.get('node_id') or node.get('id')
        if not nid:
            continue
        label = (node.get('label') or node.get('variable_name') or nid)
        val = None
        if _get_node_output is not None:
            try:
                val = _get_node_output(executor, nid)
            except Exception:
                val = None
        if val:
            lines.append('output %s: %s'
                         % (_clip(str(label), 40), _clip(val, 600)))
            collected.append(str(val))
            shown += 1
    for nid, err in _collect_test_errors(player)[:6]:
        lines.append('error %s: %s' % (nid, _clip(err, 300)))
    if shown == 0 and not stopped and ok is not False:
        lines.append('(no Output node produced text — check the wiring)')
    if goal:
        _goal_short = _clip(' '.join(str(goal).split()), 120)
        verdict = _goal_verdict(goal, '\n'.join(collected))
        lines.append('goal (%s): %s'
                     % (_goal_short, verdict or 'not verified'))
    return _clip('\n'.join(lines), _TEST_REPORT_CHARS)


# ---------------------------------------------------------------------------
# Runtime-side execution
# ---------------------------------------------------------------------------

def resolve_chain_import_file(chain_config, alias):
    """The chain FILE a tool alias refers to, or '' when not found.

    alias matches a chain_import prefix or the imported file's basename
    (without .json).  Top-level only — level-local, never a walk.
    """
    alias_norm = str(alias or '').strip().lower()
    if not alias_norm or not isinstance(chain_config, dict):
        return ''
    for node in (chain_config.get('chain_import_nodes') or []):
        if not isinstance(node, dict):
            continue
        data = node.get('data', {}) if isinstance(node.get('data'), dict) else node
        prefix = str(data.get('prefix') or '').strip().lower()
        path = str(data.get('chain_file_path') or data.get('chain_file') or '').strip()
        base = os.path.splitext(os.path.basename(path or ''))[0].lower()
        if alias_norm in (prefix, base) and path:
            if not os.path.isabs(path):
                chain_dir = os.path.dirname(
                    str(chain_config.get('__chain_file__') or ''))
                if chain_dir:
                    cand = os.path.join(chain_dir, path)
                    if os.path.isfile(cand):
                        return cand
            if os.path.isfile(path):
                return path
    return ''


def agent_modifiable_labels(chain_config, alias=''):
    """The advertised bindable Input labels (agent_modifiable only).

    alias='' scans the chain's own input_nodes; an alias scans the imported
    chain's file.  Labels are exact — bind_input refuses anything else.
    """
    cfg = chain_config
    if alias:
        path = resolve_chain_import_file(chain_config, alias)
        if not path:
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            return []
        cfg['__chain_file__'] = path
    if not isinstance(cfg, dict):
        return []
    labels = []
    for node in (cfg.get('input_nodes') or []):
        if not isinstance(node, dict):
            continue
        data = node.get('data', {}) if isinstance(node.get('data'), dict) else node
        if not _truthy(data.get('agent_modifiable')):
            continue
        label = str(data.get('label') or '').strip()
        if label and label not in labels:
            labels.append(label)
    return labels


def _truthy(value):
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ('true', '1', 'yes', 'on')


def execute_runtime_call(name, arguments, ctx):
    """Execute ONE runtime tool call during chat-mode chain dispatch.

    ctx contract:
      ctx['chain_config']  the dispatched chain config dict
      ctx['bound']         mutable dict {label: value} for this dispatch
      ctx['read_output']   callable(alias, label) -> str
      ctx['write_context'] callable(text) -> str

    Never raises: every failure becomes a deterministic error string.
    """
    try:
        args = arguments or {}
        if name == 'bind_input':
            return _bind_input(ctx, args)
        if name == 'read_output':
            return _read_output(ctx, args)
        if name == 'write_context':
            return _write_context(ctx, args)
        return '(unknown runtime tool: %s)' % name
    except Exception as e:
        logger.exception('runtime tool %s failed', name)
        return '(runtime tool %s failed: %s)' % (name, e)


def _bind_input(ctx, args):
    alias = str(args.get('alias') or '').strip()
    label = str(args.get('label') or '').strip()
    value = args.get('value')
    if not label or value is None or not str(value).strip():
        return '(bind_input needs a label and a non-empty value)'
    chain_config = ctx.get('chain_config') or {}
    labels = agent_modifiable_labels(chain_config, alias)
    if label not in labels:
        return ('(bind_input: %r is not a bindable slot of %r — bindable: %s)'
                % (label, alias or 'this chain', ', '.join(labels) or 'none'))
    ctx.setdefault('bound', {})[label] = str(value)
    return 'bound %r = %s' % (label, str(value)[:120])


def _read_output(ctx, args):
    read_fn = ctx.get('read_output')
    if not callable(read_fn):
        return '(read_output unavailable here)'
    alias = str(args.get('alias') or '').strip()
    label = str(args.get('label') or '').strip()
    try:
        value = read_fn(alias, label)
    except Exception as e:
        return '(read_output failed: %s)' % e
    if value is None or not str(value).strip():
        return '(no output for %r)' % (label or alias)
    return str(value)


def _write_context(ctx, args):
    write_fn = ctx.get('write_context')
    if not callable(write_fn):
        return '(write_context unavailable here)'
    text = str(args.get('text') or '').strip()
    if not text:
        return '(write_context needs text)'
    try:
        result = write_fn(text)
    except Exception as e:
        return '(write_context failed: %s)' % e
    return 'context note stored: %s' % str(result or 'ok')


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

class BuilderRouter(NeedleRouter):
    """Needle session over the build-side catalogue."""

    schemas = BUILDER_TOOL_SCHEMAS
    known_tools = {t['name'] for t in BUILDER_TOOL_SCHEMAS}

    def _execute_call(self, name, args):
        return execute_builder_call(name, args, self._ctx)


class RuntimeRouter(NeedleRouter):
    """Needle session over the runtime catalogue."""

    schemas = RUNTIME_TOOL_SCHEMAS
    known_tools = {t['name'] for t in RUNTIME_TOOL_SCHEMAS}

    def _execute_call(self, name, args):
        return execute_runtime_call(name, args, self._ctx)


# ---------------------------------------------------------------------------
# Legacy text-marker fallback (needle.exe missing)
# ---------------------------------------------------------------------------

_LEGACY_RE = re.compile(
    r'^###\s*BUILDER:\s*(\w+)\s*(.*?)\s*$', re.MULTILINE)


def parse_legacy_directives(text):
    """Strict marker fallback for create_node/wire/edit_node only.

    Lines look like::

        ### BUILDER: create_node type=web_sequence label=click apply
        ### BUILDER: wire <from_id> <to_id> <input_port>
        ### BUILDER: edit <node_id> <property> <value...>

    Anything else is ignored — the fallback covers the safe subset.
    """
    out = []
    for match in _LEGACY_RE.finditer(str(text or '')):
        tool = match.group(1).strip()
        rest = (match.group(2) or '').strip()
        if tool == 'create_node':
            args = dict(
                (m.group(1), m.group(2))
                for m in re.finditer(r'(\w+)=(\S+)', rest))
            if not args.get('node_type'):
                args['node_type'] = args.pop('type', '')
            if args.get('node_type'):
                out.append((tool, args))
        elif tool == 'wire':
            parts = rest.split()
            if len(parts) >= 3:
                out.append((tool, {
                    'from_node_id': parts[0], 'to_node_id': parts[1],
                    'input_port': parts[2],
                }))
        elif tool == 'edit':
            parts = rest.split(None, 2)
            if len(parts) >= 3:
                out.append((tool, {
                    'node_id': parts[0], 'property': parts[1],
                    'value': parts[2],
                }))
    return out
