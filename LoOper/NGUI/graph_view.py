# NGUI/graph_view.py

import os
import sys
import logging
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QCursor
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
from .graph_elements.dnd_manager import DragDropManager
from .graph_elements.clipboard_manager import ClipboardManager
from .i18n import _
# Branching is handled automatically during workflow execution

# Set up logger for this module
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class GraphViewWidget(QWidget):
    """Widget for visualizing and managing sequence chains using NodeGraphQt"""
    
    node_selected = pyqtSignal(int, int)
    sequence_selected = pyqtSignal(int)
    # Emitted with the selected Code node graph object when exactly one code
    # node is selected (Code Node Studio dock).
    code_node_selected = pyqtSignal(object)
    
    def __init__(self, parent=None):
        logger.info("Initializing GraphViewWidget")
        try:
            super().__init__(parent)
            # Initialize clipboard for copied nodes
            self.copied_nodes = []
            # Last code-node id shown in the Code Node Studio (dedupe guard)
            self._last_code_selected_id = None
            # Initialize paste counter for positioning
            self._paste_counter = 0
            # Track last context menu scene position for paste anchoring
            self._last_context_scene_pos = None
            self.chain_config = {"sequences": [], "conditional_nodes": [], "llm_nodes": [], "chain_import_nodes": [], "form_filler_nodes": [], "code_nodes": [], "container_nodes": [], "context_nodes": [], "input_nodes": [], "handle_nodes": [], "mcp_nodes": [], "web_sequences": []}
            self.sequence_nodes = []
            self.action_nodes = []
            self.conditional_nodes = []
            self.llm_nodes = []
            self.form_filler_nodes = []
            self.code_nodes = []
            self.container_nodes = []
            self.context_nodes = []
            self.input_nodes = []
            self.current_view = 'sequence'  # Track current view: 'sequence' or 'action'
            self.current_sequence_idx = None  # Track currently selected sequence
            self.saved_sequence_state = None  # Store sequence view state when switching to action view
            
            # Initialize ready flag
            self._initialization_complete = False
            
            # Set up folder paths
            if getattr(sys, 'frozen', False):
                # In frozen builds, we want paths relative to the executable, 
                # not the temporary _MEIPASS directory.
                project_root = os.path.dirname(sys.executable)
            else:
                # In dev mode, use the repository root
                project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            
            self.sequences_folder = os.path.join(project_root, "sequences")
            self.chains_folder = os.path.join(project_root, "chains")
            self.web_sequences_folder = os.path.join(project_root, "web_sequences")
            
            # Ensure all folders exist
            os.makedirs(self.sequences_folder, exist_ok=True)
            os.makedirs(self.chains_folder, exist_ok=True)
            os.makedirs(self.web_sequences_folder, exist_ok=True)
            
            logger.debug(f"Set sequences_folder to: {self.sequences_folder}")
            logger.debug(f"Set chains_folder to: {self.chains_folder}")
            logger.debug(f"Set web_sequences_folder to: {self.web_sequences_folder}")
            
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
            self.dnd_manager = DragDropManager(self)
            self.clipboard_manager = ClipboardManager(self)
            # Drag tracking for wrap-into-chain detection
            self._drag_start_pos = None
            self._drag_active = False
            
            # Branching is handled automatically during workflow execution
            
            logger.debug("Setting up UI...")
            self.setup_ui()
            logger.debug("Setting up node graph...")
            self.setup_node_graph()
            
            # Mark initialization as complete
            self._initialization_complete = True
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

    def set_toolbar_visible(self, visible: bool):
        """Show or hide the right-side toolbar pane completely.
        When hidden, the splitter assigns zero width to the toolbar.
        When shown, it restores a reasonable width.
        """
        try:
            splitter = getattr(self, '_splitter', None)
            toolbar = getattr(self, '_toolbar_widget', None)
            if splitter is None or toolbar is None:
                return
            # Ensure the toolbar is part of the splitter
            idx = splitter.indexOf(toolbar)
            if idx == -1:
                return
            current_sizes = splitter.sizes()
            if not current_sizes or len(current_sizes) < 2:
                current_sizes = [900, 120]
            if visible:
                splitter.setSizes([max(1, current_sizes[0]), 160])
            else:
                # collapse right pane to zero width
                splitter.setSizes([max(1, sum(current_sizes)), 0])
        except Exception as e:
            logger.error(f"Error toggling toolbar visibility: {e}")

    def setup_node_graph(self):
        """Setup the NodeGraphQt graph"""
        logger.debug("Setting up node graph")
        try:
            self.graph_manager.setup_node_graph()
            # Store reference to the node graph for easy access
            self.node_graph = self.graph_manager.node_graph
            self.graph = self.node_graph  # For backward compatibility
            # Enable drag-and-drop of node types from the toolbar
            try:
                self._setup_drag_drop_dnd()
            except Exception as e:
                logger.warning(f"Drag-and-drop setup failed (non-critical): {e}")
            try:
                from .graph_elements.node_button_bar import NodeButtonBarManager
                self.node_button_manager = NodeButtonBarManager(self)
                self.node_button_manager.install()
            except Exception as e:
                logger.debug(f"Non-critical: node button manager init failed: {e}")
            logger.debug("Node graph setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up node graph: {str(e)}", exc_info=True)
            raise

    def resizeEvent(self, event):
        """Handle resize events"""
        super().resizeEvent(event)

    def keyPressEvent(self, event):
        """Handle keyboard events for copy/paste functionality"""
        from PyQt5.QtCore import Qt
        
        if event.key() == Qt.Key_C and event.modifiers() & Qt.ControlModifier:
            # Ctrl+C - Copy selected nodes
            self.copy_selected_nodes()
            event.accept()
        elif event.key() == Qt.Key_V and event.modifiers() & Qt.ControlModifier:
            # Ctrl+V - Paste copied nodes at mouse cursor if possible
            paste_pos = None
            try:
                if hasattr(self, "view") and hasattr(self.view, "mapToScene") and hasattr(self.view, "mapFromGlobal"):
                    local_pos = self.view.mapFromGlobal(QCursor.pos())
                    scene_point = self.view.mapToScene(local_pos)
                    paste_pos = [int(scene_point.x()), int(scene_point.y())]
            except Exception:
                paste_pos = None
            self.paste_copied_nodes(paste_pos=paste_pos)
            event.accept()
        elif event.key() == Qt.Key_Delete:
            # Supr/Delete - Remove selected nodes
            self.delete_selected_nodes()
            event.accept()
        elif event.key() == Qt.Key_Z and event.modifiers() & Qt.ControlModifier:
            # Ctrl+Z - Undo last operation
            self.undo()
            event.accept()
        elif event.key() == Qt.Key_Y and event.modifiers() & Qt.ControlModifier:
            # Ctrl+Y - Redo last undone operation
            self.redo()
            event.accept()
        else:
            super().keyPressEvent(event)

    def undo(self):
        """Undo the last graph operation via NodeGraphQt's undo stack."""
        try:
            stack = self.node_graph.undo_stack()
            if stack and stack.canUndo():
                stack.undo()
        except Exception as e:
            logger.debug(f"Undo failed: {e}")

    def redo(self):
        """Redo the last undone graph operation via NodeGraphQt's undo stack."""
        try:
            stack = self.node_graph.undo_stack()
            if stack and stack.canRedo():
                stack.redo()
        except Exception as e:
            logger.debug(f"Redo failed: {e}")

    def focusInEvent(self, event):
        """Handle focus in events and delegate to the graph view if available"""
        try:
            # Delegate focus to the graph view if it exists and is ready
            if hasattr(self, 'view') and self.view is not None:
                self.view.setFocus()
            super().focusInEvent(event)
        except Exception as e:
            logger.debug(f"Focus event handling error (non-critical): {str(e)}")
            # Continue with default behavior even if delegation fails
            super().focusInEvent(event)

    def focusOutEvent(self, event):
        """Handle focus out events"""
        try:
            super().focusOutEvent(event)
        except Exception as e:
            logger.debug(f"Focus out event handling error (non-critical): {str(e)}")
            # Continue with default behavior
            super().focusOutEvent(event)

    def _setup_drag_drop_dnd(self):
        """Install drag-and-drop using the dedicated manager (backward compatible)."""
        try:
            self.dnd_manager.install()
            logger.debug("Drag-and-drop event filter installed via DragDropManager")
        except Exception as e:
            logger.error(f"Error installing drag-and-drop filter: {e}")

    def eventFilter(self, obj, event):
        """Handle drag enter and drop events on the graph widget."""
        try:
            from PyQt5.QtCore import QEvent
            from PyQt5.QtGui import QCursor
            handled = self.dnd_manager.handle_event(obj, event)
            if handled:
                return True
            elif event.type() == QEvent.MouseButtonPress:
                try:
                    self._drag_start_pos = event.globalPos()
                    self._drag_active = False
                except Exception:
                    pass
            elif event.type() == QEvent.MouseMove:
                try:
                    if self._drag_start_pos is not None:
                        delta = event.globalPos() - self._drag_start_pos
                        if delta.manhattanLength() >= 8:
                            self._drag_active = True
                except Exception:
                    pass
            elif event.type() == QEvent.MouseButtonRelease:
                try:
                    selected = self.node_graph.selected_nodes() if hasattr(self, 'node_graph') else []
                    for n in selected:
                        try:
                            x, y = n.pos()
                            sx, sy = self._snap_point(x, y)
                            if sx != x or sy != y:
                                n.set_pos(sx, sy)
                        except Exception:
                            pass
                    # Only check for wrap if the user was actually dragging (not just selecting)
                    if selected and self._drag_active:
                        self._check_wrap_into_chain(selected)
                    # Notify the Code Node Studio when exactly one Code node is selected
                    if not self._drag_active and len(selected) == 1:
                        try:
                            _n = selected[0]
                            if str(getattr(_n, '__identifier__', '') or '').lower() in (
                                'code', 'codencode', 'code.codencode',
                            ) or str(getattr(_n, '__identifier__', '') or '').lower().endswith('codencode'):
                                _nid = str(getattr(_n, 'id', '') or id(_n))
                                if _nid != getattr(self, '_last_code_selected_id', None):
                                    self._last_code_selected_id = _nid
                                    self.code_node_selected.emit(_n)
                        except Exception:
                            pass
                except Exception:
                    pass
                finally:
                    self._drag_start_pos = None
                    self._drag_active = False
        except Exception as e:
            logger.debug(f"Event filter non-critical error: {e}")
        return super().eventFilter(obj, event)
    
    def get_property_whitelist(self, node_type: str):
        return self.clipboard_manager.get_property_whitelist(node_type)

    def copy_selected_nodes(self):
        self.clipboard_manager.copy_selected_nodes()

    def paste_copied_nodes(self, paste_pos=None):
        return self.clipboard_manager.paste_copied_nodes(paste_pos)
    
    def show_custom_context_menu(self, pos):
        """Show the context menu for the graph"""
        logger.debug(f"Showing custom context menu at position: {pos}")
        try:
            # Check if view is properly initialized
            if not hasattr(self, 'view') or self.view is None:
                logger.warning("View not initialized yet, cannot show context menu")
                return
                
            # Get selected nodes
            selected_nodes = self.node_graph.selected_nodes()
            logger.debug(f"Selected nodes: {len(selected_nodes)}")
            # Get the first selected node if any
            node = selected_nodes[0] if selected_nodes else None
            if node:
                logger.debug(f"Context menu for node: {node.name()}")
            # Convert position to global coordinates
            global_pos = self.view.mapToGlobal(pos)
            # Capture scene position for paste anchoring
            try:
                self._last_context_scene_pos = self.view.mapToScene(pos)
            except Exception:
                self._last_context_scene_pos = None
            self.context_menu_manager.show_context_menu(global_pos, node)
            logger.debug("Context menu displayed successfully")
        except Exception as e:
            logger.error(f"Error showing context menu: {str(e)}", exc_info=True)




    def _snap_point(self, x, y, grid=20):
        try:
            sx = int(round(x / float(grid)) * grid)
            sy = int(round(y / float(grid)) * grid)
            return [sx, sy]
        except Exception:
            return [x, y]

    def _check_wrap_into_chain(self, selected_nodes):
        """Check if any selected nodes overlap with an empty Chain Import node.
        If they do, trigger the 'wrap into chain' workflow."""
        try:
            from .nodes_resources.chain_import_node import ChainImportNode
            if not selected_nodes:
                return

            # Find all empty Chain Import nodes on the graph
            all_nodes = self.graph_manager.get_all_nodes()
            empty_chain_nodes = []
            for node in all_nodes:
                if not isinstance(node, ChainImportNode):
                    continue
                # An "empty" chain node has no chain_file configured
                chain_file = node.get_property('chain_file') or ''
                if not chain_file:
                    empty_chain_nodes.append(node)

            if not empty_chain_nodes:
                return

            # For each empty chain node, check if any selected node overlaps with it
            for chain_node in empty_chain_nodes:
                # Get chain node bounding rect
                chain_x, chain_y = chain_node.pos()
                chain_rect = getattr(chain_node, 'rect', None)
                if chain_rect:
                    chain_w = int(getattr(chain_rect, 'width', lambda: chain_rect.width())()) if callable(getattr(chain_rect, 'width', None)) else int(chain_rect.width())
                    chain_h = int(getattr(chain_rect, 'height', lambda: chain_rect.height())()) if callable(getattr(chain_rect, 'height', None)) else int(chain_rect.height())
                else:
                    chain_w, chain_h = 140, 60  # default node size

                # Expand the target area slightly for easier dropping
                margin = 30
                cx1 = chain_x - margin
                cy1 = chain_y - margin
                cx2 = chain_x + chain_w + margin
                cy2 = chain_y + chain_h + margin

                # Check if any selected node overlaps with this chain import node
                overlaps = False
                for sel_node in selected_nodes:
                    if sel_node is chain_node:
                        continue
                    if isinstance(sel_node, ChainImportNode):
                        continue  # nested chain imports not supported
                    sx, sy = sel_node.pos()
                    sel_rect = getattr(sel_node, 'rect', None)
                    if sel_rect:
                        sw = int(getattr(sel_rect, 'width', lambda: sel_rect.width())()) if callable(getattr(sel_rect, 'width', None)) else int(sel_rect.width())
                        sh = int(getattr(sel_rect, 'height', lambda: sel_rect.height())()) if callable(getattr(sel_rect, 'height', None)) else int(sel_rect.height())
                    else:
                        sw, sh = 140, 60

                    # Check rectangle overlap
                    if (sx < cx2 and sx + sw > cx1 and
                            sy < cy2 and sy + sh > cy1):
                        overlaps = True
                        break

                if overlaps:
                    logger.info(
                        f"Detected node overlap with empty Chain Import node {chain_node.id}, "
                        f"triggering wrap"
                    )
                    self.node_operations.wrap_selected_nodes_into_chain(
                        selected_nodes, chain_node
                    )
                    return

        except Exception as e:
            logger.debug(f"_check_wrap_into_chain error (non-critical): {e}")

    def add_conditional_node(self, position=None):
        """Add a new conditional node to the graph with comprehensive condition types"""
        logger.debug(f"Adding conditional node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_conditional_node(resolved_pos)
            logger.info(f"Conditional node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding conditional node: {str(e)}", exc_info=True)
            return None
    
    def add_web_sequence_node(self, position=None):
        """Add a previously recorded web session node to the graph"""
        logger.debug(f"Adding web sequence node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_web_sequence(resolved_pos)
            logger.info(f"Web sequence node added: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding web sequence node: {str(e)}", exc_info=True)
            return None

    def add_new_web_sequence(self, position=None):
        """Add a new web sequence node and record a session for it"""
        logger.debug(f"Adding new web sequence node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_new_web_sequence(resolved_pos)
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding new web sequence node: {str(e)}", exc_info=True)
            return None

    def add_blank_web_sequence(self, position=None):
        """Add an unassigned web sequence node (drag-and-drop from toolbar)"""
        logger.debug(f"Adding blank web sequence node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_blank_web_sequence(resolved_pos)
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding blank web sequence node: {str(e)}", exc_info=True)
            return None

    def edit_web_sequence(self, node):
        """Edit a web sequence node's properties via dialog"""
        try:
            self.node_operations.edit_web_sequence(node)
        except Exception as e:
            logger.error(f"Error editing web sequence node: {str(e)}", exc_info=True)

    def record_web_session(self, node):
        """Record a new web session and attach it to the node"""
        try:
            self.node_operations.record_web_session(node)
        except Exception as e:
            logger.error(f"Error recording web session: {str(e)}", exc_info=True)
    
    def add_llm_node(self, position=None):
        """Add a new LLM node to the graph"""
        logger.debug(f"Adding LLM node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_llm_node(resolved_pos)
            logger.info(f"LLM node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding LLM node: {str(e)}", exc_info=True)
            return None
    
    def add_form_filler_node(self, position=None):
        """Add a new Form Filling node to the graph"""
        logger.debug(f"Adding Form Filling node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_form_filler_node(resolved_pos)
            logger.info(f"Form Filling node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding Form Filling node: {str(e)}", exc_info=True)
            return None

    def add_chain_import_node(self, position=None):
        """Add a new Chain Import node to the graph"""
        logger.debug(f"Adding Chain Import node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_chain_import_node(resolved_pos)
            logger.info(f"Chain Import node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding Chain Import node: {str(e)}", exc_info=True)
            return None

    def add_code_node(self, position=None):
        """Add a new Code node to the graph"""
        logger.debug(f"Adding Code node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_code_node(resolved_pos)
            logger.info(f"Code node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding Code node: {str(e)}", exc_info=True)
            return None

    def add_context_node(self, position=None):
        """Add a new Context node to the graph"""
        logger.debug(f"Adding Context node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_context_node(resolved_pos)
            logger.info(f"Context node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding Context node: {str(e)}", exc_info=True)
            return None

    def edit_code_node(self, node):
        """Edit a Code node — opens the non-modal Code Node Studio dock."""
        logger.debug(f"Editing Code node: {node.name() if node else 'None'}")
        try:
            mw = self.window()
            if mw is not None and hasattr(mw, '_on_code_node_selected'):
                mw._on_code_node_selected(node)
                return
        except Exception:
            pass
        try:
            self.node_operations.edit_code_node(node)
        except Exception as e:
            logger.error(f"Error editing Code node: {str(e)}", exc_info=True)

    def edit_container_node(self, node):
        """Edit a Container node's properties"""
        logger.debug(f"Editing Container node: {node.name()}")
        try:
            self.node_operations.edit_container_node(node)
        except Exception as e:
            logger.error(f"Error editing Container node: {str(e)}", exc_info=True)

    def edit_context_node(self, node):
        """Edit a Context node's properties"""
        logger.debug(f"Editing Context node: {node.name()}")
        try:
            self.node_operations.edit_context_node(node)
        except Exception as e:
            logger.error(f"Error editing Context node: {str(e)}", exc_info=True)

    def add_input_node(self, position=None):
        """Add a new Input node to the graph"""
        logger.debug(f"Adding Input node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_input_node(resolved_pos)
            logger.info(f"Input node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding Input node: {str(e)}", exc_info=True)
            return None

    def edit_input_node(self, node):
        """Edit an Input node's properties"""
        logger.debug(f"Editing Input node: {node.name()}")
        try:
            self.node_operations.edit_input_node(node)
        except Exception as e:
            logger.error(f"Error editing Input node: {str(e)}", exc_info=True)

    def add_handle_node(self, position=None):
        """Add a new Handle node to the graph"""
        logger.debug(f"Adding Handle node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_handle_node(resolved_pos)
            logger.info(f"Handle node added successfully: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding Handle node: {str(e)}", exc_info=True)
            return None

    def edit_handle_node(self, node):
        """Edit a Handle node's properties"""
        logger.debug(f"Editing Handle node: {node.name()}")
        try:
            self.node_operations.edit_handle_node(node)
        except Exception as e:
            logger.error(f"Error editing Handle node: {str(e)}", exc_info=True)

    def add_mcp_node(self, position=None):
        """Add a new MCP Server node."""
        logger.debug(f"Adding MCP node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_mcp_node(resolved_pos)
            logger.info(f"MCP node added: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding MCP node: {str(e)}", exc_info=True)
            return None

    def edit_mcp_node(self, node):
        """Edit an MCP node's properties."""
        logger.debug(f"Editing MCP node: {node.name()}")
        try:
            self.node_operations.edit_mcp_node(node)
        except Exception as e:
            logger.error(f"Error editing MCP node: {str(e)}", exc_info=True)

    def add_output_node(self, position=None):
        """Add a new Output node."""
        logger.debug(f"Adding Output node at position: {position}")
        try:
            resolved_pos = self._resolve_creation_pos(position)
            result = self.node_operations.add_output_node(resolved_pos)
            logger.info(f"Output node added: {result.name() if result else 'None'}")
            try:
                if result and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(result)
            except Exception:
                pass
            return result
        except Exception as e:
            logger.error(f"Error adding Output node: {str(e)}", exc_info=True)
            return None

    def edit_output_node(self, node):
        """Edit an Output node's properties."""
        logger.debug(f"Editing Output node: {node.name()}")
        try:
            self.node_operations.edit_output_node(node)
        except Exception as e:
            logger.error(f"Error editing Output node: {str(e)}", exc_info=True)

    def _resolve_creation_pos(self, position):
        """Resolve a node creation position to scene coordinates list [x, y].

        Preference order:
        1) Provided position argument
        2) Last context menu scene position (right-click)
        3) View center mapped to scene
        4) Deterministic offset fallback
        """
        try:
            # 1) If a position is provided, normalize it
            if position is not None:
                try:
                    # QPointF-like object
                    return [int(position.x()), int(position.y())]
                except Exception:
                    pass
                if isinstance(position, (list, tuple)) and len(position) >= 2:
                    return [int(position[0]), int(position[1])]
                # Unknown type; fall through to other options

            # 2) Use last context menu scene position captured on right-click
            if getattr(self, "_last_context_scene_pos", None) is not None:
                sp = self._last_context_scene_pos
                return [int(sp.x()), int(sp.y())]

            # 3) Fallback to view center mapped to scene
            if hasattr(self, "view") and hasattr(self.view, "mapToScene"):
                sp = self.view.mapToScene(self.view.rect().center())
                return [int(sp.x()), int(sp.y())]

            # 4) Final fallback: deterministic offset so nodes don't overlap
            self._creation_counter = getattr(self, "_creation_counter", 0) + 1
            base_offset = 100
            return [100 + (self._creation_counter * base_offset), 100 + (self._creation_counter * base_offset)]
        except Exception:
            # Safe default
            return [100, 100]
    
    def add_sequence(self):
        """Add a new sequence node to the graph"""
        logger.debug("Adding sequence node")
        try:
            node = self.node_operations.add_sequence()
            try:
                if node and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.attach(node)
            except Exception:
                pass
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
        """Automatically layout nodes in the graph for readability"""
        logger.debug("Auto-layout nodes")
        try:
            self.graph_manager.auto_layout_nodes()
            logger.info("Auto-layout completed successfully")
        except Exception as e:
            logger.error(f"Error during auto-layout: {str(e)}", exc_info=True)

    def clear_graph(self):
        """Clear all nodes and connections from the graph"""
        logger.debug("Clearing graph")
        try:
            self.graph_manager.clear_graph()
            logger.info("Graph cleared successfully")
        except Exception as e:
            logger.error(f"Error clearing graph: {str(e)}", exc_info=True)

    def _validate_conditional_nodes(self):
        """Validate conditional nodes in the graph"""
        logger.debug("Validating conditional nodes")
        try:
            self.export_utils.validate_conditional_nodes()
            logger.info("Conditional nodes validation completed successfully")
        except Exception as e:
            logger.error(f"Error validating conditional nodes: {str(e)}", exc_info=True)

    def get_chain_config(self):
        """Get the current chain configuration"""
        logger.debug("Getting chain configuration")
        try:
            config_copy = self.config_manager.get_chain_config()
            logger.debug(f"Chain config contains {len(config_copy.get('sequences', []))} sequences, {len(config_copy.get('conditional_nodes', []))} conditional nodes, {len(config_copy.get('llm_nodes', []))} LLM nodes, {len(config_copy.get('chain_import_nodes', []))} chain import nodes, {len(config_copy.get('form_filler_nodes', []))} form filler nodes, {len(config_copy.get('handle_nodes', []))} handle nodes, {len(config_copy.get('mcp_nodes', []))} mcp nodes, {len(config_copy.get('web_sequences', []))} web sequence nodes")
            return config_copy
        except Exception as e:
            logger.error(f"Error getting chain config: {e}")
            raise

    def save_current_state(self):
        """Save the current state of the node graph to the chain configuration"""
        logger.debug("Saving current state")
        try:
            self.config_manager.save_current_state()
            logger.info("Current state saved successfully")
        except Exception as e:
            logger.error(f"Error saving current state: {str(e)}", exc_info=True)
            raise

    def is_ready(self):
        """Check if the GraphViewWidget is fully initialized and ready for operations."""
        return (getattr(self, '_initialization_complete', False) and
                hasattr(self, 'node_graph') and self.node_graph is not None and
                hasattr(self, 'view') and self.view is not None)

    def load_chain(self, file_path):
        """Load a chain configuration from a file"""
        logger.debug(f"Loading chain from file: {file_path}")
        try:
            # Check if initialization is complete
            if not getattr(self, '_initialization_complete', False):
                logger.error("Cannot load chain: GraphViewWidget initialization not complete")
                raise RuntimeError("Graph view is not ready for chain loading")
            
            # Ensure the graph is properly initialized before loading
            if not hasattr(self, 'node_graph') or self.node_graph is None:
                logger.error("Node graph not initialized, cannot load chain")
                raise RuntimeError("Node graph not initialized")
                
            if not hasattr(self, 'view') or self.view is None:
                logger.error("Graph view not initialized, cannot load chain")
                raise RuntimeError("Graph view not initialized")
                
            self.config_manager.load_chain(file_path)
            logger.info("Chain loaded successfully")
        except Exception as e:
            logger.error(f"Error loading chain: {str(e)}", exc_info=True)
            raise

    def save_chain(self, file_path):
        """Save the current chain configuration to a file"""
        logger.debug(f"Saving chain to file: {file_path}")
        try:
            self.config_manager.save_chain(file_path)
            logger.info("Chain saved successfully")
        except Exception as e:
            logger.error(f"Error saving chain: {str(e)}", exc_info=True)
            raise

    def build_graph_from_config(self, config=None):
        """Build the graph from a chain configuration"""
        logger.debug("Building graph from config")
        try:
            self.config_manager.build_graph_from_config(config)
            logger.info("Graph built successfully")
            try:
                if hasattr(self, 'node_button_manager'):
                    self.node_button_manager.install()
            except Exception as e:
                logger.debug(f"Non-critical: failed to attach buttons after build: {e}")
        except Exception as e:
            logger.error(f"Error building graph from config: {str(e)}", exc_info=True)
            raise

    def export_as_executable(self):
        """Export the current chain as an executable batch file"""
        logger.debug("Exporting chain as executable")
        try:
            self.export_utils.export_as_executable()
            logger.info("Export completed successfully")
        except Exception as e:
            logger.error(f"Error exporting chain: {str(e)}", exc_info=True)
            raise

    def export_chain(self, bat_path, config):
        """Export the current chain configuration to a batch file"""
        logger.debug(f"Exporting chain to batch file: {bat_path}")
        try:
            self.export_utils.export_chain(bat_path, config)
            logger.info("Chain exported successfully")
        except Exception as e:
            logger.error(f"Error exporting chain: {str(e)}", exc_info=True)
            raise

    def edit_conditional(self, node):
        """Open the dialog to edit a conditional node"""
        logger.debug(f"Editing conditional node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_conditional(node)
            logger.info("Conditional node edited successfully")
        except Exception as e:
            logger.error(f"Error editing conditional node: {str(e)}", exc_info=True)

    def edit_llm(self, node):
        """Open the dialog to edit an LLM node"""
        logger.debug(f"Editing LLM node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_llm(node)
            logger.info("LLM node edited successfully")
        except Exception as e:
            logger.error(f"Error editing LLM node: {str(e)}", exc_info=True)

    def edit_llm_node(self, node):
        """Alias for edit_llm to support different call patterns"""
        self.edit_llm(node)

    def edit_chain_import_node(self, node):
        """Open the dialog to edit a Chain Import node"""
        logger.debug(f"Editing Chain Import node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_chain_import_node(node)
            logger.info("Chain Import node edited successfully")
        except Exception as e:
            logger.error(f"Error editing Chain Import node: {str(e)}", exc_info=True)

    def edit_code_node(self, node):
        """Edit a Code node — opens the non-modal Code Node Studio dock."""
        logger.debug(f"Editing Code node: {node.name() if node else 'None'}")
        try:
            mw = self.window()
            if mw is not None and hasattr(mw, '_on_code_node_selected'):
                mw._on_code_node_selected(node)
                return
        except Exception:
            pass
        try:
            self.node_operations.edit_code_node(node)
        except Exception as e:
            logger.error(f"Error editing Code node: {str(e)}", exc_info=True)

    def edit_form_filler_node(self, node):
        """Open the dialog to edit a Form Filler node"""
        logger.debug(f"Editing Form Filler node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_form_filler_node(node)
            logger.info("Form Filler node edited successfully")
        except Exception as e:
            logger.error(f"Error editing Form Filler node: {str(e)}", exc_info=True)

    def edit_sequence(self, node):
        """Open the dialog to edit a Sequence node"""
        logger.debug(f"Editing Sequence node: {node.name() if node else 'None'}")
        try:
            self.node_operations.edit_sequence(node)
            logger.info("Sequence node edited successfully")
        except Exception as e:
            logger.error(f"Error editing Sequence node: {str(e)}", exc_info=True)

    def expand_chain_import(self, node):
        """Expand a Chain Import node by opening it in a separate dialog for editing"""
        logger.debug(f"Opening chain editor for: {node.name() if node else 'None'}")
        try:
            from .dialogs.chain_expansion_dialog import ChainExpansionDialog
            dialog = ChainExpansionDialog(node, self)
            dialog.exec_()
            # The dialog may have saved changes to the imported chain file
            # (added/renamed Output nodes or their variable names) — reload
            # the chain and reconcile the node's named data ports.
            try:
                cfg = node.get_chain_import_config()
                node.set_chain_import_data(
                    cfg.get('chain_file', ''),
                    cfg.get('import_mode', 'full'),
                    cfg.get('prefix', ''),
                    cfg.get('loop_count', 1),
                    cfg.get('extra_delay', 0),
                    cfg.get('enabled', True),
                )
            except Exception:
                pass
            logger.info("Chain editor dialog closed")
        except Exception as e:
            logger.error(f"Error opening chain editor: {str(e)}", exc_info=True)

    def delete_node(self, node):
        """Delete a node from the graph"""
        logger.debug(f"Deleting node: {node.name() if node else 'None'}")
        try:
            try:
                if node and hasattr(self, 'node_button_manager'):
                    self.node_button_manager.detach(node)
            except Exception:
                pass
            self.node_operations.delete_node(node)
            logger.info("Node deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting node: {str(e)}", exc_info=True)

    def delete_action_node(self, node):
        """Delete an action node from the graph"""
        logger.debug(f"Deleting action node: {node.name() if node else 'None'}")
        try:
            self.node_operations.delete_action_node(node)
            logger.info("Action node deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting action node: {str(e)}", exc_info=True)

    def record_new_sequence(self):
        """Record a new sequence of actions"""
        logger.debug("Recording new sequence")
        try:
            self.node_operations.record_new_sequence()
            logger.info("New sequence recorded successfully")
        except Exception as e:
            logger.error(f"Error recording new sequence: {str(e)}", exc_info=True)

    

    def refresh_sequences_toolbar(self):
        """Refresh sequences in the left sidebar."""
        try:
            chains_lib = getattr(self, 'chains_library', None)
            if chains_lib and hasattr(chains_lib, 'refresh_sequences_list'):
                chains_lib.refresh_sequences_list()
        except Exception as e:
            logger.error(f"Failed to refresh sequences list: {e}")

    def record_single_action(self, sequence_node):
        """Record a single action within a sequence"""
        logger.debug(f"Recording single action for sequence node: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.record_single_action(sequence_node)
            logger.info("Single action recorded successfully")
        except Exception as e:
            logger.error(f"Error recording single action: {str(e)}", exc_info=True)

    def remove_action_from_sequence(self, sequence_node):
        """Remove an action from a sequence"""
        logger.debug(f"Removing action from sequence node: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.remove_action_from_sequence(sequence_node)
            logger.info("Action removed successfully")
        except Exception as e:
            logger.error(f"Error removing action from sequence: {str(e)}", exc_info=True)

    def remove_sequence_from_chain(self, sequence_node):
        """Remove a sequence from the chain"""
        logger.debug(f"Removing sequence from chain: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.remove_sequence_from_chain(sequence_node)
            logger.info("Sequence removed successfully")
        except Exception as e:
            logger.error(f"Error removing sequence from chain: {str(e)}", exc_info=True)

    def add_conditional_branch_to_sequence(self, sequence_node):
        """Add a conditional branch to a sequence"""
        logger.debug(f"Adding conditional branch to sequence node: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.add_conditional_branch_to_sequence(sequence_node)
            logger.info("Conditional branch added to sequence successfully")
        except Exception as e:
            logger.error(f"Error adding conditional branch: {str(e)}", exc_info=True)

    def add_conditional_branch_to_llm(self, llm_node):
        """Add a conditional branch to an LLM node"""
        logger.debug(f"Adding conditional branch to LLM node: {llm_node.name() if llm_node else 'None'}")
        try:
            self.node_operations.add_conditional_branch_to_llm(llm_node)
            logger.info("Conditional branch added to LLM successfully")
        except Exception as e:
            logger.error(f"Error adding conditional branch to LLM: {str(e)}", exc_info=True)

    def add_conditional_branch_to_chain_import(self, chain_import_node):
        """Add a conditional branch to a Chain Import node"""
        logger.debug(f"Adding conditional branch to Chain Import node: {chain_import_node.name() if chain_import_node else 'None'}")
        try:
            self.node_operations.add_conditional_branch_to_chain_import(chain_import_node)
            logger.info("Conditional branch added to Chain Import successfully")
        except Exception as e:
            logger.error(f"Error adding conditional branch to Chain Import: {str(e)}", exc_info=True)

    def add_conditional_branch_to_sequence(self, sequence_node):
        """Add a conditional branch to a sequence"""
        logger.debug(f"Adding conditional branch to sequence node: {sequence_node.name() if sequence_node else 'None'}")
        try:
            self.node_operations.add_conditional_branch_to_sequence(sequence_node)
            logger.info("Conditional branch added to sequence successfully")
        except Exception as e:
            logger.error(f"Error adding conditional branch to sequence: {str(e)}", exc_info=True)

    def add_conditional_branch_to_llm(self, llm_node):
        """Add a conditional branch to an LLM"""
        logger.debug(f"Adding conditional branch to LLM node: {llm_node.name() if llm_node else 'None'}")
        try:
            self.node_operations.add_conditional_branch_to_llm(llm_node)
            logger.info("Conditional branch added to LLM successfully")
        except Exception as e:
            logger.error(f"Error adding conditional branch to LLM: {str(e)}", exc_info=True)

    def add_conditional_branch_to_chain_import(self, chain_import_node):
        """Add a conditional branch to a Chain Import node"""
        logger.debug(f"Adding conditional branch to Chain Import node: {chain_import_node.name() if chain_import_node else 'None'}")
        try:
            self.node_operations.add_conditional_branch_to_chain_import(chain_import_node)
            logger.info("Conditional branch added to Chain Import successfully")
        except Exception as e:
            logger.error(f"Error adding conditional branch to Chain Import: {str(e)}", exc_info=True)

    def delete_selected_nodes(self):
        """Delete all currently selected nodes using node operations."""
        logger.debug("Deleting selected nodes")
        try:
            selected_nodes = self.node_graph.selected_nodes()
            if not selected_nodes:
                logger.debug("No selected nodes to delete")
                return
            logger.info(f"Deleting {len(selected_nodes)} selected nodes")
            # Iterate and delete via existing type-aware node operations
            for node in list(selected_nodes):
                try:
                    logger.debug(f"Deleting node: {node.name()} ({type(node).__name__})")
                    self.node_operations.delete_node(node)
                except Exception as inner_e:
                    logger.error(f"Failed to delete node {node.name()}: {inner_e}", exc_info=True)
            # Clear selection after deletion
            try:
                self.node_graph.clear_selection()
            except Exception:
                pass
            logger.info("Selected nodes deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting selected nodes: {str(e)}", exc_info=True)
