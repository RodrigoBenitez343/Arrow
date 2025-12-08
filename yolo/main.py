import sys
import os
import cv2
import numpy as np
from ultralytics import YOLO
from huggingface_hub import hf_hub_download
from shapely.geometry import Polygon

# Add the Computer_vision directory to path to import object.py
VISION_PATH = r"C:\Users\User\Desktop\LoOperV2\LoOper\player\Computer_vision"
sys.path.append(VISION_PATH)

try:
    import object
except ImportError:
    print("Error: Could not import 'object' module. Check path.")
    sys.exit(1)

def calculate_intersection(box1, box2):
    try:
        # Convert xyxy to polygon points
        x1, y1, x2, y2 = box1
        rect_1 = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        
        x1, y1, x2, y2 = box2
        rect_2 = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]

        poly1 = Polygon(rect_1)
        poly2 = Polygon(rect_2)

        return poly1.intersection(poly2).area
    except Exception as e:
        return 0

def is_box_inside_box(box1, box2):
    """Check if box1 is inside box2"""
    x1, y1, x2, y2 = box1
    middle_x = (x2 + x1) / 2
    middle_y = (y2 + y1) / 2
    
    # box2 is the container (e.g., window)
    if middle_x > box2[0] and middle_x < box2[2]:
        if middle_y > box2[1] and middle_y < box2[3]:
            return True
    return False

def main():
    print("Initializing...")
    
    # --- Configuration ---
    ICON_MODEL_REPO = "macpaw-research/yolov11l-ui-elements-detection"
    ICON_MODEL_FILE = "ui-elements-detection.pt"
    TEXT_MODEL_PATH = os.path.join(VISION_PATH, "Computer_Vision_1.5.3.onnx")
    IMAGE_PATH = r"C:\Users\User\Desktop\LoOperV2\LoOper\chains\cond2.PNG"

    # --- Load Models ---
    print("Loading Icon Detection Model...")
    try:
        icon_model_path = hf_hub_download(repo_id=ICON_MODEL_REPO, filename=ICON_MODEL_FILE)
        icon_model = YOLO(icon_model_path)
    except Exception as e:
        print(f"Error loading icon model: {e}")
        return

    print("Loading Text/Structure Detection Model...")
    try:
        text_model = YOLO(TEXT_MODEL_PATH, task="detect")
    except Exception as e:
        print(f"Error loading text model: {e}")
        return

    # --- Load Image ---
    if not os.path.exists(IMAGE_PATH):
        print(f"Error: Image not found at {IMAGE_PATH}")
        return
        
    image = cv2.imread(IMAGE_PATH)
    if image is None:
        print("Error: Could not read image.")
        return

    # --- Step 1: Text & Structure Detection ---
    print("Running Text & Structure Detection...")
    # Using the Computer_vision model
    structure_results = text_model(image, conf=0.25)
    
    # Label categorization (mimicking label_description.json)
    label_mapping = {
        "bottomlayer": "Property",
        "toplayer": "Property",
        "close_window" : "Interaction",
        "maximize" : "Interaction",
        "minize_window" : "Interaction",
        "text" : "Text",
        "window" : "Object"
    }
    
    # Collect data from Structure Model
    data = {} # {label: [coords, ...]}
    
    for result in structure_results:
        for box in result.boxes:
            cls_id = int(box.cls)
            label = text_model.names[cls_id]
            coords = box.xyxy.tolist()[0]
            
            if label not in data:
                data[label] = []
            data[label].append(coords)

    # --- Step 2: Apply Mask (Mask Text for Icon Detection) ---
    print("Applying Mask to Text regions...")
    masked_image = image.copy()
    
    text_regions = data.get("text", [])
    for box in text_regions:
        x1, y1, x2, y2 = map(int, box)
        # Fill with grey to hide text from icon model
        cv2.rectangle(masked_image, (x1, y1), (x2, y2), (128, 128, 128), -1)

    # --- Step 3: Icon Detection ---
    print("Running Icon Detection on Masked Image...")
    icon_results = icon_model(masked_image, conf=0.25)
    
    # Collect data from Icon Model
    for result in icon_results:
        for box in result.boxes:
            cls_id = int(box.cls)
            label = icon_model.names[cls_id]
            coords = box.xyxy.tolist()[0]
            
            # Treat all detections from this model as "Icon"
            if label not in data:
                data[label] = []
            data[label].append(coords)
            
            # Update mapping if new label
            if label not in label_mapping:
                label_mapping[label] = "Icon"

    # --- Step 4: Logic Processing (Object Association) ---
    print("Processing Objects and Interactions...")
    
    objects = []
    
    # 4.1 Create Main Objects (Windows)
    object_labels = [l for l, k in label_mapping.items() if k == "Object"]
    for label in object_labels:
        for coords in data.get(label, []):
            obj = object.Object(label=label, coords=coords)
            objects.append(obj)
            
    # Fallback: If no Window detected, treat the whole image as a window?
    # For this exercise, we'll assume windows are detected or we just show unassigned items later.
    if not objects:
        print("Warning: No 'window' objects detected. Creating a full-screen dummy window object.")
        h, w, _ = image.shape
        dummy_obj = object.Object(label="full_screen", coords=[0, 0, w, h])
        objects.append(dummy_obj)

    # 4.2 Assign Properties (toplayer/bottomlayer)
    property_labels = [l for l, k in label_mapping.items() if k == "Property"]
    for label in property_labels:
        for coords in data.get(label, []):
            # Find best overlapping object
            best_obj = None
            max_area = 0
            for obj in objects:
                area = calculate_intersection(coords, obj.get_coords())
                if area > max_area:
                    max_area = area
                    best_obj = obj
            
            if best_obj:
                best_obj.set_property(label)

    # 4.3 Assign Interactions, Text, and Icons
    # Items to assign
    assignable_labels = [l for l, k in label_mapping.items() if k in ["Interaction", "Text", "Icon"]]
    
    for label in assignable_labels:
        # Use a copy to avoid modification issues if we were removing (we aren't here)
        for coords in data.get(label, []):
            kind = label_mapping.get(label)
            
            for obj in objects:
                if is_box_inside_box(coords, obj.get_coords()):
                    if kind == "Text":
                        # Create Text object
                        t = object.Text(coords=coords, object=obj)
                        obj.add_text(t)
                    elif kind in ["Interaction", "Icon"]:
                        # Create Interaction object
                        # Type 1 for Standard Interactions, 2 for Icons
                        itype = 2 if kind == "Icon" else 1
                        inter = object.Interaction(label=label, coords=coords, object=obj, interaction_type=itype)
                        obj.add_interaction(inter)
                    
                    # Once assigned to the first container, break? 
                    # Usually items belong to the smallest container, but here we assume one level of windows.
                    break

    # --- Step 5: Visualization ---
    print("Visualizing Results...")
    display_img = image.copy()
    
    for obj in objects:
        # Draw Object (Window)
        x1, y1, x2, y2 = map(int, obj.get_coords())
        cv2.rectangle(display_img, (x1, y1), (x2, y2), (255, 0, 0), 2) # Blue
        label_text = f"{obj.get_label()}"
        if obj.get_property():
            label_text += f" ({obj.get_property()})"
        cv2.putText(display_img, label_text, (x1, max(0, y1-10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
        
        # Draw Text
        for txt in obj.get_text() or []:
            tx1, ty1, tx2, ty2 = map(int, txt.get_coords())
            cv2.rectangle(display_img, (tx1, ty1), (tx2, ty2), (0, 255, 0), 1) # Green
            # cv2.putText(display_img, "Text", (tx1, max(0, ty1-2)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
            
        # Draw Interactions/Icons
        for inter in obj.get_interactions() or []:
            ix1, iy1, ix2, iy2 = map(int, inter.get_coords())
            
            if inter.get_interaction_type() == 2: # Icon
                color = (0, 255, 255) # Yellow
            else: # Standard Interaction
                color = (0, 165, 255) # Orange
                
            cv2.rectangle(display_img, (ix1, iy1), (ix2, iy2), color, 2)
            cv2.putText(display_img, inter.get_label(), (ix1, max(0, iy1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    # Show results
    # Resize if too big
    scale = 1.0
    if display_img.shape[0] > 1000:
        scale = 1000 / display_img.shape[0]
        display_img = cv2.resize(display_img, None, fx=scale, fy=scale)
        masked_image = cv2.resize(masked_image, None, fx=scale, fy=scale)

    cv2.imshow("Final Result (Mixed Models)", display_img)
    cv2.imshow("Masked Image (Input for Icon Model)", masked_image)
    
    print("Press any key to close windows...")
    cv2.waitKey(0)
    cv2.destroyAllWindows()
    print("Done.")

if __name__ == "__main__":
    main()
