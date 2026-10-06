import datetime
import json
import logging
import os
logger = logging.getLogger(__name__)
try:
    from logging_setup import log_block
except Exception:
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)


class ContextMixin:
    """Port-scoped context node execution — turn-based history + SQLite persistence.

    Two memory scopes (configured via node ``scope`` property):
    - ``"global"`` — visible across ALL chains (system-chain knowledge)
    - ``"local"``  — visible only within this chain (tool-chain state)

    **clear_on_finish** nodes (used in the system chain loop) accumulate
    a turn-based history via ``PortScopedStore``.  Each loop iteration
    produces one turn record with the user query, tool executed, and
    result.  Oldest turns are dropped by turn-count, not by character
    truncation — giving predictable context size for small LLMs.

    Non-**clear_on_finish** nodes persist data to SQLite via
    ``ContextDatabase`` for cross-session memory.
    """

    # Maximum number of turns retained for clear_on_finish nodes.
    # 0 = UNLIMITED: the pool keeps its WHOLE history (no entry cap).  A node
    # can still bound what it SERVES by setting its own max_history.
    _MAX_TURNS = 0

    # Namespace for propagated context copies shared between chains
    # (keyed by "<source_node_id>:<clone_node_id>" — chain-id independent
    # so GUI temp-chain runs resolve identically).  No chain's pre-init
    # touches it; a clone's copy is deleted only when ITS chain executes
    # with clear_on_finish toggled.
    _SHARED_COPY_NS = "__shared_copy__"

    def _get_context_db(self):
        """Lazy-init the shared ContextDatabase instance."""
        db = getattr(self, '_context_db', None)
        if db is None:
            from AI.context_database import ContextDatabase
            db = ContextDatabase()
            self._context_db = db
        return db

    def _get_chain_description(self) -> str:
        """Get the current chain's description for semantic key derivation."""
        # Try the executor-level description first (set by core.py at init)
        desc = getattr(self, '_chain_description', None)
        if desc:
            return str(desc)
        # Fall back to the chain config description from variables
        desc = self.llm_executor.get_variable("_chain_description") or ""
        return str(desc) if desc else ""

    def _resolve_turn_query(self, upstream_data) -> tuple:
        """Best-effort user-prompt text for a context turn.

        Resolution order:
        1. A direct Input-type upstream's value (its data port).
        2. One hop through llm-type upstreams — the Input nodes that fed the
           router (system-chain pattern: the context node only sees the
           router's output, never the user's prompt).
        3. Legacy fallback: the first non-empty upstream value.

        Returns ``(query_text, source_label)``; ``('', '')`` when nothing
        resolves.
        """
        graph = self.workflow_graph or {}
        chain_id = getattr(self, 'chain_id', '') or ''

        def _input_data_value(src_id):
            val = None
            try:
                val = self.port_store.get_output(chain_id, src_id, 'data')
            except Exception:
                val = None
            if val is None:
                try:
                    val = self.llm_executor.get_variable(f"node_{src_id}_data")
                except Exception:
                    val = None
            if val is None or not str(val).strip():
                return ""
            return str(val).strip()

        # 1) Direct Input-type upstreams.
        for fid, _port, fval in upstream_data:
            if (graph.get(fid, {}) or {}).get('type') != 'input':
                continue
            v = str(fval).strip() if fval is not None else ""
            if v:
                return v, f"input-upstream:{fid}"

        # 2) One hop through llm-type upstreams -> their Input-type inputs.
        for fid, _port, _fval in upstream_data:
            unode = graph.get(fid, {}) or {}
            if unode.get('type') != 'llm':
                continue
            for inp in unode.get('inputs', []) or []:
                src = inp.get('from_node')
                if not src:
                    continue
                if (graph.get(src, {}) or {}).get('type') != 'input':
                    continue
                v = _input_data_value(src)
                if v:
                    return v, f"via-router-input:{src}"

        # 3) Legacy fallback: first non-empty upstream value.
        for fid, _port, fval in upstream_data:
            v = str(fval).strip() if fval is not None else ""
            if v:
                return v, f"first-upstream:{fid}"
        return "", ""

    def _pull_shared_feed(self, context_db, identity: str,
                          exclude_node_id: str, limit: int) -> str:
        """Read the shared identity feed for *identity*, minus own entries.

        Feed rows are written by every participant (the original node and each
        clone that references it) with ``source_node_id`` = the writing node's
        own id, so a reader excludes its own rows (they are already part of its
        local output) and merges everybody else's - the bidirectional link that
        lets a subchain's context reach the node it was copied from.
        """
        try:
            vals = context_db.pull_channel(
                chain_id=self._SHARED_COPY_NS,
                node_id=str(identity),
                exclude_source_node_id=exclude_node_id,
                limit=int(limit),
            )
        except Exception:
            return ""
        parts = [
            f"[shared] {str(v).strip()}"
            for v in vals if v and str(v).strip()
        ]
        return "\n\n".join(parts)

    @staticmethod
    def _resolve_clone_source(shared_chain_file: str, ref_node_id: str,
                              label: str, scope: str) -> str:
        """Resolve the SOURCE context node id a clone inherits from.

        Priority: explicit reference id -> label match in the referenced
        chain -> scope match -> the single context node of that chain.
        Returns '' when no source can be resolved.
        """
        if ref_node_id:
            return ref_node_id
        try:
            if not shared_chain_file or not os.path.exists(shared_chain_file):
                return ''
            with open(shared_chain_file, 'r', encoding='utf-8') as _f:
                _cfg = json.load(_f)
            _nodes = _cfg.get('context_nodes') or []
            if not _nodes:
                return ''
            _match = None
            if label:
                for _sn in _nodes:
                    if str(_sn.get('label') or '').strip() == label:
                        _match = _sn
                        break
            if _match is None:
                _sc = str(scope or 'local').lower()
                for _sn in _nodes:
                    if str(_sn.get('scope') or 'local').lower() == _sc:
                        _match = _sn
                        break
            if _match is None and len(_nodes) == 1:
                _match = _nodes[0]
            if _match is None:
                return ''
            return str(_match.get('node_id') or _match.get('id') or '').strip()
        except Exception:
            return ''

    def _get_chains_folder(self) -> str:
        """Resolve the chains library folder (mirrors NGUI graph_view)."""
        try:
            import sys as _sys
            if getattr(_sys, 'frozen', False):
                return os.path.join(os.path.dirname(_sys.executable), 'chains')
            # context_ops.py -> executor_modules -> worflow_interpreter_modules
            # -> multi_sequence -> player -> LoOper
            _root = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )))
            return os.path.join(_root, 'chains')
        except Exception:
            return ''

    def _get_shared_clone_index(self) -> dict:
        """Scan the chains library once per run for clone context nodes.

        Returns ``{source_node_id: [(clone_node_id, clone_chain_name), ...]}``
        so a source context node can push a copy of its output to every
        clone that references it (subid + reference design).  Clones without
        an explicit reference resolve their source by label/scope so legacy
        shared_context_chain_file nodes keep working.
        """
        idx = getattr(self, '_shared_clone_index', None)
        if idx is not None:
            return idx
        idx = {}
        _folder_scanned = ""
        try:
            folder = self._get_chains_folder()
            _folder_scanned = str(folder or "")
            if folder and os.path.isdir(folder):
                for _f in os.listdir(folder):
                    if not _f.lower().endswith('.json'):
                        continue
                    try:
                        with open(os.path.join(folder, _f), 'r', encoding='utf-8') as _fh:
                            _cdata = json.load(_fh)
                        for _ctx in (_cdata.get('context_nodes') or []):
                            _sfile = str(_ctx.get('shared_context_chain_file') or '').strip()
                            if not _sfile:
                                continue
                            _cnid = str(_ctx.get('node_id') or _ctx.get('id') or '').strip()
                            if not _cnid:
                                continue
                            _src_id = ContextMixin._resolve_clone_source(
                                _sfile,
                                str(_ctx.get('shared_context_node_id') or '').strip(),
                                str(_ctx.get('label') or '').strip(),
                                str(_ctx.get('scope') or 'local'),
                            )
                            if _src_id:
                                idx.setdefault(_src_id, []).append(
                                    (_cnid, os.path.splitext(_f)[0])
                                )
                    except Exception:
                        continue
        except Exception as _e:
            logger.warning("Shared clone index scan failed: %s", _e)
        _clone_summary = {_src: [c[1] for c in _cl] for _src, _cl in idx.items()}
        logger.info(
            "[CTX] Shared-clone index scan: folder=%r sources=%d -> %s",
            _folder_scanned, len(idx), _clone_summary,
        )
        self._shared_clone_index = idx
        return idx

    # ------------------------------------------------------------------
    # Turn formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_turn(turn: dict) -> str:
        """Format a single turn record into human-readable text."""
        parts = []
        parts.append(f"[Turn {turn.get('turn', '?')}]")
        query = turn.get('query', '')
        if query:
            parts.append(f"User query: {query}")
        decision = turn.get('decision', '')
        if decision:
            parts.append(f"Decision: {str(decision)[:500]}")
        tool = turn.get('tool', '')
        if tool:
            # FIRST PERSON, deliberately: the LLM reading these turns IS the
            # actor.  A third-person label plus a result-less "name: what it
            # does" line read as a tool CATALOGUE, and the summariser
            # answered with a description of the tools instead of a report of
            # what it had done.
            parts.append(f"Tool I ran: {tool}")
        result = turn.get('result', '')
        if result:
            parts.append(f"What it did: {str(result)[:500]}")
        summary = turn.get('summary', '')
        if summary:
            parts.append(f"Summary: {str(summary)[:500]}")
        return "\n".join(parts)

    @staticmethod
    def _already_reported(record, seen_seq) -> bool:
        """True when a chain-tool record belongs to an EARLIER turn.

        Records are keyed by TOOL and live for the whole run, and the turn text
        is built from them — so without this every later turn re-reports every
        tool that ever ran (observed: turn 2 listing turn 1's CLOSE tool, and
        the summariser describing tools it had not used).  chain_ops stamps
        each execution with a monotonic seq; anything at or below the last seq
        this node reported is history.  An unstamped record (seq 0) is always
        treated as current: never drop information on a missing mark.
        """
        if not isinstance(record, dict):
            return False
        try:
            seq = int(record.get("seq") or 0)
        except Exception:
            return False
        return bool(seq) and seq <= int(seen_seq or 0)

    @staticmethod
    def _tool_outcome(record) -> str:
        """What a tool chain DID, from its recorded last-node summary.

        The tool's RETURN VALUE — every Output node the sub-chain ran — wins
        when present, because that is what the tool produced; the last-node
        summary only describes the final step.  A recorded-action chain
        (sequence, web, form filler, handle) has no result slot at all, so "it
        completed" is then the only honest outcome — and saying so is what the
        summariser was missing.
        """
        if not isinstance(record, dict):
            return ""
        _collected = str(record.get("tool_output") or "").strip()
        if _collected:
            return _collected
        summary = record.get("last_node") or {}
        if not isinstance(summary, dict):
            return ""
        _type = str(summary.get("type") or "")
        _result = summary.get("result")
        if _type in ("output", "code") and _result:
            return str(_result)
        if _type == "llm" and isinstance(_result, dict):
            return str(_result.get("response") or "")
        if _type == "conditional":
            return "condition evaluated to %s" % bool(_result)
        if _type:
            return "ran successfully (%s)" % _type
        return ""

    @staticmethod
    def _format_turns_for_llm(turns: list[dict]) -> str:
        """Format a list of turns into a single structured string."""
        if not turns:
            return ""
        return "\n---\n".join(
            ContextMixin._format_turn(t) for t in turns
        )

    def _execute_context_node(self, node, stop_flag):
        """Execute a context node: collect inputs, store, and output recent entries.

        Two execution paths:

        * **clear_on_finish** (system chain loop): builds a turn record from
          connected upstream ports and appends it to the in-memory turn history.
          Output is the last N turns formatted as structured text.

        * **persistent** (tool-chain memory): stores upstream data in SQLite
          via ``ContextDatabase`` for cross-session recall.

        Args:
            node (dict): The Context node from the workflow graph.
            stop_flag (callable): Stop flag function.

        Returns:
            str: Next node ID to execute, or None if workflow should end.
        """
        node_data = node.get('data', {})
        node_id = (node.get('id') or node.get('node_id')
                   or node_data.get('node_id') or node_data.get('id'))

        # -- Config --
        label = node_data.get('label', '') or ''
        # 0 = UNLIMITED (no entry cap).  add_turn/get_turns already treat 0 as
        # "no trim / return all"; the SQLite pulls need a real numeric bound.
        try:
            max_history = int(node_data.get('max_history', self._MAX_TURNS) or 0)
        except (TypeError, ValueError):
            max_history = 0
        _pull_limit = max_history if max_history > 0 else 1000000
        scope = str(node_data.get('scope', 'local')).lower()
        if scope not in ("global", "local"):
            scope = "local"

        # -- Derive chain_id from the executor's single-source-of-truth --
        chain_id = getattr(self, 'chain_id', None) or getattr(self, 'chain_file', 'default_chain')
        # Already normalized by core.py, but safe-call in case caller didn't
        try:
            from AI.context_database import ContextDatabase
            chain_id = ContextDatabase.normalize_chain_id(chain_id)
        except Exception:
            pass
        chain_description = self._get_chain_description()

        # Shared-clone config: which chain this node was copied from and the
        # source node id inside it.  Local rows are always stored under THIS
        # node's own chain (own-chain, own-node) - cross-chain sharing happens
        # through the identity feed below, never by writing into the source
        # chain's namespace (which leaked rows the source cleanup could not see).
        shared_chain_file = str(node_data.get('shared_context_chain_file', '') or '')
        effective_chain_id = chain_id
        if shared_chain_file and os.path.exists(shared_chain_file):
            try:
                from AI.context_database import ContextDatabase
                effective_chain_id = ContextDatabase.normalize_chain_id(shared_chain_file)
            except Exception:
                effective_chain_id = os.path.splitext(os.path.basename(str(shared_chain_file)))[0]

        if shared_chain_file:
            logger.info("[CTX] Context node %s (label='%s', scope=%s) shared=%s chain=%s desc=%s",
                         node_id, label, scope, shared_chain_file, effective_chain_id,
                         chain_description[:60] if chain_description else "-")
        else:
            logger.info("[CTX] Context node %s (label='%s', scope=%s) executing chain=%s desc=%s",
                         node_id, label, scope, chain_id,
                         chain_description[:60] if chain_description else "-")

        # -- ContextDatabase backend --
        context_db = self._get_context_db()

        # -- Inherited context copy --
        # A context node in a sub-chain receives a COPY of the source's
        # context.  Two clone flavors are supported:
        #   (a) SAME node id as the parent's context node — import handoff
        #       shared_context_<node_id> (legacy same-id sharing);
        #   (b) subid + reference (shared_context_node_id set on the clone)
        #       — import handoff shared_context_<ref_node_id>, falling back
        #       to the persisted shared copy written by the source chain's
        #       run (namespace __shared_copy__, key "<ref>:<clone_id>").
        # Each chain instance holds its own copy and cleans it when IT
        # finishes — the source's copy is untouched until the source chain
        # itself finishes (clear_on_finish per instance).
        _ref_node_id = ContextMixin._resolve_clone_source(
            shared_chain_file,
            str(node_data.get('shared_context_node_id') or '').strip(),
            str(node_data.get('label') or '').strip(),
            scope,
        )
        _inherited_copy = ""
        _inherited_src = "(none)"
        try:
            _inherited_copy = str(
                self.llm_executor.get_variable(f"shared_context_{node_id}") or ""
            ).strip()
            if _inherited_copy:
                _inherited_src = f"var:shared_context_{node_id}"
        except Exception:
            pass
        if not _inherited_copy and _ref_node_id:
            try:
                _inherited_copy = str(
                    self.llm_executor.get_variable(f"shared_context_{_ref_node_id}") or ""
                ).strip()
                if _inherited_copy:
                    _inherited_src = f"var:shared_context_{_ref_node_id}"
            except Exception:
                pass
        if not _inherited_copy and _ref_node_id:
            try:
                _pulled = context_db.pull(
                    chain_id=self._SHARED_COPY_NS,
                    node_id=f"{_ref_node_id}:{node_id}",
                    limit=_pull_limit,
                )
                _copy_parts = []
                for _k, _vals in (_pulled or {}).items():
                    for _v in _vals:
                        if _v and str(_v).strip():
                            _copy_parts.append(f"[{_k}] {str(_v).strip()}")
                _inherited_copy = "\n\n".join(_copy_parts)
                if _inherited_copy:
                    _inherited_src = f"dbpull:{_ref_node_id}:{node_id}"
            except Exception:
                pass
        if _ref_node_id or _inherited_copy:
            logger.info(
                "[CTX] node %s inherited copy: source=%r ref=%r len=%d chars",
                node_id, _inherited_src, _ref_node_id, len(_inherited_copy),
            )

        # -- Collect data from connected upstream ports (NOT graph scan) --
        upstream_data = self.port_store.resolve_connection_list(node, chain_id)
        pushed_from_nodes = {uid for uid, _, _ in upstream_data}
        if upstream_data:
            logger.debug(
                "[CTX] node %s upstream connections (%d): %s",
                node_id, len(upstream_data),
                [(uid, port, (str(val)[:60] if val is not None else None))
                 for uid, port, val in upstream_data],
            )
        else:
            logger.debug("[CTX] node %s has NO connected upstream data", node_id)

        # Learned-answer entries already stored (key 'learned/<label>'): a
        # repeated push of the SAME answer is NOT duplicated (the pool is
        # append-only otherwise).
        _existing_learned = {}
        try:
            _priorrows = context_db.pull(chain_id=chain_id, node_id=node_id,
                                         limit=_pull_limit)
            for _k, _vs in (_priorrows or {}).items():
                if _k.startswith("learned/") and _vs:
                    _existing_learned[_k] = str(_vs[0])
        except Exception:
            _existing_learned = {}

        # -- Also store each upstream value into SQLite (for queryable history) --
        for from_id, input_port, val in upstream_data:
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            # Determine upstream type for auto-keying
            source_type = 'unknown'
            upstream_node = {}
            if hasattr(self, 'workflow_graph') and self.workflow_graph:
                upstream_node = self.workflow_graph.get(from_id, {}) or {}
                source_type = upstream_node.get('type', 'unknown')
            upstream_data_node = upstream_node.get('data', {}) or {}
            semantic_desc = (upstream_data_node.get('label', '')
                             or upstream_data_node.get('description', '') or '')
            key = f"{source_type}/{semantic_desc}" if semantic_desc else source_type
            value_str = str(val) if val else ""
            # A value carrying a learned-answers block is SPLIT: each answer is
            # stored as its OWN 'learned/<label>' entry (so the audit dialog
            # edits ONE answer at a time) and stripped from the row served as
            # the upstream value.  The block is re-rendered on serve, so a
            # consumer (the form filler) still reads its own format.
            try:
                from .form_filler_modules.common import _ff_split_corrections
                value_str, _learned = _ff_split_corrections(value_str)
            except Exception:
                _learned = {}
            if value_str.strip():
                context_db.push(
                    chain_id=chain_id,
                    node_id=node_id,
                    key=key,
                    value=value_str,
                    source_node_id=from_id,
                    source_type=source_type,
                )
            for _lab, _ans in (_learned or {}).items():
                _lk = "learned/%s" % _lab
                _av = str(_ans)
                if not _av.strip() or _existing_learned.get(_lk) == _av:
                    continue
                context_db.push(
                    chain_id=chain_id, node_id=node_id, key=_lk, value=_av,
                    source_node_id=from_id, source_type='learned',
                )
                _existing_learned[_lk] = _av
            logger.debug("[CTX] Stored [%s] key='%s' from %s (%s)", scope, key, from_id, source_type)

        # Learned entries (key 'learned/<label>') are collected below for the
        # re-render; they never appear as raw rows in the plain output.
        _learned_pairs = {}

        # -- Output --
        clear_on_finish = bool(node_data.get('clear_on_finish', False))
        if clear_on_finish:
            # ── Turn-based accumulation ──
            # Build a turn record from connected upstream data and tool info.
            # No graph scanning for Input nodes — data comes from port connections.

            # Determine user query: try 'query' input port first, then first upstream
            query_text = ""
            _query_src = ""
            query_val = self.port_store.resolve_connection(node, 'query', chain_id)
            if query_val and isinstance(query_val, str) and query_val.strip():
                query_text = query_val.strip()
                _query_src = "query-port"
            else:
                # The user's prompt is not always a direct upstream: in the
                # router pattern the context node only sees the router's
                # output ('USE_TOOL:...'), so look for the Input nodes that
                # fed the router (see _resolve_turn_query).
                query_text, _query_src = self._resolve_turn_query(upstream_data)
            logger.info(
                "[CTX] node %s turn query from %s: %r",
                node_id, _query_src or "(none)", query_text[:120],
            )

            # Router/LLM decision text: any connected upstream produced by an
            # 'llm' node (the router's own message - tool pick, args, code).
            # Without this the turn only records query/tool/result and the
            # final LLM never sees WHY/what the router chose.
            _decision_parts = []
            if hasattr(self, 'workflow_graph') and self.workflow_graph:
                for _fid, _fport, _fval in upstream_data:
                    if _fval is None or not str(_fval).strip():
                        continue
                    _utype = ((self.workflow_graph.get(_fid, {}) or {}).get('type') or '')
                    if _utype == 'llm':
                        _decision_parts.append(str(_fval).strip())
            decision_text = "\n".join(_decision_parts)[:1200]

            # Executed tools: chain_import nodes whose NAMED data ports hold
            # a published value (one port per exposed Output node — the
            # tool's actual result), or that have a recorded chain-tool
            # result.  'selected_by_llm' still counts when set (agent mode).
            # The old bare 'output' port never matched: chain imports publish
            # results on named data ports only.
            _chain_tool_records = {}
            try:
                _chain_tool_records = (
                    self.llm_executor.get_variable("chain_tool_results") or {}
                ) or {}
            except Exception:
                _chain_tool_records = {}
            # What THIS turn executed.  Records (and the named-port values
            # they publish) outlive the turn, so scanning the graph alone made
            # every later turn re-report every tool that ever ran — the
            # summariser then described tools it had NOT used in that turn.
            # chain_ops stamps each record with a monotonic seq; only records
            # newer than the last seq this node reported belong here.
            _seen_key = f"_ctx_turn_seq_{node_id}"
            try:
                _seen_seq = int(
                    self.llm_executor.get_variable(_seen_key) or 0)
            except Exception:
                _seen_seq = 0
            try:
                _head_seq = int(
                    self.llm_executor.get_variable("_tool_exec_seq") or 0)
            except Exception:
                _head_seq = 0
            _executed_tool_nodes = []
            if hasattr(self, 'workflow_graph') and self.workflow_graph:
                for _wnid, _wnode in self.workflow_graph.items():
                    if _wnode.get('type') != 'chain_import':
                        continue
                    if self._already_reported(
                            _chain_tool_records.get(str(_wnid)), _seen_seq):
                        continue        # already reported in an earlier turn
                    _selected = bool(_wnode.get('selected_by_llm'))
                    try:
                        _ports = self.port_store.get_node_ports(chain_id, _wnid)
                    except Exception:
                        _ports = {}
                    _values = [
                        str(_pval).strip()
                        for _pname, _pval in (_ports or {}).items()
                        if _pname != 'output' and _pval is not None
                        and str(_pval).strip()
                    ]
                    _wout = "\n".join(_values) if _values else None
                    if _wout is None:
                        # Legacy aggregate fallback (pre-named-port saves).
                        try:
                            _legacy = self.llm_executor.get_variable(
                                f"node_{_wnid}_output"
                            )
                        except Exception:
                            _legacy = None
                        if _legacy is not None and str(_legacy).strip():
                            _wout = str(_legacy).strip()
                    _has_record = str(_wnid) in (_chain_tool_records or {})
                    if _selected or _wout is not None or _has_record:
                        _executed_tool_nodes.append((_wnid, _wnode, _wout))

            # Tool descriptions as the router saw them: llm_ops enriches the
            # executing LLM node's llm_configuration.tool_descriptions map at
            # prompt time - reuse it instead of only re-reading chain files
            # (a tool chain may have no description field, or the file may not
            # resolve on the executing machine).
            _tool_desc_map = {}
            try:
                for _n in (self.workflow_graph or {}).values():
                    if _n.get('type') != 'llm':
                        continue
                    _lc = ((_n.get('data', {}) or {}).get('llm_configuration', {}) or {})
                    _td = _lc.get('tool_descriptions') or {}
                    if isinstance(_td, str):
                        try:
                            _td = json.loads(_td)
                        except Exception:
                            _td = {}
                    if isinstance(_td, dict):
                        _tool_desc_map.update(_td)
            except Exception:
                pass

            def _tool_desc(wnode_id, wnode, chain_file, tool_name):
                """Chain-file description first, then the router prompt map."""
                desc = ""
                if chain_file and os.path.exists(chain_file):
                    try:
                        with open(chain_file, 'r', encoding='utf-8') as _f:
                            _cdata = json.loads(_f.read())
                        desc = (_cdata.get('description') or '').strip()
                    except Exception:
                        pass
                elif chain_file:
                    logger.warning(
                        "[CTX] tool chain file for %s NOT FOUND on disk: %s",
                        tool_name, chain_file,
                    )
                if not desc:
                    for _k in (wnode_id, tool_name, f"<{tool_name}>"):
                        _v = str(_tool_desc_map.get(_k) or '').strip()
                        if _v:
                            desc = _v
                            break
                return desc

            tool_lines = []
            for _wnid, _wnode, _wout in _executed_tool_nodes:
                _wdata = _wnode.get('data', {}) or {}
                _chain_file = (
                    _wdata.get('chain_file_path', '')
                    or _wdata.get('chain_file', '')
                )
                _prefix = _wdata.get('prefix', '') or ''
                _tool_name = (
                    _prefix
                    or os.path.splitext(os.path.basename(_chain_file or ''))[0]
                    or _wnid
                )
                _desc = _tool_desc(_wnid, _wnode, _chain_file, _tool_name)
                logger.info(
                    "[CTX] executed tool candidate %s: name=%r desc=%d chars file=%s",
                    _wnid, _tool_name, len(_desc), _chain_file or "-",
                )
                tool_lines.append(
                    f"{_tool_name}: {_desc}" if _desc else f"{_tool_name}"
                )
            if not _executed_tool_nodes:
                logger.info(
                    "[CTX] node %s: no executed chain_import tools detected", node_id,
                )

            # A tool selection is already reported by the 'tool' line: the
            # executor publishes the tool's DESCRIPTION as the LLM output
            # (routing ids and aliases never surface), so don't repeat it as
            # the turn's decision.  Match the description part of the tool
            # line too, so a longer published text (appended fields/args)
            # still dedups.
            _dd = decision_text.strip()
            _tool_desc_parts = [_tl.partition(': ')[2] for _tl in tool_lines]
            if _dd and tool_lines and (
                _dd == " | ".join(tool_lines)
                or any(_dd == _tl for _tl in tool_lines)
                or any(_d and _dd.startswith(_d) for _d in _tool_desc_parts)
            ):
                decision_text = ""

            # Build turn record
            turn_record = {
                "context_node_id": node_id,
                "query": query_text,
                "decision": decision_text,
                "tool": " | ".join(tool_lines) if tool_lines else "",
                "result": "",
                "summary": "",
            }

            # Collect tool execution output if available.  A recorded-action
            # tool publishes nothing on named ports, and that empty result is
            # what let the summariser recite tool DESCRIPTIONS as if they were
            # outcomes — so fall back to the record's own last-node outcome.
            if _executed_tool_nodes:
                result_parts = []
                for _wnid, _wnode, _wout in _executed_tool_nodes:
                    _exec_out = _wout
                    if not (_exec_out and str(_exec_out).strip()):
                        _exec_out = self._tool_outcome(
                            _chain_tool_records.get(str(_wnid)))
                    if _exec_out and str(_exec_out).strip():
                        result_parts.append(str(_exec_out).strip()[:500])
                if result_parts:
                    turn_record["result"] = "\n".join(result_parts)

            # A node with no local data must not record an empty turn over
            # the inherited context copy (would surface as a bare "[Turn N]").
            # Note: only the per-node-id copy (shared_context_<node_id>) is
            # inherited — never a concatenation of every context node in the
            # importing chain (fine context management, not bulk merging).
            _turn_has_content = bool(
                str(turn_record.get("query") or "").strip()
                or str(turn_record.get("decision") or "").strip()
                or str(turn_record.get("tool") or "").strip()
                or str(turn_record.get("result") or "").strip()
                or str(turn_record.get("summary") or "").strip()
            )
            if not (_inherited_copy and not _turn_has_content):
                # Store turn in port_store
                self.port_store.add_turn(
                    chain_id, node_id, turn_record, max_turns=max_history,
                )
                # Those tools are part of the history now: move the watermark
                # so the NEXT turn reports only its own.
                if _executed_tool_nodes:
                    try:
                        self.llm_executor.set_variable(_seen_key, _head_seq)
                    except Exception:
                        pass

            # Output: last N turns as structured text
            history = self.port_store.get_turns(chain_id, node_id=node_id, limit=max_history)
            output_value = self._format_turns_for_llm(history)

            # Prepend the inherited context copy when present
            if _inherited_copy:
                if output_value and str(output_value).strip():
                    output_value = f"{_inherited_copy}\n\n{output_value}"
                else:
                    output_value = _inherited_copy
                logger.info(
                    "[CTX] node %s — merged inherited context copy "
                    "(%d chars) with %d local turn(s)",
                    node_id, len(_inherited_copy), len(history),
                )

            logger.info(
                "[CTX] clear_on_finish node %s — turn recorded, "
                "history=%d turns, output=%d chars",
                node_id, len(history), len(output_value),
            )
        else:
            # ── Persistent: query SQLite for past entries ──
            # Own-chain, own-node rows only (fine context management).  The
            # inherited copy (if any) is prepended below — clones never read
            # the whole shared chain.
            pulled = context_db.pull(
                chain_id=chain_id,
                node_id=node_id,
                limit=_pull_limit,
            )
            output_parts = []
            if pulled:
                for key, values in pulled.items():
                    if key.startswith("learned/"):
                        # Learned entries are re-rendered as ONE sentinel block
                        # below, never as raw rows.
                        for v in values:
                            if v and str(v).strip():
                                _learned_pairs[key[len("learned/"):]] = str(v).strip()
                        continue
                    for v in values:
                        if v and str(v).strip():
                            # Full value — no per-entry truncation so shared
                            # clones inherit the COMPLETE context.
                            output_parts.append(f"[{key}] {str(v).strip()}")
            output_value = "\n\n".join(output_parts) if output_parts else ""

            if _inherited_copy:
                if output_value and str(output_value).strip():
                    output_value = f"{_inherited_copy}\n\n{output_value}"
                else:
                    output_value = _inherited_copy

        # -- Merge the shared identity feed (bidirectional sharing) --
        # Entries produced by OTHER participants of this shared identity (the
        # original node's subchain clones, or the original node itself when
        # this is a clone) are merged into the output; own entries are excluded
        # because they already appear in the local output above.
        try:
            _identity = _ref_node_id if (shared_chain_file and _ref_node_id) else node_id
            _feed_text = self._pull_shared_feed(
                context_db, _identity, node_id, max_history,
            )
            if _feed_text:
                if output_value and str(output_value).strip():
                    output_value = f"{output_value}\n\n{_feed_text}"
                else:
                    output_value = _feed_text
                logger.info(
                    "[CTX] node %s merged shared feed %r (%d chars)",
                    node_id, _identity, len(_feed_text),
                )
        except Exception as _feed_err:
            logger.warning(
                "Context feed merge failed for %s: %s", node_id, _feed_err,
            )

        # -- Learned stream: re-render the per-answer rows as ONE sentinel block
        # so a consumer (the form filler) reads them in the format it wrote.
        if _learned_pairs:
            try:
                from .form_filler_modules.common import _ff_render_corrections
                _lb = _ff_render_corrections(list(_learned_pairs.items()))
            except Exception:
                _lb = ""
            if _lb:
                output_value = (f"{output_value}\n\n{_lb}"
                                if output_value and str(output_value).strip()
                                else _lb)

        # -- Documents / Skills streams: file-backed knowledge (or literal notes)
        # held ALONGSIDE history and served as their OWN streams, so a downstream
        # node gets the material over ctx_out WITHOUT attaching anything itself.
        _docs_text = self._ctx_list_text(node_data.get('documents'))
        if _docs_text:
            _docs_block = "## Documents\n" + _docs_text
            output_value = (f"{_docs_block}\n\n{output_value}"
                            if output_value and str(output_value).strip()
                            else _docs_block)
        _skills_text = self._ctx_list_text(node_data.get('skills'))
        if _skills_text:
            _skills_block = "## Skills\n" + _skills_text
            output_value = (f"{_skills_block}\n\n{output_value}"
                            if output_value and str(output_value).strip()
                            else _skills_block)

        # -- Store output (both port_store + legacy for migration) --
        # 'ctx_out' is the node's REAL output port: a consumer wired
        # ctx_out -> ctx_in resolves THIS key, so the pool's material
        # (documents + history + learned facts) must be published here.  The
        # legacy 'context' / 'output' keys stay for older readers.
        self.port_store.set_output(chain_id, node_id, 'ctx_out', output_value)
        self.port_store.set_output(chain_id, node_id, 'context', output_value)
        self.port_store.set_output(chain_id, node_id, 'output', output_value)
        # Legacy fallback for code that still reads from llm_executor.variables
        self.llm_executor.set_variable(f"node_{node_id}_context", output_value)
        self.llm_executor.set_variable(f"node_{node_id}_output", output_value)

        logger.info("[CTX] Context node %s done [%s] — output=%d chars",
                     node_id, scope, len(output_value))
        if output_value and str(output_value).strip():
            log_block(
                logger, logging.INFO,
                f"Context Node Output ({scope})", str(output_value),
            )

        # ── Feed the shared identity (bidirectional) ──
        # Every participant of a shared identity (the source node itself, or a
        # clone referencing it) appends its locally-produced output to ONE feed
        # keyed by the identity (the source node id).  Readers exclude their own
        # rows, so context flows both ways: clones see the original node and the
        # original node sees what its subchain clones produced.  The feed is
        # cleared only when the OWNING chain starts/finishes a per-run
        # (clear_on_finish) context - never by a subchain's finish.
        _is_participant = bool(shared_chain_file and _ref_node_id)
        if not _is_participant:
            try:
                _clone_index = self._get_shared_clone_index()
                _is_participant = bool(_clone_index.get(node_id))
                if not _is_participant:
                    logger.info(
                        "[CTX] source node %s: no clones reference it "
                        "(index sources=%d) - feed not written",
                        node_id, len(_clone_index),
                    )
            except Exception as _idx_err:
                logger.warning("Shared clone index lookup failed: %s", _idx_err)
        if _is_participant and output_value and str(output_value).strip():
            try:
                _identity = _ref_node_id if shared_chain_file else node_id
                context_db.push(
                    chain_id=self._SHARED_COPY_NS,
                    node_id=str(_identity),
                    key='shared',
                    value=str(output_value),
                    source_node_id=str(node_id),
                )
                logger.info(
                    "[CTX] shared identity %s fed by node %s (%d chars)",
                    _identity, node_id, len(str(output_value)),
                )
            except Exception as _feed_err:
                logger.warning(
                    "Shared feed push failed for %s: %s", node_id, _feed_err,
                )

        return self._get_context_next_node(node, 'output')

    def _ctx_serve(self, node_id, stop_flag=None):
        """PASSIVE serve: materialize a Context node's output on demand.

        A Context node never runs as an execution node - it is wired only via
        data ports (ctx_out/ctx_in), so the main loop neither schedules it nor
        waits on it.  Instead it is materialized HERE, when a consumer wired to
        its ctx_out actually reads it: the pool first ingests whatever its
        upstream producers last wrote (persisting learned answers / facts it
        would otherwise never store, since it never executes), then serves its
        whole material (documents + skills + stored rows + the learned block).
        Nothing here gates or blocks the chain.

        Returns the served text ('' when unknown / not a context node).
        """
        if not node_id:
            return ""
        try:
            node = (getattr(self, 'workflow_graph', {}) or {}).get(node_id)
        except Exception:
            node = None
        if not node or node.get('type') != 'context':
            return ""
        try:
            self._execute_context_node(node, stop_flag or (lambda: False))
        except Exception as exc:  # noqa: BLE001 - passive serve never breaks a run
            logger.debug("[CTX] passive serve of %s failed: %s", node_id, exc)
        try:
            _cid = (getattr(self, 'chain_id', None)
                    or getattr(self, 'chain_file', ''))
            return str(self.port_store.get_output(_cid, node_id, 'ctx_out') or "")
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Semantic query API — exposed to LLM, code, and conditional nodes
    # ------------------------------------------------------------------

    def _query_context_node(self, node_id: str = "", question: str = "",
                            scope: str | None = None,
                            chain_id: str = "") -> str:
        """Query a context node's stored entries from SQLite.
    
        Called by LLM nodes (via injected ``context_query()`` function),
        code nodes, and conditionals to retrieve relevant workflow memory.
    
        Args:
            node_id: Optional context node ID to scope to.
            question: Ignored in SQLite mode (no semantic search).
            scope: ``"global"``, ``"local"``, or ``None`` for cross-chain.
            chain_id: Required when scope is ``"local"``.
    
        Returns:
            Natural-language summary string (<2000 chars).
        """
        # Resolve shared context chain from node data
        if node_id and hasattr(self, 'workflow_graph') and self.workflow_graph:
            node = self.workflow_graph.get(node_id, {}) or {}
            node_data = node.get('data', {}) or {}
            shared_chain_file = str(node_data.get('shared_context_chain_file', '') or '')
            if shared_chain_file and os.path.exists(shared_chain_file):
                chain_id = os.path.splitext(os.path.basename(str(shared_chain_file)))[0]
    
        context_db = self._get_context_db()
    
        # Build chain_id filter: if scope is global, query all chains
        query_chain_id = chain_id
        if scope == 'global':
            query_chain_id = ''
    
        pulled = context_db.pull(
            chain_id=query_chain_id or '',
            node_id=node_id or '',
            limit=5,
        )
        output_parts = []
        if pulled:
            for key, values in pulled.items():
                for v in values:
                    if v and str(v).strip():
                        output_parts.append(f"[{key}] {str(v).strip()}")
        result = "\n\n".join(output_parts) if output_parts else ""
        return result[:2000]

    # ------------------------------------------------------------------
    # Pre-initialization — load stored context before any node runs
    # ------------------------------------------------------------------

    def _pre_initialize_context_nodes(self):
        """Initialize context nodes at workflow start — all start fresh per run.

        No historical GraphRAG data is loaded for any context node type.  Each
        run is isolated so LLM nodes only see current-run context, not stale
        cross-run data.  The execution phase (_execute_context_node) populates
        output from upstream connections within this run.
        """
        if not hasattr(self, 'workflow_graph') or not self.workflow_graph:
            return

        chain_id_raw = getattr(self, 'chain_id', None) or getattr(self, 'chain_file', 'default_chain')
        chain_id = os.path.splitext(os.path.basename(str(chain_id_raw)))[0]

        context_db = self._get_context_db()

        # clear_on_finish semantics: context is scoped to ONE chain run.
        # Reset the in-memory turn history (and turn counters) at run start
        # so every run — including self-callback re-runs — starts clean,
        # while graph loops WITHIN the run keep accumulating turns.
        _has_clear_on_finish = any(
            (n.get('data', {}) or {}).get('clear_on_finish', False)
            for n in self.workflow_graph.values()
            if n.get('type') == 'context'
        )
        if _has_clear_on_finish:
            self.port_store.clear_turns(chain_id)

        for node_id, node in self.workflow_graph.items():
            if node.get('type') != 'context':
                continue

            try:
                node_data = node.get('data', {}) or {}

                # Resolve shared context chain
                shared_chain_file = str(node_data.get('shared_context_chain_file', '') or '')
                effective_chain_id = chain_id
                if shared_chain_file and os.path.exists(shared_chain_file):
                    effective_chain_id = os.path.splitext(os.path.basename(str(shared_chain_file)))[0]

                persistent = bool(node_data.get('persistent', True))
                clear_on_finish = bool(node_data.get('clear_on_finish', False))

                # clear_on_finish nodes are per-run scoped: SQLite is wiped at
                # run start regardless of the persistent flag (persistent only
                # matters for non-clear_on_finish nodes).  EXCEPT shared nodes
                # (shared_context_chain_file): their content is owned by the
                # chain they share with, which may still be running while this
                # (sub) chain executes — wiping it at our run start would
                # erase the very context we are meant to read.  The owning
                # chain clears it when IT finishes (clear_on_finish belongs to
                # the owner, not to a reader sub-chain).
                if shared_chain_file:
                    logger.debug(
                        "[CTX] Skipped pre-init clear for shared node %s "
                        "(shared chain=%s) — content owned by that chain",
                        node_id, effective_chain_id,
                    )
                elif (not persistent) or clear_on_finish:
                    context_db.clear(chain_id=effective_chain_id, node_id=node_id)
                    if clear_on_finish:
                        # Per-run owner: reset the identity feed too, so stale
                        # entries from earlier sessions (written by subchain
                        # clones) do not leak into this run.
                        context_db.clear(ContextMixin._SHARED_COPY_NS, node_id)
                    logger.info("[CTX] Cleared node %s (clear_on_finish=%s)", node_id, clear_on_finish)
                # All context nodes start fresh per run
                output_value = ""
                # Clear port-scoped store for this node
                if hasattr(self, 'port_store'):
                    self.port_store.clear_node(chain_id, node_id)

                self.llm_executor.set_variable(f"node_{node_id}_context", output_value)
                self.llm_executor.set_variable(f"node_{node_id}_output", output_value)
                logger.debug("[CTX] Pre-init node %s: persistent=%s, output=%d chars",
                             node_id, persistent, len(output_value))
            except Exception as e:
                logger.warning("[CTX] Pre-init failed for node %s: %s", node_id, e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ctx_list_text(self, raw):
        """Read a JSON list where each entry is a FILE PATH (read to text) or a
        LITERAL note (used verbatim).  Cached per list so a looping chain does
        not re-read the files every iteration.

        Backs the Documents and Skills streams: a resume file, a skill file, or
        a plain typed note all live in ONE list and are served as their stream.
        """
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = [raw] if raw.strip() else []
        items = [str(p) for p in (raw or []) if str(p).strip()]
        if not items:
            return ""
        key = tuple(items)
        cache = getattr(self, '_ctx_list_text_cache', None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                self._ctx_list_text_cache = cache
            except Exception:
                pass
        if key in cache:
            return cache[key]
        texts = []
        for it in items:
            if not os.path.exists(it):
                texts.append(it)          # a literal note, used verbatim
                continue
            try:
                from ...llm_executor_resources import rag_utils as _rag
                ext = os.path.splitext(it)[1].lower()
                if ext in ('.txt', '.md', '.csv', '.json', '.log'):
                    t = _rag._read_text_file(it)
                elif ext == '.docx':
                    t = _rag._extract_docx_text(it)
                elif ext == '.pdf':
                    t = _rag._extract_pdf_text(it)
                else:
                    t = ""
                if t:
                    texts.append(t)
            except Exception as exc:
                logger.debug("[CTX] list text unavailable: %s", exc)
        out = "\n\n".join(texts).strip()
        cache[key] = out
        return out

    def _get_context_next_node(self, node, port):
        connections = node.get('connections', {})
        if port in connections and connections[port]:
            return connections[port][0].get('node_id')
        if port == 'output':
            return "__done__"
        return None
