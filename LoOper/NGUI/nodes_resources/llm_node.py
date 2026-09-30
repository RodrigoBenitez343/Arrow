import os

from NodeGraphQt import BaseNode

from ..constants import (
    DARK_GREY,
    INPUT_PORT_COLOR,
    LLM_COLOR,
    OUTPUT_PORT_COLOR,
    UNIVERSAL_PORT_TYPE,
)
from .base_node import _add_multi_input, get_default_api_url


class LLMNode(BaseNode):
    """Node representing an LLM (Large Language Model) that can generate text for automation workflows"""

    __identifier__ = "llm"
    NODE_NAME = "LLM"

    def __init__(self):
        super(LLMNode, self).__init__()
        self.llm_config = {
            # Empty = engine-driven: llama.cpp nodes run their GGUF, Ollama
            # nodes need an explicit model — never default to an Ollama name.
            "model": "",
            "prompt": "",
            "system_message": "",
            "temperature": 0.7,
            "max_tokens": 4096,
            "output_variable": "llm_output",
            "api_url": get_default_api_url(),
            "write_text": True,
            "use_vision": False,
            "vision_model": "",  # Empty — images go directly to the main model
            "screenshot_enabled": True,
            "typing_batch_size": 20,  # Characters per batch for optimized typing
            "typing_batch_delay": 0.05,  # Delay between batches in seconds
            "input_source": "none",
            # Orchestrator switch (OFF = vanilla LLM node).  ON morphs the
            # node into the goal loop over the chains wired to its 'tools'
            # port; the worker picker is Laya (fixed).
            "orchestrator_mode": False,
            "orch_max_steps": 15,
            # Fixed goal for the orchestrator loop; a goal wired to the node's
            # 'prompt' port overrides it (prompt port wins).
            "orch_goal": "",
            "orch_synthesize": True,
            "orch_synthesis_system": "",
            "orch_use_goal_ledger": False,
            # Web mode: route input/vision/write on the chain's shared browser
            # instead of the desktop screen (mirrors ConditionalNode.web_mode).
            "web_mode": False,
            "ocr_region": "",  # desktop OCR clip: JSON [x,y,w,h]; '' = full screen
            "web_ocr_locator": "",  # web OCR element locator JSON; '' = whole viewport
            "ocr_confidence": 0.5,
            "ocr_preprocessing": True,
            "use_async": True,  # Enable asynchronous processing by default
            # Llama.cpp specific config
            # gpu_layers 0 = CPU-first, threads -1 = auto, context 0 = model native
            "use_llamacpp": False,
            "llamacpp_model_path": "",
            "llamacpp_gpu_layers": 0,
            "llamacpp_threads": -1,
            "llamacpp_context_size": 0,
        }

        # Use a darkened version of the special color for better text readability
        # Keep the color identity but make it dark enough for white text
        base_color = LLM_COLOR
        r, g, b = (
            int(base_color.strip("#")[0:2], 16),
            int(base_color.strip("#")[2:4], 16),
            int(base_color.strip("#")[4:6], 16),
        )
        # Darken to 25% brightness for good contrast with white text
        dark_r, dark_g, dark_b = int(r * 0.25), int(g * 0.25), int(b * 0.25)
        self.set_color(dark_r, dark_g, dark_b)
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
        in_rgb = tuple(
            int(INPUT_PORT_COLOR.strip("#")[i : i + 2], 16) for i in (0, 2, 4)
        )
        out_rgb = tuple(
            int(OUTPUT_PORT_COLOR.strip("#")[i : i + 2], 16) for i in (0, 2, 4)
        )

        in_port = _add_multi_input(
            self, "input", in_rgb, True, data_type=UNIVERSAL_PORT_TYPE
        )
        # 'prompt' port: text wired here REPLACES this node's prompt template
        # (the system message is untouched).  The 'context' port is data/
        # history only.  The 'tools' port is NOT built here — it belongs to
        # orchestrator mode (see rebuild_orchestrator_ports).
        prompt_in_port = _add_multi_input(
            self, "prompt", in_rgb, True, data_type=UNIVERSAL_PORT_TYPE
        )
        context_in_port = _add_multi_input(
            self, "context", in_rgb, True, data_type=UNIVERSAL_PORT_TYPE
        )
        out_port = self.add_output(
            "output", color=out_rgb, display_name=True, multi_output=True
        )
        # out_port.model.type_ = UNIVERSAL_PORT_TYPE
        context_out_port = self.add_output(
            "context", color=out_rgb, display_name=True, multi_output=True
        )
        # context_out_port.model.type_ = UNIVERSAL_PORT_TYPE
        try:
            in_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(in_port, "_multi_connection", True)
            except Exception:
                pass
        try:
            prompt_in_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(prompt_in_port, "_multi_connection", True)
            except Exception:
                pass
        try:
            context_in_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(context_in_port, "_multi_connection", True)
            except Exception:
                pass
        try:
            out_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(out_port, "_multi_connection", True)
            except Exception:
                pass
        try:
            context_out_port.set_multi_connection(True)
        except Exception:
            try:
                setattr(context_out_port, "_multi_connection", True)
            except Exception:
                pass

        # Add text inputs for LLM configuration
        # Register properties without rendering inline input widgets
        # Empty = engine-driven: llama.cpp nodes use their GGUF; Ollama nodes
        # must pick a model explicitly (no Ollama default baked in).
        self.create_property("model", "")
        self.create_property("prompt", "")
        self.create_property("system_message", "")
        self.create_property("temperature", "0.7")
        self.create_property("max_tokens", "4096")
        self.create_property("output_variable", "llm_output")
        self.create_property("api_url", get_default_api_url())
        self.create_property("write_text", "true")
        self.create_property("use_vision", "false")
        self.create_property("vision_model", "")
        self.create_property("screenshot_enabled", "true")
        self.create_property("input_source", "none")
        self.create_property("orchestrator_mode", False)  # OFF = vanilla LLM node
        self.create_property("orch_max_steps", "15")
        self.create_property("orch_goal", "")
        self.create_property("orch_synthesize", True)
        self.create_property("orch_synthesis_system", "")
        self.create_property("orch_use_goal_ledger", False)
        # Web mode (see llm_config comment above).
        self.create_property("web_mode", "false")
        self.create_property("ocr_region", "")
        self.create_property("web_ocr_locator", "")
        self.create_property("ocr_confidence", "0.5")
        self.create_property("ocr_preprocessing", "true")
        self.create_property("typing_batch_size", "20")
        self.create_property("typing_batch_delay", "0.05")
        self.create_property("use_async", "true")
        self.create_property("use_direct_rag", "true")
        # Empty = auto: llama.cpp nodes embed with the local embedding server
        # (embeddinggemma GGUF), Ollama nodes with nomic-embed-text.
        self.create_property("rag_embedding_model", "")
        self.create_property("rag_chunk_size", "500")
        self.create_property("rag_overlap", "100")
        self.create_property("rag_top_k", "3")
        self.create_property("rag_include_raw_input", "false")
        self.create_property("rag_max_chars", "1500")
        self.create_property("rag_documents", "[]")
        self.create_property("tool_descriptions", "{}")
        self.create_property("skills", "[]")
        self.create_property("use_skill_routing", "true")
        self.create_property("semantic_description", "")
        # Context consolidation (ComoRAG-inspired)
        self.create_property("use_context_consolidation", "false")
        self.create_property("consolidation_chunk_size", "1000")
        self.create_property("consolidation_overlap", "200")
        self.create_property("consolidation_top_k", "5")
        self.create_property("consolidation_max_tokens", "64")
        # Llama.cpp properties
        self.create_property("use_llamacpp", "false")
        self.create_property("llamacpp_model_path", "")
        self.create_property("llamacpp_gpu_layers", "0")
        self.create_property("llamacpp_threads", "-1")
        self.create_property("llamacpp_context_size", "0")
        # The orchestrator switch adds/removes ports at runtime, which needs
        # NodeGraphQt's port deletion enabled on this node.
        try:
            self.set_port_deletion_allowed(True)
        except Exception:
            pass
        # Ports follow the switch (OFF by default = vanilla LLM node).
        self.rebuild_orchestrator_ports()

    def set_llm_config(self, config):
        """Set the LLM configuration for this node"""
        self.llm_config.update(config)

        # Update the node name to reflect the model (GGUF name for llama.cpp
        # nodes when no Ollama model is set).
        model_name = config.get("model") or ""
        if not model_name and str(config.get("use_llamacpp", False)).lower() in (
            "true", "1", "yes", "on",
        ):
            model_name = (
                os.path.basename(config.get("llamacpp_model_path") or "") or "llama.cpp"
            )
        self.set_name(f"LLM: {model_name}")

        # Update the text inputs to reflect the new configuration
        self.set_property("model", config.get("model", ""))
        self.set_property("prompt", config.get("prompt", ""))
        self.set_property("system_message", config.get("system_message", ""))
        self.set_property("temperature", str(config.get("temperature", 0.7)))
        self.set_property("max_tokens", str(config.get("max_tokens", 4096)))
        self.set_property(
            "output_variable", config.get("output_variable", "llm_output")
        )
        self.set_property("api_url", config.get("api_url", get_default_api_url()))
        self.set_property("write_text", str(config.get("write_text", True)).lower())
        self.set_property("use_vision", str(config.get("use_vision", False)).lower())
        self.set_property("vision_model", config.get("vision_model", ""))
        self.set_property(
            "screenshot_enabled", str(config.get("screenshot_enabled", True)).lower()
        )
        self.set_property("typing_batch_size", str(config.get("typing_batch_size", 20)))
        self.set_property(
            "typing_batch_delay", str(config.get("typing_batch_delay", 0.05))
        )
        self.set_property("input_source", config.get("input_source", "none"))
        self.set_property("orchestrator_mode", bool(config.get("orchestrator_mode", False)))
        self.set_property("orch_max_steps", str(config.get("orch_max_steps", 15) or 15))
        self.set_property("orch_goal", config.get("orch_goal") or "")
        self.set_property("orch_synthesize", bool(config.get("orch_synthesize", True)))
        self.set_property("orch_synthesis_system", config.get("orch_synthesis_system") or "")
        self.set_property("orch_use_goal_ledger", bool(config.get("orch_use_goal_ledger", False)))
        self.rebuild_orchestrator_ports()
        self.set_property("web_mode", str(config.get("web_mode", False)).lower())
        self.set_property("ocr_region", config.get("ocr_region", "") or "")
        self.set_property(
            "web_ocr_locator", config.get("web_ocr_locator", "") or ""
        )
        self.set_property("ocr_confidence", str(config.get("ocr_confidence", 0.5)))
        self.set_property(
            "ocr_preprocessing", str(config.get("ocr_preprocessing", True)).lower()
        )
        self.set_property("use_async", str(config.get("use_async", True)).lower())
        self.set_property(
            "typing_batch_delay", str(config.get("typing_batch_delay", 0.05))
        )
        self.set_property(
            "use_direct_rag", str(config.get("use_direct_rag", False)).lower()
        )
        self.set_property(
            "rag_embedding_model", config.get("rag_embedding_model", "")
        )
        self.set_property("rag_chunk_size", str(config.get("rag_chunk_size", 500)))
        self.set_property("rag_overlap", str(config.get("rag_overlap", 100)))
        self.set_property("rag_top_k", str(config.get("rag_top_k", 3)))
        self.set_property(
            "rag_include_raw_input",
            str(config.get("rag_include_raw_input", False)).lower(),
        )
        self.set_property("rag_max_chars", str(config.get("rag_max_chars", 1500)))
        try:
            import json as _json

            self.set_property(
                "rag_documents", _json.dumps(config.get("rag_documents", []))
            )
        except Exception:
            self.set_property("rag_documents", "[]")
        if "skills" in config:
            val = config["skills"]
            self.set_property(
                "skills", _json.dumps(val) if isinstance(val, list) else val
            )
        if "use_skill_routing" in config:
            self.set_property(
                "use_skill_routing", str(config["use_skill_routing"]).lower()
            )
        if "semantic_description" in config:
            self.set_property("semantic_description", config["semantic_description"])
        # Context consolidation properties
        if "use_context_consolidation" in config:
            self.set_property(
                "use_context_consolidation",
                str(config["use_context_consolidation"]).lower(),
            )
        if "consolidation_chunk_size" in config:
            self.set_property(
                "consolidation_chunk_size", str(config["consolidation_chunk_size"])
            )
        if "consolidation_overlap" in config:
            self.set_property(
                "consolidation_overlap", str(config["consolidation_overlap"])
            )
        if "consolidation_top_k" in config:
            self.set_property(
                "consolidation_top_k", str(config["consolidation_top_k"])
            )
        if "consolidation_max_tokens" in config:
            self.set_property(
                "consolidation_max_tokens", str(config["consolidation_max_tokens"])
            )
        # Llama.cpp config
        if "use_llamacpp" in config:
            self.set_property("use_llamacpp", str(config["use_llamacpp"]).lower())
        if "llamacpp_model_path" in config:
            self.set_property("llamacpp_model_path", config["llamacpp_model_path"])
        if "llamacpp_gpu_layers" in config:
            self.set_property("llamacpp_gpu_layers", str(config["llamacpp_gpu_layers"]))
        if "llamacpp_threads" in config:
            self.set_property("llamacpp_threads", str(config["llamacpp_threads"]))
        if "llamacpp_context_size" in config:
            self.set_property(
                "llamacpp_context_size", str(config["llamacpp_context_size"])
            )

    # ---------------------------------------------------- orchestrator switch

    def set_orchestrator_mode(self, enabled):
        """Switch between the vanilla LLM node and the orchestrator brain."""
        self.set_property("orchestrator_mode", bool(enabled))
        self.rebuild_orchestrator_ports()
        self.refresh_visual_state()

    def rebuild_orchestrator_ports(self):
        """ON = orchestrator: expose the fused 'tools' INPUT port (chains +
        brains) plus the 'trace' + 'route' outputs.  OFF = vanilla LLM node:
        no 'tools' port — a plain LLM node has no tools, the orchestrator
        owns tool routing."""
        try:
            on = bool(self.get_property("orchestrator_mode"))
        except Exception:
            on = False

        # 'tools' INPUT port follows the switch (brains + chains fused into
        # one port).  Added/removed here so a vanilla LLM node never shows it.
        _tools_port = getattr(self, "_orch_tools_port", None)
        if on and not _tools_port:
            try:
                in_rgb = tuple(
                    int(INPUT_PORT_COLOR.strip("#")[i:i + 2], 16)
                    for i in (0, 2, 4)
                )
                self._orch_tools_port = _add_multi_input(
                    self, "tools", in_rgb, True,
                    data_type=UNIVERSAL_PORT_TYPE,
                )
            except Exception:
                self._orch_tools_port = None
        elif not on and _tools_port:
            try:
                self.set_port_deletion_allowed(True)
            except Exception:
                pass
            # Drop any pipes first: delete_input removes the port item from
            # the scene but leaves its connections dangling.
            try:
                _tools_port.clear_connections(push_undo=False)
            except Exception:
                pass
            try:
                self.delete_input(_tools_port)
            except Exception:
                pass
            self._orch_tools_port = None

        added = getattr(self, "_orch_ports", None) or []
        if on and not added:
            try:
                out_rgb = tuple(
                    int(OUTPUT_PORT_COLOR.strip("#")[i:i + 2], 16)
                    for i in (0, 2, 4)
                )
                for name in ("trace", "route"):
                    try:
                        p = self.add_output(
                            name, color=out_rgb, display_name=True,
                            multi_output=True,
                        )
                        try:
                            if hasattr(p, "set_multi_connection"):
                                p.set_multi_connection(True)
                            else:
                                setattr(p, "_multi_connection", True)
                        except Exception:
                            pass
                        added.append(p)
                    except Exception:
                        pass
                self._orch_ports = added
            except Exception:
                pass
        elif not on and added:
            # delete_output takes a port NAME/INDEX but raises unless port
            # deletion is enabled on the node — and it silently did nothing
            # inside the old try, which made the switch one-way.
            try:
                self.set_port_deletion_allowed(True)
            except Exception:
                pass
            for p in list(added):
                try:
                    p.clear_connections(push_undo=False)
                except Exception:
                    pass
                try:
                    self.delete_output(p)
                except Exception:
                    pass
            self._orch_ports = []

    def refresh_visual_state(self):
        """Teal + 'Orchestrator' while ON; the LLM colour/name again when OFF."""
        try:
            on = bool(self.get_property("orchestrator_mode"))
        except Exception:
            on = False
        try:
            if on:
                # Remember the pre-switch name so OFF is not a one-way rename.
                if getattr(self, "_orch_prev_name", None) is None:
                    try:
                        self._orch_prev_name = self.name()
                    except Exception:
                        self._orch_prev_name = ""
                self.set_color(10, 90, 75)
                self.set_name("Orchestrator")
            else:
                prev = getattr(self, "_orch_prev_name", None)
                self._orch_prev_name = None
                if prev:
                    try:
                        self.set_name(prev)
                    except Exception:
                        pass
                base = LLM_COLOR
                r, g, b = (int(base.strip("#")[i:i + 2], 16) for i in (0, 2, 4))
                self.set_color(int(r * 0.25), int(g * 0.25), int(b * 0.25))
        except Exception:
            pass

    def get_llm_config(self):
        """Get the current LLM configuration from the node"""
        write_text_str = self.get_property("write_text") or "true"
        write_text = write_text_str.lower() in ("true", "1", "yes", "on")
        use_vision_str = self.get_property("use_vision") or "false"
        use_vision = use_vision_str.lower() in ("true", "1", "yes", "on")
        screenshot_enabled_str = self.get_property("screenshot_enabled") or "true"
        screenshot_enabled = screenshot_enabled_str.lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        ocr_preprocessing_str = self.get_property("ocr_preprocessing") or "true"
        ocr_preprocessing = ocr_preprocessing_str.lower() in ("true", "1", "yes", "on")
        use_async_str = self.get_property("use_async") or "true"
        use_async = use_async_str.lower() in ("true", "1", "yes", "on")
        use_direct_rag_str = self.get_property("use_direct_rag") or "false"
        use_direct_rag = use_direct_rag_str.lower() in ("true", "1", "yes", "on")
        rag_include_raw_input_str = (
            self.get_property("rag_include_raw_input") or "false"
        )
        rag_include_raw_input = rag_include_raw_input_str.lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
        docs_raw = self.get_property("rag_documents") or "[]"
        try:
            import json as _json

            rag_documents = (
                _json.loads(docs_raw) if isinstance(docs_raw, str) else (docs_raw or [])
            )
            if isinstance(rag_documents, str):
                rag_documents = [rag_documents]
        except Exception:
            rag_documents = []
        tool_desc_raw = self.get_property("tool_descriptions") or "{}"
        try:
            import json as _json

            tool_descriptions = (
                _json.loads(tool_desc_raw)
                if isinstance(tool_desc_raw, str)
                else (tool_desc_raw or {})
            )
        except Exception:
            tool_descriptions = {}
        return {
            "model": self.get_property("model"),
            "prompt": self.get_property("prompt"),
            "system_message": self.get_property("system_message"),
            "temperature": float(self.get_property("temperature") or 0.7),
            "max_tokens": int(self.get_property("max_tokens") or 4096),
            "output_variable": self.get_property("output_variable"),
            "api_url": self.get_property("api_url"),
            "write_text": write_text,
            "use_vision": use_vision,
            "vision_model": self.get_property("vision_model"),
            "screenshot_enabled": screenshot_enabled,
            "typing_batch_size": int(self.get_property("typing_batch_size") or 20),
            "typing_batch_delay": float(
                self.get_property("typing_batch_delay") or 0.05
            ),
            "input_source": self.get_property("input_source") or "none",
            "orchestrator_mode": bool(self.get_property("orchestrator_mode")),
            "orch_max_steps": int(self.get_property("orch_max_steps") or 15),
            "orch_goal": self.get_property("orch_goal") or "",
            "orch_synthesize": bool(self.get_property("orch_synthesize")),
            "orch_synthesis_system": self.get_property("orch_synthesis_system") or "",
            "orch_use_goal_ledger": bool(self.get_property("orch_use_goal_ledger")),
            "web_mode": str(self.get_property("web_mode") or "false").lower()
            in ("true", "1", "yes", "on"),
            "ocr_region": self.get_property("ocr_region") or "",
            "web_ocr_locator": self.get_property("web_ocr_locator") or "",
            "ocr_confidence": float(self.get_property("ocr_confidence") or 0.5),
            "ocr_preprocessing": ocr_preprocessing,
            "use_async": use_async,
            "use_direct_rag": use_direct_rag,
            "rag_embedding_model": self.get_property("rag_embedding_model") or "",
            "rag_chunk_size": int(self.get_property("rag_chunk_size") or 500),
            "rag_overlap": int(self.get_property("rag_overlap") or 100),
            "rag_top_k": int(self.get_property("rag_top_k") or 3),
            "rag_include_raw_input": rag_include_raw_input,
            "rag_max_chars": int(self.get_property("rag_max_chars") or 1500),
            "rag_documents": rag_documents,
            "tool_descriptions": tool_descriptions,
            "skills": _json.loads(self.get_property("skills") or "[]"),
            "use_skill_routing": str(
                self.get_property("use_skill_routing") or "true"
            ).lower()
            in ("true", "1"),
            "semantic_description": self.get_property("semantic_description") or "",
            # Context consolidation (opt-in — disabled by default)
            "use_context_consolidation": str(
                self.get_property("use_context_consolidation") or "false"
            ).lower()
            in ("true", "1"),
            "consolidation_chunk_size": int(
                self.get_property("consolidation_chunk_size") or 1000
            ),
            "consolidation_overlap": int(
                self.get_property("consolidation_overlap") or 200
            ),
            "consolidation_top_k": int(
                self.get_property("consolidation_top_k") or 5
            ),
            "consolidation_max_tokens": int(
                self.get_property("consolidation_max_tokens") or 64
            ),
            # Llama.cpp config
            "use_llamacpp": str(self.get_property("use_llamacpp") or "false").lower()
            in ("true", "1"),
            "llamacpp_model_path": self.get_property("llamacpp_model_path") or "",
            "llamacpp_gpu_layers": int(self.get_property("llamacpp_gpu_layers") or 0),
            "llamacpp_threads": int(self.get_property("llamacpp_threads") or -1),
            "llamacpp_context_size": int(
                self.get_property("llamacpp_context_size") or 0
            ),
        }

    def is_vision_model(self, model_name):
        """Check if the specified model supports vision capabilities"""
        import os
        import sys

        # Add the AI directory to the path to import the OllamaClient
        ai_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "AI",
        )
        if ai_dir not in sys.path:
            sys.path.insert(0, ai_dir)

        try:
            from ...AI.consult import OllamaClient

            client = OllamaClient()
            return client.is_vision_model(model_name)
        except Exception:
            # Fallback: check common vision model names
            vision_models = [
                "llava",
                "bakllava",
                "llava-llama3",
                "llava-phi3",
                "llava-vicuna",
                "moondream",
                "cogvlm",
                "minicpm-v",
                "qwen-vl",
                "internvl",
            ]
            model_lower = model_name.lower()
            return any(vision_model in model_lower for vision_model in vision_models)

    def take_screenshot(self):
        """Take a screenshot for vision analysis"""
        import os
        import time

        import pyautogui

        # Disable PyAutoGUI failsafe to prevent interruption when mouse moves to corner
        pyautogui.FAILSAFE = False

        try:
            # Create screenshots directory if it doesn't exist
            screenshots_dir = os.path.join(
                os.path.dirname(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                ),
                "screenshots",
            )
            os.makedirs(screenshots_dir, exist_ok=True)

            # Take screenshot
            screenshot = pyautogui.screenshot()

            # Save with timestamp
            timestamp = int(time.time())
            screenshot_path = os.path.join(
                screenshots_dir, f"llm_vision_{timestamp}.png"
            )
            screenshot.save(screenshot_path)

            # Register for cleanup after use
            try:
                import sys

                player_dir = os.path.join(
                    os.path.dirname(
                        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    ),
                    "player",
                )
                if player_dir not in sys.path:
                    sys.path.append(player_dir)
                from ...player.screenshot_cleanup import register_temporal_screenshot

                register_temporal_screenshot(screenshot_path)
            except ImportError:
                # Fallback if cleanup utility is not available
                pass

            return screenshot_path
        except Exception as e:
            print(f"Error taking screenshot: {e}")
            return None

    def execute_llm(self, context_variables=None):
        """Execute the LLM request and return the generated text"""
        import os
        import sys

        config = self.get_llm_config()

        # Check if using llama.cpp
        if config.get("use_llamacpp", False):
            return self._execute_llamacpp(config, context_variables)

        # Otherwise use Ollama (existing code)
        # Add the AI directory to the path to import the OllamaClient
        ai_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "AI",
        )
        if ai_dir not in sys.path:
            sys.path.insert(0, ai_dir)

        try:
            from ...AI.consult import OllamaClient

            client = OllamaClient(config["api_url"])

            # Replace variables in prompt if context is provided
            prompt = config["prompt"]
            if context_variables:
                for var_name, var_value in context_variables.items():
                    prompt = prompt.replace(f"{{{var_name}}}", str(var_value))

            # Check if this is a vision model and handle screenshots
            images = None
            if config.get("use_vision", False) or self.is_vision_model(config["model"]):
                if config.get("screenshot_enabled", True):
                    screenshot_path = self.take_screenshot()
                    if screenshot_path:
                        images = [screenshot_path]
                        print(
                            f"Vision model detected, screenshot taken: {screenshot_path}"
                        )
                    else:
                        print("Warning: Vision model detected but screenshot failed")

            # Make the API call
            response = client.chat(
                model=config["model"],
                prompt=prompt,
                system=config["system_message"] if config["system_message"] else None,
                temperature=config["temperature"],
                max_tokens=config["max_tokens"],
                images=images,
            )

            # Clean up the screenshot now that the node is done using it
            def _cleanup_vision_images():
                if not images:
                    return
                try:
                    import sys

                    player_dir = os.path.join(
                        os.path.dirname(
                            os.path.dirname(
                                os.path.dirname(os.path.abspath(__file__))
                            )
                        ),
                        "player",
                    )
                    if player_dir not in sys.path:
                        sys.path.append(player_dir)
                    from ...player.screenshot_cleanup import cleanup_screenshot

                    for image_path in images:
                        cleanup_screenshot(image_path)
                except ImportError:
                    # Fallback cleanup if utility is not available
                    for image_path in images:
                        try:
                            if os.path.exists(image_path):
                                os.remove(image_path)
                        except Exception:
                            pass

            if "error" in response:
                _cleanup_vision_images()
                return {"error": response["error"]}
            else:
                generated_text = response.get("response", "")
                _cleanup_vision_images()
                return {
                    "success": True,
                    "text": generated_text,
                    "variable_name": config["output_variable"],
                    "used_vision": images is not None,
                    "screenshot_path": images[0] if images else None,
                }

        except Exception as e:
            return {"error": str(e)}

    def _execute_llamacpp(self, config, context_variables=None):
        """Execute LLM using llama.cpp engine"""
        import os

        import requests

        try:
            # Get API URL for llama.cpp endpoints
            api_url = config.get("api_url", get_default_api_url()).rstrip("/")

            # Replace variables in prompt if context is provided
            prompt = config["prompt"]
            if context_variables:
                for var_name, var_value in context_variables.items():
                    prompt = prompt.replace(f"{{{var_name}}}", str(var_value))

            # Build request payload
            # max_tokens=-1: no output cap — the model generates until it
            # finishes or the llama.cpp context window fills.  gpu_layers 0 =
            # CPU-first, threads -1 = auto, context 0 = model native.
            payload = {
                "model": config.get("llamacpp_model_path", ""),
                "prompt": prompt,
                "system": config.get("system_message", ""),
                "temperature": config.get("temperature", 0.7),
                "max_tokens": -1,
                "stream": False,
                "use_llamacpp": True,
                "gpu_layers": config.get("llamacpp_gpu_layers", 0),
                "threads": config.get("llamacpp_threads", -1),
                "context_size": config.get("llamacpp_context_size", 0),
            }

            # Make API call to llama.cpp endpoint
            # No timeout — thinking models can take minutes to answer and the
            # engine continues truncated generations internally until they
            # finish naturally.
            response = requests.post(
                f"{api_url}/llamacpp/generate", json=payload, timeout=None
            )

            if response.status_code == 200:
                result = response.json()
                generated_text = result.get("response", "")
                return {
                    "success": True,
                    "text": generated_text,
                    "variable_name": config["output_variable"],
                    "used_vision": False,
                    "engine": "llamacpp",
                }
            else:
                return {
                    "error": f"llama.cpp API error: {response.status_code} - {response.text}"
                }

        except Exception as e:
            return {"error": f"llama.cpp execution failed: {str(e)}"}
