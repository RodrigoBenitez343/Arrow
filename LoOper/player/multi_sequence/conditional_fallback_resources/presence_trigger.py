# presence_trigger.py
"""
Presence trigger module for conditional fallback handler.
Handles presence-based conditional triggers using template matching.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from ...config import logger


class PresenceTrigger:
    """Handles presence-based conditional triggers"""
    
    def __init__(self, template_matcher, validation_cache, path_resolver, stop_flag=None):
        """
        Initialize presence trigger handler.
        
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
    
    def check_presence_trigger(self, trigger_config, screen_cv=None):
        """
        Check if a visual element is present on screen - OPTIMIZED: Single check only with caching
        
        Args:
            trigger_config (dict): Configuration with 'image_path', 'confidence', etc.
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
        Returns:
            bool: True if element is found
        """
        try:
            # Check for cached result first
            cached_result = self.validation_cache.get_cached_validation_result(
                trigger_config, screen_cv, self.template_matcher, self.path_resolver
            )
            if cached_result is not None:
                logger.debug(f"Using cached presence trigger result: {cached_result}")
                return cached_result
            
            image_path = trigger_config.get('image_path')
            # Resolve image path relative to the chain file directory for nested imports
            image_path = self.path_resolver.resolve_path(image_path, add_json=False)
            confidence = trigger_config.get('confidence', 0.8)
            scales = [1.0, 0.9, 1.1]  # Default scales for template matching
            
            if not image_path or not os.path.exists(image_path):
                logger.warning(f"Presence trigger image not found: {image_path}")
                # Cache the negative result for missing images
                self.validation_cache.cache_validation_result(
                    trigger_config, False, screen_cv, self.template_matcher, self.path_resolver
                )
                return False
            
            logger.info(f"Checking presence of image: {image_path} (confidence: {confidence})")
            
            # Check stop flag before starting presence trigger
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected before presence trigger check - aborting")
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
            else:
                logger.info(f"Presence trigger not satisfied - element not found: {image_path}")
                return False
            
        except Exception as e:
            logger.error(f"Error checking presence trigger: {e}")
            # Don't cache error results
            return False
    
    def _perform_single_presence_check(self, image_path, confidence, scales, trigger_config, screen_cv):
        """
        Perform a single presence check with template matching.
        
        Args:
            image_path (str): Path to the template image
            confidence (float): Matching confidence threshold
            scales (list): List of scales to try for template matching
            trigger_config (dict): Original trigger configuration for caching
            screen_cv (numpy.ndarray): Screen image to search in
            
        Returns:
            bool: True if template is found
        """
        try:
            # Check stop flag before each check
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during presence check - aborting")
                return False
            
            # Use multiscale template matching
            result = self.template_matcher.multiscale_template_match(
                image_path, 
                confidence=confidence, 
                scales=scales,
                screen_cv=screen_cv
            )
            
            found = result is not None
            
            # Cache the result
            self.validation_cache.cache_validation_result(
                trigger_config, found, screen_cv, self.template_matcher, self.path_resolver
            )
            
            if found:
                logger.debug(f"Template found at: {result}")
            else:
                logger.debug(f"Template not found with confidence {confidence}")
                
            return found
            
        except Exception as e:
            logger.error(f"Error in single presence check: {e}")
            return False
    
    def check_presence_with_timeout(self, trigger_config, timeout=10, check_interval=None):
        """
        Check for presence with timeout and periodic checking.
        
        Args:
            trigger_config (dict): Trigger configuration
            timeout (float): Maximum time to wait
            check_interval (float): Time between checks (optional, defaults to 0.5s)
            
        Returns:
            bool: True if element is found within timeout
        """
        # Use check_interval from trigger_config if provided, otherwise default to 0.5s
        if check_interval is None:
            check_interval = float(trigger_config.get('check_interval', 0.5))
        
        start_time = time.time()
        attempt_count = 0
        
        logger.info(f"Starting presence check with timeout: {timeout}s, check_interval: {check_interval}s")
        
        while time.time() - start_time < timeout:
            attempt_count += 1
            
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during presence timeout check - aborting")
                return False
                
            if self.check_presence_trigger(trigger_config):
                logger.info(f"Presence found after {attempt_count} attempts ({time.time() - start_time:.2f}s)")
                return True
                
            logger.debug(f"Attempt {attempt_count} failed, waiting {check_interval}s before retry")
            time.sleep(check_interval)
        
        logger.info(f"Presence trigger timeout after {timeout} seconds ({attempt_count} attempts)")
        return False
    
    def check_multiple_presence_triggers(self, trigger_configs, operator='OR'):
        """
        Check multiple presence triggers with AND/OR logic.
        
        Args:
            trigger_configs (list): List of trigger configurations
            operator (str): 'AND' or 'OR' logic
            
        Returns:
            bool: Result based on operator logic
        """
        if not trigger_configs:
            return False
            
        # Capture screen once for all checks
        screen_cv = self.template_matcher.capture_screen()
        
        results = []
        for config in trigger_configs:
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during multiple presence checks - aborting")
                return False
                
            result = self.check_presence_trigger(config, screen_cv)
            results.append(result)
            
            # Early exit for OR operation if any is true
            if operator == 'OR' and result:
                return True
            # Early exit for AND operation if any is false
            elif operator == 'AND' and not result:
                return False
        
        # Final result based on operator
        if operator == 'OR':
            return any(results)
        else:  # AND
            return all(results)
