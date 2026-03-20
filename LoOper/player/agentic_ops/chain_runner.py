import json
import os
import tempfile


class ChainRunner:
    def __init__(self, get_llm_executor, on_minimize=None, on_restore=None):
        self._get_llm_executor = get_llm_executor
        self._on_minimize = on_minimize
        self._on_restore = on_restore

    def run_chain_batch(self, tools):
        if not tools:
            return True
        temp_path = None
        try:
            temp_path = self._build_chain_batch_file(tools)
            return self.run_chain_file(temp_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def run_chain_file(self, chain_path):
        if not chain_path or not os.path.exists(chain_path):
            return False
        try:
            with open(chain_path, "r", encoding="utf-8") as f:
                chain_config = json.load(f)
        except Exception:
            return False
        chain_name = ""
        chain_desc = ""
        try:
            if isinstance(chain_config, dict):
                chain_name = chain_config.get("name") or ""
                chain_desc = chain_config.get("description") or ""
        except Exception:
            chain_name = ""
            chain_desc = ""
        if self._on_minimize:
            try:
                self._on_minimize()
            except Exception:
                pass
        try:
            from player.multi_sequence_player import MultiSequencePlayer
            from player.keyboard_monitor import start_global_monitoring, stop_global_monitoring, create_stop_flag
            from player.multi_sequence.llm_executor import LLMExecutor
        except Exception:
            return False
        ran_ok = True
        player = None
        try:
            llm_executor = self._get_llm_executor(LLMExecutor)
            player = MultiSequencePlayer(chain_config, chain_file_path=chain_path, variables=llm_executor.variables)
            start_global_monitoring()
            stop_flag = create_stop_flag()
            try:
                player.play_chain(stop_flag=stop_flag)
            finally:
                stop_global_monitoring()
        except Exception:
            ran_ok = False
        try:
            if player is not None:
                llm_executor = self._get_llm_executor(None)
                if llm_executor is not None:
                    player_results = {}
                    player_result_text = ""
                    try:
                        if hasattr(player, "llm_executor") and player.llm_executor:
                            val = player.llm_executor.get_variable("chain_tool_results")
                            if isinstance(val, dict):
                                player_results = val
                            val = player.llm_executor.get_variable("chain_tool_last_result_text")
                            if val:
                                player_result_text = str(val)
                    except Exception:
                        pass
                    summary = None
                    if not player_result_text:
                        try:
                            if hasattr(player, "workflow_executor") and player.workflow_executor:
                                summary = getattr(player.workflow_executor, "last_node_summary", None)
                                player_result_text = self.format_chain_last_result(summary, chain_name, chain_desc)
                        except Exception:
                            summary = None
                    if player_result_text:
                        llm_executor.set_variable("chain_tool_last_result_text", player_result_text)
                    if player_results:
                        llm_executor.set_variable("chain_tool_results", player_results)
                    elif summary:
                        llm_executor.set_variable("chain_tool_results", {"last_run": {"chain_name": chain_name, "chain_description": chain_desc, "last_node": summary}})
        except Exception:
            pass
        if self._on_restore:
            try:
                self._on_restore()
            except Exception:
                pass
        return ran_ok

    def _build_chain_batch_file(self, tools):
        chain_import_nodes = []
        node_ids = []
        for idx, tool in enumerate(tools):
            node_id = tool.get("id") or f"chat_chain_{idx}"
            node_ids.append(node_id)
        for idx, tool in enumerate(tools):
            node_id = node_ids[idx]
            connections = []
            if idx < len(tools) - 1:
                connections = [{"target_node_id": node_ids[idx + 1], "input_port": "input"}]
            chain_import_nodes.append({
                "type": "chain_import",
                "chain_file_path": tool.get("path", ""),
                "import_mode": "full",
                "prefix": "",
                "loop_count": 1,
                "extra_delay": 0,
                "enabled": True,
                "node_id": node_id,
                "position": [0.0, 0.0],
                "connections": connections
            })
        chain_config = {
            "sequences": [],
            "conditional_nodes": [],
            "llm_nodes": [],
            "tts_nodes": [],
            "chain_import_nodes": chain_import_nodes,
            "form_filler_nodes": [],
            "code_nodes": [],
            "description": "Temporary chat chain batch"
        }
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        temp_path = tmp.name
        tmp.close()
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(chain_config, f, indent=2)
        return temp_path

    def get_last_chain_result_text(self):
        try:
            llm_executor = self._get_llm_executor(None)
            if llm_executor is None:
                return ""
            val = llm_executor.get_variable("chain_tool_last_result_text")
            return str(val) if val else ""
        except Exception:
            return ""

    def get_chain_tool_results(self):
        try:
            llm_executor = self._get_llm_executor(None)
            if llm_executor is None:
                return {}
            val = llm_executor.get_variable("chain_tool_results")
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}

    def format_chain_last_result(self, summary, chain_name="", chain_desc=""):
        if not isinstance(summary, dict):
            return ""
        parts = []
        if chain_name:
            parts.append(f"Chain Name: {chain_name}")
        if chain_desc:
            parts.append(f"Chain Description: {chain_desc}")
        node_id = summary.get("node_id")
        node_type = summary.get("type")
        if node_id or node_type:
            parts.append(f"Last Node: {node_id} ({node_type})")
        if node_type == "code":
            parts.append(f"Code Result: {summary.get('result')}")
        elif node_type == "conditional":
            parts.append(f"Conditional Result: {summary.get('result')}")
            if summary.get("criteria"):
                parts.append(f"Criteria: {summary.get('criteria')}")
        elif node_type == "llm":
            llm_result = summary.get("result") or {}
            if isinstance(llm_result, dict):
                q = llm_result.get("query")
                r = llm_result.get("response")
                if q:
                    parts.append(f"LLM Query: {q}")
                if r is not None:
                    parts.append(f"LLM Response: {r}")
        return "\n".join([p for p in parts if p])
