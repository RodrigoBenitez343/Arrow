import os
from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    CODE_NODE_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)

class CodeNode(BaseNode):
    """Node representing a Code Execution unit that can run Python code."""
    
    __identifier__ = 'code'
    NODE_NAME = 'Code'
    
    def __init__(self):
        super(CodeNode, self).__init__()
        
        # Set a distinct color for Code nodes
        self.set_color(*[int(CODE_NODE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        
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
        # out_port.model.type_ = UNIVERSAL_PORT_TYPE
        
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
        self.create_property('code', '# Write your Python code here\n# input_data is available\n# result = ...')
        self.create_property('file_path', '')
        self.create_property('execute_on_input', 'true')
        self.create_property('output_variable', 'result')
        self.create_property('timeout', '30')
        self.create_property('gen_model', 'llama3.2:latest')
        self.create_property('gen_prompt', '')
        self.create_property('description', '')

        # Visual indicator
        try:
            from ..constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass
            
    def get_code_config(self):
        """Get the configuration for this node"""
        return {
            'code': self.get_property('code'),
            'file_path': self.get_property('file_path'),
            'execute_on_input': self.get_property('execute_on_input') == 'true',
            'output_variable': self.get_property('output_variable'),
            'timeout': int(self.get_property('timeout') or 30),
            'gen_model': self.get_property('gen_model'),
            'gen_prompt': self.get_property('gen_prompt'),
            'description': self.get_property('description') or ''
        }

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
