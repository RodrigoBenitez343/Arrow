from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    CONDITIONAL_COLOR,
    INPUT_PORT_COLOR,
    PORT_TRUE_COLOR,
    PORT_FALSE_COLOR
)

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

        # Add single input for conditional evaluation (blue)
        in_port = _add_multi_input(self, 'input', in_rgb, False)

        true_port = self.add_output('true', color=out_true_rgb, display_name=False)
        false_port = self.add_output('false', color=out_false_rgb, display_name=False)
        try:
            in_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(in_port, '_multi_connection', True)
            except Exception:
                pass
        for p in (true_port, false_port):
            try:
                p.set_multi_connection(True)
            except Exception:
                try:
                    setattr(p, '_multi_connection', True)
                except Exception:
                    pass
        
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
        self.create_property('scroll_direction', '1')  # 1 = up, -1 = down
        self.create_property('target_x', '')
        self.create_property('target_y', '')
        self.create_property('target_w', '')
        self.create_property('target_h', '')
        self.create_property('position_tolerance', '6')
        self.create_property('code', 'result = True')
        
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
            from ..constants import BLOCK_HOVER
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
        self.set_property('scroll_direction', str(condition_data.get('scroll_direction', 1)))  # 1 = up, -1 = down
        
        # OCR-specific properties
        self.set_property('target_text', condition_data.get('target_text', ''))
        self.set_property('case_sensitive', str(condition_data.get('case_sensitive', True)).lower())
        self.set_property('language', condition_data.get('language', 'eng'))
        
        # Update additional properties for loop conditions
        if condition_type in ['while_present', 'while_absent', 'until_present', 'until_absent']:
            self.set_property('max_attempts', str(condition_data.get('max_iterations', 10)))
            self.set_property('delay_between_attempts', str(condition_data.get('iteration_delay', 1.0)))
        if condition_type == 'code':
            self.set_property('code', condition_data.get('code', self.get_property('code') or 'result = True'))
    
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

        if self.condition_type == 'code':
            config.update({
                'code': self.get_property('code') or self.condition_data.get('code', '')
            })
        
        return config
