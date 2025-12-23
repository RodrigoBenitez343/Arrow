from ....config import logger

class ConditionalMixin:
    def _evaluate_conditional(self, conditional_node, stop_flag):
        """
        Evaluate a conditional node to determine which branch to take.
        
        Args:
            conditional_node (dict): The conditional node configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if condition is met, False otherwise
        """
        # Extract condition configuration
        raw_trigger_type = conditional_node.get('trigger_type', 'presence_trigger')
        condition_type = raw_trigger_type.lower()
        
        # Build condition configuration from node data
        condition_config = {
            'image_path': conditional_node.get('image_path'),
            'confidence': conditional_node.get('confidence', 0.8),
            'timeout': conditional_node.get('timeout', 10),
            'target_text': conditional_node.get('target_text'),
            'region': conditional_node.get('region'),
            'strict_validation': conditional_node.get('strict_validation', False)
        }
        
        # Also check if there's a nested condition_data (for backward compatibility)
        if 'condition_data' in conditional_node:
            nested_config = conditional_node['condition_data']
            condition_config.update(nested_config)
        
        logger.info(f"Evaluating conditional with raw trigger type: {raw_trigger_type} -> mapped to: {condition_type}")
        logger.debug(f"Condition config: {condition_config}")
        
        # Set stop flag for the fallback handler
        self.fallback_handler.stop_flag = stop_flag
        
        # Capture a single screenshot for visual trigger evaluations to improve performance
        screen_cv = None
        if condition_type in ['presence_trigger', 'absence_trigger']:
            screen_cv = self.fallback_handler.template_matcher.capture_screen()
        
        if condition_type == 'presence_trigger':
            return self.fallback_handler.check_presence_trigger(condition_config, screen_cv=screen_cv)
        elif condition_type == 'absence_trigger':
            timeout = float(condition_config.get('timeout', 10))
            return self.fallback_handler.check_absence_trigger(condition_config, timeout, screen_cv=screen_cv)
        elif condition_type == 'ocr_trigger':
            return self.fallback_handler.check_ocr_trigger(condition_config, stop_flag=stop_flag)
        else:
            logger.warning(f"Unknown condition type: {condition_type} (raw: {raw_trigger_type})")
            return False
    
    def _execute_conditional_loop(self, conditional_data, stop_flag):
        """
        Execute a conditional loop by reconstructing the loop configuration and calling the fallback handler.
        
        Args:
            conditional_data (dict): The conditional node data containing loop configuration
            stop_flag (callable): Stop flag function
            
        Returns:
            bool: True if loop completed successfully, False otherwise
        """
        try:
            import json
            from ...sequence_player import SequencePlayer
            
            # Reconstruct loop configuration from conditional node data
            loop_type = conditional_data.get('loop_type', 'while_present')
            max_iterations = int(conditional_data.get('max_loops', 10))
            sequence_file = conditional_data.get('sequence_file', '')
            iteration_delay = float(conditional_data.get('iteration_delay', 0.05))  # Reduced from 0.3
            
            # Parse the stored loop condition
            loop_condition_json = conditional_data.get('loop_condition', '{}')
            try:
                condition = json.loads(loop_condition_json) if loop_condition_json else {}
            except (json.JSONDecodeError, TypeError):
                # Fallback to basic image condition if JSON parsing fails
                condition = {
                    'trigger_type': 'presence',
                    'image_path': conditional_data.get('image_path', ''),
                    'confidence': float(conditional_data.get('threshold', 0.8))
                }
            
            # Build loop configuration
            loop_config = {
                'type': loop_type,
                'max_iterations': max_iterations,
                'sequence_file': sequence_file,
                'iteration_delay': iteration_delay,
                'condition': condition
            }
            
            logger.info(f"Executing conditional loop: {loop_type}, sequence: {sequence_file}, max_iterations: {max_iterations}")
            
            # Set stop flag for the fallback handler
            self.fallback_handler.stop_flag = stop_flag
            
            # Execute the conditional loop using the fallback handler
            # Create a temporary SequencePlayer for loop execution with the sequence file
            sequence_file = loop_config.get('sequence_file', '')
            if not sequence_file:
                logger.error(f"No sequence file specified for conditional loop")
                return False
            
            temp_sequence_player = SequencePlayer(sequence_file)
            # Share selection memory with temporary sequence player
            try:
                temp_sequence_player.selection_memory = self.selection_memory if self.selection_memory is not None else {}
            except Exception:
                pass
            return self.fallback_handler.execute_conditional_loop(loop_config, temp_sequence_player)
            
        except Exception as e:
            logger.error(f"Error executing conditional loop: {e}")
            return False
    
    def _get_conditional_branch_node(self, node, result):
        """
        Get the next node ID based on conditional result.
        
        Args:
            node (dict): The conditional node
            result (bool): The conditional evaluation result
            
        Returns:
            str: Next node ID, or None if no connection found
        """
        # Determine which branch to follow based on result
        branch = 'true' if result else 'false'
        
        # Get connections for this branch
        connections = node.get('connections', {})
        branch_connections = connections.get(branch, [])
        
        target_node = None
        if branch_connections:
            # Take the first connection in the branch
            target_node = branch_connections[0].get('node_id')
        
        if target_node:
            return target_node
        else:
            logger.info(f"No connection for '{branch}' branch, ending workflow")
            return None

    def _mark_skipped(self, conditional_id, unchosen_port):
        """
        Mark nodes in the unchosen branch as skipped.
        Correctly handles merge points (nodes with inputs from multiple branches).
        
        Args:
            conditional_id (str): The conditional node ID
            unchosen_port (str): The unchosen branch ('true' or 'false')
        """
        import json
        from collections import deque
        
        new_skipped = set()
        conditional_data = self.workflow_graph[conditional_id]
        connections = conditional_data.get('connections', {})
        if isinstance(connections, str):
            connections = json.loads(connections)
        
        # Handle the connections structure - it's organized by branch (true/false)
        unchosen_starts = []
        if isinstance(connections, dict) and unchosen_port in connections:
            unchosen_starts = [conn['node_id'] for conn in connections[unchosen_port]]
        
        queue = deque(unchosen_starts)
        
        # We process nodes and their descendants. A node is only marked as skipped
        # if ALL paths leading to it are from skipped nodes or the unchosen branch.
        while queue:
            current = queue.popleft()
            if current in new_skipped:
                continue
                
            current_data = self.workflow_graph.get(current, {})
            inputs = current_data.get('inputs', [])
            
            all_inputs_skippable = True
            for inp in inputs:
                from_id = inp['from_node']
                from_port = inp.get('output_type', inp.get('output_port'))
                
                # If the input is from the SAME conditional that triggered this skip:
                if from_id == conditional_id:
                    if from_port == unchosen_port:
                        # This path is the one we are unchoosing
                        continue
                    else:
                        # This path is the ACTIVE path of the same conditional.
                        # This node is a merge point for the true/false branches of this conditional.
                        # It should NOT be skipped via this call.
                        all_inputs_skippable = False
                        break
                # If the input is already skipped (globally or in this traversal)
                elif from_id in new_skipped or from_id in self.skipped_nodes:
                    continue
                else:
                    # This input is from a node that is NOT skipped.
                    # It could be from the active branch or some other part of the graph.
                    all_inputs_skippable = False
                    break
            
            if all_inputs_skippable:
                new_skipped.add(current)
                # Add connected nodes to queue for re-evaluation
                current_connections = current_data.get('connections', {})
                if isinstance(current_connections, dict):
                    for output_type, output_list in current_connections.items():
                        if isinstance(output_list, list):
                            for conn in output_list:
                                queue.append(conn['node_id'])
        
        if new_skipped:
            self.skipped_nodes.update(new_skipped)
            logger.info(f"Marked {len(new_skipped)} nodes as skipped from unchosen branch {unchosen_port} of {conditional_id}: {new_skipped}")
