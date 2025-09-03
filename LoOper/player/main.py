# main.py
"""
Main entry point for the automation player.
Handles command-line arguments and orchestrates sequence playback.
"""

import argparse
import json
import os
import sys
import threading
from pynput import keyboard
from .config import logger
from .sequence_player import SequencePlayer
from .multi_sequence_player import MultiSequencePlayer


def main():
    """
    Main function that handles command-line arguments and starts the appropriate player.
    """
    parser = argparse.ArgumentParser(description="Desktop Automation Player")
    parser.add_argument("file", help="Path to sequence file (.json) or chain config file (.json)")
    args = parser.parse_args()

    # Global stop flag for ESC key cancellation
    stop_requested = threading.Event()
    
    def stop_flag():
        """Returns True if playback should stop"""
        return stop_requested.is_set()
    
    def on_press(key):
        """Handle key press events"""
        if key == keyboard.Key.esc:
            logger.info("ESC key pressed - stopping playback")
            stop_requested.set()
            return False  # Stop listener
    
    # Start keyboard listener for ESC key
    keyboard_listener = keyboard.Listener(on_press=on_press)
    keyboard_listener.start()
    
    print("\n[START] Playback started. Press ESC to cancel.")
    logger.info("Keyboard listener started for ESC cancellation")

    try:
        # Check if the file exists
        if not os.path.exists(args.file):
            logger.error(f"File not found: {args.file}")
            sys.exit(1)

        # Load the file to determine its type
        with open(args.file, 'r') as f:
            data = json.load(f)

        # Determine if it's a sequence file or chain config
        if "actions" in data:
            # It's a sequence file
            logger.info(f"Playing single sequence: {args.file}")
            player = SequencePlayer(args.file)
            player.play_sequence(stop_flag=stop_flag)
        elif isinstance(data, list) and all("sequence_file" in item for item in data):
            # It's a legacy chain config file (list format)
            logger.info(f"Playing legacy chain configuration: {args.file}")
            player = MultiSequencePlayer(data, args.file)
            player.play_chain(stop_flag=stop_flag)
        elif isinstance(data, dict) and "sequences" in data:
            # It's a new chain config file (dict format with sequences and conditional_nodes)
            logger.info(f"Playing workflow chain configuration: {args.file}")
            player = MultiSequencePlayer(data, args.file)
            player.play_chain(stop_flag=stop_flag)
        else:
            logger.error("Invalid file format. Expected sequence file with 'actions', legacy chain config (list), or workflow chain config (dict with 'sequences').")
            sys.exit(1)
            
        if stop_requested.is_set():
            print("\n[STOP] Playback cancelled by user")
        else:
            print("\n[SUCCESS] Playback completed successfully")

    except FileNotFoundError:
        logger.error(f"File not found: {args.file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON format: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        sys.exit(1)
    finally:
        # Stop keyboard listener
        keyboard_listener.stop()


if __name__ == "__main__":
    main()