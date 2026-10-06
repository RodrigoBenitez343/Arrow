"""Node config parsing and graph helpers."""


from .common import (
    _as_bool,
    _as_float,
    _as_int,
    _split_list,
)


class FormFillerConfigMixin:
    """Node config parsing and graph helpers."""

    def _ff_cfg(self, data):
        """Normalise the node config dict (string-or-typed tolerant)."""
        return {
            "mode": (str(data.get("mode") or "web")).strip().lower() or "web",
            "instruction": str(data.get("instruction") or ""),
            "fields_include": _split_list(data.get("fields_include")),
            "fields_skip": _split_list(data.get("fields_skip")),
            "probe_top_k": _as_int(data.get("probe_top_k"), 3),
            "probe_char_budget": _as_int(data.get("probe_char_budget"), 1500),
            "probe_context_chars": _as_int(data.get("probe_context_chars"), 6000),
            "verify": _as_bool(data.get("verify"), True),
            "answer_no": _as_bool(data.get("answer_no"), True),
            # LAST-RESORT skip policy: a field that genuinely CANNOT be answered
            # (a text box asking for a photo / a file, an off-topic option list,
            # or a source with nothing about it) is written 'N/A' instead of
            # being left blank - so a required unanswerable field stops the
            # wizard from re-entering the node forever.  Strictly a LAST RESORT:
            # it is only reached once ask_user and answer_no produced nothing.
            "answer_na": _as_bool(data.get("answer_na"), True),
            # Ask the user directly (the same blocking callback Input nodes use)
            # when nothing grounds a field, and learn the answer into the
            # node-owned corrections file so a later run retrieves it instead
            # of asking.
            "ask_user": _as_bool(data.get("ask_user"), False),
            # Post-pass: re-check every written field's NATIVE validity and
            # correct the ones the page rejected (format / constraint).
            "repair": _as_bool(data.get("repair"), True),
            "repair_attempts": _as_int(data.get("repair_attempts"), 2),
            "max_fields": _as_int(data.get("max_fields"), 40),
            # Loop guard: the chain re-enters this node whenever a field does not
            # land, so a required field that can NEVER land loops the wizard
            # unboundedly (live: 51 identical passes burned ~10.7h on one page).
            # When the per-field OUTCOME is identical this many consecutive
            # times the node ends the chain instead of re-entering again.
            # 0 disables the cap.
            "max_stall_passes": _as_int(data.get("max_stall_passes"), 6),
            # ComoRAG consolidation (iterative multi-cycle probing) is ON by
            # default; turn it OFF to rely on one single-pass retrieval per
            # field (lighter - for a bigger model that needs less scaffolding).
            "consolidate": _as_bool(data.get("consolidate"), True),
            "probe_cycles": _as_int(data.get("probe_cycles"), 3),
            "engine": str(data.get("engine") or "llamacpp").strip().lower(),
            "model": str(data.get("model") or ""),
            "temperature": _as_float(data.get("temperature"), 0.1),
            # A "little longer" field (a summary, a why-this-role answer) needs
            # headroom; 256 was exhausted by reasoning models before the value.
            # This is a budget for the FINAL ANSWER only — the engine adds the
            # chain of thought's own headroom on top (see
            # ``_budget_for_final_answer``), so 1024 leaves room for a real
            # paragraph without paying for the reasoning twice.
            "max_tokens": _as_int(data.get("max_tokens"), 1024),
            # Explicit context window (0 = engine auto/RAM-aware).  Naming a
            # value overrides a RAM cap that can otherwise reject every prompt.
            "context_size": _as_int(data.get("context_size"), 0),
            "typing_batch_size": _as_int(data.get("typing_batch_size"), 20),
            "typing_batch_delay": _as_float(data.get("typing_batch_delay"), 0.05),
            # Picked page-scope container (web only): ``""`` = whole document.
            "web_scope": data.get("web_scope") or "",
        }

    def _ff_node_id(self, node):
        data = node.get("data", {}) or {}
        return (
            node.get("id") or node.get("node_id")
            or data.get("node_id") or data.get("id")
        )

    def _ff_next_node(self, node):
        conns = node.get("connections", {}) or {}
        out = conns.get("output") or []
        if out:
            return out[0].get("node_id") or "__done__"
        return "__done__"

    def _ff_gather_source_text(self, node):
        """Concatenate the upstream inputs' outputs/context (the source docs).

        Prefers the ``ctx_in`` DATA PORT - the Context pool's ``ctx_out`` the
        node was wired to - so the source travels over the CONTEXT ports rather
        than the exec-edge variables.  A Context node is PASSIVE (it serves, it
        never runs in the exec graph), so it is PULLED here on demand via
        ``_ctx_serve`` instead of waiting for an execution that never happens.
        ``node_{fid}_output`` / ``node_{fid}_context`` stay as the fallback for
        an upstream that only publishes those.
        """
        parts = []
        _cid = (getattr(self, 'chain_id', None)
                or getattr(self, 'chain_file', '') or '')
        _graph = getattr(self, 'workflow_graph', {}) or {}
        for inp in node.get("inputs", []) or []:
            fid = inp.get("from_node")
            if not fid:
                continue
            val = None
            if str(inp.get("input_port") or "") in ("ctx_in", "context"):
                _src_type = (_graph.get(fid, {}) or {}).get('type')
                if _src_type == 'context':
                    # Passive pull: materialize the pool (documents + stored
                    # rows + learned facts) on demand - no execution needed.
                    try:
                        val = self._ctx_serve(fid)
                    except Exception:
                        val = None
                else:
                    try:
                        _oport = (inp.get("output_type")
                                  or inp.get("output_port") or "ctx_out")
                        val = self.port_store.get_output(_cid, fid, _oport)
                    except Exception:
                        val = None
            if val:
                parts.append(str(val))
                continue
            for key in (f"node_{fid}_output", f"node_{fid}_context"):
                try:
                    v = self.llm_executor.get_variable(key)
                except Exception:
                    v = None
                if v:
                    parts.append(str(v))
        return "\n\n".join(parts).strip()
