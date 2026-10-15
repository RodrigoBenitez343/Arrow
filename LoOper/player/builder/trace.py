"""trace.py — one readable builder line to the log AND the panel.

The builder's chatter (goal, Laya verdicts, chosen node types, stored
arguments, needle reasoning, questions) must be greppable in the terminal /
session md AND visible in the builder chat.  ``emit`` writes the prefixed
line to this module's logger and mirrors it to the panel's trace channel
when one is given.  Never raises — tracing must not break a build.
"""

import logging

logger = logging.getLogger(__name__)

PREFIX = '[BUILDER] '


def emit(trace, message):
    """Log one builder line (prefixed) and mirror it to ``trace`` (bare).

    The prefix marks the terminal/session log; the panel shows the plain
    line dimmed in the chat.  Never raises.
    """
    try:
        logger.info(PREFIX + str(message))
    except Exception:
        pass
    if callable(trace):
        try:
            trace(str(message))
        except Exception:
            pass
