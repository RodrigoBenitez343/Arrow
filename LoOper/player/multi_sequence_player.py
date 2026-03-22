# multi_sequence_player.py
"""
Multi-sequence player for executing chains of automation sequences with advanced conditional fallbacks.
"""

import sys
import json
import time
import os
from .config import logger
from .base_bot import SeleniumBot
from .sequence_player import SequencePlayer
from .json_cache import preload_chain, get_cache
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


# ConditionalFallbackHandler, WorkflowGraphBuilder, WorkflowNavigator, 
# LLMExecutor, and SequenceExecutor are now imported from the multi_sequence module


class MultiSequencePlayer(SeleniumBot):
    """
    Plays multiple sequences with conditional branching support, as defined in a chain configuration file.
    This allows for looping, chaining, and conditional branching of automation tasks.
    
    This class now uses modularized components for better maintainability.
    """
    def __init__(self, chain_config, chain_file_path=None, variables=None):
        """
        Initializes the multi-sequence player.

        Args:
            chain_config (dict or list): Either a legacy list format or new dict format with:
                                        - sequences: list of sequence configurations
                                        - conditional_nodes: list of conditional node configurations
            chain_file_path (str, optional): Path to the chain file for resolving relative sequence paths
            variables (dict, optional): Shared variables dictionary from parent player (for nested chains)
        """
        super().__init__()
        
        # Store chain file directory for path resolution
        self.chain_file_dir = None
        if chain_file_path:
            import os
            self.chain_file_dir = os.path.dirname(os.path.abspath(chain_file_path))
        
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
            self.tts_nodes = []
            self.chain_import_nodes = []
            self.code_nodes = []
            self.container_nodes = []
            self.workflow_graph = None
            logger.info(f"Loaded legacy chain with {len(chain_config)} sequences")
        else:
            # New format: dict with sequences, conditional_nodes, llm_nodes, and tts_nodes
            self.sequences = chain_config.get('sequences', [])
            self.conditional_nodes = chain_config.get('conditional_nodes', [])
            self.llm_nodes = chain_config.get('llm_nodes', [])
            self.tts_nodes = chain_config.get('tts_nodes', [])
            self.chain_import_nodes = chain_config.get('chain_import_nodes', [])
            self.code_nodes = chain_config.get('code_nodes', [])
            self.container_nodes = chain_config.get('container_nodes', [])
            
            # Use modularized workflow graph builder
            graph_builder = WorkflowGraphBuilder(self.sequences, self.conditional_nodes, self.llm_nodes, self.tts_nodes, self.chain_import_nodes, self.code_nodes, self.container_nodes)
            self.workflow_graph = graph_builder.build_workflow_graph()
            logger.info(f"Loaded workflow chain with {len(self.sequences)} sequences, {len(self.conditional_nodes)} conditional nodes, {len(self.llm_nodes)} LLM nodes, {len(self.tts_nodes)} TTS nodes, {len(self.chain_import_nodes)} chain import nodes, {len(self.code_nodes)} code nodes, and {len(self.container_nodes)} container nodes")
        
        self.current_sequence_index = 0
        
        # Initialize skipped nodes tracking
        self.skipped_nodes = set()
        
        # Initialize modularized components
        # Propagate chain_file_dir so conditional assets (images/sequences) resolve correctly in nested imports
        self.fallback_handler = ConditionalFallbackHandler(chain_file_dir=self.chain_file_dir)
        self.workflow_navigator = WorkflowNavigator(self.workflow_graph) if self.workflow_graph else None
        self.llm_executor = LLMExecutor(variables=variables)
        self.sequence_executor = SequenceExecutor(self.fallback_handler, self.chain_file_dir)
        
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
    

    def _chain_uses_comorag(self):
        try:
            llm_nodes = self.llm_nodes or []
        except Exception:
            llm_nodes = []
        if not llm_nodes:
            return False
        for n in llm_nodes:
            try:
                cfg = (n.get('llm_configuration') or n) if isinstance(n, dict) else {}
                val = cfg.get('use_comorag', True)
                if isinstance(val, str):
                    val = val.strip().lower() in ('true', '1', 'yes', 'on')
                if bool(val):
                    return True
            except Exception:
                return True
        return False


    def _wait_for_comorag_ready(self, base_url, timeout=30, stop_flag=None):
        """Waits for ComoRAG engine to be ready."""
        import requests
        import time
        
        start_time = time.time()
        logger.info(f"Waiting for ComoRAG engine to be ready at {base_url}...")
        
        while time.time() - start_time < timeout:
            try:
                if stop_flag and stop_flag():
                    return False
            except Exception:
                pass
            try:
                response = requests.get(f"{base_url}/comorag/status?init_if_needed=true", timeout=8)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("ready", False):
                        logger.info("ComoRAG engine is ready")
                        return True
                
                try:
                    init_payload = {"memory_size": 1000}
                    requests.post(f"{base_url}/comorag/init", json=init_payload, timeout=10)
                except Exception:
                    pass
            except Exception:
                pass
            
            time.sleep(1)
            
        logger.warning("ComoRAG engine did not become ready within timeout")
        return False

    def play_chain(self, stop_flag=None, preserve_selection_memory=False):
        """
        Plays the entire chain of sequences with proper workflow execution.
        
        Args:
            stop_flag (callable): Optional function that returns True when playback should stop.
        """
        logger.info("Starting chain playback")
        logger.debug(f"Chain playback - workflow_graph exists: {self.workflow_graph is not None}")
        logger.debug(f"Chain playback - sequences count: {len(self.sequences) if self.sequences else 0}")
        sandbox_ctx = None
        sandbox_agent_url = None
        
        try:
            if _cleanup_ollama_runners:
                _cleanup_ollama_runners()
        except Exception:
            pass

        if self._chain_uses_comorag():
            try:
                import requests
                tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
                if not tailscale_ip or not str(tailscale_ip).strip():
                    tailscale_ip = "localhost"
                port = os.getenv("API_PORT", "8000")
                if not port or not str(port).strip():
                    port = "8000"
                base_url = f"http://{tailscale_ip}:{port}"
                overwatch_url = os.getenv("OVERWATCH_API_URL")
                if overwatch_url and str(overwatch_url).strip():
                    base_url = overwatch_url.strip().rstrip('/')
                if self._wait_for_comorag_ready(base_url, stop_flag=stop_flag):
                    url = f"{base_url}/comorag/memory"
                    logger.info(f"Clearing ComoRAG memory at {url}...")
                    response = requests.delete(url, timeout=5)
                    if response.status_code == 200:
                        logger.info("Successfully cleared ComoRAG memory")
                    else:
                        logger.warning(f"Failed to clear ComoRAG memory: {response.status_code} {response.text}")
            except Exception as e:
                logger.warning(f"Could not clear ComoRAG memory (API might be down or unreachable): {e}")

        # Reset component states to prevent variable persistence across runs
        try:
            if hasattr(self, 'llm_executor'):
                self.llm_executor.reset()
            if hasattr(self, 'workflow_executor'):
                self.workflow_executor.reset()
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
                    
                result = self.workflow_executor.execute_workflow(stop_flag, getattr(self, 'action_handlers', None))
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
            if isinstance(sandbox_ctx, dict) and sandbox_ctx.get("sandboxed"):
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
            try:
                if _cleanup_ollama_runners:
                    _cleanup_ollama_runners()
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
                        if (action.get('type') in ['click', 'double_click'] and 
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
                            if action.get('type') in ['click', 'double_click'] and action.get('screenshot'):
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
    

    

    
    def _execute_tts_standalone(self, tts_data, context_variables=None):
        """
        Execute TTS without GUI dependencies - delegated to LLMExecutor.
        
        Args:
            tts_data (dict): TTS configuration data
            context_variables (dict): Optional context variables for text substitution
            
        Returns:
            dict: Result dictionary with success status and message/error
        """
        return self.llm_executor.execute_tts_standalone(tts_data, context_variables)
    

    
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
