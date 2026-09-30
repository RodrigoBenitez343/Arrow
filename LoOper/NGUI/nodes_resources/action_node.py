from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    ACTION_COLOR,
    SCREENSHOT_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR
)

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

        in_port = _add_multi_input(self, 'input', in_rgb, True)
        out_port = self.add_output('output', color=out_rgb, display_name=True)
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
