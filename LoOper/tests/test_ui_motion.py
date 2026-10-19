"""Motion contract for the agent surfaces: MORPH, don't vanish.

The invariants the morph helpers must never break:
  * a morph actually travels (size interpolates), and hands the widget's own
    min/max back to the layout on landing (Qt never re-grows a widget whose
    maximum was left lowered, and a lost fixed height pins the panel small);
  * an animation lands its end state EXACTLY once, and a superseded one lands
    never (its callback used to hide/swap widgets that the newer request won);
  * the screen-capture paths stay instant.

Run:  python -m pytest LoOper/tests/test_ui_motion.py
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QRect  # noqa: E402
from PyQt5.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget  # noqa: E402

from NGUI.widgets import ui_motion  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _force_motion(monkeypatch):
    """Headless Qt gets no motion by default (see ui_motion._HEADLESS); these
    tests exist to measure the travel, so they switch it back on."""
    monkeypatch.setattr(ui_motion, "_FORCE", True)


def _pump(ms):
    """Drive the event loop so the morphs actually run."""
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


@pytest.fixture
def column(app):
    """A host with two stretch-1 views, the shape of the overlay's chat column."""
    host = QWidget()
    lay = QHBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    outgoing = QLabel("outgoing view")
    incoming = QLabel("incoming view")
    lay.addWidget(outgoing, 1)
    lay.addWidget(incoming, 1)
    host.resize(600, 300)
    host.show()
    incoming.hide()
    _pump(50)
    yield incoming, outgoing
    host.deleteLater()


def test_unfold_travels_and_hands_the_size_back(column):
    incoming, _ = column
    orig_max, orig_min = incoming.maximumHeight(), incoming.minimumHeight()
    ui_motion.unfold(incoming, start_px=8, axis="x")
    _pump(40)
    mid = incoming.width()
    _pump(500)
    assert mid > 8 and incoming.width() > mid          # it grew, in steps
    assert incoming.maximumWidth() == orig_max          # the layout owns it again
    assert incoming.minimumWidth() == orig_min


def test_fold_away_travels_then_lands_once(column):
    incoming, outgoing = column
    fired = []
    ui_motion.fold_away(outgoing, end_px=8, axis="x",
                        on_done=lambda: fired.append(1))
    _pump(40)
    mid = outgoing.width()
    _pump(500)
    assert mid < 600 and len(fired) == 1
    assert outgoing.maximumWidth() == 16777215


def test_superseded_morph_never_runs_its_end_state(column):
    """Two requests on the SAME widget (a rapid toggle): only the latest lands.

    The older callback used to hide/swap the widget the newer request won, which
    is how a panel could vanish behind the user's hand.
    """
    incoming, _ = column
    incoming.show()
    _pump(40)
    calls = []
    ui_motion.fold_away(incoming, end_px=8, axis="x",
                        on_done=lambda: calls.append("fold"))
    ui_motion.unfold(incoming, start_px=8, axis="x",
                     on_done=lambda: calls.append("unfold"))
    _pump(700)
    assert calls == ["unfold"]
    assert not incoming.isHidden()


def test_fixed_height_survives_a_morph_round_trip(column):
    """The server panel is pinned to its own height — a morph must not lose it."""
    incoming, _ = column
    incoming.setFixedHeight(120)
    _pump(30)
    ui_motion.unfold(incoming, start_px=8, target_px=120)
    _pump(500)
    assert (incoming.minimumHeight(), incoming.maximumHeight()) == (120, 120)
    ui_motion.fold_away(incoming, end_px=8, on_done=incoming.hide)
    _pump(500)
    assert incoming.isHidden()
    assert (incoming.minimumHeight(), incoming.maximumHeight()) == (120, 120)


def test_geometry_morph_lands_once_on_target(app):
    w = QWidget()
    w.resize(200, 100)
    w.show()
    target = QRect(40, 50, 200, 160)
    start = QRect(60, 50, 60, 160)
    fired = []
    ui_motion.morph_from(w, start, target, on_done=lambda: fired.append(1))
    _pump(30)
    mid = w.width()
    _pump(500)
    assert start.width() < mid < target.width()        # width interpolated too
    assert w.geometry() == target and len(fired) == 1
    w.deleteLater()


def test_kill_switch_skips_the_morph(app, column):
    incoming, outgoing = column
    incoming.hide()
    ui_motion._DISABLED = True
    try:
        fired = []
        ui_motion.unfold(incoming, start_px=8, axis="x",
                         on_done=lambda: fired.append(1))
        assert fired == [1] and not incoming.isHidden()
    finally:
        ui_motion._DISABLED = False
    assert ui_motion.motion_enabled() is True


def test_fade_fallback_still_lands_once(app):
    """The opacity helpers stay for surfaces with nothing to resize."""
    w = QWidget()
    w.resize(120, 60)
    w.show()
    fired = []
    ui_motion.fade_away(w, on_done=lambda: fired.append(1))
    _pump(600)
    assert len(fired) == 1
    assert w.graphicsEffect() is None          # no lingering effect
    w.deleteLater()


def test_window_morph_travels_and_lands_on_target(app):
    """A dialog arrives by growing into its rect, not by popping in."""
    dlg = QWidget()
    dlg.resize(600, 400)
    dlg.show()
    _pump(30)
    target = dlg.geometry()
    orig_min_w = dlg.minimumWidth()
    ui_motion.morph_window(dlg)
    _pump(30)
    early = dlg.geometry()
    _pump(600)
    assert early.width() < target.width() and early.height() < target.height()
    assert dlg.geometry() == target          # lands exactly on the real rect
    assert dlg.minimumWidth() == orig_min_w  # limits handed back
    dlg.deleteLater()


def test_window_morph_yields_to_a_refit(app):
    """A dialog that re-fits mid-morph keeps its own geometry (no snap-back)."""
    dlg = QWidget()
    dlg.resize(600, 400)
    dlg.show()
    _pump(30)
    ui_motion.morph_window(dlg)
    _pump(40)
    refit = QRect(80, 90, 520, 360)            # as if the content grew
    dlg.setGeometry(refit)
    _pump(600)
    assert dlg.geometry() == refit
    dlg.deleteLater()


def test_modern_dialog_morphs_on_show(app):
    """Every config dialog inherits this: it grows into its fitted rect."""
    from NGUI.dialogs.base_dialog import ModernDialog

    dlg = ModernDialog(None, title="motion probe")
    dlg.show()
    _pump(30)
    early_w = dlg.width()
    _pump(700)
    assert early_w < dlg.width()               # it travelled
    assert dlg.minimumWidth() > 0              # the dialog keeps its floor
    dlg.hide()
    dlg.deleteLater()


def test_notch_morphs_out_of_a_pill_and_back(app):
    from NGUI.widgets.agent_notch import AgentNotch, NOTCH_GLOW, NOTCH_WIDTH

    expect = NOTCH_WIDTH + 2 * NOTCH_GLOW
    n = AgentNotch()
    n.summon()
    _pump(40)
    mid = n.width()
    _pump(600)
    assert 8 < mid < expect and n.width() == expect
    n.retreat()
    _pump(600)
    assert n.isHidden()
    assert n.minimumWidth() == expect           # repinned, not left relaxed
    n.deleteLater()


def test_notch_capture_hide_stays_instant(app):
    """The capture path must not go through motion at all."""
    from NGUI.widgets.agent_notch import AgentNotch

    n = AgentNotch()
    n.summon()
    _pump(400)
    assert not n.isHidden()
    n.hide_for_capture()
    assert n.isHidden() and n._capture_hidden
    assert n not in ui_motion._running          # nothing animating a capture hide
    n.deleteLater()
