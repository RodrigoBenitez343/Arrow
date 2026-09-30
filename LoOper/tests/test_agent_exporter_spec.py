"""Unit tests for builder/agent_exporter.py spec generation.

Verifies that the generated PyInstaller spec bundles the SAME llama.cpp
runtime that agent mode uses in the Arrow app: the full AI/bin set
(including the Vulkan backend DLL), the embeddinggemma GGUF, and a
patched config.json whose model references are relative to the bundle.

The project-root discovery is monkeypatched to a temp tree so the tests
are hermetic.
"""

import json
import os

import pytest

from builder import agent_exporter as ae


@pytest.fixture
def export_project(tmp_path, monkeypatch):
    """Fake project tree with LoOper/{AI/bin, AI/models, chains, sequences}."""
    root = tmp_path
    looper = root / "LoOper"
    ai = looper / "AI"
    models = ai / "models"
    bins = ai / "bin"
    chains = looper / "chains"
    models.mkdir(parents=True)
    bins.mkdir(parents=True)
    chains.mkdir(parents=True)
    (looper / "sequences").mkdir(parents=True)
    monkeypatch.setattr(ae, "_find_project_root", lambda: str(root))
    monkeypatch.setattr(
        ae.AgentExporter, "_find_project_root_for_spec", lambda self: str(root)
    )
    return {
        "root": root,
        "ai": ai,
        "models": models,
        "bins": bins,
        "chains": chains,
    }


def _export(project, bin_names=None, chain_name="system.json"):
    """Write fake AI/bin + system chain, run collect_dependencies() +
    generate_spec(), and return (spec_text, exporter)."""
    if bin_names is not None:
        for n in bin_names:
            (project["bins"] / n).write_bytes(b"BIN")
    chain = project["chains"] / chain_name
    chain.write_text(
        json.dumps(
            {
                "llm_nodes": [],
                "chain_import_nodes": [],
                "conditional_nodes": [],
                "sequences": [],
            }
        ),
        encoding="utf-8",
    )
    exporter = ae.AgentExporter(
        system_chain_path=str(chain),
        output_dir=str(project["root"] / "dist"),
        exe_name="Agent",
    )
    exporter.collect_dependencies()
    spec_path = exporter.generate_spec()
    with open(spec_path, "r", encoding="utf-8") as f:
        return f.read(), exporter


# ---------------------------------------------------------------------------
# llama.cpp runtime bundling (agent-mode parity, incl. Vulkan)
# ---------------------------------------------------------------------------


def test_generate_spec_bundles_llamacpp_vulkan_binaries(export_project):
    spec, _ = _export(
        export_project,
        bin_names=[
            "llama-server.exe",
            "llama-server-impl.dll",
            "ggml-vulkan.dll",
            "ggml-cpu.dll",
            "ggml-base.dll",
            "llama-common.dll",
            "llama.dll",
            "mtmd.dll",
            "llama-mtmd-cli.exe",
        ],
    )
    # Every AI/bin file must ship to 'LoOper/AI/bin' — the first directory
    # LlamaCppEngine._find_server_executable probes in frozen builds
    # (_MEIPASS/LoOper/AI/bin/llama-server.exe).
    for fname in (
        "llama-server.exe",
        "llama-server-impl.dll",
        "ggml-vulkan.dll",
        "ggml-cpu.dll",
        "llama-mtmd-cli.exe",
    ):
        assert f"{fname}', 'LoOper/AI/bin')" in spec, f"{fname} not bundled"


def test_generate_spec_warns_without_vulkan_dll(export_project, caplog):
    with caplog.at_level("WARNING", logger="builder.agent_exporter"):
        _export(export_project, bin_names=["llama-server.exe", "ggml-cpu.dll"])
    assert "ggml-vulkan.dll" in caplog.text
    assert "CPU-only" in caplog.text


def test_generate_spec_warns_without_bin_dir(export_project, caplog):
    import shutil

    shutil.rmtree(export_project["bins"])
    with caplog.at_level("WARNING", logger="builder.agent_exporter"):
        _export(export_project, bin_names=[])
    assert "LoOper/AI/bin not found" in caplog.text


# ---------------------------------------------------------------------------
# Embedding model bundling
# ---------------------------------------------------------------------------


def test_generate_spec_bundles_embedding_gguf(export_project):
    (export_project["models"] / "embeddinggemma-300M-Q8_0.gguf").write_bytes(b"GGUF")
    spec, _ = _export(export_project, bin_names=["llama-server.exe"])
    assert (
        "embeddinggemma-300M-Q8_0.gguf', 'LoOper/AI/models')" in spec
    ), "embedding GGUF not bundled to LoOper/AI/models"


def test_resolve_embedding_gguf_path_default(export_project):
    model = export_project["models"] / "embeddinggemma-300M-Q8_0.gguf"
    model.write_bytes(b"GGUF")
    assert ae._resolve_embedding_gguf_path() == str(model)


def test_resolve_embedding_gguf_path_missing_returns_none(export_project):
    assert ae._resolve_embedding_gguf_path() is None


def test_resolve_embedding_gguf_path_absolute_from_config(export_project):
    ext = export_project["models"] / "custom-embed.gguf"
    ext.write_bytes(b"GGUF")
    (export_project["ai"] / "config.json").write_text(
        json.dumps({"LLAMA_CPP": {"embedding_model_path": str(ext)}}),
        encoding="utf-8",
    )
    assert ae._resolve_embedding_gguf_path() == str(ext)


# ---------------------------------------------------------------------------
# Bundled config.json parity (relative model refs, unchanged engine settings)
# ---------------------------------------------------------------------------


def test_generate_spec_config_patch_makes_model_paths_relative(export_project):
    cfg = {
        "LLAMA_CPP": {
            "model_path": str(export_project["models"] / "SmolLM3-Q4_K_M.gguf"),
            "embedding_model_path": str(
                export_project["models"] / "embeddinggemma-300M-Q8_0.gguf"
            ),
            "gpu_layers": 0,
            "threads": -1,
        }
    }
    (export_project["ai"] / "config.json").write_text(
        json.dumps(cfg), encoding="utf-8"
    )
    (export_project["models"] / "SmolLM3-Q4_K_M.gguf").write_bytes(b"GGUF")
    (export_project["models"] / "embeddinggemma-300M-Q8_0.gguf").write_bytes(b"GGUF")
    _, exporter = _export(export_project, bin_names=["llama-server.exe"])

    patched_path = os.path.join(exporter._build_dir, "config.json")
    with open(patched_path, "r", encoding="utf-8") as f:
        patched = json.load(f)
    llm = patched["LLAMA_CPP"]
    assert llm["model_path"] == "SmolLM3-Q4_K_M.gguf"
    assert llm["embedding_model_path"] == "embeddinggemma-300M-Q8_0.gguf"
    # Engine settings must survive the patch untouched (agent-mode parity).
    assert llm["gpu_layers"] == 0
    assert llm["threads"] == -1


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------


def test_generate_spec_hiddenimports_embedding_server(export_project):
    spec, _ = _export(export_project, bin_names=["llama-server.exe"])
    assert "'AI.embedding_server'" in spec


# ---------------------------------------------------------------------------
# Orchestrator root + brains — export parity for the v2 node surface
# ---------------------------------------------------------------------------


def test_export_bundles_orchestrator_brains_with_relative_paths(export_project):
    """A chain_import whose chain_file_path is RELATIVE (like the shipped
    ORCHESTRATOR.json brains) must still be collected — the exporter resolves
    it against the importing chain's own directory, the same fallback the
    player uses at runtime, and the spec bundles every chain file."""
    chains = export_project["chains"]
    (chains / "BRAIN_A.json").write_text(json.dumps({
        "name": "Brain A", "collection": "Brains",
        "input_nodes": [{
            "type": "input", "label": "q", "node_id": "a1",
            "question_mode": "yes_no", "route_on_answer": True,
        }],
        "output_nodes": [],
    }), encoding="utf-8")
    (chains / "BRAIN_B.json").write_text(json.dumps({
        "name": "Brain B", "collection": "Brains", "output_nodes": [],
    }), encoding="utf-8")

    system = chains / "system.json"
    system.write_text(json.dumps({
        "name": "Root", "collection": "System", "is_default": True,
        "orchestrator_nodes": [{
            "type": "orchestrator", "description": "route", "max_steps": 15,
            "picker": "llm", "node_id": "o1",
        }],
        "chain_import_nodes": [
            {"type": "chain_import", "label": "A",
             "chain_file_path": "BRAIN_A.json", "node_id": "c1"},
            {"type": "chain_import", "label": "B",
             "chain_file_path": "BRAIN_B.json", "node_id": "c2"},
        ],
    }), encoding="utf-8")

    exporter = ae.AgentExporter(
        system_chain_path=str(system),
        output_dir=str(export_project["root"] / "dist"),
        exe_name="Agent",
    )
    deps = exporter.collect_dependencies()
    names = {os.path.basename(p) for p in deps["chains"]}
    assert names == {"system.json", "BRAIN_A.json", "BRAIN_B.json"}

    spec_path = exporter.generate_spec()
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = f.read()
    for name in ("BRAIN_A.json", "BRAIN_B.json"):
        assert name in spec, f"{name} not bundled into the spec"


def test_export_bundles_shipped_orchestrator_and_brains(tmp_path):
    """The shipped ORCHESTRATOR.json exports with every chain it references,
    transitively (router brains AND the atomic tool chains inside them).
    Discovery-based: the set of referenced chains is derived from the file
    itself, so rewiring the root never breaks or silently weakens this."""
    from builder import agent_exporter as _ae

    chains_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chains",
    )
    root = os.path.join(chains_dir, "ORCHESTRATOR.json")

    # Every chain referenced, transitively, from the shipped root.
    # Cycle-safe: chains may import themselves (jobsearch.json does — the
    # self-callback pattern), so a visited set is mandatory.
    wanted = set()
    visited = set()
    queue = [root]
    while queue:
        path = queue.pop()
        if path in visited:
            continue
        visited.add(path)
        wanted.add(os.path.basename(path))
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            continue
        for cin in (cfg.get("chain_import_nodes") or []):
            ref = str(cin.get("chain_file_path") or "")
            if not ref:
                continue
            cand = os.path.join(chains_dir, os.path.basename(ref))
            if os.path.exists(cand) and cand not in visited:
                queue.append(cand)

    assert len(wanted) > 1, "shipped root references no chains?"

    exporter = _ae.AgentExporter(
        system_chain_path=root,
        output_dir=str(tmp_path / "dist"),
        exe_name="Agent",
    )
    deps = exporter.collect_dependencies()
    names = {os.path.basename(p) for p in deps["chains"]}
    missing = wanted - names
    assert not missing, f"exporter missed chain files: {sorted(missing)}"
