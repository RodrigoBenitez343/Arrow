# sequence_player.py
"""
Single sequence player for executing recorded automation sequences.
"""

import json
import time
from .config import logger
from .base_bot import SeleniumBot


class SequencePlayer(SeleniumBot):
    """
    Plays back a sequence of recorded desktop actions from a JSON file.
    """
    def __init__(self, sequence_file):
        """
        Initializes the player.

        Args:
            sequence_file (str): The path to the JSON file containing the actions.
        """
        super().__init__()
        self.sequence_data = self.load_sequence(sequence_file)
        self.running = True
        self.loop_counter = 0
        logger.info(f"Loaded sequence for playback: {sequence_file}")

    def load_sequence(self, filename):
        """
        Loads and validates the action sequence from a JSON file.

        Args:
            filename (str): The path to the JSON file.
        
        Returns:
            dict: The parsed JSON data.
        """
        with open(filename, 'r') as f:
            data = json.load(f)
        if "actions" not in data:
            raise ValueError("Invalid sequence format: Missing actions")
        logger.info(f"Sequence contains {len(data['actions'])} actions")
        return data

    def play_sequence(self, fallback_callback=None, stop_flag=None):
        """
        Iterates through the loaded sequence and executes each action.
        
        Args:
            fallback_callback (callable): Optional callback to trigger when visual matching fails.
            stop_flag (callable): Optional function that returns True when playback should stop.
        """
        logger.info("Starting desktop playback")
        logger.debug(f"Sequence contains {len(self.sequence_data['actions'])} actions")
        
        time.sleep(2) # Give the user time to switch to the target window.
        self.last_action_time = time.time()
        
        # Loop through each action in the sequence.
        for idx, action in enumerate(self.sequence_data['actions']):
            # Check stop flag before each action
            if stop_flag and stop_flag():
                logger.info("Playback stopped by user request")
                return
                
            logger.debug(f"Executing action {idx + 1}/{len(self.sequence_data['actions'])}: {action.get('type', 'unknown')}")
            
            try:
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
                
        logger.info("Playback completed")