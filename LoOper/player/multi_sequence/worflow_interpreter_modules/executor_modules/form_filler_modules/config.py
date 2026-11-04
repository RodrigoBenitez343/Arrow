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
            # ── CRAG retrieval (the port in AI/crag) ──
            # ``crag_enabled`` off = one plain embedding pass per field.
            "crag_enabled": _as_bool(data.get("crag_enabled"), True),
            # Backend for the relevance SCORE: 'auto' = Laya (calibrated
            # probability), falling back to embeddings when the engine is
            # unavailable; 'laya' / 'embed' / 'llm' force one of them.
            "crag_evaluator": (
                str(data.get("crag_evaluator") or "auto").strip().lower()
                or "auto"),
            # The two CRAG thresholds in the active backend's score space:
            # >= upper = CORRECT, >= lower = AMBIGUOUS, below both = INCORRECT.
            "crag_upper_threshold": _as_float(
                data.get("crag_upper_threshold"), 0.60),
            "crag_lower_threshold": _as_float(
                data.get("crag_lower_threshold"), 0.40),
            # Decompose-then-recompose mode: selection | excerption | fixed_num.
            "crag_decompose_mode": (
                str(data.get("crag_decompose_mode") or "excerption")
                .strip().lower() or "excerption"),
            # Passages retrieved + scored per field, and the strip scoring
            # budget (one evaluator forward each).
            "crag_top_k": _as_int(data.get("crag_top_k"), 6),
            "crag_max_strips": _as_int(data.get("crag_max_strips"), 12),
            # Strips kept from the internal knowledge / from the corrective
            # store (upstream's two top-n values; selection mode keeps 3).
            "crag_top_n_internal": _as_int(
                data.get("crag_top_n_internal"), 6),
            "crag_top_n_external": _as_int(
                data.get("crag_top_n_external"), 5),
            # The corrective stage, and the query rewrite it feeds on.
            "crag_external": _as_bool(data.get("crag_external"), True),
            "crag_rewrite": _as_bool(data.get("crag_rewrite"), True),
            # CONSOLIDATION CYCLES per field.  A sweep that comes back
            # incorrect/ambiguous has only shown where the answer is NOT: the
            # next one is handed what the last already scored, so it searches
            # what nothing has looked at yet.  A CEILING, not a quota - the
            # loop stops as soon as a sweep is `correct` or runs out of fresh
            # material, so the common field still costs ONE sweep.
            "crag_cycles": _as_int(data.get("crag_cycles"), 2),
            # Cap on the material handed to the extractor.
            "crag_char_budget": _as_int(data.get("crag_char_budget"), 4000),
            "verify": _as_bool(data.get("verify"), True),
            "answer_no": _as_bool(data.get("answer_no"), True),
            # LAST-RESORT skip policy: a field that genuinely CANNOT be answered
            # (a text box asking for a photo / a file, an off-topic option list,
            # or a source with nothing about it) is written 'N/A' instead of
            # being left blank - so a required unanswerable field stops the
            # wizard from re-entering the node forever.  Strictly a LAST RESORT:
            # it is only reached once ask_user and answer_no produced nothing.
            "answer_na": _as_bool(data.get("answer_na"), True),
            # UNSURE -> ASK.  A field the pipeline cannot decide is the USER's to
            # answer - never a guess and never a silent skip.  The answer is
            # learned into the wired Context pool, so a later run retrieves it
            # instead of asking again.  ON by default; the toggle exists to turn
            # it OFF for unattended runs.
            "ask_user": _as_bool(data.get("ask_user"), True),
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
            # CONFIDENCE GATE for a mechanically-reused (learned/cached) answer.
            # A learned answer is written verbatim only when the match is at
            # least this confident AND the page did not reject it; otherwise the
            # node FABRICATES a fresh answer from the WHOLE context pool instead
            # of repeating the stored one.  ``confidence_source`` picks the
            # signal: 'laya' uses the calibrated noul probability (falling back
            # to the embedding cosine), 'cosine' uses the embedding match only.
            "reuse_confidence": _as_float(data.get("reuse_confidence"), 0.82),
            "confidence_source": (
                str(data.get("confidence_source") or "laya").strip().lower()
                or "laya"),
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
