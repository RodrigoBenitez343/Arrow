from PyQt5.QtWidgets import QMessageBox
from .utils import get_logger
import json
import os

logger = get_logger(__name__)

class ContextOperationsMixin:
    def add_context_node(self, pos=None):
        """Add a new Context node."""
        logger.info(f"Adding Context node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating Context node")
            node = self.parent_widget.graph_manager.create_node(
                'context.ContextNode', 
                name='Context', 
                pos=pos
            )
            
            if node:
                logger.debug(f"Setting default properties for Context node {node.id}")
                # Defaults are set in ContextNode.__init__
                
                logger.info(f"Successfully added Context node {node.id}")
                return node
            else:
                logger.error("Failed to create Context node")
                
        except Exception as e:
            logger.error(f"Error adding Context node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add Context node: {str(e)}"
            )
            
        return None

    def add_context_node_with_shared_file(self, chain_file_path, context_node_data):
        """Add a Context node pre-configured to share context with another chain."""
        logger.info(f"Adding Context node with shared file: {chain_file_path}")
        try:
            # Create the node
            logger.debug("Creating Context node with shared file")
            node = self.parent_widget.graph_manager.create_node(
                'context.ContextNode', 
                name='Context', 
                pos=None
            )
            
            if node:
                # Copy configuration from the source context node
                scope = context_node_data.get('scope', 'chain')
                default_merge_policy = context_node_data.get('default_merge_policy', 'append')
                keys = context_node_data.get('keys', '[]')
                key_policies = context_node_data.get('key_policies', '{}')
                max_entries = context_node_data.get('max_entries_per_key', 100)
                auto_capture = context_node_data.get('auto_capture_responses', True)
                output_format = context_node_data.get('output_format', 'structured')
                context_label = context_node_data.get('context_node_label', '')
                retrieval_top_k = context_node_data.get('retrieval_top_k', 3)
                use_semantic = context_node_data.get('use_semantic_retrieval', False)
                source_node_id = context_node_data.get('node_id', '')  # NEW: Store the source node ID
                
                # Set properties
                node.set_property('scope', scope)
                node.set_property('default_merge_policy', default_merge_policy)
                node.set_property('keys', keys)
                node.set_property('key_policies', key_policies)
                node.set_property('max_entries_per_key', str(max_entries))
                node.set_property('auto_capture_responses', 'true' if auto_capture else 'false')
                node.set_property('output_format', output_format)
                node.set_property('context_node_label', context_label)
                node.set_property('retrieval_top_k', retrieval_top_k)
                node.set_property('use_semantic_retrieval', 'true' if use_semantic else 'false')
                
                # CRITICAL: Set the shared context chain file AND the specific node ID
                node.set_property('shared_context_chain_file', chain_file_path)
                node.set_property('shared_context_node_id', source_node_id)  # NEW
                
                # Update node name
                if context_label:
                    node.set_name(f"Context: {context_label} (Shared)")
                else:
                    node.set_name(f"Context (Shared)")
                
                logger.info(f"Successfully added shared Context node {node.id} from source node {source_node_id}")
                return node
            else:
                logger.error("Failed to create shared Context node")
                
        except Exception as e:
            logger.error(f"Error adding shared Context node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add shared Context node: {str(e)}"
            )
            
        return None

    def edit_context_node(self, node):
        """Edit a Context node's properties."""
        logger.info(f"Editing Context node: {node.id}")
        try:
            logger.debug("Creating Context Node dialog")
            
            # Get current configuration
            current_config = node.get_context_config()
            
            from ...dialogs import ContextDialog
            dialog = ContextDialog(self.parent_widget, current_config, node_id=node.id)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                config = dialog.get_config()
                
                # Update node properties
                node.set_property('scope', config['scope'])
                node.set_property('default_merge_policy', config['default_merge_policy'])
                node.set_property('keys', config['keys'])
                node.set_property('key_policies', config['key_policies'])
                node.set_property('max_entries_per_key', str(config['max_entries_per_key']))
                node.set_property('auto_capture_responses', 'true' if config['auto_capture_responses'] else 'false')
                node.set_property('output_format', config['output_format'])
                node.set_property('context_node_label', config['context_node_label'])
                node.set_property('retrieval_top_k', config['retrieval_top_k'])
                node.set_property('use_semantic_retrieval', 'true' if config['use_semantic_retrieval'] else 'false')
                node.set_property('shared_context_chain_file', config['shared_context_chain_file'])  # NEW
                node.set_property('shared_context_node_id', config.get('shared_context_node_id', ''))  # NEW
                
                # Update node name if label is set
                if config['context_node_label']:
                    node.set_name(f"Context: {config['context_node_label']}")
                else:
                    node.set_name("Context")
                
                logger.info(f"Successfully edited Context node: {node.id}")
            else:
                logger.debug("Dialog cancelled")
                
        except Exception as e:
            logger.error(f"Error editing Context node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to edit Context node: {str(e)}"
            )
