from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    INPUT_NODE_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)


class InputNode(BaseNode):
    """Node representing an input request point.

    When executed in agent mode, the original user query is stored as this
    node's output so the chain can react to what was asked.
    When executed manually, a dialog pops up asking the user to type text.

    Passthrough mode (``passthrough=True``):
      Data flows to connected LLM/Code/Conditional nodes only — no screen
      typing.  Use this when the Input node feeds a reasoning pipeline
      rather than filling a visible form field.
    """

    __identifier__ = 'input'
    NODE_NAME = 'Input'

    # Lighter accent used when passthrough is enabled so users can tell at a glance
    _PASSTHROUGH_COLOR = (40, 120, 160)  # muted teal

    def __init__(self):
        super(InputNode, self).__init__()

        # Darken the color for text readability
        base_color = INPUT_NODE_COLOR
        r, g, b = int(base_color.strip('#')[0:2], 16), int(base_color.strip('#')[2:4], 16), int(base_color.strip('#')[4:6], 16)
        dark_r, dark_g, dark_b = int(r * 0.25), int(g * 0.25), int(b * 0.25)
        self._default_color = (dark_r, dark_g, dark_b)
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
        # - input : execution-driver port — signals execution flow and carries data
        # - output : execution-driver port — drives execution order (first
        #   connected consumer runs next, like every other node)
        # - data : explicit data-output port — the resolved value flows ONLY
        #   to consumers connected here, so no reachability walk is needed to
        #   decide how far the input propagates.
        input_port = _add_multi_input(self, 'input', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        output_port = self.add_output('output', color=out_rgb, display_name=True, multi_output=True)
        data_port = self.add_output('data', color=out_rgb, display_name=True, multi_output=True)

        for p in [input_port, output_port, data_port]:
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass

        # Properties
        self.create_property('label', '')           # Instruction/description text for what to input
        self.create_property('default_value', '')   # Optional default text for the input dialog
        self.create_property('passthrough', False)  # True = data-only mode (no screen typing)
        # Web mode: type the resolved value into the chain's focused browser
        # editable instead of the desktop (mirrors LLMNode.web_mode).  Ignored
        # in passthrough mode (nothing is typed).
        self.create_property('web_mode', False)
        self.create_property('user_prompt', '')     # Custom question shown when agent requests user input
        self.create_property('agent_modifiable', False)  # When True, parent LLM fills value
        # Decision routing: the node itself judges its resolved value against a
        # natural-language criterion and routes true/false (no regex).
        self.create_property('decision_mode', False)
        self.create_property('decision_criterion', '')
        self.create_property('decision_evaluator', 'llm')   # llm | laya
        self.create_property('decision_model', '')          # blank = borrow chain LLM node
        self.create_property('decision_default', False)     # fail-closed default branch
        # Question modes: what the user is asked when user_prompt fires.
        self.create_property('question_mode', 'text')       # text | yes_no | choice
        self.create_property('choices', '[]')               # JSON [{"label", "value"}]
        self.create_property('route_on_answer', False)      # yes/no answer drives true/false
        # Accepted media: which input kinds this node accepts from the user.
        self.create_property('accept_text', True)
        self.create_property('accept_images', False)
        self.create_property('accept_documents', False)
        # TTS (speak the user_prompt question when shown in the overlay)
        self.create_property('tts_enabled', False)
        self.create_property('tts_text', '')        # Static text fallback for speech (else user_prompt)
        self.create_property('tts_language', 'en')
        self.create_property('tts_voice_model', '')
        self.create_property('tts_speed', 1.0)
        self.create_property('tts_speaker_id', None)

    # ------------------------------------------------------------------
    # Passthrough / decision helpers
    # ------------------------------------------------------------------

    _DECISION_COLOR = (46, 110, 92)  # muted deep teal for routing gates

    def set_passthrough(self, enabled: bool):
        """Toggle passthrough mode and update the node colour accordingly."""
        self.set_property('passthrough', enabled)
        self.refresh_visual_state()

    def is_passthrough(self) -> bool:
        return bool(self.get_property('passthrough'))

    def is_decision_routing(self) -> bool:
        """True when the node routes execution through true/false branches."""
        return bool(self.get_property('decision_mode')) or bool(self.get_property('route_on_answer'))

    def set_decision_mode(self, enabled: bool, route_on_answer: bool = None):
        """Toggle decision routing and add/remove the true/false ports."""
        self.set_property('decision_mode', bool(enabled))
        if route_on_answer is not None:
            self.set_property('route_on_answer', bool(route_on_answer))
        self.rebuild_decision_ports()
        self.refresh_visual_state()

    def rebuild_decision_ports(self):
        """Add or remove the true/false output ports to match the routing flags."""
        routing = self.is_decision_routing()
        present = getattr(self, '_decision_ports_present', False)
        if routing and not present:
            out_rgb = tuple(
                int(OUTPUT_PORT_COLOR.strip('#')[i:i + 2], 16) for i in (0, 2, 4)
            )
            for name in ('true', 'false'):
                try:
                    p = self.add_output(name, color=out_rgb, display_name=True, multi_output=True)
                    try:
                        if hasattr(p, 'set_multi_connection'):
                            p.set_multi_connection(True)
                        else:
                            setattr(p, '_multi_connection', True)
                    except Exception:
                        pass
                except Exception:
                    pass
            self._decision_ports_present = True
        elif not routing and present:
            for name in ('true', 'false'):
                try:
                    self.delete_output(name)
                except Exception:
                    pass
            self._decision_ports_present = False

    def refresh_visual_state(self):
        """Colour precedence: passthrough > decision routing > default."""
        try:
            if bool(self.get_property('passthrough')):
                self.set_color(*self._PASSTHROUGH_COLOR)
            elif self.is_decision_routing():
                self.set_color(*self._DECISION_COLOR)
            else:
                self.set_color(*self._default_color)
        except Exception:
            pass

    def get_input_config(self):
        """Return the minimal configuration for this node."""
        return {
            'label': self.get_property('label') or '',
            'default_value': self.get_property('default_value') or '',
            'passthrough': bool(self.get_property('passthrough')),
            'web_mode': bool(self.get_property('web_mode')),
            'user_prompt': self.get_property('user_prompt') or '',
            'agent_modifiable': bool(self.get_property('agent_modifiable')),
            'decision_mode': bool(self.get_property('decision_mode')),
            'decision_criterion': self.get_property('decision_criterion') or '',
            'decision_evaluator': str(self.get_property('decision_evaluator') or 'llm'),
            'decision_model': self.get_property('decision_model') or '',
            'decision_default': bool(self.get_property('decision_default')),
            'question_mode': str(self.get_property('question_mode') or 'text'),
            'choices': self.get_property('choices') or '[]',
            'route_on_answer': bool(self.get_property('route_on_answer')),
            'accept_text': bool(self.get_property('accept_text')),
            'accept_images': bool(self.get_property('accept_images')),
            'accept_documents': bool(self.get_property('accept_documents')),
            'tts_enabled': bool(self.get_property('tts_enabled')),
            'tts_text': self.get_property('tts_text') or '',
            'tts_language': self.get_property('tts_language') or 'en',
            'tts_voice_model': self.get_property('tts_voice_model') or '',
            'tts_speed': self.get_property('tts_speed') if self.get_property('tts_speed') is not None else 1.0,
            'tts_speaker_id': self.get_property('tts_speaker_id'),
        }
