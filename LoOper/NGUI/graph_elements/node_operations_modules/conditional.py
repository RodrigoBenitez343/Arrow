import json
from PyQt5.QtWidgets import QMessageBox
from ...dialogs import AdvancedConditionalDialog
from .utils import get_logger

logger = get_logger(__name__)

class ConditionalOperationsMixin:
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
            if condition_type == 'code' and (node.get_property('code') or '').strip():
                current_config['code_condition'] = {
                    'code': node.get_property('code') or '',
                    'timeout': float(node.get_property('timeout') or 5.0)
                }
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
                
                if 'code_condition' in config and config['code_condition'] and str(config['code_condition'].get('code', '')).strip():
                    condition_type = 'code'
                    condition_data = config['code_condition']
                    node.set_property('condition_type', 'code')
                    node.set_property('code', str(condition_data.get('code', '')))
                    timeout = float(condition_data.get('timeout', 5.0) or 5.0)
                    node.set_property('timeout', str(timeout))
                    node.set_name('Code Condition')
                # Check for presence triggers
                elif 'presence_triggers' in config and config['presence_triggers']:
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
