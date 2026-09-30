import logging
logger = logging.getLogger(__name__)
# absence_trigger.py
"""
Absence trigger module for conditional fallback handler.
Handles absence-based conditional triggers with timeout functionality.
"""
import os
import time


class AbsenceTrigger:
    """Handles absence-based conditional triggers"""
    
    def __init__(self, template_matcher, validation_cache, path_resolver, stop_flag=None):
        """
        Initialize absence trigger handler.
        
        Args:
            template_matcher: TemplateMatching instance
            validation_cache: ValidationCache instance
            path_resolver: PathResolver instance
            stop_flag: Function to check if execution should stop
        """
        self.template_matcher = template_matcher
        self.validation_cache = validation_cache
        self.path_resolver = path_resolver
        self.stop_flag = stop_flag
    
    def check_absence_trigger(self, trigger_config, timeout=10, check_interval=None):
        """
        Check if a visual element is absent from screen within timeout.
        
        Args:
            trigger_config (dict): Configuration with 'image_path', 'image_data', 'confidence', etc.
            timeout (float): Maximum time to wait for absence
            check_interval (float): Time between checks (optional, defaults to 0.5s)
        
        Returns:
            bool: True if element is absent within timeout
        """
        # Use check_interval from trigger_config if provided, otherwise default to 0.5s
        if check_interval is None:
            check_interval = float(trigger_config.get('check_interval', 0.5))
        
        try:
            image_path = trigger_config.get('image_path')
            image_data = trigger_config.get('image_data')
            
            # Priority 1: Embedded base64 image data
            if image_data:
                image_path = image_data  # Pass base64 string to template matcher
                logger.debug(f"Using embedded image data for absence trigger")
            else:
                # Priority 2: File path - resolve relative to chain file directory
                image_path = self.path_resolver.resolve_path(image_path, add_json=False)
                if not image_path or not os.path.exists(image_path):
                    logger.warning(f"Absence trigger image not found: {image_path}")
                    return True  # If image doesn't exist, consider it "absent"
            
            confidence = trigger_config.get('confidence', 0.8)
            logger.info(f"Checking absence of image (timeout: {timeout}s, check_interval: {check_interval}s)")
            
            start_time = time.time()
            attempt_count = 0
            
            while time.time() - start_time < timeout:
                attempt_count += 1
                
                # Check stop flag
                if self.stop_flag and self.stop_flag():
                    logger.info("Stop flag detected during absence trigger check - aborting")
                    return False
                
                # Check if element is present
                screen_cv = self.template_matcher.capture_screen()
                
                # Create a temporary presence config for checking
                presence_config = trigger_config.copy()
                presence_config['trigger_type'] = 'presence'
                
                # Check for cached result first
                # FORCE bypass cache reading to guarantee a real screen check
                cached_result = None
                
                if cached_result is not None:
                    is_present = cached_result
                else:
                    # Perform presence check
                    result = self.template_matcher.multiscale_template_match(
                        image_path, 
                        confidence=confidence, 
                        scales=[1.0, 0.9, 1.1],
                        screen_cv=screen_cv
                    )
                    is_present = result is not None
                    
                    # Cache the presence result
                    self.validation_cache.cache_validation_result(
                        presence_config, is_present, screen_cv, self.template_matcher, self.path_resolver
                    )
                
                # If element is not present, absence condition is satisfied
                if not is_present:
                    logger.info(f"Absence trigger satisfied - element not found: {image_path} (attempt {attempt_count})")
                    return True
                
                logger.debug(f"Attempt {attempt_count} failed, waiting {check_interval}s before retry")
                # Wait before next check
                time.sleep(check_interval)
            
            # Timeout reached and element is still present
            logger.info(f"Absence trigger timeout - element still present: {image_path} ({attempt_count} attempts)")
            return False
            
        except Exception as e:
            logger.error(f"Error checking absence trigger: {e}")
            return False
    
    def check_absence_immediate(self, trigger_config, screen_cv=None):
        """
        Check if a visual element is immediately absent (single check).
        
        Args:
            trigger_config (dict): Configuration with 'image_path', 'image_data', 'confidence', etc.
        
        Returns:
            bool: True if element is not found
        """
        try:
            image_path = trigger_config.get('image_path')
            image_data = trigger_config.get('image_data')
            
            # Priority 1: Embedded base64 image data
            if image_data:
                image_path = image_data  # Pass base64 string to template matcher
                logger.debug(f"Using embedded image data for absence immediate check")
            else:
                # Priority 2: File path - resolve relative to chain file directory
                image_path = self.path_resolver.resolve_path(image_path, add_json=False)
                if not image_path or not os.path.exists(image_path):
                    logger.warning(f"Absence trigger image not found: {image_path}")
                    return True  # If image doesn't exist, consider it "absent"
            
            confidence = trigger_config.get('confidence', 0.8)
            logger.info(f"Checking immediate absence of image")
            
            # Check stop flag
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during immediate absence check - aborting")
                return False
            
            if screen_cv is None:
                screen_cv = self.template_matcher.capture_screen()
            
            # Create a temporary presence config for caching
            presence_config = trigger_config.copy()
            presence_config['trigger_type'] = 'presence'
            
            # Check for cached result first
            # FORCE bypass cache reading to guarantee a real screen check
            cached_result = None
            
            if cached_result is not None:
                is_present = cached_result
            else:
                # Perform presence check
                result = self.template_matcher.multiscale_template_match(
                    image_path, 
                    confidence=confidence, 
                    scales=[1.0, 0.9, 1.1],
                    screen_cv=screen_cv
                )
                is_present = result is not None
                
                # Cache the presence result
                self.validation_cache.cache_validation_result(
                    presence_config, is_present, screen_cv, self.template_matcher, self.path_resolver
                )
            
            # Return opposite of presence (absence = not present)
            absent = not is_present
            
            if absent:
                logger.info(f"Immediate absence confirmed - element not found: {image_path}")
            else:
                logger.info(f"Immediate absence failed - element found: {image_path}")
            
            return absent
            
        except Exception as e:
            logger.error(f"Error checking immediate absence trigger: {e}")
            return False
    
    def check_multiple_absence_triggers(self, trigger_configs, timeout=10, operator='AND'):
        """
        Check multiple absence triggers with AND/OR logic.
        
        Args:
            trigger_configs (list): List of trigger configurations
            timeout (float): Maximum time to wait for absence
            operator (str): 'AND' or 'OR' logic
            
        Returns:
            bool: Result based on operator logic
        """
        if not trigger_configs:
            return True  # No triggers to check = all absent
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during multiple absence checks - aborting")
                return False
            
            results = []
            for config in trigger_configs:
                result = self.check_absence_immediate(config)
                results.append(result)
                
                # Early exit for OR operation if any is true
                if operator == 'OR' and result:
                    return True
                # Early exit for AND operation if any is false
                elif operator == 'AND' and not result:
                    break  # Continue to next iteration of while loop
            
            # Check final result for this iteration
            if operator == 'OR':
                final_result = any(results)
            else:  # AND
                final_result = all(results)
            
            if final_result:
                return True
            
            # Wait before next check
            time.sleep(0.5)
        
        # Timeout reached
        logger.info(f"Multiple absence triggers timeout after {timeout} seconds")
        return False
