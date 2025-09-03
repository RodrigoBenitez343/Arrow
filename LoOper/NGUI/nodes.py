# graphui/nodes.py

import os
from NodeGraphQt import BaseNode
from .constants import FALLBACK_COLOR, SCREENSHOT_COLOR, ACTION_COLOR, DARK_GREY, CONDITIONAL_COLOR, LLM_COLOR, TTS_COLOR

# Get default API URL from environment variables (same as consult.py)
def get_default_api_url():
    """Get default API URL using same logic as consult.py"""
    tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{tailscale_ip}:{port}"

class ConditionalNode(BaseNode):
    """Node representing a conditional check that can control sequence execution flow with comprehensive condition types"""
    
    __identifier__ = 'conditional'
    NODE_NAME = 'Conditional'
    
    def __init__(self):
        super(ConditionalNode, self).__init__()
        self.condition_type = 'presence_trigger'
        self.condition_data = {
            'type': 'presence_trigger',
            'image_path': '',
            'confidence': 0.8,
            'timeout': 5.0
        }
        
        # Set a distinct color for conditional nodes (purple)
        self.set_color(*[int(CONDITIONAL_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
        # Add single input for conditional evaluation
        self.add_input('input')
        
        # Add only true/false outputs for conditional branching
        self.add_output('true')  # Output for when condition is true
        self.add_output('false')  # Output for when condition is false
        
        self.add_text_input('condition_type', 'Condition Type', 'presence')
        self.add_text_input('image_path', 'Image Path', '')
        self.add_text_input('confidence', 'Confidence', '0.8')
        self.add_text_input('threshold', 'Threshold', '0.8')
        self.add_text_input('timeout', 'Timeout (s)', '5.0')
        self.add_text_input('wait_time', 'Wait Time', '5')
        self.add_text_input('max_loops', 'Max Loops', '10')
        self.add_text_input('max_attempts', 'Max Attempts', '3')
        self.add_text_input('delay_between_attempts', 'Delay Between Attempts', '1.0')
        
        # OCR-specific fields (language and region removed - uses full screen with default English)
        self.add_text_input('target_text', 'Target Text', '')
        self.add_text_input('ocr_text', 'OCR Text', '')
        self.add_text_input('case_sensitive', 'Case Sensitive', 'true')
        
        # Loop-specific fields
        self.add_text_input('loop_type', 'Loop Type', 'while_present')
        self.add_text_input('loop_position', 'Loop Position', 'pre')
        self.add_text_input('sequence_file', 'Sequence File', '')
        self.add_text_input('iteration_delay', 'Iteration Delay', '1.0')
        self.add_text_input('loop_condition', 'Loop Condition', '')
        
    def set_condition_data(self, condition_type, condition_data):
        """Set the condition data for this node"""
        self.condition_type = condition_type
        self.condition_data = condition_data
        
        self.set_name(f"Condition: {condition_type}")
        
        # Update the text inputs to reflect the new data
        self.set_property('condition_type', condition_type)
        self.set_property('image_path', condition_data.get('image_path', ''))
        self.set_property('confidence', str(condition_data.get('confidence', 0.8)))
        self.set_property('timeout', str(condition_data.get('timeout', 5.0)))
        # Region removed - OCR now uses full screen
        self.set_property('max_attempts', str(condition_data.get('max_attempts', 3)))
        self.set_property('delay_between_attempts', str(condition_data.get('delay_between_attempts', 1.0)))
        
        # OCR-specific properties
        self.set_property('target_text', condition_data.get('target_text', ''))
        self.set_property('case_sensitive', str(condition_data.get('case_sensitive', True)).lower())
        self.set_property('language', condition_data.get('language', 'eng'))
        
        # Update additional properties for loop conditions
        if condition_type in ['while_present', 'while_absent', 'until_present', 'until_absent']:
            self.set_property('max_attempts', str(condition_data.get('max_iterations', 10)))
            self.set_property('delay_between_attempts', str(condition_data.get('iteration_delay', 1.0)))
    
    def get_condition_config(self):
        """Get the condition configuration for this node"""
        config = {
            'type': self.condition_type,
            'image_path': self.condition_data.get('image_path', ''),
            'confidence': self.condition_data.get('confidence', 0.8)
        }
        
        # Add timeout for relevant condition types
        if self.condition_type in ['absence_trigger', 'wait_presence', 'wait_absence']:
            config['timeout'] = self.condition_data.get('timeout', 5.0)
        
        # Add OCR-specific parameters for OCR trigger
        if self.condition_type == 'ocr_trigger':
            config.update({
                'target_text': self.condition_data.get('target_text', ''),
                'case_sensitive': self.condition_data.get('case_sensitive', True)
            })
        
        # Add loop parameters for loop condition types
        if self.condition_type in ['while_present', 'while_absent', 'until_present', 'until_absent']:
            config.update({
                'sequence_file': self.condition_data.get('sequence_file', ''),
                'max_iterations': self.condition_data.get('max_iterations', 10),
                'iteration_delay': self.condition_data.get('iteration_delay', 1.0)
            })
        
        return config

class SequenceNode(BaseNode):
    """Node representing a sequence in the chain"""
    
    __identifier__ = 'sequence'
    NODE_NAME = 'Sequence'
    
    def __init__(self):
        super(SequenceNode, self).__init__()
        self.sequence_idx = 0
        self.sequence_config = {}
        self.action_fallbacks = {}
        
        self.set_color(*[int(DARK_GREY.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
        # Add 5 inputs and 5 outputs for flexible connections
        for i in range(5):
            self.add_input(f'input_{i+1}')
            self.add_output(f'output_{i+1}')
        
        self.add_text_input('sequence_file', 'Sequence File', '')
        self.add_text_input('loop_count', 'Loop Count', '1')
        self.add_text_input('extra_delay', 'Extra Delay', '1.0')
        
    def set_sequence_data(self, sequence_idx, sequence_config, action_fallbacks=None):
        """Set the sequence data for this node"""
        self.sequence_idx = sequence_idx
        self.sequence_config = sequence_config
        self.action_fallbacks = action_fallbacks or {}
        
        # Handle both string and dict sequence_config formats
        if isinstance(sequence_config, str):
            # Legacy format - sequence_config is just the file path
            seq_name = os.path.basename(sequence_config)
            sequence_file = sequence_config
            loop_count = 1
            extra_delay = 1.0
        else:
            # New format - sequence_config is a dictionary
            seq_name = os.path.basename(sequence_config.get('sequence_file', ''))
            sequence_file = sequence_config.get('sequence_file', '')
            loop_count = sequence_config.get('loop_count', 1)
            extra_delay = sequence_config.get('extra_delay', 1.0)
        
        # CRITICAL FIX: Use only the sequence filename to prevent automatic numbering
        # that breaks sequence file references during playback
        # The node name should be just the filename (e.g., "test.json") not "test.json 2"
        self.set_name(seq_name)
        
        self.set_property('sequence_file', sequence_file)
        self.set_property('loop_count', str(loop_count))
        self.set_property('extra_delay', str(extra_delay))
        
        has_fallbacks = any(key.startswith(f"{sequence_idx}_") for key in self.action_fallbacks.keys())
        if has_fallbacks:
            self.set_color(*[int(FALLBACK_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        else:
            self.set_color(*[int(DARK_GREY.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
    
    def get_sequence_config(self):
        """Get the current sequence configuration from the node"""
        return {
            'sequence_file': self.get_property('sequence_file'),
            'loop_count': int(self.get_property('loop_count') or 1),
            'extra_delay': float(self.get_property('extra_delay') or 1.0)
        }

class ActionNode(BaseNode):
    """Node representing an individual action within a sequence"""
    
    __identifier__ = 'action'
    NODE_NAME = 'Action'
    
    def __init__(self):
        super(ActionNode, self).__init__()
        self.sequence_idx = 0
        self.action_idx = 0
        self.action_data = {}
        self.has_fallback = False
        
        self.set_color(*[int(ACTION_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
        # Add 5 inputs and 5 outputs for flexible connections
        for i in range(5):
            self.add_input(f'input_{i+1}')
            self.add_output(f'output_{i+1}')
        
        self.add_text_input('action_type', 'Type', '')
        self.add_text_input('details', 'Details', '')
        
    def set_action_data(self, sequence_idx, action_idx, action_data, has_fallback=False):
        """Set the action data for this node"""
        self.sequence_idx = sequence_idx
        self.action_idx = action_idx
        self.action_data = action_data
        self.has_fallback = has_fallback
        
        action_type = action_data.get('type', 'unknown')
        self.set_name(f"Action {action_idx + 1}: {action_type.upper()}")
        
        self.set_property('action_type', action_type)
        self.set_property('details', self.get_action_details(action_data))
        
        if has_fallback:
            self.set_color(0, 100, 0)  # Green for actions with fallbacks
        elif action_data.get('screenshot'):
            self.set_color(*[int(SCREENSHOT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        else:
            self.set_color(*[int(ACTION_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
    
    def get_action_details(self, action_data):
        """Get formatted details for the action"""
        action_type = action_data.get('type', 'unknown')
        if action_type == 'click':
            coords = action_data.get('coordinates')
            if isinstance(coords, dict):
                x = coords.get('x', 0)
                y = coords.get('y', 0)
            elif isinstance(coords, (list, tuple)) and len(coords) >= 2:
                x, y = coords[0], coords[1]
            else:
                x = action_data.get('x', 0)
                y = action_data.get('y', 0)
            return f"({x}, {y})"
        elif action_type == 'key':
            return action_data.get('key', 'unknown')
        elif action_type == 'scroll':
            dx = action_data.get('dx', 0)
            dy = action_data.get('dy', 0)
            direction = action_data.get('direction', '')
            amount = action_data.get('amount', '')
            if dx or dy:
                return f"dx:{dx}, dy:{dy}"
            elif direction and amount:
                return f"{direction} {amount}"
            else:
                return "scroll"
        elif action_type == 'delay':
            return f"{action_data.get('duration', 0)}s"
        return "unknown"

class LLMNode(BaseNode):
    """Node representing an LLM (Large Language Model) that can generate text for automation workflows"""
    
    __identifier__ = 'llm'
    NODE_NAME = 'LLM'
    
    def __init__(self):
        super(LLMNode, self).__init__()
        self.llm_config = {
            'model': 'llama3.2:latest',
            'prompt': '',
            'system_message': '',
            'temperature': 0.7,
            'max_tokens': 500,
            'output_variable': 'llm_output',
            'api_url': get_default_api_url(),
            'write_text': True,
            'use_vision': False,
            'screenshot_enabled': True,
            'use_comorag': True,
            'max_cycles': 5,
            'memory_size': 1000
        }
        
        # Set a distinct color for LLM nodes (orange)
        self.set_color(*[int(LLM_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
        # Add 5 inputs and 5 outputs for flexible connections
        for i in range(5):
            self.add_input(f'input_{i+1}')
            self.add_output(f'output_{i+1}')
        
        # Add text inputs for LLM configuration
        self.add_text_input('model', 'Model', 'llama3.2:latest')
        self.add_text_input('prompt', 'Prompt', '')
        self.add_text_input('system_message', 'System Message', '')
        self.add_text_input('temperature', 'Temperature', '0.7')
        self.add_text_input('max_tokens', 'Max Tokens', '500')
        self.add_text_input('output_variable', 'Output Variable', 'llm_output')
        self.add_text_input('api_url', 'API URL', get_default_api_url())
        self.add_text_input('write_text', 'Write Text', 'true')
        self.add_text_input('use_vision', 'Use Vision', 'false')
        self.add_text_input('screenshot_enabled', 'Screenshot Enabled', 'true')
        self.add_text_input('use_comorag', 'Use ComoRAG', 'true')
        self.add_text_input('max_cycles', 'Max Cycles', '5')
        self.add_text_input('memory_size', 'Memory Size', '1000')
        
    def set_llm_config(self, config):
        """Set the LLM configuration for this node"""
        self.llm_config.update(config)
        
        # Update the node name to reflect the model
        model_name = config.get('model', 'Unknown Model')
        self.set_name(f"LLM: {model_name}")
        
        # Update the text inputs to reflect the new configuration
        self.set_property('model', config.get('model', 'llama3.2:latest'))
        self.set_property('prompt', config.get('prompt', ''))
        self.set_property('system_message', config.get('system_message', ''))
        self.set_property('temperature', str(config.get('temperature', 0.7)))
        self.set_property('max_tokens', str(config.get('max_tokens', 500)))
        self.set_property('output_variable', config.get('output_variable', 'llm_output'))
        self.set_property('api_url', config.get('api_url', get_default_api_url()))
        self.set_property('write_text', str(config.get('write_text', True)).lower())
        self.set_property('use_vision', str(config.get('use_vision', False)).lower())
        self.set_property('screenshot_enabled', str(config.get('screenshot_enabled', True)).lower())
        self.set_property('use_comorag', str(config.get('use_comorag', True)).lower())
        self.set_property('max_cycles', str(config.get('max_cycles', 5)))
        self.set_property('memory_size', str(config.get('memory_size', 1000)))
    
    def get_llm_config(self):
        """Get the current LLM configuration from the node"""
        write_text_str = self.get_property('write_text') or 'true'
        write_text = write_text_str.lower() in ('true', '1', 'yes', 'on')
        use_vision_str = self.get_property('use_vision') or 'false'
        use_vision = use_vision_str.lower() in ('true', '1', 'yes', 'on')
        screenshot_enabled_str = self.get_property('screenshot_enabled') or 'true'
        screenshot_enabled = screenshot_enabled_str.lower() in ('true', '1', 'yes', 'on')
        use_comorag_str = self.get_property('use_comorag') or 'true'
        use_comorag = use_comorag_str.lower() in ('true', '1', 'yes', 'on')
        return {
            'model': self.get_property('model'),
            'prompt': self.get_property('prompt'),
            'system_message': self.get_property('system_message'),
            'temperature': float(self.get_property('temperature') or 0.7),
            'max_tokens': int(self.get_property('max_tokens') or 500),
            'output_variable': self.get_property('output_variable'),
            'api_url': self.get_property('api_url'),
            'write_text': write_text,
            'use_vision': use_vision,
            'screenshot_enabled': screenshot_enabled,
            'use_comorag': use_comorag,
            'max_cycles': int(self.get_property('max_cycles') or 5),
            'memory_size': int(self.get_property('memory_size') or 1000)
        }
    
    def is_vision_model(self, model_name):
        """Check if the specified model supports vision capabilities"""
        import sys
        import os
        
        # Add the AI directory to the path to import the OllamaClient
        ai_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'AI')
        if ai_dir not in sys.path:
            sys.path.insert(0, ai_dir)
        
        try:
            from consult import OllamaClient
            client = OllamaClient()
            return client.is_vision_model(model_name)
        except Exception:
            # Fallback: check common vision model names
            vision_models = [
                'llava', 'bakllava', 'llava-llama3', 'llava-phi3', 'llava-vicuna',
                'moondream', 'cogvlm', 'minicpm-v', 'qwen-vl', 'internvl'
            ]
            model_lower = model_name.lower()
            return any(vision_model in model_lower for vision_model in vision_models)
    
    def take_screenshot(self):
        """Take a screenshot for vision analysis"""
        import pyautogui
        import os
        import time
        
        # Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
        pyautogui.FAILSAFE = False
        
        try:
            # Create screenshots directory if it doesn't exist
            screenshots_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'screenshots')
            os.makedirs(screenshots_dir, exist_ok=True)
            
            # Take screenshot
            screenshot = pyautogui.screenshot()
            
            # Save with timestamp
            timestamp = int(time.time())
            screenshot_path = os.path.join(screenshots_dir, f'llm_vision_{timestamp}.png')
            screenshot.save(screenshot_path)
            
            # Register for cleanup after use
            try:
                import sys
                player_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'player')
                if player_dir not in sys.path:
                    sys.path.append(player_dir)
                from screenshot_cleanup import register_temporal_screenshot
                register_temporal_screenshot(screenshot_path)
            except ImportError:
                # Fallback if cleanup utility is not available
                pass
            
            return screenshot_path
        except Exception as e:
            print(f"Error taking screenshot: {e}")
            return None
    
    def execute_llm(self, context_variables=None):
        """Execute the LLM request and return the generated text"""
        import sys
        import os
        
        # Add the AI directory to the path to import the OllamaClient
        ai_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'AI')
        if ai_dir not in sys.path:
            sys.path.insert(0, ai_dir)
        
        try:
            from consult import OllamaClient
            
            config = self.get_llm_config()
            client = OllamaClient(config['api_url'])
            
            # Replace variables in prompt if context is provided
            prompt = config['prompt']
            if context_variables:
                for var_name, var_value in context_variables.items():
                    prompt = prompt.replace(f"{{{var_name}}}", str(var_value))
            
            # Check if this is a vision model and handle screenshots
            images = None
            if config.get('use_vision', False) or self.is_vision_model(config['model']):
                if config.get('screenshot_enabled', True):
                    screenshot_path = self.take_screenshot()
                    if screenshot_path:
                        images = [screenshot_path]
                        print(f"Vision model detected, screenshot taken: {screenshot_path}")
                    else:
                        print("Warning: Vision model detected but screenshot failed")
            
            # Make the API call
            response = client.chat(
                model=config['model'],
                prompt=prompt,
                system=config['system_message'] if config['system_message'] else None,
                temperature=config['temperature'],
                max_tokens=config['max_tokens'],
                images=images
            )
            
            if 'error' in response:
                return {'error': response['error']}
            else:
                generated_text = response.get('response', '')
                return {
                    'success': True,
                    'text': generated_text,
                    'variable_name': config['output_variable'],
                    'used_vision': images is not None,
                    'screenshot_path': images[0] if images else None
                }
                
                # Clean up screenshot after use
                if images:
                    try:
                        import sys
                        player_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'player')
                        if player_dir not in sys.path:
                            sys.path.append(player_dir)
                        from screenshot_cleanup import cleanup_screenshot
                        for image_path in images:
                            cleanup_screenshot(image_path)
                    except ImportError:
                        # Fallback cleanup if utility is not available
                        for image_path in images:
                            try:
                                if os.path.exists(image_path):
                                    os.remove(image_path)
                            except Exception:
                                pass
                
        except Exception as e:
            return {'error': str(e)}


class TTSNode(BaseNode):
    """Node representing a Text-to-Speech (TTS) action that synthesizes speech from text"""
    
    __identifier__ = 'tts'
    NODE_NAME = 'TTS'
    
    def __init__(self):
        super(TTSNode, self).__init__()
        self.tts_config = {
            'text': 'Hello, this is a text to speech message.',
            'language': 'en'  # Language code
        }
        
        # Set a distinct color for TTS nodes (dark turquoise)
        self.set_color(*[int(TTS_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
        # Add 5 inputs and 5 outputs for flexible connections
        for i in range(5):
            self.add_input(f'input_{i+1}')
            self.add_output(f'output_{i+1}')
        
        # Add text inputs for TTS configuration
        self.add_text_input('text', 'Text to Speak', 'Hello, this is a text to speech message.')
        self.add_text_input('language', 'Language', 'en')
    
    def set_tts_config(self, config):
        """Set the TTS configuration for this node"""
        self.tts_config.update(config)
        
        # Update the node name to reflect the text (first 30 chars)
        text_preview = config.get('text', 'TTS')[:30]
        if len(config.get('text', '')) > 30:
            text_preview += '...'
        self.set_name(f"TTS: {text_preview}")
        
        # Update the text inputs to reflect the new configuration
        self.set_property('text', config.get('text', 'Hello, this is a text to speech message.'))
        self.set_property('language', config.get('language', 'en'))
    
    def get_tts_config(self):
        """Get the current TTS configuration"""
        return {
            'text': self.get_property('text') or self.tts_config['text'],
            'language': self.get_property('language') or self.tts_config['language'],
            'rate': self.get_property('rate') or 200,
            'volume': self.get_property('volume') or 0.9,
            'voice_id': self.get_property('voice_id') or 0,
            'save_to_file': self.get_property('save_to_file') or False,
            'output_file': self.get_property('output_file') or '',
            'wait_for_completion': self.get_property('wait_for_completion') or True
        }
    
    def execute_tts(self, context_variables=None):
        """Execute the text-to-speech synthesis"""
        try:
            import pyttsx3
            
            config = self.get_tts_config()
            
            # Replace context variables in text if provided
            text_to_speak = config['text']
            if context_variables:
                for var_name, var_value in context_variables.items():
                    text_to_speak = text_to_speak.replace(f'{{{var_name}}}', str(var_value))
            
            # Initialize TTS engine
            engine = pyttsx3.init()
            
            # Configure TTS settings
            engine.setProperty('rate', config['rate'])
            engine.setProperty('volume', config['volume'])
            
            # Set voice if specified
            voices = engine.getProperty('voices')
            if voices and 0 <= config['voice_id'] < len(voices):
                engine.setProperty('voice', voices[config['voice_id']].id)
            
            # Save to file if requested
            if config['save_to_file'] and config['output_file']:
                engine.save_to_file(text_to_speak, config['output_file'])
                if config['wait_for_completion']:
                    engine.runAndWait()
                return {
                    'success': True,
                    'text': text_to_speak,
                    'saved_to_file': config['output_file'],
                    'message': f'TTS saved to file: {config["output_file"]}'
                }
            else:
                # Speak the text
                engine.say(text_to_speak)
                if config['wait_for_completion']:
                    engine.runAndWait()
                return {
                    'success': True,
                    'text': text_to_speak,
                    'message': f'TTS spoke: "{text_to_speak[:50]}..."' if len(text_to_speak) > 50 else f'TTS spoke: "{text_to_speak}"'
                }
                
        except ImportError:
            return {
                'error': 'pyttsx3 library not installed. Please install it using: pip install pyttsx3'
            }
        except Exception as e:
            return {
                'error': f'TTS execution failed: {str(e)}'
            }