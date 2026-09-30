import logging
logger = logging.getLogger(__name__)
# sequence_player.py
"""
Single sequence player for executing recorded automation sequences.
"""

import json
import time
import os
from threading import Lock
from .base_bot import SeleniumBot


class SequenceCache:
    """
    Thread-safe cache for sequence data to avoid repeated file loading and parsing.
    """
    def __init__(self, max_size=100):
        self._cache = {}
        self._file_timestamps = {}
        self._max_size = max_size
        self._lock = Lock()
    
    def get(self, filename):
        """
        Get cached sequence data if available and file hasn't changed.
        
        Args:
            filename (str): Path to the sequence file
            
        Returns:
            dict or None: Cached sequence data or None if not cached/outdated
        """
        with self._lock:
            if filename not in self._cache:
                return None
            
            # Check if file has been modified since caching
            try:
                current_mtime = os.path.getmtime(filename)
                cached_mtime = self._file_timestamps.get(filename, 0)
                
                if current_mtime != cached_mtime:
                    # File has been modified, remove from cache
                    del self._cache[filename]
                    del self._file_timestamps[filename]
                    return None
                
                return self._cache[filename]
            except (OSError, IOError):
                # File doesn't exist or can't be accessed
                if filename in self._cache:
                    del self._cache[filename]
                if filename in self._file_timestamps:
                    del self._file_timestamps[filename]
                return None
    
    def put(self, filename, data):
        """
        Cache sequence data with file timestamp tracking.
        
        Args:
            filename (str): Path to the sequence file
            data (dict): Sequence data to cache
        """
        with self._lock:
            # Implement LRU eviction if cache is full
            if len(self._cache) >= self._max_size and filename not in self._cache:
                # Remove oldest entry (simple FIFO for now)
                oldest_file = next(iter(self._cache))
                del self._cache[oldest_file]
                if oldest_file in self._file_timestamps:
                    del self._file_timestamps[oldest_file]
            
            try:
                self._cache[filename] = data
                self._file_timestamps[filename] = os.path.getmtime(filename)
            except (OSError, IOError):
                # If we can't get file timestamp, still cache but without timestamp tracking
                self._cache[filename] = data
    
    def clear(self):
        """Clear all cached data."""
        with self._lock:
            self._cache.clear()
            self._file_timestamps.clear()
    
    def size(self):
        """Get current cache size."""
        with self._lock:
            return len(self._cache)


# Global sequence cache instance
_sequence_cache = SequenceCache()


def _fast_json_load(filename):
    """
    Optimized JSON loading function with better performance.
    
    Args:
        filename (str): Path to the JSON file
        
    Returns:
        dict: Parsed JSON data
    """
    try:
        # Get file size for buffer optimization
        file_size = os.path.getsize(filename)
        
        # Use optimized buffer size based on file size
        if file_size < 1024:  # Small files (< 1KB)
            buffer_size = -1  # Use default buffering
        elif file_size < 10240:  # Medium files (< 10KB)
            buffer_size = 4096
        else:  # Large files
            buffer_size = 8192
        
        # Read file with optimized buffer
        with open(filename, 'r', encoding='utf-8', buffering=buffer_size) as f:
            # For small files, read all at once
            if file_size < 10240:
                content = f.read()
                return json.loads(content)
            else:
                # For larger files, use streaming JSON parser
                return json.load(f)
                
    except Exception as e:
        # Fallback to standard method
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)


class SequencePlayer(SeleniumBot):
    """
    Plays back a sequence of recorded desktop actions from a JSON file.
    """
    def __init__(self, sequence_file, lazy_load=False):
        """
        Initializes the player with optimized loading.

        Args:
            sequence_file (str): The path to the JSON file containing the actions.
            lazy_load (bool): If True, defer sequence loading until play_sequence is called.
        """
        super().__init__()
        self.sequence_file = sequence_file
        self.sequence_data = None
        self.running = True
        self.loop_counter = 0
        self.last_app_context = None
        
        # Load sequence immediately unless lazy loading is enabled
        if not lazy_load:
            self.sequence_data = self.load_sequence(sequence_file)
            logger.info(f"Loaded sequence for playback: {sequence_file}")
        else:
            logger.debug(f"Sequence player initialized with lazy loading: {sequence_file}")

    def load_sequence(self, filename):
        """
        Loads and validates the action sequence from a JSON file with caching and optimized loading.
        Resolves hash-referenced images (screenshot_hash -> screenshot_data) for player compatibility.

        Args:
            filename (str): The path to the JSON file.
        
        Returns:
            dict: The parsed JSON data.
        """
        # Try to get from cache first
        cached_data = _sequence_cache.get(filename)
        if cached_data is not None:
            logger.debug(f"Using cached sequence data for: {filename}")
            return cached_data
        
        # Load from file if not cached using optimized JSON loading
        try:
            data = _fast_json_load(filename)
            
            if "actions" not in data:
                raise ValueError("Invalid sequence format: Missing actions")
            
            # Resolve hash-referenced images (screenshot_hash -> screenshot_data)
            images_map = data.get('images', {})
            if images_map:
                resolved_count = 0
                for action in data['actions']:
                    if isinstance(action, dict) and action.get('screenshot_hash'):
                        data_hash = action['screenshot_hash']
                        if data_hash in images_map:
                            action['screenshot_data'] = images_map[data_hash]
                            del action['screenshot_hash']
                            resolved_count += 1
                if resolved_count:
                    logger.debug(f"Resolved {resolved_count} hash-referenced screenshots")
            
            # Cache the loaded data
            _sequence_cache.put(filename, data)
            logger.info(f"Sequence loaded and cached: {len(data['actions'])} actions from {filename}")
            return data
            
        except Exception as e:
            logger.error(f"Failed to load sequence from {filename}: {e}")
            raise

    def _ensure_sequence_loaded(self):
        """
        Ensures sequence data is loaded (for lazy loading support).
        """
        if self.sequence_data is None:
            self.sequence_data = self.load_sequence(self.sequence_file)
            logger.info(f"Lazy-loaded sequence for playback: {self.sequence_file}")

    def play_sequence(self, fallback_callback=None, stop_flag=None, skip_initial_delay=False):
        """
        Iterates through the loaded sequence and executes each action.
        
        Args:
            fallback_callback (callable): Optional callback to trigger when visual matching fails.
            stop_flag (callable): Optional function that returns True when playback should stop.
            skip_initial_delay (bool): If True, skip the initial 2-second delay for faster execution.
        """
        # Ensure sequence is loaded (for lazy loading)
        self._ensure_sequence_loaded()
        
        logger.info("Starting desktop playback")
        logger.debug(f"Sequence contains {len(self.sequence_data['actions'])} actions")
        
        # Reduced initial delay for faster execution
        if not skip_initial_delay:
            time.sleep(0.5)  # Reduced from 2 seconds to 0.5 seconds
        
        self.last_action_time = time.time()
        
        try:
            # Loop through each action in the sequence.
            for idx, action in enumerate(self.sequence_data['actions']):
                # Check stop flag before each action
                if stop_flag and stop_flag():
                    logger.info("Playback stopped by user request")
                    return
                    
                logger.debug(f"Executing action {idx + 1}/{len(self.sequence_data['actions'])}: {action.get('type', 'unknown')}")
                
                try:
                    try:
                        if isinstance(action, dict) and action.get("app_context"):
                            self.last_app_context = action.get("app_context")
                    except Exception:
                        pass
                    # Use the execute_with_timing method from the base class.
                    success = self.execute_with_timing(idx, action, fallback_callback, stop_flag)
                    logger.debug(f"Action {idx} execution result: {success}")
                    
                    if not success:
                        logger.info(f"Fallback triggered for action {idx}. Stopping current sequence.")
                        return  # Exit current sequence to allow fallback to run
                except Exception as e:
                    logger.error(f"Action {idx} failed: {str(e)}")
                    import traceback
                    logger.error(f"Full traceback: {traceback.format_exc()}")
                    break # Stop playback on failure.
        finally:
            # Release any keys still held by key_event replay (stuck-key safety)
            # so a stopped/aborted playback never leaves modifiers stuck down.
            try:
                self.action_handlers.release_all_keys()
            except Exception:
                pass
                
        logger.info("Playback completed")
