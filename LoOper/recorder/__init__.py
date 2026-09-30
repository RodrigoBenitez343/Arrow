# Modularized recorder package
"""
This package contains the modularized version of the LocalOperator recorder system.
"""

from .element_recorder import ElementRecorder
from .sequence_manager import SequenceManager
from .screenshot_manager import ScreenshotManager
from .scroll_manager import ScrollManager
from .keyboard_handler import KeyboardHandler
from .mouse_handler import MouseHandler
from .main import start_recording

__all__ = [
    'ElementRecorder',
    'SequenceManager', 
    'ScreenshotManager',
    'ScrollManager',
    'KeyboardHandler',
    'MouseHandler',
    'start_recording'
]