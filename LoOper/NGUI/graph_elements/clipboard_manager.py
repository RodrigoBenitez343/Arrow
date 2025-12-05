import logging

logger = logging.getLogger(__name__)

class ClipboardManager:
    def __init__(self, parent_widget):
        self.parent = parent_widget
        self.copied_nodes = []

    def get_property_whitelist(self, node_type: str):
        if node_type in ("sequence", "sequence.SequenceNode") or node_type.endswith("SequenceNode"):
            return {"sequence_file", "loop_count", "extra_delay"}
        if node_type in ("conditional", "conditional.ConditionalNode") or node_type.endswith("ConditionalNode"):
            return {
                "condition_type","image_path","confidence","threshold","timeout","wait_time",
                "max_loops","max_attempts","delay_between_attempts","target_text","ocr_text","case_sensitive",
                "loop_type","loop_position","sequence_file","iteration_delay","loop_condition",
            }
        if node_type in ("llm", "llm.LLMNode") or node_type.endswith("LLMNode"):
            return {
                "model","prompt","system_message","temperature","max_tokens","output_variable","api_url",
                "write_text","use_vision","screenshot_enabled","use_comorag","max_cycles","memory_size",
                "typing_batch_size","typing_batch_delay","input_source","ocr_confidence","ocr_preprocessing","use_async",
            }
        if node_type in ("tts", "tts.TTSNode") or node_type.endswith("TTSNode"):
            return {"text","language"}
        if node_type in ("chain_import", "chain_import.ChainImportNode") or node_type.endswith("ChainImportNode"):
            return {"chain_file","import_mode","prefix","loop_count","extra_delay","enabled"}
        if node_type in ("form_filler", "form_filler.FormFillerNode") or node_type.endswith("FormFillerNode"):
            return {"instruction","model","confidence_threshold"}
        return set()

    def copy_selected_nodes(self):
        self.copied_nodes = []
        ng = self.parent.node_graph
        selected_nodes = ng.selected_nodes()
        if not selected_nodes:
            return
        selected_ids = {n.id for n in selected_nodes}
        for node in selected_nodes:
            node_type = getattr(node, "__identifier__", node.type_) if hasattr(node, "__identifier__") else node.type_
            whitelist = self.get_property_whitelist(node_type)
            props = {}
            for key, value in node.properties().items():
                if key in whitelist:
                    props[key] = value
            if node_type.endswith("ChainImportNode") or node_type == "chain_import" or node_type == "chain_import.ChainImportNode":
                try:
                    import_cfg = node.get_chain_import_config()
                    if isinstance(import_cfg, dict):
                        props = {k: import_cfg.get(k) for k in whitelist}
                except Exception:
                    pass
            if node_type.endswith("SequenceNode") or node_type == "sequence" or node_type == "sequence.SequenceNode":
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
            self.copied_nodes.append(node_data)

    def paste_copied_nodes(self, paste_pos=None):
        if not self.copied_nodes:
            return
        try:
            min_x = min(nd.get("position", [0, 0])[0] for nd in self.copied_nodes)
            min_y = min(nd.get("position", [0, 0])[1] for nd in self.copied_nodes)
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
        for idx, node_data in enumerate(self.copied_nodes):
            node_type = node_data["type"]
            original_id = node_data["node_id"]
            rel_x = node_data.get("position", [0, 0])[0] - min_x
            rel_y = node_data.get("position", [0, 0])[1] - min_y
            np_x = paste_pos[0] + rel_x + (idx * 10)
            np_y = paste_pos[1] + rel_y + (idx * 10)
            new_pos = self.parent.graph_manager._snap_point(np_x, np_y)

            new_node = None
            try:
                if node_type.endswith("SequenceNode") or node_type == "sequence":
                    new_node = self.parent.graph_manager.create_node("sequence.SequenceNode", pos=new_pos)
                elif node_type.endswith("ConditionalNode") or node_type == "conditional":
                    new_node = self.parent.graph_manager.create_node("conditional.ConditionalNode", pos=new_pos)
                elif node_type.endswith("LLMNode") or node_type == "llm":
                    new_node = self.parent.graph_manager.create_node("llm.LLMNode", pos=new_pos)
                elif node_type.endswith("TTSNode") or node_type == "tts":
                    new_node = self.parent.graph_manager.create_node("tts.TTSNode", pos=new_pos)
                elif node_type.endswith("ChainImportNode") or node_type == "chain_import":
                    new_node = self.parent.graph_manager.create_node("chain_import.ChainImportNode", pos=new_pos)
                elif node_type.endswith("FormFillerNode") or node_type == "form_filler":
                    new_node = None
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
                except Exception:
                    pass
            elif node_type.endswith("SequenceNode") or node_type == "sequence" or node_type == "sequence.SequenceNode":
                cfg = node_data.get("properties", {})
                try:
                    seq_config = {
                        "sequence_file": cfg.get("sequence_file", "") or "",
                        "loop_count": int(cfg.get("loop_count", 1) or 1),
                        "extra_delay": float(cfg.get("extra_delay", 1.0) or 1.0),
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

            base_name = node_data.get("name") or getattr(new_node, "NODE_NAME", "Node")
            try:
                new_node.set_name(f"{base_name}_copy")
            except Exception:
                pass

            id_mapping[original_id] = new_node
            new_nodes.append(new_node)

        for node_data in self.copied_nodes:
            source_new = id_mapping.get(node_data["node_id"])
            if not source_new:
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

