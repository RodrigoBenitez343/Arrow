from PyQt5.QtWidgets import QMessageBox
try:
    from ...dialogs.chain_import_dialogs import ChainImportDialog
except ImportError:
    # Handle potential import error if the module structure is different
    from ...dialogs import ChainImportDialog

from .utils import get_logger
import os

logger = get_logger(__name__)

class ChainImportOperationsMixin:
    def add_chain_import_node(self, pos=None):
        """Add a new Chain Import node."""
        logger.info(f"Adding Chain Import node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating Chain Import node")
            node = self.parent_widget.graph_manager.create_node(
                'chain_import.ChainImportNode', 
                name='Chain Import', 
                pos=pos
            )
            
            if node:
                logger.debug(f"Setting default properties for Chain Import node {node.id}")
                # Default properties are set in the ChainImportNode class
                # We don't need to set them here as they're already configured in the node class
                
                logger.info(f"Successfully added Chain Import node {node.id}")
                return node
            else:
                logger.error("Failed to create Chain Import node")
                
        except Exception as e:
            logger.error(f"Error adding Chain Import node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add Chain Import node: {str(e)}"
            )
            
        return None

    def add_chain_import_from_file(self, file_path, pos=None):
        """Add a Chain Import node pre-configured with a specific chain file."""
        logger.info(f"Adding Chain Import node from file: {file_path} at position: {pos}")
        try:
            node = self.add_chain_import_node(pos)
            if node:
                # Extract file name without extension as default prefix
                file_name = os.path.basename(file_path)
                prefix = os.path.splitext(file_name)[0]
                
                # Update the node with the chain file
                node.set_chain_import_data(
                    file_path,
                    import_mode='full',
                    prefix=prefix,
                    loop_count=1,
                    extra_delay=0.0,
                    enabled=True
                )
                logger.info(f"Successfully configured Chain Import node {node.id} with {file_path}")
                return node
        except Exception as e:
            logger.error(f"Error adding Chain Import from file: {e}")
        return None
        
    def edit_chain_import_node(self, node):
        """Edit a Chain Import node configuration."""
        logger.info(f"Editing Chain Import node: {node.name()}")
        try:
            # Get current configuration
            current_config = node.get_chain_import_config()
            
            # Show dialog
            dialog = ChainImportDialog(current_config, self.parent_widget)
            
            if dialog.exec_() == dialog.Accepted:
                config = dialog.get_config()
                logger.debug(f"New Chain Import configuration: {config}")
                
                # Update the node with new configuration
                node.set_chain_import_data(
                    config['chain_file'],
                    config['import_mode'],
                    config['prefix'],
                    config['loop_count'],
                    config['extra_delay'],
                    config['enabled']
                )
                node.set_property('run_in_sandbox', 'true' if config.get('run_in_sandbox') else 'false')
                node.set_property('show_sandbox_window', 'true' if config.get('show_sandbox_window', True) else 'false')
                
                logger.info(f"Successfully updated Chain Import node {node.id}")
                
        except Exception as e:
            logger.error(f"Error editing Chain Import node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to edit Chain Import node: {str(e)}"
            )
            
    def expand_chain_import(self, node):
        """Expand a Chain Import node by importing its nodes into the current graph."""
        logger.info(f"Expanding Chain Import node: {node.name()}")
        try:
            # Get the chain configuration from the node
            chain_config = node.get_imported_chain_config()
            import_config = node.get_chain_import_config()
            
            if not chain_config:
                QMessageBox.warning(
                    self.parent_widget,
                    "Warning",
                    "No chain configuration loaded. Please configure the chain import first."
                )
                return
                
            # Get import settings
            import_mode = import_config.get('import_mode', 'full')
            prefix = import_config.get('prefix', '')
            
            # Create a node mapping for connection restoration
            node_map = {}
            imported_nodes = []
            
            if import_mode in ['full', 'sequences_only']:
                # Import sequences with full configuration
                sequences = chain_config.get('sequences', [])
                for seq_config in sequences:
                    seq_name = seq_config.get('name', 'Imported Sequence')
                    if prefix:
                        seq_name = f"{prefix}_{seq_name}"
                    
                    # Create sequence node
                    seq_node = self.parent_widget.graph_manager.create_node(
                        'sequence.SequenceNode',
                        name=seq_name
                    )
                    if seq_node:
                        # Extract sequence file from name (format: "Seq X: filename.json")
                        if ": " in seq_name:
                            sequence_file = seq_name.split(": ", 1)[1]
                        else:
                            sequence_file = seq_name
                        
                        # Create proper sequence configuration
                        sequence_config = {
                            'sequence_file': os.path.join(self.parent_widget.sequences_folder, sequence_file),
                            'loop_count': 1,
                            'extra_delay': 1.0
                        }
                        
                        # Set sequence configuration with proper parameters
                        seq_node.set_sequence_data(
                            0,  # sequence_idx
                            sequence_config,  # sequence_config dict
                            {}  # action_fallbacks
                        )
                        
                        # Set position if available
                        position = seq_config.get('position', [0, 0])
                        seq_node.set_pos(*position)
                        
                        # Map original node ID to new node
                        original_id = seq_config.get('node_id')
                        if original_id:
                            node_map[original_id] = seq_node
                        
                        imported_nodes.append(seq_node)
                        logger.debug(f"Imported sequence node: {seq_name}")
                        
            if import_mode in ['full', 'nodes_only']:
                # Import conditional nodes with full configuration
                conditionals = chain_config.get('conditional_nodes', [])
                for cond_config in conditionals:
                    cond_name = cond_config.get('name', 'Imported Conditional')
                    if prefix:
                        cond_name = f"{prefix}_{cond_name}"
                    
                    cond_node = self.parent_widget.graph_manager.create_node(
                        'conditional.ConditionalNode',
                        name=cond_name
                    )
                    if cond_node:
                        # Set conditional configuration using set_property method
                        cond_node.set_property('condition_type', cond_config.get('condition_type', 'presence'))
                        cond_node.set_property('image_path', cond_config.get('image_path', ''))
                        cond_node.set_property('threshold', str(cond_config.get('threshold', 0.8)))
                        cond_node.set_property('wait_time', str(cond_config.get('wait_time', 5)))
                        cond_node.set_property('max_loops', str(cond_config.get('max_loops', 10)))
                        cond_node.set_property('ocr_text', cond_config.get('ocr_text', ''))
                        cond_node.set_property('code', cond_config.get('code', ''))
                        cond_node.set_property('timeout', str(cond_config.get('timeout', 5.0)))
                        cond_node.set_property('max_attempts', str(cond_config.get('max_attempts', 3)))
                        # Note: delay_between_attempts not used for layout match - runs at max speed
                        if cond_config.get('target_x') is not None and cond_config.get('target_y') is not None and cond_config.get('target_w') is not None and cond_config.get('target_h') is not None:
                            cond_node.set_property('target_x', str(cond_config.get('target_x')))
                            cond_node.set_property('target_y', str(cond_config.get('target_y')))
                            cond_node.set_property('target_w', str(cond_config.get('target_w')))
                            cond_node.set_property('target_h', str(cond_config.get('target_h')))
                            cond_node.set_property('position_tolerance', str(cond_config.get('position_tolerance', 6)))
                        else:
                            cond_node.set_property('target_x', '')
                            cond_node.set_property('target_y', '')
                            cond_node.set_property('target_w', '')
                            cond_node.set_property('target_h', '')
                            cond_node.set_property('position_tolerance', '6')
                        cond_node.set_property('loop_type', cond_config.get('loop_type', ''))
                        cond_node.set_property('loop_position', cond_config.get('loop_position', ''))
                        cond_node.set_property('sequence_file', cond_config.get('sequence_file', ''))
                        cond_node.set_property('iteration_delay', str(cond_config.get('iteration_delay', 1.0)))
                        cond_node.set_property('loop_condition', cond_config.get('loop_condition', ''))
                        cond_node.set_property('case_sensitive', cond_config.get('case_sensitive', 'true'))
                        
                        # Set position if available
                        position = cond_config.get('position', [0, 0])
                        cond_node.set_pos(*position)
                        
                        # Map original node ID to new node
                        original_id = cond_config.get('node_id')
                        if original_id:
                            node_map[original_id] = cond_node
                        
                        imported_nodes.append(cond_node)
                        logger.debug(f"Imported conditional node: {cond_name}")
                        
                # Import LLM nodes with full configuration
                llm_nodes = chain_config.get('llm_nodes', [])
                for llm_config in llm_nodes:
                    llm_name = llm_config.get('name', 'Imported LLM')
                    if prefix:
                        llm_name = f"{prefix}_{llm_name}"
                    
                    llm_node = self.parent_widget.graph_manager.create_node(
                        'llm.LLMNode',
                        name=llm_name
                    )
                    if llm_node:
                        # Set LLM properties using set_property method
                        llm_node.set_property('prompt', llm_config.get('prompt', ''))
                        llm_node.set_property('model', llm_config.get('model', 'gpt-3.5-turbo'))
                        llm_node.set_property('temperature', str(llm_config.get('temperature', 0.7)))
                        llm_node.set_property('max_tokens', str(llm_config.get('max_tokens', 150)))
                        
                        # Set position if available
                        position = llm_config.get('position', [0, 0])
                        llm_node.set_pos(*position)
                        
                        # Map original node ID to new node
                        original_id = llm_config.get('node_id')
                        if original_id:
                            node_map[original_id] = llm_node
                        
                        imported_nodes.append(llm_node)
                        logger.debug(f"Imported LLM node: {llm_name}")
                        
                # Import TTS nodes with full configuration
                tts_nodes = chain_config.get('tts_nodes', [])
                for tts_config in tts_nodes:
                    tts_name = tts_config.get('name', 'Imported TTS')
                    if prefix:
                        tts_name = f"{prefix}_{tts_name}"
                    
                    tts_node = self.parent_widget.graph_manager.create_node(
                        'tts.TTSNode',
                        name=tts_name
                    )
                    if tts_node:
                        # Set TTS properties using set_property method
                        tts_node.set_property('text', tts_config.get('text', 'Hello, this is a text to speech message.'))
                        tts_node.set_property('language', tts_config.get('language', 'en'))
                        
                        # Set position if available
                        position = tts_config.get('position', [0, 0])
                        tts_node.set_pos(*position)
                        
                        # Map original node ID to new node
                        original_id = tts_config.get('node_id')
                        if original_id:
                            node_map[original_id] = tts_node
                        
                        imported_nodes.append(tts_node)
                        logger.debug(f"Imported TTS node: {tts_name}")
            
            # Restore connections between imported nodes
            self._restore_imported_connections(chain_config, node_map, import_mode)
            
            # Auto-layout the imported nodes if no positions were set
            if imported_nodes and not any(node_config.get('position') for node_config in 
                                        chain_config.get('sequences', []) + 
                                        chain_config.get('conditional_nodes', []) + 
                                        chain_config.get('llm_nodes', []) + 
                                        chain_config.get('tts_nodes', [])):
                self.parent_widget.auto_layout_nodes()
                
            QMessageBox.information(
                self.parent_widget,
                "Success",
                f"Successfully imported {len(imported_nodes)} nodes from chain."
            )
            
            logger.info(f"Successfully expanded Chain Import node, imported {len(imported_nodes)} nodes")
            
        except Exception as e:
            logger.error(f"Error expanding Chain Import node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to expand Chain Import node: {str(e)}"
            )
    
    def _restore_imported_connections(self, chain_config, node_map, import_mode):
        """Restore connections between imported nodes."""
        logger.debug("Restoring connections for imported nodes")
        try:
            # Collect all node data based on import mode
            all_node_data = []
            
            if import_mode in ['full', 'sequences_only']:
                all_node_data.extend(chain_config.get('sequences', []))
                
            if import_mode in ['full', 'nodes_only']:
                all_node_data.extend(chain_config.get('conditional_nodes', []))
                all_node_data.extend(chain_config.get('llm_nodes', []))
                all_node_data.extend(chain_config.get('tts_nodes', []))
            
            connections_restored = 0
            for node_data in all_node_data:
                source_node_id = node_data.get('node_id')
                source_node = node_map.get(source_node_id)
                
                if not source_node:
                    logger.warning(f"Source node not found in node map: {source_node_id}")
                    continue
                    
                for connection in node_data.get('connections', []):
                    target_node_id = connection.get('target_node_id')
                    target_node = node_map.get(target_node_id)
                    
                    if target_node:
                        # Connect nodes
                        output_port = connection.get('output_port')
                        input_port = connection.get('input_port')
                        logger.debug(f"Connecting imported nodes {source_node_id}:{output_port} -> {target_node_id}:{input_port}")
                        
                        try:
                            self.parent_widget.graph_manager.connect_nodes(
                                source_node,
                                target_node,
                                output_port,
                                input_port
                            )
                            connections_restored += 1
                        except Exception as e:
                            logger.warning(f"Failed to connect {source_node_id}:{output_port} -> {target_node_id}:{input_port}: {e}")
                    else:
                        logger.warning(f"Target node not found in node map: {target_node_id}")
            
            logger.info(f"Successfully restored {connections_restored} connections for imported nodes")
            
        except Exception as e:
            logger.error(f"Error restoring imported connections: {e}")
            raise
