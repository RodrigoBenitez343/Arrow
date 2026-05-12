# agentic_ops/__init__.py
"""
Agentic operations: visual agent loop for desktop automation.

Key components:
  - ChatController     — UI-facing entry point
  - VisualAgent        — observe-decide-act loop
  - ScreenObserver     — LFM vision interface
  - DecisionEngine     — LLM-based action selection
  - ChainRegistry      — scans and describes chain tools
  - ChainRunner        — executes chain JSON files
  - LearningStore      — persistent memory (SQLite + optional Qdrant)
  - ChatLogger         — session logging
  - TypingAssistant    — keyboard text input
  - ActionExecutor     — low-level desktop action executor
  - ChainBuilder       — creates chain JSON files from recorded actions
"""

from .action_executor import ActionExecutor
from .chain_builder import ChainBuilder
from .chain_registry import ChainRegistry
from .chain_runner import ChainRunner
from .chat_controller import ChatController
from .chat_logger import ChatLogger
from .decision_engine import Decision, DecisionEngine
from .learning_store import LearningStore
from .screen_observer import ScreenObserver
from .typing_ops import TypingAssistant
from .visual_agent import VisualAgent

__all__ = [
    "ActionExecutor",
    "ChainBuilder",
    "ChainRegistry",
    "ChainRunner",
    "ChatController",
    "ChatLogger",
    "Decision",
    "DecisionEngine",
    "LearningStore",
    "ScreenObserver",
    "TypingAssistant",
    "VisualAgent",
]
