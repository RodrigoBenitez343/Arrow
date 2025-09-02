import logging
from PyQt5.QtWidgets import QVBoxLayout, QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from ..constants import (DARK_GREY, LIGHT_GREY, TEXT_COLOR)

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
            
            # Main layout
            logger.debug("Creating main layout")
            main_layout = QVBoxLayout(self.parent_widget)
            main_layout.setContentsMargins(5, 5, 5, 5)
            main_layout.setSpacing(5)
            
            # Control panel (skip if None)
            logger.debug("Creating control panel")
            control_panel = self.create_control_panel()
            if control_panel is not None:
                main_layout.addWidget(control_panel)
                logger.debug("Control panel added to main layout")
            else:
                logger.debug("Control panel skipped (None returned)")
            
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
            main_layout.addWidget(self.parent_widget.node_graph_widget)
            logger.debug("Node graph widget added to main layout")
            
            logger.info("UI setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up UI: {e}")
            raise
        
    def create_control_panel(self):
        """Create an empty control panel (view switching buttons removed)."""
        logger.debug("Creating empty control panel")
        try:
            # Return None since we no longer need the control panel
            logger.info("Control panel creation skipped (buttons removed)")
            return None
        except Exception as e:
            logger.error(f"Error creating control panel: {e}")
            raise