import logging
logger = logging.getLogger(__name__)
# conditional_loop.py
"""
Conditional loop module for conditional fallback handler.
Handles loop execution logic with various loop types and conditions.
"""
import json
import time
from ...sequence_player import SequencePlayer


class ConditionalLoop:
    """Handles conditional loop execution"""
    
    def __init__(self, template_matcher, validation_cache, path_resolver, presence_trigger, absence_trigger, ocr_trigger, stop_flag=None):
        """
        Initialize conditional loop handler.
        
        Args:
            template_matcher: TemplateMatching instance
            validation_cache: ValidationCache instance
            path_resolver: PathResolver instance
            presence_trigger: PresenceTrigger instance
            absence_trigger: AbsenceTrigger instance
            ocr_trigger: OCRTrigger instance
            stop_flag: Function to check if execution should stop
        """
        self.template_matcher = template_matcher
        self.validation_cache = validation_cache
        self.path_resolver = path_resolver
        self.presence_trigger = presence_trigger
        self.absence_trigger = absence_trigger
        self.ocr_trigger = ocr_trigger
        self.stop_flag = stop_flag
    
    def execute_conditional_loop(self, loop_config, sequence_player):
        """
        Execute a conditional loop until the loop condition is no longer met.

        The loop is infinite by design — there is no iteration cap.  It ends
        only when the condition stops being valid (or execution is stopped),
        then the workflow continues via the conditional's single output port.

        Args:
            loop_config (dict): Loop configuration containing type, condition, etc.
            sequence_player (SequencePlayer): Player instance for executing sequences

        Returns:
            bool: True if the loop completed (condition no longer met), False otherwise
        """
        try:
            loop_type = loop_config.get('type', 'while_present')
            sequence_file = loop_config.get('sequence_file', '')
            iteration_delay = float(loop_config.get('iteration_delay', 0.05))
            condition = loop_config.get('condition', {})

            # The caller (conditional_ops) configures the passed player with the
            # sandbox URL; carry it into the fresh per-iteration players so the
            # loop body acts on the same desktop the condition observes.
            self._loop_sandbox_url = None
            try:
                if sequence_player is not None:
                    self._loop_sandbox_url = getattr(sequence_player, 'sandbox_agent_url', None)
            except Exception:
                self._loop_sandbox_url = None
            
            logger.info(f"Starting conditional loop: {loop_type} (runs until the condition is no longer met)")
            
            # Accept chain_file alongside sequence_file for direct chain execution in loops
            chain_file = loop_config.get('chain_file', '') or loop_config.get('chain', '')
            if not sequence_file and not chain_file:
                logger.error("No sequence file specified for conditional loop")
                return False
            
            # Resolve the file path (sequence or chain)
            resolved_file = self.path_resolver.resolve_path(chain_file or sequence_file, add_json=True)
            if not resolved_file:
                logger.error(f"Could not resolve file: {chain_file or sequence_file}")
                return False
            
            iteration_count = 0
            while True:
                # Check stop flag
                if self.stop_flag and self.stop_flag():
                    logger.info("Stop flag detected during conditional loop - aborting")
                    return False
                
                # Evaluate loop condition
                try:
                    self.validation_cache.clear_cache()
                except Exception:
                    pass
                
                screen_cv = None
                try:
                    trigger_type = condition.get('trigger_type', 'presence')
                    if trigger_type in ['presence', 'absence', 'ocr']:
                        screen_cv = self.template_matcher.capture_screen()
                except Exception:
                    screen_cv = None
                
                should_continue = self._evaluate_loop_condition(loop_type, condition, screen_cv)
                
                if not should_continue:
                    logger.info(f"Loop condition no longer met after {iteration_count} iteration(s) — continuing workflow")
                    return True
                
                # Execute the sequence
                logger.info(f"Executing loop iteration {iteration_count + 1}")
                
                try:
                    if chain_file:
                        # Execute a full chain in this iteration
                        import json
                        with open(resolved_file, 'r', encoding='utf-8') as f:
                            chain_config = json.load(f)
                        try:
                            from ...multi_sequence_player import MultiSequencePlayer
                        except ImportError:
                            from ....multi_sequence_player import MultiSequencePlayer
                        except ImportError:
                            from player.multi_sequence_player import MultiSequencePlayer
                        temp_chain_player = MultiSequencePlayer(
                            chain_config,
                            chain_file_path=resolved_file
                        )
                        temp_chain_player.selection_memory = {}
                        if getattr(self, '_loop_sandbox_url', None):
                            try:
                                temp_chain_player.sandbox_agent_url = self._loop_sandbox_url
                                _ov = getattr(temp_chain_player, 'workflow_executor', None)
                                if _ov is not None:
                                    _g = getattr(_ov, 'global_app_context_override', None)
                                    if not isinstance(_g, dict):
                                        _g = {}
                                        try:
                                            _ov.global_app_context_override = _g
                                        except Exception:
                                            pass
                                    _g['sandboxed'] = True
                                    _g['sandbox_agent_url'] = self._loop_sandbox_url
                            except Exception:
                                pass
                        success = temp_chain_player.play_chain(stop_flag=self.stop_flag, preserve_selection_memory=False)
                    else:
                        # Create a temporary sequence player for this iteration
                        temp_player = SequencePlayer(resolved_file)
                        temp_player.selection_memory = {}
                        if getattr(self, '_loop_sandbox_url', None):
                            try:
                                temp_player.sandbox_agent_url = self._loop_sandbox_url
                                temp_player.bot.sandbox_agent_url = self._loop_sandbox_url
                                if hasattr(temp_player, 'action_handlers') and temp_player.action_handlers:
                                    temp_player.action_handlers.bot.sandbox_agent_url = self._loop_sandbox_url
                                from ...computer_vision import set_sandbox_agent_url
                                set_sandbox_agent_url(self._loop_sandbox_url)
                            except Exception:
                                pass
                        # Execute the sequence
                        success = temp_player.play_sequence(stop_flag=self.stop_flag, skip_initial_delay=True)
                    
                    if not success:
                        logger.warning(f"Sequence execution failed in loop iteration {iteration_count + 1}")
                        # Continue with next iteration instead of breaking
                    
                except Exception as e:
                    logger.error(f"Error executing sequence in loop iteration {iteration_count + 1}: {e}")
                    # Continue with next iteration
                
                iteration_count += 1
                
                # Add delay between iterations
                if iteration_delay > 0:
                    # Sleep in small increments to allow for interruption
                    elapsed = 0.0
                    while elapsed < iteration_delay:
                        if self.stop_flag and self.stop_flag():
                            logger.info("Stop flag detected during loop iteration delay - aborting")
                            return False
                        sleep_time = min(0.05, iteration_delay - elapsed)
                        time.sleep(sleep_time)
                        elapsed += sleep_time
            
        except Exception as e:
            logger.error(f"Error executing conditional loop: {e}")
            return False
    
    def _evaluate_loop_condition(self, loop_type, condition, screen_cv=None):
        """
        Evaluate the loop condition based on loop type.
        
        Args:
            loop_type (str): Type of loop ('while_present', 'while_absent', 'until_present', 'until_absent')
            condition (dict): Condition configuration
            
        Returns:
            bool: True if loop should continue, False otherwise
        """
        try:
            if not condition:
                logger.warning("No condition specified for loop - defaulting to single iteration")
                return False
            
            trigger_type = condition.get('trigger_type', 'presence')
            
            # Evaluate the condition based on trigger type
            if trigger_type == 'presence':
                condition_met = self.presence_trigger.check_presence_trigger(condition, screen_cv)
            elif trigger_type == 'absence':
                condition_met = self.absence_trigger.check_absence_immediate(condition, screen_cv=screen_cv)
            elif trigger_type == 'ocr':
                condition_met = self.ocr_trigger.check_ocr_immediate(condition, screen_cv=screen_cv)
            else:
                logger.warning(f"Unknown trigger type for loop condition: {trigger_type}")
                return False
            
            # Determine if loop should continue based on loop type and condition result
            if loop_type == 'while_present':
                return condition_met  # Continue while condition is true
            elif loop_type == 'while_absent':
                return not condition_met  # Continue while condition is false
            elif loop_type == 'until_present':
                return not condition_met  # Continue until condition becomes true
            elif loop_type == 'until_absent':
                return condition_met  # Continue until condition becomes false
            else:
                logger.warning(f"Unknown loop type: {loop_type}")
                return False
            
        except Exception as e:
            logger.error(f"Error evaluating loop condition: {e}")
            return False
    
    def execute_conditional_loop_from_node(self, conditional_data, stop_flag=None):
        """
        Execute a conditional loop by reconstructing the loop configuration from node data.
        
        Args:
            conditional_data (dict): The conditional node data containing loop configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if loop completed successfully, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        try:
            # Reconstruct loop configuration from conditional node data
            loop_type = conditional_data.get('loop_type', 'while_present')
            sequence_file = conditional_data.get('sequence_file', '')
            chain_file = conditional_data.get('chain_file', '') or conditional_data.get('chain', '')
            iteration_delay = float(conditional_data.get('iteration_delay', 0.05))
            
            # Parse the stored loop condition
            loop_condition_json = conditional_data.get('loop_condition', '{}')
            try:
                condition = json.loads(loop_condition_json) if loop_condition_json else {}
            except (json.JSONDecodeError, TypeError):
                # Fallback to basic image condition if JSON parsing fails
                condition = {
                    'trigger_type': 'presence',
                    'image_path': conditional_data.get('image_path', ''),
                    'image_data': conditional_data.get('image_data', ''),
                    'confidence': float(conditional_data.get('threshold', 0.8))
                }
            
            # Build loop configuration (no iteration cap — the loop ends only
            # when the condition is no longer met)
            loop_config = {
                'type': loop_type,
                'sequence_file': sequence_file,
                'chain_file': chain_file,
                'iteration_delay': iteration_delay,
                'condition': condition
            }
            
            logger.info(f"Executing conditional loop from node: {loop_type}, sequence: {sequence_file}, chain: {chain_file}")
            
            # Create a temporary SequencePlayer for loop execution
            if not sequence_file and not chain_file:
                logger.error("No sequence file specified for conditional loop")
                return False
            
            temp_sequence_player = SequencePlayer(sequence_file)
            # Share selection memory if available
            try:
                temp_sequence_player.selection_memory = getattr(self, 'selection_memory', {})
            except Exception:
                pass
                
            return self.execute_conditional_loop(loop_config, temp_sequence_player)
            
        except Exception as e:
            logger.error(f"Error executing conditional loop from node: {e}")
            return False
    
    def validate_loop_config(self, loop_config):
        """
        Validate a loop configuration for completeness and correctness.
        
        Args:
            loop_config (dict): Loop configuration to validate
            
        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            # Check required fields
            if 'type' not in loop_config:
                return False, "Loop type not specified"
            
            if 'sequence_file' not in loop_config or not loop_config['sequence_file']:
                return False, "Sequence file not specified"
            
            if 'condition' not in loop_config or not loop_config['condition']:
                return False, "Loop condition not specified"
            
            # Validate loop type
            valid_types = ['while_present', 'while_absent', 'until_present', 'until_absent']
            if loop_config['type'] not in valid_types:
                return False, f"Invalid loop type. Must be one of: {valid_types}"
            
            # Validate condition
            condition = loop_config['condition']
            if 'trigger_type' not in condition:
                return False, "Condition trigger type not specified"
            
            valid_triggers = ['presence', 'absence', 'ocr']
            if condition['trigger_type'] not in valid_triggers:
                return False, f"Invalid trigger type. Must be one of: {valid_triggers}"
            
            # Validate sequence file exists
            sequence_file = loop_config['sequence_file']
            resolved_file = self.path_resolver.resolve_path(sequence_file, add_json=True)
            if not resolved_file:
                return False, f"Sequence file not found: {sequence_file}"
            
            return True, "Loop configuration is valid"
            
        except Exception as e:
            return False, f"Error validating loop config: {e}"
