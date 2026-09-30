"""LLM dialog layout: 5 consolidated tabs and external-input-only toggles.

Pins the reworked dialog: connection-driven input toggles (previous node /
Input node) are gone — a connection into the input/context port already feeds
the node — five coherent categories exist, and get_config / load round-trip
the remaining external readers.  If the input mapping drifts, a saved
OCR / page-text / clipboard node silently falls back to none.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from NGUI.dialogs.llm_dialogs import LLMPropertiesDialog  # noqa: E402


def _make(config=None):
    return LLMPropertiesDialog(None, current_config=config or {})


def test_dialog_has_five_consolidated_tabs():
    dlg = _make()
    labels = [dlg.tab_widget.tabText(i) for i in range(dlg.tab_widget.count())]
    assert labels == ["Model", "Prompt", "Output", "Knowledge", "Tools & Skills"]


def test_external_input_offers_only_non_connection_readers():
    dlg = _make()
    sources = {dlg.input_source_group.id(btn) for btn in dlg.input_source_group.buttons()}
    assert sources == {0, 1, 2, 3}
    for removed in ("input_previous_radio", "input_node_radio"):
        assert not hasattr(dlg, removed)


def test_input_source_round_trips_ocr_page_text_clipboard():
    for name, radio in (
        ("ocr", "input_ocr_radio"),
        ("page_text", "input_page_text_radio"),
        ("clipboard", "input_clipboard_radio"),
    ):
        dlg = _make()
        getattr(dlg, radio).setChecked(True)
        assert dlg.get_config()["input_source"] == name


def test_legacy_previous_loads_as_none():
    dlg = _make({"input_source": "previous"})
    assert dlg.input_none_radio.isChecked()
    assert dlg.get_config()["input_source"] == "none"


def test_tab_widget_reports_the_current_page_not_the_tallest():
    """A plain QTabWidget pins every tab to the tallest page's height, which
    is what left dead space under the short tabs.  The shrink tab widget must
    report each page's own height instead."""
    dlg = _make()
    heights = []
    for i in range(dlg.tab_widget.count()):
        dlg.tab_widget.setCurrentIndex(i)
        heights.append(dlg.tab_widget.sizeHint().height())
    assert len(set(heights)) > 1  # not every tab pinned to the same height
    assert heights[2] < max(heights)  # the short Output tab is not the tallest


def test_content_widget_is_not_stretched_to_the_tallest_page():
    """The scroll area must not stretch its content to the tallest page: doing
    so stretched the tab widget too and left a block of vertical dead space
    under every short tab."""
    dlg = _make()
    dlg.tab_widget.setCurrentIndex(4)  # Tools & Skills (tallest)
    dlg._refit_to_current_tab()
    dlg._apply_fit()
    tall = dlg.content_widget.height()

    dlg.tab_widget.setCurrentIndex(2)  # Output (short)
    dlg._refit_to_current_tab()
    dlg._apply_fit()
    short = dlg.content_widget.height()

    assert short == dlg.content_layout.sizeHint().height()
    assert short < tall
