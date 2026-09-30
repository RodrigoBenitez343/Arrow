from PyQt5.QtWidgets import QMessageBox, QInputDialog, QLineEdit
try:
    from ...dialogs.chain_import_dialogs import ChainImportDialog
except ImportError:
    # Handle potential import error if the module structure is different
    from ...dialogs import ChainImportDialog

from .utils import get_logger
import os
import json

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

    def wrap_selected_nodes_into_chain(self, selected_nodes, target_chain_import_node):
        """Wrap the selected nodes into a new chain JSON file and configure the
        target Chain Import node to reference it.  The selected nodes are removed
        from the main graph after being saved."""
        from ...nodes_resources.chain_import_node import ChainImportNode
        from ...nodes_resources.sequence_node import SequenceNode
        from ...nodes_resources.conditional_node import ConditionalNode
        from ...nodes_resources.llm_node import LLMNode
        from ...nodes_resources.code_node import CodeNode
        from ...nodes_resources.context_node import ContextNode
        try:
            from ...nodes_resources.container_node import ContainerNode
        except ImportError:
            ContainerNode = None

        logger.info(f"Wrapping {len(selected_nodes)} nodes into chain")

        # Exclude the target chain import node itself and any chain import nodes
        nodes_to_wrap = []
        for n in selected_nodes:
            if n is target_chain_import_node:
                continue
            if isinstance(n, ChainImportNode):
                continue  # don't nest chain imports
            nodes_to_wrap.append(n)

        if not nodes_to_wrap:
            QMessageBox.warning(
                self.parent_widget,
                "Wrap in Chain",
                "No valid nodes selected to wrap.  Chain Import nodes cannot be nested."
            )
            return False

        # --- Ask the user for a name and description ---
        chain_name, ok = QInputDialog.getText(
            self.parent_widget,
            "New Chain Name",
            "Enter a file name for the new chain (without .json):",
            QLineEdit.Normal,
            ""
        )
        if not ok or not chain_name.strip():
            return False
        chain_name = chain_name.strip()

        chain_desc, ok = QInputDialog.getText(
            self.parent_widget,
            "Chain Description",
            "Enter a description for this chain:",
            QLineEdit.Normal,
            ""
        )
        if not ok:
            return False
        chain_desc = chain_desc.strip()

        # --- Build the chain configuration from the selected nodes ---
        config_manager = self.parent_widget.config_manager
        # Merge EVERY node-category list the config manager produced.  A
        # hardcoded category list here silently excluded whole node types
        # (web sequences, input / orchestrator / handle / mcp / output nodes)
        # from the wrapped chain while their nodes were still deleted from
        # the graph - the nodes vanished without ever reaching the bundle.
        merged_config = {"description": chain_desc}
        wrapped_nodes = []
        for node in nodes_to_wrap:
            single_cfg = config_manager.get_single_node_config(node)
            added = 0
            for key, value in (single_cfg or {}).items():
                # Node categories are lists; scalars (is_default, ...) are
                # chain-level state, not node data.
                if not isinstance(value, list):
                    continue
                merged_config.setdefault(key, []).extend(value)
                added += len(value)
            if added:
                wrapped_nodes.append(node)
            else:
                # Never delete a node that contributed nothing to the chain:
                # unsupported / unexportable node types stay in the graph.
                logger.warning(
                    f"No configuration extracted for node {node.id} "
                    f"({type(node).__name__}) - keeping it in the graph"
                )

        total_wrapped = sum(
            len(v) for v in merged_config.values() if isinstance(v, list)
        )
        if total_wrapped == 0:
            QMessageBox.warning(
                self.parent_widget,
                "Wrap in Chain",
                "Could not extract configuration from the selected nodes."
            )
            return False

        # --- Save the chain JSON file ---
        chains_folder = self.parent_widget.chains_folder
        safe_name = "".join(c for c in chain_name if c.isalnum() or c in " _-").rstrip()
        if not safe_name:
            safe_name = "wrapped_chain"
        chain_file_path = os.path.join(chains_folder, f"{safe_name}.json")

        # Avoid overwriting an existing file – append a number if needed
        counter = 2
        base_path = chain_file_path
        while os.path.exists(chain_file_path):
            chain_file_path = os.path.join(chains_folder, f"{safe_name}_{counter}.json")
            counter += 1

        try:
            with open(chain_file_path, "w", encoding="utf-8") as f:
                json.dump(merged_config, f, indent=2)
            logger.info(f"Saved wrapped chain to: {chain_file_path}")
        except Exception as e:
            logger.error(f"Failed to save wrapped chain: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to save chain file: {str(e)}"
            )
            return False

        # --- Remove the wrapped nodes from the graph ---
        for node in list(wrapped_nodes):
            try:
                self.parent_widget.graph_manager.delete_node(node)
            except Exception as e:
                logger.error(f"Error deleting wrapped node {node.id}: {e}")

        # --- Configure the target Chain Import node with the new chain ---
        prefix = safe_name
        target_chain_import_node.set_chain_import_data(
            chain_file_path,
            import_mode='full',
            prefix=prefix,
            loop_count=1,
            extra_delay=0.0,
            enabled=True
        )

        skipped = len(nodes_to_wrap) - len(wrapped_nodes)
        message = (
            f"Successfully wrapped {len(wrapped_nodes)} node(s) into chain:\n"
            f"{safe_name}.json"
        )
        if skipped:
            message += (
                f"\n\n{skipped} node(s) had no exportable configuration and "
                f"were kept in the graph."
            )
        QMessageBox.information(
            self.parent_widget,
            "Chain Created",
            message
        )
        logger.info(
            f"Wrap complete: {len(wrapped_nodes)} nodes -> {chain_file_path}"
        )
        return True
        
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
                node.set_property('emit_data', 'true' if config.get('emit_data') else 'false')
                try:
                    node.set_property('data_output_nodes', json.dumps(config.get('data_output_nodes') or []))
                except Exception:
                    node.set_property('data_output_nodes', '[]')
                
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
            # Legacy embedded TTS nodes become audio Output nodes on import.
            try:
                from ....player.chain_migrations import migrate_legacy_tts
            except ImportError:
                from LoOper.player.chain_migrations import migrate_legacy_tts
            migrate_legacy_tts(chain_config)
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
            
            if import_mode in ['full', 'sequences_only']:
                # Import web sequences with full configuration
                web_sequences = chain_config.get('web_sequences', [])
                for ws_config in web_sequences:
                    ws_name = ws_config.get('name', 'Imported Web Sequence')
                    if prefix:
                        ws_name = f"{prefix}_{ws_name}"
            
                    ws_node = self.parent_widget.graph_manager.create_node(
                        'web_sequence.WebSequenceNode',
                        name=ws_name
                    )
                    if ws_node:
                        session_file = ws_config.get('session_file', '') or ''
                        if ": " in ws_name and not session_file:
                            session_file = ws_name.split(": ", 1)[1]
            
                        ws_node.set_property('session_file', session_file)
                        try:
                            ws_node.set_web_sequence_data(0, {
                                'session_file': session_file,
                                'headless': ws_config.get('headless', False),
                                'speed': ws_config.get('speed', 1.0),
                                'native_actions': ws_config.get('native_actions', True),
                                'loop_count': ws_config.get('loop_count', 1),
                                'extra_delay': ws_config.get('extra_delay', 1.0),
                                'extract_items': ws_config.get('extract_items', []),
                            })
                        except Exception:
                            pass
            
                        position = ws_config.get('position', [0, 0])
                        ws_node.set_pos(*position)
            
                        original_id = ws_config.get('node_id')
                        if original_id:
                            node_map[original_id] = ws_node
            
                        imported_nodes.append(ws_node)
                        logger.debug(f"Imported web sequence node: {ws_name}")
            
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
                        
                        # Morph ports for file-loop conditionals: single 'output'
                        # port (loop continuation) instead of true/false branches.
                        try:
                            cond_node.update_loop_port_mode()
                        except Exception:
                            pass
                        
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
                        # Apply the FULL LLM configuration (rag_embedding_model,
                        # use_context_consolidation + consolidation_*, llamacpp
                        # settings, skills, ...) — the previous minimal copy
                        # silently dropped everything except prompt/model/
                        # temperature/max_tokens.
                        _import_cfg = dict(llm_config)
                        # Mirrors the executor's merge: an llm_configuration
                        # sub-dict (if present) overrides top-level fields.
                        _import_cfg.update(llm_config.get("llm_configuration") or {})
                        llm_node.set_llm_config(_import_cfg)
                        # Restore the import naming convention (set_llm_config
                        # renames the node to "LLM: <model>").
                        llm_node.set_name(llm_name)

                        # Set position if available
                        position = llm_config.get('position', [0, 0])
                        llm_node.set_pos(*position)

                        # Map original node ID to new node
                        original_id = llm_config.get('node_id')
                        if original_id:
                            node_map[original_id] = llm_node

                        imported_nodes.append(llm_node)
                        logger.debug(f"Imported LLM node: {llm_name}")
                        
                # Import Output nodes with full configuration (legacy standalone
                # TTS nodes arrive here as audio-mode Output nodes via migration)
                output_nodes = chain_config.get('output_nodes', [])
                for output_config in output_nodes:
                    out_name = output_config.get('name', 'Imported Output')
                    if prefix:
                        out_name = f"{prefix}_{out_name}"

                    out_node = self.parent_widget.graph_manager.create_node(
                        'output.OutputNode',
                        name=out_name
                    )
                    if out_node:
                        for prop in ('label', 'variable_name', 'agent_visible',
                                     'overlay_visible',
                                     'popup_on_finish',
                                     'show_rating', 'render_mode', 'tts_enabled',
                                     'tts_text', 'tts_language', 'tts_voice_model',
                                     'tts_speed', 'tts_speaker_id', 'tts_wait',
                                     'image_source'):
                            if prop in output_config:
                                out_node.set_property(prop, output_config.get(prop))

                        # Set position if available
                        position = output_config.get('position', [0, 0])
                        out_node.set_pos(*position)

                        # Map original node ID to new node
                        original_id = output_config.get('node_id')
                        if original_id:
                            node_map[original_id] = out_node

                        imported_nodes.append(out_node)
                        logger.debug(f"Imported Output node: {out_name}")
            
            # Restore connections between imported nodes
            self._restore_imported_connections(chain_config, node_map, import_mode)
            
            # Auto-layout the imported nodes if no positions were set
            if imported_nodes and not any(node_config.get('position') for node_config in 
                                        chain_config.get('sequences', []) + 
                                        chain_config.get('conditional_nodes', []) + 
                                        chain_config.get('llm_nodes', []) + 
                                        chain_config.get('output_nodes', [])):
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
                all_node_data.extend(chain_config.get('output_nodes', []))
            
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
                        # File-loop conditionals expose a single 'output' port;
                        # legacy saves may still reference 'true'/'false' —
                        # normalize so the loop continuation restores correctly.
                        try:
                            if (
                                output_port in ('true', 'false')
                                and hasattr(source_node, 'is_file_loop_conditional')
                                and source_node.is_file_loop_conditional()
                            ):
                                output_port = 'output'
                        except Exception:
                            pass
                        logger.debug(f"Connecting imported nodes {source_node_id}:{output_port} -> {target_node_id}:{input_port}")
                        
                        try:
                            # Stale named ports must be SKIPPED:
                            # graph_manager.connect_nodes silently falls back
                            # to output port #0 (the execution port) when the
                            # named port is missing, which would mis-wire.
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
