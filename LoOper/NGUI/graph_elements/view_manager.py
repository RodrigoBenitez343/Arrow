import logging
from ..nodes import SequenceNode, ActionNode, ConditionalNode, LLMNode, ContextNode

logger = logging.getLogger(__name__)


class ViewManager:
    """Manages different views in the graph (sequence view, action view)."""
    
    def __init__(self, parent_widget):
        logger.info("Initializing ViewManager")
        try:
            self.parent_widget = parent_widget
            self.current_view = 'sequence'  # 'sequence' or 'action'
            logger.debug(f"ViewManager initialized with default view: {self.current_view}")
        except Exception as e:
            logger.error(f"Error initializing ViewManager: {e}")
            raise
        
    def show_sequence_view(self):
        """Show only sequence nodes and their connections."""
        logger.info("Switching to sequence view")
        try:
            if not self.parent_widget.graph_manager.node_graph:
                logger.warning("No node graph available for sequence view")
                return
                
            self.current_view = 'sequence'
            logger.debug("Current view set to sequence")
            
            # Get all nodes
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            logger.debug(f"Found {len(all_nodes)} total nodes")
            
            # Show/hide nodes based on type
            visible_count = 0
            hidden_count = 0
            for node in all_nodes:
                if isinstance(node, (SequenceNode, ConditionalNode, LLMNode, ContextNode)):
                    node.set_disabled(False)
                    visible_count += 1
                elif isinstance(node, ActionNode):
                    node.set_disabled(True)
                    hidden_count += 1
                    
            logger.debug(f"Sequence view: {visible_count} nodes visible, {hidden_count} nodes hidden")
            
            # Update button states
            self._update_button_states()
            logger.info("Sequence view activated successfully")
        except Exception as e:
            logger.error(f"Error switching to sequence view: {e}")
            raise
        
    def show_action_view(self):
        """Show only action nodes and their connections."""
        logger.info("Switching to action view")
        try:
            if not self.parent_widget.graph_manager.node_graph:
                logger.warning("No node graph available for action view")
                return
                
            self.current_view = 'action'
            logger.debug("Current view set to action")
            
            # Get all nodes
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            logger.debug(f"Found {len(all_nodes)} total nodes")
            
            # Show/hide nodes based on type
            visible_count = 0
            hidden_count = 0
            for node in all_nodes:
                if isinstance(node, ActionNode):
                    node.set_disabled(False)
                    visible_count += 1
                elif isinstance(node, (SequenceNode, ConditionalNode, LLMNode, ContextNode)):
                    node.set_disabled(True)
                    hidden_count += 1
                    
            logger.debug(f"Action view: {visible_count} nodes visible, {hidden_count} nodes hidden")
            
            # Update button states
            self._update_button_states()
            logger.info("Action view activated successfully")
        except Exception as e:
            logger.error(f"Error switching to action view: {e}")
            raise
        
    def show_all_nodes(self):
        """Show all nodes in the graph."""
        logger.info("Switching to show all nodes view")
        try:
            if not self.parent_widget.graph_manager.node_graph:
                logger.warning("No node graph available for show all nodes")
                return
                
            self.current_view = 'all'
            logger.debug("Current view set to all")
            
            # Get all nodes
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            logger.debug(f"Found {len(all_nodes)} total nodes")
            
            # Show all nodes
            for node in all_nodes:
                node.set_disabled(False)
                
            logger.debug(f"All {len(all_nodes)} nodes are now visible")
            
            # Update button states
            self._update_button_states()
            logger.info("Show all nodes view activated successfully")
        except Exception as e:
            logger.error(f"Error switching to show all nodes view: {e}")
            raise
        
    def filter_nodes_by_type(self, node_types):
        """Filter nodes to show only specified types."""
        logger.info(f"Filtering nodes by types: {[t.__name__ for t in node_types]}")
        try:
            if not self.parent_widget.graph_manager.node_graph:
                logger.warning("No node graph available for filtering")
                return
                
            # Get all nodes
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            logger.debug(f"Found {len(all_nodes)} total nodes to filter")
            
            # Show/hide nodes based on type
            visible_count = 0
            hidden_count = 0
            for node in all_nodes:
                should_show = any(isinstance(node, node_type) for node_type in node_types)
                node.set_disabled(not should_show)
                if should_show:
                    visible_count += 1
                else:
                    hidden_count += 1
                    
            logger.debug(f"Filter result: {visible_count} nodes visible, {hidden_count} nodes hidden")
            logger.info("Node filtering completed successfully")
        except Exception as e:
            logger.error(f"Error filtering nodes by type: {e}")
            raise
            
    def get_visible_nodes(self):
        """Get all currently visible nodes."""
        logger.debug("Getting all currently visible nodes")
        try:
            if not self.parent_widget.graph_manager.node_graph:
                logger.warning("No node graph available for getting visible nodes")
                return []
                
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            visible_nodes = [node for node in all_nodes if not node.disabled()]
            logger.debug(f"Found {len(visible_nodes)} visible nodes out of {len(all_nodes)} total")
            return visible_nodes
        except Exception as e:
            logger.error(f"Error getting visible nodes: {e}")
            return []
        
    def get_nodes_by_view(self, view_type):
        """Get nodes that should be visible in a specific view."""
        logger.debug(f"Getting nodes for view type: {view_type}")
        try:
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            logger.debug(f"Found {len(all_nodes)} total nodes")
            
            if view_type == 'sequence':
                filtered_nodes = [node for node in all_nodes 
                               if isinstance(node, (SequenceNode, ConditionalNode, LLMNode, ContextNode))]
                logger.debug(f"Sequence view would show {len(filtered_nodes)} nodes")
                return filtered_nodes
            elif view_type == 'action':
                filtered_nodes = [node for node in all_nodes 
                               if isinstance(node, ActionNode)]
                logger.debug(f"Action view would show {len(filtered_nodes)} nodes")
                return filtered_nodes
            else:
                logger.debug(f"All view would show {len(all_nodes)} nodes")
                return all_nodes
        except Exception as e:
            logger.error(f"Error getting nodes by view type {view_type}: {e}")
            return []
            
    def refresh_current_view(self):
        """Refresh the current view to ensure proper visibility."""
        logger.info(f"Refreshing current view: {self.current_view}")
        try:
            if self.current_view == 'sequence':
                logger.debug("Refreshing sequence view")
                self.show_sequence_view()
            elif self.current_view == 'action':
                logger.debug("Refreshing action view")
                self.show_action_view()
            else:
                logger.debug("Refreshing all nodes view")
                self.show_all_nodes()
            logger.info("Current view refreshed successfully")
        except Exception as e:
            logger.error(f"Error refreshing current view: {e}")
            raise
            
    def _update_button_states(self):
        """Update the visual state of view buttons (buttons removed - method kept for compatibility)."""
        logger.debug(f"Button state update skipped - view buttons removed")
        # Method kept for compatibility but no longer performs any actions
        # since the view switching buttons have been removed
        pass
            
    def get_current_view(self):
        """Get the current view type."""
        logger.debug(f"Getting current view: {self.current_view}")
        return self.current_view
        
    def is_node_visible_in_current_view(self, node):
        """Check if a node should be visible in the current view."""
        logger.debug(f"Checking if node {type(node).__name__} is visible in {self.current_view} view")
        try:
            if self.current_view == 'sequence':
                is_visible = isinstance(node, (SequenceNode, ConditionalNode, LLMNode, ContextNode))
            elif self.current_view == 'action':
                is_visible = isinstance(node, ActionNode)
            else:
                is_visible = True
            
            logger.debug(f"Node {type(node).__name__} visibility in {self.current_view} view: {is_visible}")
            return is_visible
        except Exception as e:
            logger.error(f"Error checking node visibility: {e}")
            return False
            
    def auto_layout_visible_nodes(self):
        """Auto-layout only the currently visible nodes."""
        logger.info("Starting auto-layout for visible nodes")
        try:
            if not self.parent_widget.graph_manager.node_graph:
                logger.warning("No node graph available for auto-layout")
                return
                
            # Get visible nodes
            visible_nodes = self.get_visible_nodes()
            logger.debug(f"Found {len(visible_nodes)} visible nodes for auto-layout")
            
            if visible_nodes:
                # Temporarily show all nodes for layout calculation
                all_nodes = self.parent_widget.graph_manager.get_all_nodes()
                original_visibility = {}
                
                # Store original visibility
                logger.debug("Storing original node visibility states")
                for node in all_nodes:
                    original_visibility[node] = node.visible()
                    
                # Hide non-visible nodes
                logger.debug("Temporarily hiding non-visible nodes for layout calculation")
                for node in all_nodes:
                    if node not in visible_nodes:
                        node.set_visible(False)
                        
                # Perform auto layout
                logger.debug("Performing auto layout on visible nodes")
                self.parent_widget.graph_manager.auto_layout_nodes()
                
                # Restore original visibility
                logger.debug("Restoring original node visibility states")
                for node, was_visible in original_visibility.items():
                    node.set_visible(was_visible)
                    
                logger.info("Auto-layout for visible nodes completed successfully")
            else:
                logger.warning("No visible nodes found for auto-layout")
        except Exception as e:
            logger.error(f"Error during auto-layout of visible nodes: {e}")
            raise