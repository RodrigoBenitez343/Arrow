import logging
logger = logging.getLogger(__name__)
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
from .sequence_player import SequencePlayer
from .multi_sequence_player import MultiSequencePlayer, play_chain_with_tailcalls


def main():
    """
    Main function that handles command-line arguments and starts the appropriate player.
    """
    parser = argparse.ArgumentParser(description="Desktop Automation Player")
    parser.add_argument("file", help="Path to sequence file (.json) or chain config file (.json)")
    parser.add_argument("--sandbox", action="store_true", help="Run the chain in a sandboxed RDP session")
    parser.add_argument("--show-sandbox-window", action="store_true", default=True, 
                        help="Show the RDP sandbox window (for debugging). Default: True")
    parser.add_argument("--hide-sandbox-window", action="store_true",
                        help="Hide the RDP sandbox window (run in background)")
    args = parser.parse_args()

    # Clear the JSON cache to ensure we have the most updated version of all chains
    logger.info("Clearing JSON cache before execution")
    try:
        from .json_cache import clear_cache
        clear_cache()
        logger.info("JSON cache cleared successfully")
    except ImportError as e:
        logger.warning(f"Failed to import cache clearing function: {e}")
    except Exception as e:
        logger.warning(f"Failed to clear cache: {e}")

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
    
    logger.info("\n[START] Playback started. Press ESC to cancel.")
    logger.info("Keyboard listener started for ESC cancellation")

    # Build sandbox context from CLI arguments
    sandbox_context = None
    if args.sandbox:
        # If hide-sandbox-window is specified, show_window should be False
        show_window = not args.hide_sandbox_window
        sandbox_context = {
            "sandboxed": True,
            "show_window": show_window
        }
        logger.info(f"Sandbox mode enabled. Show window: {show_window}")

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
            _result, player = play_chain_with_tailcalls(
                args.file, stop_flag=stop_flag,
                player_setup_callback=(
                    (lambda p: setattr(p, 'global_app_context_override', sandbox_context))
                    if sandbox_context else None
                ),
            )
        elif isinstance(data, dict) and "sequences" in data:
            # It's a new chain config file (dict format with sequences and conditional_nodes)
            logger.info(f"Playing workflow chain configuration: {args.file}")
            _result, player = play_chain_with_tailcalls(
                args.file, stop_flag=stop_flag,
                player_setup_callback=(
                    (lambda p: setattr(p, 'global_app_context_override', sandbox_context))
                    if sandbox_context else None
                ),
            )
        else:
            logger.error("Invalid file format. Expected sequence file with 'actions', legacy chain config (list), or workflow chain config (dict with 'sequences').")
            sys.exit(1)
            
        if stop_requested.is_set():
            logger.info("\n[STOP] Playback cancelled by user")
        else:
            logger.info("\n[SUCCESS] Playback completed successfully")

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