# agentic_ops/__init__.py
"""
agentic_ops — agent mode: workspaces, the chain loop, routing, execution.

Components:
  - SimpleChainRouter     — binds agent mode to one WORKSPACE (one agent plus
                            its own chains folder) and runs the request.
  - WorkspaceAgent        — the LOOP: recall → reason → pick → run → observe →
                            freeze (agent.py). The model names an intent, Laya
                            picks the tool, Python runs and records.
  - catalog               — one entry per chain, from the chain's own flow
                            metadata: what it does, what it needs, what it returns.
  - plans                 — the episodes the agent froze: goal → [chain, why],
                            outcomes, transitions, ask/report counts, and the
                            similarity recall that feeds the reasoner.
  - ChainExecutor         — Runs chain JSON files through MultiSequencePlayer

The old "system chain" (``collection='System'``) is retired; a workspace whose
``workspace.json`` pins ``agent.legacy_brain`` still answers with that chain.
"""

from .simple_agent import SimpleChainRouter
from .chain_executor import ChainExecutor
from .agent import AgentError, WorkspaceAgent

__all__ = [
    "SimpleChainRouter",
    "ChainExecutor",
    "WorkspaceAgent",
    "AgentError",
]
