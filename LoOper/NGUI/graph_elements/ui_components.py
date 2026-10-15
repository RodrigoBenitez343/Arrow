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

            # Node graph widget (placeholder - will be replaced by GraphManager).
            # It lives in a horizontal row: the Builder chat docks to its
            # RIGHT when open, so the canvas stays visible and editable beside
            # the chat instead of being pushed below it.
            logger.debug("Creating node graph widget placeholder")
            graph_row = QWidget(center_container)
            graph_row_layout = QHBoxLayout(graph_row)
            graph_row_layout.setContentsMargins(0, 0, 0, 0)
            graph_row_layout.setSpacing(0)
            self.parent_widget.node_graph_widget = QWidget(graph_row)
            self.parent_widget.node_graph_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {GRAPH_PLANE};
                }}
            """)
            graph_row_layout.addWidget(self.parent_widget.node_graph_widget, 1)
            center_container_layout.addWidget(graph_row, 1)
            outer_layout.addWidget(center_container, 1)
            # Expose the center column (Code Node Studio host) and the graph
            # row (Builder chat docks right of the canvas).
            self.parent_widget._center_layout = center_container_layout
            self.parent_widget._graph_row = graph_row
            self.parent_widget._graph_row_layout = graph_row_layout

            # The three canvas bars are no longer edge-docked. A single
            # floating dock row of three toggle buttons sits at the top-left
            # where the top bar used to be; each button slides its panel down
            # from under the row (one at a time).
            logger.debug("Creating dock row + canvas panels")
            from ..i18n import _
            from ..widgets.float_dock import (
                COLLECTION_ITEMS, CanvasSideButton, FloatDockBar)

            self.parent_widget._float_dock = FloatDockBar(self.parent_widget)
            # Sub-dock: appears with the Library panel and picks which node
            # collection it lists, so the library itself stays a plain list.
            self.parent_widget._sub_dock = FloatDockBar(
                self.parent_widget, items=COLLECTION_ITEMS, node_icons=True)
            self.parent_widget._sub_dock.hide()
            try:
                self.parent_widget._sub_dock.panel_requested.connect(
                    self._on_sub_dock)
            except Exception:
                pass
            # Builder chat toggle: a canvas-side button (the top-bar Builder
            # button stays; this one floats on the canvas' right edge).
            self.parent_widget._builder_toggle_btn = CanvasSideButton(
                'ROBOT', _("Builder"), self.parent_widget)

            from ..widgets.chains_library import ChainsLibrary
            self.parent_widget.chains_library = ChainsLibrary(self.parent_widget)
            # The sub-dock replaces the library's own tab row.
            try:
                self.parent_widget.chains_library.set_tabs_visible(False)
            except Exception:
                pass

            from ..widgets.actions_toolbar import ActionsToolbar
            actions_toolbar = ActionsToolbar(self.parent_widget)
            self.parent_widget.actions_toolbar = actions_toolbar
            # It is no longer the wide top bar: it drops down like the other
            # two panels as a narrow icon-only column (labels on hover).
            try:
                from PyQt5.QtWidgets import QBoxLayout
                _lay = actions_toolbar._content_frame.layout()
                if _lay is not None:
                    _lay.setDirection(QBoxLayout.TopToBottom)
                    _lay.setAlignment(Qt.AlignTop)
                actions_toolbar.set_compact(True)
            except Exception:
                pass

            control_panel = self.create_control_panel()
            if control_panel is not None:
                self.parent_widget._toolbar_widget = control_panel

            #: key -> the panel widget the dock button opens.
            self._panels = {
                "actions": actions_toolbar,
                "library": self.parent_widget.chains_library,
                "nodes": control_panel,
            }
            self._panels = {k: v for k, v in self._panels.items() if v is not None}
            #: each panel's own height cap (0/unset = unbounded), restored on
            #: open so a previous close can never leave a stale clamp behind.
            self._panel_caps = {
                k: p.maximumHeight() for k, p in self._panels.items()
            }
            #: key of the currently open panel (None = all closed).
            self._open_panel_key = None
            self._panel_anim = None
            #: The Builder chat is an overlay like the panels (set by
            #: MainWindow via set_builder_panel): it floats over the canvas'
            #: right side instead of resizing it, so the canvas stays
            #: full-bleed and no empty band is ever painted.
            self._builder_panel = None
            self._builder_width = 0

            # Only the dock row and the canvas-side Builder button float; the
            # panels open/close under the dock.
            self._floating_bars = [
                (self.parent_widget._float_dock, "top"),
                (self.parent_widget._builder_toggle_btn, "right"),
            ]
            for w, _side in self._floating_bars:
                _apply_float_shadow(w)
            for pane in self._panels.values():
                _apply_float_shadow(pane)
                # The bars' own collapse triangles belong to the edge-docked
                # layouts; the dock row takes over opening/closing.
                for attr in ('_toggle_btn', '_toggle_btn_actions', '_toggle_btn_nodes'):
                    tgl = getattr(pane, attr, None)
                    if tgl is not None:
                        try:
                            tgl.hide()
                        except Exception:
                            pass

            self._relayout_filter = _RelayoutFilter(self)
            self.parent_widget.installEventFilter(self._relayout_filter)
            for w, _side in self._floating_bars:
                w.setParent(self.parent_widget)
                w.installEventFilter(self._relayout_filter)
                w.show()
                w.raise_()
            for pane in self._panels.values():
                pane.setParent(self.parent_widget)
                pane.installEventFilter(self._relayout_filter)
                pane.hide()
            try:
                self.parent_widget._float_dock.panel_requested.connect(
                    self.toggle_panel)
            except Exception:
                pass
            self.layout_floating_bars()

            # No white glow here: the bars and panels already carry the cyan
            # float shadow above, and stacking the glow's white rest halo on
            # top of it read as a broken double hue. The cyan float shadow is
            # the single hue these surfaces keep.

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

    #: padding from the base frame's edges / between dock and its panel
    _MARGIN = 12
    _DOCK_GAP = 10

    def layout_floating_bars(self):
        """Position the dock row and re-fit the open panel below it."""
        if getattr(self, '_laying_out', False):
            return
        self._laying_out = True
        try:
            parent = self.parent_widget
            W, H = parent.width(), parent.height()
            if W <= 1 or H <= 1:
                return
            m = self._MARGIN
            dock = getattr(parent, '_float_dock', None)
            if dock is not None and dock.isVisible():
                dh = dock.sizeHint().height()
                if dh < 2:
                    dh = max(34, dock.height())
                dock.setGeometry(m, m, dock.sizeHint().width(), dh)
                dock.raise_()
            # The collection sub-dock sits beside the base dock row, and only
            # exists while the Library panel is open.
            sub = getattr(parent, '_sub_dock', None)
            if sub is not None and sub.isVisible():
                sx = m
                if dock is not None and dock.isVisible():
                    sx = dock.x() + dock.width() + 8
                sh = sub.sizeHint().height() or 38
                sub.setGeometry(sx, m, sub.sizeHint().width(), sh)
                sub.raise_()
            # Re-fit the open panel so a window resize keeps it under the dock.
            # Skipped while it is animating open/closed (the animation owns the
            # geometry then).
            key = getattr(self, '_open_panel_key', None)
            if key and not self._panel_animating():
                pane = self._panels.get(key)
                if pane is not None and pane.isVisible():
                    x, y, w, h = self._panel_layout(key)
                    pane.setGeometry(x, y, w, h)
                    pane.raise_()
            # The canvas-side Builder button rides the right edge (just left
            # of the chat column when the builder is open).
            btn = getattr(parent, '_builder_toggle_btn', None)
            if btn is not None and btn.isVisible():
                # Anchored to the top-right corner: it never moves when the
                # chat opens (the chat opens below it, like the panels do).
                bx = W - m - btn.width()
                by = m
                btn.setGeometry(bx, by, btn.width(), btn.height())
                btn.raise_()
            # The Builder chat overlay follows resizes too.
            if getattr(self, '_builder_width', 0) and self._builder_panel is not None:
                self.set_builder_width(self._builder_width)
            # Keep the delete drop zone anchored to the bottom edge on resize.
            zone = getattr(parent, '_delete_zone', None)
            if zone is not None:
                zone.reposition()
        except Exception:
            pass
        finally:
            self._laying_out = False

    def _panel_layout(self, key):
        """(x, y, w, h) for a panel dropped down from under the dock row."""
        parent = self.parent_widget
        W, H = parent.width(), parent.height()
        m = self._MARGIN
        dock = getattr(parent, '_float_dock', None)
        dock_h = dock.height() if dock is not None and dock.height() > 1 else 38
        top = m + dock_h + self._DOCK_GAP
        # The builder chat owns the right column; no panel may cross into it.
        builder_w = int(getattr(parent, '_builder_reserved_width', 0) or 0)
        right_edge = W - m - (builder_w + m if builder_w else 0)
        avail_h = max(120, H - top - m)
        pane = self._panels.get(key)
        w = self._bar_width(pane, 300)
        if key == 'actions':
            # A narrow icon-only column (the labels live in the tooltips).
            w = 56
        # Never cross into the builder chat's column.
        w = min(w, max(120, right_edge - m))
        return (m, top, w, avail_h)

    def _builder_rect(self, W, H, width):
        """(x, y, w, h) for the Builder chat overlay: right side, below the
        dock band, so the anchored button above it is never covered."""
        m = self._MARGIN
        dock = getattr(self.parent_widget, '_float_dock', None)
        dock_h = dock.height() if dock is not None and dock.height() > 1 else 38
        top = m + dock_h + self._DOCK_GAP
        return (W - m - width, top, width, max(120, H - top - m))

    def set_builder_panel(self, panel):
        """Register the Builder chat so the layout can place and slide it."""
        self._builder_panel = panel
        try:
            # It is a canvas overlay now: coordinates are canvas-relative and
            # the fixed width must go (the slide drives the geometry).
            panel.setParent(self.parent_widget)
            panel.setMinimumWidth(0)
            panel.setMaximumWidth(16777215)
        except Exception:
            pass

    def set_builder_width(self, width):
        """Place the chat with *width* px (0 hides it). The slide driver."""
        pane = getattr(self, '_builder_panel', None)
        if pane is None:
            return
        width = int(max(0, width))
        self._builder_width = width
        if width < 2:
            try:
                pane.hide()
            except Exception:
                pass
            return
        parent = self.parent_widget
        x, y, w, h = self._builder_rect(parent.width(), parent.height(), width)
        try:
            pane.setGeometry(x, y, w, h)
            pane.show()
            pane.raise_()
        except Exception:
            pass

    def _on_sub_dock(self, key):
        """A collection button on the sub-dock: switch what the library lists."""
        lib = self._panels.get('library')
        if lib is not None and hasattr(lib, 'select_tab'):
            try:
                lib.select_tab(key)
            except Exception:
                pass
        sub = getattr(self.parent_widget, '_sub_dock', None)
        if sub is not None:
            sub.set_active(key)

    def _sync_sub_dock(self):
        """The sub-dock exists only as long as the Library panel is open.

        It takes over the collection switching, so the library's own tab row
        stays hidden.
        """
        sub = getattr(self.parent_widget, '_sub_dock', None)
        if sub is None:
            return
        lib = self._panels.get('library')
        if getattr(self, '_open_panel_key', None) == 'library' and lib is not None:
            try:
                sub.set_active(lib.active_tab_key())
            except Exception:
                pass
            sub.show()
            sub.raise_()
        else:
            sub.hide()

    def toggle_panel(self, key):
        """Open the clicked panel, closing the previously open one."""
        try:
            if key not in getattr(self, '_panels', {}):
                return
            if getattr(self, '_open_panel_key', None) == key:
                self._close_panel(key)
                return
            prev = getattr(self, '_open_panel_key', None)
            if prev:
                self._close_panel(prev, animate=False)
            self._open_panel(key)
        except Exception:
            pass

    def _panel_animating(self):
        """True while a panel slide is in flight."""
        try:
            from PyQt5.QtCore import QAbstractAnimation
            anim = getattr(self, '_panel_anim', None)
            return anim is not None and anim.state() == QAbstractAnimation.Running
        except Exception:
            return False

    def _open_panel(self, key):
        pane = self._panels.get(key)
        if pane is None:
            return
        x, y, w, h = self._panel_layout(key)
        # Restore the panel's own cap: a close animation must not leave a
        # stale maximum that would clamp every later open to zero height.
        try:
            pane.setMaximumHeight(self._panel_caps.get(key, 16777215))
        except Exception:
            pass
        pane.setGeometry(x, y, w, 0)
        pane.show()
        pane.raise_()
        self._open_panel_key = key
        dock = getattr(self.parent_widget, '_float_dock', None)
        if dock is not None:
            dock.set_active(key)
        self._sync_sub_dock()
        self._animate_panel(key, h)

    def _close_panel(self, key, animate=True):
        pane = self._panels.get(key)
        if pane is None:
            return
        self._open_panel_key = None
        dock = getattr(self.parent_widget, '_float_dock', None)
        if dock is not None:
            dock.set_active(None)
        self._sync_sub_dock()
        if animate:
            self._animate_panel(key, 0, then_hide=True)
        else:
            try:
                pane.hide()
            except Exception:
                pass

    def _animate_panel(self, key, target, then_hide=False):
        """Slide the panel down/up by animating its GEOMETRY height.

        Animating ``maximumHeight`` looks like it works but only shrinks a
        manual-geometry widget: raising the maximum back never grows the
        widget again, so every re-open stayed 0px tall. Driving the geometry
        instead always resizes in both directions.
        """
        pane = self._panels.get(key)
        if pane is None:
            return
        x, y, w, _full = self._panel_layout(key)
        target = int(max(0, target))
        try:
            from PyQt5.QtCore import QEasingCurve, QVariantAnimation
            start = pane.height() if pane.isVisible() else 0
            pane.setGeometry(x, y, w, int(start))
            anim = QVariantAnimation(pane)
            anim.setDuration(170)
            anim.setStartValue(int(start))
            anim.setEndValue(target)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.valueChanged.connect(
                lambda v, p=pane, gx=x, gy=y, gw=w:
                    p.setGeometry(gx, gy, gw, int(v)))
            if then_hide:
                anim.finished.connect(pane.hide)
            anim.start()
            self._panel_anim = anim  # keep the ref alive while it runs
        except Exception:
            try:
                pane.setGeometry(x, y, w, target)
                if then_hide:
                    pane.hide()
            except Exception:
                pass

    def set_floating_bars_visible(self, visible):
        """Show or hide the dock row (and close any open panel).

        The Code Node Studio owns the whole canvas while open, so the dock is
        hidden then: otherwise it floats on top of the studio and swallows the
        clicks meant for its header buttons (the studio could be entered but
        never left). Coming back, the dock returns with every panel closed.
        """
        self._bars_hidden = not visible
        for w, _side in getattr(self, '_floating_bars', []):
            try:
                w.setVisible(bool(visible))
            except Exception:
                pass
        if not visible:
            key = getattr(self, '_open_panel_key', None)
            if key:
                self._close_panel(key, animate=False)
            sub = getattr(self.parent_widget, '_sub_dock', None)
            if sub is not None:
                sub.hide()
            pane = getattr(self, '_builder_panel', None)
            if pane is not None:
                pane.hide()
        # The delete drop zone overlays the canvas too, so it hides with them.
        zone = getattr(self.parent_widget, '_delete_zone', None)
        if zone is not None and not visible:
            try:
                zone.slide_out()
            except Exception:
                pass
        if visible:
            self.layout_floating_bars()
            # Bring the chat back only when it was open as the studio took over.
            pane = getattr(self, '_builder_panel', None)
            if pane is not None and self._builder_width and bool(
                    getattr(self.parent_widget, '_builder_panel_visible', False)):
                self.set_builder_width(self._builder_width)

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
