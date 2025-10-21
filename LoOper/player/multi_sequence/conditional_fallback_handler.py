# conditional_fallback_handler.py
"""
Conditional fallback handler for multi-sequence automation.
Handles various types of conditional fallbacks for complex automation scenarios.
"""
import sys
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..config import logger
from ..computer_vision import TemplateMatching
from ..sequence_player import SequencePlayer


class ConditionalFallbackHandler:
    """Handles various types of conditional fallbacks for complex automation scenarios"""
    
    def __init__(self, stop_flag=None, chain_file_dir=None):
        self.stop_flag = stop_flag
        self.template_matcher = TemplateMatching()
        # Base directory of the current chain file, used to resolve relative paths for nested imports
        self.chain_file_dir = chain_file_dir

    def _resolve_path(self, path, add_json=False):
        """Resolve a possibly relative path against the chain file directory and cwd.
        When add_json=True, also consider .json variants and common 'sequences' subdirectories."""
        import os
        if not path:
            return path
        # If the path already exists as given, return it
        try:
            if os.path.isabs(path) and os.path.exists(path):
                return path
        except Exception:
            pass
        base_dir = self.chain_file_dir or os.getcwd()
        candidates = []
        # Original path (relative)
        candidates.append(path)
        if add_json and not path.lower().endswith('.json'):
            candidates.append(path + '.json')
        # Relative to chain file directory
        candidates.append(os.path.join(base_dir, path))
        if add_json and not path.lower().endswith('.json'):
            candidates.append(os.path.join(base_dir, path + '.json'))
        # Common subdirectories for sequences
        if add_json:
            candidates.append(os.path.join(base_dir, 'sequences', path))
            if not path.lower().endswith('.json'):
                candidates.append(os.path.join(base_dir, 'sequences', path + '.json'))
            candidates.append(os.path.join('sequences', path))
            if not path.lower().endswith('.json'):
                candidates.append(os.path.join('sequences', path + '.json'))
        # Current working directory fallbacks
        candidates.append(os.path.join(os.getcwd(), path))
        if add_json and not path.lower().endswith('.json'):
            candidates.append(os.path.join(os.getcwd(), path + '.json'))
        # Return the first existing candidate
        for candidate in candidates:
            try:
                if os.path.exists(candidate):
                    return candidate
            except Exception:
                continue
        return path

    def check_presence_trigger(self, trigger_config, screen_cv=None):
        """
        Check if a visual element is present on screen
        
        Args:
            trigger_config (dict): Configuration with 'image_path', 'confidence', 'timeout'
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
        Returns:
            bool: True if element is found within timeout
        """
        try:
            image_path = trigger_config.get('image_path')
            # Resolve image path relative to the chain file directory for nested imports
            image_path = self._resolve_path(image_path, add_json=False)
            confidence = trigger_config.get('confidence', 0.8)
            timeout = float(trigger_config.get('timeout', 10))  # Default 10 seconds timeout
            
            if not image_path or not os.path.exists(image_path):
                logger.warning(f"Presence trigger image not found: {image_path}")
                return False
            
            logger.info(f"Checking presence of image: {image_path} (confidence: {confidence}, timeout: {timeout}s)")
            
            # Use template matching with timeout support
            start_time = time.time()
            scales = [1.0, 0.9, 1.1]  # Optimized scales for faster matching
            
            while time.time() - start_time < timeout:
                if self.stop_flag and self.stop_flag():
                    logger.info("Presence trigger check stopped by user request")
                    return False
                
                # Capture shared screenshot if not provided
                current_screen_cv = screen_cv
                if current_screen_cv is None:
                    current_screen_cv = self.template_matcher.capture_screen()
                    
                # Perform single presence check using the existing method
                found = self._perform_single_presence_check(image_path, confidence, scales, trigger_config, current_screen_cv)
                if found:
                    logger.info(f"Presence trigger satisfied - element found: {image_path}")
                    return True
                
                # Only sleep if we're in a polling loop (screen_cv not provided)
                if screen_cv is None:
                    time.sleep(0.1)  # Reduced from 0.5s to 0.1s for faster polling
                else:
                    # If screen_cv was provided, this is a single-shot check
                    break
            
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
            return self.check_ocr_trigger(condition_config, stop_flag=stop_flag)
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
            iteration_delay = float(conditional_data.get('iteration_delay', 0.3))
            
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
            # Share selection memory with temporary player if available
            try:
                temp_sequence_player.selection_memory = getattr(self, 'selection_memory', {})
            except Exception:
                pass
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
                'image_path': self._resolve_path(condition_data.get('image_path', ''), add_json=False),
                'confidence': float(condition_data.get('threshold', 0.8))
            }
        }
        
        # Create a temporary SequencePlayer for loop execution
        sequence_file = loop_config.get('sequence_file', '')
        resolved_seq_file = self._resolve_path(sequence_file, add_json=True)
        if not resolved_seq_file:
            logger.error(f"No sequence file specified for until_present condition")
            return False
        
        temp_sequence_player = SequencePlayer(resolved_seq_file)
        # Share selection memory with temporary player if available
        try:
            temp_sequence_player.selection_memory = getattr(self, 'selection_memory', {})
        except Exception:
            pass
        return self.execute_conditional_loop(loop_config, temp_sequence_player)
    
    def _perform_single_presence_check(self, image_path, confidence, scales, trigger_config, screen_cv=None):
        """
        Perform a single presence check with optimized fast validation
        
        Args:
            image_path (str): Path to the image to find
            confidence (float): Confidence threshold
            scales (list): List of scales to try
            trigger_config (dict): Full trigger configuration
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
        Returns:
            bool: True if element is found
        """
        try:
            # Fast single-pass validation with early exit
            # Use only the most effective scales for speed
            fast_scales = [1.0, 0.9, 1.1]  # Reduced from 5 to 3 scales
            
            # Check if we have region hints from selection_memory or trigger config
            region_hint = None
            hint_x = hint_y = None
            
            # First, check if trigger config has explicit region or coordinates
            if 'region' in trigger_config and trigger_config['region']:
                region = trigger_config['region']
                if isinstance(region, dict):
                    hint_x = region.get('x')
                    hint_y = region.get('y')
                elif isinstance(region, (list, tuple)) and len(region) >= 2:
                    hint_x, hint_y = region[0], region[1]
            
            # If no explicit region, check selection_memory for last known coordinates
            if hint_x is None or hint_y is None:
                if hasattr(self, 'selection_memory') and self.selection_memory:
                    element_key = os.path.abspath(image_path)
                    if element_key in self.selection_memory:
                        # selection_memory stores the last selected index, not coordinates
                        # We'll need to use the original coordinates from the action if available
                        pass
                elif hasattr(self, 'template_matcher') and hasattr(self.template_matcher, 'bot') and hasattr(self.template_matcher.bot, 'selection_memory'):
                    element_key = os.path.abspath(image_path)
                    if element_key in self.template_matcher.bot.selection_memory:
                        # Similar case - selection_memory stores index, not coordinates
                        pass
            
            # If we have hint coordinates, try region-based search first for better performance
            if isinstance(hint_x, (int, float)) and isinstance(hint_y, (int, float)):
                logger.debug(f"[REGION HINT] Using region-based search with hint ({hint_x}, {hint_y})")
                
                # Try region-based search with moderate expansion factor
                region_result = self.template_matcher.region_based_search(
                    image_path,
                    int(hint_x),
                    int(hint_y),
                    confidence,
                    expand_factor=2.0,  # Reasonable expansion for conditional triggers
                    grayscale=True,
                    screen_cv=screen_cv
                )
                
                if region_result is not None:
                    logger.debug(f"[REGION HIT] Region-based search found match at {region_result}: {image_path}")
                    return True
                else:
                    logger.debug(f"[REGION MISS] Region-based search failed, falling back to full-screen: {image_path}")
            
            # Primary validation: Multi-scale template matching with shared screen
            result = self.template_matcher.multiscale_template_match(
                image_path, confidence, fast_scales, screen_cv=screen_cv
            )
            
            if result is not None:
                logger.debug(f"[FAST CHECK PASSED] Primary validation found match at {result}: {image_path}")
                
                # For strict validation, perform one additional quick check with higher confidence
                strict_validation = trigger_config.get('strict_validation', False)
                if strict_validation:
                    high_confidence = min(0.95, confidence + 0.15)
                    high_conf_result = self.template_matcher.multiscale_template_match(
                        image_path, high_confidence, [1.0], screen_cv=screen_cv  # Only check at 1.0 scale for speed
                    )
                    if high_conf_result is not None:
                        logger.debug(f"[STRICT CHECK PASSED] High confidence validation passed: {image_path}")
                        return True
                    else:
                        logger.debug(f"[STRICT CHECK FAILED] High confidence validation failed, but primary passed: {image_path}")
                        return True  # Still return True since primary validation passed
                else:
                    return True
            else:
                logger.debug(f"[FAST CHECK FAILED] Primary validation failed: {image_path}")
                return False
                
        except Exception as e:
            logger.debug(f"[FAST CHECK ERROR] Exception during validation: {e}")
            return False
    
    def check_ocr_trigger(self, trigger_config, timeout=None, stop_flag=None):
        """
        Check if specified text is detected on screen via OCR
        OCR method will always take a fresh screenshot to ensure current state validation.
        
        Args:
            trigger_config (dict): Configuration with 'target_text', 'confidence', etc.
            timeout (float): Optional timeout for waiting (not currently used)
            stop_flag (callable): Function to check if operation should be stopped
            
        Returns:
            bool: True if text is detected with sufficient confidence
        """
        # Check stop flag before starting OCR trigger
        if stop_flag and stop_flag():
            logger.info("Stop flag detected before OCR trigger check - aborting")
            return False
        
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
                case_sensitive=case_sensitive,
                stop_flag=stop_flag
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
    
    def check_absence_trigger(self, trigger_config, timeout, screen_cv=None):
        """
        Check if a visual element is absent from screen within timeout
        
        Args:
            trigger_config (dict): Configuration with 'image_path', 'confidence', etc.
            timeout (float): Maximum time to wait for absence
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
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
            
            # Capture shared screenshot if not provided
            current_screen_cv = screen_cv
            if current_screen_cv is None:
                current_screen_cv = self.template_matcher.capture_screen()
                
            # Check if element is present using shared screenshot (we want it to be absent)
            is_present = self._perform_single_presence_check(
                self._resolve_path(trigger_config.get('image_path'), add_json=False),
                trigger_config.get('confidence', 0.8),
                [1.0, 0.9, 1.1],
                trigger_config,
                current_screen_cv
            )
            
            if not is_present:
                logger.info(f"Absence trigger satisfied - element not found: {image_path}")
                return True
            else:
                logger.debug(f"Element still present, continuing absence check: {image_path}")
            
            # Only sleep if we're in a polling loop (screen_cv not provided)
            if screen_cv is None:
                time.sleep(0.1)  # Reduced from 0.5s to 0.1s for faster polling
            else:
                # If screen_cv was provided, this is a single-shot check
                break
        
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
            iteration_delay = loop_config.get('iteration_delay', 0.3)
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
                
                # Execute the sequence using the provided player when available
                loop_player = sequence_player if sequence_player else SequencePlayer(sequence_file)
                try:
                    # Share selection memory if available
                    loop_player.selection_memory = getattr(self, 'selection_memory', {})
                except Exception:
                    pass
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
        Handle conditional checks before executing a sequence with parallel processing
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        
        Returns:
            bool: True if sequence should be skipped
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        # Collect all trigger checks to run in parallel
        trigger_tasks = []
        
        # Prepare presence trigger tasks
        presence_triggers = advanced_conditions.get('presence_triggers', [])
        for trigger in presence_triggers:
            trigger_tasks.append(('presence', trigger))
        
        # Prepare absence trigger tasks
        absence_triggers = advanced_conditions.get('absence_triggers', [])
        for trigger in absence_triggers:
            trigger_tasks.append(('absence', trigger))
        
        # Execute all trigger checks in parallel if we have any
        if trigger_tasks:
            def check_trigger(trigger_type, trigger_config):
                """Helper function to check a single trigger"""
                try:
                    if stop_flag and stop_flag():
                        return False, f"Stop flag detected for {trigger_type} trigger"
                    
                    if trigger_type == 'presence':
                        result = self.check_presence_trigger(trigger_config)
                        if not result:
                            return False, f"Presence trigger not met: {trigger_config.get('image_path')}"
                        return True, None
                    
                    elif trigger_type == 'absence':
                        timeout = trigger_config.get('timeout', 10)
                        result = self.check_absence_trigger(trigger_config, timeout)
                        if not result:
                            return False, f"Absence trigger not met: {trigger_config.get('image_path')}"
                        return True, None
                    
                    return True, None
                except Exception as e:
                    return False, f"Error checking {trigger_type} trigger: {e}"
            
            # Execute trigger checks in parallel
            with ThreadPoolExecutor(max_workers=min(len(trigger_tasks), 4)) as executor:
                future_to_trigger = {
                    executor.submit(check_trigger, trigger_type, trigger_config): (trigger_type, trigger_config)
                    for trigger_type, trigger_config in trigger_tasks
                }
                
                # Check results as they complete
                for future in as_completed(future_to_trigger):
                    trigger_type, trigger_config = future_to_trigger[future]
                    try:
                        success, error_msg = future.result()
                        if not success:
                            logger.info(error_msg)
                            return True  # Skip sequence if any trigger fails
                    except Exception as e:
                        logger.error(f"Exception in {trigger_type} trigger check: {e}")
                        return True  # Skip sequence on exception
        
        # Handle conditional loops that should execute before the main sequence
        # These are executed sequentially as they may depend on each other
        pre_conditional_loops = advanced_conditions.get('pre_conditional_loops', [])
        for loop_config in pre_conditional_loops:
            if stop_flag and stop_flag():
                return True
                
            logger.info("Executing pre-sequence conditional loop")
            from ..sequence_player import SequencePlayer
            sequence_file = loop_config.get('sequence_file', '')
            if sequence_file:
                resolved_seq_file = self._resolve_path(sequence_file, add_json=True)
                temp_sequence_player = SequencePlayer(resolved_seq_file)
                try:
                    temp_sequence_player.selection_memory = getattr(self, 'selection_memory', {})
                except Exception:
                    pass
                self.execute_conditional_loop(loop_config, temp_sequence_player)
        
        return False

    def handle_post_sequence_conditions(self, item, stop_flag, advanced_conditions):
        """
        Handle conditional actions after executing a sequence with parallel processing
        
        Args:
            item (dict): Sequence configuration item
            stop_flag (callable): Stop flag function
            advanced_conditions (dict): Advanced conditions configuration
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        # Handle conditional loops that should execute after the main sequence
        # These are executed sequentially as they may depend on each other
        post_conditional_loops = advanced_conditions.get('post_conditional_loops', [])
        for loop_config in post_conditional_loops:
            if stop_flag and stop_flag():
                return
                
            logger.info("Executing post-sequence conditional loop")
            from ..sequence_player import SequencePlayer
            sequence_file = loop_config.get('sequence_file', '')
            if sequence_file:
                resolved_seq_file = self._resolve_path(sequence_file, add_json=True)
                temp_sequence_player = SequencePlayer(resolved_seq_file)
                self.execute_conditional_loop(loop_config, temp_sequence_player)
        
        # Handle wait conditions (wait for presence/absence of elements or OCR text) in parallel
        wait_conditions = advanced_conditions.get('wait_conditions', [])
        if wait_conditions:
            def check_wait_condition(condition):
                """Helper function to check a single wait condition"""
                try:
                    if stop_flag and stop_flag():
                        return False, "Stop flag detected"
                    
                    condition_type = condition.get('type', 'presence')
                    timeout = float(condition.get('timeout', 30))
                    
                    logger.info(f"Waiting for {condition_type} condition: {condition.get('image_path') or condition.get('target_text')}")
                    
                    if condition_type == 'presence':
                        result = self.check_presence_trigger(condition)
                        return result, f"Presence condition {'met' if result else 'not met'}"
                    elif condition_type == 'absence':
                        result = self.check_absence_trigger(condition, timeout)
                        return result, f"Absence condition {'met' if result else 'not met'}"
                    elif condition_type == 'ocr':
                        result = self.check_ocr_trigger(condition, stop_flag=stop_flag)
                        return result, f"OCR condition {'met' if result else 'not met'}"
                    else:
                        return False, f"Unknown condition type: {condition_type}"
                except Exception as e:
                    return False, f"Error checking wait condition: {e}"