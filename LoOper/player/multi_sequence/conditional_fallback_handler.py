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
            
            # Use multi-scale template matching for better detection
            scales = trigger_config.get('scales', [0.8, 0.9, 1.0, 1.1, 1.2])
            
            # If timeout is 0, perform single check (no waiting)
            if timeout <= 0:
                return self._perform_single_presence_check(image_path, confidence, scales, trigger_config)
            
            # Implement timeout loop for waiting
            start_time = time.time()
            check_interval = 0.5  # Check every 500ms
            
            while time.time() - start_time < timeout:
                # Check stop flag before each validation attempt
                if self.stop_flag and self.stop_flag():
                    logger.info("Presence trigger check stopped by user request")
                    return False
                
                # Perform presence check
                found = self._perform_single_presence_check(image_path, confidence, scales, trigger_config)
                
                if found:
                    elapsed_time = time.time() - start_time
                    logger.info(f"[FOUND] Presence trigger satisfied after {elapsed_time:.2f}s: {image_path}")
                    return True
                
                # Wait before next check (unless we're close to timeout)
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time > check_interval:
                    time.sleep(check_interval)
                elif remaining_time > 0:
                    time.sleep(remaining_time)
                else:
                    break
            
            logger.info(f"[TIMEOUT] Presence trigger timeout after {timeout}s: {image_path}")
            return False
            
        except Exception as e:
            logger.error(f"Error checking presence trigger: {e}")
            return False
    
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
            # For strict validation (until_present loops), require ALL validations to pass
            min_validations_required = total_validations
            logger.debug(f"[STRICT MODE] Requiring ALL {min_validations_required}/{total_validations} validations to pass")
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
                    # Increase confidence threshold for until_present to ensure accuracy
                    original_confidence = condition.get('confidence', 0.8)
                    condition['confidence'] = max(0.9, original_confidence)
                    logger.info(f"until_present: Using enhanced confidence threshold: {condition['confidence']}")
                
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
                        post_condition['confidence'] = max(0.9, post_condition.get('confidence', 0.8))
                    
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