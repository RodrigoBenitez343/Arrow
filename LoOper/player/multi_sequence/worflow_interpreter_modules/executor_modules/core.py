from ....config import logger
from ..common import chain_logger
import time

from .dependencies import DependenciesMixin
from .sequence_ops import SequenceMixin
from .conditional_ops import ConditionalMixin
from .llm_ops import LLMMixin
from .tts_ops import TTSMixin
from .chain_ops import ChainImportMixin
from .code_ops import CodeMixin
from .container_ops import ContainerMixin

class WorkflowExecutor(DependenciesMixin, SequenceMixin, ConditionalMixin, LLMMixin, TTSMixin, ChainImportMixin, CodeMixin, ContainerMixin):
    """Executes workflow graphs with proper dependency resolution and node execution"""
    
    def __init__(self, workflow_graph, sequence_executor, llm_executor, fallback_handler):
        self.workflow_graph = workflow_graph
        self.sequence_executor = sequence_executor
        self.llm_executor = llm_executor
        self.fallback_handler = fallback_handler
        self.skipped_nodes = set()
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
        self._llm_selected_next = None
        self.last_node_summary = None
        self.last_node_id = None
    
    def reset(self):
        """Resets the executor state for a new run."""
        self.skipped_nodes = set()
        self._llm_selected_next = None
        self._code_retry_counts = {}
        self.last_node_summary = None
        self.last_node_id = None
        # We don't reset runtime environment to avoid reloading venv unnecessarily,
        # but we reset the initialization flags to ensure checks happen if needed.
        # actually, keeping runtime initialized is better for performance.
        logger.info("WorkflowExecutor state reset")

    def execute_workflow(self, stop_flag=None, action_handlers=None):
        """
        Executes the workflow graph with proper dependency resolution.
        Nodes with multiple inputs will only execute after all their dependencies are satisfied.
        
        Args:
            stop_flag (callable): Optional function that returns True when execution should stop
            action_handlers: Action handlers for text writing and other actions
            
        Returns:
            bool: True if workflow completed successfully, False otherwise
        """
        if not self.workflow_graph:
            logger.warning("No workflow graph to execute")
            return False
        
        # Initialize runtime environment immediately but track if we need full setup
        # We'll do minimal initialization first, then full setup only when needed
        self._runtime_initialized = False
        self._runtime_needed = False
            
        # Track completed nodes and nodes ready for execution
        completed_nodes = set()
        ready_nodes = set()
        
        # Find all starting nodes (nodes with no inputs)
        starting_nodes = self._find_all_starting_nodes()
        if not starting_nodes:
            logger.warning("No starting nodes found in workflow graph")
            return False
        
        logger.info(f"Starting workflow execution from nodes: {starting_nodes}")
        chain_logger.info(f"=== CHAIN RUN STARTED ===")
        chain_logger.info(f"Starting nodes: {starting_nodes}")
        logger.debug(f"Workflow graph has {len(self.workflow_graph)} nodes: {list(self.workflow_graph.keys())}")
        ready_nodes.update(starting_nodes)
        last_completed_node_id = None
        
        while True:
            # Check stop flag
            if stop_flag and stop_flag():
                logger.info("Workflow execution stopped by user request")
                return True
            # Defer all execution while any LLM node is actively typing
            try:
                vars_snapshot = self.llm_executor.get_all_variables()
            except Exception:
                vars_snapshot = {}
            typing_active = any(k.endswith('_writing') and vars_snapshot.get(k) for k in vars_snapshot.keys())
            if typing_active:
                try:
                    time.sleep(0.05)
                except Exception:
                    pass
                # Refresh readiness so nodes queue up but do not execute until typing ends
                for node_id, node_data in self.workflow_graph.items():
                    if node_id in completed_nodes:
                        continue
                    if self._are_all_dependencies_satisfied(node_id, completed_nodes):
                        ready_nodes.add(node_id)
                continue
            if not ready_nodes:
                remaining = [nid for nid in self.workflow_graph.keys() if nid not in completed_nodes and nid not in self.skipped_nodes]
                if not remaining:
                    logger.info("Workflow execution completed successfully")
                    return True

                for nid in remaining:
                    if self._are_all_dependencies_satisfied(nid, completed_nodes):
                        ready_nodes.add(nid)

                if ready_nodes:
                    continue

                logger.error(f"Workflow execution stalled. Remaining nodes not ready: {remaining}")
                return False
            
            current_node_id = self._select_next_node(ready_nodes)
            
            if current_node_id in completed_nodes:
                continue  # Skip already completed nodes
            
            if current_node_id in self.skipped_nodes:
                logger.info(f"Skipping node {current_node_id} (in skipped_nodes)")
                continue

            if current_node_id not in self.workflow_graph:
                logger.warning(f"Node {current_node_id} not found in workflow graph")
                continue
            
            node = self.workflow_graph[current_node_id]
            logger.info(f"Executing node {current_node_id} (type: {node['type']})")
            chain_logger.info(f"EXECUTING NODE: {current_node_id} (Type: {node['type']})")
            try:
                from rich.table import Table
                from rich.panel import Panel
                con = getattr(self.llm_executor, '_rich_console', None)
                if con:
                    inf = Table(title="Node Execution", show_header=True, header_style="bold blue")
                    inf.add_column("Field", style="bold")
                    inf.add_column("Value")
                    inf.add_row("Node ID", str(current_node_id))
                    inf.add_row("Type", str(node.get('type')))
                    ins = node.get('inputs', []) or []
                    inf.add_row("Inputs", str(len(ins)))
                    con.print(inf)
                    if ins:
                        tbl = Table(title="Inputs", show_header=True, header_style="bold cyan")
                        tbl.add_column("From Node", style="bold")
                        tbl.add_column("Port")
                        tbl.add_column("Output Type")
                        for i in ins:
                            tbl.add_row(str(i.get('from_node')), str(i.get('input_port')), str(i.get('output_type')))
                        con.print(tbl)
            except Exception:
                pass
            
            # Execute the node based on its type
            success = False
            conditional_result = None
            
            if node['type'] == 'sequence':
                success = self._execute_sequence_node(node, stop_flag) is not None
            elif node['type'] == 'conditional':
                # For conditional nodes, evaluate the condition and store the result
                conditional_data = node['data']
                condition_type = str(conditional_data.get('condition_type', 'presence') or '').strip().lower()
                if condition_type in ('loop', 'while_present', 'until_present', 'while_absent', 'until_absent'):
                    conditional_result = self._execute_conditional_loop(conditional_data, stop_flag)
                else:
                    conditional_result = self._evaluate_conditional(conditional_data, stop_flag, node.get('inputs', []))
                success = True  # Conditional evaluation always succeeds
                logger.info(f"Conditional evaluation result: {conditional_result}")
                # Store the result in the node for later reference
                node['last_conditional_result'] = conditional_result
            elif node['type'] == 'llm':
                success = self._execute_llm_node(node, stop_flag, action_handlers) is not None
            elif node['type'] == 'tts':
                success = self._execute_tts_node(node, stop_flag) is not None
            elif node['type'] == 'chain_import':
                success = self._execute_chain_import_node(node, stop_flag) is not None
            elif node['type'] == 'code':
                success = self._execute_code_node(node, stop_flag) is not None
            elif node['type'] == 'container':
                success = self._execute_container_node(node, stop_flag) is not None
            else:
                logger.warning(f"Unknown node type: {node['type']}")
                success = False
            
            if success:
                # Mark node as completed
                completed_nodes.add(current_node_id)
                last_completed_node_id = current_node_id
                self.last_node_id = current_node_id
                self._record_last_node_summary(current_node_id, node, conditional_result)
                logger.info(f"Node {current_node_id} completed successfully")
                chain_logger.info(f"Node {current_node_id} COMPLETED successfully")
                self._apply_extra_delay(node, stop_flag)
                
                # Handle conditional branching
                if node['type'] == 'conditional':
                    # For conditional nodes, determine which branch to follow
                    branch_node = self._get_conditional_branch_node(node, conditional_result)
                    if branch_node:
                        # Mark the chosen branch in the conditional node
                        node['chosen_branch_node'] = branch_node
                        
                        # Fix: Check if node['data'] needs the update too since _are_all_dependencies_satisfied 
                        # often looks at the node config from self.workflow_graph directly which may be a copy
                        if 'data' in node:
                            node['data']['chosen_branch_node'] = branch_node
                            
                        # Add the chosen branch node to ready nodes if dependencies are satisfied
                        if self._are_all_dependencies_satisfied(branch_node, completed_nodes):
                            ready_nodes.add(branch_node)
                            logger.info(f"Conditional branch chosen: {branch_node}")
                        
                        chosen_port = 'true' if conditional_result else 'false'
                        unchosen_port = 'false' if conditional_result else 'true'
                        try:
                            self._mark_skipped(current_node_id, chosen_port, unchosen_port, completed_nodes)
                        except Exception:
                            pass
                    else:
                        # No explicit branch connection - this is fine for conditionals with only one output.
                        # The full graph scan will still discover reachable nodes downstream.
                        logger.info(f"Conditional {current_node_id} has no explicit branch target, continuing with full scan")
                
                # Standard output connection handling
                next_nodes = []
                if node['type'] == 'conditional':
                    # We already handled branch_node above, just make sure it's in next_nodes
                    # so the fallback/generic readiness check doesn't miss it
                    if branch_node:
                        next_nodes.append(branch_node)
                elif node['type'] == 'llm':
                    # LLM-specific chaining rules
                    next_nodes = self._get_output_connected_nodes(current_node_id)
                    has_pending_non_llm_outputs = False
                    for nn in next_nodes:
                        tn = self.workflow_graph.get(nn, {})
                        if tn.get('type') != 'llm' and nn not in completed_nodes:
                            has_pending_non_llm_outputs = True
                            break
                    
                    for next_node_id in next_nodes:
                        if next_node_id in completed_nodes:
                            continue
                        next_node = self.workflow_graph.get(next_node_id, {})
                        if next_node.get('type') == 'llm':
                            if has_pending_non_llm_outputs:
                                continue
                            if self._is_direct_llm_concatenation(current_node_id, next_node_id):
                                if self._are_all_dependencies_satisfied(next_node_id, completed_nodes):
                                    ready_nodes.add(next_node_id)
                        else:
                            if self._are_all_dependencies_satisfied(next_node_id, completed_nodes):
                                ready_nodes.add(next_node_id)
                elif node['type'] != 'conditional':
                    # Non-LLM, non-conditional nodes
                    port = None
                    if node.get('type') == 'code':
                        port = node.get('last_output_port') or 'output'
                    
                    connections = node.get('connections', {})
                    if port and isinstance(connections, dict):
                        for c in (connections.get(port) or []):
                            tid = c.get('node_id')
                            if tid:
                                next_nodes.append(tid)
                    else:
                        next_nodes = self._get_output_connected_nodes(current_node_id)
                    
                    for next_node_id in next_nodes:
                        if next_node_id not in completed_nodes:
                            if self._are_all_dependencies_satisfied(next_node_id, completed_nodes):
                                ready_nodes.add(next_node_id)

                # Always check for newly ready nodes (including global scan)
                newly_ready = self._find_newly_ready_nodes(current_node_id, completed_nodes)
                ready_nodes.update(newly_ready)
                
                # Handle LLM selected tool node prioritization
                if node['type'] == 'llm':
                    try:
                        sel = getattr(self, '_llm_selected_next', None)
                        self._llm_selected_next = None
                        if sel and sel in self.workflow_graph and sel not in completed_nodes:
                            if self._are_all_dependencies_satisfied(sel, completed_nodes):
                                ready_nodes.add(sel)
                                logger.info(f"Selected tool node {sel} is now ready for execution")
                    except Exception:
                        pass
            else:
                logger.error(f"Node {current_node_id} execution failed, stopping workflow")
                chain_logger.error(f"Node {current_node_id} FAILED execution. Stopping workflow.")
                return False
        
        logger.info("Workflow execution completed successfully")
        chain_logger.info("=== CHAIN RUN COMPLETED SUCCESSFULLY ===")
        return True

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
            elif summary["type"] == "code":
                try:
                    output = self.llm_executor.get_variable(f"node_{node_id}_output")
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
            else:
                summary["result"] = None
            self.last_node_summary = summary
            try:
                self.llm_executor.set_variable("chain_last_node_summary", summary)
            except Exception:
                pass
        except Exception:
            pass

    def _select_next_node(self, ready_nodes):
        priority_map = {
            'code': 0,
            'sequence': 1,
            'conditional': 2,
            'tts': 3,
            'chain_import': 4,
            'llm': 5,
        }
        candidates = list(ready_nodes)
        def _key(nid):
            nd = self.workflow_graph.get(nid, {})
            t = nd.get('type')
            p = priority_map.get(t, 5)
            idx = nd.get('index', 0)
            return (p, idx, str(nid))
        candidates.sort(key=_key)
        chosen = candidates[0]
        try:
            ready_nodes.remove(chosen)
        except Exception:
            pass
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
