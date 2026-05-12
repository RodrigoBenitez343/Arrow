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
                # Use the first real cached model if available; otherwise leave empty
                try:
                    cached = get_real_cached_models(timeout=2.0)
                    default_model = cached[0] if cached else ""
                except Exception:
                    default_model = ""
                node.set_property("model", default_model)
                node.set_property("vision_model", "minicpm-v:latest")
                node.set_property("temperature", "0.7")
                node.set_property("max_tokens", "150")

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
                node.set_property("model", llm_node_data.get("model", "llama3.2:latest"))
                node.set_property("system_message", llm_node_data.get("system_message", ""))
                node.set_property("temperature", str(llm_node_data.get("temperature", 0.7)))
                node.set_property("max_tokens", str(llm_node_data.get("max_tokens", 4096)))
                node.set_property("output_variable", llm_node_data.get("output_variable", "llm_output"))
                node.set_property("api_url", llm_node_data.get("api_url", ""))
                node.set_property("write_text", str(llm_node_data.get("write_text", True)).lower())
                node.set_property("use_vision", str(llm_node_data.get("use_vision", False)).lower())
                node.set_property("vision_model", llm_node_data.get("vision_model", "minicpm-v:latest"))
                node.set_property("use_advanced_vision", str(llm_node_data.get("use_advanced_vision", False)).lower())
                node.set_property("screenshot_enabled", str(llm_node_data.get("screenshot_enabled", True)).lower())
                node.set_property("use_comorag", str(llm_node_data.get("use_comorag", True)).lower())
                node.set_property("max_cycles", str(llm_node_data.get("max_cycles", 5)))
                node.set_property("memory_size", str(llm_node_data.get("memory_size", 1000)))
                node.set_property("typing_batch_size", str(llm_node_data.get("typing_batch_size", 20)))
                node.set_property("typing_batch_delay", str(llm_node_data.get("typing_batch_delay", 0.05)))
                node.set_property("input_source", llm_node_data.get("input_source", "none"))
                node.set_property("ocr_confidence", str(llm_node_data.get("ocr_confidence", 0.5)))
                node.set_property("ocr_preprocessing", str(llm_node_data.get("ocr_preprocessing", True)).lower())
                node.set_property("use_async", str(llm_node_data.get("use_async", True)).lower())
                node.set_property("use_direct_rag", str(llm_node_data.get("use_direct_rag", False)).lower())
                node.set_property("rag_embedding_model", llm_node_data.get("rag_embedding_model", "nomic-embed-text"))
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
                node.set_property("llamacpp_gpu_layers", str(llm_node_data.get("llamacpp_gpu_layers", 35)))
                node.set_property("llamacpp_threads", str(llm_node_data.get("llamacpp_threads", 8)))
                node.set_property("llamacpp_context_size", str(llm_node_data.get("llamacpp_context_size", 4096)))

                # Set name
                semantic_desc = llm_node_data.get("semantic_description", "")
                model = llm_node_data.get("model", "llama3.2:latest")
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
                node.get_property("model") or "llama3.2:latest"
            )
            dialog.api_url_entry.setText(node.get_property("api_url") or "")
            dialog.temperature_spin.setValue(
                float(node.get_property("temperature") or 0.7)
            )
            dialog.max_tokens_spin.setValue(
                int(node.get_property("max_tokens") or 4096)
            )
            dialog.output_variable_entry.setText(
                node.get_property("output_variable") or "llm_output"
            )

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

            # Input source configuration
            input_source = node.get_property("input_source") or "none"
            if input_source == "none":
                dialog.input_none_radio.setChecked(True)
            elif input_source == "ocr":
                dialog.input_ocr_radio.setChecked(True)
            elif input_source == "previous":
                dialog.input_previous_radio.setChecked(True)
            elif input_source == "clipboard":
                dialog.input_clipboard_radio.setChecked(True)

            # OCR configuration
            dialog.ocr_confidence_spin.setValue(
                float(node.get_property("ocr_confidence") or 0.7)
            )
            dialog.ocr_preprocessing_combo.setCurrentText(
                node.get_property("ocr_preprocessing") or "None"
            )

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

            use_advanced_vision_str = (
                node.get_property("use_advanced_vision") or "false"
            )
            use_advanced_vision = use_advanced_vision_str.lower() in (
                "true",
                "1",
                "yes",
                "on",
            )
            dialog.use_advanced_vision_checkbox.setChecked(use_advanced_vision)

            screenshot_enabled_str = node.get_property("screenshot_enabled") or "true"
            screenshot_enabled = screenshot_enabled_str.lower() in (
                "true",
                "1",
                "yes",
                "on",
            )
            dialog.screenshot_enabled_checkbox.setChecked(screenshot_enabled)

            # ComoRAG configuration
            use_comorag_str = node.get_property("use_comorag") or "true"
            use_comorag = use_comorag_str.lower() in ("true", "1", "yes", "on")
            dialog.use_comorag_checkbox.setChecked(use_comorag)

            max_cycles = int(node.get_property("max_cycles") or 5)
            dialog.max_cycles_spin.setValue(max_cycles)

            memory_size = int(node.get_property("memory_size") or 1000)
            dialog.memory_size_spin.setValue(memory_size)
            use_direct_rag_str = node.get_property("use_direct_rag") or "false"
            use_direct_rag = use_direct_rag_str.lower() in ("true", "1", "yes", "on")
            dialog.use_direct_rag_checkbox.setChecked(use_direct_rag)
            dialog.rag_embedding_model_entry.setText(
                node.get_property("rag_embedding_model") or "nomic-embed-text"
            )
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
                node.set_property("max_tokens", str(dialog.max_tokens_spin.value()))
                node.set_property(
                    "output_variable", dialog.output_variable_entry.text()
                )

                # Prompt tab properties
                node.set_property(
                    "system_message", dialog.system_message_edit.toPlainText()
                )
                node.set_property("prompt", dialog.prompt_edit.toPlainText())

                # Input source configuration
                if dialog.input_none_radio.isChecked():
                    node.set_property("input_source", "none")
                elif dialog.input_ocr_radio.isChecked():
                    node.set_property("input_source", "ocr")
                elif dialog.input_previous_radio.isChecked():
                    node.set_property("input_source", "previous")
                elif dialog.input_clipboard_radio.isChecked():
                    node.set_property("input_source", "clipboard")

                # OCR configuration
                node.set_property(
                    "ocr_confidence", str(dialog.ocr_confidence_spin.value())
                )
                node.set_property(
                    "ocr_preprocessing", dialog.ocr_preprocessing_combo.currentText()
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
                    "use_advanced_vision",
                    str(dialog.use_advanced_vision_checkbox.isChecked()).lower(),
                )
                node.set_property(
                    "screenshot_enabled",
                    str(dialog.screenshot_enabled_checkbox.isChecked()).lower(),
                )
                node.set_property(
                    "use_comorag", str(dialog.use_comorag_checkbox.isChecked()).lower()
                )
                node.set_property("max_cycles", str(dialog.max_cycles_spin.value()))
                node.set_property("memory_size", str(dialog.memory_size_spin.value()))
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
                try:
                    import json as _json

                    docs = [
                        dialog.rag_documents_list.item(i).text()
                        for i in range(dialog.rag_documents_list.count())
                    ]
                    node.set_property("rag_documents", _json.dumps(docs))
                except Exception:
                    node.set_property("rag_documents", "[]")
                try:
                    cfg = dialog.get_config()
                    td = cfg.get("tool_descriptions", {})
                    import json as _json

                    node.set_property("tool_descriptions", _json.dumps(td))
                except Exception:
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

                # ----- llama.cpp config (save) -----
                node.set_property(
                    "use_llamacpp",
                    str(dialog.engine_combo.currentText() == "llama.cpp").lower(),
                )
                node.set_property(
                    "llamacpp_model_path", dialog.llamacpp_model_combo.currentText()
                )
                # -------------------------------------

                logger.info(f"Successfully edited LLM node: {node.id}")
            else:
                logger.debug("Dialog cancelled")

        except Exception as e:
            print(f"unmapped exception {e}")
