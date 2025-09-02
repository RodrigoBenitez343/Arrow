# Modularized LocalOperator Package
"""
This package contains the modularized version of the LocalOperator automation system.
"""

from .config import logger, CV2_AVAILABLE
from .base_bot import SeleniumBot
from .sequence_player import SequencePlayer
from .multi_sequence_player import MultiSequencePlayer
from .main import main

__all__ = [
    'logger',
    'CV2_AVAILABLE', 
    'SeleniumBot',
    'SequencePlayer',
    'MultiSequencePlayer',
    'main'
]