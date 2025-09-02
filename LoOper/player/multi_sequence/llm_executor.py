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
                client = OllamaClient(base_url=api_url)
                logger.info("OllamaClient created successfully")
            except Exception as client_error:
                logger.error(f"Failed to create OllamaClient: {client_error}")
                return None
            
            # Check if this is a vision model and handle screenshots
            images = None
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

            logger.info(f"Making LLM API request...")
            logger.info(f"Request parameters: model={model}, prompt='{prompt}', system='{system_message}', temperature={temperature}, max_tokens={max_tokens}")
            
            try:
                response = client.chat(
                    model=model,
                    prompt=prompt,
                    system=system_message if system_message else None,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    images=images
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