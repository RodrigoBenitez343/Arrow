# keyboard_monitor.py
"""
Keyboard monitoring for continuous ESC key detection during playback operations.
Provides real-time abort functionality for automation sequences.
"""

import threading
import time
from pynput import keyboard
from .config import logger


class KeyboardMonitor:
    """
    Monitors keyboard input for ESC key presses to enable continuous abort functionality.
    Uses pynput to detect key presses in a separate thread without blocking main execution.
    """
    
    def __init__(self):
        self.stop_requested = False
        self.listener = None
        self.monitor_thread = None
        self._lock = threading.Lock()
        
    def start_monitoring(self):
        """
        Starts keyboard monitoring in a separate thread.
        Returns immediately while monitoring continues in background.
        """
        with self._lock:
            if self.monitor_thread and self.monitor_thread.is_alive():
                logger.warning("Keyboard monitoring is already active")
                return
                
            self.stop_requested = False
            logger.info("Starting ESC key monitoring for abort functionality")
            
            # Start monitoring in a separate daemon thread
            self.monitor_thread = threading.Thread(target=self._monitor_keyboard, daemon=True)
            self.monitor_thread.start()
    
    def stop_monitoring(self):
        """
        Stops keyboard monitoring and cleans up resources.
        """
        with self._lock:
            if self.listener:
                logger.info("Stopping ESC key monitoring")
                self.listener.stop()
                self.listener = None
            
            if self.monitor_thread and self.monitor_thread.is_alive():
                # Wait for monitor thread to finish
                self.monitor_thread.join(timeout=1.0)
                
    def is_stop_requested(self):
        """
        Returns True if ESC key was pressed and stop was requested.
        This method is thread-safe and can be called from any thread.
        
        Returns:
            bool: True if stop was requested, False otherwise
        """
        with self._lock:
            return self.stop_requested
    
    def reset_stop_flag(self):
        """
        Resets the stop flag to False.
        Useful when starting a new operation after a previous abort.
        """
        with self._lock:
            self.stop_requested = False
            logger.debug("Stop flag reset")
    
    def _monitor_keyboard(self):
        """
        Internal method that runs in a separate thread to monitor keyboard input.
        Sets up pynput keyboard listener and handles ESC key detection.
        """
        try:
            def on_key_press(key):
                try:
                    if key == keyboard.Key.esc:
                        with self._lock:
                            if not self.stop_requested:
                                self.stop_requested = True
                                logger.info("ESC key detected - stop requested for current operation")
                        return False  # Stop the listener
                except Exception as e:
                    logger.error(f"Error in keyboard event handler: {e}")
                    return False
            
            # Create and start the keyboard listener
            self.listener = keyboard.Listener(on_press=on_key_press)
            logger.debug("Keyboard listener created, starting monitoring")
            self.listener.start()
            self.listener.join()  # Block until listener stops
            
        except Exception as e:
            logger.error(f"Error in keyboard monitoring thread: {e}")
            import traceback
            logger.error(f"Keyboard monitoring traceback: {traceback.format_exc()}")
        finally:
            logger.debug("Keyboard monitoring thread finished")


# Global keyboard monitor instance for use across the application
_global_keyboard_monitor = None

def get_keyboard_monitor():
    """
    Returns the global keyboard monitor instance.
    Creates one if it doesn't exist.
    
    Returns:
        KeyboardMonitor: Global keyboard monitor instance
    """
    global _global_keyboard_monitor
    if _global_keyboard_monitor is None:
        _global_keyboard_monitor = KeyboardMonitor()
    return _global_keyboard_monitor

def start_global_monitoring():
    """
    Starts global keyboard monitoring for ESC key detection.
    This should be called at the beginning of playback operations.
    """
    monitor = get_keyboard_monitor()
    monitor.reset_stop_flag()  # Reset any previous stop requests
    monitor.start_monitoring()

def stop_global_monitoring():
    """
    Stops global keyboard monitoring and cleans up resources.
    This should be called at the end of playback operations.
    """
    monitor = get_keyboard_monitor()
    monitor.stop_monitoring()

def create_stop_flag():
    """
    Creates a stop flag function that can be used with existing playback methods.
    The returned function checks if ESC key was pressed.
    
    Returns:
        callable: Function that returns True if stop was requested, False otherwise
    """
    monitor = get_keyboard_monitor()
    return monitor.is_stop_requested