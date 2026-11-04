"""Add / edit operations for the Form Filling node.

Fills one page of a form: the node enumerates the fields (live DOM for web;
Windows UI Automation for desktop) and fills them one field at a time, grounding
each value through CRAG retrieval (evaluate -> correct -> recompose).  The
runtime owns the field->value binding; the model only ever returns one value for
one field.
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
            # Same guard as the edit path: a modal without a QApplication
            # aborts the process.
            from PyQt5.QtWidgets import QApplication
            if QApplication.instance() is not None:
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
            dialog.add_description(node.get_property('description') or '')
            if dialog.exec_() != QDialog.Accepted:
                logger.debug("Form Filling dialog cancelled")
                return

            cfg = dialog.get_config()
            node.set_property('description', dialog.description())
            node.set_property('mode', cfg['mode'])
            node.set_property('instruction', cfg['instruction'])
            node.set_property('fields_include', cfg['fields_include'])
            node.set_property('fields_skip', cfg['fields_skip'])
            node.set_property('crag_enabled', 'true' if cfg.get('crag_enabled', True) else 'false')
            node.set_property('crag_evaluator', cfg.get('crag_evaluator', 'auto'))
            node.set_property('crag_upper_threshold', str(cfg.get('crag_upper_threshold', 0.60)))
            node.set_property('crag_lower_threshold', str(cfg.get('crag_lower_threshold', 0.40)))
            node.set_property('crag_decompose_mode', cfg.get('crag_decompose_mode', 'excerption'))
            node.set_property('crag_top_k', str(cfg.get('crag_top_k', 6)))
            node.set_property('crag_max_strips', str(cfg.get('crag_max_strips', 12)))
            node.set_property('crag_top_n_internal', str(cfg.get('crag_top_n_internal', 6)))
            node.set_property('crag_top_n_external', str(cfg.get('crag_top_n_external', 5)))
            node.set_property('crag_external', 'true' if cfg.get('crag_external', True) else 'false')
            node.set_property('crag_rewrite', 'true' if cfg.get('crag_rewrite', True) else 'false')
            node.set_property('crag_char_budget', str(cfg.get('crag_char_budget', 4000)))
            node.set_property('verify', 'true' if cfg['verify'] else 'false')
            node.set_property('repair', 'true' if cfg.get('repair', True) else 'false')
            node.set_property('repair_attempts', str(cfg.get('repair_attempts', 2)))
            node.set_property('answer_no', 'true' if cfg.get('answer_no', True) else 'false')
            node.set_property('ask_user', 'true' if cfg.get('ask_user', True) else 'false')
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
            # NO modal without a GUI: QMessageBox without a QApplication
            # ABORTS the process (0xC0000409), so a failed edit in a headless
            # run / test killed the whole suite instead of reporting.
            from PyQt5.QtWidgets import QApplication
            if QApplication.instance() is not None:
                QMessageBox.critical(
                    self.parent_widget, "Error",
                    f"Failed to edit Form Filling node: {str(e)}",
                )
