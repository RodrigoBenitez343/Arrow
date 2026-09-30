"""Code Node Studio agent machinery.

The panel is a flat little-coder-style coding agent: a lean system prompt, a
small mechanical editing protocol (full-block writes, SEARCH/REPLACE hunks,
### GREP, ### NEED FILE), an auto-run/fix loop bounded per request, and
small-model safeguards (bounded history, clipped context, failure-adaptive
sampler).  There is no spec/ladder scaffold.
"""
from player.code_agent_ops.panel.core import CodeNodePanel
from player.code_agent_ops.panel.core import _selfcheck

__all__ = ['CodeNodePanel', '_selfcheck']
