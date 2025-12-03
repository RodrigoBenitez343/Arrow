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


# ConditionalFallbackHandler, WorkflowGraphBuilder, WorkflowNavigator, 
# LLMExecutor, and SequenceExecutor are now imported from the multi_sequence module


class MultiSequencePlayer(SeleniumBot):
    """
    Plays multiple sequences with conditional branching support, as defined in a chain configuration file.
    This allows for looping, chaining, and conditional branching of automation tasks.
    
    This class now uses modularized components for better maintainability.
    """
    def __init__(self, chain_config, chain_file_path=None):
        """
        Initializes the multi-sequence player.

        Args:
            chain_config (dict or list): Either a legacy list format or new dict format with:
                                        - sequences: list of sequence configurations
                                        - conditional_nodes: list of conditional node configurations
            chain_file_path (str, optional): Path to the chain file for resolving relative sequence paths
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
            self.workflow_graph = None
            logger.info(f"Loaded legacy chain with {len(chain_config)} sequences")
        else:
            # New format: dict with sequences, conditional_nodes, llm_nodes, and tts_nodes
            self.sequences = chain_config.get('sequences', [])
            self.conditional_nodes = chain_config.get('conditional_nodes', [])
            self.llm_nodes = chain_config.get('llm_nodes', [])
            self.tts_nodes = chain_config.get('tts_nodes', [])
            self.chain_import_nodes = chain_config.get('chain_import_nodes', [])
            
            # Use modularized workflow graph builder
            graph_builder = WorkflowGraphBuilder(self.sequences, self.conditional_nodes, self.llm_nodes, self.tts_nodes, self.chain_import_nodes)
            self.workflow_graph = graph_builder.build_workflow_graph()
            logger.info(f"Loaded workflow chain with {len(self.sequences)} sequences, {len(self.conditional_nodes)} conditional nodes, {len(self.llm_nodes)} LLM nodes, {len(self.tts_nodes)} TTS nodes, and {len(self.chain_import_nodes)} chain import nodes")
        
        self.current_sequence_index = 0
        
        # Initialize skipped nodes tracking
        self.skipped_nodes = set()
        
        # Initialize modularized components
        # Propagate chain_file_dir so conditional assets (images/sequences) resolve correctly in nested imports
        self.fallback_handler = ConditionalFallbackHandler(chain_file_dir=self.chain_file_dir)
        self.workflow_navigator = WorkflowNavigator(self.workflow_graph) if self.workflow_graph else None
        self.llm_executor = LLMExecutor()
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
    


    def play_chain(self, stop_flag=None):
        """
        Plays the entire chain of sequences with proper workflow execution.
        
        Args:
            stop_flag (callable): Optional function that returns True when playback should stop.
        """
        logger.info("Starting chain playback")
        logger.debug(f"Chain playback - workflow_graph exists: {self.workflow_graph is not None}")
        logger.debug(f"Chain playback - sequences count: {len(self.sequences) if self.sequences else 0}")
        
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
