# base_bot.py
"""
Base automation bot class with core functionality.
"""

import os
import time
import pyautogui
import functools

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False
from numpy import random
from datetime import datetime
from .config import logger
from .action_handlers import ActionHandlers
from .screenshot_cleanup import get_cleanup_manager, register_temporal_screenshot


class SeleniumBot:
    """
    A base class for automation bots.
    Despite the name, it does not use Selenium. It provides foundational
    functionalities like human-like delays, mouse movements, and action execution
    using pyautogui.
    """
    def __init__(self):
        """
        Initializes the bot's attributes.
        """
        self.wait = None                     # Placeholder for a Selenium-like wait object.
        self.default_timeout = 10            # Default timeout in seconds for operations.
        self.retry_attempts = 5              # Number of times to retry a failed action.
        self.ignored_exceptions = (Exception,) # Placeholder for exceptions to ignore during retries.
        self.action_count = 0                # Counter for the number of actions performed.
        self.last_action_time = time.time()  # Timestamp of the last action.
        self.last_click_time = time.time()   # Timestamp of the last click, for timing subsequent actions.
        self.mouse_movement_history = []     # Stores a history of mouse movements.
        # Per-element selection memory to avoid random clicks when multiple matches exist.
        # Maps a stable element key (e.g., screenshot path) to the last chosen index.
        self.selection_memory = {}
        self.action_handlers = ActionHandlers(self)  # Initialize action handlers

    def random_delay(self, min_seconds=2.5, max_seconds=5.5):
        """
        Waits for a random amount of time to simulate human behavior.
        The delay follows a normal (Gaussian) distribution between the min and max values.
        
        Args:
            min_seconds (float): The minimum delay time.
            max_seconds (float): The maximum delay time.
        """
        mu = (min_seconds + max_seconds) / 2      # Calculate the mean (center) of the distribution.
        sigma = (max_seconds - min_seconds) / 6   # Calculate the standard deviation.
        delay = random.normal(mu, sigma)          # Generate a delay from the normal distribution.
        delay = max(min_seconds, min(max_seconds, delay)) # Ensure the delay is within the specified bounds.
        time.sleep(delay)                         # Pause the script execution.
        logger.debug(f"Random delay: {delay:.2f}s")

    def take_screenshot(self, directory="screenshots", prefix="screenshot", is_temporal=False):
        """
        Captures a screenshot of the entire screen and saves it to the specified directory.

        Args:
            directory (str): Directory where screenshots will be saved.
            prefix (str): Filename prefix for the screenshot.
            is_temporal (bool): If True, registers screenshot for automatic cleanup.
        """
        # Ensure output directory exists
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{prefix}_{timestamp}.png"
        path = os.path.join(directory, filename)
        try:
            screenshot = pyautogui.screenshot()
            screenshot.save(path)
            logger.debug(f"Screenshot saved to {path}")
            
            # Register temporal screenshots for cleanup
            if is_temporal:
                register_temporal_screenshot(path)
                
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            raise

    def retry_on_exception(f):
        """
        A decorator that retries a function if it raises an exception.
        It uses an exponential backoff strategy, waiting longer after each failed attempt.
        """
        @functools.wraps(f)
        def wrapper(self, *args, **kwargs):
            for attempt in range(self.retry_attempts):
                try:
                    # Try to execute the function.
                    return f(self, *args, **kwargs)
                except Exception as e:
                    # Calculate wait time with exponential backoff plus some randomness.
                    wait_time = 2 ** attempt + random.uniform(0, 1)
                    if attempt == self.retry_attempts - 1:
                        # If this was the last attempt, log the final failure and re-raise the exception.
                        logger.error(f"Final attempt failed: {str(e)}")
                        raise
                    # Log the failure and the upcoming retry attempt.
                    logger.warning(f"Attempt {attempt+1} failed: {str(e)}. Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
            return None # Should not be reached if an exception is always raised.
        return wrapper

    def human_mouse_move(self, x, y):
        """
        Moves the mouse cursor to a target coordinate (x, y) in a human-like, curved path.
        This avoids straight, robotic mouse movements. It uses a quadratic Bézier curve.
        OPTIMIZED: Reduced delays for faster movement.

        Args:
            x (int): The target x-coordinate.
            y (int): The target y-coordinate.
        """
        logger.debug(f"Moving mouse to ({x}, {y})")
        start_x, start_y = pyautogui.position() # Get the current mouse position.
        distance = ((x - start_x)**2 + (y - start_y)**2)**0.5 # Calculate the distance to the target.

        # Calculate a random control point for the Bézier curve.
        # This point pulls the curve away from a straight line.
        ctrl_x = (start_x + x) / 2 + random.uniform(-distance/3, distance/3)
        ctrl_y = (start_y + y) / 2 + random.uniform(-distance/3, distance/3)

        points = []
        # Generate points along the Bézier curve.
        for t in [i/10 for i in range(1, 11)]: # t goes from 0.1 to 1.0.
            # Quadratic Bézier formula: B(t) = (1-t)^2*P0 + 2(1-t)t*P1 + t^2*P2
            bx = (1-t)**2*start_x + 2*(1-t)*t*ctrl_x + t**2*x
            by = (1-t)**2*start_y + 2*(1-t)*t*ctrl_y + t**2*y
            points.append((bx, by))

        # Move the mouse through the generated points with minimal delay
        for point in points:
            pyautogui.moveTo(point[0], point[1], duration=0.0001)  # Reduced from 0.001
            # Removed sleep for faster movement

        # Final move to the exact target coordinate with minimal duration
        pyautogui.moveTo(x, y, duration=0.001)  # Reduced from 0.01
        self.mouse_movement_history.append((start_x, start_y, x, y))

    def execute_with_timing(self, idx, action, fallback_callback=None, stop_flag=None):
        """
        Executes a single action from a sequence (e.g., click, type, scroll).
        It handles timing, delays, and different action types based on the 'action' dictionary.

        Args:
            idx (int): The index of the action in the sequence.
            action (dict): A dictionary describing the action to be performed.
            fallback_callback (callable): Optional callback to trigger when visual matching fails.
            stop_flag (callable): Optional function that returns True when playback should stop.
        
        Returns:
            bool: True if action succeeded, False if visual matching failed and fallback was triggered.
        """
        # Check stop flag before starting action
        if stop_flag and stop_flag():
            logger.info("Action execution stopped by user request")
            return True
            
        # Get the delay before the action, or use a default random delay.
        delay = action.get('delay_before', random.uniform(0.001, 0.01))  # Reduced from 0.01-0.05
        logger.debug(f"Action {idx}: Waiting {delay:.2f}s before action")
        # Ensure the delay is within a reasonable range (0.001 to 1.0 seconds).
        delay = max(0.001, min(delay, 1.0))  # Reduced minimum from 0.01
        
        # Sleep in small increments to allow for interruption
        elapsed = 0
        while elapsed < delay:
            if stop_flag and stop_flag():
                logger.info("Action execution stopped during delay")
                return True
            sleep_time = min(0.1, delay - elapsed)
            time.sleep(sleep_time)
            elapsed += sleep_time

        try:
            # Route actions to appropriate handlers
            if action['type'] in ['click', 'double_click']:
                success = self.action_handlers.handle_click_action(idx, action, fallback_callback, stop_flag)
                if not success:
                    return False  # Fallback was triggered
                    
            elif action['type'] == 'type_string':
                self.action_handlers.handle_type_string_action(idx, action)
                
            elif action['type'] == 'keystroke':
                self.action_handlers.handle_keystroke_action(idx, action)
                
            elif action['type'] == 'scroll':
                self.action_handlers.handle_scroll_action(idx, action)
                
            elif action['type'] in ['clipboard', 'copy', 'paste', 'cut', 'select_all']:
                self.action_handlers.handle_clipboard_action(idx, action)
                
            elif action['type'] in ['drag_start', 'drag_end', 'drag_drop']:
                self.action_handlers.handle_drag_actions(idx, action)
            
            # After a click action, add a very short random delay.
            if action['type'] in ['click', 'double_click']:
                self.random_delay(0.001, 0.01)  # Reduced from 0.01-0.05

            # After every successfully executed action, capture a screenshot of the entire screen
            try:
                # Mark action screenshots as temporal since they're used for debugging/verification
                self.take_screenshot(prefix=f"action_{idx}", is_temporal=True)
            except Exception as e:
                logger.warning(f"Failed to capture screenshot after action {idx}: {e}")
                
        except Exception as e:
            logger.error(f"Failed to execute action {idx}: {str(e)}")
            raise # Re-raise the exception to be handled by the caller.
        
        return True  # Action completed successfully

    def close(self):
        """
        Cleanup and close the bot. Cleans up temporal screenshots.
        """
        # Clean up temporal screenshots before closing
        try:
            cleanup_manager = get_cleanup_manager()
            cleanup_manager.cleanup_all_registered()
            # Also clean up old temporal screenshots (older than 30 minutes)
            cleanup_manager.cleanup_old_temporal_screenshots()
        except Exception as e:
            logger.warning(f"Failed to cleanup screenshots during close: {e}")
        
        logger.info("Shutting down (no browser to close)")