import json

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

    def add_code_node_with_shared_file(self, chain_file_path, code_node_data):
        """Add a Code node pre-configured from a shared code node in another chain."""
        logger.info(f"Adding Code node with shared file: {chain_file_path}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'code.CodeNode',
                name='Code',
                pos=None
            )

            if node:
                # Copy configuration from the source code node
                code = code_node_data.get('code', '')
                file_path = code_node_data.get('file_path', '')
                execute_on_input = code_node_data.get('execute_on_input', True)
                output_variable = code_node_data.get('output_variable', 'result')
                timeout = code_node_data.get('timeout', 30)
                description = code_node_data.get('description', '')
                gen_model = code_node_data.get('gen_model', 'llama3.2:latest')
                gen_prompt = code_node_data.get('gen_prompt', '')
                dependencies = code_node_data.get('dependencies', [])
                memory_context = code_node_data.get('memory_context', [])

                # Set properties
                node.set_property('code', code)
                node.set_property('file_path', file_path)
                node.set_property('execute_on_input', 'true' if execute_on_input else 'false')
                node.set_property('output_variable', output_variable)
                node.set_property('timeout', str(timeout))
                node.set_property('description', description)
                node.set_property('gen_model', gen_model)
                node.set_property('gen_prompt', gen_prompt)
                node.set_property('dependencies', json.dumps(dependencies))
                node.set_property('memory_context', json.dumps(memory_context))

                # Update node name
                if file_path:
                    import os
                    name_preview = os.path.basename(file_path)
                    node.set_name(f"Code: {name_preview} (Shared)")
                else:
                    # Use first 40 chars of code as preview name
                    code_snippet = code.replace('\n', ' ').strip()[:40]
                    node.set_name(f"Code: {code_snippet} (Shared)")

                logger.info(f"Successfully added shared Code node {node.id}")
                return node
            else:
                logger.error("Failed to create shared Code node")

        except Exception as e:
            logger.error(f"Error adding shared Code node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add shared Code node: {str(e)}"
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
                node.set_property('file_path', config.get('file_path', ''))
                node.set_property('execute_on_input', 'true' if config.get('execute_on_input', True) else 'false')
                node.set_property('output_variable', config.get('output_variable', 'result'))
                node.set_property('timeout', str(config.get('timeout', 30)))
                node.set_property('gen_model', config.get('gen_model', 'llama3.2:latest'))
                node.set_property('gen_prompt', config.get('gen_prompt', ''))
                node.set_property('description', config.get('description', ''))
                # New fields
                node.set_property('dependencies', json.dumps(config.get('dependencies', [])))
                node.set_property('memory_context', json.dumps(config.get('memory_context', [])))
                # Custom IO ports
                node.set_property('input_vars', json.dumps(config.get('input_vars', [])))
                node.set_property('output_vars', json.dumps(config.get('output_vars', [])))
                # Rebuild visual ports on the graph node
                try:
                    node.rebuild_ports()
                except Exception:
                    pass
                
                # Update node name? Maybe showing "Code: <file_name>" or "Code: <snippet>"
                if config.get('file_path'):
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
