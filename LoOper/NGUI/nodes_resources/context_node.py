from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    CONTEXT_NODE_COLOR,
    DARK_GREY,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)


class ContextNode(BaseNode):
    """Node representing a Context accumulator that stores and manages data across workflow execution."""
    
    __identifier__ = 'context'
    NODE_NAME = 'Context'
    
    def __init__(self):
        super(ContextNode, self).__init__()
        
        # Use a darkened version of the special color for better text readability
        # Keep the color identity but make it dark enough for white text
        base_color = CONTEXT_NODE_COLOR
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
        
        # Inputs
        # Main input port - receives data from LLM/Code/Conditional outputs
        input_port = _add_multi_input(self, 'input', in_rgb, False, data_type=UNIVERSAL_PORT_TYPE)
        # Query port - receives structured query requests
        query_port = _add_multi_input(self, 'query', in_rgb, False, data_type=UNIVERSAL_PORT_TYPE)
        
        # Outputs
        # Context output - outputs accumulated context
        context_port = self.add_output('context', color=out_rgb, display_name=False, multi_output=True)
        
        # Configure multi-connection
        for p in [input_port, query_port, context_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass
        
        # Register properties
        self.create_property('scope', 'chain')
        self.create_property('default_merge_policy', 'append')
        self.create_property('keys', '[]')
        self.create_property('key_policies', '{}')
        self.create_property('max_entries_per_key', '100')
        self.create_property('auto_capture_responses', 'true')
        self.create_property('output_format', 'structured')
        self.create_property('context_node_label', '')
        self.create_property('retrieval_top_k', 3)
        self.create_property('use_semantic_retrieval', False)
        self.create_property('shared_context_chain_file', '')  # NEW: Path to chain file containing shared context
        self.create_property('shared_context_node_id', '')  # NEW: Specific node ID within the shared chain
    
    def get_context_config(self):
        """Get the configuration for this node"""
        auto_capture_str = self.get_property('auto_capture_responses') or 'true'
        auto_capture_responses = auto_capture_str.lower() in ('true', '1', 'yes', 'on')
        
        return {
            'scope': self.get_property('scope') or 'chain',
            'default_merge_policy': self.get_property('default_merge_policy') or 'append',
            'keys': self.get_property('keys') or '[]',
            'key_policies': self.get_property('key_policies') or '{}',
            'max_entries_per_key': int(self.get_property('max_entries_per_key') or 100),
            'auto_capture_responses': auto_capture_responses,
            'output_format': self.get_property('output_format') or 'structured',
            'context_node_label': self.get_property('context_node_label') or '',
            'retrieval_top_k': int(self.get_property('retrieval_top_k') or 3),
            'use_semantic_retrieval': str(self.get_property('use_semantic_retrieval') or '').lower() in ('true', '1'),
            'shared_context_chain_file': self.get_property('shared_context_chain_file') or '',  # NEW
            'shared_context_node_id': self.get_property('shared_context_node_id') or '',  # NEW
        }
