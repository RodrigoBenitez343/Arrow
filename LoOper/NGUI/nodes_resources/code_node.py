import json
import os
from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    CODE_NODE_COLOR,
    DARK_GREY,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    PORT_FALSE_COLOR,
    UNIVERSAL_PORT_TYPE
)

class CodeNode(BaseNode):
    """Node representing a Code Execution unit that can run Python code."""
    
    __identifier__ = 'code'
    NODE_NAME = 'Code'
    
    def __init__(self):
        super(CodeNode, self).__init__()
        
        # Use a darkened version of the special color for better text readability
        # Keep the color identity but make it dark enough for white text
        base_color = CODE_NODE_COLOR
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
        err_rgb = tuple(int(PORT_FALSE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Inputs
        # Main execution trigger / input data
        in_port = _add_multi_input(self, 'input', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        
        # Outputs
        out_port = self.add_output('output', color=out_rgb, display_name=True, multi_output=True)
        # Error output port (visible counterpart of the executor's error routing)
        err_port = self.add_output('error', color=err_rgb, display_name=True)
        
        # Configure multi-connection
        for p in [in_port, out_port, err_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass
        
        # Register properties
        self.create_property('code', '# Write your Python code here\n# input_data is available\n# result = ...')
        self.create_property('file_path', '')
        self.create_property('execute_on_input', 'true')
        self.create_property('output_variable', 'result')
        self.create_property('timeout', '30')
        self.create_property('gen_model', 'llama3.2:latest')
        self.create_property('gen_prompt', '')
        self.create_property('description', '')
        # Llama.cpp support for code generation
        self.create_property('use_llamacpp', 'false')
        self.create_property('llamacpp_model_path', '')
        self.create_property('llamacpp_gpu_layers', '0')
        self.create_property('llamacpp_threads', '-1')
        # Dependency management
        self.create_property('dependencies', '[]')
        # Continuous memory (conversation history)
        self.create_property('memory_context', '[]')
        # Custom IO port declarations (per-instance var table)
        self.create_property('input_vars', '[]')   # JSON: [{"name": "...", "type": "string"}, ...]
        self.create_property('output_vars', '[]')  # JSON: [{"name": "...", "type": "string"}, ...]
        
        # Track custom port names for cleanup on rebuild
        self._custom_input_ports = []
        self._custom_output_ports = []
            
    def get_code_config(self):
        """Get the configuration for this node"""
        deps_raw = self.get_property('dependencies') or '[]'
        try:
            dependencies = json.loads(deps_raw) if isinstance(deps_raw, str) else []
        except Exception:
            dependencies = []
        mem_raw = self.get_property('memory_context') or '[]'
        try:
            memory_context = json.loads(mem_raw) if isinstance(mem_raw, str) else []
        except Exception:
            memory_context = []
        iv_raw = self.get_property('input_vars') or '[]'
        try:
            input_vars = json.loads(iv_raw) if isinstance(iv_raw, str) else []
        except Exception:
            input_vars = []
        ov_raw = self.get_property('output_vars') or '[]'
        try:
            output_vars = json.loads(ov_raw) if isinstance(ov_raw, str) else []
        except Exception:
            output_vars = []
        return {
            'code': self.get_property('code'),
            'file_path': self.get_property('file_path'),
            'execute_on_input': self.get_property('execute_on_input') == 'true',
            'output_variable': self.get_property('output_variable'),
            'timeout': int(self.get_property('timeout') or 30),
            'gen_model': self.get_property('gen_model'),
            'gen_prompt': self.get_property('gen_prompt'),
            'description': self.get_property('description') or '',
            # Llama.cpp config
            'use_llamacpp': self.get_property('use_llamacpp') == 'true',
            'llamacpp_model_path': self.get_property('llamacpp_model_path') or '',
            'llamacpp_gpu_layers': int(self.get_property('llamacpp_gpu_layers') or 0),
            'llamacpp_threads': int(self.get_property('llamacpp_threads') or -1),
            # New fields
            'dependencies': dependencies,
            'memory_context': memory_context,
            'input_vars': input_vars,
            'output_vars': output_vars,
        }

    def rebuild_ports(self):
        """Rebuild custom IO ports from input_vars/output_vars properties."""
        # Remove old custom input ports
        for name in self._custom_input_ports:
            try:
                self.delete_input(name)
            except Exception:
                pass
        self._custom_input_ports = []

        # Remove old custom output ports (keep built-in 'output' and 'error')
        for name in self._custom_output_ports:
            try:
                self.delete_output(name)
            except Exception:
                pass
        self._custom_output_ports = []

        # Read current declarations
        try:
            input_vars = json.loads(self.get_property('input_vars') or '[]')
        except Exception:
            input_vars = []
        try:
            output_vars = json.loads(self.get_property('output_vars') or '[]')
        except Exception:
            output_vars = []

        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))

        # Create custom input ports
        for iv in input_vars:
            name = iv.get('name', '')
            if name and name not in ('input', 'args', 'code'):
                try:
                    p = self.add_input(name, color=in_rgb, display_name=True)
                    try:
                        if hasattr(p, 'set_multi_connection'):
                            p.set_multi_connection(True)
                        else:
                            setattr(p, '_multi_connection', True)
                    except Exception:
                        pass
                    self._custom_input_ports.append(name)
                except Exception:
                    pass

        # Create custom output ports
        for ov in output_vars:
            name = ov.get('name', '')
            if name and name not in ('output', 'error'):
                try:
                    p = self.add_output(name, color=out_rgb, display_name=True, multi_output=True)
                    self._custom_output_ports.append(name)
                except Exception:
                    pass

    def execute_code(self, input_data=None, args=None, runtime_code=None):
        """
        Execute the code associated with this node.
        
        Args:
            input_data: Data passed to the 'input' port
            args: Dictionary of arguments passed to 'args' port
            runtime_code: Optional code string to override stored code/file
            
        Returns:
            dict: {'success': bool, 'result': any, 'error': str}
        """
        try:
            # Determine code to run
            code_to_run = ""
            if runtime_code:
                code_to_run = runtime_code
            else:
                file_path = self.get_property('file_path')
                if file_path and os.path.exists(file_path):
                    with open(file_path, 'r') as f:
                        code_to_run = f.read()
                else:
                    code_to_run = self.get_property('code')
            
            if not code_to_run:
                return {'success': False, 'error': 'No code to execute'}

            # Prepare execution environment
            local_scope = {
                'input_data': input_data,
                'args': args or {},
                'os': os,
                # Add other safe globals if needed
            }
            
            # Execute
            # Note: exec() is dangerous if code is untrusted.
            # Assuming local user context.
            exec(code_to_run, {}, local_scope)
            
            # Extract result
            output_var = self.get_property('output_variable') or 'result'
            result = local_scope.get(output_var)
            
            return {
                'success': True,
                'result': result,
                'error': None
            }
            
        except Exception as e:
            return {
                'success': False,
                'result': None,
                'error': str(e)
            }
