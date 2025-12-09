import sys
import os
import json

# Add current dir to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import model_vision
    print("Successfully imported model_vision")
except ImportError as e:
    print(f"Failed to import model_vision: {e}")
    sys.exit(1)

def test_analysis():
    print("Initializing Vision...")
    try:
        vision = model_vision.Vision("Computer_Vision_1.5.3.onnx")
        print("Vision initialized.")
        
        print("Starting single frame analysis...")
        data = vision.analyze_single_frame(1)
        print("Analysis complete.")
        
        print(json.dumps(data, indent=2))
        
    except Exception as e:
        print(f"Error during analysis: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_analysis()
