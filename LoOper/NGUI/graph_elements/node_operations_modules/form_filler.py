"""Add / edit operations for the Form Filling node.

Fills one page of a form: the node enumerates the fields (live DOM for web;
vision + LayoutLMv3 for desktop) and fills them one field at a time, grounding
each value through probe-based retrieval.  The runtime owns the field->value
binding; the model only ever returns one value for one field.
"""

from PyQt5.QtWidgets import QDialog, QMessageBox

from .utils import get_logger

logger = get_logger(__name__)


class FormFillerOperationsMixin:
    def add_form_filler_node(self, pos=None):
        """Add a new Form Filling node."""
        logger.info(f"Adding Form Filling node at position: {pos}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'form_filler.FormFillerNode',
                name='Form Filling',
                pos=pos,
            )
            if node:
                logger.info(f"Successfully added Form Filling node {node.id}")
                return node
            logger.error("Failed to create Form Filling node")
        except Exception as e:
            logger.error(f"Error adding Form Filling node: {e}")
            QMessageBox.critical(
                self.parent_widget, "Error",
                f"Failed to add Form Filling node: {str(e)}",
            )
        return None

    def edit_form_filler_node(self, node):
        """Edit a Form Filling node's properties via the themed dialog."""
        logger.info(f"Editing Form Filling node: {node.id}")
        try:
            from ...dialogs.form_filler_dialog import FormFillerDialog

            current = dict(node.get_form_filler_config())

            dialog = FormFillerDialog(self.parent_widget, current)
            if dialog.exec_() != QDialog.Accepted:
                logger.debug("Form Filling dialog cancelled")
                return

            cfg = dialog.get_config()
            node.set_property('mode', cfg['mode'])
            node.set_property('instruction', cfg['instruction'])
            node.set_property('fields_include', cfg['fields_include'])
            node.set_property('fields_skip', cfg['fields_skip'])
            node.set_property('probe_top_k', str(cfg['probe_top_k']))
            node.set_property('probe_char_budget', str(cfg['probe_char_budget']))
            node.set_property('probe_context_chars', str(cfg['probe_context_chars']))
            node.set_property('probe_cycles', str(cfg.get('probe_cycles', 3)))
            node.set_property('consolidate', 'true' if cfg.get('consolidate', True) else 'false')
            node.set_property('verify', 'true' if cfg['verify'] else 'false')
            node.set_property('repair', 'true' if cfg.get('repair', True) else 'false')
            node.set_property('repair_attempts', str(cfg.get('repair_attempts', 2)))
            node.set_property('answer_no', 'true' if cfg.get('answer_no', True) else 'false')
            node.set_property('ask_user', 'true' if cfg.get('ask_user', False) else 'false')
            node.set_property('max_fields', str(cfg['max_fields']))
            node.set_property('engine', cfg['engine'])
            node.set_property('model', cfg['model'])
            node.set_property('temperature', str(cfg['temperature']))
            node.set_property('max_tokens', str(cfg['max_tokens']))
            node.set_property('context_size', str(cfg.get('context_size', 0)))
            node.set_property('web_scope', cfg.get('web_scope', '') or '')

            node.set_name(f"Form Filling: {cfg['mode']}")
            logger.info(
                f"Successfully edited Form Filling node {node.id}: "
                f"mode={cfg['mode']}, engine={cfg['engine']}"
            )
        except Exception as e:
            logger.error(f"Error editing Form Filling node: {e}")
            QMessageBox.critical(
                self.parent_widget, "Error",
                f"Failed to edit Form Filling node: {str(e)}",
            )
