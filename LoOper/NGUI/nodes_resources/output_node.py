from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    OUTPUT_NODE_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)


class OutputNode(BaseNode):
    """Node representing an Output sink/bridge between chains.

    Collects data from upstream nodes and makes it available as the
    chain's execution result. In agent mode, the collected content is
    returned to the agent overlay. In manual mode, a popup is shown
    when the chain finishes.

    When used in nested chains (via ChainImport), the Output node
    bridges results back to the parent chain so that the system chain
    or agent can see what sub-chains produced.
    """

    __identifier__ = 'output'
    NODE_NAME = 'Output'

    def __init__(self):
        super(OutputNode, self).__init__()

        # Darken the color for text readability
        base_color = OUTPUT_NODE_COLOR
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

        # Ports
        input_port = _add_multi_input(self, 'input', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        self.add_output('output', color=out_rgb, display_name=True, multi_output=True)

        for p in [input_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass

        # Properties
        self.create_property('label', '')                     # Human-readable description
        # Identifier used when this chain is imported: each exposed Output
        # node becomes a data port named after this variable_name on the
        # chain import node (see player/chain_import_ports.py).
        self.create_property('variable_name', '')
        self.create_property('agent_visible', True)           # Visible to Agent Mode
        self.create_property('overlay_visible', True)         # Post to the agent chat overlay (memory records regardless)
        self.create_property('popup_on_finish', True)         # Show popup in manual mode
        self.create_property('show_rating', True)             # Show 5-star rating widget in agent overlay
        # Render + TTS (unified chat/display medium)
        self.create_property('render_mode', 'text')           # text | audio | image | html | markdown
        self.create_property('tts_enabled', False)            # Speak collected content (or tts_text)
        self.create_property('tts_text', '')                  # Static text fallback for speech
        self.create_property('tts_language', 'en')
        self.create_property('tts_voice_model', '')
        self.create_property('tts_speed', 1.0)
        self.create_property('tts_speaker_id', None)
        self.create_property('tts_wait', False)               # Block workflow while speaking
        self.create_property('image_source', '')              # Optional explicit image file for image mode
        # Internal property to preserve the original config node_id across graph save/load
        self.create_property('_config_node_id', '')

    def get_output_config(self):
        """Return the minimal configuration for this node."""
        return {
            'label': self.get_property('label') or '',
            'variable_name': self.get_property('variable_name') or '',
            'agent_visible': bool(self.get_property('agent_visible')),
            'overlay_visible': bool(self.get_property('overlay_visible')),
            'popup_on_finish': bool(self.get_property('popup_on_finish')),
            'show_rating': bool(self.get_property('show_rating')),
            'render_mode': self.get_property('render_mode') or 'text',
            'tts_enabled': bool(self.get_property('tts_enabled')),
            'tts_text': self.get_property('tts_text') or '',
            'tts_language': self.get_property('tts_language') or 'en',
            'tts_voice_model': self.get_property('tts_voice_model') or '',
            'tts_speed': self.get_property('tts_speed') if self.get_property('tts_speed') is not None else 1.0,
            'tts_speaker_id': self.get_property('tts_speaker_id'),
            'tts_wait': bool(self.get_property('tts_wait')),
            'image_source': self.get_property('image_source') or '',
        }
