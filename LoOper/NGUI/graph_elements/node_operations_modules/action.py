import json
import os
from PyQt5.QtWidgets import QMessageBox, QInputDialog
from .utils import get_logger

logger = get_logger(__name__)

class ActionOperationsMixin:
    def add_action(self, pos=None):
        """Add a new action node."""
        logger.info(f"Adding action node at position: {pos}")
        try:
            # Get action name from user
            logger.debug("Prompting user for action name")
            name, ok = QInputDialog.getText(
                self.parent_widget, 
                'Add Action', 
                'Enter action name:'
            )
            
            if ok and name:
                logger.debug(f"User entered action name: {name}")
                # Create the node
                logger.debug("Creating action node")
                node = self.parent_widget.graph_manager.create_node(
                    'action.ActionNode', 
                    name=name, 
                    pos=pos
                )
                
                if node:
                    logger.debug(f"Setting properties for action node {node.id}")
                    # Set node properties
                    node.set_property('action_name', name)
                    node.set_property('action_data', {})
                    
                    # Create action file
                    logger.debug(f"Creating action file for: {name}")
                    self._create_action_file(name)
                    
                    logger.info(f"Successfully added action: {name}")
                    return node
                else:
                    logger.error("Failed to create action node")
            else:
                logger.debug("User cancelled action name input")
                    
        except Exception as e:
            logger.error(f"Error adding action: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add action: {str(e)}"
            )
            
        return None

    def delete_action_node(self, node):
        """Delete an action node and remove it from sequences."""
        logger.info(f"Deleting action node: {node.id}")
        try:
            action_name = node.get_property('action_name') or ''
            logger.debug(f"Action name: {action_name}")
            
            # Remove action from all sequences
            logger.debug("Removing action from all sequences")
            self.parent_widget.config_manager.remove_action_from_sequence(action_name)
            
            # Delete action file
            action_file = os.path.join(
                self.parent_widget.actions_folder, 
                f"{action_name}.json"
            )
            logger.debug(f"Checking for action file: {action_file}")
            if os.path.exists(action_file):
                logger.debug(f"Deleting action file: {action_file}")
                os.remove(action_file)
            else:
                logger.warning(f"Action file not found: {action_file}")
                
            # Delete the node
            logger.debug("Deleting action node from graph")
            self.parent_widget.graph_manager.delete_node(node)
            
            logger.info(f"Successfully deleted action node: {action_name}")
            
        except Exception as e:
            logger.error(f"Error deleting action node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to delete action node: {str(e)}"
            )

    def _create_action_file(self, action_name):
        """Create a new action file."""
        logger.info(f"Creating action file for: {action_name}")
        try:
            action_file = os.path.join(
                self.parent_widget.actions_folder, 
                f"{action_name}.json"
            )
            logger.debug(f"Action file path: {action_file}")
            
            if not os.path.exists(action_file):
                logger.debug("Creating new action file")
                action_data = {
                    "name": action_name,
                    "type": "click",
                    "data": {}
                }
                
                with open(action_file, 'w') as f:
                    json.dump(action_data, f, indent=2)
                
                logger.info(f"Successfully created action file: {action_file}")
            else:
                logger.debug(f"Action file already exists: {action_file}")
        except Exception as e:
            logger.error(f"Error creating action file for {action_name}: {e}")
            raise