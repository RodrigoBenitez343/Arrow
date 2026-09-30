import logging
logger = logging.getLogger(__name__)
# validation_cache.py
"""
Validation cache module for conditional fallback handler.
Handles caching of validation results with thread safety and screen change detection.
"""
import threading
import hashlib
import time


class ValidationCache:
    """Handles caching of validation results for performance optimization"""
    
    def __init__(self, cache_ttl_seconds=2.0):
        self._validation_cache = {}
        self._last_screen_hash = None
        self._cache_lock = threading.Lock()
        self._cache_ttl = cache_ttl_seconds  # Cache TTL in seconds
        
    def get_screen_hash(self, screen_cv=None, template_matcher=None):
        """
        Generate a hash of the current screen to detect changes.
        
        Args:
            screen_cv (numpy.ndarray): Pre-captured screen image
            template_matcher: TemplateMatching instance for screen capture
            
        Returns:
            str: Hash of the screen content
        """
        try:
            if screen_cv is None and template_matcher:
                screen_cv = template_matcher.capture_screen()
            elif screen_cv is None:
                return None
            
            # Create a hash from a higher resolution sample for better change detection
            import cv2
            import numpy as np
            
            # Use 200x200 for more sensitive change detection
            small_screen = cv2.resize(screen_cv, (200, 200))
            # Convert to grayscale for consistent hashing
            if len(small_screen.shape) == 3:
                small_screen = cv2.cvtColor(small_screen, cv2.COLOR_BGR2GRAY)
            
            # Create hash from the image data
            screen_bytes = small_screen.tobytes()
            return hashlib.md5(screen_bytes).hexdigest()
            
        except Exception as e:
            logger.debug(f"Error generating screen hash: {e}")
            return None
    
    def get_validation_cache_key(self, trigger_config, screen_hash=None, path_resolver=None):
        """
        Generate a cache key for a validation trigger configuration.
        Now includes screen hash to ensure cache validity per screen state.
        
        Args:
            trigger_config (dict): The trigger configuration
            screen_hash (str): Hash of the current screen state
            path_resolver: PathResolver instance for resolving paths
            
        Returns:
            str: Cache key for this validation
        """
        try:
            # Create a key based on the validation parameters
            image_path = trigger_config.get('image_path', '')
            confidence = trigger_config.get('confidence', 0.8)
            trigger_type = trigger_config.get('trigger_type', 'presence')
            ocr_text = trigger_config.get('ocr_text', '')
            
            # Resolve the image path to get absolute path for consistent caching
            if path_resolver and image_path:
                resolved_path = path_resolver.resolve_path(image_path, add_json=False)
            else:
                resolved_path = image_path
            
            # Include screen hash in cache key to ensure screen-specific caching
            screen_part = screen_hash or 'no_screen'
            
            # Create a deterministic key that includes screen context
            key_data = f"{resolved_path}|{confidence}|{trigger_type}|{ocr_text}|{screen_part}"
            return hashlib.md5(key_data.encode()).hexdigest()
            
        except Exception as e:
            logger.debug(f"Error generating cache key: {e}")
            return None
    

    def get_cached_validation_result(self, trigger_config, screen_cv=None, template_matcher=None, path_resolver=None):
        """
        Get a cached validation result if available and not expired.
        
        Args:
            trigger_config (dict): The trigger configuration
            screen_cv (numpy.ndarray): Pre-captured screen image
            template_matcher: TemplateMatching instance for screen capture
            path_resolver: PathResolver instance for resolving paths
            
        Returns:
            bool or None: Cached result if available and valid, None if not cached or expired
        """
        try:
            # Check for cached result first
            # TEMPORARY FIX: Ignore cache entirely to force a real screen check
            # This ensures we don't return True just because the image exists in storage
            # We will refactor this cache logic later
            return None
            
            # Get current screen hash - if we can't get it, skip caching but don't block validation
            current_screen_hash = self.get_screen_hash(screen_cv, template_matcher)
            if not current_screen_hash:
                logger.debug("Cannot generate screen hash - skipping cache lookup")
                return None
                
            cache_key = self.get_validation_cache_key(trigger_config, current_screen_hash, path_resolver)
            if not cache_key:
                logger.debug("Cannot generate cache key - skipping cache lookup")
                return None
                
            with self._cache_lock:
                if cache_key in self._validation_cache:
                    cached_data = self._validation_cache[cache_key]
                    result = cached_data['result']
                    timestamp = cached_data['timestamp']
                    
                    # Check if cache entry has expired
                    if time.time() - timestamp > self._cache_ttl:
                        logger.debug(f"Cache entry expired for key: {cache_key[:8]}...")
                        del self._validation_cache[cache_key]
                        return None
                    
                    logger.debug(f"Using cached validation result: {result} for key: {cache_key[:8]}...")
                    return result
                    
            return None
            
        except Exception as e:
            logger.debug(f"Error retrieving cached validation result: {e}")
            return None
    
    def cache_validation_result(self, trigger_config, result, screen_cv=None, template_matcher=None, path_resolver=None):
        """
        Cache a validation result for future use with timestamp.
        
        Args:
            trigger_config (dict): The trigger configuration
            result (bool): The validation result
            screen_cv (numpy.ndarray): Pre-captured screen image
            template_matcher: TemplateMatching instance for screen capture
            path_resolver: PathResolver instance for resolving paths
        """
        return
        try:
            # Get current screen hash - if we can't get it, skip caching but don't fail
            current_screen_hash = self.get_screen_hash(screen_cv, template_matcher)
            if not current_screen_hash:
                logger.debug("Cannot generate screen hash - skipping cache storage")
                return
                
            cache_key = self.get_validation_cache_key(trigger_config, current_screen_hash, path_resolver)
            if not cache_key:
                logger.debug("Cannot generate cache key - skipping cache storage")
                return
                
            with self._cache_lock:
                # Store result with timestamp for TTL checking
                self._validation_cache[cache_key] = {
                    'result': result,
                    'timestamp': time.time()
                }
                logger.debug(f"Cached validation result: {result} for key: {cache_key[:8]}...")
                
                # Limit cache size to prevent memory issues
                if len(self._validation_cache) > 100:
                    # Remove expired entries first
                    current_time = time.time()
                    expired_keys = [
                        key for key, data in self._validation_cache.items()
                        if current_time - data['timestamp'] > self._cache_ttl
                    ]
                    for expired_key in expired_keys:
                        del self._validation_cache[expired_key]
                    
                    # If still too many entries, remove oldest ones
                    if len(self._validation_cache) > 100:
                        oldest_keys = sorted(
                            self._validation_cache.keys(),
                            key=lambda k: self._validation_cache[k]['timestamp']
                        )[:20]
                        for old_key in oldest_keys:
                            del self._validation_cache[old_key]
                        logger.debug("Cleaned up validation cache (removed 20 oldest entries)")
                    
        except Exception as e:
            logger.debug(f"Error caching validation result: {e}")
    
    def clear_cache(self):
        """Clear the entire validation cache"""
        with self._cache_lock:
            self._validation_cache.clear()
            self._last_screen_hash = None
            logger.debug("Validation cache cleared")
