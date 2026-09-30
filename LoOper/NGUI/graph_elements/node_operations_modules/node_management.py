from PyQt5.QtWidgets import QMessageBox
from ...nodes import SequenceNode, WebSequenceNode, ActionNode, ConditionalNode, LLMNode, ChainImportNode, FormFillerNode, CodeNode, ContainerNode, ContextNode, InputNode, HandleNode, MCPNode, OutputNode
from .utils import get_logger

logger = get_logger(__name__)

class NodeManagementMixin:
    def delete_node(self, node):
        """Delete a node based on its type."""
        logger.info(f"Deleting node: {node.id if node else 'None'} of type: {type(node).__name__ if node else 'None'}")
        try:
            if isinstance(node, SequenceNode):
                logger.debug("Deleting sequence node")
                self.delete_sequence_node(node)
            elif isinstance(node, WebSequenceNode):
                # Web sessions are valuable recordings - delete the node from
                # the graph but never the session file.
                logger.debug("Deleting web sequence node")
                self.parent_widget.graph_manager.delete_node(node)
            elif isinstance(node, ActionNode):
                logger.debug("Deleting action node")
                self.delete_action_node(node)
            elif isinstance(node, (ConditionalNode, LLMNode, ChainImportNode, FormFillerNode, CodeNode, ContainerNode, ContextNode, InputNode, HandleNode, MCPNode, OutputNode)):
                logger.debug(f"Deleting {type(node).__name__} node")
                self.parent_widget.graph_manager.delete_node(node)
            else:
                logger.warning(f"Unknown node type for deletion: {type(node).__name__ if node else 'None'}")
        except Exception as e:
            logger.error(f"Error deleting node: {e}")
            raise

    def show_custom_context_menu(self, name, pos):
        """Handle custom context menu actions."""
        logger.info(f"Handling custom context menu action: {name} at position {pos}")
        try:
            if name == "Add Sequence":
                logger.debug("Adding sequence from context menu")
                self.add_sequence(pos)
            elif name == "Add Action":
                logger.debug("Adding action from context menu")
                self.add_action(pos)
            elif name == "Add Conditional":
                logger.debug("Adding conditional node from context menu")
                self.add_conditional_node(pos)
            elif name == "Add LLM":
                logger.debug("Adding LLM node from context menu")
                self.add_llm_node(pos)
            elif name == "Add Code":
                logger.debug("Adding Code node from context menu")
                self.add_code_node(pos)
            elif name == "Add Container":
                logger.debug("Adding Container node from context menu")
                self.add_container_node(pos)
            elif name == "Add Input":
                logger.debug("Adding Input node from context menu")
                self.add_input_node(pos)
            else:
                logger.warning(f"Unknown context menu action: {name}")
        except Exception as e:
            logger.error(f"Error handling context menu action {name}: {e}")
            raise