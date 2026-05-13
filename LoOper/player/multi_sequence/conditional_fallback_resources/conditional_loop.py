# conditional_loop.py
"""
Conditional loop module for conditional fallback handler.
Handles loop execution logic with various loop types and conditions.
"""
import json
import time
from ...config import logger
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
        Execute a conditional loop with the specified configuration.
        
        Args:
            loop_config (dict): Loop configuration containing type, condition, etc.
            sequence_player (SequencePlayer): Player instance for executing sequences
            
        Returns:
            bool: True if loop completed successfully, False otherwise
        """
        try:
            loop_type = loop_config.get('type', 'while_present')
            max_iterations = int(loop_config.get('max_iterations', 10))
            sequence_file = loop_config.get('sequence_file', '')
            iteration_delay = float(loop_config.get('iteration_delay', 0.05))
            condition = loop_config.get('condition', {})
            
            logger.info(f"Starting conditional loop: {loop_type}, max_iterations: {max_iterations}")
            
            if not sequence_file:
                logger.error("No sequence file specified for conditional loop")
                return False
            
            # Resolve sequence file path
            resolved_sequence_file = self.path_resolver.resolve_path(sequence_file, add_json=True)
            if not resolved_sequence_file:
                logger.error(f"Could not resolve sequence file: {sequence_file}")
                return False
            
            iteration_count = 0
            
            while iteration_count < max_iterations:
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
                    logger.info(f"Loop condition no longer met after {iteration_count} iterations")
                    break
                
                # Execute the sequence
                logger.info(f"Executing loop iteration {iteration_count + 1}/{max_iterations}")
                
                try:
                    # Create a temporary sequence player for this iteration
                    temp_player = SequencePlayer(resolved_sequence_file)
                    temp_player.selection_memory = {}
                    
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
            
            if iteration_count >= max_iterations:
                logger.info(f"Loop completed maximum iterations: {max_iterations}")
            
            return True
            
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
            max_iterations = int(conditional_data.get('max_loops', 10))
            sequence_file = conditional_data.get('sequence_file', '')
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
            
            # Build loop configuration
            loop_config = {
                'type': loop_type,
                'max_iterations': max_iterations,
                'sequence_file': sequence_file,
                'iteration_delay': iteration_delay,
                'condition': condition
            }
            
            logger.info(f"Executing conditional loop from node: {loop_type}, sequence: {sequence_file}")
            
            # Create a temporary SequencePlayer for loop execution
            if not sequence_file:
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
