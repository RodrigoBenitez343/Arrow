from ..common import chain_logger
import json
import os
import time
import uuid
from collections import OrderedDict

from .dependencies import DependenciesMixin
from .sequence_ops import SequenceMixin
from .conditional_ops import ConditionalMixin
from .llm_ops import LLMMixin
from .chain_ops import ChainImportMixin
from .code_ops import CodeMixin
from .container_ops import ContainerMixin
from .context_ops import ContextMixin
from .input_ops import InputMixin
from .handle_ops import HandleMixin
from .form_filler_ops import FormFillerMixin
from .mcp_ops import MCPMixin, _mcp_cleanup_enter, _mcp_cleanup_exit
from .output_ops import OutputMixin
from .orchestrator_ops import OrchestratorMixin
from .port_store import PortScopedStore
import logging
try:
    from logging_setup import log_block, log_table
except Exception:
    def log_block(logger, level, title, content, lang="text"):
        text = content if isinstance(content, str) else repr(content)
        logger.log(level, "=== %s ===\n%s", title, text)

    def log_table(logger, level, title, rows):
        if not rows:
            return
        text = "\n".join(f"{k}: {v}" for k, v in rows)
        logger.log(level, "=== %s ===\n%s", title, text)
logger = logging.getLogger(__name__)

class WorkflowExecutor(DependenciesMixin, SequenceMixin, ConditionalMixin, LLMMixin, ChainImportMixin, CodeMixin, ContainerMixin, ContextMixin, InputMixin, HandleMixin, FormFillerMixin, MCPMixin, OutputMixin, OrchestratorMixin):
    """Executes workflow graphs with proper dependency resolution and node execution"""
    
    def __init__(self, workflow_graph, sequence_executor, llm_executor, fallback_handler):
        self.workflow_graph = workflow_graph
        self.sequence_executor = sequence_executor
        self.llm_executor = llm_executor
        self.llm_executor.workflow_graph = workflow_graph
        self.fallback_handler = fallback_handler
        self.skipped_nodes = set()
        # Port-scoped variable store (replaces flat variables dict for node outputs)
        self.port_store = PortScopedStore()
        # Shared selection memory reference (to be provided by MultiSequencePlayer)
        self.selection_memory = None
        self.global_app_context_override = None
        self._runtime_root = None
        self._chain_label = None
        self._chain_runtime_dir = None
        self._venv_dir = None
        self._venv_python = None
        self._venv_site_packages = None
        self._code_retry_counts = {}
        self._runtime_initialized = False
        self._runtime_needed = False
        self.last_node_summary = None
        self.last_node_id = None
        # Track conditional loops for graph-based while/until loops that
        # need to loop back to the conditional after their branch completes.
        # Keys are conditional node IDs, values track iteration state.
        self._graph_loop_state = {}
        # Node ids executed since the last graph loop-back.  A loop pass that
        # ran NO work node (only outputs/conditionals passed through) cannot
        # make progress - it is a dead end, not a loop, and must not re-iterate.
        self._loop_pass_exec = set()
        # Track how many times each node has been executed (used by Input node
        # loop-awareness: a re-executed Input during loop must show dialog
        # even in agent mode).
        self._node_execution_count = {}
        # Deferred nodes: Input nodes that should wait for a chain_import
        # tool to complete before executing (Task 4 — prevents Input from
        # asking "Can I do something else?" before the tool runs).
    
    def reset(self):
        """Resets the executor state for a new run."""
        self.skipped_nodes = set()
        self._code_retry_counts = {}
        self.last_node_summary = None
        self.last_node_id = None
        self._graph_loop_state.clear()
        self._node_execution_count.clear()
        # We don't reset runtime environment to avoid reloading venv unnecessarily,
        # but we reset the initialization flags to ensure checks happen if needed.
        # actually, keeping runtime initialized is better for performance.
        logger.info("WorkflowExecutor state reset")

    def execute_workflow(self, stop_flag=None, action_handlers=None, chain_file=None, on_node_failed=None, on_output_ready=None, chain_identity=None):
        """
        Executes the workflow graph with proper dependency resolution.
        Nodes with multiple inputs will only execute after all their dependencies are satisfied.
        
        Args:
            stop_flag (callable): Optional function that returns True when execution should stop
            action_handlers: Action handlers for text writing and other actions
            chain_file: Path to the chain file being executed (for chain_id derivation)
            on_node_failed (callable): Optional callback invoked when a node fails execution.
                Called with (node_id, node_type, node_data) from the background thread.
                Use QTimer.singleShot(0, ...) in the callback for thread-safe GUI updates.
            chain_identity (str, optional): Original chain path/name for PERSISTENCE.  The
                GUI runs chains from deterministic temp copies; context rows must be
                stored under the ORIGINAL chain's identity so persisted context follows
                the chain (and "clear after finish" clears the original's data).
            
        Returns:
            bool: True if workflow completed successfully, False otherwise
        """
        if not self.workflow_graph:
            logger.warning("No workflow graph to execute")
            return False

        # Generate run_id for this execution
        self.run_id = str(uuid.uuid4())
        
        # Set chain_id from chain_file if provided, otherwise use existing or default
        if chain_file:
            # Normalize to short name (filename without extension) as single
            # source of truth.  All downstream consumers (context_db, port_store,
            # context_ops) use this same normalized value.
            self.chain_id = os.path.splitext(os.path.basename(str(chain_file)))[0]
            self.chain_file_path = chain_file  # Keep full path for file operations
            # Extract chain description for context key derivation
            if not getattr(self, '_chain_description', None):
                try:
                    with open(chain_file, 'r', encoding='utf-8') as f:
                        cfg = json.load(f)
                    self._chain_description = cfg.get('description', '') or ''
                except Exception:
                    self._chain_description = ''
        elif not getattr(self, 'chain_id', None):
            self.chain_id = 'default_chain'
            self.chain_file_path = ''

        # Override the persistence identity when the caller runs from a
        # deterministic temp copy but wants context stored under the ORIGINAL
        # chain (GUI playback).  chain_file_path stays the temp file for asset
        # resolution; only the storage namespace switches to the real chain.
        if chain_identity:
            self.chain_id = os.path.splitext(
                os.path.basename(str(chain_identity))
            )[0]
            logger.info(
                "Persistence identity overridden: %s (runtime file: %s)",
                self.chain_id, chain_file,
            )

        logger.info(f"Starting workflow execution: run_id={self.run_id}, chain_id={self.chain_id}")

        # Track MCP server nesting so cleanup only happens at the top-level chain.
        # The finally block guarantees exit is called on every return path (stop,
        # stall, error, or normal completion).
        _mcp_cleanup_enter()
        self._cleanup_done = False  # reset for this execution
        try:
            self.action_handlers = action_handlers
        except Exception:
            self.action_handlers = None
        
        # Initialize runtime environment immediately but track if we need full setup
        # We'll do minimal initialization first, then full setup only when needed
        self._runtime_initialized = False
        self._runtime_needed = False

        # Store output-ready callback for mid-execution popups (used in output_ops.py)
        # If not passed directly, check the LLM executor variable (propagated by chain_ops.py)
        if on_output_ready is None:
            try:
                on_output_ready = self.llm_executor.get_variable("_on_output_ready_callback")
            except Exception:
                on_output_ready = None
        self._on_output_ready = on_output_ready

        # Pre-initialize context nodes with stored data from previous runs
        self._pre_initialize_context_nodes()
            
        # Track completed nodes and nodes ready for execution
        completed_nodes = set()
        ready_nodes = OrderedDict()
        
        # Find all starting nodes (nodes with no inputs)
        starting_nodes = self._find_all_starting_nodes()
        if not starting_nodes:
            logger.warning("No starting nodes found in workflow graph")
            return False
        
        logger.info(f"Starting workflow execution from nodes: {starting_nodes}")
        chain_logger.info(f"=== CHAIN RUN STARTED ===")
        chain_logger.info(f"Starting nodes: {starting_nodes}")
        logger.debug(f"Workflow graph has {len(self.workflow_graph)} nodes: {list(self.workflow_graph.keys())}")
        for _starting_nid in starting_nodes:
            ready_nodes[_starting_nid] = None
        # Skip tool provider nodes (connected only via data ports like 'tools').
        # Under the atomic LLM model, these are executed as subroutines of the
        # LLM node, not as standalone graph nodes.
        self._skip_tool_providers()
        last_completed_node_id = None
        
        while True:
            # Check stop flag
            if stop_flag and stop_flag():
                logger.info("Workflow execution stopped by user request")
                self._cleanup_run_context()
                return True
            # Defer all execution while any LLM node is actively typing
            try:
                vars_snapshot = self.llm_executor.get_all_variables()
            except Exception:
                vars_snapshot = {}
            typing_active = any(k.endswith('_writing') and vars_snapshot.get(k) for k in vars_snapshot.keys())
            if typing_active:
                # Safety counter: if typing stays active for too many
                # iterations, assume a stuck flag and force-continue.
                # Prevents infinite spin when a _writing flag from the
                # parent chain leaks into imported chain variables.
                _typing_spin = getattr(self, '_typing_spin_count', 0) + 1
                self._typing_spin_count = _typing_spin
                if _typing_spin > 600:  # ~30 seconds at 0.05s sleep
                    logger.warning(
                        "Typing-active spin limit reached (%d iterations) — "
                        "forcing continuation to prevent hang", _typing_spin
                    )
                    self._typing_spin_count = 0
                else:
                    try:
                        time.sleep(0.05)
                    except Exception:
                        pass
                    # Refresh readiness so nodes queue up but do not execute until typing ends
                    for node_id, node_data in self.workflow_graph.items():
                        if node_id in completed_nodes:
                            continue
                        if self._are_all_dependencies_satisfied(node_id, completed_nodes):
                            ready_nodes[node_id] = None
                    continue
            if not ready_nodes:
                remaining = [nid for nid in self.workflow_graph.keys() if nid not in completed_nodes and nid not in self.skipped_nodes]
                if not remaining:
                    # Before declaring completion, check if a graph-based
                    # conditional loop should iterate again.
                    if self._maybe_loop_graph_conditional(completed_nodes, stop_flag):
                        continue
                    logger.info("Workflow execution completed successfully")
                    self._cleanup_run_context()
                    return True

                # Only nodes DOWNSTREAM of the node that just ran may be armed
                # here.  ``completed_nodes`` is cumulative for the WHOLE run, so
                # a bare "any satisfied node" scan re-arms a node whose
                # predecessor completed earlier in the run - including one the
                # execution has long left behind.  That is how a node with no
                # connection to the just-executed one still fired (e.g. the form
                # filler running after the terminal submit node).  Readiness must
                # follow the edge the execution actually took.
                for nid in remaining:
                    if last_completed_node_id and nid != last_completed_node_id \
                            and not self._can_reach(last_completed_node_id, nid):
                        # Nothing can arm this node any more: it is not on any
                        # path from where the execution now stands.  Settle it
                        # as SKIPPED (the b2 advisory mark) so the run can finish
                        # instead of dying as STALL-DEADEND over nodes the chain
                        # has already moved past.  A later loop re-entry enqueues
                        # its target directly and may override the mark.
                        self.skipped_nodes.add(nid)
                        continue
                    if self._are_all_dependencies_satisfied(nid, completed_nodes):
                        ready_nodes[nid] = None

                if ready_nodes:
                    continue

                # Remaining nodes that have no execution port (base 'input')
                # dependencies are tool providers connected only via data ports.
                # Under the atomic LLM model, these are subroutines of the LLM
                # node, not standalone graph nodes — skip them.
                for nid in list(remaining):
                    node = self.workflow_graph.get(nid, {})
                    _inputs = node.get('inputs') or []
                    if not _inputs:
                        continue  # Starting node — not a tool provider
                    if node.get('type') == 'context':
                        continue  # Context nodes are graph nodes, never LLM subroutines
                    has_exec_input = any(
                        inp.get('input_port', 'input') in ('input', '', None)
                        for inp in _inputs
                    )
                    if not has_exec_input:
                        self.skipped_nodes.add(nid)
                        _input_ports = [inp.get('input_port', '') for inp in _inputs]
                        logger.info(
                            "[SKIP] Tool provider %s (type=%s) skipped — LLM subroutine "
                            "(input_ports=%s, from_nodes=%s)",
                            nid, node.get('type'), _input_ports,
                            [inp.get('from_node', '?') for inp in _inputs],
                        )

                # ── Dead-end resolution (a7): auto-complete dead tool providers ──
                # Tool-provider nodes (chain_import/code/mcp subroutines) whose
                # exec dependencies are ALL settled (completed or skipped) but
                # which were never queued — e.g. exec inputs from an unchosen
                # branch — must not stall the workflow.  Mark them completed
                # and continue; only genuinely blocked nodes remain.
                _auto_completed = False
                for nid in list(remaining):
                    if nid in self.skipped_nodes:
                        continue
                    node = self.workflow_graph.get(nid, {})
                    # Union, never replacement (task #1): the legacy type tuple
                    # still rescues UNFLAGGED leftovers; the flag adds every
                    # newly-gated type (sequence / web_sequence / conditional /
                    # handle / form_filler / ...) to this dead-end settlement.
                    if (node.get('type') not in ('chain_import', 'code', 'mcp')
                            and not node.get('tool_provider')):
                        continue
                    _inputs = node.get('inputs') or []
                    _is_tool_provider = bool(node.get('tool_provider'))
                    if not _inputs:
                        # Zero-input tool providers (LLM subroutines never
                        # selected — e.g. tool-branch chain imports with no
                        # graph inputs) must not stall the workflow.
                        if _is_tool_provider:
                            completed_nodes.add(nid)
                            _auto_completed = True
                            logger.info(
                                "[SKIP] Tool provider %s (type=%s) auto-completed "
                                "— zero-input LLM subroutine",
                                nid, node.get('type'),
                            )
                        continue
                    _has_exec_input = any(
                        inp.get('input_port', 'input') in ('input', '', None)
                        for inp in _inputs
                    )
                    if _has_exec_input:
                        # All exec-input sources must be settled (completed or
                        # skipped) — otherwise the node is genuinely blocked.
                        _all_sources_settled = True
                        for _inp in _inputs:
                            _src = _inp.get('from_node')
                            if (_src not in completed_nodes
                                    and _src not in self.skipped_nodes):
                                _all_sources_settled = False
                                break
                        if not _all_sources_settled:
                            continue
                    completed_nodes.add(nid)
                    _auto_completed = True
                    logger.info(
                        "[SKIP] Tool provider %s (type=%s) auto-completed — "
                        "LLM subroutine with no unsatisfied exec dependencies",
                        nid, node.get('type'),
                    )

                # Re-check after marking tool providers
                remaining = [nid for nid in self.workflow_graph.keys() if nid not in completed_nodes and nid not in self.skipped_nodes]
                if not remaining:
                    # Before declaring completion, check if a graph-based
                    # conditional loop should iterate again.
                    if _auto_completed and self._maybe_loop_graph_conditional(completed_nodes, stop_flag):
                        continue
                    logger.info("Workflow execution completed successfully")
                    self._cleanup_run_context()
                    return True
                if _auto_completed:
                    # Something became satisfiable again after auto-completion
                    continue

                # ── Dead-branch settlement (b3) ──
                # Before failing the run, retire nodes that can never be armed
                # again: their execution gate is a COMPLETED conditional that
                # chose a DIFFERENT branch, and they are unreachable from that
                # chosen branch once the gating conditional (and hence its
                # loop-back edge) is excluded from the search.  Such nodes sit
                # on a branch the run has legitimately left — the chain ends
                # there by design — so settle them as SKIPPED (advisory, like
                # the b2 mark) and finish cleanly instead of reporting a
                # STALL-DEADEND hard failure over a correct ending.
                _dead_branch = {
                    nid for nid in remaining
                    if self._is_dead_branch_node(nid, completed_nodes)
                }
                if _dead_branch:
                    self.skipped_nodes.update(_dead_branch)
                    logger.info(
                        "[DEAD-BRANCH] Settled %d node(s) on unchosen conditional "
                        "branch(es) as skipped: %s",
                        len(_dead_branch), sorted(_dead_branch),
                    )
                    continue

                # Log detailed info about stalled nodes and terminate with an
                # explicit dead-end FAILED reason (never a silent STALL).
                _dead_labels = []
                for _stall_id in remaining:
                    _stall_node = self.workflow_graph.get(_stall_id, {})
                    _stall_label = (
                        (_stall_node.get('data', {}) or {}).get('label')
                        or _stall_node.get('type') or _stall_id
                    )
                    _dead_labels.append(f"{_stall_id}({_stall_label})")
                logger.error(
                    "[STALL-DEADEND] Workflow execution cannot proceed. "
                    "Remaining nodes with unsatisfied dependencies: %s",
                    ", ".join(_dead_labels),
                )
                logger.error(
                    "[STALL] Workflow execution stalled with %d remaining nodes",
                    len(remaining),
                )
                for _stall_id in remaining:
                    _stall_node = self.workflow_graph.get(_stall_id, {})
                    _stall_inputs = _stall_node.get('inputs', []) or []
                    _stall_deets = {
                        'type': _stall_node.get('type'),
                        'input_count': len(_stall_inputs),
                        'input_sources': [
                            {'from': i.get('from_node'), 'port': i.get('input_port')}
                            for i in _stall_inputs
                        ],
                        'completed': _stall_id in completed_nodes,
                        'skipped': _stall_id in self.skipped_nodes,
                    }
                    logger.error("[STALL] Node %s details: %s", _stall_id, _stall_deets)
                self._cleanup_run_context()
                return False
            
            current_node_id = self._select_next_node(ready_nodes)
            
            if current_node_id in completed_nodes:
                continue  # Skip already completed nodes
            
            if current_node_id in self.skipped_nodes:
                _skip_node = self.workflow_graph.get(current_node_id, {})
                _skip_inputs = _skip_node.get('inputs', []) or []
                _has_exec_inp = any(
                    inp.get('input_port', 'input') in ('input', '', None)
                    for inp in _skip_inputs
                ) if _skip_inputs else True
                _skip_data = _skip_node.get('data', {}) or {}
                if self._are_all_dependencies_satisfied(current_node_id, completed_nodes):
                    # Skip marks are advisory (b2): a queued node whose graph
                    # dependencies are all satisfied must execute, not be
                    # silently dropped.
                    logger.warning(
                        "[SKIP-OVERRIDE] Node %s (type=%s) queued and ready — "
                        "overriding skip mark (has_exec_input=%s, input_count=%d, label=%s)",
                        current_node_id, _skip_node.get('type'),
                        _has_exec_inp, len(_skip_inputs),
                        _skip_data.get('label', ''),
                    )
                    self.skipped_nodes.discard(current_node_id)
                else:
                    logger.error(
                        "[SKIP] Node %s (type=%s) in skipped_nodes and not ready — "
                        "blocked (has_exec_input=%s, input_count=%d, label=%s)",
                        current_node_id, _skip_node.get('type'),
                        _has_exec_inp, len(_skip_inputs),
                        _skip_data.get('label', ''),
                    )
                    continue

            if current_node_id not in self.workflow_graph:
                logger.warning(f"Node {current_node_id} not found in workflow graph")
                continue
            
            node = self.workflow_graph[current_node_id]
            logger.info(f"Executing node {current_node_id} (type: {node['type']})")
            chain_logger.info(f"EXECUTING NODE: {current_node_id} (Type: {node['type']})")
            try:
                _node_rows = [
                    ("Node ID", str(current_node_id)),
                    ("Type", str(node.get('type'))),
                ]
                _ins = node.get('inputs', []) or []
                if _ins:
                    _node_rows.append(("Inputs", str(len(_ins))))
                    for _i in _ins:
                        _node_rows.append(
                            (
                                f"  input -> {_i.get('input_port')}",
                                f"{_i.get('from_node')} "
                                f"({_i.get('output_type') or _i.get('output_port') or 'output'})",
                            )
                        )
                log_table(
                    logger, logging.INFO,
                    f"Node {current_node_id} execution", _node_rows,
                )
            except Exception:
                pass
            
            # Execute the node based on its type
            success = False
            conditional_result = None
            # File-loop conditionals set this inside their dispatch; the branch
            # block below reads it for any branch-capable node.
            is_file_loop = False
            
            if node['type'] == 'sequence':
                success = self._execute_sequence_node(node, stop_flag) is not None
            elif node['type'] == 'web_sequence':
                success = self._execute_web_sequence_node(node, stop_flag) is not None
            elif node['type'] == 'conditional':
                # For conditional nodes, evaluate the condition and store the result
                conditional_data = node['data']
                condition_type = str(conditional_data.get('condition_type', 'presence') or '').strip().lower()
                loop_type = str(conditional_data.get('loop_type', '') or '').strip().lower()
                
                # A graph-based loop has a loop_type but uses workflow-graph branches
                # (true/false connections to sequences) instead of a single sequence_file.
                is_graph_loop = (
                    loop_type in ('while_present', 'until_present', 'while_absent', 'until_absent')
                    and not str(conditional_data.get('sequence_file', '') or '').strip()
                )

                # A file loop has a loop_type AND an internal sequence/chain file:
                # it loops the file until the condition stops holding, then the
                # workflow continues via a single 'output' port (no true/false).
                is_file_loop = (
                    loop_type in ('while_present', 'until_present', 'while_absent', 'until_absent')
                    and bool(str(conditional_data.get('sequence_file', '') or conditional_data.get('chain_file', '') or '').strip())
                )
                
                # ponytail: graph-based loops (loop_type set but no sequence_file)
                # must be checked BEFORE condition_type so they don't fall through
                # to _execute_conditional_loop which requires a sequence_file and
                # would return False, routing execution down the wrong branch.
                if is_graph_loop:
                    # Graph-based conditional loop: evaluate the condition now;
                    # loop-back is managed by _maybe_loop_graph_conditional() when
                    # the branch completes.
                    conditional_result = self._evaluate_conditional(conditional_data, stop_flag, node.get('inputs', []))
                    if current_node_id not in self._graph_loop_state:
                        max_loops = int(conditional_data.get('max_loops', 10))
                        self._graph_loop_state[current_node_id] = {
                            'max_iterations': max_loops,
                            'iteration': 1,
                        }
                        logger.info(f"Graph loop started: {loop_type}, max_iterations: {max_loops}")
                elif condition_type in ('loop', 'while_present', 'until_present', 'while_absent', 'until_absent'):
                    conditional_result = self._execute_conditional_loop(conditional_data, stop_flag)
                else:
                    conditional_result = self._evaluate_conditional(conditional_data, stop_flag, node.get('inputs', []))
                success = True  # Conditional evaluation always succeeds
                logger.info(f"Conditional evaluation result: {conditional_result}")
                # Store the result in the node for later reference
                node['last_conditional_result'] = conditional_result
            elif node['type'] == 'llm':
                if self._llm_node_is_orchestrator(node):
                    # Additive: an LLM node toggled into 'orchestrator' mode runs
                    # the goal loop over the chains wired to its 'tools' port
                    # (treated as plain chains).  The separate Orchestrator node
                    # is untouched.
                    _orch_node = self._orchestrator_from_llm_node(node)
                    success = self._execute_orchestrator_node(_orch_node, stop_flag) is not None
                    if success:
                        _orch_fulfilled = _orch_node.get('_orchestrator_fulfilled')
                        if _orch_fulfilled is None:
                            _orch_fulfilled = (_orch_node.get('data') or {}).get('_orchestrator_fulfilled')
                        if _orch_fulfilled is not None:
                            conditional_result = bool(_orch_fulfilled)
                else:
                    success = self._execute_llm_node(node, stop_flag, action_handlers) is not None
            elif node['type'] == 'tts':
                # Legacy standalone TTS nodes are migrated to audio Output
                # nodes at load time; keep a loud warning if one slips through.
                logger.warning("Legacy 'tts' node %s is no longer supported — "
                               "migrate the chain (resave from the editor)", node.get('id'))
                success = True
            elif node['type'] == 'chain_import':
                success = self._execute_chain_import_node(node, stop_flag) is not None
            elif node['type'] == 'code':
                success = self._execute_code_node(node, stop_flag) is not None
            elif node['type'] == 'container':
                success = self._execute_container_node(node, stop_flag) is not None
            elif node['type'] == 'context':
                success = self._execute_context_node(node, stop_flag) is not None
            elif node['type'] == 'input':
                success = self._execute_input_node(node, stop_flag) is not None
                # Branch-capable Input nodes (decision toggle / routed yes-no
                # questions) hand their routing result to the branch block.
                if success:
                    _input_branch = node.get('_branch_result')
                    if _input_branch is not None:
                        conditional_result = bool(_input_branch)
            elif node['type'] == 'orchestrator':
                success = self._execute_orchestrator_node(node, stop_flag) is not None
                # Route branch (mirrors branch-capable Input nodes): the run's
                # own verdict decides the path — achieved goals continue via
                # 'output', unfulfilled ones via 'route'.
                if success:
                    _orch_fulfilled = node.get('_orchestrator_fulfilled')
                    if _orch_fulfilled is None:
                        _orch_fulfilled = (node.get('data') or {}).get('_orchestrator_fulfilled')
                    if _orch_fulfilled is not None:
                        conditional_result = bool(_orch_fulfilled)
            elif node['type'] == 'handle':
                success = self._execute_handle_node(node, stop_flag) is not None
            elif node['type'] == 'form_filler':
                success = self._execute_form_filling_node(node, stop_flag) is not None
            elif node['type'] == 'mcp':
                success = self._execute_mcp_node(node, stop_flag) is not None
            elif node['type'] == 'output':
                success = self._execute_output_node(node, stop_flag) is not None
            else:
                logger.warning(f"Unknown node type: {node['type']}")
                success = False
            
            if success:
                # Mark node as completed
                completed_nodes.add(current_node_id)
                last_completed_node_id = current_node_id
                self.last_node_id = current_node_id
                self._record_last_node_summary(current_node_id, node, conditional_result)

                # ── Mirror LLM output into the port-scoped store ──
                # Context/Output nodes resolve upstream values via port_store,
                # but the LLM executor only writes legacy variables.  Without
                # this, Context nodes see empty upstream data and produce
                # empty turns ("No context available from connected inputs").
                if node['type'] == 'llm':
                    try:
                        _llm_resp = self.llm_executor.get_variable(
                            f"node_{current_node_id}_output"
                        )
                        if _llm_resp is not None:
                            self.port_store.set_output(
                                self.chain_id, current_node_id, 'output', _llm_resp
                            )
                        _llm_ctx = self.llm_executor.get_variable(
                            f"node_{current_node_id}_context"
                        )
                        if _llm_ctx is not None:
                            self.port_store.set_output(
                                self.chain_id, current_node_id, 'context', _llm_ctx
                            )
                        elif _llm_resp is not None:
                            self.port_store.set_output(
                                self.chain_id, current_node_id, 'context', _llm_resp
                            )
                    except Exception as _mirror_err:
                        logger.warning(
                            "port_store mirror failed for %s: %s",
                            current_node_id, _mirror_err,
                        )

                # ── Mirror captured web page data into the port-scoped store ──
                # The web sequence executor holds the browser; it stashes the
                # user-toggled page items after replay.  Publish them here so
                # downstream nodes reading the ctx_out data port (LLM/code/
                # conditional) see the page content.
                if node['type'] == 'web_sequence':
                    try:
                        _ws_data = getattr(self.sequence_executor, '_web_page_data', {}) or {}
                        if _ws_data:
                            self.port_store.set_output(
                                self.chain_id, current_node_id, 'ctx_out', _ws_data
                            )
                            _ws_text = _render_web_page_data(_ws_data)
                            self.port_store.set_output(
                                self.chain_id, current_node_id, 'context', _ws_text
                            )
                            self.llm_executor.set_variable(
                                f"node_{current_node_id}_context", _ws_text
                            )
                            # Deliberately NO node_{id}_output: the web sequence
                            # 'output' port is execution-only, and the executor's
                            # input-port injection ("User input:") would funnel
                            # the full page text into the prompt, bypassing
                            # ComoRAG consolidation and overflowing the
                            # llama.cpp context (206k tokens > 20480 cap).
                            # Page data travels via ctx_out/context ports only.
                    except Exception as _ws_err:
                        logger.warning(
                            "web ctx mirror failed for %s: %s",
                            current_node_id, _ws_err,
                        )

                # ── Mirror Form Filling output into the port-scoped store ──
                # Downstream Conditionals (multi-page composition) branch on the
                # node's JSON summary via the output/context ports.
                if node['type'] == 'form_filler':
                    try:
                        _ff_resp = self.llm_executor.get_variable(
                            f"node_{current_node_id}_output"
                        )
                        if _ff_resp is not None:
                            self.port_store.set_output(
                                self.chain_id, current_node_id, 'output', _ff_resp
                            )
                            self.port_store.set_output(
                                self.chain_id, current_node_id, 'context', _ff_resp
                            )
                    except Exception as _ff_err:
                        logger.warning(
                            "form_filler mirror failed for %s: %s",
                            current_node_id, _ff_err,
                        )

                # ── Linear run memory (player/agentic_ops/run_memory.py) ──
                # One event per executed node: its category strata (inputs /
                # outputs / llm / condition / form / web / tool) with the
                # interesting port values.  Skipped branches leave no event —
                # absence is the honest record.  Never affects execution.
                try:
                    from player.agentic_ops import run_memory as _run_memory
                    _rm_label = ''
                    try:
                        _rm_label = str(
                            (node.get('data') or {}).get('label')
                            or (node.get('data') or {}).get('name') or ''
                        )
                    except Exception:
                        _rm_label = ''
                    _run_memory.record_node(
                        chain_id=getattr(self, 'chain_id', ''),
                        node_id=current_node_id,
                        node_type=str(node.get('type') or ''),
                        label=_rm_label,
                        ports=self.port_store.get_node_ports(
                            self.chain_id, current_node_id),
                        conditional_result=conditional_result,
                    )
                except Exception:
                    pass

                # Track execution count for loop-aware Input node behavior
                self._node_execution_count[current_node_id] = self._node_execution_count.get(current_node_id, 0) + 1
                # Remember this node for the graph loop dead-end guard (see the
                # generalized loop-back block below).
                self._loop_pass_exec.add(current_node_id)
                logger.info(f"Node {current_node_id} completed successfully")
                chain_logger.info(f"Node {current_node_id} COMPLETED successfully")
                self._apply_extra_delay(node, stop_flag)
                
                # Handle branching (conditionals and branch-capable inputs)
                branch_node = None
                if self._is_branch_source(node):
                    # Determine which branch to follow.  Orchestrators branch
                    # on fulfilment ('output' achieved / 'route' not);
                    # conditionals and decision Inputs branch on true/false.
                    if self._is_orchestrator_like(node):
                        _chosen_bucket = 'output' if conditional_result else 'route'
                        _unchosen_bucket = 'route' if conditional_result else 'output'
                    else:
                        _chosen_bucket = 'true' if conditional_result else 'false'
                        _unchosen_bucket = 'false' if conditional_result else 'true'
                    branch_node = self._get_conditional_branch_node(node, conditional_result)
                    if branch_node:
                        # Mark the chosen branch in the conditional node
                        node['chosen_branch_node'] = branch_node
                                        
                        # Fix: Check if node['data'] needs the update too since _are_all_dependencies_satisfied 
                        # often looks at the node config from self.workflow_graph directly which may be a copy
                        if 'data' in node:
                            node['data']['chosen_branch_node'] = branch_node
                
                        # File-loop conditionals loop internally and continue via
                        # their single output — there is no unchosen branch to skip.
                        if not is_file_loop:
                            chosen_port = _chosen_bucket
                            unchosen_port = _unchosen_bucket
                    
                            try:
                                self._mark_skipped(current_node_id, chosen_port, unchosen_port, completed_nodes)
                            except Exception:
                                pass
                    else:
                        # File-loop conditionals have a single output; when it is
                        # not connected the loop simply ends the workflow.
                        if not is_file_loop:
                            # No explicit branch connection for this result.
                            # This means the chosen path is terminal (e.g. "true" branch
                            # doesn't exist -> loop ended). Mark the OPPOSITE branch
                            # as skipped so the workflow can finish cleanly.
                            logger.info(f"Node {current_node_id}: result={conditional_result}, no matching branch — marking opposite branch as skipped")
                            unchosen_port = _unchosen_bucket
                            try:
                                self._mark_skipped(current_node_id, '', unchosen_port, completed_nodes)
                            except Exception:
                                pass
                                
                # Build immediate successor list for ALL node types
                next_nodes = []
                if self._is_branch_source(node):
                    if branch_node:
                        next_nodes.append(branch_node)
                    # Orchestrator ports are multi-output: EVERY target of the
                    # taken bucket drives, not only the first (which serves as
                    # the chosen-branch marker for the dependency gate).
                    if self._is_orchestrator_like(node):
                        _b_conns = node.get('connections', {}) or {}
                        if isinstance(_b_conns, dict):
                            for _c in (_b_conns.get(_chosen_bucket) or []):
                                if not isinstance(_c, dict):
                                    continue
                                _tid = _c.get('node_id') or _c.get('target_node_id')
                                if _tid and _tid not in next_nodes:
                                    next_nodes.append(_tid)
                else:
                    next_nodes = self._get_output_connected_nodes(current_node_id)
                
                # === GENERALIZED LOOP-BACK DETECTION (Task 4b/5) ===
                # After ANY node type executes, check each immediate target.
                # If a target is already in completed_nodes, this is a loop-back.
                # Trace downstream path, reset completed/skipped state,
                # track iteration count, and re-queue the target.
                for target_id in list(next_nodes):
                    if target_id in completed_nodes:
                        _target_node_type = self.workflow_graph.get(target_id, {}).get('type', '?')
                        _source_node_type = self.workflow_graph.get(current_node_id, {}).get('type', '?')
                        logger.info(
                            "[LOOP] Generalized loop-back DETECTED: %s (%s) → %s (%s) "
                            "(target already in completed_nodes, iteration tracking started)",
                            current_node_id, _source_node_type,
                            target_id, _target_node_type,
                        )
                        # Initialize/update loop iteration state for the source node
                        if current_node_id not in self._graph_loop_state:
                            _max_l = int(
                                conditional_data.get('max_loops', 100)
                                if node['type'] == 'conditional' else 100
                            )
                            if node['type'] != 'conditional':
                                # A loop driven by a sequence/code node around
                                # a conditional honors the conditional's
                                # max_loops cap instead of the generic 100.
                                try:
                                    for _pid in self._collect_downstream_path(
                                        current_node_id, target_id
                                    ):
                                        _pnd = self.workflow_graph.get(_pid, {}) or {}
                                        if _pnd.get('type') == 'conditional':
                                            _max_l = int(
                                                (_pnd.get('data', {}) or {}).get(
                                                    'max_loops', 100
                                                ) or 100
                                            )
                                            break
                                except Exception:
                                    pass
                            self._graph_loop_state[current_node_id] = {
                                'max_iterations': _max_l,
                                'iteration': 0,
                                'source_type': node['type'],
                            }
                        state = self._graph_loop_state[current_node_id]
                        state['iteration'] += 1
                        # No loop cap: a graph loop-back iterates until its own
                        # conditional branch exits (the TRUE branch) - the
                        # conditional's own semantics govern termination.  A
                        # hard cap here broke legitimate loops and left the
                        # loop subgraph half-settled (STALL-DEADEND).
                        # ponytail: unbounded by design; bound it in the chain's
                        # conditional if a runaway loop ever becomes a risk.
                        path_nodes = self._collect_downstream_path(current_node_id, target_id)
                        logger.info(
                            "[LOOP] Iteration %d/%d for source %s (%s) → %s (%s), "
                            "reset path: %s",
                            state['iteration'], state['max_iterations'],
                            current_node_id, _source_node_type,
                            target_id, _target_node_type,
                            list(path_nodes),
                        )
                        for nid in path_nodes:
                            completed_nodes.discard(nid)
                            self.skipped_nodes.discard(nid)
                        # Also dequeue any node that has an input from a loop-path
                        # node (e.g. Context node connected to Router's context
                        # port).  These sit on side branches and would otherwise
                        # stay in completed_nodes, never re-executing on subsequent
                        # loop iterations.
                        for _remaining_id in list(completed_nodes):
                            if _remaining_id in path_nodes:
                                continue
                            _rem_node = self.workflow_graph.get(_remaining_id, {}) or {}
                            _rem_inputs = _rem_node.get('inputs', []) or []
                            for _inp in _rem_inputs:
                                _from = (_inp or {}).get('from_node')
                                if _from in path_nodes:
                                    completed_nodes.discard(_remaining_id)
                                    self.skipped_nodes.discard(_remaining_id)
                                    logger.info(
                                        "Loop-back: also dequeued dependent node %s "
                                        "(input from loop path node %s)",
                                        _remaining_id, _from,
                                    )
                                    break
                        # Also un-skip NON-completed dependents of the loop path:
                        # a skipped node with an exec input from a path node is a
                        # shared loop member, not an unchosen-branch leftover (b4).
                        for _skipped_id in list(self.skipped_nodes):
                            _sk_node = self.workflow_graph.get(_skipped_id, {}) or {}
                            _sk_inputs = _sk_node.get('inputs', []) or []
                            for _inp in _sk_inputs:
                                if str(_inp.get('input_port', 'input')) not in ('input', '', None):
                                    continue
                                _from = (_inp or {}).get('from_node')
                                if _from in path_nodes:
                                    self.skipped_nodes.discard(_skipped_id)
                                    logger.info(
                                        "Loop-back: un-skipped dependent node %s "
                                        "(exec input from loop path node %s)",
                                        _skipped_id, _from,
                                    )
                                    break
                        # Context nodes are loop-body memory — never skipped
                        # across iterations (b4).
                        for _ctx_id, _ctx_node in self.workflow_graph.items():
                            if _ctx_node.get('type') == 'context':
                                self.skipped_nodes.discard(_ctx_id)
                        # ── Clear stale node outputs at generalized loop-back boundary ──
                        # Clear outputs for ALL node types NOT in the reset path.
                        # Nodes inside path_nodes carry fresh data from the
                        # just-completed loop iteration.  Nodes outside the path
                        # (e.g. the entry Input node, prior Conditional outputs)
                        # are stale across loop iterations.
                        _cleared_nodes = []
                        _preserved_nodes = []
                        try:
                            chain_id = getattr(self, 'chain_id', '') or ''
                            for _nid, _n in self.workflow_graph.items():
                                # Context nodes are loop-body memory: their
                                # output feeds the loop's LLM via the context
                                # port.  Clearing it here would strip the
                                # inherited/shared context on every
                                # re-iteration (the sub-chain LLM would
                                # generate without context from iteration 2
                                # onwards).
                                if _nid in path_nodes or _n.get('type') == 'context':
                                    _preserved_nodes.append(_nid)
                                    continue
                                self.port_store.clear_node(chain_id, _nid)
                                # Also clear stale legacy variable channels so the
                                # LLM node's prompt assembly doesn't read stale
                                # data from the previous iteration (Task 4b/5).
                                # Covers _output, _context AND an Input node's
                                # value channel _data — re-executed Input nodes
                                # republish their fresh value every iteration.
                                self._clear_node_legacy_vars(_nid)
                                _cleared_nodes.append(_nid)
                            self.llm_executor.variables.pop(
                                "_last_tool_result_context", None
                            )
                            logger.info(
                                "[LOOP] Stale node cleanup for iteration %d "
                                "(source: %s → %s, path: %s) — "
                                "cleared=%s, preserved=%s",
                                state['iteration'], current_node_id, target_id,
                                list(path_nodes),
                                _cleared_nodes, _preserved_nodes,
                            )
                        except Exception:
                            pass
                        # ── Dead-end loop guard ──
                        # Only re-iterate a loop-back when the pass that just
                        # finished actually DID something (a work node ran).  A
                        # decision-only pass (every branch false) is a dead end:
                        # keeping the back-edge as a "loop" here is what made the
                        # engine re-arm the review/next/submit legs and keep
                        # spinning with nothing to fill.  Settle the loop target
                        # and let the chain finish.
                        if self._loop_pass_is_dead_end(target_id):
                            logger.info(
                                "[LOOP] Loop-back %s → %s ran no work node "
                                "(decision-only pass) — dead end, ending the "
                                "chain instead of re-iterating",
                                current_node_id, target_id,
                            )
                            self.skipped_nodes.add(target_id)
                            next_nodes.remove(target_id)
                            self._loop_pass_exec = set()
                            continue

                        # ── Selection memory survives this loop-back ──
                        # It now holds the repeating-element cursor (rows already
                        # clicked); clearing it would restart the cursor on the
                        # first row every pass.  It is reset once per chain run
                        # (MultiSequencePlayer.play_chain), not per loop-back.
                        # Re-add target to ready_nodes — standard dep scanning handles the rest
                        if self._are_all_dependencies_satisfied(target_id, completed_nodes):
                            ready_nodes[target_id] = None
                        # Remove from next_nodes since loop-back handles re-queueing
                        next_nodes.remove(target_id)
                        # This pass is consumed — start a fresh pass window.
                        self._loop_pass_exec = set()
                
                # Standard output connection readiness (remaining non-loopback targets)
                for next_node_id in next_nodes:
                    if next_node_id in completed_nodes:
                        continue
                    if self._are_all_dependencies_satisfied(next_node_id, completed_nodes):
                        ready_nodes[next_node_id] = None
            else:
                # A node returning None while the stop flag is set was ABORTED,
                # not broken: the user pressed ESC (or the caller asked to
                # stop) while the node was mid-flight.  Mirror the loop-top
                # stop handler above — a user cancel is NOT a failure, so it
                # must not log ERROR, must not highlight the node as broken,
                # and must not hand the caller a False result (agent mode
                # answers "I don't know how to do that yet." on False).
                if stop_flag and stop_flag():
                    logger.info(
                        "Node %s aborted by user request (stop flag) — "
                        "ending the workflow", current_node_id,
                    )
                    chain_logger.info(
                        "Node %s ABORTED by user request. Ending workflow.",
                        current_node_id,
                    )
                    self._cleanup_run_context()
                    return True
                logger.error(f"Node {current_node_id} execution failed, stopping workflow")
                chain_logger.error(f"Node {current_node_id} FAILED execution. Stopping workflow.")
                # Invoke failure callback for visual error reporting (e.g., highlight node in GUI)
                if on_node_failed:
                    try:
                        node_type = node.get('type', 'unknown')
                        on_node_failed(current_node_id, node_type, node)
                    except Exception as cb_err:
                        logger.warning(f"on_node_failed callback error: {cb_err}")
                self._cleanup_run_context()
                return False
        
        logger.info("Workflow execution completed successfully")
        chain_logger.info("=== CHAIN RUN COMPLETED SUCCESSFULLY ===")
        self._cleanup_run_context()
        return True

    def _clear_node_legacy_vars(self, node_id):
        """Drop every flat ``node_<id>_*`` variable for a stale node.

        Loop-boundary cleanup clears the port-scoped store, but LLM prompt
        assembly reads the flat variable channels directly (an Input node's
        value lives in ``node_<id>_data``, a context node's in
        ``node_<id>_context``).  Without clearing those channels a value
        published in a previous iteration keeps being concatenated into the
        next iteration's LLM context instead of being replaced by the fresh
        value — the router then re-reads the first query on every pass.
        """
        try:
            _vars = self.llm_executor.variables
        except Exception:
            return
        for _suffix in (
            "_output", "_context", "_data",
            "_input_context", "_is_input_passthrough",
        ):
            try:
                _vars.pop(f"node_{node_id}{_suffix}", None)
            except Exception:
                pass

    def _cleanup_run_context(self):
        """Close the context database connection at the end of execution.

        Context persistence is controlled by the 'persistent' property on each node.
        Non-persistent nodes are cleared at run start in _pre_initialize_context_nodes.

        This method is idempotent — multiple calls are safe.
        """
        # Prevent double-cleanup (e.g. stop-flag return + normal exit)
        if getattr(self, '_cleanup_done', False):
            return
        self._cleanup_done = True

        # Task 13: Clear context nodes with clear_on_finish=true after chain finishes.
        # This resets the agent's memory between sessions so the next interaction
        # starts fresh instead of accumulating stale context.
        chain_id = getattr(self, 'chain_id', '') or ''
        try:
            if hasattr(self, 'workflow_graph') and self.workflow_graph:
                for nid, nd in self.workflow_graph.items():
                    if nd.get('type') != 'context':
                        continue
                    node_data = nd.get('data', {})
                    if node_data.get('clear_on_finish', False):
                        logger.info("Task 13: Clearing context node %s (clear_on_finish=true)", nid)
                        # Clear port-scoped store entries
                        self.port_store.clear_node(chain_id, nid)
                        # Clear ContextDatabase entries for this node
                        try:
                            context_db = getattr(self, '_context_db', None)
                            if context_db is None:
                                from AI.context_database import ContextDatabase
                                context_db = ContextDatabase()
                                self._context_db = context_db
                            if context_db and chain_id:
                                rows = context_db.clear(chain_id, nid)
                                if rows == 0:
                                    logger.warning(
                                        "Task 13: clear_on_finish node %s — "
                                        "0 rows deleted (chain_id=%s) — "
                                        "possible mismatch?",
                                        nid, chain_id,
                                    )
                                else:
                                    logger.info(
                                        "Task 13: Cleared %d entries for %s/%s",
                                        rows, chain_id, nid,
                                    )
                            # Identity-feed reset for per-run OWNERS.  The feed
                            # (__shared_copy__/<owner node id>) accumulates
                            # contributions from subchain clones while the owner
                            # runs; when the owning per-run chain finishes, the
                            # session's feed ends with it.  A clone's finish
                            # never clears the feed - the owning chain may
                            # still be running (or may run again later).
                            try:
                                _is_shared = bool(
                                    str(node_data.get('shared_context_chain_file') or '').strip()
                                )
                                if not _is_shared and context_db:
                                    _feed_rows = context_db.clear(
                                        ContextMixin._SHARED_COPY_NS, nid,
                                    )
                                    logger.info(
                                        "Task 13: cleared %d shared-feed "
                                        "row(s) for owner node %s after its "
                                        "per-run chain finished",
                                        _feed_rows, nid,
                                    )
                            except Exception:
                                pass
                        except Exception as db_err:
                            logger.warning(
                                "Task 13: context_db.clear failed for %s/%s: %s",
                                chain_id, nid, db_err,
                            )
        except Exception as e:
            logger.warning("Task 13: context clear_on_finish cleanup failed: %s", e)

        try:
            context_db = getattr(self, '_context_db', None)
            if context_db is not None:
                self._context_db = None
                logger.debug("Released ContextDatabase instance")
        except Exception as e:
            logger.warning(f"Failed to release ContextDatabase: {e}")

        # Kill cached MCP server processes when the outermost chain finishes.
        # Nested chains (chain imports) decrement the counter but don't cleanup.
        try:
            _mcp_cleanup_exit()
        except Exception as e:
            logger.warning(f"Failed to cleanup MCP servers: {e}")

    def _record_last_node_summary(self, node_id, node, conditional_result):
        try:
            summary = {
                "node_id": str(node_id),
                "type": str(node.get('type') or ''),
                "label": "",
                "description": "",
                "criteria": None,
                "result": None
            }
            try:
                data = node.get('data', {}) if isinstance(node, dict) else {}
                summary["label"] = data.get('name') or data.get('label') or ""
                summary["description"] = data.get('description') or ""
            except Exception:
                pass
            if summary["type"] == "conditional":
                summary["result"] = bool(conditional_result)
                try:
                    data = node.get('data', {}) if isinstance(node, dict) else {}
                    criteria = {}
                    condition_type = data.get('condition_type') or data.get('trigger_type')
                    if condition_type:
                        criteria["condition_type"] = condition_type
                    image_path = data.get('image_path')
                    if image_path:
                        criteria["image_path"] = image_path
                    ocr_text = data.get('ocr_text') or data.get('target_text')
                    if ocr_text:
                        criteria["ocr_text"] = ocr_text
                    threshold = data.get('threshold') or data.get('confidence')
                    if threshold is not None:
                        criteria["threshold"] = threshold
                    timeout = data.get('timeout') or data.get('wait_time')
                    if timeout is not None:
                        criteria["timeout"] = timeout
                    app_ctx = data.get('app_context') or data.get('focus_app') or data.get('app')
                    if isinstance(app_ctx, dict) and app_ctx:
                        criteria["app_context"] = app_ctx
                    summary["criteria"] = criteria if criteria else None
                except Exception:
                    pass
            elif summary["type"] == "input" and conditional_result is not None:
                # Branch-capable Input (decision toggle / routed yes/no
                # question): record the routing outcome + its criterion.
                summary["result"] = bool(conditional_result)
                try:
                    data = node.get('data', {}) if isinstance(node, dict) else {}

                    def _on(v):
                        return str(v).strip().lower() in ('true', '1', 'yes', 'on')

                    criteria = {}
                    if _on(data.get('decision_mode')):
                        criteria["decision_criterion"] = str(data.get('decision_criterion') or '')
                        criteria["decision_evaluator"] = str(data.get('decision_evaluator') or 'llm')
                    if _on(data.get('route_on_answer')):
                        criteria["route_on_answer"] = True
                    summary["criteria"] = criteria if criteria else None
                except Exception:
                    pass
            elif summary["type"] == "code":
                try:
                    output = self.port_store.get_output(self.chain_id, node_id, 'output')
                    summary["result"] = output
                except Exception:
                    summary["result"] = None
            elif summary["type"] == "llm":
                try:
                    data = node.get('data', {}) if isinstance(node, dict) else {}
                    llm_conf = data.get('llm_configuration', {}) or data
                except Exception:
                    llm_conf = {}
                query = ""
                try:
                    query = llm_conf.get('prompt') or data.get('prompt') or ""
                except Exception:
                    query = ""
                response = None
                try:
                    output_var = llm_conf.get('output_variable', 'llm_output')
                    response = self.llm_executor.get_variable(output_var)
                except Exception:
                    response = None
                summary["result"] = {
                    "query": query,
                    "response": response
                }
            elif summary["type"] == "output":
                try:
                    output = self.port_store.get_output(self.chain_id, node_id, 'output')
                    summary["result"] = output
                except Exception:
                    summary["result"] = None
            self.last_node_summary = summary
            try:
                self.llm_executor.set_variable("chain_last_node_summary", summary)
            except Exception:
                pass
        except Exception:
            pass

    def _skip_tool_providers(self):
        """Skip nodes that are only connected via data ports (tools/context/etc).

        These are subroutines of the LLM node and should not be executed
        as standalone graph nodes.
        """
        logger.info("[SKIP] Beginning tool provider scan across %d nodes", len(self.workflow_graph))
        for nid, node in list(self.workflow_graph.items()):
            if nid in self.skipped_nodes:
                continue
            inputs = node.get('inputs') or []
            if not inputs:
                continue  # Starting node — not a tool provider
            if node.get('type') == 'context':
                continue  # Context nodes are graph nodes, never LLM subroutines
            has_exec_input = any(
                inp.get('input_port', 'input') in ('input', '', None)
                for inp in inputs
            )
            if not has_exec_input:
                self.skipped_nodes.add(nid)
                _tp_ports = [inp.get('input_port', '?') for inp in inputs]
                _tp_sources = [inp.get('from_node', '?') for inp in inputs]
                logger.info(
                    "[SKIP] Tool provider scan: node %s (type=%s) skipped — "
                    "input_ports=%s, from_nodes=%s, data_keys=%s",
                    nid, node.get('type'), _tp_ports, _tp_sources,
                    list(node.get('data', {}).keys())[:5],
                )
        logger.info("[SKIP] Tool provider scan complete — total skipped: %d nodes", len(self.skipped_nodes))

    def _select_next_node(self, ready_nodes):
        # Deterministic selection (b3): context nodes first — they must execute
        # as soon as their exec input completes since downstream LLMs read their
        # context — otherwise FIFO insertion order.  The queue is an OrderedDict,
        # never a set, so hash-order starvation is impossible.
        for candidate in ready_nodes:
            if self.workflow_graph.get(candidate, {}).get('type') == 'context':
                del ready_nodes[candidate]
                return candidate
        chosen = next(iter(ready_nodes))
        del ready_nodes[chosen]
        return chosen

    def _apply_extra_delay(self, node, stop_flag):
        try:
            data = node.get('data', {})
            delay = float(data.get('extra_delay', 0) or 0)
        except Exception:
            delay = 0
        if delay > 0:
            logger.info(f"Extra delay after node: {delay} seconds")
            elapsed = 0.0
            while elapsed < delay:
                if stop_flag and stop_flag():
                    logger.info("Workflow execution stopped during extra delay")
                    return
                sleep_time = min(0.1, delay - elapsed)
                time.sleep(sleep_time)
                elapsed += sleep_time

    # Node types that DO work when executed.  A graph loop pass that runs none
    # of these only walked outputs/conditionals - it has nothing to repeat.
    _WORK_NODE_TYPES = frozenset({
        'sequence', 'web_sequence', 'form_filler', 'llm', 'code', 'mcp',
        'handle', 'input', 'chain_import', 'orchestrator', 'container',
    })

    def _loop_pass_is_dead_end(self, target_id):
        """True when a just-finished loop pass is a dead end, not a loop.

        A back-edge whose pass executed only outputs/conditionals (no work
        node) can never make progress, so re-iterating it just spins - e.g. a
        decision ladder whose every branch evaluates false.  Such a pass must
        end the chain instead of being treated as a repeatable loop.
        """
        if self.workflow_graph.get(target_id, {}).get('type') != 'conditional':
            return False
        return not any(
            (self.workflow_graph.get(_p, {}) or {}).get('type') in self._WORK_NODE_TYPES
            for _p in self._loop_pass_exec
        )

    def _collect_branch_nodes(self, conditional_id):
        """
        Collect all node IDs reachable from a conditional node's output branches
        (both true and false).  This is used to clean up completed_nodes and
        skipped_nodes when looping back a graph-based conditional loop.
        """
        try:
            from collections import deque
        except Exception:
            return set()
        node = self.workflow_graph.get(conditional_id, {})
        connections = node.get('connections', {}) or {}

        starts = []
        if isinstance(connections, dict):
            for port in ('true', 'false'):
                for c in connections.get(port, []):
                    tid = c.get('node_id') or c.get('target_node_id')
                    if tid:
                        starts.append(tid)
        elif isinstance(connections, list):
            for c in connections:
                tid = c.get('target_node_id') or c.get('node_id')
                if tid:
                    starts.append(tid)

        seen = set()
        q = deque(starts)
        while q:
            nid = q.popleft()
            if not nid or nid in seen:
                continue
            seen.add(nid)
            nd = self.workflow_graph.get(nid, {})
            conns = nd.get('connections', {}) or {}
            if isinstance(conns, dict):
                for _, lst in conns.items():
                    if not isinstance(lst, list):
                        continue
                    for c in lst:
                        tid = c.get('node_id') or c.get('target_node_id')
                        if tid and tid not in seen:
                            q.append(tid)
            elif isinstance(conns, list):
                for c in conns:
                    tid = c.get('target_node_id') or c.get('node_id')
                    if tid and tid not in seen:
                        q.append(tid)
        return seen

    def _collect_downstream_path(self, source_id, target_id):
        """
        Collect all node IDs on the forward path from target_id up to
        (and including) source_id via outgoing connections.

        Two-phase approach to avoid over-collection of side-branch nodes:
        1. Reverse BFS from source_id to identify all nodes that can actually
           reach the loop source (the loop-cycle set).
        2. Forward BFS from target_id restricted to only those nodes.

        Returns a set of node IDs forming the loop subgraph that needs to
        be reset for re-execution.
        """
        from collections import deque
        if target_id not in self.workflow_graph:
            return set()

        # Phase 1: Reverse BFS — find nodes that can reach the source
        can_reach_source = set()
        rq = deque([source_id])
        while rq:
            nid = rq.popleft()
            if nid in can_reach_source:
                continue
            can_reach_source.add(nid)
            # Walk backwards: find nodes that point TO nid via execution ports
            # only — data ports never define loop paths (b4).
            for oid, onode in self.workflow_graph.items():
                if oid in can_reach_source:
                    continue
                conns = onode.get('connections', {}) or {}
                if isinstance(conns, dict):
                    for port_name, port_conns in conns.items():
                        if port_name not in ('output', 'true', 'false', 'error'):
                            continue
                        if not isinstance(port_conns, list):
                            continue
                        for c in port_conns:
                            tid = c.get('node_id') or c.get('target_node_id')
                            if tid == nid:
                                rq.append(oid)
                                break

        # Phase 2: Forward BFS from target_id, restricted to can_reach_source
        seen = set()
        q = deque([target_id])
        while q:
            nid = q.popleft()
            if not nid or nid in seen:
                continue
            seen.add(nid)
            # Stop traversing forward from source (include it but don't go past)
            if nid == source_id:
                continue
            nd = self.workflow_graph.get(nid, {})
            conns = nd.get('connections', {}) or {}
            if isinstance(conns, dict):
                for port_name, port_connections in conns.items():
                    if port_name not in ('output', 'true', 'false', 'error'):
                        continue
                    if not isinstance(port_connections, list):
                        continue
                    for c in port_connections:
                        tid = c.get('node_id') or c.get('target_node_id')
                        if tid and tid not in seen and tid in can_reach_source:
                            q.append(tid)
            elif isinstance(conns, list):
                for c in conns:
                    op = str(c.get('output_port') or 'output').lower()
                    if op not in ('output', 'true', 'false', 'error'):
                        continue
                    tid = c.get('target_node_id') or c.get('node_id')
                    if tid and tid not in seen and tid in can_reach_source:
                        q.append(tid)
        return seen

    def _maybe_loop_graph_conditional(self, completed_nodes, stop_flag):
        """
        Check whether any tracked graph-based conditional loop should iterate
        again.  If so, clean up state and re-queue the conditional.

        Returns True if a loop was started, False otherwise.
        """
        if not self._graph_loop_state:
            logger.debug("[LOOP] _maybe_loop_graph_conditional: no loop state tracked, skipping")
            return False

        stale = []
        logger.info(
            "[LOOP] _maybe_loop_graph_conditional checking %d tracked loop(s): %s",
            len(self._graph_loop_state),
            {cid: {'iter': s.get('iteration', 0), 'max': s.get('max_iterations', '∞')}
             for cid, s in self._graph_loop_state.items()},
        )
        for cid, state in list(self._graph_loop_state.items()):
            # max_loops <= 0 means unlimited iterations
            if state['max_iterations'] > 0 and state['iteration'] >= state['max_iterations']:
                logger.info(
                    "[LOOP] Conditional %s: max_iterations (%d) reached at iteration %d — "
                    "marking stale",
                    cid, state['max_iterations'], state['iteration'],
                )
                stale.append(cid)
                continue

            # Only genuine graph-based loops (a while/until loop_type with no
            # internal sequence/chain file) may re-iterate here.  A plain branch
            # conditional whose FALSE port merely points back to an earlier node
            # is a "loop only while the branch stays FALSE" construct that the
            # inline generalized loop-back already drives: it re-queues the
            # back-edge node exactly once per FALSE evaluation and must STOP the
            # moment the TRUE branch is taken (otherwise the workflow "keeps
            # going back" to the back-edge node until max_iterations).
            _guard_node = self.workflow_graph.get(cid, {}) or {}
            _guard_data = _guard_node.get('data', {}) or {}
            _guard_loop_type = str(_guard_data.get('loop_type', '') or '').strip().lower()
            _guard_seq_file = str(
                _guard_data.get('sequence_file', '')
                or _guard_data.get('chain_file', '')
                or ''
            ).strip()
            _is_genuine_graph_loop = (
                _guard_loop_type in ('while_present', 'until_present', 'while_absent', 'until_absent')
                and not _guard_seq_file
            )
            if not _is_genuine_graph_loop:
                logger.info(
                    "[LOOP] Conditional %s: plain branch conditional (loop_type=%r) — "
                    "not re-iterating; inline loop-back governs iteration",
                    cid, _guard_loop_type,
                )
                stale.append(cid)
                continue

            # Non-conditional loop sources (sequence/code/output) are driven by
            # the inline generalized loop-back, which re-queues path nodes and
            # enforces max_iterations.  Keep the state for iteration bookkeeping
            # instead of discarding it — premature removal would lose cap
            # continuity across iterations (b4).
            if state.get('source_type') != 'conditional':
                logger.debug(
                    "[LOOP] Conditional %s: source_type=%s (not 'conditional') — "
                    "iteration driven inline by generalized loop-back",
                    cid, state.get('source_type'),
                )
                continue

            should_loop = True
            _loop_termination_reason = ""
            cond_node = None
            fresh_result = None
            try:
                cond_node = self.workflow_graph.get(cid)
                if cond_node:
                    cond_data = cond_node.get('data', {})
                    loop_type = str(cond_data.get('loop_type', '') or '').strip().lower()
                    loop_position = str(cond_data.get('loop_position', 'pre') or '').strip().lower()
                    
                    # Re-evaluate the condition fresh
                    fresh_result = self._evaluate_conditional(cond_data, stop_flag, cond_node.get('inputs', []))
                    max_str = f"/{state['max_iterations']}" if state['max_iterations'] > 0 else "/∞"
                    logger.info(
                        "[LOOP] Graph loop conditional %s: iteration %d+1%s, "
                        "loop_type=%s, loop_position=%s, "
                        "fresh_re_evaluation_result=%s",
                        cid, state['iteration'], max_str,
                        loop_type, loop_position,
                        fresh_result,
                    )
                    
                    # For loop_position 'post' conditionals, the condition result
                    # from the PREVIOUS iteration determines whether we continue.
                    # For 'pre' (and default), the FRESH result determines it.
                    if loop_position == 'post':
                        prev_result = cond_node.get('last_conditional_result', fresh_result)
                        if not prev_result:
                            should_loop = False
                            _loop_termination_reason = (
                                f"loop_position='post' and prev_result={prev_result} is falsy"
                            )
                    elif not fresh_result:
                        should_loop = False
                        _loop_termination_reason = (
                            f"loop_position='{loop_position}' and fresh_result={fresh_result} is falsy"
                        )
                        
                    cond_node['last_conditional_result'] = fresh_result
                    state['loop_result'] = fresh_result
            except Exception as e:
                logger.error(f"Error re-evaluating graph loop condition: {e}", exc_info=True)
                should_loop = False
                _loop_termination_reason = f"exception during re-evaluation: {e}"

            if not should_loop:
                logger.info(
                    "[LOOP] Conditional %s: loop termination triggered after %d iterations. "
                    "Reason: %s",
                    cid, state.get('iteration', 0) + 1,
                    _loop_termination_reason,
                )
                stale.append(cid)
                continue

            state['iteration'] += 1
            logger.info(
                "[LOOP] >>> Graph loop ITERATION %d STARTING for conditional %s (loop_type=%s, result=%s)",
                state['iteration'], cid,
                cond_node.get('data', {}).get('loop_type', '?') if cond_node else '?',
                fresh_result,
            )

            # Collect all branch nodes to remove them from completed_nodes
            # so they can execute again in the new iteration.
            branch_nodes = self._collect_branch_nodes(cid)
            logger.info(
                "[LOOP] Conditional %s: collecting branch nodes for reset (%d nodes): %s",
                cid, len(branch_nodes), list(branch_nodes),
            )
            # Clear skipped nodes for this conditional's branches
            self.skipped_nodes.difference_update(branch_nodes)
            for bid in branch_nodes:
                completed_nodes.discard(bid)

            # Also remove the conditional itself from completed_nodes so that
            # the main loop sees it as a remaining node and re-queues it.
            completed_nodes.discard(cid)
            logger.info(
                "[LOOP] Conditional %s: removed %d branch nodes + conditional from completed_nodes",
                cid, len(branch_nodes),
            )

            # Selection memory is NOT cleared here: it holds the repeating-
            # element cursor, which must advance (not restart) across loop
            # iterations, mirroring the web entity cursor.

            # Mark skipped nodes based on the NEW condition result
            try:
                chosen_port = 'true' if fresh_result else 'false'
                unchosen_port = 'false' if fresh_result else 'true'
                self._mark_skipped(cid, chosen_port, unchosen_port, completed_nodes)
            except Exception:
                pass

            # ── Clear stale node outputs at loop boundary ──
            # Clear outputs for ALL node types NOT in the branch (reset path).
            # Nodes inside branch_nodes carry fresh data from the
            # just-completed loop iteration needed by LLM1 before their
            # own re-execution.  Entry-path nodes outside the branch are stale.
            _loop_cleared_nodes = []
            _loop_preserved_nodes = []
            try:
                chain_id = getattr(self, 'chain_id', '') or ''
                for _nid, _node in self.workflow_graph.items():
                    if _nid not in branch_nodes and _node.get('type') != 'context':
                        self.port_store.clear_node(chain_id, _nid)
                        # Same stale-channel cleanup as the loop-back boundary:
                        # the flat ``node_<id>_*`` variables must not survive a
                        # graph-conditional iteration either.
                        self._clear_node_legacy_vars(_nid)
                        _loop_cleared_nodes.append(_nid)
                    else:
                        _loop_preserved_nodes.append(_nid)
                self.llm_executor.variables.pop(
                    "_last_tool_result_context", None
                )
                logger.info(
                    "[LOOP] Stale node cleanup for graph conditional iteration %d "
                    "(branch: %s) — cleared=%s, preserved=%s",
                    state['iteration'], list(branch_nodes),
                    _loop_cleared_nodes, _loop_preserved_nodes,
                )
            except Exception:
                pass

            return True

        # Remove stale entries
        for cid in stale:
            try:
                last_iter = self._graph_loop_state.get(cid, {}).get('iteration', 0)
                logger.info(
                    "[LOOP] === Graph loop COMPLETED for conditional %s after %d iterations ===",
                    cid, last_iter,
                )
            except Exception:
                pass
            try:
                del self._graph_loop_state[cid]
            except Exception:
                pass

        return False


def _render_web_page_data(data):
    """Render the extracted page items dict as labeled text for LLM/legacy
    variable consumption (the structured dict stays on the ctx_out port)."""
    parts = []
    for key, val in (data or {}).items():
        if val is not None and str(val).strip():
            parts.append(f"[{key}]\n{val}")
    return "\n\n".join(parts)

