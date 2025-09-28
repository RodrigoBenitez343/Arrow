# conditional_fallback_handler.py
"""
Conditional fallback handler for multi-sequence automation.
Handles various types of conditional fallbacks for complex automation scenarios.
"""

import time
import os
from ..config import logger
from ..computer_vision import TemplateMatching
from ..sequence_player import SequencePlayer


class ConditionalFallbackHandler:
    """Handles various types of conditional fallbacks for complex automation scenarios"""
    
    def __init__(self, stop_flag=None):
        self.stop_flag = stop_flag
        self.template_matcher = TemplateMatching()
    
    def check_presence_trigger(self, trigger_config):
        """
        Check if a visual element is present on screen with enhanced validation and timeout support
        
        Args:
            trigger_config (dict): Configuration with 'image_path', 'confidence', 'timeout', etc.
        
        Returns:
            bool: True if element is found with high confidence within timeout
        """
        try:
            image_path = trigger_config.get('image_path')
            confidence = trigger_config.get('confidence', 0.8)
            timeout = float(trigger_config.get('timeout', 10))  # Default 10 seconds timeout
            
            if not image_path or not os.path.exists(image_path):
                logger.warning(f"Presence trigger image not found: {image_path}")
                return False
                
            logger.info(f"Checking presence of image: {image_path} (confidence: {confidence}, timeout: {timeout}s)")
            
            # Use template matching with timeout support
            start_time = time.time()
            scales = [1.0, 0.9, 1.1, 0.8, 1.2]  # Multi-scale detection
            
            while time.time() - start_time < timeout:
                if self.stop_flag and self.stop_flag():
                    logger.info("Presence trigger check stopped by user request")
                    return False
                    
                # Perform single presence check using the existing method
                found = self._perform_single_presence_check(image_path, confidence, scales, trigger_config)
                if found:
                    logger.info(f"Presence trigger satisfied - element found: {image_path}")
                    return True
                    
                time.sleep(0.5)
            
            logger.info(f"Presence trigger timeout - element not found: {image_path}")
            return False
            
        except Exception as e:
            logger.error(f"Error checking presence trigger: {e}")
            return False
    
    def execute_conditional_node(self, node, stop_flag=None):
        """
        Executes a conditional node and returns the next node ID based on the result.
        
        Args:
            node (dict): The conditional node from the workflow graph
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        conditional_data = node['data']
        logger.info(f"Executing conditional node: {conditional_data.get('name', conditional_data['node_id'])}")
        
        # Check if this is a loop condition
        condition_type = conditional_data.get('condition_type', 'presence')
        if condition_type == 'loop':
            result = self.execute_conditional_loop_from_node(conditional_data, stop_flag)
        else:
            result = self.evaluate_conditional_from_node(conditional_data, stop_flag)
        
        return self.get_conditional_branch_node(node, result)
    
    def evaluate_conditional_from_node(self, conditional_node, stop_flag=None):
        """
        Evaluate a conditional node using the fallback handler.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
        
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
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
        
        if condition_type == 'presence_trigger':
            return self.check_presence_trigger(condition_config)
        elif condition_type == 'absence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.check_absence_trigger(condition_config, timeout)
        elif condition_type == 'ocr_trigger':
            return self.check_ocr_trigger(condition_config)
        else:
            logger.warning(f"Unknown condition type: {condition_type} (raw: {raw_trigger_type})")
            return False
    
    def execute_conditional_loop_from_node(self, conditional_data, stop_flag=None):
        """
        Execute a conditional loop by reconstructing the loop configuration and calling the fallback handler.
        
        Args:
            conditional_data (dict): The conditional node data containing loop configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if loop completed successfully, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        try:
            import json
            from ..sequence_player import SequencePlayer
            
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
            
            # Execute the conditional loop using the fallback handler
            # Create a temporary SequencePlayer for loop execution with the sequence file
            sequence_file = loop_config.get('sequence_file', '')
            if not sequence_file:
                logger.error(f"No sequence file specified for conditional loop")
                return False
            
            temp_sequence_player = SequencePlayer(sequence_file)
            return self.execute_conditional_loop(loop_config, temp_sequence_player)
            
        except Exception as e:
            logger.error(f"Error executing conditional loop: {e}")
            return False
    
    def get_conditional_branch_node(self, node, result):
        """
        Get the next node ID based on conditional result.
        
        Args:
            node (dict): The conditional node
            result (bool): The conditional evaluation result
            
        Returns:
            str: Next node ID, or None if no connection found
        """
        # Determine which branch to follow based on result
        branch = 'true' if result else 'false'
        
        # Get connections for this branch
        connections = node.get('connections', {})
        branch_connections = connections.get(branch, [])
        
        target_node = None
        if branch_connections:
            # Take the first connection in the branch
            target_node = branch_connections[0].get('node_id')
        
        if target_node:
            return target_node
        else:
            logger.info(f"No connection for '{branch}' branch, ending workflow")
            return None
    
    def handle_until_present_condition(self, conditional_node, stop_flag=None):
        """
        Handle until present condition for backward compatibility.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        condition_data = conditional_node.get('condition_data', {})
        
        # Build the loop configuration for until_present
        loop_config = {
            'type': 'until_present',
            'max_iterations': int(condition_data.get('max_loops', 10)),
            'sequence_file': condition_data.get('sequence_file') or conditional_node.get('sequence_to_execute'),
            'iteration_delay': float(condition_data.get('iteration_delay', 1.0)),
            'condition': {
                'trigger_type': 'presence',
                'image_path': condition_data.get('image_path', ''),
                'confidence': float(condition_data.get('threshold', 0.8))
            }
        }
        
        # Create a temporary SequencePlayer for loop execution
        sequence_file = loop_config.get('sequence_file', '')
        if not sequence_file:
            logger.error(f"No sequence file specified for until_present condition")
            return False
        
        temp_sequence_player = SequencePlayer(sequence_file)
        return self.execute_conditional_loop(loop_config, temp_sequence_player)
    
    def _perform_single_presence_check(self, image_path, confidence, scales, trigger_config):
        """
        Perform a single presence check with enhanced validation
        
        Args:
            image_path (str): Path to the image to find
            confidence (float): Confidence threshold
            scales (list): List of scales to try
            trigger_config (dict): Full trigger configuration
        
        Returns:
            bool: True if element is found
        """
        # Enhanced validation: perform multiple checks with different methods
        validation_passed = 0
        total_validations = 0
        
        # Method 1: Multi-scale template matching
        result = self.template_matcher.multiscale_template_match(
            image_path, confidence, scales
        )
        total_validations += 1
        if result is not None:
            validation_passed += 1
            logger.debug(f"[VALIDATION 1/3] Multi-scale match found at {result}")
        else:
            logger.debug(f"[VALIDATION 1/3] Multi-scale match failed")
        
        # Method 2: Higher confidence check (confidence + 0.1)
        high_confidence = min(0.95, confidence + 0.1)
        result_high = self.template_matcher.multiscale_template_match(
            image_path, high_confidence, scales
        )
        total_validations += 1
        if result_high is not None:
            validation_passed += 1
            logger.debug(f"[VALIDATION 2/3] High confidence match found at {result_high}")
        else:
            logger.debug(f"[VALIDATION 2/3] High confidence match failed (threshold: {high_confidence})")
        
        # Method 3: Grayscale validation if we found something
        if result is not None:
            result_gray = self.template_matcher.multiscale_template_match(
                image_path, confidence, scales, grayscale=True
            )
            total_validations += 1
            if result_gray is not None:
                validation_passed += 1
                logger.debug(f"[VALIDATION 3/3] Grayscale match found at {result_gray}")
            else:
                logger.debug(f"[VALIDATION 3/3] Grayscale match failed")
        
        # Determine validation requirements based on strict_validation flag
        strict_validation = trigger_config.get('strict_validation', False)
        if strict_validation:
            # For strict validation (until_present loops), require at least 2 out of 3 validations to pass
            # This is more reasonable than requiring ALL validations
            min_validations_required = max(1, min(2, total_validations))
            logger.debug(f"[STRICT MODE] Requiring at least {min_validations_required}/{total_validations} validations to pass")
        else:
            # Normal validation
            min_validations_required = 1
        
        found = validation_passed >= min_validations_required
        
        if found:
            logger.debug(f"[SINGLE CHECK PASSED] Image validation passed ({validation_passed}/{total_validations}): {image_path} at coordinates {result}")
        else:
            logger.debug(f"[SINGLE CHECK FAILED] Image validation failed ({validation_passed}/{total_validations}): {image_path}")
        
        return found
    
    def check_ocr_trigger(self, trigger_config, timeout=None):
        """
        Check if specified text is detected on screen via OCR
        OCR method will always take a fresh screenshot to ensure current state validation.
        
        Args:
            trigger_config (dict): Configuration with 'target_text', 'confidence', etc.
            timeout (float): Optional timeout for waiting (not currently used)
            
        Returns:
            bool: True if text is detected with sufficient confidence
        """
        target_text = trigger_config.get('target_text')
        confidence = trigger_config.get('confidence', 0.8)
        case_sensitive = trigger_config.get('case_sensitive', True)
        
        if not target_text:
            logger.error("No target text specified for OCR trigger")
            return False
            
        try:
            logger.info(f"Checking OCR trigger for text: '{target_text}' with confidence: {confidence}")
            
            # Use TemplateMatching OCR detection - it will take its own fresh screenshot
            result = TemplateMatching.ocr_text_detection(
                target_text,
                confidence=confidence,
                case_sensitive=case_sensitive
            )
            
            if result and result.get('found'):
                logger.info(f"[OCR FOUND] Text '{target_text}' detected with confidence {result['confidence']:.3f}")
                return True
            else:
                logger.info(f"[OCR NOT FOUND] Text '{target_text}' not detected with confidence >= {confidence}")
                return False
                
        except Exception as e:
            logger.error(f"Error checking OCR trigger: {e}")
            return False
    
    def check_absence_trigger(self, trigger_config, timeout):
        """
        Check if a visual element is absent from screen within timeout
        
        Args:
            trigger_config (dict): Configuration with 'image_path', 'confidence', etc.
            timeout (float): Maximum time to wait for absence
        
        Returns:
            bool: True if element becomes absent within timeout
        """
        image_path = trigger_config.get('image_path')
        logger.info(f"Checking absence of image: {image_path} (timeout: {timeout}s)")
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.stop_flag and self.stop_flag():
                logger.info("Absence trigger check stopped by user request")
                return False
                
            # Check if element is present (we want it to be absent)
            is_present = self.check_presence_trigger(trigger_config)
            if not is_present:
                logger.info(f"Absence trigger satisfied - element not found: {image_path}")
                return True
            else:
                logger.debug(f"Element still present, continuing absence check: {image_path}")
                
            time.sleep(0.5)
        
        logger.info(f"Absence trigger timeout - element remained present: {image_path}")
        return False
    
    def execute_conditional_loop(self, loop_config, sequence_player):
        """
        Execute a conditional loop that continues while/until a condition is met
        
        Args:
            loop_config (dict): Loop configuration
            sequence_player (SequencePlayer): Player to execute sequences
        
        Returns:
            bool: True if loop completed successfully
        """
        try:
            loop_type = loop_config.get('type', 'while_present')  # while_present, while_absent, until_present, until_absent
            condition = loop_config.get('condition', {})
            max_iterations = loop_config.get('max_iterations', 10)
            iteration_delay = loop_config.get('iteration_delay', 1.0)
            sequence_file = loop_config.get('sequence_file')
            
            if not sequence_file:
                logger.error("No sequence file specified for conditional loop")
                return False
            
            logger.info(f"Starting conditional loop: {loop_type}, max_iterations: {max_iterations}")
            
            for iteration in range(max_iterations):
                if self.stop_flag and self.stop_flag():
                    logger.info("Conditional loop stopped by user request")
                    return False
                
                # Check condition based on loop type with enhanced validation for until_present
                if loop_type == 'until_present':
                    # For until_present loops, use stricter validation to prevent false positives
                    condition['strict_validation'] = True
                    # Use original confidence threshold instead of increasing it
                    original_confidence = condition.get('confidence', 0.8)
                    condition['confidence'] = original_confidence
                    logger.info(f"until_present: Using confidence threshold: {condition['confidence']}")
                
                # Determine condition type and evaluate accordingly
                # Fix: Map raw trigger types to expected condition types
                raw_trigger_type = condition.get('trigger_type') or condition.get('condition_type', 'presence')
                
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
                
                if condition_type == 'ocr_trigger':
                    condition_met = self.check_ocr_trigger(condition)
                elif condition_type == 'absence_trigger':
                    timeout = float(condition.get('timeout', 10))
                    condition_met = self.check_absence_trigger(condition, timeout)
                else:
                    # Default to presence trigger for backward compatibility
                    condition_met = self.check_presence_trigger(condition)
                logger.info(f"Conditional loop iteration {iteration + 1}/{max_iterations} - Condition check: {'Met' if condition_met else 'Not met'}")
                
                should_continue = False
                if loop_type == 'while_present' and condition_met:
                    should_continue = True
                elif loop_type == 'while_absent' and not condition_met:
                    should_continue = True
                elif loop_type == 'until_present' and not condition_met:
                    should_continue = True
                    logger.info(f"until_present: Element not found yet, continuing loop...")
                elif loop_type == 'until_absent' and condition_met:
                    should_continue = True
                
                if not should_continue:
                    if loop_type == 'until_present':
                        logger.info(f"until_present: Element FOUND! Exiting loop after {iteration} iterations")
                    else:
                        logger.info(f"Conditional loop condition satisfied after {iteration} iterations")
                    break
                
                # Execute the sequence
                loop_player = SequencePlayer(sequence_file)
                loop_player.play_sequence(stop_flag=self.stop_flag)
                
                # Check condition again after executing the sequence
                # This is crucial for until_present and until_absent loops
                if loop_type in ['until_present', 'until_absent']:
                    # Use the same enhanced configuration for post-execution check
                    post_condition = condition.copy()
                    if loop_type == 'until_present':
                        post_condition['strict_validation'] = True
                        post_condition['confidence'] = condition.get('confidence', 0.8)
                    
                    # Check condition based on type (not just presence)
                    if condition_type == 'ocr_trigger':
                        condition_met_after = self.check_ocr_trigger(post_condition)
                    elif condition_type == 'absence_trigger':
                        timeout = float(post_condition.get('timeout', 10))
                        condition_met_after = self.check_absence_trigger(post_condition, timeout)
                    else:
                        condition_met_after = self.check_presence_trigger(post_condition)
                    
                    logger.info(f"Post-execution condition check: {'Met' if condition_met_after else 'Not met'}")
                    
                    should_stop = False
                    if loop_type == 'until_present' and condition_met_after:
                        should_stop = True
                        logger.info(f"until_present: TARGET ELEMENT FOUND after sequence execution!")
                    elif loop_type == 'until_absent' and not condition_met_after:
                        should_stop = True
                    
                    if should_stop:
                        logger.info(f"Conditional loop condition satisfied after executing sequence in iteration {iteration + 1}")
                        break
                
                # Wait between iterations
                if iteration < max_iterations - 1:
                    time.sleep(iteration_delay)
            
            logger.info("Conditional loop completed")
            
            # Return whether the final condition was met, not just that the loop completed
            # Check the final condition state to determine the actual result
            if condition_type == 'ocr_trigger':
                final_condition_met = self.check_ocr_trigger(condition)
            elif condition_type == 'absence_trigger':
                timeout = float(condition.get('timeout', 10))
                final_condition_met = self.check_absence_trigger(condition, timeout)
            else:
                final_condition_met = self.check_presence_trigger(condition)
            
            # For different loop types, determine what "success" means:
            # - until_present: success means condition is now met (element found)
            # - until_absent: success means condition is now not met (element gone)
            # - while_present: success means condition is no longer met (element gone)
            # - while_absent: success means condition is now met (element appeared)
            if loop_type == 'until_present':
                result = final_condition_met
                logger.info(f"until_present loop result: {'SUCCESS - Element found' if result else 'FAILED - Element not found'}")
            elif loop_type == 'until_absent':
                result = not final_condition_met
                logger.info(f"until_absent loop result: {'SUCCESS - Element absent' if result else 'FAILED - Element still present'}")
            elif loop_type == 'while_present':
                result = not final_condition_met
                logger.info(f"while_present loop result: {'SUCCESS - Element no longer present' if result else 'FAILED - Element still present'}")
            elif loop_type == 'while_absent':
                result = final_condition_met
                logger.info(f"while_absent loop result: {'SUCCESS - Element now present' if result else 'FAILED - Element still absent'}")
            else:
                result = final_condition_met
                logger.info(f"Unknown loop type '{loop_type}', defaulting to condition state: {result}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error in conditional loop: {e}")
            return False

    def handle_pre_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle conditional checks before executing a sequence
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        
        Returns:
            bool: True if sequence should be skipped
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        # Check for presence triggers that must be met before execution
        presence_triggers = advanced_conditions.get('presence_triggers', [])
        for trigger in presence_triggers:
            if stop_flag and stop_flag():
                return True
                
            if not self.check_presence_trigger(trigger):
                logger.info(f"Presence trigger not met, skipping sequence: {trigger.get('image_path')}")
                return True
        
        # Check for absence triggers that must be met before execution
        absence_triggers = advanced_conditions.get('absence_triggers', [])
        for trigger in absence_triggers:
            if stop_flag and stop_flag():
                return True
                
            timeout = trigger.get('timeout', 10)
            if not self.check_absence_trigger(trigger, timeout):
                logger.info(f"Absence trigger not met, skipping sequence: {trigger.get('image_path')}")
                return True
        
        # Handle conditional loops that should execute before the main sequence
        pre_conditional_loops = advanced_conditions.get('pre_conditional_loops', [])
        for loop_config in pre_conditional_loops:
            if stop_flag and stop_flag():
                return True
                
            logger.info("Executing pre-sequence conditional loop")
            from ..sequence_player import SequencePlayer
            sequence_file = loop_config.get('sequence_file', '')
            if sequence_file:
                temp_sequence_player = SequencePlayer(sequence_file)
                self.execute_conditional_loop(loop_config, temp_sequence_player)
        
        return False

    def handle_post_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle conditional actions after executing a sequence
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        # Handle conditional loops that should execute after the main sequence
        post_conditional_loops = advanced_conditions.get('post_conditional_loops', [])
        for loop_config in post_conditional_loops:
            if stop_flag and stop_flag():
                return
                
            logger.info("Executing post-sequence conditional loop")
            from ..sequence_player import SequencePlayer
            sequence_file = loop_config.get('sequence_file', '')
            if sequence_file:
                temp_sequence_player = SequencePlayer(sequence_file)
                self.execute_conditional_loop(loop_config, temp_sequence_player)
        
        # Handle wait conditions (wait for presence/absence of elements or OCR text)
        wait_conditions = advanced_conditions.get('wait_conditions', [])
        for condition in wait_conditions:
            if stop_flag and stop_flag():
                return
                
            condition_type = condition.get('type', 'presence')
            timeout = float(condition.get('timeout', 30))
            
            logger.info(f"Waiting for {condition_type} condition: {condition.get('image_path') or condition.get('target_text')}")
            
            if condition_type == 'presence':
                self.check_presence_trigger(condition)
            elif condition_type == 'absence':
                self.check_absence_trigger(condition, timeout)
            elif condition_type == 'ocr':
                self.check_ocr_trigger(condition)