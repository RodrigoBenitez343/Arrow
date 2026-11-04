"""ports.py — what every port on every Arrow node is FOR.

An **execution** edge and a **data** edge are not interchangeable, and no two
nodes expose the same port set.  This module is the single authority on that:

* every port name a node can carry has a ROLE (execution, branch, data,
  prompt, context, tool, brain, trace, route, error, named IO);
* a role says what an edge through that port *means* — ``input.data ->
  llm.prompt`` REPLACES the prompt template, ``llm.context -> context.ctx_in``
  stores the turn, ``context.ctx_out -> llm.context`` serves the whole pool;
* an edge whose two roles do not pair is a HARD ERROR (``UnknownPortError`` /
  ``BuilderPortError``), never "close enough".

The readings here come from the live node classes in
``LoOper/NGUI/nodes_resources/*.py`` (the source of truth), including the ports
that only exist while a mode is on (input decision routing, LLM orchestrator
mode, conditional file-loops, code named IO).
"""

from dataclasses import dataclass

from .errors import UnknownPortError
from .node_templates import node_type_of, short_type

# ------------------------------------------------------------------ roles

EXEC_IN = 'exec_in'
EXEC_OUT = 'exec_out'
BRANCH_TRUE = 'branch_true'
BRANCH_FALSE = 'branch_false'
LOOP_OUT = 'loop_out'
DATA_IN = 'data_in'
DATA_OUT = 'data_out'
PROMPT_IN = 'prompt_in'
CONTEXT_IN = 'context_in'
CONTEXT_OUT = 'context_out'
TOOLS_IN = 'tools_in'
BRAINS_IN = 'brains_in'
TRACE_OUT = 'trace_out'
ROUTE_OUT = 'route_out'
ERROR_OUT = 'error_out'
NAMED_IN = 'named_in'
NAMED_OUT = 'named_out'

#: Roles an execution walk follows (in order of preference when a node has
#: several: the base exec output first, then the branch ports).
EXEC_ROLES = (EXEC_OUT, BRANCH_TRUE, BRANCH_FALSE, LOOP_OUT)
#: Roles that consume execution.
EXEC_SINK_ROLES = (EXEC_IN,)

#: Human labels for the role table used by the digest.
ROLE_LABEL = {
    EXEC_IN: 'exec in',
    EXEC_OUT: 'exec out',
    BRANCH_TRUE: 'branch true',
    BRANCH_FALSE: 'branch false',
    LOOP_OUT: 'loop out',
    DATA_IN: 'data in',
    DATA_OUT: 'data out',
    PROMPT_IN: 'prompt in',
    CONTEXT_IN: 'context in',
    CONTEXT_OUT: 'context out',
    TOOLS_IN: 'tools in',
    BRAINS_IN: 'brains in',
    TRACE_OUT: 'trace out',
    ROUTE_OUT: 'route out',
    ERROR_OUT: 'error out',
    NAMED_IN: 'named in',
    NAMED_OUT: 'named out',
}


@dataclass(frozen=True)
class PortSpec:
    """One port: its role and what an edge through it means."""

    role: str
    meaning: str

    @property
    def is_exec(self):
        return self.role in EXEC_ROLES or self.role in EXEC_SINK_ROLES


@dataclass(frozen=True)
class PortView:
    """A live port: name, direction, spec and resolved role."""

    name: str
    direction: str          # 'in' | 'out'
    role: str
    meaning: str
    spec: PortSpec

    @property
    def is_exec(self):
        return self.spec.is_exec


# ------------------------------------------------------------------ table
#
# shape: {node type: {'in': {port: PortSpec}, 'out': {port: PortSpec}}}

_EXEC_TRIGGER = 'execution trigger from the upstream node'
_NEXT = 'continues execution to the next node'
_VALUE = 'the value this node produces'
_STORE = 'value appended to the pool'


def _p(role, meaning):
    return PortSpec(role, meaning)


PORT_TABLE = {
    'input': {
        'in': {
            'input': _p(EXEC_IN, _EXEC_TRIGGER + ' (the upstream value rides it)'),
        },
        'out': {
            'output': _p(EXEC_OUT, _NEXT + '; carries nothing'),
            'data': _p(DATA_OUT, 'the resolved value: label / default / asked answer / document'),
            'true': _p(BRANCH_TRUE, 'YES answer or TRUE decision (only with decision_mode or route_on_answer)'),
            'false': _p(BRANCH_FALSE, 'NO answer or FALSE decision (only with decision_mode or route_on_answer)'),
        },
    },
    'output': {
        'in': {
            'input': _p(EXEC_IN, 'the value this sink displays and reports'),
        },
        'out': {
            'output': _p(EXEC_OUT, 'continues execution after the report (rarely wired)'),
        },
    },
    'llm': {
        'in': {
            'input': _p(EXEC_IN, _EXEC_TRIGGER),
            'prompt': _p(PROMPT_IN, 'text wired here REPLACES the prompt template (system_message is untouched)'),
            'context': _p(CONTEXT_IN, 'history / pool material appended to the call'),
            'tools': _p(TOOLS_IN, 'deterministic chains this orchestrator may run (orchestrator_mode only)'),
            'brains': _p(BRAINS_IN, 'sub-brains this orchestrator may delegate to (orchestrator_mode only)'),
        },
        'out': {
            'output': _p(EXEC_OUT, _NEXT),
            'context': _p(CONTEXT_OUT, 'this turn record, for a context pool or another LLM'),
            'trace': _p(TRACE_OUT, 'the orchestrator step trace (orchestrator_mode only)'),
            'route': _p(ROUTE_OUT, 'the orchestrator routing decision (orchestrator_mode only)'),
        },
    },
    'code': {
        'in': {
            'input': _p(EXEC_IN, _EXEC_TRIGGER),
        },
        'out': {
            'output': _p(EXEC_OUT, _NEXT + '; carries nothing'),
            'error': _p(ERROR_OUT, 'taken when the script raises'),
        },
    },
    'conditional': {
        'in': {
            'input': _p(EXEC_IN, _EXEC_TRIGGER),
        },
        'out': {
            'true': _p(BRANCH_TRUE, 'the criterion held'),
            'false': _p(BRANCH_FALSE, 'the criterion did not hold'),
            'output': _p(LOOP_OUT, 'single continuation of a FILE-LOOP conditional (loop_type + sequence_file)'),
        },
    },
    'chain_import': {
        'in': {
            'input': _p(EXEC_IN, _EXEC_TRIGGER + '; also feeds the imported chain'),
        },
        'out': {
            'output': _p(EXEC_OUT, _NEXT + '; the imported Output values go to the tool consumer, not through a port'),
        },
    },
    'sequence': {
        'in': {'input': _p(EXEC_IN, _EXEC_TRIGGER)},
        'out': {'output': _p(EXEC_OUT, _NEXT)},
    },
    'action': {
        'in': {'input': _p(EXEC_IN, _EXEC_TRIGGER)},
        'out': {'output': _p(EXEC_OUT, _NEXT)},
    },
    'web_sequence': {
        'in': {'input': _p(EXEC_IN, _EXEC_TRIGGER)},
        'out': {
            'output': _p(EXEC_OUT, _NEXT),
            'ctx_out': _p(DATA_OUT, 'the page data captured after replay (extract_items)'),
        },
    },
    'form_filler': {
        'in': {
            'input': _p(EXEC_IN, _EXEC_TRIGGER),
            'ctx_in': _p(CONTEXT_IN, 'the knowledge pool the filler reads (source documents / facts)'),
        },
        'out': {
            'output': _p(EXEC_OUT, _NEXT),
            'ctx_out': _p(DATA_OUT, 'what the filler filled / learned'),
        },
    },
    'handle': {
        'in': {
            'input': _p(EXEC_IN, _EXEC_TRIGGER),
            'data': _p(DATA_IN, 'the goal/object text the handle grounds (from an Input data port)'),
        },
        'out': {
            'output': _p(EXEC_OUT, _NEXT),
            'data': _p(DATA_OUT, 'what was done and on which elements'),
        },
    },
    'context': {
        'in': {
            'ctx_in': _p(DATA_IN, _STORE + ' (multi: every producer wired here is stored)'),
        },
        'out': {
            'ctx_out': _p(CONTEXT_OUT, "the pool's whole material: documents + skills + stored rows + learned facts"),
        },
    },
    'mcp': {
        'in': {'input': _p(EXEC_IN, _EXEC_TRIGGER)},
        'out': {'output': _p(EXEC_OUT, _NEXT)},
    },
    'container': {
        'in': {'input': _p(EXEC_IN, _EXEC_TRIGGER)},
        'out': {'output': _p(EXEC_OUT, _NEXT)},
    },
}

#: Ports that only exist while a mode flag is on.  Used by the digest to say
#: "this node is in orchestrator mode" rather than inventing a role.
MODE_PORTS = {
    'llm': ('tools', 'brains', 'trace', 'route'),
    'input': ('true', 'false'),
    'conditional': ('output',),
}

#: Roles a data edge may flow INTO.
DATA_SINK_ROLES = (DATA_IN, PROMPT_IN, CONTEXT_IN, TOOLS_IN, BRAINS_IN,
                   NAMED_IN)
#: Roles a data edge may flow OUT of.
DATA_SOURCE_ROLES = (DATA_OUT, CONTEXT_OUT, TRACE_OUT, ROUTE_OUT,
                     NAMED_OUT)


# ------------------------------------------------------------- resolution

def _code_named_ports(node, prop):
    """Declared named IO names of a code node (``input_vars``/``output_vars``)."""
    import json
    raw = dict(node.properties() or {}).get(prop)
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = json.loads(raw or '[]')
    names = []
    for item in (raw or []):
        name = str((item or {}).get('name') or '').strip() if isinstance(item, dict) else ''
        if name:
            names.append(name)
    return names


def spec_for(node, direction, name):
    """The ``PortSpec`` of ``node``'s port ``name`` in ``direction``.

    Raises ``UnknownPortError`` when a known node type carries a port the
    table does not classify — that is a port-reading bug, and pretending it
    is a plain data port would mis-describe the chain.
    """
    ntype = short_type(node_type_of(node))
    table = PORT_TABLE.get(ntype)
    if table is None:
        raise UnknownPortError(
            'node %r reports type %r, which has no port table'
            % (_node_label(node), ntype))
    spec = table.get(direction, {}).get(name)
    if spec is not None:
        return spec
    if ntype == 'code':
        named = _code_named_ports(
            node, 'input_vars' if direction == 'in' else 'output_vars')
        if name in named:
            return _p(NAMED_IN if direction == 'in' else NAMED_OUT,
                      'code variable %r (%s)' % (
                          name, 'read by the script' if direction == 'in'
                          else 'published by the script'))
    raise UnknownPortError(
        '%s node %r carries an unclassified %s port %r'
        % (ntype, _node_label(node), 'input' if direction == 'in' else 'output',
           name))


def _node_label(node):
    return str(getattr(node, 'id', '') or '?')


def ports_of(node):
    """``(inputs, outputs)`` — live ``PortView`` lists for one node."""
    out = {}
    for direction, getter in (('in', node.input_ports),
                              ('out', node.output_ports)):
        views = []
        for port in (getter() or []):
            name = str(port.name())
            spec = spec_for(node, direction, name)
            views.append(PortView(name=name, direction=direction,
                                  role=spec.role, meaning=spec.meaning,
                                  spec=spec))
        out[direction] = views
    return out['in'], out['out']


def exec_outputs(node, outputs=None):
    """The node's execution output ports, in walk order."""
    if outputs is None:
        _, outputs = ports_of(node)
    return [p for p in outputs if p.role in EXEC_ROLES]


def exec_input(node, inputs=None):
    """The node's execution input port (None when it has no execution)."""
    if inputs is None:
        inputs, _ = ports_of(node)
    for port in inputs:
        if port.role in EXEC_SINK_ROLES:
            return port
    return None


def edge_kind(src_role, dst_role):
    """``'exec'`` / ``'data'`` / ``'illegal'`` for a role pair.

    ``illegal`` is a real, reported state: a user can drag a wire between any
    two ports, and the digest must name that wire as a defect instead of
    pretending it carries either execution or data.
    """
    if src_role in EXEC_ROLES and dst_role in EXEC_SINK_ROLES:
        return 'exec'
    if src_role in DATA_SOURCE_ROLES and dst_role in DATA_SINK_ROLES:
        return 'data'
    return 'illegal'


def illegal_edge_reason(src_role, dst_role):
    """Why a role pair cannot carry an edge (for the violation report)."""
    return (
        'a %s output cannot feed a %s input: execution edges connect %s to '
        '%s, data edges connect %s to %s'
        % (ROLE_LABEL.get(src_role, src_role),
           ROLE_LABEL.get(dst_role, dst_role),
           '/'.join(ROLE_LABEL[r] for r in EXEC_ROLES), ROLE_LABEL[EXEC_IN],
           '/'.join(ROLE_LABEL[r] for r in DATA_SOURCE_ROLES),
           '/'.join(ROLE_LABEL[r] for r in DATA_SINK_ROLES)))


def port_role_line(view):
    """``input (exec in)`` — a port rendered with its role."""
    return '%s (%s)' % (view.name, ROLE_LABEL.get(view.role, view.role))
