import logging
from NodeGraphQt import NodeGraph
from PyQt5.QtWidgets import QVBoxLayout, QSplitter
from ..nodes import SequenceNode, ActionNode, ConditionalNode, LLMNode, TTSNode, ChainImportNode  # ContextBuilderNode deprecated

# Configure logging
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.FileHandler('graphui_debug.log')
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addHandler(console_handler)
    logger.setLevel(logging.DEBUG)


class GraphManager:
    """Manages the node graph setup and basic operations."""
    
    def __init__(self, parent_widget):
        logger.info("Initializing GraphManager")
        try:
            self.parent_widget = parent_widget
            self.node_graph = None
            logger.debug(f"GraphManager initialized with parent_widget: {type(parent_widget).__name__}")
        except Exception as e:
            logger.error(f"Error initializing GraphManager: {e}")
            raise
        
    def setup_node_graph(self):
        """Initialize and configure the node graph."""
        logger.info("Setting up node graph")
        try:
            # Create the node graph
            logger.debug("Creating NodeGraph instance")
            self.node_graph = NodeGraph()
            
            # Register custom nodes
            logger.debug("Registering custom nodes")
            self.register_nodes()
            
            # Set up the graph widget
            logger.debug("Setting up graph widget")
            self.setup_graph_widget()
            
            # Configure graph properties
            logger.debug("Configuring graph properties")
            self.configure_graph()
            
            # Store reference in parent
            logger.debug("Storing node_graph reference in parent widget")
            self.parent_widget.node_graph = self.node_graph
            
            logger.info("Node graph setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up node graph: {e}")
            raise
        
    def register_nodes(self):
        """Register all custom node types with the graph."""
        logger.debug("Registering custom node types")
        try:
            logger.debug("Registering SequenceNode")
            self.node_graph.register_node(SequenceNode)
            logger.debug("Registering ActionNode")
            self.node_graph.register_node(ActionNode)
            logger.debug("Registering ConditionalNode")
            self.node_graph.register_node(ConditionalNode)
            logger.debug("Registering LLMNode")
            self.node_graph.register_node(LLMNode)
            logger.debug("Registering TTSNode")
            self.node_graph.register_node(TTSNode)
            # DEPRECATED: Context Builder - registration removed
            # logger.debug("Registering ContextBuilderNode")
            # self.node_graph.register_node(ContextBuilderNode)
            logger.debug("Registering ChainImportNode")
            self.node_graph.register_node(ChainImportNode)
            logger.info("All custom node types registered successfully")
        except Exception as e:
            logger.error(f"Error registering custom nodes: {e}")
            raise
        
    def setup_graph_widget(self):
        """Set up the graph widget in the UI."""
        logger.debug("Setting up graph widget in UI")
        try:
            from PyQt5.QtCore import Qt
            
            # Get the graph widget
            logger.debug("Getting graph widget from node_graph")
            graph_widget = self.node_graph.widget
            
            # Set up context menu policy like the old version
            logger.debug("Setting up context menu policy")
            graph_widget.setContextMenuPolicy(Qt.CustomContextMenu)
            graph_widget.customContextMenuRequested.connect(self.parent_widget.show_custom_context_menu)
            
            # Set focus policy to ensure proper focus handling
            logger.debug("Setting focus policy for graph widget")
            graph_widget.setFocusPolicy(Qt.StrongFocus)
            
            # Replace the placeholder widget (supports both layouts and QSplitter containers)
            logger.debug("Replacing placeholder widget with graph widget")
            container = self.parent_widget.node_graph_widget.parent()
            old_widget = self.parent_widget.node_graph_widget
            layout = getattr(container, 'layout', lambda: None)()
            if isinstance(container, QSplitter):
                try:
                    idx = container.indexOf(old_widget)
                    if idx != -1:
                        container.insertWidget(idx, graph_widget)
                        old_widget.deleteLater()
                        logger.debug(f"Inserted graph widget into splitter at index {idx}")
                    else:
                        # Fallback: append if index not found
                        container.addWidget(graph_widget)
                        old_widget.deleteLater()
                        logger.warning("Old widget not found in splitter; appended graph widget")
                except Exception as e:
                    logger.error(f"Error inserting graph widget into splitter: {e}")
                    raise
            elif layout is not None:
                layout.replaceWidget(old_widget, graph_widget)
                old_widget.deleteLater()
                logger.debug("Replaced graph widget via layout.replaceWidget")
            else:
                # As a last resort, set parent and show
                graph_widget.setParent(container)
                graph_widget.show()
                old_widget.hide()
                old_widget.deleteLater()
                logger.warning("Container has no layout; set parent directly and removed placeholder")
            
            # Store reference
            logger.debug("Storing graph widget references")
            self.parent_widget.node_graph_widget = graph_widget
            self.parent_widget.view = graph_widget  # For backward compatibility
            
            logger.info("Graph widget setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up graph widget: {e}")
            raise
        
    def configure_graph(self):
        """Configure graph properties and connections."""
        logger.debug("Configuring graph properties")
        try:
            # Context menu is now handled directly in setup_graph_widget
            logger.debug("Graph configuration completed (context menu handled in setup_graph_widget)")
        except Exception as e:
            logger.error(f"Error configuring graph: {e}")
            raise
        
    def clear_graph(self):
        """Clear all nodes from the graph."""
        logger.info("Clearing graph")
        try:
            if self.node_graph:
                logger.debug("Clearing node graph session")
                self.node_graph.clear_session()
                logger.info("Graph cleared successfully")
            else:
                logger.warning("No node graph to clear")
        except Exception as e:
            logger.error(f"Error clearing graph: {e}")
            raise
            
    def auto_layout_nodes(self):
        """Automatically arrange nodes in the graph."""
        logger.info("Auto-layouting nodes")
        try:
            if self.node_graph:
                logger.debug("Applying auto layout to nodes")
                self.node_graph.auto_layout_nodes()
                logger.info("Auto layout applied successfully")
            else:
                logger.warning("No node graph for auto layout")
        except Exception as e:
            logger.error(f"Error auto-layouting nodes: {e}")
            raise
            
    def get_all_nodes(self):
        """Get all nodes in the graph."""
        logger.debug("Getting all nodes from graph")
        try:
            if self.node_graph:
                nodes = self.node_graph.all_nodes()
                logger.debug(f"Retrieved {len(nodes)} nodes from graph")
                return nodes
            else:
                logger.warning("No node graph available, returning empty list")
                return []
        except Exception as e:
            logger.error(f"Error getting all nodes: {e}")
            return []
        
    def get_node_by_id(self, node_id):
        """Get a node by its ID."""
        logger.debug(f"Getting node by ID: {node_id}")
        try:
            if self.node_graph:
                node = self.node_graph.get_node_by_id(node_id)
                if node:
                    logger.debug(f"Found node with ID {node_id}: {type(node).__name__}")
                else:
                    logger.debug(f"No node found with ID: {node_id}")
                return node
            else:
                logger.warning("No node graph available")
                return None
        except Exception as e:
            logger.error(f"Error getting node by ID {node_id}: {e}")
            return None
        
    def create_node(self, node_type, name=None, pos=None):
        """Create a new node of the specified type."""
        logger.info(f"Creating node of type: {node_type}")
        logger.debug(f"Node creation parameters - name: {name}, pos: {pos}")
        try:
            if not self.node_graph:
                logger.error("No node graph available for node creation")
                return None
            
            # Debug: Log available registered nodes
            try:
                registered_nodes = self.node_graph.registered_nodes()
                if isinstance(registered_nodes, dict):
                    logger.debug(f"Available registered nodes: {list(registered_nodes.keys())}")
                else:
                    logger.debug(f"Available registered nodes: {registered_nodes}")
            except Exception as debug_e:
                logger.debug(f"Could not get registered nodes list: {debug_e}")
                
            node = self.node_graph.create_node(node_type, name=name, pos=pos)
            if node:
                logger.info(f"Successfully created {node_type} node with ID: {node.id}")
            else:
                logger.warning(f"Failed to create {node_type} node")
            return node
        except Exception as e:
            logger.error(f"Error creating {node_type} node: {e}")
            return None
        
    def delete_node(self, node):
        """Delete a node from the graph."""
        logger.info(f"Deleting node: {node.id if node else 'None'}")
        try:
            if self.node_graph and node:
                node_id = node.id
                node_type = type(node).__name__
                logger.debug(f"Deleting {node_type} node with ID: {node_id}")
                self.node_graph.delete_node(node)
                logger.info(f"Successfully deleted {node_type} node with ID: {node_id}")
            else:
                if not self.node_graph:
                    logger.warning("No node graph available for node deletion")
                if not node:
                    logger.warning("No node provided for deletion")
        except Exception as e:
            logger.error(f"Error deleting node: {e}")
            raise
            
    def get_nodes_by_type(self, node_type):
        """Get all nodes of a specific type."""
        logger.debug(f"Getting nodes by type: {node_type.__name__ if hasattr(node_type, '__name__') else node_type}")
        try:
            all_nodes = self.get_all_nodes()
            filtered_nodes = [node for node in all_nodes if isinstance(node, node_type)]
            logger.debug(f"Found {len(filtered_nodes)} nodes of type {node_type.__name__ if hasattr(node_type, '__name__') else node_type}")
            return filtered_nodes
        except Exception as e:
            logger.error(f"Error getting nodes by type {node_type}: {e}")
            return []
        
    def connect_nodes(self, output_node, input_node, output_port=None, input_port=None):
        """Connect two nodes together."""
        logger.info(f"Connecting nodes: {output_node.id if output_node else 'None'} -> {input_node.id if input_node else 'None'}")
        try:
            if not output_node or not input_node:
                logger.warning(f"Cannot connect nodes - output_node: {output_node is not None}, input_node: {input_node is not None}")
                return False
                
            # Get output port
            if output_port is None:
                logger.debug("Getting default output port")
                output_ports = output_node.output_ports()
                if output_ports:
                    output_port = output_ports[0]
                    logger.debug(f"Using output port: {output_port.name()}")
                else:
                    logger.warning(f"No output ports available on node {output_node.id}")
                    return False
            elif isinstance(output_port, str):
                # Find output port by name
                logger.debug(f"Finding output port by name: {output_port}")
                port_name = output_port
                output_port = None
                for port in output_node.output_ports():
                    if port.name() == port_name:
                        output_port = port
                        logger.debug(f"Found output port: {port.name()}")
                        break
                if not output_port:
                    logger.warning(f"Output port '{port_name}' not found on node {output_node.id}")
                    return False
                    
            # Get input port
            if input_port is None:
                logger.debug("Getting default input port")
                input_ports = input_node.input_ports()
                if input_ports:
                    input_port = input_ports[0]
                    logger.debug(f"Using input port: {input_port.name()}")
                else:
                    logger.warning(f"No input ports available on node {input_node.id}")
                    return False
            elif isinstance(input_port, str):
                # Find input port by name
                logger.debug(f"Finding input port by name: {input_port}")
                port_name = input_port
                input_port = None
                for port in input_node.input_ports():
                    if port.name() == port_name:
                        input_port = port
                        logger.debug(f"Found input port: {port.name()}")
                        break
                if not input_port:
                    logger.warning(f"Input port '{port_name}' not found on node {input_node.id}")
                    return False
                    
            # Create connection
            logger.debug(f"Creating connection: {output_port.name()} -> {input_port.name()}")
            output_port.connect_to(input_port)
            logger.info(f"Successfully connected nodes: {output_node.id} -> {input_node.id}")
            return True
            
        except Exception as e:
            logger.error(f"Error connecting nodes: {e}")
            return False