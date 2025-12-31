# sequence_executor.py
"""
Sequence executor for multi-sequence automation.
Handles execution of sequence nodes and condition handling.
"""
import os
import sys
import json
import time
import re
from ..config import logger
from ..sequence_player import SequencePlayer
from ..json_cache import get_sequence
from ..base_bot import SeleniumBot


class SequenceExecutor:
    """Executes sequence nodes and handles conditions"""
    
    def __init__(self, fallback_handler=None, chain_file_dir=None):
        self.fallback_handler = fallback_handler
        self.chain_file_dir = chain_file_dir  # Directory containing the chain file
        # Shared selection memory to avoid random candidate selection across sequences in a chain run
        self.selection_memory = {}
    
    def execute_sequence_node(self, node, stop_flag):
        """
        Executes a sequence node and returns the next node ID.
        
        Args:
            node (dict): The sequence node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        sequence_data = node['data']
        logger.info(f"=== STARTING SEQUENCE NODE EXECUTION ===")
        # Support both 'id' and 'node_id' fields
        node_identifier = sequence_data.get('id') or sequence_data.get('node_id', 'unknown')
        logger.info(f"Executing sequence node: {sequence_data.get('name', node_identifier)}")
        logger.info(f"Full node data: {node}")
        
        # Execute the sequence
        success = self.execute_sequence(sequence_data, stop_flag)
        
        if not success:
            node_identifier = sequence_data.get('id') or sequence_data.get('node_id', 'unknown')
            logger.error(f"Sequence execution failed: {sequence_data.get('name', node_identifier)}")
            return None
        
        # Find next node in the workflow
        connections = node['connections']
        if 'output' in connections and connections['output']:
            # Take the first connection
            next_connection = connections['output'][0]
            return next_connection['node_id']
        else:
            logger.info("No output connections found for sequence node, ending workflow")
            return None
    
    def execute_sequence(self, item, stop_flag):
        """
        Execute a single sequence with all its configurations
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if sequence executed successfully
        """
        # Extract sequence file from name field if sequence_file not present
        seq_file = item.get('sequence_file')
        if not seq_file:
            name = item.get('name', '')
            if ': ' in name:
                # Extract filename from "Seq X: filename.json" format
                seq_file = name.split(': ', 1)[1]
            else:
                seq_file = name
                
            # Handle numbered sequence names (e.g., "1.json 2" -> "1.json")
            # Remove trailing space and number pattern
            if seq_file and re.match(r'^(.+\.json)\s+\d+$', seq_file):
                base_name = re.match(r'^(.+\.json)\s+\d+$', seq_file).group(1)
                logger.info(f"Detected numbered sequence name '{seq_file}', using base name '{base_name}'")
                seq_file = base_name
        
        # Enhanced path resolution with better error handling
        original_seq_file = seq_file
        
        if seq_file:
            # Determine base directory for path resolution
            base_dir = self.chain_file_dir if self.chain_file_dir else os.getcwd()
            base_dir_name = os.path.basename(os.path.normpath(base_dir)).lower() if base_dir else ""
            is_bare_filename = (
                os.path.basename(seq_file) == seq_file
                and ("/" not in seq_file and "\\" not in seq_file)
            )
            
            # Try multiple path resolution strategies
            # Determine executable directory for frozen builds
            exe_dir = None
            if getattr(sys, 'frozen', False):
                exe_dir = os.path.dirname(sys.executable)
            
            looper_sequences = os.path.join(os.getcwd(), 'LoOper', 'sequences')
            possible_paths = [seq_file]  # original path (may already be absolute)

            if is_bare_filename:
                possible_paths.extend([
                    os.path.join(base_dir, 'sequences', seq_file),  # sequences next to chain
                    os.path.join(os.getcwd(), 'sequences', seq_file),  # cwd/sequences
                    os.path.join('sequences', seq_file),  # sequences relative to cwd
                    os.path.join(looper_sequences, seq_file),  # LoOper/sequences
                ])
                possible_paths.append(os.path.join(os.getcwd(), seq_file))
                possible_paths.append(os.path.join(base_dir, seq_file))  # last-resort (can collide with chains/*.json)
            else:
                possible_paths.extend([
                    os.path.join(base_dir, seq_file),
                    os.path.join(base_dir, 'sequences', seq_file),
                    os.path.join('sequences', seq_file),
                    os.path.join(os.getcwd(), seq_file),
                    os.path.join(os.getcwd(), 'sequences', seq_file),
                    os.path.join(looper_sequences, seq_file),
                ])
            
            # Add frozen build paths if applicable
            if exe_dir:
                possible_paths.extend([
                    os.path.join(exe_dir, seq_file),
                    os.path.join(exe_dir, 'sequences', seq_file),
                ])
            
            possible_paths.extend([
                os.path.join(os.getcwd(), 'LoOper', 'sequences', seq_file),  # absolute LoOper/sequences
            ])
            
            # Add .json extension if missing
            if not seq_file.endswith('.json'):
                json_name = seq_file + '.json'
                possible_paths.append(json_name)
                if is_bare_filename:
                    possible_paths.extend([
                        os.path.join(base_dir, 'sequences', json_name),
                        os.path.join(os.getcwd(), 'sequences', json_name),
                        os.path.join('sequences', json_name),
                        os.path.join(looper_sequences, json_name),
                    ])
                    possible_paths.append(os.path.join(os.getcwd(), json_name))
                    possible_paths.append(os.path.join(base_dir, json_name))
                else:
                    possible_paths.extend([
                        os.path.join(base_dir, json_name),
                        os.path.join(base_dir, 'sequences', json_name),
                        os.path.join('sequences', json_name),
                        os.path.join(os.getcwd(), json_name),
                        os.path.join(os.getcwd(), 'sequences', json_name),
                        os.path.join(looper_sequences, json_name),
                        os.path.join(os.getcwd(), 'LoOper', 'sequences', json_name),
                    ])
                if exe_dir:
                    possible_paths.extend([
                        os.path.join(exe_dir, json_name),
                        os.path.join(exe_dir, 'sequences', json_name),
                    ])
            
            # Find the first existing file
            seq_file = None
            for path in possible_paths:
                if os.path.exists(path):
                    seq_file = path
                    logger.info(f"Found sequence file at: {seq_file}")
                    break
            
            if not seq_file:
                logger.error(f"Sequence file not found: '{original_seq_file}'")
                logger.error(f"Searched in paths: {possible_paths}")
                logger.error(f"Current working directory: {os.getcwd()}")
                logger.error(f"Available files in current directory: {os.listdir(os.getcwd()) if os.path.exists(os.getcwd()) else 'N/A'}")
                
                # Check if sequences directory exists
                sequences_dir = os.path.join(os.getcwd(), 'sequences')
                if os.path.exists(sequences_dir):
                    logger.error(f"Available files in sequences directory: {os.listdir(sequences_dir)}")
                else:
                    logger.error("Sequences directory does not exist")
                
                return False
        
        # Handle both old format (loops) and new format (loop_count)
        loops = item.get('loop_count', item.get('loops', 1))
        extra_delay = item.get('extra_delay', 0)
        advanced_conditions = item.get('advanced_conditions', {})
        
        logger.info(f"Executing sequence: {seq_file} (loops: {loops}, extra_delay: {extra_delay})")
        
        # Handle pre-sequence conditions
        if self._handle_pre_sequence_conditions(item, stop_flag, advanced_conditions):
            return True  # Sequence was skipped due to conditions
        
        # Execute the sequence for the specified number of loops
        for i in range(loops):
            if stop_flag and stop_flag():
                logger.info("Sequence execution stopped by user request")
                return False
            
            logger.info(f"Starting loop {i + 1}/{loops} for sequence: {seq_file}")
            
            # Load sequence data - try cache first for optimal performance
            try:
                # First try to get from cache
                sequence_data = get_sequence(seq_file, self.chain_file_dir)
                if not sequence_data and self.chain_file_dir:
                    sequence_data = get_sequence(seq_file)
                if sequence_data:
                    logger.info(f"Using cached sequence data for: {seq_file}")
                    # Create a player instance without reloading JSON but ensure base init occurs
                    player = SequencePlayer.__new__(SequencePlayer)
                    try:
                        # Initialize without loading the file to set up base bot state and handlers
                        SequencePlayer.__init__(player, seq_file, lazy_load=True)
                    except Exception as init_err:
                        # Fallback: directly initialize SeleniumBot base to provide action handlers
                        logger.debug(f"SequencePlayer lazy init failed, falling back to base init: {init_err}")
                        SeleniumBot.__init__(player)
                        player.sequence_file = seq_file
                    # Share selection memory and inject cached data
                    player.selection_memory = self.selection_memory
                    player.sequence_data = sequence_data.copy()
                else:
                    # Fallback to traditional loading if not in cache
                    logger.info(f"Loading sequence from disk (not in cache): {seq_file}")
                    player = SequencePlayer(seq_file)
                    # Share selection memory with the loader instance
                    player.selection_memory = self.selection_memory
                    sequence_data = player.sequence_data.copy()
            except Exception as e:
                logger.error(f"Failed to load sequence {seq_file}: {e}")
                return False
            
            # 1. Chain-level fallback sequences (applied to all click actions)
            if 'fallback_sequences' in item:
                fallback_sequences = item['fallback_sequences']
                for action in sequence_data['actions']:
                    if action.get('type') in ['click', 'double_click'] and action.get('screenshot'):
                        action['fallback_sequences'] = fallback_sequences
                        logger.info(f"Injected chain-level fallback sequences into click action: {fallback_sequences}")
            
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
                    # Share selection memory with fallback player
                    fallback_player.selection_memory = self.selection_memory
                    # Skip initial delay for faster fallback execution
                    fallback_player.play_sequence(stop_flag=stop_flag, skip_initial_delay=True)
                    logger.info(f"Fallback sequence completed. Current sequence was aborted.")
                    
                except Exception as e:
                    logger.error(f"Fallback sequence failed: {e}")
            
            # Play the sequence with fallback support
            try:
                player = SequencePlayer(seq_file)
                # Share selection memory with the player instance
                player.selection_memory = self.selection_memory
                player.sequence_data = sequence_data  # Use the modified sequence data with fallback
                player.play_sequence(fallback_callback=fallback_callback, stop_flag=stop_flag)
            except Exception as e:
                logger.error(f"Error playing sequence {seq_file}: {e}")
                return False
            
            # Add extra delay between loops if specified
            if i < loops - 1 and extra_delay > 0:
                logger.info(f"Extra delay between loops: {extra_delay} seconds")
                delay_remaining = extra_delay
                while delay_remaining > 0:
                    if stop_flag and stop_flag():
                        logger.info("Sequence execution stopped during delay")
                        return False
                    sleep_time = min(0.1, delay_remaining)
                    time.sleep(sleep_time)
                    delay_remaining -= sleep_time
        
        # Handle post-sequence conditions
        self._handle_post_sequence_conditions(item, stop_flag, advanced_conditions)
        
        return True
    
    def _handle_pre_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle conditional checks before executing a sequence
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        
        Returns:
            bool: True if sequence should be skipped
        """
        if not self.fallback_handler:
            return False
            
        # Capture a single screenshot for all trigger checks to improve performance
        screen_cv = None
        presence_triggers = advanced_conditions.get('presence_triggers', [])
        absence_triggers = advanced_conditions.get('absence_triggers', [])
        
        # Only capture screenshot if we have triggers to check
        if presence_triggers or absence_triggers:
            screen_cv = self.fallback_handler.template_matcher.capture_screen()
            
        # Check for presence triggers that must be met before execution
        for trigger in presence_triggers:
            if stop_flag and stop_flag():
                return True
                
            if not self.fallback_handler.check_presence_trigger(trigger, screen_cv=screen_cv):
                logger.info(f"Presence trigger not met, skipping sequence: {trigger.get('image_path')}")
                return True
        
        # Check for absence triggers that must be met before execution
        for trigger in absence_triggers:
            if stop_flag and stop_flag():
                return True
                
            timeout = trigger.get('timeout', 10)
            if not self.fallback_handler.check_absence_trigger(trigger, timeout, screen_cv=screen_cv):
                logger.info(f"Absence trigger not met, skipping sequence: {trigger.get('image_path')}")
                return True
        
        # Handle conditional loops that should execute before the main sequence
        pre_conditional_loops = advanced_conditions.get('pre_conditional_loops', [])
        for loop_config in pre_conditional_loops:
            if stop_flag and stop_flag():
                return True
                
            logger.info("Executing pre-sequence conditional loop")
            self.fallback_handler.execute_conditional_loop(loop_config, None)
        
        return False
    
    def _handle_post_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle conditional actions after executing a sequence
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        """
        if not self.fallback_handler:
            return
            
        # Handle conditional loops that should execute after the main sequence
        post_conditional_loops = advanced_conditions.get('post_conditional_loops', [])
        for loop_config in post_conditional_loops:
            if stop_flag and stop_flag():
                return
                
            logger.info("Executing post-sequence conditional loop")
            self.fallback_handler.execute_conditional_loop(loop_config, None)
        
        # Handle wait conditions (wait for presence/absence of elements or OCR text)
        wait_conditions = advanced_conditions.get('wait_conditions', [])
        for condition in wait_conditions:
            if stop_flag and stop_flag():
                return
                
            condition_type = condition.get('type', 'presence')
            timeout = float(condition.get('timeout', 30))
            
            logger.info(f"Waiting for {condition_type} condition: {condition.get('image_path') or condition.get('target_text')}")
            
            if condition_type == 'presence':
                start_time = time.time()
                while time.time() - start_time < timeout:
                    if stop_flag and stop_flag():
                        return
                    # Capture fresh screenshot for each polling iteration in wait conditions
                    screen_cv = self.fallback_handler.template_matcher.capture_screen()
                    if self.fallback_handler.check_presence_trigger(condition, screen_cv=screen_cv):
                        logger.info("Presence wait condition satisfied")
                        break
                    time.sleep(0.02)  # Reduced from 0.1
            elif condition_type == 'absence':
                # For absence checks, let the method handle its own polling with shared screenshots
                self.fallback_handler.check_absence_trigger(condition, timeout)
            elif condition_type == 'ocr':
                start_time = time.time()
                while time.time() - start_time < timeout:
                    if stop_flag and stop_flag():
                        return
                    if self.fallback_handler.check_ocr_trigger(condition, stop_flag=stop_flag):
                        logger.info("OCR wait condition satisfied")
                        break
                    time.sleep(0.02)  # Reduced from 0.1
            elif condition_type == 'wait_time':
                wait_time = float(condition.get('wait_time', 5))
                logger.info(f"Waiting for {wait_time} seconds")
                time.sleep(wait_time)
