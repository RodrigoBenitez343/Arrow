import logging
from PyQt5.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QSplitter
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from ..constants import (DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR, CENTER_BG, GRAPH_PLANE)
from ..widgets.collapsible_toolbar import CollapsibleToolbar

logger = logging.getLogger(__name__)


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
            
            # Main layout uses a splitter so sidebars reserve width and push the graph
            logger.debug("Creating main splitter layout with sidebars")
            outer_layout = QHBoxLayout(self.parent_widget)
            outer_layout.setContentsMargins(5, 5, 5, 5)
            outer_layout.setSpacing(5)
            
            splitter = QSplitter(Qt.Horizontal, self.parent_widget)
            splitter.setObjectName("graphViewSplitter")
            try:
                splitter.setHandleWidth(6)
            except Exception:
                pass
            
            # Left-side collapsible chains library (with context nodes tab)
            logger.debug("Creating left-side chains library with context nodes tab")
            from ..widgets.chains_library import ChainsLibrary
            self.parent_widget.chains_library = ChainsLibrary(self.parent_widget)
            splitter.addWidget(self.parent_widget.chains_library)
            
            # Center container holds actions toolbar (top) and the graph (below)
            logger.debug("Creating center container for actions toolbar and graph")
            center_container = QWidget(self.parent_widget)
            center_container_layout = QVBoxLayout(center_container)
            center_container_layout.setContentsMargins(0, 0, 0, 0)
            center_container_layout.setSpacing(5)
            
            # Node graph widget (placeholder - will be replaced by GraphManager)
            logger.debug("Creating node graph widget placeholder")
            self.parent_widget.node_graph_widget = QWidget(center_container)
            self.parent_widget.node_graph_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {CENTER_BG};
                    border-radius: 10px;
                }}
            """)
            
            from ..widgets.actions_toolbar import ActionsToolbar
            actions_toolbar = ActionsToolbar(self.parent_widget)
            self.parent_widget.actions_toolbar = actions_toolbar
            try:
                actions_toolbar.setMaximumHeight(42)
            except Exception:
                pass
            center_container_layout.addWidget(actions_toolbar, 0)
            center_container_layout.addWidget(self.parent_widget.node_graph_widget, 1)
            splitter.addWidget(center_container)
            # Expose the center column (graph's real estate, between the left/right
            # bars) so the Code Node Studio can be hosted here and inherit its width.
            self.parent_widget._center_layout = center_container_layout
            
            # Right-side collapsible toolbar (spans full height to the top)
            logger.debug("Creating right-side collapsible toolbar")
            control_panel = self.create_control_panel()
            if control_panel is not None:
                splitter.addWidget(control_panel)
                self.parent_widget._toolbar_widget = control_panel
            
            # Initial sizes: left=160, center expands, right=160
            try:
                splitter.setSizes([320, 740, 160])
            except Exception:
                pass
            
            outer_layout.addWidget(splitter, 1)
            
            # Store references for visibility toggling
            self.parent_widget._splitter = splitter
            
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

    def _enforce_toolbar_width(self):
        try:
            splitter = getattr(self.parent_widget, '_splitter', None)
            toolbar = getattr(self.parent_widget, '_toolbar_widget', None)
            if splitter is None or toolbar is None:
                return
            sizes = splitter.sizes()
            total = sum(sizes) if sizes else splitter.width()
            desired = max(1, toolbar.sizeHint().width())
            splitter.setSizes([max(1, total - desired), desired])
        except Exception:
            pass
