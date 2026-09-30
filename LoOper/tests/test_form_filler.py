"""Collector shim for the form-filler suite.

The suite moved next to the code it covers and got split by concern:

    .../executor_modules/form_filler_modules/
        _test_support.py    shared _Harness / _node / _wire
        suite_ask_user.py   the opt-in ask-user fallback + knowledge file
        suite_common.py     config, helpers, field identity, filters
        suite_extract.py    extraction prompt, sentinels, yes/no
        suite_fill_flow.py  the node loop (fill / verify / skip / abort)
        suite_scope.py      picked-scope resolution
        suite_probe.py      retrieval, ComoRAG hooks, facts vs verbatim
        suite_repair.py     the post-pass
        suite_web.py        JS contracts, writes, the overlay
        suite_dialog.py     dialog + chain save/load
        suite_wiring.py     builder + executor wiring

The suite modules are deliberately NOT named ``test_*.py``: a bare ``pytest``
from ``LoOper/`` would otherwise collect them twice (once through the package,
once through this shim).  Importing them here keeps ``pytest tests`` complete.
"""
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_ask_user import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_common import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_dialog import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_extract import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_fill_flow import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_probe import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_repair import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_scope import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_web import *  # noqa: F401,F403
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    form_filler_modules.suite_wiring import *  # noqa: F401,F403
