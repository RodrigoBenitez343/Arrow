"""Code Node Studio - entry point.

The implementation lives in LoOper/player/code_agent_ops (constants,
parsing, execution, chat_stream, prompts and the panel/ mixin package);
this module only re-exports the public API so NGUI/widgets importers and
the -m self-check keep working unchanged.
"""
from player.code_agent_ops.panel.core import CodeNodePanel
from player.code_agent_ops.panel.core import _selfcheck


if __name__ == '__main__':
    _selfcheck()
