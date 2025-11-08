# llm_executor.py
"""
Entry point for LLM node execution (modularized).
This file remains for backward compatibility and re-exports the executor.
"""

from .llm_executor_resources import LLMExecutor, get_default_api_url

__all__ = [
    "LLMExecutor",
    "get_default_api_url",
]