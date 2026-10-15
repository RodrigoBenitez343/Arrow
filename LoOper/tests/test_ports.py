"""ports.py -- the single publisher + resolver for node data ports.

Run:  python -m pytest LoOper/tests/test_ports.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from player.multi_sequence.llm_executor_resources.inputs import upstream_value  # noqa: E402
from player.multi_sequence.worflow_interpreter_modules.executor_modules.ports import (  # noqa: E402,E501
    EXEC_ONLY_OUTPUT_PORTS,
    publish_ports,
    resolve_port,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store import (  # noqa: E402
    PortScopedStore,
)


# --------------------------------------------------------------------------- resolver


def test_exec_only_ports_carry_no_data():
    """Base/branch output ports drive execution only — always None."""
    assert resolve_port({'node_a_output': 'X'}, 'a', 'output') is None
    assert resolve_port({'node_a_output': 'X'}, 'a', '') is None
    assert resolve_port({'node_a_output': 'X'}, 'a', 'true') is None
    assert resolve_port({'node_a_output': 'X'}, 'a', 'route') is None
    assert set(EXEC_ONLY_OUTPUT_PORTS) == {
        '', 'output', 'true', 'false', 'error', 'route'}


def test_named_channels_resolve_by_port_name():
    assert resolve_port({'node_a_data': 'D'}, 'a', 'data') == 'D'
    assert resolve_port({'node_a_context': 'C'}, 'a', 'context') == 'C'
    assert resolve_port({'node_a_context': 'C'}, 'a', 'ctx_out') == 'C'
    assert resolve_port({'node_a_output_total': 'T'}, 'a', 'total') == 'T'


def test_named_port_never_falls_back_to_base_output():
    """A named port must not read the exec-only base 'output' channel."""
    assert resolve_port({'node_a_output': 'G'}, 'a', 'summary') is None
    assert resolve_port(
        {'node_a_output_summary': None, 'node_a_output': 'G'},
        'a', 'summary') is None


def test_store_fallback_when_no_flat_variable():
    store = PortScopedStore()
    store.set_output('c1', 'a', 'ctx_out', 'from store')
    assert resolve_port({}, 'a', 'ctx_out', port_store=store,
                        chain_id='c1') == 'from store'
    # Flat variable wins over the store (producers write both).
    assert resolve_port({'node_a_context': 'flat'}, 'a', 'ctx_out',
                        port_store=store, chain_id='c1') == 'flat'


def test_resolve_port_value_is_verbatim_never_unwrapped():
    """A JSON-looking answer must NOT be shape-guessed."""
    blob = '{"response": "keep whole"}'
    assert resolve_port({'node_a_context': blob}, 'a', 'context') == blob
    assert resolve_port({'node_a_data': blob}, 'a', 'data') == blob


# --------------------------------------------------------------------------- parity


def test_upstream_value_delegates_to_the_shared_resolver():
    """The LLM input reader and the shared resolver agree on every channel."""
    vars_ = {'node_a_data': 'D', 'node_a_context': 'C',
             'node_a_output_total': 'T', 'node_a_output': 'O'}
    for port in ('data', 'context', 'ctx_out', 'total', 'output', 'true'):
        assert upstream_value(vars_, 'a', port) == \
            resolve_port(vars_, 'a', port), port


# --------------------------------------------------------------------------- publisher


def test_publish_ports_writes_named_values_and_skips_none():
    store = PortScopedStore()
    publish_ports(store, 'c1', 'a',
                  {'context': 'text', 'total': 3, 'missing': None})
    assert store.get_output('c1', 'a', 'context') == 'text'
    assert store.get_output('c1', 'a', 'total') == 3
    assert store.get_output('c1', 'a', 'missing') is None


def test_publish_ports_never_raises_on_bad_store():
    publish_ports(None, 'c1', 'a', {'context': 'x'})     # no store
    publish_ports(object(), 'c1', 'a', {'context': 'x'})  # no set_output
