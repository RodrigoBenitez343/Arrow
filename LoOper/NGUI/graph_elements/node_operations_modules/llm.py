import json

from PyQt5.QtWidgets import QMessageBox

from ...dialogs import LLMPropertiesDialog
from .utils import get_logger

try:
    # Prefer real cached models; if unavailable, fall back to empty list
    from ....AI.model_cache import get_real_cached_models
except Exception:

    def get_real_cached_models(timeout: float = 5.0):
        return []


logger = get_logger(__name__)


class LLMOperationsMixin:
    def add_llm_node(self, pos=None):
        """Add a new LLM node."""
        logger.info(f"Adding LLM node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating LLM node")
            node = self.parent_widget.graph_manager.create_node(
                "llm.LLMNode", name="LLM", pos=pos
            )

            if node:
                logger.debug(f"Setting default properties for LLM node {node.id}")
                # Set default properties
                node.set_property("prompt", "")
                # Leave the model empty — engine-driven: llama.cpp nodes use
                # their GGUF; Ollama nodes pick a model explicitly.  Never
                # default to an Ollama model name.
                node.set_property("model", "")
                node.set_property("vision_model", "minicpm-v:latest")
                node.set_property("temperature", "0.7")
                node.set_property("orchestrator_mode", False)

                logger.info(f"Successfully added LLM node {node.id}")
                return node
            else:
                logger.error("Failed to create LLM node")

        except Exception as e:
            logger.error(f"Error adding LLM node: {e}")
            QMessageBox.critical(
                self.parent_widget, "Error", f"Failed to add LLM node: {str(e)}"
            )

        return None

    def add_llm_node_with_shared_file(self, chain_file_path, llm_node_data):
        """Add an LLM node pre-configured from a shared LLM node in another chain."""
        logger.info(f"Adding LLM node with shared file: {chain_file_path}")
        try:
            node = self.parent_widget.graph_manager.create_node(
                "llm.LLMNode", name="LLM", pos=None
            )

            if node:
                # Copy configuration from the source LLM node
                node.set_property("prompt", llm_node_data.get("prompt", ""))
                node.set_property("model", llm_node_data.get("model", ""))
                node.set_property("system_message", llm_node_data.get("system_message", ""))
                node.set_property("temperature", str(llm_node_data.get("temperature", 0.7)))
                node.set_property("output_variable", llm_node_data.get("output_variable", "llm_output"))
                node.set_property("api_url", llm_node_data.get("api_url", ""))
                node.set_property("write_text", str(llm_node_data.get("write_text", True)).lower())
                node.set_property("use_vision", str(llm_node_data.get("use_vision", False)).lower())
                node.set_property("vision_model", llm_node_data.get("vision_model", "minicpm-v:latest"))
                node.set_property("screenshot_enabled", str(llm_node_data.get("screenshot_enabled", True)).lower())
                node.set_property("typing_batch_size", str(llm_node_data.get("typing_batch_size", 20)))
                node.set_property("typing_batch_delay", str(llm_node_data.get("typing_batch_delay", 0.05)))
                node.set_property("input_source", llm_node_data.get("input_source", "none"))
                node.set_property("orchestrator_mode", bool(llm_node_data.get("orchestrator_mode", False)))
                node.set_property("orch_max_steps", str(llm_node_data.get("orch_max_steps", 15) or 15))
                node.set_property("orch_goal", llm_node_data.get("orch_goal") or "")
                node.set_property("orch_synthesize", bool(llm_node_data.get("orch_synthesize", True)))
                node.set_property("orch_synthesis_system", llm_node_data.get("orch_synthesis_system") or "")
                node.set_property("orch_use_goal_ledger", bool(llm_node_data.get("orch_use_goal_ledger", False)))
                node.set_property("web_mode", str(llm_node_data.get("web_mode", False)).lower())
                node.set_property("ocr_region", llm_node_data.get("ocr_region", "") or "")
                node.set_property("web_ocr_locator", llm_node_data.get("web_ocr_locator", "") or "")
                node.set_property("ocr_confidence", str(llm_node_data.get("ocr_confidence", 0.5)))
                node.set_property("ocr_preprocessing", str(llm_node_data.get("ocr_preprocessing", True)).lower())
                node.set_property("use_async", str(llm_node_data.get("use_async", True)).lower())
                node.set_property("use_direct_rag", str(llm_node_data.get("use_direct_rag", False)).lower())
                node.set_property("rag_embedding_model", llm_node_data.get("rag_embedding_model", ""))
                node.set_property("rag_chunk_size", str(llm_node_data.get("rag_chunk_size", 500)))
                node.set_property("rag_overlap", str(llm_node_data.get("rag_overlap", 100)))
                node.set_property("rag_top_k", str(llm_node_data.get("rag_top_k", 3)))
                node.set_property("rag_include_raw_input", str(llm_node_data.get("rag_include_raw_input", False)).lower())
                node.set_property("rag_max_chars", str(llm_node_data.get("rag_max_chars", 1500)))
                node.set_property("rag_documents", llm_node_data.get("rag_documents", "[]"))
                node.set_property("tool_descriptions", llm_node_data.get("tool_descriptions", "{}"))
                node.set_property("skills", llm_node_data.get("skills", "[]"))
                node.set_property("use_skill_routing", str(llm_node_data.get("use_skill_routing", True)).lower())
                node.set_property("semantic_description", llm_node_data.get("semantic_description", ""))
                node.set_property("use_llamacpp", str(llm_node_data.get("use_llamacpp", False)).lower())
                node.set_property("llamacpp_model_path", llm_node_data.get("llamacpp_model_path", ""))
                node.set_property("llamacpp_gpu_layers", str(llm_node_data.get("llamacpp_gpu_layers", 0)))
                node.set_property("llamacpp_threads", str(llm_node_data.get("llamacpp_threads", -1)))
                node.set_property("llamacpp_context_size", str(llm_node_data.get("llamacpp_context_size", 0)))

                # Set name
                semantic_desc = llm_node_data.get("semantic_description", "")
                model = llm_node_data.get("model", "")
                if semantic_desc:
                    node.set_name(f"LLM: {semantic_desc} (Shared)")
                else:
                    node.set_name(f"LLM: {model} (Shared)")

                logger.info(f"Successfully added shared LLM node {node.id}")
                return node
            else:
                logger.error("Failed to create shared LLM node")

        except Exception as e:
            logger.error(f"Error adding shared LLM node: {e}")
            QMessageBox.critical(
                self.parent_widget, "Error", f"Failed to add shared LLM node: {str(e)}"
            )

        return None

    def edit_llm(self, node):
        """Edit an LLM node's properties."""
        logger.info(f"Editing LLM node: {node.id}")
        try:
            logger.debug("Creating LLM dialog")
            dialog = LLMPropertiesDialog(self.parent_widget)

            # Set current values - Basic tab
            logger.debug("Loading current values into dialog")
            dialog.model_combo.setCurrentText(
                node.get_property("model") or ""
            )
            dialog.api_url_entry.setText(node.get_property("api_url") or "")
            dialog.temperature_spin.setValue(
                float(node.get_property("temperature") or 0.7)
            )
            # Restore the llama.cpp context size (0 = model's native context).
            dialog.context_size_spin.setValue(
                int(node.get_property("llamacpp_context_size") or 0)
            )
            # Restore the GPU layers (0 = CPU-first).
            dialog.gpu_layers_spin.setValue(
                int(node.get_property("llamacpp_gpu_layers") or 0)
            )
            dialog.output_variable_entry.setText(
                node.get_property("output_variable") or "llm_output"
            )
            # Orchestrator switch + settings — MUST reflect the node, otherwise
            # the dialog reopens with defaults and the toggle looks unpersisted.
            dialog.orchestrator_mode_check.setChecked(
                bool(node.get_property("orchestrator_mode"))
            )
            try:
                dialog.orch_max_steps_spin.setValue(
                    int(node.get_property("orch_max_steps") or 15)
                )
            except Exception:
                pass
            _syn = node.get_property("orch_synthesize")
            dialog.orch_synthesize_check.setChecked(
                True if _syn is None else bool(_syn))
            dialog.orch_goal_edit.setText(
                node.get_property("orch_goal") or ""
            )
            dialog.orch_synthesis_system_edit.setText(
                node.get_property("orch_synthesis_system") or ""
            )
            dialog.orch_use_goal_ledger_check.setChecked(
                bool(node.get_property("orch_use_goal_ledger"))
            )
            try:
                dialog._apply_node_mode_visibility()
            except Exception:
                pass

            # Prompt tab
            dialog.system_message_edit.setPlainText(
                node.get_property("system_message") or ""
            )
            dialog.prompt_edit.setPlainText(node.get_property("prompt") or "")

            try:
                import json as _json

                td_raw = node.get_property("tool_descriptions") or "{}"
                td_map = (
                    _json.loads(td_raw) if isinstance(td_raw, str) else (td_raw or {})
                )
                # Auto-detect connected tools via 'tools' input port
                try:
                    tool_ids = []
                    id_to_label = {}
                    for p in node.input_ports():
                        if p.name() == "tools":
                            for cp in p.connected_ports():
                                src_node = (
                                    getattr(cp, "node")()
                                    if hasattr(cp, "node")
                                    else None
                                )
                                if src_node:
                                    tid = getattr(src_node, "id", None) or (
                                        src_node.id if hasattr(src_node, "id") else None
                                    )
                                    if tid:
                                        tool_ids.append(tid)
                                        label = ""
                                        try:
                                            label = src_node.name()
                                        except Exception:
                                            label = ""
                                        id_to_label[tid] = label
                    # Merge: ensure all detected tools appear in mapping
                    for tid in tool_ids:
                        if tid not in td_map:
                            td_map[tid] = id_to_label.get(tid, "")
                except Exception:
                    pass
                dialog.load_tool_descriptions(td_map)
            except Exception:
                try:
                    dialog.load_tool_descriptions({})
                except Exception:
                    pass

            # External input: only non-connection readers are offered now.
            # A legacy "previous"/"input"/"input_node" falls back to none.
            input_source = str(node.get_property("input_source") or "none").lower()
            _radio_by_source = {
                "ocr": dialog.input_ocr_radio,
                "page_text": dialog.input_page_text_radio,
                "clipboard": dialog.input_clipboard_radio,
            }
            _radio_by_source.get(input_source, dialog.input_none_radio).setChecked(True)

            # OCR configuration
            dialog.ocr_confidence_spin.setValue(
                float(node.get_property("ocr_confidence") or 0.7)
            )
            dialog.ocr_preprocessing_combo.setCurrentText(
                node.get_property("ocr_preprocessing") or "None"
            )

            # Web mode + OCR region (desktop [x,y,w,h] / web element locator)
            _web_mode_str = node.get_property("web_mode") or "false"
            dialog.web_mode_checkbox.setChecked(
                str(_web_mode_str).lower() in ("true", "1", "yes", "on")
            )
            _region_raw = node.get_property("ocr_region") or ""
            _region = None
            try:
                _region = json.loads(_region_raw) if _region_raw else None
            except Exception:
                _region = None
            if isinstance(_region, (list, tuple)) and len(_region) == 4:
                dialog._ocr_region = [int(v) for v in _region]
            dialog._web_ocr_locator = node.get_property("web_ocr_locator") or ""
            dialog._update_ocr_region_label()

            # Advanced tab - Output configuration
            write_text_str = node.get_property("write_text") or "true"
            write_text = write_text_str.lower() in ("true", "1", "yes", "on")
            dialog.write_text_checkbox.setChecked(write_text)

            dialog.typing_batch_size_spin.setValue(
                int(node.get_property("typing_batch_size") or 20)
            )
            dialog.typing_batch_delay_spin.setValue(
                float(node.get_property("typing_batch_delay") or 0.05)
            )

            # Async processing
            use_async_str = node.get_property("use_async") or "true"
            use_async = use_async_str.lower() in ("true", "1", "yes", "on")
            dialog.use_async_checkbox.setChecked(use_async)

            # Features tab - Vision configuration
            use_vision_str = node.get_property("use_vision") or "false"
            use_vision = use_vision_str.lower() in ("true", "1", "yes", "on")
            dialog.use_vision_checkbox.setChecked(use_vision)

            # vision_model is no longer a separate combo — images go directly to the main model

            screenshot_enabled_str = node.get_property("screenshot_enabled") or "true"
            screenshot_enabled = screenshot_enabled_str.lower() in (
                "true",
                "1",
                "yes",
                "on",
            )
            dialog.screenshot_enabled_checkbox.setChecked(screenshot_enabled)

            use_direct_rag_str = node.get_property("use_direct_rag") or "false"
            use_direct_rag = use_direct_rag_str.lower() in ("true", "1", "yes", "on")
            dialog.use_direct_rag_checkbox.setChecked(use_direct_rag)
            # Engine-aware default: llama.cpp nodes embed with the local
            # embeddinggemma GGUF; Ollama nodes with nomic-embed-text.
            _use_llamacpp_now = str(
                node.get_property("use_llamacpp") or "false"
            ).lower() in ("true", "1", "yes", "on")
            _rag_model = (node.get_property("rag_embedding_model") or "").strip()
            if _rag_model.lower() in ("", "auto", "default") or (
                _use_llamacpp_now
                and _rag_model.lower() in ("nomic-embed-text", "nomic-embed-text:v1.5")
            ):
                _rag_model = (
                    "embeddinggemma-300M-Q8_0.gguf"
                    if _use_llamacpp_now
                    else "nomic-embed-text"
                )
            dialog.rag_embedding_model_entry.setText(_rag_model)
            dialog.rag_chunk_size_spin.setValue(
                int(node.get_property("rag_chunk_size") or 500)
            )
            dialog.rag_overlap_spin.setValue(
                int(node.get_property("rag_overlap") or 100)
            )
            dialog.rag_top_k_spin.setValue(int(node.get_property("rag_top_k") or 3))
            rag_include_raw_input_str = (
                node.get_property("rag_include_raw_input") or "false"
            )
            rag_include_raw_input = rag_include_raw_input_str.lower() in (
                "true",
                "1",
                "yes",
                "on",
            )
            dialog.rag_include_raw_input_checkbox.setChecked(rag_include_raw_input)
            dialog.rag_max_chars_spin.setValue(
                int(node.get_property("rag_max_chars") or 1500)
            )
            try:
                import json as _json

                docs = node.get_property("rag_documents") or "[]"
                docs_list = _json.loads(docs) if isinstance(docs, str) else []
            except Exception:
                docs_list = []
            for p in docs_list:
                dialog.rag_documents_list.addItem(p)

            # Context consolidation config (load)
            _use_cons = str(
                node.get_property("use_context_consolidation") or "false"
            ).lower()
            dialog.use_consolidation_checkbox.setChecked(
                _use_cons in ("true", "1", "yes", "on")
            )
            dialog.consolidation_chunk_size_spin.setValue(
                int(node.get_property("consolidation_chunk_size") or 1000)
            )
            dialog.consolidation_overlap_spin.setValue(
                int(node.get_property("consolidation_overlap") or 200)
            )
            dialog.consolidation_top_k_spin.setValue(
                int(node.get_property("consolidation_top_k") or 5)
            )
            dialog.consolidation_max_tokens_spin.setValue(
                int(node.get_property("consolidation_max_tokens") or 64)
            )

            # Skills configuration
            try:
                import json as _json

                skills_raw = node.get_property("skills") or "[]"
                skills_list = (
                    _json.loads(skills_raw)
                    if isinstance(skills_raw, str)
                    else (skills_raw or [])
                )
                dialog._populate_skills_table(skills_list)
            except Exception:
                pass

            use_skill_routing_str = node.get_property("use_skill_routing") or "true"
            use_skill_routing = use_skill_routing_str.lower() in (
                "true",
                "1",
                "yes",
                "on",
            )
            dialog.skill_routing_check.setChecked(use_skill_routing)

            # ----- llama.cpp config (load) -----
            use_llamacpp_str = node.get_property("use_llamacpp") or "false"
            use_llamacpp = use_llamacpp_str.lower() in ("true", "1", "yes", "on")
            if use_llamacpp:
                dialog.engine_combo.setCurrentText("llama.cpp")
            llamacpp_model_path = node.get_property("llamacpp_model_path") or ""
            if llamacpp_model_path:
                dialog.llamacpp_model_combo.setCurrentText(llamacpp_model_path)
            # ------------------------------------

            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")

                # Basic tab properties
                node.set_property("model", dialog.model_combo.currentText())
                node.set_property("api_url", dialog.api_url_entry.text())
                node.set_property("temperature", str(dialog.temperature_spin.value()))
                node.set_property(
                    "output_variable", dialog.output_variable_entry.text()
                )

                # Prompt tab properties
                node.set_property(
                    "system_message", dialog.system_message_edit.toPlainText()
                )
                node.set_property("prompt", dialog.prompt_edit.toPlainText())

                # External input (connection-driven values are not offered).
                if dialog.input_ocr_radio.isChecked():
                    node.set_property("input_source", "ocr")
                elif dialog.input_page_text_radio.isChecked():
                    node.set_property("input_source", "page_text")
                elif dialog.input_clipboard_radio.isChecked():
                    node.set_property("input_source", "clipboard")
                else:
                    node.set_property("input_source", "none")

                # OCR configuration
                node.set_property(
                    "ocr_confidence", str(dialog.ocr_confidence_spin.value())
                )
                node.set_property(
                    "ocr_preprocessing", dialog.ocr_preprocessing_combo.currentText()
                )
                node.set_property(
                    "web_mode",
                    str(dialog.web_mode_checkbox.isChecked()).lower(),
                )
                _region = getattr(dialog, "_ocr_region", "")
                node.set_property(
                    "ocr_region", json.dumps(_region) if _region else ""
                )
                node.set_property(
                    "web_ocr_locator",
                    getattr(dialog, "_web_ocr_locator", "") or "",
                )

                # Advanced tab properties
                node.set_property(
                    "write_text", str(dialog.write_text_checkbox.isChecked()).lower()
                )
                node.set_property(
                    "typing_batch_size", str(dialog.typing_batch_size_spin.value())
                )
                node.set_property(
                    "typing_batch_delay", str(dialog.typing_batch_delay_spin.value())
                )
                node.set_property(
                    "use_async", str(dialog.use_async_checkbox.isChecked()).lower()
                )

                # Features tab properties
                node.set_property(
                    "use_vision", str(dialog.use_vision_checkbox.isChecked()).lower()
                )
                node.set_property(
                    "vision_model",
                    "",  # Empty — images go directly to the main model
                )
                node.set_property(
                    "screenshot_enabled",
                    str(dialog.screenshot_enabled_checkbox.isChecked()).lower(),
                )
                node.set_property(
                    "use_direct_rag",
                    str(dialog.use_direct_rag_checkbox.isChecked()).lower(),
                )
                node.set_property(
                    "rag_embedding_model", dialog.rag_embedding_model_entry.text()
                )
                node.set_property(
                    "rag_chunk_size", str(dialog.rag_chunk_size_spin.value())
                )
                node.set_property("rag_overlap", str(dialog.rag_overlap_spin.value()))
                node.set_property("rag_top_k", str(dialog.rag_top_k_spin.value()))
                node.set_property(
                    "rag_include_raw_input",
                    str(dialog.rag_include_raw_input_checkbox.isChecked()).lower(),
                )
                node.set_property(
                    "rag_max_chars", str(dialog.rag_max_chars_spin.value())
                )
                # Context consolidation config (save)
                node.set_property(
                    "use_context_consolidation",
                    str(dialog.use_consolidation_checkbox.isChecked()).lower(),
                )
                node.set_property(
                    "consolidation_chunk_size",
                    str(dialog.consolidation_chunk_size_spin.value()),
                )
                node.set_property(
                    "consolidation_overlap",
                    str(dialog.consolidation_overlap_spin.value()),
                )
                node.set_property(
                    "consolidation_top_k", str(dialog.consolidation_top_k_spin.value())
                )
                node.set_property(
                    "consolidation_max_tokens",
                    str(dialog.consolidation_max_tokens_spin.value()),
                )
                try:
                    import json as _json

                    docs = [
                        dialog.rag_documents_list.item(i).text()
                        for i in range(dialog.rag_documents_list.count())
                    ]
                    node.set_property("rag_documents", _json.dumps(docs))
                except Exception:
                    node.set_property("rag_documents", "[]")
                node.set_property("tool_descriptions", "{}")

                # Skills configuration
                try:
                    cfg = dialog.get_config()
                    skills = cfg.get("skills", [])
                    import json as _json

                    node.set_property(
                        "skills",
                        _json.dumps(skills) if isinstance(skills, list) else skills,
                    )
                except Exception:
                    node.set_property("skills", "[]")
                try:
                    cfg = dialog.get_config()
                    use_skill_routing = cfg.get("use_skill_routing", True)
                    node.set_property(
                        "use_skill_routing", str(use_skill_routing).lower()
                    )
                except Exception:
                    node.set_property("use_skill_routing", "true")

                # Orchestrator switch (ON = orchestrator; OFF = vanilla LLM).
                try:
                    node.set_orchestrator_mode(
                        bool(dialog.orchestrator_mode_check.isChecked()))
                    node.set_property(
                        "orch_max_steps", str(dialog.orch_max_steps_spin.value()))
                    node.set_property(
                        "orch_goal", dialog.orch_goal_edit.toPlainText())
                    node.set_property(
                        "orch_synthesize",
                        bool(dialog.orch_synthesize_check.isChecked()))
                    node.set_property(
                        "orch_synthesis_system",
                        dialog.orch_synthesis_system_edit.text())
                    node.set_property(
                        "orch_use_goal_ledger",
                        bool(dialog.orch_use_goal_ledger_check.isChecked()))
                except Exception:
                    node.set_property("orchestrator_mode", False)

                # ----- llama.cpp config (save) -----
                node.set_property(
                    "use_llamacpp",
                    str(dialog.engine_combo.currentText() == "llama.cpp").lower(),
                )
                node.set_property(
                    "llamacpp_model_path", dialog.llamacpp_model_combo.currentText()
                )
                node.set_property(
                    "llamacpp_context_size", str(dialog.context_size_spin.value())
                )
                node.set_property(
                    "llamacpp_gpu_layers", str(dialog.gpu_layers_spin.value())
                )
                # -------------------------------------

                logger.info(f"Successfully edited LLM node: {node.id}")
            else:
                logger.debug("Dialog cancelled")

        except Exception as e:
            print(f"unmapped exception {e}")
