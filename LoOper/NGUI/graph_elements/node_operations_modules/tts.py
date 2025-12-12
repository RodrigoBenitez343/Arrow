from PyQt5.QtWidgets import QMessageBox
from ...dialogs import TTSPropertiesDialog
from .utils import get_logger

logger = get_logger(__name__)

class TTSOperationsMixin:
    def add_tts_node(self, pos=None):
        """Add a new TTS node."""
        logger.info(f"Adding TTS node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating TTS node")
            node = self.parent_widget.graph_manager.create_node(
                'tts.TTSNode', 
                name='TTS', 
                pos=pos
            )
            
            if node:
                logger.debug(f"Setting default properties for TTS node {node.id}")
                # Set default properties
                node.set_property('text', 'Hello, this is a text-to-speech test.')
                node.set_property('language', 'en')
                
                logger.info(f"Successfully added TTS node {node.id}")
                return node
            else:
                logger.error("Failed to create TTS node")
                
        except Exception as e:
            logger.error(f"Error adding TTS node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add TTS node: {str(e)}"
            )
            
        return None

    def edit_tts(self, node):
        """Edit a TTS node's properties."""
        logger.info(f"Editing TTS node: {node.id}")
        try:
            logger.debug("Creating TTS dialog")
            
            # Get current TTS configuration
            current_config = {
                'text': node.get_property('text') or 'Hello, this is a text-to-speech test.',
                'language': node.get_property('language') or 'en'
            }
            
            dialog = TTSPropertiesDialog(self.parent_widget, current_config)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                config = dialog.get_config()
                
                # Update node properties
                node.set_property('text', config['text'])
                node.set_property('language', config['language'])
                
                # Update node name to show preview of text
                preview_text = config['text'][:20] + "..." if len(config['text']) > 20 else config['text']
                node.set_name(f"TTS: {preview_text}")
                
                logger.info(f"Successfully edited TTS node: {node.id}")
            else:
                logger.debug("Dialog cancelled")
                
        except Exception as e:
            logger.error(f"Error editing TTS node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to edit TTS node: {str(e)}"
            )