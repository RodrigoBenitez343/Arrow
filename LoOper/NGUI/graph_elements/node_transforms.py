import logging
from PyQt5.QtWidgets import QMessageBox
try:
    from ...AI.model_cache import get_real_cached_models
except Exception:
    def get_real_cached_models(timeout: float = 5.0):
        return []
from ..nodes import SequenceNode, ActionNode, ConditionalNode, LLMNode

logger = logging.getLogger(__name__)


class NodeTransforms:
    """Handles transformations between different node types."""
    
    def __init__(self, parent_widget):
        """Initialize the NodeTransforms with a reference to the parent widget."""
        try:
            logger.info("Initializing NodeTransforms")
            self.parent_widget = parent_widget
            logger.debug(f"NodeTransforms initialized with parent widget: {type(parent_widget).__name__}")
        except Exception as e:
            logger.error(f"Error initializing NodeTransforms: {str(e)}")
            raise
        
    def transform_sequence_to_conditional(self, sequence_node):
        """Transform a sequence node into a conditional node."""
        try:
            logger.info(f"Starting transformation of sequence node to conditional node")
            logger.debug(f"Sequence node: {sequence_node}, type: {type(sequence_node).__name__}")
            
            if not isinstance(sequence_node, SequenceNode):
                logger.error("Invalid node type - expected SequenceNode")
                raise ValueError("Node must be a SequenceNode")
                
            # Get sequence node properties
            sequence_name = sequence_node.get_property('sequence_name') or ''
            node_pos = sequence_node.pos()
            # NodeGraphQt returns position as [x, y] list, not QPoint
            pos_x, pos_y = node_pos[0], node_pos[1]
            logger.debug(f"Sequence properties - name: {sequence_name}, position: ({pos_x}, {pos_y})")
            
            # Get connections
            input_connections = self._get_input_connections(sequence_node)
            output_connections = self._get_output_connections(sequence_node)
            
            # Create new conditional node
            logger.debug("Creating new conditional node")
            conditional_node = self.parent_widget.node_operations.add_conditional_node(
                pos=[pos_x, pos_y]
            )
            
            if conditional_node:
                logger.debug("Conditional node created successfully")
                # Set conditional node name based on sequence
                conditional_node.set_name(f"Conditional: {sequence_name}")
                
                # Set default conditional properties
                conditional_node.set_property('condition_type', 'presence')
                conditional_node.set_property('image_path', '')
                conditional_node.set_property('threshold', '0.8')
                conditional_node.set_property('wait_time', '5')
                conditional_node.set_property('max_loops', '10')
                conditional_node.set_property('ocr_text', '')
                logger.debug(f"Set conditional properties - condition_type: presence, threshold: 0.8")
                
                # Restore connections
                logger.debug("Restoring connections for conditional node")
                self._restore_connections(conditional_node, input_connections, output_connections)
                
                # Delete original sequence node
                logger.debug("Deleting original sequence node")
                self.parent_widget.graph_manager.delete_node(sequence_node)
                
                logger.info("Successfully transformed sequence node to conditional node")
                return conditional_node
            else:
                logger.error("Failed to create conditional node")
                
        except Exception as e:
            logger.error(f"Error transforming sequence to conditional: {str(e)}")
            QMessageBox.critical(
                self.parent_widget,
                "Transform Error",
                f"Failed to transform sequence to conditional: {str(e)}"
            )
            
        return None
        
    def transform_llm_to_conditional(self, llm_node):
        """Transform an LLM node into a conditional node."""
        try:
            logger.info(f"Starting transformation of LLM node to conditional node")
            logger.debug(f"LLM node: {llm_node}, type: {type(llm_node).__name__}")
            
            if not isinstance(llm_node, LLMNode):
                logger.error("Invalid node type - expected LLMNode")
                raise ValueError("Node must be an LLMNode")
                
            # Get LLM node properties
            llm_prompt = llm_node.get_property('prompt') or ''
            node_pos = llm_node.pos()
            # NodeGraphQt returns position as [x, y] list, not QPoint
            pos_x, pos_y = node_pos[0], node_pos[1]
            logger.debug(f"LLM properties - prompt: {llm_prompt[:50]}..., position: ({pos_x}, {pos_y})")
            
            # Get connections
            input_connections = self._get_input_connections(llm_node)
            output_connections = self._get_output_connections(llm_node)
            
            # Create new conditional node
            logger.debug("Creating new conditional node from LLM")
            conditional_node = self.parent_widget.node_operations.add_conditional_node(
                pos=[pos_x, pos_y]
            )
            
            if conditional_node:
                logger.debug("Conditional node created successfully from LLM")
                # Set conditional node name based on LLM prompt
                prompt_preview = llm_prompt[:30] + "..." if len(llm_prompt) > 30 else llm_prompt
                conditional_node.set_name(f"Conditional: {prompt_preview}")
                
                # Set conditional properties - could use OCR to wait for LLM response
                conditional_node.set_property('condition_type', 'ocr')
                conditional_node.set_property('ocr_text', '')
                conditional_node.set_property('threshold', '0.8')
                conditional_node.set_property('wait_time', '10')
                conditional_node.set_property('max_loops', '5')
                logger.debug(f"Set conditional properties from LLM - condition_type: ocr, prompt_preview: {prompt_preview}")
                
                # Restore connections
                logger.debug("Restoring connections for conditional node from LLM")
                self._restore_connections(conditional_node, input_connections, output_connections)
                
                # Delete original LLM node
                logger.debug("Deleting original LLM node")
                self.parent_widget.graph_manager.delete_node(llm_node)
                
                logger.info("Successfully transformed LLM node to conditional node")
                return conditional_node
            else:
                logger.error("Failed to create conditional node from LLM")
                
        except Exception as e:
            logger.error(f"Error transforming LLM to conditional: {str(e)}")
            QMessageBox.critical(
                self.parent_widget,
                "Transform Error",
                f"Failed to transform LLM to conditional: {str(e)}"
            )
            
        return None
        
    def transform_conditional_to_sequence(self, conditional_node):
        """Transform a conditional node into a sequence node."""
        try:
            logger.info(f"Starting transformation of conditional node to sequence node")
            logger.debug(f"Conditional node: {conditional_node}, type: {type(conditional_node).__name__}")
            
            if not isinstance(conditional_node, ConditionalNode):
                logger.error("Invalid node type - expected ConditionalNode")
                raise ValueError("Node must be a ConditionalNode")
                
            # Get conditional node properties
            condition_type = conditional_node.get_property('condition_type') or conditional_node.get_property('trigger_type') or 'presence'  # backward compatibility
            node_pos = conditional_node.pos()
            # NodeGraphQt returns position as [x, y] list, not QPoint
            pos_x, pos_y = node_pos[0], node_pos[1]
            logger.debug(f"Conditional properties - condition_type: {condition_type}, position: ({pos_x}, {pos_y})")
            
            # Get connections
            input_connections = self._get_input_connections(conditional_node)
            output_connections = self._get_output_connections(conditional_node)
            
            # Create new sequence node
            sequence_name = f"Sequence_{condition_type}"
            logger.debug(f"Creating new sequence node with name: {sequence_name}")
            sequence_node = self.parent_widget.node_operations.add_sequence(
                pos=[pos_x, pos_y]
            )
            
            if sequence_node:
                logger.debug("Sequence node created successfully from conditional")
                # Set sequence name
                sequence_node.set_name(sequence_name)
                sequence_node.set_property('sequence_name', sequence_name)
                logger.debug(f"Set sequence properties - name: {sequence_name}")
                
                # Restore connections
                logger.debug("Restoring connections for sequence node from conditional")
                self._restore_connections(sequence_node, input_connections, output_connections)
                
                # Delete original conditional node
                logger.debug("Deleting original conditional node")
                self.parent_widget.graph_manager.delete_node(conditional_node)
                
                logger.info("Successfully transformed conditional node to sequence node")
                return sequence_node
            else:
                logger.error("Failed to create sequence node from conditional")
                
        except Exception as e:
            logger.error(f"Error transforming conditional to sequence: {str(e)}")
            QMessageBox.critical(
                self.parent_widget,
                "Transform Error",
                f"Failed to transform conditional to sequence: {str(e)}"
            )
            
        return None
        
    def transform_conditional_to_llm(self, conditional_node):
        """Transform a conditional node into an LLM node."""
        try:
            logger.info(f"Starting transformation of conditional node to LLM node")
            logger.debug(f"Conditional node: {conditional_node}, type: {type(conditional_node).__name__}")
            
            if not isinstance(conditional_node, ConditionalNode):
                logger.error("Invalid node type - expected ConditionalNode")
                raise ValueError("Node must be a ConditionalNode")
                
            # Get conditional node properties
            condition_type = conditional_node.get_property('condition_type') or conditional_node.get_property('trigger_type') or 'presence'  # backward compatibility
            ocr_text = conditional_node.get_property('ocr_text') or ''
            node_pos = conditional_node.pos()
            # NodeGraphQt returns position as [x, y] list, not QPoint
            pos_x, pos_y = node_pos[0], node_pos[1]
            logger.debug(f"Conditional properties - condition_type: {condition_type}, ocr_text: {ocr_text[:50]}..., position: ({pos_x}, {pos_y})")
            
            # Get connections
            input_connections = self._get_input_connections(conditional_node)
            output_connections = self._get_output_connections(conditional_node)
            
            # Create new LLM node
            logger.debug("Creating new LLM node from conditional")
            llm_node = self.parent_widget.node_operations.add_llm_node(
                pos=[pos_x, pos_y]
            )
            
            if llm_node:
                logger.debug("LLM node created successfully from conditional")
                # Set LLM properties based on conditional
                if condition_type == 'ocr' and ocr_text:
                    prompt = f"Analyze the following text: {ocr_text}"
                else:
                    prompt = f"Process condition type: {condition_type}"
                    
                llm_node.set_property('prompt', prompt)
                # Choose a cached real model if available
                try:
                    cached = get_real_cached_models(timeout=2.0)
                    default_model = cached[0] if cached else ''
                except Exception:
                    default_model = ''
                llm_node.set_property('model', default_model)
                llm_node.set_property('temperature', '0.7')
                llm_node.set_property('max_tokens', '150')
                logger.debug(f"Set LLM properties from conditional - prompt: {prompt[:50]}..., model: {default_model}")
                
                # Restore connections
                logger.debug("Restoring connections for LLM node from conditional")
                self._restore_connections(llm_node, input_connections, output_connections)
                
                # Delete original conditional node
                logger.debug("Deleting original conditional node")
                self.parent_widget.graph_manager.delete_node(conditional_node)
                
                logger.info("Successfully transformed conditional node to LLM node")
                return llm_node
            else:
                logger.error("Failed to create LLM node from conditional")
                
        except Exception as e:
            logger.error(f"Error transforming conditional to LLM: {str(e)}")
            QMessageBox.critical(
                self.parent_widget,
                "Transform Error",
                f"Failed to transform conditional to LLM: {str(e)}"
            )
            
        return None
        
    def transform_sequence_to_llm(self, sequence_node):
        """Transform a sequence node into an LLM node."""
        try:
            logger.info(f"Starting transformation of sequence node to LLM node")
            logger.debug(f"Sequence node: {sequence_node}, type: {type(sequence_node).__name__}")
            
            if not isinstance(sequence_node, SequenceNode):
                logger.error("Invalid node type - expected SequenceNode")
                raise ValueError("Node must be a SequenceNode")
                
            # Get sequence node properties
            sequence_name = sequence_node.get_property('sequence_name') or ''
            actions = sequence_node.get_property('actions') or []
            node_pos = sequence_node.pos()
            # NodeGraphQt returns position as [x, y] list, not QPoint
            pos_x, pos_y = node_pos[0], node_pos[1]
            logger.debug(f"Sequence properties - name: {sequence_name}, actions count: {len(actions)}, position: ({pos_x}, {pos_y})")
            
            # Get connections
            input_connections = self._get_input_connections(sequence_node)
            output_connections = self._get_output_connections(sequence_node)
            
            # Create new LLM node
            logger.debug("Creating new LLM node from sequence")
            llm_node = self.parent_widget.node_operations.add_llm_node(
                pos=[pos_x, pos_y]
            )
            
            if llm_node:
                logger.debug("LLM node created successfully from sequence")
                # Set LLM properties based on sequence
                prompt = f"Analyze and optimize the sequence '{sequence_name}' with {len(actions)} actions"
                llm_node.set_property('prompt', prompt)
                # Choose a cached real model if available
                try:
                    cached = get_real_cached_models(timeout=2.0)
                    default_model = cached[0] if cached else ''
                except Exception:
                    default_model = ''
                llm_node.set_property('model', default_model)
                llm_node.set_property('temperature', '0.7')
                llm_node.set_property('max_tokens', '200')
                logger.debug(f"Set LLM properties from sequence - prompt: {prompt[:50]}..., model: {default_model}")
                
                # Restore connections
                logger.debug("Restoring connections for LLM node from sequence")
                self._restore_connections(llm_node, input_connections, output_connections)
                
                # Delete original sequence node
                logger.debug("Deleting original sequence node")
                self.parent_widget.node_operations.delete_sequence_node(sequence_node)
                
                logger.info("Successfully transformed sequence node to LLM node")
                return llm_node
            else:
                logger.error("Failed to create LLM node from sequence")
                
        except Exception as e:
            logger.error(f"Error transforming sequence to LLM: {str(e)}")
            QMessageBox.critical(
                self.parent_widget,
                "Transform Error",
                f"Failed to transform sequence to LLM: {str(e)}"
            )
            
        return None
        
    def _get_input_connections(self, node):
        """Get all input connections for a node."""
        try:
            logger.debug(f"Getting input connections for node: {node}")
            connections = []
            
            for input_port in node.input_ports():
                for connected_port in input_port.connected_ports():
                    connections.append({
                        'source_node': connected_port.node(),
                        'source_port': connected_port.name(),
                        'target_port': input_port.name()
                    })
                    
            logger.debug(f"Found {len(connections)} input connections")
            return connections
        except Exception as e:
            logger.error(f"Error getting input connections: {str(e)}")
            return []
        
    def _get_output_connections(self, node):
        """Get all output connections for a node."""
        try:
            logger.debug(f"Getting output connections for node: {node}")
            connections = []
            
            for output_port in node.output_ports():
                for connected_port in output_port.connected_ports():
                    connections.append({
                        'source_port': output_port.name(),
                        'target_node': connected_port.node(),
                        'target_port': connected_port.name()
                    })
                    
            return connections
        except Exception as e:
            logger.error(f"Error getting output connections: {str(e)}")
            return []
        
    def _restore_connections(self, new_node, input_connections, output_connections):
        """Restore connections for a transformed node."""
        try:
            logger.debug(f"Restoring connections for new node: {new_node}")
            logger.debug(f"Input connections to restore: {len(input_connections)}, Output connections: {len(output_connections)}")
            # Restore input connections
            for conn in input_connections:
                source_node = conn['source_node']
                source_port_name = conn['source_port']
                target_port_name = conn['target_port']
                
                # Find matching ports on new node
                target_ports = [p for p in new_node.input_ports() 
                              if p.name() == target_port_name]
                if not target_ports:
                    # Use first available input port if exact match not found
                    target_ports = new_node.input_ports()
                    
                if target_ports:
                    source_ports = [p for p in source_node.output_ports() 
                                  if p.name() == source_port_name]
                    if source_ports:
                        source_ports[0].connect_to(target_ports[0])
                        
            # Restore output connections
            for conn in output_connections:
                source_port_name = conn['source_port']
                target_node = conn['target_node']
                target_port_name = conn['target_port']
                
                # Find matching ports on new node
                source_ports = [p for p in new_node.output_ports() 
                              if p.name() == source_port_name]
                if not source_ports:
                    # Use first available output port if exact match not found
                    source_ports = new_node.output_ports()
                    
                if source_ports:
                    target_ports = [p for p in target_node.input_ports() 
                                  if p.name() == target_port_name]
                    if target_ports:
                        source_ports[0].connect_to(target_ports[0])
                        
            logger.info(f"Successfully restored connections for transformed node")
        except Exception as e:
            logger.error(f"Failed to restore some connections: {str(e)}")
            print(f"Warning: Failed to restore some connections: {str(e)}")
            
    def get_available_transforms(self, node):
        """Get available transformation options for a node."""
        try:
            logger.debug(f"Getting available transforms for node: {node}, type: {type(node).__name__}")
            transforms = []
            
            if isinstance(node, SequenceNode):
                transforms.extend([
                    ('conditional', 'Transform to Conditional'),
                    ('llm', 'Transform to LLM')
                ])
            elif isinstance(node, ConditionalNode):
                transforms.extend([
                    ('sequence', 'Transform to Sequence'),
                    ('llm', 'Transform to LLM')
                ])
            elif isinstance(node, LLMNode):
                transforms.extend([
                    ('conditional', 'Transform to Conditional'),
                    ('sequence', 'Transform to Sequence')
                ])
                
            logger.debug(f"Available transforms for {type(node).__name__}: {[t[0] for t in transforms]}")
            return transforms
        except Exception as e:
            logger.error(f"Error getting available transforms: {str(e)}")
            return []
        
    def transform_node(self, node, target_type):
        """Transform a node to the specified target type."""
        try:
            logger.info(f"Transforming node {node} from {type(node).__name__} to {target_type}")
            
            if isinstance(node, SequenceNode):
                if target_type == 'conditional':
                    logger.debug("Calling transform_sequence_to_conditional")
                    return self.transform_sequence_to_conditional(node)
                elif target_type == 'llm':
                    logger.debug("Calling transform_sequence_to_llm")
                    return self.transform_sequence_to_llm(node)
            elif isinstance(node, ConditionalNode):
                if target_type == 'sequence':
                    logger.debug("Calling transform_conditional_to_sequence")
                    return self.transform_conditional_to_sequence(node)
                elif target_type == 'llm':
                    logger.debug("Calling transform_conditional_to_llm")
                    return self.transform_conditional_to_llm(node)
            elif isinstance(node, LLMNode):
                if target_type == 'conditional':
                    logger.debug("Calling transform_llm_to_conditional")
                    return self.transform_llm_to_conditional(node)
                elif target_type == 'sequence':
                    logger.warning("LLM to sequence transformation not implemented yet")
                    # LLM to sequence not implemented yet
                    pass
            
            logger.warning(f"No transformation available from {type(node).__name__} to {target_type}")
            return None
        except Exception as e:
            logger.error(f"Error in transform_node: {str(e)}")
            return None