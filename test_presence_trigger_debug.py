import json
import sys
import os

# Add the LoOper directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'LoOper'))

from player.multi_sequence_player import MultiSequencePlayer
from player.config import logger
import logging

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('presence_trigger_debug.log')
    ]
)

def test_presence_evaluation():
    """Test presence trigger evaluation specifically"""
    
    # Load the chain
    chain_file = r"c:\Users\david.benitez\OneDrive - Motivus\Documents\LoOper\LoOper\chains\testchainformeeting.json"
    
    with open(chain_file, 'r') as f:
        chain_data = json.load(f)
    
    # Create player
    player = MultiSequencePlayer(chain_data)
    
    print("\n=== TESTING PRESENCE TRIGGER EVALUATION ===")
    
    # Get conditional nodes from the correct location
    conditionals = chain_data.get('conditional_nodes', [])
    
    print(f"Found {len(conditionals)} conditional nodes")
    
    for i, conditional in enumerate(conditionals):
        print(f"\n--- Testing Conditional {i} ---")
        print(f"Node ID: {conditional.get('node_id')}")
        print(f"Condition Type: {conditional.get('condition_type')}")
        print(f"Image Path: {conditional.get('image_path')}")
        print(f"Threshold: {conditional.get('threshold')}")
        print(f"Wait Time: {conditional.get('wait_time')}")
        print(f"Loop Type: {conditional.get('loop_type')}")
        print(f"Loop Position: {conditional.get('loop_position')}")
        
        # Check if image exists
        image_path = conditional.get('image_path', '')
        if os.path.exists(image_path):
            print(f"✓ Image file exists: {image_path}")
        else:
            print(f"✗ Image file missing: {image_path}")
            continue
        
        # Test the actual _evaluate_conditional method
        print("\nTesting _evaluate_conditional method:")
        
        # Test with 0 timeout (immediate check)
        test_conditional = conditional.copy()
        test_conditional['wait_time'] = 0
        print(f"\n  Testing with 0 timeout:")
        try:
            result = player._evaluate_conditional(test_conditional, lambda: False)
            print(f"  Result: {result}")
        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Test with 1 second timeout
        test_conditional['wait_time'] = 1
        print(f"\n  Testing with 1 second timeout:")
        try:
            result = player._evaluate_conditional(test_conditional, lambda: False)
            print(f"  Result: {result}")
        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Test with lower confidence threshold
        test_conditional['threshold'] = 0.5
        test_conditional['wait_time'] = 1
        print(f"\n  Testing with lower confidence (0.5):")
        try:
            result = player._evaluate_conditional(test_conditional, lambda: False)
            print(f"  Result: {result}")
        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
        
        # Test direct template matching
        print(f"\n  Testing direct template matching:")
        try:
            from player.computer_vision import TemplateMatching
            tm = TemplateMatching()
            
            # Take a screenshot and try to find the template
            import pyautogui
            screenshot = pyautogui.screenshot()
            
            # Try to find the template
            result = tm.find_template(screenshot, image_path, threshold=0.8)
            print(f"  Template matching result: {result}")
            
            if result:
                print(f"  Found at: {result}")
            else:
                print(f"  Template not found on screen")
                
                # Try with lower threshold
                result_low = tm.find_template(screenshot, image_path, threshold=0.5)
                if result_low:
                    print(f"  Found with lower threshold at: {result_low}")
                else:
                    print(f"  Still not found with threshold 0.5")
                
        except Exception as e:
            print(f"  Template matching error: {e}")
            import traceback
            traceback.print_exc()

def analyze_conditional_config():
    """Analyze the conditional configuration format"""
    
    chain_file = r"c:\Users\david.benitez\OneDrive - Motivus\Documents\LoOper\LoOper\chains\testchainformeeting.json"
    
    with open(chain_file, 'r') as f:
        chain_data = json.load(f)
    
    print("\n=== CONDITIONAL CONFIGURATION ANALYSIS ===")
    
    conditionals = chain_data.get('conditional_nodes', [])
    
    for i, conditional in enumerate(conditionals):
        print(f"\n--- Conditional {i} Full Configuration ---")
        for key, value in conditional.items():
            print(f"  {key}: {value}")
        
        # Check what _evaluate_conditional expects
        print(f"\n--- What _evaluate_conditional will receive ---")
        condition_type = conditional.get('condition_type', 'presence')
        print(f"  Condition type: {condition_type}")
        
        if condition_type == 'presence':
            condition_config = {
                'trigger_type': 'presence',
                'image_path': conditional.get('image_path', ''),
                'confidence': float(conditional.get('threshold', 0.8)),
                'timeout': float(conditional.get('wait_time', 5.0))
            }
            print(f"  Condition config that will be passed to fallback handler: {condition_config}")

if __name__ == "__main__":
    print("Starting presence trigger debug...")
    
    try:
        analyze_conditional_config()
        test_presence_evaluation()
        
        print("\n=== SUMMARY ===")
        print("The presence trigger evaluation has been tested.")
        print("Check the output above to see if images are being detected.")
        print("If images are not found, ensure:")
        print("1. The UI elements are currently visible on screen")
        print("2. The screenshot images match the current UI state")
        print("3. The confidence threshold is appropriate")
        
    except Exception as e:
        print(f"Error during testing: {e}")
        import traceback
        traceback.print_exc()