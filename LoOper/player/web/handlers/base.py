from __future__ import annotations

"""Base class for web replay action handlers.

A handler turns ONE recorded action type into real browser interaction: it
runs a deterministic ladder of interaction methods (trusted native CDP input
first, in-browser JS dispatch as fallback) and reports WHICH method worked, so
a replay log line reads "click OK via native" instead of requiring a debug
hunt to see what interaction method works.  Extend the architecture by adding
a handler class per action type and one registration line in
``handlers/__init__.py``.
"""
import logging
import time

from ..actions import ElementNotFoundError

logger = logging.getLogger(__name__)


class BaseHandler:
    """Common execute/report scaffolding for one action type."""

    type_name = "base"

    def _execute(self, driver, event, config) -> str:
        """Perform the action; return the method name used, raise on failure."""
        raise NotImplementedError

    def execute(self, driver, event, config) -> dict:
        """Dict-returning wrapper used by the replay engine (never raises)."""
        t0 = time.time()
        try:
            method = self._execute(driver, event, config)
            return {
                "ok": True,
                "method": method or self.type_name,
                "error": None,
                "took": round(time.time() - t0, 3),
            }
        except Exception as exc:
            return {
                "ok": False,
                "method": None,
                "error": str(exc),
                "took": round(time.time() - t0, 3),
            }

    @staticmethod
    def _ladder(steps):
        """Run ``(method_name, callable)`` steps in order; return the name of
        the first step that completes without raising.  All-fail propagates
        the LAST error so the loudest (usually most specific) message wins.
        """
        last_error: Exception | None = None
        for name, fn in steps:
            try:
                fn()
                return name
            except Exception as exc:  # step failed - try the next method
                last_error = exc
                logger.debug("action method '%s' failed: %s", name, exc)
        if last_error is not None:
            raise last_error
        raise ElementNotFoundError("no interaction method succeeded")
