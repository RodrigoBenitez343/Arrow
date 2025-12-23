from ....config import logger

class TTSMixin:
    def _execute_tts_node(self, node, stop_flag):
        """
        Executes a TTS node and returns the next node ID.
        
        Args:
            node (dict): The TTS node from the workflow graph
            stop_flag (callable): Stop flag function
            
        Returns:
            str: Next node ID to execute, or None if workflow should end
        """
        tts_data = node['data']
        logger.info(f"=== STARTING TTS NODE EXECUTION ===")
        node_identifier = tts_data.get('id') or tts_data.get('node_id', 'unknown')
        logger.info(f"Executing TTS node: {tts_data.get('name', node_identifier)}")
        logger.info(f"Full node data: {node}")
        
        try:
            # Execute TTS using the LLM executor's TTS functionality
            result = self.llm_executor.execute_tts_standalone(tts_data, self.llm_executor.get_all_variables())
            
            if result.get('success', False):
                logger.info(f"TTS node execution successful: {result.get('message', 'No message')}")
                
                # Find next node in the workflow
                connections = node.get('connections', {})
                if 'output' in connections and connections['output']:
                    next_connection = connections['output'][0]
                    return next_connection['node_id']
                else:
                    logger.info("No output connections found for TTS node, ending workflow")
                    return "__done__"
            else:
                error_msg = result.get('error', 'Unknown TTS error')
                logger.error(f"TTS node execution failed: {error_msg}")
                return None
                
        except Exception as e:
            logger.error(f"TTS node execution failed with exception: {str(e)}")
            return None
