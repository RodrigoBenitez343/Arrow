"""Unit tests for builder/agent_exporter.py dependency-collection helpers.

The project-root discovery is monkeypatched to a temp tree so tests are
hermetic; one test exercises the real repository layout for the genuine
LoOper/sequences directory.
"""

import json
import os

import pytest

from builder import agent_exporter as ae


@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Create LoOper/{AI/models,sequences,chains} under tmp_path and point
    _find_project_root at it."""
    root = tmp_path
    models = root / "LoOper" / "AI" / "models"
    models.mkdir(parents=True)
    sequences = root / "LoOper" / "sequences"
    sequences.mkdir(parents=True)
    chains = root / "LoOper" / "chains"
    chains.mkdir(parents=True)
    monkeypatch.setattr(ae, "_find_project_root", lambda: str(root))
    return {"root": root, "models": models, "sequences": sequences, "chains": chains}


def _write_sequence(path, name="seq.json"):
    p = path / name
    p.write_text(json.dumps({"actions": []}), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Shape validation
# ---------------------------------------------------------------------------


def test_is_sequence_shape(fake_project, tmp_path):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    chain = tmp_path / "c.json"
    chain.write_text(json.dumps({"sequences": []}), encoding="utf-8")
    assert ae._is_sequence_shape(str(seq)) is True
    assert ae._is_sequence_shape(str(chain)) is False
    assert ae._is_sequence_shape(str(tmp_path / "nope.json")) is False


# ---------------------------------------------------------------------------
# Sequence resolution
# ---------------------------------------------------------------------------


def test_resolve_sequence_path_prefers_sequences_dir(fake_project):
    _write_sequence(fake_project["sequences"], "CLOSE.json")
    # Same name exists in chains dir but chain-shaped
    (fake_project["chains"] / "CLOSE.json").write_text(
        json.dumps({"sequences": []}), encoding="utf-8")
    resolved = ae._resolve_sequence_path("CLOSE.json", str(fake_project["chains"]))
    assert resolved == str(fake_project["sequences"] / "CLOSE.json")


def test_resolve_sequence_path_chain_dir_fallback(fake_project):
    chain_dir = fake_project["chains"]
    seq = chain_dir / "local.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    resolved = ae._resolve_sequence_path("local.json", str(chain_dir))
    assert resolved == str(seq)


def test_resolve_sequence_path_absolute_shape_validated(fake_project, tmp_path):
    seq = tmp_path / "abs.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    assert ae._resolve_sequence_path(str(seq), str(tmp_path)) == str(seq)
    chain = tmp_path / "chain.json"
    chain.write_text(json.dumps({"sequences": []}), encoding="utf-8")
    assert ae._resolve_sequence_path(str(chain), str(tmp_path)) is None


def test_resolve_sequence_path_empty(fake_project):
    assert ae._resolve_sequence_path("", "x") is None


# ---------------------------------------------------------------------------
# Model collection
# ---------------------------------------------------------------------------


def test_collect_models_from_chain(fake_project):
    model_file = fake_project["models"] / "model.gguf"
    model_file.write_bytes(b"GGUF")
    config = {
        "llm_nodes": [
            {"llamacpp_model_path": "model.gguf"},              # resolves via models dir
            {"llamacpp_model_path": ""},                        # skipped
            {"llamacpp_model_path": "missing.gguf"},            # skipped (no file)
        ],
    }
    found = ae._collect_models_from_chain(config, str(fake_project["chains"]))
    assert found == {str(model_file)}


def test_collect_models_relative_to_chain_dir(fake_project):
    local = fake_project["chains"] / "local.gguf"
    local.write_bytes(b"GGUF")
    config = {"llm_nodes": [{"llamacpp_model_path": "local.gguf"}]}
    found = ae._collect_models_from_chain(config, str(fake_project["chains"]))
    assert found == {str(local)}


def test_collect_models_recursive(fake_project):
    model = fake_project["models"] / "m.gguf"
    model.write_bytes(b"GGUF")
    sub_chain = fake_project["chains"] / "sub.json"
    sub_chain.write_text(json.dumps({
        "llm_nodes": [{"llamacpp_model_path": "m.gguf"}],
    }), encoding="utf-8")
    system_chain = fake_project["chains"] / "system.json"
    system_chain.write_text(json.dumps({
        "llm_nodes": [{"llamacpp_model_path": "m.gguf"}],
        "chain_import_nodes": [{"chain_file_path": str(sub_chain)}],
    }), encoding="utf-8")
    found = ae.collect_models_recursive(str(system_chain))
    assert found == {str(model)}


def test_collect_models_recursive_cycle_safe(fake_project):
    a = fake_project["chains"] / "a.json"
    b = fake_project["chains"] / "b.json"
    a.write_text(json.dumps({"chain_import_nodes": [{"chain_file_path": str(b)}]}), encoding="utf-8")
    b.write_text(json.dumps({"chain_import_nodes": [{"chain_file_path": str(a)}]}), encoding="utf-8")
    assert ae.collect_models_recursive(str(a)) == set()


# ---------------------------------------------------------------------------
# Screenshot collection
# ---------------------------------------------------------------------------


def test_resolve_screenshot_path(fake_project):
    screenshots = fake_project["sequences"] / "screenshots"
    screenshots.mkdir()
    img = screenshots / "target.png"
    img.write_bytes(b"PNG")
    assert ae._resolve_screenshot_path("target.png", str(fake_project["chains"])) == str(img)
    assert ae._resolve_screenshot_path("missing.png", str(fake_project["chains"])) is None


def test_collect_screenshots_from_chain(fake_project):
    screenshots = fake_project["sequences"] / "screenshots"
    screenshots.mkdir()
    img = screenshots / "cond.png"
    img.write_bytes(b"PNG")
    config = {
        "conditional_nodes": [
            {"image_path": "cond.png"},
            {"image_path": ""},
            {"image_path": "missing.png"},
        ],
    }
    found = ae._collect_screenshots_from_chain(config, str(fake_project["chains"]))
    assert found == {str(img)}


# ---------------------------------------------------------------------------
# Sequence collection from chains
# ---------------------------------------------------------------------------


def test_collect_sequences_from_chain_all_sources(fake_project):
    main_seq = _write_sequence(fake_project["sequences"], "main.json")
    import_seq = _write_sequence(fake_project["sequences"], "imported.json")
    loop_seq = _write_sequence(fake_project["sequences"], "loop.json")
    config = {
        "sequences": [{"sequence_file": "main.json"}],
        "chain_import_nodes": [{"sequences": [{"sequence_file": "imported.json"}]}],
        "conditional_nodes": [{"sequence_file": "loop.json"}],
    }
    found = ae._collect_sequences_from_chain(config, str(fake_project["chains"]))
    assert found == {main_seq, import_seq, loop_seq}


def test_real_repo_sequences_resolvable():
    """Invariance guard: every sequence referenced by name in chains/*.json
    must resolve to a real, sequence-shaped file in the actual repository."""
    root = ae._find_project_root()
    chains_dir = os.path.join(root, "LoOper", "chains")
    if not os.path.isdir(chains_dir):
        pytest.skip("chains dir not present")
    for fname in os.listdir(chains_dir):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(chains_dir, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            continue
        for seq in config.get("sequences", []):
            ref = (seq.get("sequence_file") or "").strip()
            if ref and not os.path.isabs(ref):
                resolved = ae._resolve_sequence_path(ref, chains_dir)
                assert resolved is not None, f"{fname} references unresolvable sequence {ref}"
                assert ae._is_sequence_shape(resolved)
