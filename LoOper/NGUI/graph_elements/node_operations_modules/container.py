from PyQt5.QtWidgets import QMessageBox
from .utils import get_logger

logger = get_logger(__name__)

class ContainerOperationsMixin:
    def add_container_node(self, pos=None):
        """Add a new Container node."""
        logger.info(f"Adding Container node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating Container node")
            node = self.parent_widget.graph_manager.create_node(
                'container.ContainerNode', 
                name='Container', 
                pos=pos
            )
            
            if node:
                logger.info(f"Successfully added Container node {node.id}")
                return node
            else:
                logger.error("Failed to create Container node")
                
        except Exception as e:
            logger.error(f"Error adding Container node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add Container node: {str(e)}"
            )
            
        return None

    def edit_container_node(self, node):
        """Edit a Container node's properties."""
        logger.info(f"Editing Container node: {node.id}")
        try:
            logger.debug("Creating Container Node dialog")
            
            # Get current configuration
            current_config = node.get_container_config()
            
            from ...dialogs import ContainerNodeDialog
            dialog = ContainerNodeDialog(self.parent_widget, current_config)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                config = dialog.get_config()
                
                # Update node properties
                node.set_property('iso_path', config['iso_path'])
                node.set_property('memory_mb', str(config['memory_mb']))
                node.set_property('cpu_cores', str(config['cpu_cores']))
                node.set_property('hide_window', 'true' if config['hide_window'] else 'false')
                node.set_property('execute_on_input', 'true' if config['execute_on_input'] else 'false')
                node.set_property('output_variable', config['output_variable'])
                node.set_property('timeout', str(config['timeout']))
                
                # Update node name visually if desired
                if config['iso_path']:
                    import os
                    name_preview = os.path.basename(config['iso_path'])
                    node.set_name(f"VM: {name_preview}")
                
                logger.info(f"Successfully updated Container node {node.id}")
                return True
                
            logger.debug("Dialog cancelled")
            return False
            
        except Exception as e:
            logger.error(f"Error editing Container node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to edit Container node: {str(e)}"
            )
            return False
