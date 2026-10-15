"""ports.py -- the SINGLE publisher + resolver for node data ports.

The harness contract, in one place:

- Base ports (``input``/``output``, the empty default and the branch ports
  ``true``/``false``/``error``/``route``) drive EXECUTION order only -- they
  carry NO data.  A consumer reached through one sees ``None``.
- Every OTHER port (``data``, ``context``, ``ctx_out`` and any custom port a
  Code node declares in its IO tab) carries DATA.  The value is published
  under the port's own NAME and resolved by that name -- no shape guessing.
- The LLM ``context`` output is the plain RESPONSE TEXT.  The structured
  ``{user_input, response}`` envelope is internal metadata (published as
  ``context_meta``/``input_context``); it never reaches a data port, so no
  consumer has to unwrap it.

Two functions keep this honest:

* ``publish_ports`` -- writers declare a node's data ports once.
* ``resolve_port``  -- the ONE reader, imported by ``code_ops`` and by
  ``llm_executor_resources.inputs`` so a Code node and an LLM node reading
  the same edge see the same value.
"""

from typing import Any, Dict, Mapping, Optional

#: Output ports that drive execution ONLY -- they carry no data.
EXEC_ONLY_OUTPUT_PORTS = frozenset(
    {'', 'output', 'true', 'false', 'error', 'route'}
)

#: Port name -> the flat-variable key a producer also writes (legacy channel).
#: Any port not listed here maps to ``node_<id>_output_<port>``.
_LEGACY_KEY = {
    'data': 'node_{id}_data',
    'context': 'node_{id}_context',
    'ctx_out': 'node_{id}_context',
}


def publish_ports(port_store, chain_id: str, node_id: str,
                  values: Optional[Mapping[str, Any]] = None) -> None:
    """Publish ``{port: value}`` for one node into the port-scoped store.

    ``None`` values are skipped (a missing port is honest); never raises.
    """
    if port_store is None or not node_id:
        return
    for port, val in (values or {}).items():
        if val is None:
            continue
        try:
            port_store.set_output(chain_id, str(node_id), str(port), val)
        except Exception:
            pass


def resolve_port(variables, from_id: str, output_type: Optional[str] = None,
                 port_store=None, chain_id: str = "") -> Any:
    """The value a producer published on ``output_type``, or ``None``.

    Exec-only ports carry no data.  The flat-variable channel is read first
    (every producer writes it), then the :class:`PortScopedStore` as a
    fallback for store-only producers.  The value is returned VERBATIM --
    consumers never re-interpret its shape.
    """
    ot = str(output_type or 'output')
    if ot.lower() in EXEC_ONLY_OUTPUT_PORTS:
        return None
    if variables is not None:
        key = _LEGACY_KEY.get(ot.lower())
        if key is not None:
            key = key.format(id=from_id)
        else:
            key = 'node_{id}_output_{port}'.format(id=from_id, port=ot)
        val = _safe_get(variables, key)
        if val is None:
            # Named ports are stored case-sensitively; try the lowered form.
            val = _safe_get(
                variables,
                'node_{id}_output_{port}'.format(id=from_id, port=ot.lower()),
            )
        if val is not None:
            return val
    if port_store is not None:
        try:
            return port_store.get_output(chain_id, str(from_id), ot)
        except Exception:
            return None
    return None


def _safe_get(mapping, key: str) -> Any:
    try:
        return mapping.get(key)
    except Exception:
        return None
