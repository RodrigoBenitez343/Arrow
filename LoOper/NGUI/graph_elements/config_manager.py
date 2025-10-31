import json
import os
import logging
from PyQt5.QtWidgets import QMessageBox, QFileDialog
from ..nodes import SequenceNode, ActionNode, ConditionalNode, LLMNode, TTSNode, ChainImportNode

# Import cache invalidation function
try:
    from ...player.json_cache import invalidate_file, clear_cache
except ImportError:
    # Fallback if import fails
    def invalidate_file(file_path):
        pass
    def clear_cache():
        pass

# Configure logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.FileHandler('graphui_debug.log')
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.addHandler(console_handler)
    logger.setLevel(logging.DEBUG)


class ConfigManager:
    """Manages configuration loading, saving, and graph building."""
    
    def __init__(self, parent_widget):
        logger.info("Initializing ConfigManager")
        try:
            self.parent_widget = parent_widget
            self.chain_config = {"sequences": [], "conditional_nodes": [], "llm_nodes": [], "tts_nodes": [], "chain_import_nodes": []}
            logger.debug("ConfigManager initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing ConfigManager: {e}")
            raise
        
    def get_chain_config(self):
        """Get the current chain configuration."""
        logger.debug("Getting chain configuration")
        try:
            self.save_current_state()
            config_copy = self.chain_config.copy()
            logger.debug(f"Chain config contains {len(config_copy.get('sequences', []))} sequences, {len(config_copy.get('conditional_nodes', []))} conditional nodes, {len(config_copy.get('llm_nodes', []))} LLM nodes, {len(config_copy.get('tts_nodes', []))} TTS nodes, {len(config_copy.get('chain_import_nodes', []))} chain import nodes")
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
            self.chain_config = {"sequences": [], "conditional_nodes": [], "llm_nodes": [], "tts_nodes": [], "chain_import_nodes": []}
            
            # Get all nodes
            logger.debug("Getting all nodes from graph")
            all_nodes = self.parent_widget.graph_manager.get_all_nodes()
            logger.debug(f"Found {len(all_nodes)} nodes to process")
            
            # Process each node type
            for node in all_nodes:
                if isinstance(node, SequenceNode):
                    logger.debug(f"Saving sequence node: {node.id}")
                    self._save_sequence_node(node)
                elif isinstance(node, ConditionalNode):
                    logger.debug(f"Saving conditional node: {node.id}")
                    self._save_conditional_node(node)
                elif isinstance(node, LLMNode):
                    logger.debug(f"Saving LLM node: {node.id}")
                    self._save_llm_node(node)
                elif isinstance(node, TTSNode):
                    logger.debug(f"Saving TTS node: {node.id}")
                    self._save_tts_node(node)
                elif isinstance(node, ChainImportNode):
                    logger.debug(f"Saving chain import node: {node.id}")
                    self._save_chain_import_node(node)
                else:
                    logger.warning(f"Unknown node type: {type(node).__name__}")
            
            logger.info(f"Successfully saved current state: {len(self.chain_config['sequences'])} sequences, {len(self.chain_config['conditional_nodes'])} conditional nodes, {len(self.chain_config['llm_nodes'])} LLM nodes, {len(self.chain_config['tts_nodes'])} TTS nodes, {len(self.chain_config['chain_import_nodes'])} chain import nodes")
                    
        except Exception as e:
            logger.error(f"Error saving current state: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to save current state: {str(e)}"
            )
            
    def load_chain(self, file_path=None):
        """Load a chain configuration from file."""
        logger.info(f"Loading chain configuration from file: {file_path or 'user selection'}")
        try:
            if not file_path:
                logger.debug("No file path provided, opening file dialog")
                file_path, _ = QFileDialog.getOpenFileName(
                    self.parent_widget,
                    "Load Chain",
                    self.parent_widget.chains_folder,
                    "JSON files (*.json)"
                )
                logger.debug(f"User selected file: {file_path}")
                
            if file_path and os.path.exists(file_path):
                logger.debug(f"Loading configuration from: {file_path}")
                with open(file_path, 'r') as f:
                    loaded_config = json.load(f)
                
                # Ensure all required keys exist
                self.chain_config = {
                    "sequences": loaded_config.get("sequences", []),
                    "conditional_nodes": loaded_config.get("conditional_nodes", []),
                    "llm_nodes": loaded_config.get("llm_nodes", []),
                    "tts_nodes": loaded_config.get("tts_nodes", []),
                    "chain_import_nodes": loaded_config.get("chain_import_nodes", [])
                }
                
                logger.debug(f"Loaded config with {len(self.chain_config.get('sequences', []))} sequences, {len(self.chain_config.get('conditional_nodes', []))} conditional nodes, {len(self.chain_config.get('llm_nodes', []))} LLM nodes, {len(self.chain_config.get('tts_nodes', []))} TTS nodes, {len(self.chain_config.get('chain_import_nodes', []))} chain import nodes")
                    
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
                self.parent_widget, 
                "Error", 
                f"Failed to load chain: {str(e)}"
            )
            
        return False
        
    def save_chain(self, file_path=None):
        """Save the current chain configuration to file."""
        logger.info(f"Saving chain configuration to file: {file_path or 'user selection'}")
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
                    "JSON files (*.json)"
                )
                logger.debug(f"User selected save path: {file_path}")
                
            if file_path:
                logger.debug(f"Saving configuration to: {file_path}")
                with open(file_path, 'w') as f:
                    json.dump(self.chain_config, f, indent=2)
                
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
                self.parent_widget, 
                "Error", 
                f"Failed to save chain: {str(e)}"
            )
            
        return False
        
    def build_graph_from_config(self):
        """Build the node graph from the current configuration."""
        logger.info("Building node graph from configuration")
        try:
            # Ensure the graph and view are properly initialized before building
            if not hasattr(self.parent_widget, 'node_graph') or self.parent_widget.node_graph is None:
                logger.error("Node graph not initialized, cannot build graph from config")
                raise RuntimeError("Node graph not initialized")
                
            if not hasattr(self.parent_widget, 'view') or self.parent_widget.view is None:
                logger.error("Graph view not initialized, cannot build graph from config")
                raise RuntimeError("Graph view not initialized")
            
            # Clear existing graph
            logger.debug("Clearing existing graph")
            self.parent_widget.graph_manager.clear_graph()
            
            # Clear tracking lists to prevent duplicate nodes
            if hasattr(self.parent_widget, 'sequence_nodes'):
                self.parent_widget.sequence_nodes.clear()
                logger.debug("Cleared sequence_nodes tracking list")
            if hasattr(self.parent_widget, 'conditional_nodes'):
                self.parent_widget.conditional_nodes.clear()
                logger.debug("Cleared conditional_nodes tracking list")
            if hasattr(self.parent_widget, 'llm_nodes'):
                self.parent_widget.llm_nodes.clear()
                logger.debug("Cleared llm_nodes tracking list")
            if hasattr(self.parent_widget, 'tts_nodes'):
                self.parent_widget.tts_nodes.clear()
                logger.debug("Cleared tts_nodes tracking list")
            if hasattr(self.parent_widget, 'chain_import_nodes'):
                self.parent_widget.chain_import_nodes.clear()
                logger.debug("Cleared chain_import_nodes tracking list")
            
            # Store node references for connections
            node_map = {}
            logger.debug("Created node map for connection restoration")
            
            # Create sequence nodes
            logger.debug("Creating sequence nodes")
            self._create_sequence_nodes(node_map)
            
            # Create conditional nodes
            logger.debug("Creating conditional nodes")
            self._create_conditional_nodes(node_map)
            
            # Create LLM nodes
            logger.debug("Creating LLM nodes")
            self._create_llm_nodes(node_map)
            
            # Create TTS nodes
            logger.debug("Creating TTS nodes")
            self._create_tts_nodes(node_map)
            
            # Create chain import nodes
            logger.debug("Creating chain import nodes")
            self._create_chain_import_nodes(node_map)
            
            # Restore connections
            logger.debug("Restoring node connections")
            self._restore_connections(node_map)
            
            # Refresh current view
            if hasattr(self.parent_widget, 'view_manager'):
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
                f"Failed to build graph from config: {str(e)}"
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
                seq for seq in self.chain_config["sequences"] 
                if seq.get("name") != sequence_name
            ]
            new_count = len(self.chain_config["sequences"])
            removed_count = original_count - new_count
            logger.debug(f"Removed {removed_count} sequence(s) named '{sequence_name}' from chain config")
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
                        action for action in sequence["actions"] 
                        if action != action_name
                    ]
                    removed_count = original_count - len(sequence["actions"])
                    total_removed += removed_count
                    if removed_count > 0:
                        logger.debug(f"Removed {removed_count} instance(s) of action '{action_name}' from sequence '{sequence.get('name', 'unknown')}'")
            
            logger.debug(f"Total removed {total_removed} instance(s) of action '{action_name}' from all sequences")
        except Exception as e:
            logger.error(f"Error removing action from sequences: {e}")
            raise
                
    def _save_sequence_node(self, node):
        """Save a sequence node to configuration."""
        logger.debug(f"Saving sequence node {node.id} to configuration")
        try:
            # CRITICAL: Always use the sequence_file property which contains the original file path
            # The node name may have GUI numbering (e.g., "test.json 2") but sequence_file should be clean
            sequence_file = node.get_property('sequence_file')
            
            if not sequence_file:
                # Fallback: extract base filename from node name if sequence_file is missing
                node_name = node.name()
                import re
                if re.match(r'^(.+\.json)\s+\d+$', node_name):
                    # Remove GUI numbering from node name
                    sequence_file = re.match(r'^(.+\.json)\s+\d+$', node_name).group(1)
                    logger.warning(f"sequence_file property missing! Extracted '{sequence_file}' from numbered node name '{node_name}'")
                else:
                    sequence_file = node_name
                    logger.warning(f"sequence_file property missing! Using node name '{sequence_file}' as fallback")
            else:
                logger.debug(f"Using sequence_file property: '{sequence_file}' for node '{node.name()}'")
                
            # Ensure we only store the filename, not the full path for consistency
            import os
            if os.path.sep in sequence_file or '/' in sequence_file:
                sequence_file = os.path.basename(sequence_file)
                logger.debug(f"Extracted filename '{sequence_file}' from full path")
            
            sequence_data = {
                "name": node.get_property('sequence_name') or node.name(),
                "sequence_file": sequence_file,  # Use cleaned sequence file name
                "loop_count": int(node.get_property('loop_count') or 1),
                "extra_delay": float(node.get_property('extra_delay') or 1.0),
                "actions": node.get_property('actions') or [],
                "node_id": node.id,
                "position": node.pos(),  # pos() returns [x, y] list directly
                "connections": self._get_node_connections(node)
            }
            
            self.chain_config["sequences"].append(sequence_data)
            logger.debug(f"Successfully saved sequence node: {sequence_data['name']} with sequence_file: {sequence_data['sequence_file']}")
        except Exception as e:
            logger.error(f"Error saving sequence node {node.id}: {e}")
            raise
        
    def _save_conditional_node(self, node):
        """Save a conditional node to configuration."""
        logger.debug(f"Saving conditional node {node.id} to configuration")
        try:
            conditional_data = {
                "condition_type": node.get_property('condition_type') or 'presence',
                "image_path": node.get_property('image_path') or '',
                "threshold": float(node.get_property('threshold') or 0.8),
                "wait_time": float(node.get_property('wait_time') or 5),
                "max_loops": int(node.get_property('max_loops') or 10),
                "ocr_text": node.get_property('ocr_text') or '',
                # Timeout and retry properties
                "timeout": float(node.get_property('timeout') or 5.0),
                "max_attempts": int(node.get_property('max_attempts') or 3),
                "delay_between_attempts": float(node.get_property('delay_between_attempts') or 1.0),
                # Loop-specific properties
                "loop_type": node.get_property('loop_type') or '',
                "loop_position": node.get_property('loop_position') or '',
                "sequence_file": node.get_property('sequence_file') or '',
                "iteration_delay": float(node.get_property('iteration_delay') or 1.0),
                "loop_condition": node.get_property('loop_condition') or '',
                # OCR-specific properties
                "case_sensitive": node.get_property('case_sensitive') or 'true',
                "node_id": node.id,
                "position": node.pos(),  # pos() returns [x, y] list directly
                "connections": self._get_node_connections(node)
            }
            
            self.chain_config["conditional_nodes"].append(conditional_data)
            logger.debug(f"Successfully saved conditional node with condition type: {conditional_data['condition_type']}")
        except Exception as e:
            logger.error(f"Error saving conditional node {node.id}: {e}")
            raise
        
    def _save_llm_node(self, node):
        """Save an LLM node to configuration."""
        logger.debug(f"Saving LLM node {node.id} to configuration")
        try:
            # Get the complete LLM configuration from the node
            llm_config = node.get_llm_config() if hasattr(node, 'get_llm_config') else {}
            
            # Build comprehensive LLM data with all properties from llm_config
            llm_data = {
                "prompt": llm_config.get('prompt', ''),
                "model": llm_config.get('model', 'llama3.2:latest'),
                "system_message": llm_config.get('system_message', ''),
                "temperature": llm_config.get('temperature', 0.7),
                "max_tokens": llm_config.get('max_tokens', 500),
                "output_variable": llm_config.get('output_variable', 'llm_output'),
                "api_url": llm_config.get('api_url', ''),
                "write_text": llm_config.get('write_text', True),
                "use_vision": llm_config.get('use_vision', False),
                "screenshot_enabled": llm_config.get('screenshot_enabled', True),
                "use_comorag": llm_config.get('use_comorag', True),
                "max_cycles": llm_config.get('max_cycles', 5),
                "memory_size": llm_config.get('memory_size', 1000),
                "typing_batch_size": llm_config.get('typing_batch_size', 20),
                "typing_batch_delay": llm_config.get('typing_batch_delay', 0.05),
                "input_source": llm_config.get('input_source', 'none'),
                "ocr_confidence": llm_config.get('ocr_confidence', 0.5),
                "ocr_preprocessing": llm_config.get('ocr_preprocessing', True),
                "use_async": llm_config.get('use_async', True),
                "node_id": node.id,
                "position": node.pos(),  # pos() returns [x, y] list directly
                "connections": self._get_node_connections(node)
            }
            
            self.chain_config["llm_nodes"].append(llm_data)
            logger.debug(f"Successfully saved LLM node with model: {llm_data['model']} and all properties")
        except Exception as e:
            logger.error(f"Error saving LLM node {node.id}: {e}")
            raise
        
    def _save_tts_node(self, node):
        """Save a TTS node to configuration."""
        logger.debug(f"Saving TTS node {node.id} to configuration")
        try:
            tts_data = {
                "type": "tts",
                "text": node.get_property('text') or 'Hello, this is a text to speech message.',
                "language": node.get_property('language') or 'en',
                "node_id": node.id,
                "position": node.pos(),  # pos() returns [x, y] list directly
                "connections": self._get_node_connections(node)
            }
            
            self.chain_config["tts_nodes"].append(tts_data)
            logger.debug(f"Successfully saved TTS node with text: {tts_data['text'][:30]}...")
        except Exception as e:
            logger.error(f"Error saving TTS node {node.id}: {e}")
            raise
    
    def _save_chain_import_node(self, node):
        """Save a chain import node to configuration."""
        logger.debug(f"Saving chain import node {node.id} to configuration")
        try:
            import_config = node.get_chain_import_config()
            imported_chain_config = node.get_imported_chain_config()
            
            chain_import_data = {
                "type": "chain_import",
                "chain_file_path": import_config.get('chain_file', ''),
                "import_mode": import_config.get('import_mode', 'full'),
                "prefix": import_config.get('prefix', ''),
                "loop_count": import_config.get('loop_count', 1),
                "extra_delay": import_config.get('extra_delay', 0),
                "enabled": import_config.get('enabled', True),
                "node_id": node.id,
                "position": node.pos(),
                "connections": self._get_node_connections(node)
            }
            
            # CRITICAL: If we have an imported chain configuration (from ChainExpansionDialog editing),
            # save the actual chain configuration instead of just the import configuration
            if imported_chain_config and (
                imported_chain_config.get('sequences') or 
                imported_chain_config.get('conditional_nodes') or 
                imported_chain_config.get('llm_nodes') or 
                imported_chain_config.get('tts_nodes')
            ):
                # This is an edited chain from ChainExpansionDialog - save the full configuration
                logger.debug(f"Saving full imported chain configuration for node {node.id}")
                chain_import_data.update({
                    "sequences": imported_chain_config.get("sequences", []),
                    "conditional_nodes": imported_chain_config.get("conditional_nodes", []),
                    "llm_nodes": imported_chain_config.get("llm_nodes", []),
                    "tts_nodes": imported_chain_config.get("tts_nodes", []),
                    "chain_import_nodes": imported_chain_config.get("chain_import_nodes", [])
                })
            
            self.chain_config["chain_import_nodes"].append(chain_import_data)
            logger.debug(f"Successfully saved chain import node with file: {chain_import_data['chain_file_path']}")
        except Exception as e:
            logger.error(f"Error saving chain import node {node.id}: {e}")
            raise
        
    def _get_node_connections(self, node):
        """Get all connections for a node."""
        logger.debug(f"Getting connections for node {node.id}")
        try:
            connections = []
            
            # Get output connections
            for output_port in node.output_ports():
                for connected_port in output_port.connected_ports():
                    connection = {
                        "output_port": output_port.name(),
                        "target_node_id": connected_port.node().id,
                        "input_port": connected_port.name()
                    }
                    connections.append(connection)
                    logger.debug(f"Found connection: {output_port.name()} -> {connected_port.node().id}:{connected_port.name()}")
            
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
                
                logger.debug(f"Creating sequence node: {node_name} with sequence_file: {sequence_file}")
                
                # Use the sequence filename for the node name to prevent numbering issues
                display_name = os.path.basename(sequence_file) if sequence_file else node_name
                
                node = self.parent_widget.graph_manager.create_node(
                    'sequence.SequenceNode',
                    name=display_name
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
                        logger.warning(f"sequence_file missing in config, using fallback: {sequence_file}")
                    
                    # Ensure we store only the filename, not full path
                    if sequence_file and (os.path.sep in sequence_file or '/' in sequence_file):
                        sequence_file = os.path.basename(sequence_file)
                    
                    # Set properties that exist on SequenceNode
                    node.set_property('sequence_file', sequence_file)
                    node.set_property('loop_count', str(seq_data.get('loop_count', 1)))
                    node.set_property('extra_delay', str(seq_data.get('extra_delay', 1.0)))
                    logger.debug(f"Set sequence_file property for sequence node: {sequence_file}")
                    logger.debug(f"Set loop_count property for sequence node: {seq_data.get('loop_count', 1)}")
                    logger.debug(f"Set extra_delay property for sequence node: {seq_data.get('extra_delay', 1.0)}")
                    
                    # Set position
                    if "position" in seq_data:
                        node.set_pos(*seq_data["position"])
                        logger.debug(f"Set position for sequence node: {seq_data['position']}")
                        
                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, 'sequence_nodes'):
                        self.parent_widget.sequence_nodes.append(node)
                        logger.debug(f"Added sequence node to parent widget tracking list")
                        
                    # Store in node map
                    node_id = seq_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added sequence node to node map: {node_id}")
                else:
                    logger.error(f"Failed to create sequence node: {node_name}")
        except Exception as e:
            logger.error(f"Error creating sequence nodes: {e}")
            raise
                
    def _create_conditional_nodes(self, node_map):
        """Create conditional nodes from configuration."""
        logger.debug("Creating conditional nodes from configuration")
        try:
            conditional_nodes = self.chain_config.get("conditional_nodes", [])
            logger.debug(f"Found {len(conditional_nodes)} conditional nodes to create")
            
            for cond_data in conditional_nodes:
                # Create node
                condition_type = cond_data.get('condition_type') or cond_data.get('trigger_type', 'presence')  # backward compatibility
                logger.debug(f"Creating conditional node with condition type: {condition_type}")
                node = self.parent_widget.graph_manager.create_node(
                    'conditional.ConditionalNode',
                    name='Conditional'
                )
                
                if node:
                    # Set properties
                    node.set_property('condition_type', condition_type)
                    node.set_property('image_path', cond_data.get('image_path', ''))
                    node.set_property('threshold', str(cond_data.get('threshold', 0.8)))
                    node.set_property('wait_time', str(cond_data.get('wait_time', 5)))
                    node.set_property('max_loops', str(cond_data.get('max_loops', 10)))
                    node.set_property('ocr_text', cond_data.get('ocr_text', ''))
                    # Timeout and retry properties
                    node.set_property('timeout', str(cond_data.get('timeout', 5.0)))
                    node.set_property('max_attempts', str(cond_data.get('max_attempts', 3)))
                    node.set_property('delay_between_attempts', str(cond_data.get('delay_between_attempts', 1.0)))
                    # Loop-specific properties
                    node.set_property('loop_type', cond_data.get('loop_type', ''))
                    node.set_property('loop_position', cond_data.get('loop_position', ''))
                    node.set_property('sequence_file', cond_data.get('sequence_file', ''))
                    node.set_property('iteration_delay', str(cond_data.get('iteration_delay', 1.0)))
                    node.set_property('loop_condition', cond_data.get('loop_condition', ''))
                    # OCR-specific properties (language and region removed - uses full screen with default English)
                    node.set_property('case_sensitive', cond_data.get('case_sensitive', 'true'))
                    logger.debug(f"Set properties for conditional node with condition: {condition_type}")
                    
                    # Set position
                    if "position" in cond_data:
                        node.set_pos(*cond_data["position"])
                        logger.debug(f"Set position for conditional node: {cond_data['position']}")
                        
                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, 'conditional_nodes'):
                        self.parent_widget.conditional_nodes.append(node)
                        logger.debug(f"Added conditional node to parent widget tracking list")
                        
                    # Store in node map
                    node_id = cond_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added conditional node to node map: {node_id}")
                else:
                    logger.error(f"Failed to create conditional node with condition: {condition_type}")
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
                # Handle both formats: direct properties and nested llm_configuration
                if "llm_configuration" in llm_data:
                    # New format with nested configuration
                    config = llm_data["llm_configuration"]
                    model = config.get('model', 'llama3.2:latest')
                    prompt = config.get('prompt', '')
                    temperature = config.get('temperature', 0.7)
                    max_tokens = config.get('max_tokens', 500)
                else:
                    # Old format with direct properties - use all available properties
                    model = llm_data.get('model', 'llama3.2:latest')
                    prompt = llm_data.get('prompt', '')
                    temperature = llm_data.get('temperature', 0.7)
                    max_tokens = llm_data.get('max_tokens', 500)
                
                logger.debug(f"Creating LLM node with model: {model}")
                node = self.parent_widget.graph_manager.create_node(
                    'llm.LLMNode',
                    name='LLM'
                )
                
                if node:
                    # Set all available properties from the saved data
                    node.set_property('prompt', llm_data.get('prompt', ''))
                    node.set_property('model', llm_data.get('model', 'llama3.2:latest'))
                    node.set_property('system_message', llm_data.get('system_message', ''))
                    node.set_property('temperature', str(llm_data.get('temperature', 0.7)))
                    node.set_property('max_tokens', str(llm_data.get('max_tokens', 500)))
                    node.set_property('output_variable', llm_data.get('output_variable', 'llm_output'))
                    node.set_property('api_url', llm_data.get('api_url', ''))
                    node.set_property('write_text', str(llm_data.get('write_text', 'true')))
                    node.set_property('use_vision', str(llm_data.get('use_vision', 'false')))
                    node.set_property('screenshot_enabled', str(llm_data.get('screenshot_enabled', 'true')))
                    node.set_property('use_comorag', str(llm_data.get('use_comorag', 'true')))
                    node.set_property('max_cycles', str(llm_data.get('max_cycles', 5)))
                    node.set_property('memory_size', str(llm_data.get('memory_size', 1000)))
                    node.set_property('typing_batch_size', str(llm_data.get('typing_batch_size', 20)))
                    node.set_property('typing_batch_delay', str(llm_data.get('typing_batch_delay', 0.05)))
                    node.set_property('input_source', llm_data.get('input_source', 'none'))
                    node.set_property('ocr_confidence', str(llm_data.get('ocr_confidence', 0.5)))
                    node.set_property('ocr_preprocessing', str(llm_data.get('ocr_preprocessing', 'true')))
                    node.set_property('use_async', str(llm_data.get('use_async', 'true')))
                    
                    logger.debug(f"Set all properties for LLM node with model: {model}")
                    
                    # Set position
                    position = llm_data.get("position", [0, 0])
                    node.set_pos(*position)
                    logger.debug(f"Set position for LLM node: {position}")
                    
                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, 'llm_nodes'):
                        self.parent_widget.llm_nodes.append(node)
                        logger.debug(f"Added LLM node to parent widget tracking list")
                    # Set position
                    position = llm_data.get("position", [0, 0])
                    node.set_pos(*position)
                    logger.debug(f"Set position for LLM node: {position}")
                    
                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, 'llm_nodes'):
                        self.parent_widget.llm_nodes.append(node)
                        logger.debug(f"Added LLM node to parent widget tracking list")
                        
                    # Store in node map
                    node_id = llm_data.get("id") or llm_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added LLM node to node map: {node_id}")
                else:
                    logger.error(f"Failed to create LLM node with model: {model}")
        except Exception as e:
            logger.error(f"Error creating LLM nodes: {e}")
            raise
                
    def _create_tts_nodes(self, node_map):
        """Create TTS nodes from configuration."""
        logger.debug("Creating TTS nodes from configuration")
        try:
            tts_nodes = self.chain_config.get("tts_nodes", [])
            logger.debug(f"Found {len(tts_nodes)} TTS nodes to create")
            
            for tts_data in tts_nodes:
                text = tts_data.get('text', 'Hello, this is a text to speech message.')
                language = tts_data.get('language', 'en')
                
                logger.debug(f"Creating TTS node with text: {text[:30]}...")
                node = self.parent_widget.graph_manager.create_node(
                    'tts.TTSNode',
                    name='TTS'
                )
                
                if node:
                    # Set properties
                    node.set_property('text', text)
                    node.set_property('language', language)
                    logger.debug(f"Set properties for TTS node with text: {text[:30]}...")
                    
                    # Update node name to show preview of text
                    preview_text = text[:20] + "..." if len(text) > 20 else text
                    node.set_name(f"TTS: {preview_text}")
                    
                    # Set position
                    position = tts_data.get("position", [0, 0])
                    node.set_pos(*position)
                    logger.debug(f"Set position for TTS node: {position}")
                    
                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, 'tts_nodes'):
                        self.parent_widget.tts_nodes.append(node)
                        logger.debug(f"Added TTS node to parent widget tracking list")
                        
                    # Store in node map
                    node_id = tts_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added TTS node to node map: {node_id}")
                else:
                    logger.error(f"Failed to create TTS node with text: {text[:30]}...")
        except Exception as e:
            logger.error(f"Error creating TTS nodes: {e}")
            raise
                
    def _create_chain_import_nodes(self, node_map):
        """Create chain import nodes from configuration."""
        logger.debug("Creating chain import nodes from configuration")
        try:
            chain_import_nodes = self.chain_config.get("chain_import_nodes", [])
            logger.debug(f"Found {len(chain_import_nodes)} chain import nodes to create")
            
            for import_data in chain_import_nodes:
                file_path = import_data.get('chain_file_path', '')
                import_mode = import_data.get('import_mode', 'full')
                prefix = import_data.get('prefix', '')
                
                logger.debug(f"Creating chain import node with file: {file_path}")
                node = self.parent_widget.graph_manager.create_node(
                    'chain_import.ChainImportNode',
                    name='Chain Import'
                )
                
                if node:
                    # Set chain import data (this loads the chain configuration)
                    enabled = import_data.get('enabled', True)
                    if isinstance(enabled, str):
                        enabled = enabled.lower() == 'true'
                    
                    # Get loop_count and extra_delay from import_data
                    loop_count = import_data.get('loop_count', 1)
                    extra_delay = import_data.get('extra_delay', 0)
                    
                    if file_path:  # Only set data if we have a file path
                        node.set_chain_import_data(file_path, import_mode, prefix, loop_count, extra_delay, enabled)
                        logger.debug(f"Set chain import data for node: {file_path}")
                    else:
                        # Set properties manually if no file path
                        node.set_property('chain_file', file_path)
                        node.set_property('import_mode', import_mode)
                        node.set_property('prefix', prefix)
                        node.set_property('loop_count', str(loop_count))
                        node.set_property('extra_delay', str(extra_delay))
                        node.set_property('enabled', str(enabled).lower())
                        node.set_name("Import: No file")
                        logger.debug(f"Set properties for chain import node with no file path")
                    
                    # CRITICAL: If the imported chain configuration is already included in the saved data
                    # (from ChainExpansionDialog editing), restore it to the node
                    if (import_data.get('sequences') or import_data.get('conditional_nodes') or 
                        import_data.get('llm_nodes') or import_data.get('tts_nodes')):
                        
                        logger.debug(f"Restoring imported chain configuration for node {node.id}")
                        # Reconstruct the imported chain configuration
                        imported_config = {
                            "sequences": import_data.get('sequences', []),
                            "conditional_nodes": import_data.get('conditional_nodes', []),
                            "llm_nodes": import_data.get('llm_nodes', []),
                            "tts_nodes": import_data.get('tts_nodes', []),
                            "chain_import_nodes": import_data.get('chain_import_nodes', [])
                        }
                        # Set the reconstructed configuration on the node
                        node.chain_config = imported_config
                        logger.debug(f"Successfully restored imported chain configuration for node {node.id}")
                    
                    # Set position
                    position = import_data.get("position", [0, 0])
                    node.set_pos(*position)
                    logger.debug(f"Set position for chain import node: {position}")
                    
                    # Add to parent widget's tracking list
                    if hasattr(self.parent_widget, 'chain_import_nodes'):
                        self.parent_widget.chain_import_nodes.append(node)
                        logger.debug(f"Added chain import node to parent widget tracking list")
                        
                    # Store in node map
                    node_id = import_data.get("node_id", node.id)
                    node_map[node_id] = node
                    logger.debug(f"Added chain import node to node map: {node_id}")
                else:
                    logger.error(f"Failed to create chain import node with file: {file_path}")
        except Exception as e:
            logger.error(f"Error creating chain import nodes: {e}")
            raise
                
    def _restore_connections(self, node_map):
        """Restore connections between nodes."""
        logger.debug("Restoring connections between nodes")
        try:
            all_node_data = (
                self.chain_config.get("sequences", []) +
                self.chain_config.get("conditional_nodes", []) +
                self.chain_config.get("llm_nodes", []) +
                self.chain_config.get("tts_nodes", []) +
                self.chain_config.get("chain_import_nodes", [])
            )
            
            total_connections = sum(len(node_data.get("connections", [])) for node_data in all_node_data)
            logger.debug(f"Found {total_connections} connections to restore")
            
            connections_restored = 0
            for node_data in all_node_data:
                source_node_id = node_data.get("node_id")
                source_node = node_map.get(source_node_id)
                
                if not source_node:
                    logger.warning(f"Source node not found in node map: {source_node_id}")
                    continue
                    
                for connection in node_data.get("connections", []):
                    target_node_id = connection.get("target_node_id")
                    target_node = node_map.get(target_node_id)
                    
                    if target_node:
                        # Connect nodes
                        output_port = connection.get("output_port")
                        input_port = connection.get("input_port")
                        logger.debug(f"Connecting {source_node_id}:{output_port} -> {target_node_id}:{input_port}")
                        
                        self.parent_widget.graph_manager.connect_nodes(
                            source_node,
                            target_node,
                            output_port,
                            input_port
                        )
                        connections_restored += 1
                    else:
                        logger.warning(f"Target node not found in node map: {target_node_id}")
            
            logger.info(f"Successfully restored {connections_restored} connections")
        except Exception as e:
            logger.error(f"Error restoring connections: {e}")
            raise