# graphui/nodes.py

import os
from NodeGraphQt import BaseNode
from .constants import (
    FALLBACK_COLOR,
    SCREENSHOT_COLOR,
    ACTION_COLOR,
    DARK_GREY,
    CONDITIONAL_COLOR,
    LLM_COLOR,
    TTS_COLOR,
    CHAIN_IMPORT_COLOR,
    FORM_FILLER_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    PORT_TRUE_COLOR,
    PORT_FALSE_COLOR,
)

# Get default API URL from environment variables (same as consult.py)
def get_default_api_url():
    """Get default API URL using same logic as consult.py"""
    tailscale_ip = os.getenv("TAILSCALE_SERVER_IP", "localhost")
    port = os.getenv("API_PORT", "8000")
    return f"http://{tailscale_ip}:{port}"

# Fallback: ensure a property registration method exists even if inline widget API differs
if not hasattr(BaseNode, 'create_property'):
    def _create_property_fallback(self, name, value):
        try:
            # Many NodeGraphQt versions allow creating properties via set_property
            self.set_property(name, value)
        except Exception:
            # Silently ignore to avoid breaking node creation
            pass
    BaseNode.create_property = _create_property_fallback

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
        # Title styling: bigger font, black text
        try:
            self.set_text_color(0, 0, 0)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
        
        # Port color mapping
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_true_rgb = tuple(int(PORT_TRUE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_false_rgb = tuple(int(PORT_FALSE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Add single input for conditional evaluation with label
        self.add_input('input', color=in_rgb, display_name=True)

        # Add true/false outputs with distinct colors and labels for clarity
        self.add_output('true', color=out_true_rgb, display_name=True)   # green
        self.add_output('false', color=out_false_rgb, display_name=True) # red
        
        # Register properties without rendering inline input widgets
        self.create_property('condition_type', 'presence')
        self.create_property('image_path', '')
        self.create_property('confidence', '0.8')
        self.create_property('threshold', '0.8')
        self.create_property('timeout', '5.0')
        self.create_property('wait_time', '5')
        self.create_property('max_loops', '10')
        self.create_property('max_attempts', '3')
        self.create_property('delay_between_attempts', '1.0')
        
        # OCR-specific fields (language and region removed - uses full screen with default English)
        self.create_property('target_text', '')
        self.create_property('ocr_text', '')
        self.create_property('case_sensitive', 'true')
        
        # Loop-specific fields
        self.create_property('loop_type', 'while_present')
        self.create_property('loop_position', 'pre')
        self.create_property('sequence_file', '')
        self.create_property('iteration_delay', '1.0')
        self.create_property('loop_condition', '')

        # Visual indicator for editable nodes (border accent)
        try:
            from .constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass
        
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
        if self.condition_type in ['presence_trigger', 'absence_trigger', 'wait_presence', 'wait_absence', 'ocr_trigger']:
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
        # Title styling: bigger font, white text for dark background
        try:
            self.set_text_color(255, 255, 255)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
        
        # Port color mapping
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Add labeled inputs and outputs for sequence
        # Input to trigger the sequence
        self.add_input('input', color=in_rgb, display_name=True)
        
        # Outputs for success and failure paths
        self.add_output('success', color=out_rgb, display_name=True)
        self.add_output('failed (opt)', color=tuple(int(PORT_FALSE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)), display_name=True)
        
        # Register properties without rendering inline input widgets
        self.create_property('sequence_file', '')
        self.create_property('loop_count', '1')
        self.create_property('extra_delay', '1.0')

        # Visual indicator for editable nodes (border accent)
        try:
            from .constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass
        
    def set_sequence_data(self, sequence_idx, sequence_config, action_fallbacks=None):
        """Set the sequence data for this node"""
        self.sequence_idx = sequence_idx
        self.sequence_config = sequence_config
        self.action_fallbacks = action_fallbacks or {}
        
        # Handle both string and dict sequence_config formats
        if isinstance(sequence_config, str):
            # Legacy format - sequence_config is just the file path
            seq_name = os.path.basename(sequence_config)
            sequence_file = os.path.basename(sequence_config)  # Store filename only
            loop_count = 1
            extra_delay = 1.0
        else:
            # New format - sequence_config is a dictionary
            sequence_file = sequence_config.get('sequence_file', '')
            # Ensure we store only the filename, not full path
            if sequence_file and (os.path.sep in sequence_file or '/' in sequence_file):
                sequence_file = os.path.basename(sequence_file)
            seq_name = sequence_file or 'unknown'
            loop_count = sequence_config.get('loop_count', 1)
            extra_delay = sequence_config.get('extra_delay', 1.0)
        
        # CRITICAL FIX: Use only the sequence filename to prevent automatic numbering
        # that breaks sequence file references during playback
        # The node name should be just the filename (e.g., "test.json") not "test.json 2"
        self.set_name(seq_name)
        
        # CRITICAL: Always store only the filename in sequence_file property
        # This ensures the player looks for the correct file regardless of GUI numbering
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
        # Title styling: bigger font, black text
        try:
            self.set_text_color(0, 0, 0)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
        
        # Port color mapping
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Add labeled inputs and outputs for actions
        # Input to trigger the action
        self.add_input('input', color=in_rgb, display_name=True)
        
        # Outputs for success and optional fallback
        self.add_output('success', color=out_rgb, display_name=True)
        self.add_output('fallback (opt)', color=tuple(int(PORT_FALSE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)), display_name=True)
        
        # Register properties without rendering inline input widgets
        self.create_property('action_type', '')
        self.create_property('details', '')
        
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
            'memory_size': 1000,
            'typing_batch_size': 20,  # Characters per batch for optimized typing
            'typing_batch_delay': 0.05,  # Delay between batches in seconds
            'input_source': 'none',
            'ocr_confidence': 0.5,
            'ocr_preprocessing': True,
            'use_async': True  # Enable asynchronous processing by default
        }
        
        # Set a distinct color for LLM nodes (orange)
        self.set_color(*[int(LLM_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        # Title styling: bigger font, black text
        try:
            self.set_text_color(0, 0, 0)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
        
        # Port color mapping
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Add labeled inputs - only what's actually needed
        # Main input (required) for prompt or chain input
        self.add_input('input', color=in_rgb, display_name=True)
        # Optional context input for additional information
        self.add_input('context (opt)', color=in_rgb, display_name=True)
        
        # Add single output (multiple connections allowed by default in NodeGraphQt)
        self.add_output('output', color=out_rgb, display_name=True)
        
        # Add text inputs for LLM configuration
        # Register properties without rendering inline input widgets
        self.create_property('model', 'llama3.2:latest')
        self.create_property('prompt', '')
        self.create_property('system_message', '')
        self.create_property('temperature', '0.7')
        self.create_property('max_tokens', '500')
        self.create_property('output_variable', 'llm_output')
        self.create_property('api_url', get_default_api_url())
        self.create_property('write_text', 'true')
        self.create_property('use_vision', 'false')
        self.create_property('screenshot_enabled', 'true')
        self.create_property('use_comorag', 'true')
        self.create_property('max_cycles', '5')
        self.create_property('memory_size', '1000')
        self.create_property('input_source', 'none')
        self.create_property('ocr_confidence', '0.5')
        self.create_property('ocr_preprocessing', 'true')
        self.create_property('typing_batch_size', '20')
        self.create_property('typing_batch_delay', '0.05')
        self.create_property('use_async', 'true')
        self.create_property('use_direct_rag', 'false')
        self.create_property('rag_embedding_model', 'nomic-embed-text')
        self.create_property('rag_chunk_size', '500')
        self.create_property('rag_overlap', '100')
        self.create_property('rag_top_k', '3')
        self.create_property('rag_include_raw_input', 'false')
        self.create_property('rag_max_chars', '1500')
        self.create_property('rag_documents', '[]')

        # Visual indicator for editable nodes (border accent)
        try:
            from .constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass
    
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
        self.set_property('typing_batch_size', str(config.get('typing_batch_size', 20)))
        self.set_property('typing_batch_delay', str(config.get('typing_batch_delay', 0.05)))
        self.set_property('input_source', config.get('input_source', 'none'))
        self.set_property('ocr_confidence', str(config.get('ocr_confidence', 0.5)))
        self.set_property('ocr_preprocessing', str(config.get('ocr_preprocessing', True)).lower())
        self.set_property('use_async', str(config.get('use_async', True)).lower())
        self.set_property('typing_batch_delay', str(config.get('typing_batch_delay', 0.05)))
        self.set_property('use_direct_rag', str(config.get('use_direct_rag', False)).lower())
        self.set_property('rag_embedding_model', config.get('rag_embedding_model', 'nomic-embed-text'))
        self.set_property('rag_chunk_size', str(config.get('rag_chunk_size', 500)))
        self.set_property('rag_overlap', str(config.get('rag_overlap', 100)))
        self.set_property('rag_top_k', str(config.get('rag_top_k', 3)))
        self.set_property('rag_include_raw_input', str(config.get('rag_include_raw_input', False)).lower())
        self.set_property('rag_max_chars', str(config.get('rag_max_chars', 1500)))
        try:
            import json as _json
            self.set_property('rag_documents', _json.dumps(config.get('rag_documents', [])))
        except Exception:
            self.set_property('rag_documents', '[]')
    
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
        ocr_preprocessing_str = self.get_property('ocr_preprocessing') or 'true'
        ocr_preprocessing = ocr_preprocessing_str.lower() in ('true', '1', 'yes', 'on')
        use_async_str = self.get_property('use_async') or 'true'
        use_async = use_async_str.lower() in ('true', '1', 'yes', 'on')
        use_direct_rag_str = self.get_property('use_direct_rag') or 'false'
        use_direct_rag = use_direct_rag_str.lower() in ('true', '1', 'yes', 'on')
        rag_include_raw_input_str = self.get_property('rag_include_raw_input') or 'false'
        rag_include_raw_input = rag_include_raw_input_str.lower() in ('true', '1', 'yes', 'on')
        docs_raw = self.get_property('rag_documents') or '[]'
        try:
            import json as _json
            rag_documents = _json.loads(docs_raw) if isinstance(docs_raw, str) else (docs_raw or [])
            if isinstance(rag_documents, str):
                rag_documents = [rag_documents]
        except Exception:
            rag_documents = []
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
            'memory_size': int(self.get_property('memory_size') or 1000),
            'typing_batch_size': int(self.get_property('typing_batch_size') or 20),
            'typing_batch_delay': float(self.get_property('typing_batch_delay') or 0.05),
            'input_source': self.get_property('input_source') or 'none',
            'ocr_confidence': float(self.get_property('ocr_confidence') or 0.5),
            'ocr_preprocessing': ocr_preprocessing,
            'use_async': use_async,
            'use_direct_rag': use_direct_rag,
            'rag_embedding_model': self.get_property('rag_embedding_model') or 'nomic-embed-text',
            'rag_chunk_size': int(self.get_property('rag_chunk_size') or 500),
            'rag_overlap': int(self.get_property('rag_overlap') or 100),
            'rag_top_k': int(self.get_property('rag_top_k') or 3),
            'rag_include_raw_input': rag_include_raw_input,
            'rag_max_chars': int(self.get_property('rag_max_chars') or 1500),
            'rag_documents': rag_documents
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
        # Title styling: bigger font, black text
        try:
            self.set_text_color(0, 0, 0)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
        
        # Port color mapping
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Add labeled inputs and outputs
        # Input for text to speak (can come from LLM or other source)
        self.add_input('text', color=in_rgb, display_name=True)
        # Optional input for voice settings override
        self.add_input('voice (opt)', color=in_rgb, display_name=True)
        
        # Single output for flow control
        self.add_output('output', color=out_rgb, display_name=True)
        
        # Add text inputs for TTS configuration
        # Register properties without rendering inline input widgets
        self.create_property('text', 'Hello, this is a text to speech message.')
        self.create_property('language', 'en')

        # Visual indicator for editable nodes (border accent)
        try:
            from .constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass
    
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


# DEPRECATED: Context Builder - class disabled
# class ContextBuilderNode(LLMNode):
#     """Node representing a context builder that uses LLM capabilities to build context without text output"""
#     
#     __identifier__ = 'context_builder'
#     NODE_NAME = 'Context Builder'
#     
#     def __init__(self):
#         super(ContextBuilderNode, self).__init__()
#         # Override default LLM config with context-specific defaults
#         self.llm_config.update({
#             'write_text': False,  # Don't write text to screen
#             'output_variable': 'context',  # Default variable name
#             'model': 'llama3.2:latest',
#             'system_message': 'You are a context builder assistant. Extract relevant information from the screen.',
#             'use_vision': True,  # Default to using vision
#             'screenshot_enabled': True,
#             'use_async': True  # Use async processing by default
#         })
#         
#         # Set a distinct color for Context Builder nodes (a variation of LLM color)
#         self.set_color(*[int(LLM_COLOR.strip('#')[i:i+2], 16) - 20 for i in (0, 2, 4)])
#         
#         # Update the node name
#         self.set_name("Context Builder")
#         
#     def set_context_builder_config(self, config):
#         """Set the Context Builder configuration for this node"""
#         # Ensure write_text is always False for context builders
#         config['write_text'] = False
#         super().set_llm_config(config)
#         
#         # Always update the name to reflect this is a context builder
#         self.set_name(f"Context Builder: {config.get('output_variable', 'context')}")
#         
#     def get_context_builder_config(self):
#         """Get the current Context Builder configuration"""
#         config = super().get_llm_config()
#         # Ensure write_text is always False
#         config['write_text'] = False
#         return config


class ChainImportNode(BaseNode):
    """Node representing an imported chain that can be composed with other chains to create larger automations"""
    
    __identifier__ = 'chain_import'
    NODE_NAME = 'Chain Import'
    
    def __init__(self):
        super(ChainImportNode, self).__init__()
        self.chain_file_path = ''
        self.chain_config = {}
        self.import_mode = 'full'  # 'full', 'sequences_only', 'nodes_only'
        self.prefix = ''  # Optional prefix for imported node names
        self.loop_count = 1  # Number of times to execute the imported chain
        self.extra_delay = 0  # Extra delay between loop iterations
        
        # Set a distinct color for chain import nodes (lime green)
        self.set_color(*[int(CHAIN_IMPORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        # Title styling: bigger font, black text
        try:
            self.set_text_color(0, 0, 0)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
        
        # Port color mapping
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Add labeled inputs and outputs for chain import
        # Input to trigger the imported chain
        self.add_input('input', color=in_rgb, display_name=True)
        # Optional parameters input
        self.add_input('params (opt)', color=in_rgb, display_name=True)
        
        # Single output (chain can have multiple exit points internally)
        self.add_output('output', color=out_rgb, display_name=True)
        
        # Add configuration inputs
        # Register properties without rendering inline input widgets
        self.create_property('chain_file', '')
        self.create_property('import_mode', 'full')
        self.create_property('prefix', '')
        self.create_property('loop_count', '1')
        self.create_property('extra_delay', '0')
        self.create_property('enabled', 'true')

        # Visual indicator for editable nodes (border accent)
        try:
            from .constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass
        
    def set_chain_import_data(self, chain_file_path, import_mode='full', prefix='', loop_count=1, extra_delay=0, enabled=True):
        """Set the chain import data for this node"""
        self.chain_file_path = chain_file_path
        self.import_mode = import_mode
        self.prefix = prefix
        self.loop_count = loop_count
        self.extra_delay = extra_delay
        
        # Load the chain configuration
        try:
            if os.path.exists(chain_file_path):
                import json
                with open(chain_file_path, 'r') as f:
                    self.chain_config = json.load(f)
                
                # Update node name to reflect the imported chain
                chain_name = os.path.basename(chain_file_path).replace('.json', '')
                if prefix:
                    self.set_name(f"Import: {prefix}_{chain_name}")
                else:
                    self.set_name(f"Import: {chain_name}")
                
                # Update properties
                self.set_property('chain_file', chain_file_path)
                self.set_property('import_mode', import_mode)
                self.set_property('prefix', prefix)
                self.set_property('loop_count', str(loop_count))
                self.set_property('extra_delay', str(extra_delay))
                self.set_property('enabled', str(enabled).lower())
                
                # Change color based on status
                if enabled:
                    self.set_color(*[int(CHAIN_IMPORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
                else:
                    # Darker color when disabled
                    self.set_color(*[int(CHAIN_IMPORT_COLOR.strip('#')[i:i+2], 16) // 2 for i in (0, 2, 4)])
                    
        except Exception as e:
            self.set_name(f"Import: ERROR - {os.path.basename(chain_file_path)}")
            # Red color for error state
            self.set_color(200, 50, 50)
    
    def get_chain_import_config(self):
        """Get the chain import configuration for this node"""
        return {
            'chain_file': self.get_property('chain_file'),
            'import_mode': self.get_property('import_mode'),
            'prefix': self.get_property('prefix'),
            'loop_count': int(self.get_property('loop_count') or '1'),
            'extra_delay': float(self.get_property('extra_delay') or '0'),
            'enabled': self.get_property('enabled').lower() == 'true'
        }
    
    def get_imported_chain_config(self):
        """Get the loaded chain configuration"""
        return self.chain_config
    
    def get_sequences_count(self):
        """Get the number of sequences in the imported chain"""
        return len(self.chain_config.get('sequences', []))
    
    def get_nodes_count(self):
        """Get the total number of nodes in the imported chain"""
        sequences = len(self.chain_config.get('sequences', []))
        conditionals = len(self.chain_config.get('conditional_nodes', []))
        llm_nodes = len(self.chain_config.get('llm_nodes', []))
        tts_nodes = len(self.chain_config.get('tts_nodes', []))
        return sequences + conditionals + llm_nodes + tts_nodes
    
    def is_valid_chain(self):
        """Check if the imported chain is valid"""
        return bool(self.chain_config and (
            self.chain_config.get('sequences') or 
            self.chain_config.get('conditional_nodes') or 
            self.chain_config.get('llm_nodes') or 
            self.chain_config.get('tts_nodes')
        ))


class FormFillerNode(BaseNode):
    """Node representing a Form Filler action using Computer Vision and LLM"""
    
    __identifier__ = 'form_filler'
    NODE_NAME = 'Form Filler'
    
    def __init__(self):
        super(FormFillerNode, self).__init__()
        
        self.set_color(*[int(FORM_FILLER_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
        # Title styling: bigger font, black text
        try:
            self.set_text_color(0, 0, 0)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
            
        # Port color mapping
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Add labeled inputs and outputs for form filler
        # Input to trigger form filling
        self.add_input('input', color=in_rgb, display_name=True)
        # Form data input (optional - can use LLM to generate)
        self.add_input('form_data (opt)', color=in_rgb, display_name=True)
        
        # Outputs for success and failure
        self.add_output('success', color=out_rgb, display_name=True)
        self.add_output('failed', color=tuple(int(PORT_FALSE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)), display_name=True)
            
        # Register properties
        self.create_property('instruction', 'Fill the form on the screen.')
        self.create_property('model', 'llama3.2:latest')
        self.create_property('confidence_threshold', '0.8')
        
        # Visual indicator for editable nodes (border accent)
        try:
            from .constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass

    def get_form_filler_config(self):
        """Get the configuration for this node"""
        return {
            'instruction': self.get_property('instruction'),
            'model': self.get_property('model'),
            'confidence_threshold': float(self.get_property('confidence_threshold') or 0.8)
        }
