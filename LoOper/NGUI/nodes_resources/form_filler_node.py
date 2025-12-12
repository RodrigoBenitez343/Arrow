from NodeGraphQt import BaseNode
from ..constants import (
    FORM_FILLER_COLOR,
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

        self.add_input('input', color=in_rgb, display_name=False)
        self.add_output('output', color=out_rgb, display_name=False)
            
        # Register properties
        self.create_property('instruction', 'Fill the form on the screen.')
        self.create_property('model', 'llama3.2:latest')
        self.create_property('confidence_threshold', '0.8')
        
        # Visual indicator for editable nodes (border accent)
        try:
            from ..constants import BLOCK_HOVER
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
