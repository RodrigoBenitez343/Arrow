"""Node wrapping an MCP server tool call.

Points at a cloned MCP server folder.  On execution, spawns the server
as a subprocess, sends a single JSON-RPC ``tools/call``, and stores the
result as ``node_{id}_output`` for downstream Code / LLM nodes.
"""

from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    MCP_NODE_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE,
)


class MCPNode(BaseNode):
    """Node representing an MCP server tool call"""

    __identifier__ = 'mcp'
    NODE_NAME = 'MCP Server'

    def __init__(self):
        super(MCPNode, self).__init__()

        base_color = MCP_NODE_COLOR
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

        input_port = _add_multi_input(self, 'input', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        output_port = self.add_output('output', color=out_rgb, display_name=True, multi_output=True)

        for p in [input_port, output_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass

        # Properties
        self.create_property('mcp_folder', '')       # Path to cloned MCP server
        self.create_property('tool_name', '')         # MCP tool name (e.g. browser_navigate)
        self.create_property('tool_args', '{}')       # JSON string of tool arguments
        self.create_property('mcp_tools', '')         # Cached JSON of discovered tool schemas
        self.create_property('keep_alive', False)     # Keep server alive across chain iterations

    def get_mcp_config(self):
        """Return configuration dict for save/restore."""
        return {
            'mcp_folder': self.get_property('mcp_folder') or '',
            'tool_name': self.get_property('tool_name') or '',
            'tool_args': self.get_property('tool_args') or '{}',
            'mcp_tools': self.get_property('mcp_tools') or '',
            'keep_alive': self.get_property('keep_alive') or False,
        }
