import logging

logger = logging.getLogger(__name__)

class ClipboardManager:
    # Shared clipboard across all GraphViewWidget instances (main graph, expansion dialogs, etc.)
    # Each entry is a FULL single-node config dict (ConfigManager.get_single_node_config),
    # so paste restores every property AND rebuilds dynamic ports exactly like loading a chain.
    _shared_clipboard = []

    def __init__(self, parent_widget):
        self.parent = parent_widget

    @staticmethod
    def _empty_config():
        """A chain-config skeleton holding every node-category list."""
        return {
            "sequences": [],
            "conditional_nodes": [],
            "llm_nodes": [],
            "chain_import_nodes": [],
            "form_filler_nodes": [],
            "code_nodes": [],
            "container_nodes": [],
            "context_nodes": [],
            "input_nodes": [],
            "handle_nodes": [],
            "mcp_nodes": [],
            "output_nodes": [],
            "web_sequences": [],
        }

    def _scene_view(self):
        """The parent's cursor<->scene view (NodeGraphQt viewer), if any."""
        try:
            getter = getattr(self.parent, "_scene_view", None)
            if callable(getter):
                return getter()
        except Exception:
            pass
        v = getattr(self.parent, "view", None)
        if v is not None and hasattr(v, "mapToScene"):
            return v
        return None

    def _resolve_paste_pos(self):
        """Scene point the pasted group is CENTERED on.

        Preference: the live cursor when it is over the canvas (matches the
        Ctrl+V gesture), else the last canvas mouse position seen, else the
        last right-click position, else the view centre.  Returns [x, y] or
        None.
        """
        view = self._scene_view()
        if view is not None and hasattr(view, "mapFromGlobal") and hasattr(view, "rect"):
            try:
                from PyQt5.QtGui import QCursor

                local = view.mapFromGlobal(QCursor.pos())
                if view.rect().contains(local):
                    sp = view.mapToScene(local)
                    return [int(sp.x()), int(sp.y())]
            except Exception:
                pass
        tracked = getattr(self.parent, "_last_canvas_scene_pos", None)
        if tracked:
            try:
                return [int(tracked[0]), int(tracked[1])]
            except Exception:
                pass
        sp = getattr(self.parent, "_last_context_scene_pos", None)
        if sp is not None:
            try:
                return [int(sp.x()), int(sp.y())]
            except Exception:
                pass
        if view is not None and hasattr(view, "rect"):
            try:
                sc = view.mapToScene(view.rect().center())
                return [int(sc.x()), int(sc.y())]
            except Exception:
                pass
        return None

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
                # The orchestrator switch and its settings: without these a
                # pasted orchestrator node would come back as a vanilla LLM
                # node and lose its web/headless observation surface.
                "orchestrator_mode","orch_max_steps","orch_goal","orch_synthesize",
                "orch_synthesis_system","orch_use_goal_ledger","orch_headless","web_mode",
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
                    "typing_batch_delay",
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
                    "scope", "shared_context_chain_file", "shared_context_node_id",
                    "documents", "skills"}
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
        """Snapshot each selected node with the CANONICAL serializer.

        Uses ConfigManager.get_single_node_config so the copied payload carries
        every property the save path writes (ports follow from that on paste) -
        never a hand-maintained whitelist that silently drops fields.
        """
        ClipboardManager._shared_clipboard.clear()
        ng = self.parent.node_graph
        selected_nodes = ng.selected_nodes()
        if not selected_nodes:
            return
        config_manager = getattr(self.parent, "config_manager", None)
        if config_manager is None:
            logger.warning("No config_manager on parent; cannot copy nodes")
            return
        for node in selected_nodes:
            try:
                config = config_manager.get_single_node_config(node)
            except Exception as e:
                logger.warning(f"Failed to serialise node {node} for copy: {e}")
                continue
            if not isinstance(config, dict):
                continue
            # Record the node's on-canvas size so paste can CENTER the group on
            # the cursor (positions alone only give each node's top-left).
            try:
                _br = node.view.boundingRect()
                _w, _h = int(_br.width()), int(_br.height())
            except Exception:
                _w, _h = 160, 60
            for _val in config.values():
                if isinstance(_val, list):
                    for _nd in _val:
                        if isinstance(_nd, dict):
                            _nd["_copy_w"] = _w
                            _nd["_copy_h"] = _h
            ClipboardManager._shared_clipboard.append(config)

    def paste_copied_nodes(self, paste_pos=None):
        """Recreate the copied nodes under the cursor.

        Paste is driven through the SAME per-type config loaders the chain
        loader uses (_create_* + _restore_connections), so every property and
        every dynamic port (Input true/false, LLM orchestrator tools/brains,
        conditional loop port, ...) is rebuilt exactly as on a fresh load.
        """
        if not ClipboardManager._shared_clipboard:
            return
        config_manager = getattr(self.parent, "config_manager", None)
        if config_manager is None:
            logger.warning("No config_manager on parent; cannot paste nodes")
            return

        # Flatten the copied single-node configs into (list_key, node_data) pairs.
        entries = []
        for config in ClipboardManager._shared_clipboard:
            if not isinstance(config, dict):
                continue
            for key, value in config.items():
                if isinstance(value, list):
                    for node_data in value:
                        if isinstance(node_data, dict):
                            entries.append((key, node_data))
        if not entries:
            return

        # Anchor the group's top-left corner at the cursor / anchor position.
        if paste_pos is None:
            paste_pos = self._resolve_paste_pos()
        if paste_pos is None:
            self.parent._paste_counter = getattr(self.parent, "_paste_counter", 0) + 1
            base_offset = 100
            paste_pos = [
                100 + (self.parent._paste_counter * base_offset),
                100 + (self.parent._paste_counter * base_offset),
            ]
        else:
            paste_pos = [int(paste_pos[0]), int(paste_pos[1])]

        # Bounding box of the copied group: positions are top-left corners, so
        # each node's recorded size is added to find the visual CENTRE.
        try:
            gx0 = min(nd.get("position", [0, 0])[0] for _, nd in entries)
            gy0 = min(nd.get("position", [0, 0])[1] for _, nd in entries)
            gx1 = max(
                nd.get("position", [0, 0])[0] + nd.get("_copy_w", 160)
                for _, nd in entries
            )
            gy1 = max(
                nd.get("position", [0, 0])[1] + nd.get("_copy_h", 60)
                for _, nd in entries
            )
        except Exception:
            gx0 = gy0 = 0
            gx1 = gy1 = 0
        centre_x = (gx0 + gx1) / 2.0
        centre_y = (gy0 + gy1) / 2.0

        graph_manager = self.parent.graph_manager

        def _snap(x, y):
            try:
                return list(graph_manager._snap_point(x, y))
            except Exception:
                return [x, y]

        # Build a temporary chain config holding only the copied nodes, with
        # positions shifted so the group's CENTRE lands on paste_pos.
        merged = self._empty_config()
        for key, node_data in entries:
            node_copy = dict(node_data)
            node_copy.pop("_copy_w", None)
            node_copy.pop("_copy_h", None)
            try:
                cur_x, cur_y = node_data.get("position", [0, 0])[:2]
            except Exception:
                cur_x, cur_y = 0, 0
            node_copy["position"] = _snap(
                int(round(paste_pos[0] - centre_x + cur_x)),
                int(round(paste_pos[1] - centre_y + cur_y)),
            )
            merged.setdefault(key, [])
            merged[key].append(node_copy)

        backup_config = config_manager.chain_config
        node_map = {}
        config_manager.chain_config = merged
        try:
            config_manager._create_sequence_nodes(node_map)
            config_manager._create_web_sequence_nodes(node_map)
            config_manager._create_conditional_nodes(node_map)
            config_manager._create_llm_nodes(node_map)
            config_manager._create_chain_import_nodes(node_map)
            config_manager._create_form_filler_nodes(node_map)
            config_manager._create_code_nodes(node_map)
            config_manager._create_container_nodes(node_map)
            config_manager._create_context_nodes(node_map)
            config_manager._create_input_nodes(node_map)
            config_manager._create_handle_nodes(node_map)
            config_manager._create_mcp_nodes(node_map)
            config_manager._create_output_nodes(node_map)
            config_manager._restore_connections(node_map)
        except Exception as e:
            logger.error(f"Error rebuilding pasted nodes: {e}")
        finally:
            config_manager.chain_config = backup_config

        new_nodes = [n for n in node_map.values() if n is not None]
        for new_node in new_nodes:
            # Context/Output nodes persist an internal '_config_node_id' that
            # the save path writes as the node_id.  A pasted copy inherited the
            # ORIGINAL id, so saving the chain emitted two nodes with the same
            # node_id (broken graph on reload).  Re-point each copy at its own
            # runtime id so every pasted node is self-identifying and unique.
            #
            # ONLY nodes that actually declare '_config_node_id' may be written
            # here, and the write MUST skip the undo stack: NodeGraphQt routes a
            # command-backed set_property through a QUndoStack redo() callback,
            # so a NodePropertyError raised there never propagates back to this
            # try/except - it reaches PyQt's qFatal and ABORTS the app
            # (0xC0000409).
            try:
                _props = new_node.properties()
                _has_cfg_id = ('_config_node_id' in _props) or (
                    '_config_node_id' in (_props.get('custom') or {})
                )
                if _has_cfg_id:
                    new_node.set_property(
                        '_config_node_id', new_node.id, push_undo=False,
                    )
            except Exception:
                pass
            try:
                new_node.set_name(f"{new_node.name()}_copy")
            except Exception:
                pass

        try:
            self.parent.node_graph.clear_selection()
            for new_node in new_nodes:
                new_node.set_selected(True)
        except Exception:
            pass
