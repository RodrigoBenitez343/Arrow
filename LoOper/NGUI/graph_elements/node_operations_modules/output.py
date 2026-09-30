from PyQt5.QtWidgets import QMessageBox
from ...dialogs.output_dialog import OutputPropertiesDialog
from .utils import get_logger

logger = get_logger(__name__)


class OutputOperationsMixin:
    def add_output_node(self, pos=None):
        """Add a new Output node."""
        logger.info(f"Adding Output node at position: {pos}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'output.OutputNode',
                name='Output',
                pos=pos
            )

            if node:
                logger.info(f"Successfully added Output node {node.id}")
                return node
            else:
                logger.error("Failed to create Output node")

        except Exception as e:
            logger.error(f"Error adding Output node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add Output node: {str(e)}"
            )

        return None

    def edit_output_node(self, node):
        """Edit an Output node's properties using the themed dialog."""
        logger.info(f"Editing Output node: {node.id}")
        try:
            # Gather current values from the node properties
            current_config = {
                'label': node.get_property('label') or '',
                'variable_name': node.get_property('variable_name') or '',
                'agent_visible': bool(node.get_property('agent_visible')),
                'overlay_visible': bool(node.get_property('overlay_visible')),
                'popup_on_finish': bool(node.get_property('popup_on_finish')),
                'show_rating': bool(node.get_property('show_rating')),
                'render_mode': node.get_property('render_mode') or 'text',
                'tts_enabled': bool(node.get_property('tts_enabled')),
                'tts_text': node.get_property('tts_text') or '',
                'tts_language': node.get_property('tts_language') or 'en',
                'tts_voice_model': node.get_property('tts_voice_model') or '',
                'tts_speed': node.get_property('tts_speed'),
                'tts_speaker_id': node.get_property('tts_speaker_id'),
                'tts_wait': bool(node.get_property('tts_wait')),
            }

            dialog = OutputPropertiesDialog(self.parent_widget, current_config)
            if dialog.exec_() != dialog.Accepted:
                logger.debug("Dialog cancelled")
                return

            config = dialog.get_config()
            node.set_property('label', config['label'])
            node.set_property('variable_name', config.get('variable_name', '') or '')
            node.set_property('agent_visible', config['agent_visible'])
            node.set_property('overlay_visible', config['overlay_visible'])
            node.set_property('popup_on_finish', config['popup_on_finish'])
            node.set_property('show_rating', config['show_rating'])
            node.set_property('render_mode', config['render_mode'])
            node.set_property('tts_enabled', config['tts_enabled'])
            node.set_property('tts_text', config['tts_text'])
            node.set_property('tts_language', config.get('language', 'en'))
            node.set_property('tts_voice_model', config.get('voice_model', ''))
            node.set_property('tts_speed', config.get('speed', 1.0))
            node.set_property('tts_speaker_id', config.get('speaker_id'))
            node.set_property('tts_wait', config['tts_wait'])

            name = f"Output: {config['label']}" if config['label'] else "Output"
            if config['render_mode'] == 'audio' or config['tts_enabled']:
                name += " \U0001f50a"
            node.set_name(name)

            logger.info(f"Successfully edited Output node: {node.id}")

        except Exception as e:
            logger.error(f"Error editing Output node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to edit Output node: {str(e)}"
            )
