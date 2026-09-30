# __init__.py
"""
Multi-sequence automation module.
Provides modular components for complex workflow automation.
"""

from .conditional_fallback_handler import ConditionalFallbackHandler
from .workflow_graph import WorkflowGraphBuilder, WorkflowNavigator, WorkflowExecutor
from .llm_executor import LLMExecutor
from .sequence_executor import SequenceExecutor

__all__ = [
    'ConditionalFallbackHandler',
    'WorkflowGraphBuilder',
    'WorkflowNavigator',
    'LLMExecutor',
    'SequenceExecutor'
]

__version__ = '1.0.0'
__author__ = 'LoOper Team'
__description__ = 'Modular multi-sequence automation framework'