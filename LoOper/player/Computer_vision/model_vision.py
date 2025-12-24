import os
import sys

# Fix for Intel OpenMP runtime duplicate error
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Ensure DLL load path on Windows
if os.name == 'nt' and hasattr(os, 'add_dll_directory'):
    try:
        import site
        packages = []
        try:
            packages.extend(site.getsitepackages())
        except Exception:
            pass
        try:
            if hasattr(site, 'getusersitepackages'):
                packages.append(site.getusersitepackages())
        except Exception:
            pass
        # Also check current venv specifically
        try:
            venv_path = os.path.dirname(os.path.dirname(sys.executable)) 
            site_pkg = os.path.join(venv_path, 'Lib', 'site-packages')
            if os.path.isdir(site_pkg):
                packages.append(site_pkg)
        except Exception:
            pass
            
        for p in packages:
            torch_lib = os.path.join(p, 'torch', 'lib')
            if os.path.isdir(torch_lib):
                try:
                    os.add_dll_directory(torch_lib)
                    # Also prepend to PATH as a fallback for some DLL loaders
                    os.environ['PATH'] = torch_lib + os.pathsep + os.environ['PATH']
                except Exception:
                    pass
    except Exception:
        pass

from ultralytics import YOLO
import numpy as np
import cv2
import win32gui
import win32con
import win32api
import keyboard
from mss import mss
from PIL import Image, ImageOps
import json
import time
import logging
import multiprocessing
import pytesseract
import re
import os
import object
from shapely.geometry import Polygon
from huggingface_hub import hf_hub_download

class Vision:
    
    def __init__(self, structure_model_path):
        # Load Structure/Text Model
        print(f"Loading Structure Model: {structure_model_path}")
        self.structure_model = YOLO(structure_model_path, task="detect")
        
        # Tracking state
        self.tracked_objects = []
        
        # Load Icon Model
        print("Loading Icon Model...")
        try:
            icon_model_path = hf_hub_download(
                repo_id="macpaw-research/yolov11l-ui-elements-detection",
                filename="ui-elements-detection.pt"
            )
            self.icon_model = YOLO(icon_model_path)
        except Exception as e:
            print(f"Error loading icon model: {e}")
            self.icon_model = None

        print("Models loaded.")
        
    def find_matching_object(self, coords, label):
        """Find the closest matching object of the same class"""
        min_distance = float('inf')
        matching_object = None
        
        # Coords are [x1, y1, x2, y2]
        center_x = (coords[0] + coords[2]) / 2
        center_y = (coords[1] + coords[3]) / 2
        center = np.array([center_x, center_y])
        
        for obj in self.tracked_objects:
            if obj.get_label() == label:
                obj_coords = obj.get_coords()
                obj_center_x = (obj_coords[0] + obj_coords[2]) / 2
                obj_center_y = (obj_coords[1] + obj_coords[3]) / 2
                obj_center = np.array([obj_center_x, obj_center_y])
                
                distance = np.linalg.norm(center - obj_center)
                
                if distance < min_distance:
                    min_distance = distance
                    matching_object = obj
                    
        # Only match if the centers are reasonably close
        if min_distance > 50: 
            matching_object = None
            
        return matching_object

    def analyze_single_frame(self, monitor_index=1):
        """
        Perform a single pass of vision analysis on the specified monitor.
        Returns a dictionary containing detected objects, text, and interactions.
        """
        logging.getLogger('ultralytics').setLevel(logging.WARNING)
        
        sct = mss()
        monitors = sct.monitors
        if monitor_index >= len(monitors):
            monitor_index = 0 # Fallback
        monitor = monitors[monitor_index]
        
        # Capture screen
        screen = np.array(sct.grab(monitor))
        screen = screen[:, :, :3] # Remove alpha
        
        # Setup labels
        structure_labels = list(self.structure_model.names.values())
        icon_labels = list(self.icon_model.names.values()) if self.icon_model else []
        all_labels = list(set(structure_labels + icon_labels))
        
        label_objects = {label: "Interaction" for label in all_labels}
        
        try:
            json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "label_description.json")
            if os.path.exists(json_path):
                with open(json_path, "r") as f:
                    json_data = json.load(f)
                for label in all_labels:
                    if label in json_data:
                        label_objects[label] = json_data[label]
        except Exception as e:
            print(f"Error loading label description: {e}")
            
        # Initialize data
        data = {label: [] for label in all_labels}
        
        # 1. Structure Detection
        results_structure = self.structure_model(screen, conf=0.25)
        for result in results_structure:
            for box in result.boxes:
                label = self.structure_model.names[int(box.cls)]
                data[label].append(box.xyxy.tolist()[0])
                
        # 2. Masking
        masked_screen = screen.copy()
        if "text" in data:
            for coords in data["text"]:
                x1, y1, x2, y2 = map(int, coords)
                cv2.rectangle(masked_screen, (x1, y1), (x2, y2), (128, 128, 128), -1)
                
        # 3. Icon Detection
        if self.icon_model:
            results_icons = self.icon_model(masked_screen, conf=0.25)
            for result in results_icons:
                for box in result.boxes:
                    label = self.icon_model.names[int(box.cls)]
                    if label not in data:
                        data[label] = []
                    data[label].append(box.xyxy.tolist()[0])
                    
        # 4. Create Objects
        objects = []
        
        # Identify "Object" type labels
        obj_labels = [key for key in label_objects if label_objects[key] == "Object"]
        
        for label in obj_labels:
            if label in data:
                for coords in data[label]:
                    new_obj = object.Object(label=label, coords=coords)
                    objects.append(new_obj)
                    
        # 5. Assign Properties
        property_labels = [key for key in label_objects if label_objects[key] == "Property"]
        
        for obj in objects:
            best_property = None
            max_area = 0
            
            for prop_label in property_labels:
                if prop_label in data:
                    for prop_coords in data[prop_label]:
                        area = self.calculate_intersection(prop_coords, obj.get_coords())
                        if area > max_area:
                            max_area = area
                            best_property = prop_label
            
            if best_property:
                obj.set_property(best_property)
                
        # 6. Assign Interactions and Text
        for obj in objects:
            obj_coords = obj.get_coords()
            
            # Check for interactions and text inside object
            for key, coords_list in data.items():
                if key not in label_objects: continue
                type_ = label_objects[key]
                
                if type_ == "Interaction":
                    for coords in coords_list:
                        if self.is_box_inside_box(coords, obj_coords):
                            interaction = object.Interaction(label=key, coords=coords, object=obj, interaction_type=1)
                            obj.add_interaction(interaction)
                            
                elif type_ == "Text":
                    for coords in coords_list:
                        if self.is_box_inside_box(coords, obj_coords):
                            # PERFORM OCR HERE
                            try:
                                text_content = self.read_image(screen, coords)
                            except Exception:
                                text_content = ""
                            
                            if text_content and text_content.strip():
                                text_item = object.Text(content=text_content, coords=coords, object=obj)
                                obj.add_text(text_item)
                                
        # 7. Format Output
        output_data = {"Object": []}
        for i, obj in enumerate(objects):
            obj_dict = {
                "index": f"{obj.get_label()}_{i}",
                "label": obj.get_label(),
                "property": obj.get_property(),
                "coords": obj.get_coords(),
                "text_content": [t.get_content() for t in obj.get_text() or []],
                "interactions": obj.interactions_to_list()
            }
            output_data["Object"].append(obj_dict)

        # 8. Generate Annotated Image
        try:
            annotated_img = self.draw_annotations(screen.shape, data, objects)
            timestamp = int(time.time())
            # Save to a temporary location or specific directory
            # Assuming we are in LoOper/player/Computer_vision
            # We want to save where the other screenshots are, or locally.
            # LoOper/screenshots is a good place.
            
            # Try to find the screenshots directory relative to this file
            # this file: .../LoOper/player/Computer_vision/model_vision.py
            # screenshots: .../LoOper/screenshots
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            screenshots_dir = os.path.join(base_dir, "screenshots")
            if not os.path.exists(screenshots_dir):
                os.makedirs(screenshots_dir, exist_ok=True)
            
            image_filename = f"yolo_annotation_{timestamp}.png"
            image_path = os.path.join(screenshots_dir, image_filename)
            cv2.imwrite(image_path, annotated_img)
            output_data["image_path"] = image_path
            print(f"Annotated image saved to: {image_path}")
        except Exception as e:
            print(f"Error generating annotated image: {e}")
            output_data["image_path"] = None
            
        return output_data

    def draw_annotations(self, shape, data, objects):
        """Draw annotations on a black background"""
        h, w = shape[:2]
        display_overlay = np.zeros((h, w, 3), dtype=np.uint8)
        
        # Draw all raw data first (Green)
        for label, boxes in data.items():
            for coords in boxes:
                x1, y1, x2, y2 = map(int, coords)
                cv2.rectangle(display_overlay, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(display_overlay, str(label), (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        # Draw objects (Blue)
        for obj in objects:
            x1, y1, x2, y2 = map(int, obj.get_coords())
            cv2.rectangle(display_overlay, (x1, y1), (x2, y2), (255, 0, 0), 2)
            cv2.putText(display_overlay, str(obj.get_label()), (x1, max(0, y1 - 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
            if obj.get_property():
                cv2.putText(display_overlay, str(obj.get_property()), (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
            
            # Draw interactions inside objects (Orange)
            for inter in obj.get_interactions() or []:
                ix1, iy1, ix2, iy2 = map(int, inter.get_coords())
                cv2.rectangle(display_overlay, (ix1, iy1), (ix2, iy2), (0, 165, 255), 2)
                cv2.putText(display_overlay, str(inter.get_label()), (ix1, max(0, iy1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)
        
        return display_overlay


    def start_vision(self, num):

        logging.getLogger('ultralytics').setLevel(logging.WARNING)

        sct = mss()
        monitors = sct.monitors
        monitor = monitors[num]

        # Combine labels from both models
        structure_labels = list(self.structure_model.names.values())
        icon_labels = list(self.icon_model.names.values()) if self.icon_model else []
        
        all_labels = list(set(structure_labels + icon_labels))

        label_objects = {label: "Interaction" for label in all_labels} # Default to Interaction

        try:
            with open("label_description.json", "r") as f:
                json_data = json.load(f)
            
            for label in all_labels:
                if label in json_data:
                    label_objects[label] = json_data[label]
                else:
                    # If not in json, we keep the default "Interaction" (or whatever logic we want)
                    # For icons from the new model, they are likely interactions.
                    if label in icon_labels and label not in structure_labels:
                         label_objects[label] = "Interaction"
                    elif label not in json_data:
                         print(f"Warning: Label '{label}' not found in label_description.json. Defaulting to Interaction.")
                         
        except Exception as e:
            print(f"Error loading label description: {e}")
            print(f"Valid labels: {all_labels}")


        # Setup OpenCV window
        window_name = "Vision Overlay"
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        
        # Create a dummy black image to ensure window is created and sized
        dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
        cv2.imshow(window_name, dummy_img)
        cv2.waitKey(1) # Process events to actually create the window

        # Get Window Handle
        hwnd = win32gui.FindWindow(None, window_name)
        
        # Retry finding window if missed
        if not hwnd:
            time.sleep(0.1)
            hwnd = win32gui.FindWindow(None, window_name)

        if hwnd:
            print(f"Overlay Window Found: {hwnd}")
            
            # Set Always on Top, Transparent (Click-through), and Layered
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOPMOST)
            
            # Use LWA_COLORKEY to make BLACK (0,0,0) pixels fully transparent
            # This removes the "dark background" and only shows drawn colors
            win32gui.SetLayeredWindowAttributes(hwnd, 0, 0, win32con.LWA_COLORKEY)
            
            # Exclude from Screen Capture (Fixes Infinite Mirroring)
            try:
                # WDA_EXCLUDEFROMCAPTURE = 0x00000011
                win32gui.SetWindowDisplayAffinity(hwnd, 0x00000011)
                print("Window excluded from capture.")
            except Exception as e:
                print(f"Could not set window affinity (capture exclusion): {e}")
        else:
            print("Could not find Overlay Window!")
        
        # Set to Fullscreen
        cv2.setWindowProperty(window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

        while True:
            
            if keyboard.is_pressed('esc'):
                print("Escape pressed. Exiting...")
                break

            screen = np.array(sct.grab(monitor))
            screen = screen[:, :, :3]
            
            # Initialize data dictionary for all labels
            data = {label: [] for label in all_labels}

            # --- Step 1: Structure Detection ---
            results_structure = self.structure_model(screen, conf=0.25)

            for result in results_structure:
                for box in result.boxes:
                    label = self.structure_model.names[int(box.cls)]
                    data[label].append(box.xyxy.tolist()[0])

            # --- Step 2: Masking ---
            # Create a mask for text regions to hide them from the icon model
            masked_screen = screen.copy()
            if "text" in data:
                for coords in data["text"]:
                    x1, y1, x2, y2 = map(int, coords)
                    # Draw grey rectangle over text
                    cv2.rectangle(masked_screen, (x1, y1), (x2, y2), (128, 128, 128), -1)
            
            # --- Step 3: Icon Detection ---
            if self.icon_model:
                results_icons = self.icon_model(masked_screen, conf=0.25)
                
                for result in results_icons:
                    for box in result.boxes:
                        label = self.icon_model.names[int(box.cls)]
                        # Add icon detections to data
                        # Note: If label exists in both models, we append. 
                        # Ideally, icon model labels are distinct or we want them merged.
                        if label not in data:
                            data[label] = [] # Should be covered by all_labels init, but just in case
                        data[label].append(box.xyxy.tolist()[0])

            # --- Processing Objects ---
            obj_label = []

            for key in label_objects:

                if label_objects[key] == "Object":

                    obj_label.append(key)

            # --- Tracking Integration ---
            
            # 1. Reset state of tracked objects
            for obj in self.tracked_objects:
                obj.set_text([])
                obj.set_interactions([])
                obj.set_property(None)

            # 2. Identify current frame detections
            current_frame_detections = []
            for label in obj_label:
                if label in data:
                    for coords in data[label]:
                         current_frame_detections.append({'label': label, 'coords': coords})

            # 3. Match with tracked objects
            current_detections_set = set() 
            
            for det in current_frame_detections:
                 matched_obj = self.find_matching_object(det['coords'], det['label'])
                 if matched_obj:
                     matched_obj.update(det['coords'])
                     current_detections_set.add(matched_obj)
                 else:
                     new_obj = object.Object(label=det['label'], coords=det['coords'])
                     self.tracked_objects.append(new_obj)
                     current_detections_set.add(new_obj)
            
            # 4. Check visibility & Cleanup
            debounce_time = 0.2 
            for obj in self.tracked_objects:
                if obj not in current_detections_set:
                    obj.check_visibility(debounce_time)
            
            self.tracked_objects = [obj for obj in self.tracked_objects if time.time() - obj.last_seen < debounce_time * 5]
            
            # 5. Define 'objects' for downstream logic
            objects = [obj for obj in self.tracked_objects if obj.is_visible]

            property_labels = []

            for key in label_objects:

                if label_objects[key] == "Property":

                    property_labels.append(key)

            intersections = {}

            for property in property_labels:
                
                if property in data:
                    property_coords = data[property]
    
                    for coords in property_coords:
    
                        for obj in objects:
    
                            intersections[self.calculate_intersection(coords, obj.get_coords())] = [property, obj]
            
            sorted_keys = sorted(intersections, reverse=True)

            i = 0

            for key in sorted_keys:

                intersections[key][1].set_property(intersections[key][0])

                if i == len(property_labels)-1:

                    break  

                i+=1

            
            for obj in objects:

                if obj.get_property() == "toplayer":

                    for key in data:

                        if key in label_objects and label_objects[key] == "Interaction":

                            for coords in data[key]:

                                if self.is_box_inside_box(coords, obj.get_coords()):

                                    interaction = object.Interaction(label=key, coords=coords, object=obj, interaction_type=1)

                                    obj.add_interaction(interaction)
                                    
                                    # Note: We are iterating over a list we are removing from? 
                                    # Original code: data[key].remove(coords)
                                    # It's better to not modify list while iterating, but following original logic for now 
                                    # assuming simple loop. Actually, iterating over data[key] and removing is dangerous.
                                    # The original code did: for coords in data[key]: ... data[key].remove(coords)
                                    # This will skip elements. However, since we re-loop every frame, maybe it's "okay" visually, 
                                    # but logically flawed. I'll leave it as is to minimize refactor risk unless it breaks.
                                    # Wait, I should probably fix it or use a copy.
                                    pass

                        if key in label_objects and label_objects[key] == "Text":

                            for coords in data[key]:

                                if self.is_box_inside_box(coords, obj.get_coords()):

                                    text = object.Text(coords=coords, object=obj)

                                    obj.add_text(text)
                                    
                                    pass

            # Fix for removing items while iterating:
            # Re-implementing the removal logic properly or just ignoring removal since we rebuild 'objects' every frame.
            # The original code removed them probably to avoid double assignment? 
            # Or maybe just to clear them.
            # Given the original code had this bug (or feature), I will just iterate normally and not remove, 
            # or if removal is critical (to avoid assigning to multiple objects), I'll handle it.
            # In original code:
            # for coords in data[key]:
            #    if inside: add; remove
            # This means an item is assigned to the FIRST "toplayer" object it fits in? 
            # But the loop `for obj in objects` is outer. So it tries to assign all items to obj1, removes them. 
            # Then obj2 only sees remaining items. Yes, that's the logic.
            
            # Let's preserve that logic but safely.
            
            # Helper to process assignments
            def assign_items_to_objects(layer_name):
                 for obj in objects:
                    if obj.get_property() == layer_name:
                        for key in list(data.keys()): # keys are strings
                            if key not in label_objects: continue
                            
                            type_ = label_objects[key]
                            
                            if type_ == "Interaction":
                                # Iterate over a copy of the list
                                for coords in list(data[key]):
                                    if self.is_box_inside_box(coords, obj.get_coords()):
                                        interaction = object.Interaction(label=key, coords=coords, object=obj, interaction_type=1)
                                        obj.add_interaction(interaction)
                                        if coords in data[key]:
                                            data[key].remove(coords)
                                            
                            elif type_ == "Text":
                                for coords in list(data[key]):
                                    if self.is_box_inside_box(coords, obj.get_coords()):
                                        text = object.Text(coords=coords, object=obj)
                                        obj.add_text(text)
                                        if coords in data[key]:
                                            data[key].remove(coords)

            assign_items_to_objects("toplayer")
            assign_items_to_objects("bottomlayer")


            del json_data

            json_data = []

            i = 0

            data_dict = {"Object": []}

            for obj in objects:

                temp_dict = {"index": f"{obj.get_label()}_{i}",
                             "label": obj.get_label(),
                             "property": obj.get_property(),
                             "coords": obj.get_coords(),
                             "textes": obj.num_of_text(),
                             "interactions": obj.interactions_to_list()
                            }
                
                data_dict["Object"].append(temp_dict)

                i+=1

            with open("view_output.json", "w") as f:

                json.dump(data_dict, f, indent=4)

            # Create black background image (which will be transparent)
            h, w = screen.shape[:2]
            # Use zeros for black background (key color)
            display_overlay = np.zeros((h, w, 3), dtype=np.uint8)
            
            # Only draw on the overlay, do not blend with screen capture
            # This prevents the "darkening" effect since we only show the overlay window
            
            # Draw remaining items (not assigned to objects? or all items? Original drew all in 'data')
            # Original code: for label, boxes in data.items(): ...
            # Note: 'data' has items removed if they were assigned! 
            # So the original code only drew UNASSIGNED items in the first loop?
            # Let's check original code:
            # It drew `data` items (green) -> then drew `objects` (blue) -> then drew `obj.interactions` (orange).
            # So yes, unassigned items are green. Assigned interactions are orange.
            
            for label, boxes in data.items():
                for coords in boxes:
                    x1, y1, x2, y2 = map(int, coords)
                    # Draw directly on the black image
                    cv2.rectangle(display_overlay, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.putText(display_overlay, str(label), (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            for obj in objects:
                x1, y1, x2, y2 = map(int, obj.get_coords())
                cv2.rectangle(display_overlay, (x1, y1), (x2, y2), (255, 0, 0), 2)
                cv2.putText(display_overlay, str(obj.get_label()), (x1, max(0, y1 - 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 0, 0), 2)
                if obj.get_property():
                    cv2.putText(display_overlay, str(obj.get_property()), (x1, max(0, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 0, 0), 2)
                for inter in obj.get_interactions() or []:
                    ix1, iy1, ix2, iy2 = map(int, inter.get_coords())
                    cv2.rectangle(display_overlay, (ix1, iy1), (ix2, iy2), (0, 165, 255), 2)
                    cv2.putText(display_overlay, str(inter.get_label()), (ix1, max(0, iy1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

            cv2.imshow(window_name, display_overlay)
            cv2.waitKey(1) # Process window events but don't block


        cv2.destroyAllWindows()
    

    def contains_key(self, data, outer_key, search_key):
        if outer_key in data:
    
            inner_dict = data[outer_key]
            if search_key in inner_dict[0]:
                return True
        return False
    
                            
    def read_image(self, image, coords):

        pytesseract.pytesseract.tesseract_cmd = r'C:/Program Files/Tesseract-OCR/tesseract.exe'  # Tesseract path

        
        # Konvertiere die Koordinaten in Ganzzahlen
        coords = [int(x) for x in coords]
        x1, y1, x2, y2 = coords
            

        try:
            # Füge Puffer hinzu, um das Zuschneiden etwas großzügiger zu machen
            x1 -= 5
            y1 -= 5
            x2 += 5
            y2 += 5

            # Verwende die crop() Methode, um den Bereich des Bildes zu extrahieren
            image = image[y1:y2, x1:x2]
        except Exception as e:
            print(f"Error cropping image: {e}")
            x1, y1, x2, y2 = coords
            image = image[y1:y2, x1:x2]
            pass


        text = pytesseract.image_to_string(image)

        # Bereinige den Text, um nicht druckbare Zeichen zu entfernen
        text = re.sub(r'[^\x00-\x7F]+', '', text)
        text = text.replace('\n', '')

        return text
    
    def calculate_intersection(self, box1, box2):

        try:
            rect_1 = self.convert_xyxy_to_xyxyxyxy(coords=box1)
            rect_2 = self.convert_xyxy_to_xyxyxyxy(coords=box2)

            rect_1 = Polygon(rect_1)
            rect_2 = Polygon(rect_2)

            poly_intersection = rect_1.intersection(rect_2)

            return poly_intersection.area
        
        except:

            return 0

    def convert_xyxy_to_xyxyxyxy(self, coords):
        """Korrigiere die Reihenfolge der Punkte, um ein valides Polygon zu erstellen"""
        coords_1 = (coords[0], coords[1])  # (x1, y1)
        coords_2 = (coords[2], coords[1])  # (x2, y1)
        coords_3 = (coords[2], coords[3])  # (x2, y2)
        coords_4 = (coords[0], coords[3])  # (x1, y2)

        return_coords = [coords_1, coords_2, coords_3, coords_4]

        return return_coords
    
    def is_box_inside_box(self, box1, box2):

        x1, y1, x2, y2 = box1

        middle_x = (x2 + x1) / 2
        middle_y = (y2 + y1) / 2

        if middle_x > box2[0] and middle_x < box2[2]:

            if middle_y > box2[1] and middle_y < box2[3]:

                return True
            
        return False
        

class Api:

    def __init__(self) -> None:
        pass

    def get_data(self, label = None):
        with open("view_output.json") as f:
            data = json.load(f)
        
        if not label:
            return data
        else:
            
            new_data = {}

            for key in data:

                if key.split("_")[0] == label:
                    new_data[key] = data[key]
                
                
            return new_data

if __name__ == "__main__":
    vision = Vision("Computer_Vision_1.5.3.onnx")
    vision.start_vision(1)
