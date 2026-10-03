import logging
from NodeGraphQt import NodeGraph
from PyQt5.QtWidgets import QVBoxLayout, QSplitter, QGraphicsView
from ..nodes import SequenceNode, WebSequenceNode, ActionNode, ConditionalNode, LLMNode, ChainImportNode, FormFillerNode, CodeNode, ContextNode, InputNode, HandleNode, MCPNode, OutputNode
from ..constants import GRAPH_PLANE

logger = logging.getLogger(__name__)


# NOTE: canvas nodes used to carry a per-node QGraphicsDropShadowEffect "glow".
# That is REMOVED: NodeGraphQt rebuilds the graph on every chain load, and a
# QGraphicsEffect on a node view is not safe across that teardown - Qt took an
# ACCESS VIOLATION (silent crash, no Python traceback) while repainting the
# rebuilt graph. Reproduced by bisecting graph rebuilds: 30 rounds with the
# glow died on the first round (exit 0xC0000005); 30 rounds without it are
# clean. (Forcing setCacheMode(NoCache) did not save it.) If the halo is wanted
# back it must be PAINTED inside the node's own paint(), not applied as an
# effect.


# --- Monkey Patch for NodeGraphQt KeyError ---
def _patch_nodegraph_moved_error():
    """
    Monkey patch for NodeGraphQt.base.graph.NodeGraph._on_nodes_moved
    to prevent KeyError when node view exists but model node is missing.
    """
    try:
        # Check if method exists and hasn't been patched
        if hasattr(NodeGraph, '_on_nodes_moved') and not hasattr(NodeGraph, '_original_on_nodes_moved'):
            NodeGraph._original_on_nodes_moved = NodeGraph._on_nodes_moved
            
            def safe_on_nodes_moved(self, node_view):
                try:
                    return self._original_on_nodes_moved(node_view)
                except KeyError:
                    # Log warning but don't crash. 
                    # This happens when a node view exists but the node is not in the model.
                    node_id = getattr(node_view, 'id', 'unknown')
                    logger.warning(f"Ignored KeyError in _on_nodes_moved for node view ID: {node_id}")
                except Exception as e:
                    # Re-raise other exceptions
                    raise e
            
            NodeGraph._on_nodes_moved = safe_on_nodes_moved
            logger.info("Applied monkey patch for NodeGraph._on_nodes_moved to fix KeyError")
    except Exception as e:
        logger.error(f"Failed to apply monkey patch to NodeGraphQt: {e}")


def _patch_nodegraph_selected_none():
    """Guard NodeGraphQt's node-selected/double-click emits against a None node.

    The viewer emits a node id and the graph resolves it with get_node_by_id();
    when that id is no longer in the model - a QUEUED select or double-click for
    a node that was deleted earlier in the same event turn - the resolved node
    is None.  NodeGraphQt then emits a ``Signal(NodeObject)`` with None, which
    raises TypeError inside a Qt slot; PyQt5 turns an unhandled slot exception
    into a fatal abort with no logged error (the app "crashes silently and
    emits no failures").  Skip the emit when the node is gone.
    """
    try:
        def make_guard(original):
            def guarded(self, node_id):
                try:
                    if self.get_node_by_id(node_id) is None:
                        return None
                except Exception:
                    return None
                return original(self, node_id)
            guarded._looper_none_guard = True
            return guarded

        for method_name in ("_on_node_selected", "_on_node_double_clicked"):
            original = getattr(NodeGraph, method_name, None)
            if original is None or getattr(original, "_looper_none_guard", False):
                continue
            setattr(NodeGraph, method_name, make_guard(original))
        logger.info("Applied monkey patch for NodeGraph node-selected None guard")
    except Exception as e:
        logger.error(f"Failed to apply NodeGraph node-selected guard: {e}")


_patch_nodegraph_moved_error()
_patch_nodegraph_selected_none()
# ---------------------------------------------


class GraphManager:
    """Manages the node graph setup and basic operations."""
    
    def __init__(self, parent_widget):
        logger.info("Initializing GraphManager")
        try:
            self.parent_widget = parent_widget
            self.node_graph = None
            self._context_port_validation_enabled = True
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

            # Repaint the WHOLE viewport whenever anything changes.
            # NodeGraphQt defaults to BoundingRectViewportUpdate (+ a
            # CacheBackground) while its node items use DeviceCoordinateCache,
            # and our nodes carry always-on drop-shadow GLOW effects that paint
            # OUTSIDE their boundingRect. Qt only invalidates an item's own
            # boundingRect on change, so a deleted/moved node left a stale
            # "after image" until some other action happened to repaint that
            # region. FullViewportUpdate is the correct mode when items paint
            # outside their rects and clears every such ghost (add / delete /
            # move / connect).
            try:
                self.node_graph.viewer().setViewportUpdateMode(
                    QGraphicsView.FullViewportUpdate)
            except Exception as e:
                logger.debug(f"Could not set full viewport update: {e}")

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
            
            # Connect port connection signal for validation
            logger.debug("Connecting port_connected signal for validation")
            self.node_graph.port_connected.connect(self._on_port_connected)
            
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
            logger.debug("Registering WebSequenceNode")
            self.node_graph.register_node(WebSequenceNode)
            logger.debug("Registering ActionNode")
            self.node_graph.register_node(ActionNode)
            logger.debug("Registering ConditionalNode")
            self.node_graph.register_node(ConditionalNode)
            logger.debug("Registering LLMNode")
            self.node_graph.register_node(LLMNode)
            logger.debug("Registering ChainImportNode")
            self.node_graph.register_node(ChainImportNode)
            logger.debug("Registering FormFillerNode")
            self.node_graph.register_node(FormFillerNode)
            logger.debug("Registering CodeNode")
            self.node_graph.register_node(CodeNode)
            logger.debug("Registering ContextNode")
            self.node_graph.register_node(ContextNode)
            logger.debug("Registering InputNode")
            self.node_graph.register_node(InputNode)
            logger.debug("Registering HandleNode")
            self.node_graph.register_node(HandleNode)
            logger.debug("Registering MCPNode")
            self.node_graph.register_node(MCPNode)
            logger.debug("Registering OutputNode")
            self.node_graph.register_node(OutputNode)
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
                        # Immediately detach and hide the placeholder to avoid a stray pane
                        try:
                            old_widget.hide()
                            old_widget.setParent(None)
                        except Exception:
                            pass
                        old_widget.deleteLater()
                        logger.debug(f"Inserted graph widget into splitter at index {idx}")
                    else:
                        # Fallback: append if index not found
                        container.addWidget(graph_widget)
                        try:
                            old_widget.hide()
                            old_widget.setParent(None)
                        except Exception:
                            pass
                        old_widget.deleteLater()
                        logger.warning("Old widget not found in splitter; appended graph widget")
                    # Ensure reasonable sizes after insertion
                    try:
                        container.setSizes([900, 240])
                    except Exception:
                        pass
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
            from NodeGraphQt.constants import PipeLayoutEnum, ViewerEnum
            # Enable cyclic/feedback connections (e.g., LLM -> Context -> LLM)
            self.node_graph.set_acyclic(False)
            try:
                self.node_graph.set_pipe_style(PipeLayoutEnum.CURVED.value)
            except Exception:
                pass
            try:
                self.node_graph.set_grid_mode(ViewerEnum.GRID_DISPLAY_SQUARES.value)
            except Exception:
                pass
            # The graph canvas fills its area and shares the frame colour
            # (GRAPH_PLANE) so there is no seam; the bars float on top of it.
            try:
                r, g, b = (int(GRAPH_PLANE.strip('#')[i:i + 2], 16) for i in (0, 2, 4))
                self.node_graph.set_background_color(r, g, b)
                self.node_graph.set_grid_color(
                    min(255, r + 12), min(255, g + 12), min(255, b + 14)
                )
            except Exception:
                pass
            logger.debug("Graph configuration completed (curved pipes, grid mode, and background colors applied)")
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
                
            snap_pos = None
            if isinstance(pos, (list, tuple)) and len(pos) >= 2:
                snap_pos = self._snap_point(int(pos[0]), int(pos[1]))
            node = self.node_graph.create_node(node_type, name=name, pos=snap_pos or pos)
            if node:
                logger.info(f"Successfully created {node_type} node with ID: {node.id}")
                try:
                    for p in node.input_ports():
                        try:
                            # Prefer underlying PortItem setter if available
                            port_item = getattr(p, '_port', None) or getattr(p, 'port', None)
                            if port_item and hasattr(port_item, 'set_multi_connection'):
                                port_item.set_multi_connection(True)
                            elif hasattr(p, 'set_multi_connection'):
                                p.set_multi_connection(True)
                            else:
                                setattr(p, '_multi_connection', True)
                        except Exception:
                            pass
                    for p in node.output_ports():
                        try:
                            port_item = getattr(p, '_port', None) or getattr(p, 'port', None)
                            if port_item and hasattr(port_item, 'set_multi_connection'):
                                port_item.set_multi_connection(True)
                            elif hasattr(p, 'set_multi_connection'):
                                p.set_multi_connection(True)
                            else:
                                setattr(p, '_multi_connection', True)
                        except Exception:
                            pass
                except Exception:
                    pass
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
                # Detach the hover button bar FIRST for EVERY delete path (key,
                # context menu, drop zone). The bar is a child graphics item of
                # the node view; removing it before the node dies keeps the
                # scene's mouse-grabber bookkeeping clean.
                try:
                    mgr = getattr(self.parent_widget, 'node_button_manager', None)
                    if mgr is not None:
                        mgr.detach(node_id)
                except Exception:
                    pass
                view = getattr(node, 'view', None)
                # Capture the region the node occupies (plus room for its glow)
                # BEFORE removal: a QGraphicsScene only invalidates the removed
                # item's own boundingRect, but the node paints a drop-shadow
                # glow OUTSIDE that rect (and caches in device coordinates), so
                # the vacated pixels otherwise lingered as an on-screen ghost
                # until some other action repainted that area.
                stale_rect = None
                try:
                    if view is not None:
                        stale_rect = view.sceneBoundingRect().adjusted(
                            -48, -48, 48, 48)
                except Exception:
                    stale_rect = None
                # Drop the node's graphics effect first: leaving a
                # QGraphicsDropShadowEffect attached while NodeGraphQt tears the
                # item down can hard-crash the app (no traceback, log just ends)
                # right after an otherwise successful delete.
                try:
                    if view is not None and view.graphicsEffect() is not None:
                        view.setGraphicsEffect(None)
                except Exception:
                    pass
                self.node_graph.delete_node(node)
                logger.info(f"Successfully deleted {node_type} node with ID: {node_id}")
                # NodeGraphQt leaves the removed view in the scene's spatial
                # index: its scene is cleared (view.scene() is None) but
                # scene.items() still returns it. Every later mouse press then
                # re-grabs that orphan, and Qt floods "QGraphicsItem::ungrabMouse:
                # cannot ungrab mouse without scene" (one warning per mouse
                # event, wedging the UI). Hiding the orphan drops it from the
                # index and ends the flood (verified). This is unrelated to the
                # hover button bar - it reproduces with a pure NodeGraphQt
                # delete too.
                try:
                    if view is not None and view.scene() is None:
                        view.setVisible(False)
                except Exception:
                    pass
                # Force the vacated region to repaint so no ghost is left behind.
                try:
                    if stale_rect is not None:
                        scene = self.node_graph.scene()
                        if scene is not None:
                            scene.update(stale_rect)
                    viewer = self.node_graph.viewer()
                    if viewer is not None:
                        viewer.viewport().update()
                except Exception:
                    pass
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
                    ports = output_node.output_ports()
                    output_port = ports[0] if ports else None
                    if not output_port:
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
                    ports = input_node.input_ports()
                    input_port = ports[0] if ports else None
                    if not input_port:
                        return False
                    
            # ── Restrict LLM 'tools' port to Chain Import nodes only ──
            # MCP/code logic must be wrapped inside a chain and imported as the
            # tool, so the LLM node only ever calls chain-import subroutines.
            # This guard covers programmatic wiring (clipboard paste, chain
            # load); the interactive drag path is handled in _on_port_connected.
            try:
                if input_port.name() == 'tools':
                    _out_identifier = getattr(output_node, '__identifier__', '') or ''
                    if _out_identifier != 'chain_import':
                        logger.warning(
                            "Blocked tools-port connection: only Chain Import "
                            "nodes can be used as LLM tools (got identifier "
                            "'%s') — wrap MCP/code logic in a chain and import it",
                            _out_identifier,
                        )
                        return False
            except Exception:
                pass

            # Create connection
            logger.debug(f"Creating connection: {output_port.name()} -> {input_port.name()}")
            output_port.connect_to(input_port)
            logger.info(f"Successfully connected nodes: {output_node.id} -> {input_node.id}")
            return True

        except Exception as e:
            logger.error(f"Error connecting nodes: {e}")
            return False

    def _snap_point(self, x, y, grid=20):
        try:
            sx = int(round(x / float(grid)) * grid)
            sy = int(round(y / float(grid)) * grid)
            return [sx, sy]
        except Exception:
            return [x, y]

    def _on_port_connected(self, input_port, output_port):
        """
        Validate port connections - restrict Context node 'context' output to LLM nodes only.
        
        The NodeGraphQt port_connected signal emits (input_port, output_port).
        - input_port: The port that receives the connection (on the target node)
        - output_port: The port that sends data (on the source node)
        """
        if not self._context_port_validation_enabled:
            return
            
        try:
            output_node = output_port.node()
            input_node = input_port.node()

            # Get node identifiers
            output_identifier = getattr(output_node, '__identifier__', '')
            input_identifier = getattr(input_node, '__identifier__', '')

            # Context nodes emit JSON data — allow connection to any node
            if output_identifier == 'context':
                pass

            # ── Restrict LLM 'tools' port to Chain Import nodes only ──
            # The interactive drag path calls port.connect_to() directly and
            # bypasses connect_nodes(), so undo the connection here (mirrors
            # the existing Context-port validation pattern).
            try:
                if input_port.name() == 'tools':
                    if output_identifier != 'chain_import':
                        logger.warning(
                            "Blocked tools-port connection: only Chain Import "
                            "nodes can be used as LLM tools (got identifier "
                            "'%s') — wrap MCP/code logic in a chain and import it",
                            output_identifier,
                        )
                        self._disconnect_ports(output_port, input_port)
                        try:
                            from PyQt5.QtWidgets import QMessageBox
                            QMessageBox.warning(
                                getattr(self, 'parent_widget', None),
                                "Invalid Tool Connection",
                                "Only Chain Import nodes can be used as LLM tools.\n\n"
                                "Wrap MCP/code logic in a chain and import it as the tool.",
                            )
                        except Exception:
                            pass
                        return
            except Exception as e:
                logger.debug(f"Tools-port validation error: {e}")
        except Exception as e:
            logger.debug(f"Connection validation error: {e}")

    def _disconnect_ports(self, output_port, input_port):
        """Safely disconnect two ports."""
        try:
            if output_port.connected_ports():
                output_port.disconnect_from(input_port)
                logger.info("Disconnected invalid Context->non-LLM connection")
        except Exception as e:
            logger.debug(f"Error disconnecting ports: {e}")
