"""trace.py — one readable builder line to the log AND the panel.

The builder's reading trace (rounds, tool calls, digest sizes) must be
greppable in the terminal/session log AND visible in the builder chat.
``emit`` writes the prefixed line to this module's logger and mirrors it to
the panel's trace channel when one is given.

A failing trace channel is reported in the log, never dropped in silence.
"""

import logging

logger = logging.getLogger(__name__)

PREFIX = '[BUILDER] '


def emit(trace, message):
    """Log one builder line (prefixed) and mirror it to ``trace`` (bare)."""
    logger.info(PREFIX + str(message))
    if callable(trace):
        try:
            trace(str(message))
        except Exception:
            logger.exception('builder trace channel failed')
