from PyQt5.QtWidgets import QMessageBox
from .utils import get_logger

logger = get_logger(__name__)

class CodeOperationsMixin:
    def add_code_node(self, pos=None):
        """Add a new Code node."""
        logger.info(f"Adding Code node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating Code node")
            node = self.parent_widget.graph_manager.create_node(
                'code.CodeNode', 
                name='Code', 
                pos=pos
            )
            
            if node:
                logger.debug(f"Setting default properties for Code node {node.id}")
                # Defaults are set in CodeNode.__init__
                
                logger.info(f"Successfully added Code node {node.id}")
                return node
            else:
                logger.error("Failed to create Code node")
                
        except Exception as e:
            logger.error(f"Error adding Code node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add Code node: {str(e)}"
            )
            
        return None

    def edit_code_node(self, node):
        """Edit a Code node's properties."""
        logger.info(f"Editing Code node: {node.id}")
        try:
            logger.debug("Creating Code Node dialog")
            
            # Get current configuration
            current_config = node.get_code_config()
            
            from ...dialogs import CodeNodeDialog
            dialog = CodeNodeDialog(self.parent_widget, current_config)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                config = dialog.get_config()
                
                # Update node properties
                node.set_property('code', config['code'])
                node.set_property('file_path', config['file_path'])
                node.set_property('execute_on_input', 'true' if config['execute_on_input'] else 'false')
                node.set_property('output_variable', config['output_variable'])
                node.set_property('timeout', str(config['timeout']))
                node.set_property('gen_model', config.get('gen_model', 'llama3.2:latest'))
                node.set_property('gen_prompt', config.get('gen_prompt', ''))
                
                # Update node name? Maybe showing "Code: <file_name>" or "Code: <snippet>"
                if config['file_path']:
                    import os
                    name_preview = os.path.basename(config['file_path'])
                    node.set_name(f"Code: {name_preview}")
                else:
                    node.set_name("Code")
                
                logger.info(f"Successfully edited Code node: {node.id}")
            else:
                logger.debug("Dialog cancelled")
                
        except Exception as e:
            logger.error(f"Error editing Code node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to edit Code node: {str(e)}"
            )
