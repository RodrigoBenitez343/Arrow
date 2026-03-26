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
        # Get sandbox configuration
        chain_data = node.get('data', {})
        run_in_sandbox = str(chain_data.get('run_in_sandbox', 'false')).lower() == 'true'
        show_sandbox_window = str(chain_data.get('show_sandbox_window', 'true')).lower() == 'true'
        
        # Build app context override for sandboxing if needed
        app_context_override = None
        if run_in_sandbox:
            app_context_override = {
                "sandboxed": True,
                "show_window": show_sandbox_window
            }

        try:
            from ....multi_sequence_player import MultiSequencePlayer
        except ImportError:
            # Fallback for different directory structures
            try:
                from ...multi_sequence_player import MultiSequencePlayer
            except ImportError:
                from player.multi_sequence_player import MultiSequencePlayer
        
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
                
                # Always load the imported chain directly from disk to ensure we have the full, up-to-date workflow
                # The embedded nodes in the JSON are only for UI representation and may be incomplete or stale.
                logger.info(f"Loading imported chain from disk: {resolved_chain_path}")
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
                
                # Apply sandbox context if configured
                if app_context_override:
                    logger.info(f"Applying sandbox override to imported chain: {app_context_override}")
                    if hasattr(imported_player, 'workflow_executor') and imported_player.workflow_executor:
                        imported_player.workflow_executor.global_app_context_override = app_context_override
                    if hasattr(imported_player, 'sequence_executor') and imported_player.sequence_executor:
                        imported_player.sequence_executor.global_app_context_override = app_context_override
                else:
                    # Inherit parent's sandbox context if the imported chain is not explicitly sandboxed
                    parent_sandbox_ctx = None
                    try:
                        if hasattr(self, 'workflow_executor') and self.workflow_executor:
                            parent_sandbox_ctx = getattr(self.workflow_executor, 'global_app_context_override', None)
                        elif hasattr(self, 'sequence_executor') and self.sequence_executor:
                            parent_sandbox_ctx = getattr(self.sequence_executor, 'global_app_context_override', None)
                    except Exception:
                        pass
                    
                    if parent_sandbox_ctx and isinstance(parent_sandbox_ctx, dict) and parent_sandbox_ctx.get("sandboxed"):
                        logger.info(f"Inheriting parent sandbox override to imported chain: {parent_sandbox_ctx}")
                        if hasattr(imported_player, 'workflow_executor') and imported_player.workflow_executor:
                            imported_player.workflow_executor.global_app_context_override = parent_sandbox_ctx
                        if hasattr(imported_player, 'sequence_executor') and imported_player.sequence_executor:
                            imported_player.sequence_executor.global_app_context_override = parent_sandbox_ctx
                        
                # Share selection memory with imported chain player
                try:
                    # Do not share selection memory to allow fresh runs on recursive chains/loops.
                    # This fixes the issue where conditionals keep hitting the same cached elements in loops.
                    imported_player.selection_memory = {}
                    
                    # Propagate the fresh memory to all modular components
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
                success = imported_player.play_chain(stop_flag, preserve_selection_memory=False)
                
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
                    summary = None
                    if hasattr(imported_player, "workflow_executor") and imported_player.workflow_executor:
                        summary = getattr(imported_player.workflow_executor, "last_node_summary", None)
                    if summary and hasattr(self, "llm_executor") and self.llm_executor:
                        tool_id = node.get('id') or node.get('node_id') or ""
                        if not tool_id and isinstance(node.get("data"), dict):
                            tool_id = node.get("data", {}).get("id") or node.get("data", {}).get("node_id") or ""
                        if not tool_id and resolved_chain_path:
                            tool_id = os.path.splitext(os.path.basename(resolved_chain_path))[0]
                        chain_name = ""
                        chain_desc = ""
                        try:
                            if isinstance(chain_config, dict):
                                chain_name = chain_config.get("name") or ""
                                chain_desc = chain_config.get("description") or ""
                        except Exception:
                            pass
                        record = {
                            "tool_id": str(tool_id),
                            "chain_path": str(resolved_chain_path),
                            "chain_name": str(chain_name),
                            "chain_description": str(chain_desc),
                            "last_node": summary
                        }
                        try:
                            self.llm_executor.set_variable(f"node_{tool_id}_last_chain_result", record)
                        except Exception:
                            pass
                        try:
                            results = self.llm_executor.get_variable("chain_tool_results") or {}
                            if not isinstance(results, dict):
                                results = {}
                            results[str(tool_id)] = record
                            self.llm_executor.set_variable("chain_tool_results", results)
                        except Exception:
                            pass
                        try:
                            parts = []
                            if tool_id:
                                parts.append(f"Tool ID: {tool_id}")
                            if chain_name:
                                parts.append(f"Chain Name: {chain_name}")
                            if chain_desc:
                                parts.append(f"Chain Description: {chain_desc}")
                            if summary:
                                parts.append(f"Last Node: {summary.get('node_id')} ({summary.get('type')})")
                                if summary.get("type") == "code":
                                    parts.append(f"Code Result: {summary.get('result')}")
                                elif summary.get("type") == "conditional":
                                    parts.append(f"Conditional Result: {summary.get('result')}")
                                    if summary.get("criteria"):
                                        parts.append(f"Criteria: {summary.get('criteria')}")
                                elif summary.get("type") == "llm":
                                    llm_result = summary.get("result") or {}
                                    if isinstance(llm_result, dict):
                                        q = llm_result.get("query")
                                        r = llm_result.get("response")
                                        if q:
                                            parts.append(f"LLM Query: {q}")
                                        if r is not None:
                                            parts.append(f"LLM Response: {r}")
                            result_text = "\n".join([p for p in parts if p])
                            if result_text:
                                self.llm_executor.set_variable("chain_tool_last_result_text", result_text)
                        except Exception:
                            pass
                
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
        
        return "__done__"
