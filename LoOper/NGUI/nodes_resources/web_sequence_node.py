import json
import os
import re
from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    DARK_GREY,
    FALLBACK_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE
)

class WebSequenceNode(BaseNode):
    """Node representing a web sequence (DOM-based browser session) in the chain"""

    __identifier__ = 'web_sequence'
    NODE_NAME = 'Web Sequence'

    def __init__(self):
        super(WebSequenceNode, self).__init__()
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
        # Data-only output port: carries the user-selected page data (toggled
        # in the properties dialog) to downstream nodes WITHOUT driving
        # execution — same semantics as Context/Input node ctx_out.
        ctx_out_port = self.add_output('ctx_out', color=out_rgb, display_name=True, multi_output=True)
        for p in [in_port, out_port, ctx_out_port]:
            try:
                p.set_multi_connection(True)
            except Exception:
                try:
                    setattr(p, '_multi_connection', True)
                except Exception:
                    pass

        # Register properties without rendering inline input widgets
        self.create_property('session_file', '')
        self.create_property('headless', 'false')
        self.create_property('speed', '1.0')
        self.create_property('native_actions', 'true')
        self.create_property('loop_count', '1')
        self.create_property('extra_delay', '1.0')
        # How the node repeats: 'count' = fixed loop_count passes;
        # 'per_element' = once for each matching element of the session's
        # entity-marked (Insert) repeating element.
        self.create_property('repeat_mode', 'count')
        # JSON list of page items to extract onto the ctx_out port after replay
        # (e.g. ["page_text", "page_html", "title", "url"]); empty = none.
        self.create_property('extract_items', '[]')
        # Human-readable: what this web session DOES (orchestrator routing).
        self.create_property('description', '')

        # Visual indicator for editable nodes (border accent)
        try:
            from ..constants import BLOCK_HOVER
            border_rgb = [int(BLOCK_HOVER.strip('#')[i:i+2], 16) for i in (0, 2, 4)]
            self.set_border_color(*border_rgb)
        except Exception:
            pass

    def set_web_sequence_data(self, sequence_idx, sequence_config, action_fallbacks=None):
        """Set the web sequence data for this node"""
        self.sequence_idx = sequence_idx
        self.sequence_config = sequence_config
        self.action_fallbacks = action_fallbacks or {}

        # Handle both string and dict sequence_config formats
        if isinstance(sequence_config, str):
            # Legacy format - sequence_config is just the file path
            session_file = os.path.basename(sequence_config)  # Store filename only
            loop_count = 1
            extra_delay = 1.0
            headless = 'false'
            speed = 1.0
            native_actions = 'true'
            repeat_mode = 'count'
        else:
            # New format - sequence_config is a dictionary
            session_file = sequence_config.get('session_file', '')
            # Ensure we store only the filename, not full path
            if session_file and (os.path.sep in session_file or '/' in session_file):
                session_file = os.path.basename(session_file)
            loop_count = sequence_config.get('loop_count', 1)
            extra_delay = sequence_config.get('extra_delay', 1.0)
            headless = sequence_config.get('headless', 'false')
            speed = sequence_config.get('speed', 1.0)
            native_actions = sequence_config.get('native_actions', 'true')
            repeat_mode = sequence_config.get('repeat_mode', 'count')
            if 'extract_items' in sequence_config:
                self.set_property('extract_items', json.dumps(sequence_config.get('extract_items') or []))
            # Same rule as the sequence node: an absent key never wipes it.
            if 'description' in sequence_config:
                self.set_property(
                    'description', str(sequence_config.get('description') or ''))

        # The node name defaults to the session filename (e.g. "login.json"),
        # but a user-chosen name is NEVER clobbered: re-recording keeps the
        # custom label while session_file stays the authoritative file binding.
        if self._is_default_name():
            self.set_name(session_file or 'web_sequence')

        # CRITICAL: Always store only the filename in session_file property
        # This ensures the player looks for the correct file regardless of GUI numbering
        self.set_property('session_file', session_file)
        self.set_property('loop_count', str(loop_count))
        self.set_property('extra_delay', str(extra_delay))
        self.set_property('headless', 'true' if self._as_bool(headless) else 'false')
        speed_val = self._as_float(speed)
        self.set_property('speed', str(speed_val if speed_val is not None else 1.0))
        self.set_property('native_actions', 'true' if self._as_bool(native_actions) else 'false')
        self.set_property('repeat_mode', str(repeat_mode or 'count'))

        has_fallbacks = any(key.startswith(f"{sequence_idx}_") for key in self.action_fallbacks.keys())
        if has_fallbacks:
            self.set_color(*[int(FALLBACK_COLOR.strip('#')[i:i+2], 16) for i in (0, 2, 4)])
        else:
            self.set_color(*[int(DARK_GREY.strip('#')[i:i+2], 16) for i in (0, 2, 4)])

    def _is_default_name(self):
        """True when the node still carries an auto-generated label instead of
        a user-chosen one: blank, the 'web_sequence' placeholder, or a
        timestamped 'web_session_YYYYMMDD-HHMMSS.json' filename."""
        name = (self.name() or '').strip()
        if not name or name == 'web_sequence':
            return True
        return bool(re.match(r'^web_session_\d{8}-\d{6}\.json$', name))

    @staticmethod
    def _as_bool(value):
        try:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("true", "1", "yes", "y", "on")
        except Exception:
            return False

    @staticmethod
    def _as_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_web_sequence_config(self):
        """Get the current web sequence configuration from the node"""
        return {
            'session_file': self.get_property('session_file'),
            'headless': str(self.get_property('headless') or 'false').strip().lower() in ("true", "1", "yes", "y", "on"),
            'speed': float(self.get_property('speed') or 1.0),
            'native_actions': str(self.get_property('native_actions') or 'false').strip().lower() in ("true", "1", "yes", "y", "on"),
            'loop_count': int(self.get_property('loop_count') or 1),
            'extra_delay': float(self.get_property('extra_delay') or 1.0),
            'repeat_mode': str(self.get_property('repeat_mode') or 'count'),
            'extract_items': self._parse_extract_items(self.get_property('extract_items')),
            'description': str(self.get_property('description') or ''),
        }

    @staticmethod
    def _parse_extract_items(value):
        """Parse the extract_items property (JSON list string) into a list."""
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value or '[]')
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
