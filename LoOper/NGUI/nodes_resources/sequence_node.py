import os
from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    DARK_GREY,
    FALLBACK_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)

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

        in_port = _add_multi_input(self, 'input', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        out_port = self.add_output('output', color=out_rgb, display_name=True, multi_output=True)
        # out_port.model.type_ = UNIVERSAL_PORT_TYPE
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
        self.create_property('sequence_file', '')
        self.create_property('loop_count', '1')
        self.create_property('extra_delay', '1.0')
        self.create_property('use_app_opened', 'true')
        # Human-readable: what this sequence DOES (orchestrator routing context).
        self.create_property('description', '')
        # Click drift: each click lands a random min-max px off the match centre
        # (0-0 disables the nudge).  Editable from the sequence properties dialog.
        self.create_property('click_drift_min', '5.0')
        self.create_property('click_drift_max', '10.0')

        # Visual indicator for editable nodes (border accent)
        try:
            from ..constants import BLOCK_HOVER
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
            use_app_opened = sequence_config.get('use_app_opened', None)
        
        # CRITICAL FIX: Use only the sequence filename to prevent automatic numbering
        # that breaks sequence file references during playback
        # The node name should be just the filename (e.g., "test.json") not "test.json 2"
        self.set_name(seq_name)
        
        # CRITICAL: Always store only the filename in sequence_file property
        # This ensures the player looks for the correct file regardless of GUI numbering
        self.set_property('sequence_file', sequence_file)
        self.set_property('loop_count', str(loop_count))
        self.set_property('extra_delay', str(extra_delay))
        try:
            if use_app_opened is not None:
                if isinstance(use_app_opened, str):
                    use_app_opened = use_app_opened.strip().lower() in ("true", "1", "yes", "y", "on")
                self.set_property('use_app_opened', 'true' if bool(use_app_opened) else 'false')
        except Exception:
            pass

        # Drift bounds are optional: absent keys keep the current/default values
        if isinstance(sequence_config, dict):
            for prop, default in (('click_drift_min', 5.0), ('click_drift_max', 10.0)):
                if prop in sequence_config:
                    try:
                        self.set_property(prop, str(float(sequence_config.get(prop))))
                    except (TypeError, ValueError):
                        self.set_property(prop, str(default))
            # Same rule for the description: an absent key never wipes it.
            if 'description' in sequence_config:
                self.set_property(
                    'description', str(sequence_config.get('description') or ''))
        
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
            'extra_delay': float(self.get_property('extra_delay') or 1.0),
            'use_app_opened': str(self.get_property('use_app_opened') or 'true').strip().lower() in ("true", "1", "yes", "y", "on"),
            'click_drift_min': float(self.get_property('click_drift_min') or 5.0),
            'click_drift_max': float(self.get_property('click_drift_max') or 10.0),
            'description': str(self.get_property('description') or ''),
        }
