"""Shared motion for the agent-mode surfaces: morph in, morph back out.

Hard show()/hide() swaps are what reads as flicker: the panel appears at full
size in one frame, the notch drops in after the app has already minimized, and
a collapsed panel leaves a blank frame where the builder has not been repainted
yet.  Every helper here is fire-and-forget and re-entrancy safe — a new request
on the same widget cancels the running one, so rapid toggling lands on the
latest intent instead of stacking animations.

The motion language is MORPH, not fade: a panel unfolds to its size (and takes
that space from its neighbours), a bubble pushes the transcript open, the notch
grows out of a pill and folds back into it.  An opacity ramp alone reads as a
vanish, so ``fade_in``/``fade_away`` are only a fallback for surfaces with
nothing to resize; prefer ``unfold``/``fold_away`` (layout-managed widgets) and
``morph_from``/``grow_in``/``shrink_away`` (top-level windows).

Two rules keep it out of trouble:
  * Motion never gates state.  Entries show the widget first and animate from
    there; only *_away* finishes with hide(), and any new show() cancels a
    pending away so the widget cannot vanish behind the user's back.
  * Nothing animates on a capture path (hide_for_capture / _automation_hide):
    a screen read must never contain a half-morphed panel.

Size is driven through the widget's own maximum *during* the animation and the
original min/max are restored on landing (Qt never re-grows a widget whose
maximumHeight was lowered, so leaving the cap in place would pin it small
forever).  Top-levels animate geometry directly.  Opacity is only usable on
widgets without native child windows (no QWebEngineView), because Qt graphics
effects do not reach native children — they would pop while everything around
them faded.
"""

import os

from PyQt5.QtCore import (QEasingCurve, QPropertyAnimation, QRect, QTimer,
                          QVariantAnimation)
from PyQt5.QtWidgets import QGraphicsOpacityEffect

ENTER_MS = 220           # arrival: enough travel to read as movement
AWAY_MS = 170            # departure: shorter, so it never feels slow
EASE_IN = QEasingCurve.OutCubic
EASE_OUT = QEasingCurve.InCubic

_DISABLED = os.environ.get("LOOPER_NO_ANIMATION", "").lower() not in ("", "0")
# Headless runs (tests, CI) have no window manager: a geometry animation there is
# meaningless and would make assertions timing-dependent.  LOOPER_ANIMATION=1
# forces motion back on — the motion tests do that, since they measure travel.
_HEADLESS = os.environ.get("QT_QPA_PLATFORM", "").lower() in ("offscreen", "minimal")
_FORCE = os.environ.get("LOOPER_ANIMATION", "").lower() in ("1", "on", "yes", "force")

_running = {}            # widget -> (QAbstractAnimation, disarm)
_pristine = {}           # (widget, axis) -> size limits from before the first morph

# axis -> the Qt accessor names for that axis.  They must be CALLED: PyQt
# exposes these as methods, so assigning widget.maximumHeight = 8 would shadow
# the method with a plain attribute and silently do nothing to the layout.
_AXES = {
    "x": dict(set_max="setMaximumWidth", set_min="setMinimumWidth",
              get_max="maximumWidth", get_min="minimumWidth", get_size="width"),
    "y": dict(set_max="setMaximumHeight", set_min="setMinimumHeight",
              get_max="maximumHeight", get_min="minimumHeight", get_size="height"),
}


def motion_enabled() -> bool:
    """Motion is off when switched off, and in headless runs (see _HEADLESS)."""
    if _DISABLED or (_HEADLESS and not _FORCE):
        return False
    return True


def _cancel(widget):
    entry = _running.pop(widget, None)
    if entry is None:
        return
    anim, disarm = entry
    # A superseded animation must not run its end state (hide, chat swap…):
    # disarm it before stopping, or the queued safety net would still fire it.
    try:
        disarm()
    except Exception:
        pass
    anim.stop()
    anim.deleteLater()


def _remember(widget, anim, on_done):
    """Run *anim*; call *on_done* once it lands, and guarantee it lands."""
    _cancel(widget)
    done = {"fired": False}

    def _disarm():
        done["fired"] = True

    def _finish():
        if done["fired"]:
            return
        done["fired"] = True
        _running.pop(widget, None)
        try:
            anim.stop()
        except Exception:
            pass
        if on_done:
            on_done()

    _running[widget] = (anim, _disarm)
    anim.finished.connect(_finish)
    anim.start()
    # Safety net: a widget hidden mid-animation never emits finished(), and the
    # end state (hide, restore size…) must still be reached.
    QTimer.singleShot(anim.duration() + 250, _finish)
    return _finish


def unfold(widget, start_px=1, target_px=None, axis="y", duration=ENTER_MS,
           on_done=None):
    """Layout morph: the widget grows from a sliver to its laid-out size.

    Its neighbours give up that space as it grows, so a swapped-in panel reads
    as pushing the old one out instead of appearing.  On landing the original
    min/max are handed back, so the layout (not the animation) owns the size.
    """
    if widget is None or not motion_enabled():
        if widget is not None:
            _cancel(widget)
            widget.setVisible(True)
            if on_done:
                on_done()
        return
    _cancel(widget)
    ax = _AXES[axis]
    widget.setVisible(True)
    _limits(widget, ax, axis)          # remember the real bounds before bending them
    getattr(widget, ax["set_min"])(0)
    _activate(widget)
    natural = int(target_px or getattr(widget, ax["get_size"])())
    # A word-wrapped label reports its FULL height as its minimum hint, which
    # would leave nothing to travel; cap the floor at half the target so there is
    # always a morph, while a genuinely small minimum still stops the squash.
    floor = min(_minimum_for(widget, axis), int(natural * 0.5))
    start = max(int(start_px), floor)
    if natural - start < 8:
        _hand_back(widget, ax, axis)
        if on_done:
            on_done()
        return
    set_max = getattr(widget, ax["set_max"])
    anim = _int_anim(widget, start, natural, duration, EASE_IN, set_max)

    def _land():
        _hand_back(widget, ax, axis)
        if on_done:
            on_done()
    _remember(widget, anim, _land)


def fold_away(widget, on_done=None, end_px=1, axis="y", duration=AWAY_MS):
    """Layout morph out: fold the widget down to *end_px*, then *on_done*.

    Used by the paths that finish with hide(); the capture paths never come here.
    """
    if widget is None or not motion_enabled() or widget.isHidden():
        if widget is not None:
            _cancel(widget)
        if on_done:
            on_done()
        return
    _cancel(widget)
    ax = _AXES[axis]
    start = getattr(widget, ax["get_size"])()
    end = min(int(end_px), start)
    if start - end < 8:
        _hand_back(widget, ax, axis)
        if on_done:
            on_done()
        return
    _limits(widget, ax, axis)          # remember the real bounds before bending them
    getattr(widget, ax["set_min"])(0)
    anim = _int_anim(widget, start, end, duration, EASE_OUT,
                     getattr(widget, ax["set_max"]))

    def _land():
        _hand_back(widget, ax, axis)
        if on_done:
            on_done()
    _remember(widget, anim, _land)


def _int_anim(widget, start, end, duration, curve, setter):
    anim = QVariantAnimation(widget)
    anim.setDuration(int(duration))
    anim.setStartValue(int(start))
    anim.setEndValue(int(end))
    anim.setEasingCurve(curve)
    anim.valueChanged.connect(lambda v: setter(int(v)))
    return anim


def _activate(widget):
    """Force a layout pass so the widget's laid-out size is readable now."""
    parent = widget.parent()
    lay = parent.layout() if parent is not None else None
    if lay is not None:
        try:
            lay.activate()
        except Exception:
            pass


def _minimum_for(widget, axis):
    try:
        hint = widget.minimumSizeHint()
        return int(hint.width() if axis == "x" else hint.height())
    except Exception:
        return 0


def _limits(widget, ax, axis):
    """The widget's own size limits from before any morph touched them.

    Recorded once per (widget, axis) and kept across a cancelled morph, so a
    superseded animation cannot leave the widget pinned at the temporary 0
    minimum (the server panel's fixed 288 px would otherwise be lost).
    """
    key = (widget, axis)
    if key not in _pristine:
        _pristine[key] = (getattr(widget, ax["get_max"])(),
                          getattr(widget, ax["get_min"])())
    return _pristine[key]


def _hand_back(widget, ax, axis):
    """Return the size limits to the layout once a morph has landed."""
    orig = _pristine.pop((widget, axis), None)
    if orig is None:
        return                    # nothing was ever constrained: nothing to restore
    try:
        getattr(widget, ax["set_min"])(int(orig[1]))
        getattr(widget, ax["set_max"])(int(orig[0]))
    except Exception:
        pass


def fade_in(widget, duration=ENTER_MS):
    """Opacity 0 -> 1 on an already-visible widget (no state change involved)."""
    if widget is None or not motion_enabled():
        return
    effect = widget.graphicsEffect()
    if isinstance(effect, QGraphicsOpacityEffect):
        pass
    elif effect is not None:
        return                      # another effect owns this widget: leave it
    else:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    _cancel(widget)
    effect.setOpacity(0.0)

    def _land():
        try:
            effect.setOpacity(1.0)
            widget.setGraphicsEffect(None)   # no lingering effect
        except Exception:
            pass
    _remember(widget, _opacity_anim(effect, 0.0, 1.0, duration, EASE_IN), _land)


def fade_away(widget, on_done=None, duration=AWAY_MS):
    """Opacity 1 -> 0, then *on_done* (typically the real hide())."""
    if widget is None or not motion_enabled():
        if on_done:
            on_done()
        return
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsOpacityEffect):
        if effect is not None:
            if on_done:
                on_done()
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)

    def _land():
        try:
            effect.setOpacity(1.0)
            widget.setGraphicsEffect(None)
        except Exception:
            pass
        if on_done:
            on_done()
    _remember(widget, _opacity_anim(effect, float(effect.opacity()), 0.0,
                                     duration, EASE_OUT), _land)


def grow_in(widget, rect, from_height_ratio=0.45, duration=ENTER_MS, on_done=None):
    """Top-level arrival: take *rect*, rising from a shorter version of itself."""
    if widget is None or not motion_enabled():
        if widget is not None:
            widget.setGeometry(rect)
            if on_done:
                on_done()
        return
    start = QRect(rect.x(), rect.y(), rect.width(),
                  max(36, int(rect.height() * from_height_ratio)))
    morph_from(widget, start, rect, duration, on_done=on_done)


def morph_from(widget, start, rect, duration=ENTER_MS, on_done=None):
    """Top-level morph: interpolate position AND size from *start* to *rect*."""
    if widget is None or not motion_enabled():
        if widget is not None:
            widget.setGeometry(rect)
            if on_done:
                on_done()
        return
    _relax_fixed_size(widget)
    widget.setGeometry(start)
    _animate_geometry(widget, start, rect, duration, EASE_IN, on_done=on_done)


def morph_window(widget, scale=0.94, offset_px=12, duration=ENTER_MS,
                 on_done=None):
    """A whole window's arrival: rise from a slightly smaller, lower copy of
    itself into its final geometry.

    Dialogs decide their rect at show time (fit + clamp + centre), so this is
    called right after that; the widget's own size limits are handed back on
    landing, and the morph yields if the dialog re-fits mid-flight (never snaps
    it back to a stale rect).
    """
    if widget is None or not motion_enabled():
        if on_done:
            on_done()
        return
    target = QRect(widget.geometry())
    w = max(160, int(target.width() * scale))
    h = max(120, int(target.height() * scale))
    start = QRect(target.center().x() - w // 2, target.y() + int(offset_px), w, h)
    limits = _capture_limits(widget)
    _relax_fixed_size(widget)
    widget.setGeometry(start)
    # Animate from the geometry the window manager actually granted (it may
    # adjust an off-screen rect), so the first frame is not read as an external
    # change by the guard in _animate_geometry.
    start = QRect(widget.geometry())

    def _land():
        _restore_limits(widget, limits)
        if on_done:
            on_done()
    _animate_geometry(widget, start, target, duration, EASE_IN, on_done=_land,
                      yield_to_external=True)


def to_rect(widget, rect, duration=ENTER_MS, on_done=None):
    """Glide a top-level window from where it is to *rect*."""
    if widget is None or not motion_enabled():
        if widget is not None:
            widget.setGeometry(rect)
            if on_done:
                on_done()
        return
    _relax_fixed_size(widget)
    _animate_geometry(widget, widget.geometry(), rect, duration, EASE_IN,
                      on_done=on_done)


def slide_in(widget, rect, offset_px=28, duration=ENTER_MS, on_done=None):
    """Top-level arrival from a small offset (the notch drops into place)."""
    if widget is None or not motion_enabled():
        if widget is not None:
            widget.setGeometry(rect)
            if on_done:
                on_done()
        return
    start = QRect(rect.x(), rect.y() - int(offset_px), rect.width(), rect.height())
    widget.setGeometry(start)
    _animate_geometry(widget, start, rect, duration, EASE_IN, on_done=on_done)


def shrink_away(widget, rect, on_done=None, duration=AWAY_MS,
                to_height_ratio=0.4, to_width_ratio=1.0):
    """Top-level departure: morph toward a smaller version of itself, then
    *on_done*.  Narrowing the width as well is what makes it read as folding
    into the notch instead of just sinking."""
    if widget is None or not motion_enabled():
        if on_done:
            on_done()
        return
    _relax_fixed_size(widget)
    start = widget.geometry()
    end_w = max(64, int(rect.width() * to_width_ratio))
    end = QRect(rect.x() + (rect.width() - end_w) // 2, rect.y(),
                end_w, max(24, int(rect.height() * to_height_ratio)))

    def _land():
        if on_done:
            on_done()
    _animate_geometry(widget, start, end, duration, EASE_OUT, on_done=_land)


def _opacity_anim(effect, start, end, duration, curve):
    anim = QPropertyAnimation(effect, b"opacity", effect)
    anim.setDuration(int(duration))
    anim.setStartValue(float(start))
    anim.setEndValue(float(end))
    anim.setEasingCurve(curve)
    return anim


def _animate_geometry(widget, start, end, duration, curve, on_done=None,
                      yield_to_external=False):
    """Drive setGeometry() from a QVariantAnimation (layout-independent)."""
    anim = QVariantAnimation(widget)
    anim.setDuration(int(duration))
    anim.setStartValue(QRect(start))
    anim.setEndValue(QRect(end))
    anim.setEasingCurve(curve)
    last = {"rect": QRect(start)}

    def _apply(value):
        # yield_to_external: the window was moved/resized by someone else (a
        # dialog re-fitting to new content) — stop fighting it and let the other
        # geometry win; _land skips the snap for the same reason.
        if yield_to_external and widget.geometry() != last["rect"]:
            anim.stop()
            return
        last["rect"] = QRect(value)
        widget.setGeometry(value)

    anim.valueChanged.connect(_apply)

    def _land():
        # yield_to_external: a dialog that re-fitted while the morph ran owns
        # the geometry now — snapping it back to our stale end rect would leave
        # it sized wrong until the next resize.
        if not (yield_to_external and widget.geometry() != last["rect"]):
            try:
                widget.setGeometry(end)
            except Exception:
                pass
        if on_done:
            on_done()
    # _remember wires finished() -> _land; a second connect would land twice
    # (and callers end in hide()/signals that must fire exactly once).
    _remember(widget, anim, _land)
    return anim


def _relax_fixed_size(widget):
    """setFixedSize pins min=max, which would clamp a geometry animation."""
    try:
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(16777215, 16777215)
    except Exception:
        pass


_LIMIT_GETTERS = ("minimumWidth", "minimumHeight", "maximumWidth", "maximumHeight")
_LIMIT_SETTERS = ("setMinimumWidth", "setMinimumHeight", "setMaximumWidth",
                  "setMaximumHeight")


def _capture_limits(widget):
    """The window's own size limits, so a geometry morph can hand them back."""
    return tuple(getattr(widget, name)() for name in _LIMIT_GETTERS)


def _restore_limits(widget, limits):
    try:
        for setter, value in zip(_LIMIT_SETTERS, limits):
            getattr(widget, setter)(int(value))
    except Exception:
        pass


def restore_fixed_size(widget, size):
    """Re-pin the fixed size after a geometry animation ended."""
    try:
        widget.setFixedSize(size)
    except Exception:
        pass
