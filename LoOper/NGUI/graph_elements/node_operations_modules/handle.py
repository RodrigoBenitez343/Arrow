from PyQt5.QtWidgets import QMessageBox, QLineEdit, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QCheckBox
from .utils import get_logger

logger = get_logger(__name__)

VALID_ACTION_TYPES = [
    'click',
    'drag',
]


class HandleOperationsMixin:
    def add_handle_node(self, pos=None):
        """Add a new Handle node."""
        logger.info(f"Adding Handle node at position: {pos}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                'handle.HandleNode',
                name='Handle',
                pos=pos
            )

            if node:
                logger.info(f"Successfully added Handle node {node.id}")
                return node
            else:
                logger.error("Failed to create Handle node")

        except Exception as e:
            logger.error(f"Error adding Handle node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add Handle node: {str(e)}"
            )

        return None

    def edit_handle_node(self, node):
        """Edit a Handle node's properties using a dialog.

        Click nodes expose a single goal field (what to click).
        Drag nodes expose two fields: the source object to drag and the
        target location to drop it at.
        """
        logger.info(f"Editing Handle node: {node.id}")
        try:
            current_action = node.get_property('action_type') or 'click'
            current_goal = node.get_property('goal_description') or ''
            current_target = node.get_property('target_description') or ''
            current_adaptive = bool(node.get_property('agent_adaptive'))
            current_web_mode = bool(node.get_property('web_mode'))

            # Build dialog with dropdown for action type and text for goal
            dialog = QDialog(self.parent_widget)
            dialog.setWindowTitle("Edit Handle Node")
            layout = QFormLayout(dialog)

            # Action type dropdown
            action_combo = QComboBox(dialog)
            action_combo.addItems(VALID_ACTION_TYPES)
            if current_action in VALID_ACTION_TYPES:
                action_combo.setCurrentText(current_action)
            layout.addRow(QLabel("Action type:"), action_combo)

            # Goal description — click: what to click; drag: source object
            goal_label = QLabel("Goal description:")
            goal_input = QLineEdit(dialog)
            goal_input.setText(current_goal)
            goal_input.setPlaceholderText(
                "e.g. click on any video thumbnail on the screen"
            )
            layout.addRow(goal_label, goal_input)

            # Target description — drag destination only
            target_label = QLabel("Target location:")
            target_input = QLineEdit(dialog)
            target_input.setText(current_target)
            target_input.setPlaceholderText(
                "e.g. the recycle bin icon"
            )
            layout.addRow(target_label, target_input)

            # Show/hide + relabel fields depending on the selected action
            def _on_action_changed(act):
                is_drag = act == 'drag'
                goal_label.setText(
                    "Source object:" if is_drag else "Goal description:"
                )
                goal_input.setPlaceholderText(
                    "e.g. the chrome window tab"
                    if is_drag else "e.g. click on any video thumbnail on the screen"
                )
                target_label.setVisible(is_drag)
                target_input.setVisible(is_drag)
                # Web mode is a click feature: hide it on drag
                web_label.setVisible(not is_drag)
                web_check.setVisible(not is_drag)

            action_combo.currentTextChanged.connect(_on_action_changed)

            # Web mode toggle — resolve the goal against the browser DOM
            web_check = QCheckBox(
                "Web mode: click on the browser page (Laya picks the element)",
                dialog,
            )
            web_check.setChecked(current_web_mode)
            web_check.setToolTip(
                "When ON (click only), the goal is resolved against the chain's "
                "browser page instead of the screen: the page's clickable "
                "elements are enumerated, the embedded Laya engine picks the "
                "one the goal asks for, and it is clicked in the page — no "
                "screenshot grounding (LocateAnything) and no screen click."
            )
            web_label = QLabel("Web mode:")
            layout.addRow(web_label, web_check)

            _on_action_changed(action_combo.currentText())

            # Agent-adaptive toggle
            adaptive_check = QCheckBox(
                "Let agent adapt the goal dynamically (VLM + OCR + LLM reasoning)",
                dialog,
            )
            adaptive_check.setChecked(current_adaptive)
            adaptive_check.setToolTip(
                "When ON and the chain is run by the agent, the node uses vision+OCR+LLM "
                "reasoning to determine what to click. When OFF, it always uses the "
                "goal description directly (independent / manual mode)."
            )
            layout.addRow(QLabel("Agent-adaptive:"), adaptive_check)

            # Buttons
            buttons = QDialogButtonBox(
                QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addRow(buttons)

            if dialog.exec_() != QDialog.Accepted:
                logger.debug("Dialog cancelled")
                return

            action_type = action_combo.currentText()
            goal_description = goal_input.text().strip()
            target_description = (
                target_input.text().strip() if action_type == 'drag' else ''
            )
            agent_adaptive = adaptive_check.isChecked()
            web_mode = bool(web_check.isChecked()) and action_type == 'click'

            node.set_property('action_type', action_type)
            node.set_property('goal_description', goal_description)
            node.set_property('target_description', target_description)
            node.set_property('agent_adaptive', agent_adaptive)
            node.set_property('web_mode', web_mode)

            # Update node name
            node.set_name(
                f"Handle: {action_type}" + (" (web)" if web_mode else "")
            )

            logger.info(
                f"Successfully edited Handle node {node.id}: "
                f"action={action_type}, goal={goal_description[:60]}, "
                f"target={target_description[:60]}, "
                f"agent_adaptive={agent_adaptive}, web_mode={web_mode}"
            )

        except Exception as e:
            logger.error(f"Error editing Handle node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to edit Handle node: {str(e)}"
            )
