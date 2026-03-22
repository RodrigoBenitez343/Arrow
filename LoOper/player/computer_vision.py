# computer_vision.py
"""
Computer vision and template matching functionality for the LocalOperator automation system.
"""

import os
import time
import pyautogui
from functools import lru_cache
from .config import logger, CV2_AVAILABLE, OCR_AVAILABLE
from .ocr_engine import extract_text_elements

_SANDBOX_AGENT_URL = None

def set_sandbox_agent_url(agent_url):
    global _SANDBOX_AGENT_URL
    try:
        if agent_url and str(agent_url).strip():
            _SANDBOX_AGENT_URL = str(agent_url).strip()
        else:
            _SANDBOX_AGENT_URL = None
    except Exception:
        _SANDBOX_AGENT_URL = None

def get_sandbox_agent_url():
    return _SANDBOX_AGENT_URL

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False

# Import mss for faster screenshot capture
try:
    import mss
    MSS_AVAILABLE = True
    logger.debug("mss library available for fast screenshot capture")
except ImportError:
    MSS_AVAILABLE = False
    logger.debug("mss library not available, falling back to pyautogui")

if CV2_AVAILABLE:
    import cv2
    import numpy as np


class TemplateMatching:
    """
    Handles computer vision operations for template matching and image recognition.
    """
    
    # Template cache to avoid repeated disk I/O
    _template_cache = {}
    _cache_max_size = 100  # Maximum number of templates to cache
    
    @classmethod
    def _load_template(cls, template_path):
        """
        Load template with caching to avoid repeated disk I/O.
        
        Args:
            template_path (str): Path to the template image
            
        Returns:
            numpy.ndarray: Template image or None if failed to load
        """
        if not CV2_AVAILABLE:
            return None
            
        # Convert to absolute path for consistent caching
        abs_path = os.path.abspath(template_path)
        
        # Check cache first
        if abs_path in cls._template_cache:
            logger.debug(f"Template cache hit: {abs_path}")
            return cls._template_cache[abs_path]
        
        # Load from disk
        template = cv2.imread(abs_path)
        if template is None:
            logger.debug(f"Failed to load template: {abs_path}")
            return None
        
        # Add to cache with LRU eviction
        if len(cls._template_cache) >= cls._cache_max_size:
            # Remove oldest entry (simple FIFO for now)
            oldest_key = next(iter(cls._template_cache))
            del cls._template_cache[oldest_key]
            logger.debug(f"Template cache evicted: {oldest_key}")
        
        cls._template_cache[abs_path] = template.copy()
        logger.debug(f"Template cached: {abs_path}")
        return template
    
    @classmethod
    def clear_template_cache(cls):
        """Clear the template cache."""
        cls._template_cache.clear()
        logger.debug("Template cache cleared")
    
    @staticmethod
    def capture_screen():
        """
        Capture screen using the fastest available method.
        
        Returns:
            numpy.ndarray: Screen image in BGR format for OpenCV
        """
        if CV2_AVAILABLE and _SANDBOX_AGENT_URL:
            try:
                import urllib.request
                import json as _json
                import base64 as _base64
                import io as _io
                from PIL import Image

                base_url = str(_SANDBOX_AGENT_URL).rstrip('/')
                req = urllib.request.Request(
                    f"{base_url}/action",
                    data=_json.dumps({"action": "screenshot"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    payload = _json.loads(resp.read().decode("utf-8"))
                if isinstance(payload, dict) and payload.get("status") == "error":
                    raise RuntimeError(payload.get("message") or "Agent screenshot failed")
                img_b64 = payload.get("image") if isinstance(payload, dict) else None
                if img_b64:
                    img = Image.open(_io.BytesIO(_base64.b64decode(img_b64))).convert("RGB")
                    screen_np = np.array(img)
                    return cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
            except Exception:
                pass
        if MSS_AVAILABLE and CV2_AVAILABLE:
            # Use mss for faster capture
            with mss.mss() as sct:
                # Capture the entire screen
                monitor = sct.monitors[0]  # All monitors combined
                screenshot = sct.grab(monitor)
                
                # Convert to numpy array and BGR format
                screen_np = np.array(screenshot)
                # mss returns BGRA, convert to BGR for OpenCV
                screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_BGRA2BGR)
                return screen_cv
        else:
            # Fallback to pyautogui
            screen = pyautogui.screenshot()
            screen_np = np.array(screen)
            # pyautogui returns RGB, convert to BGR for OpenCV
            screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
            return screen_cv
    
    # Adaptive multi-scale tracking
    _template_attempt_counts = {}  # Track attempts per template for adaptive scaling
    _adaptive_reset_threshold = 10  # Reset attempt count after this many tries
    
    @staticmethod
    def multiscale_template_match(template_path, confidence, scales=None, grayscale=True, screen_cv=None):
        """
        Performs multi-scale template matching with adaptive strategy for repeated checks.
        
        Args:
            template_path (str): Path to the template image
            confidence (float): Matching confidence threshold (0.0 to 1.0)
            scales (list): List of scale factors to try (default: [1.0, 0.9, 1.1])
            grayscale (bool): Whether to use grayscale matching
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
        Returns:
            tuple: (x, y) coordinates if found, None otherwise
        """
        if not CV2_AVAILABLE:
            logger.debug("OpenCV not available, falling back to pyautogui")
            return None
        
        # Load template using cache
        template = TemplateMatching._load_template(template_path)
        if template is None:
            logger.debug(f"Failed to load template: {template_path}")
            return None
        
        # Capture screen if not provided
        if screen_cv is None:
            screen_cv = TemplateMatching.capture_screen()
        
        # Convert to OpenCV format (BGR)
        if hasattr(screen_cv, 'convert'):
            # PIL Image
            screen_cv = cv2.cvtColor(np.array(screen_cv), cv2.COLOR_RGB2BGR)
        elif len(screen_cv.shape) == 3 and screen_cv.shape[2] == 4:
            # RGBA to BGR
            screen_cv = cv2.cvtColor(screen_cv, cv2.COLOR_RGBA2BGR)
        
        # Adaptive multi-scale strategy
        abs_path = os.path.abspath(template_path)
        attempt_count = TemplateMatching._template_attempt_counts.get(abs_path, 0)
        
        # Default scales with adaptive adjustment
        if scales is None:
            scales = [1.0, 0.9, 1.1]
        
        # Adaptive scale selection based on attempt history
        if attempt_count > 0:
            if attempt_count <= 3:
                # First few attempts: try 1.0 scale only for speed
                adaptive_scales = [1.0]
                logger.debug(f"[ADAPTIVE] Using 1.0-only strategy for attempt {attempt_count}: {template_path}")
            elif attempt_count <= 6:
                # Medium attempts: use reduced scale set
                adaptive_scales = [1.0, 0.9, 1.1]
                logger.debug(f"[ADAPTIVE] Using reduced scales for attempt {attempt_count}: {template_path}")
            else:
                # Many attempts: use full scale set
                adaptive_scales = scales
                logger.debug(f"[ADAPTIVE] Using full scales for attempt {attempt_count}: {template_path}")
        else:
            # First attempt: use provided scales
            adaptive_scales = scales
        
        # Update attempt count
        TemplateMatching._template_attempt_counts[abs_path] = attempt_count + 1
        
        # Reset counter if it gets too high
        if TemplateMatching._template_attempt_counts[abs_path] > TemplateMatching._adaptive_reset_threshold:
            TemplateMatching._template_attempt_counts[abs_path] = 0
            logger.debug(f"[ADAPTIVE] Reset attempt counter for: {template_path}")
        
        # Sort scales to prioritize 1.0 (original size) for speed
        sorted_scales = sorted(adaptive_scales, key=lambda x: abs(x - 1.0))
        
        best_match = None
        best_confidence = 0
        early_exit_threshold = min(0.95, confidence + 0.1)  # Early exit if we get very high confidence
        
        for scale in sorted_scales:
            try:
                # Scale template
                if scale != 1.0:
                    h, w = template.shape[:2]
                    new_h, new_w = int(h * scale), int(w * scale)
                    if new_h < 10 or new_w < 10:  # Skip very small templates
                        continue
                    scaled_template = cv2.resize(template, (new_w, new_h))
                else:
                    scaled_template = template
                
                # Convert to grayscale if needed
                template_work = scaled_template.copy()
                screen_work = screen_cv.copy()
                
                if grayscale:
                    if len(template_work.shape) == 3:
                        template_work = cv2.cvtColor(template_work, cv2.COLOR_BGR2GRAY)
                    if len(screen_work.shape) == 3:
                        screen_work = cv2.cvtColor(screen_work, cv2.COLOR_BGR2GRAY)
                
                # Perform template matching
                result = cv2.matchTemplate(screen_work, template_work, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                
                logger.debug(f"Scale {scale}: confidence {max_val:.3f} at ({max_loc[0]}, {max_loc[1]})")
                
                # Check if this is our best match so far
                if max_val > best_confidence:
                    best_confidence = max_val
                    if max_val >= confidence:
                        # Calculate center coordinates
                        h, w = template_work.shape[:2]
                        center_x = max_loc[0] + w // 2
                        center_y = max_loc[1] + h // 2
                        best_match = (center_x, center_y)
                        
                        # Early exit for very high confidence matches
                        if max_val >= early_exit_threshold:
                            logger.debug(f"[EARLY EXIT] High confidence {max_val:.3f} >= {early_exit_threshold:.3f}")
                            break
                
            except Exception as e:
                logger.debug(f"Scale {scale} failed: {e}")
                continue
        
        if best_match and best_confidence >= confidence:
            logger.debug(f"Multi-scale match found: confidence {best_confidence:.3f}, coords {best_match}")
            return best_match
        else:
            logger.debug(f"Multi-scale match failed: best confidence {best_confidence:.3f} < threshold {confidence}")
            return None

    @staticmethod
    def region_based_search(template_path, hint_x, hint_y, confidence, expand_factor, grayscale=True, screen_cv=None):
        """
        Performs region-based search around the original click coordinates.
        
        Args:
            template_path (str): Path to the template image
            hint_x (int): Original x coordinate as search hint
            hint_y (int): Original y coordinate as search hint
            confidence (float): Matching confidence threshold
            expand_factor (float): Factor to expand search region
            grayscale (bool): Whether to use grayscale matching
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
        Returns:
            tuple: (x, y) coordinates if found, None otherwise
        """
        try:
            # Load template using cache to get its dimensions
            template = TemplateMatching._load_template(template_path) if CV2_AVAILABLE else None
            if template is None:
                logger.debug(f"Failed to load template or OpenCV unavailable: {template_path}")
                # Fallback to standard pyautogui region search
                screen_w, screen_h = pyautogui.size()
                region_size = int(200 * expand_factor)  # Scale default region size
                left = max(0, hint_x - region_size // 2)
                top = max(0, hint_y - region_size // 2)
                right = min(screen_w, hint_x + region_size // 2)
                bottom = min(screen_h, hint_y + region_size // 2)
                
                try:
                    location = pyautogui.locateCenterOnScreen(
                        template_path,
                        region=(left, top, right - left, bottom - top),
                        confidence=confidence,
                        grayscale=grayscale
                    )
                    if location:
                        logger.debug(f"Fallback region search found match at {location}")
                    return location
                except Exception as e:
                    logger.debug(f"Fallback region search failed: {e}")
                    return None
                
            template_h, template_w = template.shape[:2]
            
            # Calculate expanded search region with minimum size constraints
            min_search_size = max(template_w, template_h) * 2  # Ensure reasonable minimum
            search_w = max(int(template_w * expand_factor), min_search_size)
            search_h = max(int(template_h * expand_factor), min_search_size)
            
            # Define search region bounds
            screen_w, screen_h = pyautogui.size()
            left = max(0, hint_x - search_w // 2)
            top = max(0, hint_y - search_h // 2)
            right = min(screen_w, hint_x + search_w // 2)
            bottom = min(screen_h, hint_y + search_h // 2)
            
            region_w = right - left
            region_h = bottom - top
            
            logger.debug(f"Region search: hint ({hint_x}, {hint_y}), region ({left}, {top}, {region_w}, {region_h}), expand_factor {expand_factor}")
            
            if region_w < template_w or region_h < template_h:
                logger.debug(f"Region too small: {region_w}x{region_h} < template {template_w}x{template_h}")
                return None
            
            # Use provided screen or capture region
            if screen_cv is not None and CV2_AVAILABLE:
                # Extract region from full screen
                region_cv = screen_cv[top:bottom, left:right]
                
                # Convert template and region to grayscale if needed
                template_work = template.copy()
                region_work = region_cv.copy()
                
                if grayscale:
                    if len(template_work.shape) == 3:
                        template_work = cv2.cvtColor(template_work, cv2.COLOR_BGR2GRAY)
                    if len(region_work.shape) == 3:
                        region_work = cv2.cvtColor(region_work, cv2.COLOR_BGR2GRAY)
                
                # Perform template matching on region
                result = cv2.matchTemplate(region_work, template_work, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                
                logger.debug(f"Region CV matching: confidence {max_val:.3f} at local ({max_loc[0]}, {max_loc[1]})")
                
                if max_val >= confidence:
                    # Convert local coordinates to global coordinates
                    global_x = left + max_loc[0] + template_w // 2
                    global_y = top + max_loc[1] + template_h // 2
                    logger.info(f"Region CV search found match: confidence {max_val:.3f}, coords ({global_x}, {global_y})")
                    return (global_x, global_y)
                else:
                    logger.debug(f"Region CV search failed: confidence {max_val:.3f} < threshold {confidence}")
                    return None
            else:
                # Fallback to pyautogui region search
                try:
                    location = pyautogui.locateCenterOnScreen(
                        template_path,
                        region=(left, top, region_w, region_h),
                        confidence=confidence,
                        grayscale=grayscale
                    )
                    if location:
                        logger.info(f"PyAutoGUI region search found match at {location}")
                    else:
                        logger.debug(f"PyAutoGUI region search failed")
                    return location
                except Exception as e:
                    logger.debug(f"PyAutoGUI region search failed: {e}")
                    return None
                        
        except Exception as e:
            logger.debug(f"Region-based search failed: {e}")
            return None

    @staticmethod
    def ocr_text_detection(target_text, confidence=0.8, case_sensitive=True, screen_cv=None, stop_flag=None):
        """
        Performs OCR text detection to find specific text on screen.
        
        Args:
            target_text (str): Text to search for
            confidence (float): Matching confidence threshold (0.0 to 1.0)
            case_sensitive (bool): Whether text matching should be case sensitive
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
            stop_flag (callable): Function to check if operation should be stopped
        
        Returns:
            dict: Dictionary with 'found' (bool), 'text' (str), 'confidence' (float), 'bbox' (tuple) if found, None otherwise
        """
        if not OCR_AVAILABLE:
            logger.warning("OCR not available, skipping text detection")
            return None
            
        try:
            # Check stop flag before starting OCR
            if stop_flag and stop_flag():
                logger.info("Stop flag detected before OCR text detection - aborting")
                return None
            
            # Use provided screen or capture new one
            if screen_cv is None:
                logger.debug("Taking screenshot for OCR validation")
                screen_cv = TemplateMatching.capture_screen()
            
            # Check stop flag after screenshot
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR screenshot - aborting")
                return None
            
            # Check stop flag before OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected before OCR processing - aborting")
                return None

            ocr_elements = extract_text_elements(image=screen_cv, min_confidence=confidence, language="eng")
            
            # Check stop flag after OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR processing - aborting")
                return None
            
            # Process OCR results
            found_matches = []

            for element in ocr_elements:
                # Check stop flag during OCR result processing
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected during OCR result processing - aborting")
                    return None

                text = (element.get("text") or "").strip()
                normalized_conf = float(element.get("confidence") or 0.0)
                bbox = element.get("bbox")
                if not text or not bbox:
                    continue
                
                # Perform text matching
                search_text = text if case_sensitive else text.lower()
                target_search = target_text if case_sensitive else target_text.lower()
                
                # Balanced text matching logic - prevent single character false positives
                # but allow proper substring matching for multi-word targets
                if (target_search == search_text or  # Exact match
                    target_search in search_text or  # Target found in OCR text
                    (len(search_text) > 2 and search_text in target_search)):  # OCR text in target (but not single chars)
                    
                    # Calculate bounding box (full screen coordinates)
                    x, y, w, h = bbox
                    
                    match_result = {
                        'found': True,
                        'text': text,
                        'confidence': normalized_conf,
                        'bbox': (x, y, w, h),
                        'center': (x + w // 2, y + h // 2)
                    }
                    
                    found_matches.append(match_result)
                    logger.info(f"OCR text found: '{text}' (confidence: {normalized_conf:.3f}) at {match_result['center']}")
            
            # Return the best match (highest confidence)
            if found_matches:
                best_match = max(found_matches, key=lambda x: x['confidence'])
                logger.info(f"OCR detection successful: '{best_match['text']}' with confidence {best_match['confidence']:.3f}")
                return best_match
            else:
                logger.debug(f"OCR text '{target_text}' not found with confidence >= {confidence}")
                return None
                
        except Exception as e:
            logger.error(f"OCR text detection failed: {e}")
            return None
    
    @staticmethod
    def ocr_extract_all_text(min_confidence=0.6, screen_cv=None, stop_flag=None):
        """
        Extracts all readable text from full screen using OCR.
        
        Args:
            min_confidence (float): Minimum confidence threshold for text extraction
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
            stop_flag (callable): Function to check if operation should be stopped
        
        Returns:
            list: List of dictionaries with text, confidence, and bounding box information
        """
        if not OCR_AVAILABLE:
            logger.warning("OCR not available, skipping text extraction")
            return []
            
        try:
            # Check stop flag before starting OCR
            if stop_flag and stop_flag():
                logger.info("Stop flag detected before OCR text extraction - aborting")
                return []
            
            # Use provided screen or capture new one
            if screen_cv is None:
                logger.debug("Taking screenshot for OCR text extraction")
                screen_cv = TemplateMatching.capture_screen()
            
            # Check stop flag after screenshot
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR screenshot - aborting")
                return []
            
            # Check stop flag before OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected before OCR processing - aborting")
                return []

            logger.debug(f"Starting OCR extraction with confidence {min_confidence}")
            extracted_text = extract_text_elements(image=screen_cv, min_confidence=min_confidence, language="eng")
            logger.debug(f"OCR extraction finished, found {len(extracted_text)} elements")
            
            # Check stop flag after OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR processing - aborting")
                return []
            
            logger.info(f"OCR extracted {len(extracted_text)} text elements with confidence >= {min_confidence}")
            return extracted_text
            
        except Exception as e:
            import traceback
            logger.error(f"OCR text extraction failed: {e!r}")
            logger.error(traceback.format_exc())
            return []
