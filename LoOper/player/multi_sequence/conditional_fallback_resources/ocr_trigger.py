import logging
logger = logging.getLogger(__name__)
# ocr_trigger.py
"""
OCR trigger module for conditional fallback handler.
Handles OCR-based conditional triggers with text detection and validation.
"""
import time
from ...config import OCR_AVAILABLE
from ...ocr_engine import extract_text


class OCRTrigger:
    """Handles OCR-based conditional triggers"""
    
    def __init__(self, template_matcher, validation_cache, path_resolver, stop_flag=None):
        """
        Initialize OCR trigger handler.
        
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

    @staticmethod
    def _coerce_region(value):
        """Stored search box -> (x, y, w, h) ints, or None for whole screen.

        The GUI persists it as a JSON string on the node property; a chain
        config may carry it as a list - accept both.  Coordinates are in the
        same space ``capture_screen`` returns (device/physical pixels), which
        is what the element picker captures.
        """
        if isinstance(value, str):
            try:
                import json
                value = json.loads(value)
            except Exception:
                return None
        if isinstance(value, (list, tuple)) and len(value) == 4:
            try:
                return tuple(int(v) for v in value)
            except (TypeError, ValueError):
                return None
        return None
    
    def check_ocr_trigger(self, trigger_config, stop_flag=None):
        """
        Check if specific text is present on screen using OCR.
        
        Args:
            trigger_config (dict): Configuration with 'target_text', 'case_sensitive', etc.
            stop_flag (callable): Function to check if execution should stop
        
        Returns:
            bool: True if text is found
        """
        if stop_flag:
            self.stop_flag = stop_flag
            
        if not OCR_AVAILABLE:
            logger.warning("OCR not available")
            return False
        
        try:
            target_text = trigger_config.get('target_text', '') or trigger_config.get('ocr_text', '')
            case_sensitive = trigger_config.get('case_sensitive', True)
            language = trigger_config.get('language', 'eng')
            region = self._coerce_region(trigger_config.get('region'))
            timeout = float(trigger_config.get('timeout', 10))
            
            if not target_text:
                logger.warning("No target text specified for OCR trigger")
                return False
            
            logger.info(f"Checking OCR for text: '{target_text}' (case_sensitive: {case_sensitive})")
            
            # Check for cached result first
            # FORCE bypass cache reading to guarantee a real screen check
            cached_result = None
            if cached_result is not None:
                logger.debug(f"Using cached OCR trigger result: {cached_result}")
                return cached_result
            
            start_time = time.time()
            check_interval = 1.0  # Check every second for OCR
            
            while time.time() - start_time < timeout:
                # Check stop flag
                if self.stop_flag and self.stop_flag():
                    logger.info("Stop flag detected during OCR trigger check - aborting")
                    return False
                
                # Capture screen
                screen_cv = self.template_matcher.capture_screen()
                
                # Perform OCR
                found_text = self._perform_ocr_detection(screen_cv, language, region)
                
                if found_text:
                    # Check if target text is in found text
                    if case_sensitive:
                        text_found = target_text in found_text
                    else:
                        text_found = target_text.lower() in found_text.lower()
                    
                    if text_found:
                        logger.info(f"OCR trigger satisfied - text found: '{target_text}'")
                        # Cache the positive result
                        self.validation_cache.cache_validation_result(
                            trigger_config, True, screen_cv, self.template_matcher, self.path_resolver
                        )
                        return True
                
                # Wait before next check
                time.sleep(check_interval)
            
            # Timeout reached without finding text
            logger.info(f"OCR trigger timeout - text not found: '{target_text}'")
            # Cache the negative result
            self.validation_cache.cache_validation_result(
                trigger_config, False, None, self.template_matcher, self.path_resolver
            )
            return False
            
        except Exception as e:
            logger.error(f"Error checking OCR trigger: {e}")
            return False
    
    def _perform_ocr_detection(self, screen_cv, language='eng', region=None):
        """
        Perform OCR text detection on screen image.
        
        Args:
            screen_cv (numpy.ndarray): Screen image
            language (str): OCR language code
            region (tuple): Optional region (x, y, width, height) to limit OCR
            
        Returns:
            str: Detected text or empty string
        """
        try:
            if not OCR_AVAILABLE:
                return ""

            import cv2
            
            # Crop to region if specified
            if region and len(region) == 4:
                x, y, w, h = region
                screen_cv = screen_cv[y:y+h, x:x+w]
            
            # Convert to grayscale for better OCR
            if len(screen_cv.shape) == 3:
                gray = cv2.cvtColor(screen_cv, cv2.COLOR_BGR2GRAY)
            else:
                gray = screen_cv
            
            # Apply some preprocessing for better OCR
            # Increase contrast
            gray = cv2.convertScaleAbs(gray, alpha=1.2, beta=10)
            
            text = extract_text(gray, language=language)
            
            # Clean up the text
            text = text.strip()
            
            if text:
                logger.debug(f"OCR detected text: '{text[:100]}...' (truncated)")
            else:
                logger.debug("No text detected by OCR")
            
            return text
            
        except Exception as e:
            logger.error(f"Error performing OCR detection: {e}")
            return ""
    
    def check_ocr_immediate(self, trigger_config, screen_cv=None):
        """
        Check if specific text is immediately present on screen (single check).
        
        Args:
            trigger_config (dict): Configuration with 'target_text', 'case_sensitive', etc.
        
        Returns:
            bool: True if text is found
        """
        if not OCR_AVAILABLE:
            logger.warning("OCR not available")
            return False
        
        try:
            target_text = trigger_config.get('target_text', '') or trigger_config.get('ocr_text', '')
            case_sensitive = trigger_config.get('case_sensitive', True)
            language = trigger_config.get('language', 'eng')
            region = self._coerce_region(trigger_config.get('region'))
            
            if not target_text:
                logger.warning("No target text specified for immediate OCR trigger")
                return False
            
            logger.info(f"Checking immediate OCR for text: '{target_text}'")
            
            # Check stop flag
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during immediate OCR check - aborting")
                return False
            
            # Check for cached result first
            # FORCE bypass cache reading to guarantee a real screen check
            cached_result = None
            if cached_result is not None:
                logger.debug(f"Using cached immediate OCR result: {cached_result}")
                return cached_result
            
            if screen_cv is None:
                screen_cv = self.template_matcher.capture_screen()
            found_text = self._perform_ocr_detection(screen_cv, language, region)
            
            if found_text:
                # Check if target text is in found text
                if case_sensitive:
                    text_found = target_text in found_text
                else:
                    text_found = target_text.lower() in found_text.lower()
                
                # Cache the result
                self.validation_cache.cache_validation_result(
                    trigger_config, text_found, screen_cv, self.template_matcher, self.path_resolver
                )
                
                if text_found:
                    logger.info(f"Immediate OCR satisfied - text found: '{target_text}'")
                else:
                    logger.info(f"Immediate OCR failed - text not found: '{target_text}'")
                
                return text_found
            else:
                # Cache the negative result
                self.validation_cache.cache_validation_result(
                    trigger_config, False, screen_cv, self.template_matcher, self.path_resolver
                )
                logger.info(f"Immediate OCR failed - no text detected")
                return False
            
        except Exception as e:
            logger.error(f"Error checking immediate OCR trigger: {e}")
            return False
    
    def extract_text_from_region(self, region, language='eng'):
        """
        Extract text from a specific screen region using OCR.
        
        Args:
            region (tuple): Region (x, y, width, height) to extract text from
            language (str): OCR language code
            
        Returns:
            str: Extracted text or empty string
        """
        if not OCR_AVAILABLE:
            logger.warning("OCR not available")
            return ""
        
        try:
            # Check stop flag
            if self.stop_flag and self.stop_flag():
                logger.info("Stop flag detected during text extraction - aborting")
                return ""
            
            # Capture screen
            screen_cv = self.template_matcher.capture_screen()
            
            # Perform OCR on the region
            text = self._perform_ocr_detection(screen_cv, language, region)
            
            logger.info(f"Extracted text from region {region}: '{text[:50]}...' (truncated)")
            return text
            
        except Exception as e:
            logger.error(f"Error extracting text from region: {e}")
            return ""
