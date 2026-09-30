import logging
logger = logging.getLogger(__name__)
# base_bot.py
"""
Base automation bot class with core functionality.
"""

import os
import time
import pyautogui
import functools
import math

# Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
pyautogui.FAILSAFE = False
from numpy import random as np_random
import random as py_random
from datetime import datetime
from .action_handlers import ActionHandlers
from .screenshot_cleanup import get_cleanup_manager, register_temporal_screenshot
from .execution_overlay_bus import bus as _exec_bus


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
        # Maps a stable element key (e.g., screenshot path) to selection state dict.
        self.selection_memory = {}
        self.action_handlers = ActionHandlers(self)  # Initialize action handlers
        self._noise_seed = int(np_random.randint(0, 1e9))
        self._perlin_perm = self._build_perm_table(self._noise_seed)
        self.noise_amplitude_px = 3.0
        self.noise_frequency = 1.5

    def random_delay(self, min_seconds=2.5, max_seconds=5.5):
        """
        Waits for a random amount of time to simulate human behavior.
        The delay follows a normal (Gaussian) distribution between the min and max values.
        
        Args:
            min_seconds (float): The minimum delay time.
            max_seconds (float): The maximum delay time.
        """
        mu = (min_seconds + max_seconds) / 2
        sigma = (max_seconds - min_seconds) / 6
        delay = np_random.normal(mu, sigma)
        delay = max(min_seconds, min(max_seconds, delay)) # Ensure the delay is within the specified bounds.
        time.sleep(delay)                         # Pause the script execution.
        logger.debug(f"Random delay: {delay:.2f}s")

    def take_screenshot(self, directory="screenshots", prefix="screenshot", is_temporal=False):
        """
        Captures a screenshot of the entire screen.
        Temporal screenshots are kept in-memory only (no disk write).
        Non-temporal screenshots are saved to the specified directory.

        In sandboxed runs the screenshot is taken from the RDP agent so the
        evidence/verification image shows the desktop the action affected.

        Args:
            directory (str): Directory where permanent screenshots will be saved.
            prefix (str): Filename prefix for the screenshot.
            is_temporal (bool): If True, screenshot is ephemeral (memory-only).
        """
        agent_url = getattr(self, 'sandbox_agent_url', None)
        screenshot = None
        if agent_url:
            try:
                screenshot = self.action_handlers._get_screen_pil()
            except Exception as e:
                logger.warning(f"Agent screenshot failed ({e}); falling back to host capture")
                screenshot = None

        if is_temporal:
            # Ephemeral mode: capture but don't save to disk
            try:
                if screenshot is None:
                    pyautogui.screenshot()
                logger.debug(f"Temporal screenshot captured (in-memory): {prefix}")
                return
            except Exception as e:
                logger.error(f"Failed to capture temporal screenshot: {e}")
                return

        # Permanent screenshot: save to disk
        if not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{prefix}_{timestamp}.png"
        path = os.path.join(directory, filename)
        try:
            if screenshot is None:
                screenshot = pyautogui.screenshot()
            screenshot.save(path)
            logger.debug(f"Screenshot saved to {path}")
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
                    wait_time = 2 ** attempt + np_random.uniform(0, 1)
                    if attempt == self.retry_attempts - 1:
                        # If this was the last attempt, log the final failure and re-raise the exception.
                        logger.error(f"Final attempt failed: {str(e)}")
                        raise
                    # Log the failure and the upcoming retry attempt.
                    logger.warning(f"Attempt {attempt+1} failed: {str(e)}. Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
            return None # Should not be reached if an exception is always raised.
        return wrapper

    def _build_human_path(self, x, y, start=None):
        """
        Build the waypoints for a human-like, curved mouse path from *start*
        (default: current cursor position) to (x, y), using a quadratic Bézier
        curve plus Perlin noise. Returns the waypoint list.

        The waypoints are used directly by the host path and are also sent to
        the RDP sandbox agent so in-session cursor travel keeps the same
        graceful, non-robotic feel as the direct path.
        """
        if start is None:
            start_x, start_y = pyautogui.position()
        else:
            start_x, start_y = start
        distance = math.hypot(x - start_x, y - start_y)
        ctrl_x = (start_x + x) / 2 + np_random.uniform(-distance/3, distance/3)
        ctrl_y = (start_y + y) / 2 + np_random.uniform(-distance/3, distance/3)
        points = []
        for i in range(1, 11):
            t = i / 10.0
            bx = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * x
            by = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * y
            amp = min(self.noise_amplitude_px, max(1.0, distance * 0.03))
            nfx = self._perlin2(t * self.noise_frequency, 0.0)
            nfy = self._perlin2(t * self.noise_frequency, 100.0)
            bx += nfx * amp
            by += nfy * amp
            points.append((bx, by))
        return points

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
        start_x, start_y = pyautogui.position()
        points = self._build_human_path(x, y, start=(start_x, start_y))
        # Feed the execution overlay's cursor trail.  The waypoints are a ~1 ms
        # burst, so an overlay that only polls pyautogui.position() would keep
        # the endpoints and lose the path.
        _exec_bus.add_trail(start_x, start_y)
        for point in points:
            pyautogui.moveTo(point[0], point[1], duration=0.0001)
            _exec_bus.add_trail(point[0], point[1])
        pyautogui.moveTo(x, y, duration=0.001)
        _exec_bus.add_trail(x, y)
        # The destination is where the next click lands: name it so the overlay
        # boxes the element about to be acted on (it does not follow the cursor).
        _exec_bus.set_target(x, y)
        self.mouse_movement_history.append((start_x, start_y, x, y))

    def human_click(self, button='left', double=False):
        count = 2 if bool(double) else 1
        # Pulse the execution overlay's click marker at the live position.
        try:
            _cx, _cy = pyautogui.position()
            _exec_bus.add_click(_cx, _cy)
        except Exception:
            pass
        for i in range(count):
            dx = np_random.uniform(-1.5, 1.5)
            dy = np_random.uniform(-1.5, 1.5)
            pyautogui.moveRel(dx, dy, duration=np_random.uniform(0.001, 0.01))
            pyautogui.mouseDown(button=button)
            time.sleep(np_random.uniform(0.035, 0.12))
            pyautogui.mouseUp(button=button)
            if i < count - 1:
                time.sleep(np_random.uniform(0.08, 0.22))

    def _build_perm_table(self, seed):
        rng = py_random.Random(seed)
        base = list(range(256))
        rng.shuffle(base)
        return base * 2

    def _fade(self, t):
        return t * t * t * (t * (t * 6 - 15) + 10)

    def _lerp(self, a, b, t):
        return a + t * (b - a)

    def _grad2(self, h, x, y):
        v = h & 3
        if v == 0:
            return x + y
        if v == 1:
            return -x + y
        if v == 2:
            return x - y
        return -x - y

    def _perlin2(self, x, y):
        xi = int(math.floor(x)) & 255
        yi = int(math.floor(y)) & 255
        xf = x - math.floor(x)
        yf = y - math.floor(y)
        u = self._fade(xf)
        v = self._fade(yf)
        aa = self._perlin_perm[self._perlin_perm[xi] + yi]
        ab = self._perlin_perm[self._perlin_perm[xi] + yi + 1]
        ba = self._perlin_perm[self._perlin_perm[xi + 1] + yi]
        bb = self._perlin_perm[self._perlin_perm[xi + 1] + yi + 1]
        x1 = self._lerp(self._grad2(aa, xf, yf), self._grad2(ba, xf - 1, yf), u)
        x2 = self._lerp(self._grad2(ab, xf, yf - 1), self._grad2(bb, xf - 1, yf - 1), u)
        return self._lerp(x1, x2, v)

    def clear_selection_memory(self):
        """
        Clear the selection memory to reset element clicking order.
        This is useful when starting a new chain or sequence.
        """
        self.selection_memory.clear()
        logger.info("Selection memory cleared")

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

        # Tell the execution overlay what is about to run ("what will be clicked next").
        _exec_bus.set_action(f"{idx}: {action.get('type', 'action')}")

        # Get the delay before the action, or use a default random delay.
        delay = action.get('delay_before', np_random.uniform(0.001, 0.01))  # Reduced from 0.01-0.05
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
            if action['type'] in ['click', 'double_click', 'ctrl_click', 'shift_click']:
                success = self.action_handlers.handle_click_action(idx, action, fallback_callback, stop_flag)
                if not success:
                    return False  # Fallback was triggered
            elif action['type'] == 'move_to':
                self.action_handlers.handle_move_to_action(idx, action, stop_flag)
            elif action['type'] == 'relative_click':
                self.action_handlers.handle_relative_click_action(idx, action, stop_flag)
            elif action['type'] == 'absolute_click':
                self.action_handlers.handle_absolute_click_action(idx, action, stop_flag)
                    
            elif action['type'] == 'type_string':
                self.action_handlers.handle_type_string_action(idx, action, stop_flag)
                
            elif action['type'] == 'keystroke':
                self.action_handlers.handle_keystroke_action(idx, action, stop_flag)
                
            elif action['type'] == 'key_event':
                self.action_handlers.handle_key_event_action(idx, action, stop_flag)
                
            elif action['type'] == 'scroll':
                self.action_handlers.handle_scroll_action(idx, action, stop_flag)
                
            elif action['type'] in ['clipboard', 'copy', 'paste', 'cut', 'select_all']:
                self.action_handlers.handle_clipboard_action(idx, action)
                
            elif action['type'] in ['drag_start', 'drag_end', 'drag_drop', 'relative_drag_drop']:
                self.action_handlers.handle_drag_actions(idx, action)
            
            # After a click action, add a very short random delay.
            if action['type'] in ['click', 'double_click', 'ctrl_click', 'shift_click', 'relative_click', 'absolute_click']:
                self.random_delay(0.001, 0.01)  # Reduced from 0.01-0.05

            # After every successfully executed action, capture a screenshot of the entire screen
            try:
                # Mark action screenshots as temporal since they're used for debugging/verification
                # key_event chords are skipped: a screenshot between a key press
                # and its release would distort chord timing.
                if action['type'] != 'key_event':
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
