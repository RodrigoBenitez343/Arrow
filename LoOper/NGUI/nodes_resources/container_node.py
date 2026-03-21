import os
from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    CONTAINER_NODE_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)

class ContainerNode(BaseNode):
    """Node representing a Container Execution unit for headless visual tasks."""
    
    __identifier__ = 'container'
    NODE_NAME = 'Container'
    
    def __init__(self):
        super(ContainerNode, self).__init__()
        
        # Set a distinct color for Container nodes
        self.set_color(*[int(CONTAINER_NODE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
        # Title styling
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

        # Inputs
        # Main execution trigger / input data
        in_port = _add_multi_input(self, 'input', in_rgb, False, data_type=UNIVERSAL_PORT_TYPE)
        
        # Outputs
        out_port = self.add_output('output', color=out_rgb, display_name=False, multi_output=True)
        
        # Configure multi-connection
        for p in [in_port, out_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass
        
        # Register properties
        self.create_property('iso_path', '')
        self.create_property('memory_mb', '1024')
        self.create_property('cpu_cores', '1')
        self.create_property('hide_window', 'true')
        self.create_property('execute_on_input', 'true')
        self.create_property('output_variable', 'container_result')
        self.create_property('timeout', '300')
        
    def get_container_config(self):
        """Get the configuration for this node"""
        return {
            'iso_path': self.get_property('iso_path'),
            'memory_mb': int(self.get_property('memory_mb') or 1024),
            'cpu_cores': int(self.get_property('cpu_cores') or 1),
            'hide_window': self.get_property('hide_window') == 'true',
            'execute_on_input': self.get_property('execute_on_input') == 'true',
            'output_variable': self.get_property('output_variable'),
            'timeout': int(self.get_property('timeout') or 300)
        }
