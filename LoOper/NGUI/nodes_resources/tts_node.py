from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    TTS_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR
)

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

        in_port = _add_multi_input(self, 'input', in_rgb, False)
        out_port = self.add_output('output', color=out_rgb, display_name=False)
        try:
            in_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(in_port, '_multi_connection', True)
            except Exception:
                pass
        try:
            out_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(out_port, '_multi_connection', True)
            except Exception:
                pass
        
        # Add text inputs for TTS configuration
        # Register properties without rendering inline input widgets
        self.create_property('text', 'Hello, this is a text to speech message.')
        self.create_property('language', 'en')

        # Visual indicator for editable nodes (border accent)
        try:
            from ..constants import BLOCK_HOVER
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
