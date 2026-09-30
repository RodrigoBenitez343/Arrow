import os
import logging
from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    CHAIN_IMPORT_COLOR,
    DETERMINISTIC_CHAIN_COLOR,
    DARK_GREY,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR
)
try:
    from ...player.chain_import_ports import classify_chain
except ImportError:
    try:
        from LoOper.player.chain_import_ports import classify_chain
    except ImportError:
        from player.chain_import_ports import classify_chain

logger = logging.getLogger(__name__)

class ChainImportNode(BaseNode):
    """Node representing an imported chain that can be composed with other chains to create larger automations"""
    
    __identifier__ = 'chain_import'
    NODE_NAME = 'Chain Import'
    
    def __init__(self):
        super(ChainImportNode, self).__init__()
        self.chain_file_path = ''
        self.chain_config = {}
        self.import_mode = 'full'  # 'full', 'sequences_only', 'nodes_only'
        self.prefix = ''  # Optional prefix for imported node names
        self.loop_count = 1  # Number of times to execute the imported chain
        self.extra_delay = 0  # Extra delay between loop iterations
        
        # Use a darkened version of the special color for better text readability
        # Keep the color identity but make it dark enough for white text
        base_color = CHAIN_IMPORT_COLOR
        r, g, b = int(base_color.strip('#')[0:2], 16), int(base_color.strip('#')[2:4], 16), int(base_color.strip('#')[4:6], 16)
        # Darken to 25% brightness for good contrast with white text
        dark_r, dark_g, dark_b = int(r * 0.25), int(g * 0.25), int(b * 0.25)
        self.set_color(dark_r, dark_g, dark_b)
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

        # Ports — there are NO dynamic data ports: the imported chain's Output
        # content is delivered directly to the tool consumer (LLM / agent
        # context), never via wired ports.  The 'data_output_nodes' selection
        # (dialog) controls WHICH Output nodes' content is delivered.
        # - input  : feeds the imported chain / drives execution order
        # - output : execution-driver port — drives execution order
        in_port = _add_multi_input(self, 'input', in_rgb, True)
        out_port = self.add_output('output', color=out_rgb, display_name=True)
        for _port in (in_port, out_port):
            try:
                _port.set_multi_connection(True)
            except Exception:
                try:
                    setattr(_port, '_multi_connection', True)
                except Exception:
                    pass
        
        # Add configuration inputs
        # Register properties without rendering inline input widgets
        self.create_property('chain_file', '')
        self.create_property('run_in_sandbox', 'false')
        self.create_property('show_sandbox_window', 'true')
        self.create_property('import_mode', 'full')
        self.create_property('prefix', '')
        self.create_property('loop_count', '1')
        self.create_property('extra_delay', '0')
        self.create_property('enabled', 'true')
        # Content selection: which imported-chain Output nodes' content is
        # delivered to the outer connection (the tool consumer / agent
        # context).  'data_output_nodes' is a JSON list of Output node ids;
        # empty = every Output node.  'emit_data' restricts delivery to the
        # selection when on; off = deliver every Output node.
        self.create_property('emit_data', 'false')
        self.create_property('data_output_nodes', '[]')
        # Classification (single rule, shared with the runtime): a chain with an
        # orchestrator (transitively) is a BRAIN — it belongs on the
        # orchestrator's 'brains' port; otherwise it is a DETERMINISTIC chain
        # for the 'chains' port.  Re-derived whenever a file is attached.
        self.create_property('import_kind', 'chain')
        
    def set_chain_import_data(self, chain_file_path, import_mode='full', prefix='', loop_count=1, extra_delay=0, enabled=True):
        """Set the chain import data for this node"""
        # Callers occasionally pass a dict / None — coerce so the property
        # below always holds a string and the save path stays JSON-safe.
        if not isinstance(chain_file_path, str):
            chain_file_path = ''
        self.chain_file_path = chain_file_path
        self.import_mode = import_mode
        self.prefix = prefix
        self.loop_count = loop_count
        self.extra_delay = extra_delay

        # Persist the file reference on the node UNCONDITIONALLY — including
        # when the file does not resolve on this machine.  ConfigManager's
        # _save_chain_import_node writes this property as chain_file_path;
        # setting it only inside the os.path.exists() branch below silently
        # ERASED the reference on the next save whenever the path was missing
        # (chain moved/renamed, chain authored on another machine) — the node
        # then saved as chain_file_path:'' and did nothing at runtime.
        self.set_property('chain_file', chain_file_path)
        self.set_property('import_mode', import_mode)
        self.set_property('prefix', prefix)
        self.set_property('loop_count', str(loop_count))
        self.set_property('extra_delay', str(extra_delay))
        self.set_property('enabled', str(enabled).lower())

        # Missing file: keep the path (it survives the next save) and show the
        # broken state instead of posing as a healthy "Chain Import" node.
        if not chain_file_path or not os.path.exists(chain_file_path):
            if chain_file_path:
                self.set_name(f"Import: MISSING - {os.path.basename(chain_file_path)}")
                self.set_color(200, 50, 50)
            else:
                self.set_name("Import: No file")
            return

        # Load the chain configuration
        try:
            import json
            with open(chain_file_path, 'r') as f:
                self.chain_config = json.load(f)

            # Update node name to reflect the imported chain
            chain_name = os.path.basename(chain_file_path).replace('.json', '')
            if prefix:
                self.set_name(f"Import: {prefix}_{chain_name}")
            else:
                self.set_name(f"Import: {chain_name}")

            # Kind + color: deterministic chains ride the 'chains' port and
            # are painted in the deterministic palette; brains keep the
            # chain-import identity.
            kind = classify_chain(self.chain_config, os.path.dirname(chain_file_path))
            self.set_property('import_kind', kind)

            # Change color based on status
            _kind_color = (
                DETERMINISTIC_CHAIN_COLOR if kind == 'chain' else CHAIN_IMPORT_COLOR
            )
            if enabled:
                self.set_color(*[int(_kind_color.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
            else:
                # Darker color when disabled
                self.set_color(*[int(_kind_color.strip('#')[i:i+2], 16) // 2 for i in (0, 2, 4)])

        except Exception as e:
            self.set_name(f"Import: ERROR - {os.path.basename(chain_file_path)}")
            # Red color for error state
            self.set_color(200, 50, 50)

    def get_data_output_nodes(self):
        """Output node ids of the imported chain to expose as data ports."""
        import json
        try:
            val = self.get_property('data_output_nodes')
            if isinstance(val, str):
                val = json.loads(val or '[]')
            return [str(v) for v in (val or []) if str(v).strip()]
        except Exception:
            return []

    def get_chain_import_config(self):
        """Get the chain import configuration for this node"""
        return {
            'chain_file': self.get_property('chain_file'),
            'run_in_sandbox': self.get_property('run_in_sandbox') == 'true',
            'show_sandbox_window': self.get_property('show_sandbox_window') == 'true',
            'import_mode': self.get_property('import_mode'),
            'import_kind': str(self.get_property('import_kind') or 'chain'),
            'prefix': self.get_property('prefix'),
            'loop_count': int(self.get_property('loop_count') or '1'),
            'extra_delay': float(self.get_property('extra_delay') or '0'),
            'enabled': self.get_property('enabled').lower() == 'true',
            'emit_data': str(self.get_property('emit_data') or 'false').lower() == 'true',
            'data_output_nodes': self.get_data_output_nodes()
        }
    
    def get_imported_chain_config(self):
        """Get the loaded chain configuration"""
        return self.chain_config
    
    def get_sequences_count(self):
        """Get the number of sequences in the imported chain"""
        return len(self.chain_config.get('sequences', []))
    
    def get_nodes_count(self):
        """Get the total number of nodes in the imported chain"""
        sequences = len(self.chain_config.get('sequences', []))
        conditionals = len(self.chain_config.get('conditional_nodes', []))
        llm_nodes = len(self.chain_config.get('llm_nodes', []))
        output_nodes = len(self.chain_config.get('output_nodes', []))
        return sequences + conditionals + llm_nodes + output_nodes
    
    def is_valid_chain(self):
        """Check if the imported chain is valid"""
        return bool(self.chain_config and (
            self.chain_config.get('sequences') or 
            self.chain_config.get('conditional_nodes') or 
            self.chain_config.get('llm_nodes') or 
            self.chain_config.get('output_nodes')
        ))
