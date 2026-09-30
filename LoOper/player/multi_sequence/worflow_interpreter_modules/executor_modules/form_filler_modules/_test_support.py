"""Shared harness for the form-filler suite."""

import tempfile

from . import FormFillerMixin


class _Harness(FormFillerMixin):
    """Minimal executor: mixin + a dict-backed llm_executor interface."""

    def __init__(self):
        self._runtime_initialized = True
        self.workflow_graph = {}
        self._vars = {}
        self.llm_executor = self
        self.writes = []
        self.reads = []
        self.llm_calls = []
        self.probes = []
        # The corrections file is NODE-OWNED and derived from the chain dir, so
        # point it at a throwaway directory: a suite run must never write into
        # the real runtime/app-data location.
        self.sequence_executor = type(
            "S", (), {"chain_file_dir": tempfile.mkdtemp(prefix="ff_harness_")}
        )()

    # llm_executor duck-type
    def get_variable(self, key):
        return self._vars.get(key)

    def set_variable(self, key, value):
        self._vars[key] = value


def _node(mode="web", **overrides):
    data = {
        "mode": mode,
        "instruction": "fill the form",
        "verify": True,
        "max_fields": 40,
        "probe_top_k": 3,
        "probe_char_budget": 1500,
        "engine": "llamacpp",
        "model": "",
        "temperature": 0.1,
        "max_tokens": 256,
    }
    data.update(overrides)
    return {"id": "ff1", "data": data, "inputs": [], "connections": {"output": []}}


def _wire(h, fields, evidence="evidence text", value="My Value",
          reads=None, llm_value="My Value", current=""):
    """Wire stub enumeration/probe/llm/write/read onto the harness.

    ``current`` is the value the field ALREADY holds (the harness's own skip
    rule): "" means empty, so the field is filled as usual.
    """
    h._ff_enumerate_web = lambda cfg, sf: list(fields)
    h._ff_enumerate_desktop = lambda cfg, sf: list(fields)

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint=""):
        h.probes.append(label)
        return evidence
    h._ff_probe = _probe

    def _llm(prompt, system, cfg, sf):
        h.llm_calls.append(prompt)
        return llm_value
    h._ff_llm_call = _llm

    def _write(field, val, sf, driver=None):
        h.writes.append((field.get("id"), val))
        return True
    h._ff_write_web = _write
    h._ff_write_desktop = lambda field, val, cfg, sf: (h.writes.append((field.get("id"), val)), True)[1]

    seq = list(reads) if reads is not None else [value]

    def _read(field, sf, driver=None):
        h.reads.append(field.get("id"))
        return seq.pop(0) if seq else value
    h._ff_read_web = _read
    # The pre-fill read (the harness's "already filled -> skip" rule).
    h._ff_current_value = lambda field, sf, driver=None, **kw: current

    # The repair pass reads each field's native validity through the same
    # substrate; default to "valid" so no browser is touched unless a test
    # overrides it.
    h._ff_field_state = lambda field, sf, driver=None: {
        "found": True, "valid": True, "message": ""}
