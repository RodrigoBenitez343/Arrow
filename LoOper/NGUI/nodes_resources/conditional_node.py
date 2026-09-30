import logging
from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    CONDITIONAL_COLOR,
    DARK_GREY,
    INPUT_PORT_COLOR,
    PORT_TRUE_COLOR,
    PORT_FALSE_COLOR
)

logger = logging.getLogger(__name__)


class ConditionalNode(BaseNode):
    """Node representing a conditional check that can control sequence execution flow with comprehensive condition types"""

    # Loop conditionals that run an internal sequence/chain file until the
    # condition stops holding — they are NOT single-pass true/false branches.
    LOOP_TYPES = ('while_present', 'while_absent', 'until_present', 'until_absent')

    __identifier__ = 'conditional'
    NODE_NAME = 'Conditional'
    
    def __init__(self):
        super(ConditionalNode, self).__init__()
        self.condition_type = 'presence_trigger'
        self.condition_data = {
            'type': 'presence_trigger',
            'image_path': '',
            'confidence': 0.8,
            'timeout': 5.0
        }
        
        # Use a darkened version of the special color for better text readability
        # Keep the color identity but make it dark enough for white text
        base_color = CONDITIONAL_COLOR
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
        out_true_rgb = tuple(int(PORT_TRUE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        out_false_rgb = tuple(int(PORT_FALSE_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4))
        self._out_true_rgb = out_true_rgb
        self._out_false_rgb = out_false_rgb

        # Add single input for conditional evaluation (blue)
        in_port = _add_multi_input(self, 'input', in_rgb, True)

        true_port = self.add_output('true', color=out_true_rgb, display_name=True)
        false_port = self.add_output('false', color=out_false_rgb, display_name=True)
        try:
            in_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(in_port, '_multi_connection', True)
            except Exception:
                pass
        for p in (true_port, false_port):
            try:
                p.set_multi_connection(True)
            except Exception:
                try:
                    setattr(p, '_multi_connection', True)
                except Exception:
                    pass
        
        # Register properties without rendering inline input widgets
        self.create_property('condition_type', 'presence')
        self.create_property('image_path', '')
        self.create_property('image_data', '')
        self.create_property('confidence', '0.8')
        self.create_property('threshold', '0.8')
        self.create_property('timeout', '5.0')
        self.create_property('wait_time', '5')
        self.create_property('max_loops', '10')
        self.create_property('max_attempts', '3')
        self.create_property('delay_between_attempts', '1.0')
        self.create_property('scroll_direction', '1')  # 1 = up, -1 = down
        self.create_property('target_x', '')
        self.create_property('target_y', '')
        self.create_property('target_w', '')
        self.create_property('target_h', '')
        self.create_property('position_tolerance', '6')
        self.create_property('code',
            '# input_data contains the output from the previous node (LLM result, code output, etc.)\n'
            '# Set result = True for true branch, False for false branch\n'
            'result = True')
        
        # OCR-specific fields (language removed - uses default English)
        self.create_property('target_text', '')
        self.create_property('ocr_text', '')
        self.create_property('region', '')  # picked OCR search box (JSON [x,y,w,h]); '' = full screen
        self.create_property('case_sensitive', 'true')
        
        # LLM-conditional fields
        self.create_property('llm_engine', 'ollama')  # 'ollama' or 'llamacpp'
        self.create_property('llm_model', '')
        self.create_property('llm_prompt',
            'You are a strict routing classifier.\n'
            'Respond with EXACTLY one word: "True" or "False".\n'
            'No other text, explanation, punctuation, or formatting.\n'
            'Respond "True" only if the context satisfies this condition; otherwise "False".\n'
            '\n'
            'Condition to evaluate: [user describes the condition here]')
        self.create_property('llm_timeout', '10.0')
        self.create_property('llm_use_vision', 'false')  # capture screenshot + send to VLM
        
        # Loop-specific fields (empty by default — only set by loop conditional dialog)
        self.create_property('loop_type', '')
        self.create_property('loop_position', 'pre')
        self.create_property('sequence_file', '')
        self.create_property('iteration_delay', '1.0')
        self.create_property('loop_condition', '')

        # Web-mode conditional: when 'web_mode' is true the node routes on a
        # live-browser condition (evaluated by player/.../web_conditions.py)
        # instead of the desktop visual conditions.  'web_condition_type'
        # selects the variant:
        #   element_located - visual trigger: an element is located on the page
        #   text_present    - visible text (page or element) is present
        #   browser_js      - a JS snippet run in the page returns truthy
        #   llm             - an LLM reads the page text + element presence
        #   layout_match    - scroll until an element sits at a window location
        self.create_property('web_mode', 'false')
        self.create_property('web_condition_type', 'element_located')
        # element_located / layout_match target (locator JSON captured by the picker)
        self.create_property('web_element_locator', '')
        self.create_property('web_layout_locator', '')
        # text_present
        self.create_property('web_text_source', 'page')  # 'page' | 'element'
        self.create_property('web_text_locator', '')
        self.create_property('web_target_text', '')
        self.create_property('web_case_sensitive', 'false')
        # browser_js
        self.create_property('web_js', '')
        # llm
        self.create_property('web_llm_engine', 'ollama')
        self.create_property('web_llm_model', '')
        self.create_property('web_llm_prompt', '')
        self.create_property('web_llm_timeout', '10.0')
        # layout_match target window rect + scroll behaviour
        self.create_property('web_layout_x', '')
        self.create_property('web_layout_y', '')
        self.create_property('web_layout_w', '')
        self.create_property('web_layout_h', '')
        self.create_property('web_layout_direction', '-1')  # 1 = up, -1 = down
        self.create_property('web_layout_attempts', '20')
        self.create_property('web_layout_tolerance', '12')
        self.create_property('web_timeout', '10.0')
        # Human-readable: what this check DOES (orchestrator routing context).
        self.create_property('description', '')
        
    def is_file_loop_conditional(self):
        """
        Whether this node is a file-loop conditional: a loop_type set AND an
        internal sequence/chain file.  Such conditionals loop the file until
        the condition stops holding and then continue the workflow — they are
        not single-pass true/false branches.
        """
        loop_type = str(self.get_property('loop_type') or '').strip().lower()
        seq_file = str(self.get_property('sequence_file') or '').strip()
        return bool(loop_type in self.LOOP_TYPES and seq_file)

    def update_loop_port_mode(self):
        """
        Morph the output ports to match the conditional semantics.

        File-loop conditionals expose a single ``output`` port (the loop
        continuation) instead of ``true``/``false``: the loop decides when it
        ends, so the author wires one continuation, not two branches.
        Regular single-pass conditionals keep the true/false branches.

        Existing connections are moved between port shapes so no wiring is
        lost when a node is converted back and forth.
        """
        try:
            loop_mode = self.is_file_loop_conditional()
        except Exception:
            return
        try:
            out_ports = {p.name(): p for p in self.output_ports()}
        except Exception:
            return
        true_port = out_ports.get('true')
        false_port = out_ports.get('false')
        output_port = out_ports.get('output')

        if loop_mode:
            if output_port is None:
                try:
                    output_port = self.add_output(
                        'output', color=self._out_true_rgb, display_name=True
                    )
                except Exception:
                    output_port = None
            # Preserve existing branch wiring on the single loop output.
            for src in (true_port, false_port):
                if src is None:
                    continue
                for dst in list(src.connected_ports()):
                    try:
                        src.disconnect_from(dst, push_undo=False)
                        if output_port is not None:
                            output_port.connect_to(dst, push_undo=False)
                    except Exception:
                        pass
            for p in (true_port, false_port):
                if p is not None:
                    try:
                        p.set_visible(False, push_undo=False)
                    except Exception:
                        pass
        else:
            # Restore the true/false branches for single-pass conditionals.
            for p in (true_port, false_port):
                if p is not None:
                    try:
                        p.set_visible(True, push_undo=False)
                    except Exception:
                        pass
            if output_port is not None:
                # Keep the continuation wiring on the true branch.
                for dst in list(output_port.connected_ports()):
                    try:
                        output_port.disconnect_from(dst, push_undo=False)
                        if true_port is not None:
                            true_port.connect_to(dst, push_undo=False)
                    except Exception:
                        pass
                try:
                    output_port.set_visible(False, push_undo=False)
                except Exception:
                    pass

    def set_condition_data(self, condition_type, condition_data):
        """Set the condition data for this node"""
        self.condition_type = condition_type
        self.condition_data = condition_data
        
        self.set_name(f"Condition: {condition_type}")
        
        # Update the text inputs to reflect the new data
        self.set_property('condition_type', condition_type)
        self.set_property('image_path', condition_data.get('image_path', ''))
        self.set_property('image_data', condition_data.get('image_data', ''))
        self.set_property('confidence', str(condition_data.get('confidence', 0.8)))
        self.set_property('timeout', str(condition_data.get('timeout', 5.0)))
        # Region removed - OCR now uses full screen
        self.set_property('max_attempts', str(condition_data.get('max_attempts', 3)))
        self.set_property('delay_between_attempts', str(condition_data.get('delay_between_attempts', 1.0)))
        self.set_property('scroll_direction', str(condition_data.get('scroll_direction', 1)))  # 1 = up, -1 = down
        
        # OCR-specific properties
        self.set_property('target_text', condition_data.get('target_text', ''))
        self.set_property('case_sensitive', str(condition_data.get('case_sensitive', True)).lower())
        self.set_property('language', condition_data.get('language', 'eng'))
        
        # Update additional properties for loop conditions
        if condition_type in ['while_present', 'while_absent', 'until_present', 'until_absent']:
            self.set_property('max_attempts', str(condition_data.get('max_iterations', 10)))
            self.set_property('delay_between_attempts', str(condition_data.get('iteration_delay', 1.0)))
        if condition_type == 'code':
            self.set_property('code', condition_data.get('code', self.get_property('code') or 'result = True'))
        if condition_type == 'llm':
            self.set_property('llm_engine', condition_data.get('engine', self.get_property('llm_engine') or 'ollama'))
            self.set_property('llm_model', condition_data.get('model', self.get_property('llm_model') or ''))
            self.set_property('llm_prompt', condition_data.get('prompt', self.get_property('llm_prompt') or ''))
            self.set_property('llm_timeout', str(condition_data.get('timeout', float(self.get_property('llm_timeout') or 10.0))))
            self.set_property('llm_use_vision', str(condition_data.get('use_vision', self.get_property('llm_use_vision') or 'false')).lower())
    
    def get_condition_config(self):
        """Get the condition configuration for this node"""
        config = {
            'type': self.condition_type,
            'image_path': self.condition_data.get('image_path', ''),
            'confidence': self.condition_data.get('confidence', 0.8)
        }
        
        # Add timeout for relevant condition types
        if self.condition_type in ['presence_trigger', 'absence_trigger', 'wait_presence', 'wait_absence', 'ocr_trigger']:
            config['timeout'] = self.condition_data.get('timeout', 5.0)
        
        # Add OCR-specific parameters for OCR trigger
        if self.condition_type == 'ocr_trigger':
            config.update({
                'target_text': self.condition_data.get('target_text', ''),
                'case_sensitive': self.condition_data.get('case_sensitive', True)
            })
        
        # Add loop parameters for loop condition types
        if self.condition_type in ['while_present', 'while_absent', 'until_present', 'until_absent']:
            config.update({
                'sequence_file': self.condition_data.get('sequence_file', ''),
                'max_iterations': self.condition_data.get('max_iterations', 10),
                'iteration_delay': self.condition_data.get('iteration_delay', 1.0)
            })

        if self.condition_type == 'code':
            config.update({
                'code': self.get_property('code') or self.condition_data.get('code', '')
            })
        
        if self.condition_type == 'llm':
            config.update({
                'engine': self.get_property('llm_engine') or self.condition_data.get('engine', 'ollama'),
                'model': self.get_property('llm_model') or self.condition_data.get('model', ''),
                'prompt': self.get_property('llm_prompt') or self.condition_data.get('prompt', ''),
                'timeout': float(self.get_property('llm_timeout') or self.condition_data.get('timeout', 10.0)),
                'use_vision': self.get_property('llm_use_vision') or self.condition_data.get('use_vision', 'false')
            })
        
        return config
