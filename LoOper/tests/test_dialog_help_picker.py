"""Tests for the dialog hover-help picker (the "?" button).

The "?" used to open the user guide.  It now starts a green picker that
outlines the hovered control and shows what it does and which values it
accepts; ESC or the "?" button again closes it.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QPoint  # noqa: E402
from PyQt5.QtWidgets import QApplication, QCheckBox, QComboBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from NGUI.dialogs.base_dialog import HoverHelp, ModernDialog  # noqa: E402


class _Dlg(ModernDialog):
    def __init__(self):
        super().__init__(None, title="Test")
        self.cb = QCheckBox("Write target: browser page")
        self.cb.setWhatsThis("ON: the value goes into the chain's browser.")
        self.content_layout.addWidget(self.cb)
        self.combo = QComboBox()
        self.combo.addItems(["web", "desktop"])
        self.content_layout.addWidget(self.combo)


def _dialog():
    dlg = _Dlg()
    dlg.resize(460, 400)
    dlg.show()
    _app.processEvents()
    return dlg


def test_question_mark_toggles_the_picker_and_escape_is_live_only_while_up():
    dlg = _dialog()

    assert dlg._help_overlay is None
    assert not dlg._help_esc.isEnabled()

    dlg.help_btn.click()
    assert dlg._help_overlay is not None
    assert dlg.help_btn.isChecked()
    assert dlg._help_esc.isEnabled()

    dlg.help_btn.click()
    assert dlg._help_overlay is None
    assert not dlg.help_btn.isChecked()
    assert not dlg._help_esc.isEnabled()


def test_escape_closes_the_picker_but_leaves_the_dialog_open():
    dlg = _dialog()
    dlg.help_btn.click()

    dlg._help_esc.activated.emit()

    assert dlg._help_overlay is None
    assert not dlg.help_btn.isChecked()
    assert dlg.isVisible()


def test_help_text_is_whats_this_plus_the_controls_own_options():
    dlg = _dialog()
    overlay = HoverHelp(dlg)

    assert overlay._help_for(dlg.cb) == (
        "ON: the value goes into the chain's browser.\n\nstates: ON / OFF"
    )
    assert overlay._help_for(dlg.combo) == "options: web, desktop"


def test_picker_hit_tests_the_control_under_the_point():
    dlg = _dialog()
    overlay = HoverHelp(dlg)
    center = dlg.cb.mapTo(dlg, QPoint(dlg.cb.width() // 2, dlg.cb.height() // 2))

    assert overlay._widget_at(dlg, center) is dlg.cb


def test_draw_outlines_the_target_in_overlay_coordinates():
    """The overlay is not an ancestor of the control, so the outline is taken
    in the dialog's space - mapping straight to the overlay lands elsewhere."""
    dlg = _dialog()
    overlay = HoverHelp(dlg)
    overlay.setGeometry(dlg.rect())

    overlay._draw(dlg.cb, "text")

    assert overlay._box.geometry().topLeft() == dlg.cb.mapTo(dlg, QPoint(0, 0))
    assert overlay._box.geometry().size() == dlg.cb.size()
    assert not overlay._box.isHidden()
    assert not overlay._panel.isHidden()
