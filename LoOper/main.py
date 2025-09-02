#!/usr/bin/env python3
"""
LocalOperator - Main Entry Point

A powerful desktop automation tool that allows you to record, edit, and replay 
complex sequences of mouse clicks, keyboard inputs, scrolling, and drag-and-drop 
operations with visual template matching capabilities.

This is the main entry point that launches the GUI application.
"""

import sys
import os

# Add the current directory to the Python path to ensure proper imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from NGUI.app import main
from AI.model_cache import load_models_at_startup
from AI.config_loader import load_ai_config

if __name__ == "__main__":
    # Load AI configuration
    print("LocalOperator starting up...")
    print("Loading AI configuration...")
    load_ai_config()
    
    # Preload models asynchronously before starting the GUI
    load_models_at_startup()
    
    # Start the GUI application
    main()