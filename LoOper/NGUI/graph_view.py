# NGUI/graph_view.py

import os
import logging
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import pyqtSignal
from .constants import *
from .node_context_menu import NodeContextMenuManager
from .graph_elements import (
    UIComponents,
    GraphManager,
    NodeOperations,
    ViewManager,
    ConfigManager,
    ExportUtils,
    NodeTransforms
)
# Branching is handled automatically during workflow execution

# Set up logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Create file handler if it doesn't exist
if not logger.handlers:
    handler = logging.FileHandler('graphui_debug.log')
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    # Also add console handler for immediate feedback
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

class GraphViewWidget(QWidget):
    """Widget for visualizing and managing sequence chains using NodeGraphQt"""
    
    node_selected = pyqtSignal(int, int)
    sequence_selected = pyqtSignal(int)
    
    def __init__(self, parent=None):
        logger.info("Initializing GraphViewWidget")
        try:
            super().__init__(parent)
            self.chain_config = []
            self.sequence_nodes = []
            self.action_nodes = []
            self.conditional_nodes = []
            self.llm_nodes = []
            self.tts_nodes = []
            self.current_view = 'sequence'  # Track current view: 'sequence' or 'action'
            self.current_sequence_idx = None  # Track currently selected sequence
            self.saved_sequence_state = None  # Store sequence view state when switching to action view
            
            # Set up folder paths
            project_root = os.path.dirname(os.path.dirname(__file__))
            self.sequences_folder = os.path.join(project_root, "sequences")
            self.chains_folder = os.path.join(project_root, "chains")
            
            # Ensure both folders exist
            os.makedirs(self.sequences_folder, exist_ok=True)
            os.makedirs(self.chains_folder, exist_ok=True)
            
            logger.debug(f"Set sequences_folder to: {self.sequences_folder}")
            logger.debug(f"Set chains_folder to: {self.chains_folder}")
            
            # Initialize modular components
            logger.debug("Initializing modular components...")
            self.ui_components = UIComponents(self)
            logger.debug("UIComponents initialized")
            self.graph_manager = GraphManager(self)
            logger.debug("GraphManager initialized")
            self.node_operations = NodeOperations(self)
            logger.debug("NodeOperations initialized")
            self.view_manager = ViewManager(self)
            logger.debug("ViewManager initialized")
            self.config_manager = ConfigManager(self)
            logger.debug("ConfigManager initialized")
            self.export_utils = ExportUtils(self)
            logger.debug("ExportUtils initialized")
            self.node_transforms = NodeTransforms(self)
            logger.debug("NodeTransforms initialized")
            self.context_menu_manager = NodeContextMenuManager(self)
            logger.debug("NodeContextMenuManager initialized")
            
            # Branching is handled automatically during workflow execution
            
            logger.debug("Setting up UI...")
            self.setup_ui()
            logger.debug("Setting up node graph...")
            self.setup_node_graph()
            logger.info("GraphViewWidget initialization completed successfully")
        except Exception as e:
            logger.error(f"Error during GraphViewWidget initialization: {str(e)}", exc_info=True)
            raise
        
    def setup_ui(self):
        """Setup the UI for the node graph widget"""
        logger.debug("Setting up UI components")
        try:
            self.ui_components.setup_ui()
            logger.debug("UI setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up UI: {str(e)}", exc_info=True)
            raise

    def setup_node_graph(self):
        """Setup the NodeGraphQt graph"""
        logger.debug("Setting up node graph")
        try:
            self.graph_manager.setup_node_graph()
            # Store reference to the node graph for easy access
            self.node_graph = self.graph_manager.node_graph
            self.graph = self.node_graph  # For backward compatibility
            logger.debug("Node graph setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up node graph: {str(e)}", exc_info=True)
            raise

    def show_custom_context_menu(self, pos):
        """Show the context menu for the graph"""
        logger.debug(f"Showing custom context menu at position: {pos}")
        try:
            # Get selected nodes
            selected_nodes = self.node_graph.selected_nodes()
            logger.debug(f"Selected nodes: {len(selected_nodes)}")
            # Get the first selected node if any
            node = selected_nodes[0] if selected_nodes else None
            if node:
                logger.debug(f"Context menu for node: {node.name()}")
            # Convert position to global coordinates
            global_pos = self.view.mapToGlobal(pos)
            self.context_menu_manager.show_context_menu(global_pos, node)
            logger.debug("Context menu displayed successfully")
        except Exception as e:
            logger.error(f"Error showing context menu: {str(e)}", exc_info=True)


    
    def add_conditional_node(self, position=None):
        """Add a new conditional node to the graph with comprehensive condition types"""
        logger.debug(f"Adding conditional node at position: {position}")
        try:
            result = self.node_operations.add_conditional_node(position)
            logger.info(f"Conditional node added successfully: {result.name() if result else 'None'}")
            return result
        except Exception as e:
            logger.error(f"Error adding conditional node: {str(e)}", exc_info=True)
            return None
    
    def add_llm_node(self, position=None):
        """Add a new LLM node to the graph"""
        logger.debug(f"Adding LLM node at position: {position}")
        try:
            result = self.node_operations.add_llm_node(position)
            logger.info(f"LLM node added successfully: {result.name() if result else 'None'}")
            return result
        except Exception as e:
            logger.error(f"Error adding LLM node: {str(e)}", exc_info=True)
            return None
    
    def add_tts_node(self, position=None):
        """Add a new TTS node to the graph"""
        logger.debug(f"Adding TTS node at position: {position}")
        try:
            result = self.node_operations.add_tts_node(position)
            logger.info(f"TTS node added successfully: {result.name() if result else 'None'}")
            return result
        except Exception as e:
            logger.error(f"Error adding TTS node: {str(e)}", exc_info=True)
            return None
            
    def add_context_builder_node(self, position=None):
        """Add a new Context Builder node to the graph"""
        logger.debug(f"Adding Context Builder node at position: {position}")
        try:
            result = self.node_operations.add_context_builder_node(position)
            logger.info(f"Context Builder node added successfully: {result.name() if result else 'None'}")
            return result
        except Exception as e:
            logger.error(f"Error adding Context Builder node: {str(e)}", exc_info=True)
            return None
    
    def add_sequence(self):
        """Add a new sequence node to the graph"""
        logger.debug("Adding sequence node")
        try:
            self.node_operations.add_sequence()
            logger.info("Sequence node added successfully")
        except Exception as e:
            logger.error(f"Error adding sequence node: {str(e)}", exc_info=True)
    
    def add_action(self, sequence_node):
        """Add a new action to a sequence node"""
        logger.debug(f"Adding action to sequence node: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.add_action(sequence_node)
            logger.info("Action added successfully")
        except Exception as e:
            logger.error(f"Error adding action: {str(e)}", exc_info=True)

    def show_sequence_view(self):
        """Display the sequence nodes in the graph"""
        logger.debug("Switching to sequence view")
        try:
            self.view_manager.show_sequence_view()
            logger.info("Sequence view displayed successfully")
        except Exception as e:
            logger.error(f"Error showing sequence view: {str(e)}", exc_info=True)

    def show_action_view(self):
        """Display the action nodes of a selected sequence"""
        logger.debug("Switching to action view")
        try:
            self.view_manager.show_action_view()
            logger.info("Action view displayed successfully")
        except Exception as e:
            logger.error(f"Error showing action view: {str(e)}", exc_info=True)

    def auto_layout_nodes(self):
        """Arrange the nodes in the graph automatically"""
        logger.debug("Auto-layouting nodes")
        try:
            self.graph_manager.auto_layout_nodes()
            logger.info("Auto-layout completed successfully")
        except Exception as e:
            logger.error(f"Error during auto-layout: {str(e)}", exc_info=True)

    def clear_graph(self):
        """Clear all nodes from the graph"""
        logger.debug("Clearing graph")
        try:
            self.graph_manager.clear_graph()
            self._validate_conditional_nodes()
            logger.info("Graph cleared successfully")
        except Exception as e:
            logger.error(f"Error clearing graph: {str(e)}", exc_info=True)

    def _validate_conditional_nodes(self):
        """Remove any invalid entries from conditional_nodes list"""
        logger.debug("Validating conditional nodes")
        try:
            original_count = len(self.conditional_nodes)
            self.conditional_nodes = [node for node in self.conditional_nodes 
                                    if node is not None and hasattr(node, 'id') and node in self.node_graph.all_nodes()]
            removed_count = original_count - len(self.conditional_nodes)
            if removed_count > 0:
                logger.info(f"Removed {removed_count} invalid conditional nodes")
            logger.debug("Conditional nodes validation completed")
        except Exception as e:
            logger.error(f"Error validating conditional nodes: {str(e)}", exc_info=True)

    def get_chain_config(self):
        """Constructs and returns the chain configuration from the graph"""
        logger.debug("Getting chain configuration")
        try:
            config = self.config_manager.get_chain_config()
            logger.info(f"Chain configuration retrieved with {len(config.get('sequences', []))} sequences")
            return config
        except Exception as e:
            logger.error(f"Error getting chain config: {str(e)}", exc_info=True)
            return {"sequences": [], "conditional_nodes": [], "llm_nodes": []}

    def save_current_state(self):
        """Save the current graph state for restoration later"""
        logger.debug("Saving current state")
        try:
            result = self.config_manager.save_current_state()
            logger.info("Current state saved successfully")
            return result
        except Exception as e:
            logger.error(f"Error saving current state: {str(e)}", exc_info=True)
            return None

    def load_chain(self, file_path):
        """Loads a chain configuration from a file and builds the graph"""
        logger.debug(f"Loading chain from: {file_path}")
        try:
            self.config_manager.load_chain(file_path)
            logger.info(f"Chain loaded successfully from {file_path}")
        except Exception as e:
            logger.error(f"Error loading chain from {file_path}: {str(e)}", exc_info=True)

    def save_chain(self, file_path):
        """Saves the current graph layout to a file"""
        logger.debug(f"Saving chain to: {file_path}")
        try:
            self.config_manager.save_chain(file_path)
            logger.info(f"Chain saved successfully to {file_path}")
        except Exception as e:
            logger.error(f"Error saving chain to {file_path}: {str(e)}", exc_info=True)

    def build_graph_from_config(self, config=None):
        """Builds the node graph from the loaded chain configuration"""
        logger.debug("Building graph from configuration")
        try:
            self.config_manager.build_graph_from_config(config)
            logger.info("Graph built successfully from configuration")
        except Exception as e:
            logger.error(f"Error building graph from config: {str(e)}", exc_info=True)

    def export_as_executable(self):
        """Export the chain as an executable batch file"""
        logger.debug("Exporting chain as executable")
        try:
            self.export_utils.export_as_executable()
            logger.info("Chain exported as executable successfully")
        except Exception as e:
            logger.error(f"Error exporting chain as executable: {str(e)}", exc_info=True)

    def export_chain(self, bat_path, config):
        """Export the chain as a batch file"""
        logger.debug(f"Exporting chain to: {bat_path}")
        try:
            self.export_utils.export_chain(bat_path, config)
            logger.info(f"Chain exported successfully to {bat_path}")
        except Exception as e:
            logger.error(f"Error exporting chain to {bat_path}: {str(e)}", exc_info=True)


    def edit_conditional(self, node):
        """Edit a conditional node with comprehensive condition types"""
        logger.debug(f"Editing conditional node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_conditional(node)
            logger.info("Conditional node edited successfully")
        except Exception as e:
            logger.error(f"Error editing conditional node: {str(e)}", exc_info=True)
    
    def edit_llm(self, node):
        """Edit an LLM node configuration"""
        logger.debug(f"Editing LLM node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_llm(node)
            logger.info("LLM node edited successfully")
        except Exception as e:
            logger.error(f"Error editing LLM node: {str(e)}", exc_info=True)
    
    def edit_tts_node(self, node):
        """Edit a TTS node configuration"""
        logger.debug(f"Editing TTS node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_tts(node)
            logger.info("TTS node edited successfully")
        except Exception as e:
            logger.error(f"Error editing TTS node: {str(e)}", exc_info=True)
    
    def edit_context_builder_node(self, node):
        """Edit a Context Builder node configuration"""
        logger.debug(f"Editing Context Builder node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_context_builder_node(node)
            logger.info("Context Builder node edited successfully")
        except Exception as e:
            logger.error(f"Error editing Context Builder node: {str(e)}", exc_info=True)
    
    def delete_node(self, node):
        """Delete a node from the graph"""
        logger.debug(f"Deleting node: {node.name() if node else 'None'}")
        try:
            self.node_operations.delete_node(node)
            logger.info("Node deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting node: {str(e)}", exc_info=True)
    
    def delete_action_node(self, node):
        """Delete an action node from the graph and update the JSON file"""
        logger.debug(f"Deleting action node: {node.name() if node else 'None'}")
        try:
            self.node_operations.delete_action_node(node)
            logger.info("Action node deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting action node: {str(e)}", exc_info=True)
    
    def record_new_sequence(self):
        """Start recording a new sequence"""
        logger.debug("Starting new sequence recording")
        try:
            self.node_operations.record_new_sequence()
            logger.info("New sequence recording started successfully")
        except Exception as e:
            logger.error(f"Error starting new sequence recording: {str(e)}", exc_info=True)
    
    def record_single_action(self, sequence_node):
        """Record a single action and add it to the specified sequence"""
        logger.debug(f"Recording single action for sequence: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.record_single_action(sequence_node)
            logger.info("Single action recorded successfully")
        except Exception as e:
            logger.error(f"Error recording single action: {str(e)}", exc_info=True)
    
    def remove_action_from_sequence(self, sequence_node):
        """Remove an action from the specified sequence"""
        logger.debug(f"Removing action from sequence: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.remove_action_from_sequence(sequence_node)
            logger.info("Action removed from sequence successfully")
        except Exception as e:
            logger.error(f"Error removing action from sequence: {str(e)}", exc_info=True)
    
    def remove_sequence_from_chain(self, sequence_node):
        """Remove a sequence from the chain configuration"""
        logger.debug(f"Removing sequence from chain: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.remove_sequence_from_chain(sequence_node)
            logger.info("Sequence removed from chain successfully")
        except Exception as e:
            logger.error(f"Error removing sequence from chain: {str(e)}", exc_info=True)
    
    def add_conditional_branch_to_sequence(self, sequence_node):
        """Add a conditional node that controls execution flow to the sequence node"""
        logger.debug(f"Adding conditional branch to sequence: {sequence_node.name() if sequence_node else 'None'}")
        try:
            # Get sequence node position
            seq_pos = sequence_node.pos()
            # Position conditional node to the left of the sequence
            conditional_pos = [seq_pos[0] - 200, seq_pos[1]]
            
            # Create conditional node
            conditional_node = self.add_conditional_node(conditional_pos)
            
            if conditional_node:
                # Set conditional node name based on sequence
                sequence_name = sequence_node.get_property('sequence_name') or sequence_node.name()
                conditional_node.set_name(f"Branch: {sequence_name}")
                
                # Connect conditional 'true' output to sequence input
                conditional_true_output = conditional_node.get_output('true')
                sequence_input = sequence_node.get_input('input')
                
                if conditional_true_output and sequence_input:
                    conditional_true_output.connect_to(sequence_input)
                    logger.info(f"Connected conditional branch to sequence: {sequence_name}")
                
                return conditional_node
            else:
                logger.error("Failed to create conditional node")
                return None
                
        except Exception as e:
            logger.error(f"Error adding conditional branch to sequence: {str(e)}", exc_info=True)
            return None

    def add_conditional_branch_to_llm(self, llm_node):
        """Add a conditional node that controls execution flow to the LLM node"""
        logger.debug(f"Adding conditional branch to LLM: {llm_node.name() if llm_node else 'None'}")
        try:
            # Get LLM node position
            llm_pos = llm_node.pos()
            # Position conditional node to the left of the LLM
            conditional_pos = [llm_pos[0] - 200, llm_pos[1]]
            
            # Create conditional node
            conditional_node = self.add_conditional_node(conditional_pos)
            
            if conditional_node:
                # Set conditional node name based on LLM
                llm_prompt = llm_node.get_property('prompt') or llm_node.name()
                prompt_preview = llm_prompt[:20] + "..." if len(llm_prompt) > 20 else llm_prompt
                conditional_node.set_name(f"Branch: {prompt_preview}")
                
                # Connect conditional 'true' output to LLM input
                conditional_true_output = conditional_node.get_output('true')
                llm_input = llm_node.get_input('input_1')
                
                if conditional_true_output and llm_input:
                    conditional_true_output.connect_to(llm_input)
                    logger.info(f"Connected conditional branch to LLM: {prompt_preview}")
                
                return conditional_node
            else:
                logger.error("Failed to create conditional node")
                return None
                
        except Exception as e:
            logger.error(f"Error adding conditional branch to LLM: {str(e)}", exc_info=True)
            return None
    
    # Auto-branching is now handled automatically during workflow execution