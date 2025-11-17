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
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'action':
            self.add_action_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'conditional':
            self.add_conditional_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'llm':
            self.add_llm_node_actions(menu, node)
        # DEPRECATED: Context Builder - context menu branch removed
        # elif hasattr(node, '__identifier__') and node.__identifier__ == 'context_builder':
        #     self.add_context_builder_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'tts':
            self.add_tts_node_actions(menu, node)
        elif hasattr(node, '__identifier__') and node.__identifier__ == 'chain_import':
            self.add_chain_import_node_actions(menu, node)
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
    
    # DEPRECATED: Context Builder - actions removed
    # def add_context_builder_node_actions(self, menu, node):
    #     """Add actions for a Context Builder node to the context menu"""
    #     edit_action = QAction("Edit Context Builder Configuration", menu)
    #     edit_action.triggered.connect(lambda: self.graph_view.edit_context_builder_node(node))
    #     menu.addAction(edit_action)
    #     
    #     add_conditional_action = QAction("Add Conditional Branch", menu)
    #     add_conditional_action.triggered.connect(lambda: self.graph_view.add_conditional_branch_to_llm(node))
    #     menu.addAction(add_conditional_action)
    #     
    #     delete_action = QAction("Delete Context Builder Node", menu)
    #     delete_action.triggered.connect(lambda: self.graph_view.delete_node(node))
    #     menu.addAction(delete_action)
    def add_tts_node_actions(self, menu, node):
        """Add actions for a TTS node to the context menu"""
        edit_action = QAction(_("Edit TTS Properties"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_tts_node(node))
        menu.addAction(edit_action)
    
    def add_chain_import_node_actions(self, menu, node):
        """Add actions for a Chain Import node to the context menu"""
        edit_action = QAction(_("Edit Chain Import Configuration"), menu)
        edit_action.triggered.connect(lambda: self.graph_view.edit_chain_import_node(node))
        menu.addAction(edit_action)
        
        expand_action = QAction(_("Expand Chain (Edit in Separate View)"), menu)
        expand_action.triggered.connect(lambda: self.graph_view.expand_chain_import(node))
        menu.addAction(expand_action)
    


    def add_graph_actions(self, menu, pos):
        """Add actions for the graph background to the context menu"""
        # Global actions trimmed per toolbar changes
        
        add_conditional_action = QAction(_("Add Conditional Node"), menu)
        add_conditional_action.triggered.connect(lambda: self.graph_view.add_conditional_node())
        menu.addAction(add_conditional_action)
        
        add_llm_action = QAction(_("Add LLM Node"), menu)
        add_llm_action.triggered.connect(lambda: self.graph_view.add_llm_node())
        menu.addAction(add_llm_action)
        
        # DEPRECATED: Context Builder - add action removed from graph menu
        # add_context_builder_action = QAction("Add Context Builder Node", menu)
        # add_context_builder_action.triggered.connect(lambda: self.graph_view.add_context_builder_node())
        # menu.addAction(add_context_builder_action)
        add_tts_action = QAction(_("Add TTS Node"), menu)
        add_tts_action.triggered.connect(lambda: self.graph_view.add_tts_node())
        menu.addAction(add_tts_action)
        
        add_chain_import_action = QAction(_("Add Chain Import Node"), menu)
        add_chain_import_action.triggered.connect(lambda: self.graph_view.add_chain_import_node())
        menu.addAction(add_chain_import_action)