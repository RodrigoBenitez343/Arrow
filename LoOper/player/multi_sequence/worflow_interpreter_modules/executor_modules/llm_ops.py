from ....config import logger
import copy
import json
import os

class LLMMixin:
    def _execute_llm_node(self, node, stop_flag, action_handlers=None):
        """
        Executes an LLM node and returns the next node ID.
        
        Args:
            node (dict): The LLM node from the workflow graph
            stop_flag (callable): Stop flag function
            action_handlers: Action handlers for text writing
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        # Lazy initialization: Only set up runtime environment when actually needed
        if not self._runtime_initialized:
            self._initialize_chain_runtime()
            self._runtime_initialized = True
        
        # Check for LLM -> TTS connection with "write automatically" enabled
        # If found, disable typing and redirect output to TTS node
        llm_data = node.get('data', {})
        llm_config = llm_data.get('llm_configuration', {}) or llm_data
        write_text = llm_config.get('write_text', True)
        
        node_to_execute = node
        
        # Inject chain descriptions for connected tools
        try:
            inputs = node.get('inputs', [])
            tool_descriptions_update = {}
            
            for inp in inputs:
                if str(inp.get('input_port')) == 'tools':
                    from_node_id = inp.get('from_node')
                    if from_node_id and from_node_id in self.workflow_graph:
                        tool_node = self.workflow_graph[from_node_id]
                        if tool_node.get('type') == 'chain_import':
                            # Get chain file path
                            chain_data = tool_node.get('data', {})
                            chain_file = chain_data.get('chain_file') or chain_data.get('chain_import_config', {}).get('chain_file')
                            
                            if chain_file:
                                try:
                                    resolved_path = chain_file
                                    if not os.path.exists(resolved_path):
                                         base_dir = getattr(self.sequence_executor, 'chain_file_dir', None)
                                         if base_dir:
                                             candidate = os.path.join(base_dir, chain_file)
                                             if os.path.exists(candidate):
                                                 resolved_path = candidate
                                    
                                    if os.path.exists(resolved_path):
                                        with open(resolved_path, 'r', encoding='utf-8') as f:
                                            cdata = json.load(f)
                                            desc = cdata.get('description')
                                            if desc:
                                                tool_descriptions_update[from_node_id] = desc
                                except Exception as e:
                                    logger.warning(f"Failed to load description for chain tool {chain_file}: {e}")
            
            if tool_descriptions_update:
                if node_to_execute is node:
                    node_to_execute = copy.deepcopy(node)
                
                ndata = node_to_execute.get('data', {})
                if 'llm_configuration' not in ndata:
                    ndata['llm_configuration'] = {}
                
                llm_conf = ndata['llm_configuration']
                
                # Merge with existing tool descriptions
                existing_td = llm_conf.get('tool_descriptions', {})
                if isinstance(existing_td, str):
                    try:
                        existing_td = json.loads(existing_td)
                    except:
                        existing_td = {}
                
                if not isinstance(existing_td, dict):
                    existing_td = {}
                    
                existing_td.update(tool_descriptions_update)
                llm_conf['tool_descriptions'] = existing_td
                
        except Exception as e:
            logger.error(f"Error injecting chain descriptions: {e}")
        
        # Check for LLM -> TTS connection
        # Always redirect output to TTS if connected, regardless of write_text setting
        connections = node.get('connections', {}).get('output', [])
        for connection in connections:
            target_node_id = connection.get('node_id')
            if target_node_id and target_node_id in self.workflow_graph:
                target_node = self.workflow_graph[target_node_id]
                if target_node.get('type') == 'tts':
                    # Configure TTS node to use the LLM output variable
                    output_variable = llm_config.get('output_variable', 'llm_output')
                    
                    # Update the TTS node in the graph to use the variable
                    # This modifies the graph for the rest of this execution
                    target_node['data']['text'] = f"{{{output_variable}}}"
                    logger.info(f"Updated TTS node {target_node_id} text to use variable {{{output_variable}}}")
                    
                    # If write_text is enabled, disable it since we are redirecting to TTS
                    if write_text:
                        logger.info(f"LLM node {node.get('id', 'unknown')} connected to TTS node {target_node_id}. Disabling write_text.")
                        
                        # Create a copy of the node to modify write_text without affecting the graph permanently
                        node_to_execute = copy.deepcopy(node)
                        
                        # Disable write_text in the copy
                        node_data_copy = node_to_execute.get('data', {})
                        if 'llm_configuration' in node_data_copy:
                            node_data_copy['llm_configuration']['write_text'] = False
                        else:
                            node_data_copy['write_text'] = False
                    
                    # Only handle the first TTS connection found
                    break

        # Use the LLMExecutor to handle LLM execution and return its decision on next node
        next_node_id = self.llm_executor.execute_llm_node(node_to_execute, stop_flag, action_handlers)
        try:
            self._llm_selected_next = None
            chosen_id = next_node_id if isinstance(next_node_id, str) else None
            # Fallback: parse tool marker or scan for tool ids/descriptions
            try:
                if (not chosen_id) or (chosen_id not in self.workflow_graph):
                    out_var = None
                    llm_id = node.get('id') or node.get('data', {}).get('node_id') or node.get('data', {}).get('id')
                    if llm_id:
                        out_var = self.llm_executor.get_variable(f"node_{llm_id}_output") or ""
                    s = str(out_var or "")
                    if s:
                        for mk in ("USE_TOOL:", "CALL_TOOL:", "TOOL:"):
                            idx = s.find(mk)
                            if idx >= 0:
                                val = s[idx+len(mk):].strip()
                                val = val.split()[0].strip()
                                while val and val[-1] in (":", ",", ";", ".", ")", "]", "}"):
                                    val = val[:-1]
                                if val:
                                    chosen_id = val
                                    break
                    if not chosen_id:
                        tools = []
                        for inp in (node.get('inputs', []) or []):
                            if str(inp.get('input_port')) == 'tools':
                                tools.append(str(inp.get('from_node')))
                        for tid in tools:
                            if tid and (tid in s):
                                chosen_id = tid
                                break
                        if not chosen_id:
                            try:
                                dmap = node.get('data', {}).get('tool_descriptions', {})
                            except Exception:
                                dmap = {}
                            sl = s.lower()
                            for tid, desc in (dmap.items() if isinstance(dmap, dict) else []):
                                d = str(desc or '').lower()
                                if d and d in sl:
                                    chosen_id = tid
                                    break
                                tail = str(desc or '').split(":")[-1].strip().lower()
                                if tail and tail in sl:
                                    chosen_id = tid
                                    break
                        # Semantic fallback based on previous/context values
                        if not chosen_id:
                            try:
                                prev_text = ""
                                # prefer context value
                                for inp in (node.get('inputs', []) or []):
                                    if str(inp.get('input_port')) == 'context':
                                        pid = str(inp.get('from_node'))
                                        v = self.llm_executor.get_variable(f"node_{pid}_context") or ""
                                        if v:
                                            prev_text = str(v)
                                            break
                                if not prev_text:
                                    for inp in (node.get('inputs', []) or []):
                                        if str(inp.get('input_port')) == 'input':
                                            pid = str(inp.get('from_node'))
                                            v = self.llm_executor.get_variable(f"node_{pid}_output") or ""
                                            if v:
                                                prev_text = str(v)
                                                break
                                pl = prev_text.lower()
                                want_open = False
                                want_close = False
                                for w in ("no browser", "not open", "closed", "no visible", "none open"):
                                    if w in pl:
                                        want_open = True
                                        break
                                if not want_open:
                                    for w in ("browser is open", "is open", "open window", "visible browser"):
                                        if w in pl:
                                            want_close = True
                                            break
                                if want_open:
                                    for tid, desc in (dmap.items() if isinstance(dmap, dict) else []):
                                        dl = str(desc or '').lower()
                                        if ("open_browser" in dl) or ("open browser" in dl) or dl.endswith("open_browser"):
                                            chosen_id = tid
                                            break
                                elif want_close:
                                    for tid, desc in (dmap.items() if isinstance(dmap, dict) else []):
                                        dl = str(desc or '').lower()
                                        if ("close_browser" in dl) or ("close browser" in dl) or dl.endswith("close_browser"):
                                            chosen_id = tid
                                            break
                            except Exception:
                                pass
            except Exception:
                pass
            if chosen_id and chosen_id in self.workflow_graph:
                target_node = self.workflow_graph.get(chosen_id, {})
                if target_node.get('type') == 'chain_import' and target_node.get('tool_provider'):
                    target_node['selected_by_llm'] = True
                self._llm_selected_next = chosen_id
                logger.info(f"LLM selected next node: {chosen_id}")
        except Exception:
            pass
        return next_node_id
