import json
import logging
import os

from PyQt5.QtWidgets import QFileDialog, QMessageBox

from ..nodes import (
    ActionNode,
    ChainImportNode,
    CodeNode,
    ConditionalNode,
    ContextNode,
    FormFillerNode,
    HandleNode,
    InputNode,
    LLMNode,
    MCPNode,
    OutputNode,
    SequenceNode,
    WebSequenceNode,
)

# Import ContainerNode with fallback - feature not fully implemented yet
try:
    from ..nodes import ContainerNode
except ImportError:
    ContainerNode = None

try:
    from ...player.chain_migrations import migrate_legacy_tts
except ImportError:
    from LoOper.player.chain_migrations import migrate_legacy_tts

# Import cache invalidation function
try:
    from ...player.json_cache import clear_cache, invalidate_file
except ImportError:
    # Fallback if import fails
    def invalidate_file(file_path):
        pass

    def clear_cache():
        pass


logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration loading, saving, and graph building."""

    def __init__(self, parent_widget):
        logger.info("Initializing ConfigManager")
        try:
            self.parent_widget = parent_widget
            self.chain_config = {
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
            self._current_chain_description = ""
            self._current_chain_collection = ""
            self._current_chain_file = ""
            # Accumulated routing examples (written by the player's orchestrator:
            # the verified steps each chain served).  Held with the chain through
            # editor load/save so the reinforcement is not silently erased.
            self._current_chain_routing = {}
            # The default system-chain flag must survive editor load/save
            # cycles — losing it silently demotes ORCHESTRATOR.json to "some
            # System chain" and the router may boot a different root.
            self._current_chain_is_default = False
            # ONE shared web browser scope for the whole app: every chain
            # (saved or not) records and plays on the SAME durable Chrome
            # profile, so cookies/logins made once (e.g. signing into
            # Google) survive app restarts in every run mode - GUI, agent,
            # scheduler, CLI - while pages are never restored.  See
            # player.web.session.SHARED_WEB_SCOPE.
            self._chain_scope_key = self._new_chain_scope_key()
            logger.debug("ConfigManager initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing ConfigManager: {e}")
            raise

    def _new_chain_scope_key(self) -> str:
        """The app-wide web browser scope: one shared profile for every chain.

        Per-chain profiles could not survive for chains that are never saved
        to a file (their identity died with every app session, taking the
        recorded logins with it), so web state is now app-wide: recording and
        every run mode use the same durable profile keyed by this constant
        (player.web.session.SHARED_WEB_SCOPE).  ``reset_chain_scope`` keeps
        calling this so "New chain" stays a harmless no-op.
        """
        return "default"

    def _derive_chain_scope_key(self, file_path) -> str:
        """Back-compat alias: every chain resolves to the shared scope.

        Kept so older save/load paths and callers that derive a scope from a
        chain file path keep working - the result is the single shared key.
        """
        return self._new_chain_scope_key()

    def reset_chain_scope(self) -> None:
        """Reset to the shared app-wide browser scope (no-op by design).

        Web state is app-wide (see _new_chain_scope_key), so a new chain does
        not get its own profile - it keeps the one shared browser where the
        user's logins live.
        """
        self._chain_scope_key = self._new_chain_scope_key()
        logger.debug("Chain web scope reset: %s", self._chain_scope_key)

    def get_chain_config(self):
        """Get the current chain configuration."""
        logger.debug("Getting chain configuration")
        try:
            self.save_current_state()
            config_copy = self.chain_config.copy()
            logger.debug(
                f"Chain config contains {len(config_copy.get('sequences', []))} sequences, {len(config_copy.get('conditional_nodes', []))} conditional nodes, {len(config_copy.get('llm_nodes', []))} LLM nodes, {len(config_copy.get('chain_import_nodes', []))} chain import nodes, {len(config_copy.get('form_filler_nodes', []))} form filler nodes, {len(config_copy.get('code_nodes', []))} code nodes, {len(config_copy.get('web_sequences', []))} web sequence nodes"
            )
            return config_copy
        except Exception as e:
            logger.error(f"Error getting chain config: {e}")
            raise

    def save_current_state(self):
        """Save the current graph state to configuration."""
        logger.info("Saving current graph state to configuration")
        try:
            # Clear current config
            logger.debug("Clearing current configuration")
            self.chain_config = {
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
                "collection": getattr(self, "_current_chain_collection", ""),
                "description": getattr(self, "_current_chain_description", ""),
            }
            # The orchestrator writes routing.examples into the chain file;
            # dropping them here would erase the accumulated reinforcement on the
            # next GUI save, so they ride through the editor untouched.
            _routing = getattr(self, "_current_chain_routing", None)
            if _routing:
                self.chain_config["routing"] = _routing
            # Preserve the default-root flag across save cycles (see load).
            if getattr(self, "_current_chain_is_default", False):
                self.chain_config["is_default"] = True

            # Get all nodes
            logger.debug("Getting all nodes from graph")
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            logger.debug(f"Found {len(all_nodes)} nodes to process")

            # Process each node type
            for node in all_nodes:
                if isinstance(node, SequenceNode):
                    logger.debug(f"Saving sequence node: {node.id}")
                    self._save_sequence_node(node)
                elif isinstance(node, WebSequenceNode):
                    logger.debug(f"Saving web sequence node: {node.id}")
                    self._save_web_sequence_node(node)
                elif isinstance(node, ConditionalNode):
                    logger.debug(f"Saving conditional node: {node.id}")
                    self._save_conditional_node(node)
                elif isinstance(node, LLMNode):
                    logger.debug(f"Saving LLM node: {node.id}")
                    self._save_llm_node(node)
                elif isinstance(node, ChainImportNode):
                    logger.debug(f"Saving chain import node: {node.id}")
                    self._save_chain_import_node(node)
                elif isinstance(node, FormFillerNode):
                    logger.debug(f"Saving form filler node: {node.id}")
                    self._save_form_filler_node(node)
                elif isinstance(node, CodeNode):
                    logger.debug(f"Saving Code node: {node.id}")
                    self._save_code_node(node)
                elif ContainerNode is not None and isinstance(node, ContainerNode):
                    logger.debug(f"Saving Container node: {node.id}")
                    self._save_container_node(node)
                elif isinstance(node, ContextNode):
                    logger.debug(f"Saving Context node: {node.id}")
                    self._save_context_node(node)
                elif isinstance(node, InputNode) or getattr(node, '__identifier__', None) == 'input':
                    logger.debug(f"Saving Input node: {node.id}")
                    self._save_input_node(node)
                elif isinstance(node, HandleNode) or getattr(node, '__identifier__', None) == 'handle':
                    logger.debug(f"Saving Handle node: {node.id}")
                    self._save_handle_node(node)
                elif isinstance(node, MCPNode) or getattr(node, '__identifier__', None) == 'mcp':
                    logger.debug(f"Saving MCP node: {node.id}")
                    self._save_mcp_node(node)
                elif isinstance(node, OutputNode) or getattr(node, '__identifier__', None) == 'output':
                    logger.debug(f"Saving Output node: {node.id}")
                    self._save_output_node(node)
                else:
                    logger.warning(f"Unknown node type: {type(node).__name__}")

                logger.info(
                f"Successfully saved current state: {len(self.chain_config['sequences'])} sequences, {len(self.chain_config['conditional_nodes'])} conditional nodes, {len(self.chain_config['llm_nodes'])} LLM nodes, {len(self.chain_config['chain_import_nodes'])} chain import nodes, {len(self.chain_config['form_filler_nodes'])} form filler nodes, {len(self.chain_config['code_nodes'])} code nodes, {len(self.chain_config.get('container_nodes', []))} container nodes, {len(self.chain_config.get('context_nodes', []))} context nodes, {len(self.chain_config.get('input_nodes', []))} input nodes, {len(self.chain_config.get('handle_nodes', []))} handle nodes, {len(self.chain_config.get('mcp_nodes', []))} mcp nodes, {len(self.chain_config.get('output_nodes', []))} output nodes"
            )

        except Exception as e:
            logger.error(f"Error saving current state: {e}")
            QMessageBox.critical(
                self.parent_widget, "Error", f"Failed to save current state: {str(e)}"
            )

    def get_single_node_config(self, node):
        """Get configuration for a single node."""
        logger.debug(f"Getting configuration for single node: {node.id}")
        # Backup current config
        backup_config = self.chain_config

        try:
            # Initialize temporary empty config
            self.chain_config = {
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
            migrate_legacy_tts(self.chain_config)

            # Save the single node
            if isinstance(node, SequenceNode):
                self._save_sequence_node(node)
            elif isinstance(node, WebSequenceNode):
                self._save_web_sequence_node(node)
            elif isinstance(node, ConditionalNode):
                self._save_conditional_node(node)
            elif isinstance(node, LLMNode):
                self._save_llm_node(node)
            elif isinstance(node, ChainImportNode):
                self._save_chain_import_node(node)
            elif isinstance(node, FormFillerNode):
                self._save_form_filler_node(node)
            elif isinstance(node, CodeNode):
                self._save_code_node(node)
            elif ContainerNode is not None and isinstance(node, ContainerNode):
                self._save_container_node(node)
            elif isinstance(node, ContextNode):
                self._save_context_node(node)
            elif isinstance(node, InputNode) or getattr(node, '__identifier__', None) == 'input':
                self._save_input_node(node)
            elif isinstance(node, HandleNode) or getattr(node, '__identifier__', None) == 'handle':
                self._save_handle_node(node)
            elif isinstance(node, MCPNode) or getattr(node, '__identifier__', None) == 'mcp':
                self._save_mcp_node(node)
            elif isinstance(node, OutputNode) or getattr(node, '__identifier__', None) == 'output':
                self._save_output_node(node)

            return self.chain_config.copy()

        finally:
            # Restore original config
            self.chain_config = backup_config

    def load_chain(self, file_path=None):
        """Load a chain configuration from file."""
        logger.info(
            f"Loading chain configuration from file: {file_path or 'user selection'}"
        )
        try:
            if not file_path:
                logger.debug("No file path provided, opening file dialog")
                file_path, selected_filter = QFileDialog.getOpenFileName(
                    self.parent_widget,
                    "Load Chain",
                    self.parent_widget.chains_folder,
                    "JSON files (*.json)",
                )
                logger.debug(f"User selected file: {file_path}")

            if file_path and os.path.exists(file_path):
                logger.debug(f"Loading configuration from: {file_path}")
                # UTF-8 with or without BOM: the locale codec (cp1252 on
                # Windows) silently MOJIBAKES a real em dash and CRASHES on the
                # bytes it leaves undefined (measured 2026-09-24: a chain with
                # an em dash in an LLM system_message became unloadable —
                # "can't decode byte 0x9d in position 2845" — which looked like
                # a broken orchestrator).  utf-8-sig reads plain UTF-8 too.
                with open(file_path, "r", encoding="utf-8-sig") as f:
                    loaded_config = json.load(f)
                migrate_legacy_tts(loaded_config)
                self._migrate_orchestrator_nodes(loaded_config)

                self._current_chain_description = loaded_config.get("description", "")
                self._current_chain_collection = loaded_config.get("collection", "")
                self._current_chain_routing = loaded_config.get("routing") or {}
                self._current_chain_is_default = bool(loaded_config.get("is_default"))
                self._current_chain_file = file_path
                # Loaded chains keep the shared app-wide web browser scope:
                # every chain records/plays on the SAME durable profile, so
                # cookies/history are not tied to where a file lives.  (The
                # stored _web_chain_scope field, when present, is the shared
                # key too - legacy per-chain keys are intentionally ignored.)
                self._chain_scope_key = self._derive_chain_scope_key(file_path)
                logger.debug("Chain web scope set from file: %s", self._chain_scope_key)

                # Ensure all required keys exist
                self.chain_config = {
                    "sequences": loaded_config.get("sequences", []),
                    "conditional_nodes": loaded_config.get("conditional_nodes", []),
                    "llm_nodes": loaded_config.get("llm_nodes", []),
                    "chain_import_nodes": loaded_config.get("chain_import_nodes", []),
                    "form_filler_nodes": loaded_config.get("form_filler_nodes", []),
                    "code_nodes": loaded_config.get("code_nodes", []),
                    "container_nodes": loaded_config.get("container_nodes", []),
                    "context_nodes": loaded_config.get("context_nodes", []),
                    "input_nodes": loaded_config.get("input_nodes", []),
                    "handle_nodes": loaded_config.get("handle_nodes", []),
                    "mcp_nodes": loaded_config.get("mcp_nodes", []),
                    "output_nodes": loaded_config.get("output_nodes", []),
                    "web_sequences": loaded_config.get("web_sequences", []),
                }
                if self._current_chain_routing:
                    self.chain_config["routing"] = self._current_chain_routing
                if self._current_chain_is_default:
                    self.chain_config["is_default"] = True

                logger.debug(
                    f"Loaded config with {len(self.chain_config.get('sequences', []))} sequences, {len(self.chain_config.get('conditional_nodes', []))} conditional nodes, {len(self.chain_config.get('llm_nodes', []))} LLM nodes, {len(self.chain_config.get('chain_import_nodes', []))} chain import nodes, {len(self.chain_config.get('form_filler_nodes', []))} form filler nodes, {len(self.chain_config.get('code_nodes', []))} code nodes, {len(self.chain_config.get('container_nodes', []))} container nodes, {len(self.chain_config.get('context_nodes', []))} context nodes, {len(self.chain_config.get('input_nodes', []))} input nodes, {len(self.chain_config.get('handle_nodes', []))} handle nodes, {len(self.chain_config.get('mcp_nodes', []))} mcp nodes"
                )

                # Build graph from config
                logger.debug("Building graph from loaded configuration")
                self.build_graph_from_config()

                logger.info(f"Successfully loaded chain from: {file_path}")
                return True
            else:
                logger.warning(f"File path invalid or doesn't exist: {file_path}")

        except Exception as e:
            logger.error(f"Error loading chain: {e}")
            QMessageBox.critical(
                self.parent_widget, "Error", f"Failed to load chain: {str(e)}"
            )

        return False

    def save_chain(self, file_path=None):
        """Save the current chain configuration to file."""
        logger.info(
            f"Saving chain configuration to file: {file_path or 'user selection'}"
        )
        try:
            # Update current state
            logger.debug("Updating current state before saving")
            self.save_current_state()

            if not file_path:
                logger.debug("No file path provided, opening save dialog")
                file_path, _ = QFileDialog.getSaveFileName(
                    self.parent_widget,
                    "Save Chain",
                    self.parent_widget.chains_folder,
                    "JSON files (*.json)",
                )
                logger.debug(f"User selected save path: {file_path}")

            if file_path:
                # Record the shared web browser scope in the file for
                # compatibility (older builds may read it): every chain uses
                # the same durable profile, so the field always carries the
                # shared key and is never used to split browsers per chain.
                self.chain_config["_web_chain_scope"] = str(self._chain_scope_key)
                logger.debug(f"Saving configuration to: {file_path}")
                # Same codec as the load path, so a chain round-trips exactly;
                # json.dump keeps non-ASCII escaped (pure-ASCII file).
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(self.chain_config, f, indent=2)

                # The chain now has a real identity: remember it.
                self._current_chain_file = file_path
                logger.debug("Chain web scope kept on save: %s", self._chain_scope_key)

                # Clear entire cache to ensure all chains are updated immediately
                logger.debug("Clearing entire cache to ensure all chains are updated")
                clear_cache()

                logger.info(f"Successfully saved chain to: {file_path}")
                return True
            else:
                logger.warning("No file path provided for saving")

        except Exception as e:
            logger.error(f"Error saving chain: {e}")
            QMessageBox.critical(
                self.parent_widget, "Error", f"Failed to save chain: {str(e)}"
            )

        return False

    def build_graph_from_config(self):
        """Build the node graph from the current configuration."""
        logger.info("Building node graph from configuration")
        try:
            # Ensure the graph and view are properly initialized before building
            if (
                not hasattr(self.parent_widget, "node_graph")
                or self.parent_widget.node_graph is None
            ):
                logger.error(
                    "Node graph not initialized, cannot build graph from config"
                )
                raise RuntimeError("Node graph not initialized")

            if (
                not hasattr(self.parent_widget, "view")
                or self.parent_widget.view is None
            ):
                logger.error(
                    "Graph view not initialized, cannot build graph from config"
                )
                raise RuntimeError("Graph view not initialized")

            # Clear existing graph
            logger.debug("Clearing existing graph")
            self.parent_widget.graph_manager.clear_graph()

            # Clear tracking lists to prevent duplicate nodes
            if hasattr(self.parent_widget, "sequence_nodes"):
                self.parent_widget.sequence_nodes.clear()
                logger.debug("Cleared sequence_nodes tracking list")
            if hasattr(self.parent_widget, "conditional_nodes"):
                self.parent_widget.conditional_nodes.clear()
                logger.debug("Cleared conditional_nodes tracking list")
            if hasattr(self.parent_widget, "llm_nodes"):
                self.parent_widget.llm_nodes.clear()
                logger.debug("Cleared llm_nodes tracking list")
            if hasattr(self.parent_widget, "chain_import_nodes"):
                self.parent_widget.chain_import_nodes.clear()
                logger.debug("Cleared chain_import_nodes tracking list")
            if hasattr(self.parent_widget, "code_nodes"):
                self.parent_widget.code_nodes.clear()
                logger.debug("Cleared code_nodes tracking list")
            if hasattr(self.parent_widget, "container_nodes"):
                self.parent_widget.container_nodes.clear()
                logger.debug("Cleared container_nodes tracking list")
            if hasattr(self.parent_widget, "context_nodes"):
                self.parent_widget.context_nodes.clear()
                logger.debug("Cleared context_nodes tracking list")
            if hasattr(self.parent_widget, "input_nodes"):
                self.parent_widget.input_nodes.clear()
                logger.debug("Cleared input_nodes tracking list")
            if hasattr(self.parent_widget, "handle_nodes"):
                self.parent_widget.handle_nodes.clear()
                logger.debug("Cleared handle_nodes tracking list")
            if hasattr(self.parent_widget, "mcp_nodes"):
                self.parent_widget.mcp_nodes.clear()
                logger.debug("Cleared mcp_nodes tracking list")

            # Store node references for connections
            node_map = {}
            logger.debug("Created node map for connection restoration")

            # Create sequence nodes
            logger.debug("Creating sequence nodes")
            self._create_sequence_nodes(node_map)

            # Create web sequence nodes
            logger.debug("Creating web sequence nodes")
            self._create_web_sequence_nodes(node_map)

            # Create conditional nodes
            logger.debug("Creating conditional nodes")
            self._create_conditional_nodes(node_map)

            # Create LLM nodes
            logger.debug("Creating LLM nodes")
            self._create_llm_nodes(node_map)

            # Create chain import nodes
            logger.debug("Creating chain import nodes")
            self._create_chain_import_nodes(node_map)

            # Create form filler nodes
            logger.debug("Creating form filler nodes")
            self._create_form_filler_nodes(node_map)

            # Create Code nodes
            logger.debug("Creating Code nodes")
            self._create_code_nodes(node_map)

            # Create Container nodes
            logger.debug("Creating Container nodes")
            self._create_container_nodes(node_map)

            # Create Context nodes
            logger.debug("Creating Context nodes")
            self._create_context_nodes(node_map)

            # Create Input nodes
            logger.debug("Creating Input nodes")
            self._create_input_nodes(node_map)

            # Create Handle nodes
            logger.debug("Creating Handle nodes")
            self._create_handle_nodes(node_map)

            # Create MCP nodes
            logger.debug("Creating MCP nodes")
            self._create_mcp_nodes(node_map)

            # Create Output nodes
            logger.debug("Creating Output nodes")
            self._create_output_nodes(node_map)

            # Restore connections
            logger.debug("Restoring node connections")
            self._restore_connections(node_map)

            # Refresh current view
            if hasattr(self.parent_widget, "view_manager"):
                logger.debug("Refreshing current view")
                self.parent_widget.view_manager.refresh_current_view()
            else:
                logger.warning("No view_manager found on parent widget")

            logger.info(f"Successfully built graph with {len(node_map)} nodes")

        except Exception as e:
            logger.error(f"Error building graph from config: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to build graph from config: {str(e)}",
            )

    def remove_sequence_from_chain(self, sequence_name):
        """Remove a sequence from the chain configuration."""
        logger.info(f"Removing sequence from chain: {sequence_name}")
        try:
            # Ensure sequences key exists
            if "sequences" not in self.chain_config:
                self.chain_config["sequences"] = []
                logger.debug("Initialized missing 'sequences' key in chain_config")

            original_count = len(self.chain_config["sequences"])
            self.chain_config["sequences"] = [
                seq
                for seq in self.chain_config["sequences"]
                if seq.get("name") != sequence_name
            ]
            new_count = len(self.chain_config["sequences"])
            removed_count = original_count - new_count
            logger.debug(
                f"Removed {removed_count} sequence(s) named '{sequence_name}' from chain config"
            )
        except Exception as e:
            logger.error(f"Error removing sequence from chain: {e}")
            raise

    def remove_action_from_sequence(self, action_name):
        """Remove an action from all sequences."""
        logger.info(f"Removing action from all sequences: {action_name}")
        try:
            total_removed = 0
            for sequence in self.chain_config["sequences"]:
                if "actions" in sequence:
                    original_count = len(sequence["actions"])
                    sequence["actions"] = [
                        action
                        for action in sequence["actions"]
                        if action != action_name
                    ]
                    removed_count = original_count - len(sequence["actions"])
                    total_removed += removed_count
                    if removed_count > 0:
                        logger.debug(
                            f"Removed {removed_count} instance(s) of action '{action_name}' from sequence '{sequence.get('name', 'unknown')}'"
                        )

            logger.debug(
                f"Total removed {total_removed} instance(s) of action '{action_name}' from all sequences"
            )
        except Exception as e:
            logger.error(f"Error removing action from sequences: {e}")
            raise

    def _save_sequence_node(self, node):
        """Save a sequence node to configuration."""
        logger.debug(f"Saving sequence node {node.id} to configuration")
        try:
            # CRITICAL: Always use the sequence_file property which contains the original file path
            # The node name may have GUI numbering (e.g., "test.json 2") but sequence_file should be clean
            sequence_file = node.get_property("sequence_file")

            if not sequence_file:
                # Fallback: extract base filename from node name if sequence_file is missing
                node_name = node.name()
                import re

                if re.match(r"^(.+\.json)\s+\d+$", node_name):
                    # Remove GUI numbering from node name
                    sequence_file = re.match(r"^(.+\.json)\s+\d+$", node_name).group(1)
                    logger.warning(
                        f"sequence_file property missing! Extracted '{sequence_file}' from numbered node name '{node_name}'"
                    )
                else:
                    sequence_file = node_name
                    logger.warning(
                        f"sequence_file property missing! Using node name '{sequence_file}' as fallback"
                    )
            else:
                logger.debug(
                    f"Using sequence_file property: '{sequence_file}' for node '{node.name()}'"
                )

            # Ensure we only store the filename, not the full path for consistency
            import os

            if os.path.sep in sequence_file or "/" in sequence_file:
                sequence_file = os.path.basename(sequence_file)
                logger.debug(f"Extracted filename '{sequence_file}' from full path")

            sequence_data = {
                "name": node.get_property("sequence_name") or node.name(),
                "sequence_file": sequence_file,  # Use cleaned sequence file name
                "loop_count": int(node.get_property("loop_count") or 1),
                "extra_delay": float(node.get_property("extra_delay") or 1.0),
                "use_app_opened": (
                    str(node.get_property("use_app_opened") or "true").strip().lower()
                    in ("true", "1", "yes", "y", "on")
                ),
                "click_drift_min": float(node.get_property("click_drift_min") or 5.0),
                "click_drift_max": float(node.get_property("click_drift_max") or 10.0),
                "actions": node.get_property("actions") or [],
                "node_id": node.id,
                "position": node.pos(),  # pos() returns [x, y] list directly
                "connections": self._get_node_connections(node),
                # Human-readable: what this sequence does (orchestrator routing).
                "description": str(node.get_property("description") or ""),
            }

            self.chain_config["sequences"].append(sequence_data)
            logger.debug(
                f"Successfully saved sequence node: {sequence_data['name']} with sequence_file: {sequence_data['sequence_file']}"
            )
        except Exception as e:
            logger.error(f"Error saving sequence node {node.id}: {e}")
            raise

    def _save_web_sequence_node(self, node):
        """Save a web sequence node to configuration."""
        logger.debug(f"Saving web sequence node {node.id} to configuration")
        try:
            # Always use the session_file property which contains the original file path
            session_file = node.get_property("session_file")

            if not session_file:
                # Fallback: extract base filename from node name
                session_file = node.name()
                logger.warning(
                    f"session_file property missing! Using node name '{session_file}' as fallback"
                )

            # Ensure we only store the filename, not the full path for consistency
            if os.path.sep in session_file or "/" in session_file:
                session_file = os.path.basename(session_file)
                logger.debug(f"Extracted filename '{session_file}' from full path")

            web_sequence_data = {
                # Persist the node's DISPLAY label (a custom name chosen at
                # re-record time).  Chains saved before the rename feature
                # stored the session filename here, which is the same value.
                "name": node.name() or node.get_property("session_file") or "Web Sequence",
                "session_file": session_file,  # Use cleaned session file name
                "headless": (
                    str(node.get_property("headless") or "false").strip().lower()
                    in ("true", "1", "yes", "y", "on")
                ),
                "speed": float(node.get_property("speed") or 1.0),
                "native_actions": (
                    str(node.get_property("native_actions") or "true").strip().lower()
                    in ("true", "1", "yes", "y", "on")
                ),
                "loop_count": int(node.get_property("loop_count") or 1),
                "extra_delay": float(node.get_property("extra_delay") or 1.0),
                "repeat_mode": str(node.get_property("repeat_mode") or "count"),
                "extract_items": self._parse_web_extract_items(node.get_property("extract_items")),
                "node_id": node.id,
                "position": node.pos(),  # pos() returns [x, y] list directly
                "connections": self._get_node_connections(node),
                # Human-readable: what this web session does (orchestrator routing).
                "description": str(node.get_property("description") or ""),
            }

            self.chain_config["web_sequences"].append(web_sequence_data)
            logger.debug(
                f"Successfully saved web sequence node: {web_sequence_data['name']} "
                f"with session_file: {web_sequence_data['session_file']}"
            )
        except Exception as e:
            logger.error(f"Error saving web sequence node {node.id}: {e}")
            raise

    def _parse_web_extract_items(self, value):
        """Parse the extract_items property (JSON list string) into a list."""
        try:
            parsed = json.loads(value or '[]')
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []

    def _safe_get_int(self, node, prop_name, default):
        """Safely get an integer property from a node."""
        try:
            val = node.get_property(prop_name)
            if val is None:
                return default
            if isinstance(val, str):
                if val.strip() == "":
                    return default
                return int(float(val))
            return int(val)
        except Exception:
            return default

    def _safe_get_float(self, node, prop_name, default):
        """Safely get a float property from a node."""
        try:
            val = node.get_property(prop_name)
            if val is None:
                return default
            if isinstance(val, str):
                if val.strip() == "":
                    return default
                return float(val)
            return float(val)
        except Exception:
            return default

    def _save_conditional_node(self, node):
        """Save a conditional node to configuration."""
        logger.debug(f"Saving conditional node {node.id} to configuration")
        try:
            # Get image path and embed as base64 if it's a valid file
            image_path = node.get_property("image_path") or ""
            image_data = node.get_property("image_data") or ""
            if not image_data and image_path and os.path.exists(image_path):
                try:
                    from ...player.image_utils import read_image_to_base64
                    b64 = read_image_to_base64(image_path)
                    if b64:
                        image_data = b64
                        logger.debug(f"Embedded conditional image {os.path.basename(image_path)} as base64")
                except Exception as e:
                    logger.debug(f"Failed to embed conditional image: {e}")

            conditional_data = {
                "condition_type": node.get_property("condition_type") or "presence",
                "image_path": image_path,
                "image_data": image_data,
                "threshold": self._safe_get_float(node, "threshold", 0.8),
                "wait_time": self._safe_get_float(node, "wait_time", 5),
                "max_loops": self._safe_get_int(node, "max_loops", 10),
                "ocr_text": node.get_property("ocr_text") or "",
                "region": node.get_property("region") or "",
                "code": node.get_property("code") or "",
                # Timeout and retry properties
                "timeout": self._safe_get_float(
                    node, "timeout", 0
                ),  # 0 = disabled for layout match
                "max_attempts": self._safe_get_int(node, "max_attempts", 500),
                # Note: delay_between_attempts kept for backward compat but not used - layout match runs at max speed
                # "delay_between_attempts": self._safe_get_float(node, 'delay_between_attempts', 0.01),
                # Layout match-specific properties
                "scroll_direction": self._safe_get_int(
                    node, "scroll_direction", 1
                ),  # 1 = up, -1 = down
                # Loop-specific properties
                "loop_type": node.get_property("loop_type") or "",
                "loop_position": node.get_property("loop_position") or "",
                "sequence_file": node.get_property("sequence_file") or "",
                "iteration_delay": self._safe_get_float(node, "iteration_delay", 1.0),
                "loop_condition": node.get_property("loop_condition") or "",
                # OCR-specific properties
                "case_sensitive": node.get_property("case_sensitive") or "true",
                "node_id": node.id,
                "position": node.pos(),  # pos() returns [x, y] list directly
                "connections": self._get_node_connections(node),
                # Human-readable: what this check does (orchestrator routing).
                "description": str(node.get_property("description") or ""),
            }

            # LLM conditional properties — must be serialized so runtime can evaluate
            if conditional_data.get('condition_type') == 'llm':
                conditional_data['llm_engine'] = node.get_property('llm_engine') or 'ollama'
                conditional_data['llm_model'] = node.get_property('llm_model') or ''
                conditional_data['llm_prompt'] = node.get_property('llm_prompt') or ''
                conditional_data['llm_timeout'] = self._safe_get_float(node, 'llm_timeout', 10.0)
                conditional_data['llm_use_vision'] = str(node.get_property('llm_use_vision') or 'false').strip().lower() in ('true', '1', 'yes')

            # Web-mode conditional fields — serialized so the runtime can
            # evaluate a live-browser condition (player/.../web_conditions.py).
            if str(node.get_property('web_mode') or 'false').strip().lower() in ('true', '1', 'yes'):
                conditional_data['web_mode'] = True
                conditional_data['web_condition_type'] = node.get_property('web_condition_type') or 'element_located'
                conditional_data['web_element_locator'] = node.get_property('web_element_locator') or ''
                conditional_data['web_text_source'] = node.get_property('web_text_source') or 'page'
                conditional_data['web_text_locator'] = node.get_property('web_text_locator') or ''
                conditional_data['web_target_text'] = node.get_property('web_target_text') or ''
                conditional_data['web_case_sensitive'] = str(node.get_property('web_case_sensitive') or 'false').strip().lower() in ('true', '1', 'yes')
                conditional_data['web_js'] = node.get_property('web_js') or ''
                conditional_data['web_llm_engine'] = node.get_property('web_llm_engine') or 'ollama'
                conditional_data['web_llm_model'] = node.get_property('web_llm_model') or ''
                conditional_data['web_llm_prompt'] = node.get_property('web_llm_prompt') or ''
                conditional_data['web_llm_timeout'] = self._safe_get_float(node, 'web_llm_timeout', 10.0)
                conditional_data['web_layout_locator'] = node.get_property('web_layout_locator') or ''
                for _k in ('web_layout_x', 'web_layout_y', 'web_layout_w', 'web_layout_h'):
                    if str(node.get_property(_k) or '').strip() != '':
                        conditional_data[_k] = self._safe_get_int(node, _k, 0)
                conditional_data['web_layout_direction'] = self._safe_get_int(node, 'web_layout_direction', -1)
                conditional_data['web_layout_attempts'] = self._safe_get_int(node, 'web_layout_attempts', 20)
                conditional_data['web_layout_tolerance'] = self._safe_get_int(node, 'web_layout_tolerance', 12)
                conditional_data['web_timeout'] = self._safe_get_float(node, 'web_timeout', 10.0)

            tx = node.get_property("target_x")
            ty = node.get_property("target_y")
            tw = node.get_property("target_w")
            th = node.get_property("target_h")
            if (
                str(tx or "").strip() != ""
                and str(ty or "").strip() != ""
                and str(tw or "").strip() != ""
                and str(th or "").strip() != ""
            ):
                conditional_data["target_x"] = self._safe_get_int(node, "target_x", 0)
                conditional_data["target_y"] = self._safe_get_int(node, "target_y", 0)
                conditional_data["target_w"] = self._safe_get_int(node, "target_w", 0)
                conditional_data["target_h"] = self._safe_get_int(node, "target_h", 0)
                conditional_data["position_tolerance"] = self._safe_get_int(
                    node, "position_tolerance", 6
                )

            self.chain_config["conditional_nodes"].append(conditional_data)
            logger.debug(
                f"Successfully saved conditional node with condition type: {conditional_data['condition_type']}"
            )
        except Exception as e:
            logger.error(f"Error saving conditional node {node.id}: {e}")
            raise

    def _save_llm_node(self, node):
        """Save an LLM node to configuration."""
        logger.debug(f"Saving LLM node {node.id} to configuration")
        try:
            llm_config = (
                node.get_llm_config() if hasattr(node, "get_llm_config") else {}
            )
            llm_data = {
                "prompt": llm_config.get("prompt", ""),
                "model": llm_config.get("model", ""),
                "system_message": llm_config.get("system_message", ""),
                "temperature": llm_config.get("temperature", 0.7),
                "max_tokens": llm_config.get("max_tokens", 4096),
                "output_variable": llm_config.get("output_variable", "llm_output"),
                "api_url": llm_config.get("api_url", ""),
                "write_text": llm_config.get("write_text", True),
                "use_vision": llm_config.get("use_vision", False),
                "vision_model": llm_config.get("vision_model", "minicpm-v:latest"),
                "screenshot_enabled": llm_config.get("screenshot_enabled", True),
                "typing_batch_size": llm_config.get("typing_batch_size", 20),
                "typing_batch_delay": llm_config.get("typing_batch_delay", 0.05),
                "input_source": llm_config.get("input_source", "none"),
                "orchestrator_mode": bool(llm_config.get("orchestrator_mode", False)),
                "orch_max_steps": llm_config.get("orch_max_steps", 15),
                "orch_goal": llm_config.get("orch_goal", ""),
                "orch_synthesize": llm_config.get("orch_synthesize", True),
                "orch_synthesis_system": llm_config.get("orch_synthesis_system", ""),
                "orch_use_goal_ledger": llm_config.get("orch_use_goal_ledger", False),
                "orch_headless": llm_config.get("orch_headless", False),
                # Web mode (see LLMNode.llm_config).
                "web_mode": llm_config.get("web_mode", False),
                "ocr_region": llm_config.get("ocr_region", ""),
                "web_ocr_locator": llm_config.get("web_ocr_locator", ""),
                "ocr_confidence": llm_config.get("ocr_confidence", 0.5),
                "ocr_preprocessing": llm_config.get("ocr_preprocessing", True),
                "use_async": llm_config.get("use_async", True),
                "use_direct_rag": llm_config.get("use_direct_rag", False),
                "rag_embedding_model": llm_config.get("rag_embedding_model", ""),
                "rag_chunk_size": llm_config.get("rag_chunk_size", 500),
                "rag_overlap": llm_config.get("rag_overlap", 100),
                "rag_top_k": llm_config.get("rag_top_k", 3),
                "rag_include_raw_input": llm_config.get("rag_include_raw_input", False),
                "rag_max_chars": llm_config.get("rag_max_chars", 1500),
                "rag_documents": llm_config.get("rag_documents", []),
                "skills": llm_config.get("skills", []),
                "use_skill_routing": llm_config.get("use_skill_routing", True),
                "semantic_description": llm_config.get("semantic_description", ""),
                # Context consolidation (opt-in — disabled by default)
                "use_context_consolidation": llm_config.get(
                    "use_context_consolidation", False
                ),
                "consolidation_chunk_size": llm_config.get(
                    "consolidation_chunk_size", 1000
                ),
                "consolidation_overlap": llm_config.get(
                    "consolidation_overlap", 200
                ),
                "consolidation_top_k": llm_config.get("consolidation_top_k", 5),
                "consolidation_max_tokens": llm_config.get(
                    "consolidation_max_tokens", 64
                ),
                # Llama.cpp config
                "use_llamacpp": llm_config.get("use_llamacpp", False),
                "llamacpp_model_path": llm_config.get("llamacpp_model_path", ""),
                "llamacpp_gpu_layers": llm_config.get("llamacpp_gpu_layers", 0),
                "llamacpp_threads": llm_config.get("llamacpp_threads", -1),
                "llamacpp_context_size": llm_config.get("llamacpp_context_size", 0),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }
            self.chain_config["llm_nodes"].append(llm_data)
            logger.debug(
                f"Successfully saved LLM node with model: {llm_data['model']} and all properties"
            )
        except Exception as e:
            logger.error(f"Error saving LLM node {node.id}: {e}")
            raise

    def _save_chain_import_node(self, node):
        """Save a chain import node to configuration."""
        logger.debug(f"Saving chain import node {node.id} to configuration")
        try:
            import_config = node.get_chain_import_config()
            imported_chain_config = node.get_imported_chain_config()

            chain_import_data = {
                "type": "chain_import",
                "chain_file_path": import_config.get("chain_file", ""),
                "import_mode": import_config.get("import_mode", "full"),
                # 'brain' | 'chain' — re-derived from the file on load too
                # (set_chain_import_data calls classify_chain).
                "import_kind": import_config.get("import_kind", "chain"),
                "prefix": import_config.get("prefix", ""),
                "loop_count": import_config.get("loop_count", 1),
                "extra_delay": import_config.get("extra_delay", 0),
                "enabled": import_config.get("enabled", True),
                "run_in_sandbox": import_config.get("run_in_sandbox", False),
                "show_sandbox_window": import_config.get("show_sandbox_window", True),
                "emit_data": import_config.get("emit_data", False),
                "data_output_nodes": import_config.get("data_output_nodes", []),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            # CRITICAL: If we have an imported chain configuration (from ChainExpansionDialog editing),
            # save the actual chain configuration instead of just the import configuration
            if imported_chain_config and (
                imported_chain_config.get("sequences")
                or imported_chain_config.get("conditional_nodes")
                or imported_chain_config.get("llm_nodes")
                or imported_chain_config.get("tts_nodes")
            ):
                # This is an edited chain from ChainExpansionDialog - save the full configuration
                logger.debug(
                    f"Saving full imported chain configuration for node {node.id}"
                )
                chain_import_data.update(
                    {
                        "sequences": imported_chain_config.get("sequences", []),
                        "conditional_nodes": imported_chain_config.get(
                            "conditional_nodes", []
                        ),
                        "llm_nodes": imported_chain_config.get("llm_nodes", []),
                        "tts_nodes": imported_chain_config.get("tts_nodes", []),
                        "chain_import_nodes": imported_chain_config.get(
                            "chain_import_nodes", []
                        ),
                        # output_nodes must round-trip too: the loader
                        # (_create_chain_import_nodes) restores it, so dropping
                        # it here deleted every embedded output node on save.
                        "output_nodes": imported_chain_config.get(
                            "output_nodes", []
                        ),
                    }
                )

            self.chain_config["chain_import_nodes"].append(chain_import_data)
            logger.debug(
                f"Successfully saved chain import node with file: {chain_import_data['chain_file_path']}"
            )
        except Exception as e:
            logger.error(f"Error saving chain import node {node.id}: {e}")
            raise

    def _save_form_filler_node(self, node):
        """Save a Form Filling node to configuration."""
        logger.debug(f"Saving Form Filling node {node.id} to configuration")
        try:
            config = node.get_form_filler_config()

            form_filler_data = {
                "type": "form_filler",
                "mode": config.get("mode", "web"),
                "instruction": config.get("instruction", ""),
                "fields_include": config.get("fields_include", ""),
                "fields_skip": config.get("fields_skip", ""),
                "probe_top_k": config.get("probe_top_k", 3),
                "probe_char_budget": config.get("probe_char_budget", 1500),
                "probe_context_chars": config.get("probe_context_chars", 6000),
                "consolidate": bool(config.get("consolidate", True)),
                "probe_cycles": config.get("probe_cycles", 3),
                "verify": bool(config.get("verify", True)),
                "repair": bool(config.get("repair", True)),
                "repair_attempts": config.get("repair_attempts", 2),
                "answer_no": bool(config.get("answer_no", True)),
                "answer_na": bool(config.get("answer_na", True)),
                "ask_user": bool(config.get("ask_user", False)),
                "max_fields": config.get("max_fields", 40),
                "engine": config.get("engine", "llamacpp"),
                "model": config.get("model", ""),
                "temperature": config.get("temperature", 0.1),
                "max_tokens": config.get("max_tokens", 1024),
                "context_size": config.get("context_size", 0),
                "typing_batch_size": config.get("typing_batch_size", 20),
                "typing_batch_delay": config.get("typing_batch_delay", 0.05),
                "rag_documents": config.get("rag_documents", []),
                "web_scope": config.get("web_scope", ""),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            self.chain_config["form_filler_nodes"].append(form_filler_data)
            logger.debug(
                f"Successfully saved Form Filling node (mode={form_filler_data['mode']})"
            )
        except Exception as e:
            logger.error(f"Error saving Form Filling node {node.id}: {e}")
            raise

    def _save_code_node(self, node):
        """Save a Code node to configuration."""
        logger.debug(f"Saving Code node {node.id} to configuration")
        try:
            config = node.get_code_config()
            code_data = {
                "type": "code",
                "code": config.get("code", ""),
                "file_path": config.get("file_path", ""),
                "execute_on_input": config.get("execute_on_input", True),
                "output_variable": config.get("output_variable", "result"),
                "timeout": config.get("timeout", 30),
                "description": config.get("description", ""),
                "input_vars": config.get("input_vars", []),
                "output_vars": config.get("output_vars", []),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            if "code_nodes" not in self.chain_config:
                self.chain_config["code_nodes"] = []

            self.chain_config["code_nodes"].append(code_data)
            logger.debug(f"Successfully saved Code node: {node.id}")
        except Exception as e:
            logger.error(f"Error saving Code node {node.id}: {e}")
            raise

    def _save_container_node(self, node):
        """Save a Container node to configuration."""
        logger.debug(f"Saving Container node {node.id} to configuration")
        try:
            config = node.get_container_config()
            container_data = {
                "type": "container",
                "iso_path": config.get("iso_path", ""),
                "memory_mb": config.get("memory_mb", 1024),
                "cpu_cores": config.get("cpu_cores", 1),
                "hide_window": config.get("hide_window", True),
                "execute_on_input": config.get("execute_on_input", True),
                "output_variable": config.get("output_variable", "container_result"),
                "timeout": config.get("timeout", 300),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            if "container_nodes" not in self.chain_config:
                self.chain_config["container_nodes"] = []

            self.chain_config["container_nodes"].append(container_data)
            logger.debug(f"Successfully saved Container node: {node.id}")
        except Exception as e:
            logger.error(f"Error saving Container node {node.id}: {e}")
            raise

    def _save_context_node(self, node):
        """Save a Context node to configuration."""
        logger.debug(f"Saving Context node {node.id} to configuration")
        try:
            config = node.get_context_config()
            context_data = {
                "type": "context",
                "label": config.get("label", ""),
                "max_history": config.get("max_history", 10),
                "persistent": config.get("persistent", True),
                "clear_on_finish": config.get("clear_on_finish", False),
                "scope": config.get("scope", "local"),
                "shared_context_chain_file": config.get("shared_context_chain_file", ""),
                "shared_context_node_id": config.get("shared_context_node_id", ""),
                "node_id": node.get_property('_config_node_id') or node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            if "context_nodes" not in self.chain_config:
                self.chain_config["context_nodes"] = []

            self.chain_config["context_nodes"].append(context_data)
            logger.debug(f"Successfully saved Context node: {node.id}")
        except Exception as e:
            logger.error(f"Error saving Context node {node.id}: {e}")
            raise

    def _save_input_node(self, node):
        """Save an Input node to configuration."""
        logger.debug(f"Saving Input node {node.id} to configuration")
        try:
            config = node.get_input_config()
            input_data = {
                "type": "input",
                "label": config.get("label", ""),
                "default_value": config.get("default_value", ""),
                "user_prompt": config.get("user_prompt", ""),
                "passthrough": bool(config.get("passthrough", False)),
                "web_mode": bool(config.get("web_mode", False)),
                "agent_modifiable": bool(config.get("agent_modifiable", False)),
                "decision_mode": bool(config.get("decision_mode", False)),
                "decision_criterion": config.get("decision_criterion", ""),
                "decision_evaluator": config.get("decision_evaluator", "llm"),
                "decision_model": config.get("decision_model", ""),
                "decision_default": bool(config.get("decision_default", False)),
                "question_mode": config.get("question_mode", "text"),
                "choices": config.get("choices", "[]"),
                "route_on_answer": bool(config.get("route_on_answer", False)),
                "accept_text": bool(config.get("accept_text", True)),
                "accept_images": bool(config.get("accept_images", False)),
                "accept_documents": bool(config.get("accept_documents", False)),
                "tts_enabled": bool(config.get("tts_enabled", False)),
                "tts_text": config.get("tts_text", ""),
                "tts_language": config.get("tts_language", "en"),
                "tts_voice_model": config.get("tts_voice_model", ""),
                "tts_speed": config.get("tts_speed", 1.0),
                "tts_speaker_id": config.get("tts_speaker_id"),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            if "input_nodes" not in self.chain_config:
                self.chain_config["input_nodes"] = []

            self.chain_config["input_nodes"].append(input_data)
            logger.debug(f"Successfully saved Input node: {node.id}")
        except Exception as e:
            logger.error(f"Error saving Input node {node.id}: {e}")
            raise

    def _migrate_orchestrator_nodes(self, cfg):
        """Fold legacy Orchestrator nodes into LLM nodes in 'orchestrator' mode.

        The Orchestrator is no longer a node type — it is an LLM node switch
        (like the Input node's passthrough).  Old chains keep working: each
        ``orchestrator_nodes`` entry becomes an ``llm_nodes`` entry with
        ``orchestrator_mode=true``, and any edge that landed on its
        ``brains``/``chains`` port is re-pointed at the LLM node's ``tools``
        port.  Runs on the freshly-loaded dict, before the graph is built.
        """
        try:
            orch = cfg.get("orchestrator_nodes") or []
            if orch:
                ids = {str(n.get("node_id") or n.get("id"))
                       for n in orch if isinstance(n, dict)}
                llm_nodes = cfg.setdefault("llm_nodes", [])
                for n in orch:
                    if not isinstance(n, dict):
                        continue
                    llm = {
                        "type": "llm",
                        "node_id": n.get("node_id") or n.get("id"),
                        "description": n.get("description") or "",
                        "model": n.get("model") or "",
                        "orchestrator_mode": True,
                        "orch_max_steps": n.get("max_steps", 15),
                        "position": n.get("position"),
                        "connections": n.get("connections") or [],
                    }
                    llm_nodes.append({k: v for k, v in llm.items()
                                      if v not in (None, "")})
                for key in ("sequences", "conditional_nodes", "llm_nodes",
                            "chain_import_nodes", "code_nodes",
                            "context_nodes", "input_nodes", "handle_nodes",
                            "mcp_nodes", "output_nodes"):
                    for entry in (cfg.get(key) or []):
                        if not isinstance(entry, dict):
                            continue
                        for conn in (entry.get("connections") or []):
                            if not isinstance(conn, dict):
                                continue
                            tgt = str(conn.get("target_node_id")
                                      or conn.get("node_id") or "")
                            if (tgt in ids
                                    and str(conn.get("input_port") or "") in ("brains", "chains")):
                                conn["input_port"] = "tools"
                logger.info(
                    "Migrated %d Orchestrator node(s) to LLM 'orchestrator' mode",
                    len(ids),
                )
            cfg.pop("orchestrator_nodes", None)
        except Exception as e:
            logger.warning("Orchestrator->LLM migration failed: %s", e)

    def _save_handle_node(self, node):
        """Save a Handle node to configuration."""
        logger.debug(f"Saving Handle node {node.id} to configuration")
        try:
            config = node.get_handle_config()
            handle_data = {
                "type": "handle",
                "action_type": config.get("action_type", "click"),
                "goal_description": config.get("goal_description", ""),
                "target_description": config.get("target_description", ""),
                "agent_adaptive": bool(config.get("agent_adaptive", False)),
                "web_mode": bool(config.get("web_mode", False)),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            if "handle_nodes" not in self.chain_config:
                self.chain_config["handle_nodes"] = []

            self.chain_config["handle_nodes"].append(handle_data)
            logger.debug(f"Successfully saved Handle node: {node.id}")
        except Exception as e:
            logger.error(f"Error saving Handle node {node.id}: {e}")
            raise

    def _save_mcp_node(self, node):
        """Save an MCP node to configuration."""
        logger.debug(f"Saving MCP node {node.id} to configuration")
        try:
            config = node.get_mcp_config()
            mcp_data = {
                "type": "mcp",
                "mcp_folder": config.get("mcp_folder", ""),
                "tool_name": config.get("tool_name", ""),
                "tool_args": config.get("tool_args", "{}"),
                "mcp_tools": config.get("mcp_tools", ""),
                "keep_alive": config.get("keep_alive", False),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            if "mcp_nodes" not in self.chain_config:
                self.chain_config["mcp_nodes"] = []

            self.chain_config["mcp_nodes"].append(mcp_data)
            logger.debug(f"Successfully saved MCP node: {node.id}")
        except Exception as e:
            logger.error(f"Error saving MCP node {node.id}: {e}")
            raise

    def _save_output_node(self, node):
        """Save an Output node to configuration."""
        logger.debug(f"Saving Output node {node.id} to configuration")
        try:
            config = node.get_output_config()
            output_data = {
                "type": "output",
                "label": config.get("label", ""),
                "variable_name": config.get("variable_name", ""),
                "agent_visible": bool(config.get("agent_visible", True)),
                "overlay_visible": bool(config.get("overlay_visible", True)),
                "popup_on_finish": bool(config.get("popup_on_finish", True)),
                "show_rating": bool(config.get("show_rating", True)),
                "render_mode": config.get("render_mode", "text"),
                "tts_enabled": bool(config.get("tts_enabled", False)),
                "tts_text": config.get("tts_text", ""),
                "tts_language": config.get("tts_language", "en"),
                "tts_voice_model": config.get("tts_voice_model", ""),
                "tts_speed": config.get("tts_speed", 1.0),
                "tts_speaker_id": config.get("tts_speaker_id"),
                "tts_wait": bool(config.get("tts_wait", False)),
                "image_source": config.get("image_source", ""),
                "node_id": node.get_property('_config_node_id') or node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node),
            }

            if "output_nodes" not in self.chain_config:
                self.chain_config["output_nodes"] = []

            self.chain_config["output_nodes"].append(output_data)
            logger.debug(f"Successfully saved Output node: {node.id}")
        except Exception as e:
            logger.error(f"Error saving Output node {node.id}: {e}")
            raise

    def _create_output_nodes(self, node_map):
        """Create Output nodes from configuration."""
        logger.debug("Creating Output nodes from configuration")
        try:
            output_nodes = self.chain_config.get("output_nodes", [])
            logger.debug(f"Found {len(output_nodes)} Output nodes to create")

            for output_data in output_nodes:
                logger.debug(f"Creating Output node")
                node = self.parent_widget.graph_manager.create_node(
                    "output.OutputNode", name="Output"
                )

                if node:
                    # Set properties
                    node.set_property("label", output_data.get("label", ""))
                    node.set_property("variable_name", output_data.get("variable_name", "") or "")
                    node.set_property("agent_visible", bool(output_data.get("agent_visible", True)))
                    node.set_property("overlay_visible", bool(output_data.get("overlay_visible", True)))
                    node.set_property("popup_on_finish", bool(output_data.get("popup_on_finish", True)))
                    node.set_property("show_rating", bool(output_data.get("show_rating", True)))
                    node.set_property("render_mode", output_data.get("render_mode", "text") or "text")
                    node.set_property("tts_enabled", bool(output_data.get("tts_enabled", False)))
                    node.set_property("tts_text", output_data.get("tts_text", "") or "")
                    node.set_property("tts_language", output_data.get("tts_language", "en") or "en")
                    node.set_property("tts_voice_model", output_data.get("tts_voice_model", "") or "")
                    node.set_property("tts_speed", output_data.get("tts_speed", 1.0))
                    node.set_property("tts_speaker_id", output_data.get("tts_speaker_id"))
                    node.set_property("tts_wait", bool(output_data.get("tts_wait", False)))
                    node.set_property("image_source", output_data.get("image_source", "") or "")

                    # Update name if label is provided
                    label = output_data.get("label", "")
                    if label:
                        name = f"Output: {label}"
                        if output_data.get("render_mode") == "audio" or output_data.get("tts_enabled"):
                            name += " \U0001f50a"
                        node.set_name(name)

                    # Set position
                    position = output_data.get("position", [0, 0])
                    node.set_pos(*position)

                    # Add to parent widget's tracking list if it exists
                    if hasattr(self.parent_widget, "output_nodes"):
                        self.parent_widget.output_nodes.append(node)

                    # Store in node map
                    node_id = output_data.get("node_id", node.id)
                    # Preserve config node_id on the node
                    try:
                        node.set_property('_config_node_id', node_id)
                    except Exception:
                        pass
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create Output node")
        except Exception as e:
            logger.error(f"Error creating Output nodes: {e}")
            raise

    def _get_node_connections(self, node):
        """Get all output connections for a node.

        Uses the config-preserved node ID for targets that track it
        (ContextNode via ``_config_node_id``) so that saved connections
        remain resolvable across save/load cycles.  Without this, the
        runtime NodeGraphQt ID diverges from the saved ``node_id`` and
        ``_restore_connections`` silently drops the connection.
        """
        try:
            connections = []

            # Get output connections
            for output_port in node.output_ports():
                for connected_port in output_port.connected_ports():
                    target_node = connected_port.node()
                    # ContextNode preserves its original config ID across
                    # save/load via _config_node_id — use that, not the
                    # ephemeral NodeGraphQt runtime ID.
                    try:
                        target_id = (
                            target_node.get_property('_config_node_id')
                            or target_node.id
                        )
                    except Exception:
                        target_id = target_node.id

                    connection = {
                        "output_port": output_port.name(),
                        "target_node_id": target_id,
                        "input_port": connected_port.name(),
                    }
                    connections.append(connection)
                    logger.debug(
                        f"Found connection: {output_port.name()} -> {target_id}:{connected_port.name()}"
                    )

            logger.debug(f"Node {node.id} has {len(connections)} output connections")
            return connections
        except Exception as e:
            logger.error(f"Error getting connections for node {node.id}: {e}")
            raise

    def _create_sequence_nodes(self, node_map):
        """Create sequence nodes from configuration."""
        logger.debug("Creating sequence nodes from configuration")
        try:
            sequences = self.chain_config.get("sequences", [])
            logger.debug(f"Found {len(sequences)} sequence nodes to create")

            for seq_data in sequences:
                # CRITICAL: Use sequence_file property directly, not the display name
                # The sequence_file should contain the original filename without GUI numbering
                sequence_file = seq_data.get("sequence_file", "")
                node_name = seq_data.get("name", "Sequence")

                logger.debug(
                    f"Creating sequence node: {node_name} with sequence_file: {sequence_file}"
                )

                # Use the sequence filename for the node name to prevent numbering issues
                display_name = (
                    os.path.basename(sequence_file) if sequence_file else node_name
                )

                node = self.parent_widget.graph_manager.create_node(
                    "sequence.SequenceNode", name=display_name
                )

                if node:
                    # CRITICAL: Always use the sequence_file from config, not derived from name
                    # This ensures we reference the correct file regardless of GUI display
                    if not sequence_file:
                        # Fallback: extract from name if sequence_file is missing
                        if ": " in node_name:
                            sequence_file = node_name.split(": ", 1)[1]
                        else:
                            sequence_file = node_name
                        logger.warning(
                            f"sequence_file missing in config, using fallback: {sequence_file}"
                        )

                    # Ensure we store only the filename, not full path
                    if sequence_file and (
                        os.path.sep in sequence_file or "/" in sequence_file
                    ):
                        sequence_file = os.path.basename(sequence_file)

                    # Set properties that exist on SequenceNode
                    node.set_property("sequence_file", sequence_file)
                    node.set_property("loop_count", str(seq_data.get("loop_count", 1)))
                    node.set_property(
                        "extra_delay", str(seq_data.get("extra_delay", 1.0))
                    )
                    node.set_property(
                        "use_app_opened",
                        "true"
                        if bool(seq_data.get("use_app_opened", True))
                        else "false",
                    )
                    node.set_property(
                        "click_drift_min", str(seq_data.get("click_drift_min", 5.0))
                    )
                    node.set_property(
                        "click_drift_max", str(seq_data.get("click_drift_max", 10.0))
                    )
                    node.set_property(
                        "description", str(seq_data.get("description") or "")
                    )
                    logger.debug(
                        f"Set sequence_file property for sequence node: {sequence_file}"
                    )
                    logger.debug(
                        f"Set loop_count property for sequence node: {seq_data.get('loop_count', 1)}"
                    )
                    logger.debug(
                        f"Set extra_delay property for sequence node: {seq_data.get('extra_delay', 1.0)}"
                    )

                    # Set position
                    if "position" in seq_data:
                        node.set_pos(*seq_data["position"])
                        logger.debug(
                            f"Set position for sequence node: {seq_data['position']}"
                        )

                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, "sequence_nodes"):
                        self.parent_widget.sequence_nodes.append(node)
                        logger.debug(
                            f"Added sequence node to parent widget tracking list"
                        )

                    # Store in node map
                    node_id = seq_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added sequence node to node map: {node_id}")
                else:
                    logger.error(f"Failed to create sequence node: {node_name}")
        except Exception as e:
            logger.error(f"Error creating sequence nodes: {e}")
            raise

    def _create_web_sequence_nodes(self, node_map):
        """Create web sequence nodes from configuration."""
        logger.debug("Creating web sequence nodes from configuration")
        try:
            web_sequences = self.chain_config.get("web_sequences", [])
            logger.debug(f"Found {len(web_sequences)} web sequence nodes to create")

            for ws_data in web_sequences:
                # CRITICAL: Use session_file property directly, not the display name
                session_file = ws_data.get("session_file", "")
                node_name = ws_data.get("name", "Web Sequence")

                logger.debug(
                    f"Creating web sequence node: {node_name} with session_file: {session_file}"
                )

                # The saved name is the node's DISPLAY label: a custom name
                # chosen at re-record time survives chain reloads.  Chains
                # saved before the rename feature stored the session filename
                # here, identical to the basename fallback below.
                display_name = node_name or (
                    os.path.basename(session_file) if session_file else "Web Sequence"
                )

                node = self.parent_widget.graph_manager.create_node(
                    "web_sequence.WebSequenceNode", name=display_name
                )

                if node:
                    if not session_file:
                        session_file = node_name
                        logger.warning(
                            f"session_file missing in config, using fallback: {session_file}"
                        )

                    # Ensure we store only the filename, not full path
                    if session_file and (
                        os.path.sep in session_file or "/" in session_file
                    ):
                        session_file = os.path.basename(session_file)

                    # Set properties that exist on WebSequenceNode
                    node.set_property("session_file", session_file)
                    _headless = str(ws_data.get("headless", False)).strip().lower() in ("true", "1", "yes", "y", "on")
                    node.set_property("headless", "true" if _headless else "false")
                    try:
                        node.set_property("speed", str(float(ws_data.get("speed", 1.0))))
                    except (TypeError, ValueError):
                        node.set_property("speed", "1.0")
                    _native = str(ws_data.get("native_actions", True)).strip().lower() in ("true", "1", "yes", "y", "on")
                    node.set_property("native_actions", "true" if _native else "false")
                    node.set_property("loop_count", str(ws_data.get("loop_count", 1)))
                    node.set_property("extra_delay", str(ws_data.get("extra_delay", 1.0)))
                    node.set_property("repeat_mode", str(ws_data.get("repeat_mode", "count")))
                    # Restore the ctx_out extraction toggles (page_text/url/… plus
                    # the repeating-element status); without this the dialog
                    # checkboxes silently reset to none on every chain reload.
                    node.set_property(
                        "extract_items",
                        json.dumps(ws_data.get("extract_items") or []),
                    )
                    node.set_property(
                        "description", str(ws_data.get("description") or "")
                    )
                    logger.debug(
                        f"Set session_file property for web sequence node: {session_file}"
                    )

                    # Set position
                    if "position" in ws_data:
                        node.set_pos(*ws_data["position"])
                        logger.debug(
                            f"Set position for web sequence node: {ws_data['position']}"
                        )

                    # Store in node map
                    node_id = ws_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added web sequence node to node map: {node_id}")
                else:
                    logger.error(f"Failed to create web sequence node: {node_name}")
        except Exception as e:
            logger.error(f"Error creating web sequence nodes: {e}")
            raise

    def _create_conditional_nodes(self, node_map):
        """Create conditional nodes from configuration."""
        logger.debug("Creating conditional nodes from configuration")
        try:
            conditional_nodes = self.chain_config.get("conditional_nodes", [])
            logger.debug(f"Found {len(conditional_nodes)} conditional nodes to create")

            for cond_data in conditional_nodes:
                # Create node
                condition_type = cond_data.get("condition_type") or cond_data.get(
                    "trigger_type", "presence"
                )  # backward compatibility
                logger.debug(
                    f"Creating conditional node with condition type: {condition_type}"
                )
                node = self.parent_widget.graph_manager.create_node(
                    "conditional.ConditionalNode", name="Conditional"
                )

                if node:
                    # Set properties
                    node.set_property("condition_type", condition_type)
                    node.set_property("image_path", cond_data.get("image_path", ""))
                    image_data = cond_data.get("image_data", "")
                    if image_data:
                        try:
                            node.set_property("image_data", image_data, push_undo=False)
                        except Exception:
                            pass  # Non-critical - image_data is a cache
                    node.set_property("threshold", str(cond_data.get("threshold", 0.8)))
                    node.set_property("wait_time", str(cond_data.get("wait_time", 5)))
                    node.set_property("max_loops", str(cond_data.get("max_loops", 10)))
                    node.set_property("ocr_text", cond_data.get("ocr_text", ""))
                    node.set_property("region", cond_data.get("region", ""))
                    node.set_property("code", cond_data.get("code", ""))
                    # Timeout and retry properties
                    node.set_property(
                        "timeout", str(cond_data.get("timeout", 0))
                    )  # 0 = disabled for layout match
                    node.set_property(
                        "max_attempts", str(cond_data.get("max_attempts", 500))
                    )
                    # Note: delay_between_attempts kept for backward compat but not used
                    # node.set_property('delay_between_attempts', str(cond_data.get('delay_between_attempts', 0.01)))
                    # Layout match-specific properties
                    node.set_property(
                        "scroll_direction", str(cond_data.get("scroll_direction", 1))
                    )  # 1 = up, -1 = down
                    if (
                        cond_data.get("target_x") is not None
                        and cond_data.get("target_y") is not None
                        and cond_data.get("target_w") is not None
                        and cond_data.get("target_h") is not None
                    ):
                        node.set_property("target_x", str(cond_data.get("target_x")))
                        node.set_property("target_y", str(cond_data.get("target_y")))
                        node.set_property("target_w", str(cond_data.get("target_w")))
                        node.set_property("target_h", str(cond_data.get("target_h")))
                        node.set_property(
                            "position_tolerance",
                            str(cond_data.get("position_tolerance", 6)),
                        )
                    else:
                        node.set_property("target_x", "")
                        node.set_property("target_y", "")
                        node.set_property("target_w", "")
                        node.set_property("target_h", "")
                        node.set_property("position_tolerance", "6")
                    # Web-mode conditional fields
                    if str(cond_data.get("web_mode") or "").strip().lower() in ("true", "1", "yes"):
                        node.set_property("web_mode", "true")
                        node.set_property("web_condition_type", cond_data.get("web_condition_type", "element_located"))
                        node.set_property("web_element_locator", cond_data.get("web_element_locator", ""))
                        node.set_property("web_text_source", cond_data.get("web_text_source", "page"))
                        node.set_property("web_text_locator", cond_data.get("web_text_locator", ""))
                        node.set_property("web_target_text", cond_data.get("web_target_text", ""))
                        node.set_property("web_case_sensitive", str(cond_data.get("web_case_sensitive", False)).lower())
                        node.set_property("web_js", cond_data.get("web_js", ""))
                        node.set_property("web_llm_engine", cond_data.get("web_llm_engine", "ollama"))
                        node.set_property("web_llm_model", cond_data.get("web_llm_model", ""))
                        node.set_property("web_llm_prompt", cond_data.get("web_llm_prompt", ""))
                        node.set_property("web_llm_timeout", str(cond_data.get("web_llm_timeout", 10.0)))
                        node.set_property("web_layout_locator", cond_data.get("web_layout_locator", ""))
                        for _wk in ("web_layout_x", "web_layout_y", "web_layout_w", "web_layout_h"):
                            if str(cond_data.get(_wk, "") or "").strip() != "":
                                node.set_property(_wk, str(cond_data.get(_wk)))
                        node.set_property("web_layout_direction", str(cond_data.get("web_layout_direction", -1)))
                        node.set_property("web_layout_attempts", str(cond_data.get("web_layout_attempts", 20)))
                        node.set_property("web_layout_tolerance", str(cond_data.get("web_layout_tolerance", 12)))
                        node.set_property("web_timeout", str(cond_data.get("web_timeout", 10.0)))
                    # Loop-specific properties
                    node.set_property("loop_type", cond_data.get("loop_type", ""))
                    node.set_property(
                        "loop_position", cond_data.get("loop_position", "")
                    )
                    node.set_property(
                        "sequence_file", cond_data.get("sequence_file", "")
                    )
                    node.set_property(
                        "iteration_delay", str(cond_data.get("iteration_delay", 1.0))
                    )
                    node.set_property(
                        "loop_condition", cond_data.get("loop_condition", "")
                    )
                    # Morph ports for file-loop conditionals: they expose a
                    # single 'output' port (loop continuation) instead of the
                    # true/false branches used by single-pass conditionals.
                    try:
                        node.update_loop_port_mode()
                    except Exception:
                        pass
                    # OCR-specific properties (language removed - uses default English)
                    node.set_property(
                        "case_sensitive", cond_data.get("case_sensitive", "true")
                    )
                    node.set_property(
                        "description", str(cond_data.get("description") or "")
                    )
                    # LLM conditional properties
                    if condition_type == 'llm':
                        node.set_property('llm_engine', cond_data.get('llm_engine', 'ollama'))
                        node.set_property('llm_model', cond_data.get('llm_model', ''))
                        node.set_property('llm_prompt', cond_data.get('llm_prompt', ''))
                        node.set_property('llm_timeout', str(cond_data.get('llm_timeout', 10.0)))
                        node.set_property('llm_use_vision', str(cond_data.get('llm_use_vision', 'false')).lower())
                    # Set descriptive name matching the condition type (matches GUI behavior)
                    _cond_names = {
                        'code': 'Code Condition',
                        'llm': 'LLM Conditional',
                        'layout_match': 'Layout Match',
                        'presence': 'Wait for Image',
                        'absence': 'Wait for Absence',
                        'ocr': 'OCR Condition',
                        'web': 'Web Conditional',
                    }
                    _cname = _cond_names.get(condition_type)
                    if _cname:
                        node.set_name(_cname)
                    logger.debug(
                        f"Set properties for conditional node with condition: {condition_type}"
                    )

                    # Set position
                    if "position" in cond_data:
                        node.set_pos(*cond_data["position"])
                        logger.debug(
                            f"Set position for conditional node: {cond_data['position']}"
                        )

                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, "conditional_nodes"):
                        self.parent_widget.conditional_nodes.append(node)
                        logger.debug(
                            f"Added conditional node to parent widget tracking list"
                        )

                    # Store in node map
                    node_id = cond_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added conditional node to node map: {node_id}")
                else:
                    logger.error(
                        f"Failed to create conditional node with condition: {condition_type}"
                    )
        except Exception as e:
            logger.error(f"Error creating conditional nodes: {e}")
            raise

    def _create_llm_nodes(self, node_map):
        """Create LLM nodes from configuration."""
        logger.debug("Creating LLM nodes from configuration")
        try:
            llm_nodes = self.chain_config.get("llm_nodes", [])
            logger.debug(f"Found {len(llm_nodes)} LLM nodes to create")
            for llm_data in llm_nodes:
                if "llm_configuration" in llm_data:
                    config = llm_data["llm_configuration"]
                    model = config.get("model", "")
                    prompt = config.get("prompt", "")
                    temperature = config.get("temperature", 0.7)
                    max_tokens = config.get("max_tokens", 4096)
                else:
                    model = llm_data.get("model", "")
                    prompt = llm_data.get("prompt", "")
                    temperature = llm_data.get("temperature", 0.7)
                    max_tokens = llm_data.get("max_tokens", 4096)
                logger.debug(f"Creating LLM node with model: {model}")
                node = self.parent_widget.graph_manager.create_node(
                    "llm.LLMNode", name="LLM"
                )
                if node:
                    node.set_property("prompt", llm_data.get("prompt", ""))
                    node.set_property("model", llm_data.get("model", ""))
                    node.set_property(
                        "system_message", llm_data.get("system_message", "")
                    )
                    node.set_property(
                        "temperature", str(llm_data.get("temperature", 0.7))
                    )
                    node.set_property(
                        "max_tokens", str(llm_data.get("max_tokens", 4096))
                    )
                    node.set_property(
                        "output_variable", llm_data.get("output_variable", "llm_output")
                    )
                    node.set_property("api_url", llm_data.get("api_url", ""))
                    node.set_property(
                        "write_text", str(llm_data.get("write_text", "true"))
                    )
                    node.set_property(
                        "use_vision", str(llm_data.get("use_vision", "false"))
                    )
                    node.set_property(
                        "vision_model",
                        str(llm_data.get("vision_model", "minicpm-v:latest")),
                    )
                    node.set_property(
                        "screenshot_enabled",
                        str(llm_data.get("screenshot_enabled", "true")),
                    )
                    node.set_property(
                        "typing_batch_size", str(llm_data.get("typing_batch_size", 20))
                    )
                    node.set_property(
                        "typing_batch_delay",
                        str(llm_data.get("typing_batch_delay", 0.05)),
                    )
                    node.set_property(
                        "input_source", llm_data.get("input_source", "none")
                    )
                    node.set_property(
                        "orchestrator_mode", bool(llm_data.get("orchestrator_mode", False))
                    )
                    node.set_property(
                        "orch_max_steps", str(llm_data.get("orch_max_steps", 15) or 15)
                    )
                    node.set_property(
                        "orch_goal", llm_data.get("orch_goal") or "")
                    node.set_property(
                        "orch_synthesize", bool(llm_data.get("orch_synthesize", True))
                    )
                    node.set_property(
                        "orch_synthesis_system", llm_data.get("orch_synthesis_system") or ""
                    )
                    node.set_property(
                        "orch_use_goal_ledger",
                        bool(llm_data.get("orch_use_goal_ledger", False)),
                    )
                    try:
                        node.rebuild_orchestrator_ports()
                    except Exception:
                        pass
                    node.set_property(
                        "ocr_confidence", str(llm_data.get("ocr_confidence", 0.5))
                    )
                    node.set_property(
                        "ocr_preprocessing",
                        str(llm_data.get("ocr_preprocessing", "true")),
                    )
                    node.set_property(
                        "use_async", str(llm_data.get("use_async", "true"))
                    )
                    node.set_property(
                        "use_direct_rag",
                        str(llm_data.get("use_direct_rag", "true")).lower(),
                    )
                    node.set_property(
                        "rag_embedding_model", llm_data.get("rag_embedding_model", "")
                    )
                    node.set_property(
                        "rag_chunk_size", str(llm_data.get("rag_chunk_size", 500))
                    )
                    node.set_property(
                        "rag_overlap", str(llm_data.get("rag_overlap", 100))
                    )
                    node.set_property("rag_top_k", str(llm_data.get("rag_top_k", 3)))
                    node.set_property(
                        "rag_include_raw_input",
                        str(llm_data.get("rag_include_raw_input", "false")).lower(),
                    )
                    node.set_property(
                        "rag_max_chars", str(llm_data.get("rag_max_chars", 1500))
                    )
                    try:
                        import json as _json

                        node.set_property(
                            "rag_documents",
                            _json.dumps(llm_data.get("rag_documents", [])),
                        )
                    except Exception:
                        node.set_property("rag_documents", "[]")
                    try:
                        import json as _json

                        node.set_property(
                            "tool_descriptions",
                            _json.dumps(llm_data.get("tool_descriptions", {})),
                        )
                    except Exception:
                        node.set_property("tool_descriptions", "{}")
                    try:
                        import json as _json

                        node.set_property(
                            "skills", _json.dumps(llm_data.get("skills", []))
                        )
                    except Exception:
                        node.set_property("skills", "[]")
                    node.set_property(
                        "use_skill_routing",
                        str(llm_data.get("use_skill_routing", True)).lower(),
                    )
                    node.set_property(
                        "semantic_description", llm_data.get("semantic_description", "")
                    )
                    # ----- Context consolidation (restore) -----
                    node.set_property(
                        "use_context_consolidation",
                        str(llm_data.get("use_context_consolidation", False)).lower(),
                    )
                    node.set_property(
                        "consolidation_chunk_size",
                        str(llm_data.get("consolidation_chunk_size", 1000)),
                    )
                    node.set_property(
                        "consolidation_overlap",
                        str(llm_data.get("consolidation_overlap", 200)),
                    )
                    node.set_property(
                        "consolidation_top_k",
                        str(llm_data.get("consolidation_top_k", 5)),
                    )
                    node.set_property(
                        "consolidation_max_tokens",
                        str(llm_data.get("consolidation_max_tokens", 64)),
                    )
                    # ----- llama.cpp config (restore) -----
                    node.set_property(
                        "use_llamacpp", str(llm_data.get("use_llamacpp", False)).lower()
                    )
                    node.set_property(
                        "llamacpp_model_path", llm_data.get("llamacpp_model_path", "")
                    )
                    node.set_property(
                        "llamacpp_gpu_layers",
                        str(llm_data.get("llamacpp_gpu_layers", 0)),
                    )
                    node.set_property(
                        "llamacpp_threads", str(llm_data.get("llamacpp_threads", -1))
                    )
                    node.set_property(
                        "llamacpp_context_size",
                        str(llm_data.get("llamacpp_context_size", 0)),
                    )
                    # ----------------------------------------
                    position = llm_data.get("position", [0, 0])
                    node.set_pos(*position)
                    if hasattr(self.parent_widget, "llm_nodes"):
                        self.parent_widget.llm_nodes.append(node)
                    node_id = llm_data.get("id") or llm_data.get("node_id", node.id)
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create LLM node with model: {model}")
        except Exception as e:
            logger.error(f"Error creating LLM nodes: {e}")
            raise

    def _create_chain_import_nodes(self, node_map):
        """Create chain import nodes from configuration."""
        logger.debug("Creating chain import nodes from configuration")
        try:
            chain_import_nodes = self.chain_config.get("chain_import_nodes", [])
            logger.debug(
                f"Found {len(chain_import_nodes)} chain import nodes to create"
            )

            for import_data in chain_import_nodes:
                file_path = import_data.get("chain_file_path", "")
                import_mode = import_data.get("import_mode", "full")
                prefix = import_data.get("prefix", "")

                logger.debug(f"Creating chain import node with file: {file_path}")
                node = self.parent_widget.graph_manager.create_node(
                    "chain_import.ChainImportNode", name="Chain Import"
                )

                if node:
                    # Set chain import data (this loads the chain configuration)
                    enabled = import_data.get("enabled", True)
                    if isinstance(enabled, str):
                        enabled = enabled.lower() == "true"

                    # Get loop_count and extra_delay from import_data
                    loop_count = import_data.get("loop_count", 1)
                    extra_delay = import_data.get("extra_delay", 0)

                    if file_path:  # Only set data if we have a file path
                        node.set_chain_import_data(
                            file_path,
                            import_mode,
                            prefix,
                            loop_count,
                            extra_delay,
                            enabled,
                        )
                        logger.debug(f"Set chain import data for node: {file_path}")
                    else:
                        # Set properties manually if no file path
                        node.set_property("chain_file", file_path)
                        node.set_property("import_mode", import_mode)
                        node.set_property("prefix", prefix)
                        node.set_property("loop_count", str(loop_count))
                        node.set_property("extra_delay", str(extra_delay))
                        node.set_property("enabled", str(enabled).lower())
                        node.set_name("Import: No file")
                        logger.debug(
                            f"Set properties for chain import node with no file path"
                        )

                    # Also restore sandbox properties if available
                    if "run_in_sandbox" in import_data:
                        node.set_property(
                            "run_in_sandbox",
                            "true" if import_data.get("run_in_sandbox") else "false",
                        )
                    if "show_sandbox_window" in import_data:
                        node.set_property(
                            "show_sandbox_window",
                            "true"
                            if import_data.get("show_sandbox_window")
                            else "false",
                        )

                    # Output content selection (emit_data / data_output_nodes)
                    if "emit_data" in import_data:
                        node.set_property(
                            "emit_data",
                            "true" if import_data.get("emit_data") else "false",
                        )
                    if "data_output_nodes" in import_data:
                        try:
                            import json as _json

                            node.set_property(
                                "data_output_nodes",
                                _json.dumps(
                                    import_data.get("data_output_nodes") or []
                                ),
                            )
                        except Exception:
                            node.set_property("data_output_nodes", "[]")

                    # CRITICAL: If the imported chain configuration is already included in the saved data
                    # (from ChainExpansionDialog editing), restore it to the node
                    if (
                        import_data.get("sequences")
                        or import_data.get("conditional_nodes")
                        or import_data.get("llm_nodes")
                        or import_data.get("output_nodes")
                    ):
                        logger.debug(
                            f"Restoring imported chain configuration for node {node.id}"
                        )
                        # Reconstruct the imported chain configuration
                        imported_config = {
                            "sequences": import_data.get("sequences", []),
                            "conditional_nodes": import_data.get(
                                "conditional_nodes", []
                            ),
                            "llm_nodes": import_data.get("llm_nodes", []),
                            "output_nodes": import_data.get("output_nodes", []),
                            "chain_import_nodes": import_data.get(
                                "chain_import_nodes", []
                            ),
                        }
                        # Set the reconstructed configuration on the node
                        node.chain_config = imported_config
                        logger.debug(
                            f"Successfully restored imported chain configuration for node {node.id}"
                        )

                    # Set position
                    position = import_data.get("position", [0, 0])
                    node.set_pos(*position)
                    logger.debug(f"Set position for chain import node: {position}")

                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, "chain_import_nodes"):
                        self.parent_widget.chain_import_nodes.append(node)
                        logger.debug(
                            f"Added chain import node to parent widget tracking list"
                        )

                    # Store in node map
                    node_id = import_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added chain import node to node map: {node_id}")
                else:
                    logger.error(
                        f"Failed to create chain import node with file: {file_path}"
                    )
        except Exception as e:
            logger.error(f"Error creating chain import nodes: {e}")
            raise

    def _create_form_filler_nodes(self, node_map):
        """Create Form Filling nodes from configuration."""
        logger.debug("Creating Form Filling nodes from configuration")
        try:
            form_filler_nodes = self.chain_config.get("form_filler_nodes", [])
            logger.debug(f"Found {len(form_filler_nodes)} Form Filling nodes to create")

            for ff_data in form_filler_nodes:
                mode = ff_data.get("mode", "web")
                node = self.parent_widget.graph_manager.create_node(
                    "form_filler.FormFillerNode", name=f"Form Filling: {mode}"
                )

                if node:
                    node.set_property("mode", mode)
                    node.set_property("instruction", ff_data.get("instruction", ""))
                    node.set_property("fields_include", ff_data.get("fields_include", ""))
                    node.set_property("fields_skip", ff_data.get("fields_skip", ""))
                    node.set_property("probe_top_k", str(ff_data.get("probe_top_k", 3)))
                    node.set_property("probe_char_budget", str(ff_data.get("probe_char_budget", 1500)))
                    node.set_property("probe_context_chars", str(ff_data.get("probe_context_chars", 6000)))
                    node.set_property("consolidate", "true" if ff_data.get("consolidate", True) else "false")
                    node.set_property("probe_cycles", str(ff_data.get("probe_cycles", 3)))
                    node.set_property("verify", "true" if ff_data.get("verify", True) else "false")
                    node.set_property("repair", "true" if ff_data.get("repair", True) else "false")
                    node.set_property("repair_attempts", str(ff_data.get("repair_attempts", 2)))
                    node.set_property("answer_no", "true" if ff_data.get("answer_no", True) else "false")
                    node.set_property("answer_na", "true" if ff_data.get("answer_na", True) else "false")
                    node.set_property("ask_user", "true" if ff_data.get("ask_user", False) else "false")
                    node.set_property("max_fields", str(ff_data.get("max_fields", 40)))
                    node.set_property("engine", ff_data.get("engine", "llamacpp"))
                    node.set_property("model", ff_data.get("model", ""))
                    node.set_property("temperature", str(ff_data.get("temperature", 0.1)))
                    node.set_property("max_tokens", str(ff_data.get("max_tokens", 1024)))
                    node.set_property("context_size", str(ff_data.get("context_size", 0)))
                    node.set_property("typing_batch_size", str(ff_data.get("typing_batch_size", 20)))
                    node.set_property("typing_batch_delay", str(ff_data.get("typing_batch_delay", 0.05)))
                    node.set_property("rag_documents", json.dumps(ff_data.get("rag_documents", []) or []))
                    node.set_property("web_scope", ff_data.get("web_scope", "") or "")

                    # Set position
                    position = ff_data.get("position", [0, 0])
                    node.set_pos(*position)

                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, "form_filler_nodes"):
                        self.parent_widget.form_filler_nodes.append(node)

                    # Store in node map
                    node_id = ff_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added Form Filling node to node map: {node_id}")
                else:
                    logger.error("Failed to create Form Filling node")
        except Exception as e:
            logger.error(f"Error creating Form Filling nodes: {e}")
            raise

    def _create_code_nodes(self, node_map):
        """Create Code nodes from configuration."""
        logger.debug("Creating Code nodes from configuration")
        try:
            code_nodes = self.chain_config.get("code_nodes", [])
            logger.debug(f"Found {len(code_nodes)} Code nodes to create")

            for code_data in code_nodes:
                logger.debug(f"Creating Code node")
                node = self.parent_widget.graph_manager.create_node(
                    "code.CodeNode", name="Code"
                )

                if node:
                    # Set properties
                    node.set_property("code", code_data.get("code", ""))
                    node.set_property("file_path", code_data.get("file_path", ""))
                    node.set_property(
                        "execute_on_input",
                        "true" if code_data.get("execute_on_input", True) else "false",
                    )
                    node.set_property(
                        "output_variable", code_data.get("output_variable", "result")
                    )
                    node.set_property("timeout", str(code_data.get("timeout", 30)))
                    node.set_property("description", code_data.get("description", ""))
                    # Custom IO ports
                    node.set_property("input_vars", json.dumps(code_data.get("input_vars", [])))
                    node.set_property("output_vars", json.dumps(code_data.get("output_vars", [])))
                    try:
                        node.rebuild_ports()
                    except Exception:
                        pass

                    # Update name
                    if code_data.get("file_path"):
                        import os

                        name_preview = os.path.basename(code_data["file_path"])
                        node.set_name(f"Code: {name_preview}")

                    # Set position
                    position = code_data.get("position", [0, 0])
                    node.set_pos(*position)

                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, "code_nodes"):
                        self.parent_widget.code_nodes.append(node)

                    # Store in node map
                    node_id = code_data.get("node_id", node.id)
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create Code node")
        except Exception as e:
            logger.error(f"Error creating Code nodes: {e}")
            raise

    def _create_container_nodes(self, node_map):
        """Create Container nodes from configuration."""
        logger.debug("Creating Container nodes from configuration")
        try:
            container_nodes = self.chain_config.get("container_nodes", [])
            logger.debug(f"Found {len(container_nodes)} Container nodes to create")

            for container_data in container_nodes:
                logger.debug(f"Creating Container node")
                node = self.parent_widget.graph_manager.create_node(
                    "container.ContainerNode", name="Container"
                )

                if node:
                    # Set properties
                    node.set_property("iso_path", container_data.get("iso_path", ""))
                    node.set_property(
                        "memory_mb", str(container_data.get("memory_mb", 1024))
                    )
                    node.set_property(
                        "cpu_cores", str(container_data.get("cpu_cores", 1))
                    )
                    node.set_property(
                        "hide_window",
                        "true" if container_data.get("hide_window", True) else "false",
                    )
                    node.set_property(
                        "execute_on_input",
                        "true"
                        if container_data.get("execute_on_input", True)
                        else "false",
                    )
                    node.set_property(
                        "output_variable",
                        container_data.get("output_variable", "container_result"),
                    )
                    node.set_property(
                        "timeout", str(container_data.get("timeout", 300))
                    )

                    # Update name
                    if container_data.get("iso_path"):
                        import os

                        name_preview = os.path.basename(container_data["iso_path"])
                        node.set_name(f"VM: {name_preview}")

                    # Set position
                    position = container_data.get("position", [0, 0])
                    node.set_pos(*position)

                    # Add to parent widget's tracking list if it exists
                    if hasattr(self.parent_widget, "container_nodes"):
                        self.parent_widget.container_nodes.append(node)

                    # Store in node map
                    node_id = container_data.get("node_id", node.id)
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create Container node")
        except Exception as e:
            logger.error(f"Error creating Container nodes: {e}")
            raise

    def _create_context_nodes(self, node_map):
        """Create Context nodes from configuration."""
        logger.debug("Creating Context nodes from configuration")
        try:
            context_nodes = self.chain_config.get("context_nodes", [])
            logger.debug(f"Found {len(context_nodes)} Context nodes to create")

            for context_data in context_nodes:
                logger.debug(f"Creating Context node")
                node = self.parent_widget.graph_manager.create_node(
                    "context.ContextNode", name="Context"
                )

                if node:
                    # Set simplified properties
                    node.set_property("label", context_data.get("label", ""))
                    node.set_property(
                        "max_history", int(context_data.get("max_history", 10))
                    )
                    # "Persist across chain runs" and "clear when the chain
                    # finishes" are opposite modes of the same choice.  Legacy
                    # chains may have both flags true (or clear_on_finish
                    # derived from agent_visible); clear_on_finish wins because
                    # the executor treats such nodes as per-run scoped, so
                    # normalize persistent=False for them on load.
                    _cof = bool(
                        context_data.get("clear_on_finish", False)
                        or context_data.get("agent_visible", False)
                    )
                    _persistent = bool(context_data.get("persistent", True))
                    if _cof:
                        _persistent = False
                    node.set_property("persistent", _persistent)
                    node.set_property(
                        "clear_on_finish", _cof
                    )
                    node.set_property("scope", str(context_data.get("scope", "local")))
                    node.set_property("shared_context_chain_file", str(context_data.get("shared_context_chain_file", "")))
                    node.set_property("shared_context_node_id", str(context_data.get("shared_context_node_id", "")))

                    # Update name if label is provided
                    label = context_data.get("label", "")
                    if label:
                        node.set_name(f"Context: {label}")

                    # Set position
                    position = context_data.get("position", [0, 0])
                    node.set_pos(*position)

                    # Add to parent widget's tracking list if it exists
                    if hasattr(self.parent_widget, "context_nodes"):
                        self.parent_widget.context_nodes.append(node)

                    # Store in node map
                    node_id = context_data.get("node_id", node.id)
                    # Preserve config node_id on the node for dialog preview lookups
                    try:
                        node.set_property('_config_node_id', node_id)
                    except Exception:
                        pass
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create Context node")
        except Exception as e:
            logger.error(f"Error creating Context nodes: {e}")
            raise

    def _create_input_nodes(self, node_map):
        """Create Input nodes from configuration."""
        logger.debug("Creating Input nodes from configuration")
        try:
            input_nodes = self.chain_config.get("input_nodes", [])
            logger.debug(f"Found {len(input_nodes)} Input nodes to create")

            for input_data in input_nodes:
                logger.debug(f"Creating Input node")
                node = self.parent_widget.graph_manager.create_node(
                    "input.InputNode", name="Input"
                )

                if node:
                    # Set properties
                    node.set_property("label", input_data.get("label", ""))
                    node.set_property("default_value", input_data.get("default_value", ""))
                    node.set_property("user_prompt", input_data.get("user_prompt", ""))
                    node.set_property("passthrough", bool(input_data.get("passthrough", False)))
                    node.set_property("web_mode", bool(input_data.get("web_mode", False)))
                    node.set_property("agent_modifiable", bool(input_data.get("agent_modifiable", False)))
                    node.set_property("decision_mode", bool(input_data.get("decision_mode", False)))
                    node.set_property("decision_criterion", input_data.get("decision_criterion", "") or "")
                    node.set_property("decision_evaluator", str(input_data.get("decision_evaluator", "llm") or "llm"))
                    node.set_property("decision_model", input_data.get("decision_model", "") or "")
                    node.set_property("decision_default", bool(input_data.get("decision_default", False)))
                    node.set_property("question_mode", str(input_data.get("question_mode", "text") or "text"))
                    node.set_property("choices", input_data.get("choices", "[]") or "[]")
                    node.set_property("route_on_answer", bool(input_data.get("route_on_answer", False)))
                    node.set_property("accept_text", bool(input_data.get("accept_text", True)))
                    node.set_property("accept_images", bool(input_data.get("accept_images", False)))
                    node.set_property("accept_documents", bool(input_data.get("accept_documents", False)))
                    # Decision routing ports must exist before connections restore.
                    if input_data.get("decision_mode") or input_data.get("route_on_answer"):
                        if hasattr(node, "rebuild_decision_ports"):
                            node.rebuild_decision_ports()
                    node.set_property("tts_enabled", bool(input_data.get("tts_enabled", False)))
                    node.set_property("tts_text", input_data.get("tts_text", "") or "")
                    node.set_property("tts_language", input_data.get("tts_language", "en") or "en")
                    node.set_property("tts_voice_model", input_data.get("tts_voice_model", "") or "")
                    node.set_property("tts_speed", input_data.get("tts_speed", 1.0))
                    node.set_property("tts_speaker_id", input_data.get("tts_speaker_id"))

                    # Apply passthrough colour if enabled
                    if input_data.get("passthrough") and hasattr(node, "set_passthrough"):
                        node.set_passthrough(True)

                    # Update name if label is provided
                    label = input_data.get("label", "")
                    passthrough = bool(input_data.get("passthrough", False))
                    suffix = " [passthrough]" if passthrough else ""
                    if bool(input_data.get("decision_mode", False)) or bool(input_data.get("route_on_answer", False)):
                        suffix += " [decide]"
                    if bool(input_data.get("agent_modifiable", False)):
                        suffix += " [agent]"
                    if label:
                        node.set_name(f"Input: {label}{suffix}")
                    elif passthrough:
                        node.set_name(f"Input{suffix}")

                    # Set position
                    position = input_data.get("position", [0, 0])
                    node.set_pos(*position)

                    # Add to parent widget's tracking list if it exists
                    if hasattr(self.parent_widget, "input_nodes"):
                        self.parent_widget.input_nodes.append(node)

                    # Store in node map
                    node_id = input_data.get("node_id", node.id)
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create Input node")
        except Exception as e:
            logger.error(f"Error creating Input nodes: {e}")
            raise

    def _create_handle_nodes(self, node_map):
        """Create Handle nodes from configuration."""
        logger.debug("Creating Handle nodes from configuration")
        try:
            handle_nodes = self.chain_config.get("handle_nodes", [])
            logger.debug(f"Found {len(handle_nodes)} Handle nodes to create")

            for handle_data in handle_nodes:
                logger.debug(f"Creating Handle node")
                node = self.parent_widget.graph_manager.create_node(
                    "handle.HandleNode", name="Handle"
                )

                if node:
                    # Set properties
                    node.set_property("action_type", handle_data.get("action_type", "click"))
                    node.set_property("goal_description", handle_data.get("goal_description", ""))
                    node.set_property("target_description", handle_data.get("target_description", ""))
                    node.set_property("agent_adaptive", bool(handle_data.get("agent_adaptive", False)))
                    node.set_property("web_mode", bool(handle_data.get("web_mode", False)))

                    # Update name
                    action_type = handle_data.get("action_type", "click")
                    _web_suffix = " (web)" if handle_data.get("web_mode") else ""
                    node.set_name(f"Handle: {action_type}{_web_suffix}")

                    # Set position
                    position = handle_data.get("position", [0, 0])
                    node.set_pos(*position)

                    # Add to parent widget's tracking list if it exists
                    if hasattr(self.parent_widget, "handle_nodes"):
                        self.parent_widget.handle_nodes.append(node)

                    # Store in node map
                    node_id = handle_data.get("node_id", node.id)
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create Handle node")
        except Exception as e:
            logger.error(f"Error creating Handle nodes: {e}")
            raise

    def _create_mcp_nodes(self, node_map):
        """Create MCP nodes from configuration."""
        logger.debug("Creating MCP nodes from configuration")
        try:
            mcp_nodes = self.chain_config.get("mcp_nodes", [])
            logger.debug(f"Found {len(mcp_nodes)} MCP nodes to create")

            for mcp_data in mcp_nodes:
                logger.debug(f"Creating MCP node")
                node = self.parent_widget.graph_manager.create_node(
                    "mcp.MCPNode", name="MCP Server"
                )

                if node:
                    node.set_property("mcp_folder", mcp_data.get("mcp_folder", ""))
                    node.set_property("tool_name", mcp_data.get("tool_name", ""))
                    node.set_property("tool_args", mcp_data.get("tool_args", "{}"))
                    node.set_property("mcp_tools", mcp_data.get("mcp_tools", ""))
                    node.set_property("keep_alive", mcp_data.get("keep_alive", False))

                    tool_name = mcp_data.get("tool_name", "") or "(no tool)"
                    node.set_name(f"MCP: {tool_name}")

                    position = mcp_data.get("position", [0, 0])
                    node.set_pos(*position)

                    if hasattr(self.parent_widget, "mcp_nodes"):
                        self.parent_widget.mcp_nodes.append(node)

                    node_id = mcp_data.get("node_id", node.id)
                    node_map[node_id] = node
                else:
                    logger.error(f"Failed to create MCP node")
        except Exception as e:
            logger.error(f"Error creating MCP nodes: {e}")
            raise

    def _restore_connections(self, node_map):
        """Restore connections between nodes."""
        logger.debug("Restoring connections between nodes")
        try:
            all_node_data = (
                self.chain_config.get("sequences", [])
                + self.chain_config.get("conditional_nodes", [])
                + self.chain_config.get("llm_nodes", [])
                + self.chain_config.get("chain_import_nodes", [])
                + self.chain_config.get("form_filler_nodes", [])
                + self.chain_config.get("code_nodes", [])
                + self.chain_config.get("container_nodes", [])
                + self.chain_config.get("context_nodes", [])
                + self.chain_config.get("input_nodes", [])
                + self.chain_config.get("orchestrator_nodes", [])
                + self.chain_config.get("handle_nodes", [])
                + self.chain_config.get("mcp_nodes", [])
                + self.chain_config.get("output_nodes", [])
                + self.chain_config.get("web_sequences", [])
            )

            total_connections = sum(
                len(node_data.get("connections", [])) for node_data in all_node_data
            )
            logger.debug(f"Found {total_connections} connections to restore")

            connections_restored = 0
            for node_data in all_node_data:
                source_node_id = node_data.get("node_id")
                source_node = node_map.get(source_node_id)

                if not source_node:
                    logger.warning(
                        f"Source node not found in node map: {source_node_id}"
                    )
                    continue

                for connection in node_data.get("connections", []):
                    target_node_id = connection.get("target_node_id")
                    target_node = node_map.get(target_node_id)

                    if target_node:
                        # Connect nodes
                        output_port = connection.get("output_port")
                        input_port = connection.get("input_port")
                        # File-loop conditionals expose a single 'output' port;
                        # legacy saves may still reference 'true'/'false' —
                        # normalize so the loop continuation restores correctly.
                        try:
                            if (
                                output_port in ("true", "false")
                                and hasattr(source_node, "is_file_loop_conditional")
                                and source_node.is_file_loop_conditional()
                            ):
                                output_port = "output"
                        except Exception:
                            pass
                        logger.debug(
                            f"Connecting {source_node_id}:{output_port} -> {target_node_id}:{input_port}"
                        )

                        # Stale named ports (e.g. a renamed chain-import data
                        # port) must be SKIPPED: graph_manager.connect_nodes
                        # silently falls back to output port #0 — the
                        # execution port — which would mis-wire the target.
                        if isinstance(output_port, str):
                            try:
                                _out_names = {
                                    p.name() for p in source_node.output_ports()
                                }
                            except Exception:
                                # Node cannot enumerate ports — keep the
                                # legacy lenient behavior for it.
                                _out_names = None
                            if _out_names is not None and output_port not in _out_names:
                                logger.warning(
                                    f"Skipping connection {source_node_id}:"
                                    f"{output_port} -> {target_node_id}:"
                                    f"{input_port} — source port no longer "
                                    f"exists"
                                )
                                continue

                        # Symmetric guard for the TARGET input port: a stale
                        # name (e.g. 'tools' on a vanilla LLM node) would
                        # silently fall back to input port #0 and mis-wire.
                        if isinstance(input_port, str) and input_port:
                            try:
                                _in_names = {
                                    p.name() for p in target_node.input_ports()
                                }
                            except Exception:
                                _in_names = None
                            if _in_names is not None and input_port not in _in_names:
                                logger.warning(
                                    f"Skipping connection {source_node_id}:"
                                    f"{output_port} -> {target_node_id}:"
                                    f"{input_port} — target port no longer "
                                    f"exists"
                                )
                                continue

                        self.parent_widget.graph_manager.connect_nodes(
                            source_node, target_node, output_port, input_port
                        )
                        connections_restored += 1
                    else:
                        logger.warning(
                            f"Target node not found in node map: {target_node_id}"
                        )

            logger.info(f"Successfully restored {connections_restored} connections")
        except Exception as e:
            logger.error(f"Error restoring connections: {e}")
            raise
