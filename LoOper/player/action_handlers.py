# action_handlers.py
"""
Action execution handlers for different types of automation actions.
"""

import os
import time
import pyautogui
from numpy import random

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False
from .config import logger, CV2_AVAILABLE
from .computer_vision import TemplateMatching
from .screenshot_cleanup import register_temporal_screenshot, cleanup_screenshot

if CV2_AVAILABLE:
    import cv2
    import numpy as np


class ActionHandlers:
    """
    Contains methods for handling different types of automation actions.
    """
    
    def __init__(self, bot_instance):
        """
        Initialize with reference to the bot instance for accessing shared state.
        
        Args:
            bot_instance: Reference to the SeleniumBot instance
        """
        self.bot = bot_instance
        self.template_matcher = TemplateMatching()

    def handle_click_action(self, idx, action, fallback_callback=None, stop_flag=None):
        """
        Handles click actions with visual template matching.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
            fallback_callback (callable): Optional fallback callback
            stop_flag (callable): Optional stop flag function
            
        Returns:
            bool: True if successful, False if fallback was triggered
        """
        self.bot.random_delay(0.5, 1.0)  # Add a small random delay before clicking.

        if not action.get('screenshot'):
            logger.error(f"Action {idx} is a click without a screenshot. Aborting playback.")
            raise Exception(f"Action {idx} is a click without a screenshot.")

        screenshot_path = action['screenshot']
        if not os.path.isabs(screenshot_path):
            screenshot_path = os.path.join(os.getcwd(), screenshot_path)

        if not os.path.exists(screenshot_path):
            logger.error(f"Screenshot file for action {idx} does not exist: {screenshot_path}")
            raise Exception(f"Screenshot file not found: {screenshot_path}")

        target_coords = None
        screenshot_found = False
        
        # Get retry configuration from action
        retry_attempts = action.get('fallback_retry_attempts', 3)
        retry_delay = action.get('fallback_retry_delay', 1.0)

        # Capture single screenshot for all search attempts to improve efficiency
        current_screen_cv = None
        temporal_screenshot_path = None
        try:
            if CV2_AVAILABLE:
                # Capture once and convert for OpenCV operations
                screen_pil = pyautogui.screenshot()
                screen_np = np.array(screen_pil)
                current_screen_cv = cv2.cvtColor(screen_np, cv2.COLOR_RGB2BGR)
                
                # Save temporal screenshot for debugging
                temporal_screenshot_path = f"temporal_screen_{idx}_{int(time.time())}.png"
                screen_pil.save(temporal_screenshot_path)
                # Register for automatic cleanup
                register_temporal_screenshot(temporal_screenshot_path)
                logger.debug(f"Single screenshot captured and converted for all search methods")
            else:
                # Fallback: capture screenshot for pyautogui methods
                temporal_screenshot = pyautogui.screenshot()
                temporal_screenshot_path = f"temporal_screen_{idx}_{int(time.time())}.png"
                temporal_screenshot.save(temporal_screenshot_path)
                # Register for automatic cleanup
                register_temporal_screenshot(temporal_screenshot_path)
                logger.debug(f"Temporal screenshot captured for pyautogui methods")
        except Exception as e:
            logger.debug(f"Failed to capture screenshot: {e}")

        for attempt in range(retry_attempts):
            # Check stop flag before each retry attempt
            if stop_flag and stop_flag():
                logger.info(f"Click action {idx} stopped by user request during retry attempt {attempt + 1}")
                return True
                
            logger.info(f"Attempting to find button for action {idx} via screenshot: {os.path.basename(screenshot_path)} (Attempt {attempt + 1}/{retry_attempts})")
            
            # Optimized search strategies with improved confidence thresholds
            search_strategies = [
                # High-confidence template matching first
                {'method': 'template', 'confidence': 0.85, 'grayscale': True, 'name': 'High-confidence grayscale (0.85)'},
                {'method': 'template', 'confidence': 0.85, 'grayscale': False, 'name': 'High-confidence color (0.85)'},
                
                # Multi-scale matching for size variations (prioritized)
                {'method': 'multiscale', 'confidence': 0.8, 'grayscale': True, 'scales': [0.9, 1.0, 1.1], 'name': 'Fine multi-scale (0.8)'},
                {'method': 'multiscale', 'confidence': 0.75, 'grayscale': True, 'scales': [0.8, 1.0, 1.2], 'name': 'Standard multi-scale (0.75)'},
                
                # Region-based search with reasonable expansion
                {'method': 'region', 'confidence': 0.8, 'grayscale': True, 'expand_factor': 2.5, 'name': 'Focused region search (0.8)'},
                {'method': 'region', 'confidence': 0.75, 'grayscale': True, 'expand_factor': 3.5, 'name': 'Expanded region search (0.75)'},
                
                # Medium confidence fallbacks
                {'method': 'template', 'confidence': 0.75, 'grayscale': True, 'name': 'Medium-confidence grayscale (0.75)'},
                {'method': 'multiscale', 'confidence': 0.7, 'grayscale': True, 'scales': [0.7, 1.0, 1.3], 'name': 'Wide multi-scale (0.7)'},
                
                # Lower confidence as last resort
                {'method': 'template', 'confidence': 0.65, 'grayscale': True, 'name': 'Low-confidence grayscale (0.65)'},
                {'method': 'region', 'confidence': 0.65, 'grayscale': True, 'expand_factor': 4.0, 'name': 'Large region search (0.65)'},
            ]

            for strategy in search_strategies:
                # Check stop flag before each search strategy
                if stop_flag and stop_flag():
                    logger.info(f"Click action {idx} stopped by user request during search strategy: {strategy['name']}")
                    return True
                    
                try:
                    logger.info(f"Attempting match with: {strategy['name']}")
                    
                    # Allow screen to stabilize
                    time.sleep(0.15)
                    
                    location = None
                    
                    if strategy['method'] == 'template':
                        # Template matching with multiple candidates to avoid random clicks
                        try:
                            boxes = list(pyautogui.locateAllOnScreen(
                                screenshot_path,
                                confidence=strategy['confidence'],
                                grayscale=strategy['grayscale']
                            ))
                        except Exception:
                            boxes = []

                        if boxes:
                            # Convert to centers and sort deterministically (row-major: top, then left)
                            centers = []
                            for b in boxes:
                                try:
                                    left = getattr(b, 'left', b[0])
                                    top = getattr(b, 'top', b[1])
                                    width = getattr(b, 'width', b[2])
                                    height = getattr(b, 'height', b[3])
                                except Exception:
                                    continue
                                cx = int(left + width / 2)
                                cy = int(top + height / 2)
                                centers.append((cx, cy, int(top), int(left)))

                            if centers:
                                centers.sort(key=lambda c: (c[2], c[3]))  # sort by top, then left

                                # Determine selection using per-element memory
                                element_key = os.path.abspath(screenshot_path)
                                last_index = self.bot.selection_memory.get(element_key, -1)

                                # If first time seeing this element, prefer nearest to original hint if available
                                hint_x = action.get('x')
                                hint_y = action.get('y')
                                if last_index == -1 and isinstance(hint_x, (int, float)) and isinstance(hint_y, (int, float)):
                                    try:
                                        hx = int(hint_x)
                                        hy = int(hint_y)
                                        dists = [((c[0] - hx) ** 2 + (c[1] - hy) ** 2) for c in centers]
                                        chosen_index = int(dists.index(min(dists)))
                                    except Exception:
                                        chosen_index = 0
                                elif last_index == -1:
                                    chosen_index = 0
                                else:
                                    chosen_index = (int(last_index) + 1) % len(centers)

                                self.bot.selection_memory[element_key] = chosen_index
                                location = (centers[chosen_index][0], centers[chosen_index][1])
                                logger.info(f"Selected candidate {chosen_index + 1}/{len(centers)} for element {os.path.basename(screenshot_path)}")
                            else:
                                location = None
                        else:
                            # Fallback to single best match if multiple candidates not found
                            location = pyautogui.locateCenterOnScreen(
                                screenshot_path,
                                confidence=strategy['confidence'],
                                grayscale=strategy['grayscale']
                            )
                    
                    elif strategy['method'] == 'multiscale':
                        # Multi-scale template matching with shared screenshot
                        location = self.template_matcher.multiscale_template_match(
                            screenshot_path, 
                            strategy['confidence'],
                            strategy['scales'],
                            strategy['grayscale'],
                            current_screen_cv
                        )
                    
                    elif strategy['method'] == 'region':
                        # Region-based search with original coordinates as hint and shared screenshot
                        location = self.template_matcher.region_based_search(
                            screenshot_path,
                            action['x'],
                            action['y'],
                            strategy['confidence'],
                            strategy['expand_factor'],
                            strategy['grayscale'],
                            current_screen_cv
                        )
                    
                    if location:
                        screen_width, screen_height = pyautogui.size()
                        target_x, target_y = int(location[0]), int(location[1])

                        if 0 <= target_x <= screen_width and 0 <= target_y <= screen_height:
                            target_coords = (target_x, target_y)
                            logger.info(f"Element found at {target_coords} using {strategy['name']}")
                            screenshot_found = True
                            break
                        else:
                            logger.warning(f"Found coordinates {location} are outside screen bounds")
                except Exception as e:
                    logger.debug(f"{strategy['name']} failed for action {idx}: {e}")

            if screenshot_found:
                break
            
            if attempt < retry_attempts - 1:
                logger.info(f"Element not found for action {idx}, retrying in {retry_delay}s...")
                # Sleep in small increments to allow for interruption
                elapsed = 0
                while elapsed < retry_delay:
                    if stop_flag and stop_flag():
                        logger.info(f"Click action {idx} stopped by user request during retry delay")
                        return True
                    sleep_time = min(0.1, retry_delay - elapsed)
                    time.sleep(sleep_time)
                    elapsed += sleep_time
        
        # Clean up temporal screenshot using the cleanup utility
        if temporal_screenshot_path:
            cleanup_screenshot(temporal_screenshot_path)

        if target_coords:
            self.bot.human_mouse_move(target_coords[0], target_coords[1])
            time.sleep(0.2)
            
            # Get button type from action, default to left
            button = action.get('button', 'left')
            
            # Handle different click types
            if action.get('type') == 'double_click':
                # Perform double-click
                if button == 'right':
                    pyautogui.rightClick()
                    time.sleep(0.1)
                    pyautogui.rightClick()
                elif button == 'middle':
                    pyautogui.middleClick()
                    time.sleep(0.1)
                    pyautogui.middleClick()
                else:  # left or default
                    pyautogui.doubleClick()
                logger.info(f"Double-clicked ({button}) at {target_coords}")
            else:
                # Perform single click
                if button == 'right':
                    pyautogui.rightClick()
                elif button == 'middle':
                    pyautogui.middleClick()
                else:  # left or default
                    pyautogui.click()
                logger.info(f"Clicked ({button}) at {target_coords}")
            
            self.bot.last_click_time = time.time()
            return True
        else:
            logger.warning(f"Visual match failed for action {idx} after all attempts.")
            
            if action.get('fallback_sequence') and fallback_callback:
                fallback_path = action['fallback_sequence']
                if not os.path.isabs(fallback_path):
                    fallback_path = os.path.join(os.getcwd(), fallback_path)
                
                logger.info(f"Triggering fallback sequence for action {idx}: {fallback_path}")
                fallback_callback(fallback_path, idx)
                return False  # Indicate fallback was triggered
            else:
                logger.error(f"Visual match for action {idx} failed and no fallback is configured. Aborting playback.")
                raise Exception("Visual match failed and no fallback configured.")

    def handle_type_string_action(self, idx, action, stop_flag=None):
        """
        Handles typing text actions with optimized speed and completion tracking.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
            stop_flag (callable): Function to check if execution should stop
            
        Returns:
            bool: True when typing is complete
        """
        if 'text' in action:
            # Check stop flag before starting typing
            if stop_flag and stop_flag():
                logger.info("Stop flag detected before typing - aborting type string action")
                return False
            
            # Check if a specific delay is required after a click before typing
            if 'delay_after_click' in action:
                time_since_click = time.time() - self.bot.last_click_time
                required_delay = action['delay_after_click']
                if time_since_click < required_delay:
                    wait_time = required_delay - time_since_click
                    logger.debug(f"Waiting {wait_time:.2f}s after click before typing")
                    time.sleep(wait_time)
            
            # Get text to type and ensure it's a string
            text = action['text']
            if not isinstance(text, str):
                try:
                    text = str(text)
                    logger.warning(f"Converted non-string text to string: {type(action['text'])} -> {text[:50]}...")
                except Exception as str_error:
                    logger.error(f"Failed to convert text to string: {str_error}")
                    return False
            
            # Get typing speed configuration
            # Check if we should use character-by-character typing for maximum reliability
            char_by_char = action.get('char_by_char', False)  # Default to batch mode
            
            # Get keystroke delay for preventing duplicate characters on slow machines
            keystroke_delay = action.get('keystroke_delay', 0.01)  # Default 10ms delay between keystrokes
            
            if char_by_char:
                # Character-by-character typing for maximum reliability
                batch_size = 1  # One character at a time
                batch_delay = action.get('char_delay', 0.05)  # Default to 0.05s between characters
                logger.info(f"Typing text character-by-character with delay={batch_delay}s, keystroke_delay={keystroke_delay}s")
            else:
                # Batch typing mode
                batch_size = action.get('batch_size', 5)  # Default to 5 chars per batch
                batch_delay = action.get('batch_delay', 0.15)  # Default to 0.15s between batches
                logger.info(f"Typing text with batch_size={batch_size}, batch_delay={batch_delay}, keystroke_delay={keystroke_delay}s")
            
            # Type text in batches for significantly improved speed
            for i in range(0, len(text), batch_size):
                # Check stop flag during typing
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected during typing - aborting type string action")
                    return False
                
                # Get the next batch of characters
                batch = text[i:i+batch_size]
                
                try:
                    # Filter out non-typeable characters that might cause issues with pyautogui
                    # Keep only printable ASCII characters and common whitespace
                    filtered_batch = ''.join(c for c in batch if c.isprintable() or c in '\n\t\r ')
                    
                    if filtered_batch != batch:
                        logger.warning(f"Filtered out non-printable characters from batch")
                    
                    # Handle line breaks specially to ensure they're properly processed
                    if '\n' in filtered_batch:
                        # Split by newlines and type each segment separately with proper Enter key presses
                        segments = filtered_batch.split('\n')
                        for j, segment in enumerate(segments):
                            # Check stop flag before each segment
                            if stop_flag and stop_flag():
                                logger.info("Stop flag detected during line break typing - aborting type string action")
                                return False
                            
                            if segment:  # Only type non-empty segments
                                # Type character by character with delay to prevent duplicates
                                for char in segment:
                                    pyautogui.write(char)
                                    if keystroke_delay > 0:
                                        time.sleep(keystroke_delay)
                            # Press Enter after each segment except the last one
                            if j < len(segments) - 1:
                                time.sleep(0.05)  # Small delay before Enter key
                                pyautogui.press('enter')
                                time.sleep(0.05)  # Small delay after Enter key
                    else:
                        # Type the filtered batch character by character with delay
                        for char in filtered_batch:
                            pyautogui.write(char)
                            if keystroke_delay > 0:
                                time.sleep(keystroke_delay)
                except Exception as write_error:
                    logger.error(f"Error writing batch: {write_error}")
                    logger.error(f"Problematic batch content: {repr(batch)}")
                    return False
                
                # Small delay between batches for natural typing feel
                if i + batch_size < len(text):
                    time.sleep(batch_delay)
            
            logger.info(f"Typed text ({len(text)} chars) using optimized batching")
            
            # Set completion flag in action for workflow integration
            action['completed'] = True
            return True
        
        return False

    def handle_keystroke_action(self, idx, action):
        """
        Handles special keystroke actions (Enter, Tab, etc.).
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
        """
        # Similar to typing, check for a required delay after a click.
        if 'delay_after_click' in action:
            time_since_click = time.time() - self.bot.last_click_time
            required_delay = action['delay_after_click']
            if time_since_click < required_delay:
                wait_time = required_delay - time_since_click
                logger.debug(f"Waiting {wait_time:.2f}s after click before keystroke")
                time.sleep(wait_time)
        # Clean the key name (e.g., 'Key.enter' -> 'enter').
        key = action['key'].replace('Key.', '')
        # Map pynput key names to pyautogui key names.
        key_mapping = {
            'space': 'space', 'enter': 'enter', 'backspace': 'backspace',
            'tab': 'tab', 'esc': 'escape', 'up': 'up', 'down': 'down',
            'left': 'left', 'right': 'right', 'delete': 'delete',
            'shift': 'shift', 'ctrl': 'ctrl', 'alt': 'alt'
        }
        if key in key_mapping:
            # Get keystroke delay to prevent issues on slow machines
            keystroke_delay = action.get('keystroke_delay', 0.01)  # Default 10ms delay
            
            pyautogui.press(key_mapping[key]) # Press the special key.
            logger.info(f"Pressed special key: {key}")
            
            # Add small delay after keystroke to prevent issues on slow machines
            if keystroke_delay > 0:
                time.sleep(keystroke_delay)

    def handle_scroll_action(self, idx, action):
        """
        Handles scroll actions.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
        """
        # Extract vertical scroll amount from correct field
        dy = action.get('total_delta', 0)  # Use total_delta from recordings
        
        # Fallback to old 'delta' structure for backward compatibility
        if dy == 0 and 'delta' in action and isinstance(action['delta'], dict):
            dy = action['delta'].get('y', 0)
        
        if dy == 0:
            logger.info("Scroll action with zero delta, skipping.")
            return True

        # Get step count and duration from recording
        num_steps = max(1, int(action.get('steps', 1)))  # Ensure at least 1 step
        total_duration = action.get('duration_sec', 0.0)
        
        # Calculate per-step scroll amount and timing
        step_dy = dy / num_steps
        step_duration = total_duration / num_steps if total_duration > 0 else 0
        
        logger.info(f"Simulating scroll: total_delta={dy}, steps={num_steps}, duration={total_duration:.3f}s")
        
        # Execute scroll in small steps (mimics real user behavior)
        accumulated = 0.0
        for i in range(num_steps):
            # Calculate exact step amount (avoids precision loss)
            current_target = (i + 1) * step_dy
            rounded_target = round(current_target)
            step_amount = int(rounded_target - accumulated)
            accumulated = rounded_target
            
            if step_amount != 0:
                pyautogui.scroll(step_amount)  # Execute small scroll
            
            # Pause between steps to match original timing
            if i < num_steps - 1 and step_duration > 0:
                time.sleep(step_duration)

    def handle_clipboard_action(self, idx, action):
        """
        Handles clipboard actions (copy, paste, cut, select all).
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
        """
        operation = None
        # Determine the operation (e.g., 'copy', 'paste').
        if action['type'] == 'clipboard':
            operation = action.get('operation')
        else:
            operation = action['type']
        
        # Execute the corresponding hotkey.
        if operation == 'copy' or operation == 'c':
            time.sleep(0.1) # Small delay to ensure text is selected before copying.
            pyautogui.hotkey('ctrl', 'c')
            logger.info("Performed Ctrl+C operation")
        elif operation == 'paste' or operation == 'v':
            time.sleep(0.1) # Small delay to ensure the input field is focused.
            pyautogui.hotkey('ctrl', 'v')
            logger.info("Performed Ctrl+V operation")
        elif operation == 'cut' or operation == 'x':
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'x')
            logger.info("Performed Ctrl+X operation")
        elif operation == 'select_all' or operation == 'a':
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'a')
            logger.info("Performed Ctrl+A operation")

    def handle_drag_actions(self, idx, action):
        """
        Handles drag and drop actions.
        
        Args:
            idx (int): Action index
            action (dict): Action configuration
        """
        if action['type'] == 'drag_start':
            abs_x = action['coordinates']['x']
            abs_y = action['coordinates']['y']
            self.bot.human_mouse_move(abs_x, abs_y)
            pyautogui.mouseDown() # Press and hold the mouse button.
            logger.info(f"Started drag at ({abs_x}, {abs_y})")

        elif action['type'] == 'drag_end':
            abs_x = action['coordinates']['x']
            abs_y = action['coordinates']['y']
            self.bot.human_mouse_move(abs_x, abs_y)
            pyautogui.mouseUp() # Release the mouse button.
            logger.info(f"Ended drag at ({abs_x}, {abs_y})")

        elif action['type'] == 'drag_drop':
            start_x, start_y = action['from']['x'], action['from']['y']
            end_x, end_y = action['to']['x'], action['to']['y']
            self.bot.human_mouse_move(start_x, start_y)
            pyautogui.mouseDown()
            self.bot.human_mouse_move(end_x, end_y)
            pyautogui.mouseUp()
            logger.info(f"Performed drag_drop from ({start_x}, {start_y}) to ({end_x}, {end_y})")