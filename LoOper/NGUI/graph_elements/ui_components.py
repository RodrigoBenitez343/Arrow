import logging
from PyQt5.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QSplitter
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from ..constants import (DARK_GREY, MEDIUM_GREY, LIGHT_GREY, TEXT_COLOR)
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
            
            # Main layout: horizontal, so right bar spans full height to the top
            logger.debug("Creating main horizontal layout with right sidebar")
            main_layout = QHBoxLayout(self.parent_widget)
            main_layout.setContentsMargins(5, 5, 5, 5)
            main_layout.setSpacing(5)
            
            # Node graph widget (placeholder - will be set by graph manager)
            logger.debug("Creating node graph widget placeholder")
            self.parent_widget.node_graph_widget = QWidget()
            self.parent_widget.node_graph_widget.setStyleSheet(f"""
                QWidget {{
                    background-color: {MEDIUM_GREY};
                    border: 1px solid {LIGHT_GREY};
                    border-radius: 8px;
                }}
            """)
            
            # Left column holds actions toolbar on top and the graph below
            left_column = QVBoxLayout()
            left_column.setContentsMargins(0, 0, 0, 0)
            left_column.setSpacing(5)

            from ..widgets.actions_toolbar import ActionsToolbar
            actions_toolbar = ActionsToolbar(self.parent_widget)
            try:
                actions_toolbar.setMaximumHeight(42)
            except Exception:
                pass
            left_column.addWidget(actions_toolbar)

            left_column.addWidget(self.parent_widget.node_graph_widget, 1)
            main_layout.addLayout(left_column, 1)

            # Right-side collapsible toolbar (spans full height to the top)
            logger.debug("Creating right-side collapsible toolbar")
            control_panel = self.create_control_panel()
            if control_panel is not None:
                main_layout.addWidget(control_panel, 0)
                self.parent_widget._toolbar_widget = control_panel
            
            # Store references
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
