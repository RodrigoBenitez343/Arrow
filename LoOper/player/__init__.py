# player/__init__.py
"""
Modularized LocalOperator Package: recorder, player, NGUI and AI components.
"""

import logging
logger = logging.getLogger(__name__)

from .config import CV2_AVAILABLE
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