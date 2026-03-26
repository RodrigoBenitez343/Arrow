from ....config import logger
import copy
import json
import os
import re

class LLMMixin:
    def _execute_llm_node(self, node, stop_flag, action_handlers=None):
        """
        Executes an LLM node and returns the next node ID.
        Supports Agentic Tool Use loop (ReAct pattern).
        """
        # Lazy initialization
        if not self._runtime_initialized:
            self._initialize_chain_runtime()
            self._runtime_initialized = True
        
        # Check for LLM -> TTS connection (redirect logic)
        llm_data = node.get('data', {})
        llm_config = llm_data.get('llm_configuration', {}) or llm_data
        write_text = llm_config.get('write_text', True)
        
        # --- Tool Discovery & Classification ---
        agent_tools = {}
        routing_tools = []
        
        inputs = node.get('inputs', [])
        for inp in inputs:
            if str(inp.get('input_port')) == 'tools':
                from_id = inp.get('from_node')
                if from_id and from_id in self.workflow_graph:
                    target_node = self.workflow_graph[from_id]
                    if target_node.get('type') == 'code':
                        # It's a Code Node -> Agentic Tool
                        agent_tools[from_id] = target_node
                    else:
                        # It's likely a Chain Import or other -> Routing Tool
                        # TODO: In the future, Chain Import nodes could also be treated as agent tools (subroutines)
                        # For now, we treat them as routing destinations (exit paths) unless we implement _execute_chain_node
                        routing_tools.append(from_id)
        
        # Inject chain descriptions for routing tools (existing logic)
        self._inject_chain_descriptions(node, routing_tools)

        # Check for TTS redirection (existing logic)
        output_variable, node_to_execute = self._handle_tts_redirection(node, llm_config, write_text)
        
        # --- Execution Strategy ---
        # Use Agent Loop if there are tools connected that require it, 
        # otherwise use standard execution to respect context options (clipboard, OCR, custom instructions, etc).
        if agent_tools or routing_tools or llm_config.get('use_agent_loop', False):
            return self._execute_agent_loop(node_to_execute, stop_flag, action_handlers, agent_tools, routing_tools)
        else:
            return self.llm_executor.execute_llm_node(node_to_execute, stop_flag, action_handlers)

    def _inject_chain_descriptions(self, node, routing_tool_ids):
        try:
            tool_descriptions_update = {}
            for tid in routing_tool_ids:
                if tid in self.workflow_graph:
                    tool_node = self.workflow_graph[tid]
                    if tool_node.get('type') == 'chain_import':
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
                                            tool_descriptions_update[tid] = desc
                            except Exception:
                                pass
            
            if tool_descriptions_update:
                ndata = node.get('data', {})
                if 'llm_configuration' not in ndata:
                    ndata['llm_configuration'] = {}
                llm_conf = ndata['llm_configuration']
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

    def _handle_tts_redirection(self, node, llm_config, write_text):
        node_to_execute = node
        output_variable = llm_config.get('output_variable', 'llm_output')
        connections = node.get('connections', {}).get('output', [])
        for connection in connections:
            target_node_id = connection.get('node_id')
            if target_node_id and target_node_id in self.workflow_graph:
                target_node = self.workflow_graph[target_node_id]
                if target_node.get('type') == 'tts':
                    target_node['data']['text'] = f"{{{output_variable}}}"
                    if write_text:
                        node_to_execute = copy.deepcopy(node)
                        node_data_copy = node_to_execute.get('data', {})
                        if 'llm_configuration' in node_data_copy:
                            node_data_copy['llm_configuration']['write_text'] = False
                        else:
                            node_data_copy['write_text'] = False
                    break
        return output_variable, node_to_execute

    def _execute_agent_loop(self, node, stop_flag, action_handlers, agent_tools, routing_tools):
        """
        Runs the ReAct loop: Think -> Act -> Observe -> Repeat
        Follows Dash-inspired mistake reasoning and on-the-fly tool creation.
        """
        history = []
        max_turns = 10
        current_turn = 0
        
        # Prepare Agent Instructions
        instructions = ["\n\n## AGENTIC CAPABILITIES ENABLED"]
        instructions.append("You are an autonomous agent. You can Reason, Act (use tools/code), and Observe results.")
        
        # Dynamic Python Tool (Always available in Approach 2)
        instructions.append("\n### 1. Dynamic Python Execution (Preferred for new tasks):")
        instructions.append("   - Create and execute Python tools on the fly.")
        instructions.append("   - **Library Support**: You can import any standard or external library (e.g., `requests`, `pandas`, `psutil`).")
        instructions.append("   - The system will AUTOMATICALLY detect imports and install them in a temporary virtual environment.")
        instructions.append("   - Use this to calculate values, call external APIs, or process data.")
        instructions.append("   - **IMPORTANT**: If an API requires a key (like OpenWeatherMap), do NOT ask the user for it. Instead, try to find an alternative way (e.g., using 'wttr.in' with requests, web scraping with BeautifulSoup, or other free libraries).")
        instructions.append("   - **Format:**")
        instructions.append("     RUN_PYTHON:")
        instructions.append("     ```python")
        instructions.append("     # your code here")
        instructions.append("     print(result)")
        instructions.append("     ```")
        
        # List connected Code Nodes
        if agent_tools:
            instructions.append("\n### 2. Available Code Tools:")
            for tid, tnode in agent_tools.items():
                name = tnode.get('data', {}).get('name') or f"Tool_{tid}"
                desc = tnode.get('data', {}).get('label') or tnode.get('data', {}).get('description') or "Custom Code Node"
                instructions.append(f"   - **{name}** (ID: {tid})")
                instructions.append(f"     Description: {desc}")
                instructions.append(f"     To Call: CALL_TOOL: {tid} {{json_arguments}}")
                instructions.append(f"     (Arguments are passed as 'args' dictionary to the code)")

        instructions.append("\n### 3. Workflow Routing (Final Action):")
        if routing_tools:
            instructions.append("   - Use these to hand off control to a specialized sub-chain.")
            for tid in routing_tools:
                 # Try to get description
                 desc = "Chain/Sub-process"
                 if tid in self.workflow_graph:
                     tdata = self.workflow_graph[tid].get('data', {})
                     desc = tdata.get('label') or tdata.get('description') or desc
                 instructions.append(f"   - Route to ID {tid}: {desc}")
            instructions.append("   - To Route: USE_TOOL: {id}")
        else:
            instructions.append("   - No specific routing tools available. Provide final answer when done.")
        
        instructions.append("\n### EXECUTION STRATEGY:")
        instructions.append("1. **THOUGHT**: Plan your next step. What do you need to do?")
        instructions.append("2. **ACTION**: Use RUN_PYTHON (create new tool) or CALL_TOOL (use existing).")
        instructions.append("   - You MUST start your code block with 'RUN_PYTHON:' to execute it.")
        instructions.append("   - DO NOT just explain the code. EXECUTE it.")
        instructions.append("3. **OBSERVATION**: The system will execute and show you the result.")
        instructions.append("4. **MISTAKE REASONING (CRITICAL)**: If execution fails or gives unexpected results:")
        instructions.append("   - Analyze the error in the history.")
        instructions.append("   - EXPLAIN why it failed.")
        instructions.append("   - MODIFY your code/arguments and try again.")
        instructions.append("   - **Loop until it behaves as expected.**")
        instructions.append("5. **FINAL ANSWER**: When satisfied, output the final result directly (do NOT repeat code).")
        instructions.append("   - If the user asked a question, answer it using the tool results.")
        instructions.append("   - Do NOT output 'RUN_PYTHON' or 'CALL_TOOL' in your final answer.")
        
        base_system_msg_suffix = "\n".join(instructions)
        
        node_id = node.get('id') or node.get('data', {}).get('node_id')
        
        while current_turn < max_turns:
            current_turn += 1
            logger.info(f"Agent Loop Turn {current_turn}/{max_turns} for node {node_id}")
            
            # Create a temporary node for this turn
            temp_node = copy.deepcopy(node)
            
            # Filter 'tools' inputs to HIDE agent_tools from executor.py
            # We only keep routing_tools in the inputs list for the executor to see
            filtered_inputs = []
            for inp in temp_node.get('inputs', []):
                if str(inp.get('input_port')) == 'tools':
                    if inp.get('from_node') in routing_tools:
                        filtered_inputs.append(inp)
                else:
                    filtered_inputs.append(inp)
            temp_node['inputs'] = filtered_inputs
            
            # Update System Message with Instructions + History
            llm_conf = temp_node.get('data', {}).get('llm_configuration', {})
            if not llm_conf:
                llm_conf = temp_node.get('data', {})
            
            sys_msg = llm_conf.get('system_message', '')
            
            # Append History
            history_text = ""
            if history:
                history_text = "\n\n## EXECUTION HISTORY (Learn from this):\n"
                for entry in history:
                    history_text += f"\n--- Turn {entry['step']} ---\n"
                    history_text += f"You: {entry['agent_output']}\n"
                    if entry['tool_output']:
                        history_text += f"System: {entry['tool_output']}\n"

            chain_result_text = ""
            try:
                recent_result = self.llm_executor.get_variable("chain_tool_last_result_text")
                if recent_result:
                    chain_result_text = "\n\n## LAST CHAIN TOOL RESULT:\n" + str(recent_result)
            except Exception:
                pass
            
            llm_conf['system_message'] = sys_msg + base_system_msg_suffix + history_text + chain_result_text
            
            # Update prompt to encourage next step
            if history:
                 llm_conf['prompt'] = llm_conf.get('prompt', '') + "\n(Analyze the previous history. If you have the answer, provide it directly. If there was an error, FIX it.)"
            
            # Execute LLM
            next_node_id = self.llm_executor.execute_llm_node(temp_node, stop_flag, action_handlers)
            
            # Retrieve Output
            output_var = llm_conf.get('output_variable', 'llm_output')
            llm_output = self.llm_executor.get_variable(output_var)
            
            if not llm_output:
                logger.warning("LLM produced no output. Ending loop.")
                return next_node_id
                
            # Parse for Actions
            action_taken = False
            tool_output_str = ""
            
            # Check 1: RUN_PYTHON
            # Regex to capture "RUN_PYTHON:" then code (maybe in backticks)
            py_match = re.search(r"RUN_PYTHON:\s*(?:```(?:python)?\s*)?(.*?)(?:```|$)", str(llm_output), re.DOTALL | re.IGNORECASE)
            
            # Check 2: CALL_TOOL (Code Node)
            # CALL_TOOL: <id> {args}
            tool_match = re.search(r"CALL_TOOL:\s*([^\s\{]+)\s*(.*)", str(llm_output), re.DOTALL | re.IGNORECASE)

            # Check 3: USE_TOOL (Routing - Explicit Exit)
            route_match = re.search(r"USE_TOOL:\s*([^\s]+)", str(llm_output), re.DOTALL | re.IGNORECASE)
            
            if py_match:
                code = py_match.group(1).strip()
                logger.info(f"Agent requested Dynamic Python execution")
                
                # Execute Dynamic Code
                # We use a virtual code node
                virtual_node = {
                    'id': f"{node_id}_dynamic_{current_turn}",
                    'type': 'code',
                    'data': {
                        'code': code,
                        'output_variable': f"dynamic_res_{current_turn}"
                    }
                }
                
                # Run it
                if hasattr(self, '_execute_code_node'):
                    self._execute_code_node(virtual_node, stop_flag)
                    
                    # Get result
                    res = self.llm_executor.get_variable(f"node_{virtual_node['id']}_output")
                    stdout = self.llm_executor.get_variable(f"node_{virtual_node['id']}_stdout")
                    stderr = self.llm_executor.get_variable(f"node_{virtual_node['id']}_stderr")
                    err = self.llm_executor.get_variable(f"node_{virtual_node['id']}_error")
                    
                    tool_output_str = f"Execution Result:\n{res}\n"
                    if stdout: tool_output_str += f"Stdout: {stdout}\n"
                    if stderr: tool_output_str += f"Stderr: {stderr}\n"
                    if err: tool_output_str += f"ERROR: {err}\n"
                else:
                    tool_output_str = "Error: System cannot execute code (CodeMixin not found)."
                
                action_taken = True
                
            elif tool_match and not action_taken:
                tid = tool_match.group(1).strip()
                args_str = tool_match.group(2).strip()
                
                if tid.endswith(':'): tid = tid[:-1]
                
                if tid in agent_tools:
                    logger.info(f"Agent requested Tool execution: {tid}")
                    target_node = agent_tools[tid]
                    
                    # Parse Args
                    args = {}
                    try:
                        import json
                        if args_str.startswith('{'):
                             idx = args_str.rfind('}')
                             if idx != -1:
                                 json_str = args_str[:idx+1]
                                 args = json.loads(json_str)
                    except:
                        pass
                    
                    # Execute
                    if hasattr(self, '_execute_code_node'):
                        # Pass args as overrides
                        self._execute_code_node(target_node, stop_flag, arg_overrides=args)
                        
                        # Get result
                        res = self.llm_executor.get_variable(f"node_{tid}_output")
                        err = self.llm_executor.get_variable(f"node_{tid}_error")
                        stdout = self.llm_executor.get_variable(f"node_{tid}_stdout")
                        
                        tool_output_str = f"Tool '{tid}' Output: {res}"
                        if stdout: tool_output_str += f"\nStdout: {stdout}"
                        if err: tool_output_str += f"\nError: {err}"
                    else:
                        tool_output_str = "Error: System cannot execute tool (CodeMixin not found)."
                        
                    action_taken = True
                else:
                    # If not in agent_tools, check routing or just ignore
                    if tid in routing_tools:
                         logger.info(f"Agent requested routing to {tid}. Exiting loop.")
                         return next_node_id # Let executor route
            
            elif route_match and not action_taken:
                 tid = route_match.group(1).strip()
                 logger.info(f"Agent requested explicit routing to {tid}")
                 return next_node_id # Exit and route

            if action_taken:
                history.append({
                    'step': current_turn,
                    'agent_output': llm_output,
                    'tool_output': tool_output_str
                })
            else:
                # No action taken -> Final Answer
                logger.info("Agent produced final answer (no tool calls detected).")
                return next_node_id

        # End of Loop
        logger.warning("Agent Loop max turns reached.")
        return None
