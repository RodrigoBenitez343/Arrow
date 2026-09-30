import json
import logging

logger = logging.getLogger(__name__)

class ClipboardManager:
    # Shared clipboard across all GraphViewWidget instances (main graph, expansion dialogs, etc.)
    _shared_clipboard = []

    def __init__(self, parent_widget):
        self.parent = parent_widget

    @staticmethod
    def _parse_extract_items(value):
        """Parse the extract_items value (JSON string or list) into a list."""
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value or '[]')
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def get_property_whitelist(self, node_type: str):
        # WebSequenceNode ends with "SequenceNode" - it MUST be checked before
        # the desktop sequence branch or web nodes would copy/paste as desktop.
        if node_type in ("web_sequence", "web_sequence.WebSequenceNode") or node_type.endswith("WebSequenceNode"):
            return {"session_file", "headless", "speed", "native_actions", "loop_count", "extra_delay", "extract_items"}
        if node_type in ("sequence", "sequence.SequenceNode") or node_type.endswith("SequenceNode"):
            return {"sequence_file", "loop_count", "extra_delay", "use_app_opened", "click_drift_min", "click_drift_max"}
        if node_type in ("conditional", "conditional.ConditionalNode") or node_type.endswith("ConditionalNode"):
            return {
                "condition_type","image_path","confidence","threshold","timeout","wait_time",
                "max_loops","max_attempts","delay_between_attempts","target_text","ocr_text","case_sensitive",
                "target_x","target_y","target_w","target_h","position_tolerance",
                "loop_type","loop_position","sequence_file","iteration_delay","loop_condition",
            }
        if node_type in ("llm", "llm.LLMNode") or node_type.endswith("LLMNode"):
            return {
                "model","prompt","system_message","temperature","max_tokens","output_variable","api_url",
                "write_text","use_vision","screenshot_enabled",
                "typing_batch_size","typing_batch_delay","input_source","ocr_confidence","ocr_preprocessing","use_async",
            }
        if node_type in ("chain_import", "chain_import.ChainImportNode") or node_type.endswith("ChainImportNode"):
            return {"chain_file","import_mode","prefix","loop_count","extra_delay","enabled","emit_data","data_output_nodes"}
        if node_type in ("form_filler", "form_filler.FormFillerNode") or node_type.endswith("FormFillerNode"):
            # EVERY property get_form_filler_config() serialises must be listed
            # here: an omitted one is silently reset to its default on paste -
            # a missing 'web_scope' turned a scoped node into a whole-page one
            # (and the picker result was lost every time the node was copied).
            return {"mode", "instruction", "fields_include", "fields_skip",
                    "probe_top_k", "probe_char_budget", "probe_context_chars",
                    "probe_cycles", "consolidate", "verify", "repair",
                    "repair_attempts", "answer_no", "answer_na", "ask_user",
                    "max_fields", "engine", "model", "temperature",
                    "max_tokens", "context_size", "typing_batch_size",
                    "typing_batch_delay", "rag_documents",
                    "web_scope"}
        if node_type in ("code", "code.CodeNode") or node_type.endswith("CodeNode"):
            return {"code", "file_path", "execute_on_input", "output_variable", "timeout"}
        if node_type in ("container", "container.ContainerNode") or node_type.endswith("ContainerNode"):
            return {"iso_path", "memory_mb", "cpu_cores", "hide_window", "execute_on_input", "output_variable", "timeout"}
        if node_type in ("input", "input.InputNode") or node_type.endswith("InputNode"):
            return {"label", "default_value", "passthrough", "web_mode", "user_prompt",
                    "agent_modifiable",
                    "decision_mode", "decision_criterion", "decision_evaluator",
                    "decision_model", "decision_default", "question_mode", "choices",
                    "route_on_answer", "accept_text", "accept_images", "accept_documents",
                    "tts_enabled", "tts_text",
                    "tts_language", "tts_voice_model", "tts_speed",
                    "tts_speaker_id"}
        if node_type in ("context", "context.ContextNode") or node_type.endswith("ContextNode"):
            return {"label", "max_history", "persistent", "clear_on_finish",
                    "scope", "shared_context_chain_file", "shared_context_node_id"}
        if node_type in ("mcp", "mcp.MCPNode") or node_type.endswith("MCPNode"):
            return {"mcp_folder", "tool_name", "tool_args", "mcp_tools", "keep_alive"}
        if node_type in ("handle", "handle.HandleNode") or node_type.endswith("HandleNode"):
            return {"action_type", "goal_description", "target_description", "agent_adaptive", "web_mode"}
        if node_type in ("output", "output.OutputNode") or node_type.endswith("OutputNode"):
            return {"label", "variable_name", "agent_visible", "overlay_visible",
                    "popup_on_finish", "show_rating",
                    "render_mode", "tts_enabled", "tts_text", "tts_language",
                    "tts_voice_model", "tts_speed", "tts_speaker_id", "tts_wait",
                    "image_source"}
        return set()

    def copy_selected_nodes(self):
        ClipboardManager._shared_clipboard.clear()
        ng = self.parent.node_graph
        selected_nodes = ng.selected_nodes()
        if not selected_nodes:
            return
        selected_ids = {n.id for n in selected_nodes}
        for node in selected_nodes:
            node_type = node.type_
            whitelist = self.get_property_whitelist(node_type)
            props = {}
            # Custom properties (from create_property) are nested under 'custom'
            # by NodeGraphQt's model.to_dict — extract both top-level and custom
            node_props = node.properties()
            for key, value in node_props.items():
                if key in whitelist:
                    props[key] = value
            for key, value in node_props.get('custom', {}).items():
                if key in whitelist:
                    props[key] = value
            if node_type.endswith("ChainImportNode") or node_type == "chain_import" or node_type == "chain_import.ChainImportNode":
                try:
                    import_cfg = node.get_chain_import_config()
                    if isinstance(import_cfg, dict):
                        props = {k: import_cfg.get(k) for k in whitelist}
                except Exception:
                    pass
            elif node_type.endswith("WebSequenceNode") or node_type == "web_sequence" or node_type == "web_sequence.WebSequenceNode":
                try:
                    ws_cfg = node.get_web_sequence_config()
                    if isinstance(ws_cfg, dict):
                        props = {k: ws_cfg.get(k) for k in whitelist}
                except Exception:
                    pass
            elif node_type.endswith("SequenceNode") or node_type == "sequence" or node_type == "sequence.SequenceNode":
                try:
                    seq_cfg = node.get_sequence_config()
                    if isinstance(seq_cfg, dict):
                        props = {k: seq_cfg.get(k) for k in whitelist}
                except Exception:
                    pass
            try:
                nx, ny = node.pos()
            except Exception:
                nx, ny = 0, 0
            node_data = {
                "type": node_type,
                "name": node.name(),
                "node_id": node.id,
                "position": [nx, ny],
                "properties": props,
                "connections": [],
            }
            try:
                for out_port in node.output_ports():
                    for connected_port in out_port.connected_ports():
                        target_node = connected_port.node()
                        if target_node and target_node.id in selected_ids:
                            node_data["connections"].append({
                                "source_port": out_port.name(),
                                "target_node_id": target_node.id,
                                "target_port": connected_port.name(),
                            })
            except Exception:
                pass
            ClipboardManager._shared_clipboard.append(node_data)

    def paste_copied_nodes(self, paste_pos=None):
        if not ClipboardManager._shared_clipboard:
            return
        try:
            min_x = min(nd.get("position", [0, 0])[0] for nd in ClipboardManager._shared_clipboard)
            min_y = min(nd.get("position", [0, 0])[1] for nd in ClipboardManager._shared_clipboard)
        except Exception:
            min_x, min_y = 0, 0
        if paste_pos is None:
            try:
                if getattr(self.parent, "_last_context_scene_pos", None) is not None:
                    scene_point = self.parent._last_context_scene_pos
                elif hasattr(self.parent, "view") and hasattr(self.parent.view, "mapToScene"):
                    scene_point = self.parent.view.mapToScene(self.parent.view.rect().center())
                else:
                    raise AttributeError("view.mapToScene not available")
                paste_pos = [int(scene_point.x()), int(scene_point.y())]
            except Exception:
                self.parent._paste_counter = getattr(self.parent, "_paste_counter", 0) + 1
                base_offset = 100
                paste_pos = [100 + (self.parent._paste_counter * base_offset), 100 + (self.parent._paste_counter * base_offset)]
        else:
            paste_pos = [paste_pos[0], paste_pos[1]]

        id_mapping = {}
        new_nodes = []
        for idx, node_data in enumerate(ClipboardManager._shared_clipboard):
            node_type = node_data["type"]
            original_id = node_data["node_id"]
            rel_x = node_data.get("position", [0, 0])[0] - min_x
            rel_y = node_data.get("position", [0, 0])[1] - min_y
            np_x = paste_pos[0] + rel_x + (idx * 10)
            np_y = paste_pos[1] + rel_y + (idx * 10)
            new_pos = self.parent.graph_manager._snap_point(np_x, np_y)

            new_node = None
            try:
                # WebSequenceNode ends with "SequenceNode" - must be created as
                # its own type BEFORE the desktop sequence branch matches.
                if node_type.endswith("WebSequenceNode") or node_type == "web_sequence" or node_type == "web_sequence.WebSequenceNode":
                    new_node = self.parent.graph_manager.create_node("web_sequence.WebSequenceNode", pos=new_pos)
                elif node_type.endswith("SequenceNode") or node_type == "sequence":
                    new_node = self.parent.graph_manager.create_node("sequence.SequenceNode", pos=new_pos)
                elif node_type.endswith("ConditionalNode") or node_type == "conditional":
                    new_node = self.parent.graph_manager.create_node("conditional.ConditionalNode", pos=new_pos)
                elif node_type.endswith("LLMNode") or node_type == "llm":
                    new_node = self.parent.graph_manager.create_node("llm.LLMNode", pos=new_pos)
                elif node_type.endswith("ChainImportNode") or node_type == "chain_import":
                    new_node = self.parent.graph_manager.create_node("chain_import.ChainImportNode", pos=new_pos)
                elif node_type.endswith("FormFillerNode") or node_type == "form_filler":
                    new_node = self.parent.graph_manager.create_node("form_filler.FormFillerNode", pos=new_pos)
                elif node_type.endswith("CodeNode") or node_type == "code":
                    new_node = self.parent.graph_manager.create_node("code.CodeNode", pos=new_pos)
                elif node_type.endswith("ContextNode") or node_type == "context":
                    new_node = self.parent.graph_manager.create_node("context.ContextNode", pos=new_pos)
                elif node_type.endswith("InputNode") or node_type == "input":
                    new_node = self.parent.graph_manager.create_node("input.InputNode", pos=new_pos)
                elif node_type.endswith("MCPNode") or node_type == "mcp":
                    new_node = self.parent.graph_manager.create_node("mcp.MCPNode", pos=new_pos)
                else:
                    new_node = self.parent.graph_manager.create_node(node_type, pos=new_pos)
            except Exception:
                continue
            if new_node is None:
                continue

            whitelist = self.get_property_whitelist(node_type)
            applied_keys = []
            if node_type.endswith("ChainImportNode") or node_type == "chain_import" or node_type == "chain_import.ChainImportNode":
                cfg = node_data.get("properties", {})
                try:
                    chain_file = cfg.get("chain_file", "") or ""
                    import_mode = cfg.get("import_mode", "full") or "full"
                    prefix = cfg.get("prefix", "") or ""
                    loop_count_val = cfg.get("loop_count", 1)
                    try:
                        loop_count = int(loop_count_val)
                    except Exception:
                        loop_count = int(str(loop_count_val or "1").strip() or 1)
                    extra_delay_val = cfg.get("extra_delay", 0)
                    try:
                        extra_delay = float(extra_delay_val)
                    except Exception:
                        extra_delay = float(str(extra_delay_val or "0").strip() or 0)
                    try:
                        new_node.set_chain_import_data({
                            "chain_file": chain_file,
                            "import_mode": import_mode,
                            "prefix": prefix,
                            "loop_count": loop_count,
                            "extra_delay": extra_delay
                        })
                        applied_keys = list(whitelist)
                    except Exception:
                        pass
                    # Data output port config — set explicitly (the paste above
                    # only restores the file/mode/loop fields).
                    try:
                        new_node.set_property(
                            "emit_data",
                            "true" if cfg.get("emit_data") else "false",
                        )
                        new_node.set_property(
                            "data_output_nodes",
                            json.dumps(cfg.get("data_output_nodes") or []),
                        )
                    except Exception:
                        pass
                except Exception:
                    pass
            elif node_type.endswith("WebSequenceNode") or node_type == "web_sequence" or node_type == "web_sequence.WebSequenceNode":
                cfg = node_data.get("properties", {})
                try:
                    ws_config = {
                        "session_file": cfg.get("session_file", "") or "",
                        "headless": cfg.get("headless", False),
                        "speed": cfg.get("speed", 1.0),
                        "native_actions": cfg.get("native_actions", True),
                        "loop_count": int(cfg.get("loop_count", 1) or 1),
                        "extra_delay": float(cfg.get("extra_delay", 1.0) or 1.0),
                        "extract_items": self._parse_extract_items(cfg.get("extract_items", [])),
                    }
                    new_node.set_web_sequence_data(0, ws_config)
                    applied_keys = list(whitelist)
                except Exception:
                    pass
            elif node_type.endswith("SequenceNode") or node_type == "sequence" or node_type == "sequence.SequenceNode":
                cfg = node_data.get("properties", {})
                try:
                    seq_config = {
                        "sequence_file": cfg.get("sequence_file", "") or "",
                        "loop_count": int(cfg.get("loop_count", 1) or 1),
                        "extra_delay": float(cfg.get("extra_delay", 1.0) or 1.0),
                        "use_app_opened": cfg.get("use_app_opened", True),
                        "click_drift_min": float(cfg.get("click_drift_min", 5.0)),
                        "click_drift_max": float(cfg.get("click_drift_max", 10.0)),
                    }
                    new_node.set_sequence_data(0, seq_config)
                    applied_keys = list(whitelist)
                except Exception:
                    for key, value in node_data.get("properties", {}).items():
                        if key in whitelist:
                            try:
                                new_node.set_property(key, value)
                                applied_keys.append(key)
                            except Exception:
                                pass
            else:
                for key, value in node_data.get("properties", {}).items():
                    if key in whitelist:
                        try:
                            new_node.set_property(key, value)
                            applied_keys.append(key)
                        except Exception:
                            pass

            # Morph conditional node ports so file-loop conditionals expose
            # their single 'output' port before connections are restored.
            if node_type.endswith("ConditionalNode") or node_type == "conditional":
                try:
                    new_node.update_loop_port_mode()
                except Exception:
                    pass

            base_name = node_data.get("name") or getattr(new_node, "NODE_NAME", "Node")
            try:
                new_node.set_name(f"{base_name}_copy")
            except Exception:
                pass

            id_mapping[original_id] = new_node
            new_nodes.append(new_node)

        for node_data in ClipboardManager._shared_clipboard:
            source_new = id_mapping.get(node_data["node_id"])
            if not source_new:
                continue
            # Shared-context clones never restore stored edges into the pasted
            # copy: their cross-chain link is the runtime identity feed, and any
            # copied edge would reference ids that belong to the source chain
            # (reported as dangling by the graph builder).
            _props = node_data.get("properties", {}) or {}
            _ntype = str(node_data.get("type", ""))
            if (
                (_ntype == "context" or "ContextNode" in _ntype)
                and (
                    str(_props.get("shared_context_chain_file") or "").strip()
                    or str(_props.get("shared_context_node_id") or "").strip()
                )
            ):
                continue
            for conn in node_data.get("connections", []):
                target_new = id_mapping.get(conn.get("target_node_id"))
                if not target_new:
                    continue
                try:
                    self.parent.graph_manager.connect_nodes(
                        source_new,
                        target_new,
                        conn.get("source_port"),
                        conn.get("target_port"),
                    )
                except Exception:
                    pass

        try:
            self.parent.node_graph.clear_selection()
            for n in new_nodes:
                n.set_selected(True)
        except Exception:
            pass

