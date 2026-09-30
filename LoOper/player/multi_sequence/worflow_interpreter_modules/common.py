import logging

# Chain-run logger — records propagate to the unified telemetry dispatch
# (logs/automation.log + logs/session_*.md); see logging_setup.py.  The old
# cwd-relative run.log writer was removed: it truncated the file on every
# import, destroying run history.
chain_logger = logging.getLogger("chain_run")
chain_logger.setLevel(logging.INFO)
