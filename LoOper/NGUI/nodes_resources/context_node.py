from NodeGraphQt import BaseNode
from ..constants import (
    CONTEXT_NODE_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
)


class ContextNode(BaseNode):
    """Node representing a Context pool (NOT an executable node).

    A Context node is a shared pool of context: other nodes CONSULT it by
    wiring its 'ctx_out' to their 'ctx_in'/'context' port, and WRITE into it
    by connecting their data-output ports to its 'ctx_in' port.  It drives no
    execution, so it has no base 'input'/'output' ports.
    """

    __identifier__ = 'context'
    NODE_NAME = 'Context'

    def __init__(self):
        super(ContextNode, self).__init__()

        # Darken the color for text readability
        base_color = CONTEXT_NODE_COLOR
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

        # Ports — DATA ONLY (the Context node is a non-executable pool):
        # - ctx_in  : data IN  — other nodes WRITE into the pool by connecting
        #             their data-output ports here.
        # - ctx_out : data OUT — other nodes CONSULT the pool by wiring it to
        #             their own 'ctx_in' / 'context' port.
        # No base 'input'/'output' ports: a Context node drives no execution.
        ctx_in_port = self.add_input('ctx_in', color=in_rgb, display_name=True, multi_input=True)
        ctx_out_port = self.add_output('ctx_out', color=out_rgb, display_name=True, multi_output=True)

        for p in [ctx_in_port, ctx_out_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass

        # Properties — minimal config
        self.create_property('label', '')         # Human-readable description
        self.create_property('max_history', 10)   # Recent entries to keep/serve
        self.create_property('persistent', True)  # Keep context across runs
        self.create_property('clear_on_finish', False)  # Task 13: Clear all stored data after chain finishes
        # Internal property to preserve the original config node_id across graph save/load.
        # Must be registered here (not set dynamically at load time) because PyInstaller
        # compiled builds cannot create NodeGraphQt properties on-the-fly.
        self.create_property('scope', 'local')  # 'local' or 'global'
        self.create_property('shared_context_chain_file', '')  # source chain path for cross-chain sharing
        self.create_property('shared_context_node_id', '')  # source context node id (clone reference)
        self.create_property('_config_node_id', '')

    def get_context_config(self):
        """Return the minimal configuration for this node."""
        return {
            'label': self.get_property('label') or '',
            'max_history': int(self.get_property('max_history') or 10),
            'persistent': bool(self.get_property('persistent')),
            'clear_on_finish': bool(self.get_property('clear_on_finish')),
            'scope': str(self.get_property('scope') or 'local'),
            'shared_context_chain_file': str(self.get_property('shared_context_chain_file') or ''),
            'shared_context_node_id': str(self.get_property('shared_context_node_id') or ''),
        }
