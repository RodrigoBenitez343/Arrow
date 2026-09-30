"""agent_exporter.py — Build orchestration for standalone agent executables.

Collects transitive chain dependencies, generates a PyInstaller .spec file,
and runs the build via subprocess.  Designed for use from the AgentExportDialog
but callable programmatically too.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── Constants ──────────────────────────────────────────────────────────
HIDDEN_IMPORTS = [
    # AI API
    "AI.api", "AI.config_loader", "AI.model_cache",
    "AI.llama_cpp_engine", "AI.ollama_engine",
    "AI.inprocess_transport", "AI.embedding_server",
    # Embedded Laya decision engine (imported lazily inside consumer functions;
    # without these the exported agent silently falls back to its LLM paths)
    "AI.laya_client", "AI.laya_hooks",
    # Agent-mode UI (overlay + floating dot, same as the full app)
    "NGUI.widgets.agent_overlay", "NGUI.widgets.agent_dot",
    "NGUI.widgets.agent_web_server",
    # Player / agentic ops
    "player.agentic_ops.simple_agent", "player.agentic_ops.chain_executor",
    "player.agentic_ops.run_memory", "player.agentic_ops.description_repair",
    "player.multi_sequence_player", "player.sequence_player",
    "player.base_bot", "player.config",
    "player.keyboard_monitor", "player.json_cache",
    "player.computer_vision", "player.image_utils",
    "player.action_handlers",
    "player.chain_migrations", "player.tts_service",
    "player.multi_sequence",
    "player.multi_sequence.player_utils",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.chain_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.conditional_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.sequence_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.llm_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.code_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.container_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.context_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.handle_ops",
    "player.multi_sequence.worflow_interpreter_modules.executor_modules.mcp_ops",
    "player.multi_sequence.worflow_interpreter_modules.graph_builder",
    "player.multi_sequence.worflow_interpreter_modules.graph_navigator",
    # Web automation (WebSequenceNode): DOM-based browser record/replay
    "player.web", "player.web.events", "player.web.session",
    "player.web.actions", "player.web.engine", "player.web.recorder",
    "undetected_chromedriver", "selenium", "keyboard",
    # Speech-to-text for desktop voice mode (overlay mic) + web STT
    "player.stt_engine", "player.tts_engine", "vosk", "pyaudio",
    # External
    "fastapi", "uvicorn", "pynput", "mss", "cv2", "requests",
    "PIL", "numpy", "pytweening", "mouseinfo",
    # Embeddings for tool retrieval & RAG — llama.cpp embeddinggemma GGUF
    # via AI.embedding_server (no sentence-transformers/transformers).
    "torch",
]

EXCLUDES = [
    "PyQt5.QtWebEngineWidgets", "PyQt5.QtWebEngine",
    "PyQt5.QtWebChannel", "PyQt5.QtMultimedia",
    "NodeGraphQt", "pyqtgraph",
    "paddleocr", "paddle", "paddlepaddle",
    "matplotlib", "tkinter",
    "NotoSans",
    # NGUI graph editor stack — the standalone agent only needs the
    # overlay/dot widgets; dialogs pull in nodes_resources → NodeGraphQt
    "NGUI.dialogs", "NGUI.nodes_resources", "NGUI.graph_elements",
    # Heavy ML stack — excluded unless user opts into vision/OCR
    # These crash the PyInstaller isolated subprocess on memory-constrained machines
    "onnx", "onnxruntime",
    "piper", "piper.tashkeel",
    "torchvision",
    "numba", "llvmlite",
]


def _find_project_root() -> str:
    """Find the LoOper project root (where LoOper/ directory lives)."""
    # Walk up from this file's location
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(10):
        if os.path.isdir(os.path.join(here, "LoOper", "AI")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return os.path.dirname(os.path.abspath(__file__))


def _is_sequence_shape(path: str) -> bool:
    """Return True when the JSON file at *path* has sequence shape.

    A valid sequence file carries an ``actions`` list.  Chain files
    (``chains/*.json``) carry chain-only fields and no ``actions`` — they
    must never be accepted as sequences.  This check prevents the
    chains/CLOSE.json-vs-sequences/CLOSE.json collision.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    return isinstance(data, dict) and isinstance(data.get("actions"), list)


def _resolve_embedding_gguf_path() -> Optional[str]:
    """Locate the llama.cpp embedding GGUF to bundle (embeddinggemma).

    Reads ``LLAMA_CPP.embedding_model_path`` from AI/config.json (default
    ``embeddinggemma-300M-Q8_0.gguf``) and resolves relative names under
    ``LoOper/AI/models`` — the same resolution the runtime uses in
    ``AI.embedding_server``.  Returns None when the file is missing locally.
    """
    name = "embeddinggemma-300M-Q8_0.gguf"
    try:
        cfg_path = os.path.join(
            _find_project_root(), "LoOper", "AI", "config.json"
        )
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        name = (
            (cfg.get("LLAMA_CPP", {}) or {}).get("embedding_model_path")
            or name
        ).strip()
    except Exception:
        pass
    path = (
        name
        if os.path.isabs(name)
        else os.path.join(_find_project_root(), "LoOper", "AI", "models", name)
    )
    return path if os.path.isfile(path) else None


def _resolve_sequence_path(seq_file: str, chain_dir: str) -> Optional[str]:
    """Resolve a sequence file reference to a REAL sequence file.

    Priority:
      1. The project's ``LoOper/sequences/<name>.json`` whenever the name
         exists there (the real sequences directory).
      2. Chain-dir candidates — accepted ONLY after sequence shape
         validation (an ``actions`` list), so a ``chains/*.json`` with the
         same name can never win over the real sequence.
    Logs which file was chosen.
    """
    if not seq_file:
        return None
    # If it's already absolute, accept it only when sequence-shaped
    if os.path.isabs(seq_file):
        if os.path.exists(seq_file) and _is_sequence_shape(seq_file):
            return seq_file
        logger.warning(
            "Sequence '%s' is absolute but not sequence-shaped — ignored",
            seq_file,
        )
        return None
    seq_dir = os.path.join(_find_project_root(), "LoOper", "sequences")
    # 1) Prefer the real sequences directory
    candidate = os.path.join(seq_dir, os.path.basename(seq_file))
    if os.path.exists(candidate) and _is_sequence_shape(candidate):
        logger.info(
            "Sequence '%s' resolved to %s (LoOper/sequences)",
            seq_file, candidate,
        )
        return os.path.normpath(candidate)
    # 2) Chain-dir candidate only with shape validation
    candidate = os.path.join(chain_dir, seq_file)
    if os.path.exists(candidate) and _is_sequence_shape(candidate):
        logger.info(
            "Sequence '%s' resolved to %s (chain dir, shape-validated)",
            seq_file, candidate,
        )
        return os.path.normpath(candidate)
    return None


def _collect_models_from_chain(chain_config: dict, chain_dir: str) -> Set[str]:
    """Collect model files referenced by llm_nodes in a chain config.

    Model paths can be relative (just a filename) or absolute.
    Returns a set of absolute paths to .gguf files that exist.
    """
    models_dir = os.path.join(_find_project_root(), "LoOper", "AI", "models")
    found: Set[str] = set()
    for node in chain_config.get("llm_nodes", []):
        model_rel = (node.get("llamacpp_model_path") or "").strip()
        if not model_rel:
            continue
        # Try relative to chain dir first
        candidate = os.path.join(chain_dir, model_rel)
        if os.path.isfile(candidate):
            found.add(os.path.normpath(candidate))
            continue
        # Try absolute
        if os.path.isabs(model_rel) and os.path.isfile(model_rel):
            found.add(os.path.normpath(model_rel))
            continue
        # Try models directory
        candidate = os.path.join(models_dir, os.path.basename(model_rel))
        if os.path.isfile(candidate):
            found.add(os.path.normpath(candidate))
    return found


def collect_models_recursive(system_chain_path: str) -> Set[str]:
    """Recursively collect model files from system chain and all transitive tool chains.

    Scans llm_nodes[].llamacpp_model_path in the system chain and every
    chain referenced via chain_import_nodes, returning absolute paths to
    existing .gguf files.
    """
    visited: Set[str] = set()
    found: Set[str] = set()
    queue: List[str] = [system_chain_path]

    while queue:
        chain_path = queue.pop(0)
        norm = os.path.normpath(chain_path)
        if norm in visited:
            continue
        if not os.path.exists(norm):
            continue
        visited.add(norm)
        chain_dir = os.path.dirname(norm)
        try:
            with open(norm, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception:
            continue
        found.update(_collect_models_from_chain(config, chain_dir))
        for cin in config.get("chain_import_nodes", []):
            sub_path = (cin.get("chain_file_path") or "").strip()
            if sub_path and os.path.exists(sub_path):
                queue.append(sub_path)
    return found


def _resolve_screenshot_path(img: str, chain_dir: str) -> Optional[str]:
    """Resolve a conditional-node image path (absolute or relative).

    Relative paths are resolved against the chain dir, then the project's
    ``LoOper/sequences/screenshots`` and ``LoOper/sequences`` directories.
    """
    if not img:
        return None
    if os.path.isabs(img):
        return os.path.normpath(img) if os.path.isfile(img) else None
    root = _find_project_root()
    for candidate in (
        os.path.join(chain_dir, img),
        os.path.join(root, "LoOper", "sequences", "screenshots", os.path.basename(img)),
        os.path.join(root, "LoOper", "sequences", img),
        os.path.join(root, "LoOper", "sequences", os.path.basename(img)),
    ):
        if os.path.isfile(candidate):
            return os.path.normpath(candidate)
    return None


def _collect_screenshots_from_chain(chain_config: dict, chain_dir: str) -> Set[str]:
    """Collect screenshot image paths from conditional nodes (absolute or relative)."""
    found: Set[str] = set()
    for node in chain_config.get("conditional_nodes", []):
        img = (node.get("image_path") or "").strip()
        path = _resolve_screenshot_path(img, chain_dir)
        if path:
            found.add(path)
    return found


def _collect_sequences_from_chain(chain_config: dict, chain_dir: str) -> Set[str]:
    """Collect sequence JSON files referenced anywhere in a chain config."""
    found: Set[str] = set()
    # Top-level sequences
    for seq in chain_config.get("sequences", []):
        path = _resolve_sequence_path(seq.get("sequence_file", ""), chain_dir)
        if path:
            found.add(path)
    # Sequences inside chain import nodes
    for cin in chain_config.get("chain_import_nodes", []):
        for seq in cin.get("sequences", []):
            path = _resolve_sequence_path(seq.get("sequence_file", ""), chain_dir)
            if path:
                found.add(path)
    # Loop-type conditional nodes reference a sequence file to run each iteration
    for node in chain_config.get("conditional_nodes", []):
        path = _resolve_sequence_path((node.get("sequence_file") or "").strip(), chain_dir)
        if path:
            found.add(path)
    return found


class AgentExporter:
    """Orchestrates a PyInstaller build for a standalone agent .exe.

    Usage::

        exporter = AgentExporter(
            system_chain_path="path/to/system_chain.json",
            output_dir="dist/my_agent",
            exe_name="MyAgent",
            model_files=["path/to/model.gguf"],
        )
        exporter.collect_dependencies()
        exporter.generate_spec()
        exporter.run_build(progress_callback=print)
    """

    def __init__(
        self,
        system_chain_path: str,
        output_dir: str,
        exe_name: str = "Agent",
        model_files: Optional[List[str]] = None,
        include_vision: bool = False,
        parent_widget=None,
    ):
        self.system_chain_path = os.path.normpath(system_chain_path)
        self.output_dir = os.path.normpath(output_dir)
        self.exe_name = exe_name
        self._extra_model_files = list(model_files or [])
        self._include_vision = include_vision
        self._parent_widget = parent_widget

        # Populated by collect_dependencies()
        self.chain_files: Set[str] = set()
        self.sequence_files: Set[str] = set()
        self.screenshot_files: Set[str] = set()
        self.model_files: Set[str] = set()
        self._build_dir: Optional[str] = None
        self._process: Optional[subprocess.Popen] = None
        self._cancel_event = threading.Event()

    # ── Dependency collection ──────────────────────────────────────────

    def collect_dependencies(self) -> Dict[str, list]:
        """Recursively resolve all transitive chain dependencies.

        Starting from the system chain, scans ``chain_import_nodes``
        to find all referenced tool chains, then recursively scans
        each of those.  Returns a dict of collected file paths.
        """
        self.chain_files.clear()
        self.sequence_files.clear()
        self.screenshot_files.clear()
        self.model_files.clear()

        visited: Set[str] = set()
        queue: List[str] = [self.system_chain_path]

        while queue:
            chain_path = queue.pop(0)
            norm = os.path.normpath(chain_path)
            if norm in visited:
                continue
            if not os.path.exists(norm):
                logger.warning("Chain file not found, skipping: %s", norm)
                continue
            visited.add(norm)
            self.chain_files.add(norm)

            chain_dir = os.path.dirname(norm)
            try:
                with open(norm, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception as e:
                logger.error("Failed to parse chain %s: %s", norm, e)
                continue

            # Collect sequences
            self.sequence_files.update(
                _collect_sequences_from_chain(config, chain_dir)
            )

            # Collect screenshots
            self.screenshot_files.update(
                _collect_screenshots_from_chain(config, chain_dir)
            )

            # Collect models from llm_nodes
            self.model_files.update(
                _collect_models_from_chain(config, chain_dir)
            )

            # Enqueue sub-chains (chain_import_nodes)
            for cin in config.get("chain_import_nodes", []):
                sub_path = (cin.get("chain_file_path") or "").strip()
                if not sub_path:
                    continue
                if not os.path.exists(sub_path):
                    # Relative reference: resolve against the chain's own
                    # directory — the same fallback the player uses for
                    # nested imports (frozen builds probe by basename too).
                    sub_alt = os.path.join(chain_dir, os.path.basename(sub_path))
                    if os.path.exists(sub_alt):
                        sub_path = sub_alt
                if os.path.exists(sub_path):
                    queue.append(sub_path)

        # Add any extra model files the user specified
        for mf in self._extra_model_files:
            if os.path.isfile(mf):
                self.model_files.add(os.path.normpath(mf))

        return {
            "chains": sorted(self.chain_files),
            "sequences": sorted(self.sequence_files),
            "screenshots": sorted(self.screenshot_files),
            "models": sorted(self.model_files),
        }

    # ── Spec generation ────────────────────────────────────────────────

    def generate_spec(self) -> str:
        """Write a dynamic PyInstaller .spec file to a temp build directory.

        Returns the absolute path to the generated .spec file.
        """
        self._build_dir = tempfile.mkdtemp(prefix="agent_build_")
        spec_path = os.path.join(self._build_dir, f"{self.exe_name}.spec")

        # Copy the standalone entry point to the build dir
        entry_src = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "agent_standalone_entry.py",
        )
        entry_dst = os.path.join(self._build_dir, "agent_standalone_entry.py")
        shutil.copy2(entry_src, entry_dst)

        project_root = self._find_project_root_for_spec()

        # ── Chain data hygiene: patch bundled chain files before bundling ──
        # 1. chain_file_path values -> basenames (relative resolution at
        #    runtime instead of build-machine absolute paths)
        # 2. placeholder sequences (empty 'actions') -> real sequence files
        # 3. re-encode as UTF-8 (fixes CP1252 mojibake from old exports)
        _patched_chains_dir = os.path.join(self._build_dir, "patched_chains")
        try:
            os.makedirs(_patched_chains_dir, exist_ok=True)
        except Exception:
            pass
        _real_sequences_dir = os.path.join(
            _find_project_root(), "LoOper", "sequences"
        )
        patched_chain_src = {}
        for cf in sorted(self.chain_files):
            try:
                with open(cf, "rb") as f:
                    raw = f.read()
                # Decode as UTF-8 first; fall back to cp1252 so mojibake from
                # old CP1252 exports is re-encoded as proper UTF-8 on dump.
                try:
                    cfg = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    cfg = json.loads(raw.decode("cp1252"))
            except Exception as e:
                logger.warning(
                    "Chain data hygiene: failed to parse %s: %s — bundling original",
                    cf, e,
                )
                patched_chain_src[cf] = cf
                continue
            try:
                # 1. Rewrite ALL project-internal absolute paths (e.g.
                #    D:\LoOperV2\...) to basenames so the bundled JSON
                #    contains no build-machine paths.  Runtime resolution
                #    (chain_ops / path_resolver) searches basenames under the
                #    bundled dirs, so relative resolution works on any machine.
                _project_root_norm = os.path.normpath(_find_project_root())

                def _basename_if_project_path(s):
                    if not re.match(r"^[A-Za-z]:[\\/]", str(s)):
                        return s
                    norm = os.path.normpath(s)
                    try:
                        common = os.path.commonpath([norm, _project_root_norm])
                    except Exception:
                        return s
                    if common == _project_root_norm:
                        return os.path.basename(norm)
                    return s

                def _fix_mojibake(s):
                    """Reverse CP1252-misread UTF-8 text (e.g. '\u00e2\u20ac\u201d'
                    -> em-dash).  Applied only when a strict cp1252->utf-8
                    round trip succeeds, so correctly-encoded text is never
                    corrupted."""
                    if not isinstance(s, str) or not s:
                        return s
                    try:
                        fixed = s.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
                        return fixed
                    except Exception:
                        return s

                def _rewrite_abs_paths(obj):
                    if isinstance(obj, dict):
                        for k, v in list(obj.items()):
                            if isinstance(v, str):
                                obj[k] = _fix_mojibake(_basename_if_project_path(v))
                            else:
                                _rewrite_abs_paths(v)
                    elif isinstance(obj, list):
                        for v in obj:
                            _rewrite_abs_paths(v)

                _rewrite_abs_paths(cfg)
                # 2. placeholder sequences (empty 'actions') -> real sequence
                for seq_entry in cfg.get("sequences", []) or []:
                    sfile = (seq_entry.get("sequence_file") or "").strip()
                    if not sfile:
                        continue
                    if seq_entry.get("actions"):
                        continue  # real inline sequence data — keep as-is
                    real = os.path.join(
                        _real_sequences_dir, os.path.basename(sfile)
                    )
                    if os.path.isfile(real):
                        seq_entry["sequence_file"] = os.path.basename(sfile)
                        seq_entry.pop("actions", None)
                        logger.info(
                            "Chain data hygiene: %s sequences[] placeholder "
                            "'%s' rewired to real sequence file",
                            os.path.basename(cf), sfile,
                        )
                # 3. UTF-8 re-encode (mojibake fix)
                patched_path = os.path.join(
                    _patched_chains_dir, os.path.basename(cf)
                )
                with open(patched_path, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                patched_chain_src[cf] = patched_path
                logger.info(
                    "Chain data hygiene: patched %s -> %s", cf, patched_path
                )
            except Exception as e:
                logger.warning(
                    "Chain data hygiene: failed to patch %s: %s — bundling original",
                    cf, e,
                )
                patched_chain_src[cf] = cf

        # Build datas list (data files to bundle)
        datas_lines = []

        # Chain JSON files
        for cf in sorted(self.chain_files):
            rel = os.path.relpath(cf, project_root)
            dest_dir = os.path.dirname(rel).replace("\\", "/")
            src = patched_chain_src.get(cf, cf)
            datas_lines.append(f"    ({repr(src)}, '{dest_dir}'),")
        
        # Sequence JSON files
        for sf in sorted(self.sequence_files):
            rel = os.path.relpath(sf, project_root)
            dest_dir = os.path.dirname(rel).replace("\\", "/")
            datas_lines.append(f"    ({repr(sf)}, '{dest_dir}'),")

        # Web session JSON files (WebSequenceNode) - resolve every session_file
        # referenced by web_sequences[] in the bundled chains.
        _web_seqs_dir = os.path.join(project_root, "web_sequences")
        _seen_web_sessions = set()
        for _cf in patched_chain_src:
            try:
                with open(patched_chain_src[_cf], "r", encoding="utf-8") as _f:
                    _cfg = json.load(_f)
                for _ws_entry in (_cfg.get("web_sequences", []) or []):
                    _ws_file = (_ws_entry.get("session_file") or "").strip()
                    if not _ws_file:
                        continue
                    _ws_src = os.path.join(_web_seqs_dir, os.path.basename(_ws_file))
                    if os.path.isfile(_ws_src) and _ws_src not in _seen_web_sessions:
                        _seen_web_sessions.add(_ws_src)
                        datas_lines.append(f"    ({repr(_ws_src)}, 'web_sequences'),")
            except Exception:
                continue

        # Web automation injected JS payload (LoOper/player/web/inject) - read
        # via Path(__file__).parent / "inject" at runtime in frozen builds.
        _web_inject_dir = os.path.join(project_root, "LoOper", "player", "web", "inject")
        if os.path.isdir(_web_inject_dir):
            datas_lines.append(f"    ({repr(_web_inject_dir)}, 'LoOper/player/web/inject'),")
        
        # Screenshot PNGs
        for sc in sorted(self.screenshot_files):
            rel = os.path.relpath(sc, project_root)
            dest_dir = os.path.dirname(rel).replace("\\", "/")
            datas_lines.append(f"    ({repr(sc)}, '{dest_dir}'),")
        
        # Model GGUF files
        for mf in sorted(self.model_files):
            rel = os.path.relpath(mf, project_root)
            dest_dir = os.path.dirname(rel).replace("\\", "/")
            datas_lines.append(f"    ({repr(mf)}, '{dest_dir}'),")
        
        # AI config.json — rewrite with relative model path for frozen builds
        ai_config_src = os.path.join(project_root, "LoOper", "AI", "config.json")
        if os.path.exists(ai_config_src):
            # Patch model_path to just the filename — absolute source-tree paths
            # are invalid in the frozen build where models live next to the exe.
            try:
                with open(ai_config_src, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                llm_cfg = config_data.get("LLAMA_CPP", {})
                if llm_cfg.get("model_path"):
                    llm_cfg["model_path"] = os.path.basename(llm_cfg["model_path"])
                # Embedding GGUF ships under AI/models — keep the reference
                # relative so the frozen agent resolves it next to the exe.
                if llm_cfg.get("embedding_model_path"):
                    llm_cfg["embedding_model_path"] = os.path.basename(
                        llm_cfg["embedding_model_path"]
                    )
                # Write patched config to build dir as config.json so
                # PyInstaller bundles it with the correct filename.
                patched_path = os.path.join(self._build_dir, "config.json")
                with open(patched_path, "w", encoding="utf-8") as f:
                    json.dump(config_data, f, indent=2)
                datas_lines.append(f"    ({repr(patched_path)}, 'LoOper/AI'),")
            except Exception:
                logger.exception("Failed to patch config.json — bundling original")
                datas_lines.append(f"    ({repr(ai_config_src)}, 'LoOper/AI'),")

        # Bundle the llama.cpp embedding GGUF (embeddinggemma) so the
        # exported agent embeds fully offline via AI.embedding_server —
        # no sentence-transformers, no huggingface.co.  The runtime resolves
        # the model under AI/models via config_loader.get_models_dir(), so
        # the single GGUF lands directly in that dir (no subfolder needed).
        _emb_gguf_src = _resolve_embedding_gguf_path()
        if _emb_gguf_src:
            _emb_dst = os.path.join(
                self._build_dir, os.path.basename(_emb_gguf_src)
            )
            try:
                shutil.copy2(_emb_gguf_src, _emb_dst)
                datas_lines.append(
                    f"    ({repr(_emb_dst)}, 'LoOper/AI/models'),"
                )
                logger.info(
                    "Bundling embedding model from %s", _emb_gguf_src
                )
            except Exception as e:
                logger.warning(
                    "Failed to bundle embedding model: %s", e
                )
        else:
            logger.warning(
                "Embedding model (embeddinggemma-300M-Q8_0.gguf) not found "
                "locally — exported agent falls back to lexical ranking"
            )
        
        # Build binaries list (extra DLLs / executables)
        binaries_lines = []

        # AI bin directory — use binaries (not datas) so PyInstaller scans
        # for transitive DLL dependencies (llama-server-impl.dll, ggml*.dll,
        # llama-common.dll, llama.dll, mtmd.dll, etc.).
        # Inspired by LoOperApp.spec which bundles these as binaries.
        ai_bin_dir = os.path.join(project_root, "LoOper", "AI", "bin")
        if os.path.isdir(ai_bin_dir):
            for fname in os.listdir(ai_bin_dir):
                fpath = os.path.join(ai_bin_dir, fname)
                if os.path.isfile(fpath):
                    binaries_lines.append(f"    ({repr(fpath)}, 'LoOper/AI/bin'),")
            # Parity guard: agent mode in Arrow runs the Vulkan-capable fork
            # (ggml-vulkan.dll).  A CPU-only bin dir must warn at export time
            # instead of silently producing an agent that cannot use the GPU.
            if not os.path.isfile(os.path.join(ai_bin_dir, "ggml-vulkan.dll")):
                logger.warning(
                    "ggml-vulkan.dll not found in %s — exported agent will "
                    "be CPU-only (agent mode uses the Vulkan backend)",
                    ai_bin_dir,
                )
        else:
            logger.warning(
                "LoOper/AI/bin not found (%s) — exported agent has no local "
                "llama.cpp inference", ai_bin_dir,
            )

        # Bundle VC++ runtime DLLs from System32 — inspired by LoOperApp.spec.
        # The venv/conda copies are OLDER versions that cause
        # STATUS_ACCESS_VIOLATION (0xC0000005) when llama-server.exe
        # (compiled with modern MSVC) tries to start.
        # LoOperApp.spec removes any venv-bundled copies of these DLLs
        # and uses System32's newer versions instead.
        _sys32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        _vc_dlls = ["msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
        for _dll in _vc_dlls:
            _src = os.path.join(_sys32, _dll)
            if os.path.exists(_src):
                binaries_lines.append(f"    ({repr(_src)}, '.'),")
                # Also bundle to torch/lib so torch's c10.dll can find the right runtime
                binaries_lines.append(f"    ({repr(_src)}, 'torch/lib'),")
                _vsize = os.path.getsize(_src)
                logger.info("Bundling %s from System32 (%d bytes)", _dll, _vsize)

        # Copy torch_runtime_hook.py to build dir so spec can reference it.
        # Runtime hooks moved to build_pipeline/ with the build scripts.
        _hook_src = next(
            (
                p
                for p in (
                    os.path.join(project_root, "build_pipeline", "torch_runtime_hook.py"),
                    os.path.join(project_root, "torch_runtime_hook.py"),
                )
                if os.path.exists(p)
            ),
            None,
        )
        _hook_dst = os.path.join(self._build_dir, "torch_runtime_hook.py")
        if _hook_src:
            shutil.copy2(_hook_src, _hook_dst)
            _runtime_hooks_str = repr(_hook_dst)
            logger.info("Bundling torch_runtime_hook.py as runtime hook")
        else:
            _runtime_hooks_str = ""
            logger.warning("torch_runtime_hook.py not found — torch may fail to load")

        datas_str = "\n".join(datas_lines) if datas_lines else "    # no data files"
        binaries_str = "\n".join(binaries_lines) if binaries_lines else "    # no binaries"

        hidden_imports_str = ", ".join(repr(h) for h in HIDDEN_IMPORTS)
        excludes_str = ", ".join(repr(e) for e in EXCLUDES)

        spec_content = f"""# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by AgentExporter — do not edit manually.

import os
import sys

# ponytail: prevent OpenMP DLL conflict crash in PyInstaller isolated child
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import torch
# Pre-load torch DLLs to PATH to avoid WinError 127 during import in analysis
torch_lib = os.path.join(os.path.dirname(os.path.abspath(torch.__file__)), 'lib')
if os.path.exists(torch_lib):
    os.environ['PATH'] = torch_lib + os.pathsep + os.environ.get('PATH', '')

# Add project source directories to sys.path so PyInstaller can resolve
# imports like AI.api during analysis (same pattern as LoOperApp.spec)
_project_root = {repr(project_root)}
_looper_dir = os.path.join(_project_root, 'LoOper')
for _p in (_project_root, _looper_dir,
           os.path.join(_looper_dir, 'AI'),
           os.path.join(_looper_dir, 'player'),
           os.path.join(_looper_dir, 'builder'),
           os.path.join(_looper_dir, 'recorder')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

_datas = []
_binaries = []
_hiddenimports_extra = []

# Collect everything from complex ML packages via collect_all (torch DLLs, transformers data, etc.)
for _pkg in ['torch']:
    try:
        _ret = collect_all(_pkg, include_py_files=False)
        _datas += _ret[0]
        _binaries += _ret[1]
        _hiddenimports_extra += _ret[2]
    except ImportError:
        pass

# Bundled chain/sequence/screenshot/model/data files
_datas += [
{datas_str}
]

# AI binaries (llama-server.exe, DLLs) + VC++ runtime DLLs
_binaries += [
{binaries_str}
]

# Handle libiomp5md conflict: remove any non-torch copies, add torch's version
import os as _os
import torch as _t
_t_lib = _os.path.join(_os.path.dirname(_t.__file__), 'lib')
_t_libiomp = _os.path.join(_t_lib, 'libiomp5md.dll')
_binaries = [b for b in _binaries if not (
    _os.path.basename(b[0]).lower() == 'libiomp5md.dll'
    and 'torch' not in b[1].lower()
)]
if _os.path.exists(_t_libiomp):
    # Deduplicate: only add if torch version not already present
    if not any(_os.path.normcase(b[0]) == _os.path.normcase(_t_libiomp) for b in _binaries):
        _binaries.append((_t_libiomp, '.'))
        _binaries.append((_t_libiomp, 'torch/lib'))

a = Analysis(
    ['agent_standalone_entry.py'],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=[
{hidden_imports_str}
    ] + _hiddenimports_extra,
    excludes=[
{excludes_str}
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[{_runtime_hooks_str}],
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='{self.exe_name}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # keep console for logging during development
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
"""

        with open(spec_path, "w", encoding="utf-8") as f:
            f.write(spec_content)

        logger.info("Generated spec at %s", spec_path)
        return spec_path

    def _find_project_root_for_spec(self) -> str:
        """Find the project root for relative path resolution in the spec."""
        # Walk up from this file's location
        here = os.path.dirname(os.path.abspath(__file__))
        for _ in range(10):
            if os.path.basename(here) == "LoOper" and os.path.isdir(
                os.path.join(here, "AI")
            ):
                return os.path.dirname(here)  # parent of LoOper/
            parent = os.path.dirname(here)
            if parent == here:
                break
            here = parent
        # Fallback: use the directory containing the LoOper/ subdirectory
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ── Build execution ────────────────────────────────────────────────

    def run_build(
        self, progress_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Execute PyInstaller via subprocess.

        Args:
            progress_callback: Called with each line of PyInstaller output.

        Returns:
            True if the build succeeded, False otherwise.
        """
        if not self._build_dir:
            spec_path = self.generate_spec()
        else:
            spec_path = os.path.join(
                self._build_dir, f"{self.exe_name}.spec"
            )

        if not os.path.exists(spec_path):
            if progress_callback:
                progress_callback(f"ERROR: Spec file not found: {spec_path}")
            return False

        self._cancel_event.clear()

        # Find the Python executable (preferring the .venv)
        python_exe = sys.executable

        cmd = [
            python_exe,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            self.output_dir,
            "--workpath",
            os.path.join(self._build_dir, "build"),
            spec_path,
        ]

        if progress_callback:
            progress_callback(
                f"Starting PyInstaller build for '{self.exe_name}'...\n"
                f"  Output:  {self.output_dir}\n"
                f"  Spec:    {spec_path}\n"
                f"  Chains:  {len(self.chain_files)}\n"
                f"  Models:  {len(self.model_files)}\n"
            )

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

            for line in iter(self._process.stdout.readline, ""):
                if self._cancel_event.is_set():
                    self._process.terminate()
                    if progress_callback:
                        progress_callback("\n--- Build cancelled ---\n")
                    return False
                if progress_callback:
                    progress_callback(line.rstrip())

            self._process.wait()
            success = self._process.returncode == 0

            if success:
                exe_path = os.path.join(self.output_dir, f"{self.exe_name}.exe")
                if progress_callback:
                    progress_callback(
                        f"\n--- Build {'succeeded' if success else 'failed'} ---\n"
                        f"Executable: {exe_path}\n"
                    )
            else:
                if progress_callback:
                    progress_callback(
                        f"\n--- PyInstaller exited with code {self._process.returncode} ---\n"
                    )

            return success

        except Exception as e:
            if progress_callback:
                progress_callback(f"\nERROR: {e}\n")
            logger.exception("PyInstaller build failed")
            return False
        finally:
            self._process = None

    def cancel(self):
        """Cancel a running build."""
        self._cancel_event.set()
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                logger.info("Build process terminated")
            except Exception:
                pass
