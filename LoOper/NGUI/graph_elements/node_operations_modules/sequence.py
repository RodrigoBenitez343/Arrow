import json
import os
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QFileDialog
from ...dialogs.sequence_dialogs import SequencePropertiesDialog
from .utils import get_logger

logger = get_logger(__name__)

class SequenceOperationsMixin:
    def remove_sequence_from_chain(self, sequence_node):
        """Remove a sequence from the chain configuration."""
        logger.info(f"Removing sequence from chain: {sequence_node.name() if sequence_node else 'None'}")
        try:
            if not sequence_node:
                logger.warning("No sequence node provided for removal")
                return
                
            # Get sequence name from the node
            sequence_name = sequence_node.get_property('sequence_name') or sequence_node.name()
            logger.debug(f"Sequence name to remove: {sequence_name}")
            
            # Remove from chain config using config manager
            logger.debug("Removing sequence from chain config")
            self.parent_widget.config_manager.remove_sequence_from_chain(sequence_name)
            
            # Delete the node from the graph
            logger.debug("Deleting sequence node from graph")
            self.parent_widget.graph_manager.delete_node(sequence_node)
            
            logger.info(f"Successfully removed sequence from chain: {sequence_name}")
            
        except Exception as e:
            logger.error(f"Error removing sequence from chain: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to remove sequence from chain: {str(e)}"
            )
            raise

    def add_sequence(self, pos=None):
        """Add a previously recorded sequence node to the graph."""
        logger.info(f"Adding sequence node at position: {pos}")
        try:
            # Open file dialog to select a sequence file from the sequences folder
            logger.debug(f"Opening file dialog for sequence selection from: {self.parent_widget.sequences_folder}")
            file_path, selected_filter = QFileDialog.getOpenFileName(
                self.parent_widget,
                "Select Sequence File",
                self.parent_widget.sequences_folder,  # Default to sequences folder
                "JSON Files (*.json)"
            )
            
            if file_path:
                logger.debug(f"Selected sequence file: {file_path}")
                # Extract sequence name from file path
                sequence_name = os.path.splitext(os.path.basename(file_path))[0]
                logger.debug(f"Extracted sequence name: {sequence_name}")
                
                # Create the node with just the filename to prevent automatic numbering
                # that would break sequence file references (e.g., test.json should stay test.json)
                logger.debug("Creating sequence node")
                sequence_filename = os.path.basename(file_path)  # Use full filename including .json
                node = self.parent_widget.graph_manager.create_node(
                    'sequence.SequenceNode', 
                    name=sequence_filename, 
                    pos=pos
                )
                
                if node:
                    logger.debug(f"Setting properties for sequence node {node.id}")
                    # CRITICAL: Store only the filename in sequence_file property to prevent path issues
                    # The player should look for sequences by filename only, not full paths
                    sequence_filename_only = os.path.basename(file_path)
                    node.set_property('sequence_file', sequence_filename_only)
                    
                    # Use the set_sequence_data method like the old implementation
                    sequence_config = {
                        'sequence_file': sequence_filename_only,  # Use filename only
                        'loop_count': 1,
                        'extra_delay': 1.0
                    }
                    node.set_sequence_data(0, sequence_config)
                    
                    logger.info(f"Successfully added sequence: {sequence_filename_only} (from {file_path})")
                    return node
                else:
                    logger.error("Failed to create sequence node")
            else:
                logger.debug("No file selected for sequence")
                    
        except Exception as e:
            logger.error(f"Error adding sequence: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add sequence: {str(e)}"
            )
            
        return None
        
    def add_new_sequence(self, pos=None):
        """Add a new sequence node (for creating new sequences)."""
        logger.info(f"Adding new sequence node at position: {pos}")
        try:
            # Get sequence name from user
            logger.debug("Prompting user for sequence name")
            name, ok = QInputDialog.getText(
                self.parent_widget, 
                'Add New Sequence', 
                'Enter sequence name:'
            )
            
            if ok and name:
                logger.debug(f"User entered sequence name: {name}")
                # Create the node with .json extension to maintain consistency
                # and prevent automatic numbering that breaks file references
                logger.debug("Creating new sequence node")
                sequence_filename = f"{name}.json"  # Ensure .json extension for consistency
                node = self.parent_widget.graph_manager.create_node(
                    'sequence.SequenceNode', 
                    name=sequence_filename, 
                    pos=pos
                )
                
                if node:
                    logger.debug(f"Creating sequence file for: {name}")
                    # Create sequence file first
                    sequence_file_path = self._create_sequence_file(name)
                    
                    # Set node properties using the proper method
                    logger.debug(f"Setting sequence data for node {node.id}")
                    sequence_config = {
                        'sequence_file': sequence_file_path,
                        'loop_count': 1,
                        'extra_delay': 1.0
                    }
                    node.set_sequence_data(0, sequence_config)
                    
                    logger.info(f"Successfully added new sequence: {name}")
                    return node
                else:
                    logger.error("Failed to create new sequence node")
            else:
                logger.debug("User cancelled sequence name input")
                    
        except Exception as e:
            logger.error(f"Error adding new sequence: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add new sequence: {str(e)}"
            )
        
    def add_sequence_from_file(self, file_path: str, pos=None):
        try:
            if not file_path:
                return None
            sequence_filename = os.path.basename(file_path)
            node = self.parent_widget.graph_manager.create_node(
                'sequence.SequenceNode',
                name=sequence_filename,
                pos=pos
            )
            if node:
                sequence_filename_only = os.path.basename(file_path)
                node.set_property('sequence_file', sequence_filename_only)
                sequence_config = {
                    'sequence_file': sequence_filename_only,
                    'loop_count': 1,
                    'extra_delay': 1.0
                }
                node.set_sequence_data(0, sequence_config)
                return node
            return None
        except Exception as e:
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to add sequence: {str(e)}"
            )
            return None
        return None

    def delete_sequence_node(self, node):
        """Remove a sequence node from the graph and the chain config.

        The recorded sequence FILE is kept on disk: deleting the node removes it
        from the graph only (the same rule as the drop-to-delete zone), so a
        recording is never lost by accident. The node stores the file in the
        ``sequence_file`` property, not ``sequence_name`` - reading the wrong
        property is what produced the bogus "Sequence file not found" warnings
        and left the chain entry behind.
        """
        logger.info(f"Deleting sequence node: {node.id}")
        try:
            sequence_file = node.get_property('sequence_file') or ''
            sequence_name = os.path.basename(sequence_file) if sequence_file else node.name()
            logger.debug(f"Sequence file: {sequence_file} (name: {sequence_name})")

            # Best-effort: drop the matching entry from the chain config.
            try:
                self.parent_widget.config_manager.remove_sequence_from_chain(
                    sequence_name)
            except Exception as e:
                logger.debug(f"remove_sequence_from_chain skipped: {e}")

            # Delete the node from the graph (not the file).
            logger.debug("Deleting sequence node from graph")
            self.parent_widget.graph_manager.delete_node(node)
            logger.info(f"Successfully deleted sequence node: {sequence_name}")

        except Exception as e:
            logger.error(f"Error deleting sequence node: {e}", exc_info=True)
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to delete sequence node: {str(e)}"
            )

    def edit_sequence(self, node):
        """Edit a Sequence node's properties via dialog."""
        try:
            logger.info(f"Editing Sequence node: {node.id}")
            # Load current configuration
            current_config = {}
            if hasattr(node, 'get_sequence_config'):
                current_config = node.get_sequence_config() or {}
            else:
                current_config = {
                    'sequence_file': node.get_property('sequence_file') or '',
                    'loop_count': int(node.get_property('loop_count') or 1),
                    'extra_delay': float(node.get_property('extra_delay') or 1.0),
                }

            dialog = SequencePropertiesDialog(current_config, self.parent_widget)
            if dialog.exec_() == dialog.Accepted:
                props = dialog.get_properties()
                logger.debug(f"Sequence dialog accepted with properties: {props}")
                node.set_property('loop_count', str(props.get('loop_count', 1)))
                node.set_property('extra_delay', str(props.get('extra_delay', 1.0)))
                node.set_property('use_app_opened', 'true' if props.get('use_app_opened', True) else 'false')
                node.set_property('click_drift_min', str(props.get('click_drift_min', 5.0)))
                node.set_property('click_drift_max', str(props.get('click_drift_max', 10.0)))
                node.set_property('description', str(props.get('description', '') or ''))
                logger.info(f"Successfully edited Sequence node: {node.id}")
            else:
                logger.debug("Sequence dialog cancelled")
        except Exception as e:
            logger.error(f"Error editing Sequence node: {e}")
            QMessageBox.critical(
                self.parent_widget,
                "Error",
                f"Failed to edit sequence node: {str(e)}"
            )

    def _create_sequence_file(self, sequence_name):
        """Create a new sequence file."""
        logger.info(f"Creating sequence file for: {sequence_name}")
        try:
            sequence_file = os.path.join(
                self.parent_widget.sequences_folder, 
                f"{sequence_name}.json"
            )
            logger.debug(f"Sequence file path: {sequence_file}")
            
            if not os.path.exists(sequence_file):
                logger.debug("Creating new sequence file")
                # Create sequence data in the format matching the example (7.json)
                sequence_data = {
                    "metadata": {
                        "created_at": "",
                        "total_actions": 0,
                        "duration_sec": 0.0,
                        "mode": "desktop_only"
                    },
                    "actions": []
                }
                
                with open(sequence_file, 'w') as f:
                    json.dump(sequence_data, f, indent=2)
                
                logger.info(f"Successfully created sequence file: {sequence_file}")
            else:
                logger.debug(f"Sequence file already exists: {sequence_file}")
                
            return sequence_file
        except Exception as e:
            logger.error(f"Error creating sequence file for {sequence_name}: {e}")
            raise

    def record_new_sequence(self):
        """Start recording a new sequence"""
        logger.info("Starting new sequence recording")
        try:
            # Get the main window to trigger recording
            logger.debug("Finding main window for recording")
            main_window = self.parent_widget.parent()
            while main_window and not hasattr(main_window, 'record_sequence'):
                main_window = main_window.parent()
            
            if main_window and hasattr(main_window, 'record_sequence'):
                logger.debug("Found main window, starting recording")
                main_window.record_sequence()
                logger.info("Successfully started sequence recording")
            else:
                logger.error("Could not find main window to start recording")
                print("Could not find main window to start recording")
        except Exception as e:
            logger.error(f"Error starting sequence recording: {e}")
            raise
