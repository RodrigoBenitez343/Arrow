"""Node description fields (task #2) — the dialogs round-trip the text the
orchestrator routes node brains by.

These are the only automated checks over the GUI edits: each dialog is
constructed offscreen, and the description is proven to be read from the
config dict and returned by ``get_properties()`` / ``get_config()``.

Run:  python -m pytest LoOper/tests/test_node_description_dialogs.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


def test_sequence_dialog_roundtrips_the_description(qapp):
    from NGUI.dialogs.sequence_dialogs import SequencePropertiesDialog

    d = SequencePropertiesDialog(
        {"description": "Open the jobs page", "loop_count": 1})
    assert d.get_properties()["description"] == "Open the jobs page"

    # The dialog strips surrounding whitespace on the way out.
    d.description_input.setText("  trimmed text  ")
    assert d.get_properties()["description"] == "trimmed text"


def test_web_sequence_dialog_roundtrips_the_description(qapp):
    from NGUI.dialogs.web_sequence_dialogs import WebSequencePropertiesDialog

    d = WebSequencePropertiesDialog(
        {"description": "Search the job board", "session_file": "search.json"})
    assert d.get_properties()["description"] == "Search the job board"


def test_conditional_dialog_roundtrips_the_description(qapp):
    from NGUI.dialogs.conditional_dialogs import AdvancedConditionalDialog

    d = AdvancedConditionalDialog(
        None, 0, [], {"description": "Is the Apply button visible?"})
    assert d.get_config().get("description") == "Is the Apply button visible?"


def test_dialogs_default_to_empty_without_a_description(qapp):
    from NGUI.dialogs.sequence_dialogs import SequencePropertiesDialog
    from NGUI.dialogs.web_sequence_dialogs import WebSequencePropertiesDialog

    assert SequencePropertiesDialog({"loop_count": 1}).get_properties()["description"] == ""
    assert WebSequencePropertiesDialog({}).get_properties()["description"] == ""
