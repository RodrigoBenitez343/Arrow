"""runtime_tools.py — helpers behind the chat-mode runtime binding tools.

These are the ctx callables the ``RuntimeRouter`` (player/builder/devtools.py)
uses during chat-mode dispatch of a built system chain:

- ``read_output_value``: read what an Output node produced (port_store
  first, legacy variable fallback — same order as
  chain_executor._get_node_output).
- ``write_context_value``: stage a note the chain's Context node consumes
  on its next run (context_ops ``_chain_bound_context_text``).
"""

import logging

logger = logging.getLogger(__name__)


def find_output_node_id(chain_config, label):
    """The node_id of an Output node matched by label or variable_name.

    Returns '' when no Output node matches (exact label first, then
    variable_name, then case-insensitive label).
    """
    if not isinstance(chain_config, dict):
        return ''
    nodes = chain_config.get('output_nodes') or []
    if not nodes:
        return ''
    wanted = str(label or '').strip()
    if not wanted:
        return ''
    for key in ('label', 'variable_name'):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            data = node.get('data', {}) if isinstance(node.get('data'), dict) else node
            if str(data.get(key) or '').strip() == wanted:
                return str(data.get('node_id') or node.get('node_id') or '')
    wanted_l = wanted.lower()
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get('data', {}) if isinstance(node.get('data'), dict) else node
        if str(data.get('label') or '').strip().lower() == wanted_l:
            return str(data.get('node_id') or node.get('node_id') or '')
    return ''


def read_output_value(executor, chain_config, label):
    """Read an Output node's value (str) or None when unavailable."""
    if executor is None:
        return None
    node_id = find_output_node_id(chain_config, label)
    if not node_id:
        return None
    port_store = getattr(executor, 'port_store', None)
    if port_store is not None:
        try:
            chain_id = getattr(executor, 'chain_id', '') or ''
            val = port_store.get_output(chain_id, node_id, 'output')
            if val is not None:
                return str(val) if not isinstance(val, str) else val
        except Exception:
            pass
    try:
        llm_ex = getattr(executor, 'llm_executor', None)
        if llm_ex is not None:
            val = llm_ex.get_variable(f"node_{node_id}_output")
            if val is not None:
                return str(val) if not isinstance(val, str) else val
    except Exception:
        pass
    return None


def write_context_value(executor, text):
    """Stage a note for the chain's Context node (one-shot consume).

    Returns 'ok' or a deterministic error string; never raises.
    """
    if executor is None:
        return 'no executor available'
    try:
        llm_ex = getattr(executor, 'llm_executor', None)
        if llm_ex is None:
            return 'no llm_executor available'
        prev = llm_ex.get_variable('_chain_bound_context_text') or ''
        merged = (str(prev).strip() + '\n' + str(text).strip()).strip()
        llm_ex.set_variable('_chain_bound_context_text', merged)
        return 'ok'
    except Exception as e:
        logger.warning('write_context_value failed: %s', e)
        return 'failed: %s' % e
