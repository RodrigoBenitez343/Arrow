from NodeGraphQt import BaseNode
from ..constants import (
    FORM_FILLER_COLOR,
    DARK_GREY,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR
)

class FormFillerNode(BaseNode):
    """Node representing a Form Filler action using Computer Vision and LLM"""
    
    __identifier__ = 'form_filler'
    NODE_NAME = 'Form Filler'
    
    def __init__(self):
        super(FormFillerNode, self).__init__()
        self.set_name('Form Filler (Deprecated)')
        
        # Use a darkened version of the special color for better text readability
        # Keep the color identity but make it dark enough for white text
        base_color = FORM_FILLER_COLOR
        r, g, b = int(base_color.strip('#')[0:2], 16), int(base_color.strip('#')[2:4], 16), int(base_color.strip('#')[4:6], 16)
        # Darken to 25% brightness for good contrast with white text
        dark_r, dark_g, dark_b = int(r * 0.25), int(g * 0.25), int(b * 0.25)
        self.set_color(dark_r, dark_g, dark_b)
        
        # Title styling - white text for dark background
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

        self.add_input('input', color=in_rgb, display_name=False)
        self.add_output('output', color=out_rgb, display_name=False)
            
        # Register properties
        self.create_property('instruction', 'Fill the form on the screen.')
        self.create_property('model', 'llama3.2:latest')
        self.create_property('confidence_threshold', '0.8')

    def get_form_filler_config(self):
        """Get the configuration for this node"""
        return {
            'instruction': self.get_property('instruction'),
            'model': self.get_property('model'),
            'confidence_threshold': float(self.get_property('confidence_threshold') or 0.8)
        }
