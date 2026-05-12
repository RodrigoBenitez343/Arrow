import copy
import json
import os
import re
import subprocess as _sp
import sys
import threading
import time
from typing import Any, Dict, Optional

import requests as _rq

# Cache for embedding model to avoid reloading
_EMBEDDING_MODEL = None

from ...config import logger
from .config_utils import get_default_api_url
from .inputs import _context_input, get_input_text
from .prompt_utils import (
    _HISTORY_INSERT_MARKER,
    assemble_enhanced_prompt,
    substitute_variables,
)
from .rag_utils import build_rag_context, build_rag_context_from_docs
from .vision_utils import prepare_vision_images


def _strip_think_tags(text):
    """
    Strip chain-of-thought/reasoning tags from LLM output.
    
    Handles common formats:
      <think>...</think>       - DeepSeek, Qwen, Granite, etc.
       ...     - Standard thought tags
      <reasoning>...</reasoning> - Reasoning tags
      <scratchpad>...</scratchpad> - Scratchpad tags
      [thinking]...[/thinking] - Bracket-style tags
    
    Also strips any content before the final <answer>...</answer> or [answer]...[/answer]
    if those tags are present (keeps only the answer block).
    
    Args:
        text: Raw LLM response text
        
    Returns:
        Cleaned text with think tags removed
    """
    if not text or not isinstance(text, str):
        return text
    
    # Try answer tags first — if present, keep only the answer content
    import re as _re
    answer_match = _re.search(r'<answer>(.*?)</answer>', text, _re.DOTALL | _re.IGNORECASE)
    if answer_match:
        return answer_match.group(1).strip()
    answer_match = _re.search(r'\[answer\](.*?)\[/answer\]', text, _re.DOTALL | _re.IGNORECASE)
    if answer_match:
        return answer_match.group(1).strip()
    
    # Remove standard think tags: <think>...</think>
    stripped = _re.sub(r'<think>.*?</think>', '', text, flags=_re.DOTALL | _re.IGNORECASE)
    # Handle dangling </think> tag (no matching opening tag).
    # Some models output reasoning text followed by a standalone </think> before the final answer,
    # e.g. "...yes or no...\n</think>\nNo." — we must strip everything before and including </think>
    # to prevent reasoning text (with words like "yes" or "no") from polluting the answer.
    dangle = _re.search(r'</think>', stripped, flags=_re.IGNORECASE)
    if dangle:
        stripped = stripped[dangle.end():].strip()
    # Remove '```think```' code block style
    stripped = _re.sub(r'```think\s*.*?```', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove <reasoning>...</reasoning>
    stripped = _re.sub(r'<reasoning>.*?</reasoning>', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove <scratchpad>...</scratchpad>
    stripped = _re.sub(r'<scratchpad>.*?</scratchpad>', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove [thinking]...[/thinking]
    stripped = _re.sub(r'\[thinking\].*?\[/thinking\]', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove standalone  tags (thinking block without closing tag, in some models)
    stripped = _re.sub(r'<\s*/?\s*think\s*>', '', stripped, flags=_re.DOTALL | _re.IGNORECASE)
    # Remove markdown think headers: ## Thinking
    stripped = _re.sub(r'#{1,3}\s*think(?:ing)?\s*', '', stripped, flags=_re.IGNORECASE)
    # Remove trailing/leading whitespace
    stripped = stripped.strip()
    
    return stripped

class LLMExecutor:
    """Executes LLM nodes in workflow graphs (modularized)."""

    def __init__(self, variables: Optional[Dict[str, object]] = None):
        # Store initial variables to support resetting state between runs
        # Use deepcopy to ensure we have a true independent snapshot
        try:
            self._initial_variables = copy.deepcopy(variables) if variables else {}
        except Exception:
            # Fallback to shallow copy if deepcopy fails (e.g. non-pickleable objects)
            self._initial_variables = variables.copy() if variables else {}

        try:
            self.variables: Dict[str, object] = copy.deepcopy(self._initial_variables)
        except Exception:
            self.variables = self._initial_variables.copy()

        self.context_collector = None
        self.context_db = None
        self._initialize_context_collector()
        self._initialize_context_database()
        self._rich_console = None
        try:
            from rich.console import Console

            self._rich_console = Console()
        except Exception:
            self._rich_console = None

    def reset(self) -> None:
        """Resets the executor state to initial configuration."""
        try:
            self.variables = copy.deepcopy(self._initial_variables)
        except Exception:
            self.variables = self._initial_variables.copy()
        logger.info("LLMExecutor state reset")

    def _initialize_context_collector(self) -> None:
        try:
            project_root = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.context_collector import ContextCollector  # noqa: F401

            self.context_collector = (
                ContextCollector() if "ContextCollector" in globals() else None
            )
        except Exception:
            self.context_collector = None

    def _initialize_context_database(self) -> None:
        try:
            project_root = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.context_database import ContextDatabase  # noqa: F401

            self.context_db = (
                ContextDatabase() if "ContextDatabase" in globals() else None
            )
        except Exception:
            self.context_db = None

    def _format_yolo_data(self, vision_data: Dict) -> str:
        """Formats raw YOLO JSON into a concise, semantic string for LLMs, sorted spatially."""
        objects = vision_data.get("Object", [])
        if not objects:
            return "No UI elements detected."

        # Sort objects top-to-bottom (y1), then left-to-right (x1)
        # Assuming coords are [x1, y1, x2, y2]
        def sort_key(obj):
            coords = obj.get("coords", [])
            if coords and len(coords) == 4:
                return (coords[1], coords[0])  # y1, x1
            return (0, 0)

        sorted_objects = sorted(objects, key=sort_key)

        lines = ["DETECTED UI ELEMENTS (Ordered Top-to-Bottom):"]
        for obj in sorted_objects:
            label = obj.get("label", "Unknown")
            coords = obj.get("coords", [])
            text_content = obj.get("text_content", [])
            text = (
                ", ".join(text_content)
                if isinstance(text_content, list)
                else str(text_content)
            )

            # Format coordinates to integer for readability
            loc_str = ""
            if coords and len(coords) == 4:
                try:
                    x1, y1, x2, y2 = map(int, coords)
                    # Calculate center for easier spatial reasoning
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    loc_str = f"at ({cx}, {cy})"
                except Exception:
                    loc_str = f"at {coords}"

            # Build line
            line = f"- {label} {loc_str}"
            if text and text.strip():
                line += f" | Text: '{text}'"

            # Interactions/Properties
            props = obj.get("property")
            if props:
                line += f" | Prop: {props}"

            lines.append(line)

        return "\n".join(lines)

    def execute_llm_node(
        self, node: Dict, stop_flag, action_handlers=None
    ) -> Optional[str]:
        """Execute an LLM node and return the next node ID to run."""
        llm_data = node.get("data", {})
        _node_id = (
            node.get("id")
            or node.get("node_id")
            or llm_data.get("node_id")
            or llm_data.get("id")
            or "Unknown"
        )
        logger.info("=== STARTING LLM NODE EXECUTION ===")
        logger.info(
            f"Executing LLM node: {llm_data.get('name', llm_data.get('id', llm_data.get('node_id', 'Unknown')))}"
        )
        logger.info(f"Full node data: {node}")

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request")
            return None

        try:
            project_root = os.path.dirname(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                )
            )
            if project_root not in sys.path:
                sys.path.append(project_root)
            from AI.consult import OllamaClient
        except Exception as e:
            logger.error(f"Failed to import OllamaClient: {e}")
            return None

        llm_config = llm_data.get("llm_configuration", {}) or llm_data
        logger.info(f"LLM configuration: {llm_config}")

        # ── Direct clipboard capture for context timeline ──
        # Capture raw clipboard content BEFORE any input processing or context supplement
        _raw_clipboard_for_context = ""
        try:
            _configured_source = (
                llm_config.get("input_source", "none") if llm_config else "none"
            )
            if str(_configured_source).strip().lower() == "clipboard":
                import pyperclip

                _raw_clipboard_for_context = pyperclip.paste() or ""
                logger.info(
                    f"Direct clipboard capture for context: {len(_raw_clipboard_for_context)} chars"
                )
            elif str(_configured_source).strip().lower() in ("ocr", "screen"):
                _raw_clipboard_for_context = (
                    "[OCR/screen input - captured at processing time]"
                )
            elif str(_configured_source).strip().lower() == "previous":
                # For 'previous' source, capture from upstream node output
                for inp in node.get("inputs", []):
                    upstream_id = inp.get("from_node")
                    if upstream_id:
                        upstream_val = self.variables.get(
                            f"node_{upstream_id}_output", ""
                        )
                        if upstream_val:
                            _raw_clipboard_for_context = str(upstream_val)[:500]
                            break
        except Exception as e:
            logger.warning(f"Direct clipboard capture failed: {e}")

        # Store it immediately under an unambiguous variable name
        if _node_id:
            self.variables[f"node_{_node_id}_raw_user_input"] = (
                _raw_clipboard_for_context
            )
            logger.info(
                f"Stored raw_user_input for {_node_id}: {len(_raw_clipboard_for_context)} chars, preview: {_raw_clipboard_for_context[:100]}"
            )

        model = llm_config.get("model", "llama3.2")
        logger.info(f"Resolved model for execution: '{model}' (Default: 'llama3.2')")

        def _normalize_api_url(value):
            raw = str(value or "").strip()
            if not raw:
                return None
            if raw.startswith("http://") or raw.startswith("https://"):
                try:
                    from urllib.parse import urlparse

                    parsed = urlparse(raw)
                    if not parsed.hostname:
                        return None
                except Exception:
                    return None
                return raw.rstrip("/")
            return f"http://{raw}".rstrip("/")

        api_url = _normalize_api_url(llm_config.get("api_url")) or get_default_api_url()
        system_message = llm_config.get("system_message", "")
        prompt = llm_config.get("prompt", "")
        temperature = llm_config.get("temperature", 0.7)
        max_tokens = int(llm_config.get("max_tokens", 4096) or 4096)
        output_variable = llm_config.get("output_variable", "llm_output")
        write_text = llm_config.get("write_text", True)
        use_vision = llm_config.get("use_vision", False)
        use_advanced_vision = str(
            llm_config.get("use_advanced_vision", "false")
        ).lower() in ("true", "1", "yes", "on")
        screenshot_enabled = llm_config.get("screenshot_enabled", True)

        def _is_vision_model_name(name: str) -> bool:
            ml = str(name or "").lower()
            for v in (
                "llava",
                "bakllava",
                "moondream",
                "llava-llama3",
                "llava-phi3",
                "minicpm-v",
            ):
                if v in ml:
                    return True
            # Also check for GGUF vision models by common keywords in the path/filename
            for v in ("vl-", "-vl", "vision", "multimodal", "vl."):
                if v in ml:
                    return True
            return False

        # Also check if the llama.cpp model path indicates a vision model
        _llamacpp_path = llm_config.get("llamacpp_model_path", "")
        _use_llamacpp = str(llm_config.get("use_llamacpp", False)).lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        is_vision_model = _is_vision_model_name(model) or (
            _use_llamacpp and _is_vision_model_name(_llamacpp_path)
        )
        should_use_vision = use_vision or is_vision_model
        images, _ocr_text = prepare_vision_images(
            should_use_vision, screenshot_enabled, stop_flag
        )

        # Auto-detect concatenation: if this node has any inputs in the workflow graph,
        # treat them as "previous" source and enable direct RAG unless explicitly disabled.
        node_inputs = node.get("inputs", []) or []
        main_inputs = []
        try:
            for _inp in node_inputs:
                if isinstance(_inp, dict) and str(_inp.get("input_port")) in (
                    "tools",
                    "context",
                ):
                    continue
                main_inputs.append(_inp)
        except Exception:
            main_inputs = node_inputs
        has_input_connection = bool(main_inputs)
        connected_ids = []
        try:
            connected_ids = [
                inp.get("from_node")
                for inp in main_inputs
                if isinstance(inp, dict) and inp.get("from_node")
            ]
        except Exception:
            connected_ids = []
        has_previous_llm_output = any(
            f"node_{cid}_output" in self.variables for cid in connected_ids
        )

        tool_inputs = []
        try:
            tool_inputs = [
                inp
                for inp in node_inputs
                if isinstance(inp, dict) and str(inp.get("input_port", "")) == "tools"
            ]
        except Exception:
            tool_inputs = []
        tool_ids = []
        try:
            tool_ids = [
                inp.get("from_node") for inp in tool_inputs if inp.get("from_node")
            ]
        except Exception:
            tool_ids = []
        tool_desc_map = {}
        try:
            import json as _json

            raw_td = llm_config.get("tool_descriptions") or {}
            if isinstance(raw_td, str):
                raw_td = _json.loads(raw_td)
            if isinstance(raw_td, dict):
                tool_desc_map = raw_td
        except Exception:
            tool_desc_map = {}
        # Tool processing moved to after prompt substitution for better context awareness

        # Skills
        skills_raw = llm_config.get("skills", [])
        if isinstance(skills_raw, str):
            try:
                import json as _json_skills

                skills_raw = _json_skills.loads(skills_raw)
            except Exception:
                skills_raw = []
        enabled_skills = [s for s in skills_raw if s.get("enabled", True)]
        use_skill_routing = llm_config.get("use_skill_routing", True)
        if isinstance(use_skill_routing, str):
            use_skill_routing = use_skill_routing.lower() in ("true", "1", "yes")

        try:
            code_paths = {}
            for k, v in (
                self.variables.items() if isinstance(self.variables, dict) else []
            ):
                if isinstance(k, str) and k.endswith("_code_path") and v:
                    nid = k.replace("node_", "").replace("_code_path", "")
                    code_paths[nid] = str(v)
            if code_paths:
                alias_map = {}
                for k, v in (
                    self.variables.items() if isinstance(self.variables, dict) else []
                ):
                    if isinstance(k, str) and k.endswith("_module_alias") and v:
                        nid = k.replace("node_", "").replace("_module_alias", "")
                        alias_map[nid] = str(v)
                lines = ["Available Code Files:"]
                for nid, p in code_paths.items():
                    alias = alias_map.get(nid, "")
                    if alias:
                        lines.append(f"- {nid}: {p} (alias: {alias})")
                    else:
                        lines.append(f"- {nid}: {p}")
                lines.append(
                    "Import by path using importlib.util.spec_from_file_location and exec_module."
                )
                lines.append("At the top of your single code block, include:")
                lines.append("# ALIAS:<short_name>")
                lines.append(
                    "This describes the module purpose for composite app integration."
                )
                extra = "\n".join(lines)
                system_message = (system_message + "\n\n" + extra).strip()
        except Exception:
            pass

        # Detect context connections and whether upstream context is available
        context_inputs = []
        try:
            context_inputs = [
                inp
                for inp in node_inputs
                if isinstance(inp, dict) and str(inp.get("input_port", "")) == "context"
            ]
        except Exception:
            context_inputs = []
        context_ids = []
        try:
            context_ids = [
                inp.get("from_node") for inp in context_inputs if inp.get("from_node")
            ]
        except Exception:
            context_ids = []
        has_context_connection = bool(context_ids)
        has_upstream_context_value = any(
            self.variables.get(f"node_{cid}_context", "")
            and str(self.variables.get(f"node_{cid}_context", "")).strip()
            for cid in context_ids
        )

        # CRITICAL FIX: Capture the raw input source before any auto-detection.
        # The user's explicit choice (clipboard, OCR, etc.) must be preserved.
        # The 'or none' fallback was causing valid values like 'clipboard' to be
        # lost when the config value was empty string or None, triggering unwanted
        # auto-detection that overwrote the user's choice with 'context'.
        raw_input_source = llm_config.get("input_source")
        if raw_input_source and str(raw_input_source).strip():
            configured_input_source = str(raw_input_source).strip().lower()
        else:
            configured_input_source = "none"

        input_source = configured_input_source

        # Auto-detection ONLY if user didn't explicitly set an input source
        if configured_input_source in ("none", "", "null"):
            if has_context_connection and has_upstream_context_value:
                input_source = "context"
                logger.info(
                    "Auto-enabled input_source='context' due to detected context connection"
                )
            elif has_input_connection and has_previous_llm_output:
                input_source = "previous"
                logger.info(
                    "Auto-enabled input_source='previous' due to detected input connection for concatenation"
                )

        # DEBUG: Log the final input_source to help diagnose issues
        logger.info(
            f"Final input_source resolved to: '{input_source}' (configured: '{configured_input_source}', raw: '{raw_input_source}')"
        )

        raw_conf = llm_config.get("ocr_confidence")
        if raw_conf is None:
            ocr_confidence = 0.6
        else:
            try:
                ocr_confidence = float(raw_conf)
                if ocr_confidence > 1.0:
                    ocr_confidence = ocr_confidence / 100.0
            except Exception:
                ocr_confidence = 0.6

        logger.info(f"Input source: {input_source}, OCR confidence: {ocr_confidence}")

        use_comorag = str(llm_config.get("use_comorag", "true")).lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        max_cycles = int(llm_config.get("max_cycles", 5))
        memory_size = int(llm_config.get("memory_size", 1000))
        if use_comorag:
            try:
                env_overwatch = _normalize_api_url(os.getenv("OVERWATCH_API_URL"))
            except Exception:
                env_overwatch = None
            if env_overwatch:
                api_url = env_overwatch
            else:
                if api_url and ":11434" in api_url:
                    api_url = "http://127.0.0.1:8000"
        if use_comorag:
            candidates = []
            try:
                candidates.append(str(api_url or "").strip())
            except Exception:
                pass
            try:
                env_url = _normalize_api_url(os.getenv("OVERWATCH_API_URL"))
                if env_url:
                    candidates.append(env_url)
            except Exception:
                pass
            candidates.extend(["http://localhost:8000", "http://127.0.0.1:8000"])
            picked = None
            for u in candidates:
                if not u:
                    continue
                try:
                    r = _rq.get(
                        f"{u.rstrip('/')}/health",
                        headers={"Connection": "close"},
                        timeout=1.5,
                    )
                    if getattr(r, "status_code", 0) == 200:
                        picked = u.rstrip("/")
                        break
                except Exception:
                    continue
            if picked:
                if str(api_url or "").rstrip("/") != picked:
                    logger.info(f"ComoRAG: using reachable API URL {picked}")
                api_url = picked
            else:
                logger.warning(
                    f"ComoRAG: no reachable API server for {candidates}. Disabling ComoRAG."
                )
                use_comorag = False

        use_direct_rag = str(llm_config.get("use_direct_rag", "false")).lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        # Respect explicit user choice for RAG; do not auto-enable on concatenation.
        # Allow explicit selection of previous/context input mode overriding use_direct_rag
        prev_mode = (
            (
                llm_config.get("previous_input_mode")
                or llm_config.get("context_input_mode")
                or ""
            )
            .strip()
            .lower()
        )
        if input_source.lower() in (
            "previous",
            "previous_node",
            "context",
        ) and prev_mode in ("raw", "embed"):
            use_direct_rag = prev_mode == "embed"
        rag_embedding_model = llm_config.get("rag_embedding_model", "nomic-embed-text")
        rag_chunk_size = int(llm_config.get("rag_chunk_size", 500))
        rag_overlap = int(llm_config.get("rag_overlap", 100))
        rag_top_k = int(llm_config.get("rag_top_k", 3))
        rag_include_raw_input = str(
            llm_config.get("rag_include_raw_input", "false")
        ).lower() in ("true", "1", "yes", "on")
        rag_max_chars = int(llm_config.get("rag_max_chars", 1500))

        logger.info("LLM Parameters:")
        logger.info(
            f"  Config model: {model} (engine: {'llamacpp' if _use_llamacpp else 'ollama'})"
        )
        logger.info(f"  API URL: {api_url}")
        logger.info(f"  System Message: {system_message}")
        logger.info(f"  Original Prompt: {prompt}")
        logger.info(f"  Temperature: {temperature}")
        logger.info(f"  Max Tokens: {max_tokens}")
        logger.info(f"  Output Variable: {output_variable}")
        logger.info(f"  Write Text: {write_text}")
        logger.info(f"  Use Vision: {use_vision}")
        logger.info(f"  Screenshot Enabled: {screenshot_enabled}")
        logger.info(f"  Advanced Vision: {use_advanced_vision}")
        try:
            if self._rich_console:
                from rich.panel import Panel
                from rich.table import Table

                self._rich_console.rule("[bold yellow]LLM Request")
                info = Table(
                    title="LLM Node", show_header=True, header_style="bold magenta"
                )
                info.add_column("Field", style="bold")
                info.add_column("Value")
                # Determine the actual model being used
                _use_llamacpp = str(llm_config.get("use_llamacpp", False)).lower() in (
                    "true",
                    "1",
                    "yes",
                    "on",
                )
                if _use_llamacpp:
                    _display_model = llm_config.get("llamacpp_model_path") or model
                    _display_engine = "llama.cpp"
                else:
                    _display_model = model
                    _display_engine = "Ollama"

                info.add_row("Engine", _display_engine)
                info.add_row("Model", str(_display_model))
                info.add_row("API", str(api_url))
                info.add_row("Input Source", str(input_source))
                info.add_row("Prev/Context Mode", str(prev_mode or ""))
                info.add_row("Direct RAG", "true" if use_direct_rag else "false")
                info.add_row("RAG Model", str(rag_embedding_model))
                info.add_row("Top K", str(rag_top_k))
                self._rich_console.print(info)
                self._rich_console.print(
                    Panel(
                        (system_message or ""),
                        title="System Message",
                        border_style="cyan",
                    )
                )
                self._rich_console.print(
                    Panel(prompt, title="Prompt", border_style="cyan")
                )
                if tool_ids:
                    tools_tbl = Table(
                        title="Available Tools",
                        show_header=True,
                        header_style="bold cyan",
                    )
                    tools_tbl.add_column("ID", style="bold")
                    tools_tbl.add_column("Description")
                    for tid in tool_ids:
                        d = tool_desc_map.get(tid, "")
                        tools_tbl.add_row(str(tid), str(d or ""))
                    self._rich_console.print(tools_tbl)
        except Exception:
            pass

        if stop_flag and stop_flag():
            logger.info(
                "LLM node execution stopped by user request during configuration"
            )
            return None

        prompt = substitute_variables(prompt, self.variables)
        logger.info(f"Prompt after variable substitution: {prompt}")

        # --- Intelligent Tool Selection ---
        if tool_ids:
            try:
                # Use cached embedding model
                global _EMBEDDING_MODEL
                if _EMBEDDING_MODEL is None:
                    try:
                        from sentence_transformers import SentenceTransformer

                        # Use all-MiniLM-L6-v2 as it's fast and effective for this
                        _EMBEDDING_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
                    except ImportError:
                        logger.warning(
                            "sentence_transformers not found. Skipping intelligent tool selection."
                        )
                    except Exception as e:
                        logger.warning(f"Failed to load embedding model: {e}")

                if _EMBEDDING_MODEL and prompt and prompt.strip():
                    from sentence_transformers import util

                    query_text = prompt.strip()
                    tool_texts = []
                    tool_id_list = []

                    for tid in tool_ids:
                        raw_desc = tool_desc_map.get(tid, "")
                        # Clean description: replace underscores with spaces for better semantic matching
                        clean_desc = raw_desc.replace("_", " ")
                        tool_texts.append(clean_desc)
                        tool_id_list.append(tid)

                    if tool_texts:
                        logger.info(
                            f"Ranking {len(tool_texts)} tools against query: '{query_text[:50]}...'"
                        )
                        query_emb = _EMBEDDING_MODEL.encode(
                            query_text, convert_to_tensor=True
                        )
                        tool_embs = _EMBEDDING_MODEL.encode(
                            tool_texts, convert_to_tensor=True
                        )

                        scores = util.cos_sim(query_emb, tool_embs)[0]
                        best_idx = int(scores.argmax())
                        best_score = float(scores[best_idx])
                        best_tid = tool_id_list[best_idx]

                        logger.info(
                            f"Tool Selection: Best match is {best_tid} with score {best_score:.4f}"
                        )

                        # If we have a decent match, restrict the tools to just this one
                        # This forces the LLM to use the semantically correct tool
                        if (
                            best_score > 0.1
                        ):  # Threshold based on testing (winner ~0.20, loser ~0.14)
                            tool_ids = [best_tid]
                            logger.info(
                                f"Tool Selection: Restricted available tools to {best_tid}"
                            )

            except Exception as e:
                logger.warning(f"Tool selection process failed: {e}")

            # Re-generate tool instructions with potentially filtered list
            try:
                lines = ["Available Tools:"]
                for tid in tool_ids:
                    desc = tool_desc_map.get(tid, "")
                    if desc:
                        lines.append(f"- {tid}: {desc}")
                    else:
                        lines.append(f"- {tid}")
                lines.append("Tool Usage Guide:")
                lines.append("- Choose exactly one tool id.")
                lines.append("- Respond ONLY with 'USE_TOOL:<id>' with no other text.")
                lines.append("- Output must be exactly one line: USE_TOOL:<id>.")
                lines.append(
                    "- Do not include quotes, markdown, code blocks, punctuation, or extra lines."
                )
                lines.append("- Do not explain, reason, or justify the choice.")
                extra = "\n".join(lines)
                system_message = (system_message + "\n\n" + extra).strip()
            except Exception:
                pass
        # ----------------------------------

        # ----------------------------------

        # ── Skill routing and injection ──
        if enabled_skills:
            if use_skill_routing and len(enabled_skills) > 1:
                # Gather context from connected Context nodes
                context_text = ""
                for cid in context_ids:
                    ctx_val = self.variables.get(f"node_{cid}_context", "")
                    if ctx_val:
                        context_text += str(ctx_val) + "\n"

                if context_text.strip() and _EMBEDDING_MODEL is not None:
                    try:
                        from sentence_transformers import util as st_util

                        skill_texts = [
                            f"{s.get('name', '')}: {s.get('description', '')} {s.get('trigger_keywords', '')}"
                            for s in enabled_skills
                        ]
                        query_emb = _EMBEDDING_MODEL.encode(
                            context_text, convert_to_tensor=True
                        )
                        skill_embs = _EMBEDDING_MODEL.encode(
                            skill_texts, convert_to_tensor=True
                        )
                        scores = st_util.cos_sim(query_emb, skill_embs)[0]

                        selected = []
                        for idx, skill in enumerate(enabled_skills):
                            score = float(scores[idx])
                            if score > 0.15:
                                selected.append((score, skill))

                        if selected:
                            selected.sort(
                                key=lambda x: (-x[0], x[1].get("priority", 5))
                            )
                            enabled_skills = [s for _, s in selected]
                            logger.info(
                                f"Skill routing: Selected {len(enabled_skills)} skills based on context relevance"
                            )
                        # If nothing matched, keep all enabled skills
                    except Exception as e:
                        logger.debug(f"Skill routing error: {e}")

            # Inject selected skills into system_message
            skill_block = "\n\n## Active Skills\n"
            for skill in enabled_skills:
                skill_block += f"\n### {skill.get('name', 'Unnamed')}\n{skill.get('instructions', '')}\n"
            system_message = system_message + skill_block
            logger.info(f"Injected {len(enabled_skills)} skills into system message")
        # ----------------------------------

        # Determine if we are using the Form Filling Preset
        def _is_form_filling_prompt(p: str) -> bool:
            pl = (p or "").strip().lower()
            if not pl:
                return False
            if "analyze the form fields provided" not in pl:
                return False
            if "next empty field" not in pl:
                return False
            return True

        is_form_filling_preset = _is_form_filling_prompt(prompt)
        if is_form_filling_preset and use_comorag:
            try:
                if int(max_cycles) > 2:
                    max_cycles = 2
            except Exception:
                pass

        if stop_flag and stop_flag():
            logger.info(
                "LLM node execution stopped by user request before input processing"
            )
            return None

        input_text, use_input_text_in_prompt = get_input_text(
            node,
            self.variables,
            input_source,
            ocr_confidence,
            stop_flag,
            use_direct_rag,
        )

        # Store the actual source input text and type so downstream nodes
        # (e.g. Context node) can capture what was really fed to the LLM,
        # not just the prompt template.
        # CRITICAL: This must happen BEFORE any prompt assembly or context supplement.
        try:
            if _node_id:
                # Store the RAW input text (clipboard, OCR, etc.) before any processing
                raw_input_to_store = input_text if input_text is not None else ""
                self.variables[f"node_{_node_id}_source_input"] = raw_input_to_store
                self.variables[f"node_{_node_id}_input_source"] = input_source
                # DEBUG: Log what we're storing to help diagnose issues
                preview = (
                    str(raw_input_to_store)[:200] if raw_input_to_store else "(empty)"
                )
                logger.info(
                    f"Stored source_input for node {_node_id}: input_source='{input_source}', content_preview='{preview}...'"
                )
        except Exception as e:
            logger.warning(f"Failed to store source_input for node {_node_id}: {e}")

        # Fallback OCR removed to respect explicit 'none' input source
        # if input_text is None and ocr_confidence > 0 and input_source.lower() in ('none', '', 'null'):
        #      logger.info(f"Input source is '{input_source}' but ocr_confidence={ocr_confidence}. Attempting fallback OCR.")
        #      ocr_text, _ = get_input_text(
        #         node,
        #         self.variables,
        #         "ocr",
        #         ocr_confidence,
        #         stop_flag,
        #         use_direct_rag,
        #      )
        #      if ocr_text:
        #          input_text = ocr_text
        #          # Update source for prompt assembly
        #          input_source = "SCREEN_CONTEXT"
        #          use_input_text_in_prompt = True
        #          logger.info("Fallback OCR successful. Using extracted text as input.")

        if input_text is not None:
            if is_form_filling_preset:
                # Add the form field data to the prompt context
                prompt = f"Current Form Layout & Fields:\n{input_text}\n\n{prompt}"
                logger.info(f"Enhanced prompt with Vision Form Field input")
                if use_comorag and api_url and str(api_url).strip():
                    try:
                        from AI.consult import OllamaClient as _MemClient

                        mem_client = _MemClient(
                            base_url=api_url,
                            debug=False,
                            use_comorag=True,
                            timeout=None,
                        )
                        mem_client.add_memory(
                            content=str(input_text),
                            source="form_fields",
                            metadata={
                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "type": "vision_pruned_fields",
                            },
                        )
                        try:
                            mem_client.close()
                        except Exception:
                            pass
                    except Exception:
                        pass
            elif use_input_text_in_prompt:
                prompt = assemble_enhanced_prompt(
                    prompt, input_source, input_text, include_raw=True
                )
                logger.info(f"Enhanced prompt with {input_source} input")
            else:
                logger.info(
                    "Direct RAG enabled; skipping raw previous text injection into prompt"
                )
        if use_comorag and input_text and not use_input_text_in_prompt:
            prompt = f"{input_text}\n\n{prompt}"

        # ── Context supplement (inserted between CURRENT INPUT and QUESTION sections) ──
        # Skip if input_source is already 'context' — the context is already in the prompt
        # via assemble_enhanced_prompt(), so inserting it again would be redundant.
        #
        # The prompt produced by assemble_enhanced_prompt() contains a
        # _HISTORY_INSERT_MARKER sentinel between CURRENT INPUT and QUESTION.
        # We replace that marker with the actual interaction-history block,
        # or strip it if no history is available.
        if (
            has_context_connection
            and has_upstream_context_value
            and input_source != "context"
        ):
            context_text_parts = []
            for cid in context_ids:
                ctx_val = self.variables.get(f"node_{cid}_context", "")
                if ctx_val and str(ctx_val).strip():
                    context_text_parts.append(str(ctx_val))
            if context_text_parts:
                # SIMPLIFIED: Just inject the history directly, no fancy headers
                # Small models get confused by elaborate section structures
                history_section = "\n".join(context_text_parts)

                if _HISTORY_INSERT_MARKER in prompt:
                    prompt = prompt.replace(_HISTORY_INSERT_MARKER, history_section)
                    logger.info(
                        f"Inserted context from {len(context_text_parts)} context node(s) into prompt (via marker)"
                    )
                else:
                    # Fallback: prepend history before the prompt
                    prompt = history_section + "\n\n" + prompt
                    logger.info(
                        f"Prepended context from {len(context_text_parts)} context node(s) to prompt (fallback)"
                    )
                # Debug: log a preview of the context being injected
                for i, part in enumerate(context_text_parts):
                    logger.info(f"  Context part {i + 1} preview: {part[:300]}...")

        # Strip the marker if it wasn't replaced (no context available)
        if _HISTORY_INSERT_MARKER in prompt:
            prompt = prompt.replace(_HISTORY_INSERT_MARKER, "")

        # Debug: log context detection state for diagnostics
        logger.info(
            f"Context detection: has_connection={has_context_connection}, has_value={has_upstream_context_value}, context_ids={context_ids}, input_source={input_source}"
        )
        for cid in context_ids:
            v = self.variables.get(f"node_{cid}_context", "<NOT SET>")
            logger.info(
                f"  node_{cid}_context: {'<EMPTY>' if v == '' else (str(v)[:200] + '...') if len(str(v)) > 200 else v}"
            )

        try:
            if self._rich_console:
                from rich.panel import Panel

                ctx_text = _context_input(node, self.variables)
                routes = []
                for inp in node.get("inputs", []) or []:
                    if str(inp.get("input_port")) == "context":
                        routes.append(str(inp.get("from_node")))
                ctx_info = f"Sources: {', '.join(routes) or 'None'}\n\n" + (
                    ctx_text[:800] or ""
                )
                self._rich_console.print(
                    Panel(ctx_info, title="Context Routing", border_style="green")
                )
        except Exception:
            pass

        # Determine engine before logging — avoid misleading "Ollama" messages for llama.cpp
        _use_llamacpp_flag = str(llm_config.get("use_llamacpp", False)).lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        if _use_llamacpp_flag:
            logger.info(f"Preparing llama.cpp request thread for base_url: {api_url}")
        else:
            logger.info(f"Preparing Ollama request thread for base_url: {api_url}")
        # Always attempt to stop running models to ensure clean state for the new one
        # unless explicitly disabled via OLLAMA_PRE_STOP=false
        # IMPORTANT: If using ComoRAG (API Server), do NOT stop models from the client side.
        # The API server manages its own models and stopping them here can cause race conditions or API failures.
        _base = f"http://{os.getenv('OLLAMA_HOST', 'localhost')}:{os.getenv('OLLAMA_PORT', '11434')}"
        try:
            _pre_stop = str(os.getenv("OLLAMA_PRE_STOP", "true")).lower() not in (
                "false",
                "0",
                "no",
                "off",
            )
            if _pre_stop and not use_comorag:
                logger.info("Checking for running Ollama models to unload...")
                try:
                    _ps = _rq.get(
                        f"{_base}/api/ps", headers={"Connection": "close"}, timeout=4
                    )
                    if _ps.status_code == 200:
                        _data = _ps.json() if hasattr(_ps, "json") else {}
                        _items = (
                            _data.get("models")
                            if isinstance(_data, dict)
                            else (_data if isinstance(_data, list) else [])
                        )

                        if isinstance(_items, list) and _items:
                            logger.info(f"Found {len(_items)} running models.")
                            for _m in _items:
                                _pid = _m.get("id") if isinstance(_m, dict) else None
                                _name = (
                                    (_m.get("model") or _m.get("name"))
                                    if isinstance(_m, dict)
                                    else None
                                )

                                # Optimization: Don't stop the model we are about to use
                                # improved matching to handle tags (e.g. llama3.2 vs llama3.2:latest)
                                if _name == model or (
                                    _name
                                    and model
                                    and (
                                        _name.startswith(model + ":")
                                        or model.startswith(_name + ":")
                                    )
                                ):
                                    logger.info(
                                        f"Model {_name} matches target {model}. Keeping it."
                                    )
                                    continue

                                # Optimization: Don't stop the embedding model if we are about to use it for RAG
                                if (
                                    use_direct_rag
                                    and rag_embedding_model
                                    and _name
                                    and (
                                        _name == rag_embedding_model
                                        or _name.startswith(rag_embedding_model + ":")
                                        or rag_embedding_model.startswith(_name + ":")
                                    )
                                ):
                                    logger.info(
                                        f"Model {_name} matches RAG embedding model {rag_embedding_model}. Keeping it."
                                    )
                                    continue

                                _payload = {}
                                if _pid:
                                    _payload["id"] = _pid
                                if _name:
                                    _payload["model"] = _name

                                if _payload:
                                    try:
                                        logger.info(
                                            f"Stopping competing model: {_name or _pid}"
                                        )
                                        _rq.post(
                                            f"{_base}/api/stop",
                                            json=_payload,
                                            headers={"Connection": "close"},
                                            timeout=3,
                                        )
                                    except Exception as e:
                                        logger.warning(
                                            f"Failed to stop model {_name}: {e}"
                                        )
                        else:
                            logger.debug("No running models found.")
                except Exception as e:
                    logger.warning(f"Failed to query/stop Ollama models: {e}")
        except Exception:
            pass

        rag_context_text = None
        rag_docs_context = None
        emb_client = None
        try:
            # Use direct Ollama connection for embeddings to avoid OverWatch queue/cleanup deadlocks
            ollama_host = os.getenv("OLLAMA_HOST", "localhost")
            ollama_port = os.getenv("OLLAMA_PORT", "11434")
            ollama_base_url = f"http://{ollama_host}:{ollama_port}"
            logger.info(
                f"RAG: Connecting to embedding client at {ollama_base_url} (Direct Ollama)"
            )
            emb_client = OllamaClient(
                base_url=ollama_base_url, debug=True, use_comorag=False
            )
        except Exception:
            logger.warning(
                "RAG: Failed to connect to direct Ollama, falling back to API URL"
            )
            emb_client = OllamaClient(
                base_url=api_url, debug=True, use_comorag=use_comorag
            )
        if (
            use_direct_rag
            and input_source in ("previous", "previous_node")
            and input_text
            and input_text.strip()
        ):
            pre_chunks = None
            pre_embs = None
            try:
                if connected_ids:
                    # iterate in reverse to find the most recent/relevant predecessor with a vectorstore
                    for prev_id in reversed(connected_ids):
                        store = self.variables.get(f"node_{prev_id}_vectorstore")
                        if isinstance(store, dict):
                            pre_chunks = store.get("chunks")
                            pre_embs = store.get("embeddings")
                            if pre_chunks and pre_embs:
                                logger.info(
                                    f"Using precomputed vectorstore from node {prev_id}"
                                )
                                break
            except Exception:
                pass
            _node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")
            rag_context_text, updated_prompt, vector_store = build_rag_context(
                emb_client,
                prompt,
                input_text,
                rag_embedding_model,
                rag_chunk_size,
                rag_overlap,
                rag_top_k,
                rag_include_raw_input,
                rag_max_chars,
                input_source,
                use_input_text_in_prompt,
                pre_chunks,
                pre_embs,
            )
            if updated_prompt:
                prompt = updated_prompt
            try:
                if vector_store and _node_id:
                    self.variables[f"node_{_node_id}_vectorstore"] = vector_store
            except Exception:
                pass

        try:
            import json as _json

            rag_docs = llm_config.get("rag_documents", []) or []
            if isinstance(rag_docs, str):
                rag_docs = _json.loads(rag_docs)
        except Exception:
            rag_docs = []
        if rag_docs and use_direct_rag and emb_client is not None:
            rag_docs_context = build_rag_context_from_docs(
                emb_client,
                prompt,
                rag_docs,
                rag_embedding_model,
                rag_chunk_size,
                rag_overlap,
                rag_top_k,
                rag_max_chars,
            )
            if rag_docs_context:
                prompt = f"{rag_docs_context}\n\n{prompt}"
                logger.info("Injected RAG document context into prompt")

        advanced_vision_image_path = None
        if use_advanced_vision:
            logger.info("Advanced Vision enabled. Starting analysis...")
            try:
                # Pre-check for torch to avoid WinError 1114 hard crash
                try:
                    # Fix for Intel OpenMP runtime duplicate error
                    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

                    # Ensure DLL load path on Windows
                    if os.name == "nt":
                        if hasattr(os, "add_dll_directory"):
                            # Try to find torch/lib in site-packages
                            import site

                            packages = []
                            try:
                                packages.extend(site.getsitepackages())
                            except Exception:
                                pass
                            try:
                                if hasattr(site, "getusersitepackages"):
                                    packages.append(site.getusersitepackages())
                            except Exception:
                                pass

                            # Also check current venv specifically
                            try:
                                venv_path = os.path.dirname(
                                    os.path.dirname(sys.executable)
                                )  # .venv/Scripts/python -> .venv
                                site_pkg = os.path.join(
                                    venv_path, "Lib", "site-packages"
                                )
                                if os.path.isdir(site_pkg):
                                    packages.append(site_pkg)
                            except Exception:
                                pass

                            for p in packages:
                                torch_lib = os.path.join(p, "torch", "lib")
                                if os.path.isdir(torch_lib):
                                    try:
                                        os.add_dll_directory(torch_lib)
                                        # Also prepend to PATH as a fallback for some DLL loaders
                                        os.environ["PATH"] = (
                                            torch_lib + os.pathsep + os.environ["PATH"]
                                        )
                                    except Exception:
                                        pass

                    import torch
                except ImportError:
                    logger.warning(
                        "Torch not found. Skipping Advanced Vision to avoid crash."
                    )
                    raise Exception("Torch not found")
                except Exception as e:
                    logger.warning(
                        f"Torch import failed ({e}). Skipping Advanced Vision to avoid crash."
                    )
                    raise e

                # Calculate path to Computer_vision module
                cv_dir = None
                if getattr(sys, "frozen", False):
                    # Frozen mode (PyInstaller)
                    base_path = getattr(
                        sys, "_MEIPASS", os.path.dirname(sys.executable)
                    )
                    exe_dir = os.path.dirname(sys.executable)
                    internal_dir = base_path
                    if os.path.basename(internal_dir).lower() != "_internal":
                        candidate_internal = os.path.join(exe_dir, "_internal")
                        if os.path.isdir(candidate_internal):
                            internal_dir = candidate_internal
                        else:
                            candidate_internal = os.path.join(base_path, "_internal")
                            if os.path.isdir(candidate_internal):
                                internal_dir = candidate_internal

                    possible_paths = [
                        os.path.join(
                            internal_dir, "LoOper", "player", "Computer_vision"
                        ),
                        os.path.join(exe_dir, "LoOper", "player", "Computer_vision"),
                        # Fallback for flattened structures
                        os.path.join(internal_dir, "player", "Computer_vision"),
                        os.path.join(exe_dir, "player", "Computer_vision"),
                    ]

                    for p in possible_paths:
                        if os.path.exists(p):
                            cv_dir = p
                            break

                if not cv_dir:
                    # Source mode or fallback
                    current_dir = os.path.dirname(os.path.abspath(__file__))
                    # executor.py is in LoOper/player/multi_sequence/llm_executor_resources/
                    # We need LoOper/player/Computer_vision/
                    # Up 3 levels to LoOper/player
                    looper_root = os.path.dirname(
                        os.path.dirname(os.path.dirname(current_dir))
                    )
                    cv_dir = os.path.join(looper_root, "player", "Computer_vision")

                if cv_dir and os.path.exists(cv_dir):
                    if cv_dir not in sys.path:
                        sys.path.append(cv_dir)
                    logger.info(f"Added Computer_vision path: {cv_dir}")
                else:
                    logger.warning(
                        f"Could not locate Computer_vision directory. Attempting import anyway."
                    )

                import json as _json

                import model_vision

                model_path = os.path.join(cv_dir, "Computer_Vision_1.5.3.onnx")
                if not os.path.exists(model_path):
                    logger.warning(f"Vision model not found at {model_path}")
                else:
                    # Initialize vision
                    vision_instance = model_vision.Vision(model_path)

                    vision_data = None
                    try:
                        if images and images[0] and os.path.exists(images[0]):
                            import cv2

                            screen_bgr = cv2.imread(images[0])
                            if screen_bgr is not None:
                                vision_data = vision_instance.analyze_frame(screen_bgr)
                    except Exception:
                        vision_data = None

                    if vision_data is None:
                        vision_data = vision_instance.analyze_single_frame(1)
                    advanced_vision_image_path = vision_data.get("image_path")

                    # Serialize to text description
                    vision_text = self._format_yolo_data(vision_data)

                    # Store vision data in variables for later retrieval in task runner
                    if _node_id:
                        self.variables[f"node_{_node_id}_vision_data"] = vision_text
                        # Also store raw path just in case
                        self.variables[f"node_{_node_id}_vision_image"] = (
                            advanced_vision_image_path
                        )

                    # Integrate with ComoRAG if enabled
                    vision_integrated = False
                    if use_comorag and emb_client:
                        logger.info(
                            "Injecting Advanced Vision data into ComoRAG memory..."
                        )
                        try:
                            # Create a descriptive content string
                            memory_content = (
                                f"Real-time Screen Analysis (YOLO+OCR):\n{vision_text}"
                            )

                            # Add to memory
                            mem_client = emb_client
                            try:
                                from AI.consult import OllamaClient as _MemClient

                                # IMPORTANT: Ensure we use the correct base_url for the API server (port 8010 usually)
                                # and ensure use_comorag is True for memory addition
                                current_api_url = api_url or get_default_api_url()

                                # Warning if URL seems to point to standard Ollama (11434) instead of ComoRAG (8010)
                                if (
                                    use_comorag
                                    and ":11434" in current_api_url
                                    and "8010" not in current_api_url
                                ):
                                    logger.warning(
                                        f"ComoRAG is enabled but API URL ({current_api_url}) looks like standard Ollama. Ensure ComoRAG API server is running on port 8010."
                                    )

                                if (
                                    not getattr(mem_client, "use_comorag", False)
                                    or getattr(mem_client, "base_url", "")
                                    != current_api_url
                                ):
                                    logger.info(
                                        f"Re-initializing memory client for ComoRAG at {current_api_url}"
                                    )
                                    mem_client = _MemClient(
                                        base_url=current_api_url,
                                        debug=True,
                                        use_comorag=True,
                                        timeout=None,
                                    )

                                # Pre-check API health to avoid ugly stack traces if server is down
                                try:
                                    if hasattr(mem_client, "health_check"):
                                        status_res = mem_client.health_check()
                                        if status_res.get("status") != "healthy":
                                            logger.warning(
                                                f"ComoRAG API at {current_api_url} reports unhealthy: {status_res}"
                                            )
                                            # If unhealthy, don't attempt add_memory to avoid timeout/hang
                                            mem_client = None
                                except Exception as he:
                                    logger.warning(f"ComoRAG health check failed: {he}")
                                    # If health check fails completely (connection error), disable client
                                    mem_client = None

                            except Exception as e:
                                logger.error(
                                    f"Failed to re-initialize memory client: {e}"
                                )
                                mem_client = None

                            if mem_client:
                                logger.info(
                                    f"ComoRAG memory client base_url: {getattr(mem_client, 'base_url', '')}"
                                )
                                # Use a string timestamp to avoid PowerShell Int32 overflow issues if payload is logged/processed by shell
                                ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
                                success = bool(
                                    mem_client.add_memory(
                                        content=memory_content,
                                        source="advanced_vision",
                                        metadata={
                                            "timestamp": ts_str,
                                            "type": "yolo_ocr_analysis",
                                            "monitor": 1,
                                            "raw_timestamp": time.time(),
                                        },
                                    )
                                )
                            else:
                                success = False

                            if success:
                                logger.info(
                                    "Successfully added vision data to ComoRAG memory."
                                )
                                vision_integrated = True
                            else:
                                logger.warning(
                                    "Failed to add vision data to ComoRAG memory (API might be down or busy)."
                                )
                        except Exception as mem_err:
                            logger.error(f"Error adding to ComoRAG memory: {mem_err}")

                    if not vision_integrated:
                        # Fallback to prompt injection
                        prompt = f"Screen Analysis Data (Objects, Text, Interactions):\n{vision_text}\n\n{prompt}"
                        logger.info("Advanced Vision data injected into prompt.")

            except Exception as e:
                logger.error(f"Advanced Vision execution failed: {e}")
                import traceback

                logger.error(traceback.format_exc())

        if use_advanced_vision:
            logger.info("Advanced Vision: running multi-model pipeline...")
            try:
                from AI.recursive_vision_reasoner import quick_vision_reason

                vision_context = (
                    self.variables.get(f"node_{_node_id}_vision_data", "") or ""
                )
                if not vision_context:
                    logger.warning("Advanced Vision pipeline: missing vision_data")
                else:
                    pipeline_model = llm_config.get("vision_model") or model
                    pipeline_cycles = 2
                    pipeline_conf = 0.85

                    logger.info(f"Advanced Vision pipeline model: {pipeline_model}")

                    reasoner_client = OllamaClient(
                        base_url=api_url, debug=False, use_comorag=False, timeout=None
                    )
                    quick_result = quick_vision_reason(
                        ollama_client=reasoner_client,
                        task=prompt,
                        vision_context=vision_context,
                        images=images
                        if (images and os.path.exists(images[0]))
                        else None,
                        model=pipeline_model,
                        max_cycles=pipeline_cycles,
                    )
                    try:
                        reasoner_client.close()
                    except Exception:
                        pass

                    if isinstance(quick_result, dict) and quick_result.get(
                        "final_plan"
                    ):
                        final_plan = quick_result.get("final_plan", []) or []
                        summary = quick_result.get("summary", "") or ""
                        cycles_used = int(quick_result.get("cycles_used", 0) or 0)
                        confidence = float(quick_result.get("confidence", 0.0) or 0.0)

                        logger.info(
                            f"Advanced Vision pipeline completed: {cycles_used} cycles, confidence: {confidence:.2f}"
                        )

                        plan_str = "\n".join(
                            [f"{i + 1}. {step}" for i, step in enumerate(final_plan)]
                        )
                        pipeline_context = (
                            "=== ADVANCED VISION PIPELINE RESULT ===\n"
                            f"Analysis Summary: {summary}\n\n"
                            f"Action Plan ({cycles_used} cycles, confidence: {confidence:.2f}):\n{plan_str}\n"
                            "======================================\n"
                        )

                        if use_comorag and api_url and str(api_url).strip():
                            try:
                                mem_client = OllamaClient(
                                    base_url=api_url,
                                    debug=False,
                                    use_comorag=True,
                                    timeout=None,
                                )
                                try:
                                    mem_client.set_comorag_config(
                                        max_cycles=max_cycles, memory_size=memory_size
                                    )
                                except Exception:
                                    pass
                                mem_client.add_memory(
                                    content=pipeline_context,
                                    source="advanced_vision_pipeline",
                                    metadata={
                                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "type": "advanced_vision_pipeline",
                                        "cycles": cycles_used,
                                        "confidence": confidence,
                                    },
                                )
                                try:
                                    mem_client.close()
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        prompt = f"{pipeline_context}\n\n{prompt}"
            except Exception:
                import traceback

                logger.error("Advanced Vision pipeline failed", exc_info=True)
                logger.error(traceback.format_exc())

        if advanced_vision_image_path and should_use_vision:
            images = [advanced_vision_image_path]
            logger.info(
                f"Using Advanced Vision annotated image for vision: {advanced_vision_image_path}"
            )

        try:
            if emb_client:
                emb_client.close()
        except Exception:
            pass

        if rag_context_text:
            prompt = f"{rag_context_text}\n\n{prompt}"
        if rag_docs_context:
            prompt = f"{rag_docs_context}\n\n{prompt}"
        if (
            use_direct_rag
            and not rag_context_text
            and input_source in ("previous", "previous_node")
            and input_text
        ):
            prompt = assemble_enhanced_prompt(
                prompt, input_source, input_text, include_raw=True
            )

        # Inject chain modules context to encourage composition over rebuilding
        try:
            chain_ctx = self.variables.get("chain_modules_context") or ""
            if chain_ctx:
                guidance = (
                    "\n\nUSING PREVIOUS MODULES:\n"
                    "The following modules are already available as variables in the execution scope.\n"
                    "Use them directly by their listed names; do not recreate their functionality.\n"
                    "If you need file imports, paths are provided. Prefer composing by calling their functions/classes.\n"
                    "\nMODULE DESIGN RULES:\n"
                    "- Do NOT launch GUI or perform side-effects at import time.\n"
                    "- Place any demo/app launch under: if __name__ == '__main__':\n"
                    "- Expose clean functions/classes (e.g., create_window(), make_button()).\n"
                    "- The final composition script will import and orchestrate these components.\n\n"
                )
                prompt = f"{prompt}\n\n{guidance}{chain_ctx}"
        except Exception:
            pass

        # Enforce a single UI framework across the chain
        try:
            chain_fw = (self.variables.get("chain_framework") or "").strip()
            if chain_fw:
                prompt = (
                    f"{prompt}\n\nFRAMEWORK CONSISTENCY:\n"
                    f"Use the existing framework: {chain_fw}. Do NOT switch frameworks.\n"
                    f"Integrate by importing and composing available modules; avoid rewriting components in a different framework."
                )
        except Exception:
            pass

        if stop_flag and stop_flag():
            logger.info("LLM node execution stopped by user request before API call")
            return None

        # Debug: log the final prompt that will be sent to the LLM
        logger.info(
            f"FINAL PROMPT for LLM (length={len(prompt)} chars):\n{prompt[:2000]}{'...[TRUNCATED]' if len(prompt) > 2000 else ''}"
        )
        if system_message:
            logger.info(f"SYSTEM MESSAGE for LLM: {system_message[:500]}")

        def _task_runner(out_dict: Dict[str, Any]):
            if stop_flag and stop_flag():
                return

            _node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")

            try:
                # Check if using llama.cpp
                use_llamacpp = llm_config.get("use_llamacpp", False)
                if isinstance(use_llamacpp, str):
                    use_llamacpp = use_llamacpp.lower() in ("true", "1", "yes", "on")

                if use_llamacpp:
                    # Use llama.cpp engine
                    logger.info("Using llama.cpp engine for LLM execution")
                    llamacpp_model_path = llm_config.get("llamacpp_model_path", "")
                    llamacpp_gpu_layers = int(
                        llm_config.get("llamacpp_gpu_layers", 35) or 35
                    )
                    llamacpp_threads = int(llm_config.get("llamacpp_threads", 8) or 8)
                    llamacpp_context_size = int(
                        llm_config.get("llamacpp_context_size", 4096) or 4096
                    )

                    if not llamacpp_model_path:
                        logger.error("llama.cpp model path not specified")
                        out_dict["error"] = "llama.cpp model path not specified"
                        return

                    # Resolve relative model path against LoOper/AI/models/
                    if not os.path.isabs(llamacpp_model_path):
                        try:
                            # Go up from player/multi_sequence/llm_executor_resources/ to LoOper/
                            current_file = os.path.abspath(__file__)
                            looper_dir = os.path.dirname(
                                os.path.dirname(
                                    os.path.dirname(os.path.dirname(current_file))
                                )
                            )
                            models_dir = os.path.join(looper_dir, "AI", "models")
                            resolved_path = os.path.normpath(
                                os.path.join(models_dir, llamacpp_model_path)
                            )
                            logger.debug(
                                f"Resolved llama.cpp model path: {llamacpp_model_path} -> {resolved_path}"
                            )
                            llamacpp_model_path = resolved_path
                        except Exception as e:
                            logger.warning(
                                f"Failed to resolve relative model path: {e}"
                            )

                    if not os.path.exists(llamacpp_model_path):
                        logger.error(
                            f"llama.cpp model not found: {llamacpp_model_path}"
                        )
                        out_dict["error"] = f"Model not found: {llamacpp_model_path}"
                        return

                    # Build llama.cpp API payload
                    try:
                        import requests as llm_req

                        llamacpp_api_url = api_url.rstrip("/")

                        payload = {
                            "model": llamacpp_model_path,
                            "prompt": effective_prompt
                            if "effective_prompt" in locals()
                            else prompt,
                            "system": system_message or "",
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "stream": False,
                            "use_llamacpp": True,
                            "gpu_layers": llamacpp_gpu_layers,
                            "threads": llamacpp_threads,
                            "context_size": llamacpp_context_size,
                        }
                        # Include images for vision-capable GGUF models
                        # images is captured from the outer closure (prepare_vision_images)
                        if images is not None:
                            payload["images"] = images
                            logger.info(
                                f"Including {len(images)} image(s) in llama.cpp payload"
                            )
                        else:
                            logger.info(
                                "No images in llama.cpp payload (images is None)"
                            )

                        logger.info(
                            f"Sending request to llama.cpp endpoint: {llamacpp_api_url}/llamacpp/generate"
                        )
                        response = llm_req.post(
                            f"{llamacpp_api_url}/llamacpp/generate",
                            json=payload,
                            timeout=None,  # No timeout — wait for model to complete
                        )

                        if response.status_code == 200:
                            result = response.json()
                            out_dict["result"] = result
                            logger.info(
                                f"llama.cpp generation successful: {len(result.get('response', ''))} chars, "
                                f"response keys: {list(result.keys())}"
                            )
                            _debug = result.get("_debug")
                            if (
                                _debug
                                and isinstance(_debug, dict)
                                and any(_debug.values())
                            ):
                                logger.info(
                                    f"llama.cpp API debug: {json.dumps(_debug)}"
                                )
                            elif _debug:
                                logger.info(
                                    f"llama.cpp _debug field present but empty: {json.dumps(_debug)}"
                                )
                        else:
                            error_msg = f"llama.cpp API error: {response.status_code} - {response.text}"
                            logger.error(error_msg)
                            out_dict["error"] = error_msg
                    except Exception as llamacpp_err:
                        error_msg = f"llama.cpp execution failed: {str(llamacpp_err)}"
                        logger.error(error_msg, exc_info=True)
                        out_dict["error"] = error_msg
                    return

                # Otherwise use Ollama (existing code)
                from AI.consult import OllamaClient as _Client

                # Disable client request timeouts for local LLMs
                cli = _Client(
                    base_url=api_url, debug=True, use_comorag=use_comorag, timeout=None
                )
                if use_comorag and hasattr(cli, "set_comorag_config"):
                    cli.set_comorag_config(
                        max_cycles=max_cycles, memory_size=memory_size
                    )

                # --- Vision Pass Logic ---
                effective_prompt = prompt
                effective_images = images

                vision_model = llm_config.get("vision_model")
                use_advanced_vision = str(
                    llm_config.get("use_advanced_vision", "false")
                ).lower() in ("true", "1", "yes", "on")

                # If a separate vision model is explicitly configured AND advanced vision is on,
                # use that model to analyze images first, then pass the description to the main model.
                # Otherwise, images go directly to the main model (default behavior).
                has_separate_vision = (
                    vision_model
                    and str(vision_model).strip()
                    and str(vision_model) != str(model)
                )
                if effective_images and has_separate_vision and use_advanced_vision:
                    logger.info(
                        f"Running separate vision pass with model: {vision_model}"
                    )
                    try:
                        # Construct a vision-specific prompt
                        # Ask for spatial layout to correlate with YOLO data
                        vision_prompt = (
                            f"Please analyze this screen image in detail. "
                            f"Describe the visual layout, specifically identifying input fields, buttons, and text labels. "
                            f"Explain where each element is located relative to others (e.g., 'below the Name label'). "
                            f"\n\nContext from user: {effective_prompt}"
                        )

                        # Use standard generation (no ComoRAG for the vision pass to keep it fast)
                        # We temporarily disable ComoRAG on the client for this call if needed,
                        # but cli.generate uses the instance flag.
                        # We can create a temp client or just call generate.
                        # Since we want to bypass ComoRAG for this pre-pass (usually),
                        # let's assume standard generation is fine.
                        # However, cli.generate uses self.use_comorag.

                        # Create a lightweight client for vision pass if ComoRAG is on
                        v_cli = cli
                        if use_comorag:
                            v_cli = _Client(
                                base_url=api_url,
                                debug=True,
                                use_comorag=False,
                                timeout=None,
                            )

                        vision_res = v_cli.generate(
                            model=vision_model,
                            prompt=vision_prompt,
                            images=effective_images,
                            temperature=0.5,  # Lower temp for accurate description
                            max_tokens=1024,
                        )

                        vision_text = None
                        if isinstance(vision_res, dict):
                            vision_text = (
                                vision_res.get("response")
                                or vision_res.get("text")
                                or (vision_res.get("message") or {}).get("content")
                            )
                        elif isinstance(vision_res, str):
                            vision_text = vision_res

                        if vision_text:
                            logger.info(
                                f"Vision pass complete. Length: {len(vision_text)}"
                            )

                            # Retrieve formatted YOLO data
                            yolo_data = self.variables.get(
                                f"node_{_node_id}_vision_data", ""
                            )

                            # Construct unified prompt
                            screen_context = (
                                "=== UNIFIED SCREEN CONTEXT ===\n"
                                f"1. VISUAL DESCRIPTION (General Layout):\n{vision_text}\n\n"
                                f"2. DETECTED UI ELEMENTS (Detailed Metadata):\n{yolo_data}\n"
                                "==============================\n"
                            )

                            effective_prompt = (
                                f"{screen_context}\n\nUSER REQUEST:\n{effective_prompt}"
                            )
                            # Clear images — the main model only gets the text description
                            effective_images = None
                    except Exception as ve:
                        logger.error(f"Vision pass failed: {ve}")
                        # If vision pass fails, we keep images and try main model as fallback
                        pass

                # If separate vision pass didn't run (no vision_model or advanced vision off),
                # but we have YOLO data, inject it now
                if not (
                    effective_images and has_separate_vision and use_advanced_vision
                ):
                    yolo_data = self.variables.get(f"node_{_node_id}_vision_data", "")
                    if yolo_data and "UNIFIED SCREEN CONTEXT" not in effective_prompt:
                        effective_prompt = f"DETECTED UI ELEMENTS (YOLO):\n{yolo_data}\n\nUSER REQUEST:\n{effective_prompt}"

                # Mark writing active for streaming typing paths
                if use_comorag:
                    res = cli.generate(
                        model=model,
                        prompt=effective_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        system=system_message or None,
                        images=effective_images,
                        context=None,
                        async_request=False,
                    )
                    # Check for fallback
                    if isinstance(res, dict) and res.get("comorag_fallback"):
                        logger.warning(
                            f"ComoRAG execution failed and fell back to standard generation: {res.get('comorag_error')}"
                        )
                    elif isinstance(res, dict) and isinstance(
                        res.get("comorag_metadata"), dict
                    ):
                        # Log success stats
                        meta = res.get("comorag_metadata")
                        cycles = meta.get("reasoning_cycles", 0)
                        logger.info(
                            f"ComoRAG execution completed with {cycles} reasoning cycles."
                        )
                        if cycles == 0:
                            logger.warning(
                                "ComoRAG returned 0 reasoning cycles. Check OverWatch logs for details."
                            )
                    out_dict["result"] = res
                else:
                    stream_flag = bool(llm_config.get("stream", True)) and (
                        not tool_ids
                    )
                    out_dict["stream_used"] = stream_flag
                    if (
                        stream_flag
                        and write_text
                        and action_handlers
                        and hasattr(action_handlers, "handle_type_string_action")
                        and hasattr(cli, "chat_stream")
                    ):
                        write_mode = str(llm_config.get("write_mode") or "type").lower()
                        _accum = []
                        # Signal: upstream LLM is actively typing via stream
                        try:
                            if _node_id:
                                self.variables[f"node_{_node_id}_writing"] = True
                        except Exception:
                            pass
                        if write_mode == "paste":
                            res = cli.chat_stream(
                                model=model,
                                prompt=effective_prompt,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                system=system_message or None,
                                images=effective_images,
                                on_delta=None,
                            )
                        else:
                            _buf = []
                            _full_text_tracker = ""
                            _last_raw_chunk = None
                            _last_flush = time.time()
                            _FLUSH_CHARS = int(
                                llm_config.get("stream_flush_chars", 120) or 120
                            )
                            _FLUSH_MS = float(
                                llm_config.get("stream_flush_ms", 0.25) or 0.25
                            )

                            def _flush_buffer():
                                if stop_flag and stop_flag():
                                    return
                                if not _buf:
                                    return
                                try:
                                    chunk = "".join(_buf)
                                    _buf.clear()
                                    action_handlers.handle_type_string_action(
                                        0,
                                        {
                                            "text": chunk,
                                            "force_typing": True,
                                            "batch_size": int(
                                                llm_config.get("typing_batch_size", 5)
                                                or 5
                                            ),
                                            "batch_delay": float(
                                                llm_config.get(
                                                    "typing_batch_delay", 0.01
                                                )
                                                or 0.01
                                            ),
                                            "char_by_char": bool(
                                                llm_config.get("char_by_char", False)
                                            ),
                                            "keystroke_delay": float(
                                                llm_config.get("keystroke_delay", 0.001)
                                                or 0.001
                                            ),
                                        },
                                        stop_flag,
                                    )
                                except Exception:
                                    pass

                            def _on_delta(s: str):
                                nonlocal _full_text_tracker, _last_raw_chunk
                                if stop_flag and stop_flag():
                                    raise Exception("Stopped by user")
                                try:
                                    # Handle case where backend sends full text instead of deltas
                                    real_delta = s

                                    # Determine if we are in Full Text mode (s extends previous chunk)
                                    is_full_text = False
                                    if (
                                        _last_raw_chunk is not None
                                        and len(s) > len(_last_raw_chunk)
                                        and s.startswith(_last_raw_chunk)
                                    ):
                                        is_full_text = True
                                    elif (
                                        len(_full_text_tracker) > 0
                                        and len(s) > len(_full_text_tracker)
                                        and s.startswith(_full_text_tracker)
                                    ):
                                        is_full_text = True

                                    if is_full_text:
                                        # Full Text Mode
                                        if _last_raw_chunk and s.startswith(
                                            _last_raw_chunk
                                        ):
                                            real_delta = s[len(_last_raw_chunk) :]
                                            # We must update tracker to match s because we trust s is the new full text
                                            _full_text_tracker = s
                                        elif s.startswith(_full_text_tracker):
                                            real_delta = s[len(_full_text_tracker) :]
                                            _full_text_tracker = s
                                        else:
                                            # Fallback (should be covered by elif above)
                                            real_delta = ""
                                    else:
                                        # Delta Mode or Duplicate
                                        if s == _full_text_tracker or (
                                            _last_raw_chunk and s == _last_raw_chunk
                                        ):
                                            # Duplicate
                                            real_delta = ""
                                        elif (
                                            _full_text_tracker
                                            and _full_text_tracker.startswith(s)
                                        ):
                                            # Regression / Prefix Duplicate (ignore)
                                            real_delta = ""
                                        else:
                                            # Standard Delta
                                            real_delta = s
                                            _full_text_tracker += s

                                    _last_raw_chunk = s

                                    if not real_delta:
                                        return

                                    _accum.append(real_delta)
                                    _buf.append(real_delta)
                                    try:
                                        if _node_id:
                                            _cur = out_dict.get("_accum_text") or ""
                                            _cur += real_delta
                                            out_dict["_accum_text"] = _cur
                                            self.variables[
                                                f"node_{_node_id}_output"
                                            ] = _cur
                                            self.variables[output_variable] = _cur
                                    except Exception:
                                        pass
                                    if (
                                        sum(len(x) for x in _buf) >= _FLUSH_CHARS
                                        or (time.time() - _last_flush) >= _FLUSH_MS
                                    ):
                                        _flush_buffer()
                                        _last_flush = time.time()
                                except Exception:
                                    pass

                            res = cli.chat_stream(
                                model=model,
                                prompt=effective_prompt,
                                temperature=temperature,
                                max_tokens=max_tokens,
                                system=system_message or None,
                                images=effective_images,
                                on_delta=_on_delta,
                            )
                            # Final flush for remaining buffered text
                            try:
                                _flush_buffer()
                            except Exception:
                                pass
                        # If streaming returned no result but we accumulated deltas, construct a response
                        if (not isinstance(res, dict)) or (
                            isinstance(res, dict)
                            and not (res.get("response") or res.get("text"))
                        ):
                            if _accum:
                                res = {"response": "".join(_accum)}
                        # Detect streaming failure and fallback to non-stream
                        if (not isinstance(res, dict)) or (
                            isinstance(res, dict)
                            and (res.get("error") or not res.get("response"))
                        ):
                            out_dict["stream_failed"] = True
                            try:
                                res2 = cli.chat(
                                    model=model,
                                    prompt=effective_prompt,
                                    temperature=temperature,
                                    max_tokens=max_tokens,
                                    system=system_message or None,
                                    images=effective_images,
                                    stream=False,
                                )
                                res = res2
                            except Exception:
                                pass
                        out_dict["result"] = res
                    else:
                        res = cli.chat(
                            model=model,
                            prompt=effective_prompt,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            system=system_message or None,
                            images=effective_images,
                            stream=False,
                        )
                        out_dict["result"] = res
            except Exception as _e:
                logger.error(f"LLM task runner exception: {_e}", exc_info=True)
                out_dict["error"] = str(_e)
            finally:
                # Clear typing-active signal
                try:
                    if _node_id:
                        self.variables.pop(f"node_{_node_id}_writing", None)
                except Exception:
                    pass

                # Only stop models if NOT using ComoRAG (API Server manages its own models)
                if not use_comorag:
                    try:
                        _rq.post(
                            f"{_base}/api/stop",
                            json={"model": model},
                            headers={"Connection": "close"},
                            timeout=3,
                        )
                    except Exception:
                        pass
                    try:

                        def _find_ollama_bin():
                            cands = []
                            for k in ("OLLAMA_BIN", "OLLAMA_EXE"):
                                v = os.getenv(k)
                                if v:
                                    cands.append(v)
                            la = os.getenv("LOCALAPPDATA")
                            if la:
                                cands.append(
                                    os.path.join(la, "Programs", "Ollama", "ollama.exe")
                                )
                            cands.append(r"C:\\Program Files\\Ollama\\ollama.exe")
                            cands.append("ollama")
                            return cands

                        env = os.environ.copy()
                        cflags = 0x08000000 if os.name == "nt" else 0
                        for cmd in _find_ollama_bin():
                            try:
                                _sp.run(
                                    [cmd, "stop", model],
                                    capture_output=True,
                                    text=True,
                                    timeout=4,
                                    env=env,
                                    creationflags=cflags,
                                )
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass

                try:
                    cli.close()
                except Exception:
                    pass

        shared: Dict[str, Any] = {}
        embedding_selected_tool = None

        # Embedding-based tool selection (User Request: "LLM too small")
        if tool_ids and tool_desc_map:
            try:
                # Construct query from prompt and input
                query_text = prompt
                if input_text and not use_input_text_in_prompt:
                    query_text = f"{input_text}\n\n{query_text}"

                # Check for cached model preference or default
                embed_model_name = llm_config.get(
                    "tool_embedding_model", "all-MiniLM-L6-v2"
                )

                # Only proceed if we can import sentence_transformers
                try:
                    from sentence_transformers import SentenceTransformer, util

                    logger.info(
                        f"Attempting embedding-based tool selection using {embed_model_name}"
                    )

                    # Initialize model (downloads if needed, checks cache)
                    embedder = SentenceTransformer(embed_model_name)

                    tool_corpus = []
                    tool_corpus_ids = []

                    for tid in tool_ids:
                        desc = tool_desc_map.get(tid, "")
                        # Combine ID and description
                        text_to_embed = f"{tid}: {desc}" if desc else str(tid)
                        tool_corpus.append(text_to_embed)
                        tool_corpus_ids.append(tid)

                    if tool_corpus:
                        query_embedding = embedder.encode(
                            query_text, convert_to_tensor=True
                        )
                        corpus_embeddings = embedder.encode(
                            tool_corpus, convert_to_tensor=True
                        )

                        # Find best match
                        hits = util.semantic_search(
                            query_embedding, corpus_embeddings, top_k=1
                        )

                        if hits and hits[0]:
                            best_hit = hits[0][0]
                            score = best_hit["score"]
                            idx = best_hit["corpus_id"]

                            logger.info(
                                f"Embedding tool match: {tool_corpus_ids[idx]} (score={score:.4f})"
                            )

                            # Threshold for selection
                            if score > 0.35:
                                embedding_selected_tool = tool_corpus_ids[idx]
                                logger.info(
                                    f"Auto-selected tool via embeddings: {embedding_selected_tool}"
                                )
                except ImportError:
                    logger.debug(
                        "sentence_transformers not available for tool selection"
                    )
                except Exception as e:
                    logger.warning(f"Embedding tool selection error: {e}")
            except Exception:
                pass

        if embedding_selected_tool:
            shared["result"] = {"response": f"USE_TOOL:{embedding_selected_tool}"}
            shared["stream_used"] = False
        else:
            logger.info("Sending request to LLM API in thread")
            th = threading.Thread(target=_task_runner, args=(shared,), daemon=True)
            th.start()

            # Disable request timeouts for local LLMs
            timeout_s = 0.0

            start_t = time.time()
            while th.is_alive():
                if stop_flag and stop_flag():
                    logger.info(
                        "Stop flag detected. Attempting to stop LLM generation..."
                    )
                    if not use_comorag:
                        try:
                            for _ in range(2):
                                _rq.post(
                                    f"{_base}/api/stop",
                                    json={"model": model},
                                    headers={"Connection": "close"},
                                    timeout=2,
                                )
                                time.sleep(0.1)
                        except Exception:
                            pass
                    break
                # No timeout: allow long-running local models to complete
                try:
                    time.sleep(0.05)
                except Exception:
                    break
            try:
                th.join(timeout=0.2)
            except Exception:
                pass
        result = shared.get("result")
        if result is None and shared.get("error"):
            logger.error(f"LLM request failed: {shared.get('error')}")
            # Continue execution with empty response to avoid workflow stall
            result = {"response": ""}
        # If no explicit result but streaming accumulated text exists, use it
        if result is None and isinstance(shared.get("stream_used"), bool):
            # Attempt to salvage from a typed buffer stored via variables
            try:
                # Prefer node-specific output if already captured by action handler
                node_id = (
                    llm_data.get("node_id") or llm_data.get("id") or node.get("id")
                )
                fallback_text = None
                if node_id:
                    fallback_text = self.variables.get(f"node_{node_id}_output")
                fallback_text = fallback_text or self.variables.get(output_variable)
                if fallback_text:
                    result = {"response": str(fallback_text)}
            except Exception:
                pass

        # Normalize response
        response_text = None
        if isinstance(result, dict):
            # Try various keys, and explicit dict lookups
            if "response" in result:
                response_text = result["response"]
            elif "text" in result:
                response_text = result["text"]
            elif "message" in result and isinstance(result["message"], dict):
                response_text = result["message"].get("content")

            # Convert non-string responses safely
            if response_text is not None and not isinstance(response_text, str):
                response_text = str(response_text)
        # If tool selection is expected, extract and sanitize output to the marker only
        selected_tool_id = None
        if tool_ids and isinstance(response_text, str) and response_text:
            try:
                for marker in ("USE_TOOL:", "CALL_TOOL:", "TOOL:"):
                    idx = response_text.find(marker)
                    if idx >= 0:
                        val = response_text[idx + len(marker) :].strip()
                        val = val.split()[0].strip()
                        while val and val[-1] in (":", ",", ";", ".", ")", "]", "}"):
                            val = val[:-1]
                        if val:
                            selected_tool_id = val
                            break
                # Accept bare tool id if model omitted marker
                if (not selected_tool_id) and (response_text.strip() in tool_ids):
                    selected_tool_id = response_text.strip()
                # Scan for any known tool id present anywhere in text
                if not selected_tool_id:
                    for tid in tool_ids:
                        if tid and (str(tid) in response_text):
                            selected_tool_id = tid
                            break
                # Scan by tool description keywords (e.g., open_browser/close_browser)
                if not selected_tool_id and isinstance(tool_desc_map, dict):
                    rt_lower = response_text.lower()
                    for tid, desc in tool_desc_map.items():
                        if not tid:
                            continue
                        d = str(desc or "")
                        if not d:
                            continue
                        d_lower = d.lower()
                        if d_lower and d_lower in rt_lower:
                            selected_tool_id = tid
                            break
                        tail = d.split(":")[-1].strip().lower()
                        if tail and tail in rt_lower:
                            selected_tool_id = tid
                            break
                if selected_tool_id and (selected_tool_id in tool_ids):
                    response_text = f"USE_TOOL:{selected_tool_id}"
            except Exception:
                pass
        # Semantic fallback based on previous/context input when no explicit selection detected
        if (not selected_tool_id) and tool_ids:
            try:
                sctx = input_text or _context_input(node, self.variables) or ""
                sl = str(sctx).lower()
                want_open = False
                want_close = False
                neg_indicators = (
                    "no browser",
                    "not open",
                    "closed",
                    "no visible",
                    "none open",
                )
                pos_indicators = (
                    "browser is open",
                    "is open",
                    "open window",
                    "visible browser",
                )
                for w in neg_indicators:
                    if w in sl:
                        want_open = True
                        break
                if not want_open:
                    for w in pos_indicators:
                        if w in sl:
                            want_close = True
                            break
                # Prefer mapping via descriptions first
                if want_open and isinstance(tool_desc_map, dict):
                    for tid in tool_ids:
                        desc = str(tool_desc_map.get(tid, "")).lower()
                        if (
                            ("open_browser" in desc)
                            or ("open browser" in desc)
                            or (desc.endswith("open_browser"))
                        ):
                            selected_tool_id = tid
                            break
                if (
                    (not selected_tool_id)
                    and want_close
                    and isinstance(tool_desc_map, dict)
                ):
                    for tid in tool_ids:
                        desc = str(tool_desc_map.get(tid, "")).lower()
                        if (
                            ("close_browser" in desc)
                            or ("close browser" in desc)
                            or (desc.endswith("close_browser"))
                        ):
                            selected_tool_id = tid
                            break
                # Fallback by simple name heuristics if descriptions are missing
                if not selected_tool_id:
                    if want_open:
                        for tid in tool_ids:
                            nm = str(tool_desc_map.get(tid, "")).lower()
                            if "open" in nm and "close" not in nm:
                                selected_tool_id = tid
                                break
                    elif want_close:
                        for tid in tool_ids:
                            nm = str(tool_desc_map.get(tid, "")).lower()
                            if "close" in nm and "open" not in nm:
                                selected_tool_id = tid
                                break
                if selected_tool_id and (selected_tool_id in tool_ids):
                    response_text = f"USE_TOOL:{selected_tool_id}"
            except Exception:
                pass
        if (
            is_form_filling_preset
            and (not tool_ids)
            and isinstance(response_text, str)
            and response_text
        ):
            try:
                import re as _re

                def _sanitize_action_commands(t: str) -> str:
                    raw = (t or "").strip()
                    if not raw:
                        return ""
                    m_done = _re.search(r"\bDONE\b", raw, flags=_re.IGNORECASE)
                    if m_done:
                        return "DONE"

                    patt = _re.compile(
                        r"(CLICK:\s*\d+\s*,\s*\d+|TYPE:\s*[^\r\n]+|SCROLL:\s*-?\d+|WAIT:\s*\d+(?:\.\d+)?)",
                        flags=_re.IGNORECASE,
                    )
                    matches = [(m.start(), m.group(1)) for m in patt.finditer(raw)]
                    if not matches:
                        lines = []
                        for ln in raw.splitlines():
                            ln = ln.strip()
                            if ln:
                                lines.append(ln)
                        return "\n".join(lines[:3])

                    cmds = []
                    for _, s in sorted(matches, key=lambda x: x[0]):
                        s2 = s.strip()
                        up = s2.upper()
                        if up.startswith("CLICK:"):
                            s2 = "CLICK:" + s2.split(":", 1)[1]
                        elif up.startswith("TYPE:"):
                            s2 = "TYPE:" + s2.split(":", 1)[1]
                        elif up.startswith("SCROLL:"):
                            s2 = "SCROLL:" + s2.split(":", 1)[1]
                        elif up.startswith("WAIT:"):
                            s2 = "WAIT:" + s2.split(":", 1)[1]
                        cmds.append(s2.strip())

                    final = []
                    saw_click = False
                    saw_type = False
                    # Allow multiple clicks/types/scrolls in a plan (up to 10)
                    for c in cmds:
                        cup = c.upper()
                        if (
                            cup.startswith("CLICK:")
                            or cup.startswith("TYPE:")
                            or cup.startswith("SCROLL:")
                            or cup.startswith("WAIT:")
                        ):
                            final.append(c)

                        # Keep it to a reasonable plan size
                        if len(final) >= 10:
                            break

                    return "\n".join(final).strip()

                response_text = _sanitize_action_commands(response_text)
            except Exception:
                pass
        if not response_text:
            logger.error(f"LLM returned unexpected format or empty response: {result}")
            # Proceed with empty text to allow chaining
            response_text = ""
        try:
            if self._rich_console:
                from rich.panel import Panel

                self._rich_console.rule("[bold green]LLM Response")
                self._rich_console.print(
                    Panel(
                        response_text or "", title="Model Output", border_style="green"
                    )
                )
        except Exception:
            pass

        # Strip think tags from response before storing as output
        try:
            response_text = _strip_think_tags(response_text)
        except Exception:
            pass

        # Store outputs and artifact metadata
        try:
            self.variables[output_variable] = response_text
            node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")
            if node_id:
                self.variables[f"node_{node_id}_output"] = response_text
                try:
                    import re

                    nm = llm_data.get("name") or ""
                    if not nm:
                        nm = f"mod_{node_id}"
                    s = re.sub(r"\W+", "_", str(nm).strip())
                    if not s:
                        s = "mod"
                    if not re.match(r"^[A-Za-z_]", s):
                        s = "mod_" + s
                    alias = "module_" + s
                    desc = (
                        llm_config.get("prompt")
                        or llm_config.get("system_message")
                        or ""
                    )
                    desc = str(desc).strip().split("\n")[0]
                    self.variables[f"node_{node_id}_artifact_alias"] = alias
                    self.variables[f"node_{node_id}_artifact_desc"] = desc
                except Exception:
                    pass
                try:
                    chain_ctx = self.variables.get("chain_modules_context") or ""
                    if chain_ctx:
                        self.variables[f"node_{node_id}_context"] = (
                            (response_text or "") + "\n\n" + str(chain_ctx)
                        )
                    else:
                        self.variables[f"node_{node_id}_context"] = response_text
                except Exception:
                    self.variables[f"node_{node_id}_context"] = response_text
        except Exception:
            pass

        # Optional: write text via handlers when not streaming
        try:
            # Check if streaming was actually used.
            # If ComoRAG was used, streaming is bypassed even if 'stream' is True in config.
            # So we rely on the runtime flag 'stream_used' from the task runner.
            was_streamed = shared.get("stream_used", False)

            # --- Parse and execute Form Filling Preset Actions ---
            form_action_taken = False
            if is_form_filling_preset and action_handlers and response_text:
                logger.info("Parsing LLM response for Form Filling actions...")
                lines = response_text.split("\n")
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue

                    if stop_flag and stop_flag():
                        break

                    try:
                        if line.startswith("CLICK:"):
                            coords = line[6:].strip().split(",")
                            if len(coords) >= 2:
                                x = int(float(coords[0].strip()))
                                y = int(float(coords[1].strip()))
                                action_handlers.bot.human_mouse_move(x, y)
                                action_handlers.bot.human_click(button="left")
                                logger.info(f"LLM clicked at {x}, {y}")
                                form_action_taken = True
                        elif line.startswith("TYPE:"):
                            text = line[5:].strip()
                            action = {
                                "text": text,
                                "force_typing": True,
                                "batch_size": 5,
                                "batch_delay": 0.01,
                                "char_by_char": False,
                                "keystroke_delay": 0.001,
                            }
                            action_handlers.handle_type_string_action(
                                0, action, stop_flag
                            )
                            form_action_taken = True
                        elif line.startswith("SCROLL:"):
                            delta = int(float(line[7:].strip()))
                            action = {
                                "total_delta": delta,
                                "steps": max(1, abs(delta) // 100),
                                "duration_sec": 0.2,
                            }
                            action_handlers.handle_scroll_action(0, action, stop_flag)
                            form_action_taken = True
                        elif line.startswith("WAIT:"):
                            delay = float(line[5:].strip())
                            time.sleep(delay)
                            form_action_taken = True
                    except Exception as e:
                        logger.warning(
                            f"Failed to execute form filling action '{line}': {e}"
                        )
            # --- Standard text writing ---
            elif (
                write_text
                and action_handlers
                and hasattr(action_handlers, "handle_type_string_action")
                and response_text
                and (not was_streamed or bool(shared.get("stream_failed")))
            ):
                logger.info("Writing LLM output via action handlers")

                # Prevent repetition if streaming partially succeeded but failed at the end
                text_to_write = response_text
                if bool(shared.get("stream_failed")) and bool(
                    llm_config.get("stream", True)
                ):
                    accumulated = shared.get("_accum_text", "")
                    if accumulated and text_to_write.startswith(accumulated):
                        text_to_write = text_to_write[len(accumulated) :]
                        logger.info(
                            f"Skipping {len(accumulated)} chars already typed during partial stream"
                        )
                    elif accumulated:
                        # Fallback: if not a clean prefix, we might have a conflict.
                        # For now, we write the full text to ensure correctness,
                        # or we could try to find overlap.
                        # Given the user complaint about repetition, let's try to be smart.
                        # If the new text is just a continuation, we might see it.
                        pass

                if text_to_write:
                    write_mode = str(llm_config.get("write_mode") or "type").lower()
                    if write_mode == "paste":
                        try:
                            node_id = (
                                llm_data.get("node_id")
                                or llm_data.get("id")
                                or node.get("id")
                            )
                            if node_id:
                                self.variables[f"node_{node_id}_writing"] = True
                        except Exception:
                            pass
                        action = {"text": text_to_write, "use_clipboard": True}
                    else:
                        action = {
                            "text": text_to_write,
                            "force_typing": True,
                            "batch_size": int(
                                llm_config.get("typing_batch_size", 5) or 5
                            ),
                            "batch_delay": float(
                                llm_config.get("typing_batch_delay", 0.01) or 0.01
                            ),
                            "char_by_char": bool(llm_config.get("char_by_char", False)),
                            "keystroke_delay": float(
                                llm_config.get("keystroke_delay", 0.001) or 0.001
                            ),
                        }
                    action_handlers.handle_type_string_action(0, action, stop_flag)
                    try:
                        node_id = (
                            llm_data.get("node_id")
                            or llm_data.get("id")
                            or node.get("id")
                        )
                        if node_id:
                            self.variables.pop(f"node_{node_id}_writing", None)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to write text via action handlers: {e}")

        # Determine next node
        next_node = llm_data.get("next_node") or node.get("next")
        if not next_node:
            try:
                connections = node.get("connections", {})
                if isinstance(connections, dict):
                    output_conns = connections.get("output", [])
                    if output_conns:
                        next_node = output_conns[0].get("node_id")
                elif isinstance(connections, list) and connections:
                    # Handle list-style connections from chain JSON
                    next_node = connections[0].get("target_node_id") or connections[
                        0
                    ].get("node_id")
            except Exception:
                pass
        try:
            if tool_ids and isinstance(response_text, str):
                sel = selected_tool_id
                if sel and (sel in tool_ids):
                    next_node = sel
                    logger.info(f"Tool selected by LLM: {sel}")
                    try:
                        if self._rich_console:
                            from rich.panel import Panel
                            from rich.table import Table

                            desc = tool_desc_map.get(sel, "")
                            t = Table(show_header=True, header_style="bold magenta")
                            t.add_column("Selected ID", style="bold")
                            t.add_column("Description")
                            t.add_row(str(sel), str(desc or ""))
                            self._rich_console.print(
                                Panel(t, title="Tool Selected", border_style="magenta")
                            )
                    except Exception:
                        pass
                else:
                    try:
                        if self._rich_console:
                            self._rich_console.print(
                                "[bold red]No tool selection marker found in response"
                            )
                    except Exception:
                        pass
        except Exception:
            pass

        # Ensure a non-None return to indicate successful execution to the workflow engine
        if not next_node:
            next_node = "__done__"

        # Adversarial loop for form filling: verify each step recursively
        if (
            is_form_filling_preset
            and "form_action_taken" in locals()
            and form_action_taken
        ):
            _node_id = llm_data.get("node_id") or llm_data.get("id") or node.get("id")

            # Keep track of loop count to prevent infinite loops
            loop_key = f"form_fill_loop_{_node_id}"
            loop_count = self.variables.get(loop_key, 0)

            if loop_count < 10:
                self.variables[loop_key] = loop_count + 1
                logger.info(
                    f"Form action taken (Step {loop_count + 1}/10). Looping back to same node for recursive verification."
                )

                # Make sure ComoRAG re-evaluates the screen state by triggering a new OCR/vision pass
                # instead of using stale context
                self.variables[f"node_{_node_id}_force_refresh"] = True

                if _node_id:
                    next_node = _node_id
            else:
                logger.warning(
                    f"Form filling reached maximum adversarial loop limit (10 steps) for node {_node_id}. Forcing progression."
                )
                self.variables[loop_key] = 0  # reset for future executions

        logger.info(f"LLM node execution complete. Next node: {next_node}")
        return next_node

    # --- Variable helpers for compatibility ---
    def get_variable(self, name: str, default: Any = None) -> Any:
        try:
            return self.variables.get(name, default)
        except Exception:
            return default

    def set_variable(self, name: str, value: Any) -> None:
        try:
            self.variables[name] = value
        except Exception:
            pass

    def get_all_variables(self) -> Dict[str, Any]:
        try:
            return dict(self.variables)
        except Exception:
            return {}

    # --- TTS execution (standalone, no GUI) ---
    def execute_tts_standalone(
        self,
        tts_data: Dict[str, Any],
        context_variables: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute text-to-speech using pyttsx3, mirroring TTSNode behavior.

        Args:
            tts_data: TTS configuration (text, language, rate, volume, voice_id, save_to_file, output_file, wait_for_completion)
            context_variables: Optional variables for {var} substitution in text

        Returns:
            dict: Result with 'success' and 'message' or 'error'
        """
        try:
            import pyttsx3

            # Extract configuration with defaults aligned to TTSNode
            text = tts_data.get("text") or "Hello, this is a text to speech message."
            language = tts_data.get("language") or "en"
            rate = tts_data.get("rate", 200)
            volume = tts_data.get("volume", 0.9)
            voice_id = tts_data.get("voice_id", 0)
            save_to_file = bool(tts_data.get("save_to_file", False))
            output_file = tts_data.get("output_file") or ""
            wait_for_completion = bool(tts_data.get("wait_for_completion", True))

            # Perform variable substitution using provided context and internal variables
            text_to_speak = str(text)
            # First apply explicit context variables
            if context_variables:
                for var_name, var_value in context_variables.items():
                    try:
                        text_to_speak = text_to_speak.replace(
                            f"{{{var_name}}}", str(var_value)
                        )
                    except Exception:
                        continue
            # Then apply executor's variables for broader compatibility
            if self.variables:
                for var_name, var_value in self.variables.items():
                    try:
                        text_to_speak = text_to_speak.replace(
                            f"{{{var_name}}}", str(var_value)
                        )
                    except Exception:
                        continue

            # Initialize TTS engine and configure
            engine = pyttsx3.init()
            try:
                engine.setProperty("rate", rate)
            except Exception:
                pass
            try:
                engine.setProperty("volume", volume)
            except Exception:
                pass

            # Select voice if available
            try:
                voices = engine.getProperty("voices")
                if voices and 0 <= int(voice_id) < len(voices):
                    engine.setProperty("voice", voices[int(voice_id)].id)
            except Exception:
                pass

            if save_to_file and output_file:
                # Save synthesized speech to a file
                engine.save_to_file(text_to_speak, output_file)
                if wait_for_completion:
                    engine.runAndWait()
                return {
                    "success": True,
                    "text": text_to_speak,
                    "saved_to_file": output_file,
                    "message": f"TTS saved to file: {output_file}",
                }
            else:
                # Speak out loud
                engine.say(text_to_speak)
                if wait_for_completion:
                    engine.runAndWait()
                preview = text_to_speak[:50]
                msg = (
                    f'TTS spoke: "{preview}..."'
                    if len(text_to_speak) > 50
                    else f'TTS spoke: "{text_to_speak}"'
                )
                return {"success": True, "text": text_to_speak, "message": msg}

        except ImportError:
            return {
                "error": "pyttsx3 library not installed. Please install it using: pip install pyttsx3"
            }
        except Exception as e:
            return {"error": f"TTS execution failed: {str(e)}"}
