# Modularized Automation Player

This directory contains the refactored version of the original `player.py` script, broken down into logical modules for easier maintenance and development.

## Module Structure

### Core Modules

- **`config.py`** - Configuration and logging setup, OpenCV availability check
- **`computer_vision.py`** - Template matching and computer vision utilities
- **`base_bot.py`** - Base SeleniumBot class with core automation functionality
- **`action_handlers.py`** - Action execution handlers for different automation types
- **`sequence_player.py`** - Single sequence player for executing recorded actions
- **`multi_sequence_player.py`** - Multi-sequence player for chain execution
- **`main.py`** - Main entry point with command-line argument handling

### Entry Points

- **`__init__.py`** - Package initialization and exports
- **`player_standalone.py`** - Standalone script that mimics original player.py interface

## Usage

### As a Package
```python
from modularized import SequencePlayer, MultiSequencePlayer

# Play a single sequence
player = SequencePlayer("sequence.json")
player.play_sequence()

# Play a chain of sequences
with open("chain_config.json", 'r') as f:
    chain_config = json.load(f)
multi_player = MultiSequencePlayer(chain_config)
multi_player.play_chain()
```

### Command Line (Standalone)
```bash
# From the modularized directory
python player_standalone.py sequence.json
python player_standalone.py chain_config.json
```

### Command Line (Module)
```bash
# From the parent directory
python -m modularized.main sequence.json
python -m modularized.main chain_config.json
```

## Key Features Preserved

- All original functionality maintained
- Human-like mouse movements and delays
- Multi-scale template matching with OpenCV
- Fallback mechanisms for visual matching failures
- Screenshot capture and logging
- Support for various action types (click, type, keystroke, scroll, clipboard, drag)
- Chain execution with looping and conditional fallbacks
- Comprehensive error handling and logging

## Dependencies

- pyautogui
- pynput
- opencv-python (optional, falls back to pyautogui if not available)
- numpy
- pandas
- beautifulsoup4
- Standard library modules: json, time, os, sys, logging, hashlib, functools, datetime, argparse

## Migration from Original Script

The modularized version maintains 100% compatibility with the original `player.py` script. Simply replace calls to the original script with the standalone version or use the package interface for more advanced integration.