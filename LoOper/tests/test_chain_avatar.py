"""Procedural chain avatars: unique per chain, stable, tintable.

The System-chain (coworker) list and the compact notch both derive their avatar
from the chain id via chain_avatar_qicon; this pins the contract that makes
chains distinguishable in the notch's 26 px slot.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from NGUI.widgets.chain_avatar import chain_avatar_qicon, chain_avatar_svg  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _digest(pixmap):
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(buf.data())


def test_same_seed_is_deterministic(app):
    a = chain_avatar_qicon("researcher", 26).pixmap(26, 26)
    b = chain_avatar_qicon("researcher", 26).pixmap(26, 26)
    assert _digest(a) == _digest(b)


def test_different_chains_are_distinguishable(app):
    a = chain_avatar_qicon("researcher", 26).pixmap(26, 26)
    b = chain_avatar_qicon("form_filler", 26).pixmap(26, 26)
    assert not a.isNull() and not b.isNull()
    assert _digest(a) != _digest(b)


def test_state_tint_changes_the_pattern(app):
    plain = chain_avatar_qicon("researcher", 26).pixmap(26, 26)
    red = chain_avatar_qicon("researcher", 26, "#ef4444").pixmap(26, 26)
    assert _digest(plain) != _digest(red)


def test_svg_is_vector_and_stable(app):
    svg = chain_avatar_svg("researcher")
    assert svg.startswith("<svg") and "viewBox" in svg
    assert svg == chain_avatar_svg("researcher")
