class SequenceMixin:
    def _execute_sequence_node(self, node, stop_flag):
        """
        Executes a sequence node and returns the next node ID.
        
        Args:
            node (dict): The sequence node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Use the SequenceExecutor to handle sequence execution
        # Apply global context override (e.g. for Sandbox)
        if hasattr(self, 'global_app_context_override') and self.global_app_context_override:
            self.sequence_executor.global_app_context_override = self.global_app_context_override
            
        next_node_id = self.sequence_executor.execute_sequence_node(node, stop_flag)
        return next_node_id

    def _execute_web_sequence_node(self, node, stop_flag):
        """
        Executes a web sequence node (DOM-based browser session) and returns
        the next node ID.
        
        Args:
            node (dict): The web sequence node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        next_node_id = self.sequence_executor.execute_web_sequence_node(node, stop_flag)
        return next_node_id
