"""Form Filling node — fills ONE PAGE of a form with a small language model.

The node enumerates the form's fields deterministically (live DOM for web;
vision + LayoutLMv3 for desktop) and fills them ONE FIELD AT A TIME.  Each
field's value is grounded by probe-based retrieval over the connected source
documents; the RUNTIME owns the field->value binding.  The model only ever
returns a single value for a single field (or a ``SKIP`` sentinel).

Multi-page / wizard flows are composed by chaining several Form Filling nodes
with Conditionals — this node contains no wizard logic.
"""

from NodeGraphQt import BaseNode
from .base_node import _add_multi_input
from ..constants import (
    FORM_FILLER_COLOR,
    INPUT_PORT_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE,
)

# Explicit per-node substrate (no auto-detection).
VALID_MODES = ("web", "desktop")


def _bool(text) -> bool:
    return str(text).strip().lower() in ("true", "1", "yes", "on")


class FormFillerNode(BaseNode):
    """Fill one page of a form: enumerate fields, then fill them one by one."""

    __identifier__ = 'form_filler'
    NODE_NAME = 'Form Filling'

    def __init__(self):
        super(FormFillerNode, self).__init__()

        # Darken the color for text readability (keeps the node's identity hue)
        base_color = FORM_FILLER_COLOR
        r, g, b = (int(base_color.strip('#')[i:i + 2], 16) for i in (0, 2, 4))
        self.set_color(int(r * 0.25), int(g * 0.25), int(b * 0.25))

        try:
            self.set_text_color(255, 255, 255)
        except Exception:
            pass
        try:
            self.set_font_size(14)
        except Exception:
            pass

        in_rgb = tuple(int(INPUT_PORT_COLOR.strip('#')[i:i + 2], 16) for i in (0, 2, 4))
        out_rgb = tuple(int(OUTPUT_PORT_COLOR.strip('#')[i:i + 2], 16) for i in (0, 2, 4))

        # Input is optional (upstream trigger); output flows on.
        input_port = _add_multi_input(self, 'input', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        output_port = self.add_output('output', color=out_rgb, display_name=True, multi_output=True)
        # Context pool ports: 'ctx_in' reads the knowledge pool (a Context node
        # holding the source documents / prior state), 'ctx_out' publishes this
        # node's result so it can be stored back into the pool (mirrors the
        # Context node's own ctx_in/ctx_out).  The node NO LONGER attaches
        # documents to itself — the source is wired in from a Context node.
        ctx_in_port = _add_multi_input(self, 'ctx_in', in_rgb, True, data_type=UNIVERSAL_PORT_TYPE)
        ctx_out_port = self.add_output('ctx_out', color=out_rgb, display_name=True, multi_output=True)
        for p in (input_port, output_port, ctx_in_port, ctx_out_port):
            try:
                if hasattr(p, 'set_multi_connection'):
                    p.set_multi_connection(True)
                else:
                    setattr(p, '_multi_connection', True)
            except Exception:
                pass

        # ── Properties ──
        # Substrate is EXPLICIT per node: 'web' (live DOM) or 'desktop' (vision).
        self.create_property('mode', 'web')
        # Goal text for the form (what to fill / from which source documents).
        self.create_property('instruction', 'Fill the form using the provided context.')
        # Optional label allow/deny lists (comma-separated, case-insensitive).
        self.create_property('fields_include', '')
        self.create_property('fields_skip', '')
        # Probe-based retrieval tuning (kept small for small-model windows).
        self.create_property('probe_top_k', '3')
        self.create_property('probe_char_budget', '1500')
        self.create_property('probe_context_chars', '6000')
        # Iterative ComoRAG retrieval cycles per field (1 = single-pass top-k;
        # more cycles sweep the corpus further for better-grounded answers).
        # ``consolidate`` gates whether the ComoRAG engine is used at all.
        self.create_property('consolidate', 'true')
        self.create_property('probe_cycles', '3')
        # Verify each field after writing (re-ask that field once on mismatch).
        self.create_property('verify', 'true')
        # Post-pass: re-check every written field's NATIVE validity and repair
        # the values the page rejected (format / constraint failures).
        self.create_property('repair', 'true')
        self.create_property('repair_attempts', '2')
        # Answer "No" to a yes/no question when the evidence grounds no "Yes".
        self.create_property('answer_no', 'true')
        # LAST-RESORT: write "N/A" for a field that genuinely cannot be answered
        # (a text box asking for a photo / a file, an off-topic option list, or a
        # source with nothing about it), instead of leaving the page blank.
        self.create_property('answer_na', 'true')
        # Ask the user directly (like an Input node) when the context holds
        # nothing for a field: the fill PAUSES for the answer, which then fills
        # the field AND is learned into this node's own corrections file.
        self.create_property('ask_user', 'false')
        # NOTE: there is NO knowledge/corrections PATH property.  The node OWNS
        # its corrections file and derives the location itself at runtime
        # (`_ff_corrections_path`: beside the chain, else the runtime dir), so
        # nothing asks the user to point at a file.
        # Safety cap on the number of fields processed per run.
        self.create_property('max_fields', '40')
        # Extraction LLM settings.
        self.create_property('engine', 'llamacpp')  # 'llamacpp' | 'ollama'
        self.create_property('model', '')
        self.create_property('temperature', '0.1')
        # Budget for the FINAL ANSWER only — the engine adds the chain of
        # thought's own headroom on top, so a reasoning model cannot spend this
        # allowance on thinking and truncate the value mid-sentence.
        self.create_property('max_tokens', '1024')
        # Explicit context window for the llama.cpp engine (0 = engine auto,
        # which is RAM-aware and can cap HARD on a low-RAM box - and then every
        # prompt is rejected with exceed_context_size_error).
        self.create_property('context_size', '0')
        # Typing pacing (desktop write path reuses these).
        self.create_property('typing_batch_size', '20')
        self.create_property('typing_batch_delay', '0.05')
        # Optional picked page-scope container (web): a JSON picker result
        # ({"locator": {...}, ...}) that confines field enumeration to that
        # element (a form / modal / iframe) so page chrome behind the current
        # frame is never read.  '' = whole document.
        self.create_property('web_scope', '')

    def get_form_filler_config(self):
        """Return the serialisable configuration for this node."""
        mode = (self.get_property('mode') or 'web')
        mode = mode if mode in VALID_MODES else 'web'
        return {
            'mode': mode,
            'instruction': self.get_property('instruction') or '',
            'fields_include': self.get_property('fields_include') or '',
            'fields_skip': self.get_property('fields_skip') or '',
            'probe_top_k': int(self.get_property('probe_top_k') or 3),
            'probe_char_budget': int(self.get_property('probe_char_budget') or 1500),
            'probe_context_chars': int(self.get_property('probe_context_chars') or 6000),
            'probe_cycles': int(self.get_property('probe_cycles') or 3),
            'consolidate': _bool(self.get_property('consolidate') if self.get_property('consolidate') is not None else 'true'),
            'verify': _bool(self.get_property('verify') if self.get_property('verify') is not None else 'true'),
            'repair': _bool(self.get_property('repair') if self.get_property('repair') is not None else 'true'),
            'repair_attempts': int(self.get_property('repair_attempts') or 2),
            'answer_no': _bool(self.get_property('answer_no') if self.get_property('answer_no') is not None else 'true'),
            'answer_na': _bool(self.get_property('answer_na') if self.get_property('answer_na') is not None else 'true'),
            'ask_user': _bool(self.get_property('ask_user') if self.get_property('ask_user') is not None else 'false'),
            'max_fields': int(self.get_property('max_fields') or 40),
            'engine': self.get_property('engine') or 'llamacpp',
            'model': self.get_property('model') or '',
            'temperature': float(self.get_property('temperature') or 0.1),
            'max_tokens': int(self.get_property('max_tokens') or 1024),
            'context_size': int(self.get_property('context_size') or 0),
            'typing_batch_size': int(self.get_property('typing_batch_size') or 20),
            'typing_batch_delay': float(self.get_property('typing_batch_delay') or 0.05),
            'web_scope': self.get_property('web_scope') or '',
        }
