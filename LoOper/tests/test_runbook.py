"""Runbook — completeness, docs sync, and the builder-chat views.

Run:  python -m pytest LoOper/tests/test_runbook.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from player.builder import node_templates as nt  # noqa: E402
from player.builder import runbook as rb  # noqa: E402


def test_every_creatable_type_is_documented():
    for node_type in nt.NODE_TYPES:
        entry = rb.RUNBOOK.get(node_type)
        assert entry, "missing runbook entry: %s" % node_type
        assert entry["purpose"].strip()
        assert entry["ports_in"] is not None
        assert entry["ports_out"] is not None
        assert entry["when_to_use"], node_type
        assert entry["when_to_ask"], node_type


def test_declined_types_are_documented_too():
    for node_type in nt.DECLINED_TYPES:
        assert node_type in rb.RUNBOOK


def test_edit_allowlists_are_documented():
    """Every builder-editable field is described in the runbook entry."""
    for node_type, fields in nt.NODE_EDIT_FIELDS.items():
        documented = set((rb.RUNBOOK.get(node_type) or {}).get("fields") or {})
        missing = set(fields) - documented
        assert not missing, (node_type, sorted(missing))


def test_node_purposes_is_a_view_over_the_runbook():
    assert set(nt.NODE_PURPOSES) == {
        "input", "output", "llm", "code", "conditional", "chain_import",
        "sequence", "web_sequence", "form_filler", "handle", "context",
        "mcp", "container",
    }
    for node_type, purpose in nt.NODE_PURPOSES.items():
        assert purpose == rb.purpose_line(node_type)


def test_docs_file_is_in_sync():
    """docs/NODE_RUNBOOK.md must be byte-equal to render_markdown()."""
    path = rb._doc_path()
    assert os.path.isfile(path), "run `python -m player.builder.runbook --write`"
    with open(path, encoding="utf-8") as f:
        on_disk = f.read()
    assert on_disk == rb.render_markdown()


def test_criteria_carry_purpose_and_usage():
    text = rb.criteria_for("llm")
    assert "orchestrator_mode" in text
    assert "Use when:" in text
    assert len(text) <= 400
    assert rb.criteria_for("bogus") == ""


def test_provider_note_lists_model_fields():
    joined = " ".join(rb.PROVIDER_NOTE)
    assert "provider:model" in joined
    pairs = rb.model_using_fields()
    assert "llm.model" in pairs and "input.decision_model" in pairs
    for pair in pairs:
        node_type, field = pair.split(".", 1)
        documented = (rb.RUNBOOK.get(node_type) or {}).get("fields") or {}
        assert field in documented, pair


def test_every_creatable_type_documents_its_modes():
    """The builder must know how a node's MODES change its behavior:
    llm vanilla vs orchestrator, input passthrough vs decision, web vs
    desktop conditional, desktop click strategies, and so on."""
    for node_type in nt.NODE_TYPES:
        entry = rb.RUNBOOK[node_type]
        modes = entry.get("modes")
        assert modes, "no modes documented: %s" % node_type
        assert all(str(v).strip() for v in modes.values()), node_type


def test_modes_render_and_ride_the_builder_prompts():
    """Modes are rendered into the doc, carried by Laya's criteria, and
    included in the cheat sheet the builder model's prompts get."""
    md = rb.render_markdown()
    assert "**Modes:**" in md
    sheet = rb.cheat_sheet()
    for token in ("orchestrator", "passthrough", "brains", "tools",
                  "decision"):
        assert token in sheet, token
    assert len(sheet) <= 3000
    crit = rb.criteria_for("input")
    assert "Modes:" in crit and "passthrough" in crit
    assert len(crit) <= 400
