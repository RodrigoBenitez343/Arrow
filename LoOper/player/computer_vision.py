# computer_vision.py
"""
Computer vision and template matching functionality for the LocalOperator automation system.
"""

import os
import time
import pyautogui
from .config import logger, CV2_AVAILABLE, OCR_AVAILABLE

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False

if CV2_AVAILABLE:
    import cv2
    import numpy as np

if OCR_AVAILABLE:
    import pytesseract
    # Set tesseract command path for Windows
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


class TemplateMatching:
    """
    Handles computer vision operations for template matching and image recognition.
    """
    
    @staticmethod
    def multiscale_template_match(template_path, confidence, scales, grayscale=True, screen_cv=None):
        """
        Performs multi-scale template matching to handle size variations.
        
        Args:
            template_path (str): Path to the template image
            confidence (float): Matching confidence threshold
            scales (list): List of scale factors to try
            grayscale (bool): Whether to use grayscale matching
            screen_cv (numpy.ndarray): Pre-captured screen image to avoid multiple screenshots
        
        Returns:
            tuple: (x, y) coordinates if found, None otherwise
        """
        if not CV2_AVAILABLE:
            logger.debug("OpenCV not available, skipping multi-scale template matching")
            return None
            
        try:
            # Load template
            template = cv2.imread(template_path)
            if template is None:
                logger.debug(f"Failed to load template: {template_path}")
                return None
            
            # Use provided screen or capture new one
            if screen_cv is None:
                screen = pyautogui.screenshot()
                screen_np = np.array(screen)
                screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
            
            # Convert to grayscale if needed
            template_work = template.copy()
            screen_work = screen_cv.copy()
            
            if grayscale:
                if len(template_work.shape) == 3:
                    template_work = cv2.cvtColor(template_work, cv2.COLOR_BGR2GRAY)
                if len(screen_work.shape) == 3:
                    screen_work = cv2.cvtColor(screen_work, cv2.COLOR_BGR2GRAY)
            
            best_match = None
            best_confidence = 0
            
            logger.debug(f"Multi-scale matching with {len(scales)} scales, confidence threshold: {confidence}")
            
            for scale in scales:
                # Resize template
                width = int(template_work.shape[1] * scale)
                height = int(template_work.shape[0] * scale)
                
                if width < 5 or height < 5 or width > screen_work.shape[1] or height > screen_work.shape[0]:
                    logger.debug(f"Skipping scale {scale}: invalid dimensions {width}x{height}")
                    continue
                    
                scaled_template = cv2.resize(template_work, (width, height))
                
                # Perform template matching
                result = cv2.matchTemplate(screen_work, scaled_template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(result)
                
                logger.debug(f"Scale {scale}: confidence {max_val:.3f} at ({max_loc[0]}, {max_loc[1]})")
                
                if max_val > best_confidence and max_val >= confidence:
                    best_confidence = max_val
                    # Calculate center coordinates
                    center_x = max_loc[0] + width // 2
                    center_y = max_loc[1] + height // 2
                    best_match = (center_x, center_y)
                    logger.debug(f"New best match at scale {scale}: confidence {best_confidence:.3f}, coords ({center_x}, {center_y})")
            
            if best_match:
                logger.info(f"Multi-scale match found: confidence {best_confidence:.3f}, coords {best_match}")
            else:
                logger.debug(f"Multi-scale matching failed: best confidence {best_confidence:.3f} < threshold {confidence}")
                    
            return best_match
            
        except Exception as e:
            logger.debug(f"Multi-scale template matching failed: {e}")
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
            # Load template to get its dimensions
            template = cv2.imread(template_path) if CV2_AVAILABLE else None
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
        Performs OCR text detection to find specific text on screen using full screen screenshot.
        ALWAYS takes a fresh screenshot to ensure current state validation.
        
        Args:
            target_text (str): Text to search for
            confidence (float): Matching confidence threshold (0.0 to 1.0)
            case_sensitive (bool): Whether text matching should be case sensitive
            screen_cv (numpy.ndarray): IGNORED - Always takes fresh screenshot
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
            
            # ALWAYS take a fresh screenshot - ignore any passed screen_cv to prevent stale data
            logger.debug("Taking fresh screenshot for OCR validation")
            screen = pyautogui.screenshot()  # Full screen capture
            screen_np = np.array(screen)
            if CV2_AVAILABLE:
                screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
            else:
                screen_cv = screen_np
            
            # Check stop flag after screenshot
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR screenshot - aborting")
                return None
            
            # Convert to PIL Image for pytesseract
            if CV2_AVAILABLE and len(screen_cv.shape) == 3:
                # Convert BGR to RGB for PIL
                screen_rgb = cv2.cvtColor(screen_cv, cv2.COLOR_BGR2RGB)
                from PIL import Image
                pil_image = Image.fromarray(screen_rgb)
            else:
                from PIL import Image
                pil_image = Image.fromarray(screen_cv)
            
            # Configure OCR settings for better accuracy (using default English)
            custom_config = '--oem 3 --psm 6 -l eng'
            
            # Check stop flag before OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected before OCR processing - aborting")
                return None
            
            # Get detailed OCR data with confidence scores
            ocr_data = pytesseract.image_to_data(pil_image, config=custom_config, output_type=pytesseract.Output.DICT)
            
            # Check stop flag after OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR processing - aborting")
                return None
            
            # Process OCR results
            found_matches = []
            
            for i in range(len(ocr_data['text'])):
                # Check stop flag during OCR result processing
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected during OCR result processing - aborting")
                    return None
                
                text = ocr_data['text'][i].strip()
                conf = float(ocr_data['conf'][i])
                
                if not text:  # Skip empty text
                    continue
                    
                # Normalize confidence to 0-1 range (pytesseract uses 0-100)
                normalized_conf = conf / 100.0
                
                # Check if confidence meets threshold
                if normalized_conf < confidence:
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
                    x = ocr_data['left'][i]
                    y = ocr_data['top'][i]
                    w = ocr_data['width'][i]
                    h = ocr_data['height'][i]
                    
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
            
            # Always use full screen screenshot
            if screen_cv is None:
                screen = pyautogui.screenshot()  # Full screen capture
                screen_np = np.array(screen)
                if CV2_AVAILABLE:
                    screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
                else:
                    screen_cv = screen_np
            
            # Check stop flag after screenshot
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR screenshot - aborting")
                return []
            
            # Convert to PIL Image for pytesseract
            if CV2_AVAILABLE and len(screen_cv.shape) == 3:
                # Convert BGR to RGB for PIL
                screen_rgb = cv2.cvtColor(screen_cv, cv2.COLOR_BGR2RGB)
                from PIL import Image
                pil_image = Image.fromarray(screen_rgb)
            else:
                from PIL import Image
                pil_image = Image.fromarray(screen_cv)
            
            # Configure OCR settings (using default English)
            custom_config = '--oem 3 --psm 6 -l eng'
            
            # Check stop flag before OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected before OCR processing - aborting")
                return []
            
            # Get detailed OCR data
            ocr_data = pytesseract.image_to_data(pil_image, config=custom_config, output_type=pytesseract.Output.DICT)
            
            # Check stop flag after OCR processing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected after OCR processing - aborting")
                return []
            
            # Process and filter results
            extracted_text = []
            
            for i in range(len(ocr_data['text'])):
                # Check stop flag during OCR result processing
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected during OCR result processing - aborting")
                    return extracted_text  # Return partial results
                
                text = ocr_data['text'][i].strip()
                conf = float(ocr_data['conf'][i])
                
                if not text:  # Skip empty text
                    continue
                    
                # Normalize confidence to 0-1 range
                normalized_conf = conf / 100.0
                
                # Check if confidence meets threshold
                if normalized_conf < min_confidence:
                    continue
                
                # Calculate bounding box (full screen coordinates)
                x = ocr_data['left'][i]
                y = ocr_data['top'][i]
                w = ocr_data['width'][i]
                h = ocr_data['height'][i]
                
                text_info = {
                    'text': text,
                    'confidence': normalized_conf,
                    'bbox': (x, y, w, h),
                    'center': (x + w // 2, y + h // 2)
                }
                
                extracted_text.append(text_info)
            
            logger.info(f"OCR extracted {len(extracted_text)} text elements with confidence >= {min_confidence}")
            return extracted_text
            
        except Exception as e:
            logger.error(f"OCR text extraction failed: {e}")
            return []