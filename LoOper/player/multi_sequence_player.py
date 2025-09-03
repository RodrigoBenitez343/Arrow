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
from .multi_sequence import (
    ConditionalFallbackHandler,
    WorkflowGraphBuilder,
    WorkflowNavigator,
    LLMExecutor,
    SequenceExecutor
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
            
            # Use modularized workflow graph builder
            graph_builder = WorkflowGraphBuilder(self.sequences, self.conditional_nodes, self.llm_nodes, self.tts_nodes)
            self.workflow_graph = graph_builder.build_workflow_graph()
            logger.info(f"Loaded workflow chain with {len(self.sequences)} sequences, {len(self.conditional_nodes)} conditional nodes, {len(self.llm_nodes)} LLM nodes, and {len(self.tts_nodes)} TTS nodes")
        
        self.current_sequence_index = 0
        
        # Initialize modularized components
        self.fallback_handler = ConditionalFallbackHandler()
        self.workflow_navigator = WorkflowNavigator(self.workflow_graph) if self.workflow_graph else None
        self.llm_executor = LLMExecutor()
        self.sequence_executor = SequenceExecutor(self.fallback_handler, self.chain_file_dir)
    
    def _find_starting_node(self):
        """
        Find the starting node for workflow execution.
        
        Returns:
            str: The ID of the starting node
        """
        if self.workflow_navigator:
            return self.workflow_navigator.find_starting_node()
        return None
    
    def _find_all_starting_nodes(self):
        """
        Find all starting nodes for workflow execution (nodes with no inputs).
        
        Returns:
            list: List of node IDs that have no input dependencies
        """
        starting_nodes = []
        
        for node_id, node_data in self.workflow_graph.items():
            if 'inputs' not in node_data or not node_data['inputs']:
                starting_nodes.append(node_id)
                logger.debug(f"Found starting node: {node_id} (type: {node_data['type']})")
        
        return starting_nodes
    
    def _find_newly_ready_nodes(self, completed_node_id, completed_nodes):
        """
        Find nodes that are now ready to execute after a node completes.
        
        Args:
            completed_node_id (str): The node that just completed
            completed_nodes (set): Set of all completed node IDs
        
        Returns:
            list: List of node IDs that are now ready to execute
        """
        newly_ready = []
        
        # Check all nodes to see if any are now ready
        for node_id, node_data in self.workflow_graph.items():
            if node_id in completed_nodes:
                continue  # Skip already completed nodes
            
            # Check if this node has the completed node as an input
            node_inputs = node_data.get('inputs', [])
            if not node_inputs:
                continue  # No inputs, should already be in ready set
            
            # Special handling for nodes that have DIRECT conditional node inputs
            # These should NOT be automatically marked as ready since conditional
            # nodes handle their own branching logic
            has_direct_conditional_input = False
            for input_connection in node_inputs:
                input_node_id = input_connection['from_node']
                if input_node_id in self.workflow_graph:
                    input_node = self.workflow_graph[input_node_id]
                    if input_node.get('type') == 'conditional':
                        has_direct_conditional_input = True
                        break
            
            if has_direct_conditional_input:
                continue  # Skip nodes with direct conditional inputs - they're handled by conditional branching logic
            
            # Check if all input dependencies are satisfied
            all_inputs_satisfied = True
            for input_connection in node_inputs:
                input_node_id = input_connection['from_node']
                if input_node_id not in completed_nodes:
                    all_inputs_satisfied = False
                    break
            
            if all_inputs_satisfied:
                newly_ready.append(node_id)
                logger.debug(f"Node {node_id} is now ready (all inputs satisfied)")
        
        return newly_ready
    
    def _evaluate_conditional(self, conditional_node, stop_flag):
        """
        Evaluate a conditional node using the fallback handler.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
        
        Returns:
            bool: True if condition is met, False otherwise
        """
        # Fix: The JSON stores 'trigger_type' with values like 'absence', 'presence', 'ocr'
        # We need to map these to the expected condition types for evaluation
        raw_trigger_type = conditional_node.get('trigger_type') or conditional_node.get('condition_type', 'presence')
        
        # Map the raw trigger type to the expected condition type format
        condition_type_mapping = {
            'presence': 'presence_trigger',
            'absence': 'absence_trigger', 
            'ocr': 'ocr_trigger',
            'presence_trigger': 'presence_trigger',  # backward compatibility
            'absence_trigger': 'absence_trigger',    # backward compatibility
            'ocr_trigger': 'ocr_trigger'             # backward compatibility
        }
        
        condition_type = condition_type_mapping.get(raw_trigger_type, 'presence_trigger')
        
        # Build condition_config from the conditional node properties
        # The JSON stores properties directly, not nested under 'condition_data'
        condition_config = {
            'image_path': conditional_node.get('image_path', ''),
            'confidence': float(conditional_node.get('threshold', 0.8)),
            'timeout': float(conditional_node.get('wait_time', 10)),
            'max_loops': int(conditional_node.get('max_loops', 10)),
            'target_text': conditional_node.get('ocr_text', '') or conditional_node.get('target_text', ''),
            'case_sensitive': conditional_node.get('case_sensitive', True),
            'language': conditional_node.get('language', 'eng'),
            'region': conditional_node.get('region')
        }
        
        # Also check if there's a nested condition_data (for backward compatibility)
        if 'condition_data' in conditional_node:
            nested_config = conditional_node['condition_data']
            condition_config.update(nested_config)
        
        logger.info(f"Evaluating conditional with raw trigger type: {raw_trigger_type} -> mapped to: {condition_type}")
        logger.debug(f"Condition config: {condition_config}")
        
        # Set stop flag for the fallback handler
        self.fallback_handler.stop_flag = stop_flag
        
        if condition_type == 'presence_trigger':
            return self.fallback_handler.check_presence_trigger(condition_config)
        elif condition_type == 'absence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.fallback_handler.check_absence_trigger(condition_config, timeout)
        elif condition_type == 'ocr_trigger':
            return self.fallback_handler.check_ocr_trigger(condition_config)
        else:
            logger.warning(f"Unknown condition type: {condition_type} (raw: {raw_trigger_type})")
            return False
    
    def _execute_conditional_loop(self, conditional_data, stop_flag):
        """
        Execute a conditional loop by reconstructing the loop configuration and calling the fallback handler.
        
        Args:
            conditional_data (dict): The conditional node data containing loop configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if loop completed successfully, False otherwise
        """
        try:
            # Reconstruct loop configuration from conditional node data
            loop_type = conditional_data.get('loop_type', 'while_present')
            max_iterations = int(conditional_data.get('max_loops', 10))
            sequence_file = conditional_data.get('sequence_file', '')
            iteration_delay = float(conditional_data.get('iteration_delay', 1.0))
            
            # Parse the stored loop condition
            loop_condition_json = conditional_data.get('loop_condition', '{}')
            try:
                condition = json.loads(loop_condition_json) if loop_condition_json else {}
            except (json.JSONDecodeError, TypeError):
                # Fallback to basic image condition if JSON parsing fails
                condition = {
                    'trigger_type': 'presence',
                    'image_path': conditional_data.get('image_path', ''),
                    'confidence': float(conditional_data.get('threshold', 0.8))
                }
            
            # Build loop configuration
            loop_config = {
                'type': loop_type,
                'max_iterations': max_iterations,
                'sequence_file': sequence_file,
                'iteration_delay': iteration_delay,
                'condition': condition
            }
            
            logger.info(f"Executing conditional loop: {loop_type}, sequence: {sequence_file}, max_iterations: {max_iterations}")
            
            # Set stop flag for the fallback handler
            self.fallback_handler.stop_flag = stop_flag
            
            # Execute the conditional loop using the fallback handler
            # Create a temporary SequencePlayer for loop execution with the sequence file
            sequence_file = loop_config.get('sequence_file', '')
            if not sequence_file:
                logger.error(f"No sequence file specified for conditional loop")
                return False
            
            temp_sequence_player = SequencePlayer(sequence_file)
            return self.fallback_handler.execute_conditional_loop(loop_config, temp_sequence_player)
            
        except Exception as e:
            logger.error(f"Error executing conditional loop: {e}")
            return False

    def play_chain(self, stop_flag=None):
        """
        Plays sequences with conditional branching support.
        For legacy format, plays sequences linearly. For new format, follows workflow graph.
        
        Args:
            stop_flag (callable): Optional function that returns True when playback should stop.
        """
        logger.info("Starting chain playback")
        time.sleep(2) # Initial delay.
        
        # Set stop flag for all components
        self.fallback_handler.stop_flag = stop_flag
        
        try:
            if self.workflow_graph:
                # New workflow format
                return self._play_workflow_graph(stop_flag)
            else:
                # Legacy format
                return self._play_legacy_chain(stop_flag)
        except Exception as e:
            logger.error(f"Error during chain playback: {e}")
            return False
    
    def _handle_until_present_condition(self, conditional_node, stop_flag):
        """
        Handles 'until_present' condition type by executing a sequence repeatedly
        until the target image is found or max iterations are reached.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if target image was found, False if max iterations reached
        """
        condition_data = conditional_node.get('condition_data', {})
        
        # Use the fallback handler's execute_conditional_loop method
        loop_config = {
            'type': 'until_present',
            'condition': condition_data.get('condition', condition_data),
            'max_iterations': condition_data.get('max_iterations', condition_data.get('max_attempts', 10)),
            'iteration_delay': condition_data.get('iteration_delay', condition_data.get('delay_between_attempts', 1.0)),
            'sequence_file': condition_data.get('sequence_file') or conditional_node.get('sequence_to_execute')
        }
        
        # Set stop flag for the fallback handler
        self.fallback_handler.stop_flag = stop_flag
        
        # Use a dummy sequence player for the loop execution
        dummy_player = SequencePlayer("")
        
        return self.fallback_handler.execute_conditional_loop(loop_config, dummy_player)
    
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
            extra_delay = item.get('extra_delay', 1)
            
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
                                action['fallback_retry_delay'] = fallback_config.get('retry_delay', 1.0)
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
                        fallback_player.play_sequence(stop_flag=stop_flag)
                        logger.info(f"Fallback sequence completed. Current sequence was aborted.")
                        
                    except Exception as e:
                        logger.error(f"Fallback sequence failed: {e}")
                
                # Play the sequence with fallback support
                try:
                    player = SequencePlayer(seq_file)
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
            
            # Move to the next sequence in the chain
            self.current_sequence_index += 1
    
    def _play_workflow_graph(self, stop_flag):
        """
        Plays sequences following the workflow graph with proper dependency resolution.
        Nodes with multiple inputs will only execute after all their dependencies are satisfied.
        """
        # Track completed nodes and nodes ready for execution
        completed_nodes = set()
        ready_nodes = set()
        
        # Find all starting nodes (nodes with no inputs)
        starting_nodes = self._find_all_starting_nodes()
        if not starting_nodes:
            logger.warning("No starting nodes found in workflow graph")
            return
        
        logger.info(f"Starting workflow execution from nodes: {starting_nodes}")
        ready_nodes.update(starting_nodes)
        
        # Execute nodes in dependency order
        while ready_nodes:
            # Check stop flag
            if stop_flag and stop_flag():
                logger.info("Workflow execution stopped by user request")
                return
            
            # Get next ready node to execute
            current_node_id = ready_nodes.pop()
            
            if current_node_id in completed_nodes:
                continue  # Skip already completed nodes
            
            if current_node_id not in self.workflow_graph:
                logger.warning(f"Node {current_node_id} not found in workflow graph")
                continue
            
            node = self.workflow_graph[current_node_id]
            logger.info(f"Executing node {current_node_id} (type: {node['type']})")
            
            # Execute the node based on its type
            success = False
            conditional_result = None
            
            if node['type'] == 'sequence':
                success = self._execute_sequence_node(node, stop_flag) is not None
            elif node['type'] == 'conditional':
                # For conditional nodes, evaluate the condition and store the result
                conditional_data = node['data']
                condition_type = conditional_data.get('condition_type', 'presence')
                if condition_type == 'loop':
                    conditional_result = self._execute_conditional_loop(conditional_data, stop_flag)
                else:
                    conditional_result = self._evaluate_conditional(conditional_data, stop_flag)
                success = True  # Conditional evaluation always succeeds
                logger.info(f"Conditional evaluation result: {conditional_result}")
            elif node['type'] == 'llm':
                success = self._execute_llm_node(node, stop_flag) is not None
            elif node['type'] == 'tts':
                success = self._execute_tts_node(node, stop_flag) is not None
            else:
                logger.warning(f"Unknown node type: {node['type']}")
                continue
            
            if success:
                # Mark node as completed
                completed_nodes.add(current_node_id)
                logger.info(f"Node {current_node_id} completed successfully")
                
                # For conditional nodes, only add nodes from the chosen branch
                if node['type'] == 'conditional':
                    next_node_id = self._get_conditional_branch_node(node, conditional_result)
                    if next_node_id and next_node_id not in completed_nodes:
                        ready_nodes.add(next_node_id)
                        logger.info(f"Conditional node chose branch leading to: {next_node_id}")
                else:
                    # For non-conditional nodes, check which nodes are now ready to execute
                    newly_ready = self._find_newly_ready_nodes(current_node_id, completed_nodes)
                    ready_nodes.update(newly_ready)
                    
                    if newly_ready:
                        logger.info(f"Nodes now ready for execution: {newly_ready}")
            else:
                logger.error(f"Node {current_node_id} execution failed, stopping workflow")
                return
        
        logger.info("Workflow execution completed successfully")
    
    def _execute_sequence_node(self, node, stop_flag):
        """
        Executes a sequence node and returns the next node ID.
        
        Args:
            node (dict): The sequence node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Use the SequenceExecutor to handle sequence execution
        next_node_id = self.sequence_executor.execute_sequence_node(node, stop_flag)
        return next_node_id
    
    def _get_conditional_branch_node(self, node, conditional_result):
        """
        Returns the next node ID based on the conditional result without re-evaluating.
        
        Args:
            node (dict): The conditional node from the workflow graph
            conditional_result (bool): The already evaluated conditional result
            
        Returns:
            str: Next node ID to execute based on conditional result
        """
        conditional_data = node['data']
        logger.info(f"Processing conditional branch for node: {conditional_data.get('name', conditional_data['node_id'])}")
        
        # Determine next node based on result
        connections = node['connections']
        branch = 'true' if conditional_result else 'false'
        
        logger.info(f"Conditional result: {conditional_result}, taking '{branch}' branch")
        
        if branch in connections and connections[branch]:
            # Take the first connection in the branch
            next_connection = connections[branch][0]
            return next_connection['node_id']
        else:
            logger.info(f"No connections found for '{branch}' branch, ending workflow")
            return None
    
    def _execute_conditional_node(self, node, stop_flag):
        """
        Executes a conditional node and returns the next node ID based on the result.
        
        Args:
            node (dict): The conditional node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute based on conditional result
        """
        conditional_data = node['data']
        logger.info(f"Executing conditional node: {conditional_data.get('name', conditional_data['node_id'])}")
        
        # Check if this is a loop condition
        condition_type = conditional_data.get('condition_type', 'presence')
        if condition_type == 'loop':
            # Handle loop execution
            result = self._execute_conditional_loop(conditional_data, stop_flag)
        else:
            # Evaluate the conditional normally
            result = self._evaluate_conditional(conditional_data, stop_flag)
        
        return self._get_conditional_branch_node(node, result)
    
    def _execute_llm_node(self, node, stop_flag):
        """
        Executes an LLM node and returns the next node ID.
        
        Args:
            node (dict): The LLM node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Use the LLMExecutor to handle LLM execution
        success = self.llm_executor.execute_llm_node(node, stop_flag, self.action_handlers)
        
        if success:
            # Find next node in the workflow
            connections = node['connections']
            if 'output' in connections and connections['output']:
                # Take the first connection
                next_connection = connections['output'][0]
                return next_connection['node_id']
            else:
                logger.info("No output connections found for LLM node, ending workflow")
                return None
        else:
            # LLM execution failed, stop workflow
            return None
    
    def _execute_tts_node(self, node, stop_flag):
        """
        Executes a TTS node and returns the next node ID.
        
        Args:
            node (dict): The TTS node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        tts_data = node['data']
        logger.info(f"Executing TTS node: {tts_data.get('text', 'Unknown TTS')[:30]}...") 
        
        try:
            # Execute TTS directly without GUI dependencies
            result = self._execute_tts_standalone(tts_data)
            
            if result.get('success', False):
                logger.info(f"TTS execution successful: {result.get('message', 'No message')}")
                
                # Find next node in the workflow
                connections = node['connections']
                if 'output' in connections and connections['output']:
                    # Take the first connection
                    next_connection = connections['output'][0]
                    return next_connection['node_id']
                else:
                    logger.info("No output connections found for TTS node, ending workflow")
                    return None
            else:
                logger.error(f"TTS execution failed: {result.get('error', 'Unknown error')}")
                return None
                
        except Exception as e:
            logger.error(f"Error executing TTS node: {str(e)}")
            return None
    
    def _execute_tts_standalone(self, tts_data, context_variables=None):
        """
        Execute TTS without GUI dependencies.
        
        Args:
            tts_data (dict): TTS configuration data
            context_variables (dict, optional): Context variables for text replacement
            
        Returns:
            dict: Result dictionary with success status and message
        """
        try:
            import pyttsx3
            
            # Get TTS configuration with defaults
            text_to_speak = tts_data.get('text', 'Hello, this is a text to speech message.')
            language = tts_data.get('language', 'en')
            rate = tts_data.get('rate', 200)
            volume = tts_data.get('volume', 0.9)
            voice_id = tts_data.get('voice_id', 0)
            save_to_file = tts_data.get('save_to_file', False)
            output_file = tts_data.get('output_file', '')
            wait_for_completion = tts_data.get('wait_for_completion', True)
            
            # Replace context variables in text if provided
            if context_variables:
                for var_name, var_value in context_variables.items():
                    text_to_speak = text_to_speak.replace(f'{{{var_name}}}', str(var_value))
            
            # Initialize TTS engine
            engine = pyttsx3.init()
            
            # Configure TTS settings
            engine.setProperty('rate', rate)
            engine.setProperty('volume', volume)
            
            # Set voice if specified
            voices = engine.getProperty('voices')
            if voices and 0 <= voice_id < len(voices):
                engine.setProperty('voice', voices[voice_id].id)
            
            # Save to file if requested
            if save_to_file and output_file:
                engine.save_to_file(text_to_speak, output_file)
                if wait_for_completion:
                    engine.runAndWait()
                return {
                    'success': True,
                    'text': text_to_speak,
                    'saved_to_file': output_file,
                    'message': f'TTS saved to file: {output_file}'
                }
            else:
                # Speak the text
                engine.say(text_to_speak)
                if wait_for_completion:
                    engine.runAndWait()
                return {
                    'success': True,
                    'text': text_to_speak,
                    'message': f'TTS spoke: "{text_to_speak[:50]}..."' if len(text_to_speak) > 50 else f'TTS spoke: "{text_to_speak}"'
                }
                
        except ImportError:
            return {
                'success': False,
                'error': 'pyttsx3 library not installed. Please install it using: pip install pyttsx3'
            }
        except Exception as e:
            return {
                'success': False,
                'error': f'TTS execution failed: {str(e)}'
            }
    
    # _find_next_sequence_node method removed - now handled by WorkflowNavigator
    
    # Old sequence execution methods removed - now handled by SequenceExecutor
    # _execute_sequence, _handle_pre_sequence_conditions, _handle_post_sequence_conditions