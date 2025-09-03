# llm_executor.py
"""
LLM node executor for multi-sequence automation.
Handles execution of LLM nodes in workflow graphs.
"""

import sys
import os
import time
from ..config import logger
from ..screenshot_cleanup import register_temporal_screenshot, cleanup_screenshot
import glob
import json


# Get default API URL from environment variables (same as consult.py)
def get_default_api_url():
    """Get default API URL using same logic as consult.py"""
    tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{tailscale_ip}:{port}"


class LLMExecutor:
    """Executes LLM nodes in workflow graphs"""
    
    def __init__(self, variables=None):
        self.variables = variables or {}
        self.context_collector = None
        self._initialize_context_collector()
    
    def execute_llm_node(self, node, stop_flag, action_handlers=None):
        """
        Executes an LLM node and returns the next node ID.
        
        Args:
            node (dict): The LLM node from the workflow graph
            stop_flag (callable): Stop flag function
            action_handlers: Action handlers for text writing
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        llm_data = node['data']
        logger.info(f"=== STARTING LLM NODE EXECUTION ===")
        logger.info(f"Executing LLM node: {llm_data.get('name', llm_data.get('id', llm_data.get('node_id', 'Unknown')))}")
        logger.info(f"Full node data: {node}")
        
        try:
            # Add the project root to the path to import from AI module
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            from AI.consult import OllamaClient
            from AI.context_collector import ContextCollector
            
            # Get LLM configuration - handle both nested and direct formats
            llm_config = llm_data.get('llm_configuration', {})
            
            # If no nested configuration, use direct properties from llm_data
            if not llm_config:
                llm_config = llm_data
                
            logger.info(f"LLM configuration: {llm_config}")
            
            model = llm_config.get('model', 'llama3.2')
            api_url = llm_config.get('api_url', get_default_api_url())
            system_message = llm_config.get('system_message', '')
            prompt = llm_config.get('prompt', '')
            temperature = llm_config.get('temperature', 0.7)
            max_tokens = llm_config.get('max_tokens', 1000)
            output_variable = llm_config.get('output_variable', 'llm_output')
            write_text = llm_config.get('write_text', True)
            use_vision = llm_config.get('use_vision', False)
            screenshot_enabled = llm_config.get('screenshot_enabled', True)
            
            # ComoRAG-specific configuration
            use_comorag = str(llm_config.get('use_comorag', 'true')).lower() in ('true', '1', 'yes', 'on')
            max_cycles = int(llm_config.get('max_cycles', 5))
            memory_size = int(llm_config.get('memory_size', 1000))
            
            logger.info(f"LLM Parameters:")
            logger.info(f"  Model: {model}")
            logger.info(f"  API URL: {api_url}")
            logger.info(f"  System Message: {system_message}")
            logger.info(f"  Original Prompt: {prompt}")
            logger.info(f"  Temperature: {temperature}")
            logger.info(f"  Max Tokens: {max_tokens}")
            logger.info(f"  Output Variable: {output_variable}")
            logger.info(f"  Write Text: {write_text}")
            logger.info(f"  Use Vision: {use_vision}")
            logger.info(f"  Screenshot Enabled: {screenshot_enabled}")
            
            # Replace context variables in prompt using previously stored variables
            original_prompt = prompt
            if self.variables:
                logger.info(f"Available variables for substitution: {self.variables}")
                for var_name, var_value in self.variables.items():
                    prompt = prompt.replace(f"{{{var_name}}}", str(var_value))
                logger.info(f"Prompt after variable substitution: {prompt}")
            else:
                logger.info("No variables available for substitution")
            
            # Create OllamaClient and make request
            logger.info(f"Creating OllamaClient with base_url: {api_url}")
            try:
                client = OllamaClient(base_url=api_url, debug=True, use_comorag=use_comorag)
                
                # Set ComoRAG parameters if enabled
                if use_comorag and hasattr(client, 'set_comorag_config'):
                    client.set_comorag_config(max_cycles=max_cycles, memory_size=memory_size)
                    logger.info(f"ComoRAG enabled with max_cycles={max_cycles}, memory_size={memory_size}")
                
                logger.info("OllamaClient created successfully")
            except Exception as client_error:
                logger.error(f"Failed to create OllamaClient: {client_error}")
                return None
            
            # Check if this is a vision model and handle screenshots
            images = None
            ocr_text = None
            is_vision_model = client.is_vision_model(model)
            should_use_vision = use_vision or is_vision_model
            
            if should_use_vision:
                logger.info(f"Vision model detected: {model}")
                if screenshot_enabled:
                    logger.info("Taking screenshot for vision analysis...")
                    try:
                        import pyautogui
                        
                        # Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
                        pyautogui.FAILSAFE = False
                        
                        # Create screenshots directory if it doesn't exist
                        screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'screenshots')
                        os.makedirs(screenshots_dir, exist_ok=True)
                        
                        # Take screenshot
                        screenshot = pyautogui.screenshot()
                        
                        # Save with timestamp
                        timestamp = int(time.time())
                        screenshot_path = os.path.join(screenshots_dir, f'llm_vision_{timestamp}.png')
                        screenshot.save(screenshot_path)
                        
                        # Register for cleanup after use
                        register_temporal_screenshot(screenshot_path)
                        
                        images = [screenshot_path]
                        logger.info(f"Screenshot saved: {screenshot_path}")
                        
                        # Extract OCR text from screenshot for enhanced context
                        try:
                            from ..computer_vision import TemplateMatching
                            ocr_results = TemplateMatching.ocr_extract_all_text(screenshot_path)
                            if ocr_results:
                                ocr_text = "\n".join([item['text'] for item in ocr_results if item['confidence'] > 60])
                                if ocr_text.strip():
                                    logger.info(f"Extracted OCR text ({len(ocr_text)} chars): {ocr_text[:200]}...")
                                    # Add OCR text to context for ComoRAG integration
                                    if hasattr(client, 'add_context'):
                                        client.add_context({
                                            'ocr_text': ocr_text,
                                            'source': 'screen_capture',
                                            'type': 'ocr',
                                            'timestamp': time.time()
                                        })
                                        logger.info(f"Added OCR text to ComoRAG context: {len(ocr_text)} characters")
                                else:
                                    logger.info("No high-confidence OCR text extracted")
                            else:
                                logger.info("No OCR text extracted from screenshot")
                        except Exception as ocr_error:
                            logger.warning(f"OCR extraction failed: {ocr_error}")
                            ocr_text = None
                        
                        # Add screenshot context for ComoRAG integration
                        if hasattr(client, 'add_context'):
                            client.add_context({
                                'screenshot_path': screenshot_path,
                                'description': f'Screen capture for vision analysis at {time.strftime("%Y-%m-%d %H:%M:%S")}',
                                'source': 'screen_capture',
                                'type': 'image',
                                'timestamp': time.time()
                            })
                            logger.info(f"Added screenshot to ComoRAG context: {screenshot_path}")
                        
                    except Exception as screenshot_error:
                        logger.error(f"Failed to take screenshot: {screenshot_error}")
                        logger.warning("Continuing without screenshot")
                else:
                    logger.info("Screenshot disabled for vision model")
            else:
                logger.info(f"Text-only model: {model}")
            
            # Prepare messages (legacy code, not used in new API call)
            messages = []
            if system_message:
                messages.append({"role": "system", "content": system_message})
            if prompt:
                messages.append({"role": "user", "content": prompt})
            logger.info(f"Prepared messages (legacy): {messages}")

            # Collect minimal context data for ComoRAG integration (current context only)
            config = llm_config.get('comorag_config', {})
            context_data = self._collect_workflow_context(node, action_handlers, config=config)
            
            # Add only essential current workflow state context for ComoRAG
            if hasattr(client, 'add_context'):
                # Add minimal current workflow state (only essential info)
                workflow_state = {
                    'current_node': node.get('id', 'unknown'),
                    'node_type': 'llm',
                    'timestamp': time.time()
                }
                client.add_context({
                    'workflow_state': workflow_state,
                    'source': 'current_execution',
                    'type': 'state',
                    'timestamp': time.time()
                })
                logger.info(f"Added current workflow state to ComoRAG context")
            
            # Only add minimal context data (no historical data)
            if context_data and hasattr(client, 'add_context'):
                # Filter to only current execution context, skip historical data
                current_context_items = [item for item in context_data if item.get('type') == 'workflow_state']
                for context_item in current_context_items:
                    enhanced_context = dict(context_item)
                    if 'timestamp' not in enhanced_context:
                        enhanced_context['timestamp'] = time.time()
                    if 'source' not in enhanced_context:
                        enhanced_context['source'] = 'current_workflow'
                    client.add_context(enhanced_context)
                logger.info(f"Added {len(current_context_items)} current context items to ComoRAG client (historical data excluded)")
            
            # Current screenshot context is already handled above via OCR and vision analysis
            # No need to add additional screenshot context to avoid duplication
            logger.info("Current screenshot and OCR context handled via vision analysis and prompt enhancement")
            
            # Enhance prompt with OCR text if available
            enhanced_prompt = prompt
            if ocr_text and ocr_text.strip():
                enhanced_prompt = f"{prompt}\n\n[Current Screen Text (OCR):]\n{ocr_text}"
                logger.info(f"Enhanced prompt with OCR text ({len(ocr_text)} chars)")
            
            logger.info(f"Making LLM API request with {len(context_data)} context items...")
            logger.info(f"Request parameters: model={model}, prompt='{enhanced_prompt[:200]}...', system='{system_message}', temperature={temperature}, max_tokens={max_tokens}")
            
            try:
                # Use generate method with context for ComoRAG integration
                response = client.generate(
                    model=model,
                    prompt=enhanced_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    system=system_message if system_message else None,
                    images=images if images else None,
                    context=context_data if context_data else None
                )
                logger.info(f"Raw API response: {response}")
                logger.info(f"Response type: {type(response)}")
                
                # Clean up screenshot after API call
                if images:
                    for image_path in images:
                        cleanup_screenshot(image_path)
            except Exception as api_error:
                logger.error(f"API request failed: {api_error}")
                return None

            # Extract generated text from different possible response formats
            generated_text = None
            if response:
                logger.info("Processing API response...")
                if isinstance(response, dict):
                    logger.info(f"Response is dict with keys: {response.keys()}")
                    if 'response' in response:
                        generated_text = response['response']
                        logger.info(f"Extracted text from 'response' key: {generated_text}")
                    elif 'text' in response:
                        generated_text = response['text']
                        logger.info(f"Extracted text from 'text' key: {generated_text}")
                    elif 'message' in response and isinstance(response['message'], dict) and 'content' in response['message']:
                        generated_text = response['message']['content']
                        logger.info(f"Extracted text from 'message.content': {generated_text}")
                    else:
                        logger.warning(f"Could not find expected response format in: {response}")
                else:
                    logger.warning(f"Response is not a dict: {type(response)}")
            else:
                logger.error("No response received from API")

            if generated_text is not None:
                logger.info(f"=== LLM GENERATED TEXT ===")
                logger.info(f"Generated text (full): {generated_text}")
                logger.info(f"Generated text length: {len(generated_text)}")
                
                # Store the generated text in the variable system
                self.variables[output_variable] = generated_text
                logger.info(f"Stored LLM output in variable '{output_variable}': {generated_text}")
                logger.info(f"Current variables state: {self.variables}")

                # Check if LLM should write the text (default behavior for text models)
                should_write_text = write_text
                logger.info(f"Should write text: {should_write_text}")
                
                if should_write_text and generated_text.strip():
                    logger.info(f"=== ATTEMPTING TO WRITE TEXT ===")
                    # Create a type_string action to write the generated text
                    type_action = {
                        'type': 'type_string',
                        'text': generated_text,
                        'timestamp': time.time()
                    }
                    logger.info(f"Created type_action: {type_action}")
                    
                    # Check if action_handlers exists
                    if action_handlers:
                        logger.info(f"Action handlers available: {type(action_handlers)}")
                        try:
                            # Execute the typing action using action handlers
                            logger.info(f"Calling handle_type_string_action with text: '{generated_text[:50]}...'")
                            result = action_handlers.handle_type_string_action(0, type_action)
                            logger.info(f"handle_type_string_action result: {result}")
                            logger.info(f"=== TEXT WRITING COMPLETED ===")
                        except Exception as write_error:
                            logger.error(f"Error during text writing: {write_error}")
                            import traceback
                            logger.error(f"Write error traceback: {traceback.format_exc()}")
                    else:
                        logger.error("No action_handlers available for text writing")
                else:
                    if not should_write_text:
                        logger.info("Text writing disabled by configuration")
                    elif not generated_text.strip():
                        logger.warning("Generated text is empty or whitespace only")
            else:
                logger.error("Failed to get valid response from LLM - no generated text")
                return None
                
        except Exception as e:
            logger.error(f"Error executing LLM node: {e}")
            import traceback
            logger.error(f"LLM node error traceback: {traceback.format_exc()}")
            return None
        
        # Find next node in the workflow
        connections = node['connections']
        if 'output' in connections and connections['output']:
            # Take the first connection
            next_connection = connections['output'][0]
            return next_connection['node_id']
        else:
            logger.info("No output connections found for LLM node, ending workflow")
            return None
    
    def get_variable(self, var_name):
        """Get a variable value"""
        return self.variables.get(var_name)
    
    def set_variable(self, var_name, value):
        """Set a variable value"""
        self.variables[var_name] = value
    
    def get_all_variables(self):
        """Get all variables"""
        return self.variables.copy()
    
    def _initialize_context_collector(self):
        """Initialize the context collector for ComoRAG integration"""
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.append(project_root)
            
            from AI.context_collector import ContextCollector
            self.context_collector = ContextCollector(workspace_path=project_root)
            logger.info("Context collector initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize context collector: {e}")
            self.context_collector = None
    
    def _collect_workflow_context(self, node, action_handlers=None, config=None):
        """Collect context data for the current workflow state based on configuration
        
        Modified to limit context collection to only current execution context,
        avoiding loading all historical data from chains, screenshots, and sequences folders.
        """
        if not self.context_collector:
            return []
        
        # Default configuration - disabled historical data collection by default
        if config is None:
            config = {
                'collect_workflow_state': True,
                'collect_screenshots': False,  # Disabled: historical screenshots not needed
                'collect_automations': False,  # Disabled: historical automations not needed
                'max_context_items': 3,  # Reduced: only current context needed
                'screenshot_limit': 1,  # Only current screenshot if enabled
                'automation_limit': 0   # No historical automations
            }
        
        context_items = []
        max_items = config.get('max_context_items', 10)
        
        try:
            # Collect workflow state context if enabled
            if config.get('collect_workflow_state', True):
                workflow_data = {
                    "current_node": node.get('id', 'unknown'),
                    "node_type": node.get('type', 'unknown'),
                    "variables": self.variables.copy(),
                    "status": "executing",
                    "name": node.get('data', {}).get('name', 'unknown_workflow')
                }
                
                workflow_context = self.context_collector.collect_workflow_state_context(workflow_data)
                if workflow_context:
                    context_items.append(workflow_context)
            
            # Historical data collection disabled by default to avoid loading all context
            # Only collect if explicitly enabled in config (for backward compatibility)
            if config.get('collect_screenshots', False):
                logger.info("Historical screenshot collection is disabled by default for LLM nodes")
                logger.info("Current screenshot is captured separately and added via OCR and vision analysis")
            
            if config.get('collect_automations', False):
                logger.info("Historical automation data collection is disabled by default for LLM nodes")
                logger.info("LLM nodes now focus on current screen context and user query only")
                
            # Note: Current screenshot and OCR data are handled separately in execute_llm_node
            # This ensures LLM nodes only receive current context, not historical data
            
            # Limit total context items
            context_items = context_items[:max_items]
            
            logger.info(f"Collected {len(context_items)} context items for ComoRAG (historical data collection disabled)")
            return context_items
            
        except Exception as e:
            logger.error(f"Error collecting workflow context: {e}")
            return []