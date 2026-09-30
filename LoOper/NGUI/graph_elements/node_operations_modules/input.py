from PyQt5.QtWidgets import QMessageBox
from ...dialogs.input_dialog import InputPropertiesDialog
from .utils import get_logger

logger = get_logger(__name__)


class InputOperationsMixin:
    def add_input_node(self, pos=None):
        """Add a new Input node."""
        logger.info(f"Adding Input node at position: {pos}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'input.InputNode',
                name='Input',
                pos=pos
            )

            if node:
                logger.info(f"Successfully added Input node {node.id}")
                return node
            else:
                logger.error("Failed to create Input node")

        except Exception as e:
            logger.error(f"Error adding Input node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add Input node: {str(e)}"
            )

        return None

    def edit_input_node(self, node):
        """Edit an Input node's properties using the themed dialog.

        Includes a passthrough toggle: when enabled the Input node acts as a
        data source for downstream LLM/Code/Conditional nodes and does NOT
        type text on screen.
        """
        logger.info(f"Editing Input node: {node.id}")
        try:
            # Gather current values from the node properties
            current_config = {
                'label': node.get_property('label') or '',
                'default_value': node.get_property('default_value') or '',
                'passthrough': bool(node.get_property('passthrough')),
                'web_mode': bool(node.get_property('web_mode')),
                'user_prompt': node.get_property('user_prompt') or '',
                'agent_modifiable': bool(node.get_property('agent_modifiable')),
                'decision_mode': bool(node.get_property('decision_mode')),
                'decision_criterion': node.get_property('decision_criterion') or '',
                'decision_evaluator': str(node.get_property('decision_evaluator') or 'llm'),
                'decision_model': node.get_property('decision_model') or '',
                'decision_default': bool(node.get_property('decision_default')),
                'question_mode': str(node.get_property('question_mode') or 'text'),
                'choices': node.get_property('choices') or '[]',
                'route_on_answer': bool(node.get_property('route_on_answer')),
                'accept_text': bool(node.get_property('accept_text')),
                'accept_images': bool(node.get_property('accept_images')),
                'accept_documents': bool(node.get_property('accept_documents')),
                'tts_enabled': bool(node.get_property('tts_enabled')),
                'tts_text': node.get_property('tts_text') or '',
                'tts_language': node.get_property('tts_language') or 'en',
                'tts_voice_model': node.get_property('tts_voice_model') or '',
                'tts_speed': node.get_property('tts_speed'),
                'tts_speaker_id': node.get_property('tts_speaker_id'),
            }

            dialog = InputPropertiesDialog(self.parent_widget, current_config)
            if dialog.exec_() != dialog.Accepted:
                logger.debug("Dialog cancelled")
                return

            config = dialog.get_config()
            node.set_property('label', config['label'])
            node.set_property('default_value', config['default_value'])
            node.set_property('user_prompt', config['user_prompt'])
            node.set_property('passthrough', config['passthrough'])
            node.set_property('web_mode', config['web_mode'])
            node.set_property('agent_modifiable', config['agent_modifiable'])
            node.set_property('decision_mode', bool(config.get('decision_mode', False)))
            node.set_property('decision_criterion', config.get('decision_criterion', ''))
            node.set_property('decision_evaluator', config.get('decision_evaluator', 'llm'))
            node.set_property('decision_model', config.get('decision_model', ''))
            node.set_property('decision_default', bool(config.get('decision_default', False)))
            node.set_property('question_mode', config.get('question_mode', 'text'))
            node.set_property('choices', config.get('choices', '[]'))
            node.set_property('route_on_answer', bool(config.get('route_on_answer', False)))
            node.set_property('accept_text', bool(config.get('accept_text', True)))
            node.set_property('accept_images', bool(config.get('accept_images', False)))
            node.set_property('accept_documents', bool(config.get('accept_documents', False)))
            node.set_property('tts_enabled', config['tts_enabled'])
            node.set_property('tts_text', config['tts_text'])
            node.set_property('tts_language', config.get('language', 'en'))
            node.set_property('tts_voice_model', config.get('voice_model', ''))
            node.set_property('tts_speed', config.get('speed', 1.0))
            node.set_property('tts_speaker_id', config.get('speaker_id'))

            # Update visual colour to reflect passthrough state
            if hasattr(node, 'set_passthrough'):
                node.set_passthrough(config['passthrough'])
            # Decision routing adds/removes the true/false output ports.
            if hasattr(node, 'set_decision_mode'):
                node.set_decision_mode(
                    bool(config.get('decision_mode', False)),
                    route_on_answer=bool(config.get('route_on_answer', False)),
                )
            elif hasattr(node, 'rebuild_decision_ports'):
                node.rebuild_decision_ports()
            # Edit-time hint: while routing is enabled the true/false ports own
            # the flow; a leftover 'output' wiring is ignored at runtime.
            try:
                if config.get('decision_mode') or config.get('route_on_answer'):
                    _out_port = node.output_ports().get('output') if hasattr(node, 'output_ports') else None
                    if _out_port is not None and _out_port.connected_ports():
                        logger.warning(
                            "Input %s routes on true/false but its 'output' port "
                            "still has connections — they are ignored while "
                            "routing is enabled", node.id,
                        )
            except Exception:
                pass

            suffix = " [passthrough]" if config['passthrough'] else ""
            if config.get('decision_mode') or config.get('route_on_answer'):
                suffix += " [decide]"
            if config['agent_modifiable']:
                suffix += " [agent]"
            if config['label']:
                node.set_name(f"Input: {config['label']}{suffix}")
            else:
                node.set_name(f"Input{suffix}")

            logger.info(f"Successfully edited Input node: {node.id}")

        except Exception as e:
            logger.error(f"Error editing Input node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to edit Input node: {str(e)}"
            )
