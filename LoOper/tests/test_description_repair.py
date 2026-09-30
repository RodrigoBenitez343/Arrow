"""description_repair: penalty ladder, tree discovery, safe rewrite pipeline.

Run:  python -m pytest LoOper/tests/test_description_repair.py
"""

import json
import os

import player.agentic_ops.description_repair as dr

OLD = "this tool fills a job posting on linkedin autonomously"
NEW = ("Fills the LinkedIn Easy Apply form only when it is already open on the "
       "page; requires the linkedin tool to have opened LinkedIn first. Never "
       "use it to open LinkedIn, search jobs, or find postings.")


def _chains_dir(tmp_path):
    d = tmp_path / "chains"
    d.mkdir()
    (d / "repairtest.json").write_text(
        '{\n  "collection": "",\n  "description": "%s"\n}\n' % OLD, encoding="utf-8")
    (d / "linkedin.json").write_text(
        json.dumps({"description": "Opens linkedin.com and navigates to Jobs."}),
        encoding="utf-8")
    (d / "jobsearch.json").write_text(
        json.dumps({"description": "Scans the open jobs list for Easy Apply."}),
        encoding="utf-8")
    # A router referencing both tools -> they become siblings for targeting.
    (d / "brain.json").write_text(json.dumps({
        "chain_import_nodes": [
            {"chain_file_path": str(d / "linkedin.json"), "prefix": "linkedin"},
            {"chain_file_path": str(d / "repairtest.json"), "prefix": "repairtest"},
        ],
    }), encoding="utf-8")
    return d


def test_penalty_weight_ladder():
    assert dr.penalty_weight(10) == 0
    assert dr.penalty_weight(9) == 0
    assert dr.penalty_weight(8) == 1
    assert dr.penalty_weight(5) == 4
    assert dr.penalty_weight(1) == 8
    assert dr.penalty_weight("junk") == 0


def test_no_penalty_records_only(tmp_path, monkeypatch):
    d = _chains_dir(tmp_path)
    monkeypatch.setenv("LOOPER_DESC_FEEDBACK_LOG", str(tmp_path / "fb.jsonl"))
    monkeypatch.setattr(dr, "_call_local_model",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("model must not run on 9-10")))
    ev = dr.repair_description(str(d / "repairtest.json"), "repairtest", 9)
    assert ev["updated"] is False and "no penalty" in ev["reason"]
    assert json.loads((d / "repairtest.json").read_text(encoding="utf-8"))["description"] == OLD
    assert os.path.exists(str(tmp_path / "fb.jsonl"))


def test_penalty_rewrites_rated_chain(tmp_path, monkeypatch):
    d = _chains_dir(tmp_path)
    monkeypatch.setenv("LOOPER_DESC_FEEDBACK_LOG", str(tmp_path / "fb.jsonl"))
    monkeypatch.delenv("LOOPER_DESC_REPAIR", raising=False)
    monkeypatch.setattr(dr, "_call_local_model",
                        lambda prompt, system, model, max_tokens=220:
                        "FILE: repairtest.json\nDESCRIPTION: " + NEW)
    ev = dr.repair_description(str(d / "repairtest.json"), "repairtest", 2,
                               feedback_text="you filled a form, I said open LinkedIn",
                               goal_text="open linkedin")
    assert ev["updated"] is True
    text = (d / "repairtest.json").read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert parsed["description"] == NEW
    assert parsed["collection"] == "", "rest of the chain file must be preserved"
    audit = (tmp_path / "fb.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(audit) == 1 and json.loads(audit[0])["updated"] is True


def test_penalty_can_target_a_candidate_chain(tmp_path, monkeypatch):
    d = _chains_dir(tmp_path)
    monkeypatch.setenv("LOOPER_DESC_FEEDBACK_LOG", str(tmp_path / "fb.jsonl"))
    monkeypatch.setattr(dr, "_call_local_model",
                        lambda prompt, system, model, max_tokens=220:
                        "FILE: linkedin.json\nDESCRIPTION: " + NEW)
    ev = dr.repair_description(str(d / "repairtest.json"), "repairtest", 2,
                               feedback_text="the linkedin opener should have run instead")
    assert ev["updated"] is True
    assert os.path.basename(ev["target"]) == "linkedin.json"
    assert json.loads((d / "linkedin.json").read_text(encoding="utf-8"))[
        "description"] == NEW
    assert json.loads((d / "repairtest.json").read_text(encoding="utf-8"))[
        "description"] == OLD


def test_unknown_target_falls_back_to_rated_chain(tmp_path, monkeypatch):
    d = _chains_dir(tmp_path)
    monkeypatch.setenv("LOOPER_DESC_FEEDBACK_LOG", str(tmp_path / "fb.jsonl"))
    monkeypatch.setattr(dr, "_call_local_model",
                        lambda prompt, system, model, max_tokens=220:
                        "FILE: somewhere-else.json\nDESCRIPTION: " + NEW)
    ev = dr.repair_description(str(d / "repairtest.json"), "repairtest", 3)
    assert ev["updated"] is True
    assert os.path.basename(ev["target"]) == "repairtest.json"


def test_invalid_rewrite_is_rejected(tmp_path, monkeypatch):
    d = _chains_dir(tmp_path)
    monkeypatch.setenv("LOOPER_DESC_FEEDBACK_LOG", str(tmp_path / "fb.jsonl"))
    monkeypatch.setattr(dr, "_call_local_model",
                        lambda prompt, system, model, max_tokens=220:
                        "FILE: repairtest.json\nDESCRIPTION: short")
    ev = dr.repair_description(str(d / "repairtest.json"), "repairtest", 2)
    assert ev["updated"] is False and "rejected" in ev["reason"]
    assert json.loads((d / "repairtest.json").read_text(encoding="utf-8"))[
        "description"] == OLD


def test_kill_switch(tmp_path, monkeypatch):
    d = _chains_dir(tmp_path)
    monkeypatch.setenv("LOOPER_DESC_FEEDBACK_LOG", str(tmp_path / "fb.jsonl"))
    monkeypatch.setenv("LOOPER_DESC_REPAIR", "off")
    monkeypatch.setattr(dr, "_call_local_model",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("model must not run when disabled")))
    ev = dr.repair_description(str(d / "repairtest.json"), "repairtest", 1)
    assert ev["updated"] is False and "off" in ev["reason"]


def test_tree_context_finds_router_siblings(tmp_path):
    d = _chains_dir(tmp_path)
    ctx = dr.tree_context(str(d / "repairtest.json"))
    bases = {os.path.basename(c["path"]) for c in ctx}
    assert "linkedin.json" in bases
    assert "repairtest.json" not in bases


def test_parse_reply_multiline_and_quotes():
    fb, desc = dr._parse_reply(
        'FILE: repairtest.json\nDESCRIPTION: "Opens LinkedIn... never fills '
        'forms unless a form is already open."')
    assert fb == "repairtest.json"
    assert desc.startswith("Opens LinkedIn...")
    assert '"' not in desc
