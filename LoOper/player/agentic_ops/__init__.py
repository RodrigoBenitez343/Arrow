# agentic_ops/__init__.py
"""
agentic_ops — Simple LLM-based chain routing for LoOper.

Components:
  - SimpleChainRouter     — System-chain-driven agent: routes every query through a user-defined system chain.
                         No hardcoded behavior — the system chain is the agent's brain.
  - ChainExecutor         — Runs chain JSON files through MultiSequencePlayer
"""

from .simple_agent import SimpleChainRouter
from .chain_executor import ChainExecutor

__all__ = [
    "SimpleChainRouter",
    "ChainExecutor",
]
