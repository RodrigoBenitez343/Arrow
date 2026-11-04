"""The agent overlay's chat bubbles fit their text instead of filling the panel.

Locks the geometry contract: a reply short enough for one line stays a small
bubble (width hugs the text), and a long one wraps at a readable measure
instead of stretching across a window-sized panel.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication, QLabel  # noqa: E402

from NGUI.widgets.agent_overlay import BUBBLE_MAX_PX, AgentOverlay  # noqa: E402

LONG_TEXT = (
    "List every python file under LoOper/NGUI that touches QWebEngineView, and "
    "tell me which ones create a new profile per window; then summarise the "
    "differences between them in two short paragraphs of prose."
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_bubble_widths_follow_the_text(qapp):
    overlay = AgentOverlay()
    overlay._render_bubble("ok", True, "text")
    overlay._render_bubble(LONG_TEXT, False, "text")
    overlay._apply_bubble_widths()

    short, long = overlay._chat_container.findChildren(QLabel, "chatBubbleLabel")[:2]
    line = short.fontMetrics().height()

    assert short.width() < 80, "a two-letter reply must not own a wide bubble"
    assert short.height() <= line * 2, "and it must stay one line tall"
    assert long.width() <= BUBBLE_MAX_PX, "a long reply wraps at the readable measure"
    assert long.height() > line, "wrapping, not clipping"
