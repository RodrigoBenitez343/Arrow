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
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Computer_Vision_1.5.3.onnx")
        vision = model_vision.Vision(model_path)
        print("Vision initialized.")
        
        test_img_path = r"d:\LoOperV2\LoOper\vision_output\image.png"
        print(f"Starting image analysis on: {test_img_path} ...")
        
        data = vision.analyze_image_file(test_img_path)
        print("Analysis complete.\n")
        
        p = data.get("pruned_output_path")
        if p and os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                pruned = json.load(f)
            
            print("--- Extracted Fields ---")
            for field in pruned.get("fields", []):
                print(f"Index {field.get('index')}: Label='{field.get('label')}' | Filled={field.get('filled')} | Text='{field.get('value_text')}'")
                
        m = data.get("masked_output_path")
        if m and os.path.exists(m):
            print(f"\nMasked image saved to: {m}")
            
        lv = data.get("layout_vis_path")
        if lv and os.path.exists(lv):
            print(f"LayoutLMv3 visualization saved to: {lv}")
        
    except Exception as e:
        print(f"Error during analysis: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_analysis()
