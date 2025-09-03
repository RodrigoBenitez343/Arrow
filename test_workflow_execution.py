#!/usr/bin/env python3

import json
import sys
import os
import logging

# Add the LoOper directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'LoOper'))

from LoOper.player.multi_sequence_player import MultiSequencePlayer
from LoOper.player.config import logger

def test_workflow_execution():
    """Test the actual workflow execution with detailed logging"""
    
    # Set up detailed logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logger.setLevel(logging.DEBUG)
    
    # Load the chain configuration
    chain_file = r"c:\Users\david.benitez\OneDrive - Motivus\Documents\LoOper\LoOper\chains\testchainformeeting.json"
    
    with open(chain_file, 'r') as f:
        chain_config = json.load(f)
    
    print(f"Testing workflow execution for chain with {len(chain_config.get('sequences', []))} sequences")
    
    # Create player
    player = MultiSequencePlayer(chain_config)
    
    # Add custom logging to track execution
    original_execute_sequence = player._execute_sequence_node
    original_execute_conditional = player._execute_conditional_node
    original_execute_llm = player._execute_llm_node
    original_execute_tts = player._execute_tts_node
    
    def log_sequence_execution(node, stop_flag):
        seq_name = node['data'].get('name', 'Unknown')
        seq_id = node['data'].get('node_id', 'Unknown')
        print(f"\n*** EXECUTING SEQUENCE: {seq_name} (ID: {seq_id}) ***")
        result = original_execute_sequence(node, stop_flag)
        print(f"*** SEQUENCE {seq_name} COMPLETED, NEXT: {result} ***\n")
        return result
    
    def log_conditional_execution(node, stop_flag):
        cond_id = node['data'].get('node_id', 'Unknown')
        print(f"\n*** EXECUTING CONDITIONAL: {cond_id} ***")
        result = original_execute_conditional(node, stop_flag)
        print(f"*** CONDITIONAL {cond_id} COMPLETED, NEXT: {result} ***\n")
        return result
    
    def log_llm_execution(node, stop_flag):
        llm_id = node['data'].get('node_id', 'Unknown')
        print(f"\n*** EXECUTING LLM: {llm_id} ***")
        result = original_execute_llm(node, stop_flag)
        print(f"*** LLM {llm_id} COMPLETED, NEXT: {result} ***\n")
        return result
    
    def log_tts_execution(node, stop_flag):
        tts_id = node['data'].get('node_id', 'Unknown')
        tts_text = node['data'].get('text', 'Unknown')[:50]
        print(f"\n*** EXECUTING TTS: {tts_id} - {tts_text}... ***")
        result = original_execute_tts(node, stop_flag)
        print(f"*** TTS {tts_id} COMPLETED, NEXT: {result} ***\n")
        return result
    
    # Monkey patch the methods
    player._execute_sequence_node = log_sequence_execution
    player._execute_conditional_node = log_conditional_execution
    player._execute_llm_node = log_llm_execution
    player._execute_tts_node = log_tts_execution
    
    # Create a stop flag that stops after maximize sequence
    execution_count = 0
    def stop_after_maximize():
        nonlocal execution_count
        execution_count += 1
        # Stop after 10 nodes to prevent infinite execution
        if execution_count > 10:
            print("\n*** STOPPING EXECUTION AFTER 10 NODES FOR TESTING ***")
            return True
        return False
    
    try:
        print("\n=== STARTING WORKFLOW EXECUTION ===")
        player.play_chain(stop_flag=stop_after_maximize)
        print("\n=== WORKFLOW EXECUTION COMPLETED ===")
    except Exception as e:
        print(f"\n*** ERROR DURING EXECUTION: {e} ***")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_workflow_execution()