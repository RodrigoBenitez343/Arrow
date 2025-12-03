import logging
from PyQt5.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QSplitter
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from ..constants import (DARK_GREY, LIGHT_GREY, TEXT_COLOR)
from ..widgets.collapsible_toolbar import CollapsibleToolbar

# Configure logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Create file handler
file_handler = logging.FileHandler('graphui_debug.log')
file_handler.setLevel(logging.DEBUG)

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create formatter
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add handlers to logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)


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
                    background-color: {DARK_GREY};
                    color: {TEXT_COLOR};
                    border: none;
                }}
            """)
            
            # Main layout contains a horizontal splitter so the toolbar can be resized by dragging
            logger.debug("Creating main horizontal layout with splitter")
            main_layout = QHBoxLayout(self.parent_widget)
            main_layout.setContentsMargins(5, 5, 5, 5)
            main_layout.setSpacing(5)
            splitter = QSplitter(Qt.Horizontal)
            splitter.setChildrenCollapsible(True)
            splitter.setHandleWidth(6)
            
            # Node graph widget (placeholder - will be set by graph manager)
            logger.debug("Creating node graph widget placeholder")
            self.parent_widget.node_graph_widget = QWidget()
            self.parent_widget.node_graph_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {DARK_GREY};
                    border: 1px solid {LIGHT_GREY};
                    border-radius: 5px;
                }}
            """)
            splitter.addWidget(self.parent_widget.node_graph_widget)
            splitter.setStretchFactor(0, 1)
            logger.debug("Node graph widget added to splitter (stretchFactor=1)")

            # Right-side collapsible toolbar
            logger.debug("Creating right-side collapsible toolbar")
            control_panel = self.create_control_panel()
            if control_panel is not None:
                splitter.addWidget(control_panel)
                splitter.setStretchFactor(1, 0)
                # Allow collapsing the right pane completely now that it exists
                try:
                    splitter.setCollapsible(1, True)
                except Exception:
                    pass
                logger.debug("Toolbar added to splitter")
            else:
                logger.debug("Toolbar skipped (None returned)")

            # Add splitter to the main layout
            main_layout.addWidget(splitter)
            # Provide sensible initial sizes; left expands, right starts around its preferred width
            try:
                splitter.setSizes([900, 240])
            except Exception:
                pass

            # Store references for programmatic show/hide control
            self.parent_widget._splitter = splitter
            self.parent_widget._toolbar_widget = control_panel
            
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