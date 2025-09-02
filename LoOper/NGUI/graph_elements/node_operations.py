import json
import logging
import os
from PyQt5.QtWidgets import QMessageBox, QInputDialog, QFileDialog
from ..dialogs import AdvancedConditionalDialog, LLMPropertiesDialog, TTSPropertiesDialog
from ..nodes import SequenceNode, ActionNode, ConditionalNode, LLMNode, TTSNode

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
                    # Set node properties with the selected file
                    node.set_property('sequence_file', file_path)
                    
                    # Use the set_sequence_data method like the old implementation
                    sequence_config = {
                        'sequence_file': file_path,
                        'loop_count': 1,
                        'extra_delay': 1.0
                    }
                    node.set_sequence_data(0, sequence_config)
                    
                    logger.info(f"Successfully added sequence: {file_path}")
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
            elif isinstance(node, (ConditionalNode, LLMNode, TTSNode)):
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
                    'timeout': float(node.get_property('wait_time') or 5)
                }]
            elif condition_type == 'absence' and node.get_property('image_path'):
                current_config['absence_triggers'] = [{
                    'image_path': node.get_property('image_path'),
                    'confidence': float(node.get_property('threshold') or 0.8),
                    'timeout': float(node.get_property('wait_time') or 5)
                }]
            elif condition_type == 'ocr' and node.get_property('ocr_text'):
                current_config['ocr_triggers'] = [{
                    'target_text': node.get_property('ocr_text'),
                    'confidence': float(node.get_property('threshold') or 0.8),
                    'timeout': float(node.get_property('wait_time') or 5),
                    'case_sensitive': str(node.get_property('case_sensitive') or 'True').lower() == 'true'
                }]
            elif condition_type == 'wait' and node.get_property('wait_time'):
                current_config['wait_conditions'] = [{
                    'type': 'wait_time',
                    'wait_time': float(node.get_property('wait_time')),
                    'timeout': 30
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
                    node.set_name('Wait for Image')
                # Check for absence triggers
                elif 'absence_triggers' in config and config['absence_triggers']:
                    condition_type = 'absence'
                    condition_data = config['absence_triggers'][0]
                    node.set_property('condition_type', 'absence')
                    node.set_property('image_path', condition_data.get('image_path', ''))
                    node.set_property('threshold', str(condition_data.get('confidence', 0.8)))
                    node.set_name('Wait for Absence')
                # Check for OCR triggers
                elif 'ocr_triggers' in config and config['ocr_triggers']:
                    condition_type = 'ocr'
                    condition_data = config['ocr_triggers'][0]
                    node.set_property('condition_type', 'ocr')
                    node.set_property('ocr_text', condition_data.get('target_text', ''))
                    node.set_property('threshold', str(condition_data.get('confidence', 0.8)))
                    node.set_property('case_sensitive', str(condition_data.get('case_sensitive', True)))
                    node.set_name('Wait for Text')
                # Check for wait conditions
                elif 'wait_conditions' in config and config['wait_conditions']:
                    condition_type = 'wait'
                    condition_data = config['wait_conditions'][0]
                    node.set_property('condition_type', 'wait')
                    node.set_property('wait_time', str(condition_data.get('wait_time', 5)))
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
            
            # Set current values
            logger.debug("Loading current values into dialog")
            dialog.prompt_edit.setPlainText(node.get_property('prompt') or '')
            dialog.model_combo.setCurrentText(node.get_property('model') or 'llama3.2:latest')
            dialog.system_message_edit.setPlainText(node.get_property('system_message') or '')
            dialog.api_url_entry.setText(node.get_property('api_url') or '')
            dialog.temperature_spin.setValue(float(node.get_property('temperature') or 0.7))
            dialog.max_tokens_spin.setValue(int(node.get_property('max_tokens') or 500))
            dialog.output_variable_entry.setText(node.get_property('output_variable') or 'llm_output')
            
            # Handle boolean properties
            write_text_str = node.get_property('write_text') or 'true'
            write_text = write_text_str.lower() in ('true', '1', 'yes', 'on')
            dialog.write_text_checkbox.setChecked(write_text)
            
            use_vision_str = node.get_property('use_vision') or 'false'
            use_vision = use_vision_str.lower() in ('true', '1', 'yes', 'on')
            dialog.use_vision_checkbox.setChecked(use_vision)
            
            screenshot_enabled_str = node.get_property('screenshot_enabled') or 'true'
            screenshot_enabled = screenshot_enabled_str.lower() in ('true', '1', 'yes', 'on')
            dialog.screenshot_enabled_checkbox.setChecked(screenshot_enabled)
            
            if dialog.exec_() == dialog.Accepted:
                logger.debug("Dialog accepted, updating node properties")
                # Update node properties
                node.set_property('prompt', dialog.prompt_edit.toPlainText())
                node.set_property('model', dialog.model_combo.currentText())
                node.set_property('system_message', dialog.system_message_edit.toPlainText())
                node.set_property('api_url', dialog.api_url_entry.text())
                node.set_property('temperature', str(dialog.temperature_spin.value()))
                node.set_property('max_tokens', str(dialog.max_tokens_spin.value()))
                node.set_property('output_variable', dialog.output_variable_entry.text())
                node.set_property('write_text', str(dialog.write_text_checkbox.isChecked()).lower())
                node.set_property('use_vision', str(dialog.use_vision_checkbox.isChecked()).lower())
                node.set_property('screenshot_enabled', str(dialog.screenshot_enabled_checkbox.isChecked()).lower())
                
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