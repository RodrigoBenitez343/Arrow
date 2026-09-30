# graphui/node_context_menu.py

from PyQt5.QtWidgets import QMenu, QAction, QFileDialog
from .nodes import SequenceNode, ActionNode
from .dialogs import SequencePropertiesDialog
from .i18n import _

class NodeContextMenuManager:
    """Manages the context menu for nodes in the graph view"""
    
    def __init__(self, graph_view):
        self.graph_view = graph_view

    def show_context_menu(self, pos, node):
        """Show the context menu for the given node at the given position"""
        menu = QMenu()
        
        if hasattr(node, '__identifier__') and node.__identifier__ == 'sequence':
            self.add_sequence_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'web_sequence':
            self.add_web_sequence_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'action':
            self.add_action_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'conditional':
            self.add_conditional_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'llm':
            self.add_llm_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'chain_import':
            self.add_chain_import_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'code':
            self.add_code_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'form_filler':
            self.add_form_filler_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'container':
            self.add_container_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'context':
            self.add_context_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'input':
            self.add_input_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'handle':
            self.add_handle_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'mcp':
            self.add_mcp_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'output':
            self.add_output_node_actions(menu, node)
        else:
            # Show graph actions for background clicks or any other case
            self.add_graph_actions(menu, pos)
            
        menu.exec_(pos)

    def add_sequence_node_actions(self, menu, node):
        """Add actions for a sequence node to the context menu"""
        # Trimmed actions: no copy/paste or conditional branch from sequence node menu

        # Edit properties via dialog (no inline inputs on node)
        edit_sequence_action = QAction(_("Edit Sequence Properties"), menu)
        edit_sequence_action.triggered.connect(lambda: self.graph_view.edit_sequence(node))
        menu.addAction(edit_sequence_action)
        
        '''add_action_action = QAction("Add Action", menu)
        add_action_action.triggered.connect(lambda: self.graph_view.record_single_action(node))
        menu.addAction(add_action_action)'''#add action is deprecated
        
        # Removed: Remove Sequence and Add Conditional Branch

    def add_web_sequence_node_actions(self, menu, node):
        """Add actions for a web sequence node to the context menu"""
        edit_sequence_action = QAction(_("Edit Web Sequence Properties"), menu)
        edit_sequence_action.triggered.connect(lambda: self.graph_view.edit_web_sequence(node))
        menu.addAction(edit_sequence_action)

        record_action = QAction(_("Record Web Session"), menu)
        record_action.triggered.connect(lambda: self.graph_view.record_web_session(node))
        menu.addAction(record_action)

    def add_action_node_actions(self, menu, node):
        """Add actions for an action node to the context menu"""
        remove_action = QAction(_("Remove Action"), menu)
        remove_action.triggered.connect(lambda: self.graph_view.delete_node(node))
        menu.addAction(remove_action)
    
    def add_conditional_node_actions(self, menu, node):
        """Add actions for a conditional node to the context menu"""
        edit_conditional_action = QAction(_("Edit Conditionals"), menu)
        edit_conditional_action.triggered.connect(lambda: self.graph_view.edit_conditional(node))
        menu.addAction(edit_conditional_action)
    
    def add_llm_node_actions(self, menu, node):
        """Add actions for an LLM node to the context menu"""
        edit_llm_action = QAction(_("Edit LLM Configuration"), menu)
        edit_llm_action.triggered.connect(lambda: self.graph_view.edit_llm(node))
        menu.addAction(edit_llm_action)

    def add_chain_import_node_actions(self, menu, node):
        """Add actions for a Chain Import node to the context menu"""
        edit_action = QAction(_("Edit Chain Import Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_chain_import_node(node))
        menu.addAction(edit_action)
        
        expand_action = QAction(_("Expand Chain (Edit in Separate View)"), menu)
        expand_action.triggered.connect(lambda: self.graph_view.expand_chain_import(node))
        menu.addAction(expand_action)

    def add_code_node_actions(self, menu, node):
        """Add actions for a Code node to the context menu"""
        edit_action = QAction(_("Edit Code"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_code_node(node))
        menu.addAction(edit_action)

    def add_container_node_actions(self, menu, node):
        """Add actions for a Container node to the context menu"""
        edit_action = QAction(_("Edit Container Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_container_node(node))
        menu.addAction(edit_action)

    def add_context_node_actions(self, menu, node):
        """Add actions for a Context node to the context menu"""
        edit_action = QAction(_("Edit Context Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_context_node(node))
        menu.addAction(edit_action)

    def add_input_node_actions(self, menu, node):
        """Add actions for an Input node to the context menu"""
        edit_action = QAction(_("Edit Input Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_input_node(node))
        menu.addAction(edit_action)

    def add_handle_node_actions(self, menu, node):
        """Add actions for a Handle node to the context menu"""
        edit_action = QAction(_("Edit Handle Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_handle_node(node))
        menu.addAction(edit_action)

    def add_mcp_node_actions(self, menu, node):
        """Add actions for an MCP node to the context menu"""
        edit_action = QAction(_("Edit MCP Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_mcp_node(node))
        menu.addAction(edit_action)

    def add_output_node_actions(self, menu, node):
        """Add actions for an Output node to the context menu"""
        edit_action = QAction(_("Edit Output Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_output_node(node))
        menu.addAction(edit_action)

    def add_form_filler_node_actions(self, menu, node):
        """Add actions for a Form Filling node to the context menu"""
        edit_action = QAction(_("Edit Form Filling Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_form_filler_node(node))
        menu.addAction(edit_action)
    


    def add_graph_actions(self, menu, pos):
        """Add actions for the graph background to the context menu"""
        # Global actions trimmed per toolbar changes
        
        add_conditional_action = QAction(_("Add Conditional Node"), menu)
        add_conditional_action.triggered.connect(lambda: self.graph_view.add_conditional_node())
        menu.addAction(add_conditional_action)

        add_web_sequence_action = QAction(_("Add Web Sequence Node"), menu)
        add_web_sequence_action.triggered.connect(lambda: self.graph_view.add_web_sequence_node())
        menu.addAction(add_web_sequence_action)

        add_new_web_sequence_action = QAction(_("Add New Web Sequence (Record)"), menu)
        add_new_web_sequence_action.triggered.connect(lambda: self.graph_view.add_new_web_sequence())
        menu.addAction(add_new_web_sequence_action)
        
        add_llm_action = QAction(_("Add LLM Node"), menu)
        add_llm_action.triggered.connect(lambda: self.graph_view.add_llm_node())
        menu.addAction(add_llm_action)
        
        add_code_action = QAction(_("Add Code Node"), menu)
        add_code_action.triggered.connect(lambda: self.graph_view.add_code_node())
        menu.addAction(add_code_action)
        
        add_container_action = QAction(_("Add Container Node"), menu)
        add_container_action.triggered.connect(lambda: self.graph_view.add_container_node())
        menu.addAction(add_container_action)
        
        add_context_action = QAction(_("Add Context Node"), menu)
        add_context_action.triggered.connect(lambda: self.graph_view.add_context_node())
        menu.addAction(add_context_action)

        add_chain_import_action = QAction(_("Add Chain Import Node"), menu)
        add_chain_import_action.triggered.connect(lambda: self.graph_view.add_chain_import_node())
        menu.addAction(add_chain_import_action)
        
        add_input_action = QAction(_("Add Input Node"), menu)
        add_input_action.triggered.connect(lambda: self.graph_view.add_input_node())
        menu.addAction(add_input_action)
        
        add_handle_action = QAction(_("Add Handle Node"), menu)
        add_handle_action.triggered.connect(lambda: self.graph_view.add_handle_node())
        menu.addAction(add_handle_action)

        add_mcp_action = QAction(_("Add MCP Server Node"), menu)
        add_mcp_action.triggered.connect(lambda: self.graph_view.add_mcp_node())
        menu.addAction(add_mcp_action)

        add_output_action = QAction(_("Add Output Node"), menu)
        add_output_action.triggered.connect(lambda: self.graph_view.add_output_node())
        menu.addAction(add_output_action)
