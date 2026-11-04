"""Form Filling node, split into single-responsibility modules.

The node was one ~2000-line module; it is now eight focused ones:

* :mod:`.common`   - shared constants, text helpers, the embedding client
* :mod:`.config`   - node config parsing and graph helpers
* :mod:`.fields`   - field identity: selectors, choice merging, filtering
* :mod:`.crag`     - per-field retrieval through the CRAG pipeline
* :mod:`.extract`  - one value for one field, and the engine calls
* :mod:`.web`      - the web substrate (enumerate / write / read / overlay)
* :mod:`.tree`     - Laya-first element resolution (the page tree + its walk)
* :mod:`.repair`   - the post-pass (validity, content problems, corrections)
* :mod:`.desktop`  - the desktop substrate (UIA field scan + the Handle actuator)
* :mod:`.node`     - the node shell (the field loop and its output)

``FormFillerMixin`` composes them, so the runtime keeps importing one name.
"""
from .common import (
    _FF_PLAIN_RULE,
    _FFEmbedClient,
    _as_bool,
    _as_float,
    _as_int,
    _ff_finding_lines,
    _ff_plain_text,
    _split_list,
    _strip_fences,
    _strip_think,
    log_block,
    log_table,
    logger,
)
from .config import FormFillerConfigMixin
from .crag import FormFillerCragMixin
from .desktop import FormFillerDesktopMixin
from .extract import FormFillerExtractMixin
from .fields import FormFillerFieldsMixin
from .node import FormFillerNodeMixin
from .repair import FormFillerRepairMixin
from .tree import FormFillerTreeMixin
from .web import FormFillerWebMixin


class FormFillerMixin(
    FormFillerConfigMixin,
    FormFillerFieldsMixin,
    FormFillerCragMixin,
    FormFillerExtractMixin,
    FormFillerWebMixin,
    FormFillerTreeMixin,
    FormFillerRepairMixin,
    FormFillerDesktopMixin,
    FormFillerNodeMixin,
):
    """Fill one page of a form: enumerate fields, then fill them one by one."""


__all__ = [
    "FormFillerMixin",
    "FormFillerConfigMixin",
    "FormFillerFieldsMixin",
    "FormFillerCragMixin",
    "FormFillerExtractMixin",
    "FormFillerWebMixin",
    "FormFillerTreeMixin",
    "FormFillerRepairMixin",
    "FormFillerDesktopMixin",
    "FormFillerNodeMixin",
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
