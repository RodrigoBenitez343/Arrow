from __future__ import annotations

"""
port_store.py -- Port-scoped variable store for connection-driven data flow.

Replaces the flat ``self.llm_executor.variables`` dict with a structured store
keyed by ``(chain_id, node_id, port_name)``.  Values are only accessible
through graph connections, preventing the contamination problems that plague
a shared global namespace.

Two classes are provided:

* **PortScopedStore** -- the primary store, used by all node executors.
* **VariableShim** -- backward-compatibility shim that delegates
  ``node_{id}_*`` keys to a ``PortScopedStore`` and everything else to a
  plain fallback dict.  Keeps existing code working during migration.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Base ports drive execution ONLY — they carry NO data.  A source publishing
# under one of these is not a data channel, so connection resolution skips it.
EXEC_ONLY_OUTPUT_PORTS = frozenset(
    {'', 'output', 'true', 'false', 'error', 'route'}
)


class PortScopedStore:
    """Scoped storage for per-node port outputs.

    Keys are ``(chain_id, node_id, port_name)`` tuples.  Unlike a flat
    namespace, there is no way to enumerate all values or read another
    node's output by accident -- you must know the exact triple.

    Turn history is stored in-memory per chain and provides a structured
    record of agent conversation turns for small-model-friendly context
    injection.
    """

    def __init__(self) -> None:
        # (chain_id, node_id, port_name) -> value
        self._store: dict[tuple[str, str, str], Any] = {}
        # chain_id -> [turn_data_dict, ...]
        self._turn_history: dict[str, list[dict]] = {}
        # chain_id -> (node_id -> turn_counter)
        self._turn_counters: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------
    # Port output management
    # ------------------------------------------------------------------

    def set_output(self, chain_id: str, node_id: str,
                   port: str, value: Any) -> None:
        """Store a node's output for a specific port."""
        self._store[(chain_id, node_id, port)] = value

    def get_output(self, chain_id: str, node_id: str,
                   port: str = "output") -> Any:
        """Retrieve a node's output for a specific port.

        Returns ``None`` if no value has been stored (never executed /
        no connection matched).
        """
        return self._store.get((chain_id, node_id, port))

    def get_node_ports(self, chain_id: str, node_id: str) -> dict:
        """All published port values for a node: ``{port_name: value}``.

        Consumers that need every exposed output of a producer (e.g. the
        context node reading a chain import's named data ports) use this
        instead of guessing individual port names.
        """
        return {
            port: val
            for (ch, nid, port), val in self._store.items()
            if ch == chain_id and nid == node_id
        }

    # ------------------------------------------------------------------
    # Connection-driven input resolution
    # ------------------------------------------------------------------

    def resolve_connection(self, node: dict, port_name: str,
                           chain_id: str) -> Any:
        """Follow graph connections to resolve an input port value.

    Given a *node* dict and one of its declared input *port_name*\s,
    find which upstream node is connected via the ``inputs`` array
    and return that upstream node's output.

    Returns ``None`` when there is no connection for the given port,
    or the upstream node has no output yet.  **No fallback** -- no
    graph scanning, no variable-name pattern matching.
    """
        for inp in node.get("inputs", []):
            if inp.get("input_port") == port_name:
                from_node = inp.get("from_node")
                output_port = (
                    inp.get("output_type")
                    or inp.get("output_port")
                    or "output"
                )
                if str(output_port or "") in EXEC_ONLY_OUTPUT_PORTS:
                    return None
                return self.get_output(chain_id, from_node, output_port)
        return None

    def resolve_connection_list(self, node: dict, chain_id: str,
                                dedup: bool = True) -> list[tuple[str, str, Any]]:
        """Resolve ALL input connections of a node.

        Returns a list of ``(from_node_id, input_port_name, value)`` tuples,
        in the order they appear in the node's ``inputs`` array.  When
        *dedup* is ``True`` (default), duplicate ``from_node_id`` entries
        are skipped so the same upstream is only listed once.

        Useful for Context nodes and Output nodes that collect from all
        upstream sources.
        """
        results: list[tuple[str, str, Any]] = []
        seen: set[str] = set()
        for inp in node.get("inputs", []):
            from_node = inp.get("from_node")
            if not from_node:
                continue
            input_port = str(inp.get("input_port", ""))
            # Base ports carry no data — neither the base 'input' target nor
            # an exec-only source port is a data channel.
            if input_port in ("", "input"):
                continue
            output_port = (
                inp.get("output_type")
                or inp.get("output_port")
                or "output"
            )
            if str(output_port or "") in EXEC_ONLY_OUTPUT_PORTS:
                continue
            if dedup and from_node in seen:
                continue
            seen.add(from_node)
            val = self.get_output(chain_id, from_node, output_port)
            results.append((from_node, input_port, val))
        return results

    # ------------------------------------------------------------------
    # Chain-level cleanup
    # ------------------------------------------------------------------

    def clear_chain(self, chain_id: str) -> int:
        """Remove all port entries for a chain.

        Returns the number of entries removed (useful for validation).
        """
        keys = [k for k in self._store if k[0] == chain_id]
        for k in keys:
            del self._store[k]
        self._turn_history.pop(chain_id, None)
        self._turn_counters.pop(chain_id, None)
        return len(keys)

    def clear_node(self, chain_id: str, node_id: str,
                   port: str | None = None) -> int:
        """Remove entries for a specific node (and optionally port).

        When *port* is ``None``, all ports for the node are cleared.
        Returns the number of entries removed.
        """
        if port is not None:
            key = (chain_id, node_id, port)
            if key in self._store:
                del self._store[key]
                return 1
            return 0
        keys = [k for k in self._store
                if k[0] == chain_id and k[1] == node_id]
        for k in keys:
            del self._store[k]
        return len(keys)

    # ------------------------------------------------------------------
    # Turn history (in-memory, per chain)
    # ------------------------------------------------------------------

    def _next_turn_number(self, chain_id: str, node_id: str) -> int:
        """Return the next turn number for a chain+node pair."""
        counters = self._turn_counters.setdefault(chain_id, {})
        counters[node_id] = counters.get(node_id, 0) + 1
        return counters[node_id]

    def add_turn(self, chain_id: str, node_id: str,
                 turn_data: dict, max_turns: Optional[int] = None) -> dict:
        """Append a turn record.

        The *turn_data* dict is augmented with ``turn`` number and
        ``context_node_id``, then stored.  When *max_turns* is set, only the
        newest *max_turns* records for THIS node are kept — older turns are
        dropped so the in-memory history stays bounded ("oldest turns dropped
        by turn-count").  Returns the enriched dict.
        """
        turn = dict(turn_data)
        turn["turn"] = self._next_turn_number(chain_id, node_id)
        turn["context_node_id"] = node_id
        history = self._turn_history.setdefault(chain_id, [])
        history.append(turn)
        if max_turns is not None and max_turns > 0:
            # Node-aware trim: drop the oldest turns of THIS node beyond the
            # cap; other nodes' turns in the same chain are untouched.
            node_idx = [i for i, t in enumerate(history)
                        if t.get("context_node_id") == node_id]
            if len(node_idx) > max_turns:
                excess = len(node_idx) - max_turns
                drop = set(node_idx[:excess])
                history[:] = [t for i, t in enumerate(history)
                              if i not in drop]
        return turn

    def get_turns(self, chain_id: str,
                  node_id: str | None = None,
                  limit: int = 10) -> list[dict]:
        """Get the most recent turn records.

        When *node_id* is provided, only turns for that node are returned.
        When ``None``, all turns for the chain are returned.
        """
        all_turns = self._turn_history.get(chain_id, [])
        if node_id is not None:
            all_turns = [t for t in all_turns if t.get("context_node_id") == node_id]
        return all_turns[-limit:]

    def clear_turns(self, chain_id: str) -> None:
        """Remove all turn history for a chain."""
        self._turn_history.pop(chain_id, None)
        self._turn_counters.pop(chain_id, None)


# ------------------------------------------------------------------
# Backward-compatibility shim
# ------------------------------------------------------------------


class VariableShim:
    """Compatibility layer between old ``get_variable`` / ``set_variable``
    calls and the new ``PortScopedStore``.

    ``node_{id}_output`` and ``node_{id}_context`` keys are delegated to
    the port store.  Everything else (runtime callbacks, internal flags)
    lives in a plain fallback dict.
    """

    _NODE_KEY_PATTERNS = {
        "_output": "output",
        "_context": "context",
        "_input_context": "input_context",
        "_output_meta": "output_meta",
        "_last_chain_result": "last_chain_result",
        "_is_input_passthrough": "is_input_passthrough",
    }

    def __init__(self, port_store: PortScopedStore,
                 chain_id: str = "",
                 fallback: dict | None = None) -> None:
        self._port = port_store
        self._chain_id = chain_id
        self._fallback: dict = {} if fallback is None else fallback

    @property
    def chain_id(self) -> str:
        return self._chain_id

    @chain_id.setter
    def chain_id(self, value: str) -> None:
        self._chain_id = value

    def _parse_node_key(self, key: str) -> tuple[str, str, str] | None:
        """If *key* looks like ``node_{id}_{suffix}``, return
        ``(chain_id, node_id, port_name)``.  Otherwise ``None``."""
        for suffix, port_name in self._NODE_KEY_PATTERNS.items():
            if key.startswith("node_") and key.endswith(suffix):
                # Extract node_id between "node_" and suffix
                middle = key[len("node_"):-len(suffix)]
                if middle:
                    return (self._chain_id, middle, port_name)
        return None

    def get_variable(self, key: str, default: Any = None) -> Any:
        parsed = self._parse_node_key(key)
        if parsed is not None:
            _, node_id, port_name = parsed
            val = self._port.get_output(*parsed)
            return val if val is not None else default
        return self._fallback.get(key, default)

    def set_variable(self, key: str, value: Any) -> None:
        parsed = self._parse_node_key(key)
        if parsed is not None:
            self._port.set_output(*parsed, value)
        else:
            self._fallback[key] = value

    @property
    def variables(self) -> dict:
        """Return a combined view (read-only).  Used by legacy code that
        iterates over all variables (e.g. chain_ops.py _clean_vars)."""
        combined = dict(self._fallback)
        # Reconstruct node_* keys from port store
        for (ch, nid, port), val in self._port._store.items():
            if ch == self._chain_id:
                for suffix, pname in self._NODE_KEY_PATTERNS.items():
                    if port == pname:
                        combined[f"node_{nid}{suffix}"] = val
                        break
        return combined

    def __contains__(self, key: str) -> bool:
        return self.get_variable(key) is not None
