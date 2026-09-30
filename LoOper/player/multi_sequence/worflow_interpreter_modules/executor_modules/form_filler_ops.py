"""Back-compat shim for the Form Filling node.

The implementation was one ~2000-line module and now lives in individual
single-responsibility modules under
:mod:`...executor_modules.form_filler_modules` (common / config / fields /
probe / extract / web / repair / desktop / node).

This module re-exports them so the existing imports keep working:

    from .form_filler_ops import FormFillerMixin

Behaviour is unchanged: ``FormFillerMixin`` composes the same methods.
"""
from .form_filler_modules import (  # noqa: F401
    FormFillerMixin,
    _FFEmbedClient,
    _as_bool,
    _as_float,
    _as_int,
    _ff_finding_lines,
    _split_list,
    _strip_fences,
    _strip_think,
    log_block,
    log_table,
    logger,
)

__all__ = [
    "FormFillerMixin",
    "_FFEmbedClient",
    "_as_bool",
    "_as_float",
    "_as_int",
    "_ff_finding_lines",
    "_split_list",
    "_strip_fences",
    "_strip_think",
    "log_block",
    "log_table",
    "logger",
]
