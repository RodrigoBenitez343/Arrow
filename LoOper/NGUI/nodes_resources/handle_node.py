"""Node representing a handle action request point.

Supports two actions:
- click: locate an element and click it
- drag: locate a source element and a target location, then drag the
  source to the target (mouse down, move, mouse up)

When executed in agent mode, LocateAnything-3B grounds the goal
(or the source/target pair for drag) to screen coordinates, then the
action is executed on screen.

Web mode (click only): the goal is resolved against the chain's browser
page instead — the page's clickable elements are enumerated and the
embedded Laya engine picks the element the goal asks for, then the web
click ladder clicks it.  No screenshot, no LocateAnything grounding.

When executed manually, a dialog pops up asking the user what to do.
"""

from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    HANDLE_NODE_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)


class HandleNode(BaseNode):
    """Node representing a handle action request point"""
    
    __identifier__ = 'handle'
    NODE_NAME = 'Handle'
    
    def __init__(self):
        super(HandleNode, self).__init__()
        
        # Darken the color for text readability
        base_color = HANDLE_NODE_COLOR
        r, g, b = int(base_color.strip('#')[0:2], 16), int(base_color.strip('#')[2:4], 16), int(base_color.strip('#')[4:6], 16)
        dark_r, dark_g, dark_b = int(r * 0.25), int(g * 0.25), int(b * 0.25)
        self.set_color(dark_r, dark_g, dark_b)
        
        try:
            self.set_text_color(255, 255, 255)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass
        
        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        
        # Ports — base 'input'/'output' drive execution only (no data).
        # 'data' (in/out) is the DATA channel: the Handle node can receive data
        # from an Input node on its 'data' input, and returns what it did / to
        # which elements on its 'data' output.
        input_port = _add_multi_input(self, 'input', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        data_in_port = _add_multi_input(self, 'data', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        output_port = self.add_output('output', color=out_rgb, display_name=True, multi_output=True)
        data_out_port = self.add_output('data', color=out_rgb, display_name=True, multi_output=True)
        
        for p in [input_port, data_in_port, output_port, data_out_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass
        
        # Properties
        self.create_property('action_type', 'click')         # 'click' or 'drag'
        self.create_property('goal_description', '')          # click target / drag source object
        self.create_property('target_description', '')        # drag destination (drag only)
        self.create_property('agent_adaptive', False)        # Enable agent-driven grounding
        self.create_property('web_mode', False)              # Resolve against the browser DOM (click only)
    
    def get_handle_config(self):
        """Return the minimal configuration for this node."""
        return {
            'action_type': self.get_property('action_type') or 'click',
            'goal_description': self.get_property('goal_description') or '',
            'target_description': self.get_property('target_description') or '',
            'agent_adaptive': bool(self.get_property('agent_adaptive')),
            'web_mode': bool(self.get_property('web_mode')),
        }
