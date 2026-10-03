import logging
from PyQt5.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget
from PyQt5.QtCore import Qt, QObject, QEvent
from PyQt5.QtGui import QFont
from ..constants import (DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR, CENTER_BG, GRAPH_PLANE,
                         ACCENT_COLOR, FLOAT_SHADOW_OFFSET_Y,
                         BAR_SHADOW_BLUR, BAR_SHADOW_ALPHA)
from ..widgets.collapsible_toolbar import CollapsibleToolbar

logger = logging.getLogger(__name__)


class _RelayoutFilter(QObject):
    """Re-runs the floating-bar layout when the canvas or a bar is resized."""

    def __init__(self, ui):
        super().__init__(ui.parent_widget)
        self._ui = ui

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            try:
                self._ui.layout_floating_bars()
            except Exception:
                pass
        return False


def _apply_float_shadow(bar):
    """Give a floating bar a soft degrade underneath so it reads as lifted off
    the canvas. Always on while the bar is visible (not hover-based).

    Drawn from the bar's own rendered shape, so the rounded panel casts a soft
    falloff below it instead of a hard border.
    """
    if bar is None:
        return
    try:
        from PyQt5.QtWidgets import QGraphicsDropShadowEffect
        from PyQt5.QtGui import QColor
        effect = QGraphicsDropShadowEffect(bar)
        color = QColor(ACCENT_COLOR)
        color.setAlpha(BAR_SHADOW_ALPHA)
        effect.setColor(color)            # soft cyan degrade
        effect.setBlurRadius(BAR_SHADOW_BLUR)  # the falloff
        effect.setOffset(0, FLOAT_SHADOW_OFFSET_Y)  # sits under the bar
        bar.setGraphicsEffect(effect)
    except Exception as e:
        logger.debug(f"Could not add float shadow: {e}")


class UIComponents:
    """Handles UI component creation and styling for the graph view."""
    
    def __init__(self, parent_widget):
        logger.info("Initializing UIComponents")
        try:
            self.parent_widget = parent_widget
            logger.debug(f"UIComponents initialized with parent widget: {type(parent_widget).__name__}")
        except Exception as e:
            logger.error(f"Error initializing UIComponents: {e}")
            raise
        
    def setup_ui(self):
        """Set up the main UI layout and components."""
        logger.info("Setting up main UI layout and components")
        try:
            # Apply main widget styling
            logger.debug("Applying main widget styling")
            self.parent_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {GRAPH_PLANE};
                    color: {TEXT_COLOR};
                    border: none;
                }}
                QToolButton {{
                    background: transparent;
                    border: none;
                    color: {TEXT_COLOR};
                }}
                QScrollBar:vertical {{
                    background: {MEDIUM_GREY};
                    width: 8px;
                    margin: 0px;
                    border: none;
                }}
                QScrollBar::handle:vertical {{
                    background: {LIGHT_GREY};
                    min-height: 20px;
                    border-radius: 4px;
                }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            """)
            
            # Full-bleed canvas: the graph fills the whole area and the bars float
            # on top of it as overlays (they no longer reserve width).
            logger.debug("Creating full-bleed canvas with floating bars")
            outer_layout = QVBoxLayout(self.parent_widget)
            outer_layout.setContentsMargins(0, 0, 0, 0)
            outer_layout.setSpacing(0)

            center_container = QWidget(self.parent_widget)
            center_container_layout = QVBoxLayout(center_container)
            center_container_layout.setContentsMargins(0, 0, 0, 0)
            center_container_layout.setSpacing(0)

            # Node graph widget (placeholder - will be replaced by GraphManager)
            logger.debug("Creating node graph widget placeholder")
            self.parent_widget.node_graph_widget = QWidget(center_container)
            self.parent_widget.node_graph_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {GRAPH_PLANE};
                }}
            """)
            center_container_layout.addWidget(self.parent_widget.node_graph_widget, 1)
            outer_layout.addWidget(center_container, 1)
            # Expose the center column so the Code Node Studio can be hosted here.
            self.parent_widget._center_layout = center_container_layout

            # Floating bars (overlays over the canvas).
            logger.debug("Creating floating bars (chains library, actions, toolbar)")
            from ..widgets.chains_library import ChainsLibrary
            self.parent_widget.chains_library = ChainsLibrary(self.parent_widget)

            from ..widgets.actions_toolbar import ActionsToolbar
            actions_toolbar = ActionsToolbar(self.parent_widget)
            self.parent_widget.actions_toolbar = actions_toolbar
            try:
                actions_toolbar.setMaximumHeight(42)
            except Exception:
                pass

            control_panel = self.create_control_panel()
            if control_panel is not None:
                self.parent_widget._toolbar_widget = control_panel

            # Keep overlays above the graph and (re)position them on resize.
            self._floating_bars = [
                (self.parent_widget.chains_library, "left"),
                (control_panel, "right"),
                (actions_toolbar, "top"),
            ]
            self._floating_bars = [(w, s) for (w, s) in self._floating_bars if w is not None]
            # Soft shadow so the bars read as floating above the canvas.
            for w, _side in self._floating_bars:
                _apply_float_shadow(w)
            self._relayout_filter = _RelayoutFilter(self)
            self.parent_widget.installEventFilter(self._relayout_filter)
            for w, _side in self._floating_bars:
                # The bar constructors take (graph_view, parent=None); without a
                # parent they become top-level windows. The old code relied on
                # splitter.addWidget() to reparent them, so re-parent explicitly
                # to make them child overlays floating on the canvas.
                w.setParent(self.parent_widget)
                w.installEventFilter(self._relayout_filter)
                w.show()
                w.raise_()
            self.layout_floating_bars()

            # Soft white->cyan glow on the bars' cards, their node/chain
            # elements, and the top bar's buttons.
            try:
                from ..glow import attach_glow, attach_glow_tree
                for w, _side in self._floating_bars:
                    attach_glow(getattr(w, '_content_frame', None))
                    attach_glow_tree(w)
            except Exception:
                pass

            # No splitter anymore; bars are overlays positioned by geometry.
            self.parent_widget._splitter = None
            
            logger.info("UI setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up UI: {e}")
            raise
        
    def create_control_panel(self):
        """Create the top collapsible toolbar for quick actions."""
        logger.debug("Creating collapsible toolbar control panel")
        try:
            toolbar = CollapsibleToolbar(self.parent_widget)
            logger.info("Collapsible toolbar created successfully")
            return toolbar
        except Exception as e:
            logger.error(f"Error creating control panel: {e}")
            raise

    def layout_floating_bars(self):
        """Position the floating bars over the full-bleed canvas."""
        if getattr(self, '_laying_out', False):
            return
        self._laying_out = True
        try:
            parent = self.parent_widget
            W, H = parent.width(), parent.height()
            if W <= 1 or H <= 1:
                return
            # Keep the bars inside the base frame with padding from its edges.
            # Bars only push each other; the canvas stays full-bleed behind them.
            m = 12     # padding between the bars and the base frame's edges
            gap = 12   # padding between a side bar and the top bar
            right_pad = 16  # the right bar needs a little more breathing room
            lib = getattr(parent, 'chains_library', None)
            panel = getattr(parent, '_toolbar_widget', None)
            top = getattr(parent, 'actions_toolbar', None)

            left_w = 0
            if lib is not None and lib.isVisible():
                lib.setGeometry(m, m, self._bar_width(lib, 320), H - 2 * m)
                lib.raise_()
                left_w = lib.width()

            right_w = 0
            if panel is not None and panel.isVisible():
                rw = self._bar_width(panel, 260)
                panel.setGeometry(0, m, rw, H - 2 * m)
                # The panel's own layout minimum can be wider than rw, in which
                # case Qt widens it and it would overrun the frame. Anchor it by
                # its RIGHT edge so it always ends `right_pad` from the edge.
                panel.move(W - right_pad - panel.width(), m)
                panel.raise_()
                right_w = panel.width()

            if top is not None and top.isVisible():
                x = m + (left_w + gap if left_w else 0)
                right_edge = (W - right_pad - right_w - gap) if right_w else (W - m)
                # Size the top bar to its own content so the collapse animation
                # (which shrinks its frame upward) leaves no transparent gap.
                th = top.sizeHint().height()
                if th < 2:
                    th = top.height() if top.height() > 1 else 42
                top.setGeometry(x, m, max(120, right_edge - x), th)
                top.raise_()

            # Keep the delete drop zone anchored to the bottom edge on resize.
            zone = getattr(parent, '_delete_zone', None)
            if zone is not None:
                zone.reposition()
        except Exception:
            pass
        finally:
            self._laying_out = False

    def set_floating_bars_visible(self, visible):
        """Show or hide every floating bar.

        The Code Node Studio owns the whole canvas while open, so the bars are
        hidden then: otherwise they float on top of the studio and swallow the
        clicks meant for its header buttons (the studio could be entered but
        never left). Showing them again re-runs the layout so they settle back.
        """
        self._bars_hidden = not visible
        for w, _side in getattr(self, '_floating_bars', []):
            try:
                w.setVisible(bool(visible))
            except Exception:
                pass
        # The delete drop zone overlays the canvas too, so it hides with them.
        zone = getattr(self.parent_widget, '_delete_zone', None)
        if zone is not None and not visible:
            try:
                zone.slide_out()
            except Exception:
                pass
        if visible:
            self.layout_floating_bars()

    @staticmethod
    def _bar_width(bar, default):
        """Preferred width for an overlay bar, respecting its own min/max."""
        try:
            w = bar.sizeHint().width()
        except Exception:
            w = 0
        if w < 2:
            try:
                w = bar.width()
            except Exception:
                w = 0
        if w < 2:
            w = default
        # Never go below the bar's own minimum, or Qt will widen it and it will
        # overrun the frame edge it was anchored to.
        try:
            w = max(int(w), bar.minimumWidth(), bar.minimumSizeHint().width())
        except Exception:
            pass
        try:
            w = min(int(w), bar.maximumWidth())
        except Exception:
            pass
        return max(0, min(int(w), 520))

    def _enforce_toolbar_width(self):
        self.layout_floating_bars()
