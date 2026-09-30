import logging
logger = logging.getLogger(__name__)
# presence_trigger.py
"""
Presence trigger module for conditional fallback handler.
Handles presence-based conditional triggers using template matching.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


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
            trigger_config (dict): Configuration with 'image_path', 'image_data', 'confidence', etc.
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
        Returns:
            bool: True if element is found
        """
        try:
            # FORCE bypass cache reading to guarantee a real screen check
            cached_result = None
            
            image_path = trigger_config.get('image_path')
            image_data = trigger_config.get('image_data')
            
            # Priority 1: Embedded base64 image data
            if image_data:
                image_path = image_data  # Pass base64 string to template matcher
                logger.debug(f"Using embedded image data for presence trigger")
            else:
                # Priority 2: File path - resolve relative to chain file directory
                image_path = self.path_resolver.resolve_path(image_path, add_json=False)
                if not image_path or not os.path.exists(image_path):
                    logger.warning(f"Presence trigger image not found: {image_path}")
                    # Cache the negative result for missing images
                    self.validation_cache.cache_validation_result(
                        trigger_config, False, screen_cv, self.template_matcher, self.path_resolver
                    )
                    return False
            
            confidence = trigger_config.get('confidence', 0.8)
            scales = trigger_config.get('scales') or [0.7, 0.85, 1.0, 1.15, 1.3]
            
            logger.info(f"Checking presence of image (confidence: {confidence})")
            
            # Check stop flag before starting presence trigger
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected before presence trigger check - aborting")
                return False
                
            # Capture shared screenshot if not provided
            current_screen_cv = screen_cv
            if current_screen_cv is None:
                current_screen_cv = self.template_matcher.capture_screen()
                
            # We must ignore the cache here too, otherwise the inner loop might just reuse a stale evaluation
            # from a previous run or manual test.
            
            # Perform single presence check using the existing method
            found = self._perform_single_presence_check(image_path, confidence, scales, trigger_config, current_screen_cv)
            if found:
                logger.info(f"Presence trigger satisfied - element found: {image_path}")
                return True
            else:
                logger.info(f"Presence trigger not satisfied on grayscale match - retrying with color")
                # Retry with color matching as a fallback
                try:
                    result_color = self.template_matcher.multiscale_template_match(
                        image_path,
                        confidence=confidence,
                        scales=scales,
                        screen_cv=current_screen_cv,
                        grayscale=False,
                    )
                    if result_color is not None:
                        logger.info(f"Presence trigger satisfied with color matching: {image_path}")
                        # Cache and return True
                        self.validation_cache.cache_validation_result(
                            trigger_config, True, current_screen_cv, self.template_matcher, self.path_resolver
                        )
                        return True
                except Exception:
                    pass
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
            
            # Cache the result (temporarily disabled logic inside cache, but we keep the call)
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
        # If timeout is zero or negative, do a single immediate check (no waiting).
        # This is used by graph loop re-evaluation (_maybe_loop_graph_conditional)
        # where we want a fast one-shot answer, not a wait loop.
        if timeout <= 0:
            return self.check_presence_trigger(trigger_config)
        
        # Use check_interval from trigger_config if provided, otherwise default to 0.5s
        if check_interval is None:
            check_interval = float(trigger_config.get('check_interval', 0.5))
        
        start_time = time.time()
        attempt_count = 0
        
        logger.info(f"Starting presence check with timeout: {timeout}s, check_interval: {check_interval}s")
        
        try:
            from ...computer_vision import get_sandbox_agent_url
            if get_sandbox_agent_url():
                init_delay = trigger_config.get('initial_delay')
                if init_delay is None:
                    init_delay = trigger_config.get('sandbox_initial_delay', trigger_config.get('rdp_initial_delay'))
                if init_delay is None:
                    try:
                        init_delay = float(trigger_config.get('timeout', 0)) * 0.2
                    except Exception:
                        init_delay = 0.6
                    init_delay = max(0.4, min(1.0, float(init_delay)))
                time.sleep(float(init_delay))
        except Exception:
            pass
        
        while time.time() - start_time < timeout:
            attempt_count += 1
            
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during presence timeout check - aborting")
                return False
                
            # FORCE clear any lingering state/cache inside the validation cache if it's somehow persisting
            if hasattr(self, 'validation_cache') and hasattr(self.validation_cache, 'clear_cache'):
                self.validation_cache.clear_cache()
                
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
