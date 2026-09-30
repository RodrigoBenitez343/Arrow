import logging
logger = logging.getLogger(__name__)
# multi_sequence_player.py
"""
Multi-sequence player for executing chains of automation sequences with advanced conditional fallbacks.
"""

import sys
import json
import time
import os
from .base_bot import SeleniumBot
from .sequence_player import SequencePlayer
from .json_cache import preload_chain, get_cache
from .chain_migrations import migrate_legacy_tts
from .multi_sequence import (
    ConditionalFallbackHandler,
    WorkflowGraphBuilder,
    WorkflowNavigator,
    LLMExecutor,
    SequenceExecutor,
    WorkflowExecutor
)
try:
    from AI.api import _cleanup_ollama_runners
except Exception:
    _cleanup_ollama_runners = None


def _safe_cleanup_ollama_runners():
    """Clean up competing Ollama runners — but NEVER in direct engine mode.

    The exported agent (ARROW_DIRECT_ENGINE=1) is a fully offline, API-less
    artifact: hitting localhost:11434 on every chain play is both useless
    and a network side effect that must not exist.
    """
    if os.environ.get("ARROW_DIRECT_ENGINE") == "1":
        return
    if _cleanup_ollama_runners:
        try:
            _cleanup_ollama_runners()
        except Exception:
            pass


def _web_profile_key_for(chain_config, chain_file_path):
    """The app-wide shared web browser scope for a chain run.

    Every chain - saved or not, GUI or agent/scheduled/CLI - plays on the
    SAME durable Chrome profile, so cookies/logins recorded once (e.g. a
    Google login made during recording) are present in every later run and
    survive app restarts.  Per-chain scopes could not work for chains that
    are never saved to a file: their identity changed every app session, so
    the session was lost on every restart.  Pages are never restored - only
    cookies/history persist.

    The arguments are kept for API compatibility (they once derived a
    per-chain key); the result is always the shared scope.  Callers that
    need a fully isolated browser (playback while a web recording is
    capturing) simply do not call this and leave the executor key-less.
    """
    return "default"


# ConditionalFallbackHandler, WorkflowGraphBuilder, WorkflowNavigator, 
# LLMExecutor, and SequenceExecutor are now imported from the multi_sequence module


class MultiSequencePlayer(SeleniumBot):
    """
    Plays multiple sequences with conditional branching support, as defined in a chain configuration file.
    This allows for looping, chaining, and conditional branching of automation tasks.
    
    This class now uses modularized components for better maintainability.
    """
    def __init__(self, chain_config, chain_file_path=None, variables=None,
                 agent_query="", llamacpp_server_url="",
                 ask_user_callback=None, ask_user_v2_callback=None, on_output_ready=None,
                 web_chain_key=None, chain_identity=None, web_isolated=False):
        """
        Initializes the multi-sequence player.

        Args:
            chain_config (dict or list): Either a legacy list format or new dict format with:
                                        - sequences: list of sequence configurations
                                        - conditional_nodes: list of conditional node configurations
            chain_file_path (str, optional): Path to the chain file for resolving relative sequence paths
            variables (dict, optional): Shared variables dictionary from parent player (for nested chains)
            agent_query (str, optional): Original user query when executed by the agent
            llamacpp_server_url (str, optional): URL of an already-running llama.cpp server
                                        (e.g. http://localhost:9083) so Input nodes can reuse
                                        the same loaded model.
            on_output_ready (callable, optional): Fire-and-forget callback for displaying
                                        output node results mid-execution. Receives
                                        (label, content) strings.
            web_chain_key (str, optional): Web browser scope.  When set (GUI
                                        playback), the chain's web sequence
                                        nodes run on the app-wide shared
                                        durable browser (cookies/history
                                        persist across runs and restarts) and
                                        it stays open afterwards.
            web_isolated (bool, optional): True to force a disposable,
                                        isolated browser for this run even
                                        when a chain scope is derivable - used
                                        when a web recording is actively
                                        capturing on the shared browser.
        """
        super().__init__()
        
        # Store chain file paths for context operations
        self.chain_file_path = chain_file_path  # Full path to chain file
        self.chain_file_dir = None
        if chain_file_path:
            import os
            self.chain_file_dir = os.path.dirname(os.path.abspath(chain_file_path))
        # Per-chain web browser scope (see SequenceExecutor web driver).
        self.web_chain_key = web_chain_key
        # Original chain identity for PERSISTENCE: when set, context rows are
        # stored under this name even if chain_file_path is a deterministic
        # temp copy (GUI playback) — the persisted context follows the
        # original chain.
        self.chain_identity = chain_identity
        
        # Preload entire chain and all referenced sequences for optimal performance
        if chain_file_path:
            try:
                logger.info("Preloading entire chain and all sequences...")
                start_time = time.time()
                preload_chain(chain_file_path, self.chain_file_dir)
                preload_time = time.time() - start_time
                cache_stats = get_cache().get_cache_stats()
                logger.info(f"Chain preloading completed in {preload_time:.3f}s - "
                           f"Cached {cache_stats['sequences_cached']} sequences, "
                           f"{cache_stats['chains_cached']} chains")
            except Exception as e:
                logger.warning(f"Failed to preload chain: {e}. Falling back to on-demand loading.")
        
        # Handle both legacy list format and new dict format
        if isinstance(chain_config, list):
            # Legacy format: list of sequences
            self.sequences = chain_config
            self.conditional_nodes = []
            self.llm_nodes = []
            self.orchestrator_nodes = []
            self.chain_import_nodes = []
            self.code_nodes = []
            self.container_nodes = []
            self.workflow_graph = None
            logger.info(f"Loaded legacy chain with {len(chain_config)} sequences")
        else:
            # New format: dict with sequences, conditional_nodes, llm_nodes, etc.
            # Migrate legacy TTS nodes into audio Output nodes before building.
            migrate_legacy_tts(chain_config)
            self.sequences = chain_config.get('sequences', [])
            self.conditional_nodes = chain_config.get('conditional_nodes', [])
            self.llm_nodes = chain_config.get('llm_nodes', [])
            self.chain_import_nodes = chain_config.get('chain_import_nodes', [])
            self.code_nodes = chain_config.get('code_nodes', [])
            self.container_nodes = chain_config.get('container_nodes', [])
            self.context_nodes = chain_config.get('context_nodes', [])
            self.input_nodes = chain_config.get('input_nodes', [])
            self.handle_nodes = chain_config.get('handle_nodes', [])
            self.mcp_nodes = chain_config.get('mcp_nodes', [])
            self.output_nodes = chain_config.get('output_nodes', [])
            self.web_sequences = chain_config.get('web_sequences', [])
            self.form_filler_nodes = chain_config.get('form_filler_nodes', [])
            self.orchestrator_nodes = chain_config.get('orchestrator_nodes', [])
            self._agent_query = agent_query
            self._ask_user_callback = ask_user_callback
            self._ask_user_v2_callback = ask_user_v2_callback
            self._on_output_ready_callback = on_output_ready
            # Keep the raw config so play_chain can read e.g. description
            self._chain_config = chain_config
            self._llamacpp_server_url = llamacpp_server_url

            # Use modularized workflow graph builder
            graph_builder = WorkflowGraphBuilder(self.sequences, self.conditional_nodes, self.llm_nodes, self.chain_import_nodes, self.code_nodes, self.container_nodes, self.context_nodes, self.input_nodes, self.handle_nodes, self.mcp_nodes, self.output_nodes, self.web_sequences, self.form_filler_nodes, self.orchestrator_nodes)
            self.workflow_graph = graph_builder.build_workflow_graph()
            logger.info(f"Loaded workflow chain with {len(self.sequences)} sequences, {len(self.conditional_nodes)} conditional nodes, {len(self.llm_nodes)} LLM nodes, {len(self.chain_import_nodes)} chain import nodes, {len(self.code_nodes)} code nodes, {len(self.container_nodes)} container nodes, {len(self.context_nodes)} context nodes, {len(self.input_nodes)} input nodes, {len(self.handle_nodes)} handle nodes, {len(self.mcp_nodes)} mcp nodes, {len(self.output_nodes)} output nodes, {len(self.web_sequences)} web sequence nodes")
        
        self.current_sequence_index = 0
        
        # Initialize skipped nodes tracking
        self.skipped_nodes = set()
        
        # Initialize modularized components
        # Propagate chain_file_dir so conditional assets (images/sequences) resolve correctly in nested imports
        self.fallback_handler = ConditionalFallbackHandler(chain_file_dir=self.chain_file_dir)
        self.workflow_navigator = WorkflowNavigator(self.workflow_graph) if self.workflow_graph else None
        self.llm_executor = LLMExecutor(variables=variables)
        if self.workflow_graph:
            self.llm_executor.workflow_graph = self.workflow_graph
        self.sequence_executor = SequenceExecutor(self.fallback_handler, self.chain_file_dir)
        # GUI playback of an in-progress chain runs on the shared durable
        # browser (web_chain_key).  Agent/scheduled/CLI runs (web_chain_key
        # None) run on the same shared durable profile via web_profile_key,
        # so cookies/logins persist across runs and app restarts in every
        # mode.  web_isolated (playback while a web recording is capturing)
        # forces a disposable browser instead.
        if web_chain_key:
            try:
                self.sequence_executor.web_chain_key = web_chain_key
            except Exception:
                pass
        elif not web_isolated:
            try:
                self.sequence_executor.web_profile_key = _web_profile_key_for(
                    chain_config, chain_file_path
                )
            except Exception:
                pass
        
        # Initialize workflow executor for new format
        if self.workflow_graph:
            self.workflow_executor = WorkflowExecutor(
                self.workflow_graph,
                self.sequence_executor,
                self.llm_executor,
                self.fallback_handler
            )

        # Propagate shared selection memory to modular components for the duration of a chain run
        # This ensures consistent candidate selection across sequences and conditional fallbacks.
        try:
            self.fallback_handler.selection_memory = self.selection_memory
        except Exception:
            pass
        try:
            self.sequence_executor.selection_memory = self.selection_memory
        except Exception:
            pass
        try:
            if hasattr(self, 'workflow_executor') and self.workflow_executor:
                self.workflow_executor.selection_memory = self.selection_memory
        except Exception:
            pass
    

    def _chain_uses_ollama_engine(self) -> bool:
        """Return True when any LLM node in this chain uses the Ollama backend.

        Gates Ollama-specific operations (runner cleanup, probes) so
        llama.cpp-only chains never touch localhost:11434.
        """
        try:
            for _llm_node in (self.llm_nodes or []):
                _data = _llm_node.get("data", {}) or {}
                _conf = _data.get("llm_configuration", {}) or {}
                _merged = dict(_data)
                _merged.update(_conf)
                _use_llamacpp = str(_merged.get("use_llamacpp", False)).lower() in (
                    "true", "1", "yes", "on",
                )
                if not _use_llamacpp:
                    return True
            return False
        except Exception:
            # Conservative default: probe when the engine cannot be determined.
            return True

    def play_chain(self, stop_flag=None, preserve_selection_memory=False, on_node_failed=None, on_output_ready=None):
        """
        Plays the entire chain of sequences with proper workflow execution.
        
        Args:
            stop_flag (callable): Optional function that returns True when playback should stop.
            preserve_selection_memory (bool): If True, keep selection memory across runs.
            on_node_failed (callable): Optional callback invoked when a node fails execution.
                Called with (node_id, node_type, node_data) from the background thread.
        """
        logger.info("Starting chain playback")
        logger.debug(f"Chain playback - workflow_graph exists: {self.workflow_graph is not None}")
        logger.debug(f"Chain playback - sequences count: {len(self.sequences) if self.sequences else 0}")
        sandbox_ctx = None
        sandbox_agent_url = None
        # Set when this run ends with a self-callback handoff: the caller
        # (play_chain_with_tailcalls) must release this instance and start a
        # fresh execution of the handoff chain in the single execution slot.
        self.chain_tailcall_path = None
        
        # Ollama runner cleanup is only useful when this chain actually runs
        # on the Ollama backend AND we are the top-level execution.  Nested
        # chain imports share the same process/GPU, so probing per level is
        # pure overhead (and it makes ESC cancellation O(depth) slow).
        _do_ollama_cleanup = False
        try:
            _is_imported_chain = bool(
                self.llm_executor.get_variable("_is_imported_chain")
            )
        except Exception:
            _is_imported_chain = False
        if not _is_imported_chain:
            _do_ollama_cleanup = self._chain_uses_ollama_engine()

        if _do_ollama_cleanup:
            try:
                _safe_cleanup_ollama_runners()
            except Exception:
                pass

        # Reset component states to prevent variable persistence across runs
        try:
            if hasattr(self, 'llm_executor'):
                self.llm_executor.reset()
            if hasattr(self, 'workflow_executor'):
                self.workflow_executor.reset()
            
            # Reset workflow execution state
            self.current_sequence_index = 0
            self.skipped_nodes = set()
            
            if not preserve_selection_memory:
                self.clear_selection_memory()
            logger.info("Reset MultiSequencePlayer component states")
        except Exception as e:
            logger.warning(f"Failed to reset component states: {e}")

        try:
            if hasattr(self, 'global_app_context_override') and isinstance(self.global_app_context_override, dict) and self.global_app_context_override:
                sandbox_ctx = self.global_app_context_override
            elif hasattr(self, 'workflow_executor') and self.workflow_executor and isinstance(getattr(self.workflow_executor, 'global_app_context_override', None), dict):
                sandbox_ctx = self.workflow_executor.global_app_context_override
            elif hasattr(self, 'sequence_executor') and self.sequence_executor and isinstance(getattr(self.sequence_executor, 'global_app_context_override', None), dict):
                sandbox_ctx = self.sequence_executor.global_app_context_override
        except Exception:
            sandbox_ctx = None

        if isinstance(sandbox_ctx, dict) and sandbox_ctx.get("sandboxed"):
            # Preserve any sandbox agent URL that was set by a previous call to
            # begin_chain_sandbox() (e.g. from the scheduler or chain import).
            # Without this, play_chain() would strip the URL from the new dict and
            # execute_sequence() would not see it, triggering a redundant second RDP
            # session setup attempt.
            _existing_url = sandbox_ctx.get("sandbox_agent_url") or sandbox_ctx.get("rdp_agent_url")
            try:
                self.sequence_executor.global_app_context_override = sandbox_ctx
            except Exception:
                pass
            try:
                if hasattr(self, 'workflow_executor') and self.workflow_executor:
                    self.workflow_executor.global_app_context_override = sandbox_ctx
            except Exception:
                pass
            try:
                sandbox_agent_url = self.sequence_executor.begin_chain_sandbox(sandbox_ctx, stop_flag=stop_flag)
                if sandbox_agent_url:
                    self.sandbox_agent_url = sandbox_agent_url
                    try:
                        if hasattr(self, 'action_handlers') and self.action_handlers:
                            self.action_handlers.bot.sandbox_agent_url = sandbox_agent_url
                    except Exception:
                        pass
                    try:
                        from .computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(sandbox_agent_url)
                    except Exception:
                        pass
                    # Carry the agent URL into the new context dict so downstream
                    # execute_sequence() sees it without needing a re-init.
                    sandbox_ctx["sandbox_agent_url"] = sandbox_agent_url
                elif _existing_url:
                    # No new URL was returned but we had one before — re-use it.
                    sandbox_ctx["sandbox_agent_url"] = _existing_url
            except Exception:
                sandbox_agent_url = None

        # Interruption-friendly initial delay
        initial_delay = 2.0
        elapsed = 0.0
        while elapsed < initial_delay:
            if stop_flag and stop_flag():
                logger.info("Chain playback stopped during initial delay")
                return False
            sleep_time = min(0.1, initial_delay - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time
        
        # Set stop flag for all components
        self.fallback_handler.stop_flag = stop_flag
        
        try:
            if self.workflow_graph:
                # New workflow format - use WorkflowExecutor
                logger.info("Using workflow executor for chain playback")
                
                # Pass down any global context overrides (like Sandbox configuration)
                if hasattr(self, 'global_app_context_override') and self.global_app_context_override:
                    self.workflow_executor.global_app_context_override = self.global_app_context_override
                
                # Forward chain input context for agent-modifiable Input nodes
                # For the system chain entry point, derive from _agent_query.
                try:
                    _ctx = self.llm_executor.get_variable("_chain_input_context")
                    if not _ctx or not str(_ctx).strip():
                        # Not inherited from a parent chain_import — set from agent_query
                        _ctx = getattr(self, '_agent_query', '') or ''
                    if _ctx and str(_ctx).strip():
                        self.workflow_executor.llm_executor.set_variable(
                            '_chain_input_context', str(_ctx)
                        )
                except Exception:
                    pass
                # Store llamacpp server URL + chain info for downstream nodes
                try:
                    llm_url = getattr(self, '_llamacpp_server_url', '') or ''
                    if llm_url:
                        self.workflow_executor.llm_executor.set_variable('_llamacpp_server_url', llm_url)
                    # Store ask_user_callback so Input nodes with a user_prompt can ask the user
                    ask_cb = getattr(self, '_ask_user_callback', None)
                    if ask_cb:
                        self.workflow_executor.llm_executor.set_variable('_ask_user_callback', ask_cb)
                    # Rich ask-user v2 channel (buttons / choices / attachments)
                    ask_v2_cb = getattr(self, '_ask_user_v2_callback', None)
                    if ask_v2_cb:
                        self.workflow_executor.llm_executor.set_variable('_ask_user_v2_callback', ask_v2_cb)
                    # Store on_output_ready callback so output nodes can display results mid-execution
                    output_cb = getattr(self, '_on_output_ready_callback', None)
                    if output_cb:
                        self.workflow_executor.llm_executor.set_variable('_on_output_ready_callback', output_cb)
                    # Store pre-collected inputs (gathered on main thread to avoid Qt dialog crash)
                    _pre_collected = getattr(self, '_pre_collected_inputs', None) or {}
                    if _pre_collected:
                        for _pid, _pval in _pre_collected.items():
                            self.workflow_executor.llm_executor.set_variable(
                                f'_pre_collected_{_pid}', _pval,
                            )
                        logger.info(
                            'Propagated %d pre-collected input(s) to executor variables',
                            len(_pre_collected),
                        )
                    # Store chain description so Handle nodes can use it for agent-mode reasoning
                    chain_cfg = getattr(self, '_chain_config', None)
                    if isinstance(chain_cfg, dict):
                        chain_desc = chain_cfg.get('description', '') or ''
                        self.workflow_executor.llm_executor.set_variable('_chain_description', chain_desc)
                except Exception:
                    pass
                    
                result = self.workflow_executor.execute_workflow(
                    stop_flag,
                    getattr(self, 'action_handlers', None),
                    chain_file=getattr(self, 'chain_file_path', None) or getattr(self, 'chain_file_dir', None),
                    on_node_failed=on_node_failed,
                    on_output_ready=on_output_ready,
                    chain_identity=getattr(self, 'chain_identity', None),
                )
                # Expose a self-callback handoff so the top-level trampoline
                # (play_chain_with_tailcalls) can release this execution and
                # start the fresh run of the same chain.
                try:
                    _tail = self.llm_executor.get_variable("_chain_tailcall_path") or ""
                    self.chain_tailcall_path = str(_tail) if str(_tail).strip() else None
                except Exception:
                    self.chain_tailcall_path = None
                logger.info(f"Workflow execution completed with result: {result}")
                return result
            else:
                # Legacy format
                logger.info("Using legacy chain playback")
                result = self._play_legacy_chain(stop_flag)
                logger.info(f"Legacy chain playback completed with result: {result}")
                return result
        except Exception as e:
            logger.error(f"Error during chain playbook: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
        finally:
            # Close this chain's shared web browser (created by the first web
            # sequence node) - one Chrome per chain run, terminated only after
            # every web sequence node has completed (success or failure), so
            # chains never leak browsers or share session state.
            try:
                if hasattr(self, 'sequence_executor') and self.sequence_executor:
                    self.sequence_executor.close_web_session()
            except Exception:
                pass
            if isinstance(sandbox_ctx, dict) and sandbox_ctx.get("sandboxed"):
                # We should only kill the sandbox session if this is the TOP LEVEL chain
                # If we have a global app context override that was passed to us,
                # we are likely an imported chain, and should NOT close the RDP session.
                is_top_level = True
                if hasattr(self, 'global_app_context_override') and self.global_app_context_override:
                    # If we were initialized with a global override, someone else is managing the sandbox
                    is_top_level = False
                    
                if is_top_level:
                    try:
                        self.sequence_executor.end_chain_sandbox(sandbox_ctx)
                    except Exception:
                        pass
                    try:
                        from .computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(None)
                    except Exception:
                        pass
                    try:
                        self.sandbox_agent_url = None
                    except Exception:
                        pass
                else:
                    logger.info("Preserving sandbox session for parent chain")
            else:
                try:
                    # A non-sandboxed run must not inherit a stale sandbox URL
                    # from a previous sandboxed run — OCR/vision would keep
                    # polling a dead agent or evaluating the wrong desktop.
                    if not getattr(self, 'global_app_context_override', None):
                        from .computer_vision import set_sandbox_agent_url
                        set_sandbox_agent_url(None)
                except Exception:
                    pass
                    
            if _do_ollama_cleanup:
                try:
                    _safe_cleanup_ollama_runners()
                except Exception:
                    pass

    
    def _play_legacy_chain(self, stop_flag):
        """
        Plays sequences in legacy linear mode.
        """
        self.current_sequence_index = 0
        while self.current_sequence_index < len(self.sequences):
            # Check stop flag before each sequence
            if stop_flag and stop_flag():
                logger.info("Chain playback stopped by user request")
                return
            
            item = self.sequences[self.current_sequence_index]
            seq_file = item['sequence_file']
            loops = item['loop_count']
            extra_delay = item.get('extra_delay', 0)
            
            # Handle pre-sequence conditional checks
            advanced_conditions = item.get('advanced_conditions', {})
            if self._handle_pre_sequence_conditions(item, stop_flag, advanced_conditions):
                self.current_sequence_index += 1
                continue
            
            try:
                # Load the sequence data for the current item.
                with open(seq_file, 'r') as f:
                    sequence_data = json.load(f)
                logger.info(f"Playing {seq_file} for {loops} loops")
            except Exception as e:
                logger.error(f"Failed to load {seq_file}: {str(e)}")
                self.current_sequence_index += 1
                continue
            
            # Play the loaded sequence for the specified number of loops.
            for i in range(loops):
                # Check stop flag before each loop
                if stop_flag and stop_flag():
                    logger.info("Chain playback stopped by user request during loop")
                    return
                    
                logger.info(f"Loop {i+1}/{loops}")
                
                # Inject fallback sequences into actions from multiple sources
                # 1. Chain-level fallback (legacy support)
                if 'fallback_sequence' in item:
                    for action in sequence_data['actions']:
                        if (action.get('type') in ['click', 'double_click', 'ctrl_click', 'shift_click'] and 
                            'screenshot' in action and 
                            'fallback_sequence' not in action):
                            action['fallback_sequence'] = item['fallback_sequence']
                            logger.info(f"Injected chain-level fallback sequence {item['fallback_sequence']} into action {sequence_data['actions'].index(action)}")
                
                # 2. Action-specific fallbacks from UI configuration
                if 'action_fallbacks' in item:
                    action_fallbacks = item['action_fallbacks']
                    for action_idx, fallback_config in action_fallbacks.items():
                        try:
                            action_idx_int = int(action_idx)
                            if action_idx_int < len(sequence_data['actions']):
                                action = sequence_data['actions'][action_idx_int]
                            if action.get('type') in ['click', 'double_click', 'ctrl_click', 'shift_click'] and action.get('screenshot'):
                                action['fallback_sequence'] = fallback_config['sequence_file']
                                action['fallback_retry_attempts'] = fallback_config.get('retry_attempts', 3)
                                action['fallback_retry_delay'] = fallback_config.get('retry_delay', 0.3)
                                logger.info(f"Injected action-specific fallback sequence {fallback_config['sequence_file']} into action {action_idx_int}")
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid action index '{action_idx}' in action_fallbacks: {e}")
                            continue
                
                # Create fallback callback that handles sequences and aborts current sequence
                def fallback_callback(fallback_seq_name, action_idx):
                    if stop_flag and stop_flag():
                        logger.info("Fallback execution stopped by user request")
                        return
                        
                    logger.info(f"Visual matching failed for action {action_idx}. Aborting current sequence and executing fallback: {fallback_seq_name}")
                    
                    try:
                        fallback_player = SequencePlayer(fallback_seq_name)
                        # Share selection memory to maintain deterministic behavior across fallbacks
                        fallback_player.selection_memory = self.selection_memory
                        fallback_player.play_sequence(stop_flag=stop_flag)
                        logger.info(f"Fallback sequence completed. Current sequence was aborted.")
                        
                    except Exception as e:
                        logger.error(f"Fallback sequence failed: {e}")
                
                # Play the sequence with fallback support
                try:
                    player = SequencePlayer(seq_file)
                    # Share selection memory with the sequence player
                    player.selection_memory = self.selection_memory
                    player.sequence_data = sequence_data  # Use the modified sequence data with fallback
                    player.play_sequence(fallback_callback=fallback_callback, stop_flag=stop_flag)
                except Exception as e:
                    logger.error(f"Error playing sequence {seq_file}: {e}")
                    break
                
                # Add extra delay between loops if specified
                if i < loops - 1 and extra_delay > 0:
                    logger.info(f"Extra delay between loops: {extra_delay} seconds")
                    delay_remaining = extra_delay
                    while delay_remaining > 0:
                        if stop_flag and stop_flag():
                            logger.info("Chain playback stopped during delay")
                            return
                        sleep_time = min(0.1, delay_remaining)
                        time.sleep(sleep_time)
                        delay_remaining -= sleep_time
            
            # Handle post-sequence conditional actions
            self._handle_post_sequence_conditions(item, stop_flag, advanced_conditions)
            
            # Extra delay after completing the sequence before moving to next
            if extra_delay and extra_delay > 0:
                logger.info(f"Extra delay after sequence: {extra_delay} seconds")
                delay_remaining = extra_delay
                while delay_remaining > 0:
                    if stop_flag and stop_flag():
                        logger.info("Chain playback stopped during post-sequence delay")
                        return
                    sleep_time = min(0.1, delay_remaining)
                    time.sleep(sleep_time)
                    delay_remaining -= sleep_time

            # Move to the next sequence in the chain
            self.current_sequence_index += 1
    
    def _handle_pre_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """Handle pre-sequence conditions - delegated to ConditionalFallbackHandler."""
        return self.fallback_handler.handle_pre_sequence_conditions(item, stop_flag, advanced_conditions)
    
    def _handle_post_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """Handle post-sequence conditions - delegated to ConditionalFallbackHandler."""
        return self.fallback_handler.handle_post_sequence_conditions(item, stop_flag, advanced_conditions)

    # _find_next_sequence_node method removed - now handled by WorkflowNavigator
    
    # Old sequence execution methods removed - now handled by SequenceExecutor
    # _execute_sequence, _handle_pre_sequence_conditions, _handle_post_sequence_conditions

    def _mark_skipped(self, node_id, branch):
        """
        Mark nodes in the unchosen branch as skipped - delegated to WorkflowExecutor.
        
        Args:
            node_id (str): The conditional node ID
            branch (str): The branch to mark as skipped ('true' or 'false')
            
        Returns:
            set: Set of skipped node IDs
        """
        return self.workflow_executor.mark_skipped(node_id, branch)


def play_chain_with_tailcalls(chain_path, agent_query="", llamacpp_server_url="",
                              ask_user_callback=None, ask_user_v2_callback=None,
                              on_output_ready=None,
                              on_node_failed=None, pre_collected_inputs=None,
                              variables=None, stop_flag=None,
                              player_setup_callback=None,
                              web_chain_key=None, chain_identity=None,
                              web_isolated=False, run_source="play"):
    """Run *chain_path*, following self-callback (tail-call) handoffs in a flat loop.

    A chain that imports itself is a *callback*: the currently executing
    instance is released and a fresh execution of the same chain starts in
    the single execution slot — no nested players, no stack growth.  This is
    the building block for perpetual agents (unbounded callbacks; ESC or the
    caller's stop_flag terminates).  Non-self-referential imports keep their
    nesting behavior inside each play_chain call.

    player_setup_callback: optional callable(player) invoked after each player
    is constructed (e.g. to apply sandbox overrides).

    web_chain_key: optional web browser scope - when set, the chain runs on
    the app-wide shared durable browser (GUI playback).  web_isolated forces
    a disposable isolated browser (used while a web recording is capturing).

    run_source: tag recorded on the linear activity timeline (run_memory) —
    "agent" | "schedule" | "play".

    Returns (result, last_player).
    """
    current_path = chain_path
    inherited = dict(variables) if variables else None
    result = None
    last_player = None
    while True:
        if not current_path or not os.path.exists(current_path):
            logger.error(
                "play_chain_with_tailcalls: chain file not found: %s",
                current_path,
            )
            break
        try:
            with open(current_path, "r", encoding="utf-8") as _f:
                _config = json.load(_f)
        except Exception as _e:
            logger.error(
                "play_chain_with_tailcalls: failed to load %s: %s",
                current_path, _e,
            )
            break
        # Linear activity timeline: open a run for this play (each tailcall
        # iteration is its own run).  Node events attach to it via the
        # thread-local current run (see player/agentic_ops/run_memory.py).
        _rm_run = None
        try:
            from player.agentic_ops import run_memory as _run_memory
            _rm_run = _run_memory.begin_run(
                current_path, source=run_source, query=agent_query or "",
            )
        except Exception:
            _rm_run = None
        last_player = MultiSequencePlayer(
            _config, current_path, variables=inherited,
            agent_query=agent_query, llamacpp_server_url=llamacpp_server_url,
            ask_user_callback=ask_user_callback, on_output_ready=on_output_ready,
            ask_user_v2_callback=ask_user_v2_callback,
            web_chain_key=web_chain_key, chain_identity=chain_identity,
            web_isolated=web_isolated,
        )
        if pre_collected_inputs:
            try:
                last_player._pre_collected_inputs = dict(pre_collected_inputs)
            except Exception:
                pass
        if player_setup_callback is not None:
            try:
                player_setup_callback(last_player)
            except Exception:
                pass
        result = last_player.play_chain(
            stop_flag=stop_flag, on_node_failed=on_node_failed,
            on_output_ready=on_output_ready,
        )
        try:
            if _rm_run is not None:
                _run_memory.end_run(
                    _rm_run, ok=(result is not False),
                    stopped=bool(stop_flag and stop_flag()),
                )
        except Exception:
            pass
        tail = getattr(last_player, "chain_tailcall_path", None)
        if not tail or (stop_flag and stop_flag()):
            break
        # Carry the runtime context into the fresh execution (same set of
        # variables chain_ops propagates to nested imports).
        try:
            _we = getattr(last_player, "workflow_executor", None)
            _le = getattr(_we, "llm_executor", None) if _we is not None else None
            inherited = {
                "_chain_input_context": (
                    (_le.get_variable("_chain_input_context") if _le is not None else None)
                    or agent_query or ""
                ),
                "_ask_user_callback": (
                    _le.get_variable("_ask_user_callback") if _le is not None else None
                ),
                "_llamacpp_server_url": (
                    (_le.get_variable("_llamacpp_server_url") if _le is not None else None)
                    or llamacpp_server_url
                ),
                "_on_output_ready_callback": (
                    _le.get_variable("_on_output_ready_callback") if _le is not None else None
                ),
                "_chain_description": (
                    _le.get_variable("_chain_description") if _le is not None else None
                ),
                "_chain_goal": (
                    _le.get_variable("_chain_goal") if _le is not None else None
                ),
                "_chain_step": (
                    _le.get_variable("_chain_step") if _le is not None else None
                ),
                # Handoff iterations are continuations of one user request:
                # per-execution setup (e.g. Ollama runner cleanup) runs once.
                "_is_imported_chain": True,
            }
        except Exception:
            inherited = None
        logger.info(
            "Self-callback handoff: releasing execution of %s, starting fresh run of %s",
            current_path, tail,
        )
        current_path = tail
    return result, last_player
