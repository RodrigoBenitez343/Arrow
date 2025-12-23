from ....config import logger
from ....json_cache import get_chain
import os
import time

class ChainImportMixin:
    def _execute_chain_import_node(self, node, stop_flag):
        """
        Executes a chain import node with loop count support using direct chain execution.
        
        Args:
            node (dict): The chain import node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        from ...multi_sequence_player import MultiSequencePlayer
        
        # Check stop flag before starting chain import
        if stop_flag and stop_flag():
            logger.info("Stop flag detected before chain import execution - aborting")
            return None
        
        chain_import_data = node.get('data', {})
        logger.debug(f"Chain import node data: {chain_import_data}")
        
        # Try both property names for backward compatibility
        chain_file_path = chain_import_data.get('chain_file_path', '') or chain_import_data.get('chain_file', '')
        loop_count = chain_import_data.get('loop_count', 1)
        extra_delay = chain_import_data.get('extra_delay', 0)
        import_mode = chain_import_data.get('import_mode', 'full')
        prefix = chain_import_data.get('prefix', '')
        
        logger.debug(f"Initial chain file path: '{chain_file_path}'")
        
        # If we still don't have a chain file path, try to get it from import_data
        if not chain_file_path and 'import_data' in chain_import_data:
            import_data = chain_import_data['import_data']
            logger.debug(f"Found import_data: {import_data}")
            chain_file_path = import_data.get('chain_file_path', '') or import_data.get('chain_file', '')
            loop_count = import_data.get('loop_count', loop_count)
            extra_delay = import_data.get('extra_delay', extra_delay)
            import_mode = import_data.get('import_mode', import_mode)
            prefix = import_data.get('prefix', prefix)
            logger.debug(f"Updated chain file path from import_data: '{chain_file_path}'")
        
        # Robustly resolve the chain file path (support nested imports and relative paths)
        resolved_chain_path = chain_file_path
        try:
            base_dir = getattr(self.sequence_executor, 'chain_file_dir', None)
            candidates = []
            if chain_file_path:
                candidates.append(chain_file_path)
                if not chain_file_path.lower().endswith('.json'):
                    candidates.append(chain_file_path + '.json')
            if base_dir:
                if chain_file_path:
                    candidates.append(os.path.join(base_dir, chain_file_path))
                    if not chain_file_path.lower().endswith('.json'):
                        candidates.append(os.path.join(base_dir, chain_file_path + '.json'))
            # Also try current working directory as a fallback
            if chain_file_path:
                candidates.append(os.path.join(os.getcwd(), chain_file_path))
                if not chain_file_path.lower().endswith('.json'):
                    candidates.append(os.path.join(os.getcwd(), chain_file_path + '.json'))
            for candidate in candidates:
                if os.path.exists(candidate):
                    resolved_chain_path = candidate
                    break
            logger.debug(f"Resolved chain import path: '{resolved_chain_path}' (base_dir={base_dir})")
        except Exception as e:
            logger.warning(f"Error resolving chain import path '{chain_file_path}': {e}")
        
        logger.info(f"Executing chain import node: {resolved_chain_path} (loops: {loop_count}, extra_delay: {extra_delay})")
        
        if not resolved_chain_path:
            logger.error(f"No chain file path specified in chain import node data: {chain_import_data}")
            # Continue to next node even if chain file is missing
            connections = node.get('connections', {})
            if 'output' in connections and connections['output']:
                next_connection = connections['output'][0]
                return next_connection['node_id']
            return "__done__"
            
        if not os.path.exists(resolved_chain_path):
            logger.error(f"Chain import file not found: {resolved_chain_path}")
            # Continue to next node even if chain file is missing
            connections = node.get('connections', {})
            if 'output' in connections and connections['output']:
                next_connection = connections['output'][0]
                return next_connection['node_id']
            return "__done__"
        
        # Execute the imported chain for the specified number of loops using direct execution
        for i in range(loop_count):
            if stop_flag and stop_flag():
                logger.info("Chain import execution stopped by user request")
                return None
            
            logger.info(f"Starting chain import loop {i + 1}/{loop_count}")
            
            try:
                # Check stop flag before loading chain
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected before loading imported chain - aborting")
                    return None
                
                # Load and execute the imported chain directly using MultiSequencePlayer
                logger.info(f"Loading imported chain: {resolved_chain_path}")
                
                # Try to get chain config from cache first for optimal performance
                chain_config = get_chain(resolved_chain_path)
                if chain_config:
                    logger.info(f"Using cached chain config for: {resolved_chain_path}")
                else:
                    # Fallback to loading from disk if not in cache
                    logger.info(f"Loading chain config from disk (not in cache): {resolved_chain_path}")
                    import json
                    with open(resolved_chain_path, 'r', encoding='utf-8') as f:
                        chain_config = json.load(f)
                
                # Check stop flag after loading chain config
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected after loading chain config - aborting")
                    return None
                
                # Pass the resolved path so nested imports and relative assets are correctly located
                # Also pass shared variables to allow aggregation of apps
                imported_player = MultiSequencePlayer(
                    chain_config, 
                    chain_file_path=resolved_chain_path,
                    variables=self.llm_executor.variables
                )
                # Share selection memory with imported chain player
                try:
                    # Ensure the imported player uses the shared selection memory
                    imported_player.selection_memory = self.selection_memory if self.selection_memory is not None else {}
                    # Propagate the shared memory to all modular components created during imported_player init
                    try:
                        if hasattr(imported_player, 'fallback_handler') and imported_player.fallback_handler:
                            imported_player.fallback_handler.selection_memory = imported_player.selection_memory
                    except Exception:
                        pass
                    try:
                        if hasattr(imported_player, 'sequence_executor') and imported_player.sequence_executor:
                            imported_player.sequence_executor.selection_memory = imported_player.selection_memory
                    except Exception:
                        pass
                    try:
                        if hasattr(imported_player, 'workflow_executor') and imported_player.workflow_executor:
                            imported_player.workflow_executor.selection_memory = imported_player.selection_memory
                    except Exception:
                        pass
                except Exception:
                    pass
                
                # Execute the imported chain using the same method as normal chains
                logger.info(f"Executing imported chain: {resolved_chain_path}")
                success = imported_player.play_chain(stop_flag)
                
                # Check stop flag after chain execution
                if stop_flag and stop_flag():
                    logger.info("Stop flag detected after imported chain execution - aborting")
                    return None
                
                if not success:
                    logger.error(f"Imported chain execution failed: {resolved_chain_path}")
                    # Continue to next loop iteration
                    continue
                else:
                    logger.info(f"Imported chain executed successfully: {resolved_chain_path}")
                
            except Exception as e:
                logger.error(f"Error during chain import execution: {e}")
                import traceback
                logger.error(f"Full traceback: {traceback.format_exc()}")
                # Continue to next loop iteration
                continue
            
            # Add extra delay between loops if specified
            if extra_delay > 0 and i < loop_count - 1:  # Don't delay after the last loop
                logger.info(f"Adding extra delay of {extra_delay} seconds between loops")
                delay_remaining = extra_delay
                while delay_remaining > 0:
                    if stop_flag and stop_flag():
                        logger.info("Chain import execution stopped during delay")
                        return None
                    sleep_time = min(0.1, delay_remaining)
                    time.sleep(sleep_time)
                    delay_remaining -= sleep_time
        
        logger.info("Chain import node execution completed")
        
        # Continue to next node in the workflow
        connections = node.get('connections', {})
        if 'output' in connections and connections['output']:
            next_connection = connections['output'][0]
            return next_connection['node_id']
        
        return None
