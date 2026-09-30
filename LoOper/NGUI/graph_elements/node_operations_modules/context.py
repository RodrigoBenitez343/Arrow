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
            node = self.parent_widget.graph_manager.create_node(
                'context.ContextNode', 
                name='Context', 
                pos=None
            )
            
            if node:
                label = context_node_data.get('label', '') or context_node_data.get('context_node_label', '')
                max_history = context_node_data.get('max_history', 10)
                persistent = context_node_data.get('persistent', True)
                clear_on_finish = context_node_data.get('clear_on_finish', False)
                # "Persist across chain runs" and "clear on finish" are the
                # same choice with opposite meanings: a node cannot be both.
                # clear_on_finish wins on legacy data (the executor treats it
                # as per-run scoped regardless of persistent).
                persistent = bool(persistent)
                clear_on_finish = bool(clear_on_finish)
                if clear_on_finish:
                    persistent = False

                node.set_property('label', label)
                node.set_property('max_history', int(max_history))
                node.set_property('persistent', persistent)
                node.set_property('clear_on_finish', clear_on_finish)
                node.set_property('scope', str(context_node_data.get('scope', 'local')))
                node.set_property('shared_context_chain_file', str(chain_file_path))

                # Clone semantics: this node keeps its OWN id (subid) and
                # stores a REFERENCE to the source context node.  At runtime
                # the executor copies the source's context into this clone
                # (keyed by source id + this clone's id) and deletes the copy
                # only when THIS chain executes with clear_on_finish toggled.
                source_id = str(
                    context_node_data.get('node_id', '')
                    or context_node_data.get('id', '')
                    or ''
                ).strip()
                if source_id:
                    try:
                        node.set_property('shared_context_node_id', source_id)
                        logger.info(
                            "Shared context clone references source node %s",
                            source_id,
                        )
                    except Exception as _id_err:
                        logger.warning(
                            "Failed to set shared context reference %s: %s",
                            source_id, _id_err,
                        )

                chain_basename = os.path.splitext(os.path.basename(str(chain_file_path)))[0]
                if label:
                    node.set_name(f"Context: {label} (Shared from {chain_basename})")
                else:
                    node.set_name(f"Context (Shared from {chain_basename})")
                
                logger.info(f"Successfully added shared Context node {node.id}")
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
            current_config = node.get_context_config()
            
            # Use the config node_id (preserved on load) so dialog preview queries
            # the same node_id that context_ops uses during execution.
            context_node_id = None
            try:
                context_node_id = node.get_property('_config_node_id')
            except Exception:
                pass
            if not context_node_id:
                context_node_id = node.id
            
            # Derive chain_id from current chain file for dialog preview
            chain_id = None
            try:
                cfg_mgr = getattr(self.parent_widget, 'config_manager', None)
                if cfg_mgr:
                    chain_file = getattr(cfg_mgr, '_current_chain_file', None) or ''
                    if chain_file:
                        chain_id = os.path.splitext(os.path.basename(str(chain_file)))[0]
            except Exception:
                pass
            
            from ...dialogs import ContextDialog
            dialog = ContextDialog(self.parent_widget, current_config, node_id=context_node_id, chain_id=chain_id)
            
            if dialog.exec_() == dialog.Accepted:
                config = dialog.get_config()
                
                node.set_property('label', config['label'])
                node.set_property('max_history', config['max_history'])
                node.set_property('persistent', config['persistent'])
                node.set_property('clear_on_finish', config['clear_on_finish'])
                node.set_property('scope', config['scope'])
                
                if config['label']:
                    node.set_name(f"Context: {config['label']}")
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
