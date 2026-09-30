import logging
logger = logging.getLogger(__name__)
# keyboard_monitor.py
"""
Keyboard monitoring for continuous ESC key detection during playback operations.
Provides real-time abort functionality for automation sequences.
"""

import threading
import time
from pynput import keyboard


class KeyboardMonitor:
    """
    Monitors keyboard input for ESC key presses to enable continuous abort functionality.
    Uses pynput to detect key presses in a separate thread without blocking main execution.
    """
    
    def __init__(self):
        self.stop_requested = False
        self.stop_event = threading.Event()  # threading.Event for integration with TTS, etc.
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
            self.stop_event.clear()
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
                self.monitor_thread = None
                
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
            self.stop_event.clear()
            logger.debug("Stop flag reset")
    
    @staticmethod
    def _stop_tts_audio():
        """Immediately stop any playing TTS audio output."""
        try:
            import sys
            if sys.platform == "win32":
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
                logger.debug("TTS audio purged via winsound")
        except Exception as e:
            logger.debug(f"Failed to stop TTS audio: {e}")

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
                                self.stop_event.set()
                                logger.info("ESC key detected - stop requested for current operation")
                                # Immediately stop any playing TTS audio
                                self._stop_tts_audio()
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

# Holder tracking for concurrent top-level executions.  The keyboard monitor
# is a process-wide singleton: overlapping executions must NOT reset the stop
# flag (that would cancel another execution's in-flight abort) and must NOT
# stop the shared listener while another execution still needs ESC.  Tracked
# per thread so double-stops (finally + error handler) and stop-without-start
# are idempotent no-ops.
_monitor_lock = threading.Lock()
_active_monitors = set()  # thread identifiers currently holding the monitor

def start_global_monitoring():
    """
    Starts global keyboard monitoring for ESC key detection.
    This should be called at the beginning of playback operations.

    Every NEW execution starts with a clean stop flag — a stale flag left by
    a previous ESC must never abort a fresh run (the playback initial-delay
    check would kill it before it starts).  The shared listener is still
    only started once: start_monitoring self-guards via the monitor thread's
    liveness, and a dead listener (it stops itself on ESC) is restarted for
    the next execution.
    """
    monitor = get_keyboard_monitor()
    _tid = threading.get_ident()
    with _monitor_lock:
        if _tid in _active_monitors:
            return  # this thread already holds the monitor (re-entrant)
        monitor.reset_stop_flag()  # fresh execution = fresh flag
        monitor.start_monitoring()  # self-guards via monitor_thread.is_alive()
        _active_monitors.add(_tid)

def stop_global_monitoring():
    """
    Stops global keyboard monitoring and cleans up resources.
    This should be called at the end of playback operations.

    Holder-tracked: the shared listener is only stopped once the LAST active
    execution finishes, so it never dies while another execution still needs
    ESC cancellation.  Extra/mismatched stops are idempotent no-ops.
    """
    monitor = get_keyboard_monitor()
    _tid = threading.get_ident()
    with _monitor_lock:
        if _tid not in _active_monitors:
            return  # no matching start in this thread — idempotent no-op
        _active_monitors.discard(_tid)
        if not _active_monitors:
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

def get_stop_event() -> threading.Event:
    """
    Returns the threading.Event that is set when ESC is pressed.
    Useful for passing to play_wav(stop_event=...) or similar APIs.

    Returns:
        threading.Event: Event that is set on ESC key press
    """
    monitor = get_keyboard_monitor()
    return monitor.stop_event