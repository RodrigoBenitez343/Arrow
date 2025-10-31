import json
import logging
import os
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QFileDialog
from ..dialogs import AdvancedConditionalDialog, LLMPropertiesDialog, TTSPropertiesDialog
from ..dialogs.chain_import_dialogs import ChainImportDialog
from ..nodes import SequenceNode, ActionNode, ConditionalNode, LLMNode, TTSNode, ChainImportNode  # ContextBuilderNode deprecated

# Configure logging
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


class NodeOperations:
    """Handles all node operations including adding, deleting, and editing."""
    
    def __init__(self, parent_widget):
        logger.info("Initializing NodeOperations")
        try:
            self.parent_widget = parent_widget
            logger.debug(f"NodeOperations initialized with parent_widget: {type(parent_widget).__name__}")
        except Exception as e:
            logger.error(f"Error initializing NodeOperations: {e}")
            raise
    
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
        
    def show_custom_context_menu(self, name, pos):
        """Handle custom context menu actions."""
        logger.info(f"Handling custom context menu action: {name} at position {pos}")
        try:
            if name == "Add Sequence":
                logger.debug("Adding sequence from context menu")
                self.add_sequence(pos)
            elif name == "Add Action":
                logger.debug("Adding action from context menu")
                self.add_action(pos)
            elif name == "Add Conditional":
                logger.debug("Adding conditional node from context menu")
                self.add_conditional_node(pos)
            elif name == "Add LLM":
                logger.debug("Adding LLM node from context menu")
                self.add_llm_node(pos)
            elif name == "Add TTS":
                logger.debug("Adding TTS node from context menu")
                self.add_tts_node(pos)
            else:
                logger.warning(f"Unknown context menu action: {name}")
        except Exception as e:
            logger.error(f"Error handling context menu action {name}: {e}")
            raise
            
    def add_sequence(self, pos=None):
        """Add a previously recorded sequence node to the graph."""
        logger.info(f"Adding sequence node at position: {pos}")
        try:
            # Open file dialog to select a sequence file from the sequences folder
            logger.debug(f"Opening file dialog for sequence selection from: {self.parent_widget.sequences_folder}")
            file_path, _ = QFileDialog.getOpenFileName(
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
            
        return None
        
    def add_action(self, pos=None):
        """Add a new action node."""
        logger.info(f"Adding action node at position: {pos}")
        try:
            # Get action name from user
            logger.debug("Prompting user for action name")
            name, ok = QInputDialog.getText(
                self.parent_widget, 
                'Add Action', 
                'Enter action name:'
            )
            
            if ok and name:
                logger.debug(f"User entered action name: {name}")
                # Create the node
                logger.debug("Creating action node")
                node = self.parent_widget.graph_manager.create_node(
                    'action.ActionNode', 
                    name=name, 
                    pos=pos
                )
                
                if node:
                    logger.debug(f"Setting properties for action node {node.id}")
                    # Set node properties
                    node.set_property('action_name', name)
                    node.set_property('action_data', {})
                    
                    # Create action file
                    logger.debug(f"Creating action file for: {name}")
                    self._create_action_file(name)
                    
                    logger.info(f"Successfully added action: {name}")
                    return node
                else:
                    logger.error("Failed to create action node")
            else:
                logger.debug("User cancelled action name input")
                    
        except Exception as e:
            logger.error(f"Error adding action: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add action: {str(e)}"
            )
            
        return None
        
    def add_conditional_node(self, pos=None):
        """Add a new conditional node."""
        logger.info(f"Adding conditional node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating conditional node")
            node = self.parent_widget.graph_manager.create_node(
                'conditional.ConditionalNode', 
                name='Conditional', 
                pos=pos
            )
            
            if node:
                logger.info(f"Successfully added conditional node {node.id} (empty, ready for configuration)")
                return node
            else:
                logger.error("Failed to create conditional node")
                
        except Exception as e:
            logger.error(f"Error adding conditional node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add conditional node: {str(e)}"
            )
            
        return None
        
    def add_llm_node(self, pos=None):
        """Add a new LLM node."""
        logger.info(f"Adding LLM node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating LLM node")
            node = self.parent_widget.graph_manager.create_node(
                'llm.LLMNode', 
                name='LLM', 
                pos=pos
            )
            
            if node:
                logger.debug(f"Setting default properties for LLM node {node.id}")
                # Set default properties
                node.set_property('prompt', '')
                node.set_property('model', 'gpt-3.5-turbo')
                node.set_property('temperature', '0.7')
                node.set_property('max_tokens', '150')
                
                logger.info(f"Successfully added LLM node {node.id}")
                return node
            else:
                logger.error("Failed to create LLM node")
                
        except Exception as e:
            logger.error(f"Error adding LLM node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add LLM node: {str(e)}"
            )
            
        return None
        
    # DEPRECATED: Context Builder - add function disabled
    # def add_context_builder_node(self, pos=None):
    #     """Add a new Context Builder node."""
    #     logger.info(f"Adding Context Builder node at position: {pos}")
    #     try:
    #         # Create the node
    #         logger.debug("Creating Context Builder node")
    #         node = self.parent_widget.graph_manager.create_node(
    #             'context_builder.ContextBuilderNode', 
    #             name='Context Builder', 
    #             pos=pos
    #         )
    #         
    #         if node:
    #             logger.debug(f"Setting default properties for Context Builder node {node.id}")
    #             # Default properties are set in the ContextBuilderNode class
    #             # We don't need to set them here as they're already configured in the node class
    #             
    #             logger.info(f"Successfully added Context Builder node {node.id}")
    #             return node
    #         else:
    #             logger.error("Failed to create Context Builder node")
    #             
    #     except Exception as e:
    #         logger.error(f"Error adding Context Builder node: {e}")
    #         QMessageBox.critical(
    #             self.parent_widget, 
    #             "Error", 
    #             f"Failed to add Context Builder node: {str(e)}"
    #         )
    #         
    #     return None
        
    def add_tts_node(self, pos=None):
        """Add a new TTS node."""
        logger.info(f"Adding TTS node at position: {pos}")
        try:
            # Create the node
            logger.debug("Creating TTS node")
            node = self.parent_widget.graph_manager.create_node(
                'tts.TTSNode', 
                name='TTS', 
                pos=pos
            )
            
            if node:
                logger.debug(f"Setting default properties for TTS node {node.id}")
                # Set default properties
                node.set_property('text', 'Hello, this is a text-to-speech test.')
                node.set_property('language', 'en')
                
                logger.info(f"Successfully added TTS node {node.id}")
                return node
            else:
                logger.error("Failed to create TTS node")
                
        except Exception as e:
            logger.error(f"Error adding TTS node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to add TTS node: {str(e)}"
            )
            
        return None
        
    def delete_node(self, node):
        """Delete a node based on its type."""
        logger.info(f"Deleting node: {node.id if node else 'None'} of type: {type(node).__name__ if node else 'None'}")
        try:
            if isinstance(node, SequenceNode):
                logger.debug("Deleting sequence node")
                self.delete_sequence_node(node)
            elif isinstance(node, ActionNode):
                logger.debug("Deleting action node")
                self.delete_action_node(node)
            elif isinstance(node, (ConditionalNode, LLMNode, TTSNode, ChainImportNode)):
                logger.debug(f"Deleting {type(node).__name__} node")
                self.parent_widget.graph_manager.delete_node(node)
            else:
                logger.warning(f"Unknown node type for deletion: {type(node).__name__ if node else 'None'}")
        except Exception as e:
            logger.error(f"Error deleting node: {e}")
            raise
            
    def delete_sequence_node(self, node):
        """Delete a sequence node and its associated file."""
        logger.info(f"Deleting sequence node: {node.id}")
        try:
            sequence_name = node.get_property('sequence_name') or ''
            logger.debug(f"Sequence name: {sequence_name}")
            
            # Remove from chain config
            logger.debug("Removing sequence from chain config")
            self.parent_widget.config_manager.remove_sequence_from_chain(sequence_name)
            
            # Delete sequence file
            sequence_file = os.path.join(
                self.parent_widget.sequences_folder, 
                f"{sequence_name}.json"
            )
            logger.debug(f"Checking for sequence file: {sequence_file}")
            if os.path.exists(sequence_file):
                logger.debug(f"Deleting sequence file: {sequence_file}")
                os.remove(sequence_file)
            else:
                logger.warning(f"Sequence file not found: {sequence_file}")
                
            # Delete the node
            logger.debug("Deleting sequence node from graph")
            self.parent_widget.graph_manager.delete_node(node)
            
            logger.info(f"Successfully deleted sequence node: {sequence_name}")
            
        except Exception as e:
            logger.error(f"Error deleting sequence node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to delete sequence node: {str(e)}"
            )
            
    def delete_action_node(self, node):
        """Delete an action node and remove it from sequences."""
        logger.info(f"Deleting action node: {node.id}")
        try:
            action_name = node.get_property('action_name') or ''
            logger.debug(f"Action name: {action_name}")
            
            # Remove action from all sequences
            logger.debug("Removing action from all sequences")
            self.parent_widget.config_manager.remove_action_from_sequence(action_name)
            
            # Delete action file
            action_file = os.path.join(
                self.parent_widget.actions_folder, 
                f"{action_name}.json"
            )
            logger.debug(f"Checking for action file: {action_file}")
            if os.path.exists(action_file):
                logger.debug(f"Deleting action file: {action_file}")
                os.remove(action_file)
            else:
                logger.warning(f"Action file not found: {action_file}")
                
            # Delete the node
            logger.debug("Deleting action node from graph")
            self.parent_widget.graph_manager.delete_node(node)
            
            logger.info(f"Successfully deleted action node: {action_name}")
            
        except Exception as e:
            logger.error(f"Error deleting action node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to delete action node: {str(e)}"
            )
            
    def edit_conditional(self, node):
        """Edit a conditional node's properties."""
        logger.info(f"Editing conditional node: {node.id}")
        try:
            logger.debug("Creating conditional dialog")
            
            # Get current conditional config from node and convert to dialog format
            condition_type = node.get_property('condition_type') or node.get_property('trigger_type') or 'presence'  # backward compatibility
            current_config = {}
            
            # Convert simple node properties to complex dialog structure
            # Only create config if node has actual data (not just defaults)
            if condition_type == 'presence' and node.get_property('image_path'):
                current_config['presence_triggers'] = [{
                    'image_path': node.get_property('image_path'),
                    'confidence': float(node.get_property('threshold') or 0.8),
                    'timeout': float(node.get_property('timeout') or 5.0)
                }]
            elif condition_type == 'absence' and node.get_property('image_path'):
                current_config['absence_triggers'] = [{
                    'image_path': node.get_property('image_path'),
                    'confidence': float(node.get_property('threshold') or 0.8),
                    'timeout': float(node.get_property('timeout') or 10.0)
                }]
            elif condition_type == 'ocr' and node.get_property('ocr_text'):
                current_config['ocr_triggers'] = [{
                    'target_text': node.get_property('ocr_text'),
                    'confidence': float(node.get_property('threshold') or 0.8),
                    'timeout': float(node.get_property('timeout') or 5.0),
                    'case_sensitive': str(node.get_property('case_sensitive') or 'True').lower() == 'true'
                }]
            elif condition_type == 'wait' and node.get_property('wait_time'):
                current_config['wait_conditions'] = [{
                    'type': 'wait_time',
                    'wait_time': float(node.get_property('wait_time')),
                    'timeout': float(node.get_property('timeout') or 30.0)
                }]
            elif condition_type == 'loop' and node.get_property('max_loops'):
                # Reconstruct loop configuration from node properties
                loop_type = node.get_property('loop_type') or 'while_present'
                loop_position = node.get_property('loop_position') or 'pre'
                
                # Parse stored condition data
                loop_condition_json = node.get_property('loop_condition')
                if loop_condition_json:
                    try:
                        condition = json.loads(loop_condition_json)
                    except (json.JSONDecodeError, TypeError):
                        # Fallback to basic image condition if JSON parsing fails
                        condition = {
                            'trigger_type': 'presence',
                            'image_path': node.get_property('image_path') or '',
                            'confidence': float(node.get_property('threshold') or 0.8)
                        }
                else:
                    # Fallback for older nodes without stored condition
                    condition = {
                        'trigger_type': 'presence',
                        'image_path': node.get_property('image_path') or '',
                        'confidence': float(node.get_property('threshold') or 0.8)
                    }
                
                loop_config = {
                    'type': loop_type,
                    'max_iterations': int(node.get_property('max_loops') or 10),
                    'sequence_file': node.get_property('sequence_file') or '',
                    'iteration_delay': float(node.get_property('iteration_delay') or 1.0),
                    'condition': condition
                }
                
                # Place in appropriate loop category
                if loop_position == 'post':
                    current_config['post_conditional_loops'] = [loop_config]
                else:
                    current_config['pre_conditional_loops'] = [loop_config]
            
            # Create dialog with required parameters
            # For conditional nodes, we use sequence_idx=0 and empty chain_config since they have single input/output
            dialog = AdvancedConditionalDialog(self.parent_widget, 0, [], current_config)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                
                # Get the configuration from the dialog
                config = dialog.get_config()
                
                # Handle the complex config structure from AdvancedConditionalDialog
                # Extract the first condition type and data from the complex structure
                condition_type = None
                condition_data = {}
                
                # Check for presence triggers
                if 'presence_triggers' in config and config['presence_triggers']:
                    condition_type = 'presence'
                    condition_data = config['presence_triggers'][0]
                    node.set_property('condition_type', 'presence')
                    node.set_property('image_path', condition_data.get('image_path', ''))
                    node.set_property('threshold', str(condition_data.get('confidence', 0.8)))
                    # Apply timeout from dialog configuration to node property
                    timeout = condition_data.get('timeout', 5.0)
                    node.set_property('timeout', str(timeout))
                    node.set_name('Wait for Image')
                # Check for absence triggers
                elif 'absence_triggers' in config and config['absence_triggers']:
                    condition_type = 'absence'
                    condition_data = config['absence_triggers'][0]
                    node.set_property('condition_type', 'absence')
                    node.set_property('image_path', condition_data.get('image_path', ''))
                    node.set_property('threshold', str(condition_data.get('confidence', 0.8)))
                    # Apply timeout from dialog configuration to node property
                    timeout = condition_data.get('timeout', 10.0)
                    node.set_property('timeout', str(timeout))
                    node.set_name('Wait for Absence')
                # Check for OCR triggers
                elif 'ocr_triggers' in config and config['ocr_triggers']:
                    condition_type = 'ocr'
                    condition_data = config['ocr_triggers'][0]
                    node.set_property('condition_type', 'ocr')
                    node.set_property('ocr_text', condition_data.get('target_text', ''))
                    node.set_property('threshold', str(condition_data.get('confidence', 0.8)))
                    node.set_property('case_sensitive', str(condition_data.get('case_sensitive', True)))
                    # Apply timeout from dialog configuration to node property
                    timeout = condition_data.get('timeout', 5.0)
                    node.set_property('timeout', str(timeout))
                    node.set_name('Wait for Text')
                # Check for wait conditions
                elif 'wait_conditions' in config and config['wait_conditions']:
                    condition_type = 'wait'
                    condition_data = config['wait_conditions'][0]
                    node.set_property('condition_type', 'wait')
                    node.set_property('wait_time', str(condition_data.get('wait_time', 5)))
                    # Apply timeout from dialog configuration to node property
                    timeout = condition_data.get('timeout', 30.0)
                    node.set_property('timeout', str(timeout))
                    node.set_name('Wait Time')
                # Check for conditional loops (pre or post)
                elif ('pre_conditional_loops' in config and config['pre_conditional_loops']) or ('post_conditional_loops' in config and config['post_conditional_loops']):
                    condition_type = 'loop'
                    # Get the first available loop configuration
                    if 'pre_conditional_loops' in config and config['pre_conditional_loops']:
                        condition_data = config['pre_conditional_loops'][0]
                        loop_position = 'pre'
                    else:
                        condition_data = config['post_conditional_loops'][0]
                        loop_position = 'post'
                    
                    node.set_property('condition_type', 'loop')
                    node.set_property('loop_type', condition_data.get('type', 'while_present'))
                    node.set_property('max_loops', str(condition_data.get('max_iterations', 10)))
                    node.set_property('sequence_file', condition_data.get('sequence_file', ''))
                    node.set_property('iteration_delay', str(condition_data.get('iteration_delay', 1.0)))
                    node.set_property('loop_position', loop_position)
                    
                    # Store the condition data as JSON for proper retrieval
                    condition = condition_data.get('condition', {})
                    node.set_property('loop_condition', json.dumps(condition))
                    
                    loop_type = condition_data.get('type', 'while_present')
                    node.set_name(f'{loop_type.title()} Loop')
                
                logger.info(f"Successfully edited conditional node: {node.id}")
            else:
                logger.debug("Dialog cancelled")
                    
        except Exception as e:
            logger.error(f"Error editing conditional node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to edit conditional node: {str(e)}"
            )
            
    def edit_llm(self, node):
        """Edit an LLM node's properties."""
        logger.info(f"Editing LLM node: {node.id}")
        try:
            logger.debug("Creating LLM dialog")
            dialog = LLMPropertiesDialog(self.parent_widget)
            
            # Set current values - Basic tab
            logger.debug("Loading current values into dialog")
            dialog.model_combo.setCurrentText(node.get_property('model') or 'llama3.2:latest')
            dialog.api_url_entry.setText(node.get_property('api_url') or '')
            dialog.temperature_spin.setValue(float(node.get_property('temperature') or 0.7))
            dialog.max_tokens_spin.setValue(int(node.get_property('max_tokens') or 500))
            dialog.output_variable_entry.setText(node.get_property('output_variable') or 'llm_output')
            
            # Prompt tab
            dialog.system_message_edit.setPlainText(node.get_property('system_message') or '')
            dialog.prompt_edit.setPlainText(node.get_property('prompt') or '')
            
            # Input source configuration
            input_source = node.get_property('input_source') or 'none'
            if input_source == 'none':
                dialog.input_none_radio.setChecked(True)
            elif input_source == 'ocr':
                dialog.input_ocr_radio.setChecked(True)
            elif input_source == 'previous':
                dialog.input_previous_radio.setChecked(True)
            elif input_source == 'clipboard':
                dialog.input_clipboard_radio.setChecked(True)
            
            # OCR configuration
            dialog.ocr_confidence_spin.setValue(float(node.get_property('ocr_confidence') or 0.7))
            dialog.ocr_preprocessing_combo.setCurrentText(node.get_property('ocr_preprocessing') or 'None')
            
            # Advanced tab - Output configuration
            write_text_str = node.get_property('write_text') or 'true'
            write_text = write_text_str.lower() in ('true', '1', 'yes', 'on')
            dialog.write_text_checkbox.setChecked(write_text)
            
            dialog.typing_batch_size_spin.setValue(int(node.get_property('typing_batch_size') or 20))
            dialog.typing_batch_delay_spin.setValue(float(node.get_property('typing_batch_delay') or 0.05))
            
            # Async processing
            use_async_str = node.get_property('use_async') or 'true'
            use_async = use_async_str.lower() in ('true', '1', 'yes', 'on')
            dialog.use_async_checkbox.setChecked(use_async)
            
            # Features tab - Vision configuration
            use_vision_str = node.get_property('use_vision') or 'false'
            use_vision = use_vision_str.lower() in ('true', '1', 'yes', 'on')
            dialog.use_vision_checkbox.setChecked(use_vision)
            
            screenshot_enabled_str = node.get_property('screenshot_enabled') or 'true'
            screenshot_enabled = screenshot_enabled_str.lower() in ('true', '1', 'yes', 'on')
            dialog.screenshot_enabled_checkbox.setChecked(screenshot_enabled)
            
            # ComoRAG configuration
            use_comorag_str = node.get_property('use_comorag') or 'true'
            use_comorag = use_comorag_str.lower() in ('true', '1', 'yes', 'on')
            dialog.use_comorag_checkbox.setChecked(use_comorag)
            
            max_cycles = int(node.get_property('max_cycles') or 5)
            dialog.max_cycles_spin.setValue(max_cycles)
            
            memory_size = int(node.get_property('memory_size') or 1000)
            dialog.memory_size_spin.setValue(memory_size)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                
                # Basic tab properties
                node.set_property('model', dialog.model_combo.currentText())
                node.set_property('api_url', dialog.api_url_entry.text())
                node.set_property('temperature', str(dialog.temperature_spin.value()))
                node.set_property('max_tokens', str(dialog.max_tokens_spin.value()))
                node.set_property('output_variable', dialog.output_variable_entry.text())
                
                # Prompt tab properties
                node.set_property('system_message', dialog.system_message_edit.toPlainText())
                node.set_property('prompt', dialog.prompt_edit.toPlainText())
                
                # Input source configuration
                if dialog.input_none_radio.isChecked():
                    node.set_property('input_source', 'none')
                elif dialog.input_ocr_radio.isChecked():
                    node.set_property('input_source', 'ocr')
                elif dialog.input_previous_radio.isChecked():
                    node.set_property('input_source', 'previous')
                elif dialog.input_clipboard_radio.isChecked():
                    node.set_property('input_source', 'clipboard')
                
                # OCR configuration
                node.set_property('ocr_confidence', str(dialog.ocr_confidence_spin.value()))
                node.set_property('ocr_preprocessing', dialog.ocr_preprocessing_combo.currentText())
                
                # Advanced tab properties
                node.set_property('write_text', str(dialog.write_text_checkbox.isChecked()).lower())
                node.set_property('typing_batch_size', str(dialog.typing_batch_size_spin.value()))
                node.set_property('typing_batch_delay', str(dialog.typing_batch_delay_spin.value()))
                node.set_property('use_async', str(dialog.use_async_checkbox.isChecked()).lower())
                
                # Features tab properties
                node.set_property('use_vision', str(dialog.use_vision_checkbox.isChecked()).lower())
                node.set_property('screenshot_enabled', str(dialog.screenshot_enabled_checkbox.isChecked()).lower())
                node.set_property('use_comorag', str(dialog.use_comorag_checkbox.isChecked()).lower())
                node.set_property('max_cycles', str(dialog.max_cycles_spin.value()))
                node.set_property('memory_size', str(dialog.memory_size_spin.value()))
                
                logger.info(f"Successfully edited LLM node: {node.id}")
            else:
                logger.debug("Dialog cancelled")
                
        except Exception as e:
            logger.error(f"Error editing LLM node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to edit LLM node: {str(e)}"
            )
            
    def edit_tts(self, node):
        """Edit a TTS node's properties."""
        logger.info(f"Editing TTS node: {node.id}")
        try:
            logger.debug("Creating TTS dialog")
            
            # Get current TTS configuration
            current_config = {
                'text': node.get_property('text') or 'Hello, this is a text-to-speech test.',
                'language': node.get_property('language') or 'en'
            }
            
            dialog = TTSPropertiesDialog(self.parent_widget, current_config)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                config = dialog.get_config()
                
                # Update node properties
                node.set_property('text', config['text'])
                node.set_property('language', config['language'])
                
                # Update node name to show preview of text
                preview_text = config['text'][:20] + "..." if len(config['text']) > 20 else config['text']
                node.set_name(f"TTS: {preview_text}")
                
                logger.info(f"Successfully edited TTS node: {node.id}")
            else:
                logger.debug("Dialog cancelled")
                
        except Exception as e:
            logger.error(f"Error editing TTS node: {e}")
            QMessageBox.critical(
                self.parent_widget, 
                "Error", 
                f"Failed to edit TTS node: {str(e)}"
            )
            
    # DEPRECATED: Context Builder - edit function disabled
    # def edit_context_builder_node(self, node):
    #     """Edit a Context Builder node's properties."""
    #     logger.info(f"Editing Context Builder node: {node.id}")
    #     try:
    #         # Use the same dialog as LLM nodes since they share properties
    #         logger.debug("Creating LLM dialog for Context Builder node")
    #         dialog = LLMPropertiesDialog(self.parent_widget)
    #         
    #         # Set current values
    #         logger.debug("Loading current values into dialog")
    #         dialog.prompt_edit.setPlainText(node.get_property('prompt') or '')
    #         dialog.model_combo.setCurrentText(node.get_property('model') or 'llama3.2:latest')
    #         dialog.system_message_edit.setPlainText(node.get_property('system_message') or '')
    #         dialog.api_url_entry.setText(node.get_property('api_url') or '')
    #         dialog.temperature_spin.setValue(float(node.get_property('temperature') or 0.7))
    #         dialog.max_tokens_spin.setValue(int(node.get_property('max_tokens') or 500))
    #         dialog.output_variable_entry.setText(node.get_property('output_variable') or 'context')
    #         
    #         # For Context Builder, write_text is always false
    #         dialog.write_text_checkbox.setChecked(False)
    #         dialog.write_text_checkbox.setEnabled(False)  # Disable the checkbox
    #         
    #         # Handle boolean properties
    #         use_vision_str = node.get_property('use_vision') or 'true'
    #         use_vision = use_vision_str.lower() in ('true', '1', 'yes', 'on')
    #         dialog.use_vision_checkbox.setChecked(use_vision)
    #         
    #         screenshot_enabled_str = node.get_property('screenshot_enabled') or 'true'
    #         screenshot_enabled = screenshot_enabled_str.lower() in ('true', '1', 'yes', 'on')
    #         dialog.screenshot_enabled_checkbox.setChecked(screenshot_enabled)
    #         
    #         # Handle ComoRAG properties
    #         use_comorag_str = node.get_property('use_comorag') or 'true'
    #         use_comorag = use_comorag_str.lower() in ('true', '1', 'yes', 'on')
    #         dialog.use_comorag_checkbox.setChecked(use_comorag)
    #         
    #         max_cycles = int(node.get_property('max_cycles') or 5)
    #         dialog.max_cycles_spin.setValue(max_cycles)
    #         
    #         memory_size = int(node.get_property('memory_size') or 1000)
    #         dialog.memory_size_spin.setValue(memory_size)
    #         
    #         # Handle async processing
    #         use_async_str = node.get_property('use_async') or 'true'
    #         use_async = use_async_str.lower() in ('true', '1', 'yes', 'on')
    #         if hasattr(dialog, 'use_async_checkbox'):
    #             dialog.use_async_checkbox.setChecked(use_async)
    #         
    #         if dialog.exec_() == dialog.Accepted:
    #             logger.debug("Dialog accepted, updating node properties")
    #             # Update node properties
    #             node.set_property('prompt', dialog.prompt_edit.toPlainText())
    #             node.set_property('model', dialog.model_combo.currentText())
    #             node.set_property('system_message', dialog.system_message_edit.toPlainText())
    #             node.set_property('api_url', dialog.api_url_entry.text())
    #             node.set_property('temperature', str(dialog.temperature_spin.value()))
    #             node.set_property('max_tokens', str(dialog.max_tokens_spin.value()))
    #             node.set_property('output_variable', dialog.output_variable_entry.text())
    #             
    #             # For Context Builder, write_text is always false
    #             node.set_property('write_text', 'false')
    #             
    #             node.set_property('use_vision', str(dialog.use_vision_checkbox.isChecked()).lower())
    #             node.set_property('screenshot_enabled', str(dialog.screenshot_enabled_checkbox.isChecked()).lower())
    #             node.set_property('use_comorag', str(dialog.use_comorag_checkbox.isChecked()).lower())
    #             node.set_property('max_cycles', str(dialog.max_cycles_spin.value()))
    #             node.set_property('memory_size', str(dialog.memory_size_spin.value()))
    #             
    #             # Save async setting if available
    #             if hasattr(dialog, 'use_async_checkbox'):
    #                 node.set_property('use_async', str(dialog.use_async_checkbox.isChecked()).lower())
    #             
    #             logger.info(f"Successfully edited Context Builder node: {node.id}")
    #         else:
    #             logger.debug("Dialog cancelled")
    #             
    #     except Exception as e:
    #         logger.error(f"Error editing Context Builder node: {e}")
    #         QMessageBox.critical(
    #             self.parent_widget, 
    #             "Error", 
    #             f"Failed to edit Context Builder node: {str(e)}"
    #         )
            
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
                
    def _create_action_file(self, action_name):
        """Create a new action file."""
        logger.info(f"Creating action file for: {action_name}")
        try:
            action_file = os.path.join(
                self.parent_widget.actions_folder, 
                f"{action_name}.json"
            )
            logger.debug(f"Action file path: {action_file}")
            
            if not os.path.exists(action_file):
                logger.debug("Creating new action file")
                action_data = {
                    "name": action_name,
                    "type": "click",
                    "data": {}
                }
                
                with open(action_file, 'w') as f:
                    json.dump(action_data, f, indent=2)
                
                logger.info(f"Successfully created action file: {action_file}")
            else:
                logger.debug(f"Action file already exists: {action_file}")
        except Exception as e:
            logger.error(f"Error creating action file for {action_name}: {e}")
            raise
                
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